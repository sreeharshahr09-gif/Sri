import json

from analyst_agent.agent import AgentRun, DataAnalystAgent
from analyst_agent.config import AgentConfig
from analyst_agent.export import to_json, to_markdown, to_notebook
from analyst_agent.llm import LLMError

from .conftest import ScriptedLLM


def make_agent(replies, sandbox, dataset, **cfg):
    llm = ScriptedLLM(replies)
    return llm, DataAnalystAgent(llm, sandbox, dataset, config=AgentConfig(**cfg))


def test_code_then_answer(sandbox, dataset):
    llm, agent = make_agent(
        [
            "I'll total sales by region.\n```python\nt = df.groupby('region')['sales'].sum()\nshow(t)\nprint(t)\n```",
            "Final Answer: West leads with 450 in sales, ahead of East at 370.5.",
        ],
        sandbox,
        dataset,
    )
    events = []
    run = agent.run("Which region sells most?", on_event=lambda kind, payload: events.append(kind))

    assert run.status == "answered"
    assert run.answer.startswith("West leads")
    assert run.executed
    assert [s.code is not None for s in run.steps] == [True, False]
    assert run.grounding.checked == ["450", "370.5"] and run.grounding.fully_traced
    assert [a.kind for a in run.report_artifacts()] == ["table"]
    assert events == ["llm_call", "executing", "step", "llm_call", "done"]

    # The model saw the real execution output before answering.
    observation = llm.calls[1][-1]["content"]
    assert "Status: success" in observation and "450" in observation


def test_error_feedback_enables_self_correction(sandbox, dataset):
    llm, agent = make_agent(
        [
            "```python\nprint(df['Sales'].sum())\n```",
            "Wrong case.\n```python\nprint(df['sales'].sum())\n```",
            "Final Answer: Total sales are 900.5.",
        ],
        sandbox,
        dataset,
    )
    run = agent.run("Total sales?")
    assert run.steps[0].result.error_type == "KeyError"
    feedback = llm.calls[1][-1]["content"]
    assert "KeyError" in feedback and "available columns" in feedback and "'sales'" in feedback
    assert run.steps[1].succeeded
    assert run.status == "answered" and run.grounding.fully_traced


def test_policy_rejection_is_fed_back(sandbox, dataset):
    llm, agent = make_agent(
        [
            "```python\nimport os\nprint(os.listdir())\n```",
            "Final Answer: I cannot list files.",
            "Final Answer: File access is not available; the data is in df.",
        ],
        sandbox,
        dataset,
    )
    run = agent.run("List files")
    assert run.steps[0].result.rejected
    assert "REJECTED" in llm.calls[1][-1]["content"]
    assert not run.executed and run.status == "answered"


def test_repeated_code_is_not_rerun(sandbox, dataset):
    code = "```python\nprint(len(df))  # count\n```"
    llm, agent = make_agent([code, code.replace("  # count", ""), "Final Answer: 6 rows."], sandbox, dataset)
    run = agent.run("How many rows?")
    assert run.steps[1].note == "repeated" and run.steps[1].result is None
    assert "identical" in llm.calls[2][-1]["content"]


def test_step_limit_forces_final_answer(sandbox, dataset):
    loop = "```python\nprint(df.shape)\n```"
    llm, agent = make_agent(
        [loop, "```python\nprint(df.columns.tolist())\n```", "Final Answer: Partial: there are 6 rows."],
        sandbox,
        dataset,
        max_steps=2,
    )
    run = agent.run("Analyse everything")
    assert run.status == "step_limit"
    assert run.answer == "Partial: there are 6 rows."
    assert "step limit" in llm.calls[2][-1]["content"].lower()
    # Message roles must alternate for strict chat templates.
    roles = [m["role"] for m in llm.calls[2]]
    assert roles[0] == "system"
    assert all(a != b for a, b in zip(roles[1:], roles[2:]))


def test_forced_final_with_code_keeps_prose_only(sandbox, dataset):
    llm, agent = make_agent(
        ["```python\nprint(1)\n```", "Here is what I found.\n```python\nprint(2)\n```"],
        sandbox,
        dataset,
        max_steps=1,
    )
    run = agent.run("q")
    assert run.status == "step_limit" and run.answer == "Here is what I found."


def test_truncated_reply_triggers_retry(sandbox, dataset):
    llm, agent = make_agent(
        ["Checking\n```python\nprint(df.sh", "```python\nprint(df.shape)\n```", "Final Answer: 6 rows and 4 columns."],
        sandbox,
        dataset,
    )
    run = agent.run("shape?")
    assert run.steps[0].note == "truncated"
    assert "cut off" in llm.calls[1][-1]["content"]
    assert run.status == "answered"


def test_answer_without_code_is_sent_back_once(sandbox, dataset):
    # The model claims a result without running anything (seen with local models).
    llm, agent = make_agent(
        [
            "Final Answer: I created a box plot; the median is about 999.",
            "Let me compute it.\n```python\nprint(df['sales'].median())\n```",
            "Final Answer: The median sale is 110.",
        ],
        sandbox,
        dataset,
    )
    run = agent.run("What is the median sale?")
    assert run.steps[0].note == "premature answer"
    assert "not run any code" in llm.calls[1][-1]["content"]
    assert run.status == "answered" and run.executed
    assert run.answer == "The median sale is 110." and run.grounding.fully_traced


def test_code_free_question_still_answered_after_one_nudge(sandbox, dataset):
    _, agent = make_agent(["I can analyse your data.", "I answer questions about the uploaded data."], sandbox, dataset)
    run = agent.run("What can you do?")
    assert run.status == "answered" and not run.executed
    assert run.answer == "I answer questions about the uploaded data."
    assert [s.note for s in run.steps] == ["premature answer", None]


def test_requested_chart_is_enforced(sandbox, dataset):
    llm, agent = make_agent(
        [
            "```python\nprint(df.groupby('region')['sales'].median())\n```",
            "Final Answer: Here is the box plot of sales by region.",
            "```python\nshow(px.box(df, x='region', y='sales', title='Sales by region'))\n```",
            "Final Answer: West has the widest spread of sales.",
        ],
        sandbox,
        dataset,
    )
    run = agent.run("need a whisker plot of sales for each region")
    assert run.steps[1].note == "premature answer"
    assert "asked for a chart" in llm.calls[2][-1]["content"]
    assert [a.kind for a in run.report_artifacts()] == ["plotly"]


def test_figure_built_but_not_shown_is_still_captured(sandbox, dataset):
    _, agent = make_agent(
        ["```python\nfig_box = px.box(df, x='region', y='sales')\n```", "Final Answer: Box plot shown."],
        sandbox,
        dataset,
    )
    run = agent.run("box plot of sales by region")
    assert [a.kind for a in run.report_artifacts()] == ["plotly"]
    assert len(run.steps) == 2  # no chart nudge needed


def test_llm_error_is_captured(sandbox, dataset):
    class Broken:
        def chat(self, messages):
            raise LLMError("server down")

    agent = DataAnalystAgent(Broken(), sandbox, dataset)
    run = agent.run("anything")
    assert run.status == "llm_error" and "server down" in run.error and run.answer == ""


def test_history_gives_follow_up_context(sandbox, dataset):
    _, agent = make_agent(
        ["```python\nshow(df.groupby('region')['sales'].sum())\n```", "Final Answer: West is top with 450."],
        sandbox,
        dataset,
    )
    first = agent.run("Sales by region?")
    llm2, agent2 = make_agent(["```python\nprint(1)\n```", "Final Answer: ok"], sandbox, dataset)
    agent2.run("Now by month", history=[first])
    messages = llm2.calls[0]
    # History is context inside one user turn, never replayed as code-free assistant answers.
    assert [m["role"] for m in messages] == ["system", "user"]
    content = messages[1]["content"]
    assert "Q1: Sales by region?" in content and "West is top" in content
    assert "groupby('region')" in content and content.endswith("Current question: Now by month")


def test_unverified_history_is_labelled(sandbox, dataset):
    _, agent = make_agent(["Hi!", "Hello, ask me about the data."], sandbox, dataset)
    first = agent.run("hello")
    llm2, agent2 = make_agent(["```python\nprint(1)\n```", "Final Answer: ok"], sandbox, dataset)
    agent2.run("next", history=[first])
    assert "A1 [not verified by code]" in llm2.calls[0][1]["content"]


def test_run_serialization_and_exports(sandbox, dataset):
    _, agent = make_agent(
        ["```python\nfig = px.bar(df, x='region', y='sales')\nshow(df.head(2))\n```", "Final Answer: Done, 6 rows."],
        sandbox,
        dataset,
    )
    run = agent.run("Chart it")
    restored = AgentRun.from_dict(json.loads(json.dumps(run.to_dict())))
    assert restored.answer == run.answer
    assert [a.kind for a in restored.report_artifacts()] == ["plotly", "table"] or [
        a.kind for a in restored.report_artifacts()
    ] == ["table", "plotly"]

    nb = json.loads(to_notebook([run], dataset))
    sources = ["".join(c["source"]) for c in nb["cells"]]
    assert nb["nbformat"] == 4 and all("id" in c for c in nb["cells"])
    assert any("px.bar" in s and "df = fresh_df()" in s for s in sources)
    for src in sources:
        if src and nb["cells"][sources.index(src)]["cell_type"] == "code":
            compile(src, "<cell>", "exec")  # every code cell is valid Python

    record = json.loads(to_json([run], dataset, settings={"x": 1}, system_prompt=agent.system_prompt()))
    assert record["dataset"]["sha256"] == dataset.sha256 and record["runs"][0]["status"] == "answered"
    assert "pandas" in record["environment"]
    assert "## 1. Chart it" in to_markdown([run], dataset)


def test_sandbox_infrastructure_failure_is_contained(sandbox, dataset, monkeypatch):
    def boom(code):
        raise OSError("disk full")

    llm, agent = make_agent(
        ["```python\nprint(1)\n```", "Final Answer: could not run.", "Final Answer: execution is failing."],
        sandbox,
        dataset,
    )
    monkeypatch.setattr(agent.sandbox, "run", boom)
    run = agent.run("q")
    assert run.steps[0].result.error_type == "SandboxError" and "disk full" in run.steps[0].result.error_message
    assert run.status == "answered"


def test_unexpected_errors_keep_partial_trace(sandbox, dataset):
    llm, agent = make_agent(["```python\nprint(1)\n```"], sandbox, dataset)  # runs out of replies
    run = agent.run("q")
    assert run.status == "error" and "AssertionError" in run.error
    assert len(run.steps) == 1 and run.steps[0].succeeded
