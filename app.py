"""Tyre Cross-Section Analysis Tool — Streamlit UI.

Run:  streamlit run app.py

Upload 1–5 DXF cross-section profiles (each component = one closed curve on its
own named layer). The app validates conformance first, then computes per-
component area / weight / centroid / volume / surface / polar MOI, and compares
design iterations against a chosen baseline.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from tyre_analysis.dxf_ingest import load_dxf
from tyre_analysis.pipeline import analyse_loops, COLUMNS
from tyre_analysis.sg_table import DEFAULT_SG_TABLE, normalise_key
from tyre_analysis.validate import validate_dxf, format_report
from tyre_analysis import reporting
from tyre_analysis.geometry import compute_geometry
from tyre_analysis.physics import compute_physics

st.set_page_config(page_title="Tyre Cross-Section Analysis", layout="wide")
st.title("Tyre Cross-Section Analysis")
st.caption(
    "Per-component area, weight, centroid, volume/surface (Pappus), and polar "
    "moment of inertia — with baseline-vs-iteration comparison."
)


# --------------------------------------------------------------------------- #
# Sidebar: inputs
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("1 · Files")
    uploads = st.file_uploader(
        "DXF cross-sections (1–5)", type=["dxf"], accept_multiple_files=True
    )
    st.caption("Each component = one closed curve on its own named layer.")

    st.header("2 · Rotation axis")
    axis_mode = st.radio(
        "Axis X-coordinate", ["Same for all files", "Per file"], index=0
    )
    global_axis = st.number_input(
        "Axis X (mm)", value=0.0, step=1.0,
        help="Tyre centreline. Distance from here drives Pappus volume/surface and polar MOI.",
    )

    st.header("3 · Flagging")
    centroid_tol = st.number_input(
        "Centroid-shift amber tolerance (mm)", value=1.0, min_value=0.0, step=0.5
    )
    flatten_tol = st.number_input(
        "Flatten tolerance (mm)", value=0.01, min_value=0.001, step=0.001, format="%.3f"
    )

if not uploads:
    st.info(
        "Upload DXF files to begin. No files yet? Generate demo profiles with "
        "`python scripts/make_sample_dxf.py` and upload `sample_data/baseline.dxf` "
        "and `sample_data/iteration1.dxf`."
    )
    st.stop()

if len(uploads) > 5:
    st.warning("More than 5 files uploaded; only the first 5 are used.")
    uploads = uploads[:5]


# Persist uploads to temp files (ezdxf needs a path/stream).
def _spool(upload) -> Path:
    tmp = Path(tempfile.gettempdir()) / f"tyre_{upload.name}"
    tmp.write_bytes(upload.getbuffer())
    return tmp

file_paths = {u.name: _spool(u) for u in uploads}
labels = list(file_paths.keys())

baseline = st.sidebar.selectbox("Baseline file", labels, index=0)

# Per-file axis inputs.
if axis_mode == "Per file":
    axis_x = {}
    st.sidebar.subheader("Per-file axis X")
    for lbl in labels:
        axis_x[lbl] = st.sidebar.number_input(f"Axis X · {lbl}", value=float(global_axis), key=f"ax_{lbl}")
else:
    axis_x = global_axis


# --------------------------------------------------------------------------- #
# Validation gate
# --------------------------------------------------------------------------- #
st.header("Conformance check")
reports = {lbl: validate_dxf(p) for lbl, p in file_paths.items()}
nonconforming = [lbl for lbl, r in reports.items() if not r.conforms]

cols = st.columns(len(labels))
for col, lbl in zip(cols, labels):
    r = reports[lbl]
    with col:
        st.metric(lbl, "✅ conforms" if r.conforms else "⛔ non-conforming")
        with st.expander("details", expanded=not r.conforms):
            st.code(format_report(r))

if nonconforming:
    st.error(
        "These files don't follow the CAD convention (each component = one closed "
        "curve on its own named layer): "
        f"**{', '.join(nonconforming)}**. Fix them in CAD (put components on named "
        "layers, run BOUNDARY to close each, export raw curves not REGIONs) and "
        "re-upload. Numbers are only shown for conforming files."
    )

conforming = [lbl for lbl in labels if reports[lbl].conforms]
if not conforming:
    st.stop()
if baseline not in conforming:
    baseline = conforming[0]
    st.info(f"Baseline reset to first conforming file: {baseline}")


# --------------------------------------------------------------------------- #
# Editable SG table
# --------------------------------------------------------------------------- #
st.header("Specific gravity table")
st.caption("Placeholder densities (g/cm³) — replace with real compound values. Matched case-insensitively by layer name.")

# Seed the table with defaults plus any layers found in the files.
found_layers = sorted({
    lay for lbl in conforming for lay in reports[lbl].component_layers
})
sg_seed = {normalise_key(k): v for k, v in DEFAULT_SG_TABLE.items()}
for lay in found_layers:
    sg_seed.setdefault(normalise_key(lay), DEFAULT_SG_TABLE.get(normalise_key(lay), 1.10))
sg_df = pd.DataFrame(
    [{"component": k, "sg_g_cm3": v} for k, v in sorted(sg_seed.items())]
)
edited = st.data_editor(sg_df, num_rows="dynamic", use_container_width=True, key="sg_editor")
sg_table = {normalise_key(row["component"]): float(row["sg_g_cm3"])
            for _, row in edited.iterrows() if row["component"]}


# --------------------------------------------------------------------------- #
# Analyse
# --------------------------------------------------------------------------- #
frames, geoms_by_file, all_warnings = [], {}, {}
for lbl in conforming:
    ax = axis_x[lbl] if isinstance(axis_x, dict) else axis_x
    load = load_dxf(file_paths[lbl], flatten_tol=flatten_tol)
    df_i, geoms, warns = analyse_loops(lbl, load.loops, ax, sg_table, fallback_sg=1.10)
    frames.append(df_i)
    geoms_by_file[lbl] = geoms
    msgs = [w.message for w in load.warnings] + warns
    if msgs:
        all_warnings[lbl] = msgs

df = pd.concat(frames, ignore_index=True)

if all_warnings:
    with st.expander("⚠️ Warnings", expanded=False):
        for lbl, msgs in all_warnings.items():
            st.markdown(f"**{lbl}**")
            for m in msgs:
                st.write("•", m)


# --------------------------------------------------------------------------- #
# Tire-level summary
# --------------------------------------------------------------------------- #
st.header("Tire-level summary")
summary = reporting.tire_summary(df, axis_x)
scols = st.columns(len(summary))
base_row = summary[summary["file"] == baseline].iloc[0]
for col, (_, row) in zip(scols, summary.iterrows()):
    with col:
        st.subheader(row["file"] + (" (baseline)" if row["file"] == baseline else ""))
        dw = row["total_weight_g"] - base_row["total_weight_g"]
        st.metric("Total weight (g)", f"{row['total_weight_g']:.1f}",
                  delta=None if row["file"] == baseline else f"{dw:+.1f}")
        st.metric("Total polar MOI (g·mm²)", f"{row['total_polar_MOI']:.3e}")
        st.metric("CG offset from axis (mm)", f"{row['cg_offset_mm']:.2f}")


# --------------------------------------------------------------------------- #
# Component-level comparison table
# --------------------------------------------------------------------------- #
st.header("Component comparison")

comps = sorted(df["component"].unique())
with st.expander("Configure 'good' direction per component (green = improvement)"):
    st.caption("Weight-down is good by default; flip components you intend to increase (e.g. bead reinforcement).")
    good_dir = {}
    gcols = st.columns(min(4, len(comps)) or 1)
    for i, c in enumerate(comps):
        with gcols[i % len(gcols)]:
            good_dir[c] = -1 if st.checkbox(f"{c}: down good", value=True, key=f"gd_{c}") else 1

frame, wcols, ccols = reporting.build_delta_frame(df, baseline, centroid_tol)
styler = reporting.style_delta_frame(frame, wcols, ccols, good_dir, centroid_tol)
st.dataframe(styler, use_container_width=True)
st.caption("Δw = weight change vs baseline (magnitude heatmap). Δcg = centroid-distance shift "
           "(amber when |shift| > tolerance, a balance flag regardless of weight).")


# --------------------------------------------------------------------------- #
# Graphics
# --------------------------------------------------------------------------- #
st.header("Graphics")
g1, g2 = st.columns(2)
with g1:
    st.plotly_chart(reporting.overlay_figure(geoms_by_file, baseline, axis_x), use_container_width=True)
with g2:
    st.plotly_chart(reporting.centroid_map_figure(df, geoms_by_file, baseline), use_container_width=True)

st.plotly_chart(reporting.polar_moi_bar(df), use_container_width=True)

others = [l for l in conforming if l != baseline]
if others:
    final = st.selectbox("Waterfall target (baseline → …)", others, index=len(others) - 1)
    st.plotly_chart(reporting.weight_waterfall(df, baseline, final), use_container_width=True)
else:
    st.info("Upload at least one more conforming file to see the weight-change waterfall.")


# --------------------------------------------------------------------------- #
# Stretch: parametric sensitivity
# --------------------------------------------------------------------------- #
st.header("Parametric sensitivity (offset / scale)")
st.caption(
    "⚠️ Limited to simple uniform gauge offset or area scaling — it CANNOT "
    "substitute for redrawing complex shape changes (e.g. moving a ply turnup). "
    "Use it for quick what-ifs only."
)

sens_file = st.selectbox("File", conforming, key="sens_file")
sens_geoms = {g.layer: g for g in geoms_by_file[sens_file]}
sens_comp = st.selectbox("Component", list(sens_geoms.keys()), key="sens_comp")
mode = st.radio("Perturbation", ["Gauge offset (mm)", "Area scale (×)"], horizontal=True)

if mode == "Gauge offset (mm)":
    offset = st.slider("Offset (− shrinks, + grows)", -5.0, 5.0, 0.0, 0.1)
    perturbed = sens_geoms[sens_comp].polygon.buffer(offset)
else:
    scale = st.slider("Area scale factor", 0.5, 1.5, 1.0, 0.01)
    from shapely.affinity import scale as _scale
    factor = scale ** 0.5  # linear scale so area scales by `scale`
    perturbed = _scale(sens_geoms[sens_comp].polygon, xfact=factor, yfact=factor)

if perturbed.is_empty or perturbed.area == 0:
    st.warning("Perturbation removed the component entirely.")
else:
    ax = axis_x[sens_file] if isinstance(axis_x, dict) else axis_x
    rings = ([perturbed.exterior.coords[:]]
             if perturbed.geom_type == "Polygon"
             else [g.exterior.coords[:] for g in perturbed.geoms])
    new_geom = compute_geometry(sens_comp, rings, ax)
    sg = sg_table.get(normalise_key(sens_comp), 1.10)
    new_res = compute_physics(new_geom, ax, sg)
    orig = df[(df["file"] == sens_file) & (df["component"] == sens_comp)].iloc[0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Area (mm²)", f"{new_res.area_mm2:.1f}", f"{new_res.area_mm2 - orig['area_mm2']:+.1f}")
    c2.metric("Weight (g)", f"{new_res.weight_g:.1f}", f"{new_res.weight_g - orig['weight_g']:+.1f}")
    c3.metric("Centroid dist (mm)", f"{new_res.centroid_distance_mm:.2f}",
              f"{new_res.centroid_distance_mm - orig['centroid_distance_mm']:+.2f}")
    c4.metric("Polar MOI", f"{new_res.polar_moi_g_mm2:.3e}",
              f"{new_res.polar_moi_g_mm2 - orig['polar_MOI']:+.2e}")

    new_total = df[df["file"] == sens_file]["weight_g"].sum() - orig["weight_g"] + new_res.weight_g
    st.metric(f"Tire total weight for {sens_file} (g)", f"{new_total:.1f}",
              f"{new_total - df[df['file'] == sens_file]['weight_g'].sum():+.1f}")
