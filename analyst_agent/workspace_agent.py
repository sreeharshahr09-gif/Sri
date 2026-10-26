"""Workspace agent: explores a local folder with tools, explains what it finds, and can propose edits.

Each step the model calls one tool (list_files, search, read_file, a Python block run in the
sandbox, or edit/create blocks in Edit mode), sees the result, and finally answers with
`path:line` citations. Citations are checked against what was actually opened during the run.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from typing import Any

from .config import AgentConfig
from .editing import ChangeSet, EditError, FileChange, editable_path
from .grounding import CitationReport, check_citations
from .llm import ChatModel
from .loop import AgentLoop, LoopState, clip, format_execution, normalize_code, recent_answered, run_code
from .parsing import ParsedAction, parse_action
from .prompts import (
    ACCESS_EDIT,
    ACCESS_READ_ONLY,
    NUDGE_REPEATED,
    NUDGE_TRUNCATED,
    STYLE_ANSWER,
    STYLE_EDIT,
    STYLE_TEACH,
    WORKSPACE_EDIT_TOOLS,
    WORKSPACE_FOOTER,
    WORKSPACE_FORCE_FINAL,
    WORKSPACE_NUDGE_NO_EVIDENCE,
    WORKSPACE_PYTHON_TOOL,
    WORKSPACE_SYSTEM_PROMPT,
)
from .runs import AgentRun, AgentStep, Event, EventHandler, StepNote
from .sandbox import Sandbox
from .workspace import Workspace, WorkspaceError

TOOLS = ("list_files", "search", "read_file")
MODES = ("answer", "teach", "edit")


@dataclass
class WorkspaceRun(AgentRun, modes=MODES):
    """A workspace run: the file citations in its answer and, in Edit mode, the changes it proposed."""

    mode: str = "answer"
    citations: CitationReport | None = None
    changes: list[FileChange] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["citations"] = self.citations.to_dict() if self.citations else None
        data["changes"] = [c.to_dict() for c in self.changes]
        return data

    @classmethod
    def _restore(cls, values: dict[str, Any]) -> dict[str, Any]:
        if values.get("citations"):
            values["citations"] = CitationReport.from_dict(values["citations"])
        values["changes"] = [FileChange.from_dict(c) for c in values.get("changes") or []]
        return values


@dataclass
class _RunState(LoopState):
    seen: dict[str, list[tuple[int, int]]] = field(default_factory=dict)  # lines viewed, for citations
    read_files: set[str] = field(default_factory=set)  # files opened with read_file (edit precondition)
    changes: ChangeSet | None = None


def _int(value, name: str, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise WorkspaceError(f"'{name}' must be an integer, got {value!r}.") from None


def _bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return bool(value)


class WorkspaceAgent(AgentLoop):
    footer = WORKSPACE_FOOTER
    force_final = WORKSPACE_FORCE_FINAL
    footer_after_nudge = True
    elided_note = "\n[... older tool output removed to save context; read it again if needed]"

    def __init__(
        self,
        llm: ChatModel,
        workspace: Workspace,
        sandbox: Sandbox | None = None,
        config: AgentConfig | None = None,
        mode: str = "answer",
    ):
        if mode not in MODES:
            raise ValueError("mode must be 'answer', 'teach' or 'edit'")
        super().__init__(llm, config or AgentConfig(max_steps=12))
        self.workspace = workspace
        self.sandbox = sandbox
        self.mode = mode
        self._overview: str | None = None

    # ------------------------------------------------------------------ prompts

    def system_prompt(self) -> str:
        if self._overview is None:
            self._overview = self.workspace.overview()
        replacements = {
            "<<PYTHON_TOOL>>": WORKSPACE_PYTHON_TOOL if self.sandbox else "",
            "<<PYTHON_OR>>": " or a ```python block" if self.sandbox else "",
            "<<STYLE>>": {"teach": STYLE_TEACH, "edit": STYLE_EDIT}.get(self.mode, STYLE_ANSWER),
            "<<ACCESS>>": ACCESS_EDIT if self.mode == "edit" else ACCESS_READ_ONLY,
            "<<EDIT_TOOLS>>": WORKSPACE_EDIT_TOOLS if self.mode == "edit" else "",
            "<<EDIT_OR>>": ", edit/create blocks" if self.mode == "edit" else "",
            "<<ROOT>>": self.workspace.root.name or str(self.workspace.root),
            "<<OVERVIEW>>": self._overview,
        }
        prompt = WORKSPACE_SYSTEM_PROMPT
        for key, value in replacements.items():
            prompt = prompt.replace(key, value)
        return prompt

    def _question_message(self, question: str, history: list[AgentRun]) -> str:
        turns = recent_answered(history, self.config.history_turns)
        if not turns:
            return question
        lines = ["Earlier in this conversation (context only; re-read files before relying on details):"]
        for n, past in enumerate(turns, 1):
            files = sorted({s.tool_args.get("path", "") for s in past.steps if s.tool == "read_file" and s.tool_args})
            lines.append(f"Q{n}: {past.question}\nA{n}: {clip(past.answer, 1500)}")
            if files:
                lines.append(f"(Files read for A{n}: {', '.join(f for f in files if f)})")
            if getattr(past, "changes", None):
                summary = ", ".join(f"{c.path} ({c.status})" for c in past.changes)
                lines.append(f"(Changes proposed in A{n}, with their current status: {summary})")
        lines.append(f"Current question: {question}")
        return "\n\n".join(lines)

    # ------------------------------------------------------------------ tools

    def execute_tool(self, name: str, args: dict, state: _RunState) -> tuple[str, bool]:
        """Run one tool. Returns (observation, is_error) and records what was viewed in `state`."""
        ws = self.workspace
        seen = state.seen
        try:
            if name == "list_files":
                return ws.list_files(str(args.get("path") or "."), depth=_int(args.get("depth"), "depth", 2)), False
            if name == "search":
                query = args.get("query") or args.get("pattern") or args.get("text")
                text, hits = ws.search(
                    str(query or ""),
                    path=str(args.get("path") or "."),
                    glob=args.get("glob") or None,
                    regex=_bool(args.get("regex", False)),
                    case_sensitive=_bool(args.get("case_sensitive", False)),
                    max_results=min(_int(args.get("max_results"), "max_results", 40), 100),
                )
                for path, line in hits:
                    seen.setdefault(path, []).append((line, line))
                return text, False
            if name == "read_file":
                if not args.get("path"):
                    raise WorkspaceError("read_file needs a 'path'.")
                text, doc, start, end = ws.read(
                    str(args["path"]),
                    start=_int(args.get("start", args.get("start_line")), "start", 1),
                    end=_int(args.get("end", args.get("end_line")), "end", None),
                )
                if end:
                    seen.setdefault(doc.path, []).append((start, end))
                state.read_files.add(doc.path)
                return text, False
        except WorkspaceError as exc:
            return f"Error: {exc}", True
        except OSError as exc:
            return f"Error: could not access the file ({exc.__class__.__name__}: {exc}).", True
        available = ", ".join(TOOLS) + (" (or a ```python block)" if self.sandbox else "")
        return f"Error: unknown tool '{name}'. Available tools: {available}.", True

    def stage_edits(self, edits: list[dict], state: _RunState) -> tuple[str, bool]:
        """Stage proposed edits in order (stopping at the first failure). Returns (observation, error)."""
        if state.changes is None:
            return ("Error: editing is not available in this mode. Describe the change in your answer, "
                    "or ask the user to switch to Edit mode."), True
        ws = self.workspace
        reports: list[str] = []
        for i, edit in enumerate(edits, 1):
            label = f"[{i}/{len(edits)}] {edit.get('op')} {edit.get('path') or '?'}"
            try:
                _, key = editable_path(ws, edit.get("path") or "")
                staged = state.changes.changes.get(key)
                before = state.changes.overlay.get(key)
                if edit["op"] == "edit":
                    if key not in state.read_files and not (staged and staged.kind == "create"):
                        raise EditError(f"read '{key}' with read_file before editing it.")
                    if before is None:
                        before = "\n".join(ws.document(key).lines)
                    change, key, (lo, hi) = state.changes.stage_edit(
                        key, edit.get("old", ""), edit.get("new", ""), bool(edit.get("replace_all"))
                    )
                    after = state.changes.overlay[key]
                    hunk = "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=2))
                    hunk = "\n".join(hunk.splitlines()[2:])  # drop the ---/+++ header
                    report = f"{label}: staged (proposal only, not written).\n{clip(hunk, 3000)}"
                else:
                    change, key, (lo, hi) = state.changes.stage_create(key, edit.get("content", ""))
                    preview = "\n".join(change.proposed.splitlines()[:30])
                    report = f"{label}: staged new file ({hi} lines, proposal only).\n{clip(preview, 2000)}"
                state.seen.setdefault(key, []).append((lo, hi))
                state.read_files.add(key)
                if change.warnings:
                    report += "\nWARNING: " + "; ".join(change.warnings) + ". Fix this before finishing."
                reports.append(report)
            except (EditError, WorkspaceError) as exc:
                reports.append(f"{label}: NOT staged: {exc}")
                if i < len(edits):
                    reports.append(f"The remaining {len(edits) - i} edit(s) in this reply were not attempted.")
                return "\n\n".join(reports), True
        return "\n\n".join(reports), False

    def _line_count(self, path: str) -> tuple[str, int]:
        try:
            norm = self.workspace.normalize(path)
            return norm, len(self.workspace.document(norm).lines)
        except WorkspaceError as exc:
            raise ValueError(str(exc)) from exc

    # ------------------------------------------------------------------ loop hooks

    def _new_run(self, question: str) -> WorkspaceRun:
        return WorkspaceRun(question=question, mode=self.mode)

    def _new_state(self) -> _RunState:
        state = _RunState()
        if self.mode == "edit":
            state.changes = ChangeSet(self.workspace)
            self.workspace.overlay = state.changes.overlay  # later reads show the proposed version
        return state

    def _parse(self, reply: str) -> ParsedAction:
        return parse_action(reply)

    def _is_action(self, action: ParsedAction) -> bool:
        return bool(action.tool or action.code or action.edits or action.error or action.truncated)

    def _premature_answer(self, run: AgentRun, question: str, state: _RunState) -> str | None:
        if not run.executed and "no_evidence" not in state.nudged:
            state.nudged.add("no_evidence")
            return WORKSPACE_NUDGE_NO_EVIDENCE
        return None

    def _act(self, step: AgentStep, action: ParsedAction, state: _RunState, emit: EventHandler) -> str:
        cfg = self.config
        if action.truncated:
            step.note = StepNote.TRUNCATED
            return NUDGE_TRUNCATED
        if action.error:
            step.note = StepNote.INVALID_TOOL_CALL
            return f"{action.error} Example:\n```tool\n{{\"tool\": \"search\", \"query\": \"...\"}}\n```"
        if action.edits:
            step.tool, step.tool_args = "propose_edits", {"edits": action.edits}
            key = "edits:" + json.dumps(action.edits, sort_keys=True)
            if key in state.done:
                step.note = StepNote.REPEATED
                return "These exact edits were already staged. Read the file to check it, or finish."
            emit(Event.TOOL, step)
            observation, step.tool_error = self.stage_edits(action.edits, state)
            if not step.tool_error:  # a failed edit may legitimately be retried
                state.done.add(key)
            step.observation = observation
            return f"Result of the edits:\n{observation}"
        if action.code is not None:
            step.code = action.code
            if self.sandbox is None:
                step.note = StepNote.NO_PYTHON
                return "Python execution is not available here. Use the tools instead."
            if state.repeat("code:" + normalize_code(action.code)):
                step.note = StepNote.REPEATED
                return NUDGE_REPEATED
            emit(Event.EXECUTING, step)
            step.result = run_code(self.sandbox, action.code)
            return format_execution(step.result, cfg.max_observation_chars)
        step.tool, step.tool_args = action.tool, action.args or {}
        if state.repeat(f"tool:{step.tool}:{sorted(step.tool_args.items())!r}"):
            step.note = StepNote.REPEATED
            return NUDGE_REPEATED
        emit(Event.TOOL, step)
        observation, step.tool_error = self.execute_tool(step.tool, step.tool_args, state)
        step.observation = clip(observation, cfg.max_observation_chars * 3)
        return f"Result of {step.tool}:\n{step.observation}"

    def _finish(self, run: WorkspaceRun, state: _RunState) -> None:
        try:
            if run.answer:
                run.citations = check_citations(run.answer, self._line_count, state.seen)
        finally:
            self.workspace.overlay = {}
        if state.changes is not None:
            run.changes = list(state.changes.changes.values())
