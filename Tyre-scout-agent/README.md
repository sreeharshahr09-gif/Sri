# Tire Scout — Local Tire Technology Scouting Agent

A local technology-scouting agent for tire R&D. It collects the latest tire
technology, business and regulatory news from publisher feeds, dated news
search and new papers, keeps only items published in the last few days, and
evaluates them with a **locally hosted LLM**
(Ollama), runs generated Python analysis only inside a **Docker isolation
boundary**, persists everything to local SQLite, sends a **daily email** to
your configured work address, and compiles a **weekly digest with PDF** every
Monday 11:00 IST. Live sending stays disabled until you explicitly enable it.

- Agent framework: **LangChain + LangGraph** (persistent SQLite checkpoints)
- LLM: local **Ollama** model with capability checks (structured output required)
- Sandbox: unprivileged Docker container — no network, no secrets, resource caps
- Email: replaceable adapters (preview / SMTP / classic Outlook COM / Graph)
- Scheduler: Windows Task Scheduler (daily + weekly + dated one-time overrides)
- Discovery: publisher RSS/Atom feeds, Bing/Google News RSS, OpenAlex papers;
  Tavily only as a capped backup. Undated and older items never reach the model.
- Everything offline-testable: 211 pytest tests with fixtures, no network needed

## Quick start (offline preview, no credentials needed)

```bash
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -e .[dev]
cp config/config.example.yaml config/config.yaml    # edit recipients first
tire-scout self-test                                # end-to-end smoke run (isolated data folder)
tire-scout check-feeds                              # do the configured feeds load? are they current?
tire-scout discover                                 # dry run: what today's run would analyse
tire-scout daily                                    # preview daily edition
tire-scout weekly                                   # preview weekly digest (gates enforced)
```

Previews land in `data/reports/` and `data/outbox/` — nothing is emailed.

## Full setup

**Upgrading from the 13 October version:** read
**docs/FRESHNESS_FIX_2026-10-14.md** (what changed, how to upgrade, how to check it).

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
tests/             211 offline tests + fixtures (fake web, fake DNS, fake feeds, fixture LLM)
examples/          generated example daily/weekly outputs + generator script
scripts/           sandbox image build, freshness scorecard, fixture-edition cleanup
docs/              setup / architecture / operations guides
```
