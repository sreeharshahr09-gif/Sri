"""
Filterable patent extract for the hybrid-cord wear analysis.

One row per patent in ANY hybrid cohort (General hybrid / Nylon-Aramid /
PET-Nylon), with boolean flag columns so the sheet can be filtered in Excel by:
  - which hybrid cord  (is_nylon_aramid / is_pet_nylon / is_general_hybrid)
  - the tech / approach (7 solution-lever flags, same taxonomy as the deck)
  - whether it makes a wear claim
plus patent number, assignee, year, legal status, CPC, and the full text
(title / abstract / claims / AI problem-method-advantages).

Uses the audited cohort definitions in cohorts.py (single source of truth).
Output: reports/hybrid_cord_patent_extract.csv
Run   : python scripts/build_patent_extract.py
"""
import re
import pandas as pd
import cohorts as C

df = C.load(); m = C.masks(df); t = df["_txt"]
GEN, NA, PN, WEAR = m["general"], m["nylon_aramid"], m["pet_nylon"], m["wear"]

# same solution taxonomy / labels as the deck charts
SOL = {
    "tech_center_vs_edge_layout": r"(center|centre|shoulder|edge|side section|side portion).{0,60}(density|spacing|epdm|ends per)",
    "tech_core_sheath_cord": r"core.{0,15}(sheath|wrap|cover)|sheath|wound around.{0,15}core|wrap yarn",
    "tech_dual_modulus_cord": r"(high|low|first|second|different).{0,20}modulus|graded modulus|elastic modulus",
    "tech_twist_geometry": r"twist|denier|dtex|filament count|cord diameter|cord thickness",
    "tech_adhesion_dip": r"adhesion|adhesive|\brfl\b|dip|epoxy|tackif",
    "tech_layered_edge_cover": r"two layer|double layer|dual layer|edge (band|cover|cap|layer)",
    "tech_position_under_grooves": r"(below|beneath|under).{0,25}(groove|land)|land portion",
}


def norm(s):
    s = str(s).split(";")[0]; s = re.sub(r"\([A-Z]{2}\)", "", s)
    return re.sub(r"\s+", " ", s).strip(" .,").upper()


# union of the three hybrid cohorts
keep = (GEN | NA | PN)
out = df[keep.values].copy()


def label(i):
    tags = []
    if NA[i]: tags.append("Nylon-Aramid")
    if PN[i]: tags.append("PET-Nylon")
    if not tags and GEN[i]: tags.append("General hybrid (other)")
    return "; ".join(tags)


out["patent_number"] = out["doc_id"]
out["hybrid_type"] = [label(i) for i in out.index]
out["is_nylon_aramid"] = NA[keep.values].values
out["is_pet_nylon"] = PN[keep.values].values
out["is_general_hybrid"] = GEN[keep.values].values
out["mentions_wear"] = WEAR[keep.values].values
for col, pat in SOL.items():
    out[col] = t[keep.values].str.contains(pat, regex=True).values
out["assignee_clean"] = out["assignee"].map(norm)
out["year"] = pd.to_numeric(out["year"], errors="coerce").astype("Int64")

# short wear snippet to orient the reader
belt = re.compile(r"cap ply|overlay|spiral|circumferential|belt|band|cord|hybrid", re.I)
wre = re.compile(C.WEAR_RE, re.I)
def snip(i):
    for f in ["ai_advantages", "ai_method", "abstract", "claims", "ai_problem"]:
        for s in re.split(r"(?<=[.;])\s+", str(df.loc[i, f])):
            if wre.search(s) and belt.search(s) and 45 < len(s) < 320:
                return s.strip()
    return ""
out["wear_snippet"] = [snip(i) for i in out.index]

COLS = [
    "patent_number", "hybrid_type",
    "is_nylon_aramid", "is_pet_nylon", "is_general_hybrid", "mentions_wear",
    *SOL.keys(),
    "assignee_clean", "year", "legal_status", "cpc",
    "wear_snippet", "title", "abstract", "claims",
    "ai_problem", "ai_method", "ai_advantages",
]
out = out[COLS].sort_values(
    ["is_pet_nylon", "is_nylon_aramid", "mentions_wear", "year"],
    ascending=[False, False, False, False])

path = "reports/hybrid_cord_patent_extract.csv"
out.to_csv(path, index=False)
print(f"saved {path} | rows={len(out)} "
      f"(Nylon-Aramid={int(NA.sum())}, PET-Nylon={int(PN.sum())}, General={int(GEN.sum())})")
print("columns:", ", ".join(COLS))
