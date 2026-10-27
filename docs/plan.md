# Implementation plan and evaluation reservation

Written before implementation on 2026-10-27.

1. Validate string identifiers and CSV boundaries. Store immutable source snapshots, typed rules, and exact evidence spans. Use three-valued deterministic matching with explicit uncertainty gates.
2. Complete the three-row Secura journey with SQLite application records and a LangGraph SQLite interrupt. Restart and resume the same investigation; update only the answered stock unit's notice findings.
3. Add evidence-constrained model extraction and tool selection behind one OpenAI SDK adapter. Fixture mode uses labeled scripted responses. No tool mutates inventory or sends a message.
4. Add Cosori and Insignia, 60 independently labeled synthetic scenarios, baseline comparisons, HTML review UI, and safe exports.
5. Run deterministic, database, API, browser, clean-checkout, and container checks. Attempt live retrieval and inference. Write documentation from observed results and publish source and CI.

## Reservation

Insignia (2024, recall 24-159) and its 20 scenarios are held out from extraction-prompt tuning. Gold labels and criteria are authored before the extraction prompt. Secura and Cosori form the development set. The held-out notice is already inspected for data preparation; this is a prompt-tuning holdout, not a blind research benchmark. Do not change the prompt based on held-out results.

## Environment discovery

Personal GitHub account: AntarangSharma (type User). Requested repository returned HTTP 404 before creation. Public repository created with explicit user authorization. Shell network and Docker require sandbox escalation. No code-review-graph tool is exposed in this session, so filesystem tools are the fallback. No OpenAI key was initially configured; live verification is pending local configuration.
