"""Cross-verification of the stiffness port against the reference HTML tool.

``verify/tool_v64_reference.js`` holds functions extracted verbatim from
``Tread_Pattern_Stiffness_Estimation_Tool_v6.4.html``.  These tests execute that
JavaScript with node and compare it, number for number, against the Python in
``tread_eval/stiffness.py`` over randomised block geometry.

This is the check that keeps the two implementations from drifting.  If node is
not installed the tests skip rather than fail -- but then the port is unverified,
which is worth knowing, so the skip reason says so.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess

import numpy as np
import pytest

from tread_eval.schema import Block, Sipe
from tread_eval.stiffness import (
    StiffnessParams,
    block_stiffness,
    compute_kz,
    effective_k,
    offset_poly,
    polygon_props,
    shore_e,
    shore_k,
)

REF_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "verify", "tool_v64_reference.js")
NODE = shutil.which("node") or shutil.which("nodejs")

pytestmark = pytest.mark.skipif(
    NODE is None or not os.path.exists(REF_JS),
    reason="node and verify/tool_v64_reference.js are required to verify the port against the reference tool",
)


def run_reference(cases: list[dict]) -> list[dict]:
    """Evaluate the reference JS for a batch of block definitions."""
    script = f"""
const R = require({json.dumps(REF_JS)});
const cases = JSON.parse(process.argv[1]);
const out = cases.map(c => {{
  const d = {{
    vertices: c.vertices, nsd: c.nsd, E: c.E, nu: c.nu,
    draft: c.draft, mode: c.mode,
    explicitSipes: c.sipes || [],
    perfShore: c.shore,
  }};
  const k = R.effectiveK(d);
  const kz = R.computeKz({{...d, nsd: c.nsd}});
  const p = R.polygonProps(c.vertices);
  return {{
    Kx: k.Kx, Ky: k.Ky, Kxy: k.Kxy, G: k.G,
    Kz: kz ? kz.Kz : 0, S: kz ? kz.S : 0, E_eff: kz ? kz.E_eff : 0, A_net: kz ? kz.A_net : 0,
    A: p.A, Ixx: p.Ixx, Iyy: p.Iyy, Ixy: p.Ixy, perimeter: p.perimeter, cx: p.cx, cy: p.cy,
  }};
}});
process.stdout.write(JSON.stringify(out));
"""
    res = subprocess.run(
        [NODE, "-e", script, json.dumps(cases)], capture_output=True, text=True, timeout=180
    )
    if res.returncode != 0:
        raise RuntimeError(f"reference JS failed: {res.stderr[:2000]}")
    return json.loads(res.stdout)


def random_polygon(rng: np.random.Generator, n: int = 0) -> list[list[float]]:
    """A random convex-ish polygon, in the size range of a real tread block."""
    n = n or int(rng.integers(3, 9))
    r = rng.uniform(6.0, 30.0, size=n)
    ang = np.sort(rng.uniform(0, 2 * np.pi, size=n))
    cx, cy = rng.uniform(-20, 20), rng.uniform(-20, 20)
    sx, sy = rng.uniform(0.6, 1.8), rng.uniform(0.6, 1.8)
    return [[float(cx + sx * ri * math.cos(a)), float(cy + sy * ri * math.sin(a))] for ri, a in zip(r, ang)]


def build_cases(seed: int, n: int, with_draft: bool, with_sipes: bool) -> list[dict]:
    rng = np.random.default_rng(seed)
    cases = []
    for _ in range(n):
        verts = random_polygon(rng)
        if polygon_props(verts) is None:
            continue
        nsd = float(rng.uniform(4.0, 12.0))
        c = {
            "vertices": verts,
            "nsd": nsd,
            "E": float(rng.uniform(2.0, 12.0)),
            "nu": 0.49,
            "draft": float(rng.uniform(0.5, 8.0)) if with_draft else 0.0,
            "mode": "parallel" if rng.random() < 0.5 else "free",
            "shore": 60,
        }
        if with_sipes:
            p = np.asarray(verts)
            xmin, xmax = float(p[:, 0].min()), float(p[:, 0].max())
            ymin, ymax = float(p[:, 1].min()), float(p[:, 1].max())
            ext = max(xmax - xmin, ymax - ymin) * 0.2
            k = int(rng.integers(1, 3))
            c["sipes"] = [
                {
                    "p1": [xmin + (i + 1) / (k + 1) * (xmax - xmin), ymin - ext],
                    "p2": [xmin + (i + 1) / (k + 1) * (xmax - xmin), ymax + ext],
                    "depth": float(rng.uniform(0.3, 0.9)) * nsd,
                    "width": 0.5,
                }
                for i in range(k)
            ]
        cases.append(c)
    return cases


def python_side(case: dict) -> dict:
    sipes = [
        Sipe(p1=tuple(s["p1"]), p2=tuple(s["p2"]), depth=s["depth"], width=s["width"])
        for s in case.get("sipes", [])
    ]
    kx, ky, kxy, _ = effective_k(
        case["vertices"], case["nsd"], case["E"], case["nu"],
        case["draft"], case["mode"], sipes,
    )
    kz, s, e_eff, a_net = compute_kz(
        case["vertices"], case["nsd"], case["E"], shore_k(case["shore"]), sipes
    )
    p = polygon_props(case["vertices"])
    return {
        "Kx": kx, "Ky": ky, "Kxy": kxy, "Kz": kz, "S": s, "E_eff": e_eff, "A_net": a_net,
        "A": p.area, "Ixx": p.ixx, "Iyy": p.iyy, "Ixy": p.ixy,
        "perimeter": p.perimeter, "cx": p.cx, "cy": p.cy,
    }


def compare(cases: list[dict], keys: list[str], rel: float = 1e-9, label: str = "") -> None:
    ref = run_reference(cases)
    assert len(ref) == len(cases)
    worst = {}
    for i, (c, r) in enumerate(zip(cases, ref)):
        mine = python_side(c)
        for k in keys:
            a, b = mine[k], r[k]
            scale = max(abs(a), abs(b), 1e-9)
            err = abs(a - b) / scale
            if err > worst.get(k, (0, -1))[0]:
                worst[k] = (err, i)
            assert err < rel, (
                f"{label} case {i} key {k}: python={a!r} reference={b!r} "
                f"rel_err={err:.3e}\nvertices={c['vertices']}\n"
                f"nsd={c['nsd']} E={c['E']} draft={c['draft']} mode={c['mode']}"
            )
    print(f"  {label}: worst relative errors " + ", ".join(f"{k}={v[0]:.2e}" for k, v in worst.items()))


# ---------------------------------------------------------------------------
def test_reference_bundle_is_present():
    assert os.path.exists(REF_JS)
    with open(REF_JS) as fh:
        head = fh.read(400)
    assert "v6.4" in head


def test_polygon_props_match_reference():
    cases = build_cases(seed=1, n=60, with_draft=False, with_sipes=False)
    compare(cases, ["A", "Ixx", "Iyy", "Ixy", "perimeter", "cx", "cy"], rel=1e-10, label="polygonProps")


def test_prismatic_shear_stiffness_matches_reference():
    """No draft, no sipes -- the Castigliano path in its simplest form."""
    cases = build_cases(seed=2, n=60, with_draft=False, with_sipes=False)
    compare(cases, ["Kx", "Ky", "Kxy"], rel=1e-9, label="effectiveK prismatic")


def test_drafted_shear_stiffness_matches_reference():
    """Draft on -- exercises offsetPoly inside the slice integration."""
    cases = build_cases(seed=3, n=60, with_draft=True, with_sipes=False)
    compare(cases, ["Kx", "Ky", "Kxy"], rel=1e-9, label="effectiveK drafted")


def test_siped_shear_stiffness_matches_reference():
    """Sipes on -- exercises the layers-in-series, sub-blocks-in-parallel path."""
    cases = build_cases(seed=4, n=40, with_draft=False, with_sipes=True)
    compare(cases, ["Kx", "Ky", "Kxy"], rel=1e-6, label="effectiveK siped")


def test_drafted_and_siped_matches_reference():
    cases = build_cases(seed=5, n=30, with_draft=True, with_sipes=True)
    compare(cases, ["Kx", "Ky", "Kxy"], rel=1e-6, label="effectiveK drafted+siped")


def test_compression_stiffness_matches_reference():
    cases = build_cases(seed=6, n=60, with_draft=False, with_sipes=False)
    compare(cases, ["Kz", "S", "E_eff", "A_net"], rel=1e-10, label="computeKz")


def test_compression_with_sipes_matches_reference():
    cases = build_cases(seed=7, n=40, with_draft=False, with_sipes=True)
    compare(cases, ["Kz", "S", "E_eff", "A_net"], rel=1e-6, label="computeKz siped")


def test_offset_poly_matches_reference():
    rng = np.random.default_rng(8)
    cases = []
    for _ in range(40):
        cases.append(
            {
                "vertices": random_polygon(rng),
                "nsd": 8.0,
                "E": 6.89,
                "nu": 0.49,
                "draft": float(rng.uniform(-3.0, 10.0)),
                "mode": "free",
                "shore": 60,
            }
        )
    script = f"""
const R = require({json.dumps(REF_JS)});
const cases = JSON.parse(process.argv[1]);
process.stdout.write(JSON.stringify(cases.map(c => R.offsetPoly(c.vertices, c.draft, 3.0))));
"""
    res = subprocess.run([NODE, "-e", script, json.dumps(cases)], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr[:2000]
    ref = json.loads(res.stdout)
    for i, (c, r) in enumerate(zip(cases, ref)):
        mine = offset_poly(c["vertices"], c["draft"], 3.0)
        assert np.allclose(mine, np.asarray(r), rtol=1e-10, atol=1e-10), f"offsetPoly case {i}"


def test_shore_tables_match_reference():
    script = f"""
const R = require({json.dumps(REF_JS)});
const s = [30,35,40,45,50,55,60,65,70];
process.stdout.write(JSON.stringify(s.map(v => [R.shoreE(v), R.shoreK(v)])));
"""
    res = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr[:2000]
    for shore, (e, k) in zip([30, 35, 40, 45, 50, 55, 60, 65, 70], json.loads(res.stdout)):
        assert shore_e(shore) == pytest.approx(e)
        assert shore_k(shore) == pytest.approx(k)


def test_block_stiffness_end_to_end_matches_reference():
    """The public entry point, on a Block, against the reference."""
    rng = np.random.default_rng(9)
    for _ in range(20):
        verts = random_polygon(rng)
        height = float(rng.uniform(5.0, 11.0))
        draft = float(rng.uniform(0.0, 6.0))
        n_sipes = int(rng.integers(0, 3))
        block = Block(
            id="b", pitch_id="p", polygon=[tuple(v) for v in verts], zone="center",
            height=height, draft_angle=draft, n_lateral_sipes=n_sipes,
        )
        params = StiffnessParams(shore_a=60, poisson=0.49, mode="parallel")
        got = block_stiffness(block, params)
        case = {
            "vertices": verts, "nsd": height, "E": shore_e(60), "nu": 0.49,
            "draft": draft, "mode": "parallel", "shore": 60,
            "sipes": [
                {"p1": list(s.p1), "p2": list(s.p2), "depth": s.depth, "width": s.width}
                for s in block.effective_sipes()
            ],
        }
        ref = run_reference([case])[0]
        assert got.kx == pytest.approx(ref["Kx"], rel=1e-6)
        assert got.ky == pytest.approx(ref["Ky"], rel=1e-6)
        assert got.kz == pytest.approx(ref["Kz"], rel=1e-6)
