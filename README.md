# Data Analyst Agent

Ask questions about a CSV, Excel, Parquet or JSON file in plain language. A local LLM writes
Python, runs it against your data in a sandbox, reads the real output, fixes its own errors,
and answers with the evidence and code behind every number.

```
question ─▶ LLM writes code ─▶ sandbox executes ─▶ output/error fed back ─▶ … ─▶ Final Answer
                                                                                   │
                                     numeric grounding audit ◀─────────────────────┘
```

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
| `AGENT_MAX_STEPS` | `6` | Code executions allowed per question |
| `SANDBOX_TIMEOUT` | `60` | Seconds per code execution |
| `SANDBOX_MEMORY_MB` | `4096` | Memory cap per execution (Linux/macOS) |

## Architecture

| Module | Responsibility |
|---|---|
| `app.py` | Streamlit UI: upload, chat, live progress, trace, data tab, exports |
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
