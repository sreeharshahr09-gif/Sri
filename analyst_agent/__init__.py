"""Data Analyst Agent: an LLM-driven analysis loop over tabular data.

The package is UI-agnostic; `app.py` is a Streamlit front end on top of it.
"""

from .agent import DataAnalystAgent, DataRun
from .config import AgentConfig, LLMConfig, SandboxConfig
from .data import Dataset, load_dataset
from .llm import LLMClient, LLMError
from .runs import AgentRun, AgentStep
from .sandbox import ExecutionResult, Sandbox
from .workspace import Workspace, WorkspaceError
from .workspace_agent import WorkspaceAgent, WorkspaceRun

__all__ = [
    "AgentConfig",
    "AgentRun",
    "AgentStep",
    "DataAnalystAgent",
    "DataRun",
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
    "WorkspaceRun",
    "load_dataset",
]
