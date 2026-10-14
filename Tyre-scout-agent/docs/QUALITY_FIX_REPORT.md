# Tire Scout research-quality fixes

Implemented on 13 October 2026 in `C:\Tyre-scout-agent`. The SSL repair remains
in place; this change addresses evidence handling and report eligibility.

## What changed

- **One bounded correction:** each source gets an initial analysis and at most
  one correction, including schema and grounding errors together. Analysis
  calls no longer multiply this budget through hidden provider retries.
- **Exact excerpt selection:** on correction, Python offers unchanged source
  slices and constrains the response schema to those choices. Word overlap
  retrieves candidate passages; it never approves an approximate quote.
  Validation preserves case, numbers, signs, units, negations, and identifiers.
  Only whitespace, canonical Unicode, and quotation-mark presentation receive
  conservative normalization. Repeated quotes count as one excerpt.
- **Complete explanations:** provider output must explicitly supply maturity,
  confidence, what changed, technical explanation, and confidence reasons.
  Placeholder or missing explanations fail validation. Prompt version is
  `analyze-1.2`; quality policy version is `quality-1.0`.
- **Separate report sections:** recent source-dated updates, background
  references, and unverified date/evidence material. A high score alone cannot
  qualify an item as recent. Useful not-new material can remain as background
  when it has quotes, an explanation, and adequate relevance/overall score.
- **Shared eligibility:** daily selection, rerendering, weekly collection, and
  weekly rendering use the same rules. The existing daily cap applies across
  all three sections, with recent material first. Weekly updates use only the
  recent section. Daily PDF, when enabled, contains only recent items; HTML/JSON
  contain all three sections.
- **Freshness relative to the edition:** source-specific configured windows
  apply to the edition date, including catch-up runs. Missing, invalid, future,
  or provider-only dates require review. Low confidence, speculation, or
  missing retained quotes also prevent recent-update eligibility.
- **Visible extraction limits:** the actual analyzed length and original length
  are distinct, truncated content is marked partial, and the limitation reaches
  the analysis/report. Video HTML without a transcript is skipped before the
  model is called.
- **Full diagnostic records:** `data/quality/<edition>/` retains the complete
  analyzed excerpt, its hash, metadata, provider responses, parsed analyses,
  full failed quotes/reasons, and correction choices. Configured credential
  values are redacted. Audit write failures stop processing.
- **Honest failure handling:** a run where every analysis is unusable does not
  become a successful no-news edition; failed research is blocked from live
  sending. Partial quarantine counts remain visible in the report.
- **Safe HTML:** escaping is explicit for `.html.j2` templates, including source
  excerpts. The previous extension-based setting did not recognize that suffix.

## Original edition, reclassified

The five original selected items become **0 recent updates, 1 background
reference, and 4 items requiring review**. The original report is preserved.
This classification does not mean no recent tire developments exist; it means
the saved five do not establish any qualifying recent updates under these rules.

The earlier run had 40 discovered URLs, 33 successful fetches, 3 duplicates,
30 analyzed candidates, 18 quarantines, 7 not-new exclusions, and 5 selected
items. These are candidate URLs and filtering outcomes, not 40 independent
validated developments. See `RESEARCH_QUALITY_AUDIT.md` for the original audit.

## Verification

- Full offline suite: **138 passed, 1 skipped**. The skip requires Windows
  symlink-creation privileges. Evidence: `data/audits/quality-fix-2026-10-13/pytest-complete-contract.txt`.
- Regressions cover scientific-value/identifier changes, safe quotation
  typography, bounded retries, raw invalid JSON/HTTP diagnostics, exact excerpt
  constraints, missing explanations, transcript-less video, truncation,
  credential redaction, date windows, caps, background retention, weekly
  exclusion, failed-run delivery blocking, report sections/links, and HTML escaping.
- Browser visual inspection was blocked by the browser tool's local-file URL
  policy. HTML structure, section placement, escaping, internal links, and JSON
  agreement were checked automatically; a visual browser check is not claimed.

## Live saved-source replay

The bounded sample uses four previously quarantined documents (RubberNews PDF,
SCIEPublish recovered carbon black, PMC isoprene-rubber formulation, and Data
Bridge tire-cord market) plus one transcript-less YouTube page. No search or
source refetch occurs. Outputs and a database copy are isolated under
`data/audits/quality-fix-2026-10-13/replay-*`.

The first diagnostic replay received HTTP 503 responses. The server was checked
without restarting it, and a capability probe was added. A subsequent replay
showed that a free-form correction still failed the four quote checks. Restricting
correction to exact source excerpts repaired those matches, but the audit exposed
missing technical explanations. The final replay adds the complete explanation
contract and reuses the same initial responses for a controlled comparison.

During a pause the separately managed local server changed to
`Qwen3.8-27B-GSQ-RCO-IQ3_XXS.gguf`. Full and reduced-input diagnostics on that
model exceeded the existing 240-second request timeout and were stopped. Those
attempts remain in `replay-20261013T083928Z` and `replay-20261013T084833Z`.
The requested model alias in config does not choose which GGUF llama.cpp loads;
capability diagnostics now also report the actual server model IDs.

The user explicitly selected the earlier Qwen3-30B setup. After confirming the
server was idle, the existing launch arguments were restored from
`D:\llama.cpp\run-llama-server_QWEN3_30B_A3B.cmd`. The restored server is healthy
and serves `Qwen3-30B-A3B-Instruct-2507-UD-Q4_K_XL.gguf`, with one slot and a
65,536-token context. No model file or launcher was modified. Restoration details
and server logs are recorded in the audit directory.

**Final result: all four full saved-source corrections passed quote/schema checks
and contain the required explanations.** Each reused its saved initial response
and made exactly one new correction call. All retained quotations also match the
original source text. The video was skipped with zero model calls. No shortened
source input was used in this final comparison.

| Sample | Correction result | Date/evidence classification before score filtering |
|---|---|---|
| RubberNews PDF | Exact quotes and explanations accepted | Unverified date |
| SCIEPublish recovered carbon black | Exact quotes and explanations accepted | Background |
| PMC urea/silane formulation | Exact quotes and explanations accepted | Background |
| Data Bridge tire-cord market | Exact quotes and explanations accepted | Unverified date; model says not new |
| YouTube without transcript | Skipped without model call | Unreadable |

Evidence: `data/audits/quality-fix-2026-10-13/replay-20261013T085807Z/summary.json`,
its per-source quality records, and `final-verification.json` in the parent directory.
The reclassified original-report preview is under that replay directory at
`reports/daily/daily-2026-10-13.html`.

Manual review found one concrete remaining interpretation issue: the RubberNews
confidence rationale calls the source peer-reviewed, which the provided excerpt
does not establish. This is recorded in `manual-review.json`; the quote validator
does not certify such narrative assertions. That item remains outside recent news.
Accepted evidence is not equivalent to fresh news or independent technical
validation. Matching excerpts can still be incomplete support for a broader
interpretation; human technical review remains necessary. This sample does not
establish the recovery rate for all 18 original quarantines.

## Preservation and operation

- Production reports and persisted research/delivery history are preserved.
- Model configuration, search settings, score weights, threshold, recipients,
  schedules, and email configuration are unchanged. The separately managed local
  server changed its loaded model during the session and was restored to Qwen3-30B
  at the user's explicit request, as documented above. No email was sent; live sending remains off.
- Baseline files and consistent SQLite snapshots are in
  `data/audits/quality-fix-2026-10-13/original*`; logical database comparisons are
  recorded in `preservation-check.json` in that directory.
- No model replacement, new dependency, database migration, or re-indexing is
  required. The next launched agent process uses the updated code. A completed
  edition rerun rerenders its saved items; it does not automatically research
  them again. The replay script is intentionally separate from production runs.

Reproduce offline tests from the project root with the existing environment:

```powershell
& 'C:\Users\shreeharsha.r\.conda\envs\tire-scout\python.exe' -m pytest tests -q --basetemp data/audits/quality-verification-new
```

Use a new project-local `--basetemp` path. To run a saved-source diagnostic:

```powershell
& 'C:\Users\shreeharsha.r\.conda\envs\tire-scout\python.exe' scripts/replay_quality.py --snapshot data/audits/quality-fix-2026-10-13
```

The local model must be running. The script creates a new isolated directory and
cannot send mail. `--repair-baseline <previous-summary.json>` reuses previous
initial responses and makes only the correction calls needed for comparison.

## Follow-up: fresh live preview on 13 October 2026

The user-authorized fresh preview completed in 16.25 minutes after a newly found Tavily recency-parameter bug was corrected. It found 20 URLs, fetched 13, removed one duplicate, and analyzed 12. Ten passed immediately and two after correction; zero were finally quarantined. The report retains zero verified recent updates, one background reference and seven review items. Full tests after this change: 145 passed, one skipped. No email was sent and production data remains unchanged.

See [the fresh-run review](../data/runs/fresh-preview-2026-10-13-02/RUN_REVIEW.md) for the recency fix and remaining discovery, source-type, date extraction, narrative interpretation and fetch-hostname issues. Automatic delivery remains off.
