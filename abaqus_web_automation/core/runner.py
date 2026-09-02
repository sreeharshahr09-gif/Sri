from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_LOCK = threading.Lock()

# Markers the generated scripts print on success.  Abaqus CAE in noGUI mode does
# not reliably return a non-zero exit code when the script raises, so the exit
# code alone is not proof that a case actually completed.
BUILD_MARKER = "AUTOMATION_JOB_COMPLETE"
EXTRACT_MARKER = "ODB_EXTRACTION_COMPLETE"


def _status_path(gen_dir: Path) -> Path:
    return gen_dir / "run_status.json"


def load_status(gen_dir: Path) -> Dict[str, Any]:
    p = _status_path(gen_dir)
    if not p.exists():
        return {"state": "not_started", "cases": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        # A read that lands mid-write should not 500 the status page.
        return {"state": "unknown", "cases": [], "note": "status file is being updated"}


def _write_status(gen_dir: Path, status: Dict[str, Any]) -> None:
    with _LOCK:
        tmp = _status_path(gen_dir).with_suffix(".tmp")
        tmp.write_text(json.dumps(status, indent=2), encoding="utf-8")
        tmp.replace(_status_path(gen_dir))


def resolve_command(abaqus_cmd: str) -> Tuple[Optional[List[str]], Optional[str]]:
    """Split the configured launcher into argv and resolve it on PATH.

    v0.6 built a single shell string on Windows and ran it with ``shell=True``,
    which both quoted ``noGUI=<path>`` as one token (breaking install paths that
    contain a space) and passed user text to the shell.  Resolving the
    executable with ``shutil.which`` honours PATHEXT, so ``abaqus`` still finds
    ``abaqus.bat`` without a shell.
    """
    try:
        parts = shlex.split(abaqus_cmd, posix=(os.name != "nt"))
    except ValueError as e:
        return None, "Could not parse the Abaqus command: %s" % e
    parts = [p.strip('"') for p in parts if p.strip()]
    if not parts:
        return None, "No Abaqus command configured."
    exe = shutil.which(parts[0])
    if not exe:
        return None, (
            "Abaqus launcher '%s' was not found on PATH. Enter the full path to the "
            "launcher (for example C:\\SIMULIA\\Commands\\abaqus.bat)." % parts[0]
        )
    return [exe] + parts[1:], None


def _run_one(cmd: List[str], cwd: Path, log_path: Path) -> int:
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        log.write("command: %s\n\n" % " ".join(cmd))
        log.flush()
        p = subprocess.Popen(cmd, cwd=str(cwd), stdout=log, stderr=subprocess.STDOUT)
        return p.wait()


def _log_has(log_path: Path, marker: str) -> bool:
    try:
        return marker in log_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False


def _tail(log_path: Path, lines: int = 15) -> str:
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except Exception:
        return ""
    return "\n".join(text[-lines:])


def run_generation(gen_dir: Path, abaqus_cmd: str = "abaqus") -> None:
    status: Dict[str, Any] = load_status(gen_dir)
    try:
        base, err = resolve_command(abaqus_cmd)
        if err:
            status.update({"state": "failed", "error": err, "finished": time.time()})
            _write_status(gen_dir, status)
            return

        manifest = json.loads((gen_dir / "manifest.json").read_text(encoding="utf-8"))
        status = {
            "state": "running",
            "started": time.time(),
            "abaqus_command": abaqus_cmd,
            "resolved_command": base[0],
            "cases": [{"name": c["name"], "state": "queued"} for c in manifest["cases"]],
        }
        _write_status(gen_dir, status)

        for i, case in enumerate(manifest["cases"]):
            case_dir = gen_dir / case["name"]
            entry = status["cases"][i]
            entry["state"] = "solving"
            _write_status(gen_dir, status)

            build_log = case_dir / "abaqus_run.log"
            try:
                rc = _run_one(base + ["cae", "noGUI=%s" % (case_dir / "build_and_run.py")], case_dir, build_log)
            except Exception as e:
                entry.update({"state": "failed", "error": str(e)})
                _write_status(gen_dir, status)
                continue
            entry["solver_return_code"] = rc
            if rc != 0 or not _log_has(build_log, BUILD_MARKER):
                entry["state"] = "failed"
                entry["error"] = (
                    "The Abaqus build/solve script did not report completion. "
                    "See abaqus_run.log in %s." % case["name"]
                )
                entry["log_tail"] = _tail(build_log)
                _write_status(gen_dir, status)
                continue

            entry["state"] = "extracting"
            _write_status(gen_dir, status)
            extract_log = case_dir / "odb_extract.log"
            try:
                rc2 = _run_one(base + ["python", str(case_dir / "extract_odb.py")], case_dir, extract_log)
            except Exception as e:
                entry.update({"state": "failed_extract", "error": str(e)})
                _write_status(gen_dir, status)
                continue
            entry["extract_return_code"] = rc2
            if rc2 == 0 and _log_has(extract_log, EXTRACT_MARKER):
                entry["state"] = "complete"
            else:
                entry["state"] = "failed_extract"
                entry["error"] = "ODB extraction did not report completion. See odb_extract.log in %s." % case["name"]
                entry["log_tail"] = _tail(extract_log)
            _write_status(gen_dir, status)

        failed = any(str(c.get("state", "")).startswith("failed") for c in status["cases"])
        status["state"] = "completed_with_errors" if failed else "complete"
    except Exception as e:
        # Without this the worker thread died silently and the status file stayed
        # at "not_started" forever while the UI waited for a result.
        status["state"] = "failed"
        status["error"] = "%s: %s" % (type(e).__name__, e)
        status["traceback"] = traceback.format_exc()
    finally:
        status["finished"] = time.time()
        _write_status(gen_dir, status)


def start_generation(gen_dir: Path, abaqus_cmd: str = "abaqus") -> Dict[str, Any]:
    current = load_status(gen_dir)
    if current.get("state") in {"running", "starting"}:
        return current
    # Written before the thread starts: the status page is fetched immediately
    # after this returns, and a "not_started" answer there made the UI conclude
    # the run had finished before it began.
    status = {"state": "starting", "started": time.time(), "abaqus_command": abaqus_cmd, "cases": []}
    _write_status(gen_dir, status)
    threading.Thread(target=run_generation, args=(gen_dir, abaqus_cmd), daemon=True).start()
    return status


def is_active(status: Dict[str, Any]) -> bool:
    """True while the status page should keep refreshing."""
    if status.get("state") in {"starting", "running", "unknown"}:
        return True
    return any(c.get("state") in {"queued", "solving", "extracting"} for c in status.get("cases", []))
