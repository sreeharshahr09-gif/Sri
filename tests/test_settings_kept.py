"""The model settings survive a page load that sends the panel's blank defaults back (a click while the very first
page load is still running): what the user or the detection chose is put back."""

from streamlit.testing.v1 import AppTest


def _app():
    from ui.common import model_settings

    model_settings()


def test_blank_widget_values_are_replaced_by_the_kept_settings():
    at = AppTest.from_function(_app, default_timeout=60)
    at.run()
    url, model, context = (at.session_state["llm_base_url"], at.session_state["llm_model"],
                           at.session_state["llm_context"])
    assert url and model and context > 2048
    # What the glitch looked like: an empty address and model, the context box at its minimum.
    at.session_state["llm_base_url"] = ""
    at.session_state["llm_model"] = ""
    at.session_state["llm_context"] = 2048
    at.run()
    assert (at.session_state["llm_base_url"], at.session_state["llm_model"],
            at.session_state["llm_context"]) == (url, model, context)
    assert at.session_state["llm_cfg"].base_url == url and not at.exception


def test_the_users_own_changes_are_kept():
    at = AppTest.from_function(_app, default_timeout=60)
    at.run()
    at.text_input(key="llm_model").input("my-other-model").run()
    at.number_input(key="llm_max_tokens").set_value(4096).run()
    at.session_state["llm_model"] = ""  # a blank value sent back later
    at.run()
    assert at.session_state["llm_model"] == "my-other-model" and at.session_state["llm_max_tokens"] == 4096
    assert at.session_state["llm_cfg"].model == "my-other-model" and not at.exception
