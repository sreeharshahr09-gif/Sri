"""An answer cut short by the step limit says how to get a complete one, and Continue asks the follow-up."""

from streamlit.testing.v1 import AppTest

from ui.data_page import CONTINUE_QUESTION, _original_question


def _app():
    import streamlit as st

    from analyst_agent import DataRun
    from analyst_agent.runs import AgentStep, RunStatus
    from analyst_agent.sandbox import ExecutionResult
    from ui.data_page import render_run

    status = RunStatus.STEP_LIMIT if st.session_state.get("cut_short", True) else RunStatus.ANSWERED
    run = DataRun(question=st.session_state.get("question", "Average sales by region?"), answer="West 150.",
                  status=status, steps=[AgentStep(index=0, reply="", code="print(1)",
                                                  result=ExecutionResult(ok=True, stdout="1"))])
    render_run(run, 0, can_continue=st.session_state.get("latest", True))
    st.text(f"pending={st.session_state.get('pending_question')}")


def _pending(at) -> str:
    return next(t.value for t in at.text if t.value.startswith("pending="))[len("pending="):]


def test_the_warning_says_how_to_finish_and_continue_asks_the_follow_up():
    at = AppTest.from_function(_app, default_timeout=30)
    at.session_state["data_max_steps"] = 10
    at.run()
    warning = at.warning[0].value
    assert "all its steps (10)" in warning and "Max reasoning steps" in warning
    at.button(key="continue-0").click().run()
    assert _pending(at) == CONTINUE_QUESTION.format(question="Average sales by region?")
    assert not at.exception


def test_continue_only_on_the_latest_answer_and_only_when_cut_short():
    at = AppTest.from_function(_app, default_timeout=30)
    at.session_state["latest"] = False
    at.run()
    assert at.warning and not at.button
    at = AppTest.from_function(_app, default_timeout=30)
    at.session_state["cut_short"] = False
    at.run()
    assert not at.warning and not at.button


def test_continuing_a_continuation_keeps_the_original_question():
    once = CONTINUE_QUESTION.format(question="Average sales by region?")
    twice = CONTINUE_QUESTION.format(question=_original_question(once))
    assert _original_question(twice) == "Average sales by region?"
    assert twice == once
