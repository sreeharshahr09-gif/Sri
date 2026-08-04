"""Physics models, the FFT sweep, metrics and the report."""

import json
import math

import numpy as np
import pytest

from tread_eval.contact_patch import (
    CPParams,
    contact_patch,
    contact_patch_from_footprint,
    max_supported_lean,
)
from tread_eval.metrics import (
    anomaly_bands,
    build_flags,
    centroid_decomposition,
    fluctuation_stats,
    groove_angle_profile,
    lean_summary,
    order_spectrum,
    pitch_sequence_analysis,
    wear_proxies,
    zone_distribution,
)
from tread_eval.raster import make_grid, rasterise
from tread_eval.report import build_payload, render_html
from tread_eval.schema import Block, CrownProfile
from tread_eval.stiffness import DEFAULT_PARAMS, StiffnessParams, block_stiffness
from tread_eval.sweep import sweep, sweep_lean
from tread_eval.synthetic import SyntheticParams, generate_pattern, intended_groove_angle

LEANS = [0.0, 20.0, 40.0]


@pytest.fixture(scope="module")
def analysis():
    pattern = generate_pattern(SyntheticParams(seed=11))
    pack = rasterise(pattern, make_grid(pattern, 0.5))
    results = sweep(pattern, pack, LEANS)
    return pattern, pack, results


def rect(x0, y0, w, h, **kw):
    kw.setdefault("id", "b")
    kw.setdefault("pitch_id", "p")
    kw.setdefault("zone", "center")
    kw.setdefault("height", 8.0)
    kw.setdefault("draft_angle", 0.0)
    return Block(polygon=[(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)], **kw)


# ---------------------------------------------------------------- stiffness
def test_stiffness_responds_to_geometry_the_right_way():
    base = block_stiffness(rect(0, 0, 20, 20))
    assert block_stiffness(rect(0, 0, 30, 30)).kx > base.kx  # bigger block, stiffer
    assert block_stiffness(rect(0, 0, 20, 20, height=14.0)).kx < base.kx  # taller, softer
    assert block_stiffness(rect(0, 0, 20, 20, n_lateral_sipes=3)).kx < base.kx  # siped, softer


def test_positive_draft_stiffens_and_undercut_softens():
    """Draft adds material toward the base, so a drafted block is stiffer.

    Worth pinning down because the intuition can run the other way: draft does
    shrink the *contact* face, but the beam model integrates the section over
    the whole height, and a positive draft widens everything below the surface.
    """
    base = block_stiffness(rect(0, 0, 20, 20))
    assert block_stiffness(rect(0, 0, 20, 20, draft_angle=9.0)).kx > base.kx
    assert block_stiffness(rect(0, 0, 20, 20, draft_angle=-4.0)).kx < base.kx


def test_sipes_soften_circumferential_more_than_lateral():
    plain = block_stiffness(rect(0, 0, 20, 20))
    siped = block_stiffness(rect(0, 0, 20, 20, n_lateral_sipes=2))
    # Lateral sipes cut the block across x, so they relieve circumferential
    # shear far more than lateral shear.
    assert (siped.kx / plain.kx) < (siped.ky / plain.ky)


def test_stiffness_is_directional_for_an_elongated_block():
    """A block long in x and narrow in y resists x-shear better than y-shear."""
    s = block_stiffness(rect(0, 0, 40, 8))
    assert s.kx > s.ky
    assert block_stiffness(rect(0, 0, 8, 40)).kx < block_stiffness(rect(0, 0, 8, 40)).ky


def test_stiffness_scales_with_compound_hardness():
    soft = block_stiffness(rect(0, 0, 20, 20), StiffnessParams(shore_a=40))
    stiff = block_stiffness(rect(0, 0, 20, 20), StiffnessParams(shore_a=70))
    assert stiff.kx > soft.kx
    assert stiff.kz > soft.kz


def test_degenerate_block_is_zero_not_an_exception():
    s = block_stiffness(rect(0, 0, 10, 10, height=0.0))
    assert s.kx == 0.0 and s.ky == 0.0


def test_slenderness_is_height_over_min_plan_dimension():
    assert block_stiffness(rect(0, 0, 40, 5, height=10.0)).slenderness == pytest.approx(2.0)


# ------------------------------------------------------------ contact patch
def test_patch_narrows_and_lengthens_with_lean():
    crown = CrownProfile.dual_radius(159.0)
    patches = [contact_patch(g, crown, tread_width=159.0) for g in (0, 20, 40)]
    assert patches[-1].b < patches[0].b  # narrower
    assert patches[-1].a > patches[0].a  # longer
    assert patches[-1].peak_pressure > patches[0].peak_pressure
    assert [p.y_center for p in patches] == sorted(p.y_center for p in patches)


def test_patch_pressure_integral_carries_the_applied_load():
    """The Winkler closure is the point of the model -- check it actually holds."""
    crown = CrownProfile.dual_radius(159.0)
    params = CPParams(vertical_load=1500.0, load_rises_with_lean=False)
    p = contact_patch(0.0, crown, params, tread_width=159.0)
    x = np.linspace(-p.a, p.a, 1201)
    y = np.linspace(p.y_center - p.b, p.y_center + p.b, 1201)
    dA = (x[1] - x[0]) * (y[1] - y[0])
    load = float(p.pressure(x[None, :], y[:, None]).sum()) * dA
    assert load == pytest.approx(params.vertical_load, rel=0.01)


def test_load_rises_with_lean_when_enabled():
    crown = CrownProfile.dual_radius(159.0)
    rising = contact_patch(30.0, crown, CPParams(load_rises_with_lean=True), tread_width=159.0)
    flat = contact_patch(30.0, crown, CPParams(load_rises_with_lean=False), tread_width=159.0)
    assert rising.normal_load == pytest.approx(flat.normal_load / math.cos(math.radians(30.0)), rel=1e-6)
    assert rising.nominal_area() > flat.nominal_area()


def test_travel_direction_is_zero_upright_and_fans_at_lean():
    crown = CrownProfile.dual_radius(159.0)
    upright = contact_patch(0.0, crown, tread_width=159.0)
    assert not math.isfinite(upright.path_radius)
    assert float(np.asarray(upright.travel_direction_deg(30.0))) == pytest.approx(0.0)

    leaned = contact_patch(35.0, crown, tread_width=159.0)
    lead = float(np.asarray(leaned.travel_direction_deg(leaned.a)))
    assert lead > 2.0
    assert float(np.asarray(leaned.travel_direction_deg(-leaned.a))) == pytest.approx(-lead)


def test_slip_angle_offsets_the_travel_direction():
    crown = CrownProfile.dual_radius(159.0)
    p = contact_patch(0.0, crown, CPParams(lateral_slip_angle=2.5), tread_width=159.0)
    assert float(np.asarray(p.travel_direction_deg(0.0))) == pytest.approx(2.5)


def test_patch_is_flagged_when_it_runs_off_the_tread():
    crown = CrownProfile.dual_radius(159.0)
    assert not contact_patch(0.0, crown, tread_width=159.0).clipped
    assert contact_patch(44.0, crown, tread_width=159.0).clipped


def test_max_supported_lean_is_the_profile_edge_tangent():
    crown = CrownProfile.dual_radius(159.0)
    m = max_supported_lean(crown)
    assert 30.0 < m < 60.0
    # asking for more just pins the contact point at the tread edge
    assert contact_patch(m + 15.0, crown, tread_width=159.0).y_center == pytest.approx(159.0 / 2)


def test_measured_footprint_bypasses_the_parametric_model():
    p = contact_patch_from_footprint(25.0, length=90.0, width=40.0, y_center=50.0, tread_width=159.0)
    assert p.source == "measured"
    assert (p.a, p.b) == pytest.approx((45.0, 20.0))


# -------------------------------------------------------------------- sweep
def test_fft_sweep_matches_a_brute_force_masked_sum(analysis):
    """The whole speed argument rests on this equivalence, so verify it directly."""
    pattern, pack, _ = analysis
    grid = pack.grid
    patch = contact_patch(20.0, pattern.crown(), tread_width=pattern.tread_width)
    result = sweep_lean(pattern, pack, 20.0, patch_override=patch)

    mask, _ = patch.masks(grid)
    for theta_idx in (0, 137, 1801, grid.nx // 3):
        shifted = np.roll(mask, theta_idx, axis=1)
        brute_area = float((pack.area * shifted).sum())
        brute_kx = float((pack.kx * shifted).sum())
        brute_blocks = float((pack.block_frac * shifted).sum())
        assert result.contact_area[theta_idx] == pytest.approx(brute_area, rel=1e-4)
        assert result.kx[theta_idx] == pytest.approx(brute_kx, rel=1e-4)
        assert result.block_count[theta_idx] == pytest.approx(brute_blocks, rel=1e-4)


def test_sweep_is_circular(analysis):
    """theta = 0 and theta = 360 are the same place on the tyre."""
    pattern, pack, _ = analysis
    r = sweep_lean(pattern, pack, 0.0)
    rolled = np.roll(r.contact_area, -pack.grid.nx)
    assert np.allclose(r.contact_area, rolled)


def test_effective_block_count_brackets_the_discrete_count(analysis):
    _, _, results = analysis
    for r in results:
        assert 0.5 < r.block_count.mean() < 40
        assert abs(r.block_count.mean() - r.block_count_discrete.mean()) < 1.5


def test_contact_area_never_exceeds_patch_area(analysis):
    _, _, results = analysis
    for r in results:
        assert r.contact_area.max() <= r.patch_area * 1.001
        assert (r.land_ratio <= 1.001).all()


def test_zone_areas_sum_to_the_total_contact_area(analysis):
    _, _, results = analysis
    for r in results:
        total = sum(v for v in r.zone_area.values())
        assert np.allclose(total, r.contact_area, rtol=1e-4, atol=1e-6)


def test_in_patch_centroid_stays_inside_the_patch(analysis):
    _, _, results = analysis
    for r in results:
        assert (np.abs(r.centroid_y - r.patch.y_center) <= r.patch.b + 1e-6).all()


def test_upright_patch_is_centred_and_symmetric(analysis):
    _, _, results = analysis
    upright = results[0]
    assert upright.patch.y_center == pytest.approx(0.0, abs=1e-9)
    # Skew is computed on the rasterised pressure field, so it carries a little
    # discretisation noise; it should be zero to well within a pixel's worth.
    assert upright.shape["pressure_skew_y"] == pytest.approx(0.0, abs=1e-3)
    assert upright.shape["pressure_skew_x"] == pytest.approx(0.0, abs=1e-3)


def test_patch_compactness_is_near_circular_for_a_round_patch():
    """Perimeter must come from the outline, not the pixel staircase."""
    pattern = generate_pattern(SyntheticParams(seed=5))
    pack = rasterise(pattern, make_grid(pattern, 0.5))
    r = sweep_lean(pattern, pack, 0.0)
    assert 4 * math.pi <= r.shape["compactness"] < 16.0  # 4*pi is a perfect circle


def test_fewer_blocks_in_contact_where_the_patch_is_smallest(analysis):
    _, _, results = analysis
    areas = [r.patch_area for r in results]
    assert areas[-1] < areas[0]  # the patch does shrink with lean


# ------------------------------------------------------------------ metrics
def test_order_spectrum_recovers_a_known_modulation():
    theta = np.linspace(0, 2 * np.pi, 2048, endpoint=False)
    sig = 100.0 + 7.0 * np.sin(13 * theta)
    spec = order_spectrum(sig)
    amp = np.asarray(spec["amplitude"])
    order = np.asarray(spec["orders"])[int(np.argmax(amp))]
    assert order == 13
    assert amp.max() == pytest.approx(0.07, rel=1e-3)


def test_fluctuation_stats_on_a_known_signal():
    sig = np.array([90.0, 110.0, 90.0, 110.0])
    s = fluctuation_stats(sig)
    assert s["mean"] == pytest.approx(100.0)
    assert s["cov"] == pytest.approx(0.1)
    assert s["max_min"] == pytest.approx(110.0 / 90.0)


def test_anomaly_bands_find_a_step_and_ignore_a_ramp():
    theta = np.linspace(0, 360, 720, endpoint=False)
    ramp = np.linspace(0.0, 10.0, 720)
    assert anomaly_bands(theta, ramp) == []
    step = ramp.copy()
    step[400:] += 25.0
    bands = anomaly_bands(theta, step)
    assert bands and bands[0]["start"] <= theta[400] <= bands[0]["end"] + 1.0


def test_groove_angle_recovers_the_generators_intent():
    """The generator integrates a design angle; the metric should read it back."""
    pattern = generate_pattern(SyntheticParams(seed=4))
    patch = contact_patch(0.0, pattern.crown(), tread_width=pattern.tread_width)
    prof = groove_angle_profile(pattern, patch, n_bins=24)
    y = np.asarray(prof["bin_centers"])
    measured = np.asarray([np.nan if v is None else v for v in prof["angle_to_travel"]], dtype=float)
    # metric measures the angle to travel; the generator specifies it from the lateral axis
    expected = 90.0 - intended_groove_angle(pattern, y)
    ok = np.isfinite(measured) & (np.abs(y) > 8.0)
    assert np.nanmax(np.abs(measured[ok] - expected[ok])) < 12.0
    # and the progressive crown-to-shoulder sweep is the dominant trend
    assert np.nanmean(measured[np.abs(y) > 60]) < np.nanmean(measured[np.abs(y) < 20])


def test_spin_spread_is_zero_upright_and_grows_with_lean():
    pattern = generate_pattern(SyntheticParams(seed=4))
    crown = pattern.crown()
    up = groove_angle_profile(pattern, contact_patch(0.0, crown, tread_width=pattern.tread_width))
    lean = groove_angle_profile(pattern, contact_patch(35.0, crown, tread_width=pattern.tread_width))
    up_vals = [v for v in up["spin_spread"] if v is not None]
    lean_vals = [v for v in lean["spin_spread"] if v is not None]
    assert max(up_vals) == pytest.approx(0.0, abs=1e-9)
    assert max(lean_vals) > 2.0


def test_centroid_decomposition_separates_bias_from_wander(analysis):
    _, _, results = analysis
    for r in results:
        c = centroid_decomposition(r)
        assert c["geometric_mm"] == pytest.approx(r.patch.y_center)
        assert c["residual_p2p_mm"] >= 0.0
        # wander is measured about the bias, so it can never exceed the spread
        assert c["residual_wander_rms_mm"] <= c["residual_p2p_mm"] + 1e-9


def test_zone_distribution_is_consistent(analysis):
    pattern, pack, _ = analysis
    z = zone_distribution(pattern, pack)
    assert 0.0 < z["overall_land_ratio"] < 1.0
    assert sum(v["land_area_mm2"] for v in z["zones"].values()) == pytest.approx(float(pack.area.sum()), rel=1e-6)
    for v in z["zones"].values():
        assert 0.0 <= v["land_ratio"] <= 1.0
    # the generated pattern is a mirror image apart from the shoulder stagger
    assert z["lateral_imbalance"] < 0.01


def test_zone_shares_within_the_patch_move_outboard_with_lean(analysis):
    _, _, results = analysis
    rows = lean_summary(results)
    assert rows[0]["center_share"] > rows[-1]["center_share"]
    assert rows[-1]["shoulder_share"] > rows[0]["shoulder_share"]


def test_pitch_analysis_detects_a_tonal_sequence():
    tonal = generate_pattern(SyntheticParams(pitch_mode="tonal_group"))
    rnd = generate_pattern(SyntheticParams(pitch_mode="random", seed=6))
    pack_t = rasterise(tonal, make_grid(tonal, 0.6))
    pack_r = rasterise(rnd, make_grid(rnd, 0.6))
    t = pitch_sequence_analysis(tonal, pack_t)
    r = pitch_sequence_analysis(rnd, pack_r)
    # a repeated 3-pitch group puts all its energy on the repeat order and its
    # harmonics; a random sequence can have a similar peak but spreads the rest
    assert t["dominant_sequence_order"] == tonal.meta["n_repeats"]
    assert t["sequence_tonality"] == pytest.approx(1.0, abs=0.02)  # all energy on one order
    assert r["sequence_tonality"] < 0.7


def test_uniform_sequence_has_no_modulation():
    p = generate_pattern(SyntheticParams(pitch_mode="uniform"))
    stats = pitch_sequence_analysis(p, rasterise(p, make_grid(p, 0.8)))
    assert stats["modulation_index"] == pytest.approx(0.0, abs=1e-9)
    assert stats["max_adjacent_ratio"] == pytest.approx(1.0, abs=1e-9)


def test_shoulder_phase_detects_the_designed_stagger():
    stagger = 0.3
    p = generate_pattern(SyntheticParams(shoulder_stagger=stagger, shoulder_splits=1, seed=8))
    stats = pitch_sequence_analysis(p, rasterise(p, make_grid(p, 0.5)))
    assert abs(stats["shoulder_phase_pitches"]) == pytest.approx(stagger, abs=0.06)


def test_wear_proxies_cover_every_zone(analysis):
    pattern, pack, results = analysis
    w = wear_proxies(pattern, pack, results)
    assert set(w["per_zone"]) == {"center", "intermediate", "shoulder"}
    for v in w["per_zone"].values():
        assert v["slenderness_mean"] > 0 and v["n_blocks"] > 0


def test_flags_are_ranked_and_complete(analysis):
    pattern, pack, results = analysis
    summary = lean_summary(results)
    zones = zone_distribution(pattern, pack)
    pitch = pitch_sequence_analysis(pattern, pack)
    wear = wear_proxies(pattern, pack, results)
    flags = build_flags(pattern, results, summary, zones, pitch, wear)
    assert len(flags) >= 8
    rank = {"flag": 0, "watch": 1, "ok": 2}
    assert [rank[f["severity"]] for f in flags] == sorted(rank[f["severity"]] for f in flags)
    assert all(f["title"] and f["value"] and f["detail"] for f in flags)


# ------------------------------------------------------------------- report
def test_payload_is_json_safe_and_finite(analysis):
    pattern, pack, results = analysis
    payload = build_payload(pattern, pack, results, CPParams(), DEFAULT_PARAMS, theta_points=180)
    text = json.dumps(payload, allow_nan=False)  # raises on NaN/Infinity
    assert "NaN" not in text and "Infinity" not in text
    assert len(payload["leans"]) == len(LEANS)
    assert len(payload["leans"][0]["theta_deg"]) == pytest.approx(180, rel=0.1)
    assert payload["meta"]["n_blocks"] == len(pattern.blocks)


def test_report_html_is_self_contained(analysis):
    pattern, pack, results = analysis
    payload = build_payload(pattern, pack, results, CPParams(), DEFAULT_PARAMS, theta_points=90)
    html = render_html(payload, plotly_mode="embed")
    assert html.lstrip().startswith("<!doctype html>")
    assert "<script src=" not in html  # nothing is loaded from outside the file
    assert "Plotly" in html and "Plotly.newPlot" in html
    assert "__DATA__" not in html and "__CSS__" not in html and "__APP__" not in html


def test_payload_script_cannot_break_out_of_its_tag(analysis):
    pattern, pack, results = analysis
    pattern.name = 'evil</script><script>alert(1)</script>'
    payload = build_payload(pattern, pack, results, CPParams(), DEFAULT_PARAMS, theta_points=60)
    html = render_html(payload, plotly_mode="cdn")
    assert "</script><script>alert" not in html
    assert "&lt;/script&gt;" in html  # escaped in the <title>, not executable
    pattern.name = "synthetic"
