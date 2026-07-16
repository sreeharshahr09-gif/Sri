"""Manual loop builder — for DXFs that DON'T follow the layer convention.

Reconstructs the drawing from raw curves, auto-detects candidate closed faces
(like AutoCAD BOUNDARY's "pick internal point"), and lets you lasso/click the
faces that make up each component, name them, and compute the same per-component
results as the main page.

Workflow:
  1. Upload one non-conforming DXF.
  2. Set the rotation axis and pick which half to work on.
  3. Lasso/box-select the face(s) for a component, type a name, click Assign.
  4. Repeat for every component, then Compute.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from tyre_analysis.reconstruct import reconstruct, assemble_component
from tyre_analysis.pipeline import analyse_components
from tyre_analysis.sg_table import DEFAULT_SG_TABLE, normalise_key
from tyre_analysis import reporting

st.set_page_config(page_title="Manual Loop Builder", layout="wide")
st.title("Manual Loop Builder")
st.caption(
    "For drawings that aren't split into named layers with closed curves. "
    "Reconstruct → pick faces per component → compute."
)


# --------------------------------------------------------------------------- #
# Upload + reconstruction (cached by bytes + params)
# --------------------------------------------------------------------------- #
upload = st.file_uploader("Non-conforming DXF", type=["dxf"])
if not upload:
    st.info("Upload a DXF (e.g. the raw full-section export). "
            "The main page handles already-conforming files.")
    st.stop()

c1, c2, c3 = st.columns(3)
flatten_tol = c1.number_input("Flatten tolerance (mm)", 0.001, 1.0, 0.05, 0.01, format="%.3f")
min_area = c2.number_input("Min face area (mm²)", 0.0, 100.0, 1.0, 0.5,
                           help="Drop slivers below this. Lower it if a thin component band is missing.")
dedupe = c3.checkbox("Merge duplicate faces", value=True,
                     help="Collapses exact-duplicate faces (e.g. overlapping copies).")


@st.cache_data(show_spinner="Reconstructing geometry…")
def _reconstruct(data: bytes, name: str, tol: float, ma: float, dd: bool):
    tmp = Path(tempfile.gettempdir()) / f"manual_{name}"
    tmp.write_bytes(data)
    rec = reconstruct(tmp, flatten_tol=tol, min_area=ma, dedupe=dd)
    return rec

rec = _reconstruct(upload.getbuffer().tobytes(), upload.name, flatten_tol, min_area, dedupe)

st.success(
    f"Reconstructed {len(rec.curves)} curves → {len(rec.faces)} candidate faces. "
    f"Extents x[{rec.xmin:.0f},{rec.xmax:.0f}] y[{rec.ymin:.0f},{rec.ymax:.0f}]."
)


# --------------------------------------------------------------------------- #
# Axis + side
# --------------------------------------------------------------------------- #
a1, a2 = st.columns([2, 1])
axis_x = a1.number_input(
    "Rotation axis X (mm)", value=float(round(rec.suggested_axis_x, 3)), step=1.0,
    help=f"Auto-suggested {rec.suggested_axis_x:.2f} (drawing mid-section / drawn centreline).",
)
side = a2.selectbox("Work on half", ["right", "left", "both"], index=0,
                    help="A full section is symmetric — analyse ONE half for correct Pappus distances.")
faces = rec.faces_on_side(axis_x, side)


# --------------------------------------------------------------------------- #
# Persistent assignment state (per file)
# --------------------------------------------------------------------------- #
state_key = f"assign::{upload.name}"
if state_key not in st.session_state:
    st.session_state[state_key] = {}          # component -> [face_id]
assignments: dict[str, list[int]] = st.session_state[state_key]

# Optional: load a previously saved assignment JSON.
with st.expander("Load / save assignments"):
    load = st.file_uploader("Load assignments JSON", type=["json"], key="load_assign")
    if load is not None and st.button("Apply loaded assignments"):
        st.session_state[state_key] = {k: list(map(int, v)) for k, v in json.load(load).items()}
        st.rerun()
    st.download_button("Save current assignments",
                       data=json.dumps(assignments, indent=2),
                       file_name=f"{Path(upload.name).stem}_assignments.json",
                       mime="application/json")

comps_so_far = list(assignments.keys())
comp_colors = reporting.component_colors(comps_so_far or ["_"])


# --------------------------------------------------------------------------- #
# Picking figure
# --------------------------------------------------------------------------- #
fig = reporting.face_picker_figure(rec, faces, axis_x, side, assignments, comp_colors)
event = st.plotly_chart(fig, use_container_width=True, on_select="rerun", key="facepick")

selected_ids: list[int] = []
if event and getattr(event, "selection", None):
    for pt in event["selection"]["points"]:
        cd = pt.get("customdata")
        if cd is not None:
            selected_ids.append(int(cd if not isinstance(cd, list) else cd[0]))
selected_ids = sorted(set(selected_ids))

st.write(f"**Selected faces:** {selected_ids or '—'}  "
         f"(area {sum(f.area for f in faces if f.id in selected_ids):.0f} mm²)")


# --------------------------------------------------------------------------- #
# Assign / manage components
# --------------------------------------------------------------------------- #
b1, b2, b3 = st.columns([2, 1, 1])
default_names = list(DEFAULT_SG_TABLE.keys())
comp_name = b1.text_input("Component name", value="", placeholder="e.g. TREAD, SIDEWALL, BEAD…")
if b2.button("➕ Assign selected", disabled=not (selected_ids and comp_name)):
    key = normalise_key(comp_name)
    existing = set(assignments.get(key, []))
    existing.update(selected_ids)
    assignments[key] = sorted(existing)
    st.rerun()
if b3.button("🗑 Clear all"):
    st.session_state[state_key] = {}
    st.rerun()

if assignments:
    st.subheader("Assigned components")
    for comp, ids in list(assignments.items()):
        cc1, cc2, cc3 = st.columns([2, 3, 1])
        cc1.markdown(f"**{comp}**")
        cc2.caption(f"faces {ids} · {sum(f.area for f in rec.faces if f.id in ids):.0f} mm²")
        if cc3.button("remove", key=f"rm_{comp}"):
            del assignments[comp]
            st.rerun()


# --------------------------------------------------------------------------- #
# SG table + compute
# --------------------------------------------------------------------------- #
st.subheader("Specific gravity (g/cm³)")
sg_seed = {normalise_key(k): v for k, v in DEFAULT_SG_TABLE.items()}
for comp in assignments:
    sg_seed.setdefault(comp, 1.10)
sg_df = pd.DataFrame([{"component": k, "sg_g_cm3": v} for k, v in sorted(sg_seed.items())])
edited = st.data_editor(sg_df, num_rows="dynamic", use_container_width=True, key="sg_manual")
sg_table = {normalise_key(r["component"]): float(r["sg_g_cm3"])
            for _, r in edited.iterrows() if r["component"]}

if st.button("📊 Compute results", type="primary", disabled=not assignments):
    components = {}
    build_errors = []
    for comp, ids in assignments.items():
        try:
            components[comp] = assemble_component(rec.faces, ids)
        except ValueError as e:
            build_errors.append(f"{comp}: {e}")
    for e in build_errors:
        st.error(e)
    if components:
        df, geoms, warns = analyse_components(
            Path(upload.name).stem, components, axis_x, sg_table, fallback_sg=1.10
        )
        for w in warns:
            st.warning(w)

        st.subheader("Per-component results")
        show = ["component", "area_mm2", "centroid_distance_mm", "volume_cm3",
                "weight_g", "surface_area_mm2", "polar_MOI", "pct_of_total_weight"]
        st.dataframe(df[show].round(2), use_container_width=True)
        st.metric("Total weight (one half, g)", f"{df['weight_g'].sum():.1f}")

        geoms_by_file = {Path(upload.name).stem: geoms}
        base = Path(upload.name).stem
        st.plotly_chart(reporting.overlay_figure(geoms_by_file, base, axis_x),
                        use_container_width=True)
        st.plotly_chart(reporting.polar_moi_bar(df), use_container_width=True)

        st.download_button("Download results CSV", df.to_csv(index=False),
                           file_name=f"{base}_manual_results.csv", mime="text/csv")
