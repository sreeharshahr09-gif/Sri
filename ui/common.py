"""Shared Streamlit pieces: model settings and rendering of execution outputs."""

from __future__ import annotations

import logging
import os
from dataclasses import replace

import streamlit as st

from analyst_agent import LLMClient, LLMConfig
from analyst_agent.config import THINKING_TOKENS, Thinking, context_budget_chars
from analyst_agent.llm import OPENROUTER_URL, is_openrouter
from analyst_agent.sandbox import Artifact, ArtifactKind, ExecutionResult, has_row_labels

DEFAULT_LLM = LLMConfig()
LOCAL = "Local model server (llama-server)"
CLOUD = "OpenRouter (cloud API)"
CLOUD_MODEL = os.environ.get("OPENROUTER_MODEL") or "nvidia/nemotron-3.5-super-vl:free"
# Cloud models offer up to a million tokens; this app never needs more than this per request.
CLOUD_CONTEXT_CAP = 131_072
THINKING_LABELS = {Thinking.OFF: "Off: fastest (default)", Thinking.ANALYSIS: "Writing and analysis",
                   Thinking.ALL: "All steps: slowest"}
THINKING_NOTES = {
    Thinking.ANALYSIS: "🧠 The model thinks when it plans, names clusters, judges white space, writes the report and "
                       "answers questions, and on the Data analysis and Workspace pages. Reading the patents stays "
                       "fast and uses the same cache as before.",
    Thinking.ALL: "🧠 The model thinks in every step, reading the patents included, so runs take much longer. "
                  "Patents read with thinking are cached apart: an earlier run's patents are read again.",
}


# Widgets whose values should survive page switches. Streamlit discards the state of widgets that
# are not on the current page; re-assigning their values on every run keeps them.
_REMEMBERED: set[str] = set()


class _StaleFragmentFilter(logging.Filter):
    """Drops Streamlit's "The fragment with id ... does not exist anymore" lines. A browser tab left open
    while the app restarted keeps asking every second for the auto-refreshing status bar of the old app;
    Streamlit ignores those requests, and the log line repeats until the tab is reloaded."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "does not exist anymore" not in record.getMessage()


def quiet_stale_fragment_logs() -> None:
    log = logging.getLogger("streamlit.runtime.app_session")
    if not any(isinstance(f, _StaleFragmentFilter) for f in log.filters):
        log.addFilter(_StaleFragmentFilter())


def remember(key: str, default) -> str:
    """Register a widget key to keep across pages and give it a first value. Create the widget
    with `key=` and without an explicit default value (the value comes from session state)."""
    _REMEMBERED.add(key)
    st.session_state.setdefault(key, default)
    return key


def keep_widget_state() -> None:
    """Call at the start of every run, before any page renders."""
    for key in _REMEMBERED:
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]


def detect_server(llm_cfg: LLMConfig) -> tuple[bool, str]:
    """Adopt the loaded model's name and context window from the server. Must run before the
    model-settings widgets are created in this script run (it writes their session-state values)."""
    if is_openrouter(llm_cfg.base_url):
        return detect_openrouter(llm_cfg)
    client = LLMClient(llm_cfg)
    models = client.models()
    if models is None:
        return False, f"Not connected: nothing answers at {llm_cfg.base_url}"
    detail = "connected"
    if models:
        current = st.session_state.get("llm_model")
        st.session_state.llm_model = current if current in models else models[0]
        detail = ", ".join(models)
    n_ctx = client.context_size()
    if n_ctx:
        st.session_state.llm_context = n_ctx
        detail += f" · context window {n_ctx:,} tokens (applied)"
    return True, f"Connected: {detail}"


def detect_openrouter(llm_cfg: LLMConfig) -> tuple[bool, str]:
    """Check the API key and the model id with OpenRouter; adopt the model's context window and note the
    account's daily request limit for free models."""
    if not llm_cfg.api_key:
        return False, "Enter your OpenRouter API key (openrouter.ai → Keys), then press Check connection."
    client = LLMClient(llm_cfg)
    key = client.key_details()
    info = client.model_details() if key is not None else None
    if key is None or info is None:
        return False, "Not connected: openrouter.ai cannot be reached. Check the internet connection."
    if key.get("error"):
        return False, f"OpenRouter refused the API key ({key['error']})."
    if not info:
        return False, (f"OpenRouter has no model “{llm_cfg.model}”. Copy the id from the model's page on "
                       f"openrouter.ai, e.g. {CLOUD_MODEL}.")
    detail = f"Connected to OpenRouter: {llm_cfg.model}"
    n_ctx = info.get("context_length")
    if isinstance(n_ctx, int) and n_ctx > 0:
        used = min(n_ctx, CLOUD_CONTEXT_CAP)
        st.session_state.llm_context = used
        detail += f" · context window {n_ctx:,} tokens" + (f" ({used:,} used)" if used < n_ctx else " (applied)")
    if llm_cfg.model.endswith(":free"):
        daily = 50 if key.get("is_free_tier", True) else 1000
        st.session_state.llm_daily_limit = daily
        detail += f" · free model: up to 20 requests a minute and {daily:,} a day on this account"
    else:
        st.session_state.llm_daily_limit = None
        price = info.get("pricing") or {}
        try:
            detail += (f" · {float(price['prompt']) * 1e6:.2f} USD per million input tokens, "
                       f"{float(price['completion']) * 1e6:.2f} USD per million output tokens")
        except (KeyError, TypeError, ValueError):
            pass
    return True, detail


def _provider_defaults(provider: str) -> dict:
    if provider == CLOUD:
        return {"llm_base_url": OPENROUTER_URL, "llm_model": CLOUD_MODEL, "llm_context": CLOUD_CONTEXT_CAP}
    local = not is_openrouter(DEFAULT_LLM.base_url)
    return {"llm_base_url": DEFAULT_LLM.base_url if local else "http://localhost:8080",
            "llm_model": DEFAULT_LLM.model if local else "", "llm_context": DEFAULT_LLM.context_tokens}


def _state_config() -> LLMConfig:
    """The model settings as they stand in session state (before the widgets of this run exist)."""
    ss = st.session_state
    return replace(DEFAULT_LLM, base_url=str(ss.get("llm_base_url", DEFAULT_LLM.base_url)).strip(),
                   model=str(ss.get("llm_model", DEFAULT_LLM.model)).strip(),
                   api_key=str(ss.get("llm_api_key", "")).strip() if ss.get("llm_provider") == CLOUD else
                   DEFAULT_LLM.api_key,
                   context_tokens=int(ss.get("llm_context", DEFAULT_LLM.context_tokens)))


def _switch_provider() -> None:
    """Selectbox callback: keep the other provider's settings, restore (or default) this one's, and check it."""
    ss = st.session_state
    saved = ss.setdefault("llm_saved", {})
    previous = LOCAL if ss.llm_provider == CLOUD else CLOUD
    saved[previous] = {k: ss.get(k) for k in ("llm_base_url", "llm_model", "llm_context")}
    ss.update(saved.get(ss.llm_provider) or _provider_defaults(ss.llm_provider))
    ss.llm_check = detect_server(_state_config())


def model_settings() -> LLMConfig:
    """Model-server settings, shown on every page so they persist across navigation."""
    ss = st.session_state
    ss.setdefault("llm_provider", CLOUD if is_openrouter(DEFAULT_LLM.base_url) else LOCAL)
    ss.setdefault("llm_base_url", DEFAULT_LLM.base_url)
    ss.setdefault("llm_model", CLOUD_MODEL if ss.llm_provider == CLOUD and not os.environ.get("LLM_MODEL")
                  else DEFAULT_LLM.model)
    ss.setdefault("llm_api_key", os.environ.get("OPENROUTER_API_KEY")
                  or (DEFAULT_LLM.api_key if is_openrouter(DEFAULT_LLM.base_url) else ""))
    # A text box still loading when another setting changes can come back empty (seen when a setting was changed
    # right after this panel was opened). An empty server address or model is never meant: keep the last one.
    for key in ("llm_base_url", "llm_model"):
        if not str(ss.get(key) or "").strip() and ss.get(f"{key}_last"):
            ss[key] = ss[f"{key}_last"]
    if "llm_autodetected" not in ss:
        # Once per browser session: pick up whatever model the server has loaded right now.
        ss.llm_autodetected = True
        detect_server(_state_config())
    cloud = ss.llm_provider == CLOUD
    notice = st.sidebar.empty()  # always there, so the expander below keeps its place (and stays open)
    if cloud:
        notice.caption("☁️ Cloud model (OpenRouter): what the app sends to the model leaves this PC.")
    with st.sidebar.expander("🧠 Model server", expanded=False):
        st.selectbox("Model source", [LOCAL, CLOUD], key="llm_provider", on_change=_switch_provider)
        if cloud:
            st.caption("⚠️ Everything the app sends to the model goes to OpenRouter and the model's provider: patent "
                       "texts, your brief and plan, data you analyse, workspace files you ask about. Free models "
                       "usually log prompts (NVIDIA's: do not send confidential information).")
            st.text_input("OpenRouter API key", type="password", key="llm_api_key",
                          help="From openrouter.ai → Keys. Kept for this browser session only. To avoid retyping "
                               "it, put  set \"OPENROUTER_API_KEY=sk-or-...\"  in my_settings.bat.")
        base_url = st.text_input("Server URL", key="llm_base_url")
        # Filled from the server: the model that is actually loaded is the one that answers.
        model = st.text_input("Model", key="llm_model",
                              help="The model id from its page on openrouter.ai, e.g. " + CLOUD_MODEL if cloud else
                                   "Detected from the model server when the app starts or on 'Check connection'.")
        temperature = st.slider("Temperature", 0.0, 1.0, DEFAULT_LLM.temperature, 0.05, key="llm_temperature",
                                help="Low values give more consistent, reproducible analyses.")
        max_tokens = st.number_input("Max tokens per reply", 256, 16384, DEFAULT_LLM.max_tokens, 256, key="llm_max_tokens")
        thinking = Thinking(st.selectbox(
            "Model thinking", list(Thinking), index=list(Thinking).index(DEFAULT_LLM.thinking), key="llm_thinking",
            format_func=lambda mode: THINKING_LABELS[Thinking(mode)],
            help="Off: the patent steps run without thinking; the Data analysis and Workspace pages leave it to the "
                 "model. Writing and analysis: the model thinks when it plans, names clusters, judges white space, "
                 "writes the report and answers questions, and on the Data analysis and Workspace pages; reading "
                 "the patents (most of a run's requests) stays fast. All steps: reading the patents thinks too. "
                 f"A step that thinks gets {THINKING_TOKENS:,} extra reply tokens for it. Models without thinking "
                 "are not affected.",
        ))
        if thinking in THINKING_NOTES:
            st.caption(THINKING_NOTES[thinking])
        read_timeout = st.number_input("Request timeout (s)", 10, 1800, int(DEFAULT_LLM.read_timeout), 10, key="llm_timeout")
        # The value lives only in session state so "Check connection" can update it.
        st.session_state.setdefault("llm_context", DEFAULT_LLM.context_tokens)
        context_tokens = st.number_input(
            "Context window (tokens)", min_value=2048, max_value=1_048_576, step=4096, key="llm_context",
            help=f"'Check connection' reads it from OpenRouter (at most {CLOUD_CONTEXT_CAP:,} are used)." if cloud
            else "Must match llama-server's -c value. 'Check connection' reads it from the server.",
        )
        seed_text = st.text_input("Sampling seed (optional)", "", key="llm_seed", help="Passed to the server if set.")
        llm_cfg = replace(
            DEFAULT_LLM,
            base_url=base_url.strip(),
            model=model.strip(),
            api_key=str(ss.llm_api_key).strip() if cloud else DEFAULT_LLM.api_key,
            temperature=float(temperature),
            max_tokens=int(max_tokens),
            read_timeout=float(read_timeout),
            seed=int(seed_text) if seed_text.strip().lstrip("-").isdigit() else None,
            context_tokens=int(context_tokens),
            thinking=thinking,
            think=thinking != Thinking.OFF,
        )
        for key, value in (("llm_base_url", base_url), ("llm_model", model)):
            if value.strip():
                ss[f"{key}_last"] = value
        st.button("Check connection", width="stretch", on_click=_check_connection, args=(llm_cfg,))
        result = st.session_state.get("llm_check")
        if result:
            (st.success if result[0] else st.error)(result[1])
        st.caption(f"Conversation budget: ~{context_budget_chars(llm_cfg):,} characters before old tool output is trimmed.")

    st.session_state.llm_cfg = llm_cfg
    return llm_cfg


def _check_connection(llm_cfg: LLMConfig) -> None:
    """Button callback: test the server and adopt its model and context window."""
    st.session_state.llm_check = detect_server(llm_cfg)


def render_artifact(art: Artifact, key: str) -> None:
    if art.title and art.kind != ArtifactKind.PLOTLY:
        st.markdown(f"**{art.title}**")
    try:
        if art.kind == ArtifactKind.PLOTLY:
            st.plotly_chart(art.to_plotly(), width="stretch", key=key)
        elif art.kind == ArtifactKind.TABLE:
            df = art.to_dataframe()
            st.dataframe(df, width="stretch", hide_index=not has_row_labels(df))
            note = f"{art.payload.get('rows', len(df)):,} rows × {art.payload.get('cols', df.shape[1])} columns"
            if art.payload.get("truncated"):
                note += f" (first {len(df):,} rows shown)"
            cols = st.columns([4, 1])
            cols[0].caption(note)
            cols[1].download_button("CSV", df.to_csv(index=False).encode(), file_name=f"{art.title or 'table'}.csv",
                                    mime="text/csv", key=f"dl-{key}", width="stretch")
        elif art.kind == ArtifactKind.IMAGE:
            st.image(art.image_bytes())
        elif art.kind == ArtifactKind.TEXT:
            st.markdown(art.text)
        elif art.is_metric:
            st.metric(art.title or "Result", art.text)
        else:
            st.code(art.text, language=None)
    except Exception as exc:  # a broken artifact must not break the page
        st.warning(f"Could not display output ({art.kind}): {exc}")


def render_execution(r: ExecutionResult, key: str) -> None:
    """stdout, outputs, warnings and errors of one sandbox execution."""
    if r.stdout.strip():
        st.text(r.stdout[:8000] + ("\n…(truncated)" if r.stdout_truncated or len(r.stdout) > 8000 else ""))
    for i, art in enumerate(r.artifacts):
        render_artifact(art, key=f"{key}-{i}")
    for w in r.warnings:
        st.caption(f"⚠️ {w}")
    if not r.ok:
        st.error(f"{r.error_type}: {r.error_message}" if r.error_type else "Execution failed")
        if r.traceback:
            st.code(r.traceback, language=None)
