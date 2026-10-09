"""Dataset loading and profiling.

Loading is defensive about real-world files (encodings, delimiters, messy headers).
Profiling produces a compact, factual description of the data that grounds the model.
"""

from __future__ import annotations

import csv
import hashlib
import io
import warnings
from dataclasses import dataclass, field
from pathlib import PurePath

import numpy as np
import pandas as pd

SUPPORTED_EXTENSIONS = (".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls", ".parquet", ".json", ".jsonl")
_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


@dataclass
class Dataset:
    df: pd.DataFrame
    name: str
    sha256: str
    source_format: str
    sheet: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def shape_text(self) -> str:
        rows, cols = self.df.shape
        return f"{rows:,} rows × {cols:,} columns"


class DataLoadError(ValueError):
    pass


def file_extension(filename: str) -> str:
    return PurePath(filename).suffix.lower()


def list_sheets(raw: bytes, filename: str) -> list[str]:
    """Sheet names of an Excel workbook; empty for other formats."""
    if file_extension(filename) not in (".xlsx", ".xlsm", ".xls"):
        return []
    try:
        return [str(s) for s in pd.ExcelFile(io.BytesIO(raw)).sheet_names]
    except Exception as exc:  # corrupt workbook, missing engine, ...
        raise DataLoadError(f"Could not open workbook: {exc}") from exc


def load_dataset(raw: bytes, filename: str, sheet: str | None = None) -> Dataset:
    ext = file_extension(filename)
    if ext not in SUPPORTED_EXTENSIONS:
        raise DataLoadError(
            f"Unsupported file type '{ext or filename}'. Supported: {', '.join(SUPPORTED_EXTENSIONS)}"
        )
    if not raw:
        raise DataLoadError("The file is empty.")

    notes: list[str] = []
    try:
        if ext in (".csv", ".tsv", ".txt"):
            df, detail = _read_delimited(raw, default_sep="\t" if ext == ".tsv" else None)
            notes.append(detail)
            fmt = "delimited text"
        elif ext in (".xlsx", ".xlsm", ".xls"):
            df = pd.read_excel(io.BytesIO(raw), sheet_name=sheet if sheet is not None else 0)
            fmt = "excel"
        elif ext == ".parquet":
            df = pd.read_parquet(io.BytesIO(raw))
            fmt = "parquet"
        else:
            df = _read_json(raw, lines=ext == ".jsonl")
            fmt = "json"
    except DataLoadError:
        raise
    except ImportError as exc:
        raise DataLoadError(f"A reader dependency is missing: {exc}") from exc
    except Exception as exc:
        raise DataLoadError(f"Could not parse {filename}: {exc}") from exc

    if df.shape[1] == 0:
        raise DataLoadError("No columns were found in the file.")

    df, merged_note = _merge_header_rows(df)
    if merged_note:
        notes.append(merged_note)
    df, header_notes = _clean_columns(df)
    notes.extend(header_notes)
    empty_rows = int(df.isna().all(axis=1).sum())
    if empty_rows:
        df = df.loc[~df.isna().all(axis=1)].reset_index(drop=True)
        notes.append(f"Dropped {empty_rows:,} completely empty rows.")

    return Dataset(
        df=df,
        name=filename,
        sha256=hashlib.sha256(raw).hexdigest(),
        source_format=fmt,
        sheet=sheet,
        notes=[n for n in notes if n],
    )


def _read_delimited(raw: bytes, default_sep: str | None) -> tuple[pd.DataFrame, str]:
    last_exc: Exception | None = None
    for encoding in _ENCODINGS:
        try:
            sample = raw[:64_000].decode(encoding)
        except UnicodeDecodeError as exc:
            # The sample may end mid-character; only the full decode is authoritative.
            if exc.start < len(raw[:64_000]) - 4:
                last_exc = exc
                continue
            sample = raw[: exc.start].decode(encoding)
        sep = default_sep or _sniff_delimiter(sample)
        try:
            df = pd.read_csv(io.BytesIO(raw), sep=sep, encoding=encoding, low_memory=False)
        except UnicodeDecodeError as exc:
            last_exc = exc
            continue
        readable = {"\t": "tab", ",": "comma", ";": "semicolon", "|": "pipe"}.get(sep, repr(sep))
        return df, f"Parsed as {encoding} text with {readable} delimiter."
    raise DataLoadError(f"Could not decode the file with any of {_ENCODINGS}: {last_exc}")


def _sniff_delimiter(sample: str) -> str:
    lines = [ln for ln in sample.splitlines() if ln.strip()][:50]
    if not lines:
        return ","
    try:
        return csv.Sniffer().sniff("\n".join(lines), delimiters=",;\t|").delimiter
    except csv.Error:
        header = lines[0]
        counts = {d: header.count(d) for d in ",;\t|"}
        best = max(counts, key=counts.get)
        return best if counts[best] else ","


def _read_json(raw: bytes, lines: bool) -> pd.DataFrame:
    text = raw.decode("utf-8-sig")
    try:
        return pd.read_json(io.StringIO(text), lines=lines)
    except ValueError:
        if lines:
            raise
        return pd.read_json(io.StringIO(text), lines=True)


def _is_unnamed(col) -> bool:
    if col is None or (isinstance(col, float) and np.isnan(col)):
        return True
    text = str(col).strip()
    return not text or text.startswith("Unnamed:")


def _is_number(value) -> bool:
    if isinstance(value, (int, float, np.number)) and not isinstance(value, bool):
        return True
    try:
        float(str(value).strip().replace(",", ""))
    except ValueError:
        return False
    return True


def _merge_header_rows(df: pd.DataFrame, max_rows: int = 2) -> tuple[pd.DataFrame, str | None]:
    """Fold header continuation rows (sub-labels or units below the header) into column names.

    Spreadsheets often put a label like "SR @ µpeak" or a unit like "[kN]" in the row under
    the main header. Read naively, such columns are "Unnamed" and numeric columns turn into
    text. A row counts as part of the header when it is text-only, labels at least one unnamed
    column or sits above numeric data, and the rows below it are numeric for those columns.
    """
    merged = 0
    touched: set[int] = set()  # positions of columns that had header text folded in
    for _ in range(max_rows):
        if len(df) < 3:
            break
        first = df.iloc[0]
        labelled = [c for c in df.columns if pd.notna(first[c]) and str(first[c]).strip()]
        if not labelled or any(_is_number(first[c]) for c in labelled):
            break
        below = df.iloc[1:51]
        numeric_below = [
            c for c in labelled
            if below[c].notna().any() and all(_is_number(v) for v in below[c].dropna())
        ]
        fills_unnamed = any(_is_unnamed(c) for c in labelled)
        if not numeric_below or not (fills_unnamed or len(numeric_below) >= max(2, len(labelled) // 2)):
            break

        touched.update(i for i, c in enumerate(df.columns) if c in labelled)
        names = []
        for col in df.columns:
            sub = first[col]
            sub = str(sub).strip() if pd.notna(sub) and str(sub).strip() else ""
            if _is_unnamed(col):
                names.append(sub or col)
            else:
                names.append(f"{str(col).strip()} {sub}".strip() if sub else col)
        df = df.iloc[1:].reset_index(drop=True)
        df.columns = names
        merged += 1

    if not merged:
        return df, None
    # Columns that were numeric under the header text can now be numeric again.
    for pos in sorted(touched):
        s = df.iloc[:, pos]
        if pd.api.types.is_object_dtype(s) or pd.api.types.is_string_dtype(s):
            converted = pd.to_numeric(s, errors="coerce")
            if s.notna().any() and converted.notna().sum() == s.notna().sum():
                df.isetitem(pos, converted)
    rows = "row" if merged == 1 else f"{merged} rows"
    return df, f"Merged a header continuation {rows} (sub-labels/units under the header) into the column names."


def _clean_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Make column names non-empty, trimmed strings and unique."""
    notes: list[str] = []
    original = list(df.columns)
    names: list[str] = []
    seen: dict[str, int] = {}
    for i, col in enumerate(original):
        name = "" if col is None or (isinstance(col, float) and np.isnan(col)) else str(col).strip()
        if not name or name.startswith("Unnamed:"):
            name = f"column_{i + 1}"
        base = name
        while name in seen:
            seen[base] += 1
            name = f"{base}_{seen[base]}"
        seen.setdefault(name, 0)
        names.append(name)

    renamed = [(str(o), n) for o, n in zip(original, names) if str(o) != n]
    if renamed:
        shown = ", ".join(f"'{o}' → '{n}'" for o, n in renamed[:8])
        more = f" (+{len(renamed) - 8} more)" if len(renamed) > 8 else ""
        notes.append(f"Renamed columns for clarity: {shown}{more}.")
    df = df.copy()
    df.columns = names
    return df, notes


# --------------------------------------------------------------------------- profiling


def _fmt(value) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "NA"
    if isinstance(value, (float, np.floating)):
        return f"{value:,.6g}" if abs(value) < 1e15 else f"{value:.4e}"
    if isinstance(value, (int, np.integer)):
        return f"{value:,}"
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    text = str(value)
    return text if len(text) <= 40 else text[:37] + "..."


def _is_textual(series: pd.Series) -> bool:
    return pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)


def _parse_rate(series: pd.Series, kind: str) -> float:
    sample = series.dropna()
    if sample.empty:
        return 0.0
    sample = sample.sample(min(len(sample), 300), random_state=0).astype(str).str.strip()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if kind == "numeric":
            cleaned = sample.str.replace(r"[,\s$€£%]", "", regex=True)
            parsed = pd.to_numeric(cleaned, errors="coerce")
        else:
            # Purely numeric strings are not dates for our purposes.
            if pd.to_numeric(sample, errors="coerce").notna().mean() > 0.5:
                return 0.0
            parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
    return float(parsed.notna().mean())


def column_summary(df: pd.DataFrame) -> pd.DataFrame:
    """One row per column with type, completeness and distribution facts."""
    rows = []
    n = len(df)
    for col in df.columns:
        s = df[col]
        non_null = int(s.notna().sum())
        try:
            n_unique = int(s.nunique(dropna=True))
        except TypeError:  # unhashable cells (lists/dicts from JSON)
            n_unique = None
        info: dict = {
            "column": col,
            "dtype": str(s.dtype),
            "non_null": non_null,
            "null_pct": round(100 * (1 - non_null / n), 2) if n else 0.0,
            "unique": n_unique,
            "summary": "",
            "hint": "",
        }
        if pd.api.types.is_bool_dtype(s):
            info["summary"] = f"true {int(s.sum()):,} / {non_null:,}"
        elif pd.api.types.is_numeric_dtype(s):
            desc = s.describe()
            info["summary"] = (
                f"min {_fmt(desc.get('min'))}, median {_fmt(s.median())}, "
                f"mean {_fmt(desc.get('mean'))}, max {_fmt(desc.get('max'))}, std {_fmt(desc.get('std'))}"
            )
        elif pd.api.types.is_datetime64_any_dtype(s):
            info["summary"] = f"from {_fmt(s.min())} to {_fmt(s.max())}"
        elif n_unique is not None and non_null:
            top = s.value_counts(dropna=True).head(4)
            info["summary"] = "top: " + ", ".join(f"{_fmt(k)!r} ({v:,})" for k, v in top.items())
            if _is_textual(s) and n_unique > 1:
                if _parse_rate(s, "numeric") > 0.95:
                    info["hint"] = "numbers stored as text (clean then pd.to_numeric)"
                elif _parse_rate(s, "datetime") > 0.9:
                    info["hint"] = "dates stored as text (use pd.to_datetime)"
            if n_unique == non_null and non_null > 20:
                info["hint"] = (info["hint"] + "; " if info["hint"] else "") + "unique per row (identifier?)"
        rows.append(info)
    return pd.DataFrame(rows)


def describe_for_llm(dataset: Dataset, sample_rows: int = 5, max_columns: int = 80) -> str:
    """A compact textual profile used as grounding context in the system prompt."""
    df = dataset.df
    summary = column_summary(df.iloc[:, :max_columns])
    lines = [f"Dataset `{dataset.name}`: {dataset.shape_text}."]
    if dataset.sheet:
        lines.append(f"Excel sheet: {dataset.sheet}.")
    dup = _duplicate_rows(df)
    if dup:
        lines.append(f"Fully duplicated rows: {dup:,}.")
    lines.append("")
    lines.append("Columns (name | dtype | missing | distinct | summary | hint):")
    for r in summary.itertuples(index=False):
        distinct = "?" if r.unique is None or pd.isna(r.unique) else f"{int(r.unique):,}"
        parts = [f"- {r.column!r}", r.dtype, f"{r.null_pct:g}% missing", f"{distinct} distinct"]
        if r.summary:
            parts.append(r.summary)
        if r.hint:
            parts.append(f"HINT: {r.hint}")
        lines.append(" | ".join(parts))
    if df.shape[1] > max_columns:
        rest = [repr(c) for c in df.columns[max_columns:]]
        lines.append(f"... plus {len(rest)} more columns: {', '.join(rest)}")

    if sample_rows > 0 and len(df):
        lines.append("")
        lines.append(f"First {min(sample_rows, len(df))} rows:")
        with pd.option_context("display.width", 200, "display.max_columns", 30, "display.max_colwidth", 30):
            lines.append(df.head(sample_rows).to_string(index=False))
    return "\n".join(lines)


def _duplicate_rows(df: pd.DataFrame) -> int:
    try:
        return int(df.duplicated().sum())
    except TypeError:
        return 0
