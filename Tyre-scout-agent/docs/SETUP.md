# Setup Guide (Windows target machine)

Order matters: get the offline preview path working first, then enable
integrations one at a time. Nothing here requires administrator rights except
Docker Desktop installation itself.

## 1. Python and project

1. Install Python 3.11+ (python.org) and Git.
2. Copy the project to a local path, e.g. `C:\tire-scout-agent`
   (keep it OUT of OneDrive-synced folders to avoid file-lock issues with SQLite).
3. `python -m venv .venv` → `.venv\Scripts\activate` → `pip install -e .[dev]`.
4. `copy config\config.example.yaml config\config.yaml` and edit:
   - `recipients.self`, `recipients.daily_to`, `recipients.weekly_to`,
     `recipients.allowed_domains`, `email.from_address`
   - topics/keywords if you want to tune scope
5. `tire-scout self-test` — runs the whole daily graph offline with fixtures.
   Expect `"status": "research_complete"` and preview files under `data/`.

## 2. Local model (Ollama)

1. Install Ollama for Windows (https://ollama.com).
2. Pull a model with **structured-output support**, e.g.:
   `ollama pull qwen2.5:14b-instruct` (needs ~9 GB VRAM/RAM; on CPU-only use
   `qwen2.5:7b-instruct` and expect slower runs).
3. Set `llm.model` in config.yaml to the pulled name.
4. `tire-scout setup-check` — verifies the endpoint is reachable and probes
   structured output. If the probe fails, the agent refuses to guess: pick a
   capable model. Tool-calling support is NOT required (graphs use bounded
   structured calls, never free tool loops).

**Alternative when Ollama cannot be installed (e.g. IT policy):** any local
server exposing the OpenAI chat-completions API works — e.g. llama.cpp
`llama-server --jinja --port 8080` with a structured-output-capable model
(such as Qwen3-30B-A3B-Instruct). In config.yaml set
`llm.provider: "openai_compat"`, `llm.base_url` to the server (e.g.
`http://localhost:8080`), and `llm.model` to any label. No extra Python
packages are needed (plain httpx). `tire-scout setup-check` probes it the same
way. Keep the server running whenever the agent runs (including the scheduled
10:00 run).

Hardware guidance: 7B models run on CPU (slow) or any 8 GB GPU; 14B wants
16 GB RAM/VRAM. Research runs are sequential and bounded (~45 min budget).

## 3. Sandbox image (Docker Desktop)

1. Install Docker Desktop for Windows (WSL2 backend).
2. `powershell scripts/build_sandbox_image.ps1`
3. `tire-scout setup-check` should report the image present.

Until this passes, the `run_python_analysis` tool stays **disabled** — the
agent never executes generated code on the host.

## 4. Search provider

Default `fixture` provider is offline-only. For real discovery:

- Tavily: set `search.provider: tavily`, then set env var
  `TIRE_SCOUT_SEARCH_KEY` (User env var, not in the file). Free tier ~1000
  credits/month; a daily run uses ≤ 24 queries by budget (~750/month worst case).
- Brave: `search.provider: brave`, same env var. Free tier ~2000 queries/month.

Only approved query strings leave the machine. No internal files, mailbox
content, or config are ever sent to the provider.

## 5. Email adapter (choose ONE, with IT if needed)

| Adapter | When | Notes |
|---|---|---|
| `preview` | default/testing | writes EML/HTML to `data/outbox/`, sends nothing |
| `outlook_com` | classic Outlook desktop with your work profile signed in | needs `pip install .[outlook]`; Task Scheduler job must run in your signed-in session; reconcile via Sent Items |
| `smtp` | only if IT confirms SMTP AUTH is allowed for the mailbox | host/port + `TIRE_SCOUT_SMTP_USER` / `TIRE_SCOUT_SMTP_PASSWORD` env vars |
| `graph` | if IT grants application `Mail.Send` consent | tenant/client IDs + `TIRE_SCOUT_GRAPH_SECRET` env var |

Inspect the legacy watcher's Outlook integration first
(`tire-scout migrate C:\Users\shreeharsha.r\OneDrive - Apollo Tyres Limited\Tyre_Tech_Watcher`)
— it prints the existing recipient lists so you can copy them intentionally
into config.yaml. Do not assume Graph consent or SMTP permission exists.

Sending gates (all three required for autonomous daily/weekly mail):
`email.live_sending: true` AND `email.daily_enabled: true` /
`email.weekly_enabled: true`. Keep them `false` until previews look right and
you have run at least one authorized self-test in production mode
(`tire-scout daily --mode production` with only your own address configured).

## 6. Scheduling (Task Scheduler)

1. Daily time is set to **10:00 IST** (`schedule.daily_time`), confirmed with
   the operator; edit if that changes.
   Weekly stays Monday 11:00 IST unless changed.
2. `tire-scout schedule install` then `tire-scout schedule inspect` —
   inspection queries the real Task Scheduler and prints each task's stored
   start boundary; configuration alone does not prove a task exists.
3. One-time overrides: add a dated entry to `schedule.one_time_overrides`
   and re-run install. This creates a `/SC ONCE` task, never a second
   recurring one.
4. PC must be on and (for `outlook_com`) you must be signed in. Missed runs:
   the bounded catch-up window (`catch_up_window_hours`) applies at next start.

## 7. Migration from the legacy watcher

```
tire-scout migrate "C:\Users\shreeharsha.r\OneDrive - Apollo Tyres Limited\Tyre_Tech_Watcher"
tire-scout migrate "C:\...\Tyre_Tech_Watcher" --apply
```

The inspect step (no `--apply`) is read-only and prints: file presence plus
sha256 integrity against `PACKAGE_CONTENTS.json`, the existing To/Cc/mailbox,
the legacy send switches (`automatic_sending_enabled`, `first_scheduled_run`,
send time), and the decoded contents of `weekly_send_history.sqlite3`
(each `week_key` is resolved back to its week Monday; any `preparing`/
`uncertain` holds are listed for reconciliation).

`--apply` imports that history into the new send journal **with this agent's
own `email.from_address`** (override with `--sender`), so a new weekly run for
an already-sent legacy week is refused as `already submitted`. Legacy
`submitted` stays `submitted` — it is never upgraded to `confirmed_delivered`
(the legacy program itself only proved submission to Outlook). Stale legacy
`preparing`/`uncertain` holds import as `uncertain`: the week stays blocked
until you reconcile Sent Items/Outbox. The source files are opened read-only
and are never modified.

Then **disable the old watcher's scheduled task** — task name
`Tire Technology Watcher - Weekly Brief` (either its own program's disable
mode, or `schtasks /Change /TN "Tire Technology Watcher - Weekly Brief" /Disable`)
so only one production sender remains. Verified against the real package
(release 2026-09-13): the legacy config had automatic sending ENABLED with
first run 2026-10-12 11:00 IST and a 10-To / 2-Cc distribution list — check
this on your machine before flipping this agent's switches.

## 8. Verify before going live

- `tire-scout setup-check` — all green
- `tire-scout daily` (preview) → review `data/reports/daily/*.html`
- `tire-scout weekly` (preview) → review HTML + PDF
- `tire-scout daily --mode production` with self-only recipients → check
  `tire-scout delivery-status` shows `submitted`, then confirm in Sent Items
  before enabling weekly/team delivery.
