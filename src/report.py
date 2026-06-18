"""
Assemble the written analysis report (Markdown) plus supporting data matrices.

The report is organized around the boss's question set: technologies, advantages,
challenges/disadvantages, design guidelines, process challenges, trade-offs — and
adds a patent-landscape layer (top assignees, yearly trend, discovered topics).

Every claim in the report is backed by extracted passages with their patent ids,
so the analyst can drill from any statement to the source patent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from . import domain
from .classify import category_columns


def _normalize_assignee(name: str) -> str:
    """Collapse country tags and multi-assignee strings to one primary name.

    "MICHELIN & CIE (FR); MICHELIN RESEARCH..." -> "MICHELIN & CIE"
    so the same company is not counted several times.
    """
    s = str(name).split(";")[0]               # primary assignee
    s = re.sub(r"\([A-Z]{2}\)", "", s)         # drop "(JP)", "(FR)", ...
    s = re.sub(r"\s+", " ", s).strip(" .,")
    return s.upper()


def _top_assignees(patents: pd.DataFrame, n: int) -> pd.DataFrame:
    a = patents.copy()
    a["assignee"] = a["assignee"].fillna("").map(_normalize_assignee)
    a = a[a["assignee"] != ""]
    counts = a["assignee"].value_counts().head(n).reset_index()
    counts.columns = ["assignee", "patents"]
    return counts


def _yearly_trend(patents: pd.DataFrame, floor: int = 1995) -> pd.DataFrame:
    t = patents.dropna(subset=["year"]).copy()
    if t.empty:
        return pd.DataFrame(columns=["year", "patents"])
    t["year"] = t["year"].astype(int)
    # Drop garbage priority years from malformed date parsing.
    t = t[t["year"] >= floor]
    out = t.groupby("year").size().reset_index(name="patents").sort_values("year")
    return out


def _category_examples(passages: pd.DataFrame, cat: str, k: int = 6) -> list[dict]:
    col = f"cat_{cat}"
    sub = passages[passages[col] > 0].sort_values(col, ascending=False)
    # One example per patent so a single verbose patent cannot fill the list.
    sub = sub.drop_duplicates("doc_id").head(k)
    out = []
    for _, r in sub.iterrows():
        snippet = r["passage"]
        snippet = (snippet[:320] + "…") if len(snippet) > 320 else snippet
        out.append({"doc_id": r["doc_id"], "assignee": r["assignee"], "text": snippet})
    return out


def build_report(
    patents: pd.DataFrame,
    passages: pd.DataFrame,
    cluster_terms: dict,
    method: str,
    cfg: dict,
    out_dir: str | Path,
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rcfg = cfg["report"]

    # --- supporting data matrices (CSV) ------------------------------------
    assignees = _top_assignees(patents, rcfg["top_assignees"])
    trend = _yearly_trend(patents)
    assignees.to_csv(out_dir / "top_assignees.csv", index=False)
    trend.to_csv(out_dir / "yearly_trend.csv", index=False)
    patents.to_csv(out_dir / "relevant_patents.csv", index=False)

    # assignee x category matrix (who works on what)
    cat_cols = category_columns()
    pa = passages.copy()
    pa["assignee"] = pa["assignee"].fillna("").str.strip().replace("", "Unknown")
    matrix = pa.groupby("assignee")[cat_cols].apply(lambda d: (d > 0).sum())
    matrix = matrix.loc[matrix.sum(axis=1).sort_values(ascending=False).index]
    matrix.head(rcfg["top_assignees"]).to_csv(out_dir / "assignee_category_matrix.csv")

    # --- narrative report (Markdown) ---------------------------------------
    lines: list[str] = []
    w = lines.append

    w(f"# {rcfg['title']}\n")
    w(f"*Corpus: {len(patents)} relevant patents · {len(passages)} extracted "
      f"passages · technology discovery via {method}.*\n")

    w("> Scope: passenger-car radial (PCR) tires, zero-degree (cap-ply / overlay / "
      "jointless band) belt reinforcement, with emphasis on hybrid (e.g. "
      "aramid/nylon) cord constructions. Each finding links to source patents.\n")

    # Executive landscape
    w("## 1. Patent landscape\n")
    w(f"- **Relevant patents in scope:** {len(patents)}")
    hybrid_n = int((patents.get("hybrid_hits", pd.Series(dtype=int)) > 0).sum())
    w(f"- **Explicitly hybrid-cord patents:** {hybrid_n}")
    if "legal_status" in patents.columns:
        ls = patents["legal_status"].astype(str).str.upper().str.strip()
        alive, dead = int((ls == "ALIVE").sum()), int((ls == "DEAD").sum())
        if alive or dead:
            w(f"- **Legal status:** {alive} alive · {dead} dead")
    yrs = patents["year"].dropna()
    if not yrs.empty:
        recent = int((yrs.astype(int) >= 2015).sum())
        w(f"- **Priority year ≥2015:** {recent} · **<2015:** {len(yrs) - recent} "
          f"(records without a year: {int(patents['year'].isna().sum())})")
    if not trend.empty:
        peak = trend.loc[trend["patents"].idxmax()]
        w(f"- **Peak filing year:** {int(peak['year'])} ({int(peak['patents'])} patents)")
    w("\n### Top assignees\n")
    if assignees.empty:
        w("_No assignee data present in the exports._\n")
    else:
        w("| Assignee | Patents |\n|---|---|")
        for _, r in assignees.iterrows():
            w(f"| {r['assignee']} | {r['patents']} |")
        w("")
    if not trend.empty:
        w("### Filings per year\n")
        w("| Year | Patents |\n|---|---|")
        for _, r in trend.iterrows():
            w(f"| {int(r['year'])} | {int(r['patents'])} |")
        w("")

    # Discovered technology clusters
    w("## 2. Discovered technology clusters\n")
    w("Unsupervised clustering of passage embeddings surfaces recurring technical "
      "themes (\"hidden technologies\"). Review and rename these with a domain "
      "expert; the terms below are the cluster's most distinctive language.\n")
    sizes = passages.groupby("cluster").size().to_dict() if "cluster" in passages else {}
    for cid in sorted(cluster_terms, key=lambda c: sizes.get(c, 0), reverse=True):
        if cid == -1:
            continue  # BERTopic noise cluster
        terms = ", ".join(cluster_terms[cid][:cfg["report"]["top_terms_per_cluster"]])
        w(f"- **Cluster {cid}** ({sizes.get(cid, 0)} passages): {terms}")
    w("")

    # The boss's question framework
    section_no = 3
    for cat, spec in domain.CATEGORY_FRAMEWORK.items():
        w(f"## {section_no}. {spec['label']}\n")
        examples = _category_examples(passages, cat)
        if not examples:
            w("_No passages matched this category in the current corpus._\n")
        else:
            w(f"Representative evidence ({len(examples)} of "
              f"{int((passages[f'cat_{cat}'] > 0).sum())} matching passages):\n")
            for ex in examples:
                tag = ex["doc_id"] or "?"
                who = f" — {ex['assignee']}" if ex["assignee"] else ""
                w(f"- **[{tag}{who}]** {ex['text']}")
            w("")
        section_no += 1

    w(f"## {section_no}. Method & caveats\n")
    w("- Relevance screening uses tire-domain lexicons (zero-degree / hybrid / "
      "PCR-vs-TBR segment scoring); see `src/domain.py`.")
    w("- Technology discovery uses passage-level embeddings + clustering; cluster "
      "labels are machine-suggested and need expert naming.")
    w("- Category evidence is weak-supervision phrase matching — high recall, so "
      "spot-check borderline passages before quoting to the boss.")
    w("- Supporting CSVs in this folder: `relevant_patents.csv`, `top_assignees.csv`, "
      "`yearly_trend.csv`, `assignee_category_matrix.csv`.\n")

    report_path = out_dir / "analysis_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  wrote report -> {report_path}")
    return report_path
