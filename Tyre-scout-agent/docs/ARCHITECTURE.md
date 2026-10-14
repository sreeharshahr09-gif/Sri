# Architecture

## Trust boundaries

```
                 PUBLIC INTERNET (untrusted)
   search provider ──┐
   web pages / PDFs ─┤   only approved queries out; only reports out
                     ▼
┌─────────────────────────────────────────────────────────────────────┐
│ TRUSTED PYTHON (no LLM influence)                                   │
│  config validation · schedules/timezone · budgets · SSRF-guarded    │
│  fetch · dedup · score arithmetic · thresholds · recipient          │
│  allowlist · delivery gates · send journal · sandbox launcher       │
├─────────────────────────────────────────────────────────────────────┤
│ LLM (local Ollama) — bounded, schema-validated calls ONLY:          │
│  plan_queries · analyze_development · weekly_synthesis              │
│  cannot: send mail, change schedules/recipients/config, run code    │
├─────────────────────────────────────────────────────────────────────┤
│ DOCKER SANDBOX — generated analysis code runs here only:            │
│  --network none · read-only root · caps on RAM/CPU/PIDs/tmpfs ·     │
│  non-root · staged read-only inputs · disposable output dir         │
└─────────────────────────────────────────────────────────────────────┘
```

Prompt-injection defence is layered: retrieved text is wrapped in
`<<<UNTRUSTED_CONTENT>>>` markers, the guard prompt (prompts/system_guard.v1.md)
forbids obeying embedded instructions, heuristics scan for instruction-like
patterns (flagged on the record), and — decisively — the LLM has no tool that
could send mail, change recipients, or run host commands even if it tried.

## Graphs (LangGraph, SQLite checkpoints)

Daily: `determine edition date + window → plan (deterministic) → discover
(feeds, news RSS, papers; date gate, exclusions, seen links, triage, cap;
Tavily backup) → fetch/extract (page date reconciliation) → deduplicate →
analyze → score/validate → persist edition (+ seen links) → render →
delivery gate → send → reconcile`

Weekly: `determine prior Mon–Sun → coverage gate (every day inside a
successful run's window) → collect items published that week →
collect (cluster-merged, threshold-filtered) → synthesize themes/actions →
validate → render HTML/PDF → delivery gate → send → reconcile`

- Checkpoints: `langgraph.checkpoint.sqlite.SqliteSaver` in
  `data/checkpoints.sqlite`, thread IDs `daily-YYYY-MM-DD` / `weekly-YYYY-MM-DD`.
  The **application DB** (not checkpoints) is the source of truth for editions
  and delivery.
- Nodes are idempotent: re-running a completed edition date skips research;
  the send journal blocks duplicate submission.

## Data model (SQLite, `data/tire_scout.sqlite3`)

`sources` (canonical URL unique, content hash, stable IDs, extraction quality)
· `developments` (full assessment + score breakdown + model/prompt/rubric
versions + quarantine state) · `clusters` (syndication groups) ·
`daily_editions` (status: research_complete / no_updates / failed / partial +
coverage summary) · `weekly_editions` · `jobs` · `send_journal` (identity =
sha256(digest|period|sender); states preparing/preview/blocked/submitted/
failed_before_submit/uncertain/confirmed_delivered) · `exclusions` ·
`feedback` · `locks`.

Date semantics are explicit columns: `published_at` (+ provenance),
`discovered_at`, `edition_date`. Unknown dates stay NULL.

## Key design decisions

- **Structured output, not tool loops.** The model must return Pydantic-valid
  payloads (bounded repair retries, then quarantine). LangGraph routing decides
  flow; the model never decides whether an email goes out.
- **Trusted scoring.** The LLM proposes 0–10 per dimension with rationale;
  Python validates weights (sum = 1.0) and computes the weighted total.
  Maturity and confidence are stored separately from relevance.
- **Grounding check.** Every evidence quote must be a verbatim substring of the
  fetched text; fabricated quotes/URLs/categories quarantine the record.
- **Dedup ladder:** canonical URL → content hash → DOI/patent → title
  similarity (Jaccard ≥ 0.6 → syndication cluster; a material change keeps a
  follow-up). Old publications are labelled background, not news.
- **Delivery honesty.** Submission ≠ delivery. Crash-after-possible-submit →
  `uncertain`, reconciled via provider evidence, never blind-resend. Legacy
  weekly history imports as confirmed records so old issues never resend.
- **Weekly gate.** All seven edition dates must be present and complete
  (research_complete/no_updates). Missing/failed dates block the send, a
  labelled local partial preview is saved, and bounded catch-up jobs
  (configurable) can recover exact missing dates before revalidation. An
  operator `--allow-partial` override sends a visibly [PARTIAL]-labelled issue.

## Replaceable adapters

Discovery: publisher feeds / Bing + Google News RSS / OpenAlex (no keys),
web search fixture / Tavily / Brave as backup (env-var API keys). Email: preview / SMTP /
Outlook COM / Graph. LLM: Ollama (capability-checked). Adding an adapter means
implementing the small Protocol in `search/base.py` or `emailer/base.py`.
