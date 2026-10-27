from __future__ import annotations

import json
import re
import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from recall.agent import Investigator
from recall.criteria import match, nodes, valid_span, validate_criteria
from recall.inventory import read_inventory
from recall.llm import Adapter
from recall.models import (
    Alias,
    Criteria,
    Evidence,
    Identifier,
    Rule,
    Snapshot,
    Stock,
    display_guard,
)
from recall.sources import BRANDS, DATA, URLS, discover, fetch, load_snapshot, parse_html
from recall.store import Store, now

ROOT = DATA.parent
LABELS = ["affected_by_notice", "excluded_by_notice", "needs_information"]


def gold() -> dict[str, Any]:
    return json.loads((ROOT / "evals/gold_criteria.json").read_text())  # type: ignore[no-any-return]


def cases() -> list[dict[str, Any]]:
    return json.loads((ROOT / "evals/scenarios.json").read_text())  # type: ignore[no-any-return]


def as_stock(row: dict[str, Any]) -> Stock:
    return Stock(
        stock_id=row["stock_id"],
        location=row["location"],
        brand=Identifier.parse(row.get("brand"), "brand"),
        model=Identifier.parse(row.get("model"), "model"),
        date_code=Identifier.parse(row.get("date_code"), "date_code"),
        serial_number=Identifier.parse(row.get("serial_number"), "serial_number"),
    )


def fixed_criteria(notice_id: str, snapshot: Snapshot) -> Criteria:
    """Evaluation-only conversion of independently authored gold into typed rules."""
    spec = gold()[notice_id]
    quote = snapshot.text.split("Description:", 1)[1].strip().split(". ", 1)[0] + "."
    start = snapshot.text.index(quote)
    evidence = Evidence(start=start, end=start + len(quote), quote=quote)
    children = []
    for group in spec["groups"]:
        rule = Rule(op="model_set", values=group["models"], evidence=evidence)
        if group["date_codes"]:
            rule = Rule(
                op="all",
                children=[
                    rule,
                    Rule(op="date_code_set", values=group["date_codes"], evidence=evidence),
                ],
                evidence=evidence,
            )
        children.append(rule)
    root = (
        children[0] if len(children) == 1 else Rule(op="any", children=children, evidence=evidence)
    )
    return Criteria(
        brand=spec["brand"],
        root=root,
        complete=True,
        aliases=[
            Alias(canonical=c, alias=a, evidence=evidence) for a, c in spec["aliases"].items()
        ],
    )


def signature(criteria: Criteria) -> dict[str, Any]:
    groups: list[tuple[str, str]] = []
    for node in nodes(criteria.root):
        if node.op == "all":
            models = [c.values for c in node.children if c.op == "model_set"]
            codes = [c.values for c in node.children if c.op == "date_code_set"]
            if models and codes:
                groups.extend((m, c) for m in models[0] for c in codes[0])
    return {
        "brand": criteria.brand.upper(),
        "models": sorted(v for n in nodes(criteria.root) if n.op == "model_set" for v in n.values),
        "aliases": sorted((a.alias, a.canonical) for a in criteria.aliases),
        "model_code_pairs": sorted(groups),
        "complete": criteria.complete,
        "unsupported": criteria.unsupported,
        "operators": sorted(n.op for n in nodes(criteria.root)),
    }


def extraction_score(
    extracted: Mapping[str, Criteria | None], snapshots: dict[str, Snapshot]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for notice, snapshot in snapshots.items():
        target = signature(fixed_criteria(notice, snapshot))
        candidate = extracted.get(notice)
        actual = signature(candidate) if candidate else {}
        # Seven exact field groups per notice. Extra conditions and omitted values fail the field.
        correct = sum(actual.get(k) == v for k, v in target.items())
        ev = [n.evidence for n in nodes(candidate.root)] if candidate else []
        if candidate:
            ev.extend(a.evidence for a in candidate.aliases)
        result[notice] = {
            "held_out": notice == "24159",
            "correct_fields": correct,
            "total_fields": len(target),
            "citation_valid": sum(valid_span(e, snapshot.text) for e in ev),
            "citation_total": len(ev),
            "fields": {k: actual.get(k) == v for k, v in target.items()},
        }
    return result


def baseline(stock: Stock, snapshot: Snapshot) -> str:
    if stock.model.state in {"missing", "empty"}:
        return "needs_information"
    model = (stock.model.value or "").strip().upper()
    found = re.search(
        r"(?<![A-Z0-9-])" + re.escape(model) + r"(?![A-Z0-9-])", snapshot.text.upper()
    )
    return "affected_by_notice" if found else "excluded_by_notice"


def comparison(
    criteria: Mapping[str, Criteria | None],
    snapshots: dict[str, Snapshot],
    use_baseline: bool = False,
) -> dict[str, Any]:
    records = []
    for case in cases():
        notice = case["notice_id"]
        snapshot = snapshots[notice].model_copy(deep=True)
        candidate = criteria[notice]
        rules = candidate.model_copy(deep=True) if candidate else None
        if case["condition"] == "incomplete":
            snapshot.complete = False
        elif case["condition"] == "unsupported" and rules:
            rules.unsupported = ["Synthetic unsupported condition: manually inspect source."]
        stock = as_stock(case["stock"])
        if use_baseline:
            prediction, required = baseline(stock, snapshot), []
        else:
            finding = match(stock, snapshot, rules)
            prediction, required = finding.status, finding.required_fields
        records.append(
            {
                "case_id": case["case_id"],
                "notice_id": notice,
                "expected": case["expected"],
                "predicted": prediction,
                "required": required,
                "expected_request": case["expected_request"],
                "rationale": case["rationale"],
            }
        )

    def score(rows: list[dict[str, Any]]) -> dict[str, Any]:
        matrix = {
            e: {p: sum(r["expected"] == e and r["predicted"] == p for r in rows) for p in LABELS}
            for e in LABELS
        }
        tp = matrix[LABELS[0]][LABELS[0]]
        actual_affected = sum(r["expected"] == LABELS[0] for r in rows)
        predicted_affected = sum(r["predicted"] == LABELS[0] for r in rows)
        requested = [r for r in rows if r["expected_request"]]
        exclusions = [r for r in rows if r["predicted"] == LABELS[1]]
        false_models = [
            r
            for r in rows
            if r["expected"] == LABELS[1]
            and r["predicted"] == LABELS[0]
            and any(
                w in r["rationale"].lower()
                for w in ("suffix", "hyphen", "model generation", "near model")
            )
        ]
        return {
            "correct": sum(r["expected"] == r["predicted"] for r in rows),
            "total": len(rows),
            "confusion": matrix,
            "affected_recall": {"numerator": tp, "denominator": actual_affected},
            "affected_precision": {"numerator": tp, "denominator": predicted_affected},
            "incorrect_exclusions": {
                "numerator": sum(r["expected"] != LABELS[1] for r in exclusions),
                "denominator": len(exclusions),
            },
            "false_model_matches": {
                "numerator": len(false_models),
                "denominator": sum(
                    r["expected"] == LABELS[1]
                    and any(
                        w in r["rationale"].lower()
                        for w in ("suffix", "hyphen", "model generation", "near model")
                    )
                    for r in rows
                ),
            },
            "correct_identifier_requests": {
                "numerator": sum(
                    set(r["required"]) == set(r["expected_request"]) for r in requested
                ),
                "denominator": len(requested),
            },
            "unnecessary_requests": sum(
                bool(r["required"]) and not r["expected_request"] for r in rows
            ),
        }

    return {
        "all": score(records),
        "held_out_insignia": score([r for r in records if r["notice_id"] == "24159"]),
        "development": score([r for r in records if r["notice_id"] != "24159"]),
        "cases": records,
    }


def report(path: Path, value: Any) -> None:
    text = json.dumps(value, indent=2, ensure_ascii=True) + "\n"
    display_guard(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def fixture_eval() -> dict[str, Any]:
    snapshots = {n: load_snapshot(n) for n in URLS}
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "app.db")
        adapter = Adapter(store, "fixtures")
        scripted = {n: adapter.extract(s) for n, s in snapshots.items()}
    fixed = {n: fixed_criteria(n, s) for n, s in snapshots.items()}
    result: dict[str, Any] = {
        "mode": "scripted_fixture",
        "created_at": now(),
        "synthetic_cases": True,
        "fixed_criteria_matching": comparison(fixed, snapshots),
        "scripted_extraction_matching": comparison(scripted, snapshots),
        "exact_model_baseline": comparison(fixed, snapshots, True),
        "scripted_extraction_fields": extraction_score(scripted, snapshots),
        "limitations": "60 synthetic cases, one prompt-tuning notice holdout. Scripted extraction is not a measurement of live model quality.",
    }
    report(ROOT / "evals/results-fixtures.json", result)
    if result["fixed_criteria_matching"]["all"]["correct"] != 60:
        raise AssertionError("Gold matching cases failed.")
    print(
        json.dumps(
            {k: v["all"] for k, v in result.items() if isinstance(v, dict) and "all" in v}, indent=2
        )
    )
    return result


def refresh() -> None:
    from recall.cli import paths

    store, _ = paths()
    outcomes = []
    for notice in URLS:
        try:
            found = discover(BRANDS[notice])
            if notice not in found:
                raise ValueError("Notice absent from bounded discovery response.")
            snapshot = parse_html(notice, fetch(URLS[notice]))
            if not snapshot.complete:
                raise ValueError("Notice details incomplete.")
            # Refresh stages a new immutable version without promoting unreviewed criteria.
            store.snapshot(snapshot, None)
            folder = Path("runtime/refreshed")
            folder.mkdir(parents=True, exist_ok=True)
            file = folder / f"{notice}-{snapshot.source_hash[:12]}.json"
            file.write_text(snapshot.model_dump_json(indent=2))
            status, detail = "retrieved", str(file)
            outcomes.append(
                {
                    "notice_id": notice,
                    "status": status,
                    "source_hash": snapshot.source_hash,
                    "retrieved_at": snapshot.retrieved_at,
                    "complete": snapshot.complete,
                }
            )
        except Exception as exc:
            status, detail = "failed", type(exc).__name__
            outcomes.append({"notice_id": notice, "status": status, "error": detail})
        with store.connect() as db:
            db.execute(
                "INSERT INTO refreshes(notice_id,status,detail,created_at) VALUES (?,?,?,?)",
                (notice, status, detail, now()),
            )
    report(ROOT / "evals/results-retrieval.json", outcomes)
    print(json.dumps(outcomes, indent=2))
    if any(o["status"] == "failed" for o in outcomes):
        raise RuntimeError(
            "One or more source refreshes failed; prior investigations were not replaced."
        )


def live_eval() -> None:
    if not __import__("os").getenv("OPENAI_API_KEY"):
        report(
            ROOT / "evals/results-live.json",
            {
                "status": "not_run",
                "reason": "OPENAI_API_KEY not configured",
                "extraction_fields": None,
                "matching": None,
            },
        )
        raise RuntimeError("Live evaluation not run: configure OPENAI_API_KEY locally.")
    snapshots: dict[str, Snapshot] = {}
    extracted: dict[str, Criteria | None] = {}
    accepted: dict[str, Criteria | None] = {}
    errors: dict[str, str] = {}
    store = Store(Path("runtime/live-eval.db"))
    adapter = Adapter(store, "eval-" + uuid.uuid4().hex, True)
    for notice in URLS:
        try:
            if notice not in discover(BRANDS[notice]):
                raise ValueError("Incomplete discovery")
            snapshots[notice] = parse_html(notice, fetch(URLS[notice]))
            extracted[notice] = adapter.extract(snapshots[notice], validate=False)
            candidate = extracted[notice]
            assert candidate is not None
            validate_criteria(candidate, snapshots[notice])
            accepted[notice] = candidate
        except Exception as exc:
            snapshots.setdefault(notice, load_snapshot(notice))
            extracted.setdefault(notice, None)
            accepted[notice], errors[notice] = None, str(exc)
    value: dict[str, Any] = {
        "mode": "live",
        "model": adapter.model,
        "created_at": now(),
        "errors": errors,
        "extraction_fields": extraction_score(extracted, snapshots),
        "matching": comparison(accepted, snapshots),
        "extracted_criteria": {n: c.model_dump() if c else None for n, c in extracted.items()},
        "source_hashes": {n: s.source_hash for n, s in snapshots.items()},
        "limitation": "Small synthetic evaluation; extraction failures yield unresolved findings, not exclusions. Retrieval failure fallback snapshots are labeled by errors.",
    }
    with store.connect() as db:
        value["model_usage"] = [
            dict(r) for r in db.execute("SELECT * FROM model_calls WHERE scope=?", (adapter.scope,))
        ]
    report(ROOT / "evals/results-live.json", value)
    print(
        json.dumps(
            {
                k: v
                for k, v in value.items()
                if k not in {"extracted_criteria", "matching", "model_usage"}
            },
            indent=2,
        )
    )


def live_smoke() -> None:
    store = Store(Path("runtime/live-smoke/app.db"))
    cp = Path("runtime/live-smoke/checkpoints.db")
    data = (DATA / "demo.csv").read_bytes()
    identity, _ = store.create(data, read_inventory(data), "live")
    first = Investigator(store, cp)
    try:
        first.run(identity)
    finally:
        first.close()
    before = store.findings(identity)
    requests = store.requests(identity, True)
    if not requests:
        raise AssertionError("Live workflow did not ask for a missing identifier.")
    second = Investigator(store, cp)
    try:
        second.run(identity, {"request_id": requests[0]["id"], "answer": "1901"})
    finally:
        second.close()
    after = store.findings(identity)
    expected = {
        "SEC-001": "affected_by_notice",
        "SEC-002": "excluded_by_notice",
        "SEC-003": "affected_by_notice",
    }
    actual = {f.stock_id: f.status for f in after if f.notice_id == "23794"}
    assert actual == expected
    assert len(before) == len(after) == 9
    value = {
        "status": "passed",
        "investigation_id": identity,
        "restart_resume": True,
        "before": [f.model_dump() for f in before if f.notice_id == "23794"],
        "after": [f.model_dump() for f in after if f.notice_id == "23794"],
        "mode": "live retrieval and inference",
    }
    report(ROOT / "evals/results-live-smoke.json", value)
    print(json.dumps({k: v for k, v in value.items() if k not in {"before", "after"}}, indent=2))


def execute(command: str) -> None:
    try:
        {
            "eval-fixtures": fixture_eval,
            "eval-live": live_eval,
            "smoke-live": live_smoke,
            "refresh-data": refresh,
        }[command]()
    except Exception as exc:
        if command == "smoke-live":
            report(
                ROOT / "evals/results-live-smoke.json",
                {
                    "status": "not_verified",
                    "error": type(exc).__name__,
                    "reason": str(exc),
                    "live_inference_verified": False,
                },
            )
        raise
