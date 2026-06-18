"""
Deep-dive: how Nylon-Aramid wear/mileage patents solve the problem.
Tags each patent with engineering solution approaches; charts prevalence;
exports a patent->approach mapping.

Output: output/nylon_aramid_solutions.png, output/nylon_aramid_solutions.csv
Run   : python scripts/nylon_aramid_solutions.py
"""
import re
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams.update({"font.size": 12, "axes.grid": True, "grid.alpha": 0.25,
                     "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 150})

df = pd.read_csv("data/interim/screened.csv").fillna("")
fields = ["ai_advantages", "ai_method", "abstract", "claims", "ai_problem", "description", "title"]
txt = df[fields].agg(" ".join, axis=1).str.lower()
def near(a, b): return txt.str.contains(rf"{a}.{{0,40}}{b}|{b}.{{0,40}}{a}", regex=True)
hybrid_word = txt.str.contains(r"hybrid cord|hybrid yarn|composite cord|co-twisted|two-component cord|dual modulus|different modulus|merged cord", regex=True)
NA = near("aramid", "nylon") | near("aramid", "polyamide") | (txt.str.contains("aramid") & txt.str.contains(r"nylon|polyamide") & hybrid_word)
wear_re = re.compile(r"mileage|tread ?wear|wear resistanc|wear life|tread life|abrasion|uneven wear|irregular wear|even wear|uniform wear|wear propert", re.I)
WEAR = txt.map(lambda t: bool(wear_re.search(t)))
naw = df[(NA & WEAR).values].copy()
nawt = txt[(NA & WEAR).values]

SOL = {
    "Twist / cord geometry tuning": r"twist|denier|dtex|filament count|cord diameter|cord thickness",
    "Adhesion / dip system": r"adhesion|adhesive|\brfl\b|dip|epoxy|coating|tackif",
    "Graded / dual-modulus cord": r"(high|low|first|second|different).{0,20}modulus|graded modulus|modulus.{0,20}(gradient|difference|ratio)|elastic modulus",
    "Two-layer / edge-cover overlay": r"two layer|double layer|dual layer|full[- ]?width.{0,30}(edge|band)|edge (band|cover|cap|layer)|plural.{0,20}layer",
    "Aramid:nylon ratio / core-sheath": r"(aramid|nylon).{0,20}(ratio|proportion|content|percentage)|blend|core.{0,15}(aramid|nylon|sheath)|sheath|wound around.{0,15}core",
    "Position relative to grooves": r"(below|beneath|under|outside|inside).{0,25}(groove|land)|groove.{0,25}(belt|reinforc|cord)|land portion",
    "Zoned density (center vs edge)": r"(center|centre|shoulder|edge|side section|side portion|end portion).{0,60}(density|spacing|count|ends per|epdm)",
    "Variable / graded winding pitch": r"(variable|gradually|graded|different).{0,30}(pitch|spacing|winding)|winding pitch",
}
for k, p in SOL.items():
    naw[k] = nawt.str.contains(p, regex=True).values

counts = pd.Series({k: int(naw[k].sum()) for k in SOL}).sort_values()
N = len(naw)

fig, ax = plt.subplots(figsize=(10, 5.6))
bars = ax.barh(counts.index, counts.values, color="#C44E52")
ax.bar_label(bars, padding=3)
ax.bar_label(bars, labels=[f"{100*v/N:.0f}%" for v in counts.values], padding=-42,
             color="white", fontweight="bold", fontsize=10)
ax.set_xlabel(f"patents (of {N} Nylon-Aramid wear/mileage patents)")
ax.set_title("How Nylon-Aramid patents tackle the wear / mileage problem",
             fontweight="bold")
plt.tight_layout(); plt.savefig("output/nylon_aramid_solutions.png", bbox_inches="tight")
plt.close()

def norm(s):
    s = str(s).split(";")[0]; s = re.sub(r"\([A-Z]{2}\)", "", s)
    return re.sub(r"\s+", " ", s).strip(" .,").upper()
naw["assignee"] = naw["assignee"].map(norm)
naw["year"] = pd.to_numeric(naw["year"], errors="coerce").astype("Int64")
cols = ["doc_id", "assignee", "year", "legal_status"] + list(SOL)
naw[cols].to_csv("output/nylon_aramid_solutions.csv", index=False)
print("saved output/nylon_aramid_solutions.png + .csv | N =", N)
print(counts.sort_values(ascending=False).to_string())
