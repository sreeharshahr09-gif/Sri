"""Streamlit entry point for the agent app.

Run with:  streamlit run app.py
Expects an OpenAI-compatible server (e.g. llama.cpp `llama-server`) at LLM_BASE_URL.

Pages:
- Data analysis: upload a dataset and ask questions; the agent writes and runs code to answer.
- Workspace: point it at a local folder; it searches, reads, explains and (with approval) edits files.
- Patent landscape: upload a patent export and an analysis brief; get a research-grade landscape.
"""

import streamlit as st

from ui import data_page, jobs, patent_page, workspace_page
from ui.common import keep_widget_state, model_settings

st.set_page_config(page_title="Research Agent", page_icon="📊", layout="wide")

navigation = st.navigation(
    [
        st.Page(data_page.page, title="Data analysis", icon="📊", default=True),  # served at "/"
        st.Page(workspace_page.page, title="Workspace", icon="📁", url_path="workspace"),
        st.Page(patent_page.page, title="Patent landscape", icon="🔬", url_path="patents"),
    ]
)
keep_widget_state()
model_settings()
# Questions run in the background; a status bar on every page shows their progress.
jobs.show_notice()
if jobs.any_active():
    jobs.status_bar(navigation.title)
navigation.run()
