# Tire Scout — Local Tire Technology Scouting Agent

A local technology-scouting agent for tire R&D. It searches the public
internet, reads and evaluates technical sources with a **locally hosted LLM**
(Ollama), runs generated Python analysis only inside a **Docker isolation
boundary**, persists everything to local SQLite, sends a **daily email** to
your configured work address, and compiles a **weekly digest with PDF** every
Monday 11:00 IST. Live sending stays disabled until you explicitly enable it.

- Agent framework: **LangChain + LangGraph** (persistent SQLite checkpoints)
- LLM: local **Ollama** model with capability checks (structured output required)
- Sandbox: unprivileged Docker container — no network, no secrets, resource caps
- Email: replaceable adapters (preview / SMTP / classic Outlook COM / Graph)
- Scheduler: Windows Task Scheduler (daily + weekly + dated one-time overrides)
- Everything offline-testable: 84 pytest tests with fixtures, no network needed

## Quick start (offline preview, no credentials needed)

```bash
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -e .[dev]
cp config/config.example.yaml config/config.yaml    # edit recipients first
tire-scout self-test                                # offline end-to-end smoke run
tire-scout daily                                    # preview daily edition
tire-scout weekly                                   # preview weekly digest (gates enforced)
```

Previews land in `data/reports/` and `data/outbox/` — nothing is emailed.

## Full setup

See **docs/SETUP.md** (Ollama model, sandbox image, search provider key, mail
adapter, Task Scheduler install, migration from the legacy watcher),
**docs/ARCHITECTURE.md** (trust boundaries and data model),
**docs/OPERATIONS.md** (runbook, recovery, retention), and **AUDIT_REPORT.md**
(what was verified, where, and what remains for your machine).

## Repository layout

```
config/            authoritative non-secret config + source registry
prompts/           versioned system/task prompts
src/tire_scout/    the agent package (graphs, tools, db, email, sandbox, ...)
tests/             84 offline tests + fixtures (fake web, fake DNS, fixture LLM)
examples/          generated example daily/weekly outputs + generator script
scripts/           sandbox image Dockerfile + build scripts
docs/              setup / architecture / operations guides
```
