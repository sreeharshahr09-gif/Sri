"""Read-only, deterministic comparisons of tables inside a Workspace."""

from __future__ import annotations

import math
import importlib.util
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from .workspace import Workspace, WorkspaceError

EXCEL_EXTENSIONS = {".xlsx", ".xlsm", ".xls", ".xlsb", ".xltx", ".xltm"}
TABLE_EXTENSIONS = EXCEL_EXTENSIONS | {".csv", ".tsv"}
MAX_COMPARE_ROWS = 50_000


class CompareError(ValueError):
    """The chosen tables cannot be compared without changing the selection."""


@dataclass(frozen=True)
class TableRange:
    header_row: int
    first_col: int
    last_col: int
    last_data_row: int | None = None

    @property
    def excel_columns(self) -> str:
        return f"{get_column_letter(self.first_col)}:{get_column_letter(self.last_col)}"


@dataclass
class ComparisonSource:
    label: str
    frame: pd.DataFrame
    key_column: str
    key_factor: float = 1.0
    value_factor: float = 1.0
    key_type: str = "numeric"


@dataclass
class ComparisonResult:
    curves: dict[str, pd.DataFrame]
    differences: pd.DataFrame
    diagnostics: pd.DataFrame


def _table_path(workspace: Workspace, relative: str) -> Path:
    try:
        path = workspace.resolve(relative)
        workspace._check_readable(path)
    except WorkspaceError as exc:
        raise CompareError(str(exc)) from exc
    if not path.is_file() or path.suffix.lower() not in TABLE_EXTENSIONS:
        raise CompareError(f"'{relative}' is not a supported table file.")
    if path.stat().st_size > 150 * 1024 * 1024:
        raise CompareError(f"'{relative}' exceeds the 150 MB comparison file limit.")
    if path.suffix.lower() in {".xls", ".xlsb"} and importlib.util.find_spec("python_calamine") is None:
        raise CompareError(f"'{relative}' requires python-calamine to read {path.suffix.lower()} files. Install the project requirements.")
    return path


def _excel_engine(path: Path) -> str:
    return "calamine" if path.suffix.lower() in {".xls", ".xlsb"} else "openpyxl"


def sheet_names(workspace: Workspace, relative: str) -> list[str]:
    path = _table_path(workspace, relative)
    if path.suffix.lower() not in EXCEL_EXTENSIONS:
        return []
    try:
        with pd.ExcelFile(path, engine=_excel_engine(path)) as book:
            return list(book.sheet_names)
    except Exception as exc:
        raise CompareError(f"Could not list sheets in '{relative}': {exc}") from exc


def _filled(value: object) -> bool:
    try:
        return value is not None and bool(pd.notna(value)) and str(value).strip() != ""
    except (TypeError, ValueError):
        return False


def _candidate_end(rows: list[tuple], header_index: int, start: int, end: int) -> int | None:
    """Stop a detected table before a footer or another block when the sample shows one."""
    first_values = [row[start] for row in rows[header_index + 1:header_index + 4]
                    if start < len(row) and _filled(row[start])]
    numeric_key = bool(first_values) and all(pd.notna(pd.to_numeric(v, errors="coerce"))
                                             for v in first_values)
    for index in range(header_index + 1, len(rows)):
        row = rows[index]
        key = row[start] if start < len(row) else None
        other = row[start + 1:end + 1]
        if (not _filled(key) or not any(_filled(v) for v in other) or
                (numeric_key and pd.isna(pd.to_numeric(key, errors="coerce")))):
            return index if index > header_index + 1 else None  # previous row, 1-based
    return None  # sample ended; the table may continue beyond it


def discover_table_ranges(workspace: Workspace, relative: str, sheet: str | None,
                          *, max_candidates: int = 20, max_scan_rows: int = 200) -> list[TableRange]:
    """Suggest candidate tables from a bounded scan; the user confirms the range."""
    path = _table_path(workspace, relative)
    if path.suffix.lower() not in EXCEL_EXTENSIONS:
        try:
            header = pd.read_csv(path, sep="\t" if path.suffix.lower() == ".tsv" else None,
                                 engine="python", encoding_errors="replace", nrows=0)
        except Exception as exc:
            raise CompareError(f"Could not inspect '{relative}': {exc}") from exc
        if len(header.columns) < 2:
            raise CompareError(f"'{relative}' has fewer than two columns to compare.")
        return [TableRange(1, 1, min(len(header.columns), 101))]
    if sheet is None:
        raise CompareError("Select a worksheet.")
    try:
        if _excel_engine(path) == "openpyxl":
            book = load_workbook(path, read_only=True, data_only=True)
            try:
                if sheet not in book.sheetnames:
                    raise CompareError(f"No sheet named '{sheet}' in '{relative}'.")
                ws = book[sheet]
                rows = list(ws.iter_rows(min_row=1, max_row=min(ws.max_row, max_scan_rows),
                                         max_col=min(ws.max_column, 120), values_only=True))
            finally:
                book.close()
        else:
            sample = pd.read_excel(path, sheet_name=sheet, header=None, nrows=max_scan_rows,
                                   engine="calamine", dtype=object)
            rows = list(sample.iloc[:, :120].itertuples(index=False, name=None))
    except CompareError:
        raise
    except Exception as exc:
        raise CompareError(f"Could not inspect '{relative}' / '{sheet}': {exc}") from exc

    candidates: list[tuple[float, TableRange]] = []
    for row_index, row in enumerate(rows[:-1]):
        start = 0
        while start < len(row):
            if not _filled(row[start]):
                start += 1
                continue
            end = start
            while end + 1 < len(row) and _filled(row[end + 1]):
                end += 1
            width = end - start + 1
            if width >= 2:
                first = str(row[start]).strip().lower()
                below = rows[row_index + 1][start:end + 1]
                filled_below = sum(_filled(value) for value in below)
                numeric_below = sum(pd.notna(pd.to_numeric(value, errors="coerce")) for value in below)
                if filled_below >= 2:
                    score = width + filled_below * 2 + numeric_below
                    if isinstance(row[start], str):
                        score += 10
                    if any(term in first for term in ("slip", "load", "time", "date")):
                        score += 40
                    candidate = TableRange(row_index + 1, start + 1, end + 1,
                                           _candidate_end(rows, row_index, start, end))
                    candidates.append((score, candidate))
            start = end + 1
    candidates.sort(key=lambda item: (-item[0], item[1].header_row))
    return [candidate for _, candidate in candidates[:max_candidates]]


def guess_table_range(workspace: Workspace, relative: str, sheet: str | None) -> TableRange:
    choices = discover_table_ranges(workspace, relative, sheet)
    if not choices:
        raise CompareError("No table header was found in the first 200 rows and 120 columns. Enter a range manually or choose a different sheet.")
    return choices[0]


def read_table(workspace: Workspace, relative: str, sheet: str | None, table_range: TableRange,
               preview_rows: int | None = None) -> pd.DataFrame:
    """Read just the selected table columns; blank cells remain missing, never zero."""
    path = _table_path(workspace, relative)
    if table_range.header_row < 1 or table_range.first_col < 1 or table_range.last_col < table_range.first_col:
        raise CompareError("Header row and column range must be positive and ordered.")
    if table_range.last_col - table_range.first_col > 100:
        raise CompareError("Choose no more than 101 columns for one comparison.")
    if table_range.last_data_row is not None and table_range.last_data_row <= table_range.header_row:
        raise CompareError("Last data row must be below the header row.")
    limit = (preview_rows or MAX_COMPARE_ROWS) + (0 if preview_rows else 1)
    if table_range.last_data_row is not None:
        limit = min(limit, table_range.last_data_row - table_range.header_row)
    try:
        if path.suffix.lower() in EXCEL_EXTENSIONS:
            if sheet is None:
                raise CompareError("Select a worksheet.")
            raw = pd.read_excel(path, sheet_name=sheet, header=None, skiprows=table_range.header_row - 1,
                                  usecols=table_range.excel_columns, nrows=limit + 1, dtype=object,
                                  engine=_excel_engine(path))
        else:
            raw = pd.read_csv(path, sep="\t" if path.suffix.lower() == ".tsv" else None,
                                engine="python", encoding_errors="replace",
                                header=None, skiprows=table_range.header_row - 1,
                                usecols=list(range(table_range.first_col - 1, table_range.last_col)),
                                nrows=limit + 1, dtype=object)
    except CompareError:
        raise
    except Exception as exc:
        raise CompareError(f"Could not read '{relative}' / '{sheet or 'table'}': {exc}") from exc
    if raw.empty:
        raise CompareError("The selected header row is empty. Choose another row or sheet.")
    frame = raw.iloc[1:].copy()
    frame.columns = list(raw.iloc[0])
    if not preview_rows and len(frame) > MAX_COMPARE_ROWS:
        raise CompareError(f"The selected table exceeds {MAX_COMPARE_ROWS:,} rows. Choose a summary sheet or a smaller range.")
    original_columns = len(frame.columns)
    frame = frame.dropna(axis=0, how="all").dropna(axis=1, how="all")
    if original_columns >= 2 and len(frame.columns) < 2:
        raise CompareError("The selected range has fewer than two populated columns. Check the range; formula cells may also lack cached results until the workbook is recalculated and saved in Excel.")
    names: list[str] = []
    for col in frame.columns:
        if not _filled(col):
            raise CompareError("A populated column has a blank header. Narrow the range or select a complete header row.")
        if isinstance(col, (int, float)) and float(col).is_integer():
            names.append(str(int(col)))
        else:
            names.append(str(col).strip())
    if len(names) != len(set(names)):
        raise CompareError("The selected table has repeated column names. Narrow the range or choose another header row.")
    frame.columns = names
    if frame.empty or len(frame.columns) < 2:
        raise CompareError("The selected range has no usable two-column table. Check the header row and columns.")
    return frame.reset_index(drop=True)


def compare_numeric(sources: list[ComparisonSource], metrics: dict[str, dict[str, str]],
                    duplicate_policy: str = "block") -> ComparisonResult:
    """Align numeric keys and values. Never invent missing values or silently average repeats."""
    if len(sources) < 2 or len({s.label for s in sources}) != len(sources):
        raise CompareError("Choose at least two distinct files.")
    if not metrics:
        raise CompareError("Choose at least one value column to compare.")
    if duplicate_policy not in {"block", "mean", "median"}:
        raise CompareError("Unknown duplicate-key policy.")
    prepared: dict[str, pd.DataFrame] = {}
    diagnostics: list[dict] = []
    for source in sources:
        if source.key_column not in source.frame:
            raise CompareError(f"'{source.label}' has no key column '{source.key_column}'.")
        if not math.isfinite(source.key_factor) or source.key_factor == 0:
            raise CompareError(f"'{source.label}' needs a nonzero key-unit multiplier.")
        if not math.isfinite(source.value_factor) or source.value_factor == 0:
            raise CompareError(f"'{source.label}' needs a nonzero value-unit multiplier.")
        if source.key_type == "numeric":
            key = pd.to_numeric(source.frame[source.key_column], errors="coerce") * source.key_factor
        elif source.key_type == "date":
            if source.key_factor != 1:
                raise CompareError(f"'{source.label}': date keys cannot have a numeric multiplier.")
            key = pd.to_datetime(source.frame[source.key_column], errors="coerce")
        elif source.key_type == "text":
            if source.key_factor != 1:
                raise CompareError(f"'{source.label}': text keys cannot have a numeric multiplier.")
            key = source.frame[source.key_column].map(lambda v: str(v).strip() if _filled(v) else pd.NA)
            key = key.replace("", pd.NA)
        else:
            raise CompareError("Key type must be Numeric, Date or Text.")
        cols: dict[str, pd.Series] = {"key": key}
        for metric, mapping in metrics.items():
            column = mapping.get(source.label)
            if column not in source.frame:
                raise CompareError(f"'{source.label}' has no value column for '{metric}'.")
            cols[metric] = pd.to_numeric(source.frame[column], errors="coerce") * source.value_factor
        table = pd.DataFrame(cols)
        invalid_keys = int(table["key"].isna().sum())
        table = table.dropna(subset=["key"])
        if table.empty:
            raise CompareError(f"'{source.label}' has no valid {source.key_type} key values in the selected table.")
        missing_values = int(table[list(metrics)].isna().sum().sum())
        empty_metrics = [metric for metric in metrics if table[metric].isna().all()]
        if empty_metrics:
            raise CompareError(f"'{source.label}' has no numeric values for {', '.join(empty_metrics)}. Check the selected columns, formula results and units.")
        repeated = int(table["key"].duplicated(keep=False).sum())
        if repeated and duplicate_policy == "block":
            raise CompareError(f"'{source.label}' has {repeated} rows with repeated keys. Choose an explicit mean/median policy or a more specific key.")
        if repeated:
            table = table.groupby("key", as_index=False).agg(duplicate_policy)
        prepared[source.label] = table.set_index("key").sort_index()
        diagnostics.append({"File": source.label, "Rows read": len(source.frame),
                            "Invalid/blank keys": invalid_keys,
                            "Rows with repeated keys": repeated,
                            "Missing values": missing_values,
                            "Keys used": len(table)})

    baseline = sources[0].label
    if len({source.key_type for source in sources}) != 1:
        raise CompareError("Choose the same key type for every file.")
    curves: dict[str, pd.DataFrame] = {}
    deltas: list[dict] = []
    for metric in metrics:
        wide = pd.concat({label: frame[metric] for label, frame in prepared.items()}, axis=1).sort_index()
        if wide.dropna(how="any").empty:
            raise CompareError(f"'{metric}' has no key with a numeric value in every file. Check key types, units and selected columns.")
        wide.index.name = sources[0].key_column
        curves[metric] = wide
        for source in sources[1:]:
            pair = wide[[baseline, source.label]].dropna()
            for key, row in pair.iterrows():
                original, current = float(row[baseline]), float(row[source.label])
                deltas.append({"Metric": metric, "Key": key, "Baseline": baseline,
                               "Comparison": source.label, "Baseline value": original,
                               "Comparison value": current, "Difference": current - original,
                               "Difference %": (current / original - 1) * 100 if original else float("nan")})
    return ComparisonResult(curves, pd.DataFrame(deltas), pd.DataFrame(diagnostics))
