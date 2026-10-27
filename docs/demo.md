# Three-minute demo

Start in a fresh runtime directory so prior answers do not hide the question:

```sh
make setup
RECALL_RUNTIME=/tmp/recall-three-minute-demo make demo
```

Open http://127.0.0.1:8000. A directory already used for a completed demo will retain its answer; choose another runtime path for a fresh run.

## 0:00 to 0:40: the workflow

Select Open fixture demo. Explain that this is a clearly labeled no-key mode using official stored CPSC sources and scripted model responses. The three stock units all identify Secura SAF-53D. The official notice binds that model to code 1901. Code 1902 is outside the notice's code set. The third unit has an empty code, distinct from an absent CSV column or malformed value.

Show the affected, excluded-by-this-notice, and needs-information rows. Point out that no finding is a declaration of universal safety. Expand a row to show all three notice findings and the stock-level precedence rule.

## 0:40 to 1:30: inspect evidence

Expand SEC-001. Show the exact description excerpt, source character offsets, snapshot hash, official URL, and retrieval date. Explain why model SAF-53 with codes 1903/1904 is a different branch. There is no Cartesian product between all models and all date codes. Case and outer whitespace normalize; meaningful punctuation and suffixes do not.

## 1:30 to 2:15: restart and clarify

Point to the specific question asking for SEC-003's date code on the silver bottom label. Stop the server with Ctrl-C and restart using the same command and runtime path. Reopen the same investigation. The request is still pending because LangGraph checkpoints and application rows are in SQLite.

Enter 1901. Show Resumed / complete and SEC-003 becoming affected. The answer is an investigation overlay, not an inventory mutation. Only SEC-003's Secura finding is recomputed; other finding revisions stay unchanged. Repeated submission of the same answer is idempotent.

## 2:15 to 3:00: exports and engineering

Open the draft supplier inquiry. It is never sent. Download the CSV and evidence JSON. Explain formula-cell neutralization, exact quote validation, independent gold labels, and the source-change invalidation rule.

Report actual results: 60/60 fixed-gold cases, 20/20 held-out Insignia cases, zero incorrect exclusions, and 10/10 expected identifier requests. The exact-model baseline gets 41/60. Scripted extraction is 21/21 field groups and 11/11 citations, which does not establish live model quality. Live CPSC retrieval passed; live OpenAI inference was unverified at delivery because a key was not configured.

For a credentialed demonstration, configure `.env` locally, run `make eval-live` and `make smoke-live`, inspect their reports, then select Live in the upload form. Do not describe fixture tool choices or extraction as live inference.
