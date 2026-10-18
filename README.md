# Data Analyst Agent

Ask questions about a CSV, Excel, Parquet or JSON file in plain language. A local LLM writes
Python, runs it against your data in a sandbox, reads the real output, fixes its own errors,
and answers with the evidence and code behind every number.

```
question ─▶ LLM writes code ─▶ sandbox executes ─▶ output/error fed back ─▶ … ─▶ Final Answer
                                                                                   │
                                     numeric grounding audit ◀─────────────────────┘
```

The app has three pages:

- **📊 Data analysis:** upload a dataset and ask questions. The agent writes and runs code to
  answer them.
- **📁 Workspace:** point the assistant at a local folder. It searches, reads and explains the
  files, citing file and line for everything it says. In **Edit** mode it proposes changes, which
  you review and apply.
- **🔬 Patent landscape:** upload a patent export, pick a standard objective (lightweighting,
  rolling resistance, NVH, ...) and/or paste your analysis brief. You get a full
  landscape report (trends, players, technology clusters, a semantic patent map, white space,
  crown jewels) and an enriched CSV in which the model has read every patent.

## Features

- **Agent loop with self-correction.** It runs up to *N* steps of code, observes the results and
  writes a final answer. Errors come back to the model with targeted hints (for example, the
  available columns after a `KeyError`). Duplicate or truncated code is caught, and the agent is
  made to answer when it reaches the step limit.
- **Grounded answers.**
  - The model sees a factual profile of the data: types, missing values, distributions, sample
    rows, and hints such as "numbers stored as text".
  - Each number in the answer is checked against the executed output. The UI shows which
    numbers could not be traced.
  - Answers that no executed code supports are flagged.
- **Sandboxed execution** (see [Security model](#security-model)): a static AST policy, plus a
  separate process with restricted builtins, an import allowlist, CPU, memory and time limits,
  and JSON-only results.
- **Full outputs.** Plotly and Matplotlib charts, tables (with CSV download), values and Markdown.
  Anything passed to `show()` goes into the report, and the full trace of every step is one click
  away.
- **Reproducibility.**
  - A fixed random seed and single-threaded BLAS in the sandbox.
  - The dataset's SHA-256 is recorded.
  - Three exports:
    - a **Jupyter notebook** that replays every successful step against the same file;
    - a **JSON transcript** with every step, the prompt, the settings and package versions;
    - a **Markdown report**.
- **Robust loading.**
  - Detects encoding (UTF-8, CP1252, Latin-1) and delimiter (`,` `;` tab `|`).
  - Lets you pick the Excel sheet.
  - Cleans up empty or duplicate headers and drops fully empty rows.
- **Conversation memory.** Follow-up questions see earlier answers and the code that produced them.

## Background runs and status bar

Each question runs as a background job, not inside the page.

- **You can switch pages while it works.** A status bar at the top of every page shows the
  question, the current activity, the step (out of the maximum) and the elapsed time, with a
  **Stop** button. Stop takes effect after the current step; a model reply already being
  generated finishes first.
- **When a job finishes on the other page,** a notification tells you, and the answer is waiting
  in that page's conversation.
- **Datasets, open folders, conversations and settings** (steps, mode, folder path) are kept when
  you switch pages.
- **Limit:** jobs belong to the browser session, so refreshing the browser tab starts a new
  session and loses runs in progress.

## Workspace assistant

Enter a folder path on the **Workspace** page and press **Open**. You can then ask things like
"what is in this folder?", "explain `fit.py` step by step" or "which report mentions slip
stiffness?".

- **Tools, not guesses.** The model works through `list_files`, `search` and `read_file`, plus
  optional sandboxed Python that reads files with `read_text()` and `load_table()`. It reads only
  what it needs, one step at a time.
- **Many file types.** It reads code and text, Word (`.docx`), PowerPoint (`.pptx`), PDF (with
  `pypdf`), Jupyter notebooks, and Excel/CSV.
- **Large files.**
  - Files are never loaded whole. The assistant searches, reads 250-line pieces, or computes with
    Python.
  - Spreadsheets appear as a data profile followed by one line per row, labelled `[Sheet r1844]`
    with Excel's row numbers. Search finds text in any cell, which is useful for patent extracts.
  - Size limits:
    - Word/PowerPoint: limited by their text, not file size, so image-heavy decks are fine.
    - PDF: up to 500 MB and 3,000 pages.
    - Spreadsheets: up to 150 MB, with a 200,000-row view (use Python for the rest).
    - Other text files: up to 50 MB.
  - Scanned PDFs without a text layer would need OCR, which isn't included.
- **Checked citations.**
  - Every `path:line` reference in an answer is checked. It is shown as verified if the assistant
    opened those lines, flagged if it cited lines it never opened, and marked invalid if the file
    or lines don't exist.
  - The cited lines are shown under **Sources**.
- **Three modes.**
  - **Answer:** concise answers.
  - **Teach me:** big picture first, then details, with jargon explained, examples quoted from
    your files, and questions to check your understanding.
  - **Edit:** proposes changes for your approval (see below).
- **Safety.**
  - The assistant never writes. Only your **Apply** click writes a file, and nothing is ever
    deleted or renamed.
  - Paths are confined to the folder you opened, including through symlinks.
  - Credentials files (`.env`, private keys, `*secret*`) and folders such as `.git` and
    `node_modules` are never read.
  - The page warns you if the model server isn't local, because file contents would leave your
    machine.
- **Long sessions.** Old tool output is trimmed automatically to fit the model's context window.

Set `WORKSPACE_DIR` to pre-fill the folder path.

### Edit mode: proposals, approval and undo

1. **The assistant proposes.** It edits with search/replace blocks (`<<<<<<< OLD … ======= …
   >>>>>>> NEW`), which small local models handle more reliably than JSON. It can also propose new
   files. Proposals are staged in memory, and its later reads show the proposed version so it can
   check its own work.
2. **Guards on proposals.**
   - It must read a file before editing it.
   - The OLD text must match exactly one place. If it doesn't, the assistant gets an explanation
     and the closest lines, and can retry.
   - Only plain-text files can be edited (code, Markdown, CSV, config), not Word, PDF, spreadsheets
     or notebooks.
   - Python, JSON and TOML files are syntax-checked, and problems are flagged.
3. **You review.** Each changed file appears as a diff with **Apply** and **Reject**.
4. **Applying.**
   - Apply refuses if the file changed on disk since the proposal.
   - It saves a backup of the original outside the folder (`~/.research_agent/backups`, or
     `AGENT_BACKUP_DIR`).
   - Writes are atomic and keep the file's encoding, BOM, line endings and permissions.
   - Every apply and undo is recorded in `journal.jsonl`, shown in the sidebar as **Change history**.
5. **Undo** restores the backup. It refuses if you've edited the file since the change was applied.

## Patent landscape

A professional patent landscape from your own database export, on any topic (uneven wear,
lightweighting, rolling resistance, durability, NVH, smart tyres, ...), run entirely on your
machine, or with a cloud model through OpenRouter (see *Cloud models*).

### How it works

1. **Upload the export** (Excel or CSV) from Derwent, PatSnap, Orbit, Lens, Espacenet or any
   other database. Columns are recognised automatically (number, title, abstract, claims,
   assignee, priority/filing/publication dates, CPC/IPC, family ID, citations, and AI summaries
   such as "Problem being solved"); you can correct the mapping.
2. **Choose a standard objective, paste your brief, or both.**
   - **Standard objective** (optional): Lightweighting, Low rolling resistance, Durability,
     Uneven wear, Smart / connected tyres, NVH, Wet / dry traction, Dynamic growth, High
     performance or Sustainable materials. **📋 Use this objective's plan** fills a complete
     plan at once, without the model: problem categories, technology groups, complexity
     levels, scope, extra CSV columns (e.g. *Weight saving claimed*, *Mass-reduction route*),
     the objective's own questions and suggested search terms for your database.
   - **Your brief**: paste it and press **Prepare analysis plan**. The model turns it into an
     editable plan: title, objective, segments, period, the problem dimension (e.g. *Wear type*)
     with its categories, technology groups, complexity levels, scope, competitors, regions,
     extra CSV columns, the report's section headings and the brief's own questions. Things the
     brief asks for that cannot be done offline (web search, image analysis) are listed.
   - **Both**: with an objective chosen the button reads **Adapt the objective's plan to my
     brief**. The model keeps the objective's categories and adds or changes only what your
     brief asks for (segments, period, competitors, more questions, ...).
3. **Review the plan**, edit anything (categories, extra columns, the brief-specific questions),
   and press **▶ Save & run analysis**. A
   trial run on 50–100 patents is a good first check. The job runs in the background with a
   progress bar and time estimate; you can switch pages or stop it.
4. **Read the report** on one long page, and download the results.
5. **Ask for more** in the box under the report (see *Follow-ups*).

### What the analysis does

| Step | Method |
|---|---|
| Cleaning | Family de-duplication (or by publication number), the plan's period, year basis priority > filing > publication, assignee names normalised (spellings, legal forms, subsidiaries) |
| Patent map | Embeddings from **PatentSBERTa** (or TF-IDF + LSA without it), 2-D **UMAP** map, topic clusters by **HDBSCAN** on a 5-D UMAP projection, cluster keywords by c-TF-IDF, cluster names and technology group by the model (seed 42) |
| Reading every patent | The model reads title, abstract, the export's summaries and the first claim of every patent, in batches sized to its context window, and fills the study's columns (below). Answers are validated against the plan's categories and cached on disk |
| Off-topic screening | Patents rated *Relevance: Low* against the plan's scope leave the analytics (they stay in the CSV) |
| Analytics | Computed, not written by the model: filing trend with the publication-lag zone and CAGR on complete years; technology roadmap and emerging/declining clusters; top assignees, HHI, assignee × technology; offices; segments; problem × solution heatmaps; complexity ranking; white space; competitor watch and crown jewels; process and manufacturing insights |
| Brief-specific analyses | Charts for your extra columns (values with a unit as box plots by technology group, short labels as stacked bars), and each of the plan's questions answered by the data agent with Python code on the enriched table; the answers also feed the report text |
| Report text | The model writes each of your brief's sections from the computed facts only; every number in its text is checked against them and unmatched ones are flagged |

The report follows your brief's section headings in order (e.g. 📊 Executive Summary · 📈 Data
Visualizations · 🧠 Technology Clusters & Solutions · 🏭 Process & Manufacturing Insights ·
🔮 Research Gaps & Future Trends); results your brief has no heading for get one of their own.
Before the end comes **🎯 Brief-specific analyses**: the charts for your extra columns, then
every question in the plan's *Brief-specific questions* box with its answer, its charts or
tables, a note on whether each number traces back to the code output, and the code itself
(fold-out). Answering takes a few model requests per question; switch it off under **Advanced
settings** if you only need the standard landscape. It ends with **Methodology and limitations**: data provenance (file, SHA-256, date), the record
flow from export to analysed set, every method and parameter used, definitions, merged assignee
names and limitations.

### The enriched CSV

One row per analysed patent: identifiers (ID, title, year, normalised assignee, co-assignees,
office), then the study's columns, then the topic cluster and map position, then **every
column of your original export** except full-text columns of over 10,000 characters per patent
(such as a description), which would only inflate the file. UTF-8 with BOM, so Excel opens it directly.

| Column | Content |
|---|---|
| Topic | The specific technical topic, in a few words |
| Segment | One or more of the plan's segments (e.g. TBR; PCR) |
| *(problem dimension)* | E.g. *Wear type*: one or more of the plan's categories, or Other |
| Technology group | One of the plan's groups (e.g. Structural/Design ... Active/Electronic) |
| Complexity | One of the plan's levels, simple to complex |
| Component | The part, material or system changed |
| Performance target | What it improves |
| Design lever | What is changed to get there |
| Mechanism | How it works |
| Core claim | The main independent claim in one plain sentence |
| Key parameters | Claimed numbers and ranges |
| Trade-offs | What it balances or sacrifices |
| Relevance · Confidence | High/Partial/Low against the scope; how sure the model is |
| *(extra columns)* | Whatever the plan adds for the topic (e.g. *Wear cause*, *Axle position*) |
| Extraction | Whether the row was read now, came from the cache, or failed (with the reason) |

Downloads, at the top and the end of the report: the **enriched CSV**, a **validation sample**
(30 random patents with the model's answers and empty *Correct?*/*Comment* columns, so an
expert can measure the extraction accuracy) and the **full report as one HTML file** (charts
stay interactive, works offline, prints to PDF).

### Follow-ups

Type a request in the box under the report; the outcome is added below it.

- **Questions and extra analyses** ("yearly trend of sensor patents in TBR only", "compare
  Michelin and Bridgestone by wear type") run the sandboxed data agent on the enriched table.
  It knows the study, the allowed values of every column and the report's key results, and
  answers with charts, tables and the code behind them.
- **Plan changes** ("only 2018–2024 and add Continental as a competitor") are shown as a list of
  changes; **Apply and run again** re-runs the analysis. Patents the model has already read
  come from the cache wherever the questions stay the same.
- **Text changes** ("make the executive summary shorter") rewrite those sections from the same
  facts, with the same number checks.

### Setup and tips

- `python -m pip install -r requirements.txt` installs scikit-learn and umap-learn. Without them
  the report skips the map and clusters and says so.
- For PatentSBERTa embeddings (as in a typical Colab workflow), also run
  `python -m pip install sentence-transformers`; the model (about 0.5 GB) downloads once and its
  embeddings are cached. Without it the map uses TF-IDF + LSA. Choose the model, or `tfidf`,
  under **Advanced settings** in the plan.
- Cache: `~/.research_agent/patent_cache` (set `PATENT_CACHE_DIR` to move it). A stopped or
  crashed run continues where it left off; delete the folder to start fresh.
- With a 16k context the model reads 6 patents per request; a larger context does not make
  requests bigger. Structured requests (plan, cluster names, reading patents, report text) ask the
  server to skip the model's "thinking" (`chat_template_kwargs: enable_thinking=false`, honoured by
  llama-server `--jinja` with Qwen3-style templates). If a model thinks anyway and runs out of
  reply tokens, the request is retried with more room and smaller batches, and the reason is
  recorded per patent. Starting llama-server with `--reasoning-budget 0` turns thinking off for
  every page. The data agent (brief-specific questions and follow-ups) also runs without thinking.
- The analysis covers exactly what your export contains: the search strategy behind it defines
  the landscape. It is a technology landscape, not a legal (validity or FTO) opinion.

## Cloud models (OpenRouter)

Instead of a model on your PC you can use a model hosted on [OpenRouter](https://openrouter.ai),
for example the free `nvidia/nemotron-3.5-super-vl:free`.

1. Create an account and an API key on openrouter.ai (*Keys*).
2. In the sidebar, open **🧠 Model server**, choose **OpenRouter (cloud API)**, paste the key and
   press **Check connection**. It checks the key and the model id, reads the model's context
   window, and for free models shows your daily request limit; for paid models, the price.
   Switching back to **Local model server** restores your local settings.
3. To start with the cloud every time, put these lines in `my_settings.bat` (the launcher then
   starts no model server):
   ```bat
   set "MODEL=cloud"
   set "OPENROUTER_API_KEY=sk-or-..."
   set "OPENROUTER_MODEL=nvidia/nemotron-3.5-super-vl:free"
   ```
   The key is stored there in plain text, on your PC only.

Things to know:

- **Your data leaves the PC.** Everything the app sends to the model goes to OpenRouter and the
  model's provider: patent texts, your brief, plan and questions, data you analyse, files you
  ask about on the Workspace page. Free models usually log prompts; NVIDIA's free endpoint says
  not to send confidential information. Patent texts are public, but your brief and questions
  show what you are researching. The sidebar shows ☁️ while a cloud model is in use.
- **Free models are rate limited:** 20 requests a minute, and 50 a day, or 1,000 a day once the
  account has bought at least $10 of credits. A landscape of 2,000 patents needs about 400
  requests; the plan step shows the estimate for your run. The app keeps under the per-minute
  limit by itself. When the daily limit is reached, the run stops with a clear message; patents
  read so far are cached, and running it again after the reset (midnight UTC) continues from
  them. Paid model ids (without `:free`) have no such limits and cost little for this work.
- Thinking is switched off for the structured steps with OpenRouter's `reasoning` setting; a
  model whose reasoning cannot be switched off is asked again without it.
- The report's methodology records the model as "… via OpenRouter". Patent answers are cached
  per model, so local and cloud runs never mix.
- Check the extraction quality on your own patents with the validation sample before relying on
  a new model.

## Windows: one-click start

1. Double-click `start_agent.bat` once. It creates `my_settings.bat` next to it: your own
   settings, which updates of the app never overwrite. Open it in Notepad and add the lines you
   need from the **SETTINGS** block at the top of `start_agent.bat`, for example:
   ```bat
   set "MODEL=open"
   set "LLAMA_SERVER=D:\llama.cpp\llama-server.exe"
   set "CTX=16384"
   ```
2. Double-click `start_agent.bat`. It:
   - starts llama-server in its own window, or reuses it if it's already running;
   - waits until the model has loaded;
   - opens the app in your browser.
   Double-clicking it again just reopens the browser tab.
3. To stop, close the two windows, or double-click `stop_agent.bat`.

**Updating the app:** copy the new files over the old ones (keep your `my_settings.bat`), then
double-click `start_agent.bat`. If the old version is still running, the launcher notices that it
was started with older files (or from another folder) and restarts it with the new ones; it waits
10 seconds first, so you can close the window to keep a running analysis. You can tell the new
version by the **🔬 Patent landscape** page in the navigation.

**Using whichever model is loaded:** set `set "MODEL=open"` in `my_settings.bat`. The launcher then
starts no model server of its own. It waits until one answers on the port (start llama-server
yourself with any model) and then opens the app. The app always detects the loaded model's name
and context window from the server, at startup and on **Check connection**, so the sidebar and
exported transcripts record the model that actually answered.

Tip: right-click `start_agent.bat` → *Send to* → *Desktop (create shortcut)* for a desktop icon.
To find Python, the launcher uses the first one that has the app's packages: the `PYTHON`
setting, then `venv\` or `.venv\` next to it, then the Python that owns the `streamlit` command,
then `py`, then `python`. If none qualifies, it explains how to find the right path.

## Quick start

1. Start an OpenAI-compatible server, for example llama.cpp. Give it enough context for
   multi-step analyses:

   ```bash
   llama-server -m Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf --port 8080 -c 32768 --jinja
   ```

2. Install and run the app:

   ```bash
   pip install -r requirements.txt
   streamlit run app.py
   ```

Upload a file in the sidebar and ask away. Use **Model server → Check connection** to verify the
LLM is reachable. It also reads the server's context window (`-c`) and sizes the app's memory budget
to match.

**Larger context.** Qwen3-30B-A3B-Instruct-2507 supports up to 256K tokens; the cost is memory.
For example, at 64K with a compressed cache:

```bash
llama-server -m Qwen3-30B-A3B-Instruct-2507-UD-Q4_K_XL.gguf --port 8080 --jinja \
  -c 65536 -np 1 -fa on -ctk q8_0 -ctv q8_0 -ngl 99 --cpu-moe
```

Then press **Check connection** in the app and the window is picked up automatically.

Any OpenAI-compatible endpoint works (llama.cpp, vLLM, Ollama's `/v1`, LM Studio, hosted APIs).

## Configuration

You can change any setting in the sidebar. Defaults come from environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:8080` | Server root (a trailing `/v1` is fine) |
| `LLM_MODEL` | `Qwen3-30B-A3B-Instruct` | Model name sent to the server |
| `LLM_API_KEY` | *(empty)* | Bearer token, if the server needs one |
| `OPENROUTER_API_KEY` | *(empty)* | OpenRouter API key, pre-filled in the sidebar |
| `OPENROUTER_MODEL` | `nvidia/nemotron-3.5-super-vl:free` | Model id used when you switch to OpenRouter |
| `LLM_TEMPERATURE` | `0.2` | Sampling temperature |
| `LLM_MAX_TOKENS` | `2048` | Max tokens per model reply |
| `LLM_TIMEOUT` | `180` | Seconds to wait for a reply |
| `LLM_CONTEXT_TOKENS` | `32768` | Server context window; **Check connection** detects it from llama-server |
| `AGENT_MAX_STEPS` | `6` | Code executions allowed per question (data page) |
| `AGENT_MAX_CONTEXT_CHARS` | *(derived)* | Overrides the conversation budget, which is otherwise derived from the context window |
| `WORKSPACE_DIR` | *(empty)* | Default folder on the Workspace page |
| `AGENT_BACKUP_DIR` | `~/.research_agent/backups` | Where backups and the change journal are kept |
| `SANDBOX_TIMEOUT` | `60` | Seconds per code execution |
| `SANDBOX_MEMORY_MB` | `4096` | Memory cap per execution (Linux/macOS) |
| `PATENT_EMBEDDING_MODEL` | `AI-Growth-Lab/PatentSBERTa` | Embedding model for the patent map (`tfidf` to skip it) |
| `PATENT_CACHE_DIR` | `~/.research_agent/patent_cache` | Cached embeddings and per-patent answers |

## Architecture

| Module | Responsibility |
|---|---|
| `app.py` | Streamlit entry point: page navigation, shared model settings, status bar |
| `ui/jobs.py` | Background jobs: questions that survive page switches, the status bar, Stop |
| `ui/data_page.py` | Data analysis page: upload, chat, live progress, trace, data tab, exports |
| `ui/workspace_page.py` | Workspace page: open a folder, chat, sources, file browser |
| `ui/patent_page.py` | Patent landscape page: upload, brief, plan, report, downloads, follow-ups |
| `analyst_agent/patents/data.py` | Export columns, cleaning, de-duplication, assignee names, record flow |
| `analyst_agent/patents/plan.py` | Brief to analysis plan (model, with a rule-based fallback) |
| `analyst_agent/patents/presets.py` | Standard objectives (lightweighting, rolling resistance, ...) with complete plans |
| `analyst_agent/patents/semantic.py` | Embeddings, UMAP map, HDBSCAN clusters, c-TF-IDF keywords, cluster names |
| `analyst_agent/patents/enrich.py` | The model reads every patent: batching, validation, retries, cache |
| `analyst_agent/patents/analytics.py` | Trends, players, heatmaps, complexity, white space, crown jewels |
| `analyst_agent/patents/narrative.py` | Report sections, number-checked text, methodology, validation sample |
| `analyst_agent/patents/html_report.py` | The report as one offline HTML file |
| `analyst_agent/patents/followup.py` | Follow-ups: routing, plan changes, rewrites, data-agent context |
| `analyst_agent/patents/pipeline.py` | Runs the stages, answers the brief's questions, assembles the report and the enriched table |
| `analyst_agent/workspace.py` | Read-only, root-bounded file access and text extraction |
| `analyst_agent/workspace_agent.py` | Tool-using loop for the workspace assistant, plus citation checks |
| `analyst_agent/editing.py` | Staged changes, diffs, apply with backup, undo, journal |
| `analyst_agent/agent.py` | The reasoning loop, observation formatting, conversation memory |
| `analyst_agent/prompts.py` | System prompt (protocol and analysis standards) and nudges |
| `analyst_agent/parsing.py` | Pulls code and final answers out of free-form replies (fences, `<think>`, truncation) |
| `analyst_agent/sandbox.py` | Code policy (AST) and the subprocess runner |
| `analyst_agent/_worker.py` | Standalone worker that runs inside the sandbox process |
| `analyst_agent/data.py` | Loading, cleaning and profiling of datasets |
| `analyst_agent/grounding.py` | Numeric grounding audit of answers |
| `analyst_agent/llm.py` | OpenAI-compatible client (llama-server, OpenRouter, ...): retries, rate limits, clear errors |
| `analyst_agent/export.py` | Notebook, JSON and Markdown exports |

The `analyst_agent` package doesn't depend on Streamlit, so you can use it from scripts or other UIs:

```python
from analyst_agent import DataAnalystAgent, LLMClient, Sandbox, load_dataset

dataset = load_dataset(open("sales.csv", "rb").read(), "sales.csv")
with Sandbox() as sandbox:
    run = DataAnalystAgent(LLMClient(), sandbox, dataset).run("Which region grew fastest?")
print(run.answer, run.grounding.untraced)
```

## Security model

Generated code is checked in three layers before and while it runs:

1. **Static policy.** It blocks:
   - imports outside an allowlist (pandas, numpy, plotly, scipy, statsmodels, sklearn,
     matplotlib, seaborn and stdlib maths/text modules);
   - dunder access;
   - `eval`, `exec`, `open`, `getattr` and similar;
   - file, network and clipboard I/O methods (`to_csv`, `read_*` and so on).
2. **Process isolation.**
   - A fresh interpreter for every execution, with restricted builtins and a guarded `__import__`.
   - CPU, memory and wall-clock limits.
   - A scrubbed environment: no API keys or tokens.
   - A temporary working directory.
3. **Data boundary.** Results come back as JSON and are never unpickled.

This stops model mistakes and casual misuse. It is not a hardened boundary against a determined
attacker. For untrusted multi-user deployments, also run the app in a container or VM without
network access.

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

The tests cover parsing, the sandbox policy and limits, data loading, the agent loop (with a
scripted LLM), grounding, the LLM client and the exports.
