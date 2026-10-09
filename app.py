"""Streamlit entry point for the agent app.

Run with:  streamlit run app.py
Expects an OpenAI-compatible server (e.g. llama.cpp `llama-server`) at LLM_BASE_URL.

Pages:
- Data analysis: upload a dataset and ask questions; the agent writes and runs code to answer.
- Workspace: point it at a local folder; it searches, reads and explains the files (read-only).
"""

import streamlit as st

from ui import data_page, workspace_page
from ui.common import model_settings

st.set_page_config(page_title="Research Agent", page_icon="📊", layout="wide")

navigation = st.navigation(
    [
        st.Page(data_page.page, title="Data analysis", icon="📊", url_path="data", default=True),
        st.Page(workspace_page.page, title="Workspace", icon="📁", url_path="workspace"),
    ]
)
model_settings()
navigation.run()
