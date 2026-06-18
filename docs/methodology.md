# Methodology

This document explains *why* the pipeline is built the way it is — useful when
defending the analysis to a boss or extending it.

## The domain question

"Hybrid zero-degree cords" in PCR tires = the **circumferentially wound
cap-ply / overlay / jointless band (JLB)** sitting over the steel belt package,
made from a **two-material (hybrid) cord** — most commonly **para-aramid + nylon
6.6 co-twisted**. The hybrid combines aramid's high modulus (restrains
centrifugal growth → high-speed durability) with nylon's elongation and
shrinkage behavior (ride comfort, processability).

The analysis must answer, for this construction: technologies used, advantages /
performance benefits, challenges / disadvantages, design guidelines, process
challenges, and trade-offs. These six axes are encoded directly as the
`CATEGORY_FRAMEWORK` in `src/domain.py`.

## Why these design choices

### 1. Domain lexicons matter more than the model
Patent + tire-chemistry language ("EPDM ends-per-dm", "RFL dip", "LASE",
"jointless band") is niche. The biggest accuracy lever is the curated vocabulary
in `src/domain.py`, not the embedding model. Everything there is expert-editable
without touching code.

### 2. Segment disambiguation is the key precision step
Belt/overlay patents are dominated by **truck (TBR)**, **off-road (OTR)** and
**aircraft** tires. A naive "zero degree belt" search returns mostly non-PCR
results. We score every document against per-segment lexicons and keep it only
when **PCR wins (or ties top)**, then hard-exclude configured segments.

### 3. Passage-level embeddings, not document-level
A patent is thousands of words spanning many ideas. Embedding it as one vector
blurs everything. We split into passages so one patent can populate several
technology clusters (adhesion, high-speed durability, shrinkage control, …).

### 4. Domain embedding model with graceful fallback
Preferred: **PatentSBERTa** (a sentence-transformer trained on patents) — base
BERT underperforms on this jargon. If the heavy ML stack is unavailable the
pipeline falls back to **TF-IDF**, so it always produces a report.

### 5. Unsupervised discovery for "hidden technologies"
**BERTopic** (UMAP + HDBSCAN + class-TF-IDF) surfaces recurring themes without
predefined labels — this is how latent / emerging technologies are found. Falls
back to **KMeans + top TF-IDF terms**. Cluster names are machine-suggested and
should be reviewed by a domain expert.

### 6. Weak-supervision category tagging
Mapping passages to the six report axes uses transparent phrase matching against
`CATEGORY_FRAMEWORK`. It needs no labeled data, is fully auditable, and is
high-recall by design — so reviewers should spot-check borderline passages.

## Limitations / honest caveats

- Recall depends on the **query used to create the exports** — combine CPC +
  keywords (see README).
- Phrase tagging can over-tag; treat the evidence lists as a shortlist, not gospel.
- Embedding clusters reflect the corpus; a thin corpus yields thin clusters.
- The pipeline reads what the export contains — if claims/description fields are
  empty (abstract-only export), depth drops accordingly. Prefer full-text exports.

## Suggested next extensions

- Add **legal-status / citation** data to weight commercially important patents.
- Add a **generative summarization** pass per cluster to auto-name topics and
  draft prose for the report (LLM over each cluster's passages).
- Add an **assignee timeline** plot (matplotlib) for the trend section.
