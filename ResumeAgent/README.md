# Local Résumé & Cover Letter Agent — V3

Paste a job description and your current CV. The agent analyses the job, checks what your CV
actually proves, then rewrites your résumé and cover letter in a loop with your **own local
llama.cpp model**. Each draft is fact-checked before it's accepted. You get editable Word files,
PDFs, plain text for web forms, an audit CSV/JSON and a ZIP, all saved locally.

No cloud API, no OpenAI key, and no LangChain. Inference goes only to `127.0.0.1`.

## What "100% match" means here

The app reports three different numbers and never fakes them:

| Metric | Meaning | Can the loop raise it? |
|---|---|---|
| **Truthful match** | Of the JD requirements your CV/facts *prove*, the share whose JD wording is visible in the résumé. | **Yes. This is the loop's target (default 100%).** |
| **Evidence-backed fit** | Weighted share of the JD (must = 3, preferred = 1) that your CV/facts prove at all. | No. Only **true facts you add** raise it. |
| **All JD keywords present** | Literal JD keyword coverage, including skills you don't have. | Not on purpose. Missing skills are never added. |

So if the JD asks for C++ and your CV has no C++, the résumé will **not** claim C++. Instead the
app lists it as a gap and asks you about it. If you really have it, type a sentence ("Built a C++
plug-in for the test-rig controller at Alpha Motors, 2023"). It becomes evidence and the fit goes up.

## How the loop works

```
JD ──► requirements (each with a literal JD quote; long JDs read in parts)
CV + your facts ──► evidence audit (each "supported" needs a verbatim quote; long CVs in parts)
          │
          ▼  for each round (1-5):
   for each rewritable section (Summary, Skills, Experience, Projects, …):
       writer ─► deterministic checks ─► model fact audit ─► score ─► keep or retry
                 • new numbers / %          • every claim vs
                 • new emails / links         your CV + facts
                 • unsupported JD terms
                 • unknown tools/names
                 • dropped dates/roles
       rejected? → retried once with the exact problems listed
   stop when truthful match ≥ target and no style problems
          │
          ▼
   Skills patch: JD terms your CV proves but never used are added to Skills
   Education, publications, patents, certifications, references → kept word-for-word
          │
          ▼
Cover letter: writer → checks → fact audit → editorial critique → revise (1-4 rounds)
          │
          ▼
Word + PDF + TXT + audit CSV/JSON + ZIP  (downloaded and auto-saved to outputs/)
```

## Quick start (Windows, existing llama-server)

1. Make sure your Qwen `llama-server` is running (normally the one your Research Agent uses).
   Test: `curl.exe http://127.0.0.1:8080/v1/models`
2. Double-click **`start_windows.bat`**. It:
   - finds the running model API on port 8080, then 8090 (never starts a second model),
   - uses `C:\ProgramData\anaconda3\python.exe` if it already has the packages, otherwise creates a
     private `.venv` and installs `requirements.txt` (one-time, needs internet),
   - opens the app on **http://127.0.0.1:8502** (8503 if busy; your Research Agent stays on 8501).
3. In the sidebar, click **Check server** and then **Test generation**.
4. Paste or upload the JD and your CV, then click **① Analyze match only**.
   Answer any gaps you genuinely have, then click **② Tailor my résumé + cover letter**.
5. Review the side-by-side view, edit if you want (the editor re-checks your edits), and download.
   Each run is also saved to `outputs\<date>_<company>_<role>\`.

If the batch file fails, use Anaconda Prompt in this folder:

```bat
"C:\ProgramData\anaconda3\python.exe" -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
set LLM_BASE_URL=http://127.0.0.1:8080/v1
.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8502
```

macOS/Linux: `./start.sh`.

### llama-server settings that matter

- **`--jinja`**: lets the app turn Qwen3 "thinking" off per request (sidebar checkbox, on by
  default). Without it, reasoning tokens can use up the answer budget. `--reasoning-budget 0`
  is a server-wide alternative.
- **`--ctx-size`**: 8192 works because the app reads `/props` and splits long documents into parts.
  16384 means fewer parts, so it runs faster and is more coherent on long PhD CVs.
- `llama_server_example_windows.bat` is an **optional** template for when no server is running,
  including `--n-cpu-moe` so a 35B-A3B MoE model fits in 8 GB of VRAM. Check the paths first.

## Input tips

- DOCX, text-based PDF, TXT and MD are supported, and so is pasting. Scanned PDFs need OCR first.
- Clear section headings in your CV (EXPERIENCE, EDUCATION, SKILLS, PUBLICATIONS…) give the best
  results: each section is rewritten and checked on its own, and factual sections are kept verbatim.
  A CV with no recognisable headings is rewritten as one document.
- **Extra true facts** can be saved to `my_fact_library.md` (a local file) and reused for every job.

## Files

```
app.py            Streamlit UI and workflow
engine.py         analysis, scoring, checks, résumé loop, cover-letter loop
llm_client.py     loopback-only llama-server client (JSON retries, thinking switch, /props)
textutils.py      file reading, section detection, chunking, phrase/number/term matching
exporters.py      Word, offline PDF (Word COM or LibreOffice), TXT, ZIP, local save
start_windows.bat launcher (reuses running llama-server + Anaconda/.venv)
tests/            offline tests with a scripted fake model and a fake HTTP llama-server
```

## Tests

```
python -m unittest discover -s tests -v
```

These are 54 offline tests. The model is **mocked**: a scripted fake model, plus a fake
llama-server over real HTTP for the client. They cover JD quote validation, long-JD and
long-CV chunking (evidence found past the old 9,500-character limit), confirmed facts, malformed
JSON retries, `<think>` stripping, the context-overflow message, invented numbers/tools/contacts,
unsupported JD terms, dropped dates and roles, retrying with feedback, verbatim publications, the
Skills patch (supported terms only, never partial), scoring maths, the cover-letter checks,
DOCX/TXT/ZIP export, and a PDF text-order check (runs only when LibreOffice is installed).

**Not yet verified:** a live run against your Qwen3.6-35B-A3B model, and the Word-COM PDF
path on your PC. Do your first run with `samples/` (fictional), then with your real CV.
Read every claim before you send an application.
