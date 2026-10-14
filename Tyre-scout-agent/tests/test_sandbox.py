"""Sandbox isolation: command construction, fail-closed behaviour, artifact
validation, timeout handling. Uses an injected runner; no Docker required."""
import subprocess
from pathlib import Path

import pytest

from tire_scout.sandbox import SandboxExecutor, SandboxUnavailable
from tire_scout.tools import AgentTools, AnalysisIn


def test_command_has_full_isolation_flags(cfg, tmp_path):
    ex = SandboxExecutor(cfg.sandbox)
    cmd = ex.build_command(tmp_path / "in", tmp_path / "out", tmp_path / "cid")
    s = " ".join(cmd)
    assert "--network none" in s
    assert "--read-only" in s
    assert "--cap-drop ALL" in s
    assert "--security-opt no-new-privileges" in s
    assert "--user 65534:65534" in s
    assert "--memory 512m" in s and "--memory-swap 512m" in s
    assert "--pids-limit 64" in s
    assert ":ro" in s                       # staged inputs read-only
    assert "--tmpfs /tmp:rw,noexec,nosuid" in s
    assert cfg.sandbox.image in cmd


def test_unavailable_when_docker_missing(cfg, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    ex = SandboxExecutor(cfg.sandbox)
    ok, msg = ex.available()
    assert not ok and "docker" in msg


def test_generated_code_never_runs_on_host(cfg, tmp_path, monkeypatch):
    """Fail closed: when the sandbox is unavailable the tool refuses and the
    code has no effect on the host."""
    monkeypatch.setattr("shutil.which", lambda _: None)
    ex = SandboxExecutor(cfg.sandbox)
    marker = tmp_path / "host_marker.txt"
    code = f"open(r'{marker}', 'w').write('pwned')"

    class FakeCompleted:
        returncode = 1
        stdout = ""
        stderr = ""

    monkeypatch.setattr(ex, "_runner", lambda *a, **k: FakeCompleted())
    with pytest.raises(SandboxUnavailable):
        ex.run(code=code)
    assert not marker.exists()


def test_tool_reports_disabled_instead_of_executing(cfg, db, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    from conftest import fake_resolve, fake_transport, make_pages
    from tire_scout.fetch import Fetcher
    from tire_scout.sandbox import SandboxExecutor
    from tire_scout.search.fixture import FixtureSearchAdapter
    tools = AgentTools(cfg=cfg, db=db, search=FixtureSearchAdapter(cfg),
                       fetcher=Fetcher(resolve=fake_resolve,
                                       transport=fake_transport(make_pages())),
                       sandbox=SandboxExecutor(cfg.sandbox))
    out = tools.run_python_analysis(AnalysisIn(code="print('hi')"))
    assert out["ok"] is False and "refused" in out["error"]


def test_artifact_symlink_rejected(cfg, tmp_path):
    ex = SandboxExecutor(cfg.sandbox)
    out = tmp_path / "out"
    out.mkdir()
    target = tmp_path / "secret.txt"
    target.write_text("secret")
    try:
        (out / "link.csv").symlink_to(target)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows account lacks permission to create symlinks")
        raise
    _, err = ex._collect_artifacts(out)
    assert err and "symlink" in err


def test_artifact_size_and_type_limits(cfg, tmp_path):
    ex = SandboxExecutor(cfg.sandbox)
    out = tmp_path / "out"
    out.mkdir()
    (out / "big.csv").write_bytes(b"x" * (cfg.sandbox.limits.output_file_kb * 1024 + 10))
    _, err = ex._collect_artifacts(out)
    assert err and "too large" in err
    (out / "big.csv").unlink()
    (out / "evil.exe").write_bytes(b"x")
    _, err = ex._collect_artifacts(out)
    assert err and "not allowed" in err


def test_valid_nested_artifact_collected(cfg, tmp_path):
    ex = SandboxExecutor(cfg.sandbox)
    out = tmp_path / "out"
    nested = out / "results"
    nested.mkdir(parents=True)
    (nested / "summary.csv").write_bytes(b"score\n5\n")
    artifacts, err = ex._collect_artifacts(out)
    assert err is None
    assert [(Path(a.relpath), a.size) for a in artifacts] == [
        (Path("results") / "summary.csv", 8)]


def test_path_traversal_artifact_rejected(cfg, tmp_path, monkeypatch):
    ex = SandboxExecutor(cfg.sandbox)
    out = tmp_path / "out"
    out.mkdir()
    # Simulate a resolved escape without requiring Windows symlink privileges.
    # The sibling deliberately shares the output directory's string prefix.
    sibling = tmp_path / "out-sibling"
    sibling.mkdir()
    outside = sibling / "outside.csv"
    outside.write_text("x")
    candidate = out / "traversal.csv"
    candidate.write_text("x")
    original_resolve = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **kw:
                        outside if self == candidate else original_resolve(self, *a, **kw))
    _, err = ex._collect_artifacts(out)
    assert err and "path traversal" in err


def test_timeout_kills_container(cfg, tmp_path, monkeypatch):
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        if "rm" in cmd:
            class P:
                returncode = 0
                stdout = ""
                stderr = ""
            return P()
        # emulate docker writing the cidfile then hanging past the timeout
        Path(cmd[cmd.index("--cidfile") + 1]).write_text("deadbeefcafe")
        raise subprocess.TimeoutExpired(cmd, timeout=1)

    ex = SandboxExecutor(cfg.sandbox, runner=runner)
    monkeypatch.setattr(ex, "ensure_image", lambda: (True, "ok"))
    # Keep this isolated unit test's temporary job under pytest's writable root.
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    res = ex.run(code="while True: pass")
    assert res.timed_out and not res.ok
    assert any("rm" in c and "-f" in c for c in calls)
