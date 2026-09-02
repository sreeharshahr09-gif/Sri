from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

import cadquery as cq
from cadquery import importers


def _vec(v) -> List[float]:
    return [float(v.x), float(v.y), float(v.z)]


def _bbox(shape) -> List[float]:
    b = shape.BoundingBox()
    return [float(b.xmin), float(b.ymin), float(b.zmin), float(b.xmax), float(b.ymax), float(b.zmax)]


def _safe_normal(face) -> List[float]:
    try:
        return _vec(face.normalAt())
    except Exception:
        return [0.0, 0.0, 0.0]


def _face_witness(face, verts, tris) -> List[float]:
    # The geometric centroid of a trimmed/holed face can lie outside the face.
    # A tessellated triangle centroid is a much safer point for Abaqus findAt.
    if tris:
        a, b, c = [verts[int(i)] for i in tris[0]]
        return [float((a.x+b.x+c.x)/3.0), float((a.y+b.y+c.y)/3.0), float((a.z+b.z+c.z)/3.0)]
    return _vec(face.Center())


def _body_witness(solid, body_diag: float) -> List[float]:
    c = solid.Center()
    try:
        if solid.isInside(c, max(body_diag*1e-8, 1e-7)):
            return _vec(c)
    except Exception:
        pass
    eps = max(body_diag*1e-5, 1e-6)
    for face in solid.Faces():
        try:
            verts, tris = face.tessellate(max(body_diag/500.0, 1e-5))
            q = _face_witness(face, verts, tris)
            n = _safe_normal(face)
            for sign in (-1.0, 1.0):
                pt = cq.Vector(q[0] + sign*eps*n[0], q[1] + sign*eps*n[1], q[2] + sign*eps*n[2])
                if solid.isInside(pt, max(body_diag*1e-8, 1e-7)):
                    return _vec(pt)
        except Exception:
            continue
    return _vec(c)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _sample_edge(edge, chord_tol: float) -> List[List[float]]:
    # CadQuery/OpenCascade exposes normalized positionAt(t).  Use more points for
    # long edges, with sane limits so a large STEP does not explode in JSON size.
    try:
        length = max(float(edge.Length()), 1e-12)
    except Exception:
        length = 1.0
    n = max(2, min(64, int(math.ceil(length / max(chord_tol, 1e-6))) + 1))
    pts = []
    for i in range(n):
        t = i / (n - 1)
        try:
            pts.append(_vec(edge.positionAt(t)))
        except Exception:
            pass
    if len(pts) < 2:
        c = _vec(edge.Center())
        pts = [c, c]
    return pts


def _import_solids(path: Path):
    """Return (root_shape, solids) covering the whole STEP file.

    importStep() can return a Workplane holding several top-level shapes.  v0.6
    called .val(), which keeps only the first one, so every body after the first
    was silently dropped from a multi-body STEP.  Compounding all values keeps
    them.
    """
    imported = importers.importStep(str(path))
    values = imported.vals()
    if not values:
        raise ValueError("The STEP file contains no shapes.")
    root = values[0] if len(values) == 1 else cq.Compound.makeCompound(values)
    solids = [s for v in values for s in v.Solids()]
    if not solids:
        # Some STEP files contain shells/faces rather than solids. Treat the root
        # shape as one body so face set tagging is still useful.
        solids = [root]
    return root, solids


def parse_step(path: str | Path, tolerance: float | None = None) -> Dict[str, Any]:
    path = Path(path)
    root, solids = _import_solids(path)

    global_bbox = _bbox(root)
    diag = math.sqrt(
        (global_bbox[3] - global_bbox[0]) ** 2
        + (global_bbox[4] - global_bbox[1]) ** 2
        + (global_bbox[5] - global_bbox[2]) ** 2
    )
    tess_tol = float(tolerance or max(diag / 500.0, 1e-4))
    edge_tol = max(diag / 100.0, tess_tol * 2.0, 1e-4)

    faces_out: List[Dict[str, Any]] = []
    edges_out: List[Dict[str, Any]] = []
    bodies_out: List[Dict[str, Any]] = []

    all_vertices: List[float] = []
    all_triangles: List[int] = []
    triangle_face_ids: List[str] = []
    vertex_offset = 0

    for bi, solid in enumerate(solids, start=1):
        body_id = f"B{bi:03d}"
        bb = _bbox(solid)
        body_diag = math.sqrt((bb[3]-bb[0])**2 + (bb[4]-bb[1])**2 + (bb[5]-bb[2])**2)
        bodies_out.append(
            {
                "id": body_id,
                "center": _vec(solid.Center()),
                "witness": _body_witness(solid, body_diag),
                "volume": float(solid.Volume()) if hasattr(solid, "Volume") else 0.0,
                "bbox": bb,
            }
        )

        for fi, face in enumerate(solid.Faces(), start=1):
            face_id = f"{body_id}_F{fi:04d}"
            verts, tris = face.tessellate(tess_tol)
            verts_list = [_vec(v) for v in verts]
            local_flat = [x for p in verts_list for x in p]
            all_vertices.extend(local_flat)
            for tri in tris:
                a, b, c = [int(x) for x in tri]
                all_triangles.extend([vertex_offset + a, vertex_offset + b, vertex_offset + c])
                triangle_face_ids.append(face_id)
            vertex_offset += len(verts_list)

            faces_out.append(
                {
                    "id": face_id,
                    "body_id": body_id,
                    "center": _vec(face.Center()),
                    "witness": _face_witness(face, verts, tris),
                    "area": float(face.Area()),
                    "normal": _safe_normal(face),
                    "bbox": _bbox(face),
                    "triangle_count": len(tris),
                }
            )

        for ei, edge in enumerate(solid.Edges(), start=1):
            edge_id = f"{body_id}_E{ei:04d}"
            try:
                midpoint = _vec(edge.positionAt(0.5))
            except Exception:
                midpoint = _vec(edge.Center())
            try:
                tangent = _vec(edge.tangentAt(0.5))
            except Exception:
                tangent = [0.0, 0.0, 0.0]
            edges_out.append(
                {
                    "id": edge_id,
                    "body_id": body_id,
                    "midpoint": midpoint,
                    "length": float(edge.Length()),
                    "tangent": tangent,
                    "bbox": _bbox(edge),
                    "polyline": _sample_edge(edge, edge_tol),
                }
            )

    metadata = {
        "source": {
            "name": path.name,
            "sha256": sha256_file(path),
            "units": "model_units",
        },
        "bbox": global_bbox,
        "tessellation_tolerance": tess_tol,
        "bodies": bodies_out,
        "faces": faces_out,
        "edges": edges_out,
    }
    mesh = {
        "vertices": all_vertices,
        "triangles": all_triangles,
        "triangle_face_ids": triangle_face_ids,
    }
    return {"metadata": metadata, "mesh": mesh}


def metadata_index(metadata: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    idx: Dict[str, Dict[str, Any]] = {}
    for kind in ("bodies", "faces", "edges"):
        for item in metadata.get(kind, []):
            idx[item["id"]] = item
    return idx
