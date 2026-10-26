"""The landscape report laid out as blocks (headings, text, charts, tables, downloads).

report.py defines the block types, html_report.py turns blocks into one HTML file and the Streamlit page
renders them; this module decides what goes where for a LandscapeResult.
"""

from __future__ import annotations

import re
from datetime import datetime

import pandas as pd

from ..agent import DataRun
from ..runs import RunStatus
from ..sandbox import Artifact, ArtifactKind, has_row_labels
from . import narrative as nar
from . import report as rb
from . import semantic as sem
from .data import PatentCorpus
from .html_report import report_html
from .result import LandscapeResult, LandscapeStatus, PipelineOptions, analysed_index, analysed_topics


def assemble_blocks(result: LandscapeResult, options: PipelineOptions | None = None) -> list[rb.Block]:
    options = options or PipelineOptions()
    blocks = title_blocks(result)
    sections = result.sections or nar.plan_sections(result.plan.report_sections)
    for sec in sections:
        parts = nar.section_blocks(sec)
        if "summary" in sec.kinds:
            plan = result.plan
            scope = " ".join(x for x in [plan.objective, plan.in_scope] if x)
            if scope:
                parts.append(rb.markdown(f"**Scope of this study:** {scope}"))
        if "clusters" in sec.kinds and result.semantic is not None:
            parts.extend(semantic_blocks(result.corpus, result.semantic, analysed_topics(result),
                                         analysed_index(result), per_patent=result.extraction is not None))
        for ex in (e for e in result.exhibits if e.section in sec.kinds):
            parts.append(rb.heading(ex.title, level=3))
            parts.extend(ex.blocks)
        if parts:
            blocks.append(rb.heading(sec.heading))
            blocks.extend(parts)
    blocks.extend(brief_specific_blocks(result))
    blocks.extend(nar.methodology_blocks(result))
    if result.warnings:
        blocks.append(rb.markdown("**Notes from this run**\n\n" + "\n".join(f"- {w}" for w in result.warnings)))
    blocks.extend(table_blocks(result, options))
    blocks.append(html_download(result, blocks))
    return blocks


def title_blocks(result: LandscapeResult) -> list[rb.Block]:
    corpus, plan = result.corpus, result.plan
    std = result.analysis_frame if result.analysis_frame is not None else corpus.std
    years = std["year"].dropna()
    span = f"{int(years.min())}–{int(years.max())}" if len(years) else "n/a"
    return [
        rb.heading(f"Patent landscape: {plan.title}", level=1),
        rb.metrics([
            ("Patents analysed", f"{len(std):,}"),
            ("Years", span),
            ("Assignees", f"{std['assignee'].nunique():,}"),
            ("Patent offices", f"{std['country'].nunique():,}"),
        ]),
    ]


def _short(text: str, limit: int = 90) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


OFF_TOPIC = "Off-topic (left out)"


def semantic_blocks(corpus: PatentCorpus, res: sem.SemanticResult, topics: pd.DataFrame | None = None,
                    analysed: pd.Index | None = None, per_patent: bool = False) -> list[rb.Block]:
    """The map shows every patent (off-topic ones in grey); the table and treemap count the analysed set. With
    `per_patent` (every patent's relevance judged), the cluster-level relevance guess is left out."""
    import plotly.express as px

    std = corpus.std
    topics = res.topics.assign(all_patents=res.topics["patents"]) if topics is None else topics
    analysed = std.index if analysed is None else analysed
    off = ~std.index.isin(analysed)
    n, n_off = int((~off).sum()), int(off.sum())
    names = {cid: f"{cid} · {_short(label, 48)}" for cid, label in zip(topics["id"], topics["label"])}
    order = [names[cid] for cid in sorted(topics["id"])] + ([OFF_TOPIC] if n_off else [])
    frame = pd.DataFrame({
        "x": std["map_x"], "y": std["map_y"], "Topic": std["cluster_id"].map(names).where(~off, OFF_TOPIC),
        "Title": std["title"].map(_short), "Assignee": std["assignee"].map(lambda a: _short(a, 40)),
        "Year": std["year"].astype("Int64").astype(str).replace("<NA>", "n/a"), "ID": std["id"],
    })
    palette = px.colors.qualitative.Dark24 + px.colors.qualitative.Light24
    fig = px.scatter(
        frame, x="x", y="y", color="Topic", category_orders={"Topic": order},
        hover_data={"x": False, "y": False, "Title": True, "Assignee": True, "Year": True, "ID": True},
        color_discrete_sequence=palette, color_discrete_map={OFF_TOPIC: "#c4c4c4"}, render_mode="webgl", height=640,
    )
    fig.update_traces(marker={"size": 6, "opacity": 0.75})
    # A numbered tag at each cluster's centre; the legend gives the number's name.
    centres = frame.assign(cid=std["cluster_id"].values).groupby("cid")[["x", "y"]].median()
    for cid, row in centres.iterrows():
        fig.add_annotation(x=row["x"], y=row["y"], yshift=16, text=f"<b>{cid}</b>", showarrow=False,
                           font={"size": 11}, bgcolor="rgba(255,255,255,0.85)", bordercolor="#888", borderwidth=1,
                           borderpad=1)
    fig.update_layout(
        xaxis={"visible": False}, yaxis={"visible": False}, legend_title_text="Topic cluster",
        margin={"l": 10, "r": 10, "t": 40, "b": 10},
        title=f"Patent map: {n + n_off:,} patents, {len(topics)} topic clusters"
              + (f" ({n_off:,} off-topic, in grey)" if n_off else ""),
    )
    blocks = [
        rb.heading("Patent map and topic clusters", level=3),
        rb.chart(fig, title="Patent map", caption=(
            "Each dot is one patent; patents with similar text sit close together. Colours are topic clusters, "
            "numbered as in the table below"
            + ("; grey dots are patents judged off-topic, which the analytics leave out. " if n_off else ". ")
            + 
            f"Text embedding: {res.info['embedding']} Map: {res.info['map']}. Clusters: {res.info['clustering']}."
        )),
    ]
    has_groups = "tech_group" in topics and topics["tech_group"].astype(bool).any()
    table = pd.DataFrame({
        "#": topics["id"],
        "Topic cluster": topics["label"],
        "Patents": topics["patents"],
        "Share": (topics["patents"] / max(n, 1) * 100).round(1).astype(str) + " %",
        "Off-topic (left out)": (topics["all_patents"] - topics["patents"]) if n_off else "",
        "Technology group": topics.get("tech_group", ""),
        "Relevance": "" if per_patent else topics.get("relevance", ""),
        "Key terms": topics["keywords"].map(lambda k: ", ".join(k[:6])),
        "What these patents do": topics.get("description", ""),
    })
    table = table[[c for c in table.columns if table[c].astype(str).str.strip().astype(bool).any()]]
    caption = res.info["naming"] + (f" Patents and share: the {n:,} analysed patents, as in the rest of the "
                                    "report; the clusters were found on all patents in the export." if n_off else "")
    blocks.append(rb.table(table, title="Topic clusters", caption=caption))
    if has_groups:
        tree = topics[topics["patents"] > 0].assign(tech_group=lambda t: t["tech_group"].replace("", "Unassigned"))
        fig2 = px.treemap(tree, path=[px.Constant("All patents"), "tech_group", "label"], values="patents",
                          color="tech_group", color_discrete_sequence=px.colors.qualitative.Set2, height=520)
        fig2.update_traces(root_color="#f4f4f4", textinfo="label+value+percent root")
        fig2.update_layout(margin={"l": 5, "r": 5, "t": 40, "b": 5}, title="Topic clusters by technology group")
        blocks.append(rb.chart(fig2, title="Topic hierarchy", caption=(
            "Box size is the number of patents. Each cluster sits under the most frequent technology group of its "
            "patents." if per_patent else
            "Box size is the number of patents. Groups come from your plan; the model assigned each cluster to one.")))
    return blocks


def brief_specific_blocks(result: LandscapeResult) -> list[rb.Block]:
    parts: list[rb.Block] = []
    for ex in (e for e in result.exhibits if e.section == "custom"):
        parts.append(rb.heading(ex.title, level=3))
        parts.extend(ex.blocks)
    for question, run in result.question_runs:
        parts.append(rb.heading(question, level=3))
        parts.extend(agent_run_blocks(run))
    if not parts:
        return []
    return [rb.heading("🎯 Brief-specific analyses"),
            rb.caption("Your extra columns, and your plan's own questions answered by the data agent with Python "
                       "code run on the enriched patent table. The code behind each answer is shown.")] + parts


def agent_run_blocks(run: DataRun) -> list[rb.Block]:
    """An AgentRun as report blocks: answer, number check, charts and tables, code."""
    blocks: list[rb.Block] = []
    if run.status in (RunStatus.LLM_ERROR, RunStatus.ERROR):
        blocks.append(rb.note(f"Not answered: {run.error or 'the analysis stopped'}"))
    if run.answer:
        blocks.append(rb.markdown(run.answer))
        if not run.executed:
            blocks.append(rb.note("This answer is not backed by any executed code; treat it as unverified."))
        elif run.status == RunStatus.STEP_LIMIT:
            blocks.append(rb.note("The step limit was reached before the agent finished; the answer may be "
                                  "incomplete."))
    g = run.grounding
    if g and g.checked and run.executed:
        everything = ("The number in the answer traces back to the code output." if len(g.checked) == 1 else
                      f"All {len(g.checked)} numbers in the answer trace back to the code output.")
        blocks.append(rb.caption(everything if g.fully_traced else
                                 f"{g.traced_count} of {len(g.checked)} numbers trace back to the code output; "
                                 f"not traced: {', '.join(g.untraced[:8])}."))
    for art in run.report_artifacts():
        block = artifact_block(art)
        if block is not None:
            blocks.append(block)
    code = "\n\n".join(run.successful_code)
    if code:
        blocks.append(rb.code(code))
    return blocks


def artifact_block(art: Artifact) -> rb.Block | None:
    try:
        if art.kind == ArtifactKind.PLOTLY:
            return rb.chart(art.to_plotly())
        if art.kind == ArtifactKind.TABLE:
            df = art.to_dataframe()
            if has_row_labels(df):  # e.g. the values counted by value_counts: keep them as a column
                df = df.reset_index()
            return rb.table(df, title=art.title or "")
        if art.kind == ArtifactKind.IMAGE:
            return rb.image(art.image_bytes(), title=art.title or "")
        if art.kind == ArtifactKind.TEXT:
            return rb.markdown(art.text)
        return rb.metrics([(art.title or "Result", art.text)]) if art.is_metric else rb.markdown(f"`{art.text}`")
    except (AttributeError, KeyError, TypeError, ValueError):  # a broken output must not break the report
        return None


BULKY_TEXT_CHARS = 10_000
KEY_COLUMNS = ["Patent ID", "Title", "Year", "Assignee (normalised)", "Topic", "Segment", "Technology group",
               "Complexity", "Performance target", "Design lever", "Relevance"]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:50] or "patent_landscape"


def table_blocks(result: LandscapeResult, options: PipelineOptions | None = None) -> list[rb.Block]:
    options = options or PipelineOptions()
    table = result.enriched
    blocks = [rb.heading("Patent table and downloads")]
    res = result.extraction
    if res is not None:
        rel = res.table["Relevance"].value_counts()
        read = res.stats["extracted"] + res.stats["from cache"]
        blocks.append(rb.metrics([
            ("Read by the model", f"{read:,} of {res.stats['patents']:,}"),
            ("High relevance", f"{int(rel.get('High', 0)):,}"),
            ("Partial", f"{int(rel.get('Partial', 0)):,}"),
            ("Low (off-topic)", f"{int(rel.get('Low', 0)):,}"),
        ]))
    problem_col = next((f.column for f in res.fields if f.key == "problem"), None) if res is not None else None
    key = [c for c in KEY_COLUMNS[:6] + ([problem_col] if problem_col else []) + KEY_COLUMNS[6:] if c in table]
    caption = (f"Key columns shown; the CSV below has all {len(table.columns)} columns, including Component, "
               "Mechanism, Core claim, Key parameters, Trade-offs, Confidence, the topic clusters and every column "
               "of your export." if res is not None else "Patent details were not extracted in this run.")
    blocks.append(rb.table(table[key], title="Every patent with the study's columns", caption=caption))
    slug = _slug(result.plan.title)
    # Full-text columns such as a description (up to 32,767 characters per cell) would make the file
    # several times larger without helping the analysis; they stay in the user's own export.
    bulky = [c for c in table.columns
             if table[c].dtype == object or pd.api.types.is_string_dtype(table[c])
             if table[c].astype(str).str.len().mean() > BULKY_TEXT_CHARS]
    csv_table = table.drop(columns=bulky)
    note = (f" Left out to keep the file small: {', '.join(bulky)} (over {BULKY_TEXT_CHARS:,} characters per "
            "patent on average); they stay in your export, matched by Patent ID." if bulky else "")
    blocks.append(rb.download(
        "Download the enriched patent table (CSV)", csv_table.to_csv(index=False).encode("utf-8-sig"),
        f"{slug}_patents.csv",
        caption=f"{len(csv_table):,} patents × {len(csv_table.columns)} columns. Opens directly in Excel.{note}",
    ))
    if res is not None:
        checked = [f.column for f in res.fields if f.key in ("topic", "segment", "problem", "tech_group", "complexity",
                                                              "design_lever", "core_claim", "relevance", "confidence")]
        sample = nar.validation_sample(table.assign(Abstract=result.corpus.std["abstract"].values), checked,
                                       n=options.validation_size, seed=options.seed)
        if not sample.empty:
            blocks.append(rb.download(
                "Download a validation sample (CSV)", sample.to_csv(index=False).encode("utf-8-sig"),
                f"{slug}_validation_sample.csv",
                caption=f"{len(sample)} random patents with the model's answers and empty Correct? / Comment columns. "
                        "Have an expert mark them to measure the extraction accuracy, and quote it with the "
                        "findings.",
            ))
    return blocks


def html_download(result: LandscapeResult, blocks: list[rb.Block]) -> rb.Block:
    frame = result.analysis_frame if result.analysis_frame is not None else result.corpus.std
    years = frame["year"].dropna()
    subtitle = (f"{len(frame):,} patents" + (f" · {int(years.min())}–{int(years.max())}" if len(years) else "")
                + f" · {result.dataset_name or 'uploaded export'} · generated "
                + datetime.fromisoformat(result.started_at).strftime("%d %b %Y")
                + (f" with {result.model_name}" if result.model_name else ""))
    if result.status == LandscapeStatus.CANCELLED:
        subtitle += " · stopped before the end: partial results"
    elif result.status == LandscapeStatus.ERROR:
        subtitle += " · part of the analysis failed: see the notes"
    page = report_html(blocks, f"Patent landscape: {result.plan.title}", subtitle)
    return rb.download("Download the full report (HTML)", page, f"{_slug(result.plan.title)}_report.html",
                       mime="text/html", caption="One file with every chart (interactive), table and text; opens in "
                                                 "any browser, also offline. Print it to PDF from the browser if needed.")
