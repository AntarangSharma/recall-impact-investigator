from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from recall.criteria import match
from recall.llm import Adapter, Choice, ModelError
from recall.models import Finding, display_guard
from recall.sources import BRANDS, URLS, discover, fetch, load_snapshot, parse_html
from recall.store import Store

LOCK = threading.RLock()


class State(TypedDict):
    investigation_id: str
    searched: bool
    candidates: list[str]
    read: list[str]
    compared: bool
    drafted: bool
    step: int
    action: dict[str, str]


class Investigator:
    def __init__(self, store: Store, checkpoint_path: Path):
        self.store = store
        self.connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
        self.checkpointer = SqliteSaver(self.connection)
        graph = StateGraph(State)
        graph.add_node("choose", self.choose)
        graph.add_node("tool", self.tool)
        graph.add_node("clarify", self.clarify)
        graph.add_edge(START, "choose")
        graph.add_conditional_edges("choose", self.route)
        graph.add_edge("tool", "choose")
        graph.add_edge("clarify", "choose")
        self.graph = graph.compile(checkpointer=self.checkpointer)

    def close(self) -> None:
        self.connection.close()

    def adapter(self, identity: str) -> Adapter:
        return Adapter(self.store, identity, self.store.investigation(identity)["mode"] == "live")

    def choose(self, state: State) -> dict[str, Any]:
        identity = state["investigation_id"]
        if state["step"] >= 24:
            raise ModelError("Agent step limit reached.")
        choices: list[Choice] = []
        if not state["searched"]:
            choices = [
                Choice(
                    tool="search_notices",
                    target="supported_catalog",
                    decision="discover_candidates",
                )
            ]
        elif unread := [n for n in state["candidates"] if n not in state["read"]]:
            choices = [
                Choice(tool="read_notice", target=n, decision="inspect_source") for n in unread
            ]
        elif not state["compared"]:
            choices = [
                Choice(tool="lookup_inventory", target="all", decision="compare_identifiers")
            ]
        elif requests := self.store.requests(identity, pending=True):
            choices = [
                Choice(tool="request_identifier", target=r["id"], decision="resolve_missing_field")
                for r in requests
            ]
        elif not state["drafted"]:
            choices = [
                Choice(tool="draft_inquiry", target="supplier_draft", decision="prepare_review")
            ]
        else:
            choices = [Choice(tool="finish", target="review_queue", decision="complete")]
        action = self.adapter(identity).choose(
            choices,
            {
                "read_notices": state["read"],
                "pending_identifiers": self.store.requests(identity, True),
                "finding_counts": {
                    s: sum(f.status == s for f in self.store.findings(identity))
                    for s in ("affected_by_notice", "excluded_by_notice", "needs_information")
                },
            },
        )
        self.store.event(
            identity,
            f"decision-{state['step']}",
            "decision",
            f"{action.decision}: {action.tool} {action.target}",
        )
        return {"action": action.model_dump(), "step": state["step"] + 1}

    def route(self, state: State) -> str:
        tool = state["action"]["tool"]
        return END if tool == "finish" else ("clarify" if tool == "request_identifier" else "tool")

    def compute(
        self, identity: str, only_stock: str | None = None, field: str | None = None
    ) -> None:
        for stock in self.store.stocks(identity):
            if only_stock and stock.stock_id != only_stock:
                continue
            for snapshot, criteria in self.store.sources(identity):
                old = next(
                    (
                        f
                        for f in self.store.findings(identity)
                        if f.stock_id == stock.stock_id and f.notice_id == snapshot.notice_id
                    ),
                    None,
                )
                if field and old and field not in old.required_fields:
                    continue
                finding = match(stock, snapshot, criteria)
                self.store.save_finding(identity, finding)
                for missing in finding.required_fields:
                    self.store.request(identity, finding, missing)

    def tool(self, state: State) -> dict[str, Any]:
        identity = state["investigation_id"]
        adapter = self.adapter(identity)
        tool, target = state["action"]["tool"], state["action"]["target"]
        self.store.event(identity, f"tool-{state['step']}", tool, target)
        if tool == "search_notices":
            candidates = list(URLS)
            if adapter.live:
                found = set()
                for brand in BRANDS.values():
                    found.update(discover(brand))
                if not set(candidates) <= found:
                    raise ModelError(
                        "Live discovery was incomplete; supported catalog was not fully retrieved."
                    )
            return {"searched": True, "candidates": candidates}
        if tool == "read_notice":
            snapshot = (
                parse_html(target, fetch(URLS[target])) if adapter.live else load_snapshot(target)
            )
            # Record source changes before inference, even when extraction later fails.
            self.store.snapshot(snapshot, None, promote=adapter.live)
            criteria = adapter.extract(snapshot)
            self.store.bind(identity, snapshot, criteria)
            return {"read": state["read"] + [target]}
        if tool == "lookup_inventory":
            self.compute(identity)
            return {"compared": True}
        if tool == "draft_inquiry":
            facts = {
                f"{f.stock_id}:{f.notice_id}": self.explanation(f)
                for f in self.store.findings(identity)
                if f.valid
            }
            statuses = {
                f"{f.stock_id}:{f.notice_id}": f.status
                for f in self.store.findings(identity)
                if f.valid
            }
            self.store.draft(identity, adapter.narrative(facts, statuses))
            self.store.status(identity, "complete")
            return {"drafted": True}
        raise ModelError("Unknown tool.")

    @staticmethod
    def explanation(finding: Finding) -> str:
        return display_guard(
            f"Stock {finding.stock_id}; CPSC notice {finding.notice_id}; "
            f"{finding.status}. {finding.reason} "
            f"Source: {URLS[finding.notice_id]} (snapshot {finding.source_hash[:12]})."
        )

    def clarify(self, state: State) -> dict[str, Any]:
        identity, target = state["investigation_id"], state["action"]["target"]
        request = next(r for r in self.store.requests(identity) if r["id"] == target)
        self.store.event(identity, "request-" + target, "request_identifier", request["question"])
        self.store.status(identity, "waiting_for_identifier")
        # The node replays after restart. Side effects before interrupt are idempotent.
        answer = interrupt({"request_id": target, "question": request["question"]})
        if not isinstance(answer, dict) or answer.get("request_id") != target:
            raise ValueError("Resume answer does not match the pending request.")
        self.store.answer(identity, target, str(answer.get("answer", "")))
        self.compute(identity, request["stock_id"], request["field"])
        self.store.event(
            identity,
            "answer-" + target,
            "identifier_answer",
            request["stock_id"] + ":" + request["field"],
        )
        return {"drafted": False}

    def run(self, identity: str, answer: dict[str, str] | None = None) -> None:
        config: RunnableConfig = {"configurable": {"thread_id": identity}, "recursion_limit": 60}
        with LOCK:
            try:
                current = self.graph.get_state(config)
                if answer:
                    if self.store.investigation(identity)["status"] == "source_changed":
                        raise ValueError(
                            "Source changed; create a new investigation before resuming."
                        )
                    pending = self.store.requests(identity, True)
                    if not any(r["id"] == answer.get("request_id") for r in pending):
                        answered = self.store.requests(identity)
                        if any(
                            r["id"] == answer.get("request_id")
                            and r["answer"] == answer.get("answer")
                            for r in answered
                        ):
                            if not current.next:
                                return
                        else:
                            raise ValueError("Request is not pending.")
                    self.graph.invoke(Command(resume=answer), config)
                elif not current.values:
                    initial: State = {
                        "investigation_id": identity,
                        "searched": False,
                        "candidates": [],
                        "read": [],
                        "compared": False,
                        "drafted": False,
                        "step": 0,
                        "action": {},
                    }
                    self.graph.invoke(initial, config)
                elif (
                    current.next
                    and self.store.investigation(identity)["status"] != "waiting_for_identifier"
                ):
                    self.graph.invoke(None, config)
            except Exception as exc:
                self.store.status(identity, "failed", f"{type(exc).__name__}: {exc}")
                self.store.event(identity, "failure", "failed", type(exc).__name__)
                raise
