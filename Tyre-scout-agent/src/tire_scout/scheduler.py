"""Windows Task Scheduler integration.

The authoritative schedule lives in config.yaml; this module renders the exact
schtasks commands, installs/removes tasks, and INSPECTS the installed task
list - configuration alone never proves a task exists or when it next runs.

On non-Windows hosts every operation reports unsupported and prints the
commands that would run, so behaviour is testable and auditable anywhere.
"""
from __future__ import annotations

import platform
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass

DAILY_TASK = "TireScout-Daily"
WEEKLY_TASK = "TireScout-Weekly"


@dataclass
class TaskInfo:
    name: str
    exists: bool
    next_run: str | None = None
    status: str | None = None
    raw: str = ""


def _run_cmd(project_dir: str, config_path: str, subcommand: str) -> str:
    return (f'cmd /c "cd /d {project_dir} && '
            f'python -m tire_scout.cli {subcommand} --config {config_path} --mode production"')


def build_daily_command(cfg, project_dir: str, config_path: str) -> list[str]:
    return ["schtasks", "/Create", "/TN", DAILY_TASK, "/F", "/SC", "DAILY",
            "/ST", cfg.schedule.daily_time,
            "/TR", _run_cmd(project_dir, config_path, "daily")]


def build_weekly_command(cfg, project_dir: str, config_path: str) -> list[str]:
    day = cfg.schedule.weekly_day[:3].upper()  # MON, TUE, ...
    return ["schtasks", "/Create", "/TN", WEEKLY_TASK, "/F", "/SC", "WEEKLY",
            "/D", day, "/ST", cfg.schedule.weekly_time,
            "/TR", _run_cmd(project_dir, config_path, "weekly")]


def build_one_time_command(run_at_local: str, project_dir: str, config_path: str,
                           job: str, name: str) -> list[str]:
    """Dated one-time override: /SC ONCE. Never creates a second recurring task."""
    dt, tm = run_at_local.split("T")
    yyyy, mm, dd = dt.split("-")
    return ["schtasks", "/Create", "/TN", name, "/F", "/SC", "ONCE",
            "/SD", f"{mm}/{dd}/{yyyy}", "/ST", tm[:5],
            "/TR", _run_cmd(project_dir, config_path, job)]


def is_windows() -> bool:
    return platform.system() == "Windows"


def install(cfg, project_dir: str, config_path: str) -> list[str]:
    """Install the daily + weekly tasks and any dated one-time overrides.
    Returns human-readable results (commands on non-Windows)."""
    cmds = [build_daily_command(cfg, project_dir, config_path),
            build_weekly_command(cfg, project_dir, config_path)]
    for i, ov in enumerate(cfg.schedule.one_time_overrides):
        cmds.append(build_one_time_command(ov.run_at, project_dir, config_path,
                                           ov.job, f"TireScout-Once-{i + 1}"))
    if not is_windows():
        return ["NON-WINDOWS HOST - not installed. Would run:\n" +
                "\n".join(" ".join(c) for c in cmds)]
    results = []
    for c in cmds:
        proc = subprocess.run(c, capture_output=True, text=True, timeout=60)
        results.append(proc.stdout.strip() or proc.stderr.strip())
    return results


def inspect_task(name: str) -> TaskInfo:
    if not is_windows():
        return TaskInfo(name=name, exists=False, status="unsupported-host")
    proc = subprocess.run(["schtasks", "/Query", "/TN", name, "/XML"],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        return TaskInfo(name=name, exists=False, status="not-found",
                        raw=proc.stderr.strip())
    return parse_task_xml(name, proc.stdout)


def parse_task_xml(name: str, xml_text: str) -> TaskInfo:
    """Extract next-run-relevant facts from schtasks /XML output."""
    info = TaskInfo(name=name, exists=True, raw=xml_text)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        info.status = "unparseable"
        return info
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    start = root.find(".//t:StartBoundary", ns)
    enabled = root.find(".//t:Enabled", ns)
    info.next_run = start.text if start is not None else None
    info.status = ("enabled" if (enabled is None or enabled.text == "true")
                   else "disabled")
    return info


def remove_all() -> list[str]:
    if not is_windows():
        return ["NON-WINDOWS HOST - nothing removed"]
    out = []
    for name in (DAILY_TASK, WEEKLY_TASK):
        proc = subprocess.run(["schtasks", "/Delete", "/TN", name, "/F"],
                              capture_output=True, text=True, timeout=60)
        out.append(proc.stdout.strip() or proc.stderr.strip())
    return out
