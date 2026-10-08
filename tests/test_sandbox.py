import json
import sys

import pandas as pd
import pytest

from analyst_agent.config import SandboxConfig
from analyst_agent.sandbox import Sandbox, validate_code


@pytest.mark.parametrize(
    "code",
    [
        "import os",
        "import subprocess",
        "from os import path",
        "import socket",
        "__import__('os')",
        "eval('1+1')",
        "exec('x=1')",
        "open('/etc/passwd').read()",
        "getattr(df, 'to_csv')('x.csv')",
        "().__class__.__base__.__subclasses__()",
        "df.to_csv('out.csv')",
        "pd.read_csv('/etc/passwd')",
        "from pandas import read_csv",
        "df.to_json('out.json')",
        "'{0.__class__}'.format(df)",
        "import numpy.ctypeslib",
        "from . import x",
        "from math import *",
        "global df",
    ],
)
def test_validator_rejects_dangerous_code(code):
    assert validate_code(code), code


@pytest.mark.parametrize(
    "code",
    [
        "import pandas as pd\nprint(df.groupby('region')['sales'].sum())",
        "import plotly.express as px\nfig = px.bar(df, x='region', y='sales')\nshow(fig)",
        "from scipy import stats\nstats.ttest_ind([1, 2, 3], [4, 5, 6])",
        "s = df.to_json()\nt = df.to_string()",
        "result = sorted({k: v for k, v in zip('ab', [1, 2])}.items(), key=lambda kv: kv[1])",
        "class Box:\n    def __init__(self, v):\n        self.v = v\nprint(Box(1).v)",
    ],
)
def test_validator_accepts_normal_analysis_code(code):
    assert validate_code(code) == []


def test_syntax_error_reported():
    problems = validate_code("print(")
    assert problems and "SyntaxError" in problems[0]


def test_run_collects_outputs(sandbox, sales_df):
    sandbox.set_dataframe(sales_df)
    code = (
        "import plotly.express as px\n"
        "totals = df.groupby('region', as_index=False)['sales'].sum()\n"
        "print('rows', len(df))\n"
        "show(totals, title='Sales by region')\n"
        "fig = px.bar(totals, x='region', y='sales', title='Sales')\n"
        "round(df['sales'].mean(), 2)"
    )
    result = sandbox.run(code)
    assert result.ok, result.traceback
    assert "rows 6" in result.stdout
    kinds = [(a.kind, a.explicit) for a in result.artifacts]
    assert kinds == [("table", True), ("value", False), ("plotly", True)]
    table = result.artifacts[0].to_dataframe()
    assert table.set_index("region")["sales"].to_dict() == {"East": 370.5, "North": 80.0, "West": 450.0}
    assert result.artifacts[1].payload["text"] == "150.08"
    assert result.artifacts[2].to_plotly().layout.title.text == "Sales"


def test_dataframe_is_fresh_each_run(sandbox, sales_df):
    sandbox.set_dataframe(sales_df)
    assert sandbox.run("df['sales'] = 0\nprint(df['sales'].sum())").ok
    result = sandbox.run("print(df['sales'].sum())")
    assert result.stdout.strip() == "900.5"


def test_runtime_error_has_analysis_traceback(sandbox, sales_df):
    sandbox.set_dataframe(sales_df)
    result = sandbox.run("x = 1\ndf['missing_column']")
    assert not result.ok
    assert result.error_type == "KeyError"
    assert '"<analysis>", line 2' in result.traceback.replace("'", '"')


def test_rejected_code_never_runs(sandbox, sales_df):
    sandbox.set_dataframe(sales_df)
    result = sandbox.run("import os\nos.remove('x')")
    assert result.rejected and not result.ok
    assert "os" in result.error_message


def test_runtime_import_guard_is_independent_of_static_checks(sandbox, sales_df, monkeypatch):
    # Defense in depth: even if static validation missed something, the worker refuses the import.
    monkeypatch.setattr("analyst_agent.sandbox.validate_code", lambda code: [])
    sandbox.set_dataframe(sales_df)
    result = sandbox.run("import os")
    assert not result.ok and result.error_type == "ImportError"
    result = sandbox.run("open('x.txt', 'w')")
    assert not result.ok and result.error_type == "NameError"


@pytest.mark.skipif(sys.platform == "win32", reason="rlimits are POSIX-only")
def test_memory_limit_is_enforced(sales_df):
    with Sandbox(SandboxConfig(timeout_seconds=60, memory_limit_mb=1024)) as sb:
        sb.set_dataframe(sales_df)
        result = sb.run("x = np.ones((4096, 1024, 1024), dtype='uint8')\nprint(x.sum())")
    assert not result.ok
    assert result.error_type in ("MemoryError", "WorkerCrash", "_ArrayMemoryError")


def test_timeout_kills_runaway_code(sales_df):
    with Sandbox(SandboxConfig(timeout_seconds=3, memory_limit_mb=0)) as sb:
        sb.set_dataframe(sales_df)
        result = sb.run("while True:\n    pass")
    assert result.timed_out and not result.ok
    assert result.error_type == "Timeout"


def test_stdout_is_capped(sales_df):
    with Sandbox(SandboxConfig(timeout_seconds=30, memory_limit_mb=0, max_stdout_chars=100)) as sb:
        sb.set_dataframe(sales_df)
        result = sb.run("for i in range(1000):\n    print('line', i)")
    assert result.ok
    assert result.stdout_truncated
    assert len(result.stdout) <= 100


def test_large_table_is_truncated_but_row_count_kept(sales_df):
    with Sandbox(SandboxConfig(timeout_seconds=30, memory_limit_mb=0, max_table_rows=10)) as sb:
        sb.set_dataframe(sales_df)
        result = sb.run("show(pd.DataFrame({'x': range(1000)}))")
    art = result.artifacts[0]
    assert art.payload["rows"] == 1000 and art.payload["truncated"]
    assert len(art.to_dataframe()) == 10


def test_fig_show_and_matplotlib_are_captured(sandbox, sales_df):
    pytest.importorskip("matplotlib")
    sandbox.set_dataframe(sales_df)
    code = (
        "import matplotlib.pyplot as plt\n"
        "fig2 = px.line(df, x='date', y='sales')\nfig2.show()\n"
        "f, ax = plt.subplots()\nax.hist(df['sales'])\nax.set_title('Histogram')\nplt.show()"
    )
    result = sandbox.run(code)
    assert result.ok, result.traceback
    assert sorted(a.kind for a in result.artifacts) == ["image", "plotly"]


def test_numpy_scalars_render_cleanly(sandbox, sales_df):
    sandbox.set_dataframe(sales_df)
    result = sandbox.run("result = {'mean': df['sales'].mean(), 'n': df['units'].sum()}")
    assert result.artifacts[0].payload["text"] == "{'mean': 150.08333333333334, 'n': 12}"


def test_groupby_multiindex_result_is_flattened(sandbox, sales_df):
    sandbox.set_dataframe(sales_df)
    result = sandbox.run("show(df.groupby(['region', 'units'])['sales'].agg(['sum', 'mean']))")
    table = result.artifacts[0].to_dataframe()
    assert list(table.columns) == ["region", "units", "sum", "mean"]


def test_secrets_not_exposed_to_worker(sandbox, sales_df, monkeypatch):
    import analyst_agent.sandbox as sandbox_module

    monkeypatch.setenv("LLM_API_KEY", "super-secret")
    monkeypatch.setenv("SOME_TOKEN", "t")
    seen = {}
    real_run = sandbox_module.subprocess.run

    def spy(*args, **kwargs):
        seen.update(kwargs["env"])
        return real_run(*args, **kwargs)

    monkeypatch.setattr(sandbox_module.subprocess, "run", spy)
    sandbox.set_dataframe(sales_df)
    assert sandbox.run("print('ok')").ok
    assert "LLM_API_KEY" not in seen and "SOME_TOKEN" not in seen
    assert seen["MPLBACKEND"] == "Agg"


def test_result_is_json_round_trippable(sandbox, sales_df):
    sandbox.set_dataframe(sales_df)
    result = sandbox.run("show(df.describe())\nshow('**bold**')")
    from analyst_agent.sandbox import ExecutionResult

    restored = ExecutionResult.from_dict(json.loads(json.dumps(result.to_dict())))
    assert [a.kind for a in restored.artifacts] == ["table", "text"]
    assert isinstance(restored.artifacts[0].to_dataframe(), pd.DataFrame)
