"""Volume, surface area, weight, and polar moment of inertia.

Everything here is derived from a :class:`~tyre_analysis.geometry.ComponentGeometry`
plus the rotation axis and a specific gravity. Units in / out:

* geometry lengths/areas are in mm / mm2 (drawing units are mm)
* volume is reported in cm3, weight in grams
* the polar MOI is the **mass** moment of inertia of the revolved solid about
  the rotation axis, in g.mm2, and the radius of gyration in mm
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .geometry import ComponentGeometry, axis_crossing

MM3_PER_CM3 = 1000.0  # 1 cm3 = 1000 mm3


@dataclass
class ComponentResult:
    layer: str
    area_mm2: float
    centroid_x: float
    centroid_y: float
    centroid_distance_mm: float
    volume_cm3: float
    weight_g: float
    surface_area_mm2: float
    polar_moi_g_mm2: float
    radius_of_gyration_mm: float
    sg: float
    sg_matched: bool
    axis_crosses: bool


def compute_physics(
    geom: ComponentGeometry,
    axis_x: float,
    sg: float,
    sg_matched: bool = True,
) -> ComponentResult:
    d_area = geom.area_centroid_distance(axis_x)
    d_arc = geom.boundary_centroid_distance(axis_x)

    # Pappus 1st theorem: volume of revolution.
    volume_mm3 = 2.0 * math.pi * d_area * geom.area_mm2
    volume_cm3 = volume_mm3 / MM3_PER_CM3

    # Pappus 2nd theorem: surface area of revolution (boundary-curve centroid).
    surface_area_mm2 = 2.0 * math.pi * d_arc * geom.boundary_length_mm

    weight_g = volume_cm3 * sg

    # Mass polar MOI of the revolved solid about the axis:
    #   I = 2*pi*rho * integral(r^3 dA),  rho [g/mm3] = sg [g/cm3] / 1000
    rho_g_per_mm3 = sg / MM3_PER_CM3
    polar_moi = 2.0 * math.pi * rho_g_per_mm3 * geom.r3_moment_mm5
    rog = math.sqrt(polar_moi / weight_g) if weight_g > 0 else 0.0

    crosses, _mn, _mx = axis_crossing(geom.polygon, axis_x)

    return ComponentResult(
        layer=geom.layer,
        area_mm2=geom.area_mm2,
        centroid_x=geom.centroid_x,
        centroid_y=geom.centroid_y,
        centroid_distance_mm=d_area,
        volume_cm3=volume_cm3,
        weight_g=weight_g,
        surface_area_mm2=surface_area_mm2,
        polar_moi_g_mm2=polar_moi,
        radius_of_gyration_mm=rog,
        sg=sg,
        sg_matched=sg_matched,
        axis_crosses=crosses,
    )
