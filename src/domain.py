"""
Domain knowledge for hybrid zero-degree (cap-ply / overlay) cords in PCR tires.

This module is deliberately the most important file in the project. The quality
of the patent screening and the technology extraction depends far more on this
domain vocabulary than on the choice of embedding model. Everything here is
editable by a domain expert without touching the pipeline code.

Three things are defined:
  1. SEGMENT_LEXICON   -> decide PCR vs TBR/OTR/aircraft (recall + precision)
  2. ZERO_DEGREE_LEXICON / HYBRID_LEXICON -> topical relevance gate
  3. CATEGORY_FRAMEWORK -> the axes the analysis must answer (the deliverable)
  4. CPC_CLASSES       -> classification codes for query design & screening
"""

from __future__ import annotations

# ----------------------------------------------------------------------------
# 1. Tire-segment disambiguation
# ----------------------------------------------------------------------------
# Belt/overlay patents are dominated by truck (TBR), off-road (OTR) and aircraft
# tires. Those pollute a PCR study, so we score every document for segment.
SEGMENT_LEXICON = {
    "PCR": [
        "passenger car", "passenger vehicle", "passenger tire", "passenger tyre",
        "pcr", "passenger car radial", "high performance tire", "uhp",
        "ultra high performance", "summer tire", "sport tire", "sedan",
        # speed ratings are an extremely strong PCR/UHP signal
        "speed rating", "speed symbol", "v-rated", "w-rated", "y-rated",
        "h-rated", "zr", "high speed durability",
    ],
    "TBR": [
        "truck", "bus", "commercial vehicle", "truck and bus", "tbr",
        "heavy duty", "load index", "regroovable", "steer axle", "drive axle",
    ],
    "OTR": [
        "off-the-road", "off the road", "otr", "earthmover", "mining tire",
        "agricultural", "construction equipment", "loader",
    ],
    "AIRCRAFT": ["aircraft", "aeroplane", "airplane", "aviation tire", "landing"],
}

# ----------------------------------------------------------------------------
# 2. Topical relevance: is this actually about the zero-degree belt + hybrid cord?
# ----------------------------------------------------------------------------
ZERO_DEGREE_LEXICON = [
    "zero degree", "zero-degree", "0 degree", "0-degree", "0°",
    "cap ply", "capply", "cap-ply", "overlay", "overlay ply", "overlay layer",
    "jointless band", "jointless cap", "jlb", "spirally wound", "spiral wound",
    "spirally-wound", "circumferential belt", "circumferentially wound",
    "reinforcing band", "belt reinforcing layer", "band ply", "edge band",
    "full width overlay", "nylon overlay", "restricting layer",
]

HYBRID_LEXICON = [
    "hybrid cord", "hybrid tire cord", "hybrid yarn", "composite cord",
    "aramid nylon", "aramid-nylon", "nylon aramid", "n66 aramid",
    "merged cord", "co-twisted", "cabled cord", "two-component cord",
    "hybrid construction", "different modulus", "dual modulus",
]

# ----------------------------------------------------------------------------
# 3. CATEGORY FRAMEWORK — the questions the report must answer.
# Each category has trigger phrases used for weak-supervision tagging of
# passages, so the final report can be organized by what the boss asked for.
# ----------------------------------------------------------------------------
CATEGORY_FRAMEWORK = {
    "technologies": {
        "label": "Technologies / Materials & Construction",
        "phrases": [
            "aramid", "para-aramid", "nylon 6", "nylon 66", "nylon 6.6",
            "polyester", "pet", "pen", "rayon", "twist", "twist multiplier",
            "denier", "dtex", "ends per", "epdm", "rfl", "resorcinol",
            "adhesive dip", "epoxy", "two-bath", "two bath", "single layer",
            "double layer", "dual layer", "filament", "cabled", "ply twist",
        ],
    },
    "advantages": {
        "label": "Advantages / Performance Benefits",
        "phrases": [
            "high speed durability", "high-speed durability", "centrifugal",
            "growth", "diameter growth", "restrain", "restrict expansion",
            "uniformity", "rfv", "radial force variation", "flat spot",
            "flat-spotting", "handling", "steering response", "cornering",
            "weight reduction", "lightweight", "rolling resistance",
            "fuel economy", "ride comfort", "noise", "durability improvement",
        ],
    },
    "challenges": {
        "label": "Challenges / Disadvantages",
        "phrases": [
            "adhesion", "poor adhesion", "delamination", "separation",
            "compression fatigue", "fatigue resistance", "brittle", "stiffness",
            "cost", "expensive", "difficult to process", "shrinkage mismatch",
            "thermal shrinkage", "dimensional stability", "tension control",
            "creep", "modulus mismatch", "edge looseness", "cord breakage",
        ],
    },
    "design_guidelines": {
        "label": "Design Guidelines / Parameters",
        "phrases": [
            "ends per dm", "epdm", "cord density", "winding tension",
            "overlay tension", "modulus of", "elongation at", "shrinkage force",
            "dry heat shrinkage", "intermediate elongation", "lase",
            "load at specified elongation", "gauge", "ply count", "number of plies",
            "winding pitch", "preferably between", "in the range of",
        ],
    },
    "process": {
        "label": "Process / Manufacturing Challenges",
        "phrases": [
            "dipping", "dip process", "heat setting", "normalizing", "stretching",
            "winding", "spiral winding", "strip winding", "calendering",
            "extrusion", "tension", "lay-down", "splice", "spool", "creel",
            "process window", "manufacturing", "vulcanization", "curing",
        ],
    },
    "tradeoffs": {
        "label": "Trade-offs",
        "phrases": [
            "trade-off", "tradeoff", "balance between", "compromise",
            "at the expense of", "while maintaining", "without sacrificing",
            "versus", "however", "on the other hand", "but", "although",
            "ride versus handling", "cost versus performance",
        ],
    },
}

# ----------------------------------------------------------------------------
# 4. CPC / classification codes — for designing exports and as a screening boost.
# ----------------------------------------------------------------------------
CPC_CLASSES = {
    # Tire belt / breaker / overlay construction
    "B60C9/18": "Structure or arrangement of belts/breakers",
    "B60C9/20": "Belts/breakers built-up from rubberised plies",
    "B60C9/22": "Breakers/belts characterised by the disposition of cords",
    "B60C9/28": "Belt characterised by reinforcing layer wound around tread",
    "B60C2009/2009": "Cap ply / overlay subgroups",
    "B60C2009/2074": "Cap ply / band construction details",
    "B60C2009/0014": "Hybrid / composite cord constructions",
    # Cord & yarn material / twist
    "D02G3/00": "Yarns/threads consisting of fibres of differing materials",
    "D07B": "Ropes / cables (cord construction)",
}


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def all_relevance_terms() -> list[str]:
    """Every term used to gate topical relevance."""
    return ZERO_DEGREE_LEXICON + HYBRID_LEXICON


def category_names() -> list[str]:
    return list(CATEGORY_FRAMEWORK.keys())
