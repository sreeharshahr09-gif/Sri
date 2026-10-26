"""The data analyst: plan → write code → execute → observe → repeat → answer, on one dataset."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from .config import AgentConfig
from .data import Dataset, describe_for_llm
from .grounding import GroundingReport, check_grounding
from .llm import ChatModel
from .loop import AgentLoop, LoopState, clip, format_execution, normalize_code, recent_answered, run_code
from .parsing import ParsedReply, parse_reply
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
from .runs import AgentRun, AgentStep, EventHandler
from .sandbox import ExecutionResult, Sandbox


@dataclass
class DataRun(AgentRun, modes=("data",)):
    """A data-analysis run: which dataset it ran on, and whether the answer's numbers trace to the output."""

    dataset_sha256: str = ""
    grounding: GroundingReport | None = None

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["grounding"] = self.grounding.to_dict() if self.grounding else None
        return data

    @classmethod
    def _restore(cls, values: dict[str, Any]) -> dict[str, Any]:
        if values.get("grounding"):
            values["grounding"] = GroundingReport.from_dict(values["grounding"])
        return values


def build_system_prompt(n_rows: int, modules: Iterable[str], profile: str) -> str:
    return SYSTEM_PROMPT.format(n_rows=n_rows, modules=", ".join(modules), profile=profile)


class DataAnalystAgent(AgentLoop):
    footer = OBSERVATION_FOOTER
    force_final = FORCE_FINAL
    elided_note = "\n[... older output removed to save context; run the code again if you need it]"

    def __init__(
        self,
        llm: ChatModel,
        sandbox: Sandbox,
        dataset: Dataset,
        config: AgentConfig | None = None,
        profile: str | None = None,
    ):
        super().__init__(llm, config or AgentConfig())
        self.sandbox = sandbox
        self.dataset = dataset
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
        turns = recent_answered(history, self.config.history_turns)
        if not turns:
            return question
        lines = ["Earlier in this conversation (context only):"]
        for n, past in enumerate(turns, 1):
            tag = "" if past.executed else " [not verified by code]"
            lines.append(f"Q{n}: {past.question}\nA{n}{tag}: {clip(past.answer, 1500)}")
        previous = next((h for h in reversed(turns) if h.successful_code), None)
        if previous is not None:
            code = clip("\n\n".join(previous.successful_code[-2:]), 3000)
            lines.append(
                "Code behind the most recent verified answer (variables do not persist; reuse or "
                f"adapt it if relevant):\n```python\n{code}\n```"
            )
        lines.append(f"Current question: {question}")
        return "\n\n".join(lines)

    def format_observation(self, result: ExecutionResult) -> str:
        hint = "" if result.ok or result.rejected else self._error_hint(result)
        return format_execution(result, self.config.max_observation_chars, hint)

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

    # ------------------------------------------------------------------ loop hooks

    def _new_run(self, question: str) -> DataRun:
        return DataRun(question=question, dataset_sha256=self.dataset.sha256)

    def _parse(self, reply: str) -> ParsedReply:
        return parse_reply(reply)

    def _is_action(self, parsed: ParsedReply) -> bool:
        return bool(parsed.code or parsed.truncated_code)

    def _premature_answer(self, run: AgentRun, question: str, state: LoopState) -> str | None:
        """Feedback when the model tries to answer before producing the evidence, else None.

        Each kind of nudge is sent at most once, so genuinely code-free questions
        ("what can you do?") still get answered.
        """
        if not run.executed and "no_code" not in state.nudged:
            state.nudged.add("no_code")
            return NUDGE_NO_CODE
        if wants_chart(question) and "no_chart" not in state.nudged:
            has_chart = any(
                a.kind in ("plotly", "image") for s in run.steps if s.succeeded for a in s.result.artifacts
            )
            if not has_chart:
                state.nudged.add("no_chart")
                return NUDGE_NO_CHART
        return None

    def _act(self, step: AgentStep, parsed: ParsedReply, state: LoopState, emit: EventHandler) -> str:
        if parsed.truncated_code:
            step.note = "truncated"
            return NUDGE_TRUNCATED
        step.code = parsed.code
        if state.repeat("code:" + normalize_code(parsed.code)):
            step.note = "repeated"
            return NUDGE_REPEATED
        emit("executing", step)
        step.result = run_code(self.sandbox, parsed.code)
        return self.format_observation(step.result)

    def _finish(self, run: DataRun, state: LoopState) -> None:
        if run.answer:
            run.grounding = check_grounding(run.answer, self._evidence_texts(run), question=run.question)

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
