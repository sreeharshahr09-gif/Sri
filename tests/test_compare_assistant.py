from __future__ import annotations

import json

import pandas as pd

from analyst_agent.compare import ComparisonSource, compare_numeric
from analyst_agent.compare_assistant import answer_comparison_question, comparison_evidence
from analyst_agent.llm import Completion


def _result():
    frames = [pd.DataFrame({"Slip": [-1, 0, 1], "Fy": [10, 0, -10]}),
              pd.DataFrame({"Slip": [-1, 0, 1], "Fy": [8, 0, -8]})]
    result = compare_numeric([ComparisonSource(label, frame, "Slip")
                              for label, frame in zip(("A", "B"), frames)],
                             {"Fy": {"A": "Fy", "B": "Fy"}})
    files = [{"path": f"{label}.xlsx", "sheet": "Fy", "label": label, "key": "Slip",
              "key_type": "numeric", "factor": 1.0, "value_factor": 1.0}
             for label in ("A", "B")]
    return result, files


def test_evidence_uses_computed_result_and_identifies_sources():
    result, files = _result()
    payload = json.loads(comparison_evidence(result, files))
    assert payload["files"][0]["path"] == "A.xlsx"
    assert payload["metrics"]["Fy"]["matched_differences_vs_first_file"][0]["matched_points"] == 3
    assert payload["metrics"]["Fy"]["sampled_key_count"] == 3
    assert payload["metrics"]["Fy"]["series_stats"]["A"]["count"] == 3


def test_llm_help_uses_one_grounded_call():
    class FakeModel:
        def __init__(self):
            self.messages = None

        def chat(self, messages):
            self.messages = messages
            return Completion(content="The two selected Fy curves differ at the matched points.")

    result, files = _result()
    model = FakeModel()
    run = answer_comparison_question(model, "What differs?", comparison_evidence(result, files))
    assert run.status == "answered"
    assert len(model.messages) == 2
    assert "A.xlsx" in model.messages[1]["content"]
    assert "What differs?" in model.messages[1]["content"]
