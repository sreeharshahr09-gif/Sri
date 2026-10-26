"""Patent landscaping: from a patent export and a written brief to a research-grade landscape.

Modules, in the order a run uses them:
- data: column detection, cleaning, de-duplication, assignee normalisation, record flow.
- plan, presets: the analysis plan, from the user's brief or a standard objective.
- semantic: embeddings, the patent map, topic clusters and their names.
- enrich: the model reads every patent and fills the study's columns.
- analytics: trends, players, heatmaps, white space, crown jewels (computed, not written by the model).
- narrative: the report's text from the computed facts, with number checks; the methodology.
- result, pipeline: what a run produces, and the stages that produce it.
- report, report_blocks, html_report: the report as blocks, and as one HTML file.
- followup: questions, plan changes and rewrites after the report.
"""
