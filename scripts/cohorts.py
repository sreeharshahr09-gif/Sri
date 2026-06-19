"""
Canonical, audited cohort definitions for the hybrid-cord wear analysis.
ALL analysis scripts and the deck import from here so numbers never drift.

Corrections applied after the pre-publication audit:
  * Motorcycle tires (primary, by title) are dropped — they are not PCR.
  * "Hybrid cord" cohorts require an EXPLICIT hybrid-cord construction, not a
    mere co-occurrence of two material names (which mostly catches materials
    lists, inflating counts ~3x).
"""
from __future__ import annotations
import re
import pandas as pd

TEXT_FIELDS = ["ai_advantages", "ai_method", "abstract", "claims", "ai_problem",
               "description", "title"]

WEAR_RE = (r"mileage|tread ?wear|wear resistanc|wear life|wear performance|"
           r"tread life|tire life|abrasion|uneven wear|irregular wear|even wear|"
           r"uniform wear|anti-?wear|wear propert")
MOTO_RE = r"motorcycle|motorbike|two-wheel|two wheel|scooter"

# explicit hybrid-cord construction signals
_HYBRID_WORD = (r"hybrid cord|hybrid tire cord|hybrid yarn|composite cord|"
                r"co-?twisted|two-component cord|dual modulus|different modulus|"
                r"merged cord")
# two materials joined as ONE cord (slash / dash / "and") — a real pairing
_PAIR = lambda a, b: rf"{a}\s*[/&+\- ]\s*{b}|{b}\s*[/&+\- ]\s*{a}"


def load(path: str = "data/interim/screened.csv", drop_motorcycle: bool = True) -> pd.DataFrame:
    df = pd.read_csv(path).fillna("")
    df["_txt"] = df[TEXT_FIELDS].agg(" ".join, axis=1).str.lower()
    df["is_motorcycle"] = df["title"].str.lower().str.contains(MOTO_RE, regex=True)
    if drop_motorcycle:
        df = df[~df["is_motorcycle"]].reset_index(drop=True)
    return df


def masks(df: pd.DataFrame) -> dict:
    t = df["_txt"]
    def has(p): return t.str.contains(p, regex=True)
    def near(a, b, n=40): return t.str.contains(rf"{a}.{{0,{n}}}{b}|{b}.{{0,{n}}}{a}", regex=True)

    hybrid_word = has(_HYBRID_WORD)
    has_aramid = has(r"aramid|kevlar|twaron|technora")
    has_nylon = has(r"nylon|polyamide|\bpa ?6")
    has_pet = has(r"polyester|\bpet\b|polyethylene terephthalate")

    # explicit material pairings written as a single cord
    pair_ar_ny = has(_PAIR("aramid", "nylon")) | has(_PAIR("aramid", "polyamide"))
    pair_pet_ny = (has(_PAIR("polyester", "nylon")) | has(_PAIR("pet", "nylon"))
                   | has(_PAIR("polyester", "polyamide")) | has(_PAIR("pet", "polyamide")))
    core_wrap = has(r"(nylon|polyamide).{0,30}(core|inner).{0,60}aramid|"
                    r"aramid.{0,40}(wrap|sheath|cover|wound).{0,40}(nylon|polyamide)")
    # PET/nylon core-sheath construction (one cord, two materials)
    pet_core_wrap = has(r"(nylon|polyamide).{0,30}(core|inner).{0,60}(polyester|pet)|"
                        r"(polyester|pet).{0,40}(wrap|sheath|cover|wound).{0,40}(nylon|polyamide)")
    # PET/nylon ZONED bandage: a fibre actually ASSIGNED to a zone (centre/shoulder
    # /side), with BOTH a PET zone AND a polyamide zone present — this is how
    # PET-nylon "hybrids" really appear (different cord per zone), as opposed to one
    # co-twisted filament. Requiring BOTH materials be zone-assigned rejects generic
    # carcass materials lists ("cords made of polyester, nylon, rayon, ...") that a
    # looser rule over-counted ~2x (cf. the Nylon-Aramid audit).
    _ZONE = r"(center|centre|middle|mid[ -]?section|shoulder|side section|side portion|side region)"
    _PETM = r"(polyester|\bpet\b|\bpen\b|polyethylene terephthalate)"
    _PAM = r"(nylon|polyamide|\bpa ?6)"
    zone_pet = has(rf"{_ZONE}.{{0,45}}{_PETM}|{_PETM}.{{0,45}}{_ZONE}")
    zone_pa = has(rf"{_ZONE}.{{0,45}}{_PAM}|{_PAM}.{{0,45}}{_ZONE}")
    pet_zoned = zone_pet & zone_pa
    # guard: drop patents whose only PET+nylon evidence is a generic carcass /
    # organic-fibre options list (not an actual hybrid construction).
    carcass_list = has(r"(carcass|organic fiber cord|organic fibre cord)"
                       r".{0,40}(such as|e\.?g|selected from|chosen from|include|including|made of)")

    # GENERAL hybrid cord: explicit hybrid wording, OR an explicit two-material
    # cord pairing, OR a core/sheath construction.
    general = hybrid_word | pair_ar_ny | pair_pet_ny | core_wrap

    # NYLON-ARAMID (strict): explicit aramid+nylon hybrid-cord construction.
    nylon_aramid = (pair_ar_ny | core_wrap
                    | (hybrid_word & has_aramid & has_nylon & near("aramid", "nylon", 80)))

    # PET-NYLON (strict): explicit polyester/polyamide hybrid construction —
    # an explicit pairing, a PET/nylon core-sheath, or a zoned PET-vs-PA bandage.
    # NOT a loose materials-list co-occurrence (polyester+nylon are commodity
    # fibres that inflate counts in lists; cf. the Nylon-Aramid audit).
    pet_nylon = (pair_pet_ny | pet_core_wrap | pet_zoned) & ~carcass_list

    wear = has(WEAR_RE)
    return dict(general=general, nylon_aramid=nylon_aramid, pet_nylon=pet_nylon, wear=wear)


if __name__ == "__main__":
    raw = pd.read_csv("data/interim/screened.csv").fillna("")
    df = load()
    m = masks(df)
    print(f"raw screened           : {len(raw)}")
    print(f"after motorcycle drop  : {len(df)}  (-{len(raw)-len(df)} primary motorcycle)")
    print(f"General hybrid (strict): {int(m['general'].sum())}  | wear {int((m['general']&m['wear']).sum())}")
    print(f"Nylon-Aramid  (strict) : {int(m['nylon_aramid'].sum())}  | wear {int((m['nylon_aramid']&m['wear']).sum())}")
    print(f"PET-Nylon     (strict) : {int(m['pet_nylon'].sum())}  | wear {int((m['pet_nylon']&m['wear']).sum())}"
          f"  | overlap NA {int((m['pet_nylon']&m['nylon_aramid']).sum())}")
