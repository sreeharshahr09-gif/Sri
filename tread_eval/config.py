"""One editable file holding every input, so a run is reproducible and readable.

The command line is fine for one-off flags, but a tyre has a lot of inputs and
most of them are properties of the tyre rather than of the run.  A config file
keeps them together, under names you can read six months later, and it travels
with the design::

    python build_report.py --config my_tyre.json

Anything given on the command line still wins, so a config file is a set of
defaults you can override for a single run.  ``--save-config out.json`` writes
back the *effective* configuration -- config plus overrides -- which is the easy
way to produce a starting file:

    python build_report.py --dxf mine.dxf --nsd 8 --save-config my_tyre.json

Unknown keys are an error, not a silent no-op: a typo in a config file that is
quietly ignored is worse than one that stops the run.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from .contact_patch import CPParams
from .cp_shapes import ShapeSpec
from .dxf import BlockDefaults
from .schema import CrownProfile
from .stiffness import StiffnessParams


@dataclass
class PatternConfig:
    """Where the tread geometry comes from, and the depths a drawing cannot carry."""

    dxf: str | None = None  # path to a tread-plan DXF
    pattern_json: str | None = None  # or a previously saved Pattern
    synthetic: bool = False  # or generate a placeholder pattern
    pitch_mode: str = "random"  # synthetic only
    seed: int | None = None

    nsd_mm: float = 8.0  # non-skid depth == block height
    draft_deg: float = 3.0
    lateral_sipes: int = 0
    sipe_depth_fraction: float = 0.6
    nsd_by_zone: dict[str, float] = field(default_factory=dict)
    draft_by_zone: dict[str, float] = field(default_factory=dict)
    sipes_by_zone: dict[str, int] = field(default_factory=dict)

    circumference_mm: float | None = None  # override the drawing extents
    tread_width_mm: float | None = None
    n_pitches: int | None = None  # override the inferred pitch count
    dxf_layers: list[str] | None = None  # restrict import to these layers
    curvature_correction: bool = False

    def block_defaults(self) -> BlockDefaults:
        return BlockDefaults(
            height=self.nsd_mm,
            draft_angle=self.draft_deg,
            n_lateral_sipes=self.lateral_sipes,
            sipe_depth_fraction=self.sipe_depth_fraction,
            height_by_zone=dict(self.nsd_by_zone),
            draft_by_zone=dict(self.draft_by_zone),
            sipes_by_zone=dict(self.sipes_by_zone),
        )


@dataclass
class TyreConfig:
    """Physical tyre properties outside the tread plan."""

    wheel_radius_mm: float = 320.0
    section_width_mm: float = 130.0
    aspect_ratio: float = 80.0
    rim_diameter_in: float = 17.0
    inflation_kpa: float = 250.0
    tyre_type: str = "2w"
    crown_r_center_mm: float = 125.0
    crown_r_shoulder_mm: float = 55.0
    crown_break_fraction: float = 0.50
    crown_section_csv: str | None = None  # measured cross-section, y_projected,z

    def crown(self, tread_width: float) -> CrownProfile:
        if self.crown_section_csv:
            import csv

            ys, zs = [], []
            with open(self.crown_section_csv, newline="") as fh:
                for row in csv.reader(fh):
                    try:
                        ys.append(float(row[0]))
                        zs.append(float(row[1]))
                    except (ValueError, IndexError):
                        continue
            if len(ys) < 5:
                raise ValueError(f"{self.crown_section_csv}: need at least 5 cross-section points")
            return CrownProfile.from_cross_section(ys, zs)
        return CrownProfile.dual_radius(
            tread_width,
            r_center=self.crown_r_center_mm,
            r_shoulder=self.crown_r_shoulder_mm,
            break_fraction=self.crown_break_fraction,
        )


@dataclass
class CompoundConfig:
    shore_a: float = 60.0
    poisson: float = 0.49
    boundary: str = "parallel"  # "parallel" | "free"
    youngs_mpa: float | None = None  # bypass the Shore table
    gent_k: float | None = None

    def params(self) -> StiffnessParams:
        return StiffnessParams(
            shore_a=self.shore_a,
            poisson=self.poisson,
            mode=self.boundary,
            e_override=self.youngs_mpa,
            k_override=self.gent_k,
        )


@dataclass
class LoadConfig:
    vertical_N: float = 1500.0
    rises_with_lean: bool = True
    slip_angle_deg: float = 0.0


@dataclass
class ContactPatchConfig:
    """How the contact patch is obtained, in the order the tool will try.

    ``shapes`` and ``import_`` may both be given -- measured footprints win over
    stated shapes, and stated shapes win over the generated model.
    """

    model: str = "winkler"  # fallback generator: "winkler" | "rhyne"
    shapes: list[dict] = field(default_factory=list)
    import_: dict = field(default_factory=dict)  # {"path":..., "gamma_deg":..., ...}
    allow_transfer: bool = True

    def shape_specs(self) -> list[ShapeSpec]:
        from .cp_shapes import specs_from_config

        return specs_from_config(self.shapes)


@dataclass
class AnalysisConfig:
    lean_angles: list[float] = field(default_factory=lambda: [0.0, 10.0, 20.0, 30.0, 40.0])
    resolution_mm: float = 0.35
    discrete_samples: int = 360


@dataclass
class OutputConfig:
    path: str = "out/tread_report.html"
    theta_points: int = 720
    plotly: str = "embed"
    notes: str = ""


@dataclass
class Config:
    name: str = ""
    pattern: PatternConfig = field(default_factory=PatternConfig)
    tyre: TyreConfig = field(default_factory=TyreConfig)
    compound: CompoundConfig = field(default_factory=CompoundConfig)
    load: LoadConfig = field(default_factory=LoadConfig)
    contact_patch: ContactPatchConfig = field(default_factory=ContactPatchConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    # ------------------------------------------------------------------
    def cp_params(self) -> CPParams:
        return CPParams(
            vertical_load=self.load.vertical_N,
            wheel_radius=self.tyre.wheel_radius_mm,
            load_rises_with_lean=self.load.rises_with_lean,
            lateral_slip_angle=self.load.slip_angle_deg,
            inflation_kpa=self.tyre.inflation_kpa,
            section_width_mm=self.tyre.section_width_mm,
            aspect_ratio=self.tyre.aspect_ratio,
            rim_diameter_in=self.tyre.rim_diameter_in,
            tyre_type=self.tyre.tyre_type,
        )

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        # `import` is a Python keyword, so the dataclass field is `import_`.
        d["contact_patch"]["import"] = d["contact_patch"].pop("import_")
        return d

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)
            fh.write("\n")

    @classmethod
    def from_file(cls, path: str) -> "Config":
        """Read a config from JSON.

        Named ``from_file`` rather than ``load`` because ``load`` is already a
        *field* of this dataclass (the load case), and a method of the same name
        silently becomes that field's default value.
        """
        if not os.path.exists(path):
            raise ValueError(
                f"config file not found: {path}. Write a starting point with "
                f"--write-example-config {path}"
            )
        if os.path.isdir(path):
            raise ValueError(f"config path is a directory, not a file: {path}")
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        cfg = cls.from_dict(raw)
        cfg._resolve_paths(os.path.dirname(os.path.abspath(path)))
        return cfg

    @classmethod
    def from_dict(cls, raw: dict) -> "Config":
        raw = {k: v for k, v in raw.items() if not k.startswith("_")}  # allow _comment keys
        sections = {
            "pattern": PatternConfig,
            "tyre": TyreConfig,
            "compound": CompoundConfig,
            "load": LoadConfig,
            "contact_patch": ContactPatchConfig,
            "analysis": AnalysisConfig,
            "output": OutputConfig,
        }
        unknown_top = set(raw) - set(sections) - {"name"}
        if unknown_top:
            raise ValueError(
                f"unknown config section(s) {sorted(unknown_top)}; expected any of "
                f"{sorted(list(sections) + ['name'])}"
            )

        kwargs: dict[str, Any] = {"name": raw.get("name", "")}
        for key, klass in sections.items():
            data = dict(raw.get(key, {}) or {})
            data = {k: v for k, v in data.items() if not k.startswith("_")}
            if key == "contact_patch" and "import" in data:
                data["import_"] = data.pop("import")
            known = set(klass.__dataclass_fields__)
            unknown = set(data) - known
            if unknown:
                raise ValueError(
                    f"unknown setting(s) {sorted(unknown)} in config section '{key}'; "
                    f"expected any of {sorted(known)}"
                )
            kwargs[key] = klass(**data)
        return cls(**kwargs)

    def _resolve_paths(self, base: str) -> None:
        """Make file paths relative to the config file, not the shell's cwd."""

        def fix(p: str | None) -> str | None:
            if not p or os.path.isabs(p):
                return p
            return os.path.normpath(os.path.join(base, p))

        self.pattern.dxf = fix(self.pattern.dxf)
        self.pattern.pattern_json = fix(self.pattern.pattern_json)
        self.tyre.crown_section_csv = fix(self.tyre.crown_section_csv)
        if self.contact_patch.import_.get("path"):
            self.contact_patch.import_["path"] = fix(self.contact_patch.import_["path"])

    def describe_sources(self) -> list[str]:
        """Human-readable summary of what this configuration will actually do."""
        out = []
        p = self.pattern
        if p.dxf:
            out.append(f"pattern: DXF {os.path.basename(p.dxf)}")
        elif p.pattern_json:
            out.append(f"pattern: saved Pattern {os.path.basename(p.pattern_json)}")
        else:
            out.append(f"pattern: synthetic ({p.pitch_mode})")
        out.append(f"depths:  NSD {p.nsd_mm} mm, draft {p.draft_deg} deg, {p.lateral_sipes} sipes/block (assumed)")
        cp = self.contact_patch
        if cp.import_.get("path"):
            out.append(f"patch:   imported {os.path.basename(cp.import_['path'])}")
        if cp.shapes:
            out.append(f"patch:   {len(cp.shapes)} user-specified shape(s)")
        out.append(f"patch:   {cp.model} model where nothing else is available")
        out.append(f"crown:   {'measured section' if self.tyre.crown_section_csv else 'dual-radius guess'}")
        return out


EXAMPLE = {
    "_comment": [
        "Every input for one tyre. Run with:  python build_report.py --config this_file.json",
        "Paths are relative to this file. Command-line flags override anything here.",
        "Delete any key to fall back to its default.",
    ],
    "name": "130/80R17 Tramplr XR",
    "pattern": {
        "_comment": "Tread geometry. A 2D DXF has no depth, so NSD/draft/sipes are stated here.",
        "dxf": "data/130_80R17_Tramplr_XR_tread_plan.dxf",
        "nsd_mm": 8.5,
        "draft_deg": 3.0,
        "lateral_sipes": 1,
        "nsd_by_zone": {"shoulder": 7.5, "intermediate": 8.2, "center": 8.8},
        "sipes_by_zone": {"center": 2, "intermediate": 1, "shoulder": 0},
        "n_pitches": None,
        "curvature_correction": False,
    },
    "tyre": {
        "_comment": "Crown radii set where the patch sits at each lean angle. Measure if you can.",
        "wheel_radius_mm": 320.0,
        "section_width_mm": 130.0,
        "aspect_ratio": 80.0,
        "rim_diameter_in": 17.0,
        "inflation_kpa": 250.0,
        "crown_r_center_mm": 125.0,
        "crown_r_shoulder_mm": 55.0,
        "crown_section_csv": None,
    },
    "compound": {"shore_a": 60.0, "poisson": 0.49, "boundary": "parallel"},
    "load": {"vertical_N": 1500.0, "rises_with_lean": True},
    "contact_patch": {
        "_comment": [
            "Three ways to define the patch, tried in this order:",
            "  import  - a measured footprint (.csv/.dxf/.json outline, or a pressure map)",
            "  shapes  - standard shapes with your own dimensions, one per lean angle",
            "  model   - generated, where neither of the above covers a lean angle",
            "Shapes: rectangle, rounded, stadium, ellipse, superellipse, trapezoid, diamond",
        ],
        "model": "winkler",
        "import": {},
        "shapes": [
            {"gamma_deg": 0, "shape": "rounded", "length": 92, "width": 50, "corner_radius": 12},
            {"gamma_deg": 40, "shape": "trapezoid", "length": 105, "width": 34, "taper": 0.25},
        ],
        "allow_transfer": True,
    },
    "analysis": {"lean_angles": [0, 10, 20, 30, 40], "resolution_mm": 0.35},
    "output": {"path": "out/tramplr.html", "theta_points": 720},
}


def write_example(path: str) -> None:
    """Write the example config, with the sample DXF path made relative to it.

    Paths inside a config are resolved against the config's own directory, so a
    hard-coded "data/..." breaks as soon as the example is written anywhere but
    the repo root. Compute it instead.
    """
    import copy

    example = copy.deepcopy(EXAMPLE)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sample = os.path.join(repo, "data", "130_80R17_Tramplr_XR_tread_plan.dxf")
    out_dir = os.path.dirname(os.path.abspath(path)) or os.getcwd()
    if os.path.exists(sample):
        example["pattern"]["dxf"] = os.path.relpath(sample, out_dir).replace(os.sep, "/")
    else:
        example["pattern"]["dxf"] = "PATH/TO/your_tread_plan.dxf"

    os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(example, fh, indent=2)
        fh.write("\n")
