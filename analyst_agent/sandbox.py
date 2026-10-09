"""Validation and isolated execution of model-generated analysis code.

Defense in depth:
1. A static AST check rejects imports outside an allowlist, dunder access, dynamic
   evaluation and file/network I/O before anything runs.
2. Code runs in a separate Python process with restricted builtins, a guarded
   `__import__`, CPU/memory limits (POSIX) and a wall-clock timeout.
3. Results come back as JSON, so the parent never unpickles untrusted objects.

This is a strong guard against accidents and casual misuse by a model, not a
security boundary against a determined attacker; run the app on a machine you trust.
"""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from .config import SandboxConfig

WORKER_PATH = Path(__file__).with_name("_worker.py")

ALLOWED_MODULES = frozenset(
    {
        "pandas", "numpy", "plotly", "scipy", "statsmodels", "sklearn", "matplotlib", "seaborn",
        "math", "statistics", "random", "datetime", "calendar", "time", "re", "string", "textwrap",
        "collections", "itertools", "functools", "operator", "decimal", "fractions", "json",
        "warnings", "typing", "dataclasses",
    }
)
BLOCKED_SUBMODULES = ("numpy.ctypeslib", "numpy.f2py", "numpy.distutils", "numpy.testing", "pandas.io")

BLOCKED_NAMES = frozenset(
    {
        "eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars", "getattr",
        "setattr", "delattr", "input", "breakpoint", "exit", "quit", "help", "memoryview",
    }
)
# Methods that read or write files, databases, the clipboard or the network.
BLOCKED_ATTRIBUTES = frozenset(
    {
        "to_csv", "to_excel", "to_parquet", "to_pickle", "to_sql", "to_hdf", "to_feather",
        "to_stata", "to_clipboard", "to_orc", "to_gbq", "read_csv", "read_excel", "read_parquet",
        "read_pickle", "read_sql", "read_sql_query", "read_sql_table", "read_hdf", "read_feather",
        "read_stata", "read_clipboard", "read_html", "read_xml", "read_json", "read_table",
        "read_fwf", "read_sas", "read_spss", "read_orc", "read_gbq", "savefig", "write_html",
        "write_image", "write_json", "save", "savez", "savez_compressed", "savetxt", "load",
        "loadtxt", "genfromtxt", "fromfile", "tofile", "memmap", "system", "popen",
    }
)
# These write to disk only when given a path/buffer; without arguments they return text.
WRITE_IF_TARGETED = frozenset({"to_json", "to_html", "to_latex", "to_markdown", "to_string", "to_xml"})
_DUNDER_STRING = re.compile(r"__\w+__")


class CodeValidationError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def available_modules() -> list[str]:
    """Allowlisted modules that are actually installed (shown to the model)."""
    return sorted(m for m in ALLOWED_MODULES if importlib.util.find_spec(m) is not None)


def validate_code(code: str) -> list[str]:
    """Return a list of policy violations (empty when the code may run)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"SyntaxError on line {exc.lineno}: {exc.msg}"]

    problems: list[str] = []

    def add(node: ast.AST, msg: str) -> None:
        problems.append(f"line {getattr(node, 'lineno', '?')}: {msg}")

    def check_module(node: ast.AST, name: str) -> None:
        if name.split(".")[0] not in ALLOWED_MODULES:
            add(node, f"import of '{name}' is not allowed")
        elif any(name == b or name.startswith(b + ".") for b in BLOCKED_SUBMODULES):
            add(node, f"import of '{name}' is not allowed")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                check_module(node, alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                add(node, "relative imports are not allowed")
                continue
            check_module(node, node.module or "")
            for alias in node.names:
                if alias.name == "*":
                    add(node, "wildcard imports are not allowed")
                elif alias.name in BLOCKED_ATTRIBUTES:
                    add(node, f"'{alias.name}' is disabled: the dataset is already loaded as `df`")
        elif isinstance(node, ast.Name):
            if node.id in BLOCKED_NAMES:
                add(node, f"use of '{node.id}' is not allowed")
            elif node.id.startswith("__") and node.id.endswith("__"):
                add(node, f"use of '{node.id}' is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                add(node, f"access to '{node.attr}' is not allowed")
            elif node.attr in BLOCKED_ATTRIBUTES:
                add(node, f"'.{node.attr}()' is disabled: no file or network I/O; use show() to output results")
        elif isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in WRITE_IF_TARGETED
                and (node.args or any(k.arg in ("path_or_buf", "buf") for k in node.keywords))
            ):
                add(node, f"'.{func.attr}()' may not write to a file; call it without a path")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _DUNDER_STRING.search(node.value):
                add(node, "string literals naming dunder attributes are not allowed")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            add(node, "global/nonlocal statements are not allowed")

    # Deduplicate while keeping order.
    return list(dict.fromkeys(problems))


# --------------------------------------------------------------------------- results


@dataclass
class Artifact:
    """A displayable output (figure, table, image, value or text) from one execution."""

    kind: str
    title: str | None
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    explicit: bool = True  # False for an auto-echoed trailing expression

    def to_plotly(self):
        import plotly.io as pio

        return pio.from_json(self.payload["figure_json"])

    def to_dataframe(self) -> pd.DataFrame:
        return pd.read_json(io.StringIO(self.payload["table_json"]), orient="split", convert_dates=False)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "title": self.title, "summary": self.summary,
                "payload": self.payload, "explicit": self.explicit}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Artifact":
        return cls(kind=data["kind"], title=data.get("title"), summary=data.get("summary", ""),
                   payload=data.get("payload", {}), explicit=data.get("explicit", True))


@dataclass
class ExecutionResult:
    ok: bool
    stdout: str = ""
    stdout_truncated: bool = False
    error_type: str | None = None
    error_message: str | None = None
    traceback: str | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    rejected: bool = False  # failed static validation and never ran
    timed_out: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = {k: v for k, v in self.__dict__.items() if k != "artifacts"}
        data["artifacts"] = [a.to_dict() for a in self.artifacts]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecutionResult":
        data = dict(data)
        artifacts = [Artifact.from_dict(a) for a in data.pop("artifacts", [])]
        return cls(artifacts=artifacts, **data)


_SENSITIVE_ENV = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH", re.IGNORECASE)


class Sandbox:
    """Runs analysis code against a DataFrame in short-lived worker processes."""

    def __init__(self, config: SandboxConfig | None = None, seed: int = 0, workspace_root: str | None = None):
        self.config = config or SandboxConfig()
        self.seed = seed
        # When set, code may read (never write) files under this folder via read_text()/load_table().
        self.workspace_root = str(Path(workspace_root).resolve()) if workspace_root else None
        self._dir = Path(tempfile.mkdtemp(prefix="analyst_sandbox_"))
        self._data_path: Path | None = None
        self._data_key: str | None = None
        self.allowed_modules = available_modules()

    def set_dataframe(self, df: pd.DataFrame, key: str | None = None) -> None:
        """Stage the dataset for workers. A matching `key` skips re-writing the same data."""
        if key is not None and key == self._data_key and self._data_path is not None:
            return
        path = self._dir / "data.pkl"
        df.to_pickle(path)
        self._data_path = path
        self._data_key = key

    def run(self, code: str) -> ExecutionResult:
        if self._data_path is None and self.workspace_root is None:
            raise RuntimeError("No dataset or workspace loaded into the sandbox.")
        problems = validate_code(code)
        if problems:
            return ExecutionResult(
                ok=False,
                rejected=True,
                error_type="PolicyViolation",
                error_message="Code was rejected before running: " + "; ".join(problems),
            )

        job_dir = self._dir / f"job_{uuid.uuid4().hex[:12]}"
        job_dir.mkdir()
        try:
            return self._run_job(job_dir, code)
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    def _run_job(self, job_dir: Path, code: str) -> ExecutionResult:
        cfg = self.config
        job = {
            "code": code,
            "data_path": str(self._data_path) if self._data_path else None,
            "workspace_root": self.workspace_root,
            "allowed_modules": self.allowed_modules,
            "seed": self.seed,
            "limits": {
                "timeout_seconds": cfg.timeout_seconds,
                "memory_limit_mb": cfg.memory_limit_mb,
                "max_stdout_chars": cfg.max_stdout_chars,
                "max_table_rows": cfg.max_table_rows,
                "max_artifacts": cfg.max_artifacts,
            },
        }
        (job_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")

        env = {k: v for k, v in os.environ.items() if not _SENSITIVE_ENV.search(k)}
        env.update(
            MPLBACKEND="Agg",
            MPLCONFIGDIR=str(job_dir),
            PYTHONHASHSEED=str(self.seed),
            PYTHONDONTWRITEBYTECODE="1",
            # Single-threaded BLAS keeps numerics reproducible and virtual memory bounded.
            OMP_NUM_THREADS="1",
            OPENBLAS_NUM_THREADS="1",
            MKL_NUM_THREADS="1",
        )
        start = time.perf_counter()
        try:
            proc = subprocess.run(
                [sys.executable, str(WORKER_PATH), str(job_dir)],
                cwd=job_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=cfg.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(
                ok=False,
                timed_out=True,
                error_type="Timeout",
                error_message=(
                    f"Execution exceeded {cfg.timeout_seconds:.0f}s and was stopped. "
                    "Use vectorized pandas operations, sample or aggregate the data first."
                ),
                duration_s=time.perf_counter() - start,
            )

        result_path = job_dir / "result.json"
        if not result_path.exists():
            # The worker died without reporting (killed by a resource limit, crash, ...).
            detail = (proc.stderr or "").strip()[-1500:]
            reason = f"exit code {proc.returncode}"
            if proc.returncode in (-9, -24, 137, 152):
                reason += " (likely killed for exceeding the CPU or memory limit)"
            return ExecutionResult(
                ok=False,
                error_type="WorkerCrash",
                error_message=f"The execution process terminated unexpectedly: {reason}. {detail}".strip(),
                duration_s=time.perf_counter() - start,
            )

        data = json.loads(result_path.read_text(encoding="utf-8"))
        error = data.get("error") or {}
        artifacts = [
            Artifact(
                kind=item["kind"],
                title=item.get("title"),
                summary=item.get("summary", ""),
                payload={k: v for k, v in item.items() if k not in ("kind", "title", "summary", "explicit")},
                explicit=bool(item.get("explicit", True)),
            )
            for item in data.get("artifacts", [])
        ]
        return ExecutionResult(
            ok=bool(data.get("ok")),
            stdout=data.get("stdout", ""),
            stdout_truncated=bool(data.get("stdout_truncated")),
            error_type=error.get("type"),
            error_message=error.get("message"),
            traceback=error.get("traceback"),
            artifacts=artifacts,
            warnings=data.get("warnings", []),
            duration_s=data.get("duration_s", time.perf_counter() - start),
        )

    def close(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)

    def __enter__(self) -> "Sandbox":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self) -> None:  # best effort; Streamlit sessions are not closed explicitly
        try:
            self.close()
        except Exception:
            pass
