# Local Résumé & Cover Letter Agent — V3

Paste a job description and your current CV. The agent analyses the job, checks what your CV
actually proves, then rewrites your résumé and cover letter in a loop with your **own local
llama.cpp model**. Each draft is fact-checked before it's accepted. You get editable Word files,
PDFs, plain text for web forms, an audit CSV/JSON and a ZIP, all saved locally.

No LangChain and no paid API. By default inference goes only to `127.0.0.1`. You can
**optionally** switch to OpenRouter's free cloud models (for example NVIDIA Nemotron); see below.

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

## Optional: OpenRouter cloud models (e.g. NVIDIA Nemotron, free)

The sidebar's **Where the model runs** switch selects either your local llama.cpp model (the default)
or **OpenRouter**. To use OpenRouter:

1. Create a key at openrouter.ai → Keys. Either paste it into the sidebar (it stays in memory only)
   or put it in `start_windows.bat`: remove `rem ` from the `OPENROUTER_API_KEY` line and paste it there.
2. Click **List free models**. NVIDIA models are listed first, for example
   `nvidia/nemotron-nano-9b-v2:free` (128K context) or `nvidia/nemotron-3-nano-30b-a3b:free`.
   You can also type any model ID.
3. Tick the consent box, then click **Test generation** before a real run.

Things to know:

- **Privacy:** the JD and CV text go to OpenRouter and the model provider. NVIDIA's free endpoints say
  that prompts and outputs are logged to improve their models. With **Hide my name, email, phone,
  address and links** ticked (the default), those details are replaced by placeholders such as
  `[EMAIL_1]` / `[NAME_1]` before sending and restored in the answers, so your documents still contain
  them. Employers, dates and achievements are not hidden, because the model needs them.
- **Limits:** free models allow about 20 requests/minute and 50 requests/day (1,000/day once you have
  bought $10 of credits). One tailoring run uses roughly 15–40 requests. The app paces requests,
  waits and retries on rate limits, and tells you when the daily cap is reached.
- **Same safeguards:** quote checks, fact audits, verbatim sections and the scoring are identical for
  both providers. Only the model doing the writing changes.
- Only the official `https://openrouter.ai/api/v1` endpoint is accepted. The key is never written to
  the report, the ZIP or the log.

## Optional: semantic search with a local embedding model

A second, small llama-server running an **embedding model** (recommended: the official
`Qwen/Qwen3-Embedding-0.6B-GGUF`, Q8_0, about 0.6 GB) lets the app find the CV lines that
are closest in *meaning* to each job requirement. It runs on the CPU, so your GPU stays free
for Qwen, and uses about 1 GB of RAM.

1. Open `llama_embed_server_windows.bat` in Notepad and check `LLAMA_SERVER` (your
   llama-server.exe). If `MODEL_PATH` doesn't exist, the first start downloads the model.
2. Double-click it and leave the window open; it serves on `http://127.0.0.1:8081`.
3. Start the app as usual. In the sidebar, under **Semantic search**, click **Check embeddings**.

What it adds:

- **A second look at gaps.** For each unproven requirement, the most similar CV lines go back to the
  model once more, so evidence the first pass missed can be found. A requirement is upgraded only when
  the model confirms it *and* the quote is found in your CV.
- **"Closest lines in your CV"** appear under each gap as a reminder when you add true facts.
- **Long CVs:** only the most relevant lines are sent, which means fewer model calls.

Similarity is never counted as evidence: "related" is not "proven". If the embedding server isn't
running, the app skips this step and works as before.

## Input tips

- DOCX, text-based PDF, TXT and MD are supported, and so is pasting. Scanned PDFs need OCR first.
  - Two-column PDFs such as LinkedIn "Save to PDF" profiles are read column by column.
  - Word CVs: list bullets (with nesting), bold section headings, tables and text boxes are read.
  - Classic CVs with "Title | Employer | City" lines, dates on the next line, and "o" / "▪" sub-bullets
    are understood. The fact checks then ensure no employer, job title or date is changed or dropped.
- Clear section headings in your CV (EXPERIENCE, EDUCATION, SKILLS, PUBLICATIONS…) give the best
  results: each section is rewritten and checked on its own, and factual sections are kept verbatim.
  A CV with no recognisable headings is rewritten as one document.
- **Extra true facts** can be saved to `my_fact_library.md` (a local file) and reused for every job.

## Files

```
app.py            Streamlit UI and workflow
engine.py         analysis, scoring, checks, résumé loop, cover-letter loop
llm_client.py     llama-server client + optional OpenRouter client (pacing, retries, redaction)
textutils.py      file reading, section detection, chunking, phrase/number/term matching
exporters.py      Word, offline PDF (Word COM or LibreOffice), TXT, ZIP, local save
embeddings.py     optional local embedding client and semantic retrieval
start_windows.bat launcher (reuses running llama-server + Anaconda/.venv)
tests/            offline tests with a scripted fake model and a fake HTTP llama-server
```

## Tests

```
python -m unittest discover -s tests -v
```

These are 84 offline tests. The model is **mocked**: a scripted fake model, plus fake
llama-server and OpenRouter servers over real HTTP for the clients. They cover JD quote validation, long-JD and
long-CV chunking (evidence found past the old 9,500-character limit), confirmed facts, malformed
JSON retries, `<think>` stripping, the context-overflow message, invented numbers/tools/contacts,
unsupported JD terms, dropped dates and roles, retrying with feedback, verbatim publications, the
Skills patch (supported terms only, never partial), scoring maths, the cover-letter checks,
DOCX/TXT/ZIP export, and a PDF text-order check (runs only when LibreOffice is installed).

**Not yet verified:** a live run against your Qwen3.6-35B-A3B model or a real OpenRouter model, and the Word-COM PDF
path on your PC. Do your first run with `samples/` (fictional), then with your real CV.
Read every claim before you send an application.
