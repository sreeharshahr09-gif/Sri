"""Data-quality checks on the master file, the curve files and their agreement.

Every finding becomes one row in a flags table:
    ID | file | area | check | severity | detail | action
severity: "error"   - the value/row cannot be used as is (excluded or replaced)
          "warning" - usable, but a person should look at it
          "info"    - handled automatically, listed for transparency
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import schema as S

TOL = {"mu_peak": 0.05, "SR_at_mupeak": 0.05, "mu_lock": 0.05}

CHECK_TEXT = {
    # check: (plain-language meaning, recommended action)
    "duplicate_id": ("The same ID appears on more than one master row.",
                     "Give each test a unique ID. These rows are excluded until fixed."),
    "nonnumeric_cell": ("Text where a number is expected (e.g. '-').",
                        "Treated as missing. Fill in the value if it was measured."),
    "out_of_range": ("Value outside the physically plausible range.",
                     "Likely a typo or unit slip. The value is ignored; correct it in the master."),
    "missing_target": ("Landmark missing in the master.", "Curve-derived value used if available."),
    "missing_curve_file": ("No µ-slip file found for this master row.",
                           "Locate the file. Only master landmarks can be used for this test."),
    "file_without_master": ("Curve file whose ID is not in the master.",
                            "Add the master row or remove/rename the file."),
    "file_without_id": ("File name has no recognisable ID.", "Rename the file or adjust --id-regex."),
    "duplicate_file_id": ("Two files carry the same ID.", "Only the first was used; remove the duplicate."),
    "unreadable_file": ("File could not be read as two numeric columns.",
                        "Re-export the file. The test is excluded from curve analysis."),
    "slip_in_percent": ("Slip recorded in percent (0-100).", "Converted to ratio automatically."),
    "negative_slip_sign": ("Slip recorded as negative (braking sign).", "Sign flipped automatically."),
    "negative_mu_sign": ("µ recorded as negative.", "Sign flipped automatically."),
    "few_points": ("Very few data points in the braking part of the curve.",
                   "Check the rig sampling / export."),
    "linkage_suspect": ("Many curve files disagree with their master rows.",
                        "The file-to-ID rule is probably wrong (e.g. the number in the file name "
                        "is not the Sl No). Check --id-regex before trusting any result."),
    "no_lock_region": ("Curve stops before full lock (slip < 0.95).",
                       "µ lock cannot be read from the curve; master value used if valid."),
    "late_start": ("Curve does not start near zero slip.", "Initial slope is unreliable for this test."),
    "slip_gap": ("Large gap in slip coverage.", "Check for dropped samples in the export."),
    "high_noise": ("Curve is much noisier than typical.",
                   "Check sensor/filter settings; consider a repeat test."),
    "spikes": ("Isolated spike points in the curve.", "Spike points removed automatically."),
    "implausible_mu": ("µ values outside a plausible range.", "Check calibration / units."),
    "master_curve_mismatch": ("Master landmark disagrees with the value read from the curve.",
                              "Check for a typo in the master or a wrongly linked file. "
                              "The curve value is used."),
    "repeat_outlier": ("Test differs strongly from its own repeats (same tyre and conditions).",
                       "Review the test log (surface, tyre damage, rig). Kept, but worth a look."),
    "single_test_config": ("Configuration tested only once.",
                           "Repeatability cannot be checked; prefer >= 3 repeats."),
    "poor_pacejka_fit": ("Magic Formula fits this curve poorly.",
                         "Shape may be unusual or data faulty; inspect the curve."),
}


def _flag(rows, sid, area, check, severity, detail, file=""):
    rows.append(dict(ID=sid, file=file, area=area, check=check, severity=severity, detail=detail,
                     meaning=CHECK_TEXT[check][0], action=CHECK_TEXT[check][1]))


def check_master(master, load_info):
    rows = []
    dup = master[S.ID_COL][master[S.ID_COL].duplicated(keep=False)]
    for sid in sorted(set(dup)):
        _flag(rows, sid, "master", "duplicate_id", "error", f"ID {sid} on {int((dup == sid).sum())} rows")
    for col, cells in load_info.get("nonnumeric", {}).items():
        for sid, val in cells:
            _flag(rows, sid, "master", "nonnumeric_cell", "warning", f"{col} = '{val}'")
    for col, (lo, hi) in S.LIMITS.items():
        if col not in master:
            continue
        v = master[col]
        bad = v.notna() & ((v < lo) | (v > hi))
        sev = "error" if col in S.MASTER_TARGETS else "warning"
        for sid, x in zip(master.loc[bad, S.ID_COL], v[bad]):
            _flag(rows, sid, "master", "out_of_range", sev, f"{col} = {x:g} (expected {lo}-{hi})")
    for col in S.MASTER_TARGETS:
        for sid in master.loc[master[col].isna(), S.ID_COL]:
            _flag(rows, sid, "master", "missing_target", "warning", col)
    return rows


def check_matching(match_report, file_notes):
    rows = []
    for sid in match_report["master_without_file"]:
        _flag(rows, sid, "matching", "missing_curve_file", "warning", "no file")
    for f in match_report["files_without_master"]:
        _flag(rows, np.nan, "matching", "file_without_master", "warning", f, file=f)
    for f in match_report["files_without_id"]:
        _flag(rows, np.nan, "matching", "file_without_id", "warning", f, file=f)
    for sid in match_report["duplicate_file_ids"]:
        _flag(rows, sid, "matching", "duplicate_file_id", "warning", f"ID {sid}")
    for sid, note, f in file_notes.itertuples(index=False):
        if note.startswith("unreadable"):
            _flag(rows, sid, "curve", "unreadable_file", "error", note, file=f)
        else:
            _flag(rows, sid, "curve", note, "info", note, file=f)
    return rows


def check_curves(desc):
    """Curve checks. Thresholds are relative to this dataset where possible, so
    a rig's normal sampling pattern is not flagged on every file."""
    rows = []
    med_noise = desc["noise_sd"].median()
    gap_lim = max(0.12, 2.5 * desc["max_slip_gap"].median())
    for sid, d in desc.iterrows():
        if d.get("n_brake_points", 0) < 20:
            _flag(rows, sid, "curve", "few_points", "warning",
                  f"{int(d.get('n_brake_points', 0))} points beyond slip 0.005")
        if d.get("sr_max", 1) < S.LOCK_SLIP_MIN:
            _flag(rows, sid, "curve", "no_lock_region", "warning", f"max slip {d['sr_max']:.2f}")
        if d.get("sr_min", 0) > 0.02:
            _flag(rows, sid, "curve", "late_start", "warning", f"first slip {d['sr_min']:.3f}")
        if d.get("max_slip_gap", 0) > gap_lim:
            _flag(rows, sid, "curve", "slip_gap", "warning",
                  f"gap {d['max_slip_gap']:.3f} (typical {desc['max_slip_gap'].median():.3f})")
        if d.get("noise_sd", 0) > 3 * med_noise:
            _flag(rows, sid, "curve", "high_noise", "warning",
                  f"noise {d['noise_sd']:.3f} vs typical {med_noise:.3f}")
        if d.get("n_spikes", 0) > 0:
            _flag(rows, sid, "curve", "spikes", "warning", f"{int(d['n_spikes'])} points removed")
        if d.get("mu_max_raw", 0) > 1.8 or d.get("mu_min_raw", 0) < -0.1:
            _flag(rows, sid, "curve", "implausible_mu", "warning",
                  f"µ range {d['mu_min_raw']:.2f} to {d['mu_max_raw']:.2f}")
    return rows


def check_consistency(master, desc):
    rows = []
    m = master.drop_duplicates(S.ID_COL, keep=False).set_index(S.ID_COL)
    j = m[S.MASTER_TARGETS].join(desc[S.MASTER_TARGETS], rsuffix="_curve", how="inner")
    bad = set()
    for t, tol in TOL.items():
        diff = j[t] - j[t + "_curve"]
        for sid in diff.index[diff.abs() > tol]:
            bad.add(sid)
            _flag(rows, sid, "consistency", "master_curve_mismatch", "warning",
                  f"{t}: master {j.at[sid, t]:.3f} vs curve {j.at[sid, t + '_curve']:.3f}")
    if len(j) >= 10 and len(bad) > 0.3 * len(j):
        _flag(rows, np.nan, "consistency", "linkage_suspect", "error",
              f"{len(bad)} of {len(j)} linked tests disagree with their master landmarks")
    return rows


def check_repeats(df, targets=("mu_peak", "mu_lock"), z_lim=4.0, min_n=3):
    """Flag tests far from their configuration's median.

    The scale is the repeat scatter pooled over all configurations (robust:
    1.4826 x median absolute deviation from each config's median), because a
    scatter estimated from only 4-6 repeats of one configuration is unstable."""
    rows = []
    for t in targets:
        g = df.groupby("config_id")[t]
        n = g.transform("count")
        dev = df[t] - g.transform("median")
        use = n >= min_n
        scale = 1.4826 * np.nanmedian(np.abs(dev[use])) if use.any() else np.nan
        if not np.isfinite(scale) or scale <= 0:
            continue
        z = dev / scale
        bad = use & (z.abs() > z_lim)
        for sid, zz, v, d0 in zip(df.loc[bad, S.ID_COL], z[bad], df.loc[bad, t], dev[bad]):
            _flag(rows, sid, "repeats", "repeat_outlier", "warning",
                  f"{t} = {v:.3f}, {d0:+.3f} from its repeats' median "
                  f"({abs(zz):.1f}x the typical repeat scatter of {scale:.3f})")
    singles = df.groupby("config_id")[S.ID_COL].transform("count") == 1
    for sid in df.loc[singles, S.ID_COL]:
        _flag(rows, sid, "repeats", "single_test_config", "info", "1 test")
    return rows


def check_fits(fits, r2_min=0.97):
    rows = []
    for sid, r in fits.iterrows():
        if not np.isfinite(r["fit_r2"]) or r["fit_r2"] < r2_min:
            _flag(rows, sid, "pacejka", "poor_pacejka_fit", "warning", f"fit R² = {r['fit_r2']:.3f}")
    return rows


def to_frame(rows):
    cols = ["ID", "file", "area", "check", "severity", "detail", "meaning", "action"]
    f = pd.DataFrame(rows, columns=cols)
    order = {"error": 0, "warning": 1, "info": 2}
    return f.sort_values(["severity", "check", "ID"], key=lambda s: s.map(order) if s.name == "severity" else s
                         ).reset_index(drop=True)


def resolve_targets(master, desc, flags):
    """One clean landmark table per test.

    Curve-derived values are preferred (one consistent definition for every
    test); master values fill in where the curve cannot provide them (no file,
    unreadable, no lock region). Values flagged as errors are never used.
    Returns DataFrame indexed by ID with TARGETS + 'target_source'.
    """
    m = master.drop_duplicates(S.ID_COL, keep=False).set_index(S.ID_COL)
    bad_master = set()
    for r in flags[(flags.check == "out_of_range") & (flags.severity == "error")].itertuples():
        bad_master.add((r.ID, r.detail.split(" =")[0]))
    out = pd.DataFrame(index=m.index)
    src = pd.Series("curve", index=m.index)
    for t in S.TARGETS:
        cv = desc[t] if t in desc else pd.Series(dtype=float)
        val = cv.reindex(m.index)
        if t in S.MASTER_TARGETS:
            mv = m[t].copy()
            for sid in m.index:
                if (sid, t) in bad_master:
                    mv.at[sid] = np.nan
            fill = val.isna() & mv.notna()
            src[fill] = "master"
            val = val.where(~fill, mv)
        out[t] = val
    out["target_source"] = src
    return out
