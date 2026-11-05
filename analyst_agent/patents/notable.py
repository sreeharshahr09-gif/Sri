"""Notable patents: the analysed set's most valuable and most novel patents, as cards with their figures.

Value and novelty are computed, not judged by the model:
- value: the crown-jewel score (forward citations when exported, relevance, claim breadth, recency), and whether
  the patent is alive when the export has a legal status;
- novelty: how rare the patent's problem × technology combination is in this set, and how few patents sit
  near it in the text-embedding space (its distance to its nearest neighbours).
The cards rank by interest, the mean of the two. The model then writes one line on why each patent matters and
one on what to check, from the patent's own fields; its figures come from the patent PDF (figures.py).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from . import analytics as an
from . import figures as fg
from .figures import Figure
from .llmjson import JSONReplyError, LLMError, ask_json_list

NEIGHBOURS = 5
_PLACEHOLDER = {"", "not stated", "none", "not specified", "nan", "n/a"}


@dataclass
class Notable:
    index: object  # the patent's row label in corpus.std
    id: str
    title: str
    assignee: str
    office: str
    year: int | None
    relevance: str
    alive: str
    segment: str
    value: float
    novelty: float
    interest: float
    reasons: list[str] = field(default_factory=list)
    badges: list[str] = field(default_factory=list)
    summary: str = ""
    why: str = ""
    check: str = ""
    link: str = ""
    figures: list[Figure] = field(default_factory=list)
    figure_note: str = ""
    fields: str = ""  # what the model is given (and its numbers are checked against)


def _stated(value) -> str:
    text = " ".join(str(value if value is not None else "").split())
    return "" if text.lower().rstrip(".") in _PLACEHOLDER else text


def _isolation(a: pd.DataFrame, std: pd.DataFrame, embeddings: np.ndarray | None) -> pd.Series | None:
    """Mean cosine distance of each patent to its nearest neighbours in the analysed set (higher = more alone)."""
    if embeddings is None or len(a) <= NEIGHBOURS:
        return None
    pos = std.index.get_indexer(a.index)
    if (pos < 0).any() or len(embeddings) != len(std):
        return None
    vectors = np.asarray(embeddings, dtype=np.float32)[pos]
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True).clip(1e-9)
    out = np.empty(len(vectors), dtype=np.float32)
    for start in range(0, len(vectors), 512):  # in blocks: a 5,000-patent set needs no 5,000 × 5,000 matrix
        sims = vectors[start:start + 512] @ vectors.T
        for row in range(sims.shape[0]):
            sims[row, start + row] = -1.0  # not its own neighbour
        nearest = np.partition(sims, -NEIGHBOURS, axis=1)[:, -NEIGHBOURS:]
        out[start:start + 512] = 1 - nearest.mean(axis=1)
    return pd.Series(out, index=a.index)


def _combination_counts(a: pd.DataFrame) -> pd.DataFrame | None:
    """Per patent: its rarest problem × technology group combination and how many patents share it."""
    if "problem" not in a or "tech_group" not in a:
        return None
    pairs = a.assign(_problem=a["problem"].fillna("").str.split(r"\s*;\s*")).explode("_problem")
    pairs = pairs[~pairs["_problem"].isin(an.NOT_SPECIFIED | {"Other"}) & (pairs["tech_group"] != an.UNCLASSIFIED)]
    if pairs.empty:
        return None
    sizes = pairs.groupby(["_problem", "tech_group"]).size()
    pairs["_count"] = [sizes[(p, g)] for p, g in zip(pairs["_problem"], pairs["tech_group"])]
    rarest = pairs.sort_values("_count", kind="stable").groupby(level=0).head(1)
    return rarest[["_problem", "tech_group", "_count"]]


def score(a: pd.DataFrame, std: pd.DataFrame, embeddings: np.ndarray | None = None) -> pd.DataFrame:
    """value, novelty and interest (0–100) for every analysed patent, with the facts behind them."""
    out = pd.DataFrame(index=a.index)
    value = an.crown_jewel_scores(a)
    if "alive" in a:
        value = 0.8 * value + 20 * (a["alive"] == "Yes")
    out["value"] = value.round(0)
    combos = _combination_counts(a)
    parts = []
    if combos is not None:
        counts = combos["_count"].reindex(a.index)
        out["combination"] = (combos["_problem"] + " × " + combos["tech_group"]).reindex(a.index).fillna("")
        out["combination_patents"] = counts
        parts.append((-counts.fillna(counts.median() if counts.notna().any() else 0)).rank(pct=True))
    isolation = _isolation(a, std, embeddings)
    if isolation is not None:
        out["isolation"] = isolation
        parts.append(isolation.rank(pct=True))
    out["novelty"] = (100 * sum(parts) / len(parts)).round(0) if parts else 0.0
    out["interest"] = ((out["value"] + out["novelty"]) / 2).round(1)
    return out


def _reasons(row: pd.Series, s: pd.Series, a: pd.DataFrame, std: pd.DataFrame) -> list[str]:
    reasons = []
    count = s.get("combination_patents")
    if pd.notna(count) and s.get("combination") and count <= max(2, 0.02 * len(a)):
        reasons.append(f"rare combination: {s['combination']} ({int(count)} patent{'s' if count != 1 else ''})")
    if "isolation" in s and pd.notna(s["isolation"]) and s["isolation_pct"] >= 0.9:
        reasons.append("few similar patents in this set")
    if "cluster_outlier" in std and bool(std.at[row.name, "cluster_outlier"]):
        reasons.append("outside every topic cluster")
    cited = row.get("cited_by")
    if pd.notna(cited) and cited > 0 and cited >= a["cited_by"].quantile(0.9):
        reasons.append(f"cited by {int(cited)}")
    if pd.notna(row.get("year")) and a["year"].notna().any() and row["year"] >= a["year"].max() - 2:
        reasons.append(f"recent ({int(row['year'])})")
    if row.get("alive") == "Yes":
        reasons.append("alive")
    return reasons


def pick(a: pd.DataFrame, std: pd.DataFrame, embeddings: np.ndarray | None = None, n: int = 10,
         within: pd.Index | None = None) -> list[Notable]:
    """The `n` patents with the highest interest, as cards without text and figures yet. Scores are relative to
    the whole analysed set; `within` limits the choice (e.g. to some technology groups)."""
    if a.empty or n <= 0:
        return []
    scores = score(a, std, embeddings)
    if "isolation" in scores:
        scores["isolation_pct"] = scores["isolation"].rank(pct=True)
    pool = scores if within is None else scores.loc[scores.index.intersection(within)]
    top = pool.sort_values(["interest", "value"], ascending=False, kind="stable").head(n)
    best_value = set(scores["value"].nlargest(n).index)
    best_novelty = set(scores["novelty"].nlargest(n).index)
    out = []
    for idx, s in top.iterrows():
        row = a.loc[idx]
        badges = [{"High": "Core", "Partial": "Adjacent"}.get(row.get("relevance", ""), "")]
        badges += ["Top value"] if idx in best_value else []
        badges += ["Top novelty"] if idx in best_novelty else []
        alive = str(row.get("alive", "") or "")
        badges += {"Yes": ["Alive"], "No": ["Dead"]}.get(alive, [])
        claim, params = _stated(row.get("core_claim")), _stated(row.get("key_parameters"))
        summary = claim or _stated(row.get("first_claim"))[:300] or str(row.get("title", ""))
        if params:
            summary += ("" if summary.endswith(".") else ".") + f" Claimed: {params}."
        fields = {k: _stated(row.get(k)) for k in ("problem", "tech_group", "component", "design_lever",
                                                   "performance_target", "mechanism", "core_claim", "key_parameters",
                                                   "trade_offs")}
        year = None if pd.isna(row.get("year")) else int(row["year"])
        out.append(Notable(
            index=idx, id=str(row["id"]), title=str(row.get("title", "")), assignee=str(row.get("assignee", "")),
            office=str(row.get("country", "")), year=year, relevance=str(row.get("relevance", "") or ""), alive=alive,
            segment=_stated(row.get("segment")), value=float(s["value"]), novelty=float(s["novelty"]),
            interest=float(s["interest"]), reasons=_reasons(row, s, a, std), badges=[b for b in badges if b],
            summary=summary, link=str(std.at[idx, "pdf_link"]) if "pdf_link" in std else "",
            fields="; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in fields.items() if v),
        ))
    return out


NOTES_SYSTEM = """\
You write short notes on notable patents for an R&D team's patent landscape. For each patent you get its \
number, title, assignee, year, why it was picked, and the facts read from it.
For each patent write:
- "why": one sentence (at most 30 words) on what is new in it and why it matters for the study;
- "check": one sentence on what to verify in the full text before relying on it (a claimed range, the claim's \
scope, a test condition).
Use only the facts given; every number you write must appear in them. No promotional wording.
Reply with only a JSON list: [{"ref": "N1", "why": "...", "check": "..."}, ...]\
"""


def write_notes(notables: list[Notable], study: str, llm) -> str:
    """Fills `why` and `check` (the model, one request; a plain fallback without it). Returns a note for the
    methodology."""
    from .narrative import unmatched_numbers

    def fallback(n: Notable) -> None:
        facts = dict(part.split(": ", 1) for part in n.fields.split("; ") if ": " in part)
        lever, target = facts.get("design lever", ""), facts.get("performance target", "")
        n.why = n.why or (f"{lever[:1].upper()}{lever[1:]} for {target}." if lever and target else
                          facts.get("mechanism", "") or n.title)
        n.check = n.check or "Check the claimed values and the scope of the independent claim in the full text."

    if not notables:
        return ""
    if llm is None:
        for n in notables:
            fallback(n)
        return "Notes from the patents' own fields (no model connected)."
    lines = [f"Study: {study}", ""]
    for i, n in enumerate(notables, start=1):
        lines.append(f"N{i} | {n.id} | {n.title} | {n.assignee} | {n.year or 'n/a'} | picked for: "
                     f"{', '.join(n.reasons) or 'value and novelty'} | {n.fields}")
    try:
        items = ask_json_list(llm, NOTES_SYSTEM, "\n".join(lines), retries=1, max_tokens=4000)
    except (LLMError, JSONReplyError, Exception) as exc:  # any failure: the cards keep the patents' own fields
        for n in notables:
            fallback(n)
        return f"The model could not write the notes ({str(exc)[:100]}); they come from the patents' own fields."
    written = 0
    for item in items:
        m = re.search(r"\d+", str(item.get("ref", "")))
        if not m or not 1 <= int(m.group()) <= len(notables):
            continue
        n = notables[int(m.group()) - 1]
        why, check = (" ".join(str(item.get(k, "")).split())[:400] for k in ("why", "check"))
        source = f"{n.fields} {n.title} {n.year or ''}"
        if why and not unmatched_numbers(why, source):  # a number the patent does not state: not used
            n.why, written = why, written + 1
        if check and not unmatched_numbers(check, source):
            n.check = check
    for n in notables:
        fallback(n)
    return f"The model wrote the notes for {written} of {len(notables)} patents from their own fields; numbers checked."


def vision_client(llm, model: str):
    """The client that looks at the figures: the model server's address and key (or LLM_VISION_BASE_URL and
    LLM_VISION_API_KEY) with the vision model. None without a model name; a test double is used as it is."""
    from ..config import thinks_in
    from ..llm import LLMClient

    if not model.strip() or llm is None:
        return None
    if not isinstance(llm, LLMClient):
        return llm
    cfg = llm.config
    vision = replace(cfg, model=model.strip(), base_url=os.environ.get("LLM_VISION_BASE_URL") or cfg.base_url,
                     api_key=os.environ.get("LLM_VISION_API_KEY") or cfg.api_key, max_tokens=2000,
                     temperature=min(cfg.temperature, 0.1), no_think=True, read_timeout=max(cfg.read_timeout, 300))
    return LLMClient(replace(vision, think=thinks_in(vision)), session=llm.session)


def illustrate(cards: list[Notable], std: pd.DataFrame, llm=None, vision_model: str = "", limit: int = 2,
               progress=None) -> tuple[int, int]:
    """Each card's PDF and figures (a vision model picks and describes them when one is named). Returns (PDFs
    fetched, cards whose figures the vision model chose)."""
    import requests

    session = requests.Session()
    vision = vision_client(llm, vision_model)
    fetched = described = 0
    for i, card in enumerate(cards):
        if progress is not None:
            progress(i, len(cards))
        chooser = None
        if vision is not None:
            row = std.loc[card.index] if card.index in std.index else {}
            context = (f"Patent {card.id}: {card.title}. Abstract: {str(row.get('abstract', ''))[:700]} "
                       f"First claim: {str(row.get('first_claim', ''))[:700]}")
            chooser = fg.vision_chooser(vision, context, limit)
        pdf = fg.patent_figures(card.id, card.link, session=session, limit=limit, chooser=chooser)
        card.figures, card.figure_note = pdf.figures, pdf.note
        card.link = card.link or pdf.url
        fetched += pdf.data is not None
        described += any(f.description for f in pdf.figures)
    if progress is not None:
        progress(len(cards), len(cards))
    return fetched, described


@dataclass
class Gallery:
    """The top patents of the technology groups the user chose, with their figures (an output on the page)."""

    groups: list[str]
    cards: list[Notable]
    note: str = ""
    study: str = ""


def facts(notables: list[Notable]) -> str:
    """The cards as facts the report text may quote."""
    if not notables:
        return ""
    return "Notable patents (highest interest = mean of value and novelty): " + "; ".join(
        f"{n.id} ({n.assignee}, {n.year or 'n/a'}): value {n.value:.0f}, novelty {n.novelty:.0f}"
        + (f", {', '.join(n.reasons[:2])}" if n.reasons else "") for n in notables) + "."


def table(notables: list[Notable]) -> pd.DataFrame:
    return pd.DataFrame([{
        "Patent ID": n.id, "Title": n.title, "Assignee": n.assignee, "Year": n.year, "Office": n.office,
        "Relevance": n.relevance, "Alive": n.alive, "Value": n.value, "Novelty": n.novelty, "Interest": n.interest,
        "Why picked": ", ".join(n.reasons), "Why it matters": n.why, "Check": n.check,
        "Figures": "; ".join(f.label for f in n.figures) or n.figure_note, "Link": n.link,
    } for n in notables])
