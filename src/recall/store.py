from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from recall.models import Criteria, Finding, Snapshot, Stock
from recall.sources import URLS, digest, load_snapshot


def now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS investigations (
              id TEXT PRIMARY KEY, upload_hash TEXT UNIQUE, mode TEXT, status TEXT,
              created_at TEXT, error TEXT, resumed INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS inventory (
              investigation_id TEXT, stock_id TEXT, payload TEXT,
              PRIMARY KEY(investigation_id,stock_id));
            CREATE TABLE IF NOT EXISTS snapshots (
              notice_id TEXT, source_hash TEXT, payload TEXT, criteria TEXT,
              PRIMARY KEY(notice_id,source_hash));
            CREATE TABLE IF NOT EXISTS latest_snapshots (notice_id TEXT PRIMARY KEY, source_hash TEXT);
            CREATE TABLE IF NOT EXISTS investigation_sources (
              investigation_id TEXT, notice_id TEXT, source_hash TEXT,
              criteria TEXT, retrieved_at TEXT, PRIMARY KEY(investigation_id,notice_id));
            CREATE TABLE IF NOT EXISTS findings (
              investigation_id TEXT, stock_id TEXT, notice_id TEXT, payload TEXT, revision INTEGER,
              PRIMARY KEY(investigation_id,stock_id,notice_id));
            CREATE TABLE IF NOT EXISTS requests (
              id TEXT PRIMARY KEY, investigation_id TEXT, stock_id TEXT, notice_id TEXT,
              field TEXT, question TEXT, answer TEXT, status TEXT,
              UNIQUE(investigation_id,stock_id,notice_id,field));
            CREATE TABLE IF NOT EXISTS events (
              investigation_id TEXT, event_key TEXT, tool TEXT, detail TEXT, created_at TEXT,
              PRIMARY KEY(investigation_id,event_key));
            CREATE TABLE IF NOT EXISTS drafts (
              investigation_id TEXT PRIMARY KEY, body TEXT, source_hashes TEXT);
            CREATE TABLE IF NOT EXISTS refreshes (
              id INTEGER PRIMARY KEY, notice_id TEXT, status TEXT, detail TEXT, created_at TEXT);
            """)
            columns = {r[1] for r in db.execute("PRAGMA table_info(investigation_sources)")}
            if "criteria" not in columns:
                db.execute("ALTER TABLE investigation_sources ADD COLUMN criteria TEXT")
                db.execute(
                    "UPDATE investigation_sources SET criteria=(SELECT criteria FROM snapshots s "
                    "WHERE s.notice_id=investigation_sources.notice_id "
                    "AND s.source_hash=investigation_sources.source_hash)"
                )
            if "retrieved_at" not in columns:
                db.execute("ALTER TABLE investigation_sources ADD COLUMN retrieved_at TEXT")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    def create(self, data: bytes, stocks: list[Stock], mode: str) -> tuple[str, bool]:
        with self.connect() as db:
            versions = dict(db.execute("SELECT notice_id,source_hash FROM latest_snapshots"))
            coverage = ":".join(versions.get(n, load_snapshot(n).source_hash) for n in sorted(URLS))
            upload_hash = digest(data + mode.encode() + coverage.encode())
            identity = upload_hash[:24]
            result = db.execute(
                "INSERT OR IGNORE INTO investigations VALUES (?,?,?,?,?,?,0)",
                (identity, upload_hash, mode, "running", now(), None),
            )
            fresh = result.rowcount == 1
            if fresh:
                db.executemany(
                    "INSERT INTO inventory VALUES (?,?,?)",
                    [(identity, s.stock_id, s.model_dump_json()) for s in stocks],
                )
        return identity, fresh

    def investigation(self, identity: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM investigations WHERE id=?", (identity,)).fetchone()
        if not row:
            raise KeyError(identity)
        return dict(row)

    def status(self, identity: str, status: str, error: str | None = None) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE investigations SET status=?,error=? WHERE id=?", (status, error, identity)
            )

    def stocks(self, identity: str, apply_answers: bool = True) -> list[Stock]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM inventory WHERE investigation_id=? ORDER BY stock_id",
                (identity,),
            ).fetchall()
            answers = db.execute(
                "SELECT stock_id,field,answer FROM requests WHERE investigation_id=? "
                "AND status='answered'",
                (identity,),
            ).fetchall()
        stocks = [Stock.model_validate_json(r[0]) for r in rows]
        if apply_answers:
            from recall.models import Identifier

            for stock in stocks:
                for a in answers:
                    if a["stock_id"] == stock.stock_id:
                        setattr(stock, a["field"], Identifier.parse(a["answer"], a["field"]))
        return stocks

    def snapshot(self, snapshot: Snapshot, criteria: Criteria | None, promote: bool = True) -> None:
        with self.connect() as db:
            old = db.execute(
                "SELECT source_hash FROM latest_snapshots WHERE notice_id=?", (snapshot.notice_id,)
            ).fetchone()
            db.execute(
                "INSERT INTO snapshots VALUES (?,?,?,?) ON CONFLICT(notice_id,source_hash) "
                "DO NOTHING",
                (
                    snapshot.notice_id,
                    snapshot.source_hash,
                    snapshot.model_dump_json(),
                    criteria.model_dump_json() if criteria else None,
                ),
            )
            if promote or not old:
                db.execute(
                    "INSERT OR REPLACE INTO latest_snapshots VALUES (?,?)",
                    (snapshot.notice_id, snapshot.source_hash),
                )
            if promote and old and old[0] != snapshot.source_hash:
                rows = db.execute(
                    "SELECT investigation_id,stock_id,payload FROM findings WHERE notice_id=?",
                    (snapshot.notice_id,),
                ).fetchall()
                for row in rows:
                    finding = Finding.model_validate_json(row["payload"])
                    if finding.source_hash != snapshot.source_hash:
                        finding.valid = False
                        db.execute(
                            "UPDATE findings SET payload=? WHERE investigation_id=? AND stock_id=? "
                            "AND notice_id=?",
                            (finding.model_dump_json(), row[0], row[1], snapshot.notice_id),
                        )
                        db.execute(
                            "UPDATE investigations SET status='source_changed' WHERE id=?",
                            (row[0],),
                        )
                        db.execute("DELETE FROM drafts WHERE investigation_id=?", (row[0],))

    def bind(self, identity: str, snapshot: Snapshot, criteria: Criteria | None) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO investigation_sources(investigation_id,notice_id,source_hash,criteria,retrieved_at) VALUES (?,?,?,?,?)",
                (
                    identity,
                    snapshot.notice_id,
                    snapshot.source_hash,
                    criteria.model_dump_json() if criteria else None,
                    snapshot.retrieved_at,
                ),
            )

    def sources(self, identity: str) -> list[tuple[Snapshot, Criteria | None]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT s.payload,i.criteria,i.retrieved_at FROM snapshots s JOIN investigation_sources i "
                "ON s.notice_id=i.notice_id AND s.source_hash=i.source_hash "
                "WHERE i.investigation_id=? ORDER BY i.notice_id",
                (identity,),
            ).fetchall()
        return [
            (
                Snapshot.model_validate_json(r[0]).model_copy(update={"retrieved_at": r[2]})
                if r[2]
                else Snapshot.model_validate_json(r[0]),
                Criteria.model_validate_json(r[1]) if r[1] else None,
            )
            for r in rows
        ]

    def save_finding(self, identity: str, finding: Finding) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO findings VALUES (?,?,?,?,1) ON CONFLICT(investigation_id,stock_id,notice_id) "
                "DO UPDATE SET payload=excluded.payload,revision=revision+1",
                (identity, finding.stock_id, finding.notice_id, finding.model_dump_json()),
            )

    def findings(self, identity: str) -> list[Finding]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM findings WHERE investigation_id=? ORDER BY stock_id,notice_id",
                (identity,),
            ).fetchall()
        return [Finding.model_validate_json(r[0]) for r in rows]

    def event(self, identity: str, key: str, tool: str, detail: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)",
                (identity, key, tool, detail, now()),
            )

    def requests(self, identity: str, pending: bool = False) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM requests WHERE investigation_id=? "
                + ("AND status='pending' " if pending else "")
                + "ORDER BY id",
                (identity,),
            ).fetchall()
        return [dict(r) for r in rows]

    def request(self, identity: str, finding: Finding, field: str) -> str:
        key = digest(f"{identity}:{finding.stock_id}:{finding.notice_id}:{field}".encode())[:24]
        location = (
            "the silver label on the bottom of the unit"
            if field == "date_code"
            else "the unit label"
        )
        question = (
            f"What is the {field.replace('_', ' ')} for stock {finding.stock_id} on {location}?"
        )
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO requests VALUES (?,?,?,?,?,?,NULL,'pending')",
                (key, identity, finding.stock_id, finding.notice_id, field, question),
            )
        return key

    def answer(self, identity: str, request_id: str, answer: str) -> dict[str, Any]:
        from recall.models import Identifier

        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM requests WHERE id=? AND investigation_id=?", (request_id, identity)
            ).fetchone()
            if not row:
                raise ValueError("Unknown identifier request.")
            if Identifier.parse(answer, row["field"]).state != "present":
                raise ValueError("Answer must be a valid identifier from the unit label.")
            if row["status"] == "answered" and row["answer"] != answer:
                raise ValueError("This request was already answered differently.")
            db.execute(
                "UPDATE requests SET answer=?,status='answered' WHERE id=?", (answer, request_id)
            )
            db.execute(
                "UPDATE investigations SET resumed=1,status='running' WHERE id=?", (identity,)
            )
        return dict(row)

    def draft(self, identity: str, body: str) -> None:
        hashes = [s.source_hash for s, _ in self.sources(identity)]
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO drafts VALUES (?,?,?)", (identity, body, json.dumps(hashes))
            )
