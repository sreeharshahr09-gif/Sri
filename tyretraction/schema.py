"""Column layout, feature lists and physical plausibility limits.

The master-file layout follows the Apollo DTT master used so far
(36 columns, read by position). If your file differs, pass a JSON column map
(see README) instead of editing this file.
"""

# Master columns, in sheet order.
MASTER_COLS = [
    "SlNo", "TyreSize", "Brand", "Pattern", "Group", "TestSurface", "SurfaceType",
    "Load_N", "IP_kPa", "Speed_kph", "RimWidth_in", "Tsurface_C", "Tair_C", "TrialNo",
    "mu_peak", "SR_at_mupeak", "mu_lock", "Tg_C", "Ep_0C", "Ep_20C", "Ep_60C",
    "Epp_0C", "Epp_20C", "Epp_60C", "M300_MPa", "TanD_0C", "TanD_20C", "TanD_60C",
    "ContactWidth_mm", "ContactLen_mm", "TotalContactArea_cm2", "NetContactArea",
    "ContactRatio", "VoidFillRatio", "TestIP_kPa", "TestLoad_kg",
]
TEXT_COLS = ["TyreSize", "Brand", "Pattern", "Group", "TestSurface", "SurfaceType"]
ID_COL = "SlNo"

# Landmarks stored in the master (rig output).
MASTER_TARGETS = ["mu_peak", "SR_at_mupeak", "mu_lock"]

# Targets the models predict. Curve-derived versions carry the same names.
# mu_slide = µ at SLIDE_SLIP (end of the Magic-Formula range, before lock-up).
TARGETS = ["mu_peak", "SR_at_mupeak", "mu_slide", "mu_lock", "init_slope"]
TARGET_LABELS = {
    "mu_peak": "Peak µ",
    "SR_at_mupeak": "Slip at peak µ",
    "mu_lock": "µ lock",
    "mu_slide": "µ slide (at 90% slip)",
    "init_slope": "Initial slope (dµ/dslip)",
    "mu_at_05": "µ at 5% slip",
    "mu_at_10": "µ at 10% slip",
    "mu_at_20": "µ at 20% slip",
}

# What defines a "configuration": one tyre, one surface, one operating point.
CONFIG_KEYS = ["TyreSize", "Pattern", "Group", "SurfaceType", "Load_N", "IP_kPa", "Speed_kph"]
# What defines a physical tyre (construction + compound + size).
TYRE_KEYS = ["TyreSize", "Pattern", "Group"]
# Compound family. "Group" in the master is treated as the compound group.
COMPOUND_KEY = "Group"
SIZE_KEY = "TyreSize"

OPERATING = ["Load_N", "IP_kPa", "Speed_kph", "Tsurface_C", "Tair_C"]
DMA = ["Tg_C", "Ep_0C", "Ep_20C", "Ep_60C", "Epp_0C", "Epp_20C", "Epp_60C",
       "M300_MPa", "TanD_0C", "TanD_20C", "TanD_60C"]
FOOTPRINT = ["ContactWidth_mm", "ContactLen_mm", "TotalContactArea_cm2",
             "NetContactArea", "ContactRatio", "VoidFillRatio"]
SIZE_FEATS = ["width_mm", "aspect", "rim_in", "RimWidth_in"]
DERIVED = ["contact_press_Nmm2", "load_per_mm_width", "contact_aspect"]

FEATURE_SETS = {
    # everything we know about the tyre and the test
    "all": ["IsDry"] + OPERATING + SIZE_FEATS + DMA + FOOTPRINT + DERIVED,
    # no compound / footprint information: size + operating point only
    "conditions": ["IsDry"] + OPERATING + SIZE_FEATS,
    # compact physics-motivated subset
    "physics": ["IsDry", "Load_N", "IP_kPa", "Speed_kph", "Tsurface_C", "Tg_C",
                "TanD_0C", "TanD_60C", "Ep_0C", "Ep_60C", "contact_press_Nmm2",
                "width_mm", "ContactRatio"],
}

# Plausibility limits used by the data-quality checks (inclusive).
LIMITS = {
    "mu_peak": (0.2, 1.6),
    "SR_at_mupeak": (0.01, 0.6),
    "mu_lock": (0.1, 1.4),
    "Load_N": (1000, 15000),
    "IP_kPa": (100, 400),
    "Speed_kph": (5, 200),
    "Tsurface_C": (-10, 70),
    "Tair_C": (-10, 55),
}

# Curve analysis settings.
SLIP_GRID_STEP = 0.005          # common slip grid for resampled curves
LOCK_SLIP_MIN = 0.95            # µ lock = mean µ for slip >= this
SLIDE_SLIP = 0.90               # Magic Formula is fitted on slip <= this; real
                                # curves drop sharply beyond it as the wheel locks
INIT_SLOPE_MAX_SLIP = 0.03      # initial slope fitted on slip <= this
