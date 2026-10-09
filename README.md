# Data Analyst Agent

Ask questions about a CSV, Excel, Parquet or JSON file in plain language. A local LLM writes
Python, runs it against your data in a sandbox, reads the real output, fixes its own errors,
and answers with the evidence and code behind every number.

```
question ─▶ LLM writes code ─▶ sandbox executes ─▶ output/error fed back ─▶ … ─▶ Final Answer
                                                                                   │
                                     numeric grounding audit ◀─────────────────────┘
```

The app has two pages:

- **📊 Data analysis:** upload a dataset and ask questions. The agent writes and runs code to
  answer them.
- **📁 Workspace:** point the assistant at a local folder. It searches, reads and explains the
  files, citing file and line for everything it says. In **Edit** mode it proposes changes, which
  you review and apply.

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
LLM is reachable.

Any OpenAI-compatible endpoint works (llama.cpp, vLLM, Ollama's `/v1`, LM Studio, hosted APIs).

## Configuration

You can change any setting in the sidebar. Defaults come from environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:8080` | Server root (a trailing `/v1` is fine) |
| `LLM_MODEL` | `Qwen3-30B-A3B-Instruct` | Model name sent to the server |
| `LLM_API_KEY` | *(empty)* | Bearer token, if the server needs one |
| `LLM_TEMPERATURE` | `0.2` | Sampling temperature |
| `LLM_MAX_TOKENS` | `2048` | Max tokens per model reply |
| `LLM_TIMEOUT` | `180` | Seconds to wait for a reply |
| `AGENT_MAX_STEPS` | `6` | Code executions allowed per question (data page) |
| `AGENT_MAX_CONTEXT_CHARS` | `90000` | Conversation size before old tool output is trimmed |
| `WORKSPACE_DIR` | *(empty)* | Default folder on the Workspace page |
| `AGENT_BACKUP_DIR` | `~/.research_agent/backups` | Where backups and the change journal are kept |
| `SANDBOX_TIMEOUT` | `60` | Seconds per code execution |
| `SANDBOX_MEMORY_MB` | `4096` | Memory cap per execution (Linux/macOS) |

## Architecture

| Module | Responsibility |
|---|---|
| `app.py` | Streamlit entry point: page navigation and shared model settings |
| `ui/data_page.py` | Data analysis page: upload, chat, live progress, trace, data tab, exports |
| `ui/workspace_page.py` | Workspace page: open a folder, chat, sources, file browser |
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
| `analyst_agent/llm.py` | OpenAI-compatible client with retries and clear errors |
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
