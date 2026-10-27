# Engineering notes

## Matching and uncertainty

`models.py` defines stock identifiers, exact evidence spans, snapshots, a recursive typed rule language, and the three finding states. `criteria.py` evaluates `model_set`, `date_code_set`, fixed-width numeric `serial_range`, `all`, `any`, and an explicit unsupported leaf. Model punctuation, suffixes, and leading zeros survive. Aliases require an explicit parenthetical source relationship. Date codes never become dates.

The evaluator uses three-valued logic, then applies conservative uncertainty gates. A false AND branch cannot authorize exclusion when the overall source is incomplete or an unsupported condition exists. Missing required identifiers cannot become negative evidence. Brand conflicts and malformed identifiers require review. Affected findings take precedence in aggregation; all notice findings remain visible.

The limited source grammar is intentional. The source auditor checks exact model enumeration, model/code associations, and Boolean semantics against the notice description. It rejects model omissions, cross-model date assignments, and changing an OR of alternatives into an AND. It does not import evaluation gold. The application supports only the three reviewed notice profiles. A content hash change requires profile review before extraction can support a new exclusion. This avoids pretending a small grammar understands arbitrary future prose.

## Evidence and extraction

Snapshots contain the official URL, notice number, retrieval time, normalized text, SHA-256 of normalized text, raw HTML hash, completeness, and attribution. The committed source excerpts are inspectable. Whitespace normalization is explicit; quotations are never rewritten to repair model output.

The official API provides discovery, followed by the full HTML details. In the observed Secura API response, `Products.Model` is empty but `Description` contains the model/date restrictions. Relying on one API field would lose the business rule. HTML extraction deliberately excludes navigation and related recalls.

The OpenAI adapter uses `client.responses.parse` with Pydantic schemas, not a language-model framework wrapper. Evidence candidates include exact offsets computed from source text. The model interprets rules; deterministic validation checks spans, tokens, enumeration completeness, aliases, grouping, and operators. A real quote does not by itself prove the rule correct. Evaluation compares raw extraction with independently specified gold field groups before acceptance checks, then separately scores matching using accepted criteria.

Narrative generation is deliberately constrained. The model selects fact IDs, approved finding-derived explanatory sentences, and inquiry wording. The renderer supplies identifiers, statuses, source links, and hashes from validated findings. A model cannot rewrite those facts or claim product safety. This reduces prose flexibility but makes explanation fidelity independently testable.

## Tool choice and authority

`agent.py` has a choose node, an ordinary tool node, and a clarification node. A live model receives observed notice progress, finding counts, pending requests, and evidence-eligible tool/target pairs. It chooses notice inspection order and which missing identifier to request. The eligibility gate controls authority; matching controls outcomes. Fixture mode selects explicitly scripted choices instead of calling OpenAI.

The graph searches only the supported catalog; reads official sources; compares inventory; asks a concrete missing-field question; and drafts an inquiry after clarification. There are no send-message or inventory-write tools. Tool logs contain concise decision labels, tool targets, and outcomes, not private reasoning. Source instructions remain data. The reader checks redirect destinations and limits response bytes/time; it never recursively follows notice links.

## Persistence and recovery

Application SQLite tables store investigations, original inventory, source versions, investigation-bound criteria, findings, missing-field requests and answers, events, drafts, and refresh outcomes. The separate checkpoint database stores LangGraph continuation state. An investigation is keyed by upload bytes, mode, and source coverage versions. Duplicate submissions within the same coverage produce the same ID; changed coverage produces a new ID.

Original inventory is immutable. Answer overlays are applied when reading stock for recomputation. Only findings whose required fields include the answered field, for that stock unit, are recomputed. A missing field has one request per stock unit, shared across notice findings. The UI displays the request selected by the graph, with other questions queued. Answers are validated against the active checkpoint before calling `Command(resume)`, since LangGraph persists that payload even if the node subsequently rejects it. Repeating an answered request cannot resume a different pending question. Database transactions and primary/unique keys make inventory creation, requests, and event records idempotent. An interrupt node can replay safely, including after an answer was stored but before the next graph checkpoint. Tests use a separate Python process and a real server/browser restart.

Source records never replace historical evidence. New source hashes invalidate findings and remove derived drafts, including investigations that bound a source before producing any findings. Investigation bindings preserve their original source and extraction. Writes of findings, answers, and drafts check current source bindings inside SQLite write transactions. A repeated retrieval of the same content stores the investigation's observed retrieval time. Failed source retrieval does not touch existing findings. Repeated resume attempts preserve the `source_changed` state; an old fixture cannot revive findings after a newer source was observed. Invalidated findings keep stock-level aggregation unresolved unless another valid affected finding takes precedence.

The process lock serializes graph invocations inside the local application. Upload processing runs in a worker thread so slow retrieval and inference do not block the HTTP event loop or health endpoint. This is a single-process local design, not a distributed transactional scheduler. A crash can replay a model call before a checkpoint, so persistent budget reservations still count that attempt. Multi-worker deployment, user authentication, and cross-process graph locking would need a separate design.

## Evaluation and boundaries

`evals/gold_criteria.json` and `evals/scenarios.json` were authored before the extraction prompt. The latter fixes labels, missing-field expectations, and human rationales independently of matcher output. Insignia and its 20 cases were reserved from prompt tuning. It was inspected for gold preparation and is not claimed as a blind holdout. There are 60 synthetic scenarios: 21 affected, 21 excluded, and 18 insufficient-information. Source-incomplete and unsupported variants are marked synthetic stress conditions, not claims that the original notices contain such restrictions.

Fixed-criteria matching and scripted extraction are reported separately from live inference. Seven exact extraction field groups per notice penalize omitted and extra values; citation validity checks exact source spans. A confusion matrix, affected recall/precision counts, incorrect exclusion denominator, near-model match denominator, and missing-field request denominator are included. No model judges the gold outcomes. The sample is too small to establish real-world performance.

All live model requests reserve a conservative estimated cost before calling, have output/time limits, and use zero SDK retries. Refusal, incomplete output, schema failures, unauthorized tool choices, unsupported narrative claims, and exhausted budgets fail explicitly. The ledger persists across restart. Live request paths are implemented but were not executed with an API key at delivery; neither the 90% live extraction target nor live clarification is claimed verified.

## Five interview questions

**1. Why use a model when these three notices could be hard-coded?**

The model converts source prose into a typed, cited proposal and chooses the next eligible tool from observed evidence. Deterministic code owns the consequential classification. For only three fixed notices, the grammar audit is close to a source-specific parser, which is a deliberate reliability tradeoff rather than a claim of broad autonomous recall understanding.

**2. How does the code prevent a Secura cross-product bug?**

The root is an OR of two AND groups. Each group contains exactly one canonical model set and its own date-code set. Evidence validation and source-clause auditing reject moving 1903 to SAF-53D. Independent gold scenarios test both directions of that wrong association.

**3. What happens when a supplier has no date code available?**

The finding stays `needs_information`. The graph persists a request naming the stock unit, field, and label location, then interrupts. Restart uses the same thread ID. An answer is stored separately and triggers only relevant recomputation. There is no default code or unsupported exclusion.

**4. Why are valid citations insufficient?**

A model can quote the complete Secura sentence while assigning its code to the wrong model or changing alternative conditions into a conjunction. Span checks only establish quote fidelity. The source grammar checks relationships, and independent gold extraction fields test interpretation separately from matching.

**5. What would you improve before real deployment?**

First run live inference and inspect raw extraction reports against the held-out gold. Then expand independently reviewed notice formats and realistic retailer data, add human profile approval for source changes, improve field-specific serial grammars, and design authentication and concurrent execution if users need shared access. More infrastructure would not fix unsupported rule semantics.

## Official references inspected

- [CPSC API information and programmer guide](https://www.cpsc.gov/Recalls/CPSC-Recalls-Application-Program-Interface-API-Information)
- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Configurable default model](https://developers.openai.com/api/docs/models/gpt-4.1-mini)
- [LangGraph interrupts and resume](https://docs.langchain.com/oss/python/langgraph/interrupts)

Compatible versions were resolved and installed with uv on Python 3.12, then locked. The executed install includes OpenAI 2.54.0, LangGraph 1.2.18, SQLite checkpoint adapter 3.1.2, FastAPI 0.162.0, and Pydantic 2.14.1. The lockfile, not these prose examples, is authoritative for setup.
