import json

import pytest

from analyst_agent.agent import AgentRun
from analyst_agent.config import AgentConfig, SandboxConfig
from analyst_agent.parsing import parse_action
from analyst_agent.sandbox import Sandbox
from analyst_agent.workspace import Workspace
from analyst_agent.workspace_agent import WorkspaceAgent

from .conftest import ScriptedLLM
from .test_workspace import ws, ws_dir  # noqa: F401  (fixtures)


def tool(name, **args):
    return f"Checking.\n```tool\n{json.dumps({'tool': name, **args})}\n```"


def make(replies, workspace, sandbox=None, mode="answer", **cfg):
    llm = ScriptedLLM(replies)
    config = AgentConfig(max_steps=cfg.pop("max_steps", 8), **cfg)
    return llm, WorkspaceAgent(llm, workspace, sandbox=sandbox, config=config, mode=mode)


# ----------------------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    "text,name,args",
    [
        ('```tool\n{"tool": "read_file", "path": "a.py"}\n```', "read_file", {"path": "a.py"}),
        ('<tool_call>\n{"name": "search", "arguments": {"query": "mu"}}\n</tool_call>', "search", {"query": "mu"}),
        ("```tool\n{'tool': 'list_files', 'depth': 3,}\n```", "list_files", {"depth": 3}),
        ('```\n{"tool": "list_files"}\n```', "list_files", {}),
        ('```json\n{"name": "read_file", "arguments": "{\\"path\\": \\"b.md\\"}"}\n```', "read_file", {"path": "b.md"}),
    ],
)
def test_parse_tool_call_formats(text, name, args):
    action = parse_action(text)
    assert (action.tool, action.args) == (name, args)


def test_parse_reports_malformed_tool_call():
    action = parse_action("```tool\n{not json\n```")
    assert action.tool is None and "Could not parse" in action.error


def test_parse_first_action_wins_and_answers_after_actions_are_ignored():
    action = parse_action('```tool\n{"tool": "list_files"}\n```\nFinal Answer: done')
    assert action.tool == "list_files" and action.final_answer is None


# ----------------------------------------------------------------------------- agent


def test_search_read_answer_with_verified_citations(ws):  # noqa: F811
    llm, agent = make(
        [
            tool("search", query="def magic_formula"),
            tool("read_file", path="src/fit.py", start=1, end=12),
            "Final Answer: `magic_formula` computes Fx from slip with Pacejka's B, C, D, E "
            "(`src/fit.py:5-8`). `peak_mu` divides the largest |Fx| by Fz (src/fit.py:11-12).",
        ],
        ws,
    )
    events = []
    run = agent.run("What does fit.py do?", on_event=lambda kind, _: events.append(kind))
    assert run.status == "answered" and run.mode == "answer"
    assert [s.tool for s in run.steps] == ["search", "read_file", None]
    assert "src/fit.py:5:" in run.steps[0].observation
    assert " 5| def magic_formula" in llm.calls[2][-1]["content"]
    assert [(c.label, c.status) for c in run.citations.citations] == [
        ("src/fit.py:5-8", "verified"),
        ("src/fit.py:11-12", "verified"),
    ]
    assert events.count("tool") == 2 and events[-1] == "done"


def test_citations_to_unopened_or_missing_lines_are_flagged(ws):  # noqa: F811
    _, agent = make(
        [tool("read_file", path="README.md"), "Final Answer: See README.md:1, src/fit.py:11 and src/ghost.py:3."],
        ws,
    )
    run = agent.run("q")
    status = {c.label: c.status for c in run.citations.citations}
    assert status == {"README.md:1": "verified", "src/fit.py:11": "unseen", "src/ghost.py:3": "invalid"}


def test_answer_without_looking_is_sent_back_once(ws):  # noqa: F811
    llm, agent = make(["Final Answer: It fits tyre curves.", tool("list_files"), "Final Answer: ok"], ws)
    run = agent.run("What is this project?")
    assert run.steps[0].note == "premature answer"
    assert "not looked at any files" in llm.calls[1][-1]["content"]
    assert run.status == "answered" and run.executed


def test_tool_errors_are_reported_and_recoverable(ws):  # noqa: F811
    llm, agent = make(
        [
            tool("read_file", path="../../etc/passwd"),
            tool("read_file", path=".env"),
            tool("delete_file", path="README.md"),
            "```tool\n{broken\n```",
            tool("read_file", path="README.md"),
            "Final Answer: done (README.md:1)",
        ],
        ws,
    )
    run = agent.run("q")
    assert [s.tool_error for s in run.steps[:3]] == [True, True, True]
    assert "outside the workspace" in run.steps[0].observation
    assert "credentials" in run.steps[1].observation
    assert "unknown tool 'delete_file'" in run.steps[2].observation
    assert run.steps[3].note == "invalid tool call"
    assert run.status == "answered" and run.citations.citations[0].status == "verified"


def test_repeated_tool_call_is_not_rerun(ws):  # noqa: F811
    call = tool("list_files", depth=1)
    llm, agent = make([call, call, "Final Answer: ok"], ws)
    run = agent.run("q")
    assert run.steps[1].note == "repeated" and run.steps[1].observation is None


def test_step_limit_forces_answer(ws):  # noqa: F811
    llm, agent = make(
        [tool("list_files"), tool("read_file", path="README.md"), "Final Answer: partial"], ws, max_steps=2
    )
    run = agent.run("q")
    assert run.status == "step_limit" and run.answer == "partial"
    assert "step limit" in llm.calls[2][-1]["content"].lower()


def test_python_reads_workspace_data_read_only(ws, ws_dir):  # noqa: F811
    with Sandbox(SandboxConfig(memory_limit_mb=0), workspace_root=str(ws_dir)) as sb:
        _, agent = make(
            [
                "```python\nt = load_table('data/tyres.csv')\nshow(t.groupby('size')['mu'].mean())\n```",
                "Final Answer: Size A averages 1.09.",
            ],
            ws,
            sandbox=sb,
        )
        run = agent.run("Average mu per size?")
    assert run.steps[0].succeeded
    assert [a.kind for a in run.report_artifacts()] == ["table"]


def test_python_disabled_without_sandbox(ws):  # noqa: F811
    llm, agent = make(["```python\nprint(1)\n```", tool("list_files"), "Final Answer: ok"], ws)
    run = agent.run("q")
    assert run.steps[0].note == "python unavailable"
    assert "```python block to compute" not in agent.system_prompt()


def test_teach_mode_changes_instructions(ws):  # noqa: F811
    _, teach = make([], ws, mode="teach")
    _, answer = make([], ws, mode="answer")
    assert "teaching" in teach.system_prompt() and "check their understanding" in teach.system_prompt()
    assert "teaching" not in answer.system_prompt()
    with pytest.raises(ValueError):
        make([], ws, mode="delete")


def test_system_prompt_contains_overview(ws):  # noqa: F811
    _, agent = make([], ws)
    prompt = agent.system_prompt()
    assert "fit.py" in prompt and "<<" not in prompt and ".env" not in prompt


def test_old_tool_outputs_are_shortened_when_context_is_full(ws, ws_dir):  # noqa: F811
    (ws_dir / "big.txt").write_text("\n".join("x" * 50 for _ in range(240)))
    llm, agent = make(
        [
            tool("read_file", path="big.txt"),
            tool("read_file", path="src/fit.py"),
            tool("read_file", path="README.md"),
            "Final Answer: ok",
        ],
        ws,
        max_context_chars=len(agent_prompt(ws)) + 6000,
    )
    agent.run("q")
    last_call = llm.calls[-1]
    first_observation = last_call[3]["content"]
    assert "older tool output removed" in first_observation and len(first_observation) < 600
    assert "README.md" in last_call[-1]["content"]  # the latest result is intact


def agent_prompt(ws):  # noqa: F811
    return WorkspaceAgent(ScriptedLLM([]), ws).system_prompt()


def test_follow_up_questions_get_context(ws):  # noqa: F811
    _, agent = make([tool("read_file", path="README.md"), "Final Answer: A tyre study (README.md:1)."], ws)
    first = agent.run("What is this?")
    llm2, agent2 = make([tool("list_files"), "Final Answer: ok"], ws)
    agent2.run("Which files hold data?", history=[first])
    content = llm2.calls[0][1]["content"]
    assert "Q1: What is this?" in content and "Files read for A1: README.md" in content
    assert content.endswith("Current question: Which files hold data?")


def test_run_round_trips_through_json(ws):  # noqa: F811
    _, agent = make([tool("read_file", path="README.md"), "Final Answer: x (README.md:1)"], ws)
    run = agent.run("q")
    restored = AgentRun.from_dict(json.loads(json.dumps(run.to_dict())))
    assert restored.steps[0].tool == "read_file" and restored.citations.citations[0].status == "verified"


def test_missing_folder_is_rejected(tmp_path):
    from analyst_agent.workspace import WorkspaceError

    with pytest.raises(WorkspaceError, match="not found"):
        WorkspaceAgent(ScriptedLLM([]), Workspace(tmp_path / "missing"))


# ----------------------------------------------------------------------------- edit mode


def edit_block(path, old, new):
    return f"Updating {path}.\n```edit\npath: {path}\n<<<<<<< OLD\n{old}\n=======\n{new}\n>>>>>>> NEW\n```"


def test_edit_mode_proposes_without_writing(ws, ws_dir):  # noqa: F811
    before = (ws_dir / "src" / "fit.py").read_text()
    llm, agent = make(
        [
            tool("read_file", path="src/fit.py"),
            edit_block("src/fit.py", "    x = B * slip", "    x = B * np.asarray(slip)  # accept lists"),
            tool("read_file", path="src/fit.py", start=5, end=8),
            "Final Answer: Proposed converting slip to an array (src/fit.py:7).",
        ],
        ws,
        mode="edit",
    )
    run = agent.run("Make magic_formula accept lists")
    assert (ws_dir / "src" / "fit.py").read_text() == before  # nothing written
    assert [c.path for c in run.changes] == ["src/fit.py"] and run.changes[0].status == "pending"
    assert "staged (proposal only" in run.steps[1].observation and "+    x = B * np.asarray" in run.steps[1].observation
    assert "np.asarray(slip)" in llm.calls[3][-1]["content"]  # the re-read shows the proposal
    assert ws.overlay == {}  # overlay cleared after the run
    assert run.citations.citations[0].status == "verified"
    assert "PROPOSE changes" in agent.system_prompt()


def test_edit_requires_reading_first(ws):  # noqa: F811
    llm, agent = make(
        [
            edit_block("README.md", "# Tyre study", "# Tyre study (2026)"),
            tool("read_file", path="README.md"),
            edit_block("README.md", "# Tyre study", "# Tyre study (2026)"),
            "Final Answer: Updated the title (README.md:1).",
        ],
        ws,
        mode="edit",
    )
    run = agent.run("Add the year to the title")
    assert run.steps[0].tool_error and "read 'README.md' with read_file before editing" in run.steps[0].observation
    assert run.changes[0].proposed.startswith("# Tyre study (2026)")


def test_editing_is_refused_outside_edit_mode(ws):  # noqa: F811
    _, agent = make(
        [tool("read_file", path="README.md"), edit_block("README.md", "# Tyre study", "# X"), "Final Answer: no edits."],
        ws,
    )
    run = agent.run("q")
    assert run.steps[1].tool_error and "not available in this mode" in run.steps[1].observation
    assert run.changes == [] and "PROPOSE" not in agent.system_prompt()


def test_failed_edit_stops_the_batch_and_reports(ws):  # noqa: F811
    two = edit_block("src/fit.py", "does not exist", "x") + "\n" + edit_block("src/fit.py", "    x = B * slip", "    x = 1")
    _, agent = make([tool("read_file", path="src/fit.py"), two, "Final Answer: failed"], ws, mode="edit")
    run = agent.run("q")
    obs = run.steps[1].observation
    assert "NOT staged" in obs and "1 edit(s) in this reply were not attempted" in obs
    assert run.changes == []


def test_syntax_warning_is_fed_back(ws):  # noqa: F811
    llm, agent = make(
        [
            tool("read_file", path="src/fit.py"),
            edit_block("src/fit.py", "def peak_mu(fx, fz):", "def peak_mu(fx, fz)"),
            "Final Answer: done",
        ],
        ws,
        mode="edit",
    )
    run = agent.run("q")
    assert "WARNING: Python syntax error" in llm.calls[2][-1]["content"]
    assert run.changes[0].warnings


def test_create_file_and_follow_up_context(ws, ws_dir):  # noqa: F811
    create = "```create\npath: docs/usage.md\n<<<<<<< CONTENT\n# Usage\nCall magic_formula().\n>>>>>>> END\n```"
    _, agent = make([tool("list_files"), create, "Final Answer: Added docs/usage.md:1-2."], ws, mode="edit")
    run = agent.run("Write a usage note")
    assert run.changes[0].kind == "create" and not (ws_dir / "docs").exists()
    llm2, agent2 = make([tool("list_files"), "Final Answer: ok"], ws, mode="edit")
    agent2.run("Also mention peak_mu", history=[run])
    assert "Changes proposed in A1, with their current status: docs/usage.md (pending)" in llm2.calls[0][1]["content"]


def test_run_with_changes_round_trips(ws):  # noqa: F811
    _, agent = make(
        [tool("read_file", path="README.md"), edit_block("README.md", "# Tyre study", "# Tyre study v2"), "Final Answer: ok"],
        ws,
        mode="edit",
    )
    run = agent.run("q")
    restored = AgentRun.from_dict(json.loads(json.dumps(run.to_dict())))
    assert restored.changes[0].diff() == run.changes[0].diff()
