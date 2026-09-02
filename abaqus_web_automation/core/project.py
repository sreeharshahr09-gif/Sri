"""Server-side project state.

v0.6 kept the entire project in browser JavaScript and rebuilt every editor on
each keystroke.  v0.7 keeps the project in Python: the browser posts plain HTML
forms, this module parses them, and the page is re-rendered from the stored
state.  The schema below is the single source of truth for both form parsing and
template rendering, so a field is declared exactly once.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

VERSION = "0.7.0"

# Element types the generator is allowed to emit.  The value is written directly
# into the generated Abaqus script, so an unvalidated string here would be code
# injection as well as a modelling error.
ELEMENT_TYPES = {
    "standard": ["C3D10H", "C3D10", "C3D4H", "C3D4"],
    "explicit": ["C3D10M", "C3D4"],
}
POSITIONS = ["NODAL", "ELEMENT_NODAL", "CENTROID", "INTEGRATION_POINT"]
AXES = ["X", "Y", "Z"]


def _num(v: Any) -> Optional[float]:
    """Blank stays blank: an empty BC field means 'leave this DOF free'."""
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _int(v: Any, fallback: int = 0) -> int:
    n = _num(v)
    return fallback if n is None else int(n)


def _str(v: Any) -> str:
    return "" if v is None else str(v).strip()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
# type: str | num | int | bool | select
# source: fills a select from live project data instead of a fixed list

SECTIONS: Dict[str, Dict[str, Any]] = {
    "steps": {
        "title": "Analysis steps",
        "singular": "step",
        "fields": [
            {"key": "name", "label": "Name", "type": "str"},
            {"key": "time_period", "label": "Time period", "type": "num"},
            {"key": "nlgeom", "label": "NLGEOM", "type": "bool"},
            {"key": "initial_increment", "label": "Initial increment", "type": "num"},
            {"key": "min_increment", "label": "Min increment", "type": "num"},
            {"key": "max_increment", "label": "Max increment", "type": "num"},
            {"key": "max_increments", "label": "Max increments", "type": "int"},
            {"key": "stabilization", "label": "Stabilization fraction", "type": "num"},
        ],
        "default": lambda p: {
            "name": _unique("Step", p["steps"]), "time_period": 1.0, "nlgeom": True,
            "initial_increment": 0.01, "min_increment": 1e-8, "max_increment": 0.1,
            "max_increments": 1000, "stabilization": None,
        },
    },
    "reference_points": {
        "title": "Reference points & couplings",
        "singular": "reference point",
        "fields": [
            {"key": "name", "label": "Name", "type": "str"},
            {"key": "x", "label": "X", "type": "num"},
            {"key": "y", "label": "Y", "type": "num"},
            {"key": "z", "label": "Z", "type": "num"},
            {"key": "coupled_surface", "label": "Coupled face set", "type": "select", "source": "sets:face", "blank": True},
        ],
        "default": lambda p: {"name": _unique("RP", p["reference_points"]), "x": 0.0, "y": 0.0, "z": 0.0, "coupled_surface": ""},
    },
    "interactions": {
        "title": "Pair contact interactions",
        "singular": "interaction",
        "fields": [
            {"key": "name", "label": "Name", "type": "str"},
            {"key": "master_set", "label": "Master face set", "type": "select", "source": "sets:face", "blank": True},
            {"key": "slave_set", "label": "Slave face set", "type": "select", "source": "sets:face", "blank": True},
            {"key": "friction", "label": "Friction mu", "type": "num"},
        ],
        "default": lambda p: {"name": _unique("Contact", p["interactions"]), "master_set": "", "slave_set": "", "friction": 0.8},
    },
    "self_contacts": {
        "title": "Self-contact (sipe walls)",
        "singular": "self-contact",
        "fields": [
            {"key": "name", "label": "Name", "type": "str"},
            {"key": "surface_set", "label": "Sipe wall face set", "type": "select", "source": "sets:face", "blank": True},
            {"key": "friction", "label": "Friction mu", "type": "num"},
        ],
        "default": lambda p: {"name": _unique("SipeSelfContact", p["self_contacts"]), "surface_set": "", "friction": 0.8},
    },
    "boundary_conditions": {
        "title": "Boundary conditions",
        "singular": "boundary condition",
        "fields": [
            {"key": "name", "label": "Name", "type": "str"},
            {"key": "operation", "label": "Operation", "type": "select",
             "options": [["create", "Create BC"], ["modify", "Modify existing BC"]]},
            {"key": "step", "label": "Step", "type": "select", "source": "steps", "blank": True},
            {"key": "target_bc", "label": "Target BC (modify)", "type": "select", "source": "created_bcs", "blank": True},
            {"key": "region_type", "label": "Region type", "type": "select",
             "options": [["set", "Geometry set"], ["rp", "Reference point"]]},
            {"key": "region", "label": "Region", "type": "select", "source": "bc_region", "blank": True},
            {"key": "u1", "label": "U1", "type": "num"},
            {"key": "u2", "label": "U2", "type": "num"},
            {"key": "u3", "label": "U3", "type": "num"},
            {"key": "ur1", "label": "UR1", "type": "num"},
            {"key": "ur2", "label": "UR2", "type": "num"},
            {"key": "ur3", "label": "UR3", "type": "num"},
        ],
        "default": lambda p: {
            "name": _unique("BC", p["boundary_conditions"]), "operation": "create", "target_bc": "",
            "step": (p["steps"][0]["name"] if p["steps"] else ""), "region_type": "set", "region": "",
            "u1": None, "u2": None, "u3": None, "ur1": None, "ur2": None, "ur3": None,
        },
    },
    "loads": {
        "title": "Loads",
        "singular": "load",
        "fields": [
            {"key": "name", "label": "Name", "type": "str"},
            {"key": "step", "label": "Step", "type": "select", "source": "steps", "blank": True},
            {"key": "type", "label": "Type", "type": "select",
             "options": [["pressure", "Pressure on face set"], ["force", "Force at RP"], ["moment", "Moment at RP"]]},
            {"key": "region", "label": "Region", "type": "select", "source": "load_region", "blank": True},
            {"key": "magnitude", "label": "Pressure magnitude", "type": "num"},
            {"key": "f1", "label": "F1", "type": "num"},
            {"key": "f2", "label": "F2", "type": "num"},
            {"key": "f3", "label": "F3", "type": "num"},
            {"key": "m1", "label": "M1", "type": "num"},
            {"key": "m2", "label": "M2", "type": "num"},
            {"key": "m3", "label": "M3", "type": "num"},
        ],
        "default": lambda p: {
            "name": _unique("Load", p["loads"]), "step": (p["steps"][0]["name"] if p["steps"] else ""),
            "type": "pressure", "region": "", "magnitude": 1.0,
            "f1": 0.0, "f2": 0.0, "f3": 0.0, "m1": 0.0, "m2": 0.0, "m3": 0.0,
        },
    },
    "outputs": {
        "title": "ODB to CSV field outputs",
        "singular": "output",
        "fields": [
            {"key": "variable", "label": "Variable", "type": "str"},
            {"key": "region", "label": "Region", "type": "select", "source": "sets:any", "blank": True},
            {"key": "position", "label": "Position", "type": "select", "options": [[x, x] for x in POSITIONS]},
        ],
        "default": lambda p: {"variable": "U", "region": "", "position": "NODAL"},
    },
    "parametric": {
        "title": "Parametric sweeps",
        "singular": "sweep",
        "fields": [
            {"key": "enabled", "label": "Enabled", "type": "bool"},
            {"key": "path", "label": "JSON path", "type": "str"},
            {"key": "start", "label": "Start", "type": "num"},
            {"key": "end", "label": "End", "type": "num"},
            {"key": "increment", "label": "Increment", "type": "num"},
        ],
        "default": lambda p: {"enabled": True, "path": "road.friction", "start": 0.4, "end": 0.8, "increment": 0.2},
    },
}

# Single-valued groups rendered as one block of fields each.
SCALAR_GROUPS: Dict[str, Dict[str, Any]] = {
    "model": {
        "title": "Model naming",
        "fields": [
            {"key": "model_name", "label": "Model name", "type": "str"},
            {"key": "part_name", "label": "Part name", "type": "str"},
            {"key": "instance_name", "label": "Instance name", "type": "str"},
        ],
    },
    "analysis": {
        "title": "Solver",
        "fields": [
            {"key": "job_name", "label": "Job name", "type": "str"},
            {"key": "solver", "label": "Solver", "type": "select",
             "options": [["standard", "Abaqus/Standard"], ["explicit", "Abaqus/Explicit"]]},
            {"key": "cpus", "label": "CPUs", "type": "int"},
        ],
    },
    "mesh": {
        "title": "Mesh",
        "fields": [
            {"key": "global_size", "label": "Global size", "type": "num"},
            {"key": "element_type", "label": "Element type", "type": "select", "source": "element_types"},
        ],
    },
    "road": {
        "title": "Analytical rigid road",
        "fields": [
            {"key": "enabled", "label": "Create road automatically", "type": "bool"},
            {"key": "normal_axis", "label": "Road normal axis", "type": "select", "options": [[x, x] for x in AXES]},
            {"key": "normal_sign", "label": "Active normal side", "type": "select",
             "options": [["+", "+ normal"], ["-", "- normal"]]},
            {"key": "length", "label": "Length", "type": "num"},
            {"key": "width", "label": "Width", "type": "num"},
            {"key": "center_x", "label": "Center X", "type": "num"},
            {"key": "center_y", "label": "Center Y", "type": "num"},
            {"key": "center_z", "label": "Center Z", "type": "num"},
            {"key": "friction", "label": "Friction mu", "type": "num"},
            {"key": "contact_set", "label": "Tread contact face set", "type": "select", "source": "sets:face", "blank": True},
        ],
    },
    "material_library": {
        "title": "Material include library",
        "fields": [
            {"key": "material_code", "label": "Material number / code", "type": "select", "source": "material_codes", "blank": True, "free_text": True},
            {"key": "body_set", "label": "Assign to body set", "type": "select", "source": "sets:body", "blank": True},
            {"key": "password", "label": "Include password", "type": "str"},
        ],
    },
    "sipe": {
        "title": "Sipe wall detection",
        "fields": [
            {"key": "radial_axis", "label": "Radial axis", "type": "select", "options": [[x, x] for x in AXES]},
            {"key": "top_sign", "label": "Tread top direction", "type": "select",
             "options": [["+", "+ axis"], ["-", "- axis"]]},
            {"key": "max_gap", "label": "Max sipe gap", "type": "num"},
            {"key": "min_depth", "label": "Min wall depth", "type": "num"},
            {"key": "opposition_tolerance_deg", "label": "Opposed normal tol. (deg)", "type": "num"},
            {"key": "wall_tilt_tolerance_deg", "label": "Wall tilt tol. (deg)", "type": "num"},
            {"key": "min_area", "label": "Min face area", "type": "num"},
            {"key": "friction", "label": "Self-contact mu", "type": "num"},
        ],
    },
}


def _unique(base: str, rows: List[Dict[str, Any]]) -> str:
    names = {str(r.get("name", "")) for r in rows}
    if base not in names:
        return base
    i = 2
    while "%s_%d" % (base, i) in names:
        i += 1
    return "%s_%d" % (base, i)


def default_project() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "step_token": None,
        "uploaded_name": None,
        "cad_metadata": None,
        "sets": [],
        "sipe_detection": {"applied": False, "result": None},
        "model": {"model_name": "Model-1", "part_name": "ImportedPart", "instance_name": "IMPORTEDPART-1"},
        "analysis": {"job_name": "AutoJob", "solver": "standard", "cpus": 4, "nlgeom": True,
                     "field_variables": ["S", "U", "RF", "LE"]},
        "mesh": {"global_size": 2.0, "element_type": "C3D10H"},
        "road": {"enabled": False, "normal_axis": "Y", "normal_sign": "+", "length": 150.0, "width": 100.0,
                 "center_x": 0.0, "center_y": 0.0, "center_z": 0.0, "friction": 0.8, "contact_set": ""},
        "material_library": {"include_token": None, "uploaded_name": None, "sha256": None,
                             "material_code": "", "body_set": "", "password": "", "available_codes": []},
        "sipe": {"radial_axis": "Z", "top_sign": "+", "max_gap": 2.0, "min_depth": 5.0,
                 "opposition_tolerance_deg": 25.0, "wall_tilt_tolerance_deg": 25.0,
                 "min_area": 0.0, "friction": 0.8},
        "steps": [{"name": "Preload", "time_period": 1.0, "nlgeom": True, "initial_increment": 0.01,
                   "min_increment": 1e-8, "max_increment": 0.1, "max_increments": 1000, "stabilization": None}],
        "reference_points": [],
        "interactions": [],
        "self_contacts": [],
        "boundary_conditions": [],
        "loads": [],
        "outputs": [{"variable": "U", "region": "", "position": "NODAL"},
                    {"variable": "S", "region": "", "position": "INTEGRATION_POINT"}],
        "parametric": [],
        "generation_id": None,
    }


def coerce(field: Dict[str, Any], raw: Any) -> Any:
    t = field["type"]
    if t == "num":
        return _num(raw)
    if t == "int":
        return _int(raw, 1)
    if t == "bool":
        return bool(raw)
    return _str(raw)


def apply_form(project: Dict[str, Any], form: Mapping[str, Any]) -> Dict[str, Any]:
    """Fold a flat HTML form mapping back into the project.

    Field names are ``group-key`` for scalars and ``section-index-key`` for rows.
    Checkboxes are absent from the payload when unticked, which is why bool
    fields read presence rather than value.
    """
    for group, spec in SCALAR_GROUPS.items():
        target = project.setdefault(group, {})
        for field in spec["fields"]:
            key = "%s-%s" % (group, field["key"])
            if field["type"] == "bool":
                target[field["key"]] = key in form
            elif key in form:
                target[field["key"]] = coerce(field, form[key])

    for section, spec in SECTIONS.items():
        rows = project.get(section, [])
        for i, row in enumerate(rows):
            for field in spec["fields"]:
                key = "%s-%d-%s" % (section, i, field["key"])
                if field["type"] == "bool":
                    row[field["key"]] = key in form
                elif key in form:
                    row[field["key"]] = coerce(field, form[key])

    _cascade_renames(project)
    _sync_field_variables(project)
    return project


def _cascade_renames(project: Dict[str, Any]) -> None:
    """Keep step references valid when a step is renamed.

    In v0.6 renaming a step silently orphaned every BC and load that pointed at
    it, and generation then failed validation with 'references unknown step'.
    """
    valid = [s.get("name") for s in project.get("steps", []) if s.get("name")]
    if not valid:
        return
    for section in ("boundary_conditions", "loads"):
        for row in project.get(section, []):
            if row.get("step") and row["step"] not in valid:
                row["step"] = valid[0]


def _sync_field_variables(project: Dict[str, Any]) -> None:
    """Field-output requests follow the variables the user asked to extract."""
    wanted = [str(o.get("variable", "")).upper() for o in project.get("outputs", []) if o.get("variable")]
    seen: List[str] = []
    for v in wanted or ["S", "U", "RF", "LE"]:
        if v not in seen:
            seen.append(v)
    project.setdefault("analysis", {})["field_variables"] = seen


def apply_action(project: Dict[str, Any], action: str) -> Optional[str]:
    """Handle 'add:<section>' and 'del:<section>:<index>' submit buttons."""
    if not action:
        return None
    parts = action.split(":")
    verb = parts[0]
    if verb == "add" and len(parts) == 2 and parts[1] in SECTIONS:
        section = parts[1]
        project.setdefault(section, []).append(SECTIONS[section]["default"](project))
        return "Added a new %s." % SECTIONS[section]["singular"]
    if verb == "del" and len(parts) == 3 and parts[1] in SECTIONS:
        section, index = parts[1], _int(parts[2], -1)
        rows = project.setdefault(section, [])
        if 0 <= index < len(rows):
            removed = rows.pop(index)
            if section == "steps":
                _cascade_renames(project)
            return "Removed %s %s." % (SECTIONS[section]["singular"], removed.get("name", index + 1))
    return None


def add_set(project: Dict[str, Any], name: str, kind: str, entity_ids: List[str]) -> Optional[str]:
    name = _str(name)
    if not name:
        return "Enter a set name."
    if kind not in {"face", "edge", "body"}:
        return "Unsupported set kind."
    if not entity_ids:
        return "Select geometry in the viewer before creating a set."
    for existing in project["sets"]:
        if existing["name"] == name:
            existing["kind"] = kind
            existing["entity_ids"] = entity_ids
            return None
    project["sets"].append({"name": name, "kind": kind, "entity_ids": entity_ids})
    return None


def delete_set(project: Dict[str, Any], name: str) -> None:
    project["sets"] = [s for s in project["sets"] if s["name"] != name]
    project["self_contacts"] = [c for c in project["self_contacts"] if c.get("surface_set") != name]
    project["interactions"] = [
        c for c in project["interactions"]
        if c.get("master_set") != name and c.get("slave_set") != name
    ]
    for group, key in (("road", "contact_set"), ("material_library", "body_set")):
        if project.get(group, {}).get(key) == name:
            project[group][key] = ""
    for rp in project["reference_points"]:
        if rp.get("coupled_surface") == name:
            rp["coupled_surface"] = ""
    for row in project["outputs"]:
        if row.get("region") == name:
            row["region"] = ""


def apply_sipe_detection(project: Dict[str, Any], detection: Dict[str, Any], friction: float) -> str:
    """Turn a detection result into named sets and self-contact definitions."""
    sipes = detection.get("sipes") or []
    if not sipes:
        return "No sipes to apply."
    project["sets"] = [s for s in project["sets"] if s.get("source") != "auto_sipe"]
    project["self_contacts"] = [c for c in project["self_contacts"] if c.get("source") != "auto_sipe"]
    created = 0
    for sipe in sipes:
        groups = (("SIDE_A", sipe.get("side_a_ids")), ("SIDE_B", sipe.get("side_b_ids")),
                  ("WALLS", sipe.get("wall_ids")))
        for suffix, ids in groups:
            # An empty side would fail validation as an empty set; skip it rather
            # than emit something the user has to hunt down later.
            if not ids:
                continue
            project["sets"].append({"name": "%s_%s" % (sipe["name"], suffix), "kind": "face",
                                    "entity_ids": list(ids), "source": "auto_sipe"})
            created += 1
        if sipe.get("wall_ids"):
            project["self_contacts"].append({
                "name": "SELF_%s" % sipe["name"], "surface_set": "%s_WALLS" % sipe["name"],
                "friction": friction, "source": "auto_sipe",
            })
    if detection.get("all_wall_ids"):
        project["sets"].append({"name": "ALL_SIPE_WALLS", "kind": "face",
                                "entity_ids": list(detection["all_wall_ids"]), "source": "auto_sipe"})
        created += 1
    project["sipe_detection"] = {"applied": True, "result": detection}
    return "Applied %d sipe set(s) and %d self-contact definition(s)." % (created, len(project["self_contacts"]))


def option_source(project: Dict[str, Any], source: str, row: Optional[Dict[str, Any]] = None) -> List[List[str]]:
    """Resolve a select's ``source`` against live project data."""
    if source.startswith("sets:"):
        kind = source.split(":", 1)[1]
        return [[s["name"], "%s (%s)" % (s["name"], s["kind"])]
                for s in project["sets"] if kind == "any" or s["kind"] == kind]
    if source == "steps":
        return [[s["name"], s["name"]] for s in project["steps"] if s.get("name")]
    if source == "created_bcs":
        return [[b["name"], b["name"]] for b in project["boundary_conditions"]
                if b.get("operation", "create") == "create" and b.get("name")]
    if source == "element_types":
        solver = project.get("analysis", {}).get("solver", "standard")
        return [[x, x] for x in ELEMENT_TYPES.get(solver, ELEMENT_TYPES["standard"])]
    if source == "material_codes":
        return [[c, c] for c in project.get("material_library", {}).get("available_codes") or []]
    if source == "rps":
        return _rp_options(project)
    if source == "bc_region":
        if (row or {}).get("region_type") == "rp":
            return _rp_options(project)
        return [[s["name"], "%s (%s)" % (s["name"], s["kind"])] for s in project["sets"]]
    if source == "load_region":
        if (row or {}).get("type") == "pressure":
            return [[s["name"], s["name"]] for s in project["sets"] if s["kind"] == "face"]
        return _rp_options(project)
    return []


def _rp_options(project: Dict[str, Any]) -> List[List[str]]:
    names = [r["name"] for r in project["reference_points"] if r.get("name")]
    if project.get("road", {}).get("enabled"):
        names.append("RP_ROAD")
    return [[n, n] for n in names]


class ProjectStore:
    """Single-project persistence.

    This is a local single-user tool, so one project on disk is enough and it
    survives a server restart, which the browser-resident v0.6 state did not.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return default_project()
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return default_project()
        project = default_project()
        project.update(stored)
        # Fill in groups added by later versions so an old file still renders.
        for group in list(SCALAR_GROUPS) + ["sipe_detection"]:
            base = default_project()[group]
            if isinstance(base, dict):
                merged = dict(base)
                merged.update(project.get(group) or {})
                project[group] = merged
        return project

    def save(self, project: Dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(project, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def reset(self) -> Dict[str, Any]:
        project = default_project()
        self.save(project)
        return project


def export_project(project: Dict[str, Any]) -> Dict[str, Any]:
    """Project copy for download; the password is never written to disk."""
    out = copy.deepcopy(project)
    out.get("material_library", {}).pop("password", None)
    return out
