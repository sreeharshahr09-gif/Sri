import json

import pytest

from analyst_agent.agent import DataAnalystAgent
from analyst_agent.config import AgentConfig
from analyst_agent.llm import LLMError
from analyst_agent.patents import enrich
from analyst_agent.patents import followup as fu
from analyst_agent.patents.data import detect_columns
from analyst_agent.patents.pipeline import PipelineOptions, run_landscape
from analyst_agent.patents.plan import AnalysisPlan

from .conftest import ScriptedLLM
from .patent_fixtures import make_export
from .test_patent_pipeline import FakeLLM
from .test_patent_plan import BRIEF, MODEL_PLAN

PLAN = AnalysisPlan.from_dict({**MODEL_PLAN, "brief": BRIEF, "questions": ["Companies: competitors?"],
                               "answers": {"Companies: competitors?": "Apollo Tyres"}})


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    enrich.CACHE_DIR = tmp_path_factory.mktemp("cache")
    raw = make_export(200, seed=12)
    return run_landscape(raw, detect_columns(list(raw.columns)), PLAN, llm=FakeLLM(PLAN),
                         options=PipelineOptions(embedding_model="tfidf"))


def reply(obj):
    return ScriptedLLM([json.dumps(obj)])


# --------------------------------------------------------------------------- routing


def test_route_by_model():
    llm = reply({"action": "change_plan", "changes": {"years": [2018, 2024], "competitors": ["Michelin", "Continental"],
                                                      "colour": "red"}, "instruction": "Narrow the period."})
    r = fu.route(llm, "Only 2018-2024 and add Continental", PLAN, ["📊 Executive Summary"])
    assert r.action == "change_plan" and r.changes == {"years": [2018, 2024], "competitors": ["Michelin", "Continental"]}
    prompt = llm.calls[0][1]["content"]
    assert '"years": [2014, 2024]' in prompt and '"📊 Executive Summary"' in prompt and "Request: Only 2018-2024" in prompt

    r = fu.route(reply({"action": "rewrite", "sections": ["Executive Summary"], "instruction": "Shorter."}), "x", PLAN, [])
    assert (r.action, r.sections, r.instruction) == ("rewrite", ["Executive Summary"], "Shorter.")

    r = fu.route(reply({"action": "change_plan", "changes": {}}), "Rerun it please", PLAN, [])
    assert r.action == "analyse" and "no concrete plan change" in r.note


def test_route_by_rules_when_the_model_fails():
    class Down:
        def chat(self, messages):
            raise LLMError("Cannot reach model server")

    r = fu.route(Down(), "Please make the executive summary shorter", PLAN, [])
    assert r.action == "rewrite" and r.note.startswith("decided by rules: the model could not")
    r = fu.route(Down(), "Re-run with only years 2018 to 2024", PLAN, [])
    assert r.action == "analyse" and "edit the plan above" in r.note  # no concrete edits without the model
    r = fu.route(None, "Which assignee leads in sensors?", PLAN, [])
    assert r.action == "analyse" and r.instruction == "Which assignee leads in sensors?"
    r = fu.route(reply({"action": "dance"}), "Which assignee leads?", PLAN, [])
    assert r.action == "analyse" and "unclear" in r.note


# --------------------------------------------------------------------------- plan changes


def test_apply_changes_and_describe_them():
    new, diff = fu.apply_changes(PLAN, {
        "years": [2018, 2024], "competitors": ["Michelin", "Bridgestone", "Continental"],
        "problems": [{"name": "Cupping"}, {"name": "River wear", "description": "grooves along the rib edge"}],
    })
    assert new.years == (2018, 2024) and new.competitors[-1] == "Continental"
    assert [p.name for p in new.problems] == ["Cupping", "River wear"]
    assert new.tech_buckets == PLAN.tech_buckets and new.brief == PLAN.brief
    assert new.answers == PLAN.answers and new.questions == PLAN.questions and new.not_possible == PLAN.not_possible
    assert diff == ["Years: 2014–2024 → 2018–2024",
                    ("Wear type: + River wear; − Camber wear, Feathering, Flat-spotting, Clawing, Chip-and-flake, "
                     "Shoulder wear"),
                    "Competitors: + Continental; − Goodyear"]
    new, diff = fu.apply_changes(PLAN, {"years": None})
    assert new.years is None and diff == ["Years: 2014–2024 → no limit"]
    assert fu.apply_changes(PLAN, {})[1] == []


# --------------------------------------------------------------------------- rewriting


def test_rewrite_uses_the_same_facts_with_the_instruction(result):
    model = FakeLLM(PLAN)
    out = fu.rewrite(result, model, ["Executive Summary"], "Make it shorter, three bullets.")
    assert [s.heading for s in out] == ["📊 Executive Summary"]
    system, user = model.prompts[0]
    assert "executive summary" in system
    assert "Revision requested by the user: Make it shorter, three bullets." in user
    assert result.all_facts[:200] in user
    assert out[0].text.startswith("- **Main finding:**") and out[0].flagged == []
    assert result.sections[0].text and result.sections[0] is not out[0]  # the original stays as it was
    assert [s.heading for s in fu.pick_sections(result, ["research gaps"])] == ["🔮 Research Gaps & Future Trends"]
    assert [s.heading for s in fu.pick_sections(result, ["nonsense"])] == ["📊 Executive Summary"]


# --------------------------------------------------------------------------- analysis


def test_agent_context(result):
    ds = fu.analysis_dataset(result)
    # the patents the report analyses, so the agent's counts match the report's
    assert "Map X" not in ds.df and "Wear type" in ds.df and len(ds.df) == len(result.analysis_frame)
    assert len(ds.df) < len(result.enriched) and set(ds.df["Relevance"]) <= {"High", "Partial"}
    profile = fu.landscape_profile(result, ds)
    assert profile.startswith("This table is the enriched patent set of a patent landscape study: Uneven wear")
    assert "'Technology group' ∈ {Structural/Design, Material Science, Process/Manufacturing, Active/Electronic}" in profile
    off = len(result.enriched) - len(ds.df)
    assert f"The table holds the {len(ds.df):,} patents the report analyses; the {off:,} patents judged off-topic" \
        in profile and "publication date year" in profile
    import copy

    lagged = copy.copy(result)
    lagged.incomplete_years = [2024, 2025]
    assert "Years 2024, 2025 are incomplete because patents publish about 18 months after filing: never read a " \
           "lower count there as a decline." in fu.landscape_profile(lagged, ds)
    assert "Key results of the report" in profile and "[Filing trend]" in profile
    assert "Dataset `enriched_patents.csv`" in profile
    assert len(profile) < 20000  # fits a 16k-token context with room for the conversation


def test_agent_context_fits_with_full_text_columns(result):
    """The user's export has descriptions of ~30,000 characters per patent: two sample rows printed in full made
    the agent's first request 26,000 tokens, more than the 16k context window."""
    import copy

    from analyst_agent.config import LLMConfig
    from analyst_agent.llm import LLMClient

    long = copy.copy(result)
    long.enriched = result.enriched.assign(Description="A tyre with a lighter belt. " * 1100,
                                           Claims="1. A pneumatic tyre comprising ... " * 300)
    ds = fu.analysis_dataset(long)
    assert len(ds.df["Description"].iloc[0]) > 30000
    profile = fu.landscape_profile(long, ds)
    assert len(profile) < 20000 and "First 2 rows" in profile
    small = fu.agent_client(LLMClient(LLMConfig(context_tokens=12000)))
    budget = fu.profile_budget(small)
    tight = fu.landscape_profile(long, ds, max_chars=budget)
    assert len(tight) <= budget and "First 2 rows" not in tight  # sample rows dropped first
    assert fu.profile_budget(FakeLLM(PLAN)) is None


def test_agent_answers_from_the_enriched_table(result, sandbox):
    ds = fu.analysis_dataset(result)
    frame = ds.df[ds.df["Relevance"] != "Low"]
    leader = frame[frame["Technology group"] == "Active/Electronic"]["Assignee (normalised)"].value_counts()
    llm = ScriptedLLM([
        ("```python\nd = df[(df['Relevance'] != 'Low') & (df['Technology group'] == 'Active/Electronic')]\n"
         "t = d['Assignee (normalised)'].value_counts()\nshow(t.head(5))\nprint(t.head(3))\n```"),
        f"Final Answer: {leader.index[0]} leads sensor-based solutions with {leader.iloc[0]} patents.",
    ])
    agent = DataAnalystAgent(llm, sandbox, ds, config=AgentConfig(), profile=fu.landscape_profile(result, ds))
    run = agent.run("Who leads in active/electronic solutions?")
    assert run.status == "answered" and run.executed and run.grounding.fully_traced
    assert "This table is the enriched patent set" in llm.calls[0][0]["content"]
    assert [a.kind for a in run.report_artifacts()] == ["table"]
