"""Self-contained, offline HTML report for the drum comparison template."""

from __future__ import annotations

import pandas as pd

from .drum_template import DrumStudy, RESEARCH_LINKS, study_findings
from .patents import report as blocks
from .patents.html_report import report_html

DRUM_REPORT_CSS = """
body { font: 15px/1.65 "Segoe UI", Arial, sans-serif; }
main { max-width: 980px; }
main h1, main h2, main h3, main h4, main h5, main h6 { font-family: inherit; line-height: 1.3; }
main h3 { font-size: 18px; margin-top: 32px; }
main h4, main h5, main h6 { font-size: 16px; margin-top: 22px; }
main p, main li { max-width: 78ch; }
main .block-title { font-size: 17px; line-height: 1.35; margin: 26px 0 10px; }
main .tablewrap { margin: 12px 0 24px; max-height: none; }
main table.data { font: 13px/1.45 "Segoe UI", Arial, sans-serif; width: 100%; }
main table.data th, main table.data td { padding: 9px 11px; min-width: 90px; }
main table.data thead th { position: sticky; top: 0; z-index: 1; }
main table.data tbody tr:nth-child(even) { background: #f8fafc; }
main .markdown-table { max-height: 560px; }
main .chartwrap { width: min(100%, 820px); margin: 22px auto 6px; }
main .chartwrap + .caption { max-width: 820px; margin: 0 auto 25px; }
main hr { border: 0; border-top: 1px solid var(--line); margin: 26px 0; }
@media (max-width: 680px) {
  main { padding: 20px 14px 48px; }
  main .chartwrap { width: 100%; }
}
"""


def _field_validation_blocks(study: DrumStudy) -> list[blocks.Block]:
    """Evidence-tied field hypotheses and a practical validation programme."""
    present = set(study.curves)
    sections = [blocks.heading("Field-performance hypotheses and validation", 2),
                blocks.markdown(
                    "The laboratory curves describe each tyre under the recorded test state. "
                    "They can motivate vehicle hypotheses, but a drum or FLAT TRAC sweep alone "
                    "does not establish a field ranking. Test the following mechanisms at the "
                    "same axle, load, pressure, temperature and vehicle setup before making a "
                    "product decision.")]
    if "Fy" in present:
        sections.extend([
            blocks.heading("Initial steering response", 3),
            blocks.markdown(
                "**Laboratory signal:** Compare the near-zero Fy slope at each common vertical "
                "load, and inspect the full signed Fy curves around zero slip. A larger local "
                "slope suggests more lateral-force build-up for a small slip-angle change in "
                "the measured state; it does not, by itself, predict vehicle understeer or "
                "driver preference.\n\n"
                "**Field test:** On the same vehicle and axle, run repeated small-amplitude "
                "steering sweeps and step-steer manoeuvres at matched speed, inflation, load "
                "and tyre temperature. Log handwheel angle and torque, yaw rate, lateral "
                "acceleration and estimated front/rear slip angles. Compare response gain, "
                "phase and onset across repeats. A reversed or unstable vehicle ranking would "
                "weaken this hypothesis."),
            blocks.heading("Sustained lateral support and load sensitivity", 3),
            blocks.markdown(
                "**Laboratory signal:** Compare measured Fy over the shared slip-angle range, "
                "its observed peak and its change with vertical load. A boundary peak is only "
                "the largest supplied point. If source force units are confirmed as N, Fy/Fz "
                "can be used for a load-normalized screening comparison.\n\n"
                "**Field test:** Use instrumented steady-state circles or controlled lane "
                "changes at several axle loads, with repeated runs and recorded surface and "
                "temperature. Examine lateral acceleration, slip angle and the shape of the "
                "response near the limit, including heat build-up. Do not infer wet-road or "
                "worn-tyre grip from a single dry laboratory sweep.")])
    if "Mz" in present:
        sections.extend([blocks.heading("Self-aligning response and steering feel", 3),
                         blocks.markdown(
                             "**Laboratory signal:** Compare the signed Mz-versus-slip curves, "
                             "especially the slope near zero, peak location and decay as Fy "
                             "rises. Peak magnitude alone can hide a different pneumatic-trail "
                             "trend or sign convention.\n\n"
                             "**Field test:** Measure handwheel torque, returnability and "
                             "on-center torque build-up in repeated steering inputs. Confirm "
                             "steering-system friction, power-assist calibration and axle "
                             "geometry before attributing a vehicle-level difference to Mz.")])
    if "Mx" in present or "Fx" in present:
        sections.extend([blocks.heading("Moment and force coupling", 3),
                         blocks.markdown(
                             "**Laboratory signal:** Inspect Mx as a signed overturning response "
                             "through the slip-angle sweep and Fx as longitudinal force "
                             "measured during that same sweep. Their load dependence may flag "
                             "different contact-patch or carcass responses.\n\n"
                             "**Field test:** If relevant to the vehicle programme, compare "
                             "wheel-force transducer measurements during matched cornering "
                             "and combined-slip events. Fx here is not a braking or traction "
                             "coefficient; run a separate slip-ratio test before making those "
                             "claims.")])
    sections.extend([
        blocks.heading("Validation sequence and decision rule", 3),
        blocks.markdown(
            "1. Confirm force and moment units, sign conventions, reference axes, test surface, "
            "conditioning, inflation, load, speed and temperature for both source reports. "
            "Resolve any cross-test method differences before interpreting small gaps.\n"
            "2. Repeat each laboratory condition on multiple specimens per tyre. Report mean, "
            "spread and confidence intervals for matched-load metrics and whole-curve contrasts.\n"
            "3. Run vehicle tests with tyre positions swapped or a controlled crossover order. "
            "Record surface, weather, wear state and temperature, and retain run-level data.\n"
            "4. Check whether the laboratory ordering predicts a consistent vehicle response "
            "under matched conditions. Report effect size and uncertainty; where the ranking "
            "changes with load or condition, state that operating envelope explicitly."),
        blocks.note("No field correlation coefficient or causal conclusion can be estimated "
                    "from the supplied workbooks without paired vehicle measurements.")])
    return sections


def _source_table(study: DrumStudy) -> pd.DataFrame:
    rows = []
    for path in study.files:
        for output, table_range in study.ranges.get(path, {}).items():
            rows.append({"Tyre": study.labels[path], "File": path,
                         "Worksheet": study.sheets[path][output], "Output": output,
                         "Header row": table_range.header_row,
                         "Columns": table_range.excel_columns,
                         "Last data row": table_range.last_data_row or "Sheet end"})
    return pd.DataFrame(rows)


def drum_report_html(study: DrumStudy, followups: list[tuple[str, str]] | None = None, branding=None) -> bytes:
    if study.status != "answered" or not study.answer.strip():
        raise ValueError("A completed model interpretation is required for the full report export.")
    body = [blocks.heading("Drum / FLAT TRAC tyre comparison", 1),
            blocks.note("Laboratory force and moment comparisons are measured evidence. Field-performance statements are directional hypotheses until linked to field measurements."),
            blocks.heading("Study brief"),
            blocks.markdown(study.requirements or "Compare the selected tyres under matched test loads."),
            *( [blocks.markdown(study.field_context, "User-supplied field observations (unverified)")]
               if study.field_context else [] ),
            blocks.heading("Sources and operating conditions"),
            blocks.table(pd.DataFrame([{"Tyre": study.labels[p], "File": p,
                                        **study.conditions.get(p, {})} for p in study.files])),
            blocks.table(_source_table(study), "Worksheet provenance"),
            blocks.heading("Measured comparison"),
            blocks.markdown("\n".join(f"- {item}" for item in study_findings(study)), "Calculated takeaways")]
    body.extend([blocks.heading("R&D interpretation", 2), blocks.markdown(study.answer)])
    body.extend([blocks.table(study.comparison, "Paired differences at common loads"),
                 blocks.table(study.metrics, "Per-tyre measured metrics"),
                 blocks.heading("Graphs"),
                 blocks.note("Use the load dropdown inside each Fy, Fx, Mx, Mz, normalized Fy and difference graph to switch between comparable vertical loads.")])
    previous_group = ""
    for chart in study.charts:
        if chart.group != previous_group:
            body.append(blocks.heading(chart.group, 3))
            previous_group = chart.group
        figure = chart.figure.__class__(chart.figure)
        figure.update_layout(height=490, width=None, autosize=True,
                             font={"family": "Segoe UI, Arial, sans-serif", "size": 13},
                             title_font={"family": "Segoe UI, Arial, sans-serif", "size": 17})
        body.append(blocks.chart(figure, chart.title, chart.caption))
    body.extend([blocks.heading("Method and interpretation limits"),
                 blocks.markdown("- Cornering stiffness: absolute OLS slope of Fy over measured -1°, 0°, +1°; free intercept. At least three points crossing zero are required.\n"
                                 "- Peak: largest absolute measured value within the supplied sweep. Boundary maxima are not extrapolated.\n"
                                 "- Load-normalized Fy: |Fy| / (load in daN × 10 N); this assumes Fy is recorded in N.\n"
                                 "- Differences use only exact shared slip angles. Empty cells remain missing.\n"
                                 "- Fx is recorded during a slip-angle sweep; it is not a slip-ratio braking or traction test.\n"
                                 "- Field implications require matched on-road or vehicle data and calibration. No field correlation coefficient is calculated from these workbooks."),
                 blocks.table(pd.DataFrame({"Data and method check": study.checks}))])
    body.extend(_field_validation_blocks(study))
    body.extend([blocks.heading("Research context"),
                 blocks.markdown("\n".join(f"- [{title}]({url})" for title, url in RESEARCH_LINKS))])
    if followups:
        body.append(blocks.heading("Follow-up analysis"))
        for i, (question, answer) in enumerate(followups, 1):
            body.append(blocks.markdown(f"**Q{i}. {question}**\n\n{answer}"))
    return report_html(body, "Drum / FLAT TRAC tyre comparison",
                       subtitle=f"{len(study.files)} workbooks · {len(study.common_loads)} common loads · {len(study.charts)} graphs",
                       extra_css=DRUM_REPORT_CSS, markdown_heading_offset=0, branding=branding)
