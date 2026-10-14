# Tire Scout improvements — 14 October 2026

Status: implementation and offline verification complete; live comparison and fresh preview in progress.

## Scope

The five approved changes address discovery quality, document classification, publication-date evidence, support for technical interpretations, and preservation of the original fetch hostname. The existing Qwen3-30B model is retained. Verification runs use separate databases under `data/runs`; email delivery is disabled.

## Implemented behavior

1. Preferred origins from `config/sources.registry.yaml` now reach the search provider as domain filters. Empty preferred searches receive a bounded open fallback. Results are interleaved across queries before fetching. Obvious market forecasts and reference pages are excluded during fresh discovery.
2. A fetched document is classified from its URL, publisher metadata, content title, and registered origin. A planner's requested category cannot turn a Wikipedia page into a patent or a market forecast into a video. Unresolved types remain `unknown`, which cannot enter the recent-development section.
3. Publication dates are extracted from HTML metadata independent of attribute order, article JSON-LD, publication-labelled time elements, and explicit page labels. Incomplete dates are rejected. Modification dates remain separate, contradictory publication dates require review, and a provider date alone never verifies recency. Date candidates and locations are retained.
4. The model must provide exact source support for novelty, specific maturity stages, and claimed benefits. Advertised demonstrations do not establish prototypes; testing-equipment availability does not establish an experimental result. Quantitative benefits need reported conditions. Non-paper/non-patent single-source analyses are capped at medium confidence. One correction attempt is allowed; unresolved failures are quarantined with their raw responses.
5. Fetching uses the original search URL and preserves the actual redirect destination. Canonicalization is used for identity and deduplication. TLS verification, redirect limits and restricted-page handling remain enforced.

Structured source details are stored using additive database schema version 2. Migration testing verifies that existing development rows survive. The production database has not been opened for writing by the verification runs; it will receive the additive column when the updated application next opens it.

## Verification

- Expanded offline suite: **180 passed, 1 skipped**, including source-label regressions, date parsing/conflicts, unsupported maturity claims, original-hostname fetching, preferred search/fallback, interleaving, and schema migration.
- Baseline backup: `data/audits/improvements-2026-10-14/original/`, with database backups and SHA-256 manifest.
- Same-source comparison: `data/runs/improvements-same20-2026-10-14-02/`. It refetches the previous 20 original URLs; upstream content may have changed. Its edition reference remains 13 October for comparison with the previous run.
- The first comparison directory was stopped during fetching, before analysis, to correct the verification wrapper. It is marked `ABORTED.md` and is not a result.

Live result tables and the final production-preservation audit will be added when both runs finish.

## Limits

These checks test necessary evidence support; they do not prove every model interpretation. Single-source supplier claims remain attributed claims. HTTP access restrictions, missing full text and absent dates can still prevent inclusion. Preferred origins are search preferences, not independent corroboration or proof of peer review. The registry contains an invalid `mrf tyres.com` entry; it is ignored with a visible warning rather than sent to search.

The target is credible technical developments, not a fixed number of links. The configured 40-page fetch budget is a ceiling; the report has a separate eight-item cap, and only sufficiently supported recent items qualify for a weekly digest.

## Interface references

Search filtering follows the [Tavily search API](https://docs.tavily.com/documentation/api-reference/endpoint/search). Publication and modification dates retain the distinct meanings of Schema.org [datePublished](https://schema.org/datePublished) and [dateModified](https://schema.org/dateModified).
