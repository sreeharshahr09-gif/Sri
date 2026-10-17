
import pandas as pd
import pytest

from analyst_agent.config import LLMConfig
from analyst_agent.llm import Completion, LLMClient, LLMError
from analyst_agent.patents import enrich
from analyst_agent.patents.data import build_corpus, detect_columns
from analyst_agent.patents.plan import AnalysisPlan

from .patent_fixtures import extract_reply, make_export
from .test_patent_plan import MODEL_PLAN


@pytest.fixture(autouse=True)
def cache_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(enrich, "CACHE_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def plan():
    return AnalysisPlan.from_dict({**MODEL_PLAN, "extra_fields": [{"name": "Test method", "description": "how the "
                                                                    "benefit was measured"}, {"name": "Topic"}]})


@pytest.fixture(scope="module")
def std():
    raw = make_export(40, seed=4)
    return build_corpus(raw, detect_columns(list(raw.columns))).std


class FakeModel:
    """Answers extraction prompts like a careful model; can drop patents or cut replies short."""

    def __init__(self, drop_first=None, cut=None, fail=False):
        self.calls = []
        self.drop_first = drop_first or set()
        self.cut = cut
        self.fail = fail

    def chat(self, messages):
        self.calls.append(messages)
        if self.fail:
            raise LLMError("Cannot reach model server at http://localhost:8080. Is llama-server running?")
        drop = self.drop_first if len(self.calls) == 1 else set()
        text = extract_reply(messages[0]["content"], messages[-1]["content"], drop=drop)
        if self.cut and len(self.calls) == 1:
            text = text[: self.cut]
        return Completion(content=text, latency_s=0.0)


# --------------------------------------------------------------------------- fields and prompts


def test_fields_follow_the_plan(plan):
    fields = enrich.build_fields(plan)
    by_key = {f.key: f for f in fields}
    assert by_key["problem"].column == "Wear type"
    assert by_key["problem"].choices[-2:] == ["Other", "Not specified"] and "Cupping" in by_key["problem"].choices
    assert by_key["segment"].choices == ["TBR", "PCR", "Not specified"] and by_key["segment"].multi
    assert by_key["tech_group"].choices == ["Structural/Design", "Material Science", "Process/Manufacturing",
                                            "Active/Electronic"]
    assert [f.column for f in fields][-3:] == ["Relevance", "Confidence", "Test method"]  # "Topic" not doubled
    assert by_key["x_test_method"].instruction == "how the benefit was measured"


def test_fields_without_categories_are_free_text():
    fields = {f.key: f for f in enrich.build_fields(AnalysisPlan(segments=[], problems=[]))}
    assert fields["segment"].choices is None and fields["problem"].choices is None
    assert fields["problem"].column == "Problem"


def test_system_prompt_lists_choices_with_descriptions(plan):
    text = enrich.system_prompt(plan, enrich.build_fields(plan))
    assert "Study: Uneven wear in TBR and PCR tires." in text
    assert "Scope: Patents on TBR or PCR tires" in text
    assert '"tech_group": the technology group of the solution. Exactly one of: Structural/Design (Tread ' in text
    assert '"segment": the product segment(s) the patent is for. One or more of: TBR; PCR; Not specified' in text
    assert '"x_test_method": how the benefit was measured' in text
    assert "ONLY a JSON list" in text and '"ref"' in text


def test_patent_prompt_uses_summaries_and_first_claim(std):
    row = std.iloc[0].copy()
    row["abstract"] = "word " * 400
    text = enrich.patent_prompt(row)
    first = text.splitlines()[0]
    assert first.startswith(row["id"]) and str(int(row["year"])) in first
    assert "Title: " in text and "Problem: " in text and "Method: " in text and "First claim: 1." in text
    abstract = next(line for line in text.splitlines() if line.startswith("Abstract: "))
    assert len(abstract) <= len("Abstract: ") + 1001 and abstract.endswith("…")


# --------------------------------------------------------------------------- replies


def test_json_objects_variants():
    assert enrich.json_objects('[{"ref": "P1"}, {"ref": "P2"}]') == [{"ref": "P1"}, {"ref": "P2"}]
    assert enrich.json_objects('```json\n{"patents": [{"ref": "P1"}]}\n```') == [{"ref": "P1"}]
    cut = '[{"ref": "P1", "mechanism": "uses {braces} and \\"quotes\\""}, {"ref": "P2", "topic": "x"}, {"ref": "P3", "to'
    assert enrich.json_objects(cut) == [{"ref": "P1", "mechanism": 'uses {braces} and "quotes"'},
                                        {"ref": "P2", "topic": "x"}]
    assert enrich.json_objects("no json here") == []


def test_clean_values(plan):
    fields = {f.key: f for f in enrich.build_fields(plan)}
    clean = enrich.clean_value
    assert clean(fields["tech_group"], "structural / DESIGN") == ("Structural/Design", False)
    assert clean(fields["tech_group"], "Chemistry") == ("", True)
    assert clean(fields["complexity"], "intermediate (material change)") == ("Intermediate", False)
    assert clean(fields["problem"], "Camber wear; cupping") == ("Camber wear; Cupping", False)
    assert clean(fields["problem"], "Chip-and-flake") == ("Chip-and-flake", False)
    assert clean(fields["problem"], "camber wear and cupping") == ("Camber wear; Cupping", False)
    assert clean(fields["problem"], "river wear") == ("Other", True)
    assert clean(fields["problem"], ["Cupping", "Feathering"]) == ("Cupping; Feathering", False)
    assert clean(fields["segment"], "not stated") == ("Not specified", False)
    assert clean(fields["segment"], "TBR, Not specified") == ("TBR", False)
    assert clean(fields["relevance"], "high") == ("High", False)
    assert clean(fields["core_claim"], None) == ("not stated", False)
    assert clean(fields["topic"], "x" * 500)[0] == "x" * 160


# --------------------------------------------------------------------------- the run


def test_extracts_every_patent_in_batches(plan, std, cache_dir):
    model = FakeModel()
    seen = []
    res = enrich.extract_details(std, plan, model, on_progress=lambda d, t: seen.append((d, t)))
    n = len(std)
    assert res.stats["extracted"] == n and res.stats["failed"] == 0
    assert len(model.calls) == res.stats["model calls"] == -(-n // 6)
    assert seen[0] == (0, n) and seen[-1] == (n, n)
    assert res.table.index.equals(std.index)
    assert list(res.table.columns) == [f.column for f in res.fields] + ["Extraction"]
    assert set(res.table["Extraction"]) == {"extracted"}
    assert set(res.table["Technology group"]) <= {c["name"] for c in MODEL_PLAN["tech_buckets"]}
    assert (res.table.loc[std["title"].str.contains("silica", case=False), "Technology group"] == "Material Science").all()
    assert (res.table.loc[std["title"].str.contains("Conductive"), "Relevance"] == "Low").all()
    assert len(list((cache_dir / "extract").glob("*.jsonl"))) == 1
    assert "6 patents per request" in res.info


def test_second_run_resumes_from_cache_and_plan_changes_invalidate(plan, std):
    first = enrich.extract_details(std, plan, FakeModel())
    model = FakeModel()
    again = enrich.extract_details(std, plan, model)
    assert model.calls == [] and again.stats["from cache"] == len(std)
    assert set(again.table["Extraction"]) == {"from cache"}
    pd.testing.assert_frame_equal(first.table.drop(columns="Extraction"), again.table.drop(columns="Extraction"))

    changed = AnalysisPlan.from_dict({**plan.to_dict(), "in_scope": "Only truck tires."})
    model = FakeModel()
    enrich.extract_details(std, changed, model)
    assert model.calls  # a different plan asks different questions: no stale answers


def test_partial_cache_after_a_stop_is_resumed(plan, std):
    first = FakeModel()

    def stop():
        return len(first.calls) >= 2

    res = enrich.extract_details(std, plan, first, should_stop=stop)
    assert res.stopped and res.stats["extracted"] == 12
    assert (res.table["Extraction"] == "not run (stopped)").sum() == len(std) - 12
    model = FakeModel()
    res2 = enrich.extract_details(std, plan, model)
    assert res2.stats["from cache"] == 12 and res2.stats["extracted"] == len(std) - 12


def test_patents_missing_from_a_reply_are_retried_alone(plan, std):
    model = FakeModel(drop_first={2, 5})
    res = enrich.extract_details(std.head(6), plan, model, use_cache=False)
    assert res.stats["extracted"] == 6
    assert len(model.calls) == 3  # the batch, then each missing patent on its own
    assert model.calls[1][-1]["content"].startswith("P1 | ")
    assert model.calls[1][-1]["content"].count("\n\nP") == 0


def test_cut_off_reply_keeps_complete_answers(plan, std):
    full = extract_reply(enrich.system_prompt(plan, enrich.build_fields(plan)),
                         "\n\n".join(f"P{i + 1} | {enrich.patent_prompt(r)}" for i, (_, r) in enumerate(std.head(6).iterrows())))
    cut_at = full.index('{"ref": "P4"') + 30
    model = FakeModel(cut=cut_at)
    res = enrich.extract_details(std.head(6), plan, model, use_cache=False)
    assert res.stats["extracted"] == 6 and len(model.calls) == 1 + 3


def test_model_down_fails_fast_with_reason(plan, std):
    model = FakeModel(fail=True)
    res = enrich.extract_details(std, plan, model, use_cache=False)
    assert res.stats["failed"] == len(std) and res.stats["extracted"] == 0
    assert len(model.calls) == 3
    assert res.table["Extraction"].str.startswith("failed: Cannot reach model server").all()


def test_retry_when_reply_is_not_json(plan, std):
    class Chatty(FakeModel):
        def chat(self, messages):
            self.calls.append(messages)
            if len(self.calls) == 1:
                return Completion(content="Sure! Here are the details you asked for.", latency_s=0.0)
            return Completion(content=extract_reply(messages[0]["content"], messages[1]["content"]), latency_s=0.0)

    model = Chatty()
    res = enrich.extract_details(std.head(3), plan, model, use_cache=False)
    assert res.stats["extracted"] == 3 and len(model.calls) == 2
    assert "not a valid JSON list" in model.calls[1][-1]["content"]


def test_batch_size_follows_context():
    assert enrich.batch_size_for(16384) == 6
    assert enrich.batch_size_for(65536) == 6
    assert enrich.batch_size_for(8192) == 3
    assert enrich.batch_size_for(4096) == 1


def test_real_client_settings_for_extraction(plan, std):
    seen = []

    class Recording(LLMClient):
        def chat(self, messages):
            seen.append(self.config)
            return Completion(content=extract_reply(messages[0]["content"], messages[-1]["content"]), latency_s=0.0)

    client = Recording(LLMConfig(model="qwen-test", temperature=0.7, max_tokens=2048, read_timeout=180,
                                 context_tokens=16384))
    res = enrich.extract_details(std.head(10), plan, client, seed=7, use_cache=False)
    assert res.stats["batch size"] == 6 and res.stats["extracted"] == 10
    cfg = seen[0]
    assert cfg.temperature == 0.2 and cfg.seed == 7 and cfg.read_timeout == 600
    assert cfg.max_tokens == 600 * 6 + 1500 and seen[1].max_tokens == 600 * 4 + 1500
    assert res.info.startswith("qwen-test, 6 patents per request")


def test_cut_off_replies_are_retried_per_patent_with_the_reason(plan, std):
    from analyst_agent.llm import ReplyCutOff

    class Thinker(FakeModel):
        """Runs out of room on batches of several patents; answers single patents."""

        def chat(self, messages):
            self.calls.append(messages)
            if messages[-1]["content"].count("\n\nP") >= 1:
                raise ReplyCutOff("The model used all 5,100 reply tokens without writing an answer")
            return Completion(content=extract_reply(messages[0]["content"], messages[-1]["content"]), latency_s=0.0)

    model = Thinker()
    res = enrich.extract_details(std.head(8), plan, model, use_cache=False)
    assert res.stats["extracted"] == 8 and res.stats["failed"] == 0  # not treated as "the model is gone"

    class Silent(FakeModel):
        def chat(self, messages):
            self.calls.append(messages)
            return Completion(content="", latency_s=0.0)

    res = enrich.extract_details(std.head(2), plan, Silent(), use_cache=False)
    assert res.table["Extraction"].eq("failed: the model's reply was empty").all()


def test_long_first_claim_keeps_its_characterising_end(std):
    row = std.iloc[0].copy()
    row["first_claim"] = ("1. A pneumatic tire comprising a carcass, a belt layer and a tread portion " + "filler " * 300
                          + "wherein the groove depth ratio is 0.6 to 0.8.")
    line = next(x for x in enrich.patent_prompt(row).splitlines() if x.startswith("First claim: "))
    assert line.startswith("First claim: 1. A pneumatic tire comprising a carcass")
    assert line.endswith("wherein the groove depth ratio is 0.6 to 0.8.") and " … " in line
    assert len(line) <= len("First claim: ") + 1110
