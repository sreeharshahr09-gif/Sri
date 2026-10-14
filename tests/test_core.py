import numpy as np
import pandas as pd
import pytest

from tyretraction import curves as cv
from tyretraction import evaluation as ev
from tyretraction import io
from tyretraction import pacejka as pj
from tyretraction import quality as q
from tyretraction import sessions as ss


def test_landmarks_to_params_reproduce_curve():
    rng = np.random.default_rng(0)
    for _ in range(40):
        P = np.array([[rng.uniform(6, 30), rng.uniform(1.3, 2.0), rng.uniform(0.5, 1.2), rng.uniform(-1, 0.95)]])
        L = pj.landmarks_from_params(P).iloc[0]
        B, C, D, E, res = pj.params_from_landmarks(L.mu_peak, L.SR_at_mupeak, L.mu_slide, C=P[0, 1])
        err = np.abs(pj.mf_matrix([[B, C, D, E]]) - pj.mf_matrix(P))[:, cv.GRID <= 0.9].max()
        assert err < 0.01


def test_assembled_curve_passes_landmarks():
    P = np.array([[18.0, 1.7, 0.72, 0.8]])
    c = pj.assemble_curves(P, [0.43])[0]
    assert abs(c.max() - 0.72) < 1e-3                         # D = peak
    assert abs(c[cv.GRID >= 0.95].mean() - 0.43) < 0.02       # lock window mean ~ µ lock


def test_fit_recovers_params():
    s = np.linspace(0, 1, 300)
    mu = pj.mf(s, 15, 1.6, 0.9, 0.5) + np.random.default_rng(1).normal(0, 0.003, 300)
    f = pj.fit_curve(s, mu)
    assert abs(f["D"] - 0.9) < 0.02 and f["fit_rmse"] < 0.01


def _write_curve(path, sr, mu, header=True):
    pd.DataFrame({"SR": sr, "Mu": mu}).to_excel(path, index=False, header=header)


def test_read_curve_handles_prebrake_percent_and_sign(tmp_path):
    rng = np.random.default_rng(2)
    pre = rng.uniform(-0.007, 0.001, 190)              # rolling phase, slightly negative slip
    brk = np.linspace(0.005, 0.99, 50)
    sr = np.r_[pre, brk]
    mu = np.r_[np.full(190, 0.02), pj.mf(brk, 18, 1.7, 0.7, 0.8)]
    _write_curve(tmp_path / "1. Trial_1.xlsx", sr, mu)
    s1, m1, notes = io.read_curve_file(str(tmp_path / "1. Trial_1.xlsx"))
    assert notes == [] and s1.max() > 0.98             # majority-negative pre-brake must not flip the sign
    _write_curve(tmp_path / "2. Trial_1.xlsx", -sr * 100, -mu)
    s2, m2, notes = io.read_curve_file(str(tmp_path / "2. Trial_1.xlsx"))
    assert set(notes) == {"negative_slip_sign", "negative_mu_sign", "slip_in_percent"}
    assert np.allclose(np.sort(s2), np.sort(s1), atol=1e-6)


def test_match_by_leading_id(tmp_path):
    for name in ["1. Trial_3.xlsx", "12. Trial_1.xlsx", "99. Trial_2.xlsx", "notes.xlsx"]:
        _write_curve(tmp_path / name, [0, 0.1, 0.2], [0, 0.5, 0.6])
    m, rep = io.match_curve_files(str(tmp_path), [1, 12, 13])
    assert sorted(m.SlNo) == [1, 12]
    assert rep["master_without_file"] == [13]
    assert rep["files_without_master"] == ["99. Trial_2.xlsx"]
    assert rep["files_without_id"] == ["notes.xlsx"]


def test_spike_detection_on_sparse_curve():
    s = np.r_[np.zeros(150), np.linspace(0.005, 0.99, 50)]
    mu = np.r_[np.full(150, 0.02), pj.mf(np.linspace(0.005, 0.99, 50), 18, 1.7, 0.7, 0.8)]
    assert cv._spike_mask(s, mu).sum() == 0
    mu2 = mu.copy()
    mu2[170] += 0.4
    mask = cv._spike_mask(s, mu2)
    assert mask[170] and mask.sum() == 1


def _toy_master(n_cfg=30, reps=4, seed=0):
    rng = np.random.default_rng(seed)
    rows, sid = [], 0
    sizes = ["205/55R16", "225/50R19", "195/65R15"]
    for c in range(n_cfg):
        size = sizes[c % 3]
        grp = f"G{c % 12}"
        for t in range(reps):
            sid += 1
            rows.append(dict(SlNo=sid, TyreSize=size, Pattern="P1", Group=grp,
                             SurfaceType="Dry" if c % 2 else "Wet", Load_N=4000 + 500 * (c % 4),
                             IP_kPa=220, Speed_kph=72, Tsurface_C=30, Tair_C=25,
                             mu_peak=0.8 + 0.1 * (c % 2) + rng.normal(0, 0.02)))
    return io.add_keys(pd.DataFrame(rows))


@pytest.mark.parametrize("case", ["A", "B", "C"])
def test_splits_never_leak(case):
    df = _toy_master()
    folds, _ = ev.make_splits(df, case)
    key = {"A": "config_id", "B": "size_id", "C": "compound_id"}[case]
    for tr, te in folds:
        assert not set(df[key].iloc[tr]) & set(df[key].iloc[te])
        if case == "A":
            assert set(df["tyre_id"].iloc[te]) <= set(df["tyre_id"].iloc[tr])
        if case == "B":
            assert set(df["compound_id"].iloc[te]) <= set(df["compound_id"].iloc[tr])


def test_config_mean_training_set():
    df = _toy_master()
    X = io.build_features(df)
    ds = ev.Dataset(df, X, {"raw": df[["mu_peak"]]})
    Xtr, ytr, w = ev._train_set(ds, np.arange(len(df)), df.mu_peak.to_numpy(), "config_mean", ["IsDry", "Load_N"])
    assert len(ytr) == df.config_id.nunique() and w is None


def test_repeat_outlier_found():
    df = _toy_master(reps=6)
    df.loc[3, "mu_peak"] -= 0.2
    rows = q.check_repeats(df, targets=("mu_peak",))
    ids = [r["ID"] for r in rows if r["check"] == "repeat_outlier"]
    assert ids == [df.loc[3, "SlNo"]]


def test_session_correction_recovers_day_effect():
    rng = np.random.default_rng(3)
    rows, sid, truth = [], 0, {}
    for session in range(8):
        day = rng.normal(0, 0.04)
        for cfg, n in [("REF", 6), (f"C{session}a", 5), (f"C{session}b", 5)]:
            for _ in range(n):
                sid += 1
                base = 0.8 if cfg == "REF" else 0.75
                rows.append(dict(SlNo=sid, TyreSize="225/50R19", Pattern=cfg, Group=cfg, SurfaceType="Wet",
                                 Load_N=5500, IP_kPa=220, Speed_kph=72,
                                 mu_peak=base * (1 + day) + rng.normal(0, 0.005)))
                truth[sid] = day
    df = io.add_keys(pd.DataFrame(rows))
    ref = ss.detect_reference(df)
    assert list(ref.values()) == ["225/50R19|REF|REF"]
    fac, _, _ = ss.correction_factors(df, df.set_index("SlNo")[["mu_peak"]], ref, "previous")
    est = 1 / fac["mu_peak"] - 1
    assert np.corrcoef(est.to_numpy(), pd.Series(truth).reindex(est.index).to_numpy())[0, 1] > 0.95
