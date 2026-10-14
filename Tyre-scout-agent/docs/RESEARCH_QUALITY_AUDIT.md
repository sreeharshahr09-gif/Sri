# Research quality review: 13 October 2026 edition

Reviewed the saved LangGraph checkpoint, SQLite records, and current source code.
No research was rerun, no sources were refetched, and no application code,
configuration, completed edition, or delivery record was changed for this review.

## Conclusion

The SSL repair succeeded, but the successful workflow status does not establish
that the five selected items are recent, publication-ready developments. There
are two separate problems: avoidable evidence-match failures and insufficient
freshness controls on accepted items. Increasing the number of included items
alone would not solve either problem.

## Corrected filtering totals

| Stage | Count |
|---|---:|
| Discovered URLs | 40 |
| Unavailable: HTTP 403/404 or redirect limits | 7 |
| Successfully fetched | 33 |
| Exact-content duplicates | 3 |
| Analyzed candidates | 30 |
| Quote-match quarantines | 17 |
| Invalid-category quarantine | 1 |
| Excluded as not a new development | 7 |
| Excluded by score or item cap | 0 |
| Included in report | 5 |

Correction to the earlier conversational explanation: the seven remaining
exclusions were **not-new classifications**, not low scores. The checkpoint
records the reasons explicitly.

## Evidence validation

The validator folds case and whitespace, then demands that each quote be a
substring of the extracted source. This properly rejects rewritten or invented
quotes, but it also rejects some presentation differences. Examples include
PDF line-break hyphenation (`depend- ent`), superscript layout (`m 2 /g`), and
quotation marks the model adds around a passage.

Some mismatches are substantive. One model quote changes a formulation
identifier from `IR-Si/urea/Si-75` to `IR-Si/Si-75/urea`; another changes
`Steel cords` to `Tire cords`. These must not be automatically accepted by a
fuzzy-matching rule. A paraphrase may be a reasonable summary while still being
an invalid verbatim quote.

The model gets bounded retries for JSON/schema errors inside `llm.py`.
Grounding is checked later in `graphs/daily.py:201`, where any failure goes
straight to quarantine. Therefore `repair_attempts: 2` does **not** repair a
quote mismatch or an invalid configured category.

Only the first 60 characters of each failed quote are saved in its error message
(`graphs/daily.py:407`). The rejected analysis is not retained. Several saved
prefixes occur exactly in the source; their discarded tails could contain the
actual mismatch. An exact recovery count cannot be reconstructed from this run.

## Review of all 18 quarantines

These are diagnostic observations, not decisions to admit the rejected items.
Complete failed quotes are unavailable except where the entire short quote fits
in the saved prefix.

| Source | Observation from saved source and error |
|---|---|
| ACS: silyl-functionalized emulsion SBR | Saved prefix matches; full quote tail is needed. |
| EP2607103A2 | Generic generated wording is absent from the saved source; patent index text also crowds the extraction. |
| Springer: functionalized S-SBR | Model rearranges/rephrases the source statement. |
| PMC: urea and silane in isoprene rubber | Formulation identifier is reordered; do not normalize this away. |
| CN117777542A | Two generic quote prefixes are absent from the saved text; patent extraction contains index noise. |
| Springer: silica-silane state-of-the-art review | Unit/superscript spacing differs; another quote needs its missing tail checked. |
| RubberNews PDF | PDF word-breaking hyphens differ from model output. |
| Continental: Taraxagum | Both saved prefixes match; missing tails prevent a final diagnosis. |
| Continental: constructing tires from dandelions | Model changes a comma at the end of the passage to a full stop. |
| Data Bridge: tire cord market | Model broadens Steel cords to Tire cords. |
| FMI: tire cord fabric market | Model expands the subject of a sentence; the market figure exists but the quote is rewritten. |
| Continental motorcycle technologies | Saved prefix matches; full quote tail is needed. |
| GreyB: reducing rolling resistance | Statement is rephrased. |
| Motorcycle.com: Metzeler steel-belted radials | Several model prefixes add quotation marks; full tails are unavailable. |
| AWS: process digital twin | Both saved prefixes match; full tails are needed. |
| YouTube: Michelin machine learning | Only 145 characters of navigation/footer were extracted, without a transcript; category was the invalid string None. |
| SCIEPublish: recovered carbon black | Superscript unit is split by extracted line breaks and recombined by the model. |
| Weibold: sustainable carbon black | Saved prefix matches; full quote tail is needed. |

## Accepted-item freshness and classification

The included hybrid aramid/nylon patent has stored source date **2012-07-24**
and `is_background=true`, yet it is included at score 9.25. The scorer/selector
does not exclude background records. The other four accepted items have no
stored publication date. Two discuss dandelion programs with 2021/2022 events
in their accepted source evidence; this alone does not establish a recent update.

All five accepted records are stored as `maturity=speculation` and
`confidence_label=low`, despite scores between 8.85 and 9.25. Those are defaults
permitted by the analysis schema. Persisted records do not tell us whether the
model explicitly selected them or omitted those fields. Classification and
scoring therefore need a consistency check, not a lower inclusion threshold.

| Accepted item | Stored date | Background flag | Score |
|---|---|---|---:|
| Functionalized SBR / silica reference | Unknown | False | 8.95 |
| Goodyear domestic dandelion rubber program | Unknown | False | 8.85 |
| Dandelion-based rubber overview | Unknown | False | 9.05 |
| Hybrid aramid/nylon tire-cord patent | 2012-07-24 | True | 9.25 |
| Supplier tire-wear prediction webpage | Unknown | False | 9.25 |

The current background calculation uses a fixed 45-day age test during
deduplication. The final daily selector uses score and item cap without an
enforced recency gate. Search-provider recency preferences did not prevent old
or undated pages from being discovered.

## Extraction limits

14 of the 30 analyzed source texts were truncated before analysis. The tool
keeps only the first 20,000 characters (`tools.py:86`) but retains the original
text-length and extraction-quality metadata. As a result, truncated texts can
still be labelled full. Patent front matter and indexing tables can consume much
of that allowance. This does not prove truncation caused a specific failed quote,
but it should be visible to the model and report reviewer.

The YouTube page demonstrates a second issue: receiving HTML is not equivalent
to receiving the video's substantive content. Footer-only text should be marked
unreadable or transcript-unavailable before invoking analysis.

## Recommended implementation sequence

1. **Retain diagnostic evidence.** Save the full rejected structured response,
   source hash, analyzed excerpt, full failed quotes, and validation reasons.
   Keep secrets out of these records.
2. **Improve quote production and repair.** Prefer selecting short, traceable
   source excerpts. On grounding failure, offer a bounded correction attempt
   using the same source and exact failure details. Apply conservative,
   auditable typography handling; preserve numbers, signs, units, formulation
   identifiers, and negations. Do not accept approximate scientific claims.
3. **Enforce freshness separately from relevance.** Date-verified recent updates,
   useful historical background, and date-unverified material should be clearly
   separated. A high relevance score must not turn old material into fresh news.
4. **Make limitations explicit.** Mark truncated text, reject empty page shells,
   and require deliberate maturity/confidence classification with score checks.
5. **Replay a bounded sample first.** Use the saved sources to test both formatting
   cases and substantive mismatches before another full production run. Preserve
   the existing edition while comparing results in a separate audit output.

These changes have **not** been implemented in this review. The original five
items remain available as a draft requiring review; sending stays disabled.

## Evidence files

- `data/audits/research-quality-2026-10-13/audit.json`: per-source diagnostics,
  corrected counts, accepted-item metadata, and recorded exclusions.
- `data/audits/research-quality-2026-10-13/review-source-config-sha256.json`:
  source/configuration fingerprints at review time.
- Original evidence: `data/checkpoints.sqlite`, thread `daily-2026-10-13`, and
  `data/tire_scout.sqlite3`, edition `2026-10-13`.
