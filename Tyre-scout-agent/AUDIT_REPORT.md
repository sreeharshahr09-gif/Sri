# Audit Report — Tire Scout build verification

Build date: 2026-10-12 · Built and tested in a Linux sandbox (Python 3.12.12).
Target machine: Windows 10/11 at Apollo Tyres (per the build prompt).

This report separates (A) what was executed and verified here, (B) what was
verified in design/code but needs your machine to confirm, and (C) residual
limitations. Nothing below claims a live email was sent, a live model was
queried, or a live container was run — none of those happened here.

## A. Verified by execution here (84/84 tests passing)

Test suite: `python -m pytest tests/` → **84 passed**. Fully offline: fake DNS,
fake web transport, fixture search provider, deterministic fixture LLM.

| Area | Tests | What was proven |
|---|---:|---|
| Scoring | 5 | weights must sum to 1.0; exact weighted arithmetic; 0–10 range enforcement; threshold inclusion; 3-case human-reviewed eval set lands in agreed bands; rubric version recorded |
| Dedup/history | 10 | URL canonicalization (utm/www); duplicates by canonical URL, content hash, DOI/patent; syndicated copy clusters and does NOT corroborate; genuine follow-up with material change kept; old paper labelled background, not news; unknown dates stay not-background |
| Fetch security | 15 | non-http(s) schemes blocked; credentials-in-URL blocked; private/loopback IP literals blocked; host resolving to private blocked (incl. dual-homed); DNS failure reported; redirect-to-private blocked after the hop; public redirect chain works; 402→paywalled (not bypassed), 403→blocked; HTML meta-date provenance; PDF text extraction; scanned PDF flagged (not pretended); video unsupported without transcript; abstract-only flagged |
| LLM contract | 6 | malformed output rejected; missing score dimensions rejected; >10 scores rejected; over-long quotes rejected; fabricated evidence quotes flagged by grounding check; wrong category flagged; partial access surfaces a limitation instead of pretending full read; quarantine path persists the record with reason |
| Daily graph end-to-end | 3 | full run: 4 queries → 8 sources → syndication merged (one Evonik story) → malformed vendor page quarantined → injected blog flagged → 3 scored developments persisted with model/prompt/rubric versions → edition `research_complete` → HTML/JSON rendered → preview EML written → journal `preview` → recipients only allowlisted → attacker address absent; rerun idempotent (no duplicate developments, single journal row); zero-result day = `no_updates` (valid coverage), distinct from failure |
| Weekly gate | 8 | 7 editions on distinct dates pass; 1 missing blocks + saves PARTIAL-labelled local preview + journal `blocked`; failed day does NOT count as coverage; genuine no-update day DOES count; duplicate dates cannot fill a gap; cluster repeat-coverage counts once; trusted re-ranking overrides LLM-suggested order (score 9.0 stays above 7.0); operator `--allow-partial` sends visibly [PARTIAL]-labelled preview; rendered weekly PDF parsed — ≥2 pages, bookmarks, "Page 1" footer, week and item titles present |
| Send journal | 7 | identity stable across subject/recipient/render changes; resend blocked after `submitted`; `uncertain` blocks resend until reconciled; retry allowed after clean `failed_before_submit`; recipient allowlist enforced; From must equal approved account; attachment existence/type/size validated |
| Injection/scrub | 5 | instruction-like patterns detected in the malicious fixture page; untrusted content wrapped in delimiters; guard prompt present in the system message; outgoing scrub removes personal Gmail, OneDrive/local paths, legacy Received headers, external-sender banners, 1×1 tracking pixels — technical content preserved |
| Sandbox | 8 | docker run command carries the full isolation set (network none, read-only root, cap-drop ALL, no-new-privileges, non-root, memory/swap, CPUs, pids, tmpfs, read-only input mount); unavailable Docker → executor refuses and code demonstrably does NOT run on host; tool reports "execution refused"; symlink / path-traversal / oversize / wrong-type artifacts rejected; timeout kills the container via cidfile |
| Scheduler | 5 | daily/weekly/one-time schtasks command construction (MON 11:00; ONCE never recurring); task-XML parse extracts next-run boundary + enabled state; non-Windows hosts report unsupported rather than pretending |
| Checkpoints | 1 | graph runs with SqliteSaver on disk; state retrievable; checkpoint rows persisted |
| Migration | 5 | **verified against the real uploaded legacy package** (release 2026-09-13): all 5 expected files found; sha256 integrity vs `PACKAGE_CONTENTS.json` (programs/docs match release; config modified since release — recipients + auto-send enabled); real journal schema `sends(week_key, state, updated)` parsed; all 4 `week_key`s decoded to weeks 2026-09-14 … 2026-10-05 (all `submitted`, matching `tire_watcher_weekly.log`); import maps them to `submitted` (never `confirmed_delivered`) with the NEW sender's identity so `prepare_send` refuses a resend of every one of those weeks while the current week stays sendable; stale `preparing`/`uncertain` holds import as `uncertain` (stay blocked); import idempotent; source journal byte-identical after import (sha256 compared); undecodable keys skipped, not guessed |

Also executed here: the example generator ran the daily graph for 7
consecutive edition dates plus the weekly graph (all preview mode) and
produced the rendered artifacts in `examples/` — daily HTML/JSON, weekly
HTML/JSON/PDF, and the resulting send-journal rows. The weekly PDF and daily
HTML were rendered and visually inspected (numbered sections, metadata tables,
linked contents, appendix by daily edition, page numbers).

## B. Implemented but requiring your machine/credentials to verify

1. **Real Ollama run** — `OllamaClient` + capability probe are implemented;
   no local model was reachable here. Verify with `tire-scout setup-check`,
   then one real daily preview run.
2. **Real Docker sandbox** — command construction and fail-closed behaviour
   are tested with an injected runner; no Docker daemon exists in this build
   sandbox. On your PC: build the image, run `setup-check`, then a real
   analysis job.
3. **Real email submission** — preview adapter wrote real EML files; SMTP /
   Outlook COM / Graph adapters are implemented but untested against a live
   mailbox. First production test must be self-only, then confirm via
   `delivery-status` AND Sent Items.
4. **Windows Task Scheduler** — commands and XML parsing are tested; the
   tasks were not installed anywhere (no Windows here). `schedule install`
   then `schedule inspect` on your PC.
5. **Live search providers** — Tavily/Brave adapters implemented with env-var
   keys; only the fixture provider ran here.
6. **Legacy watcher scheduled task** — migration itself is now verified against
   the real files (see table above). What remains on your PC: the legacy task
   **"Tire Technology Watcher - Weekly Brief"** is still the *live* sending
   authority — its config has `automatic_sending_enabled: true` with
   `first_scheduled_run: 2026-10-12T11:00 IST` (today; the 11:00 run already
   fired and correctly skipped because this week's brief was submitted at
   09:45). Run `migrate` on the real OneDrive folder, then disable that task
   before enabling this agent's weekly send. Real distribution list found:
   **10 To + 2 Cc** (all `@apollotyres.com`; surfaced by `migrate` output —
   copy intentionally, never hard-coded).

## C. Residual limitations

- OCR of scanned PDFs is flagged but not performed unless you install
  `.[ocr]` + Tesseract; scanned documents are never pretended to be analyzed.
- YouTube videos are only usable with an accessible transcript (`.[youtube]`);
  otherwise they are recorded as unsupported.
- Email "confirmed_delivered" requires provider evidence; SMTP without DSN
  support cannot prove delivery — the journal honestly caps at `submitted`.
- Syndication detection is title-similarity based (Jaccard); very differently
  worded copies of one event may survive as separate items (score novelty
  should discount them — review in previews).
- LangGraph checkpoint replay after a mid-graph crash resumes from the last
  completed node; nodes are written to be idempotent, but a crash *inside* a
  node's DB transaction rolls that node's writes back cleanly.
- The weekly gate checks research completeness; it does not re-verify that
  daily emails were actually delivered — delivery state is separate
  (`delivery-status`), per design.

## Remaining user choices before production (not blockers for the above)

1. **Daily run time confirmed: 10:00 IST** (`schedule.daily_time`, operator
   arrives ~09:00). Weekly stays Monday 11:00 IST.
2. ~~Choose and install the **Ollama model** (hardware-dependent).~~ Resolved
   during rollout — see change log below: operator's IT policy blocks the
   Ollama installer, so the agent now also supports OpenAI-compatible local
   servers; operator runs llama.cpp `llama-server` with Qwen3-30B-A3B-Instruct.
3. Pick the **search provider** and set its env-var API key.
4. Confirm the permitted **mail adapter** with IT (Outlook COM vs SMTP vs Graph).
5. Decide whether **weekly delivery** stays self-only or uses an approved team
   To/Cc list (copy from the legacy watcher via `migrate` output if desired).
6. Only then set `email.live_sending` / `daily_enabled` / `weekly_enabled`.

## Post-audit change log (rollout phase)

- **2026-10-13 — OpenAI-compatible local LLM provider added.**
  Trigger: operator's IT policy blocks the Ollama installer; operator already
  runs llama.cpp `llama-server` (OpenAI-style API on port 8080) with
  Qwen3-30B-A3B-Instruct-2507. Change: `LLMConfig.provider` now accepts
  `"openai_compat"`; new `OpenAICompatClient` in `llm.py` (plain httpx — no
  new dependencies — same versioned prompts, same schema-constrained
  structured output via `response_format: json_schema`, same bounded
  repair/contract-error behaviour, `allow_json_mode_fallback` honoured);
  `setup-check` now probes whichever provider is configured. Ollama path
  untouched. Tests: +11 (`tests/test_llm_openai_compat.py`), runnable suite
  76 passed, 1 known env-only failure (langchain_ollama absent in CI sandbox).
  Security posture unchanged: model still gets no secrets, no tools, no
  network control; endpoint must still be local.
