"""Shared Streamlit pieces: model settings and rendering of execution outputs."""

from __future__ import annotations

import base64
from dataclasses import replace

import pandas as pd
import streamlit as st

from analyst_agent import LLMClient, LLMConfig
from analyst_agent.sandbox import Artifact, ExecutionResult

DEFAULT_LLM = LLMConfig()


def model_settings() -> LLMConfig:
    """Model-server settings, shown on every page so they persist across navigation."""
    with st.sidebar.expander("🧠 Model server", expanded=False):
        base_url = st.text_input("Server URL", DEFAULT_LLM.base_url, key="llm_base_url")
        model = st.text_input("Model", DEFAULT_LLM.model, key="llm_model")
        temperature = st.slider("Temperature", 0.0, 1.0, DEFAULT_LLM.temperature, 0.05, key="llm_temperature",
                                help="Low values give more consistent, reproducible analyses.")
        max_tokens = st.number_input("Max tokens per reply", 256, 16384, DEFAULT_LLM.max_tokens, 256, key="llm_max_tokens")
        read_timeout = st.number_input("Request timeout (s)", 10, 1800, int(DEFAULT_LLM.read_timeout), 10, key="llm_timeout")
        seed_text = st.text_input("Sampling seed (optional)", "", key="llm_seed", help="Passed to the server if set.")
        llm_cfg = replace(
            DEFAULT_LLM,
            base_url=base_url.strip(),
            model=model.strip(),
            temperature=float(temperature),
            max_tokens=int(max_tokens),
            read_timeout=float(read_timeout),
            seed=int(seed_text) if seed_text.strip().lstrip("-").isdigit() else None,
        )
        if st.button("Check connection", width="stretch"):
            ok, detail = LLMClient(llm_cfg).health()
            (st.success if ok else st.error)(f"{'Connected' if ok else 'Not connected'}: {detail}")

    st.session_state.llm_cfg = llm_cfg
    return llm_cfg


def render_artifact(art: Artifact, key: str) -> None:
    if art.title and art.kind != "plotly":
        st.markdown(f"**{art.title}**")
    try:
        if art.kind == "plotly":
            st.plotly_chart(art.to_plotly(), width="stretch", key=key)
        elif art.kind == "table":
            df = art.to_dataframe()
            # Integer indexes are positional leftovers (e.g. after sorting); labelled ones such
            # as describe()'s count/mean/... carry meaning and stay visible.
            st.dataframe(df, width="stretch", hide_index=pd.api.types.is_integer_dtype(df.index))
            note = f"{art.payload.get('rows', len(df)):,} rows × {art.payload.get('cols', df.shape[1])} columns"
            if art.payload.get("truncated"):
                note += f" (first {len(df):,} rows shown)"
            cols = st.columns([4, 1])
            cols[0].caption(note)
            cols[1].download_button("CSV", df.to_csv(index=False).encode(), file_name=f"{art.title or 'table'}.csv",
                                    mime="text/csv", key=f"dl-{key}", width="stretch")
        elif art.kind == "image":
            st.image(base64.b64decode(art.payload["png_b64"]))
        elif art.kind == "text":
            st.markdown(art.payload.get("text", ""))
        else:
            text = art.payload.get("text", art.summary)
            if len(text) <= 40 and "\n" not in text:
                st.metric(art.title or "Result", text)
            else:
                st.code(text, language=None)
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
