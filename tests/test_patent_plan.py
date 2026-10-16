import json
from pathlib import Path

from analyst_agent.llm import LLMError
from analyst_agent.patents.llmjson import JSONReplyError, extract_json
from analyst_agent.patents.plan import (
    DEFAULT_BUCKETS,
    AnalysisPlan,
    Category,
    categories_from_text,
    categories_to_text,
    fallback_plan,
    list_from_text,
    parse_brief,
)

from .conftest import ScriptedLLM

BRIEF = (Path(__file__).parent / "data" / "uneven_wear_brief.md").read_text()

MODEL_PLAN = {
    "title": "Uneven wear in TBR and PCR tires",
    "objective": "Map patented solutions to uneven wear in TBR and PCR tires, 2014-2024.",
    "segments": ["TBR", "PCR"],
    "years": [2014, 2024],
    "problem_label": "Wear type",
    "problems": [{"name": "Camber wear", "description": ""}, {"name": "Cupping", "description": ""},
                 {"name": "Feathering"}, "Flat-spotting", {"name": "Clawing"}, {"name": "Chip-and-flake"},
                 {"name": "Shoulder wear"}],
    "tech_buckets": [{"name": "Structural/Design", "description": "Tread pattern geometry, belt packages"},
                     {"name": "Material Science", "description": "Silica compounds, polymer reinforcement"},
                     {"name": "Process/Manufacturing", "description": "Curing precision, mixing consistency"},
                     {"name": "Active/Electronic", "description": "AI wear prediction, TPMS integration"}],
    "complexity_levels": [{"name": "Simple", "description": "Geometric"}, {"name": "Intermediate", "description": "Material"},
                          {"name": "Complex", "description": "IoT/Active systems"}],
    "in_scope": "Patents on TBR or PCR tires that address uneven or irregular wear.",
    "keywords": ["Uneven wear", "Tread wear prediction"],
    "classification_codes": ["B60C", "B60C 23/00", "B29C"],
    "competitors": ["Michelin", "Bridgestone", "Goodyear"],
    "regions": [],
    "extra_fields": [{"name": "Wear cause", "description": "root cause the patent addresses (alignment, load, inflation...)"},
                     {"name": "Axle position", "description": "steer, drive or trailer axle if stated"}],
    "report_sections": ["📊 Executive Summary", "📈 Data Visualizations", "🧠 Technology Clusters & Solutions",
                        "🏭 Process & Manufacturing Insights", "🔮 Research Gaps & Future Trends"],
    "questions": ["Specific focus: region (US, CN, EP) or a specific wear type?", "Companies: specific competitors to watch?"],
    "not_possible": ["Web search: works offline; the uploaded export is used instead.", "Image analysis: not available."],
}


def test_extract_json_tolerates_fences_thinking_and_trailing_commas():
    assert extract_json('<think>x</think>Sure:\n```json\n{"a": 1,}\n```') == {"a": 1}
    assert extract_json("[1, 2,]") == [1, 2]
    try:
        extract_json('{"a": 1')
    except JSONReplyError as exc:
        assert "not closed" in str(exc)


def test_parse_brief_from_model_reply():
    llm = ScriptedLLM(["Here is the plan:\n```json\n" + json.dumps(MODEL_PLAN) + "\n```"])
    plan = parse_brief(llm, BRIEF)
    assert plan.source == "model" and plan.title == "Uneven wear in TBR and PCR tires"
    assert plan.years == (2014, 2024) and plan.segments == ["TBR", "PCR"]
    assert [p.name for p in plan.problems][:4] == ["Camber wear", "Cupping", "Feathering", "Flat-spotting"]
    assert [b.name for b in plan.tech_buckets] == ["Structural/Design", "Material Science", "Process/Manufacturing",
                                                   "Active/Electronic"]
    assert [c.name for c in plan.complexity_levels] == ["Simple", "Intermediate", "Complex"]
    assert plan.extra_fields[0].name == "Wear cause"
    assert plan.report_sections[0] == "📊 Executive Summary" and len(plan.questions) == 2
    assert "Brief:" in llm.calls[0][1]["content"] and "Uneven Wear" in llm.calls[0][1]["content"]


def test_bad_json_is_retried_once():
    llm = ScriptedLLM(["I think the plan is good.", json.dumps(MODEL_PLAN)])
    plan = parse_brief(llm, BRIEF)
    assert plan.source == "model" and "not valid JSON" in llm.calls[1][-1]["content"]


def test_unreachable_model_falls_back_to_rules():
    class Down:
        def chat(self, messages):
            raise LLMError("Cannot reach model server")

    plan = parse_brief(Down(), BRIEF)
    assert plan.source == "fallback"
    assert plan.years == (2014, 2024) and plan.segments == ["TBR", "PCR"]
    assert plan.title.startswith("Uneven Wear in TBR")
    assert plan.report_sections == ["📊 Executive Summary", "📈 Data Visualizations (Generated Images)",
                                    "🧠 Technology Clusters & Solutions", "🏭 Process & Manufacturing Insights",
                                    "🔮 Research Gaps & Future Trends"]
    assert plan.questions[0].startswith("Specific Focus")
    assert "B60C 23/00" in plan.classification_codes
    assert any("offline" in n for n in plan.not_possible) and any("model could not" in n for n in plan.not_possible)
    assert plan.tech_buckets == DEFAULT_BUCKETS


def test_garbage_values_are_cleaned():
    data = dict(MODEL_PLAN, years=["soon", 2020], tech_buckets="not a list", segments=None,
                problems=[{"name": ""}, {"name": "Cupping"}, {"name": "cupping"}])
    plan = parse_brief(ScriptedLLM([json.dumps(data)]), BRIEF)
    assert plan.years == (2014, 2024)  # from the brief text
    assert plan.tech_buckets == DEFAULT_BUCKETS and [p.name for p in plan.problems] == ["Cupping"]
    assert plan.segments == ["TBR", "PCR"]


def test_editable_text_round_trip():
    items = [Category("Cupping", "scalloped wear"), Category("Feathering")]
    assert categories_from_text(categories_to_text(items)) == items
    assert categories_from_text("- Camber wear — inner edge\n* Cupping\n\ncupping") == [
        Category("Camber wear", "inner edge"), Category("Cupping")]
    assert list_from_text("Michelin, Bridgestone\nGoodyear; michelin") == ["Michelin", "Bridgestone", "Goodyear"]


def test_plan_dict_round_trip():
    plan = parse_brief(ScriptedLLM([json.dumps(MODEL_PLAN)]), BRIEF)
    restored = AnalysisPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert restored == plan


def test_fallback_for_a_different_topic():
    plan = fallback_plan("Landscape on **lightweight PCR and EV tires** from 2016 to 2025. Ask: **Companies:** who?")
    assert plan.years == (2016, 2025) and plan.segments == ["PCR", "EV"]
    assert plan.title == "lightweight PCR and EV tires"
