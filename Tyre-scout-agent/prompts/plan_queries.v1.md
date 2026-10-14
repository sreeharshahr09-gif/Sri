---
version: plan-1.1
schema: QueryPlan
purpose: bounded daily research planning
---
Plan today's public-internet discovery queries for tire technology scouting.

Inputs provided: the topic list with priorities and keywords, the source-type
recency windows, a retrieval cursor (last successful run), and a compact digest
of already-known development titles from the history window (avoid re-finding
old news).

Rules:
- Produce at most {max_queries} queries, prioritising high-priority topics.
- Prefer company/supplier technical announcements, original research papers,
  patent publications, and industry news. Trusted code searches the configured
  preferred domains and records any bounded broader fallback.
- Use short focused queries for a technical event: introduced, launched,
  demonstrated, published experimental results, or a patent publication.
  Cover compound/material, reinforcement, performance and manufacturing topics.
- Avoid market size, CAGR, forecasts, corporate overview pages and tutorials.
- Video discovery is not useful without transcripts. Prefer an accompanying
  technical article or paper. Do not force one query for every source type.
- Prefer specific technical terms over generic ones ("silane coupling silica
  dispersion tire" not "tire technology news").
- Do not plan queries whose purpose is to find internal or private
  information. Public sources only.
