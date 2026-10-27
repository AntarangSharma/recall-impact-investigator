from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from recall.agent import Investigator
from recall.cli import paths
from recall.criteria import aggregate
from recall.inventory import MAX_BYTES, InventoryError, export_csv, read_inventory
from recall.llm import load_local_env
from recall.models import Identifier, display_guard
from recall.sources import BRANDS, DATA, stale
from recall.store import AnswerError, SourceChangedError

ROOT = Path(__file__).parent
load_local_env()
store, checkpoints = paths()
agent = Investigator(store, checkpoints)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    agent.close()


app = FastAPI(title="Product Recall Impact Investigator", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")


def source_quote(text: str) -> str:
    try:
        return display_guard(text)
    except ValueError:
        return "Quote withheld from display because it contains forbidden punctuation. Inspect the stored source JSON unchanged."


templates.env.globals["source_quote"] = source_quote


def view(identity: str) -> dict[str, Any]:
    try:
        investigation = store.investigation(identity)
    except KeyError as exc:
        raise HTTPException(404, "Investigation not found.") from exc
    stocks, findings, sources = (
        store.stocks(identity),
        store.findings(identity),
        store.sources(identity),
    )
    rows = []
    for stock in stocks:
        related = [f for f in findings if f.stock_id == stock.stock_id]
        primary = next(
            (
                f
                for f in related
                if BRANDS[f.notice_id].upper() == (stock.brand.value or "").strip().upper()
            ),
            None,
        )
        rows.append(
            {
                "stock": stock,
                "findings": related,
                "aggregate": aggregate(related),
                "primary": primary,
            }
        )
    with store.connect() as db:
        draft = db.execute(
            "SELECT body FROM drafts WHERE investigation_id=?", (identity,)
        ).fetchone()
        events = [
            dict(r)
            for r in db.execute(
                "SELECT tool,detail,created_at FROM events WHERE investigation_id=? "
                "ORDER BY created_at,event_key",
                (identity,),
            )
        ]
    return {
        "investigation": investigation,
        "rows": rows,
        "requests": store.requests(identity, True),
        "active_request": agent.pending_request(identity),
        "sources": [{"snapshot": s, "stale": stale(s)} for s, _ in sources],
        "draft": draft[0] if draft else None,
        "events": events,
    }


@app.get("/health")
def health() -> dict[str, str]:
    with store.connect() as db:
        db.execute("SELECT 1").fetchone()
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> Response:
    with store.connect() as db:
        investigations = [
            dict(r)
            for r in db.execute("SELECT * FROM investigations ORDER BY created_at DESC LIMIT 20")
        ]
    return templates.TemplateResponse(
        request=request, name="home.html", context={"investigations": investigations}
    )


def start(data: bytes, mode: str) -> str:
    if mode not in {"fixture", "live"}:
        raise HTTPException(422, "Mode must be fixture or live.")
    try:
        stocks = read_inventory(data)
    except InventoryError as exc:
        raise HTTPException(422, str(exc)) from exc
    identity, _ = store.create(data, stocks, mode)
    try:
        agent.run(identity)
    except Exception:
        # The persisted failure is reviewable at the investigation page.
        pass
    return identity


@app.post("/demo")
def demo() -> Response:
    return RedirectResponse(
        "/investigations/" + start((DATA / "demo.csv").read_bytes(), "fixture"), 303
    )


@app.get("/demo.csv")
def demo_csv() -> Response:
    return Response((DATA / "demo.csv").read_text(), media_type="text/csv")


@app.post("/investigations")
async def upload(
    file: Annotated[UploadFile, File()], mode: Annotated[str, Form()] = "fixture"
) -> Response:
    data = await file.read(MAX_BYTES + 1)
    await file.close()
    identity = await run_in_threadpool(start, data, mode)
    return RedirectResponse("/investigations/" + identity, 303)


@app.get("/investigations/{identity}", response_class=HTMLResponse)
def investigation(request: Request, identity: str) -> Response:
    return templates.TemplateResponse(
        request=request, name="investigation.html", context=view(identity)
    )


@app.get("/api/investigations/{identity}")
def api_investigation(identity: str) -> dict[str, Any]:
    return view(identity)


@app.post("/investigations/{identity}/answer")
def answer(
    identity: str, request_id: Annotated[str, Form()], answer: Annotated[str, Form()]
) -> Response:
    data = view(identity)
    request = next((r for r in store.requests(identity) if r["id"] == request_id), None)
    if not request or Identifier.parse(answer, request["field"]).state != "present":
        raise HTTPException(422, "Enter a valid identifier for the requested field.")
    if data["investigation"]["status"] == "source_changed":
        raise HTTPException(409, "Source changed; review a new investigation before resuming.")
    try:
        agent.run(identity, {"request_id": request_id, "answer": answer})
    except (AnswerError, SourceChangedError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return RedirectResponse("/investigations/" + identity, 303)


@app.get("/investigations/{identity}/export/{kind}")
def export(identity: str, kind: str) -> Response:
    data = view(identity)
    if kind == "findings.csv":
        locations = {s.stock_id: s.location for s in store.stocks(identity)}
        text = export_csv(
            [
                "stock_id",
                "location",
                "notice_id",
                "source_hash",
                "status",
                "valid",
                "reason",
                "source_url",
                "snapshot_date",
            ],
            [
                [
                    f.stock_id,
                    locations[f.stock_id],
                    f.notice_id,
                    f.source_hash,
                    f.status,
                    f.valid,
                    f.reason,
                    next(s.url for s, _ in store.sources(identity) if s.notice_id == f.notice_id),
                    next(
                        s.retrieved_at
                        for s, _ in store.sources(identity)
                        if s.notice_id == f.notice_id
                    ),
                ]
                for f in store.findings(identity)
            ],
        )
        media = "text/csv"
    elif kind == "evidence.json":
        payload = {
            "investigation": data["investigation"],
            "findings": [f.model_dump() for f in store.findings(identity)],
            "snapshots": [s.model_dump() for s, _ in store.sources(identity)],
            "criteria": [c.model_dump() if c else None for _, c in store.sources(identity)],
            "requests": store.requests(identity),
            "coverage": "Three supported US CPSC notices only; absence is not proof of safety.",
        }
        text, media = json.dumps(payload, indent=2, ensure_ascii=True), "application/json"
    elif kind == "inquiry.txt":
        if not data["draft"]:
            raise HTTPException(
                409, "Draft is not available until the current clarification run completes."
            )
        text, media = data["draft"], "text/plain"
    else:
        raise HTTPException(404, "Export not found.")
    return Response(
        text,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{kind}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
