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

    # explicit material pairings written as a single cord
    pair_ar_ny = has(_PAIR("aramid", "nylon")) | has(_PAIR("aramid", "polyamide"))
    pair_pet_ny = has(_PAIR("polyester", "nylon")) | has(_PAIR("pet", "nylon"))
    core_wrap = has(r"(nylon|polyamide).{0,30}(core|inner).{0,60}aramid|"
                    r"aramid.{0,40}(wrap|sheath|cover|wound).{0,40}(nylon|polyamide)")

    # GENERAL hybrid cord: explicit hybrid wording, OR an explicit two-material
    # cord pairing, OR a core/sheath construction.
    general = hybrid_word | pair_ar_ny | pair_pet_ny | core_wrap

    # NYLON-ARAMID (strict): explicit aramid+nylon hybrid-cord construction.
    nylon_aramid = (pair_ar_ny | core_wrap
                    | (hybrid_word & has_aramid & has_nylon & near("aramid", "nylon", 80)))

    wear = has(WEAR_RE)
    return dict(general=general, nylon_aramid=nylon_aramid, wear=wear)


if __name__ == "__main__":
    raw = pd.read_csv("data/interim/screened.csv").fillna("")
    df = load()
    m = masks(df)
    print(f"raw screened           : {len(raw)}")
    print(f"after motorcycle drop  : {len(df)}  (-{len(raw)-len(df)} primary motorcycle)")
    print(f"General hybrid (strict): {int(m['general'].sum())}  | wear {int((m['general']&m['wear']).sum())}")
    print(f"Nylon-Aramid  (strict) : {int(m['nylon_aramid'].sum())}  | wear {int((m['nylon_aramid']&m['wear']).sum())}")
