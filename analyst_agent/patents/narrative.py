"""The report's words: the brief's sections, model-written text checked against the numbers, methodology.

The model only writes prose from the fact sheets the analytics computed. Every number in its text
is matched back to those facts; numbers that cannot be matched are flagged in the report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from ..llm import LLMClient, LLMError
from ..parsing import strip_thinking
from . import report as rb
from .llmjson import chat_text, structured_client

CANONICAL = ["summary", "visuals", "players", "clusters", "process", "gaps"]
SECTION_RULES = [  # first match wins; "Research gaps & future trends" is about gaps, not trend charts
    ("summary", r"summary|overview|key finding|highlight|executive|abstract"),
    ("gaps", r"gap|white.?space|future|opportunit|outlook|roadmap|recommend|next step"),
    ("process", r"process|manufactur|production"),
    ("players", r"player|competit|assignee|applicant|compan|crown|portfolio|\bwho\b|leader|actor|owner"),
    ("clusters", r"cluster|solution|technolog|innovation|approach|categor"),
    ("visuals", r"visuali|chart|graph|data|statistic|trend|figure|landscape"),
]
DEFAULT_HEADINGS = {
    "summary": "Executive summary", "visuals": "Data visualisations", "players": "Players and competitors",
    "clusters": "Technology clusters and solutions", "process": "Process and manufacturing",
    "gaps": "Research gaps and future trends",
}
MAX_FACTS = 9000


@dataclass
class Section:
    heading: str
    kinds: list[str]  # which exhibits it holds (empty: text only, written from all facts)
    text: str = ""  # the model's text (markdown)
    flagged: list[str] = field(default_factory=list)  # numbers in the text not found in the facts
    note: str = ""


def plan_sections(headings: list[str]) -> list[Section]:
    """The brief's headings in its order, each holding one kind of exhibit; kinds no heading claims get
    a default heading after the closest related section, so nothing computed is left out."""
    sections: list[Section] = []
    taken: set[str] = set()
    for heading in headings:
        kind = next((k for k, pattern in SECTION_RULES if re.search(pattern, heading, re.IGNORECASE)), None)
        sections.append(Section(heading, [kind] if kind and kind not in taken else []))
        if kind:
            taken.add(kind)
    for kind in CANONICAL:
        if kind in taken:
            continue
        rank = CANONICAL.index(kind)
        pos = 0
        for i, sec in enumerate(sections):
            if any(CANONICAL.index(k) < rank for k in sec.kinds):
                pos = i + 1
        sections.insert(pos, Section(DEFAULT_HEADINGS[kind], [kind]))
        taken.add(kind)
    return sections


# --------------------------------------------------------------------------- checking numbers

_NUMBER = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w])")


def numbers_in(text: str) -> list[str]:
    return [m.group(1) for m in _NUMBER.finditer(text or "")]


def _value(token: str) -> float:
    return float(token.replace(",", ""))


def unmatched_numbers(text: str, facts: str) -> list[str]:
    """Numbers in `text` that do not appear in `facts` (allowing for rounding). Small whole numbers
    (up to 10) are allowed: they are mostly list counts ("three groups", "top 5")."""
    known = [_value(t) for t in numbers_in(facts)]
    out: list[str] = []
    for token in numbers_in(text):
        value = _value(token)
        if value.is_integer() and value <= 10:
            continue
        decimals = len(token.split(".")[1]) if "." in token else 0
        tolerance = 0.5 * 10 ** (-decimals)
        if any(abs(value - k) <= tolerance + 1e-9 or (decimals == 0 and abs(value - round(k)) < 1e-9) for k in known):
            continue
        if token not in out:
            out.append(token)
    return out


# --------------------------------------------------------------------------- writing

SECTION_SYSTEM = """\
You write one section of a patent landscape report for an R&D team. You get the study brief, the \
heading of the section to write and the facts computed from the patent data.

Rules:
- Use only the facts given. Every number you write must appear in the facts; do not calculate new numbers.
- Name companies, patents and technologies only if they appear in the facts.
- Draw no conclusion the facts do not support. Say nothing about market maturity, saturation, crowding, \
legal status, freedom to operate or licensing unless a fact states it. When a fact says an analysis was not \
possible or found nothing, report that plainly as a limit of this data set, not as a finding about the field.
- The facts come from one patent export, not the whole industry: say "in this patent set" and draw no \
conclusion about the industry, the market or new entrants.
- Write in Markdown: 3 to 7 bullet points, each starting with a short bold phrase, then one final line \
"**Implication for R&D:** ...". At most 220 words. No heading, no preamble.
- Do what the brief asks for this section where the facts allow. If the brief asks for something that belongs \
under this heading and the facts do not cover it, say so in one bullet starting with "**Not covered by this \
data:**". Leave out what belongs under other headings, and the brief's specific questions: the report answers \
those in a section of its own.\
"""
SUMMARY_SYSTEM = """\
You write the executive summary of a patent landscape report for an R&D team. You get the study brief \
and the facts computed from the patent data.

Rules:
- Use only the facts given. Every number you write must appear in the facts; do not calculate new numbers.
- Name companies, patents and technologies only if they appear in the facts.
- Draw no conclusion the facts do not support. Say nothing about market maturity, saturation, crowding, \
legal status, freedom to operate or licensing unless a fact states it. When a fact says an analysis was not \
possible or found nothing, report that plainly as a limit of this data set, not as a finding about the field.
- The facts come from one patent export, not the whole industry: say "in this patent set" and draw no \
conclusion about the industry, the market or new entrants.
- White-space combinations are statistical leads, not tested for significance: recommend only those that make \
technical sense for the brief, as leads to check, and do not call them significant.
- Write in Markdown: 5 to 8 bullet points with the most important findings (each starting with a short bold \
phrase), then "**Recommended R&D directions:**" with 2 or 3 bullet points grounded in the gaps and trends. \
At most 320 words. No heading, no preamble.\
"""


def _clean_text(text: str, heading: str) -> str:
    text = strip_thinking(text or "").strip()
    text = re.sub(r"^```(?:markdown|md)?\s*\n(.*?)\n```$", r"\1", text, flags=re.DOTALL).strip()
    lines = text.splitlines()
    while lines and (lines[0].lstrip().startswith("#") or lines[0].strip().strip("*").strip() == heading.strip()):
        lines.pop(0)  # the model repeated the heading
    # A bold label line right after a bullet ("**Implication for R&D:** ...") would join that bullet.
    return re.sub(r"\n(?=\*\*[^*\n]+:?\*\*)", "\n\n", "\n".join(lines).strip())


_COMMON_WORDS = {"with", "that", "this", "these", "from", "what", "which", "where", "when", "does", "their", "each",
                 "have", "into", "patent", "patents", "brief", "data", "asks", "facts", "provided", "covered",
                 "present", "available", "specific", "number", "show"}


def _stem(word: str) -> str:
    for suffix in ("ments", "ment", "ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _content_words(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z]+", text.lower()) if len(w) >= 4 and w not in _COMMON_WORDS}


def drop_answered_gaps(text: str, answered: list[str]) -> str:
    """Removes "Not covered by this data" lines about questions the report answers in its own section: a section
    with few facts tends to list the brief's questions there even when told not to."""
    if not answered:
        return text
    questions = [_content_words(q) for q in answered]
    kept = []
    for line in text.splitlines():
        if "not covered by this data" in line.lower():
            words = _content_words(line)
            if any(q and len(words & q) >= max(3, 0.3 * len(q)) for q in questions):
                continue
        kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def section_facts(sec: Section, facts_by_kind: dict[str, str], all_facts: str) -> str:
    facts = all_facts if "summary" in sec.kinds or not sec.kinds else "\n".join(facts_by_kind.get(k, "") for k in sec.kinds)
    return facts.strip()[:MAX_FACTS]


def write_sections(sections: list[Section], facts_by_kind: dict[str, str], all_facts: str, brief: str,
                   answers: dict[str, str], llm, on_progress=None, instruction: str = "",
                   answered_elsewhere: list[str] | None = None) -> None:
    """Fills `text`/`flagged` of every section with model text; on failure, a note instead.
    `instruction`: the user's revision request when a section is rewritten. `answered_elsewhere`: the brief's
    questions the report answers in its own section, which the other sections must not call missing."""
    if llm is None:
        for sec in sections:
            sec.note = "No model connected: this section has charts and tables only."
        return
    client = llm
    if isinstance(llm, LLMClient):
        cfg = llm.config
        client = structured_client(llm, max(cfg.max_tokens, 3000), read_timeout=max(cfg.read_timeout, 600),
                                   temperature=min(cfg.temperature, 0.3))
    context = f"Study brief:\n{brief.strip()[:5000]}"
    if answers:
        context += "\n\nThe user's answers to the brief's questions:\n" + "\n".join(f"- {q} {a}" for q, a in answers.items())
    if answered_elsewhere:
        context += ("\n\nThe report answers these questions of the brief with code in its own section "
                    "(\"Brief-specific analyses\"); never write that they are missing or not covered:\n"
                    + "\n".join(f"- {q}" for q in answered_elsewhere))
    for i, sec in enumerate(sections):
        if on_progress:
            on_progress(i, len(sections))
        is_summary = "summary" in sec.kinds
        facts = section_facts(sec, facts_by_kind, all_facts)
        if not facts:
            continue
        ask = f"Section to write: {sec.heading}"
        if instruction:
            ask += (f"\nRevision requested by the user: {instruction.strip()}\n(The rules above still apply; if "
                    "the request conflicts with them, follow the rules.)")
        messages = [
            {"role": "system", "content": SUMMARY_SYSTEM if is_summary else SECTION_SYSTEM},
            {"role": "user", "content": f"{context}\n\n{ask}\n\nFacts:\n{facts}"},
        ]
        try:
            reply = chat_text(client, messages)
        except LLMError as exc:
            sec.note = f"The model could not write this section ({exc}); the charts and tables below are complete."
            continue
        sec.text = drop_answered_gaps(_clean_text(reply, sec.heading), answered_elsewhere or [])
        if not sec.text:
            sec.note = "The model returned no text for this section; the charts and tables below are complete."
            continue
        sec.flagged = unmatched_numbers(sec.text, facts + " " + brief)
    if on_progress:
        on_progress(len(sections), len(sections))


def section_blocks(sec: Section) -> list[rb.Block]:
    blocks: list[rb.Block] = []
    if sec.text:
        blocks.append(rb.markdown(sec.text))
        if sec.flagged:
            blocks.append(rb.note("Check before quoting: these figures in the text above were not found in the "
                                  f"computed results: {', '.join(sec.flagged)}."))
        blocks.append(rb.caption("Text written by the model from the computed results below; numbers are "
                                 "checked against them."))
    elif sec.note:
        blocks.append(rb.caption(sec.note))
    return blocks


# --------------------------------------------------------------------------- facts the analytics do not hold


def cluster_facts(topics: pd.DataFrame | None, total: int) -> str:
    if topics is None or topics.empty:
        return ""
    parts = []
    for row in topics.head(15).itertuples():
        group = f", {row.tech_group}" if getattr(row, "tech_group", "") else ""
        desc = f": {row.description}" if getattr(row, "description", "") else ""
        parts.append(f"{row.label} ({row.patents} patents, {100 * row.patents / max(total, 1):.0f} %{group}){desc}")
    return f"Topic clusters from the patent map ({len(topics)} clusters): " + "; ".join(parts) + "."


# --------------------------------------------------------------------------- methodology


DEFINITIONS = [
    ("Growth (CAGR)", ("Compound annual growth of patents per year between the first and last complete years; "
                       "years affected by the publication lag are left out.")),
    ("Publication lag", ("Applications publish about 18 months after filing, so filings from the last 18 months "
                         "before the export (dated by its latest publication) are incomplete and shaded in the "
                         "trend chart.")),
    ("Concentration (HHI)", ("Sum of squared assignee shares × 10,000: below 1,500 fragmented, 1,500–2,500 moderately "
                             "concentrated, above 2,500 concentrated.")),
    ("Momentum", ("A topic cluster's share of patents in the last three years divided by the share of all patents "
                  "in those years.")),
    ("White space", ("Problem × technology combinations with at most half the patents expected if solutions were "
                     "spread evenly (expected = row total × column total / all).")),
    ("Crown-jewel score", ("Weighted percentiles of forward citations (when exported), relevance, claim breadth "
                           "(shorter first claim) and recency; weights are given under the table.")),
    ("Off-topic screening", ("Patents the model rated Relevance: Low against the plan's scope are left out of "
                             "the analytics but kept in the CSV.")),
]
LIMITATIONS = [
    "Coverage is what the uploaded export contains: the search strategy behind it decides what is in scope.",
    "The model's per-patent answers can be wrong. Check the validation sample before relying on exact shares.",
    "Topic clusters depend on the text embedding; small clusters and their names are indicative.",
    "Counts are patent families (or publications where no family ID was exported), not individual filings.",
    "This is a technology landscape, not a legal opinion on validity, infringement or freedom to operate.",
]


def methodology_blocks(result) -> list[rb.Block]:
    corpus, plan = result.corpus, result.plan
    flow = corpus.flow
    blocks = [rb.heading("Methodology and limitations")]
    when = datetime.fromisoformat(result.started_at).strftime("%d %b %Y %H:%M UTC")
    source = (f"**Data:** {result.dataset_name or 'uploaded export'}"
              + (f" (SHA-256 {result.dataset_sha256[:12]}…)" if result.dataset_sha256 else "")
              + f", {flow[0].remaining:,} records; analysed on {when}.")
    blocks.append(rb.markdown(source))
    blocks.append(rb.table(pd.DataFrame({
        "Step": [f.step for f in flow],
        "Removed": pd.array([f.removed if i else None for i, f in enumerate(flow)], dtype="Int64"),
        "Remaining": [f.remaining for f in flow],
    }), title="How the patent set was built", caption=f"Year basis: {corpus.year_basis}."))
    methods = pd.DataFrame({"Method": list(result.methods), "Detail": list(result.methods.values())})
    blocks.append(rb.table(methods, title="Methods used in this run"))
    blocks.append(rb.table(pd.DataFrame(DEFINITIONS, columns=["Measure", "Definition"]), title="Definitions"))
    amap = corpus.assignee_map
    if amap is not None and not amap.empty:
        merged = (amap.groupby("assignee")
                  .agg(variants=("assignee_raw", "nunique"), patents=("patents", "sum"),
                       examples=("assignee_raw", lambda s: "; ".join(list(s)[:3])))
                  .query("variants > 1").sort_values("patents", ascending=False).head(15).reset_index())
        if not merged.empty:
            merged.columns = ["Assignee (normalised)", "Name variants merged", "Patents", "Examples of variants"]
            blocks.append(rb.table(merged, title="Assignee names merged",
                                   caption="Spellings, legal forms and subsidiaries mapped to one name; the CSV "
                                           "keeps the original names too."))
    lines = [f"- {x}" for x in LIMITATIONS]
    if plan.not_possible:
        lines += [f"- Not done here: {x}" for x in plan.not_possible]
    blocks.append(rb.markdown("**Limitations**\n\n" + "\n".join(lines)))
    return blocks


def validation_sample(enriched: pd.DataFrame, columns: list[str], n: int = 30, seed: int = 42) -> pd.DataFrame:
    """Random patents with the model's answers, for a reviewer to mark right or wrong."""
    done = enriched[enriched.get("Extraction", pd.Series("", index=enriched.index)).isin(["extracted", "from cache"])]
    if done.empty:
        return pd.DataFrame()
    sample = done.sample(min(n, len(done)), random_state=seed)
    keep = [c for c in ["Patent ID", "Title", "Abstract"] + columns if c in sample.columns]
    out = sample[keep].copy()
    out["Correct? (Y/N)"] = ""
    out["Reviewer comment"] = ""
    return out.reset_index(drop=True)
