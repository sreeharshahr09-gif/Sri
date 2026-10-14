# Operations Runbook

All commands: `tire-scout <cmd> [--config config/config.yaml]`.

## Routine

| Command | Purpose |
|---|---|
| `setup-check` | validate config, recipients, Ollama capability probe, sandbox image, installed tasks, next run times |
| `daily` | preview daily edition (research + render + preview "send") |
| `weekly` | preview weekly digest (coverage gate enforced) |
| `coverage [--week-start YYYY-MM-DD]` | which of the 7 edition dates are present/complete |
| `delivery-status` | send journal + any **uncertain** sends needing reconciliation |
| `schedule install / inspect / remove` | Task Scheduler management; inspect reads the real installed tasks |
| `feedback <dev_id> relevant/not_relevant/repeated --note ...` | record relevance feedback (drives future manual rubric edits only) |
| `migrate <legacy_path> [--apply] [--sender X]` | inspect/import legacy watcher settings and send history; imported weeks are recorded with this agent's sender so they block a resend |
| `disable-sending` | prints the exact config switches to stop all autonomous mail |
| `self-test` | offline end-to-end smoke run with fixtures |

## Failure handling

- **Corporate HTTPS certificate failures** → see
  [TLS repair and verification](SSL_FIX_REPORT.md). Use the validated CA bundle
  through `SSL_CERT_FILE`; never disable verification. The current corporate CAs
  expire on 19 November 2026 and require a refreshed bundle after IT renewal.
- **Failed daily research** → edition status `failed` (never shown as "no
  news"), job row holds the error, completed stages stay persisted. Re-run:
  `tire-scout daily --date YYYY-MM-DD` (retrospective catch-up, labelled).
- **Missing editions at weekly time** → weekly is blocked, a PARTIAL-labelled
  local preview is saved, missing dates are recorded. With
  `weekly.recovery_enabled: true`, up to `max_recovery_dates` catch-up jobs run
  automatically for the exact missing dates, then the gate revalidates.
  Operator override: `tire-scout weekly --allow-partial` sends a visibly
  [PARTIAL] issue — a deliberate, audited action.
- **Uncertain send** (crash after possible submission): listed by
  `delivery-status`. Reconcile by checking Sent Items / provider, then either
  leave it (already sent) or mark it failed before retrying. The journal never
  auto-resends an uncertain record.
- **Model unreachable / GPU OOM** → the run fails its bounded LLM call, the
  planning node falls back to deterministic keyword queries (noted as a
  limitation); analysis failures quarantine the item with the reason.
- **Sandbox unavailable** → `run_python_analysis` returns "execution refused";
  research continues without generated-code analysis.
- **Overlapping runs** → SQLite job locks (`locks` table) refuse a second
  concurrent daily/weekly; a crashed run's stale lock can be cleared by
  deleting the row after confirming the process is gone.

## Backup / restore / retention

- Backup: stop runs, copy `data/` (SQLite files are consistent when no job
  holds a write transaction; WAL files included). Restore: put `data/` back.
- Retention (configurable): raw fetched artifacts 90 d, sandbox job outputs
  7 d, preview outbox 30 d. Historical editions/developments/journal are never
  auto-deleted; a failed send is never "fixed" by deleting history.

## Privacy of outgoing reports

Rendered bodies pass through the scrubber: personal Gmail addresses, local
filesystem/OneDrive paths, legacy Received headers, external-sender warning
banners and tracking pixels/links are removed. Technical limitations and
source attribution are preserved. The official From/To transport fields are
separate and always use the approved work account.

## Telemetry

Off by default: LangSmith/tracing env vars are stripped at startup unless
`telemetry.enabled: true`. Cloud inference is not used; the local model does
not make the system offline — search and email still need connectivity.
