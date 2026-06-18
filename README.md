# Hybrid Zero-Degree Cords in PCR Tires — Patent Analysis Pipeline

Extracts the technologies, advantages, challenges, design guidelines, process
issues, and trade-offs around **hybrid zero-degree (cap-ply / overlay / jointless
band) cords in passenger-car radial (PCR) tires** from a corpus of patents
(2015–present), and produces a written analysis report.

You provide the patent exports; the pipeline ingests, screens, embeds, clusters,
and writes the report.

## Quick start

```bash
pip install -r requirements.txt

# 1. Drop your patent exports (CSV / XLSX / XML / PDF) into:
#       data/raw/
# 2. Run:
python run.py

# Output lands in  output/analysis_report.md  (+ supporting CSV matrices)
```

Try it first on the bundled **synthetic** sample (not real patents):

```bash
python run.py --demo          # writes output_demo/analysis_report.md
```

## How it works

```
data/raw/*  (CSV/XLSX/XML/PDF exports)
   │  ingest.py     normalize many vendor schemas -> one table, dedup by doc id
   ▼
screened corpus
   │  filter_pcr.py PCR-vs-TBR/OTR segment scoring + zero-degree/hybrid gating + year window
   ▼
passages
   │  segment.py    split into passage-level chunks (a patent spans many topics)
   │  classify.py   weak-supervision tagging into the boss's category framework
   ▼
embeddings
   │  embed.py      PatentSBERTa (semantic)  ->  TF-IDF fallback if unavailable
   ▼
technology clusters
   │  discover.py   BERTopic  ->  KMeans + TF-IDF terms fallback
   ▼
output/analysis_report.md   report.py  (landscape + clusters + category evidence)
```

## What you get

- `output/analysis_report.md` — narrative report structured as: patent landscape,
  discovered technology clusters, then a section per question
  (**technologies, advantages, challenges/disadvantages, design guidelines,
  process challenges, trade-offs**), each backed by quoted passages with patent IDs.
- `output/relevant_patents.csv` — the screened corpus with scores.
- `output/top_assignees.csv`, `output/yearly_trend.csv` — landscape data.
- `output/assignee_category_matrix.csv` — who works on what (assignee × category).

## Preparing your exports

Any of these work; mix formats freely in `data/raw/`:

| Source | Recommended export | Notes |
|---|---|---|
| Lens.org | CSV (with abstract + claims + applicants) | best free global family coverage |
| Espacenet | CSV / XML | enable English abstracts |
| PatentsView | CSV | US-only, easy to automate |
| Derwent / PatSnap / Orbit | CSV / XLSX | best dedup & legal status |
| Individual PDFs | PDF | one patent per file; full text extracted |

**Query design** — combine CPC classes with keywords for recall (see
`src/domain.py → CPC_CLASSES`). Core CPC: `B60C9/18,20,22,28`,
`B60C2009/2009/2074/0014`, `D02G3/00`, `D07B`. Core keywords: *zero degree, cap
ply, overlay, jointless band, spirally wound, hybrid cord, aramid, nylon 6.6*.

## Tuning

Almost everything domain-specific lives in **`src/domain.py`** — segment
lexicons, relevance terms, and the category framework. Edit those (no code
changes) to refine precision/recall. Pipeline behavior (year window, models,
cluster count) is in **`config.yaml`**.

## Notes & caveats

- Without `sentence-transformers`/`bertopic` installed, the pipeline still runs
  using TF-IDF + KMeans — lower semantic quality but fully functional.
- Category tagging is high-recall phrase matching: **spot-check before quoting.**
- Cluster labels are machine-suggested; name them with a domain expert.
- The bundled sample data is **synthetic** and for smoke-testing only.
- See `docs/methodology.md` for the rationale behind each step.
