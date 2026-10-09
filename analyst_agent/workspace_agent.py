"""Read-only workspace agent: explores a local folder with tools and explains what it finds.

Loop: the model calls one tool per step (list_files, search, read_file, or a Python block run
in the sandbox), sees the result, and finally answers with `path:line` citations. Citations
are checked against what was actually opened during the run.
"""

from __future__ import annotations

import difflib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .agent import AgentRun, AgentStep, _clip, _normalize_code, format_execution
from .config import AgentConfig
from .editing import ChangeSet, EditError, editable_path
from .grounding import check_citations
from .llm import ChatModel, LLMError
from .parsing import parse_action, strip_thinking
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
from .sandbox import ExecutionResult, Sandbox
from .workspace import Workspace, WorkspaceError

EventHandler = Callable[[str, Any], None]
TOOLS = ("list_files", "search", "read_file")
_ELIDED = "\n[... older tool output removed to save context; read it again if needed]"


@dataclass
class _RunState:
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


class WorkspaceAgent:
    def __init__(
        self,
        llm: ChatModel,
        workspace: Workspace,
        sandbox: Sandbox | None = None,
        config: AgentConfig | None = None,
        mode: str = "answer",
    ):
        if mode not in ("answer", "teach", "edit"):
            raise ValueError("mode must be 'answer', 'teach' or 'edit'")
        self.llm = llm
        self.workspace = workspace
        self.sandbox = sandbox
        self.config = config or AgentConfig(max_steps=12)
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
        turns = [h for h in history if h.answer][-self.config.history_turns :] if self.config.history_turns else []
        if not turns:
            return question
        lines = ["Earlier in this conversation (context only; re-read files before relying on details):"]
        for n, past in enumerate(turns, 1):
            files = sorted({s.tool_args.get("path", "") for s in past.steps if s.tool == "read_file" and s.tool_args})
            lines.append(f"Q{n}: {past.question}\nA{n}: {_clip(past.answer, 1500)}")
            if files:
                lines.append(f"(Files read for A{n}: {', '.join(f for f in files if f)})")
            if past.changes:
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
                    report = f"{label}: staged (proposal only, not written).\n{_clip(hunk, 3000)}"
                else:
                    change, key, (lo, hi) = state.changes.stage_create(key, edit.get("content", ""))
                    preview = "\n".join(change.proposed.splitlines()[:30])
                    report = f"{label}: staged new file ({hi} lines, proposal only).\n{_clip(preview, 2000)}"
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

    def _fit_context(self, messages: list[dict[str, str]]) -> None:
        """Shorten the oldest tool outputs when the conversation outgrows the context budget."""
        budget = self.config.max_context_chars
        total = sum(len(m["content"]) for m in messages)
        for msg in messages[2:-2]:  # keep the system prompt, question and latest exchange
            if total <= budget:
                return
            if msg["role"] == "user" and not msg["content"].endswith(_ELIDED) and len(msg["content"]) > 600:
                shortened = msg["content"][:400] + _ELIDED
                total -= len(msg["content"]) - len(shortened)
                msg["content"] = shortened

    # ------------------------------------------------------------------ loop

    def run(
        self,
        question: str,
        history: Iterable[AgentRun] = (),
        on_event: EventHandler | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> AgentRun:
        history = list(history)
        emit = on_event or (lambda *_: None)
        cfg = self.config
        started = time.perf_counter()
        llm_cfg = getattr(self.llm, "config", None)
        run = AgentRun(
            question=question,
            model=getattr(llm_cfg, "model", type(self.llm).__name__),
            temperature=getattr(llm_cfg, "temperature", None),
            mode=self.mode,
        )
        messages = [
            {"role": "system", "content": self.system_prompt()},
            {"role": "user", "content": self._question_message(question, history)},
        ]
        state = _RunState()
        if self.mode == "edit":
            state.changes = ChangeSet(self.workspace)
            self.workspace.overlay = state.changes.overlay
        done_calls: set[str] = set()
        nudged = False

        try:
            for index in range(cfg.max_steps + 1):
                if should_stop is not None and should_stop():
                    run.status = "cancelled"
                    run.error = "Stopped at your request."
                    break
                force_final = index == cfg.max_steps
                self._fit_context(messages)
                emit("llm_call", index)
                completion = self.llm.chat(messages)
                action = parse_action(completion.content)
                step = AgentStep(
                    index=index,
                    reply=completion.content,
                    thought=action.thought,
                    llm_latency_s=completion.latency_s,
                    prompt_tokens=completion.prompt_tokens,
                    completion_tokens=completion.completion_tokens,
                )
                is_action = bool(action.tool or action.code or action.edits or action.error or action.truncated)

                if not is_action and not force_final and not run.executed and not nudged:
                    nudged = True
                    step.note = "premature answer"
                    feedback = WORKSPACE_NUDGE_NO_EVIDENCE
                elif force_final or not is_action:
                    run.steps.append(step)
                    answer = action.final_answer or action.thought or strip_thinking(completion.content)
                    run.answer = answer.strip() or "(The model returned an empty answer.)"
                    run.status = "step_limit" if force_final else "answered"
                    break
                elif action.truncated:
                    step.note = "truncated"
                    feedback = NUDGE_TRUNCATED
                elif action.error:
                    step.note = "invalid tool call"
                    feedback = f"{action.error} Example:\n```tool\n{{\"tool\": \"search\", \"query\": \"...\"}}\n```"
                elif action.edits:
                    step.tool, step.tool_args = "propose_edits", {"edits": action.edits}
                    key = "edits:" + json.dumps(action.edits, sort_keys=True)
                    if key in done_calls:
                        step.note = "repeated"
                        feedback = "These exact edits were already staged. Read the file to check it, or finish."
                    else:
                        emit("tool", step)
                        observation, step.tool_error = self.stage_edits(action.edits, state)
                        if not step.tool_error:  # a failed edit may legitimately be retried
                            done_calls.add(key)
                        step.observation = observation
                        feedback = f"Result of the edits:\n{observation}"
                elif action.code is not None:
                    step.code = action.code
                    key = "code:" + _normalize_code(action.code)
                    if self.sandbox is None:
                        step.note = "python unavailable"
                        feedback = "Python execution is not available here. Use the tools instead."
                    elif key in done_calls:
                        step.note = "repeated"
                        feedback = NUDGE_REPEATED
                    else:
                        done_calls.add(key)
                        emit("executing", step)
                        try:
                            step.result = self.sandbox.run(action.code)
                        except Exception as exc:  # infrastructure failure, not the model's code
                            step.result = ExecutionResult(
                                ok=False, error_type="SandboxError", error_message=f"{type(exc).__name__}: {exc}"
                            )
                        feedback = format_execution(step.result, cfg.max_observation_chars)
                else:
                    step.tool, step.tool_args = action.tool, action.args or {}
                    key = f"tool:{step.tool}:{sorted(step.tool_args.items())!r}"
                    if key in done_calls:
                        step.note = "repeated"
                        feedback = NUDGE_REPEATED
                    else:
                        done_calls.add(key)
                        emit("tool", step)
                        observation, step.tool_error = self.execute_tool(step.tool, step.tool_args, state)
                        step.observation = _clip(observation, cfg.max_observation_chars * 3)
                        feedback = f"Result of {step.tool}:\n{_clip(observation, cfg.max_observation_chars * 3)}"

                footer = WORKSPACE_FORCE_FINAL if index + 1 == cfg.max_steps else WORKSPACE_FOOTER
                run.steps.append(step)
                emit("step", step)
                messages.append({"role": "assistant", "content": strip_thinking(completion.content) or "(empty)"})
                messages.append({"role": "user", "content": f"{feedback}\n\n{footer}"})
        except LLMError as exc:
            run.status = "llm_error"
            run.error = str(exc)
        except Exception as exc:  # keep the partial trace instead of losing the whole run
            run.status = "error"
            run.error = f"{type(exc).__name__}: {exc}"

        try:
            if run.answer:
                run.citations = check_citations(run.answer, self._line_count, state.seen)
        finally:
            self.workspace.overlay = {}
        if state.changes is not None:
            run.changes = list(state.changes.changes.values())
        run.duration_s = time.perf_counter() - started
        emit("done", run)
        return run
