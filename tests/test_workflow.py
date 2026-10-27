from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from recall.agent import Investigator
from recall.inventory import read_inventory
from recall.llm import Adapter, Choice, ModelError
from recall.sources import DATA, digest, load_snapshot
from recall.store import Store


@pytest.fixture
def workflow(tmp_path: Path) -> tuple[Store, Path, str]:
    store, cp = Store(tmp_path / "app.db"), tmp_path / "checkpoints.db"
    data = (DATA / "demo.csv").read_bytes()
    identity, _ = store.create(data, read_inventory(data), "fixture")
    return store, cp, identity


def test_restart_resume_same_investigation_and_affected_only(
    workflow: tuple[Store, Path, str],
) -> None:
    store, cp, identity = workflow
    first = Investigator(store, cp)
    first.run(identity)
    assert store.investigation(identity)["status"] == "waiting_for_identifier"
    request = store.requests(identity, True)[0]
    assert "SEC-003" in request["question"] and "date code" in request["question"]
    with store.connect() as db:
        before = {
            (r[0], r[1]): r[2]
            for r in db.execute("SELECT stock_id,notice_id,revision FROM findings")
        }
    first.close()
    # A separate Python process proves persistence beyond object recreation.
    code = """from pathlib import Path
from recall.store import Store
from recall.agent import Investigator
import sys,json
s=Store(Path(sys.argv[1])); a=Investigator(s,Path(sys.argv[2])); a.run(sys.argv[3],json.loads(sys.argv[4])); a.close()
"""
    subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(store.path),
            str(cp),
            identity,
            json.dumps({"request_id": request["id"], "answer": "1901"}),
        ],
        check=True,
    )
    assert store.investigation(identity)["status"] == "complete"
    assert len(store.findings(identity)) == 9
    assert (
        next(
            f
            for f in store.findings(identity)
            if f.stock_id == "SEC-003" and f.notice_id == "23794"
        ).status
        == "affected_by_notice"
    )
    with store.connect() as db:
        after = {
            (r[0], r[1]): r[2]
            for r in db.execute("SELECT stock_id,notice_id,revision FROM findings")
        }
    assert {k for k in before if before[k] != after[k]} == {("SEC-003", "23794")}
    assert store.stocks(identity, False)[2].date_code.state == "empty"
    assert store.stocks(identity)[2].date_code.value == "1901"
    repeat = Investigator(store, cp)
    repeat.run(identity, {"request_id": request["id"], "answer": "1901"})
    repeat.close()
    assert len(store.findings(identity)) == 9


def test_duplicate_submissions_are_stable(workflow: tuple[Store, Path, str]) -> None:
    store, cp, identity = workflow
    data = (DATA / "demo.csv").read_bytes()
    repeated, fresh = store.create(data, read_inventory(data), "fixture")
    assert repeated == identity and not fresh
    a = Investigator(store, cp)
    a.run(identity)
    a.run(identity)
    a.close()
    assert len(store.findings(identity)) == 9 and len(store.requests(identity, True)) == 1


def test_changed_snapshot_invalidates_and_retains_history(
    workflow: tuple[Store, Path, str],
) -> None:
    store, cp, identity = workflow
    a = Investigator(store, cp)
    a.run(identity)
    a.close()
    source = load_snapshot("23794")
    changed = source.model_copy(deep=True)
    changed.text += " Corrected source text."
    changed.source_hash = digest(changed.text.encode())
    store.snapshot(changed, None)
    assert all(not f.valid for f in store.findings(identity) if f.notice_id == "23794")
    assert store.investigation(identity)["status"] == "source_changed"
    assert next(s for s, _ in store.sources(identity) if s.notice_id == "23794").text == source.text
    with store.connect() as db:
        assert (
            db.execute("SELECT count(*) FROM snapshots WHERE notice_id='23794'").fetchone()[0] == 2
        )


def test_failed_live_retrieval_never_overwrites_previous(
    workflow: tuple[Store, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, cp, identity = workflow
    a = Investigator(store, cp)
    a.run(identity)
    a.close()
    before = [f.model_dump() for f in store.findings(identity)]
    data = (DATA / "demo.csv").read_bytes()
    live, _ = store.create(data, read_inventory(data), "live")
    monkeypatch.setattr(
        "recall.agent.discover", lambda _: (_ for _ in ()).throw(ValueError("Source offline"))
    )
    # Selection is scripted so this test isolates retrieval, not missing credentials.
    monkeypatch.setattr(Adapter, "choose", lambda self, choices, observed: choices[0])
    b = Investigator(store, cp)
    with pytest.raises(ValueError):
        b.run(live)
    b.close()
    assert store.investigation(live)["status"] == "failed"
    assert not store.findings(live)
    assert [f.model_dump() for f in store.findings(identity)] == before


def test_invalid_answer_and_duplicate_answer(workflow: tuple[Store, Path, str]) -> None:
    store, cp, identity = workflow
    a = Investigator(store, cp)
    a.run(identity)
    r = store.requests(identity, True)[0]
    with pytest.raises(ValueError):
        store.answer(identity, r["id"], "19O1")
    a.run(identity, {"request_id": r["id"], "answer": "1901"})
    with pytest.raises(ValueError):
        store.answer(identity, r["id"], "1902")
    a.close()


def test_api_exports_and_formula_injection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import recall.web as web

    store, cp = Store(tmp_path / "web.db"), tmp_path / "cp.db"
    a = Investigator(store, cp)
    monkeypatch.setattr(web, "store", store)
    monkeypatch.setattr(web, "agent", a)
    with TestClient(web.app) as client:
        data = (DATA / "demo.csv").read_bytes().replace(b"Stockroom A", b"=HYPERLINK(1)")
        response = client.post(
            "/investigations",
            files={"file": ("stock.csv", data, "text/csv")},
            data={"mode": "fixture"},
        )
        assert response.status_code == 200
        identity = str(response.url).split("/")[-1]
        body = client.get(f"/api/investigations/{identity}").json()
        assert len(body["rows"]) == 3 and len(body["requests"]) == 1
        r = body["requests"][0]
        assert (
            client.post(
                f"/investigations/{identity}/answer", data={"request_id": r["id"], "answer": "1901"}
            ).status_code
            == 200
        )
        exported = client.get(f"/investigations/{identity}/export/findings.csv")
        rows = list(csv.DictReader(io.StringIO(exported.text)))
        assert len(rows) == 9
        assert any(r["location"].startswith("'=HYPERLINK") for r in rows)
        evidence = client.get(f"/investigations/{identity}/export/evidence.json").json()
        assert len(evidence["snapshots"]) == 3 and len(evidence["findings"]) == 9
        for finding in evidence["findings"]:
            source = next(
                s for s in evidence["snapshots"] if s["source_hash"] == finding["source_hash"]
            )
            for span in finding["evidence"]:
                assert source["text"][span["start"] : span["end"]] == span["quote"]
        assert "DRAFT ONLY" in client.get(f"/investigations/{identity}/export/inquiry.txt").text
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/api/investigations/absent").status_code == 404
        assert (
            client.post(
                "/investigations", files={"file": ("bad.csv", b"bad", "text/csv")}
            ).status_code
            == 422
        )


class FakeResponses:
    def __init__(self, kind: str):
        self.kind = kind

    def parse(self, **kwargs: Any) -> Any:
        from types import SimpleNamespace

        if self.kind == "malformed":
            raise ValueError("bad schema")
        return SimpleNamespace(output_parsed=None, status="completed", usage=None)


@pytest.mark.parametrize("kind", ["refusal", "malformed"])
def test_live_adapter_handles_refusal_and_malformed(tmp_path: Path, kind: str) -> None:
    from types import SimpleNamespace

    a = Adapter(Store(tmp_path / "a.db"), "test")
    a.client = SimpleNamespace(responses=FakeResponses(kind))  # type: ignore[assignment]
    with pytest.raises(ModelError):
        a.parse(Choice, "test", "prompt", {}, 100)
    with a.store.connect() as db:
        assert db.execute("SELECT status FROM model_calls").fetchone()[0] == "failed"


def test_budget_exhaustion_is_persistent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    store = Store(tmp_path / "a.db")
    monkeypatch.setenv("OPENAI_MAX_CALLS", "0")
    for _ in range(2):
        a = Adapter(store, "same")
        a.client = SimpleNamespace(responses=FakeResponses("refusal"))  # type: ignore[assignment]
        with pytest.raises(ModelError, match="budget exhausted"):
            a.parse(Choice, "test", "p", {}, 100)


def test_same_snapshot_refresh_does_not_replace_bound_criteria(
    workflow: tuple[Store, Path, str],
) -> None:
    store, cp, identity = workflow
    a = Investigator(store, cp)
    a.run(identity)
    a.close()
    before = [c.model_dump() for _, c in store.sources(identity) if c]
    store.snapshot(load_snapshot("23794"), None)
    after = [c.model_dump() for _, c in store.sources(identity) if c]
    assert before == after
    assert len(after) == 3


def test_replay_after_answer_write_before_checkpoint(workflow: tuple[Store, Path, str]) -> None:
    store, cp, identity = workflow
    a = Investigator(store, cp)
    a.run(identity)
    request = store.requests(identity, True)[0]
    store.answer(identity, request["id"], "1901")
    a.close()
    resumed = Investigator(store, cp)
    resumed.run(identity, {"request_id": request["id"], "answer": "1901"})
    resumed.close()
    assert store.investigation(identity)["status"] == "complete"
    assert (
        next(
            f
            for f in store.findings(identity)
            if f.stock_id == "SEC-003" and f.notice_id == "23794"
        ).status
        == "affected_by_notice"
    )


def test_model_explanations_cannot_contradict_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from recall.llm import ExplainedFact, Narrative

    adapter = Adapter(Store(tmp_path / "a.db"), "explanation", live=True)
    monkeypatch.setattr(
        adapter,
        "parse",
        lambda *a, **k: Narrative(
            introduction="Please review these notice-specific inventory findings.",
            explanations=[
                ExplainedFact(fact_id="unit:notice", explanation="Every product is safe.")
            ],
            closing="Please advise on the applicable recall process for these units.",
        ),
    )
    with pytest.raises(ModelError, match="unsupported"):
        adapter.narrative(
            {"unit:notice": "Validated affected finding"}, {"unit:notice": "affected_by_notice"}
        )
