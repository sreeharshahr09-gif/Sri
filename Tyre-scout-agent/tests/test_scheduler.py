"""Task Scheduler command construction and inspection parsing."""
from tire_scout.scheduler import (build_daily_command, build_one_time_command,
                                  build_weekly_command, inspect_task, parse_task_xml)

TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><CalendarTrigger><StartBoundary>2026-10-13T10:00:00+05:30</StartBoundary>
  <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>
  <Settings><Enabled>true</Enabled></Settings>
</Task>"""


def test_daily_command_uses_config_time(cfg):
    cmd = build_daily_command(cfg, r"C:\proj", r"C:\proj\config\config.yaml")
    s = " ".join(cmd)
    assert "/SC DAILY" in s and "/ST 10:00" in s and "TireScout-Daily" in s
    assert "daily" in s and "--mode production" in s


def test_weekly_command_is_monday_1100(cfg):
    cfg.schedule.weekly_time = "11:00"
    cmd = build_weekly_command(cfg, r"C:\proj", r"C:\proj\config\config.yaml")
    s = " ".join(cmd)
    assert "/SC WEEKLY" in s and "/D MON" in s and "/ST 11:00" in s


def test_one_time_override_is_once_not_recurring(cfg):
    cmd = build_one_time_command("2026-10-15T18:00:00", r"C:\proj",
                                 r"C:\proj\config\config.yaml", "weekly", "TireScout-Once-1")
    s = " ".join(cmd)
    assert "/SC ONCE" in s and "/SD 10/15/2026" in s and "/ST 18:00" in s
    assert "DAILY" not in s and "WEEKLY" not in s


def test_parse_task_xml_extracts_next_run_and_enabled():
    info = parse_task_xml("TireScout-Daily", TASK_XML)
    assert info.exists and info.status == "enabled"
    assert info.next_run == "2026-10-13T10:00:00+05:30"


def test_inspect_reports_unsupported_off_windows():
    info = inspect_task("TireScout-Daily")
    assert not info.exists and info.status in ("unsupported-host", "not-found")
