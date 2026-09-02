"""Pipeline tests.

The v0.6 file was named test_pipeline.py but defined no test_* functions, so
pytest collected zero tests from it and reported success either way.  These are
real tests; the geometry ones skip cleanly when CadQuery is unavailable.
"""

from pathlib import Path
import json
import py_compile
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import project as proj
from core.names import abaqus_name, name_collisions, prefixed
from core.parametric import ParametricError, expand_cases, _frange
from core.validation import validate_project

# ---------------------------------------------------------------------------
# Pure-Python behaviour (no CAD kernel needed)
# ---------------------------------------------------------------------------

def test_abaqus_names_are_legal_and_stable():
    assert abaqus_name("Tread Contact") == "Tread_Contact"
    assert abaqus_name("3Sipe").startswith("S_")
    assert abaqus_name("") == "S"
    # Deterministic: the generator relies on this to reference what it created.
    assert abaqus_name("a-b") == abaqus_name("a b")


def test_colliding_set_names_are_reported():
    assert name_collisions(["a b", "a-b"])
    assert not name_collisions(["alpha", "beta"])


def test_prefixed_names_stay_within_the_abaqus_limit():
    assert len(prefixed("SURF_", "x" * 200)) <= 80


def test_frange_has_no_floating_point_drift():
    assert _frange(0.6, 0.8, 0.2) == [0.6, 0.8]
    assert _frange(0.0, 1.0, 0.1)[-1] == 1.0
    assert len(_frange(0.0, 1.0, 0.1)) == 11


def test_runaway_sweeps_are_rejected():
    with pytest.raises(ParametricError):
        _frange(0.0, 1.0, 1e-5)


def test_expand_cases_applies_each_combination():
    p = proj.default_project()
    p["parametric"] = [{"enabled": True, "path": "road.friction", "start": 0.6, "end": 0.8, "increment": 0.2}]
    cases = expand_cases(p)
    assert [c["project"]["road"]["friction"] for c in cases] == [0.6, 0.8]


def test_renaming_a_step_keeps_bc_and_load_references_valid():
    p = proj.default_project()
    p["boundary_conditions"] = [{"name": "BC", "operation": "create", "step": "Preload"}]
    p["loads"] = [{"name": "L", "step": "Preload", "type": "pressure"}]
    proj.apply_form(p, {"steps-0-name": "Renamed"})
    assert p["boundary_conditions"][0]["step"] == "Renamed"
    assert p["loads"][0]["step"] == "Renamed"


def test_deleting_a_set_clears_every_reference_to_it():
    p = proj.default_project()
    p["sets"] = [{"name": "WALLS", "kind": "face", "entity_ids": ["B001_F0001"]}]
    p["self_contacts"] = [{"name": "SC", "surface_set": "WALLS"}]
    p["road"]["contact_set"] = "WALLS"
    proj.delete_set(p, "WALLS")
    assert p["sets"] == [] and p["self_contacts"] == []
    assert p["road"]["contact_set"] == ""


def test_validation_reports_missing_material_library():
    errors, _ = validate_project(proj.default_project())
    assert any("material .inc library" in e for e in errors)


def test_validation_does_not_raise_on_non_numeric_input():
    p = proj.default_project()
    p["mesh"]["global_size"] = "not a number"
    p["analysis"]["cpus"] = "abc"
    errors, _ = validate_project(p)  # v0.6 raised ValueError here, returning a 500
    assert any("mesh size" in e for e in errors)


def test_bc_modify_must_come_after_the_bc_is_created():
    p = _minimal_valid_project()
    p["steps"] = [{"name": "A", "time_period": 1}, {"name": "B", "time_period": 1}]
    p["boundary_conditions"] = [
        {"name": "M", "operation": "modify", "target_bc": "C", "step": "A", "u1": 1},
        {"name": "C", "operation": "create", "step": "B", "region_type": "rp", "region": "RP_ROAD"},
    ]
    errors, _ = validate_project(p)
    assert any("must act in a step after" in e for e in errors)


def test_element_type_must_match_the_solver():
    p = _minimal_valid_project()
    p["analysis"]["solver"] = "explicit"
    p["mesh"]["element_type"] = "C3D10H"  # Standard-only
    errors, _ = validate_project(p)
    assert any("not available for explicit" in e for e in errors)


def _minimal_valid_project():
    p = proj.default_project()
    p["step_token"] = "0" * 32
    p["cad_metadata"] = {"bodies": [], "faces": [], "edges": [], "source": {}}
    p["sets"] = [{"name": "TREAD_BODY", "kind": "body", "entity_ids": ["B001"]},
                 {"name": "TREAD_TOP", "kind": "face", "entity_ids": ["B001_F0001"]}]
    p["material_library"].update({"include_token": "1" * 32, "material_code": "RC1", "body_set": "TREAD_BODY"})
    p["road"].update({"enabled": True, "contact_set": "TREAD_TOP"})
    return p


def test_a_complete_project_validates_clean():
    errors, _ = validate_project(_minimal_valid_project())
    assert errors == []


# ---------------------------------------------------------------------------
# Geometry + generation
# ---------------------------------------------------------------------------

def _make_siped_step(path: Path):
    import cadquery as cq
    from cadquery import exporters
    shape = cq.Workplane("XY").box(30, 20, 15, centered=(True, True, False))
    for x in (-8, 0, 8):
        cutter = cq.Workplane("XY").box(0.8, 14, 12, centered=(True, True, False)).translate((x, 0, 3))
        shape = shape.cut(cutter)
    exporters.export(shape, str(path))


@pytest.fixture(scope="module")
def parsed_demo(tmp_path_factory):
    pytest.importorskip("cadquery")
    from core.cad import parse_step
    tmp = tmp_path_factory.mktemp("geom")
    step = tmp / "siped.step"
    _make_siped_step(step)
    return step, parse_step(step)


def test_step_parsing_finds_the_expected_topology(parsed_demo):
    _, parsed = parsed_demo
    meta = parsed["metadata"]
    assert len(meta["bodies"]) == 1
    assert len(meta["faces"]) == 21
    assert meta["mesh"]["vertices"] if "mesh" in meta else True


def test_multi_root_step_keeps_every_body(tmp_path):
    """v0.6 called .val() and silently kept only the first root shape."""
    pytest.importorskip("cadquery")
    import cadquery as cq
    from OCP.STEPControl import STEPControl_Writer, STEPControl_StepModelType
    from core.cad import parse_step
    path = tmp_path / "multi.step"
    writer = STEPControl_Writer()
    for solid in (cq.Workplane("XY").box(10, 10, 10),
                  cq.Workplane("XY").box(5, 5, 5).translate((30, 0, 0))):
        writer.Transfer(solid.val().wrapped, STEPControl_StepModelType.STEPControl_AsIs)
    writer.Write(str(path))
    assert len(parse_step(path)["metadata"]["bodies"]) == 2


def test_sipe_detection_on_the_controlled_model(parsed_demo):
    from core.sipe_detection import detect_sipes
    step, _ = parsed_demo
    det = detect_sipes(step, {"radial_axis": "Z", "max_gap": 1.2, "min_depth": 8,
                              "opposition_tolerance_deg": 30, "wall_tilt_tolerance_deg": 20,
                              "min_confidence": 0.2})
    assert len(det["sipes"]) == 3
    assert len(det["all_wall_ids"]) == 6
    assert all(abs(s["gap_estimate"] - 0.8) < 0.05 for s in det["sipes"])


def _project_for(parsed, det):
    meta = parsed["metadata"]
    p = proj.default_project()
    p["step_token"] = "0" * 32
    p["cad_metadata"] = meta
    p["sets"] = [
        {"name": "TREAD_BODY", "kind": "body", "entity_ids": [meta["bodies"][0]["id"]]},
        {"name": "TREAD_ROAD_CONTACT", "kind": "face",
         "entity_ids": [max(meta["faces"], key=lambda f: f["center"][2])["id"]]},
    ]
    proj.apply_sipe_detection(p, det, 0.8)
    p["material_library"].update({"include_token": "1" * 32, "uploaded_name": "lib.inc",
                                  "material_code": "RC1234", "body_set": "TREAD_BODY",
                                  "local_include_name": "lib.inc"})
    p["road"].update({"enabled": True, "normal_axis": "Z", "normal_sign": "-", "length": 50, "width": 40,
                      "center_x": 0, "center_y": 0, "center_z": 15, "friction": 0.8,
                      "contact_set": "TREAD_ROAD_CONTACT"})
    p["steps"] = [
        {"name": "Preload", "time_period": 1, "nlgeom": True, "initial_increment": 0.01,
         "min_increment": 1e-8, "max_increment": 0.05, "max_increments": 1000, "stabilization": None},
        {"name": "Longitudinal", "time_period": 1, "nlgeom": True, "initial_increment": 0.01,
         "min_increment": 1e-8, "max_increment": 0.05, "max_increments": 1000, "stabilization": None},
    ]
    p["boundary_conditions"] = [
        {"name": "RoadMotion", "operation": "create", "step": "Preload", "region_type": "rp",
         "region": "RP_ROAD", "u1": 0, "u2": 0, "u3": -1, "ur1": 0, "ur2": 0, "ur3": 0},
        {"name": "RoadMotion_Long", "operation": "modify", "target_bc": "RoadMotion",
         "step": "Longitudinal", "u1": 10},
    ]
    p["parametric"] = [{"enabled": True, "path": "road.friction", "start": 0.6, "end": 0.8, "increment": 0.2}]
    return p


def test_generated_scripts_are_valid_python_and_complete(parsed_demo, tmp_path):
    from core.sipe_detection import detect_sipes
    from core.abaqus_generator import generate_build_script, generate_odb_script
    step, parsed = parsed_demo
    det = detect_sipes(step, {"radial_axis": "Z", "max_gap": 1.2, "min_depth": 8,
                              "opposition_tolerance_deg": 30, "wall_tilt_tolerance_deg": 20,
                              "min_confidence": 0.2})
    p = _project_for(parsed, det)

    errors, _ = validate_project(p)
    assert errors == [], errors

    cases = expand_cases(p)
    assert len(cases) == 2

    case_dir = tmp_path / "case"
    case_dir.mkdir()
    build = case_dir / "build_and_run.py"
    extract = case_dir / "extract_odb.py"
    build.write_text(generate_build_script(cases[0]["project"], step, case_dir), encoding="utf-8")
    extract.write_text(generate_odb_script(cases[0]["project"], case_dir), encoding="utf-8")
    py_compile.compile(str(build), doraise=True)
    py_compile.compile(str(extract), doraise=True)

    text = build.read_text(encoding="utf-8")
    for needle in ("openStep", "AnalyticRigidSurfExtrude", "RP_ROAD", "SelfContactStd", "StaticStep",
                   "setValuesInStep", "generateMesh", "writeInput", "waitForCompletion",
                   "HistoryOutputRequest", "_inject_material_library", "_contact_pair",
                   "AUTOMATION_JOB_COMPLETE"):
        assert needle in text, needle

    ext = extract.read_text(encoding="utf-8")
    for needle in ("History_All_Steps.csv", "RP_History_Wide.csv", "Energy_History.csv",
                   "_open_csv", "for step_name, step in odb.steps.items()"):
        assert needle in ext, needle


def test_set_names_are_referenced_exactly_as_they_are_created(parsed_demo, tmp_path):
    """A set name needing sanitisation must match everywhere it is used."""
    from core.abaqus_generator import generate_build_script
    _, parsed = parsed_demo
    meta = parsed["metadata"]
    p = proj.default_project()
    p["cad_metadata"] = meta
    p["sets"] = [
        {"name": "Tread Body", "kind": "body", "entity_ids": [meta["bodies"][0]["id"]]},
        {"name": "Road Contact", "kind": "face", "entity_ids": [meta["faces"][0]["id"]]},
    ]
    p["material_library"].update({"material_code": "RC1", "body_set": "Tread Body",
                                  "local_include_name": "lib.inc"})
    p["road"].update({"enabled": True, "contact_set": "Road Contact"})
    text = generate_build_script(p, tmp_path / "in.step", tmp_path)
    assert "'Road_Contact'" in text            # part set created under the safe name
    assert "'SURF_Road_Contact'" in text       # and the surface refers to that same name
    assert "MATERIAL_BODY_SET = 'Tread_Body'"in text
    assert "Road Contact" not in text          # no raw name survives into Abaqus
