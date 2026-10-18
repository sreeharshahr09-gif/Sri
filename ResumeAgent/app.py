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

from embeddings import EmbeddingClient
from keywords import Keyword, extract_keywords, highlight, merge_requirement_terms
from keywords import _count as kw_status_count, report as kw_report, status as kw_status
from llm_client import DEFAULT_OPENROUTER_MODEL, Redactor, make_llm
from textutils import contact_lines, split_header, split_sections
from engine import (
    DEFAULT_LENGTH, LENGTH_OPTIONS, LETTER_WORDS, LocalLLM, analyze, build_source, format_resume, letter_metrics,
    optimize, optimize_cover_letter, read_file, requirements_from, review_text, score_resume,
)
from exporters import cover_letter_docx, package_files, save_package, to_docx, to_pdf, to_text, zip_files

APP_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = os.environ.get("RESUME_AGENT_OUTPUT", str(APP_DIR / "outputs"))
FACTS_FILE = APP_DIR / "my_fact_library.md"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Display: small text and a neutral title by default, so the screen stays unobtrusive.
# The choice is kept in the URL (?size=…&quiet=…), so a browser refresh keeps it.
TEXT_SIZES = {"Small": 13, "Medium": 14.5, "Large (Streamlit default)": 16}
st.session_state.setdefault("ui_size", st.query_params.get("size") if st.query_params.get("size") in TEXT_SIZES
                            else "Small")
st.session_state.setdefault("ui_quiet", st.query_params.get("quiet", "1") != "0")
APP_TITLE = "Document workbench" if st.session_state["ui_quiet"] else "Local Job Application Agent"
st.set_page_config(page_title=APP_TITLE, page_icon="📄", layout="wide")


def apply_look(size: str) -> None:
    px = TEXT_SIZES.get(size, 13)
    st.markdown(f"""<style>
html {{ font-size: {px}px !important; }}
h1 {{ font-size: 1.55rem !important; padding: 0 0 .3rem 0 !important; }}
h2 {{ font-size: 1.15rem !important; }}
[data-testid="stSidebar"] h2 {{ font-size: 1.02rem !important; }}
h3 {{ font-size: 1.12rem !important; }}
h4, h5 {{ font-size: 1rem !important; }}
[data-testid="stMetricValue"] {{ font-size: 1.6rem !important; }}
[data-testid="stMainBlockContainer"] {{ padding-top: 3.2rem !important; }}
[data-testid="stSidebarHeader"] {{ height: 2rem !important; }}
</style>""", unsafe_allow_html=True)


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

apply_look(ss["ui_size"])
st.title(APP_TITLE)
CLOUD = "OpenRouter (cloud, opt-in)"

# --- sidebar --------------------------------------------------------------------------------------
with st.sidebar:
    st.header("Model")
    provider_label = st.radio("Where the model runs", ["Local llama.cpp (private)", CLOUD],
                              help="Local keeps everything on this PC. OpenRouter sends the job description "
                                   "and your CV text to openrouter.ai and the model provider.")
    provider = "openrouter" if provider_label == CLOUD else "local"
    no_think = st.checkbox("Disable model thinking (recommended)", value=True,
                           help="Local: sends enable_thinking=false (Qwen3 + llama-server --jinja). "
                                "OpenRouter: asks for reasoning effort 'none' where the model allows it.")
    endpoint, model, api_key, redact, consent = "", "", "", True, True
    if provider == "local":
        endpoint = st.text_input("llama-server URL", os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8080/v1"),
                                 help="Your already running llama-server. Only localhost addresses are accepted.")
        model = st.text_input("Model ID (blank = server default)", "")
    else:
        api_key = st.text_input("OpenRouter API key", os.environ.get("OPENROUTER_API_KEY", ""), type="password",
                                help="From openrouter.ai → Keys. Kept in memory only; never saved or exported. "
                                     "Tip: set OPENROUTER_API_KEY in start_windows.bat instead of typing it.")
        choices = ss.get("or_free_models") or [os.environ.get("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)]
        picked = st.selectbox("Model", choices, help="Click 'List free models' to load the current catalogue.")
        model = st.text_input("…or type a model ID", "", placeholder="e.g. nvidia/nemotron-nano-9b-v2:free") or picked
        redact = st.checkbox("Hide my name, email, phone, address and links from the cloud model", value=True,
                             help="They are replaced by placeholders such as [EMAIL_1] before sending and put back "
                                  "in the answers, so your documents still contain them.")
        consent = st.checkbox("I understand the JD and my CV text are sent to OpenRouter and the model provider "
                              "(free models may log prompts to improve their models).", value=False)
        st.caption("Free models: about 20 requests/minute and 50/day (1,000/day after buying $10 of credits). "
                   "One tailoring run uses roughly 15-40 requests.")
    c1, c2 = st.columns(2)
    check_label = "Check server" if provider == "local" else "List free models"
    if c1.button(check_label):
        try:
            if provider == "local":
                probe = LocalLLM(endpoint, model, no_think)
                ids = probe.models()
                st.success(f"Connected · {', '.join(ids) or 'no model id'} · context {probe.context_size():,} tokens")
                if probe.context_size() < 8192:
                    st.warning("Context below 8k tokens: long CVs will be processed in many small parts. "
                               "Consider --ctx-size 16384.")
            else:
                free = make_llm("openrouter", api_key=api_key, model=model).free_models()
                if not free:
                    st.warning("No free models found in the OpenRouter catalogue.")
                else:
                    ss["or_free_models"] = free
                    st.success(f"{len(free)} free models found (NVIDIA first).")
                    st.rerun()
        except Exception as exc:
            st.error(f"Connection failed: {exc}")
    if c2.button("Test generation"):
        try:
            if provider == "openrouter" and not consent:
                raise ValueError("Tick the OpenRouter consent box first.")
            probe = make_llm(provider, base_url=endpoint, model=model, disable_thinking=no_think, api_key=api_key)
            started = time.monotonic()
            reply = probe.chat("Answer in one sentence.", "What is a cover letter?", max_tokens=80, json_mode=False)
            st.success(f"Generation works ({time.monotonic() - started:.1f}s)")
            st.code(reply[:400])
        except Exception as exc:
            st.error(f"Generation failed: {exc}")

    st.divider()
    st.header("Semantic search (optional)")
    use_embed = st.checkbox("Use a local embedding model", value=True,
                            help="A second, small llama-server started with --embedding (e.g. Qwen3-Embedding-0.6B). "
                                 "Finds the CV lines most related to each requirement: a second look at gaps, "
                                 "'possibly related' suggestions and fewer model calls for long CVs. "
                                 "Similarity is never counted as evidence. Skipped automatically if not running.")
    embed_url = os.environ.get("EMBED_BASE_URL", "http://127.0.0.1:8081/v1")
    if use_embed:
        embed_url = st.text_input("Embedding server URL", embed_url)
        if st.button("Check embeddings"):
            try:
                client = EmbeddingClient(embed_url)
                vec = client.embed(["finite element analysis"])[0]
                st.success(f"Embedding server works · {len(vec)}-dimensional vectors")
            except Exception as exc:
                st.error(f"Not reachable: {exc}")

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

    st.header("Display")
    st.selectbox("Text size", list(TEXT_SIZES), key="ui_size")
    st.checkbox("Quiet look (neutral page title, no subtitle)", key="ui_quiet")
    st.query_params.update(size=ss["ui_size"], quiet="1" if ss["ui_quiet"] else "0")

if not ss["ui_quiet"]:
    st.caption("Paste a job description and your CV → evidence audit → iterative, fact-checked tailoring "
               "→ cover letter → Word / PDF / ZIP. " +
               ("Runs on your own llama.cpp model; nothing leaves this PC." if provider == "local" else
                f"Using OpenRouter model {model}" + (" with your contact details hidden." if redact else ".")))

WORKBENCH = "🛠 Keyword workbench — I edit my CV, the score updates"
AI_MODE = "🤖 AI tailoring — the agent rewrites and fact-checks"
mode = st.radio("Mode", [WORKBENCH, AI_MODE], horizontal=True, key="mode", label_visibility="collapsed")

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
                 expanded=bool(ss["facts"]) and mode == AI_MODE):
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

if mode == AI_MODE:
    st.subheader("4 · Cover letter")
    letter_box = st.container()
else:
    letter_box = st.expander("Company, role and cover-letter options (used by the AI buttons below)")
with letter_box:
    make_letter = st.checkbox("Also write a fact-checked cover letter", value=True)
    company = role = addressee = motivation = ""
    tone, letter_rounds, letter_words = "Professional", 3, 300
    if make_letter or mode == WORKBENCH:
        a, b, c = st.columns(3)
        company = a.text_input("Company name", placeholder="e.g. Beta Engineering")
        role = b.text_input("Position title", placeholder="e.g. Senior R&D Engineer")
        addressee = c.text_input("Recipient (optional)", placeholder="blank → Dear Hiring Manager")
    if make_letter:
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


def build_redactor(cv: str, facts: str) -> Redactor:
    """Register the candidate's name and header contact lines found in the CV."""
    redactor = Redactor()
    sections = split_sections(cv)
    if sections[0].kind == "header":
        name, contacts, _ = split_header(sections[0].body)
    else:
        lines = [l for l in cv.splitlines() if l.strip()]
        name, contacts = (lines[0] if lines else ""), []
    contacts += [l for s in sections if s.kind == "contact" for l in contact_lines(s.body)]
    redactor.add_name(name)
    for line in contacts:
        if len(line) <= 70:  # short lines are contact details; long ones are the headline
            redactor.add_contact(line)
    return redactor


def new_llm(cv: str = "", facts: str = ""):
    if provider == "openrouter":
        if not consent:
            raise ValueError("OpenRouter is selected: tick the consent box in the sidebar first "
                             "(or switch back to the local model).")
        return make_llm("openrouter", model=model, disable_thinking=no_think, api_key=api_key,
                        redactor=build_redactor(cv, facts) if redact else None)
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
    key = fingerprint(jd, cv, facts, provider, endpoint, model, str(use_embed), embed_url)
    return (item if item and item["key"] == key else None), key


def run_analysis(jd: str, cv: str, facts: str, llm, progress) -> dict:
    found, key = cached_analysis(jd, cv, facts)
    if found:
        progress("Re-using the job/evidence analysis from before (inputs unchanged).")
        return found
    embedder = None
    if use_embed:
        try:
            embedder = EmbeddingClient(embed_url)
        except ValueError as exc:
            progress(f"Embedding URL ignored: {exc}")
    result = analyze(jd, cv, llm, facts, progress, embedder)
    result["key"] = key
    result["jd_hash"] = fingerprint(jd)
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


def run_tailoring(jd_text: str, cv_text: str) -> None:
    """Analysis (cached) + fact-checked résumé loop + optional cover letter + exports."""
    ss.pop("result", None)
    try:
        facts = ss["facts"] if mode == AI_MODE else ""
        llm = new_llm(cv_text, facts)
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
            result["model_used"] = {"provider": provider, "model": getattr(llm, "model", model),
                                    "contact_details_hidden": bool(provider == "openrouter" and redact)}
            result["progress_log"] = progress.lines
            build_exports(result)
            box.update(label=f"Done · {llm.stats.summary()}", state="complete", expanded=False)
        ss["result"] = result
    except Exception as exc:
        st.error(f"Could not complete the application: {exc}")
        with st.expander("Technical details"):
            st.code(traceback.format_exc())


STATUS_ICON = {"matched": "✅ in CV", "partial": "🟡 partly", "missing": "❌ missing"}


def _kw_key(phrase: str) -> str:
    return re.sub(r"\s+", " ", phrase.lower().replace("-", " ")).strip()


def html_block(marked: str) -> str:
    """One-line HTML (no blank lines), so Markdown never re-interprets the highlighted text."""
    return ('<div style="line-height:1.55;font-size:0.93rem">'
            + marked.replace("\n", "<br>") + "</div>")


def render_workbench() -> None:
    """Manual editing loop: JD with highlighted keywords, editable CV, instant keyword score."""
    st.divider()
    st.subheader("Keyword workbench")
    try:
        jd_text, cv_text = read_inputs()
    except ValueError as exc:
        st.info(f"{exc} Then edit your CV here and watch the keyword score update.")
        return
    cv_key, jd_key = fingerprint(cv_text), fingerprint(jd_text)
    if ss.get("wb_key") != cv_key:  # a new CV starts a fresh session
        for k in [k for k in ss if str(k).startswith("wb_editor_")]:
            del ss[k]
        ss.update({"wb_key": cv_key, "wb_text": cv_text, "wb_history": [], "wb_output": None})
    ss.setdefault("wb_ignored", set())
    ss.setdefault("wb_extra", [])

    keywords = extract_keywords(jd_text)
    analysis = ss.get("analysis")
    has_terms = bool(analysis and analysis.get("jd_hash") == jd_key)
    if has_terms:
        keywords = merge_requirement_terms(keywords, analysis["requirements"])
    known = {_kw_key(k.phrase) for k in keywords}
    for extra in ss["wb_extra"]:
        if _kw_key(extra) not in known:
            keywords.append(Keyword(extra, max(1, kw_status_count(jd_text, extra)), 1.0, "you"))
            known.add(_kw_key(extra))
    tracked = [k for k in keywords if _kw_key(k.phrase) not in ss["wb_ignored"]]
    text = ss["wb_text"]
    now, start = kw_report(tracked, text), kw_report(tracked, cv_text)

    # --- score dashboard
    goal = st.slider("Your target keyword score (%)", 50, 100, 75, key="wb_target",
                     help="Many people aim for about 75-80%. Higher is not better if it means "
                          "keywords you can't back up.")
    a, b, c, d = st.columns(4)
    a.metric("Keyword match", f"{now['score']:.0f}%", f"{now['score'] - start['score']:+.0f} pts vs your file")
    b.metric("In your CV", str(len(now["matched"])))
    c.metric("Partly there", str(len(now["partial"])), help="All the words appear, but not as this exact phrase.")
    d.metric("Missing", str(len(now["missing"])))
    st.progress(min(1.0, now["score"] / 100))
    if now["score"] >= goal:
        st.success(f"Target reached ({now['score']:.0f}% ≥ {goal}%). When you're happy, create your résumé below.")
    if now["missing"]:
        st.markdown("**Missing, most important first:** " + " · ".join(
            f"**{k}**" if any(t.phrase == k and t.weight > 1 for t in tracked) else k for k in now["missing"][:25]))
    if now["stuffed"]:
        st.warning("Used more than 5 times (looks like keyword stuffing): " + ", ".join(now["stuffed"]))
    st.caption("Add a keyword only where it truthfully describes your work; an interviewer will ask about it. "
               + ("**Bold** = key requirement terms from the model's analysis (count double)." if has_terms else
                  "Tip: 'Add key requirement terms' below lets the model pick the must-haves (they count double)."))

    # --- side by side: JD (highlighted) | CV editor
    left, right = st.columns(2)
    with left:
        st.markdown("##### Job description")
        st.markdown('<span style="background:#bbf7d0;color:#111;padding:0 4px">in your CV</span> '
                    '<span style="background:#fde68a;color:#111;padding:0 4px">partly</span> '
                    '<span style="background:#fecaca;color:#111;padding:0 4px">missing</span>',
                    unsafe_allow_html=True)
        with st.container(height=600):
            st.markdown(html_block(highlight(jd_text, tracked, text)), unsafe_allow_html=True)
    with right:
        st.markdown("##### Your CV (edit here)")
        with st.form(f"wb_form_{cv_key}", border=False):
            edited = st.text_area("Your CV text", value=text, height=560, key=f"wb_editor_{cv_key}",
                                  label_visibility="collapsed",
                                  help="'## ' starts a section, '- ' a bullet. Keep your own facts.")
            r1, r2 = st.columns(2)
            refresh = r1.form_submit_button("🔄 Refresh score", type="primary", **wide())
            reset = r2.form_submit_button("↺ Back to my original file", **wide())
        if refresh:
            ss["wb_text"] = edited
            ss["wb_history"].append({"refresh": len(ss["wb_history"]) + 1,
                                     "score": kw_report(tracked, edited)["score"]})
            st.rerun()
        if reset:
            ss["wb_text"] = cv_text
            ss.pop(f"wb_editor_{cv_key}", None)
            st.rerun()

    if ss["wb_history"]:
        hist = pd.DataFrame([{"refresh": 0, "score": start["score"]}] + ss["wb_history"]).set_index("refresh")
        st.line_chart(hist, height=180)

    # --- keyword list the user controls
    with st.expander(f"Keyword list ({len(keywords)}): untick any that don't matter, or add your own"):
        rows = pd.DataFrame([{"Track": _kw_key(k.phrase) not in ss["wb_ignored"], "Keyword": k.phrase,
                              "Status": STATUS_ICON[kw_status(text, k.phrase)], "In JD": k.count,
                              "In your CV": kw_status_count(text, k.phrase),
                              "From": {"requirement": "model (×2)" if k.weight > 1 else "model",
                                       "you": "you"}.get(k.source, "JD")} for k in keywords])
        table = st.data_editor(rows, hide_index=True, disabled=["Keyword", "Status", "In JD", "In your CV", "From"],
                               key=f"wb_table_{fingerprint(rows.to_json())}", **wide())
        ignored = {_kw_key(r["Keyword"]) for _, r in table.iterrows() if not r["Track"]}
        if ignored != ss["wb_ignored"]:
            ss["wb_ignored"] = ignored
            st.rerun()
        x, y = st.columns([3, 1])
        new = x.text_input("Add keywords (comma-separated)", key="wb_add", label_visibility="collapsed",
                           placeholder="Add keywords, comma-separated, e.g. SolidWorks, GD&T")
        if y.button("Add", **wide()) and new.strip():
            ss["wb_extra"] += [w.strip() for w in new.split(",") if w.strip()]
            st.rerun()
        if not has_terms and st.button("🔍 Add key requirement terms from the model (one analysis run)"):
            try:
                llm = new_llm(cv_text, "")
                with st.status("Asking the model for the job's key requirements…", expanded=True) as box:
                    run_analysis(jd_text, cv_text, "", llm, Progress(box, llm))
                    box.update(label="Done", state="complete", expanded=False)
                st.rerun()
            except Exception as exc:
                st.error(f"Could not run the analysis: {exc}")

    with st.expander("Your CV with keywords highlighted"):
        st.markdown(html_block(highlight(text, tracked, text)), unsafe_allow_html=True)

    # --- finish
    st.markdown("#### When you're happy with the score")
    f1, f2 = st.columns(2)
    if f1.button("✅ Create my résumé from this text (word for word, no AI)", type="primary", **wide()):
        md = format_resume(ss["wb_text"])
        out = {"markdown": md, "docx": to_docx(md), "pdf": None, "saved_to": "", "warning": ""}
        try:
            out["pdf"] = to_pdf(out["docx"])
        except Exception as exc:
            out["warning"] = str(exc)
        files = {"My_Resume.docx": out["docx"], "My_Resume.txt": to_text(md).encode("utf-8"),
                 "Keyword_Report.json": json.dumps({"score": now["score"], "matched": now["matched"],
                                                    "partial": now["partial"], "missing": now["missing"]},
                                                   ensure_ascii=False, indent=2).encode("utf-8")}
        if out["pdf"]:
            files["My_Resume.pdf"] = out["pdf"]
        if autosave:
            try:
                label = "_".join(x for x in (company, role) if x) or "my_resume"
                out["saved_to"] = str(save_package(out_dir, label + "_workbench", files))
            except Exception as exc:
                out["warning"] += f" Could not save to {out_dir}: {exc}"
        ss["wb_output"] = out
    polish = "✨ Polish with AI, fact-checked" + (" + cover letter" if make_letter else "")
    if f2.button(polish, **wide(),
                 help="Runs the AI tailoring loop with your edited text as the source of truth. "
                      "Results appear below."):
        run_tailoring(jd_text, ss["wb_text"])

    out = ss.get("wb_output")
    if out:
        if out["saved_to"]:
            st.success(f"Saved locally to: {out['saved_to']}")
        if out["warning"] and not out["pdf"]:
            st.info("PDF needs Microsoft Word (+ pywin32) or LibreOffice on this PC; the Word file is ready.")
        x, y, z = st.columns(3)
        x.download_button("Word résumé", out["docx"], "My_Resume.docx", mime=DOCX_MIME, **wide())
        if out["pdf"]:
            y.download_button("PDF résumé", out["pdf"], "My_Resume.pdf", mime="application/pdf", **wide())
        z.download_button("Plain text", to_text(out["markdown"]).encode("utf-8"), "My_Resume.txt",
                          mime="text/plain", **wide())
        with st.expander("Preview"):
            st.markdown(soft_breaks(out["markdown"]))


if mode == AI_MODE:
    st.divider()
    b1, b2 = st.columns([1, 2])
    do_analyze = b1.button("① Analyze match only", **wide()) or ss.pop("auto_analyze", False)
    do_tailor = b2.button("② Tailor my résumé" + (" + cover letter" if make_letter else "") +
                          " (runs the loop)", type="primary", **wide())

    if do_analyze:
        ss.pop("result", None)
        try:
            jd_text, cv_text = read_inputs()
            llm = new_llm(cv_text, ss["facts"])
            with st.status("Analyzing the job against your CV…", expanded=True) as box:
                run_analysis(jd_text, cv_text, ss["facts"], llm, Progress(box, llm))
                box.update(label=f"Analysis done · {llm.stats.summary()}", state="complete", expanded=False)
        except Exception as exc:
            st.error(f"Analysis failed: {exc}")
            with st.expander("Technical details"):
                st.code(traceback.format_exc())

    if do_tailor:
        try:
            run_tailoring(*read_inputs())
        except ValueError as exc:
            st.error(str(exc))
else:
    render_workbench()

# --- analysis view (before tailoring) ------------------------------------------------------------------
result = ss.get("result")
analysis = ss.get("analysis")
if analysis and not result and mode == AI_MODE:
    base = analysis["baseline"]
    st.subheader("Match analysis")
    m1, m2, m3 = st.columns(3)
    m1.metric("Evidence-backed fit", f"{base['qualification_fit']:.0f}%",
              help="How much of the JD (weighted: must = 3, preferred = 1) your CV/facts actually prove.")
    m2.metric("Truthful match in current CV", f"{base['truthful_match']:.0f}%",
              help="Of what you can prove, how much is visible in JD terms right now. The loop raises this.")
    m3.metric("Must-have gaps", str(len(base["must_missing"])))
    st.dataframe(audit_rows(analysis["requirements"]), hide_index=True, **wide())
    for note in analysis.get("notes", []):
        st.caption("ℹ️ " + note)
    if analysis.get("debug_evidence"):
        with st.expander("Model's raw evidence answer (for troubleshooting)"):
            for raw in analysis["debug_evidence"]:
                st.code(raw, language="json")
    gaps = [r for r in analysis["requirements"] if r["status"] != "supported"]
    if gaps:
        st.markdown("**Close the gaps honestly.** If you really have one of these, describe it "
                    "(where, when, what you did). Leave the rest blank — they will not be claimed.")
        with st.form("gap_form"):
            answers = {}
            related = analysis.get("related") or {}
            for r in gaps:
                answers[r["id"]] = st.text_input(f"[{r['priority']}] {r['text']} — currently {r['status']}",
                                                 key=f"gap_{r['id']}")
                if related.get(r["id"]):
                    st.caption("Closest lines in your CV (related is not the same as proven; use them as a "
                               "reminder of what you did): " +
                               " · ".join(f"“{line[:110]}”" for line, _ in related[r["id"]]))
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
        used = result.get("model_used") or {}
        if used:
            st.write(f"**Model:** {used.get('model') or 'server default'} via "
                     f"{'OpenRouter (cloud)' if used.get('provider') == 'openrouter' else 'local llama.cpp'}"
                     + (" · contact details were hidden from the model" if used.get("contact_details_hidden") else ""))
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
