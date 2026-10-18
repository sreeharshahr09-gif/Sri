"""Local Résumé & Cover Letter Agent V3.  Launch: streamlit run app.py"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import traceback
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import streamlit as st

from engine import (
    DEFAULT_LENGTH, LENGTH_OPTIONS, LETTER_WORDS, LocalLLM, analyze, build_source, letter_metrics,
    optimize, optimize_cover_letter, read_file, requirements_from, review_text, score_resume,
)
from exporters import cover_letter_docx, package_files, save_package, to_docx, to_pdf, to_text, zip_files

APP_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = os.environ.get("RESUME_AGENT_OUTPUT", str(APP_DIR / "outputs"))
FACTS_FILE = APP_DIR / "my_fact_library.md"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

st.set_page_config(page_title="Local Job Application Agent", page_icon="📄", layout="wide")


def wide() -> dict:
    major, minor = (int(x) for x in st.__version__.split(".")[:2])
    return {"width": "stretch"} if (major, minor) >= (1, 50) else {"use_container_width": True}


def soft_breaks(md: str) -> str:
    """Show single line breaks as breaks when rendering Markdown on screen."""
    return re.sub(r"(?<!\n)\n(?!\n|- |\* )", "  \n", md)


def fingerprint(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


ss = st.session_state
ss.setdefault("facts", FACTS_FILE.read_text(encoding="utf-8") if FACTS_FILE.exists() else "")
# Widget state can only change before the widget is drawn, so updates are queued.
if "facts_replace" in ss:
    ss["facts"] = ss.pop("facts_replace")
if "facts_append" in ss:
    ss["facts"] = (ss["facts"].rstrip() + "\n" + ss.pop("facts_append")).strip()

st.title("Local Job Application Agent")
st.caption("Paste a job description and your CV → evidence audit → iterative, fact-checked tailoring "
           "→ cover letter → Word / PDF / ZIP. Runs on your own llama.cpp model; nothing leaves this PC.")

# --- sidebar --------------------------------------------------------------------------------------
with st.sidebar:
    st.header("Local model (llama.cpp)")
    endpoint = st.text_input("llama-server URL", os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8080/v1"),
                             help="Your already running llama-server. Only localhost addresses are accepted.")
    model = st.text_input("Model ID (blank = server default)", "")
    no_think = st.checkbox("Disable model thinking (recommended for Qwen3)", value=True,
                           help="Sends enable_thinking=false so reasoning tokens don't use up the "
                                "output budget. Turn off only for models without this switch.")
    c1, c2 = st.columns(2)
    if c1.button("Check server"):
        try:
            probe = LocalLLM(endpoint, model, no_think)
            ids = probe.models()
            st.success(f"Connected · {', '.join(ids) or 'no model id'} · context {probe.context_size():,} tokens")
            if probe.context_size() < 8192:
                st.warning("Context below 8k tokens: long CVs will be processed in many small parts. "
                           "Consider --ctx-size 16384.")
        except Exception as exc:
            st.error(f"Connection failed: {exc}")
    if c2.button("Test generation"):
        try:
            probe = LocalLLM(endpoint, model, no_think)
            started = time.monotonic()
            reply = probe.chat("Answer in one sentence.", "What is a cover letter?", max_tokens=80, json_mode=False)
            st.success(f"Generation works ({time.monotonic() - started:.1f}s)")
            st.code(reply[:400])
        except Exception as exc:
            st.error(f"Generation failed: {exc}")

    st.divider()
    st.header("Tailoring loop")
    target = st.slider("Target truthful match (%)", 50, 100, 100,
                       help="Share of your evidence-backed JD requirements whose JD terms are visible "
                            "in the résumé. 100% is reachable without inventing anything.")
    rounds = st.slider("Maximum résumé rounds", 1, 5, 3)
    length = st.selectbox("Résumé length", list(LENGTH_OPTIONS), index=list(LENGTH_OPTIONS).index(DEFAULT_LENGTH))
    st.caption("Each round: rewrite → check numbers/dates/tools → model fact audit → score. "
               "Rejected drafts are retried with the exact problems fed back.")
    st.divider()
    st.header("Saving")
    autosave = st.checkbox("Also save every package to a local folder", value=True)
    out_dir = st.text_input("Output folder", DEFAULT_OUTPUT)

# --- inputs ------------------------------------------------------------------------------------------
left, right = st.columns(2)
with left:
    st.subheader("1 · Job description")
    jd_file = st.file_uploader("Upload JD (Word, PDF, text)", type=["docx", "pdf", "txt", "md"], key="jd_file")
    jd_paste = st.text_area("…or paste the JD", height=200, key="jd_paste")
with right:
    st.subheader("2 · Your current CV / résumé")
    cv_file = st.file_uploader("Upload CV (Word, PDF, text)", type=["docx", "pdf", "txt", "md"], key="cv_file")
    cv_paste = st.text_area("…or paste your CV", height=200, key="cv_paste")

with st.expander("3 · Extra true facts about you (optional, raises the evidence score honestly)",
                 expanded=bool(ss["facts"])):
    st.caption("Skills or achievements that are true but missing from your CV. They are treated as evidence "
               "exactly like your CV, so add only what you could defend in an interview.")
    st.text_area("Confirmed facts (one per line)", key="facts", height=120,
                 placeholder="- Built a C++ plug-in for the test-rig controller at Alpha Motors (2023).")
    f1, f2, _ = st.columns([1, 1, 3])
    if f1.button("Save to my fact library"):
        FACTS_FILE.write_text(ss["facts"], encoding="utf-8")
        st.success(f"Saved to {FACTS_FILE.name} (local file).")
    if f2.button("Reload fact library") and FACTS_FILE.exists():
        ss["facts_replace"] = FACTS_FILE.read_text(encoding="utf-8")
        st.rerun()

st.subheader("4 · Cover letter")
make_letter = st.checkbox("Also write a fact-checked cover letter", value=True)
company = role = addressee = motivation = ""
tone, letter_rounds, letter_words = "Professional", 3, 300
if make_letter:
    a, b, c = st.columns(3)
    company = a.text_input("Company name", placeholder="e.g. Beta Engineering")
    role = b.text_input("Position title", placeholder="e.g. Senior R&D Engineer")
    addressee = c.text_input("Recipient (optional)", placeholder="blank → Dear Hiring Manager")
    d, e, f = st.columns(3)
    tone = d.selectbox("Style", ["Professional", "Technical / Research", "Academic", "Executive / Leadership"])
    letter_words = e.slider("Approximate length (words)", 200, 450, 300, step=25)
    letter_rounds = f.slider("Cover-letter rounds", 1, 4, 3)
    motivation = st.text_area("Why this job? (optional; guides tone, never used as evidence)", height=70)


def read_inputs() -> tuple[str, str]:
    jd = read_file(jd_file.name, jd_file.getvalue()) if jd_file else jd_paste.strip()
    cv = read_file(cv_file.name, cv_file.getvalue()) if cv_file else cv_paste.strip()
    if len(jd) < 90:
        raise ValueError("Please provide the full job description (upload or paste).")
    if len(cv) < 90:
        raise ValueError("Please provide your CV (upload or paste).")
    return jd, cv


def new_llm() -> LocalLLM:
    return LocalLLM(endpoint, model, no_think)


class Progress:
    def __init__(self, box, llm):
        self.started, self.llm, self.lines = time.monotonic(), llm, []
        self.line = box.empty()

    def __call__(self, msg: str) -> None:
        elapsed = time.strftime("%M:%S", time.gmtime(time.monotonic() - self.started))
        self.lines.append(f"[{elapsed}] {msg}")
        self.line.markdown(f"**{msg}**  \n⏱ {elapsed} · {self.llm.stats.summary()}")


def cached_analysis(jd: str, cv: str, facts: str):
    item = ss.get("analysis")
    key = fingerprint(jd, cv, facts, endpoint, model)
    return (item if item and item["key"] == key else None), key


def run_analysis(jd: str, cv: str, facts: str, llm, progress) -> dict:
    found, key = cached_analysis(jd, cv, facts)
    if found:
        progress("Re-using the job/evidence analysis from before (inputs unchanged).")
        return found
    result = analyze(jd, cv, llm, facts, progress)
    result["key"] = key
    ss["analysis"] = result
    return result


def audit_rows(requirements: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{
        "Priority": r["priority"].title(), "Requirement": r["text"], "Status": r["status"].title(),
        "Evidence (verbatim)": r["evidence"] or "—",
        "From": {"resume": "CV", "confirmed_fact": "Your facts"}.get(r["evidence_origin"], ""),
        "JD terms": ", ".join(r["keywords"]), "Why": r["rationale"]} for r in requirements])


def build_exports(result: dict) -> None:
    """(Re)build Word/PDF/ZIP from result['markdown'] and the letter; optionally save to disk."""
    warnings = []
    result["resume_docx"] = to_docx(result["markdown"])
    result["resume_pdf"] = None
    try:
        result["resume_pdf"] = to_pdf(result["resume_docx"])
    except Exception as exc:
        warnings.append("Résumé PDF: " + str(exc))
    letter = result.get("cover_letter") or {}
    result["letter_docx"] = result["letter_pdf"] = None
    if letter.get("generated"):
        result["letter_docx"] = cover_letter_docx(letter["markdown"])
        try:
            result["letter_pdf"] = to_pdf(result["letter_docx"])
        except Exception as exc:
            warnings.append("Cover-letter PDF: " + str(exc))
    result["pdf_warnings"] = warnings
    report = {k: v for k, v in result.items() if not k.endswith(("_docx", "_pdf", "_zip", "_files"))
              and k not in ("audit_csv", "saved_to")}
    result["audit_csv"] = audit_rows(result["requirements"]).to_csv(index=False)
    result["report_json"] = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    files = package_files(result["resume_docx"], result["resume_pdf"], result["letter_docx"],
                          result["letter_pdf"], report, result["audit_csv"], result["markdown"],
                          letter.get("markdown", "") if letter.get("generated") else "")
    result["bundle_zip"] = zip_files(files)
    result["saved_to"] = ""
    if autosave:
        try:
            label = "_".join(x for x in (result.get("company"), result.get("role")) if x) or "application"
            result["saved_to"] = str(save_package(out_dir, label, files))
        except Exception as exc:
            warnings.append(f"Could not save to {out_dir}: {exc}")


# --- actions ----------------------------------------------------------------------------------------
st.divider()
b1, b2 = st.columns([1, 2])
do_analyze = b1.button("① Analyze match only", **wide()) or ss.pop("auto_analyze", False)
do_tailor = b2.button("② Tailor my résumé" + (" + cover letter" if make_letter else "") +
                      " (runs the loop)", type="primary", **wide())

if do_analyze:
    ss.pop("result", None)
    try:
        jd_text, cv_text = read_inputs()
        llm = new_llm()
        with st.status("Analyzing the job against your CV…", expanded=True) as box:
            run_analysis(jd_text, cv_text, ss["facts"], llm, Progress(box, llm))
            box.update(label=f"Analysis done · {llm.stats.summary()}", state="complete", expanded=False)
    except Exception as exc:
        st.error(f"Analysis failed: {exc}")
        with st.expander("Technical details"):
            st.code(traceback.format_exc())

if do_tailor:
    ss.pop("result", None)
    try:
        jd_text, cv_text = read_inputs()
        facts = ss["facts"]
        llm = new_llm()
        with st.status("Tailoring your application…", expanded=True) as box:
            progress = Progress(box, llm)
            analysis = run_analysis(jd_text, cv_text, facts, llm, progress)
            result = optimize(jd_text, cv_text, llm, target, rounds, progress, facts, length, analysis)
            result.update({"company": company, "role": role, "facts": facts,
                           "run_id": fingerprint(str(time.time())), "jd": jd_text,
                           "source": build_source(cv_text, facts), "cover_letter": None, "letter_error": ""})
            if make_letter:
                try:
                    letter = optimize_cover_letter(
                        jd_text, cv_text, result["markdown"], requirements_from(result["requirements"]), llm,
                        company=company, role=role, addressee=addressee, tone=tone, motivation=motivation,
                        max_rounds=letter_rounds, target=target, progress=progress, facts=facts,
                        words=letter_words)
                    result["cover_letter"] = letter
                    if not letter["generated"]:
                        result["letter_error"] = ("Every cover-letter draft failed the fact checks; see the "
                                                  "rejection notes. Your résumé is unaffected.")
                except Exception as exc:
                    result["letter_error"] = f"Cover letter could not be completed: {exc}"
            progress("Building Word / PDF / ZIP files…")
            result["llm_usage"] = asdict(llm.stats)
            result["progress_log"] = progress.lines
            build_exports(result)
            box.update(label=f"Done · {llm.stats.summary()}", state="complete", expanded=False)
        ss["result"] = result
    except Exception as exc:
        st.error(f"Could not complete the application: {exc}")
        with st.expander("Technical details"):
            st.code(traceback.format_exc())

# --- analysis view (before tailoring) ------------------------------------------------------------------
result = ss.get("result")
analysis = ss.get("analysis")
if analysis and not result:
    base = analysis["baseline"]
    st.subheader("Match analysis")
    m1, m2, m3 = st.columns(3)
    m1.metric("Evidence-backed fit", f"{base['qualification_fit']:.0f}%",
              help="How much of the JD (weighted: must = 3, preferred = 1) your CV/facts actually prove.")
    m2.metric("Truthful match in current CV", f"{base['truthful_match']:.0f}%",
              help="Of what you can prove, how much is visible in JD terms right now. The loop raises this.")
    m3.metric("Must-have gaps", str(len(base["must_missing"])))
    st.dataframe(audit_rows(analysis["requirements"]), hide_index=True, **wide())
    gaps = [r for r in analysis["requirements"] if r["status"] != "supported"]
    if gaps:
        st.markdown("**Close the gaps honestly.** If you really have one of these, describe it "
                    "(where, when, what you did). Leave the rest blank — they will not be claimed.")
        with st.form("gap_form"):
            answers = {r["id"]: st.text_input(f"[{r['priority']}] {r['text']} — currently {r['status']}",
                                              key=f"gap_{r['id']}") for r in gaps}
            if st.form_submit_button("Add my answers to the facts and re-check"):
                lines = [f"- {text.strip()}" for rid, text in answers.items() if text.strip()]
                if lines:
                    ss["facts_append"] = "\n".join(lines)
                    ss["auto_analyze"] = True
                    st.rerun()

# --- results ---------------------------------------------------------------------------------------------
if result:
    st.divider()
    head, dl = st.columns([3, 2])
    head.subheader("Your tailored application")
    dl.download_button("⬇ Download everything (ZIP)", result["bundle_zip"], "Tailored_Job_Application.zip",
                       mime="application/zip", type="primary", **wide())
    if result.get("saved_to"):
        st.success(f"Saved locally to: {result['saved_to']}")
    if result["pdf_warnings"]:
        st.info("PDF needs Microsoft Word (+ pywin32) or LibreOffice on this PC; the Word files are ready.")
    if result["letter_error"]:
        st.warning(result["letter_error"])
    notice = result.pop("notice", "")
    if notice:
        (st.warning if notice.startswith("Rebuilt, but") else st.success)(notice)

    reqs = requirements_from(result["requirements"])
    # A radio keeps the chosen view across reruns (tabs jump back to the first one).
    view = st.radio("View", ["Match dashboard", "Tailored résumé", "Cover letter", "Evidence & audit",
                             "Run log"], horizontal=True, key="result_view", label_visibility="collapsed")

    if view == "Match dashboard":
        before, after = result["baseline"], result["final"]
        a, b, c, d = st.columns(4)
        a.metric("Truthful match", f"{after['truthful_match']:.0f}%",
                 f"{after['truthful_match'] - before['truthful_match']:+.0f} pts vs your CV")
        b.metric("Evidence-backed fit", f"{after['qualification_fit']:.0f}%",
                 help="Fixed by real evidence. Only true facts you add can raise it.")
        c.metric("All JD keywords present", f"{after['literal_keyword_coverage']:.0f}%",
                 help="Includes keywords for skills you do not have; these are deliberately not added.")
        d.metric("Requirements proven", f"{after['requirements_supported']}/{after['requirements_total']}",
                 f"+{after['requirements_partial']} partial", delta_color="off")
        if after["truthful_match"] >= 99.95:
            st.success("Everything your CV can prove is now visible in the job's own terms.")
        if after["qualification_fit"] < 100:
            missing = [r.text for r in reqs if r.status != "supported"]
            st.info("**Why isn't the evidence-backed fit 100%?** These requirements are not (fully) proven by "
                    "your CV: " + "; ".join(missing) + ". If any is genuinely true, add it under "
                    "'Extra true facts' and run again — the agent will never invent it.")
        chart = pd.DataFrame({
            "Metric": ["Truthful match", "Evidence-backed fit", "All JD keywords"],
            "Your CV": [before["truthful_match"], before["qualification_fit"], before["literal_keyword_coverage"]],
            "Tailored": [after["truthful_match"], after["qualification_fit"], after["literal_keyword_coverage"]],
        }).set_index("Metric")
        st.bar_chart(chart, height=240)
        st.markdown("**Résumé rounds**")
        st.dataframe(pd.DataFrame(result["history"]).astype(str), hide_index=True, **wide())
        st.caption("Local, transparent heuristics — not a Workday/Greenhouse/Taleo score or a hiring prediction.")

    elif view == "Tailored résumé":
        if not result["tailored_draft_accepted"]:
            st.warning("No rewritten section passed the fact checks; your original text was kept. "
                       "See 'Run log' for the reasons.")
        if result["lint"]:
            st.warning("Style notes: " + "; ".join(result["lint"]))
        if result["length_note"]:
            st.info(result["length_note"])
        o, n = st.columns(2)
        with o:
            st.markdown("##### Your CV (reformatted)")
            with st.container(height=620):
                st.markdown(soft_breaks(result["original_markdown"]))
        with n:
            st.markdown("##### Tailored")
            with st.container(height=620):
                st.markdown(soft_breaks(result["markdown"]))
        x, y, z = st.columns(3)
        x.download_button("Word résumé", result["resume_docx"], "Tailored_Resume.docx", mime=DOCX_MIME, **wide())
        if result["resume_pdf"]:
            y.download_button("PDF résumé", result["resume_pdf"], "Tailored_Resume.pdf",
                              mime="application/pdf", **wide())
        z.download_button("Plain text (for web forms)", to_text(result["markdown"]).encode("utf-8"),
                          "Tailored_Resume.txt", mime="text/plain", **wide())
        with st.expander("✏️ Edit the résumé yourself, then rebuild the files"), \
                st.form(f"resume_form_{result['run_id']}", border=False):
            edited = st.text_area("Markdown (# name, ## headings, - bullets)", result["markdown"], height=420)
            if st.form_submit_button("Re-check my edits and rebuild Word/PDF/ZIP"):
                problems = review_text(result["source"], edited, reqs)
                result["markdown"] = edited
                result["final"] = score_resume(reqs, edited)
                build_exports(result)
                result["notice"] = ("Rebuilt, but please double-check: " + "; ".join(problems)) if problems \
                    else "Rebuilt. No new numbers, tools or unsupported claims detected."
                st.rerun()

    elif view == "Cover letter":
        cover = result.get("cover_letter")
        if cover and cover.get("generated"):
            m = cover["metrics"]
            a, b, c = st.columns(3)
            a.metric("Truthful match", f"{m['truthful_match']:.0f}%")
            b.metric("Words", str(m["word_count"]))
            c.metric("Editorial proxy", f"{m['editorial_proxy']:.0f}%")
            with st.container(border=True):
                st.markdown(soft_breaks(cover["markdown"]))
            x, y = st.columns(2)
            x.download_button("Word cover letter", result["letter_docx"], "Tailored_Cover_Letter.docx",
                              mime=DOCX_MIME, **wide())
            if result["letter_pdf"]:
                y.download_button("PDF cover letter", result["letter_pdf"], "Tailored_Cover_Letter.pdf",
                                  mime="application/pdf", **wide())
            with st.expander("✏️ Edit the letter yourself, then rebuild the files"), \
                    st.form(f"letter_form_{result['run_id']}", border=False):
                edited = st.text_area("Letter", cover["markdown"], height=380)
                if st.form_submit_button("Re-check my edits and rebuild Word/PDF/ZIP"):
                    extra = "\n".join([result["jd"], company, role, addressee, LETTER_WORDS])
                    problems = review_text(result["source"], edited, reqs, extra)
                    cover["markdown"] = edited
                    cover["metrics"] = letter_metrics(edited, reqs, letter_words)
                    build_exports(result)
                    result["notice"] = ("Rebuilt, but please double-check: " + "; ".join(problems)) \
                        if problems else "Rebuilt. No new numbers, tools or unsupported claims detected."
                    st.rerun()
            st.markdown("**Cover-letter rounds**")
            st.dataframe(pd.DataFrame(cover["history"]).astype(str), hide_index=True, **wide())
        elif not cover and not result["letter_error"]:
            st.info("No cover letter was requested in this run.")
        if cover and cover["rejections"]:
            with st.expander(f"Rejected cover-letter drafts ({len(cover['rejections'])})"):
                for item in cover["rejections"]:
                    st.write("• " + item)

    elif view == "Evidence & audit":
        st.dataframe(audit_rows(result["requirements"]), hide_index=True, **wide())
        if result["final"]["must_missing"]:
            st.markdown("**Must-have requirements not fully proven:** " + "; ".join(result["final"]["must_missing"]))
        if result["keyword_patch"]:
            st.info("Added to Skills because your CV proves them but never used the JD's wording: "
                    + ", ".join(result["keyword_patch"]))
        x, y = st.columns(2)
        x.download_button("Requirement audit (CSV)", result["audit_csv"].encode("utf-8-sig"),
                          "JD_Requirement_Audit.csv", mime="text/csv", **wide())
        y.download_button("Full report (JSON)", result["report_json"], "Application_Match_Report.json",
                          mime="application/json", **wide())
        st.info("Always read the final documents yourself before applying. The checks catch a lot, "
                "but a local model can still misjudge whether a quote really proves a requirement.")

    elif view == "Run log":
        usage = result.get("llm_usage") or {}
        st.write(f"**Model usage:** {usage.get('calls', 0)} calls · {usage.get('prompt_tokens', 0):,} prompt "
                 f"tokens · {usage.get('completion_tokens', 0):,} output tokens · "
                 f"{usage.get('seconds', 0):.0f}s · {usage.get('json_retries', 0)} JSON retries")
        st.markdown("**How your CV was handled**")
        st.dataframe(pd.DataFrame(result["sections"]), hide_index=True, **wide())
        for note in result.get("notes", []):
            st.caption("ℹ️ " + note)
        if result["rejections"]:
            with st.expander(f"Rejected résumé drafts ({len(result['rejections'])}) — and why"):
                for item in result["rejections"]:
                    st.write("• " + item)
        with st.expander("Progress log"):
            st.code("\n".join(result.get("progress_log", [])) or "(empty)")
