#!/usr/bin/env python3
"""Extract features, dimensions, parameters and metadata from CATIA V5 files.

The script drives a local CATIA V5 installation through its COM automation
interface, so it must run on Windows with CATIA V5 installed and licensed.
CATPart, CATProduct and CATDrawing documents are supported.

Examples:
    python catia_extract.py Bracket.CATPart
    python catia_extract.py Gearbox.CATProduct --deep --xlsx
    python catia_extract.py C:\\models --recursive --csv -o results
    python catia_extract.py                 # the document active in CATIA

For every input a <file>.json with the full extraction is written to the
output folder. --csv / --xlsx additionally write flat tables (parameters,
dimensions, features, sketches, BOM, ...) combining all inputs.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import logging
import math
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("catia_extract")

DOC_EXTENSIONS = {".catpart": "part", ".catproduct": "product", ".catdrawing": "drawing"}
DOC_TYPES = {"PartDocument": "part", "ProductDocument": "product", "DrawingDocument": "drawing"}
MAX_DEPTH = 64
BBOX_SET_NAME = "__catia_extract_bbox__"

# CATIA enumerations. The raw code is always written next to the label.
LIMIT_MODES = ["dimension", "up to next", "up to last", "up to plane", "up to surface", "up thru next"]
HOLE_TYPES = ["simple", "tapered", "counterbored", "countersunk", "counterdrilled"]
HOLE_BOTTOMS = ["flat", "v-bottom", "trimmed"]
THREAD_MODES = ["threaded", "smooth"]
CHAMFER_MODES = ["length-angle", "length-length"]
PRODUCT_SOURCES = ["unknown", "made", "bought"]
CONSTRAINT_MODES = ["driving", "driven (reference)"]
CONSTRAINT_TYPES = [
    "reference", "distance", "on", "concentric", "tangency", "length", "angle",
    "planar angle", "parallelism", "axis parallelism", "horizontality",
    "perpendicularity", "axis perpendicularity", "verticality", "radius",
    "symmetry", "midpoint", "equidistance", "major radius", "minor radius",
    "surface contact", "line contact", "point contact", "chamfer",
    "chamfer perpendicular", "annular contact", "cylinder radius",
    "st continuity", "st distance", "sd continuity", "sd shape",
]
# Parameter types that hold plain values rather than physical dimensions.
NON_DIMENSION_TYPES = {"StrParam", "BoolParam", "IntParam", "RealParam", "ListParameter", "EnumParam"}

MATERIAL_VBS = """
Public Function GetMaterial(doc, target, onBody)
  Dim manager, mat
  Set manager = doc.Product.GetItem("CATMatManagerVBExt")
  If onBody Then
    manager.GetMaterialOnBody target, mat
  Else
    manager.GetMaterialOnPart target, mat
  End If
  GetMaterial = ""
  If IsObject(mat) Then
    If Not (mat Is Nothing) Then GetMaterial = mat.Name
  End If
End Function
"""

TOLERANCE_VBS = """
Public Function GetTolerance(dimension)
  Dim tolType, tolName, upTol, lowTol, dUpTol, dLowTol, displayMode
  dimension.GetTolerances tolType, tolName, upTol, lowTol, dUpTol, dLowTol, displayMode
  GetTolerance = Array(tolType, tolName, upTol, lowTol, dUpTol, dLowTol, displayMode)
End Function
"""


@dataclass
class Options:
    bbox: bool = True             # temporary extremum points -> bounding box
    geometry: bool = True         # sketch coordinates, point/curve/surface measurements
    component_mass: bool = True   # mass of every assembly component
    deep: bool = False            # products: extract every part of the assembly too


# --------------------------------------------------------------------------
# Small helpers around COM objects
# --------------------------------------------------------------------------

def safe(fn, default=None):
    """Run fn(); CATIA raises COM errors for members a given object lacks."""
    try:
        return fn()
    except Exception as exc:
        log.debug("ignored: %s", exc)
        return default


def plain(value):
    """Convert COM return values into JSON-friendly Python values."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return float(f"{value:.12g}")  # drop floating point noise, keep precision
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, dt.datetime):
        return value.isoformat()
    return str(value)


def val(obj, path):
    """Follow a dotted attribute path; None if any step is unavailable."""
    def walk():
        current = obj
        for attr in path.split("."):
            current = getattr(current, attr)
        return current
    return plain(safe(walk))


def items(collection):
    """Iterate a CATIA collection (1-based Item())."""
    count = safe(lambda: int(collection.Count), 0) or 0
    for i in range(1, count + 1):
        item = safe(lambda i=i: collection.Item(i))
        if item is not None:
            yield item


def com_type(obj):
    """CATIA interface name of an object, e.g. 'Pad', 'Hole', 'Length'."""
    try:
        return obj._oleobj_.GetTypeInfo().GetDocumentation(-1)[0]
    except Exception:
        return None


def name(obj):
    return safe(lambda: obj.Name)


def label(table, code):
    if code is None:
        return None
    try:
        return table[int(code)]
    except (IndexError, ValueError, TypeError):
        return f"code {code}"


def drop_none(d):
    return {k: v for k, v in d.items() if v is not None and v != [] and v != {}}


def short_param_name(full, owner):
    """'Part1\\PartBody\\Pad.1\\FirstLimit\\Length' -> 'FirstLimit\\Length'."""
    parts = (full or "").split("\\")
    if owner in parts:
        rest = parts[parts.index(owner) + 1:]
        if rest:
            return "\\".join(rest)
    return parts[-1]


# --------------------------------------------------------------------------
# CATIA session
# --------------------------------------------------------------------------

class CatiaSession:
    def __init__(self, visible=False):
        try:
            import pythoncom
            import pywintypes
            from win32com.client import dynamic
        except ImportError:
            sys.exit("pywin32 is required: pip install pywin32 (Windows with CATIA V5 only)")

        # Late binding on purpose: early-bound wrappers cast items to their
        # declared base type (Shape, HybridShape, ...) which hides members of
        # the real feature (Pad.FirstLimit, Hole.Diameter, ...).
        try:
            disp = pythoncom.GetActiveObject(pywintypes.IID("CATIA.Application"))
            self.app = dynamic.Dispatch(disp.QueryInterface(pythoncom.IID_IDispatch))
            self.started_here = False
            log.info("Attached to running CATIA session")
        except Exception:
            log.info("Starting CATIA (this can take a while) ...")
            self.app = dynamic.Dispatch("CATIA.Application")
            self.started_here = True
            self.app.Visible = visible

        self._restore = {}
        for attr in ("DisplayFileAlerts", "RefreshDisplay"):
            self._restore[attr] = safe(lambda a=attr: getattr(self.app, a))
            safe(lambda a=attr: setattr(self.app, a, False))

    def close(self):
        if self.started_here:
            safe(lambda: self.app.Quit())
            return
        for attr, value in self._restore.items():
            if value is not None:
                safe(lambda a=attr, v=value: setattr(self.app, a, v))

    # -- documents --------------------------------------------------------
    def open_documents(self):
        return {str(safe(lambda d=d: d.FullName, "")).lower() for d in items(self.app.Documents)}

    def open(self, path):
        """Return (document, was_already_open)."""
        full = str(Path(path).resolve())
        for doc in items(self.app.Documents):
            if str(safe(lambda d=doc: d.FullName, "")).lower() == full.lower():
                return doc, True
        return self.app.Documents.Open(full), False

    def close_new_documents(self, keep):
        """Close (without saving) every document not in the `keep` set."""
        for doc in reversed(list(items(self.app.Documents))):
            if str(safe(lambda d=doc: d.FullName, "")).lower() not in keep:
                safe(lambda d=doc: d.Close())

    # -- VBScript bridge --------------------------------------------------
    def evaluate(self, code, function, args):
        return self.app.SystemService.Evaluate(code, 0, function, list(args))

    def call_array(self, obj, method, size):
        """Call a method that fills a caller-supplied array (GetOrigin, GetPoint, ...).

        Such out-arrays cannot be passed from Python, so the call goes through
        CATIA's VBScript engine.
        """
        code = (
            "Public Function CallArray(obj)\n"
            f"  Dim values({size - 1})\n"
            f"  obj.{method} values\n"
            "  CallArray = values\n"
            "End Function\n"
        )
        return [plain(float(v)) for v in self.evaluate(code, "CallArray", [obj])]


# --------------------------------------------------------------------------
# Shared extraction pieces
# --------------------------------------------------------------------------

def document_info(doc):
    full = safe(lambda: doc.FullName)
    info = {
        "name": name(doc),
        "full_path": full,
        "type": com_type(doc),
        "read_only": safe(lambda: bool(doc.ReadOnly)),
        "saved": safe(lambda: bool(doc.Saved)),
    }
    if full and os.path.isfile(full):
        st = os.stat(full)
        info["size_bytes"] = st.st_size
        info["modified"] = dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
    return drop_none(info)


def product_properties(product):
    source = safe(lambda: int(product.Source))
    user = {}
    for p in items(safe(lambda: product.UserRefProperties)):
        user[short_param_name(name(p), None)] = safe(lambda p=p: p.ValueAsString())
    return drop_none({
        "part_number": val(product, "PartNumber"),
        "revision": val(product, "Revision"),
        "nomenclature": val(product, "Nomenclature"),
        "definition": val(product, "Definition"),
        "description": val(product, "DescriptionRef"),
        "source": label(PRODUCT_SOURCES, source),
        "user_properties": user,
    })


def mass_properties(session, product):
    """Mass/volume/area/CoG/inertia from Product.Analyze (units as CATIA reports them)."""
    analyze = safe(lambda: product.Analyze)
    if analyze is None:
        return None
    return drop_none({
        "mass_kg": val(analyze, "Mass"),
        "volume_m3": val(analyze, "Volume"),
        "wet_area_m2": val(analyze, "WetArea"),
        "center_of_gravity_mm": safe(lambda: session.call_array(analyze, "GetGravityCenter", 3)),
        "inertia_matrix": safe(lambda: session.call_array(analyze, "GetInertia", 9)),
    }) or None


def parameter_info(p):
    return drop_none({
        "name": name(p),
        "type": com_type(p),
        "value": plain(safe(lambda: p.Value)),
        "display": safe(lambda: p.ValueAsString()),
        "comment": safe(lambda: p.Comment) or None,
        "read_only": safe(lambda: bool(p.ReadOnly)),
        "hidden": safe(lambda: bool(p.Hidden)),
    })


def is_dimension(param):
    value = param.get("value")
    return (param.get("type") not in NON_DIMENSION_TYPES
            and isinstance(value, (int, float)) and not isinstance(value, bool))


def dimension_rows(parameters):
    """Physical dimensions (lengths, angles, ...) with the feature that owns them."""
    rows = []
    for p in parameters:
        if not is_dimension(p):
            continue
        path = p["name"].split("\\")
        rows.append({
            "owner": "\\".join(path[:-1]),
            "name": path[-1],
            "value": p["value"],
            "display": p.get("display"),
            "type": p.get("type"),
        })
    return rows


def relation_info(r):
    def params(count_attr, getter):
        count = safe(lambda: int(getattr(r, count_attr)), 0) or 0
        return [name(safe(lambda i=i: getattr(r, getter)(i))) for i in range(1, count + 1)]
    return drop_none({
        "name": name(r),
        "type": com_type(r),
        "formula": val(r, "Value"),
        "comment": safe(lambda: r.Comment) or None,
        "inputs": params("NbInParameters", "GetInParameter"),
        "outputs": params("NbOutParameters", "GetOutParameter"),
    })


def constraint_info(c):
    ctype = safe(lambda: int(c.Type))
    mode = safe(lambda: int(c.Mode))
    elements = [safe(lambda i=i: c.GetConstraintElement(i).DisplayName) for i in (1, 2, 3)]
    return drop_none({
        "name": name(c),
        "type": label(CONSTRAINT_TYPES, ctype),
        "type_code": ctype,
        "mode": label(CONSTRAINT_MODES, mode),
        "value": plain(safe(lambda: c.Dimension.Value)),
        "display": safe(lambda: c.Dimension.ValueAsString()),
        "status_code": safe(lambda: int(c.Status)),
        "elements": [e for e in elements if e],
    })


# --------------------------------------------------------------------------
# Feature specific details (everything else is covered by owned parameters)
# --------------------------------------------------------------------------

def _limit(limit):
    mode = safe(lambda: int(limit.LimitMode))
    return drop_none({
        "mode": label(LIMIT_MODES, mode),
        "dimension_mm": val(limit, "Dimension.Value"),
        "limiting_element": safe(lambda: limit.LimitingElement.DisplayName),
    }) or None


def prism_details(ex, f):
    return {
        "first_limit": safe(lambda: _limit(f.FirstLimit)),
        "second_limit": safe(lambda: _limit(f.SecondLimit)),
        "symmetric": val(f, "IsSymmetric"),
        "direction_reversed": safe(lambda: int(f.DirectionOrientation) == 1),
    }


def revolution_details(ex, f):
    return {
        "first_angle_deg": val(f, "FirstAngle.Value"),
        "second_angle_deg": val(f, "SecondAngle.Value"),
    }


def hole_details(ex, f):
    htype = safe(lambda: int(f.Type))
    bottom = safe(lambda: int(f.BottomType))
    thread = safe(lambda: int(f.ThreadingMode))
    details = {
        "hole_type": label(HOLE_TYPES, htype),
        "diameter_mm": val(f, "Diameter.Value"),
        "depth_mm": val(f, "BottomLimit.Dimension.Value"),
        "depth_mode": label(LIMIT_MODES, safe(lambda: int(f.BottomLimit.LimitMode))),
        "bottom_type": label(HOLE_BOTTOMS, bottom),
        "bottom_angle_deg": val(f, "BottomAngle.Value") if bottom == 1 else None,
        "threading": label(THREAD_MODES, thread),
        "origin_mm": safe(lambda: ex.s.call_array(f, "GetOrigin", 3)),
        "direction": safe(lambda: ex.s.call_array(f, "GetDirection", 3)),
    }
    if thread == 0:
        details.update({
            "thread_description": val(f, "HoleThreadDescription.Value"),
            "thread_diameter_mm": val(f, "ThreadDiameter.Value"),
            "thread_depth_mm": val(f, "ThreadDepth.Value"),
            "thread_pitch_mm": val(f, "ThreadPitch.Value"),
        })
    if htype in (2, 3, 4):
        details.update({
            "head_diameter_mm": val(f, "HeadDiameter.Value"),
            "head_depth_mm": val(f, "HeadDepth.Value"),
            "head_angle_deg": val(f, "HeadAngle.Value"),
        })
    return details


def fillet_details(ex, f):
    return {
        "radius_mm": val(f, "Radius.Value"),
        "edges": val(f, "ObjectsToFillet.Count"),
    }


def chamfer_details(ex, f):
    mode = safe(lambda: int(f.Mode))
    return {
        "chamfer_mode": label(CHAMFER_MODES, mode),
        "length1_mm": val(f, "Length1.Value"),
        "length2_mm": val(f, "Length2.Value") if mode == 1 else None,
        "angle_deg": val(f, "Angle.Value") if mode == 0 else None,
        "edges": val(f, "ElementsToChamfer.Count"),
    }


def shell_details(ex, f):
    return {
        "inner_thickness_mm": val(f, "InternalThickness.Value"),
        "outer_thickness_mm": val(f, "ExternalThickness.Value"),
        "faces_removed": val(f, "FacesToRemove.Count"),
    }


def thickness_details(ex, f):
    return {"offset_mm": val(f, "Offset.Value")}


def draft_details(ex, f):
    return {
        "angle_deg": plain(safe(lambda: f.DraftDomains.Item(1).DraftAngle.Value)),
        "domains": val(f, "DraftDomains.Count"),
    }


def rect_pattern_details(ex, f):
    return {
        "dir1_instances": val(f, "FirstDirectionRepartition.InstancesCount.Value"),
        "dir1_spacing_mm": val(f, "FirstDirectionRepartition.Spacing.Value"),
        "dir2_instances": val(f, "SecondDirectionRepartition.InstancesCount.Value"),
        "dir2_spacing_mm": val(f, "SecondDirectionRepartition.Spacing.Value"),
    }


def circ_pattern_details(ex, f):
    return {
        "instances": val(f, "AngularRepartition.InstancesCount.Value"),
        "angular_spacing_deg": val(f, "AngularRepartition.AngularSpacing.Value"),
        "radial_instances": val(f, "RadialRepartition.InstancesCount.Value"),
    }


def boolean_details(ex, f):
    return {"operand_body": val(f, "Body.Name")}


FEATURE_DETAILS = {
    "Pad": prism_details, "Pocket": prism_details,
    "Shaft": revolution_details, "Groove": revolution_details,
    "Hole": hole_details,
    "ConstRadEdgeFillet": fillet_details, "VarRadEdgeFillet": fillet_details,
    "FaceFillet": fillet_details, "TritangentFillet": fillet_details,
    "Chamfer": chamfer_details,
    "Shell": shell_details,
    "Thickness": thickness_details,
    "Draft": draft_details,
    "RectPattern": rect_pattern_details,
    "CircPattern": circ_pattern_details,
    "Add": boolean_details, "Remove": boolean_details,
    "Intersect": boolean_details, "Assemble": boolean_details,
}


# --------------------------------------------------------------------------
# Part
# --------------------------------------------------------------------------

class PartExtractor:
    def __init__(self, session, doc, opts, allow_edits=True):
        self.s = session
        self.doc = doc
        self.part = doc.Part
        self.opts = opts
        self.allow_edits = allow_edits
        self.parameters = self.part.Parameters
        self.spa = safe(lambda: doc.GetWorkbench("SPAWorkbench"))
        self.main_body = safe(lambda: self.part.MainBody.Name)
        self.parameter_rows = []

    def run(self):
        part = self.part
        parameters = self.parameter_rows = [parameter_info(p) for p in items(self.parameters)]
        result = {
            "kind": "part",
            "document": document_info(self.doc),
            "properties": product_properties(self.doc.Product),
            "material": self.material(part, on_body=False),
            "mass_properties": mass_properties(self.s, self.doc.Product),
            "density_kg_m3": self.density(),
            "bounding_box_mm": self.bounding_box(),
            "main_body": self.main_body,
            "bodies": [self.body(b) for b in items(part.Bodies)],
            "geometrical_sets": [self.hybrid_body(h) for h in items(safe(lambda: part.HybridBodies))],
            "ordered_geometrical_sets": [
                self.hybrid_body(g) for g in items(safe(lambda: part.OrderedGeometricalSets))],
            "axis_systems": [self.axis_system(a) for a in items(safe(lambda: part.AxisSystems))],
            "constraints": [constraint_info(c) for c in items(safe(lambda: part.Constraints))],
            "relations": [relation_info(r) for r in items(safe(lambda: part.Relations))],
            "parameters": parameters,
            "dimensions": dimension_rows(parameters),
        }
        result["summary"] = part_summary(result)
        return result

    # -- material / mass --------------------------------------------------
    def material(self, target, on_body):
        mat = safe(lambda: self.s.evaluate(MATERIAL_VBS, "GetMaterial", [self.doc, target, on_body]))
        if mat:
            return mat
        if not on_body:  # fall back to a 'Material' string parameter
            for p in self.parameter_rows:
                if p.get("name", "").split("\\")[-1] == "Material":
                    return p.get("display") or p.get("value") or None
        return None

    def density(self):
        inertias = safe(lambda: self.spa.Inertias)
        inertia = safe(lambda: inertias.Add(self.part.MainBody))
        if inertia is None:
            return None
        density = val(inertia, "Density")
        safe(lambda: inertias.Remove(inertias.Count))
        return density

    def bounding_box(self):
        """Axis-aligned bounding box of the main body, via temporary extremum points."""
        if not self.opts.bbox or self.spa is None:
            return None
        if not self.allow_edits:
            log.info("  bounding box skipped: %s was already open in CATIA", name(self.doc))
            return None
        part = self.part
        temp = safe(lambda: part.HybridBodies.Add())
        if temp is None:
            return None
        box = {}
        try:
            temp.Name = BBOX_SET_NAME
            hsf = part.HybridShapeFactory
            body_ref = part.CreateReferenceFromObject(part.MainBody)
            dirs = [hsf.AddNewDirectionByCoord(*v) for v in ((1, 0, 0), (0, 1, 0), (0, 0, 1))]
            for axis in range(3):
                for is_max in (0, 1):
                    ext = hsf.AddNewExtremum(body_ref, dirs[axis], is_max)
                    # secondary directions make the extremum a single point
                    ext.Direction2 = dirs[(axis + 1) % 3]
                    ext.ExtremumType2 = 1
                    ext.Direction3 = dirs[(axis + 2) % 3]
                    ext.ExtremumType3 = 1
                    temp.AppendHybridShape(ext)
                    part.UpdateObject(ext)
                    measurable = self.spa.GetMeasurable(part.CreateReferenceFromObject(ext))
                    point = self.s.call_array(measurable, "GetPoint", 3)
                    box[("min_", "max_")[is_max] + "xyz"[axis]] = point[axis]
        except Exception as exc:
            log.warning("  bounding box failed (empty or surface-only main body?): %s", exc)
            box = {}
        finally:
            def delete_temp():
                sel = self.doc.Selection
                sel.Clear()
                sel.Add(temp)
                sel.Delete()
                sel.Clear()
            safe(delete_temp)
        if len(box) != 6:
            return None
        for axis in "xyz":
            box[f"size_{axis}"] = plain(box[f"max_{axis}"] - box[f"min_{axis}"])
        return box

    # -- tree -------------------------------------------------------------
    def owned_parameters(self, obj):
        owner = name(obj)
        result = {}
        for p in items(safe(lambda: self.parameters.SubList(obj, True))):
            result[short_param_name(name(p), owner)] = safe(lambda p=p: p.ValueAsString())
        return result

    def active(self, obj):
        inactive = safe(lambda: self.part.IsInactive(obj))
        return None if inactive is None else not inactive

    def body(self, body):
        body_name = name(body)
        return drop_none({
            "name": body_name,
            "type": com_type(body),
            "is_main_body": body_name == self.main_body,
            "in_boolean_operation": safe(lambda: bool(body.InBooleanOperation)),
            "material": self.material(body, on_body=True),
            "features": [self.feature(s) for s in items(safe(lambda: body.Shapes))],
            "sketches": [self.sketch(s) for s in items(safe(lambda: body.Sketches))],
            "hybrid_shapes": [self.hybrid_shape(h) for h in items(safe(lambda: body.HybridShapes))],
            "geometrical_sets": [self.hybrid_body(h) for h in items(safe(lambda: body.HybridBodies))],
        })

    def feature(self, shape):
        ftype = com_type(shape)
        info = {
            "name": name(shape),
            "type": ftype,
            "active": self.active(shape),
            "profile": val(shape, "Sketch.Name"),
        }
        extractor = FEATURE_DETAILS.get(ftype)
        if extractor:
            info.update(safe(lambda: extractor(self, shape), {}))
        info["parameters"] = self.owned_parameters(shape)
        return drop_none(info)

    def sketch(self, sketch):
        info = {"name": name(sketch), "type": com_type(sketch)}
        elements = list(items(safe(lambda: sketch.GeometricElements)))
        info["element_counts"] = dict(Counter(com_type(e) or "?" for e in elements))
        if self.opts.geometry:
            axis = safe(lambda: self.s.call_array(sketch, "GetAbsoluteAxisData", 9))
            if axis:
                info.update(origin_mm=axis[0:3], h_direction=axis[3:6], v_direction=axis[6:9])
            info["elements"] = [self.sketch_element(e) for e in elements]
        info["constraints"] = [constraint_info(c) for c in items(safe(lambda: sketch.Constraints))]
        return drop_none(info)

    def sketch_element(self, e):
        """2D geometry in sketch coordinates (mm)."""
        etype = com_type(e)
        info = {
            "name": name(e),
            "type": etype,
            "construction": safe(lambda: bool(e.Construction)),
        }
        arr = lambda method, size: safe(lambda: self.s.call_array(e, method, size))
        if etype == "Point2D":
            info["point"] = arr("GetCoordinates", 2)
        elif etype == "Line2D":
            ends = arr("GetEndPoints", 4)
            info["end_points"] = ends
            if ends:
                info["length_mm"] = plain(math.hypot(ends[2] - ends[0], ends[3] - ends[1]))
        elif etype == "Circle2D":
            info["center"] = arr("GetCenter", 2)
            info["radius_mm"] = val(e, "Radius")
            info["end_points"] = arr("GetEndPoints", 4)
        elif etype == "Ellipse2D":
            info["center"] = arr("GetCenter", 2)
            info["major_radius_mm"] = val(e, "MajorRadius")
            info["minor_radius_mm"] = val(e, "MinorRadius")
        elif etype in ("Spline2D", "Hyperbola2D", "Parabola2D"):
            info["end_points"] = arr("GetEndPoints", 4)
        return drop_none(info)

    def hybrid_body(self, hb, depth=0):
        if depth > MAX_DEPTH:
            return {"name": name(hb), "truncated": True}
        children = (list(items(safe(lambda: hb.HybridBodies)))
                    + list(items(safe(lambda: hb.OrderedGeometricalSets))))
        return drop_none({
            "name": name(hb),
            "type": com_type(hb),
            "shapes": [self.hybrid_shape(h) for h in items(safe(lambda: hb.HybridShapes))],
            "sketches": [self.sketch(s) for s in items(safe(lambda: hb.HybridSketches))]
            or [self.sketch(s) for s in items(safe(lambda: hb.Sketches))],
            "children": [self.hybrid_body(c, depth + 1) for c in children],
        })

    def hybrid_shape(self, shape):
        stype = com_type(shape)
        info = {
            "name": name(shape),
            "type": stype,
            "active": self.active(shape),
            "parameters": self.owned_parameters(shape),
        }
        if self.opts.geometry:
            info.update(self.measure(shape, stype or ""))
        return drop_none(info)

    def measure(self, obj, otype):
        """Point coordinates, curve length or surface area of a wireframe/surface element."""
        if self.spa is None:
            return {}
        m = safe(lambda: self.spa.GetMeasurable(self.part.CreateReferenceFromObject(obj)))
        if m is None:
            return {}
        if "Point" in otype:
            return {"point_mm": safe(lambda: self.s.call_array(m, "GetPoint", 3))}
        length = val(m, "Length")
        if length is not None:
            return {"length_mm": length,
                    "radius_mm": val(m, "Radius") if "Circle" in otype else None}
        return {"area_m2": val(m, "Area")}

    def axis_system(self, axis):
        info = {"name": name(axis), "type": com_type(axis)}
        for key, method in (("origin_mm", "GetOrigin"), ("x_axis", "GetXAxis"),
                            ("y_axis", "GetYAxis"), ("z_axis", "GetZAxis")):
            info[key] = safe(lambda m=method: self.s.call_array(axis, m, 3))
        return drop_none(info)


def walk_part_tree(result):
    """Yield (location, container) for every body and geometrical set of a part result."""
    def sets(nodes, prefix):
        for node in nodes:
            path = f"{prefix}/{node.get('name')}" if prefix else node.get("name")
            yield path, node
            yield from sets(node.get("children", []) + node.get("geometrical_sets", []), path)
    for body in result.get("bodies", []):
        yield body.get("name"), body
        yield from sets(body.get("geometrical_sets", []), body.get("name"))
    yield from sets(result.get("geometrical_sets", []) + result.get("ordered_geometrical_sets", []), "")


def part_summary(result):
    features, shapes = Counter(), Counter()
    sketches = constraints = 0
    for _, node in walk_part_tree(result):
        features.update(f.get("type") or "?" for f in node.get("features", []))
        shapes.update(s.get("type") or "?" for s in node.get("shapes", []) + node.get("hybrid_shapes", []))
        sketches += len(node.get("sketches", []))
        constraints += sum(len(s.get("constraints", [])) for s in node.get("sketches", []))
    return {
        "bodies": len(result.get("bodies", [])),
        "features": sum(features.values()),
        "feature_types": dict(features),
        "wireframe_surface_elements": sum(shapes.values()),
        "wireframe_surface_types": dict(shapes),
        "sketches": sketches,
        "sketch_constraints": constraints,
        "parameters": len(result.get("parameters", [])),
        "dimensions": len(result.get("dimensions", [])),
        "relations": len(result.get("relations", [])),
    }


# --------------------------------------------------------------------------
# Product (assembly)
# --------------------------------------------------------------------------

class ProductExtractor:
    def __init__(self, session, doc, opts, allow_edits=True):
        self.s = session
        self.doc = doc
        self.opts = opts
        self.allow_edits = allow_edits
        self.part_docs = {}

    def run(self):
        root = self.doc.Product
        safe(lambda: root.ApplyWorkMode(2))  # DESIGN_MODE: load full component data
        tree = self.node(root, 0)
        result = {
            "kind": "product",
            "document": document_info(self.doc),
            "properties": product_properties(root),
            "mass_properties": mass_properties(self.s, root),
            "parameters": [parameter_info(p) for p in items(safe(lambda: root.Parameters))],
            "relations": [relation_info(r) for r in items(safe(lambda: root.Relations))],
            "tree": tree,
            "bom": bill_of_materials(tree),
        }
        if self.opts.deep:
            parts = {}
            for path, part_doc in self.part_docs.items():
                log.info("  part %s", name(part_doc))
                try:
                    parts[path] = PartExtractor(self.s, part_doc, self.opts, self.allow_edits).run()
                except Exception as exc:
                    log.warning("  failed on %s: %s", path, exc)
                    parts[path] = {"error": str(exc)}
            result["parts"] = parts
        result["summary"] = {
            "components": count_nodes(tree) - 1,
            "unique_references": len(result["bom"]),
            "depth": tree_depth(tree),
            "assembly_constraints": count_constraints(tree),
        }
        return result

    def node(self, product, depth):
        ref_doc = safe(lambda: product.ReferenceProduct.Parent)
        file = safe(lambda: ref_doc.FullName)
        doc_type = com_type(ref_doc)
        info = {"instance_name": name(product), **product_properties(product),
                "file": file, "document_type": doc_type}
        if depth > 0:
            m = safe(lambda: self.s.call_array(product.Position, "GetComponents", 12))
            if m:
                info["position"] = {"x_axis": m[0:3], "y_axis": m[3:6], "z_axis": m[6:9],
                                    "origin_mm": m[9:12]}
            if self.opts.component_mass:
                info["mass_kg"] = val(product, "Analyze.Mass")
        if doc_type == "PartDocument" and file:
            self.part_docs.setdefault(file, ref_doc)
        info["constraints"] = [constraint_info(c) for c in
                               items(safe(lambda: product.Connections("CATIAConstraints")))]
        info["children"] = ([self.node(c, depth + 1) for c in items(safe(lambda: product.Products))]
                            if depth < MAX_DEPTH else [])
        return drop_none(info) | {"children": info["children"]}


def bill_of_materials(tree):
    rows = {}

    def walk(node):
        for child in node.get("children", []):
            key = (child.get("part_number"), child.get("file"))
            row = rows.setdefault(key, {
                "part_number": child.get("part_number"),
                "nomenclature": child.get("nomenclature"),
                "revision": child.get("revision"),
                "definition": child.get("definition"),
                "source": child.get("source"),
                "kind": "assembly" if child.get("children") else "part",
                "file": child.get("file"),
                "quantity": 0,
            })
            row["quantity"] += 1
            walk(child)
    walk(tree)
    return list(rows.values())


def count_nodes(node):
    return 1 + sum(count_nodes(c) for c in node.get("children", []))


def tree_depth(node):
    return max((1 + tree_depth(c) for c in node.get("children", [])), default=0)


def count_constraints(node):
    return len(node.get("constraints", [])) + sum(count_constraints(c) for c in node.get("children", []))


# --------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------

class DrawingExtractor:
    def __init__(self, session, doc, opts, allow_edits=True):
        self.s = session
        self.doc = doc
        self.opts = opts

    def run(self):
        sheets = [self.sheet(s) for s in items(self.doc.Sheets)]
        views = [v for s in sheets for v in s.get("views", [])]
        return {
            "kind": "drawing",
            "document": document_info(self.doc),
            "sheets": sheets,
            "summary": {
                "sheets": len(sheets),
                "views": len(views),
                "dimensions": sum(len(v.get("dimensions", [])) for v in views),
                "texts": sum(len(v.get("texts", [])) for v in views),
            },
        }

    def sheet(self, sheet):
        return drop_none({
            "name": name(sheet),
            "paper_size_code": safe(lambda: int(sheet.PaperSize)),
            "width_mm": plain(safe(lambda: sheet.GetPaperWidth())),
            "height_mm": plain(safe(lambda: sheet.GetPaperHeight())),
            "scale": val(sheet, "Scale"),
            "orientation_code": safe(lambda: int(sheet.Orientation)),
            "projection_method_code": safe(lambda: int(sheet.ProjectionMethod)),
            "is_detail_sheet": safe(lambda: bool(sheet.IsDetail())),
            "views": [self.view(v) for v in items(safe(lambda: sheet.Views))],
        })

    def view(self, view):
        return drop_none({
            "name": name(view),
            "x_mm": val(view, "x"),
            "y_mm": val(view, "y"),
            "scale": val(view, "Scale"),
            "angle_rad": val(view, "Angle"),
            "linked_document": safe(lambda: view.GenerativeBehavior.Document.Name),
            "dimensions": [self.dimension(d) for d in items(safe(lambda: view.Dimensions))],
            "texts": [drop_none({"text": val(t, "Text"), "x_mm": val(t, "x"), "y_mm": val(t, "y")})
                      for t in items(safe(lambda: view.Texts))],
            "tables": safe(lambda: int(view.Tables.Count)),
            "gdts": safe(lambda: int(view.GDTs.Count)),
        })

    def dimension(self, dim):
        info = {
            "name": name(dim),
            "dim_type_code": safe(lambda: int(dim.DimType)),
            "value": plain(safe(lambda: dim.GetValue().Value)),
        }
        tol = safe(lambda: self.s.evaluate(TOLERANCE_VBS, "GetTolerance", [dim]))
        if tol and tol[0]:
            info["tolerance"] = drop_none({
                "type_code": plain(tol[0]),
                "name": plain(tol[1]) or None,
                "upper": plain(tol[4]),
                "lower": plain(tol[5]),
                "upper_text": plain(tol[2]) or None,
                "lower_text": plain(tol[3]) or None,
            })
        return drop_none(info)


EXTRACTORS = {"part": PartExtractor, "product": ProductExtractor, "drawing": DrawingExtractor}


def extract_document(session, doc, opts, allow_edits):
    kind = DOC_TYPES.get(com_type(doc)) or DOC_EXTENSIONS.get(Path(name(doc) or "").suffix.lower())
    if kind is None:
        raise ValueError(f"unsupported document type: {com_type(doc)} ({name(doc)})")
    return EXTRACTORS[kind](session, doc, opts, allow_edits).run()


# --------------------------------------------------------------------------
# Flat tables (CSV / Excel)
# --------------------------------------------------------------------------

def flatten(result, tables):
    """Append rows for one extraction result to the shared `tables` dict."""
    src = result.get("document", {}).get("name")
    kind = result.get("kind")
    if kind == "part":
        flatten_part(result, src, tables)
    elif kind == "product":
        flatten_product(result, src, tables)
        for part in result.get("parts", {}).values():
            if "error" not in part:
                flatten_part(part, part["document"].get("name"), tables)
    elif kind == "drawing":
        flatten_drawing(result, src, tables)


def flatten_part(r, src, t):
    mass = r.get("mass_properties") or {}
    bbox = r.get("bounding_box_mm") or {}
    props = r.get("properties") or {}
    summary = r.get("summary", {})
    t["parts"].append({
        "file": src, "part_number": props.get("part_number"), "revision": props.get("revision"),
        "nomenclature": props.get("nomenclature"), "material": r.get("material"),
        "mass_kg": mass.get("mass_kg"), "volume_m3": mass.get("volume_m3"),
        "wet_area_m2": mass.get("wet_area_m2"), "density_kg_m3": r.get("density_kg_m3"),
        "cog_mm": mass.get("center_of_gravity_mm"),
        "size_x_mm": bbox.get("size_x"), "size_y_mm": bbox.get("size_y"), "size_z_mm": bbox.get("size_z"),
        **{k: v for k, v in summary.items() if not isinstance(v, dict)},
    })
    for p in r.get("parameters", []):
        t["parameters"].append({"file": src, **p})
    for d in r.get("dimensions", []):
        t["dimensions"].append({"file": src, **d})
    for rel in r.get("relations", []):
        t["relations"].append({"file": src, **rel})
    for location, node in walk_part_tree(r):
        for f in node.get("features", []):
            details = {k: v for k, v in f.items()
                       if k not in ("name", "type", "active", "profile", "parameters")}
            t["features"].append({
                "file": src, "body": location, "feature": f.get("name"), "type": f.get("type"),
                "active": f.get("active"), "profile": f.get("profile"), "details": details,
                "parameters": "; ".join(f"{k}={v}" for k, v in f.get("parameters", {}).items()),
            })
        for s in node.get("shapes", []) + node.get("hybrid_shapes", []):
            t["wireframe_surfaces"].append({
                "file": src, "set": location, "name": s.get("name"), "type": s.get("type"),
                "point_mm": s.get("point_mm"), "length_mm": s.get("length_mm"),
                "radius_mm": s.get("radius_mm"), "area_m2": s.get("area_m2"),
                "parameters": "; ".join(f"{k}={v}" for k, v in s.get("parameters", {}).items()),
            })
        for sk in node.get("sketches", []):
            t["sketches"].append({
                "file": src, "location": location, "sketch": sk.get("name"),
                "origin_mm": sk.get("origin_mm"), "h_direction": sk.get("h_direction"),
                "v_direction": sk.get("v_direction"), "element_counts": sk.get("element_counts"),
                "constraints": len(sk.get("constraints", [])),
            })
            for c in sk.get("constraints", []):
                t["sketch_constraints"].append({"file": src, "sketch": sk.get("name"), **c})
    for c in r.get("constraints", []):
        t["sketch_constraints"].append({"file": src, "sketch": "(part)", **c})


def flatten_product(r, src, t):
    def walk(node, level, path):
        here = f"{path}/{node.get('instance_name')}" if path else node.get("instance_name")
        row = {k: v for k, v in node.items() if k not in ("children", "constraints", "user_properties")}
        t["components"].append({"assembly": src, "level": level, "path": here, **row})
        for c in node.get("constraints", []):
            t["assembly_constraints"].append({"assembly": src, "product": here, **c})
        for child in node.get("children", []):
            walk(child, level + 1, here)
    walk(r["tree"], 0, "")
    for row in r.get("bom", []):
        t["bom"].append({"assembly": src, **row})
    for p in r.get("parameters", []):
        t["parameters"].append({"file": src, **p})


def flatten_drawing(r, src, t):
    for sheet in r.get("sheets", []):
        for view in sheet.get("views", []):
            t["drawing_views"].append({
                "file": src, "sheet": sheet.get("name"), "view": view.get("name"),
                "scale": view.get("scale"), "x_mm": view.get("x_mm"), "y_mm": view.get("y_mm"),
                "linked_document": view.get("linked_document"),
                "dimensions": len(view.get("dimensions", [])), "texts": len(view.get("texts", [])),
            })
            for d in view.get("dimensions", []):
                t["drawing_dimensions"].append(
                    {"file": src, "sheet": sheet.get("name"), "view": view.get("name"), **d})
            for txt in view.get("texts", []):
                t["drawing_texts"].append(
                    {"file": src, "sheet": sheet.get("name"), "view": view.get("name"), **txt})


def cell(v):
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return v


def table_columns(rows):
    columns = {}
    for row in rows:
        columns.update(dict.fromkeys(row))
    return list(columns)


def write_csv(tables, out_dir):
    for table, rows in tables.items():
        path = out_dir / f"{table}.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=table_columns(rows))
            writer.writeheader()
            writer.writerows({k: cell(v) for k, v in row.items()} for row in rows)
        log.info("Wrote %s", path)


def write_xlsx(tables, path):
    try:
        from openpyxl import Workbook
    except ImportError:
        log.error("--xlsx needs openpyxl: pip install openpyxl")
        return
    wb = Workbook()
    wb.remove(wb.active)
    for table, rows in tables.items():
        ws = wb.create_sheet(table[:31])
        columns = table_columns(rows)
        ws.append(columns)
        for row in rows:
            ws.append([cell(row.get(c)) for c in columns])
        ws.freeze_panes = "A2"
    wb.save(path)
    log.info("Wrote %s", path)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def collect_inputs(inputs, recursive):
    files = []
    for item in inputs:
        path = Path(item)
        if path.is_dir():
            pattern = path.rglob("*") if recursive else path.glob("*")
            files += sorted(p for p in pattern if p.suffix.lower() in DOC_EXTENSIONS)
        elif path.is_file() and path.suffix.lower() in DOC_EXTENSIONS:
            files.append(path)
        else:
            log.warning("Skipping %s (not a CATPart/CATProduct/CATDrawing file or folder)", item)
    return files


def print_summary(result):
    summary = result.get("summary", {})
    flat = ", ".join(f"{k}={v}" for k, v in summary.items() if not isinstance(v, dict))
    log.info("  %s: %s", result.get("kind"), flat)


def normalize_dashes(argv):
    """Accept options pasted from Word/Outlook, where '--xlsx' often becomes '–xlsx'."""
    long_options = {"out", "recursive", "csv", "xlsx", "deep", "no-bbox", "fast", "visible", "verbose"}
    fixed = []
    for arg in argv:
        bare = arg.lstrip("-–—−")  # hyphen, en dash, em dash, minus sign
        if bare != arg and bare.split("=")[0].lower() in long_options:
            arg = "--" + bare  # '-xlsx', '–xlsx' -> '--xlsx'
        fixed.append(arg)
    return fixed


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Extract features, dimensions, parameters and metadata from CATIA V5 files.")
    parser.add_argument("inputs", nargs="*",
                        help="CATPart/CATProduct/CATDrawing files or folders "
                             "(omit to use the document currently active in CATIA)")
    parser.add_argument("-o", "--out", default="catia_extract_output", help="output folder")
    parser.add_argument("-r", "--recursive", action="store_true", help="search folders recursively")
    parser.add_argument("--csv", action="store_true", help="also write flat CSV tables")
    parser.add_argument("--xlsx", action="store_true", help="also write an Excel workbook (needs openpyxl)")
    parser.add_argument("--deep", action="store_true",
                        help="for assemblies, also extract every part they contain")
    parser.add_argument("--no-bbox", action="store_true", help="skip the bounding box computation")
    parser.add_argument("--fast", action="store_true",
                        help="skip bounding box, sketch/wireframe geometry and per-component mass")
    parser.add_argument("--visible", action="store_true", help="show CATIA if the script starts it")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args(normalize_dashes(sys.argv[1:] if argv is None else argv))

    logging.basicConfig(level=logging.DEBUG if args.verbose > 1 else logging.INFO,
                        format="%(message)s")
    if sys.platform != "win32":
        sys.exit("This script automates CATIA V5 through COM and must run on Windows.")

    opts = Options(bbox=not (args.fast or args.no_bbox), geometry=not args.fast,
                   component_mass=not args.fast, deep=args.deep)
    files = collect_inputs(args.inputs, args.recursive)
    if args.inputs and not files:
        sys.exit("No CATIA files found.")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = defaultdict(list)
    failures = 0
    session = CatiaSession(visible=args.visible)
    try:
        jobs = files or [None]
        for path in jobs:
            keep = session.open_documents()
            try:
                if path is None:
                    doc = session.app.ActiveDocument
                    already_open = True
                else:
                    log.info("Opening %s", path)
                    doc, already_open = session.open(path)
                log.info("Extracting %s", name(doc))
                result = extract_document(session, doc, opts, allow_edits=not already_open)
                result["extracted_at"] = dt.datetime.now().isoformat(timespec="seconds")
                json_path = out_dir / f"{name(doc)}.json"
                json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
                log.info("  wrote %s", json_path)
                print_summary(result)
                flatten(result, tables)
            except Exception as exc:
                failures += 1
                log.error("  FAILED %s: %s", path or "active document", exc,
                          exc_info=args.verbose > 0)
            finally:
                if path is not None:
                    session.close_new_documents(keep)
    finally:
        session.close()

    if tables and args.csv:
        write_csv(tables, out_dir)
    if tables and args.xlsx:
        write_xlsx(tables, out_dir / "catia_extract.xlsx")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
