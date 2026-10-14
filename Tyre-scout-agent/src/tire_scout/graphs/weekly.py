"""Weekly workflow: determine prior Mon-Sun -> check seven edition dates ->
(optional bounded recovery, done by the runner before invocation) -> dedupe ->
synthesize themes/actions -> validate -> render HTML/PDF -> delivery gate ->
email submission -> reconcile.

The coverage gate is the final authority: a scheduler trigger never overrides
it. Partial sends require an explicit operator flag and are visibly labelled.
"""
from __future__ import annotations

from ..quality import select_report_items

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from ..emailer import journal as send_journal
from ..emailer.base import SubmissionError, build_adapter
from ..emailer.journal import OutboundMessage, mark, prepare_send, validate_message
from ..schemas import WeeklySynthesis
from ..security import scrub_report
from ..timeutil import now_local, prior_week_window
from .states import WeeklyState


@dataclass
class WeeklyDeps:
    cfg: object
    db: object
    llm: object
    tz: object


def build_weekly_graph(deps: WeeklyDeps, checkpointer=None):
    cfg, db, llm = deps.cfg, deps.db, deps.llm

    def determine_week(state: WeeklyState) -> dict:
        if state.get("week_start"):
            ws = date.fromisoformat(state["week_start"])
        else:
            ws, _ = prior_week_window(now_local(deps.tz).date())
        return {"week_start": ws.isoformat(), "week_end": (ws + timedelta(days=6)).isoformat(),
                "errors": [], "limitations": [], "recovered": []}

    def coverage_gate(state: WeeklyState) -> dict:
        cov = db.edition_coverage(date.fromisoformat(state["week_start"]))
        cov_out = {"complete": cov["complete"],
                   "present": [str(d) for d in cov["present"]],
                   "missing": [str(d) for d in cov["missing"]]}
        missing = [str(d) for d in cov["missing"]]
        if cov["complete"]:
            return {"coverage": cov_out, "missing_dates": [], "status": "complete"}
        if state.get("allow_partial"):
            return {"coverage": cov_out, "missing_dates": missing, "status": "partial_sent",
                    "limitations": state.get("limitations", []) +
                    [f"PARTIAL: missing daily editions {missing} - operator override"]}
        # blocked: mark partial; downstream nodes still produce a clearly
        # identified LOCAL partial preview, but the delivery gate will not send
        return {"coverage": cov_out, "missing_dates": missing, "status": "partial_blocked",
                "send_state": f"blocked: missing editions {missing}"}

    def collect(state: WeeklyState) -> dict:
        ws = date.fromisoformat(state["week_start"])
        we = date.fromisoformat(state["week_end"])
        # Apply the same date, evidence, and total-cap rules as each daily report.
        devs = []
        for offset in range((we - ws).days + 1):
            day = date.fromordinal(ws.toordinal() + offset)
            devs.extend(select_report_items(db.developments_for_edition(day), day, cfg)["recent"])
        # week-level cluster merge: repeated coverage of one event counts once
        best_by_cluster: dict[str, dict] = {}
        for d in devs:
            key = d.get("cluster_id") or d["id"]
            cur = best_by_cluster.get(key)
            if cur is None or (d.get("final_score") or 0) > (cur.get("final_score") or 0):
                if cur is not None:
                    d.setdefault("merged_from", []).append(cur["id"])
                best_by_cluster[key] = d
            else:
                cur.setdefault("merged_from", []).append(d["id"])
        merged = sorted(best_by_cluster.values(),
                        key=lambda d: d.get("final_score") or 0, reverse=True)
        return {"developments": merged}

    def synthesize(state: WeeklyState) -> dict:
        devs = state.get("developments", [])
        if not devs:
            return {"themes": [], "team_actions": [], "priority_ids": [], "additional_ids": []}
        try:
            syn: WeeklySynthesis = llm.synthesize_week(developments=devs)
        except Exception as exc:
            return {"themes": [], "team_actions": [],
                    "limitations": state.get("limitations", []) +
                    [f"weekly synthesis failed ({exc}); digest lists developments only"],
                    "priority_ids": [], "additional_ids": []}
        known = {d["id"] for d in devs}
        themes = [t.model_dump() for t in syn.themes
                  if set(t.development_ids) <= known]
        actions = [a.model_dump() for a in syn.team_actions]
        # trusted ranking: score desc; the LLM order may only break ties
        rank_pos = {rid: i for i, rid in enumerate(syn.weekly_rank)}
        ordered = sorted(devs, key=lambda d: (-(d.get("final_score") or 0),
                                              rank_pos.get(d["id"], 10_000)))
        n_p = cfg.weekly.max_priority_items
        n_a = cfg.weekly.max_additional_items
        return {"themes": themes, "team_actions": actions,
                "priority_ids": [d["id"] for d in ordered[:n_p]],
                "additional_ids": [d["id"] for d in ordered[n_p:n_p + n_a]]}

    def validate(state: WeeklyState) -> dict:
        if state.get("status") == "partial_blocked":
            return {}
        if not state.get("priority_ids") and state.get("developments"):
            # synthesis may have failed; still produce a ranked digest from stored scores
            devs = state["developments"]
            n_p = cfg.weekly.max_priority_items
            n_a = cfg.weekly.max_additional_items
            return {"priority_ids": [d["id"] for d in devs[:n_p]],
                    "additional_ids": [d["id"] for d in devs[n_p:n_p + n_a]]}
        return {}

    def render(state: WeeklyState) -> dict:
        from ..report.render import render_weekly
        ws = date.fromisoformat(state["week_start"])
        paths = render_weekly(
            cfg, db, ws,
            priority_ids=state.get("priority_ids", []),
            additional_ids=state.get("additional_ids", []),
            themes=state.get("themes", []), actions=state.get("team_actions", []),
            partial=bool(state.get("missing_dates")),
            missing_dates=state.get("missing_dates", []))
        db.upsert_weekly_edition(ws, state.get("status", "complete"),
                                 [date.fromisoformat(d) for d in state.get("missing_dates", [])],
                                 paths)
        return {"render_paths": paths}

    def delivery_gate(state: WeeklyState) -> dict:
        paths = state.get("render_paths", {})
        html_path = Path(paths.get("html", ""))
        if not html_path.exists():
            return {"send_state": "no-report"}
        html = scrub_report(html_path.read_text(encoding="utf-8"))
        if state.get("missing_dates"):
            html = ("<p style='border:2px solid #c00;padding:8px'><b>PARTIAL ISSUE</b>: "
                    f"missing daily editions {', '.join(state['missing_dates'])}</p>") + html
        attachments = []
        if cfg.email.attach_weekly_pdf and paths.get("pdf"):
            attachments = [Path(paths["pdf"])]
        subject = f"[Tire Scout] Weekly digest - week of {state['week_start']}"
        if state.get("missing_dates"):
            subject = f"[PARTIAL] {subject}"
        msg = OutboundMessage(subject=subject, from_address=cfg.email.from_address,
                              to=list(cfg.recipients.weekly_to), cc=list(cfg.recipients.weekly_cc),
                              html=html, text=f"Weekly digest {state['week_start']} (see HTML/PDF).",
                              attachments=attachments)
        try:
            validate_message(cfg, msg)
        except ValueError as exc:
            return {"send_state": f"blocked:{exc}"}
        if state.get("status") == "partial_blocked":
            # deterministic gate: record the blocked decision; never send
            decision = prepare_send(db, digest_type="weekly", period=state["week_start"],
                                    cfg=cfg, msg=msg, state="blocked")
            mark(db, decision.identity, "blocked",
                 error=f"missing editions {state['missing_dates']}")
            return {"send_state": state.get("send_state") or "blocked: incomplete coverage",
                    "send_identity": decision.identity}
        production = (state.get("mode") == "production" and cfg.email.live_sending
                      and cfg.email.weekly_enabled)
        if not production:
            decision = prepare_send(db, digest_type="weekly", period=state["week_start"],
                                    cfg=cfg, msg=msg, state="preview")
            from ..emailer.preview import PreviewAdapter
            provider_id = PreviewAdapter(cfg).submit(msg)
            mark(db, decision.identity, "preview", provider="preview", provider_id=provider_id)
            return {"send_state": "preview", "send_identity": decision.identity}
        decision = prepare_send(db, digest_type="weekly", period=state["week_start"],
                                cfg=cfg, msg=msg)
        if not decision.allowed:
            return {"send_state": f"skipped:{decision.reason}", "send_identity": decision.identity}
        try:
            provider_id = build_adapter(cfg).submit(msg)
            mark(db, decision.identity, "submitted", provider=cfg.email.adapter,
                 provider_id=provider_id)
            return {"send_state": "submitted", "send_identity": decision.identity}
        except SubmissionError as exc:
            mark(db, decision.identity,
                 "uncertain" if exc.ambiguous else "failed_before_submit", error=str(exc))
            return {"send_state": "uncertain" if exc.ambiguous else f"failed:{exc}",
                    "send_identity": decision.identity}

    def reconcile(state: WeeklyState) -> dict:
        return {}

    g = StateGraph(WeeklyState)
    for name, fn in [("determine_week", determine_week), ("coverage_gate", coverage_gate),
                     ("collect", collect), ("synthesize", synthesize),
                     ("validate", validate), ("render", render),
                     ("delivery_gate", delivery_gate), ("reconcile", reconcile)]:
        g.add_node(name, fn)
    g.add_edge(START, "determine_week")
    g.add_edge("determine_week", "coverage_gate")
    g.add_edge("coverage_gate", "collect")
    g.add_edge("collect", "synthesize")
    g.add_edge("synthesize", "validate")
    g.add_edge("validate", "render")
    g.add_edge("render", "delivery_gate")
    g.add_edge("delivery_gate", "reconcile")
    g.add_edge("reconcile", END)
    return g.compile(checkpointer=checkpointer)
