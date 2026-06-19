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
`scripts/nylon_aramid_solutions.py`, `scripts/pet_nylon_solutions.py`.

---

# Addendum — PET-Nylon comparison cohort (2026-06-19)

Added a parallel "how it tackles wear vs. baseline" study for **PET-Nylon
(polyester + polyamide)** cords, mirroring the Nylon-Aramid study, with its own
audit. New deck slides 8–9 + chart `output/deck_pet_vs_na.png`.

## PET-Nylon cohort definition (strict, audited)
A patent qualifies only on an **explicit PET+polyamide construction**, one of:
1. **Explicit pairing** — `polyester/nylon`, `pet/nylon`, `polyester/polyamide`
   joined as one cord (5 patents).
2. **PET/nylon core-sheath** — one cord, PET core + nylon sheath or vice-versa
   (3 patents).
3. **Zoned PET-vs-PA bandage** — a fibre actually *assigned to a zone*, requiring
   BOTH a PET zone AND a polyamide zone (center/shoulder/side), e.g. Continental
   EP3912833A1 (centre PET / PA side) and WO2019206477A1 ("side portions of pa6.6
   and the center portion pet").
A **carcass-list guard** drops patents whose only PET+nylon evidence is a generic
"carcass cords made of polyester, nylon, rayon, …" options list.

## Audit findings (PET-Nylon)
| # | Check | Finding | Action |
|---|---|---|---|
| 1 | **List inflation** | A loose "both materials co-occur near a zone word" rule gave **32** patents, but spot-check found ~10 were generic carcass/organic-fibre *materials lists* (same trap as Nylon-Aramid), precision ~65% | **Required BOTH materials zone-assigned + carcass guard: 32 → 13** |
| 2 | **Anchor recovery** | The tightened rule must not drop the genuine Continental PET/PA anchors | EP3912833A1 & DE102016223304B4 both **retained** |
| 3 | **Overlap with Nylon-Aramid** | 5 of 13 PET-Nylon patents also carry an aramid+nylon construction (multi-material bandages, e.g. EP2829419/420B1) | **Disclosed on slide 9 and below** |
| 4 | **Magnitude stability** | n=13 makes enrichment *multiples* noisy (zoned shows 13.8×) | Charts keep the figure; **prose drops the number** and slide 9 states "interpret enrichment as direction, not magnitude" |

## PET-Nylon headline numbers
| Metric | Value |
|---|---|
| PET-Nylon cohort (strict) | **13** |
| …with a wear claim | **6 (46%)** |
| …also Nylon-Aramid | 5 |

## Comparative finding (the requested study)
Both hybrids raise treadwear/mileage **indirectly** (crown stiffness + footprint
control), but by **different levers**:
- **Nylon-Aramid → the CORD itself**: core-sheath / 2-materials-in-1-cord (**2.0×**
  baseline), dual-modulus (1.9×).
- **PET-Nylon → the LAYOUT**: center-vs-edge cord layout / zoned bandage
  (**most-enriched approach by far**; direction robust, magnitude noisy at n=13).
- **Shared**: dual-modulus stiffness step, cord twist & geometry, cord-to-rubber
  adhesion (all ~1.5–1.9× in both).

This **quantifies** the earlier qualitative slide ("zoned density is PET/PA, not
Nylon-Aramid"): zoning is the PET/PA wear lever, the engineered cord is the
Nylon-Aramid wear lever.

## Residual limitations (PET-Nylon)
- **Thin evidence**: 13 patents (6 wear). Treat enrichment as *direction*, not a
  precise multiple. A manual full-text read of the 13 is the recommended next step.
- PET-Nylon "hybrid" is mostly a **per-zone** construction, not a single
  co-twisted filament — conceptually different from the Nylon-Aramid cord, which
  is why the two cohorts are near-disjoint (5 shared).
