# Freshness fix — 14 October 2026

The daily run kept reporting old or undated material (a 2012 patent, market
forecasts, "how tires are made" pages). The quote checks and report sections
added on 13 October labelled that material correctly, but nothing new was
being found, so every report was background or "requires review".

## Root causes (from the saved databases and runs)

| # | Cause | Evidence |
|---|---|---|
| 1 | The model wrote topic phrases ("functionalized SBR silica dispersion tire compound performance") for a general web search. That returns the best pages ever written on a topic, not news. | 13 Oct production: 22 pages analysed, 21 undated, one from 2012. 14 Oct re-run: 6 of 13 were market reports or reference pages. |
| 2 | "Recent" meant 14–90 days (+3), judged on dates the search provider guessed. | `search.recency_windows`; Tavily general-index dates. |
| 3 | The model's guessed source type picked the domain filter. | A TPMS query searched only video sites; a hybrid-cord query searched only patent sites (the 2012 patent). |
| 4 | Genuinely new items were demoted when the page had no machine-readable date. | Weibold/HITEC, dated 12 Oct by the provider, ended in "requires review". |
| 5 | Only accepted items were remembered (60 days); rejected and failed links came back daily. | `prior_index` reads active developments only. |
| 6 | Every candidate was fetched and analysed (~1.5–2 min each) before any cheap date or relevance check. | 16–23 minute runs with zero usable items. |
| 7 | `self-test` wrote a test-data edition into the production database. | 2026-10-12 edition built from `example.com` fixture URLs. |

## What changed

**Discovery is source-first** (`src/tire_scout/discovery/`):

- **Publisher feeds** (RSS/Atom) for the trade press and company newsrooms, listed
  in `config/sources.registry.yaml`. Each item carries the publisher's own
  publication time. If a feed address fails, the feed is auto-discovered from
  the site's homepage.
- **Dated news search** through Bing News and Google News RSS with built-in
  technology, business, regulatory, tire-maker and supplier queries.
- **New papers** from OpenAlex, published inside the window, analysed from the
  title and abstract (no blocked publisher pages).
- **Tavily is a backup only**: its news index, at most 3 queries, and only on days
  when the free channels return fewer than 10 fresh candidates. Worst case is
  about 90 credits a month on the free 1,000-credit plan.
- The model no longer writes search queries.

**"Latest" means published inside the edition window**:

- Window = the edition date and the 2 days before it (`discovery.lookback_days`).
  After missed runs (PC off over a weekend), the window reaches back to the last
  successful run, at most 7 days, so nothing is skipped.
- **Undated items are dropped. Items published before the window are dropped.**
  Both happen before any page is fetched or the model is called.
- Trusted dates, in order: date stated on the page; publisher feed date;
  bibliographic record (OpenAlex); date in the article URL; news-index date.
  If the page itself states an older publication date than its listing, the
  page wins and the item is dropped as old.

**Cheap filters before the expensive steps**:

- Market-research press-release sites and video hosts are excluded
  (`exclude_domains` in the registry); market-forecast titles are dropped.
- General rubber/automotive sources keep only items mentioning tires or rubber.
- The same announcement from several outlets is analysed once.
- **Every handled link is remembered permanently** (`seen_urls` table, schema v3),
  whatever the outcome. A failed fetch is retried once on a later day.
- At most 15 analyses per run (`discovery.max_analyses`), ranked by keyword
  relevance. Fresh items over the cap are not marked as seen and remain
  eligible the next day while still inside the window.
- A page that blocks the agent is analysed from the publisher's feed summary,
  with that limitation recorded.

**Scope** now includes business and regulatory news. Two topics, *Industry and
business* and *Regulation and standards*, are added automatically when your
`config.yaml` lacks them. The analysis prompt (`analyze-2.0`) covers launches,
plants, investments, agreements and regulatory steps, with new maturity labels
`business_event` and `regulation`.

**Reports**:

- The daily report states the window ("News published 12 to 14 October") and
  where each date came from. It lists recent items only; background and
  unverified sections are off by default (`show_background_section`,
  `show_unverified_section`).
- Confidence and maturity are shown as labels, no longer used as freshness
  gates. Only an unsupported interpretation (low confidence *and* speculation)
  is held back.
- The weekly digest holds items **published** Monday–Sunday, including those the
  Monday-morning run finds from the weekend. Coverage counts a day as covered
  when a successful run's window included it, so a Monday run after a weekend
  with the PC off covers Saturday and Sunday. If a gap remains, the weekly run
  performs one catch-up daily run for today instead of re-running past dates.

**Safety unchanged**: same URL policy for every request, untrusted-content
handling, quote grounding, one correction attempt, recipient allowlist and
sending gates. Email sending stays disabled.

## Upgrading `C:\Tyre-scout-agent`

No new Python packages are needed.

1. Close any running Tire Scout window or task.
2. Back up the folder, e.g. copy `C:\Tyre-scout-agent` to
   `C:\Tyre-scout-agent-backup-2026-10-14`.
3. Download the branch as a zip:
   `https://github.com/sreeharshahr09-gif/Sri/archive/refs/heads/claude/admiring-meitner-oom02w.zip`
   and copy everything inside its `Tyre-scout-agent` folder over
   `C:\Tyre-scout-agent`. The branch does not contain `data\`, `certs\` or
   `config\config.yaml`, so your database, certificates and settings are kept.
   It does replace `config\sources.registry.yaml` (new feed list).
4. Reinstall the package in place (fast, offline):

   ```powershell
   cd C:\Tyre-scout-agent
   & 'C:\Users\shreeharsha.r\.conda\envs\tire-scout\python.exe' -m pip install -e . --no-deps
   ```

Your `config.yaml` works as is; the new `discovery:` settings take their defaults.
To change them, copy the `discovery:` block from `config\config.example.yaml`.

## Checking it on the office PC

Use the same Python as above (shown as `python`), from `C:\Tyre-scout-agent`,
with `SSL_CERT_FILE` set as before.

1. **Tests** (offline): `python -m pytest tests -q --basetemp data\audits\pytest-freshness`
   Expected: 211 passed (one Windows-privilege skip is possible).
2. **Feeds** (about a minute; no model, no email): `tire-scout check-feeds`
   Each line shows whether a feed loaded, its item count, how many items are in
   today's window and the newest date. A `[FAIL]` line names the address and
   error. When a feed is found through its homepage, the command prints the
   feed address to paste into `url:` in the registry.
3. **Discovery dry run** (no model, no email): `tire-scout discover`
   Lists what today's run would analyse, with dates and sources, plus counts of
   everything dropped and why. The full list, with every drop reason, is saved
   as a CSV under `data\reports\discovery\` (opens in Excel).
4. **Remove the test-data edition** from 12 October:
   `python scripts\remove_fixture_edition.py` (dry run: lists the rows), then
   `python scripts\remove_fixture_edition.py --apply`. A backup is written to
   `data\backups\` first.
5. **Full preview run** (local model running; email stays in preview):
   `tire-scout daily`. Open the HTML report from `data\reports\daily\`.
6. **Scorecard over the following days**: `python scripts\freshness_scorecard.py`
   Per edition: window, items reported, how many have a source date, and the
   oldest reported item's age. Target: every item dated, none older than the window.

Run steps 5–6 for 3–5 working days before enabling the weekly email to the team.

## Known limits

- The feed addresses could not be tested from the development environment
  (no access to those sites). `check-feeds` is the authority; several company
  newsrooms may have no feed and will show `[FAIL]`. Company news still arrives
  through the trade press and the news-search queries.
- Google News links need Google's own redirect service to resolve. This is best
  effort: unresolved items are dropped, never guessed. Bing News links resolve
  directly.
- Patent publications are not a dedicated channel yet. A patent feed (for
  example the EPO's free Open Patent Services, which needs a registration key)
  can be added as another channel.
- A paywalled article is analysed from its feed summary when the page is
  blocked; the report records that limitation.
- `tire-scout self-test` now writes to `data\self-test\<timestamp>\`, never to
  the production database. It still fetches the fixture's example URLs over the
  network, so "no_updates" there is normal when those fetches fail.
