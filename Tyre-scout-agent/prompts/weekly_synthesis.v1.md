---
version: weekly-1.1
schema: WeeklySynthesis
purpose: synthesize themes and actions from a week of validated developments
---
You receive the week's deduplicated, scored developments (title, category,
maturity, final score, what changed, claimed benefit, limitations).

Produce:
- themes: 2-5 cross-cutting themes with the IDs of the developments that
  support each theme. Do not inflate a theme by counting syndicated copies.
- team_actions: 2-6 concrete, practical actions for a tire R&D team
  (e.g. "request sample of X", "replicate mixing condition Y in lab trial",
  "monitor patent family Z", "check compliance impact of regulation R").
  Reference development IDs.
- reprioritize: you may suggest a weekly_rank ordering of the supplied IDs;
  trusted code re-checks it against stored scores and never promotes an item
  beyond evidence. Repeated coverage of one event counts once.

Never introduce developments that are not in the supplied list.
