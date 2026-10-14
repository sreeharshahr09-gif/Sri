"""Synthetic demo data in the same format as the real files.

Writes a master workbook (36 columns, two header rows, sheet "Master File")
and one µ-slip workbook per test named "<ID>. Trial_<n>.xlsx". The structure
mirrors the real dataset: 543 tests, 10 sizes with very unequal test counts,
compound groups that are mostly tied to a single size, a few configurations
with dozens of repeats, and a handful of deliberately planted data problems so
the quality checks have something to find. Ground truth is known, which lets
the pipeline be verified end to end.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from . import schema as S
from .curves import curve_descriptors
from .pacejka import mf, params_from_landmarks

SIZES = [("225/50R19 96V", 200, 9), ("205/55R16 91V", 90, 5), ("215/60R16 99H", 60, 4),
         ("195/65R15 91H", 50, 4), ("235/45R18 98W", 40, 3), ("225/55R17 101W", 35, 3),
         ("185/65R15 88H", 25, 3), ("205/60R16 92V", 20, 2), ("195/55R16 87V", 13, 2),
         ("235/40R18 95Y", 10, 2)]          # (size, tests, tyres)

HEADER = ["Sl No", "Tyre Size", "Brand", "Pattern", "Group", "Test Surface", "Surface Type",
          "Load (N)", "IP (kPa)", "Speed (kph)", "Rim Width (in)", "T surface (°C)", "T air (°C)",
          "Trial No", "µpeak", "SR @ µpeak", "µlock", "Tg (°C)", "E' 0°C", "E' 20°C", "E' 60°C",
          "E'' 0°C", "E'' 20°C", "E'' 60°C", "M300 (MPa)", "TanD 0°C", "TanD 20°C", "TanD 60°C",
          "Contact Width (mm)", "Contact Length (mm)", "Total Contact Area (cm²)",
          "Net Contact Area (cm²)", "Contact Ratio", "Void Fill Ratio", "Test IP (kPa)",
          "Test Load (kg)"]
UNITS = ["", "", "", "", "", "", "", "N", "kPa", "kph", "in", "°C", "°C", "", "-", "-", "-",
         "°C", "MPa", "MPa", "MPa", "MPa", "MPa", "MPa", "MPa", "-", "-", "-", "mm", "mm",
         "cm²", "cm²", "-", "-", "kPa", "kg"]


def _split(n, k, rng, fixed=()):
    """Split n tests over k configs (each >= 2), honouring fixed leading counts."""
    out = list(fixed)
    rest, m = n - sum(out), k - len(out)
    base = np.full(m, 2)
    extra = rng.multinomial(rest - 2 * m, rng.dirichlet(np.ones(m) * 2))
    return out + list(base + extra)


# Slip sampling of the braking phase, shaped like the real rig files: dense
# at the start, sparse around and after the peak, saturating near lock.
_SR_KNOTS = np.array([0.0067, 0.0139, 0.0203, 0.0226, 0.0246, 0.034, 0.058, 0.106, 0.189,
                      0.311, 0.462, 0.625, 0.771, 0.880, 0.946, 0.976, 0.986, 0.989])


def _rig_curve(rng, params, mu_lock, noise):
    """One test as the rig records it, in time order: a rolling phase at slip
    ~ 0, then the braking sweep (~50 points) with a knee near 2-3% slip and a
    lock-up drop past 90% slip."""
    from .pacejka import assemble_curves
    n_pre, n_brk = int(rng.integers(170, 210)), 51
    u = np.linspace(0, 1, n_brk) ** rng.uniform(0.9, 1.1)          # time warp, ends fixed
    s = np.interp(u * (len(_SR_KNOTS) - 1), np.arange(len(_SR_KNOTS)), _SR_KNOTS)
    s = np.minimum(s * rng.uniform(0.995, 1.005), 0.995)
    mu = assemble_curves(np.array([params]), [mu_lock], grid=s)[0]
    amp = rng.normal(0.04, 0.01)
    mu = mu + amp * (np.exp(-((s - 0.025) / 0.01) ** 2) - 0.7 * np.exp(-((s - 0.07) / 0.03) ** 2))
    e = np.zeros(n_brk)
    for i in range(1, n_brk):                        # correlated noise
        e[i] = 0.5 * e[i - 1] + rng.normal(0, noise)
    s_pre = np.clip(rng.normal(-0.002, 0.002, n_pre), -0.007, 0.001)
    mu_pre = 0.02 + rng.normal(0, 0.003, n_pre)
    return np.r_[s_pre, s], np.r_[mu_pre, mu + e]


def generate(out_dir, seed=7, n_noise=0.003):
    """Create master.xlsx and curves/ under out_dir. Returns a dict of the
    planted issues and the true (noise-free) landmarks for verification."""
    rng = np.random.default_rng(seed)
    os.makedirs(os.path.join(out_dir, "curves"), exist_ok=True)

    # --- compounds (32 groups; 5 of them used in more than one size) -------
    n_groups = 32
    comp = {}
    for g in range(1, n_groups + 1):
        tg = rng.normal(-22, 6)
        t0 = 0.28 + 0.010 * (tg + 22) + rng.normal(0, 0.03)
        t60 = 0.12 + rng.normal(0, 0.018)
        e0 = rng.normal(22, 5)
        comp[f"G{g:02d}"] = dict(
            Tg_C=tg, TanD_0C=t0, TanD_20C=0.6 * t0 + 0.4 * t60 + rng.normal(0, 0.01),
            TanD_60C=t60, Ep_0C=e0, Ep_20C=e0 * 0.55 + rng.normal(0, 1), Ep_60C=e0 * 0.30 + rng.normal(0, 0.6),
            M300_MPa=rng.normal(10, 1.5),
            hidden=rng.normal(0, 0.035),             # grip not explained by DMA
            C_dry=1.60 + rng.normal(0, 0.04), C_wet=1.85 + rng.normal(0, 0.05))
        for k in ("0", "20", "60"):
            comp[f"G{g:02d}"][f"Epp_{k}C"] = comp[f"G{g:02d}"][f"Ep_{k}C"] * comp[f"G{g:02d}"][f"TanD_{k}C"]
    missing_m300 = {"G07", "G13", "G21", "G29"}

    # --- tyres: (size, pattern, group) ---------------------------------------
    tyres, gi = [], 0
    shared = ["G01", "G02", "G03", "G04", "G05"]   # reused in a second size
    for si, (size, n, nt) in enumerate(SIZES):
        for t in range(nt):
            if si > 0 and t == nt - 1 and si <= 5:
                grp = shared[si - 1]
            else:
                gi += 1
                grp = f"G{gi:02d}"
            tyres.append(dict(size=size, pattern=f"P{rng.integers(1, 9)}", group=grp,
                              ratio=rng.normal(0.70, 0.03), void=rng.normal(0.30, 0.03),
                              hidden_tyre=rng.normal(0, 0.012)))
    # make sure group count is exactly 32
    assert len({t["group"] for t in tyres}) == n_groups, len({t["group"] for t in tyres})

    # --- configurations and repeats ------------------------------------------
    # The first tyre of the largest size is the REFERENCE tyre: run in blocks
    # of 6 at the start of every test session (one config per surface), as
    # in the real master. Candidate configs are tested in blocks of <= 6.
    loads, ips, speeds = [4400, 5500, 6500, 7400], [200, 220, 240, 260], [64, 72, 80]
    ref_tyre = tyres[0]
    ref_cfg = {sf: dict(tyre=ref_tyre, surf=sf, load=5500, ip=220, speed=72, v=0.0, ref=True)
               for sf in ("Dry", "Wet")}
    blocks = {"Dry": [], "Wet": []}
    for size, n, nt in SIZES:
        ts = [t for t in tyres if t["size"] == size and t is not ref_tyre]
        cfgs = []
        for t in ts:
            for surf in ("Dry", "Wet"):
                for _ in range(1 + (rng.random() < 0.35)):
                    cfgs.append(dict(tyre=t, surf=surf, load=int(rng.choice(loads)),
                                     ip=int(rng.choice(ips)), speed=int(rng.choice(speeds)),
                                     v=rng.normal(0, 0.012), ref=False))
        if n >= 150:
            n -= 100                            # 55 dry + 45 wet reference tests
        k = len(cfgs)
        while 3 * k > n:                        # small sizes: fewer configs
            cfgs.pop(); k -= 1
        for c, m in zip(cfgs, _split(n, k, rng)):
            while m > 0:
                b = min(m, 6) if m != 7 else 4
                blocks[c["surf"]].append((c, b)); m -= b
    sessions = []
    for sf, n_ref in (("Dry", 55), ("Wet", 45)):
        ref_blocks = [6] * (n_ref // 6) + ([n_ref % 6] if n_ref % 6 else [])
        cand = blocks[sf]
        rng.shuffle(cand)
        parts = np.array_split(np.arange(len(cand)), len(ref_blocks))
        for rb, idx in zip(ref_blocks, parts):
            sessions.append(dict(surf=sf, effect=rng.normal(0, 0.035),
                                 blocks=[(ref_cfg[sf], rb)] + [cand[i] for i in idx]))
    rng.shuffle(sessions)
    rows = []
    for si, ses in enumerate(sessions):
        for c, b in ses["blocks"]:
            for trial in range(1, b + 1):
                rows.append((c, trial, ses["effect"], si))

    # --- tests ---------------------------------------------------------------
    w = {s: float(s[:3]) for s, _, _ in SIZES}
    recs, true_lm = [], []
    curves = {}
    for sid, (c, trial, day, session) in enumerate(rows, start=1):
        t, g = c["tyre"], comp[c["tyre"]["group"]]
        dry = c["surf"] == "Dry"
        tsurf = rng.uniform(22, 42) if dry else rng.uniform(18, 30)
        tair = tsurf - rng.uniform(2, 8)
        load, ip, spd = c["load"], c["ip"], c["speed"]
        width = w[t["size"]]
        cw = 0.80 * width * (1 + rng.normal(0, 0.005))
        area = load / (0.92 * ip / 1000.0) / 100.0 * (1 + rng.normal(0, 0.01))   # cm²
        cl = area * 100.0 / cw
        dl = (load - 5500) / 1000.0
        if dry:
            mu_p = (1.00 + 0.6 * (g["TanD_60C"] - 0.12) + 0.004 * (g["Ep_60C"] - 6.6)
                    - 0.030 * dl - 0.0025 * (tsurf - 32) - 0.0008 * (spd - 72))
            sr_p = 0.11 + 0.008 * dl - 0.0002 * (ip - 230)
            r = 0.80 - 0.0015 * (spd - 72) + 0.4 * (g["TanD_60C"] - 0.12)
            C = g["C_dry"]
        else:
            mu_p = (0.78 + 1.10 * (g["TanD_0C"] - 0.28) - 0.020 * dl - 0.0030 * (spd - 72)
                    + 0.002 * (tsurf - 24))
            sr_p = 0.13 + 0.010 * dl - 0.0002 * (ip - 230)
            r = 0.62 - 0.004 * (spd - 72) + 0.3 * (g["TanD_0C"] - 0.28)
            C = g["C_wet"]
        mu_p += g["hidden"] + t["hidden_tyre"] + c["v"]
        drop = (0.10 if dry else 0.13) + 0.002 * (spd - 72) + rng.normal(0, 0.01)
        true_lm.append((sid, mu_p, sr_p, r * mu_p, r * mu_p - drop, session, day))
        # the test surface on the day shifts every test of the session
        mu_p = mu_p * (1 + day)
        # trial-to-trial scatter (sized like the real repeats: ~0.035 on µ)
        mu_p_t = mu_p + rng.normal(0, 0.030)
        sr_p_t = float(np.clip(sr_p + rng.normal(0, 0.028), 0.04, 0.35))
        r_t = float(np.clip(r + rng.normal(0, 0.035), 0.45, 0.97))
        lock_t = r_t * mu_p_t - drop * (1 + day) + rng.normal(0, 0.02)
        B, C, D, E, _ = params_from_landmarks(mu_p_t, sr_p_t, r_t * mu_p_t, C=C)
        curves[sid] = _rig_curve(rng, (B, C, D, E), lock_t, n_noise)
        d = dict(zip(S.MASTER_COLS, [np.nan] * len(S.MASTER_COLS)))
        d.update(SlNo=sid, TyreSize=t["size"], Brand="Brand A", Pattern=t["pattern"],
                 Group=t["group"], TestSurface="Asphalt", SurfaceType=c["surf"], Load_N=load,
                 IP_kPa=ip, Speed_kph=spd, RimWidth_in=round(width / 25.4 * 0.8 * 2) / 2,
                 Tsurface_C=round(tsurf, 1), Tair_C=round(tair, 1), TrialNo=trial,
                 ContactWidth_mm=round(cw, 1), ContactLen_mm=round(cl, 1),
                 TotalContactArea_cm2=round(area, 1), NetContactArea=round(area * t["ratio"], 1),
                 ContactRatio=round(t["ratio"], 3), VoidFillRatio=round(t["void"], 3),
                 TestIP_kPa=ip, TestLoad_kg=round(load / 9.81))
        for k in S.DMA:
            d[k] = round(g[k], 4)
        if t["group"] in missing_m300:
            d["M300_MPa"] = "-"
        recs.append(d)

    master = pd.DataFrame(recs)[S.MASTER_COLS]

    # --- rig landmarks from the measured curves ------------------------------
    for i, sid in enumerate(master.SlNo):
        sr, mu = curves[sid]
        o = np.argsort(sr, kind="stable")
        dsc = curve_descriptors(sr[o], mu[o])
        master.loc[i, "mu_peak"] = round(dsc["mu_peak"] + rng.normal(0, 0.004), 3)
        master.loc[i, "SR_at_mupeak"] = round(dsc["SR_at_mupeak"] + rng.normal(0, 0.003), 3)
        master.loc[i, "mu_lock"] = round(dsc["mu_lock"] + rng.normal(0, 0.004), 3)

    # --- planted problems ----------------------------------------------------
    issues = {}
    def pick(): return int(rng.integers(1, len(master) + 1))
    used = set()
    def fresh():
        while True:
            k = pick()
            if k not in used and k != 1:
                used.add(k); return k

    k = fresh(); issues["slip_in_percent"] = k
    sr, mu = curves[k]; curves[k] = (sr * 100, mu)
    k = fresh(); issues["negative_sign"] = k
    sr, mu = curves[k]; curves[k] = (-sr, -mu)
    issues["truncated_no_lock"] = []
    for _ in range(3):
        k = fresh(); issues["truncated_no_lock"].append(k)
        sr, mu = curves[k]; keep = np.arange(len(sr)) < np.argmax(sr > 0.6)
        curves[k] = (sr[keep], mu[keep])
    issues["spikes"] = []
    for _ in range(2):
        k = fresh(); issues["spikes"].append(k)
        sr, mu = curves[k]; mu = mu.copy()
        mu[rng.choice(np.where(sr > 0.1)[0], 3, replace=False)] += 0.4
        curves[k] = (sr, mu)
    k = fresh(); issues["very_noisy"] = k
    sr, mu = curves[k]; curves[k] = (sr, mu + rng.normal(0, 0.06, len(mu)))
    k = fresh(); issues["mu_peak_typo_div10"] = k
    master.loc[master.SlNo == k, "mu_peak"] = (master.loc[master.SlNo == k, "mu_peak"] / 10).round(3)
    k = fresh(); issues["mu_peak_mismatch"] = k
    master.loc[master.SlNo == k, "mu_peak"] += 0.12
    k = fresh(); issues["sr_peak_in_percent"] = k
    master.loc[master.SlNo == k, "SR_at_mupeak"] *= 100
    # a genuine anomalous test: curve and master agree, but it is far from its repeats
    big = master.groupby(S.CONFIG_KEYS).SlNo.transform("size")
    cand = [s for s in master.SlNo[(big >= 5) & (big < 40)] if s not in used]
    k = int(rng.choice(cand)); used.add(k); issues["repeat_outlier"] = k
    sr, mu = curves[k]; curves[k] = (sr, mu * 0.82)
    for col in ("mu_peak", "mu_lock"):
        master.loc[master.SlNo == k, col] = (master.loc[master.SlNo == k, col] * 0.82).round(3)
    k = fresh(); issues["missing_file"] = k
    k = fresh(); issues["unreadable_file"] = k
    dup_row = len(master) - 1
    issues["duplicate_id"] = int(master.SlNo.iloc[dup_row - 1])
    master.loc[dup_row, "SlNo"] = master.SlNo.iloc[dup_row - 1]
    issues["extra_file_id"] = 9999

    # --- write -----------------------------------------------------------------
    sheet = pd.concat([pd.DataFrame([UNITS], columns=S.MASTER_COLS), master], ignore_index=True)
    sheet.columns = HEADER
    with pd.ExcelWriter(os.path.join(out_dir, "master.xlsx")) as xw:
        sheet.to_excel(xw, sheet_name="Master File", index=False)
    cdir = os.path.join(out_dir, "curves")
    trial_no = dict(zip(master.SlNo, master.TrialNo))
    for sid, (sr, mu) in curves.items():
        if sid == issues["missing_file"] or sid == len(master):   # dup row has no own file
            continue
        f = os.path.join(cdir, f"{sid}. Trial_{int(trial_no.get(sid, 1))}.xlsx")
        if sid == issues["unreadable_file"]:
            pd.DataFrame({"note": ["export failed", "see rig log"]}).to_excel(f, index=False)
            continue
        pd.DataFrame({"SR": np.round(sr, 5), "Mu": np.round(mu, 5)}).to_excel(f, index=False)
    sr, mu = curves[1]
    pd.DataFrame({"SR": sr, "Mu": mu}).to_excel(os.path.join(cdir, "9999. Trial_1.xlsx"), index=False)
    truth = pd.DataFrame(true_lm, columns=[S.ID_COL, "mu_peak", "SR_at_mupeak", "mu_slide", "mu_lock",
                                           "session", "session_effect"])
    truth.to_csv(os.path.join(out_dir, "ground_truth_landmarks.csv"), index=False)
    return issues


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(generate(sys.argv[1] if len(sys.argv) > 1 else "demo_data"), indent=1))
