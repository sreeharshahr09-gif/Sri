"""Guided, read-only comparison of two or more workspace tables."""

from __future__ import annotations

import hashlib
import re
from copy import copy
from pathlib import PurePosixPath

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from openpyxl.utils import column_index_from_string

from analyst_agent import AgentRun, LLMClient, LLMConfig
from analyst_agent.auto_compare import AutoCompareRun, run_auto_compare
from analyst_agent.compare import (
    EXCEL_EXTENSIONS, TABLE_EXTENSIONS, CompareError, ComparisonSource, TableRange, compare_numeric,
    discover_table_ranges, read_table, sheet_names,
)
from analyst_agent.compare_assistant import answer_comparison_question, comparison_evidence
from analyst_agent.config import context_budget_chars
from analyst_agent.drum_template import (DrumStudy, analyze_drum_study, answer_drum_followup,
                                         interpret_drum_study, study_findings)
from analyst_agent.drum_report import drum_report_html
from analyst_agent.workspace import Workspace
from ui import jobs


def _collect_compare_help(job: jobs.Job) -> None:
    saved = st.session_state.get("ws_compare_result")
    if saved is None or saved[0] != getattr(job, "comparison_signature", None):
        return
    run = job.run or AgentRun(question=job.question, status="error",
                              error=job.error or "Unknown model error", mode="compare")
    st.session_state.setdefault("ws_compare_answers", []).append(run)


jobs.JOB_SINKS["ws_compare_job"] = _collect_compare_help


def _collect_auto_compare(job: jobs.Job) -> None:
    if job.run is None:
        run = AutoCompareRun(question=job.question, status="error",
                             error=job.error or "Unknown comparison error")
    else:
        run = job.run
    if st.session_state.get("ws_auto_compare_signature") == getattr(job, "comparison_signature", None):
        st.session_state.ws_auto_compare_result = (job.comparison_signature, run)


jobs.JOB_SINKS["ws_auto_compare_job"] = _collect_auto_compare


def _collect_drum(job: jobs.Job) -> None:
    run = job.run if isinstance(job.run, DrumStudy) else DrumStudy(
        files=getattr(job, "selected_files", []), requirements=job.question,
        status="error", error=job.error or "Unknown analysis error")
    if st.session_state.get("ws_drum_signature") == getattr(job, "comparison_signature", None):
        st.session_state.ws_drum_result = (job.comparison_signature, run)


def _collect_drum_followup(job: jobs.Job) -> None:
    if st.session_state.get("ws_drum_signature") != getattr(job, "comparison_signature", None):
        return
    answer = job.run if isinstance(job.run, str) else f"Analysis could not be completed: {job.error or 'unknown model error'}"
    st.session_state.setdefault("ws_drum_followups", []).append((job.question, answer))


def _collect_drum_interpret(job: jobs.Job) -> None:
    signature = getattr(job, "comparison_signature", None)
    if st.session_state.get("ws_drum_signature") != signature:
        return
    saved = st.session_state.get("ws_drum_result")
    if saved is None or saved[0] != signature:
        return
    if isinstance(job.run, DrumStudy):
        st.session_state.ws_drum_result = (signature, job.run)
    else:
        study = copy(saved[1])
        study.model_error = job.error or "Unknown model error"
        study.status = "calculated"
        st.session_state.ws_drum_result = (signature, study)


jobs.JOB_SINKS["ws_drum_job"] = _collect_drum
jobs.JOB_SINKS["ws_drum_interpret_job"] = _collect_drum_interpret
jobs.JOB_SINKS["ws_drum_followup_job"] = _collect_drum_followup


def _widget_id(root: str, relative: str, sheet: str = "") -> str:
    return hashlib.sha1(f"{root}|{relative}|{sheet}".encode()).hexdigest()[:12]


def _columns(text: str, header_row: int) -> TableRange:
    match = re.fullmatch(r"\s*([A-Za-z]+)\s*:\s*([A-Za-z]+)\s*", text)
    if not match:
        raise CompareError("Columns must look like O:S or A:C.")
    first = column_index_from_string(match.group(1).upper())
    last = column_index_from_string(match.group(2).upper())
    return TableRange(header_row, first, last)


def _default_sheet(options: list[str]) -> int:
    if "Fy" in options:
        return options.index("Fy")
    for i, sheet in enumerate(options):
        if not sheet.startswith("_") and not sheet.lower().endswith("_data"):
            return i
    return 0


def _default_key(columns: list[str]) -> int:
    for i, name in enumerate(columns):
        if any(token in name.lower() for token in ("slip", "load", "time", "date")):
            return i
    return 0


def _default_key_type(series: pd.Series, name: str) -> int:
    values = series.dropna().head(30)
    if values.empty:
        return 0
    if values.map(lambda v: isinstance(v, str) and bool(re.match(r"^0\d+$", v.strip()))).any():
        return 2  # Preserve identifier leading zeros.
    if "date" in name.lower() or pd.api.types.is_datetime64_any_dtype(series):
        return 1
    return 0 if pd.to_numeric(values, errors="coerce").notna().mean() >= 0.8 else 2


def _numeric_candidate(series: pd.Series) -> bool:
    values = series.dropna().head(50)
    return len(values) >= 1 and pd.to_numeric(values, errors="coerce").notna().mean() >= 0.8


def render_compare_files(workspace: Workspace, llm_cfg: LLMConfig | None = None) -> None:
    """AI-led comparison is the primary view; manual mapping stays optional."""
    if llm_cfg is None:
        render_manual_compare_files(workspace)
        return
    template = st.selectbox("Analysis template", ["Drum / FLAT TRAC R&D", "General comparison"],
                            key="ws_compare_template",
                            help="More research templates can be added here without changing the general comparison workflow.")
    if template == "Drum / FLAT TRAC R&D":
        render_drum_compare_files(workspace, llm_cfg)
        return
    st.markdown("Choose the files and describe the comparison. The assistant inspects worksheets, "
                "selects comparable tables and measurements, and produces graphs with checked calculations.")
    files = [workspace.relative(path) for path in workspace.iter_files()
             if path.suffix.lower() in TABLE_EXTENSIONS]
    if len(files) < 2:
        st.info("Open a folder containing at least two Excel, CSV or TSV files.")
        return
    selected = st.multiselect("Files to compare", files, default=files if len(files) == 2 else [],
                              key="ws_auto_files", max_selections=6)
    goal = st.text_area("What should be compared?", height=90,
                        value="Compare performance across relevant worksheets. Explain the differences and show graphs.",
                        key="ws_auto_goal",
                        help="You can mention a sheet or measurement, or leave the broad goal for the assistant to explore.")
    st.caption("The model receives worksheet names, small table previews and calculated results. "
               "If your configured model service is remote, that evidence is sent to it.")
    current_signature = repr((str(workspace.root), selected, goal.strip()))
    busy = jobs.is_running("ws_auto_compare_job")
    if st.button("Analyze and graph with AI", type="primary", disabled=busy or len(selected) < 2 or not goal.strip(),
                 key="ws_auto_run"):
        signature = current_signature
        st.session_state.ws_auto_compare_signature = signature
        st.session_state.ws_auto_compare_result = None
        model = LLMClient(llm_cfg)

        def work(on_event, should_stop):
            return run_auto_compare(model, workspace, list(selected), goal.strip(), on_event, should_stop)

        def describe(kind, payload):
            return ((str(payload) + "…", None) if kind == "phase" else (None, None))

        job = jobs.start_job(goal.strip(), "Workspace comparison", work, describe, mode="compare")
        job.comparison_signature = signature
        st.session_state.ws_auto_compare_job = job
        st.rerun()
    if busy:
        jobs.job_panel("ws_auto_compare_job")
    elif jobs.active("ws_auto_compare_job") is not None:
        jobs.collect("ws_auto_compare_job")
    saved = st.session_state.get("ws_auto_compare_result")
    if saved and saved[0] == current_signature:
        run: AutoCompareRun = saved[1]
        if run.answer:
            st.subheader("Comparison summary")
            st.markdown(run.answer)
        if run.error:
            st.error(run.error)
        if run.charts:
            st.caption("Graphs and differences below are calculated from the selected workbook cells. "
                       "The written summary is the model's interpretation.")
        for chart_index, chart in enumerate(run.charts):
            st.subheader(chart.title)
            sources = "; ".join(f"{entry['path']} / {entry['sheet'] or 'table'} "
                                f"({entry['columns']}, header row {entry['header_row']}"
                                + (f", through row {entry['last_data_row']}" if entry.get('last_data_row') else "")
                                + ")"
                                for entry in chart.files)
            for metric, wide in chart.result.curves.items():
                fig = go.Figure()
                for label in wide.columns:
                    fig.add_trace(go.Scatter(x=wide.index, y=wide[label],
                                             mode="markers" if chart.files[0]["key_type"] == "text"
                                             else "lines+markers", name=label,
                                             connectgaps=False))
                fig.update_layout(title=metric, xaxis_title=chart.x_label, yaxis_title=chart.y_label,
                                  height=420, legend=dict(orientation="h", y=-0.2))
                st.plotly_chart(fig, width="stretch", key=f"ws_auto_chart_{chart_index}_{metric}")
            with st.expander("Data checks and source ranges"):
                st.caption(sources)
                st.dataframe(chart.result.diagnostics, width="stretch", hide_index=True)
            with st.expander("Matched-point differences"):
                st.dataframe(chart.result.differences, width="stretch", hide_index=True)
                st.download_button("Download differences (.csv)",
                                   chart.result.differences.to_csv(index=False).encode("utf-8-sig"),
                                   file_name=f"comparison_{chart_index + 1}.csv", mime="text/csv",
                                   key=f"ws_auto_download_{chart_index}")
        if run.notes:
            with st.expander("Validation and corrections"):
                for note in run.notes:
                    st.caption(note)
    if st.checkbox("Advanced: map tables and columns manually", value=False, key="ws_manual_mode"):
        render_manual_compare_files(workspace, llm_cfg)


def render_drum_compare_files(workspace: Workspace, llm_cfg: LLMConfig) -> None:
    st.markdown("Compare tyre force and moment reports at matched loads. Choose the files and add your R&D question; "
                "the template finds the Fy, Fx, Mx, Mz and stiffness worksheets, checks the data, and builds the graphs.")
    files = [workspace.relative(path) for path in workspace.iter_files()
             if path.suffix.lower() in EXCEL_EXTENSIONS]
    if len(files) < 2:
        st.info("Open a folder containing at least two Excel workbooks.")
        return
    selected = st.multiselect("Test workbooks", files, default=files if len(files) == 2 else [],
                              max_selections=6, key="ws_drum_files")
    requirements = st.text_area("Your requirements", key="ws_drum_requirements", height=110,
                                value="Compare cornering stiffness, Fy, Fx, Mx and Mz at common loads; explain useful trade-offs, show the graphs, and assess possible field implications.",
                                help="Add the decision you need to make, target loads, axle position, or specific questions. Worksheets and columns are selected automatically.")
    with st.expander("Optional field observations"):
        field_context = st.text_area("Vehicle or field behavior already observed", key="ws_drum_field_context",
                                     height=85, placeholder="For example: front axle steering build-up felt slower at high load on dry asphalt.",
                                     help="These notes help the model discuss plausible links; they are not treated as calibrated field measurements.")
    st.caption("Computed results and graphs stay available if the model cannot answer. The complete HTML report "
               "requires a written model interpretation. This template allows model thinking, targets 12,288 reply "
               "tokens where context permits and waits at least 30 minutes per request. If your model service is remote, calculated evidence "
               "and your requirements are sent to it.")
    signature = repr((str(workspace.root), selected, requirements.strip(), field_context.strip()))
    busy = jobs.is_running("ws_drum_job") or jobs.is_running("ws_drum_interpret_job")
    if st.button("Analyze drum / FLAT TRAC tests", type="primary", key="ws_drum_run",
                 disabled=busy or len(selected) < 2):
        st.session_state.ws_drum_signature = signature
        st.session_state.ws_drum_result = None
        st.session_state.ws_drum_followups = []
        st.session_state.ws_drum_report_cache = None
        model = LLMClient(llm_cfg)

        def work(on_event, should_stop):
            return analyze_drum_study(model, workspace, list(selected), requirements.strip(),
                                      on_event, should_stop, field_context=field_context.strip())

        def describe(kind, payload):
            return ((str(payload) + "…", None) if kind == "phase" else (None, None))

        job = jobs.start_job(requirements.strip() or "Drum / FLAT TRAC comparison",
                             "Workspace comparison", work, describe, mode="compare")
        job.comparison_signature = signature
        job.selected_files = list(selected)
        st.session_state.ws_drum_job = job
        st.rerun()
    if busy:
        jobs.job_panel("ws_drum_job")
    elif jobs.active("ws_drum_job") is not None:
        jobs.collect("ws_drum_job")
    saved = st.session_state.get("ws_drum_result")
    if not saved or saved[0] != signature:
        return
    study: DrumStudy = saved[1]
    if study.error:
        st.error(study.error)
        return
    if study.status == "cancelled":
        st.info("Analysis stopped.")
        return
    if study.answer:
        st.subheader("R&D interpretation")
        st.markdown(study.answer)
    if study.model_error:
        st.warning(f"Model interpretation unavailable: {study.model_error}. Calculated results are shown below.")
        if "timed out" in study.model_error.lower():
            st.caption("This template already allows at least 30 minutes and targets 12,288 reply tokens. "
                       "Check that the model server is running and responsive, then retry the interpretation.")
        retry_busy = jobs.is_running("ws_drum_interpret_job")
        if st.button("Retry written interpretation only", key="ws_drum_interpret_retry",
                     disabled=retry_busy):
            model = LLMClient(llm_cfg)

            def work_interpret(on_event, should_stop):
                on_event("phase", "Interpreting saved calculated results")
                return interpret_drum_study(model, copy(study), on_event=on_event)

            def describe_interpret(kind, payload):
                return ((str(payload) + "…", None) if kind == "phase" else (None, None))

            job = jobs.start_job("Retry drum-test interpretation", "Workspace comparison",
                                 work_interpret, describe_interpret, mode="compare")
            job.comparison_signature = signature
            job.selected_files = list(study.files)
            st.session_state.ws_drum_interpret_job = job
            st.rerun()
        if retry_busy:
            jobs.job_panel("ws_drum_interpret_job")
        elif jobs.active("ws_drum_interpret_job") is not None:
            jobs.collect("ws_drum_interpret_job")
            st.rerun()
    st.caption("Field implications are hypotheses until linked to measured vehicle or field data. "
               "Fx here comes from a slip-angle sweep, so it does not establish braking or traction performance.")
    st.subheader("Calculated takeaways")
    for finding in study_findings(study):
        st.markdown(f"- {finding}")
    with st.expander("How these tests may relate to field behavior"):
        st.markdown("- Near-zero Fy stiffness can inform initial steering build-up at a comparable axle load.\n"
                    "- Measured Fy at larger slip angles can inform lateral-force potential on the test surface.\n"
                    "- Mz can inform self-aligning steering tendency; Mx is the overturning-moment response.\n"
                    "- Actual vehicle behavior also depends on surface, temperature, pressure, speed, wear, "
                    "combined slip and chassis setup. Confirm with matched field or vehicle measurements.")
        if study.field_context:
            st.caption(f"Your field observations (unverified): {study.field_context}")
    if not study.comparison.empty:
        st.subheader("Matched-load differences")
        st.dataframe(study.comparison, width="stretch", hide_index=True)
    group_names = list(dict.fromkeys(chart.group for chart in study.charts))
    group = st.selectbox("Graph family", group_names, key="ws_drum_graph_family")
    if group != "Overview":
        st.caption("Use the dropdown inside the graph to select a matched vertical load.")
    for index, chart in enumerate(study.charts):
        if chart.group == group:
            st.plotly_chart(chart.figure, width="stretch", key=f"ws_drum_chart_{index}")
            st.caption(chart.caption)
    with st.expander("Measured metrics, source ranges, and checks"):
        st.dataframe(study.metrics, width="stretch", hide_index=True)
        for path in study.files:
            st.markdown(f"**{study.labels[path]}**")
            st.caption(f"File: {path}; conditions: {study.conditions.get(path) or 'not found in report'}")
            for output, table_range in study.ranges.get(path, {}).items():
                st.caption(f"{output}: {study.sheets[path][output]}, header row {table_range.header_row}, "
                           f"columns {table_range.excel_columns}, through row {table_range.last_data_row or 'sheet end'}")
        for check in study.checks:
            st.markdown(f"- {check}")
        st.download_button("Download measured metrics (.csv)",
                           study.metrics.to_csv(index=False).encode("utf-8-sig"),
                           file_name="drum_test_metrics.csv", mime="text/csv", key="ws_drum_metrics_csv")
    st.subheader("Follow-up analysis")
    if jobs.active("ws_drum_followup_job") is not None and not jobs.is_running("ws_drum_followup_job"):
        jobs.collect("ws_drum_followup_job")
    for question, answer in st.session_state.get("ws_drum_followups", []):
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            st.markdown(answer)
    followup_busy = jobs.is_running("ws_drum_followup_job")
    with st.form("ws_drum_followup_form", clear_on_submit=True):
        followup = st.text_area("Ask about a load, curve, trade-off, or field hypothesis", height=75)
        send = st.form_submit_button("Ask follow-up", disabled=followup_busy)
    if send and followup.strip():
        model = LLMClient(llm_cfg)
        history = list(st.session_state.get("ws_drum_followups", []))

        def work_followup(on_event, should_stop):
            on_event("phase", "Answering from calculated drum-test evidence")
            return answer_drum_followup(model, study, followup.strip(), history)

        def describe_followup(kind, payload):
            return ((str(payload) + "…", None) if kind == "phase" else (None, None))

        job = jobs.start_job(followup.strip(), "Workspace comparison", work_followup,
                             describe_followup, mode="compare")
        job.comparison_signature = signature
        st.session_state.ws_drum_followup_job = job
        st.rerun()
    if followup_busy:
        jobs.job_panel("ws_drum_followup_job")
    if study.answer.strip():
        followups = st.session_state.get("ws_drum_followups", [])
        report_key = (signature, len(followups), study.answer)
        cached = st.session_state.get("ws_drum_report_cache")
        if not cached or cached[0] != report_key:
            cached = (report_key, drum_report_html(study, followups))
            st.session_state.ws_drum_report_cache = cached
        st.download_button("Download complete HTML report", cached[1],
            file_name="drum_flat_trac_comparison.html", mime="text/html", key="ws_drum_html")
    else:
        st.info("Complete HTML report export will appear after the model produces its interpretation.")


def render_manual_compare_files(workspace: Workspace, llm_cfg: LLMConfig | None = None) -> None:
    st.markdown("Select **two or more files**, then choose the worksheet and table range in each. "
                "The comparison reads only the chosen columns; empty cells remain missing rather than becoming zero.")
    files = [workspace.relative(path) for path in workspace.iter_files()
             if path.suffix.lower() in TABLE_EXTENSIONS]
    if len(files) < 2:
        st.info("Open a folder with at least two CSV, TSV or Excel files to compare.")
        return
    selected = st.multiselect("Files", files, default=files if len(files) == 2 else [],
                              key="ws_compare_files")
    if len(selected) < 2:
        st.info("Choose at least two files.")
        return

    configs: list[dict] = []
    for relative in selected:
        file_id = _widget_id(str(workspace.root), relative)
        with st.container(border=True):
            st.markdown(f"**{PurePosixPath(relative).name}**")
            try:
                sheets = sheet_names(workspace, relative)
                sheet = (st.selectbox("Worksheet", sheets, index=_default_sheet(sheets),
                                      key=f"cmp_sheet_{file_id}") if sheets else None)
                candidates = discover_table_ranges(workspace, relative, sheet)
                candidate_options = [f"Row {r.header_row}, columns {r.excel_columns}" for r in candidates]
                choice = st.selectbox("Detected table (review the preview)",
                                      candidate_options + ["Custom range"],
                                      key=f"cmp_table_{file_id}_{sheet}")
                guess = candidates[candidate_options.index(choice)] if choice != "Custom range" else TableRange(1, 1, 2)
                controls = st.columns([1, 2, 2])
                header_row = int(controls[0].number_input("Header row", min_value=1, max_value=100000,
                                      value=guess.header_row, step=1,
                                      key=f"cmp_header_{file_id}_{_widget_id('', relative, sheet or '')}_{choice}"))
                column_text = controls[1].text_input("Table columns", value=guess.excel_columns,
                                      key=f"cmp_columns_{file_id}_{_widget_id('', relative, sheet or '')}_{choice}")
                label = controls[2].text_input("Graph label", value=PurePosixPath(relative).stem,
                                      key=f"cmp_label_{file_id}").strip()
                table_range = _columns(column_text, header_row)
                final_row = int(st.number_input("Last data row (0 = through sheet end)",
                                                min_value=0, max_value=1_000_000,
                                                value=guess.last_data_row or 0,
                                                key=f"cmp_end_{file_id}_{sheet}_{choice}",
                                                help="Set this when another table appears below the selected table in the same columns."))
                table_range = TableRange(table_range.header_row, table_range.first_col,
                                         table_range.last_col, final_row or None)
                preview = read_table(workspace, relative, sheet, table_range, preview_rows=50)
                columns = list(preview.columns)
                key_col = st.selectbox("Key column", columns,
                                       index=_default_key(columns), key=f"cmp_key_{file_id}_{sheet}")
                key_types = ["Numeric", "Date", "Text"]
                key_type = st.selectbox("Key type", key_types,
                                        index=_default_key_type(preview[key_col], key_col),
                                        key=f"cmp_key_type_{file_id}_{sheet}",
                                        help="Text preserves identifiers such as 001. Date aligns calendar dates. Numeric supports unit conversion.")
                factors = st.columns(2)
                factor = factors[0].number_input("Multiply key by", value=1.0, disabled=key_type != "Numeric",
                                                 format="%.6g", key=f"cmp_factor_{file_id}",
                                                 help="For example, use 0.1 to turn N into daN. All keys must use the same unit.")
                value_factor = factors[1].number_input("Multiply values by", value=1.0,
                                                        format="%.6g", key=f"cmp_value_factor_{file_id}",
                                                        help="Use this when the measured values need conversion to one shared unit.")
                st.caption(f"Preview: {len(preview)} rows, {len(columns)} columns; "
                           f"{int(preview.isna().sum().sum())} blank cells in this preview.")
                st.dataframe(preview.head(6), width="stretch", hide_index=True)
                configs.append({"path": relative, "sheet": sheet, "range": table_range,
                                "label": label, "key": key_col, "factor": float(factor),
                                "value_factor": float(value_factor), "key_type": key_type.lower(),
                                "preview": preview})
            except CompareError as exc:
                st.error(str(exc))
    if len(configs) != len(selected):
        return
    labels = [cfg["label"] for cfg in configs]
    if not all(labels) or len(set(labels)) != len(labels):
        st.warning("Give each file a distinct graph label.")
        return

    st.subheader("What to compare")
    columns_by_label = {cfg["label"]: list(cfg["preview"].columns) for cfg in configs}
    shared = set.intersection(*(set(cols) for cols in columns_by_label.values()))
    shared -= {cfg["key"] for cfg in configs}
    shared_metrics = [col for col in columns_by_label[labels[0]] if col in shared and
                      all(_numeric_candidate(cfg["preview"][col]) for cfg in configs)]
    mode = st.radio("Value columns", ["Matching column names", "Choose one column per file"],
                    horizontal=True, key="cmp_mode")
    metrics: dict[str, dict[str, str]] = {}
    if mode == "Matching column names":
        if not shared_metrics:
            st.info("No value column names match. Select ‘Choose one column per file’ to map them manually.")
        else:
            chosen = st.multiselect("Shared value columns", shared_metrics,
                                    default=shared_metrics[:min(4, len(shared_metrics))], key="cmp_metrics")
            metrics = {metric: {label: metric for label in labels} for metric in chosen}
    else:
        metric_label = st.text_input("Name for the compared value", value="Selected value", key="cmp_metric_label").strip()
        mapped: dict[str, str] = {}
        for cfg in configs:
            choices = [col for col in columns_by_label[cfg["label"]] if col != cfg["key"]]
            default_value = next((i for i, col in enumerate(choices)
                                  if _numeric_candidate(cfg["preview"][col])), 0)
            mapped[cfg["label"]] = st.selectbox(f"Value from {cfg['label']}", choices,
                                                 index=default_value,
                                                 key=f"cmp_value_{_widget_id(str(workspace.root), cfg['path'], cfg['sheet'] or '')}")
        if metric_label:
            metrics[metric_label] = mapped

    all_sheets = {cfg["sheet"] for cfg in configs}
    known_axes = {"Fy": ("Slip angle (deg)", "Fy (N)"), "Fx": ("Slip angle (deg)", "Fx (N)"),
                  "Mx": ("Slip angle (deg)", "Mx (Nm)"), "Mz": ("Slip angle (deg)", "Mz (Nm)"),
                  "Stiffness Report": ("Load", "Cornering stiffness")}
    x_default, y_default = known_axes.get(next(iter(all_sheets)), ("Key", "Value")) if len(all_sheets) == 1 else ("Key", "Value")
    axes = st.columns(2)
    x_axis = axes[0].text_input("Horizontal axis", value=x_default, key=f"cmp_xaxis_{x_default}")
    y_axis = axes[1].text_input("Vertical axis", value=y_default, key=f"cmp_yaxis_{y_default}")
    duplicate_policy = st.selectbox("Repeated key values", ["Block comparison", "Mean per key", "Median per key"],
                                    help="The default preserves repeated tests by requiring a more specific key. "
                                         "Mean or median aggregation is applied only when you choose it.")
    policy = {"Block comparison": "block", "Mean per key": "mean", "Median per key": "median"}[duplicate_policy]
    signature = repr((str(workspace.root), [(c["path"], c["sheet"], c["range"], c["label"],
                                            c["key"], c["key_type"], c["factor"], c["value_factor"]) for c in configs], metrics, policy))
    if st.button("Compare selected files", type="primary", disabled=not metrics, key="cmp_run"):
        try:
            with st.spinner("Reading the selected tables and aligning values…"):
                sources = [ComparisonSource(cfg["label"],
                           read_table(workspace, cfg["path"], cfg["sheet"], cfg["range"]),
                           cfg["key"], cfg["factor"], cfg["value_factor"], cfg["key_type"]) for cfg in configs]
                result = compare_numeric(sources, metrics, duplicate_policy=policy)
            st.session_state.ws_compare_result = (signature, result)
            st.session_state.ws_compare_answers = []
        except CompareError as exc:
            st.session_state.ws_compare_result = None
            st.error(str(exc))

    saved = st.session_state.get("ws_compare_result")
    if not saved or saved[0] != signature:
        return
    result = saved[1]
    st.subheader("Comparison")
    st.caption(f"Differences use **{labels[0]}** as the baseline. Blank values are not filled or interpolated. "
               "A percentage difference is unavailable when the baseline value is zero.")
    st.dataframe(result.diagnostics, width="stretch", hide_index=True)
    if (result.diagnostics["Invalid/blank keys"] > 0).any():
        st.warning("Some rows had blank or invalid keys and were excluded. Check whether the selected columns contain another table below the preview.")
    for metric, wide in result.curves.items():
        fig = go.Figure()
        for label in labels:
            fig.add_trace(go.Scatter(x=wide.index, y=wide[label],
                                     mode="markers" if configs[0]["key_type"] == "text" else "lines+markers",
                                     name=label, connectgaps=False))
        title = f"Load {metric} daN" if all_sheets <= {"Fy", "Fx", "Mx", "Mz"} and metric.isdigit() else metric
        fig.update_layout(title=title, xaxis_title=x_axis, yaxis_title=y_axis,
                          height=420, legend=dict(orientation="h", y=-0.2),
                          margin=dict(l=40, r=20, t=45, b=65))
        st.plotly_chart(fig, width="stretch", key=f"cmp_chart_{_widget_id(str(workspace.root), metric)}")
    if result.differences.empty:
        st.warning("No shared, nonblank numeric points were found. Check the keys, units and selected columns.")
    else:
        st.markdown("**Matched-point differences**")
        st.dataframe(result.differences, width="stretch", hide_index=True)
        st.download_button("Download differences (.csv)", result.differences.to_csv(index=False).encode("utf-8-sig"),
                           file_name="workspace_comparison.csv", mime="text/csv")

    if llm_cfg is None:
        return
    st.subheader("Ask AI about this comparison")
    st.caption("The model receives the selected file names, sheet and column choices, calculated statistics, "
               "and up to 50 plotted points per metric. It does not receive the full workbooks. "
               "Its answer is an interpretation; the tables and graphs above are the calculated results.")
    for run in st.session_state.get("ws_compare_answers", []):
        with st.chat_message("user"):
            st.markdown(run.question)
        with st.chat_message("assistant"):
            if run.status == "answered":
                st.markdown(run.answer)
            else:
                st.error(run.error or "The model could not answer.")
    busy = jobs.is_running("ws_compare_job")
    if busy:
        jobs.job_panel("ws_compare_job")
    elif jobs.active("ws_compare_job") is not None:
        jobs.collect("ws_compare_job")
    question = st.chat_input("Ask about the selected comparison", disabled=busy,
                             key="ws_compare_question")
    if question and question.strip() and not busy:
        evidence = comparison_evidence(result, configs)
        if len(evidence) > min(24_000, context_budget_chars(llm_cfg) // 2):
            st.error("This selection is too large for the configured model context. Compare fewer metrics at once.")
            return
        history = list(st.session_state.get("ws_compare_answers", []))
        model = LLMClient(llm_cfg)

        def work(on_event, should_stop):
            on_event("phase", None)
            return answer_comparison_question(model, question.strip(), evidence, history)

        job = jobs.start_job(question.strip(), "Workspace comparison", work,
                             lambda kind, payload: ("Explaining the calculated comparison…", None),
                             mode="compare")
        job.comparison_signature = signature
        st.session_state.ws_compare_job = job
        st.rerun()
