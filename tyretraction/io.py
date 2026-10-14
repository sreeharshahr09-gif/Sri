"""Loading the master file and the per-test µ-slip curve files, and matching them by ID."""
from __future__ import annotations

import glob
import json
import os
import re

import numpy as np
import pandas as pd

from . import schema as S


# ----------------------------------------------------------------------------
# Master file
# ----------------------------------------------------------------------------
def load_master(path, sheet="Master File", column_map=None):
    """Read the master file (xlsx or csv).

    Column resolution, in order: an explicit JSON column map
    ({"their header": "our name"}), headers that already use our names, or the
    36-column positional layout. Rows whose ID is not a number (a second header
    row, blank rows, notes) are dropped and counted.

    Returns (df, info). info["nonnumeric"] lists cells that held text where a
    number was expected (e.g. "-" for a missing M300); those cells become NaN.
    """
    if str(path).lower().endswith(".csv"):
        raw = pd.read_csv(path, dtype=object)
    else:
        xl = pd.ExcelFile(path)
        name = sheet if sheet in xl.sheet_names else xl.sheet_names[0]
        raw = xl.parse(name, dtype=object)

    if column_map:
        cmap = json.load(open(column_map)) if isinstance(column_map, str) else dict(column_map)
        raw = raw.rename(columns=cmap)
        missing = [c for c in (S.ID_COL, S.SIZE_KEY, "SurfaceType") if c not in raw.columns]
        if missing:
            raise ValueError(f"column map leaves required columns unmapped: {missing}")
    elif set(S.MASTER_COLS) <= set(map(str, raw.columns)):
        pass
    elif raw.shape[1] == len(S.MASTER_COLS):
        raw.columns = S.MASTER_COLS
    else:
        raise ValueError(
            f"master has {raw.shape[1]} columns; expected {len(S.MASTER_COLS)} in the standard "
            f"order or a --column-map JSON. Columns found: {list(raw.columns)[:40]}")

    ids = pd.to_numeric(raw[S.ID_COL], errors="coerce")
    keep = ids.notna()
    df = raw[keep].copy().reset_index(drop=True)
    info = {"rows_read": len(raw), "rows_dropped_non_id": int((~keep).sum()), "nonnumeric": {}}
    df[S.ID_COL] = ids[keep].astype(int).to_numpy()

    for c in df.columns:
        if c in S.TEXT_COLS or c == S.ID_COL:
            continue
        num = pd.to_numeric(df[c], errors="coerce")
        bad = df[c].notna() & num.isna() & (df[c].astype(str).str.strip() != "")
        if bad.any():
            info["nonnumeric"][c] = list(zip(df.loc[bad, S.ID_COL], df.loc[bad, c].astype(str)))
        df[c] = num
    for c in S.TEXT_COLS:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()
    # Some rigs record slip at peak in percent; normalise to a ratio.
    if "SR_at_mupeak" in df and df["SR_at_mupeak"].median() > 1.5:
        df["SR_at_mupeak"] = df["SR_at_mupeak"] / 100.0
        info["sr_percent_converted"] = True
    return df, info


def parse_size(size):
    """'225/50R19 96V' -> (225, 50, 19). NaNs when the pattern is not found."""
    m = re.search(r"(\d{3})\s*/\s*(\d{2})\s*Z?R\s*(\d{2})", str(size).upper())
    return (float(m.group(1)), float(m.group(2)), float(m.group(3))) if m else (np.nan,) * 3


def build_features(df):
    """Numeric feature table (one row per test) from master columns."""
    X = pd.DataFrame(index=df.index)
    X["width_mm"], X["aspect"], X["rim_in"] = zip(*df[S.SIZE_KEY].map(parse_size))
    X["IsDry"] = (df["SurfaceType"].str.lower() == "dry").astype(float)
    for c in S.OPERATING + S.DMA + S.FOOTPRINT + ["RimWidth_in"]:
        X[c] = df[c].astype(float) if c in df else np.nan
    X["contact_press_Nmm2"] = X["Load_N"] / (X["TotalContactArea_cm2"] * 100.0)
    X["load_per_mm_width"] = X["Load_N"] / X["width_mm"]
    X["contact_aspect"] = X["ContactLen_mm"] / X["ContactWidth_mm"]
    return X.replace([np.inf, -np.inf], np.nan)


def add_keys(df):
    """Add config / tyre / compound identifiers used for grouping."""
    df = df.copy()
    df["config_id"] = df[S.CONFIG_KEYS].astype(str).agg("|".join, axis=1)
    df["tyre_id"] = df[S.TYRE_KEYS].astype(str).agg("|".join, axis=1)
    df["compound_id"] = df[S.COMPOUND_KEY].astype(str)
    df["size_id"] = df[S.SIZE_KEY].astype(str)
    return df


# ----------------------------------------------------------------------------
# Curve files
# ----------------------------------------------------------------------------
CURVE_EXT = (".xlsx", ".xls", ".csv", ".txt")


def read_curve_file(path):
    """Read one µ-slip file. Takes the first two mostly-numeric columns as
    (slip, µ), drops header/blank rows, and normalises percent slip and negative
    (braking) sign conventions. Returns (sr, mu, notes) with sr sorted."""
    if path.lower().endswith((".csv", ".txt")):
        raw = pd.read_csv(path, header=None, sep=None, engine="python")
    else:
        raw = pd.read_excel(path, header=None)
    num = raw.apply(pd.to_numeric, errors="coerce")
    cols = [c for c in num.columns if num[c].notna().mean() > 0.5]
    if len(cols) < 2:
        raise ValueError("fewer than two numeric columns")
    t = num[cols[:2]].dropna()
    sr, mu = t.iloc[:, 0].to_numpy(float), t.iloc[:, 1].to_numpy(float)
    notes = []
    # Sign convention is judged from the extremes, not the median: real files
    # start with a long rolling phase at slip ~ 0 (often slightly negative).
    if len(sr) and abs(np.nanmin(sr)) > abs(np.nanmax(sr)):
        sr = -sr; notes.append("negative_slip_sign")
    if len(mu) and abs(np.nanmin(mu)) > abs(np.nanmax(mu)):
        mu = -mu; notes.append("negative_mu_sign")
    if len(sr) and np.nanmax(sr) > 1.5:
        sr = sr / 100.0; notes.append("slip_in_percent")
    o = np.argsort(sr, kind="stable")
    return sr[o], mu[o], notes


def match_curve_files(curve_dir, master_ids, id_regex=r"^\s*(\d+)"):
    """Map curve files to master IDs using the first capture group of id_regex
    applied to the file name. Returns (matched_df[ID, file], report dict)."""
    files = sorted(f for f in glob.glob(os.path.join(curve_dir, "**", "*"), recursive=True)
                   if f.lower().endswith(CURVE_EXT) and not os.path.basename(f).startswith("~$"))
    rx = re.compile(id_regex)
    rows, no_id = [], []
    for f in files:
        m = rx.search(os.path.basename(f))
        if m:
            rows.append((int(m.group(1)), f))
        else:
            no_id.append(os.path.basename(f))
    fm = pd.DataFrame(rows, columns=[S.ID_COL, "file"])
    dup = fm[fm.duplicated(S.ID_COL, keep=False)]
    fm = fm.drop_duplicates(S.ID_COL, keep="first")
    ids = set(int(i) for i in master_ids)
    matched = fm[fm[S.ID_COL].isin(ids)].reset_index(drop=True)
    report = {
        "n_files": len(files),
        "n_master": len(ids),
        "n_matched": len(matched),
        "master_without_file": sorted(ids - set(fm[S.ID_COL])),
        "files_without_master": sorted(os.path.basename(f) for f in fm.loc[~fm[S.ID_COL].isin(ids), "file"]),
        "files_without_id": no_id,
        "duplicate_file_ids": sorted(set(dup[S.ID_COL])),
    }
    return matched, report


def load_curves(matched):
    """Read every matched file. Returns ({ID: (sr, mu)}, notes_df)."""
    curves, notes = {}, []
    for sid, f in zip(matched[S.ID_COL], matched["file"]):
        try:
            sr, mu, n = read_curve_file(f)
            curves[int(sid)] = (sr, mu)
            notes += [(int(sid), x, os.path.basename(f)) for x in n]
        except Exception as e:  # unreadable files are reported, never fatal
            notes.append((int(sid), f"unreadable: {str(e)[:120]}", os.path.basename(f)))
    return curves, pd.DataFrame(notes, columns=[S.ID_COL, "note", "file"])
