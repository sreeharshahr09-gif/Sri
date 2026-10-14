"""Replay a bounded saved-source sample; never searches, fetches, or sends mail.

Example: python scripts/replay_quality.py --snapshot data/audits/quality-fix-2026-10-13
Output goes into a new timestamped child directory; original evidence is read-only.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from tire_scout.config import apply_telemetry_policy, load_config
from tire_scout.db import Database
from tire_scout.llm import build_llm
from tire_scout.quality import analyze_with_quality, report_section, select_report_items, evidence_options, quote_match
from tire_scout.report.render import render_daily
from tire_scout.schemas import DevelopmentAnalysis


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--edition", default="2026-10-13", type=date.fromisoformat)
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--repair-baseline", type=Path,
                        help="Reuse saved initial responses from an earlier replay and compare only the correction.")
    parser.add_argument("--focused-excerpts", action="store_true",
                        help="Diagnostic only: use retrieved source slices as partial input when testing a slower model.")
    parser.add_argument("--sample", action="append", help="URL substring; repeat to limit the default sample.")
    args = parser.parse_args()
    if args.focused_excerpts and not args.repair_baseline:
        parser.error("--focused-excerpts requires --repair-baseline")
    cfg = load_config(args.config).model_copy(deep=True)
    apply_telemetry_policy(cfg)
    output = args.snapshot / ("replay-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    output.mkdir(parents=True, exist_ok=False)
    cfg.general.data_dir = output
    cfg.email.live_sending = cfg.email.daily_enabled = cfg.email.weekly_enabled = False
    cfg.email.adapter = "preview"
    cfg.email.daily_pdf = False
    checkpoint = args.snapshot / "original-checkpoints.sqlite"
    with sqlite3.connect(checkpoint.resolve().as_uri() + "?mode=ro", uri=True,
                         check_same_thread=False) as conn:
        saver = SqliteSaver(conn)
        saver.is_setup = True  # Existing snapshot schema; never run setup/write PRAGMAs.
        saved = saver.get_tuple({"configurable": {"thread_id": f"daily-{args.edition}"}})
        if saved is None:
            raise ValueError("edition checkpoint not found")
        state = saved.checkpoint["channel_values"]

    shutil.copy2(args.snapshot / "original-tire_scout.sqlite3", output / "tire_scout.sqlite3")
    db = Database(output / "tire_scout.sqlite3")
    original = db.developments_for_edition(args.edition)
    groups = select_report_items(original, args.edition, cfg)
    summary = {"edition_date": str(args.edition), "model": cfg.llm.model,
               "scope": "bounded diagnostic sample; not a complete new edition",
               "original_items_reclassified": {k: len(v) for k, v in groups.items()},
               "reclassified_items": [{"title": d["title"], "section": k,
                                       "reason": d["review_reason"]} for k, v in groups.items() for d in v],
               "sample": []}
    baseline = json.loads(args.repair_baseline.read_text(encoding="utf-8")) if args.repair_baseline else None
    if baseline:
        summary["repair_baseline"] = str(args.repair_baseline)
        summary["scope"] += "; initial responses reused for a controlled correction comparison"
    if args.focused_excerpts:
        summary["scope"] = "partial-input diagnostic on exact saved-source slices; not equivalent to a full-source replay"
    # Render the five ORIGINAL analyses in their new sections, not a replacement production edition.
    row = db.daily_edition(args.edition)
    coverage = json.loads(row["coverage_summary"])
    coverage.update(fetch_attempts=len(state["fetched"]),
                    fetched_successfully=sum(r.get("status") == "fetched" for r in state["fetched"]))
    coverage.setdefault("limitations", []).append(
        "Quality review preview: reclassifies the original saved analyses. "
        "The bounded model replay is recorded separately; this is not a new research edition.")
    db._conn.execute("UPDATE daily_editions SET coverage_summary=? WHERE edition_date=?",
                     (json.dumps(coverage), str(args.edition)))
    summary["preview"] = render_daily(cfg, db, args.edition)
    db.close()
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"OUTPUT {output.resolve()}", flush=True)
    llm = build_llm(cfg)
    probe = llm.capabilities()
    summary["model_probe"] = probe.model_dump(mode="json")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if not probe.reachable or not probe.supports_structured_output:
        raise RuntimeError("Local model is not ready; see summary.json. No sample analyses attempted.")
    # Two PDF/layout cases, two substantive quote errors, and a transcript-less video.
    samples = args.sample or ("s3-prod.rubbernews.com", "sciepublish.com/article/pii/518",
                             "PMC12508995", "databridgemarketresearch.com", "youtube.com")
    for fragment in samples:
        rec = next(r for r in state["kept"] if fragment in r["url"])
        original_text = rec.get("text", "")
        print(f"START {rec['url']}", flush=True)
        replay_client = llm
        seeded = False
        if baseline:
            previous = next(i for i in baseline["sample"] if i["url"] == rec["url"])
            prior_audit = json.loads(Path(previous["audit_path"]).read_text(encoding="utf-8"))
            if prior_audit["attempts"] and prior_audit["attempts"][0].get("analysis"):
                first = prior_audit["attempts"][0]
                if args.focused_excerpts:
                    options = evidence_options(original_text, DevelopmentAnalysis.model_validate(first["analysis"]))
                    rec = {**rec, "text": "\n\n".join(options), "extraction_quality": "partial",
                           "text_truncated": True}
                class SeededFirst:
                    def __init__(self):
                        self.calls = 0
                        self.last_attempts = []
                    def analyze(self, **kwargs):
                        self.calls += 1
                        if self.calls == 1:
                            self.last_attempts = first.get("provider_attempts", [])
                            return DevelopmentAnalysis.model_validate(first["analysis"])
                        try:
                            return llm.analyze(**kwargs)
                        finally:
                            self.last_attempts = getattr(llm, "last_attempts", [])
                replay_client = SeededFirst()
                seeded = True
        analysis, problems, audit = analyze_with_quality(replay_client, cfg, rec, args.edition, [])
        record = json.loads(Path(audit).read_text(encoding="utf-8"))
        item = {"url": rec["url"], "outcome": record["outcome"],
                "attempts": len(record["attempts"]), "problems": problems, "audit_path": audit}
        item["initial_response_reused"] = seeded
        item["new_model_calls"] = len(record["attempts"]) - int(seeded)
        item["focused_excerpts"] = args.focused_excerpts
        if analysis:
            item["quotes_also_match_original_source"] = all(quote_match(q, original_text) for q in analysis.evidence_refs)
            if not item["quotes_also_match_original_source"]:
                item["outcome"] = "quarantined_original_source_mismatch"
                item["problems"].append("A diagnostic quote spans excerpt boundaries; it does not match the original source.")
            section, reason = report_section({**rec, **analysis.model_dump(mode="json")}, args.edition, cfg)
            item.update(is_new_development=analysis.is_new_development,
                        potential_section=section, section_reason=reason,
                        maturity=analysis.maturity.value, confidence=analysis.confidence_label.value)
        summary["sample"].append(item)
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"DONE {item['outcome']} attempts={item['attempts']}", flush=True)
    print(f"COMPLETE {summary_path.resolve()}", flush=True)


if __name__ == "__main__":
    main()
