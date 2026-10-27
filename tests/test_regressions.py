"""Regression cases found during the post-delivery review."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

from recall.agent import Investigator
from recall.criteria import EvidenceError, aggregate, match, validate_criteria
from recall.evaluation import as_stock, cases, fixed_criteria
from recall.inventory import InventoryError, read_inventory
from recall.models import Criteria, Evidence, Rule
from recall.sources import DATA, digest, load_snapshot
from recall.store import SourceChangedError, Store


def workflow(tmp_path: Path, data: bytes) -> tuple[Store, Investigator, str]:
    store = Store(tmp_path / "app.db")
    identity, _ = store.create(data, read_inventory(data), "fixture")
    return store, Investigator(store, tmp_path / "checkpoints.db"), identity


def test_one_identifier_answer_resolves_all_notice_requests(tmp_path: Path) -> None:
    data = b"stock_id,brand,model,date_code,location\nS1,Secura,,1901,Shelf\n"
    store, agent, identity = workflow(tmp_path, data)
    try:
        agent.run(identity)
        pending = store.requests(identity, True)
        assert len(pending) == 1
        agent.run(identity, {"request_id": pending[0]["id"], "answer": "SAF-53D"})
        assert store.investigation(identity)["status"] == "complete"
        assert not store.requests(identity, True)
        assert len(store.findings(identity)) == 3
    finally:
        agent.close()


def test_out_of_order_answer_does_not_poison_checkpoint(tmp_path: Path) -> None:
    data = b"stock_id,brand,model,date_code,location\nS1,Secura,SAF-53D,,A\nS2,Secura,SAF-53D,,B\n"
    store, agent, identity = workflow(tmp_path, data)
    try:
        agent.run(identity)
        state = agent.graph.get_state({"configurable": {"thread_id": identity}})
        active_id = state.values["action"]["target"]
        other = next(r for r in store.requests(identity, True) if r["id"] != active_id)
        with pytest.raises(ValueError):
            agent.run(identity, {"request_id": other["id"], "answer": "1901"})
        assert store.investigation(identity)["status"] == "waiting_for_identifier"
        agent.run(identity, {"request_id": active_id, "answer": "1901"})
        # Replaying the first answer while another question is active must be a no-op.
        agent.run(identity, {"request_id": active_id, "answer": "1901"})
        assert store.investigation(identity)["status"] == "waiting_for_identifier"
        agent.run(identity, {"request_id": other["id"], "answer": "1901"})
        assert store.investigation(identity)["status"] == "complete"
        assert len(store.findings(identity)) == 6
    finally:
        agent.close()


def test_source_changed_repeated_resume_never_revives_findings(tmp_path: Path) -> None:
    store, agent, identity = workflow(tmp_path, (DATA / "demo.csv").read_bytes())
    try:
        agent.run(identity)
        request = store.requests(identity, True)[0]
        changed = load_snapshot("23794").model_copy(deep=True)
        changed.text += " Updated recall condition."
        changed.source_hash = digest(changed.text.encode())
        store.snapshot(changed, None)
        for _ in range(2):
            with pytest.raises(ValueError):
                agent.run(identity, {"request_id": request["id"], "answer": "1901"})
            assert store.investigation(identity)["status"] == "source_changed"
            assert all(not f.valid for f in store.findings(identity) if f.notice_id == "23794")
    finally:
        agent.close()


def test_rejected_brand_cannot_authorize_a_notice_exclusion() -> None:
    snapshot = load_snapshot("23794")
    criteria = fixed_criteria("23794", snapshot)
    criteria.brand = "Amazon"  # Appears in the source, but is a retailer, not this product's brand.
    stock = as_stock(cases()[1]["stock"])
    stock.brand = stock.brand.model_copy(update={"value": "Amazon"})
    with pytest.raises(EvidenceError):
        validate_criteria(criteria, snapshot)
    assert match(stock, snapshot, criteria).status == "needs_information"


@pytest.mark.parametrize("data", [b'"stock_id,brand,model,location', b"a" * 140_000])
def test_malformed_or_oversized_header_is_an_inventory_error(data: bytes) -> None:
    with pytest.raises(InventoryError):
        read_inventory(data)


def test_invalidated_notice_keeps_inventory_aggregate_unresolved() -> None:
    snapshot = load_snapshot("23794")
    finding = match(as_stock(cases()[1]["stock"]), snapshot, fixed_criteria("23794", snapshot))
    invalid = finding.model_copy(update={"notice_id": "24159", "valid": False})
    assert aggregate([finding, invalid]) == "needs_information"
    affected = finding.model_copy(update={"status": "affected_by_notice"})
    assert aggregate([affected, invalid]) == "affected_by_notice"


def test_invalid_answer_does_not_enter_checkpoint(tmp_path: Path) -> None:
    store, agent, identity = workflow(tmp_path, (DATA / "demo.csv").read_bytes())
    try:
        agent.run(identity)
        request = agent.pending_request(identity)
        assert request
        for invalid in ("19O1", "", "1901 / 1902"):
            with pytest.raises(ValueError):
                agent.run(identity, {"request_id": request["id"], "answer": invalid})
            assert store.investigation(identity)["status"] == "waiting_for_identifier"
        agent.run(identity, {"request_id": request["id"], "answer": "1901"})
        assert store.investigation(identity)["status"] == "complete"
    finally:
        agent.close()


def test_changed_source_blocks_writes_even_before_findings(tmp_path: Path) -> None:
    store, agent, identity = workflow(tmp_path, (DATA / "demo.csv").read_bytes())
    try:
        source = load_snapshot("23794")
        criteria = fixed_criteria("23794", source)
        store.snapshot(source, criteria)
        store.bind(identity, source, criteria)
        finding = match(store.stocks(identity)[0], source, criteria)
        changed = source.model_copy(deep=True)
        changed.text += " Changed before comparison."
        changed.source_hash = digest(changed.text.encode())
        store.snapshot(changed, None)
        assert store.investigation(identity)["status"] == "source_changed"
        with pytest.raises(SourceChangedError):
            store.save_finding(identity, finding)
        with pytest.raises(SourceChangedError):
            store.draft(identity, "Stale draft")
        with pytest.raises(SourceChangedError):
            store.status(identity, "complete")
        assert not store.findings(identity)
        with store.connect() as db:
            assert db.execute("SELECT count(*) FROM drafts").fetchone()[0] == 0
    finally:
        agent.close()


@pytest.mark.parametrize(
    "data",
    [
        b'"stock_id,brand,model,location',
        b"a" * 140_000,
        b"stock_id,brand,model,location\nA,Secura,SAF-53D,A\x00B",
    ],
)
def test_bad_csv_returns_422(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data: bytes) -> None:
    import recall.web as web

    store, agent, _ = workflow(tmp_path, (DATA / "demo.csv").read_bytes())
    monkeypatch.setattr(web, "store", store)
    monkeypatch.setattr(web, "agent", agent)
    with TestClient(web.app) as client:
        response = client.post("/investigations", files={"file": ("bad.csv", data, "text/csv")})
        assert response.status_code == 422
        assert client.get("/health").status_code == 200


def test_http_out_of_order_answer_preserves_active_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import recall.web as web

    data = b"stock_id,brand,model,date_code,location\nS1,Secura,SAF-53D,,A\nS2,Secura,SAF-53D,,B\n"
    store, agent, identity = workflow(tmp_path, data)
    monkeypatch.setattr(web, "store", store)
    monkeypatch.setattr(web, "agent", agent)
    with TestClient(web.app) as client:
        agent.run(identity)
        body = client.get(f"/api/investigations/{identity}").json()
        active = body["active_request"]["id"]
        other = next(r["id"] for r in body["requests"] if r["id"] != active)
        assert client.get(f"/investigations/{identity}").text.count('class="answer-form"') == 1
        response = client.post(
            f"/investigations/{identity}/answer", data={"request_id": other, "answer": "1901"}
        )
        assert response.status_code == 409
        assert agent.pending_request(identity)["id"] == active
        response = client.post(
            f"/investigations/{identity}/answer", data={"request_id": active, "answer": "1901"}
        )
        assert response.status_code == 200
        assert agent.pending_request(identity)["id"] == other


def test_upload_work_does_not_block_the_http_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import recall.web as web

    store, agent, identity = workflow(tmp_path, (DATA / "demo.csv").read_bytes())
    monkeypatch.setattr(web, "store", store)
    monkeypatch.setattr(web, "agent", agent)
    entered, release = Event(), Event()

    def slow_start(data: bytes, mode: str) -> str:
        entered.set()
        if not release.wait(15):
            raise RuntimeError("Test did not release upload work")
        return identity

    monkeypatch.setattr(web, "start", slow_start)
    with TestClient(web.app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        upload = executor.submit(
            client.post,
            "/investigations",
            files={"file": ("stock.csv", b"test", "text/csv")},
            follow_redirects=False,
        )
        try:
            assert entered.wait(5)
            health = executor.submit(client.get, "/health")
            assert health.result(timeout=5).status_code == 200
            assert not upload.done()
        finally:
            release.set()
        assert upload.result(timeout=5).status_code == 303


@pytest.mark.parametrize(
    ("quote", "lower", "upper"),
    [
        ("Serial numbers from 000100 through 000200 are included.", "100", "200"),
        ("Serial numbers 000100 and 000200 are included.", "000100", "000200"),
    ],
)
def test_serial_bounds_need_an_explicit_complete_range(quote: str, lower: str, upper: str) -> None:
    snapshot = load_snapshot("23794").model_copy(deep=True)
    snapshot.notice_id = "synthetic-range"
    snapshot.text = "Description: Test model RANGE-1. " + quote
    snapshot.source_hash = digest(snapshot.text.encode())
    evidence = Evidence(start=0, end=len(snapshot.text), quote=snapshot.text)
    criteria = Criteria(
        brand="Test",
        complete=True,
        root=Rule(
            op="all",
            evidence=evidence,
            children=[
                Rule(op="model_set", values=["RANGE-1"], evidence=evidence),
                Rule(op="serial_range", lower=lower, upper=upper, evidence=evidence),
            ],
        ),
    )
    with pytest.raises(EvidenceError, match="serial ranges"):
        validate_criteria(criteria, snapshot)
