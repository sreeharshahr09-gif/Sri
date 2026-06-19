# Pre-Publication Audit — Hybrid Cord Wear Analysis

Audit of the hybrid-cord / Nylon-Aramid wear analysis before publication.
Result: the **qualitative conclusions hold**, but several **quantitative figures
and cited examples were wrong and have been corrected**.

## Checks performed and findings

| # | Check | Finding | Action |
|---|---|---|---|
| 1 | Patent-family double counting | Clean — 1,816 unique Simple Family IDs, 0 duplicate rows | none |
| 2 | Benefit/defensive tag precision | Clean — only 2/69 "benefit" tags were "without degrading wear" hedges | none |
| 3 | **Nylon-Aramid cohort precision** | **Overcounted ~3×.** Loose "aramid within 40 chars of nylon" mostly caught materials *lists*, not hybrid cords (sample precision ~2–3/12) | **Strict definition: 318 → 100** |
| 4 | **Solution-taxonomy chart** | Bars mostly tracked baseline tire vocabulary. Only core-sheath & dual-modulus & twist are enriched in Nylon-Aramid | **Reframed as enrichment vs. baseline** |
| 5 | Motorcycle contamination | 92 primary-motorcycle patents (8%) were labeled PCR; cited examples Pirelli US11046113B2 & Sumitomo JP2018184071A are motorcycle | **92 removed; examples swapped** |
| 6 | Cited zoned-density examples | Continental EP3912833A1 / DE102016223304B4 are **PET/PA**, not Nylon-Aramid | **Relabeled as PET/PA; added correction slide** |
| 7 | "Mileage" framing | "mileage" named in only ~12 patents; it is a treadwear analysis | **Wording softened** |

## Corrected headline numbers

| Metric | Before (published draft) | After (audited) |
|---|---|---|
| PCR corpus | 1,172 | **1,080** (motorcycle removed) |
| General hybrid cohort | 507 | **156** |
| General hybrid w/ wear | 199 (39%) | **54 (35%)** |
| Nylon-Aramid cohort | 318 | **100** |
| Nylon-Aramid w/ wear | 115 (36%) | **31 (31%)** |

## What still holds
- Wear is a **secondary, indirect** benefit of hybrid zero-degree cords (crown
  stiffness + footprint uniformity, not abrasion chemistry).
- Nylon-Aramid leans **defensive** (fixing uneven wear) relative to a clean
  mileage claim.
- The **distinctive Nylon-Aramid wear levers are in the cord itself**:
  core-sheath/wrap (2.0×), dual-modulus (1.9×), twist tuning (1.8×).
- **Zoned-density** uneven-wear fixes are a **PET/PA** story, *not* Nylon-Aramid
  (3%, 1.3× — not distinctive).

## Residual limitations (documented on the deck's Limitations slide)
- Text-mined cohorts (PatSeer fields + AI summaries); high recall — spot-check
  individual patents before quoting.
- Nylon-Aramid wear evidence is **thin** (31 patents, mostly legally dead, few
  with clean method snippets) — interpret with care.
- Corpus skews pre-2015; legal status as of the export date.

## Reproducibility
All numbers derive from `scripts/cohorts.py` (single source of truth) via
`scripts/build_ppt.py`, `scripts/hybrid_wear_analysis.py`,
`scripts/nylon_aramid_solutions.py`.
