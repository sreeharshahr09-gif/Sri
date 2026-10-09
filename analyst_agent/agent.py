"""The analysis loop: plan → write code → execute → observe → repeat → answer."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from .config import AgentConfig
from .data import Dataset, describe_for_llm
from .grounding import GroundingReport, check_grounding
from .llm import ChatModel, LLMError
from .parsing import parse_reply, strip_thinking
from .prompts import (
    FORCE_FINAL,
    NUDGE_NO_CHART,
    NUDGE_NO_CODE,
    NUDGE_REPEATED,
    NUDGE_TRUNCATED,
    OBSERVATION_FOOTER,
    SYSTEM_PROMPT,
    wants_chart,
)
from .sandbox import Artifact, ExecutionResult, Sandbox

EventHandler = Callable[[str, Any], None]


@dataclass
class AgentStep:
    index: int
    reply: str
    thought: str = ""
    code: str | None = None
    result: ExecutionResult | None = None
    note: str | None = None  # "repeated", "truncated", ...
    llm_latency_s: float = 0.0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @property
    def succeeded(self) -> bool:
        return self.result is not None and self.result.ok

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["result"] = self.result.to_dict() if self.result else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentStep":
        data = dict(data)
        result = data.pop("result", None)
        return cls(result=ExecutionResult.from_dict(result) if result else None, **data)


@dataclass
class AgentRun:
    question: str
    answer: str = ""
    status: str = "running"  # answered | step_limit | llm_error | error
    error: str | None = None
    steps: list[AgentStep] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    duration_s: float = 0.0
    model: str = ""
    temperature: float | None = None
    dataset_sha256: str = ""
    grounding: GroundingReport | None = None

    @property
    def executed(self) -> bool:
        """True if at least one code block ran successfully."""
        return any(s.succeeded for s in self.steps)

    @property
    def successful_code(self) -> list[str]:
        return [s.code for s in self.steps if s.succeeded and s.code]

    def report_artifacts(self) -> list[Artifact]:
        """Outputs meant for the user: everything passed to show() by successful steps.

        Falls back to the auto-displayed output of the last successful step when the
        model never called show(), so a correct result is never hidden.
        """
        explicit = [a for s in self.steps if s.succeeded for a in s.result.artifacts if a.explicit]
        if explicit:
            return explicit
        for step in reversed(self.steps):
            if step.succeeded and step.result.artifacts:
                return list(step.result.artifacts)
        return []

    @property
    def total_tokens(self) -> int:
        return sum((s.prompt_tokens or 0) + (s.completion_tokens or 0) for s in self.steps)

    def to_dict(self) -> dict[str, Any]:
        data = {k: v for k, v in self.__dict__.items() if k not in ("steps", "grounding")}
        data["steps"] = [s.to_dict() for s in self.steps]
        data["grounding"] = self.grounding.to_dict() if self.grounding else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentRun":
        data = dict(data)
        steps = [AgentStep.from_dict(s) for s in data.pop("steps", [])]
        grounding = data.pop("grounding", None)
        return cls(
            steps=steps,
            grounding=GroundingReport.from_dict(grounding) if grounding else None,
            **data,
        )


def _normalize_code(code: str) -> str:
    lines = [ln.split("#", 1)[0].rstrip() for ln in code.strip().splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = int(limit * 0.6)
    tail = limit - head
    return f"{text[:head]}\n... [{len(text) - limit:,} characters omitted] ...\n{text[-tail:]}"


def build_system_prompt(n_rows: int, modules: Iterable[str], profile: str) -> str:
    return SYSTEM_PROMPT.format(n_rows=n_rows, modules=", ".join(modules), profile=profile)


class DataAnalystAgent:
    def __init__(
        self,
        llm: ChatModel,
        sandbox: Sandbox,
        dataset: Dataset,
        config: AgentConfig | None = None,
        profile: str | None = None,
    ):
        self.llm = llm
        self.sandbox = sandbox
        self.dataset = dataset
        self.config = config or AgentConfig()
        self.profile = profile or describe_for_llm(dataset, sample_rows=self.config.sample_rows)
        self.sandbox.set_dataframe(dataset.df, key=f"{dataset.sha256}:{dataset.sheet}")

    # ------------------------------------------------------------------ prompts

    def system_prompt(self) -> str:
        return build_system_prompt(len(self.dataset.df), self.sandbox.allowed_modules, self.profile)

    def _question_message(self, question: str, history: list[AgentRun]) -> str:
        """The user turn: earlier Q&A as a context block, then the current question.

        Earlier turns are deliberately not replayed as assistant messages: a history made of
        bare "Final Answer:" replies teaches the model to answer without running code.
        """
        turns = [h for h in history if h.answer][-self.config.history_turns :] if self.config.history_turns else []
        if not turns:
            return question
        lines = ["Earlier in this conversation (context only):"]
        for n, past in enumerate(turns, 1):
            tag = "" if past.executed else " [not verified by code]"
            lines.append(f"Q{n}: {past.question}\nA{n}{tag}: {_clip(past.answer, 1500)}")
        previous = next((h for h in reversed(turns) if h.successful_code), None)
        if previous is not None:
            code = _clip("\n\n".join(previous.successful_code[-2:]), 3000)
            lines.append(
                "Code behind the most recent verified answer (variables do not persist; reuse or "
                f"adapt it if relevant):\n```python\n{code}\n```"
            )
        lines.append(f"Current question: {question}")
        return "\n\n".join(lines)

    def _premature_answer(self, run: AgentRun, question: str, nudged: set[str]) -> str | None:
        """Feedback when the model tries to answer before producing the evidence, else None.

        Each kind of nudge is sent at most once, so genuinely code-free questions
        ("what can you do?") still get answered.
        """
        if not run.executed and "no_code" not in nudged:
            nudged.add("no_code")
            return NUDGE_NO_CODE
        if wants_chart(question) and "no_chart" not in nudged:
            has_chart = any(
                a.kind in ("plotly", "image") for s in run.steps if s.succeeded for a in s.result.artifacts
            )
            if not has_chart:
                nudged.add("no_chart")
                return NUDGE_NO_CHART
        return None

    def format_observation(self, result: ExecutionResult) -> str:
        limit = self.config.max_observation_chars
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
            parts.append(f"stdout{note}:\n{_clip(result.stdout.rstrip(), int(limit * 0.5))}")

        if result.artifacts:
            lines = []
            for i, art in enumerate(result.artifacts, 1):
                label = "shown to user" if art.explicit else "last expression"
                title = f" '{art.title}'" if art.title else ""
                lines.append(f"[{i}] ({label}){title} {art.summary}")
            parts.append("Outputs:\n" + _clip("\n".join(lines), int(limit * 0.4)))
        elif result.ok and not result.stdout.strip():
            parts.append("The code produced no output. Use print() or show() to see results.")

        if result.warnings:
            parts.append("Warnings:\n" + "\n".join(f"- {w[:300]}" for w in result.warnings))

        if not result.ok:
            if result.traceback:
                parts.append("Traceback:\n" + _clip(result.traceback, 2500))
            parts.append(self._error_hint(result))
        return _clip("\n\n".join(p for p in parts if p), limit)

    def _error_hint(self, result: ExecutionResult) -> str:
        etype = result.error_type or ""
        if etype == "KeyError":
            cols = ", ".join(repr(c) for c in self.dataset.df.columns[:100])
            return f"Hint: the available columns are: {cols}."
        if etype == "NameError":
            return "Hint: each block runs in a fresh process; re-create variables from earlier blocks."
        if etype in ("ImportError", "ModuleNotFoundError"):
            return f"Hint: importable modules are: {', '.join(self.sandbox.allowed_modules)}."
        if etype in ("Timeout", "WorkerCrash", "MemoryError"):
            return "Hint: reduce the work: aggregate first, sample rows, or avoid row-wise loops/apply."
        return ""

    # ------------------------------------------------------------------ loop

    def run(
        self,
        question: str,
        history: Iterable[AgentRun] = (),
        on_event: EventHandler | None = None,
    ) -> AgentRun:
        history = list(history)
        emit = on_event or (lambda *_: None)
        cfg = self.config
        started = time.perf_counter()
        run = AgentRun(
            question=question,
            model=getattr(getattr(self.llm, "config", None), "model", type(self.llm).__name__),
            temperature=getattr(getattr(self.llm, "config", None), "temperature", None),
            dataset_sha256=self.dataset.sha256,
        )
        messages = [
            {"role": "system", "content": self.system_prompt()},
            {"role": "user", "content": self._question_message(question, history)},
        ]
        executed: dict[str, int] = {}
        nudged: set[str] = set()

        try:
            for index in range(cfg.max_steps + 1):
                force_final = index == cfg.max_steps
                emit("llm_call", index)
                completion = self.llm.chat(messages)
                parsed = parse_reply(completion.content)
                step = AgentStep(
                    index=index,
                    reply=completion.content,
                    thought=parsed.thought,
                    llm_latency_s=completion.latency_s,
                    prompt_tokens=completion.prompt_tokens,
                    completion_tokens=completion.completion_tokens,
                )
                is_answer = not (parsed.code or parsed.truncated_code)
                if is_answer and not force_final:
                    feedback = self._premature_answer(run, question, nudged)
                    if feedback is not None:
                        step.note = "premature answer"
                        run.steps.append(step)
                        emit("step", step)
                        footer = FORCE_FINAL if index + 1 == cfg.max_steps else ""
                        messages.append({"role": "assistant", "content": strip_thinking(completion.content) or "(empty)"})
                        messages.append({"role": "user", "content": f"{feedback}\n\n{footer}".strip()})
                        continue
                if force_final or is_answer:
                    run.steps.append(step)
                    answer = parsed.final_answer
                    if not answer:
                        # Forced final but the model wrote code anyway: keep its prose only.
                        answer = parsed.thought or strip_thinking(completion.content)
                    run.answer = answer.strip() or "(The model returned an empty answer.)"
                    run.status = "step_limit" if force_final else "answered"
                    break

                if parsed.truncated_code:
                    step.note = "truncated"
                    feedback = NUDGE_TRUNCATED
                else:
                    step.code = parsed.code
                    key = _normalize_code(parsed.code)
                    if key in executed:
                        step.note = "repeated"
                        feedback = NUDGE_REPEATED
                    else:
                        executed[key] = index
                        emit("executing", step)
                        try:
                            step.result = self.sandbox.run(parsed.code)
                        except Exception as exc:  # infrastructure failure, not the model's code
                            step.result = ExecutionResult(
                                ok=False, error_type="SandboxError", error_message=f"{type(exc).__name__}: {exc}"
                            )
                        feedback = self.format_observation(step.result)

                footer = FORCE_FINAL if index + 1 == cfg.max_steps else OBSERVATION_FOOTER
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

        if run.answer:
            run.grounding = check_grounding(run.answer, self._evidence_texts(run), question=question)
        run.duration_s = time.perf_counter() - started
        emit("done", run)
        return run

    def _evidence_texts(self, run: AgentRun) -> list[str]:
        texts = [self.profile]
        for step in run.steps:
            if not step.succeeded:
                continue
            texts.append(step.result.stdout)
            for art in step.result.artifacts:
                texts.append(art.summary)
                if art.kind == "table":
                    texts.append(art.payload.get("table_json", ""))
                elif art.kind in ("value", "text"):
                    texts.append(art.payload.get("text", ""))
                elif art.kind == "plotly":
                    # Drop opaque strings (base64 arrays, ids) whose digits would match spuriously.
                    figure = art.payload.get("figure_json", "")
                    texts.append(re.sub(r'"(?:bdata|uid|[a-z]*src)":\s*"[^"]*"', "", figure))
        return texts
