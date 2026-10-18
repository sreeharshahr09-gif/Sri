"""Follow-up requests on a finished landscape: more analysis, a changed plan, or rewritten sections.

The model first decides which of the three the user wants. Questions go to the sandboxed data
agent, which works on the enriched patent table with the report's findings as context. Plan
changes are turned into concrete edits that the user confirms before anything is re-run.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field, replace

from ..data import Dataset, describe_for_llm
from ..llm import LLMClient
from . import narrative as nar
from .llmjson import JSONReplyError, LLMError, ask_json
from .plan import AnalysisPlan, Category, clean_plan

ACTIONS = ("analyse", "change_plan", "rewrite")
PLAN_KEYS = ["title", "objective", "segments", "years", "problem_label", "problems", "tech_buckets",
             "complexity_levels", "in_scope", "competitors", "regions", "extra_fields", "report_sections",
             "analysis_questions"]

ROUTER_SYSTEM = """\
You route a follow-up request about a finished patent landscape report. Decide what the user wants:
"analyse": a question, an extra chart or table, a comparison, or a list of patents, answered from the patent table;
"change_plan": run the study again with a changed plan (period, segments, categories, competitors, regions, \
extra columns, scope, report sections);
"rewrite": rewrite the text of one or more report sections (shorter, another focus, another language) without \
new analysis.

Reply with ONLY one JSON object:
{"action": "analyse" | "change_plan" | "rewrite",
 "changes": {only the plan keys to change, each with its complete new value},
 "sections": [the headings of the sections to rewrite, exactly as listed],
 "instruction": "the request restated in one clear sentence"}

Plan keys: title, objective, segments (list), years ([first, last], or null for no limit), problem_label, \
problems / tech_buckets / complexity_levels / extra_fields (lists of {"name", "description"}), in_scope, \
competitors (list), regions (list), report_sections (list), analysis_questions (list of questions answered \
from the data in the report). Keep everything the user did not ask to change.\
"""


@dataclass
class Route:
    action: str
    changes: dict = field(default_factory=dict)
    sections: list[str] = field(default_factory=list)
    instruction: str = ""
    note: str = ""  # how the decision was made, when not by the model


@dataclass
class FollowUp:
    """One follow-up and its outcome, appended to the report page."""

    question: str
    route: Route
    run: object = None  # AgentRun for "analyse"
    sections: list[nar.Section] = field(default_factory=list)  # rewritten sections
    new_plan: AnalysisPlan | None = None  # for "change_plan"
    diff: list[str] = field(default_factory=list)
    status: str = "done"  # done | proposed | applied | discarded | error
    error: str = ""


# --------------------------------------------------------------------------- deciding


_RERUN = re.compile(r"\b(re-?run|run (?:it |the analysis )?again|change the plan|only (?:patents |years )?(?:from|"
                    r"since|between|after)|exclude|add (?:a |the )?(?:category|competitor|column|wear type|group)|"
                    r"remove (?:the )?(?:category|competitor|column)|years? \d{4})\b", re.IGNORECASE)
_REWRITE = re.compile(r"\b(rewrite|rephrase|re-?word|shorten|shorter|longer|more concise|translate|in (?:german|"
                      r"french|spanish|japanese|chinese|hindi))\b", re.IGNORECASE)


def _compact_plan(plan: AnalysisPlan) -> dict:
    data = plan.to_dict()
    return {k: data[k] for k in PLAN_KEYS}


def route(llm, text: str, plan: AnalysisPlan, headings: list[str]) -> Route:
    user = (f"Current plan:\n{json.dumps(_compact_plan(plan), ensure_ascii=False)}\n\n"
            f"Report sections: {json.dumps(headings, ensure_ascii=False)}\n\nRequest: {text.strip()}")
    try:
        data = ask_json(llm, ROUTER_SYSTEM, user, retries=1, max_tokens=2500) if llm is not None else None
    except (LLMError, JSONReplyError) as exc:
        data = None
        note = f"decided by rules: the model could not ({exc})"
    else:
        note = "decided by rules: no model" if llm is None else ""
    if isinstance(data, dict) and data.get("action") in ACTIONS:
        changes = data.get("changes") if isinstance(data.get("changes"), dict) else {}
        sections = [str(s) for s in data.get("sections") or [] if str(s).strip()]
        r = Route(data["action"], {k: v for k, v in changes.items() if k in PLAN_KEYS}, sections,
                  str(data.get("instruction") or text).strip())
        if r.action == "change_plan" and not r.changes:  # nothing concrete to change: answer it instead
            r.action, r.note = "analyse", "no concrete plan change found, so answered as a question"
        return r
    if isinstance(data, dict):
        note = "decided by rules: the model's answer was unclear"
    action = "rewrite" if _REWRITE.search(text) else "change_plan" if _RERUN.search(text) else "analyse"
    if action == "change_plan":  # without the model there are no concrete edits: let the user edit the plan
        return Route("analyse", instruction=text.strip(), note=note + "; edit the plan above to change it")
    return Route(action, instruction=text.strip(), note=note)


# --------------------------------------------------------------------------- plan changes


def _names(items) -> list[str]:
    return [c.name if isinstance(c, Category) else str(c) for c in items]


def describe_changes(old: AnalysisPlan, new: AnalysisPlan) -> list[str]:
    lines = []
    for key in PLAN_KEYS:
        a, b = getattr(old, key), getattr(new, key)
        if a == b:
            continue
        label = {"years": "Years", "problems": old.problem_label or "Problems", "tech_buckets": "Technology groups",
                 "complexity_levels": "Complexity levels", "extra_fields": "Extra columns",
                 "report_sections": "Report sections", "in_scope": "Scope", "problem_label": "Problem dimension",
                 "analysis_questions": "Brief-specific questions"
                 }.get(key, key.replace("_", " ").capitalize())
        if key == "years":
            fmt = lambda y: f"{y[0]}–{y[1]}" if y else "no limit"  # noqa: E731
            lines.append(f"{label}: {fmt(a)} → {fmt(b)}")
        elif isinstance(a, list):
            na, nb = _names(a), _names(b)
            added = [x for x in nb if x not in na]
            removed = [x for x in na if x not in nb]
            parts = ([f"+ {', '.join(added)}"] if added else []) + ([f"− {', '.join(removed)}"] if removed else [])
            lines.append(f"{label}: " + ("; ".join(parts) if parts else "reordered or descriptions changed"))
        else:
            lines.append(f"{label}: “{a}” → “{b}”")
    return lines


def apply_changes(plan: AnalysisPlan, changes: dict) -> tuple[AnalysisPlan, list[str]]:
    merged = _compact_plan(plan)
    merged.update({k: v for k, v in changes.items() if k in PLAN_KEYS})
    new = clean_plan(merged, plan.brief, fallback=plan)
    if "years" in changes and changes["years"] is None:
        new.years = None
    new.questions, new.answers, new.not_possible = list(plan.questions), dict(plan.answers), list(plan.not_possible)
    new.source = plan.source
    return new, describe_changes(plan, new)


# --------------------------------------------------------------------------- rewriting


def pick_sections(result, wanted: list[str]) -> list[nar.Section]:
    sections = result.sections
    if not sections:
        return []
    def norm(text: str) -> str:
        return re.sub(r"[^a-z]+", "", text.lower())

    picked = []
    for name in wanted:
        key = norm(name)
        for sec in sections:
            if key and (key in norm(sec.heading) or norm(sec.heading) in key) and sec not in picked:
                picked.append(sec)
    return picked or [next((s for s in sections if "summary" in s.kinds), sections[0])]


def rewrite(result, llm, wanted: list[str], instruction: str) -> list[nar.Section]:
    out = []
    for sec in pick_sections(result, wanted):
        fresh = copy.copy(sec)
        fresh.text, fresh.flagged, fresh.note = "", [], ""
        nar.write_sections([fresh], result.facts_by_kind, result.all_facts, result.plan.brief, result.plan.answers,
                           llm, instruction=instruction)
        out.append(fresh)
    return out


# --------------------------------------------------------------------------- analysis


def agent_client(llm):
    """The data agent's client: no thinking (it reasons step by step in its own loop) and room for code."""
    if not isinstance(llm, LLMClient):
        return llm
    cfg = llm.config
    return type(llm)(replace(cfg, no_think=True, max_tokens=max(cfg.max_tokens, 3000)), session=llm.session)


def analysis_dataset(result) -> Dataset:
    table = result.enriched.drop(columns=[c for c in ("Map X", "Map Y") if c in result.enriched])
    return Dataset(df=table, name="enriched_patents.csv", sha256=(result.dataset_sha256 or "0" * 64),
                   source_format="patent landscape (enriched)")


def profile_budget(llm) -> int | None:
    """Characters the data agent's description of the table may take: the context window minus room for the
    reply and a few steps of code and output (about 3 characters a token)."""
    cfg = getattr(llm, "config", None)
    if cfg is None:
        return None
    return max(6_000, (cfg.context_tokens - cfg.max_tokens - 5_000) * 3)


def landscape_profile(result, dataset: Dataset, sample_rows: int = 2, max_chars: int | None = None) -> str:
    """What the data agent knows: the study, what the columns mean, the report's key facts, the table. Shortened
    (no sample rows, fewer facts, then cut) when it would not fit `max_chars`."""
    text = _landscape_profile(result, dataset, sample_rows, 3000)
    if max_chars and len(text) > max_chars:
        text = _landscape_profile(result, dataset, 0, 1200)
    if max_chars and len(text) > max_chars:
        text = text[: max_chars - 80].rsplit("\n", 1)[0] + "\n… (description shortened to fit the model's context window)"
    return text


def _landscape_profile(result, dataset: Dataset, sample_rows: int, fact_chars: int) -> str:
    plan = result.plan
    lines = [f"This table is the enriched patent set of a patent landscape study: {plan.title}."]
    if plan.objective:
        lines.append(f"Objective: {plan.objective}")
    if plan.in_scope:
        lines.append(f"Scope: {plan.in_scope}")
    if result.extraction is not None:
        cats = {f.column: f.choices for f in result.extraction.fields if f.choices}
        lines.append("Columns filled by the model for every patent (values separated by '; ' where several apply): "
                     + "; ".join(f"'{col}' ∈ {{{', '.join(ch)}}}" for col, ch in cats.items()) + ".")
        lines.append("'Relevance' = Low means off-topic: the report's analytics leave those patents out, so do the "
                     "same unless asked. 'Year' is the " + result.corpus.year_basis + " year.")
    if "Topic cluster" in dataset.df:
        lines.append("'Topic cluster' comes from the semantic patent map (text embeddings + clustering).")
    facts = (result.all_facts or "")[:fact_chars]
    if facts:
        lines.append(f"Key results of the report (for context; recompute from the table when answering):\n{facts}")
    return "\n".join(lines) + "\n\n" + describe_for_llm(dataset, sample_rows=sample_rows)
