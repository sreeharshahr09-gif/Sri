from __future__ import annotations

import math
import statistics
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from cadquery import importers

from .cad import _import_solids


def _vec(v) -> List[float]:
    return [float(v.x), float(v.y), float(v.z)]


def _dot(a: Iterable[float], b: Iterable[float]) -> float:
    return sum(float(x) * float(y) for x, y in zip(a, b))


def _sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def _norm(a) -> float:
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _norm(a)
    if n <= 1e-15:
        return [0.0, 0.0, 0.0]
    return [x / n for x in a]


def _safe_normal(face) -> List[float]:
    try:
        return _unit(_vec(face.normalAt()))
    except Exception:
        return [0.0, 0.0, 0.0]


def _bbox(face) -> List[float]:
    b = face.BoundingBox()
    return [float(b.xmin), float(b.ymin), float(b.zmin), float(b.xmax), float(b.ymax), float(b.zmax)]


def _witness(face) -> List[float]:
    try:
        verts, tris = face.tessellate(max(math.sqrt(max(float(face.Area()), 1e-12)) / 100.0, 1e-5))
        if tris:
            a, b, c = [verts[int(i)] for i in tris[0]]
            return [(a.x+b.x+c.x)/3.0, (a.y+b.y+c.y)/3.0, (a.z+b.z+c.z)/3.0]
    except Exception:
        pass
    return _vec(face.Center())


def _vertex_at(pt):
    # import locally to avoid hard-wiring an OCC class in the JSON-oriented module API
    import cadquery as cq
    return cq.Vertex.makeVertex(float(pt[0]), float(pt[1]), float(pt[2]))


def _interval_overlap(a0, a1, b0, b1) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _bbox_gap(b1: List[float], b2: List[float]) -> float:
    """Shortest distance between two axis-aligned bounding boxes (0 if they touch)."""
    total = 0.0
    for axis in range(3):
        d = max(b1[axis] - b2[axis + 3], b2[axis] - b1[axis + 3], 0.0)
        total += d * d
    return math.sqrt(total)


def _axis_index(axis: str) -> int:
    axis = str(axis or 'Z').upper()
    return {'X': 0, 'Y': 1, 'Z': 2}.get(axis, 2)


def _radial_unit(axis: str, top_sign: str | int | float = '+') -> List[float]:
    u = [0.0, 0.0, 0.0]
    sign = -1.0 if str(top_sign).strip().startswith('-') else 1.0
    u[_axis_index(axis)] = sign
    return u


def detect_sipes(path: str | Path, config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Detect likely opposing sipe walls from STEP geometry.

    This is deliberately a *candidate detector*, not a semantic CAD classifier. It uses
    wall orientation, radial depth, opposed normals, face-to-face witness distance and
    radial overlap. Returned sets are intended to be reviewed in the HTML viewer.
    """
    cfg = {
        'radial_axis': 'Z',
        'top_sign': '+',
        'max_gap': 2.0,
        'min_depth': 3.0,
        'opposition_tolerance_deg': 25.0,
        'wall_tilt_tolerance_deg': 25.0,
        'alignment_tolerance_deg': 50.0,
        'min_area': 0.0,
        'cluster_tolerance': None,
        'min_confidence': 0.35,
    }
    if config:
        cfg.update(config)

    path = Path(path)
    # Shares the importer with cad.py so detection sees every body in a
    # multi-root STEP, not just the first one.
    _root, solids = _import_solids(path)
    radial_axis = str(cfg['radial_axis']).upper()
    ai = _axis_index(radial_axis)
    radial = _radial_unit(radial_axis, cfg.get('top_sign', '+'))

    max_gap = max(float(cfg.get('max_gap', 2.0)), 0.0)
    min_depth = max(float(cfg.get('min_depth', 0.0)), 0.0)
    opp_cos = math.cos(math.radians(float(cfg.get('opposition_tolerance_deg', 25.0))))
    wall_sin = math.sin(math.radians(float(cfg.get('wall_tilt_tolerance_deg', 25.0))))
    align_cos = math.cos(math.radians(float(cfg.get('alignment_tolerance_deg', 50.0))))
    min_area = max(float(cfg.get('min_area', 0.0)), 0.0)
    min_conf = max(0.0, min(1.0, float(cfg.get('min_confidence', 0.35))))
    cluster_tol = cfg.get('cluster_tolerance')
    cluster_tol = float(cluster_tol) if cluster_tol not in (None, '') else max(max_gap * 0.25, 1e-5)

    face_rows: List[Dict[str, Any]] = []
    shape_by_id = {}
    for bi, solid in enumerate(solids, 1):
        bid = f'B{bi:03d}'
        for fi, face in enumerate(solid.Faces(), 1):
            fid = f'{bid}_F{fi:04d}'
            bb = _bbox(face)
            n = _safe_normal(face)
            center = _vec(face.Center())
            witness = _witness(face)
            radial_extent = max(0.0, bb[ai + 3] - bb[ai])
            radial_normal = abs(_dot(n, radial))
            row = {
                'id': fid,
                'body_id': bid,
                'normal': n,
                'center': center,
                'witness': witness,
                'bbox': bb,
                'area': float(face.Area()),
                'radial_extent': radial_extent,
                'wall_score': max(0.0, 1.0 - radial_normal / max(wall_sin, 1e-9)),
            }
            face_rows.append(row)
            shape_by_id[fid] = face

    candidates = [
        f for f in face_rows
        if f['area'] >= min_area
        and f['radial_extent'] >= min_depth
        and abs(_dot(f['normal'], radial)) <= wall_sin
    ]

    pair_candidates: List[Dict[str, Any]] = []
    for i, f1 in enumerate(candidates):
        n1 = f1['normal']
        for f2 in candidates[i+1:]:
            if f1['body_id'] != f2['body_id']:
                continue
            n2 = f2['normal']
            opposition = -_dot(n1, n2)
            if opposition < opp_cos:
                continue
            dvec = _sub(f2['center'], f1['center'])
            du = _unit(dvec)
            alignment = max(abs(_dot(du, n1)), abs(_dot(du, n2)))
            if alignment < align_cos:
                continue

            # Require meaningful overlap in the radial/depth direction.
            b1, b2 = f1['bbox'], f2['bbox']
            overlap = _interval_overlap(b1[ai], b1[ai+3], b2[ai], b2[ai+3])
            min_span = min(f1['radial_extent'], f2['radial_extent'])
            overlap_ratio = overlap / max(min_span, 1e-12)
            if overlap_ratio < 0.35:
                continue

            # Bounding boxes further apart than max_gap can never be a sipe
            # pair; reject them before paying for an OCC distance query.
            if _bbox_gap(b1, b2) > max_gap + 1e-9:
                continue

            sh1, sh2 = shape_by_id[f1['id']], shape_by_id[f2['id']]
            try:
                g12 = float(sh2.distance(_vertex_at(f1['witness'])))
                g21 = float(sh1.distance(_vertex_at(f2['witness'])))
                gap = min(g12, g21)
                gap_avg = 0.5 * (g12 + g21)
            except Exception:
                gap = abs(_dot(dvec, n1))
                gap_avg = gap
            if gap > max_gap + 1e-9 and gap_avg > max_gap + 1e-9:
                continue
            gap_metric = min(gap, gap_avg)
            if gap_metric < 1e-9:
                # zero can occur at connected boundaries; the centre-plane separation
                # is a better proxy for an actual slit width in that case.
                gap_metric = abs(_dot(dvec, n1))
            if gap_metric > max_gap + 1e-9:
                continue

            wall_score = 0.5 * (f1['wall_score'] + f2['wall_score'])
            opp_score = max(0.0, min(1.0, (opposition - opp_cos) / max(1.0 - opp_cos, 1e-9)))
            align_score = max(0.0, min(1.0, (alignment - align_cos) / max(1.0 - align_cos, 1e-9)))
            gap_score = 1.0 - min(1.0, gap_metric / max(max_gap, 1e-9)) if max_gap > 0 else 1.0
            conf = 0.28 * wall_score + 0.28 * opp_score + 0.18 * align_score + 0.16 * overlap_ratio + 0.10 * gap_score
            if conf < min_conf:
                continue
            pair_candidates.append({
                'a': f1['id'], 'b': f2['id'],
                'gap': float(gap_metric), 'opposition': float(opposition),
                'alignment': float(alignment), 'overlap_ratio': float(overlap_ratio),
                'confidence': float(conf),
            })

    # Greedy one-to-one pairing. For a wall face with multiple nearby possible
    # opponents, choose the most confident / smallest-gap alternative first.
    pair_candidates.sort(key=lambda p: (-p['confidence'], p['gap']))
    used = set()
    pairs = []
    for p in pair_candidates:
        if p['a'] in used or p['b'] in used:
            continue
        used.add(p['a']); used.add(p['b']); pairs.append(p)

    # Cluster connected pair segments into one sipe. Adjacent CAD faces on a
    # segmented/curved wall generally have zero or tiny topological distance.
    parent = list(range(len(pairs)))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb: parent[rb] = ra

    for i in range(len(pairs)):
        for j in range(i+1, len(pairs)):
            pi, pj = pairs[i], pairs[j]
            connected = False
            for fi in (pi['a'], pi['b']):
                for fj in (pj['a'], pj['b']):
                    try:
                        if float(shape_by_id[fi].distance(shape_by_id[fj])) <= cluster_tol:
                            connected = True; break
                    except Exception:
                        pass
                if connected: break
            if connected:
                union(i, j)

    groups: Dict[int, List[Dict[str, Any]]] = {}
    for i, p in enumerate(pairs):
        groups.setdefault(find(i), []).append(p)

    row_by_id = {f['id']: f for f in face_rows}
    sipes = []
    all_wall_ids = []
    for si, grp in enumerate(sorted(groups.values(), key=lambda g: min(x['a'] for x in g)), 1):
        wall_ids = sorted({x[k] for x in grp for k in ('a','b')})
        ref = row_by_id[grp[0]['a']]['normal']
        side_a = sorted([fid for fid in wall_ids if _dot(row_by_id[fid]['normal'], ref) >= 0])
        side_b = sorted([fid for fid in wall_ids if fid not in side_a])
        gaps = [x['gap'] for x in grp]
        confs = [x['confidence'] for x in grp]
        depth = max((row_by_id[fid]['radial_extent'] for fid in wall_ids), default=0.0)
        name = f'SIPE_{si:03d}'
        sipes.append({
            'name': name,
            'side_a_ids': side_a,
            'side_b_ids': side_b,
            'wall_ids': wall_ids,
            'pair_count': len(grp),
            'gap_estimate': float(statistics.median(gaps)) if gaps else 0.0,
            'depth_estimate': float(depth),
            'confidence': float(sum(confs) / len(confs)) if confs else 0.0,
        })
        all_wall_ids.extend(wall_ids)

    return {
        'config': cfg,
        'candidate_face_ids': sorted(f['id'] for f in candidates),
        'pair_count': len(pairs),
        'sipes': sipes,
        'all_wall_ids': sorted(set(all_wall_ids)),
        'notes': [
            'Automatic sipe detection is geometry-based and should be reviewed before analysis.',
            'Detection is strongest for finite-width sipes with opposed, near-vertical walls.',
        ],
    }
