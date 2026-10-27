"""Exercise the fixture workflow against the application running inside its image."""

import csv
import io

import httpx

from recall.sources import DATA

with httpx.Client(base_url="http://127.0.0.1:8000", follow_redirects=True, timeout=30) as client:
    assert client.get("/health").json() == {"status": "ok"}
    page = client.post(
        "/investigations",
        files={"file": ("demo.csv", (DATA / "demo.csv").read_bytes(), "text/csv")},
        data={"mode": "fixture"},
    )
    page.raise_for_status()
    identity = page.url.path.rsplit("/", 1)[1]
    body = client.get(f"/api/investigations/{identity}").json()
    if body["requests"]:
        request = body["requests"][0]
        client.post(
            f"/investigations/{identity}/answer",
            data={"request_id": request["id"], "answer": "1901"},
        ).raise_for_status()
    final = client.get(f"/api/investigations/{identity}").json()
    assert final["investigation"]["status"] == "complete"
    assert final["investigation"]["resumed"] == 1
    assert len(final["rows"]) == 3
    expected = {
        "SEC-001": "affected_by_notice",
        "SEC-002": "excluded_by_notice",
        "SEC-003": "affected_by_notice",
    }
    assert {r["stock"]["stock_id"]: r["primary"]["status"] for r in final["rows"]} == expected
    rows = list(
        csv.DictReader(
            io.StringIO(client.get(f"/investigations/{identity}/export/findings.csv").text)
        )
    )
    assert len(rows) == 9
    print("Container health, three-row fixture clarification, resume, and 9-row export passed.")
