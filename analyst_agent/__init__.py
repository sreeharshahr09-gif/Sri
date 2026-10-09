"""Data Analyst Agent: an LLM-driven analysis loop over tabular data.

The package is UI-agnostic; `app.py` is a Streamlit front end on top of it.
"""

from .agent import AgentRun, AgentStep, DataAnalystAgent
from .config import AgentConfig, LLMConfig, SandboxConfig
from .data import Dataset, load_dataset
from .llm import LLMClient, LLMError
from .sandbox import ExecutionResult, Sandbox
from .workspace import Workspace, WorkspaceError
from .workspace_agent import WorkspaceAgent

__all__ = [
    "AgentConfig",
    "AgentRun",
    "AgentStep",
    "DataAnalystAgent",
    "Dataset",
    "ExecutionResult",
    "LLMClient",
    "LLMConfig",
    "LLMError",
    "Sandbox",
    "SandboxConfig",
    "Workspace",
    "WorkspaceAgent",
    "WorkspaceError",
    "load_dataset",
]
