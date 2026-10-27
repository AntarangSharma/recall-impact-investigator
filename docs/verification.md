# Executed verification

Executed on October 27, 2026. This report separates deterministic behavior, live retrieval, and unverified inference.

| Check actually run | Result |
|---|---|
| `make setup` | Passed with Python 3.12.13 on macOS; locked dependencies and Chromium installed |
| `make check` | Formatting, lint, strict mypy for 11 source modules, and forbidden-character scan passed |
| `RECALL_SCREENSHOTS=docs/screenshots make test` | 128 passed after recheck, including real Chromium, a server restart, and queued clarification questions |
| `make eval-fixtures` | 60/60 fixed gold cases; 60/60 scripted-pipeline cases; exact-model baseline 41/60 |
| `make refresh-data` | API discovery and complete HTML details retrieved for all 3 notices |
| `make eval-live` | Explicitly not run: no locally configured OpenAI key; no live quality figure claimed |
| `make smoke-live` | Explicitly not verified: no locally configured OpenAI key; no live clarification claim |
| `docker build -t recall-impact-investigator:local .` | Built successfully |
| `docker inspect ...` health | Healthy |
| `docker exec ... python scripts/container_smoke.py` | Three-row fixture clarification/resume and nine-row CSV export passed inside running image |
| `docker exec ... python -m recall.cli eval-fixtures` | 60/60 fixed criteria and scripted pipeline inside image |
| Clean temporary Git checkout: setup, check, test | Passed; 112 tests |
| Clean checkout: `make demo PORT=<available-port>` | Health OK, three stock rows, pending persistent identifier request |

Port 8000 already belonged to another local process, so startup verification used an available port without modifying that process. The Makefile supports `PORT=8765` or another free port.

[The first pushed implementation CI run](https://github.com/AntarangSharma/recall-impact-investigator/actions/runs/40471741501) passed on Ubuntu, including formatting, lint, mypy, text scan, pytest/browser, fixture evaluation, Docker build, container workflow, and container fixture evaluation. The [Actions page](https://github.com/AntarangSharma/recall-impact-investigator/actions) shows subsequent source and documentation runs; final delivery checks the final pushed run separately.

## Fixed evaluation counts

Affected recall: 21/21. Affected precision: 21/21. Incorrect exclusions: 0/21 predicted exclusions. False model matches: 0/15 defined near-model negative cases. Correct required-identifier requests: 10/10, with zero unnecessary requests. Held-out Insignia: 20/20; development notices: 40/40.

The baseline has affected recall 21/21, affected precision 21/37, three incorrect exclusions among 19 predicted exclusions, and no correct clarification requests among ten required. Its held-out result is 16/20.

Scripted extraction has 21/21 exact field groups and 11/11 valid rule/alias citations. The held-out scripted result is 7/7 fields and 1/1 citation. These are scripted-response checks, not live model scores. No key or model response was substituted with an invented result. The 90% live supported-field target remains unverified.

## Important exercised failures

The post-delivery recheck found and fixed duplicate identifier requests across notices, out-of-order and repeated answers entering the wrong saved interrupt, and source-change errors losing their protected status on retry. Transactional source guards now prevent stale findings or drafts from being written. Sixteen new regression cases cover those paths, invalid answer recovery, malformed CSV headers and null characters, incorrect extracted brands, complete serial-range bounds, invalidated aggregation, and HTTP responsiveness during upload work. The real browser journey also covers two sequential identifier questions and replaying the first answer while the second is pending.

The recheck reran formatting, lint, strict typing, the forbidden-character scan, all 128 tests, and the complete 60-case comparison. Fixed and scripted matching remain 60/60 with zero incorrect exclusions. Live inference remains unverified because the local key was still unavailable. The updated local application preserves the original three-row investigation and its pending Secura date-code question; it uses an available port because another application owns the earlier port.

Tests cover grouped model/date rules, suffix and punctuation differences, missing/empty/malformed identifiers, numeric serial bounds, unsupported serial syntax, explicit exclusions, unsupported rules, invented quotes, wrong offsets, right quotes with wrong grouping or Boolean semantics, source changes, stale labels, retrieval errors/limits, redirect rejection, CSV formula injection, hostile source instructions, refusal/schema failures, model budget exhaustion, unsupported narrative claims, duplicate submissions, answer replay after a partial write, database/process restart, API exports, and browser clarification.

The first application-specific HTTP User-Agent was rejected by CPSC with HTTP 403. The accepted compatibility header was tested and live retrieval then passed for all three notices. This is not a guarantee that an official origin will always be available. Failed refreshes leave prior investigations intact.

## Screenshots and provenance

`docs/screenshots/home.png`, `clarification.png`, and `resumed-evidence.png` were captured from the actual running fixture application by Playwright. The browser test stops the server while waiting for the code, starts it with the same SQLite paths, supplies 1901, and verifies the resumed state and export. The desktop screenshot was visually inspected, and the browser also checks a 390-pixel mobile viewport for overflow.

`evals/results-fixtures.json` contains case-level predictions and all confusion counts. `results-retrieval.json` records retrieval times, source hashes, and completeness. `results-live.json` and `results-live-smoke.json` record the unavailable live inference paths explicitly. Runtime databases, local environment files, private inventory, and credentials are excluded from Git.
