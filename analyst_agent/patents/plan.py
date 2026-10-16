"""From the user's written brief to a structured, editable analysis plan."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .llmjson import JSONReplyError, LLMError, ask_json


@dataclass
class Category:
    name: str
    description: str = ""


DEFAULT_BUCKETS = [
    Category("Structural / design", "tread pattern geometry, belt package, carcass and bead construction"),
    Category("Material science", "compounds, fillers, polymers, reinforcements"),
    Category("Process / manufacturing", "building, curing, mixing and other production methods"),
    Category("Active / electronic", "sensors, TPMS, prediction algorithms, connected systems"),
]
DEFAULT_COMPLEXITY = [
    Category("Simple", "geometric or dimensional changes"),
    Category("Intermediate", "material or construction changes"),
    Category("Complex", "electronic, IoT or active systems"),
]
DEFAULT_SECTIONS = [
    "📊 Executive Summary",
    "📈 Data Visualizations",
    "🧠 Technology Clusters & Solutions",
    "🏭 Process & Manufacturing Insights",
    "🔮 Research Gaps & Future Trends",
]


@dataclass
class AnalysisPlan:
    title: str = "Patent landscape"
    objective: str = ""
    segments: list[str] = field(default_factory=list)
    years: tuple[int, int] | None = None
    problem_label: str = "Problem"
    problems: list[Category] = field(default_factory=list)
    tech_buckets: list[Category] = field(default_factory=lambda: list(DEFAULT_BUCKETS))
    complexity_levels: list[Category] = field(default_factory=lambda: list(DEFAULT_COMPLEXITY))
    in_scope: str = ""
    keywords: list[str] = field(default_factory=list)
    classification_codes: list[str] = field(default_factory=list)
    competitors: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    extra_fields: list[Category] = field(default_factory=list)
    report_sections: list[str] = field(default_factory=lambda: list(DEFAULT_SECTIONS))
    questions: list[str] = field(default_factory=list)
    not_possible: list[str] = field(default_factory=list)
    answers: dict[str, str] = field(default_factory=dict)
    brief: str = ""
    source: str = "fallback"  # "model" when the model produced it

    def to_dict(self) -> dict:
        data = asdict(self)
        data["years"] = list(self.years) if self.years else None
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "AnalysisPlan":
        data = dict(data)
        for key in ("problems", "tech_buckets", "complexity_levels", "extra_fields"):
            data[key] = [Category(**c) if isinstance(c, dict) else Category(str(c)) for c in data.get(key, [])]
        data["years"] = tuple(data["years"]) if data.get("years") else None
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# --------------------------------------------------------------------------- editable text form


def categories_to_text(items: list[Category]) -> str:
    return "\n".join(f"{c.name}: {c.description}" if c.description else c.name for c in items)


def categories_from_text(text: str) -> list[Category]:
    items: list[Category] = []
    for line in (text or "").splitlines():
        line = line.strip().lstrip("-*•").strip()
        if not line:
            continue
        parts = re.split(r"\s*(?::|—|–| - )\s*", line, maxsplit=1)
        name = parts[0].strip()
        desc = parts[1].strip() if len(parts) > 1 else ""
        if name and name.lower() not in {c.name.lower() for c in items}:
            items.append(Category(name, desc))
    return items


def list_from_text(text: str) -> list[str]:
    out: list[str] = []
    for part in re.split(r"[\n,;]", text or ""):
        part = part.strip().lstrip("-*•").strip()
        if part and part.lower() not in {o.lower() for o in out}:
            out.append(part)
    return out


# --------------------------------------------------------------------------- from the brief

PLAN_SYSTEM = """\
You prepare patent landscape studies for an R&D team. Read the user's brief and turn it into an \
analysis plan. Reply with ONLY one JSON object, no other text, with exactly these keys:

"title": short title of the study (for example "Uneven wear in TBR and PCR tires")
"objective": one or two sentences on what the study must find out
"segments": list of product segments in scope as short codes or names (for example ["TBR", "PCR"]); [] if none given
"years": [first_year, last_year] if the brief limits the period, otherwise null
"problem_label": what the problem dimension is called in this study (for example "Wear type", "Noise source", "Failure mode"); "Problem" if unclear
"problems": list of {"name", "description"}: the specific problems or phenomena the brief lists or clearly implies (keep the brief's terms)
"tech_buckets": list of {"name", "description"}: the technology groups to cluster solutions into, exactly as the brief defines them; if the brief gives none, propose 4-6 suitable groups
"complexity_levels": list of {"name", "description"} ordered from simplest to most complex, as the brief defines them; if none, use Simple / Intermediate / Complex
"in_scope": one or two sentences defining which patents are relevant and which are not
"keywords": list of search keywords from the brief
"classification_codes": list of IPC/CPC codes mentioned
"competitors": list of companies named in the brief
"regions": list of regions or patent offices to focus on (for example ["US", "CN", "EP"]); [] if none
"extra_fields": list of {"name", "description"}: up to 6 additional facts worth extracting from every patent for this topic, beyond these standard ones: topic, segment, problem, technology group, complexity, component, performance target, design lever, mechanism, core claim, key parameters, trade-offs. Use short column names.
"report_sections": list of the report headings the brief asks for, in order and exactly as written (keep emojis)
"questions": list of the questions the brief says to ask the user before running the analysis
"not_possible": list of things the brief asks for that cannot be done here, each with a short reason. This tool works offline on the patent export the user uploaded: it cannot search the web or online databases (the uploaded export replaces that search) and it cannot analyse images.

Use only information from the brief, plus sensible defaults where a key is missing. Do not invent companies.\
"""


def parse_brief(llm, brief: str) -> AnalysisPlan:
    """Plan from the brief via the model; falls back to rules if the model fails or is unreachable."""
    fallback = fallback_plan(brief)
    try:
        data = ask_json(llm, PLAN_SYSTEM, f"Brief:\n\n{brief.strip()}", retries=1, max_tokens=3000)
    except (LLMError, JSONReplyError) as exc:
        fallback.not_possible.append(f"The model could not prepare the plan ({exc}); it was set up by rules instead. "
                                     "Please review it.")
        return fallback
    if not isinstance(data, dict):
        return fallback
    return clean_plan(data, brief, fallback)


def _categories(value, limit: int) -> list[Category]:
    items: list[Category] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("label") or "").strip()
            desc = str(item.get("description") or item.get("examples") or "").strip()
        else:
            name, desc = str(item).strip(), ""
        if name and name.lower() not in {c.name.lower() for c in items}:
            items.append(Category(name[:80], desc[:300]))
    return items[:limit]


def _strings(value, limit: int) -> list[str]:
    out: list[str] = []
    for item in value if isinstance(value, list) else ([value] if isinstance(value, str) and value else []):
        text = str(item).strip()
        if text and text.lower() not in {o.lower() for o in out}:
            out.append(text[:200])
    return out[:limit]


def _years(value) -> tuple[int, int] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            lo, hi = sorted(int(v) for v in value)
        except (TypeError, ValueError):
            return None
        if 1950 <= lo <= hi <= 2100:
            return lo, hi
    return None


def clean_plan(data: dict, brief: str, fallback: AnalysisPlan | None = None) -> AnalysisPlan:
    fb = fallback or fallback_plan(brief)
    plan = AnalysisPlan(
        title=str(data.get("title") or fb.title).strip()[:120],
        objective=str(data.get("objective") or fb.objective).strip()[:600],
        segments=_strings(data.get("segments"), 8) or fb.segments,
        years=_years(data.get("years")) or fb.years,
        problem_label=str(data.get("problem_label") or "Problem").strip()[:40],
        problems=_categories(data.get("problems"), 15),
        tech_buckets=_categories(data.get("tech_buckets"), 10) or list(DEFAULT_BUCKETS),
        complexity_levels=_categories(data.get("complexity_levels"), 6) or list(DEFAULT_COMPLEXITY),
        in_scope=str(data.get("in_scope") or "").strip()[:600],
        keywords=_strings(data.get("keywords"), 30),
        classification_codes=_strings(data.get("classification_codes"), 20),
        competitors=_strings(data.get("competitors"), 20),
        regions=_strings(data.get("regions"), 10),
        extra_fields=_categories(data.get("extra_fields"), 6),
        report_sections=_strings(data.get("report_sections"), 12) or list(DEFAULT_SECTIONS),
        questions=_strings(data.get("questions"), 8),
        not_possible=_strings(data.get("not_possible"), 8),
        brief=brief,
        source="model",
    )
    if not plan.in_scope:
        plan.in_scope = fb.in_scope
    return plan


_SEGMENT_TERMS = {
    "TBR": r"\bTBR\b|truck\s*(?:and|/|&)?\s*bus", "PCR": r"\bPCR\b|passenger car", "LTR": r"\bLTR\b|light truck",
    "OTR": r"\bOTR\b|off[- ]the[- ]road", "2W": r"two[- ]wheeler|motorcycle|\b2W\b", "EV": r"\bEVs?\b|electric vehicle",
    "Agri": r"agricultur|farm tire|farm tyre",
}


def fallback_plan(brief: str) -> AnalysisPlan:
    """A usable plan from the brief's text alone (years, segments, headings, questions)."""
    text = brief or ""
    plan = AnalysisPlan(brief=text, source="fallback")
    years = re.search(r"\b((?:19|20)\d{2})\s*(?:–|-|to|until)\s*((?:19|20)\d{2})\b", text)
    if years:
        plan.years = _years([years.group(1), years.group(2)])
    plan.segments = [seg for seg, pat in _SEGMENT_TERMS.items() if re.search(pat, text, re.IGNORECASE)]
    topic = re.search(r"(?:analysis|landscape|landscaping|study|review|research)\s+(?:on|of|for|into)\s+\**([^*\n.]{5,120})",
                      text, re.IGNORECASE)
    if topic:
        plan.title = topic.group(1).strip().rstrip(" .")
    plan.objective = f"Patent landscape of {plan.title}." if topic else ""
    plan.in_scope = f"Patents that address {plan.title}." if topic else "Patents relevant to the brief."
    headings = re.findall(r"^\s*[-*]\s*(.+?)\s*$", text.split("OUTPUT FORMAT", 1)[1], re.MULTILINE) if "OUTPUT FORMAT" in text else []
    if headings:
        plan.report_sections = [h.replace("**", "").strip() for h in headings][:12]
    questions = re.findall(r"\*\*([^*]{3,60}):\*\*\s*([^\n]*\?)", text)
    plan.questions = [f"{q[0]}: {q[1].strip()}" for q in questions][:8]
    plan.keywords = re.findall(r'"([^"]{3,40})"', text)[:30]
    plan.classification_codes = list(dict.fromkeys(re.findall(r"\b[A-H]\d{2}[A-Z](?:\s?\d{1,4}/\d{2,6})?\b", text)))[:20]
    if re.search(r"web search|search (?:for|the web)|online database", text, re.IGNORECASE):
        plan.not_possible.append("Web and database search: this tool works offline, so the uploaded export is used instead.")
    if re.search(r"image|photo|vision|mmproj", text, re.IGNORECASE):
        plan.not_possible.append("Image analysis is not available in this tool.")
    return plan
