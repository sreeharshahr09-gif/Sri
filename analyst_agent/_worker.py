"""Sandbox worker: executes one code block against the dataset in a separate process.

Invoked as `python _worker.py <job_dir>`. Reads `<job_dir>/job.json`, writes
`<job_dir>/result.json`. Deliberately standalone (no imports from the package) so
the parent can run it with a clean interpreter. Results are returned as JSON only,
never pickle, so nothing produced by untrusted code is deserialized as code.
"""

import os
import sys

# Running a script puts its directory first on sys.path; drop it so sibling module
# names in this package can never shadow third-party imports.
if sys.path and os.path.abspath(sys.path[0]) == os.path.dirname(os.path.abspath(__file__)):
    sys.path.pop(0)

import ast  # noqa: E402
import base64  # noqa: E402
import builtins  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import random  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
import warnings  # noqa: E402
from contextlib import redirect_stderr, redirect_stdout  # noqa: E402

CODE_FILENAME = "<analysis>"

SAFE_BUILTINS = [
    "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes", "callable", "chr",
    "complex", "dict", "dir", "divmod", "enumerate", "filter", "float", "format", "frozenset",
    "hasattr", "hash", "hex", "id", "int", "isinstance", "issubclass", "iter", "len", "list",
    "map", "max", "min", "next", "object", "oct", "ord", "pow", "print", "property", "range",
    "repr", "reversed", "round", "set", "slice", "sorted", "staticmethod", "classmethod", "str",
    "sum", "super", "tuple", "type", "zip", "__build_class__",
    "ArithmeticError", "AssertionError", "AttributeError", "Exception", "IndexError",
    "KeyError", "LookupError", "NameError", "NotImplementedError", "OverflowError",
    "RuntimeError", "StopIteration", "TypeError", "ValueError", "ZeroDivisionError",
    "Warning", "UserWarning", "RuntimeWarning", "FutureWarning", "DeprecationWarning",
    "True", "False", "None", "NotImplemented", "Ellipsis",
]


class _CappedIO(io.StringIO):
    def __init__(self, limit):
        super().__init__()
        self.limit = limit
        self.truncated = False

    def write(self, s):
        remaining = self.limit - self.tell()
        if remaining <= 0:
            self.truncated = True
            return len(s)
        if len(s) > remaining:
            self.truncated = True
            super().write(s[:remaining])
            return len(s)
        return super().write(s)


def _apply_limits(limits):
    try:
        import resource
    except ImportError:  # Windows
        return
    mem_mb = int(limits.get("memory_limit_mb") or 0)
    if mem_mb > 0:
        nbytes = mem_mb * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (nbytes, nbytes))
        except (ValueError, OSError):
            pass
    cpu = int(limits.get("timeout_seconds") or 0)
    if cpu > 0:
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu + 5, cpu + 10))
        except (ValueError, OSError):
            pass


def _make_import(allowed):
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        root = name.split(".")[0]
        if level != 0 or root not in allowed:
            raise ImportError(f"Import of '{name}' is not permitted in the analysis sandbox.")
        return real_import(name, globals, locals, fromlist, level)

    return guarded_import


def _figure_summary(fig):
    try:
        types = sorted({getattr(t, "type", "?") or "?" for t in fig.data})
        title = fig.layout.title.text if fig.layout.title and fig.layout.title.text else ""
        xaxis = fig.layout.xaxis.title.text if fig.layout.xaxis and fig.layout.xaxis.title else ""
        yaxis = fig.layout.yaxis.title.text if fig.layout.yaxis and fig.layout.yaxis.title else ""
        parts = [f"Plotly figure: {len(fig.data)} trace(s) of type {', '.join(types) or 'none'}"]
        if title:
            parts.append(f"title {title!r}")
        if xaxis or yaxis:
            parts.append(f"x-axis {xaxis!r}, y-axis {yaxis!r}")
        return "; ".join(parts)
    except Exception:
        return "Plotly figure"


class ArtifactCollector:
    def __init__(self, limits):
        self.items = []
        self.seen_ids = set()
        self.max_rows = int(limits.get("max_table_rows", 5000))
        self.max_items = int(limits.get("max_artifacts", 20))
        self.dropped = 0

    def show(self, obj, title=None):
        """Display an object (figure, DataFrame, Series, scalar or text) in the final report."""
        return self.add(obj, title, explicit=True)

    def add(self, obj, title=None, explicit=True):
        # explicit=False marks notebook-style echo of a trailing expression: it is shown
        # to the model as an observation but is not a deliberate report output.
        if id(obj) in self.seen_ids and not isinstance(obj, (str, int, float, bool)):
            return None
        if len(self.items) >= self.max_items:
            self.dropped += 1
            return None
        self.seen_ids.add(id(obj))
        try:
            item = self._serialize(obj, title)
        except Exception as exc:  # never let display failures mask analysis results
            item = {"kind": "value", "title": title, "text": repr(obj)[:2000],
                    "summary": f"(could not serialize {type(obj).__name__}: {exc})"}
        item["explicit"] = explicit
        self.items.append(item)
        return None

    def _serialize(self, obj, title):
        import numpy as np
        import pandas as pd

        try:
            import plotly.graph_objects as go
            is_plotly = isinstance(obj, go.Figure)
        except ImportError:
            is_plotly = False
        if is_plotly:
            if title and not (obj.layout.title and obj.layout.title.text):
                obj.update_layout(title=title)
            return {"kind": "plotly", "title": title, "figure_json": obj.to_json(),
                    "summary": _figure_summary(obj)}

        mpl_fig = _as_matplotlib_figure(obj)
        if mpl_fig is not None:
            return _matplotlib_item(mpl_fig, title)

        if isinstance(obj, (pd.Series, pd.Index)):
            obj = obj.to_frame() if isinstance(obj, pd.Series) else obj.to_frame(index=False)
        if isinstance(obj, pd.DataFrame):
            return self._table_item(obj, title)
        if isinstance(obj, str):
            return {"kind": "text", "title": title, "text": obj[:20000], "summary": obj[:2000]}
        if isinstance(obj, np.ndarray) and obj.ndim <= 2:
            return self._table_item(pd.DataFrame(obj), title)
        obj = _plain(obj)
        text = f"{obj:.6g}" if isinstance(obj, float) else repr(obj)
        return {"kind": "value", "title": title, "text": text[:5000], "summary": text[:2000]}

    def _table_item(self, df, title):
        import pandas as pd

        df = df.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [" | ".join(str(p) for p in col if str(p)) for col in df.columns]
        else:
            df.columns = [str(c) for c in df.columns]
        if isinstance(df.index, pd.MultiIndex) or (
            df.index.name is not None and not isinstance(df.index, pd.RangeIndex)
        ):
            names = [str(n) if n is not None else f"level_{i}" for i, n in enumerate(df.index.names)]
            if not any(n in df.columns for n in names):
                df.index.names = names
                df = df.reset_index()
        n_rows, n_cols = df.shape
        shown = df.head(self.max_rows)
        try:
            payload = shown.to_json(orient="split", date_format="iso", default_handler=str)
        except Exception:
            payload = shown.astype(str).to_json(orient="split")
        with pd.option_context("display.width", 160, "display.max_columns", 20,
                               "display.max_colwidth", 30):
            preview = df.head(15).to_string(max_rows=15)
        summary = f"Table {n_rows:,} rows × {n_cols} columns"
        if n_rows > 15:
            summary += " (first 15 shown)"
        return {"kind": "table", "title": title, "table_json": payload, "rows": n_rows,
                "cols": n_cols, "truncated": n_rows > self.max_rows,
                "summary": summary + ":\n" + preview}


def _plain(obj):
    """Convert numpy scalars inside simple containers to Python values for readable reprs."""
    if isinstance(obj, dict):
        return {_plain(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)) and not hasattr(obj, "_fields"):
        return type(obj)(_plain(v) for v in obj)
    item = getattr(obj, "item", None)
    if item is not None and type(obj).__module__ == "numpy" and getattr(obj, "ndim", 1) == 0:
        return item()
    return obj


def _as_matplotlib_figure(obj):
    if "matplotlib" not in sys.modules:
        return None
    try:
        from matplotlib.axes import Axes
        from matplotlib.figure import Figure
    except ImportError:
        return None
    if isinstance(obj, Figure):
        return obj
    if isinstance(obj, Axes):
        return obj.figure
    return None


def _matplotlib_item(fig, title):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    titles = [ax.get_title() for ax in fig.axes if ax.get_title()]
    summary = f"Matplotlib figure with {len(fig.axes)} axes"
    if titles:
        summary += f"; titles {titles!r}"
    return {"kind": "image", "title": title, "png_b64": base64.b64encode(buf.getvalue()).decode(),
            "summary": summary}


def _analysis_traceback(exc):
    """Traceback limited to frames from the analysis code and below."""
    tb = exc.__traceback__
    while tb is not None and tb.tb_frame.f_code.co_filename != CODE_FILENAME:
        tb = tb.tb_next
    lines = traceback.format_exception(
        type(exc), exc, tb if tb is not None else exc.__traceback__, chain=False
    )
    text = "".join(lines)
    if len(text) > 4000:
        text = text[:1000] + "\n...\n" + text[-3000:]
    return text


def run(job_dir):
    with open(os.path.join(job_dir, "job.json"), encoding="utf-8") as fh:
        job = json.load(fh)
    limits = job.get("limits", {})
    _apply_limits(limits)

    seed = int(job.get("seed", 0))
    random.seed(seed)
    started = time.perf_counter()
    result = {"ok": False, "stdout": "", "stdout_truncated": False, "error": None,
              "artifacts": [], "warnings": [], "duration_s": 0.0}

    stdout = _CappedIO(int(limits.get("max_stdout_chars", 20000)))
    collector = ArtifactCollector(limits)
    try:
        import numpy as np
        import pandas as pd

        np.random.seed(seed)
        df = pd.read_pickle(job["data_path"])
        namespace = {"__name__": "__main__", "df": df, "pd": pd, "np": np, "show": collector.show}
        try:
            import plotly.express as px
            import plotly.graph_objects as go

            namespace.update(px=px, go=go)
            # There is no browser here: route fig.show() to the report instead.
            go.Figure.show = lambda self, *args, **kwargs: collector.show(self)
        except ImportError:
            pass

        safe = {name: getattr(builtins, name) for name in SAFE_BUILTINS if hasattr(builtins, name)}
        safe["__import__"] = _make_import(set(job["allowed_modules"]))
        namespace["__builtins__"] = safe

        tree = ast.parse(job["code"], filename=CODE_FILENAME)
        tail_expr = None
        # Echo a trailing expression like a notebook does, unless silenced with ";".
        if tree.body and isinstance(tree.body[-1], ast.Expr) and not job["code"].rstrip().endswith(";"):
            tail_expr = ast.Expression(tree.body.pop().value)

        with warnings.catch_warnings(record=True) as caught, redirect_stdout(stdout), \
                redirect_stderr(stdout):
            warnings.simplefilter("always")
            exec(compile(tree, CODE_FILENAME, "exec"), namespace)
            if tail_expr is not None:
                value = eval(compile(tail_expr, CODE_FILENAME, "eval"), namespace)
                if value is not None:
                    collector.add(value, explicit=False)

            # Conventional variable names are displayed even if show() was not called.
            for var in ("fig", "result"):
                obj = namespace.get(var)
                if obj is not None and not callable(obj):
                    collector.show(obj)
            if "matplotlib.pyplot" in sys.modules:
                plt = sys.modules["matplotlib.pyplot"]
                for num in plt.get_fignums():
                    collector.show(plt.figure(num))
                plt.close("all")

        seen = []
        for w in caught:
            msg = f"{w.category.__name__}: {w.message}"
            if msg not in seen:
                seen.append(msg)
        result["warnings"] = seen[:5]
        result["ok"] = True
    except BaseException as exc:  # includes SystemExit/KeyboardInterrupt from user code
        if isinstance(exc, MemoryError):
            message = "Ran out of memory. Work on a subset or aggregate before expanding the data."
        else:
            message = str(exc)
        result["error"] = {"type": type(exc).__name__, "message": message[:2000],
                           "traceback": _analysis_traceback(exc)}

    result["stdout"] = stdout.getvalue()
    result["stdout_truncated"] = stdout.truncated
    result["artifacts"] = collector.items
    if collector.dropped:
        result["warnings"].append(f"{collector.dropped} additional output(s) were not displayed "
                                  f"(limit {collector.max_items}).")
    result["duration_s"] = time.perf_counter() - started

    tmp_path = os.path.join(job_dir, "result.json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, default=str)
    os.replace(tmp_path, os.path.join(job_dir, "result.json"))


if __name__ == "__main__":
    run(sys.argv[1])
