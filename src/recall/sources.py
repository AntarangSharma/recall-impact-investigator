from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from recall.models import Snapshot

DATA = Path(__file__).resolve().parents[2] / "data"
URLS = {
    "23794": "https://www.cpsc.gov/Recalls/2023/Secura-Recalls-Air-Fryers-Due-to-Fire-and-Burn-Hazards-Recall-Alert",
    "23130": "https://www.cpsc.gov/Recalls/2023/Two-Million-COSORI-Air-Fryers-Recalled-by-Atekcity-Due-to-Fire-and-Burn-Hazards",
    "24159": "https://www.cpsc.gov/Recalls/2024/Best-Buy-Recalls-Insignia-Air-Fryers-and-Air-Fryer-Ovens-Due-to-Fire-Burn-and-Laceration-Hazards",
}
BRANDS = {"23794": "Secura", "23130": "Cosori", "24159": "Insignia"}
API = "https://www.saferproducts.gov/RestWebServices/Recall"
MAX_RESPONSE = 2_000_000


class SourceError(ValueError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_url(url: str) -> str:
    p = urlsplit(url)
    if (
        p.scheme != "https"
        or p.username
        or p.password
        or p.port not in (None, 443)
        or p.fragment
        or p.hostname not in {"www.cpsc.gov", "www.saferproducts.gov"}
    ):
        raise SourceError("Source destination is not allowed.")
    if p.hostname == "www.saferproducts.gov" and p.path.lower() != "/restwebservices/recall":
        raise SourceError("Only the documented recall API is allowed on saferproducts.gov.")
    if p.hostname == "www.cpsc.gov" and not p.path.startswith("/Recalls/"):
        raise SourceError("Only CPSC recall pages are allowed.")
    return url


def fetch(url: str, client: httpx.Client | None = None) -> bytes:
    started = time.monotonic()
    own = client is None
    client = client or httpx.Client(timeout=15, follow_redirects=False, trust_env=False)
    try:
        for _ in range(4):
            validate_url(url)
            with client.stream(
                "GET",
                url,
                headers={"User-Agent": "curl/8.7.1"},
                follow_redirects=False,
                timeout=15,
            ) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get("location", ""))
                    validate_url(url)
                    continue
                response.raise_for_status()
                kind = response.headers.get("content-type", "").lower()
                if not any(t in kind for t in ("text/html", "application/json", "text/json")):
                    raise SourceError("Unsupported source content type.")
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE or time.monotonic() - started > 30:
                        raise SourceError("Source response size or elapsed-time limit exceeded.")
                return bytes(content)
        raise SourceError("Too many source redirects.")
    except (httpx.HTTPError, ValueError) as exc:
        raise SourceError(str(exc)) from exc
    finally:
        if own:
            client.close()


def discover(brand: str, client: httpx.Client | None = None) -> list[str]:
    if brand not in BRANDS.values():
        return []
    data = json.loads(
        fetch(API + "?" + urlencode({"format": "json", "RecallTitle": brand}), client)
    )
    # Discovery cannot expand the supported catalog or turn an absent result into an exclusion.
    return [str(r["RecallNumber"]) for r in data if str(r.get("RecallNumber")) in URLS]


def parse_html(notice_id: str, raw: bytes) -> Snapshot:
    soup = BeautifulSoup(raw, "html.parser")
    article = soup.select_one("#recall-product-node")
    if article is None:
        raise SourceError("Official recall article is missing.")
    for bad in article.select("script,style"):
        bad.decompose()
    info = article.select_one(".recall-product__info")
    details = article.select_one(".recall-product__details")
    title_el = soup.select_one("h1")
    title = title_el.get_text(" ", strip=True) if title_el else BRANDS[notice_id] + " recall"
    # Keep official details, including remedy and source notices. Never fetch embedded links.
    text = title + "\n" + (info.get_text(" ", strip=True) if info else "") + "\n"
    text += details.get_text(" ", strip=True) if details else ""
    text = " ".join(text.split())
    complete = all(s in text for s in ("Description:", "Remedy:", "Recall number:"))
    complete = complete and notice_id[:2] + "-" + notice_id[2:] in text
    return Snapshot(
        notice_id=notice_id,
        url=URLS[notice_id],
        retrieved_at=datetime.now(UTC).isoformat(),
        source_hash=digest(text.encode()),
        raw_hash=digest(raw),
        text=text,
        title=title,
        complete=complete,
    )


def load_snapshot(notice_id: str) -> Snapshot:
    manifest = json.loads((DATA / "catalog.json").read_text())
    snapshot = Snapshot.model_validate_json((DATA / "snapshots" / manifest[notice_id]).read_text())
    if digest(snapshot.text.encode()) != snapshot.source_hash:
        raise SourceError("Stored snapshot hash does not match source text.")
    return snapshot


def stale(snapshot: Snapshot) -> bool:
    return (
        datetime.now(UTC) - datetime.fromisoformat(snapshot.retrieved_at)
    ).total_seconds() > 86400
