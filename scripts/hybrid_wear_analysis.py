"""
Wear/mileage analysis for two hybrid-cord cohorts:
  A) General hybrid cords   B) Nylon-Aramid hybrid cords  (focus)

Input : data/interim/screened.csv
Output: output/hybrid_wear_comparison.png
        output/hybrid_general_wear.csv, output/nylon_aramid_wear.csv
Run   : python scripts/hybrid_wear_analysis.py
"""
import re
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams.update({"font.size": 11, "axes.grid": True, "grid.alpha": 0.25,
                     "axes.spines.top": False, "axes.spines.right": False,
                     "figure.dpi": 150})
BLUE, RED = "#4C72B0", "#C44E52"


def norm(s):
    s = str(s).split(";")[0]
    s = re.sub(r"\([A-Z]{2}\)", "", s)
    return re.sub(r"\s+", " ", s).strip(" .,").upper()


df = pd.read_csv("data/interim/screened.csv").fillna("")
fields = ["ai_advantages", "ai_method", "abstract", "claims", "ai_problem",
          "description", "title"]
txt = df[fields].agg(" ".join, axis=1).str.lower()

# ── cohort definitions ──────────────────────────────────────────────────────
hybrid_word = txt.str.contains(
    r"hybrid cord|hybrid tire cord|hybrid yarn|composite cord|hybrid construction|"
    r"co-twisted|two-component cord|dual modulus|different modulus|merged cord", regex=True)
def near(a, b): return txt.str.contains(rf"{a}.{{0,40}}{b}|{b}.{{0,40}}{a}", regex=True)
ar_ny = near("aramid", "nylon") | near("aramid", "polyamide")
pet_ny = near("polyester", "nylon") | near("pet", "nylon")

GENERAL = hybrid_word | ar_ny | pet_ny
NYLON_ARAMID = ar_ny | (txt.str.contains("aramid") & txt.str.contains(r"nylon|polyamide") & hybrid_word)

wear_re = re.compile(
    r"mileage|tread ?wear|wear resistanc|wear life|wear performance|tread life|"
    r"tire life|abrasion|uneven wear|irregular wear|even wear|uniform wear|"
    r"anti-?wear|wear propert", re.I)
WEAR = txt.map(lambda t: bool(wear_re.search(t)))

defensive = txt.str.contains(r"uneven wear|irregular wear|eccentric wear|partial wear", regex=True)
benefit = txt.str.contains(
    r"wear resistanc|mileage|tread life|tire life|abrasion resist|wear performance|long.{0,4}wear", regex=True)
MECH = {
    "Groove / tread pattern": r"groove|land portion|tread pattern",
    "Cord modulus / material": r"modulus|aramid|hybrid|nylon|pet|denier",
    "Growth / profile control": r"growth|profile|curvature|flatten",
    "Footprint / contact press.": r"contact (area|pressure|patch)|ground contact|footprint",
    "Circumferential rigidity": r"circumferential rigid|crown.{0,15}(rigid|stiff)|hoop",
}
mech_mask = {k: txt.str.contains(p, regex=True) for k, p in MECH.items()}

cohorts = {"General hybrid": GENERAL, "Nylon-Aramid": NYLON_ARAMID}


def stats(mask):
    m = mask & WEAR
    return dict(
        cohort=int(mask.sum()), wear=int(m.sum()),
        benefit_only=int((m & benefit & ~defensive).sum()),
        both=int((m & benefit & defensive).sum()),
        defensive_only=int((m & defensive & ~benefit).sum()),
        mech={k: int((m & v).sum()) for k, v in mech_mask.items()})


S = {name: stats(mask) for name, mask in cohorts.items()}

# ── figure ──────────────────────────────────────────────────────────────────
fig = plt.figure(figsize=(15, 10))
fig.suptitle("Wear / Mileage Claims — General Hybrid vs. Nylon-Aramid Cords",
             fontsize=15, weight="bold")

# (1) cohort vs wear share
ax = fig.add_subplot(2, 2, 1)
names = list(S)
x = np.arange(len(names)); width = 0.38
b1 = ax.bar(x - width/2, [S[n]["cohort"] for n in names], width, label="cohort size", color="#bbbbbb")
b2 = ax.bar(x + width/2, [S[n]["wear"] for n in names], width, label="mention wear/mileage", color=RED)
ax.bar_label(b1); ax.bar_label(b2)
for i, n in enumerate(names):
    ax.text(i + width/2, S[n]["wear"] + 6, f"{100*S[n]['wear']/S[n]['cohort']:.0f}%",
            ha="center", color=RED, fontweight="bold")
ax.set_xticks(x); ax.set_xticklabels(names); ax.legend(); ax.set_title("Cohort size & wear share")
ax.set_ylabel("patents")

# (2) themes grouped
ax = fig.add_subplot(2, 2, 2)
themes = ["benefit_only", "both", "defensive_only"]
tlabels = ["benefit only", "both", "uneven-wear\n(defensive) only"]
x = np.arange(len(themes)); width = 0.38
for i, (n, c) in enumerate(zip(names, [BLUE, RED])):
    vals = [S[n][t] for t in themes]
    bb = ax.bar(x + (i - 0.5)*width, vals, width, label=n, color=c)
    ax.bar_label(bb, fontsize=9)
ax.set_xticks(x); ax.set_xticklabels(tlabels); ax.legend()
ax.set_title("Wear themes (within each cohort's wear patents)"); ax.set_ylabel("patents")

# (3) mechanisms grouped
ax = fig.add_subplot(2, 2, 3)
mk = list(MECH); y = np.arange(len(mk)); h = 0.38
for i, (n, c) in enumerate(zip(names, [BLUE, RED])):
    vals = [S[n]["mech"][k] for k in mk]
    bb = ax.barh(y + (i - 0.5)*h, vals, h, label=n, color=c)
    ax.bar_label(bb, fontsize=8, padding=2)
ax.set_yticks(y); ax.set_yticklabels(mk, fontsize=9); ax.legend()
ax.set_title("Claimed wear mechanisms"); ax.set_xlabel("patents")

# (4) top assignees in Nylon-Aramid wear cohort
ax = fig.add_subplot(2, 2, 4)
na_wear = df[(NYLON_ARAMID & WEAR).values].copy()
na_wear["assignee"] = na_wear["assignee"].map(norm)
top = na_wear["assignee"].replace("", np.nan).dropna().value_counts().head(8).iloc[::-1]
bb = ax.barh([i[:26] for i in top.index], top.values, color=RED)
ax.bar_label(bb, padding=2)
ax.set_title("Top assignees — Nylon-Aramid wear patents"); ax.set_xlabel("patents")

plt.tight_layout(rect=[0, 0, 1, 0.96])
plt.savefig("output/hybrid_wear_comparison.png", bbox_inches="tight")
plt.close()

# ── exports with snippets ───────────────────────────────────────────────────
belt_re = re.compile(r"cap ply|cap-ply|overlay|jointless|spiral|circumferential|belt|band|cord|reinforc", re.I)
def snippet(i):
    for f in ["ai_advantages", "ai_method", "abstract", "claims", "ai_problem"]:
        for s in re.split(r"(?<=[.;])\s+", str(df.loc[i, f])):
            if wear_re.search(s) and belt_re.search(s) and 40 < len(s) < 320:
                return s.strip()
    return ""

def export(mask, path):
    sub = df[(mask & WEAR).values].copy()
    sub["assignee"] = sub["assignee"].map(norm)
    sub["wear_snippet"] = [snippet(i) for i in sub.index]
    sub["year"] = pd.to_numeric(sub["year"], errors="coerce").astype("Int64")
    sub[["doc_id", "assignee", "year", "legal_status", "title", "wear_snippet"]]\
        .sort_values("year", ascending=False).to_csv(path, index=False)
    return len(sub)

ng = export(GENERAL, "output/hybrid_general_wear.csv")
nn = export(NYLON_ARAMID, "output/nylon_aramid_wear.csv")

print("saved output/hybrid_wear_comparison.png")
print(f"general hybrid wear: {ng}  ->  output/hybrid_general_wear.csv")
print(f"nylon-aramid  wear: {nn}  ->  output/nylon_aramid_wear.csv")
print("\nThemes (wear patents):")
for n in names:
    print(f"  {n:14s}: benefit_only={S[n]['benefit_only']} both={S[n]['both']} "
          f"defensive_only={S[n]['defensive_only']}")
