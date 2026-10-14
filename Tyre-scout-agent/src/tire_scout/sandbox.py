"""Sandboxed Python execution via an unprivileged Docker container.

Isolation boundary: model-generated code runs ONLY inside a container with
- no network (--network none), no mail, no credentials, no env secrets
- all Linux capabilities dropped, no-new-privileges, non-root user
- read-only root FS; only /work/in (read-only staged inputs) and /work/out
  (job-specific, disposable) are writable
- wall-clock timeout, memory, CPU, PID-count, tmpfs and output-size limits
- a prepared image; runtime package installation is impossible (no network,
  read-only FS) and additionally guarded in the job wrapper

If Docker is unavailable the executor reports unavailable and the
`run_python_analysis` tool is DISABLED - generated code never falls back to
host execution.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .config import SandboxConfig


class SandboxUnavailable(Exception):
    pass


@dataclass
class Artifact:
    relpath: str
    size: int


@dataclass
class SandboxResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    artifacts: list[Artifact] = field(default_factory=list)
    error: str | None = None
    timed_out: bool = False
    output_dir: Path | None = None


JOB_WRAPPER = r'''
import sys, json, pathlib
out = pathlib.Path("/work/out"); out.mkdir(exist_ok=True)
def _no_pip(*a, **k):
    raise RuntimeError("runtime package installation is prohibited in the sandbox")
sys.modules.setdefault("pip", None)
import importlib.abc, importlib.machinery
class _BlockPip(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "pip" or name.startswith("pip."):
            raise RuntimeError("pip is disabled inside the sandbox")
sys.meta_path.insert(0, _BlockPip())
try:
    exec(compile(open("/work/in/job.py", "rb").read(), "job.py", "exec"),
         {"__name__": "__main__"})
    print(json.dumps({"status": "ok"}))
except Exception as exc:
    print(json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"}))
    sys.exit(1)
'''


class SandboxExecutor:
    def __init__(self, cfg: SandboxConfig, runner=subprocess.run):
        self.cfg = cfg
        self._runner = runner  # injectable for tests

    # -- availability -------------------------------------------------------- #
    def available(self) -> tuple[bool, str]:
        if shutil.which("docker") is None:
            return False, "docker CLI not found on PATH"
        try:
            proc = self._runner(["docker", "info", "--format", "{{.ServerVersion}}"],
                                capture_output=True, text=True, timeout=15)
        except Exception as exc:
            return False, f"docker info failed: {exc}"
        if proc.returncode != 0:
            return False, f"docker daemon not reachable: {proc.stderr.strip()[:200]}"
        return True, f"docker server {proc.stdout.strip()}"

    def ensure_image(self) -> tuple[bool, str]:
        ok, msg = self.available()
        if not ok:
            return ok, msg
        proc = self._runner(["docker", "image", "inspect", self.cfg.image],
                            capture_output=True, text=True, timeout=15)
        if proc.returncode != 0:
            return False, (f"sandbox image {self.cfg.image!r} not built - run "
                           f"scripts/build_sandbox_image.ps1 first")
        return True, "image present"

    # -- command construction (pure; unit-tested) ------------------------------ #
    def build_command(self, in_dir: Path, out_dir: Path, cid_file: Path) -> list[str]:
        L = self.cfg.limits
        return [
            "docker", "run", "--rm",
            "--name", f"tire-scout-job-{uuid.uuid4().hex[:12]}",
            "--cidfile", str(cid_file),
            "--network", "none",                       # no network at all
            "--memory", f"{L.memory_mb}m",
            "--memory-swap", f"{L.memory_mb}m",
            "--cpus", str(L.cpus),
            "--pids-limit", str(L.pids),
            "--read-only",
            "--tmpfs", f"/tmp:rw,noexec,nosuid,size={L.tmpfs_mb}m",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--user", "65534:65534",                   # nobody
            "--env", "HOME=/tmp",
            "-v", f"{in_dir}:/work/in:ro",
            "-v", f"{out_dir}:/work/out:rw",
            self.cfg.image,
            "python", "/work/in/wrapper.py",
        ]

    # -- execution -------------------------------------------------------------- #
    def run(self, *, code: str, inputs: dict[str, str | bytes] | None = None) -> SandboxResult:
        if not self.cfg.enabled:
            return SandboxResult(ok=False, error="sandbox disabled in configuration")
        ok, msg = self.ensure_image()
        if not ok:
            raise SandboxUnavailable(msg)
        base = Path(tempfile.mkdtemp(prefix="tire-scout-job-"))
        in_dir, out_dir = base / "in", base / "out"
        in_dir.mkdir(); out_dir.mkdir()
        (in_dir / "job.py").write_text(code, encoding="utf-8")
        (in_dir / "wrapper.py").write_text(JOB_WRAPPER, encoding="utf-8")
        for name, payload in (inputs or {}).items():
            safe = Path(name).name
            data = payload.encode() if isinstance(payload, str) else payload
            (in_dir / safe).write_bytes(data)
        cid_file = base / "cid"
        cmd = self.build_command(in_dir, out_dir, cid_file)
        try:
            proc = self._runner(cmd, capture_output=True, text=True,
                                timeout=self.cfg.limits.timeout_s)
            timed_out = False
            stdout, stderr, rc = proc.stdout or "", proc.stderr or "", proc.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            stderr = "wall-clock timeout exceeded"
            rc = -1
            self._kill_container(cid_file)
        artifacts, art_err = self._collect_artifacts(out_dir)
        ok_run = (rc == 0 and not timed_out and art_err is None)
        return SandboxResult(ok=ok_run, stdout=stdout[-8000:], stderr=stderr[-4000:],
                             artifacts=artifacts,
                             error=None if ok_run else (art_err or stderr.strip() or f"exit {rc}"),
                             timed_out=timed_out, output_dir=out_dir)

    def _kill_container(self, cid_file: Path) -> None:
        try:
            if cid_file.exists():
                cid = cid_file.read_text().strip()
                if cid:
                    self._runner(["docker", "rm", "-f", cid],
                                 capture_output=True, text=True, timeout=15)
        except Exception:
            pass

    # -- artifact validation ------------------------------------------------------ #
    def _collect_artifacts(self, out_dir: Path) -> tuple[list[Artifact], str | None]:
        L = self.cfg.limits
        artifacts: list[Artifact] = []
        total = 0
        root = out_dir.resolve()
        for path in sorted(out_dir.rglob("*")):
            if path.is_dir():
                continue
            if path.is_symlink():
                return artifacts, f"symlink artifact rejected: {path.name}"
            real = path.resolve()
            if not real.is_relative_to(root):
                return artifacts, f"path traversal artifact rejected: {path}"
            if path.suffix.lower() not in {e.lower() for e in L.allowed_artifact_exts}:
                return artifacts, f"artifact type not allowed: {path.name}"
            size = path.stat().st_size
            if size > L.output_file_kb * 1024:
                return artifacts, f"artifact too large: {path.name} ({size} bytes)"
            total += size
            if total > L.output_total_kb * 1024:
                return artifacts, "total artifact size limit exceeded"
            artifacts.append(Artifact(relpath=str(path.relative_to(out_dir)), size=size))
        return artifacts, None
