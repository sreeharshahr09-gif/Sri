"""What an agent run records: its steps, its outcome, and the events it reports while it works.

Both agents record steps the same way. What only one agent produces lives on its own subclass of
AgentRun: DataRun (agent.py) for the numeric grounding audit, WorkspaceRun (workspace_agent.py) for
citations and proposed file changes. Each subclass names the modes it records, so
`AgentRun.from_dict` restores a saved run as the right type.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, Callable, ClassVar

from .sandbox import Artifact, ExecutionResult

# on_event(kind, payload): progress reports from an agent or pipeline to whoever displays it.
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
    # Workspace agent tool calls (list_files, search, read_file, ...).
    tool: str | None = None
    tool_args: dict | None = None
    observation: str | None = None
    tool_error: bool = False

    @property
    def succeeded(self) -> bool:
        if self.tool is not None:
            return not self.tool_error
        return self.result is not None and self.result.ok

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["result"] = self.result.to_dict() if self.result else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentStep:
        data = dict(data)
        result = data.pop("result", None)
        return cls(result=ExecutionResult.from_dict(result) if result else None, **data)


@dataclass
class AgentRun:
    question: str
    answer: str = ""
    status: str = "running"  # answered | step_limit | llm_error | error | cancelled
    error: str | None = None
    steps: list[AgentStep] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    duration_s: float = 0.0
    model: str = ""
    temperature: float | None = None
    mode: str = "data"  # "data" analysis, or workspace "answer" / "teach" / "edit"

    _types: ClassVar[dict[str, type[AgentRun]]] = {}  # mode -> the subclass that records it

    def __init_subclass__(cls, modes: tuple[str, ...] = (), **kwargs):
        super().__init_subclass__(**kwargs)
        for mode in modes:
            AgentRun._types[mode] = cls

    @classmethod
    def for_mode(cls, mode: str) -> type[AgentRun]:
        """The run type that records `mode` (AgentRun itself for an unknown mode)."""
        return AgentRun._types.get(mode, AgentRun)

    @property
    def executed(self) -> bool:
        """True if at least one code block or tool call ran successfully."""
        return any(s.succeeded for s in self.steps)

    @property
    def successful_code(self) -> list[str]:
        return [s.code for s in self.steps if s.succeeded and s.code]

    def report_artifacts(self) -> list[Artifact]:
        """Outputs meant for the user: everything passed to show() by successful steps.

        Falls back to the auto-displayed output of the last successful step when the
        model never called show(), so a correct result is never hidden.
        """
        ran = [s for s in self.steps if s.succeeded and s.result is not None]
        explicit = [a for s in ran for a in s.result.artifacts if a.explicit]
        if explicit:
            return explicit
        for step in reversed(ran):
            if step.result.artifacts:
                return list(step.result.artifacts)
        return []

    @property
    def total_tokens(self) -> int:
        return sum((s.prompt_tokens or 0) + (s.completion_tokens or 0) for s in self.steps)

    def to_dict(self) -> dict[str, Any]:
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        data["steps"] = [s.to_dict() for s in self.steps]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentRun:
        """A run saved with to_dict(), as the type that recorded it. Keys the type does not know are skipped."""
        run_type = cls.for_mode(data.get("mode", "data")) if cls is AgentRun else cls
        known = {f.name for f in fields(run_type)}
        values = {k: v for k, v in data.items() if k in known}
        values["steps"] = [AgentStep.from_dict(s) for s in values.get("steps", [])]
        return run_type(**run_type._restore(values))

    @classmethod
    def _restore(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Turn a subclass's own saved fields back into objects (none here)."""
        return values
