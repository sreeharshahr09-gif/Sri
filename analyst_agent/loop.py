"""The loop both agents share: ask the model, act on its reply, feed back what happened, repeat, answer.

DataAnalystAgent (code against a dataset) and WorkspaceAgent (tools on a folder) differ only in how
they prompt, read a reply, act on it and check the final answer. They subclass AgentLoop and fill in
its hooks; stopping, the step limit, nudges, repeat detection, context trimming, error handling and
timing are done here, once, for both.
"""

from __future__ import annotations

import io
import time
import tokenize
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .config import AgentConfig, LLMConfig, context_budget_chars
from .llm import ChatModel, Completion, LLMError
from .parsing import strip_thinking
from .runs import AgentRun, AgentStep, EventHandler
from .sandbox import ExecutionResult, Sandbox

# --------------------------------------------------------------------------- helpers


def clip(text: str, limit: int) -> str:
    """`text` cut to `limit` characters, keeping its start and end."""
    if len(text) <= limit:
        return text
    head = int(limit * 0.6)
    tail = limit - head
    return f"{text[:head]}\n... [{len(text) - limit:,} characters omitted] ...\n{text[-tail:]}"


def normalize_code(code: str) -> str:
    """The code without comments and blank lines, to recognise a block the model sends again.

    Comments are found with the tokenizer, so a `#` inside a string (df["Item #1"]) is kept.
    """
    lines = code.strip().splitlines()
    try:
        for tok in tokenize.generate_tokens(io.StringIO("\n".join(lines)).readline):
            if tok.type == tokenize.COMMENT:
                row, col = tok.start
                lines[row - 1] = lines[row - 1][:col]
    except (tokenize.TokenError, SyntaxError):
        pass  # incomplete code: the comments found so far are dropped, the rest is compared as written
    return "\n".join(ln.rstrip() for ln in lines if ln.strip())


def format_execution(result: ExecutionResult, limit: int, hint: str = "") -> str:
    """Text the model sees after a code block runs: status, output, outputs shown, errors."""
    parts: list[str] = []
    if result.rejected:
        parts.append(f"Status: REJECTED, the code did not run.\n{result.error_message}")
        parts.append("Rewrite the code to comply with the sandbox rules.")
        return "\n\n".join(parts)

    if result.ok:
        parts.append(f"Status: success ({result.duration_s:.1f}s)")
    else:
        parts.append(f"Status: ERROR ({result.error_type}): {result.error_message}")

    if result.stdout.strip():
        note = " (truncated)" if result.stdout_truncated else ""
        parts.append(f"stdout{note}:\n{clip(result.stdout.rstrip(), int(limit * 0.5))}")

    if result.artifacts:
        lines = []
        for i, art in enumerate(result.artifacts, 1):
            label = "shown to user" if art.explicit else "last expression"
            title = f" '{art.title}'" if art.title else ""
            lines.append(f"[{i}] ({label}){title} {art.summary}")
        parts.append("Outputs:\n" + clip("\n".join(lines), int(limit * 0.4)))
    elif result.ok and not result.stdout.strip():
        parts.append("The code produced no output. Use print() or show() to see results.")

    if result.warnings:
        parts.append("Warnings:\n" + "\n".join(f"- {w[:300]}" for w in result.warnings))

    if not result.ok:
        if result.traceback:
            parts.append("Traceback:\n" + clip(result.traceback, 2500))
        parts.append(hint)
    return clip("\n\n".join(p for p in parts if p), limit)


def run_code(sandbox: Sandbox, code: str) -> ExecutionResult:
    """Run code in the sandbox. A failure of the sandbox itself (not of the code) becomes a failed result."""
    try:
        return sandbox.run(code)
    except Exception as exc:  # infrastructure failure, not the model's code
        return ExecutionResult(ok=False, error_type="SandboxError", error_message=f"{type(exc).__name__}: {exc}")


def fit_context(messages: list[dict[str, str]], budget: int, note: str) -> None:
    """Shorten the oldest observations when the conversation outgrows the context budget."""
    total = sum(len(m["content"]) for m in messages)
    for msg in messages[2:-2]:  # keep the system prompt, question and latest exchange
        if total <= budget:
            return
        if msg["role"] == "user" and not msg["content"].endswith(note) and len(msg["content"]) > 600:
            shortened = msg["content"][:400] + note
            total -= len(msg["content"]) - len(shortened)
            msg["content"] = shortened


def recent_answered(history: Iterable[AgentRun], turns: int) -> list[AgentRun]:
    """The last `turns` earlier runs that have an answer (the conversation memory)."""
    return [h for h in history if h.answer][-turns:] if turns else []


# --------------------------------------------------------------------------- the loop


@dataclass
class LoopState:
    """What one run remembers between its steps. Agents extend it with their own."""

    done: set[str] = field(default_factory=set)  # actions already carried out, to catch repeats
    nudged: set[str] = field(default_factory=set)  # kinds of one-time feedback already sent

    def repeat(self, key: str) -> bool:
        """True if this action was carried out before; otherwise records it."""
        if key in self.done:
            return True
        self.done.add(key)
        return False


class AgentLoop:
    """Template for an agent: subclasses set the prompts below and implement the hooks."""

    footer: str = ""  # sent after each observation
    force_final: str = ""  # sent before the last allowed step
    footer_after_nudge: bool = False  # whether a premature-answer nudge is followed by the footer too
    elided_note: str = "\n[... older output removed to save context]"

    def __init__(self, llm: ChatModel, config: AgentConfig):
        self.llm = llm
        self.config = config

    # ------------------------------------------------------------------ hooks

    def system_prompt(self) -> str:
        raise NotImplementedError

    def _question_message(self, question: str, history: list[AgentRun]) -> str:
        """The user turn: the question, with earlier turns as context."""
        raise NotImplementedError

    def _new_run(self, question: str) -> AgentRun:
        raise NotImplementedError

    def _new_state(self) -> LoopState:
        return LoopState()

    def _parse(self, reply: str) -> Any:
        """The reply parsed; the result has `thought` and `final_answer` attributes."""
        raise NotImplementedError

    def _is_action(self, parsed: Any) -> bool:
        """True if the reply asks for something to be done (code, a tool call), not an answer."""
        raise NotImplementedError

    def _premature_answer(self, run: AgentRun, question: str, state: LoopState) -> str | None:
        """Feedback when the model answers before it has the evidence, else None."""
        return None

    def _act(self, step: AgentStep, parsed: Any, state: LoopState, emit: EventHandler) -> str:
        """Carry out the reply's action, record it on `step`, and return the feedback for the model."""
        raise NotImplementedError

    def _finish(self, run: AgentRun, state: LoopState) -> None:
        """Check the answer and release per-run resources (runs even after an error)."""

    # ------------------------------------------------------------------ the loop

    def context_budget(self) -> int:
        """Characters of conversation kept before old observations are shortened."""
        if self.config.max_context_chars:
            return self.config.max_context_chars
        llm_cfg = getattr(self.llm, "config", None)
        return context_budget_chars(llm_cfg if isinstance(llm_cfg, LLMConfig) else None)

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
        run = self._new_run(question)
        run.model = getattr(llm_cfg, "model", type(self.llm).__name__)
        run.temperature = getattr(llm_cfg, "temperature", None)
        messages = [
            {"role": "system", "content": self.system_prompt()},
            {"role": "user", "content": self._question_message(question, history)},
        ]
        state = self._new_state()
        budget = self.context_budget()

        try:
            for index in range(cfg.max_steps + 1):
                if should_stop is not None and should_stop():
                    run.status = "cancelled"
                    run.error = "Stopped at your request."
                    break
                force_final = index == cfg.max_steps
                last_chance = index + 1 == cfg.max_steps
                fit_context(messages, budget, self.elided_note)
                emit("llm_call", index)
                completion = self.llm.chat(messages)
                parsed = self._parse(completion.content)
                step = _new_step(index, completion, parsed.thought)

                acting = self._is_action(parsed)
                feedback = None if acting or force_final else self._premature_answer(run, question, state)
                if feedback is not None:
                    step.note = "premature answer"
                    footer = self.force_final if last_chance else self.footer if self.footer_after_nudge else ""
                elif force_final or not acting:
                    run.steps.append(step)
                    answer = parsed.final_answer or parsed.thought or strip_thinking(completion.content)
                    run.answer = answer.strip() or "(The model returned an empty answer.)"
                    run.status = "step_limit" if force_final else "answered"
                    break
                else:
                    feedback = self._act(step, parsed, state, emit)
                    footer = self.force_final if last_chance else self.footer

                run.steps.append(step)
                emit("step", step)
                messages.append({"role": "assistant", "content": strip_thinking(completion.content) or "(empty)"})
                messages.append({"role": "user", "content": f"{feedback}\n\n{footer}".strip()})
        except LLMError as exc:
            run.status = "llm_error"
            run.error = str(exc)
        except Exception as exc:  # keep the partial trace instead of losing the whole run
            run.status = "error"
            run.error = f"{type(exc).__name__}: {exc}"

        self._finish(run, state)
        run.duration_s = time.perf_counter() - started
        emit("done", run)
        return run


def _new_step(index: int, completion: Completion, thought: str) -> AgentStep:
    return AgentStep(
        index=index,
        reply=completion.content,
        thought=thought,
        llm_latency_s=completion.latency_s,
        prompt_tokens=completion.prompt_tokens,
        completion_tokens=completion.completion_tokens,
    )
