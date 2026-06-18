"""
Wear / mileage overview charts for the PCR zero-degree corpus.

Input : data/interim/screened.csv  (produced by `python run.py`)
Output: output/wear_mileage_overview.png
Run   : python scripts/wear_mileage_charts.py
"""
import re
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams.update({"font.size": 10, "axes.grid": True, "grid.alpha": 0.25,
                     "axes.spines.top": False, "axes.spines.right": False})


def norm(s):
    s = str(s).split(";")[0]
    s = re.sub(r"\([A-Z]{2}\)", "", s)
    return re.sub(r"\s+", " ", s).strip(" .,").upper()


df = pd.read_csv("data/interim/screened.csv").fillna("")
fields = ["ai_advantages", "ai_method", "abstract", "claims", "ai_problem",
          "description", "title"]
txt = df[fields].agg(" ".join, axis=1).str.lower()

wear_re = re.compile(
    r"mileage|tread ?wear|wear resistanc|wear life|wear performance|tread life|"
    r"tire life|abrasion|uneven wear|irregular wear|even wear|uniform wear|"
    r"anti-?wear|wear propert", re.I)
df["wear_hits"] = txt.map(lambda t: len(wear_re.findall(t)))
w = df[df["wear_hits"] > 0].copy()
w["assignee"] = w["assignee"].map(norm)
w["hybrid"] = w["hybrid_hits"].astype(float) > 0
w["year"] = pd.to_numeric(w["year"], errors="coerce")
wt = txt[w.index]

defensive = wt.str.contains(r"uneven wear|irregular wear|eccentric wear|partial wear", regex=True)
benefit = wt.str.contains(
    r"wear resistanc|mileage|tread life|tire life|abrasion resist|wear performance|long.{0,4}wear",
    regex=True)

mech = {
    "Circumferential /\ncrown rigidity": wt.str.contains(r"circumferential rigid|crown.{0,15}(rigid|stiff)|hoop", regex=True),
    "Footprint /\ncontact pressure": wt.str.contains(r"contact (area|pressure|patch)|ground contact|footprint", regex=True),
    "Growth /\nprofile control": wt.str.contains(r"growth|profile|curvature|flatten", regex=True),
    "Groove /\ntread pattern": wt.str.contains(r"groove|land portion|tread pattern", regex=True),
    "Cord modulus /\nmaterial": wt.str.contains(r"modulus|aramid|hybrid|nylon|pet|denier", regex=True),
}

fig = plt.figure(figsize=(15, 10))
fig.suptitle(f"Mileage / Wear Performance in PCR Zero-Degree Belt Patents "
             f"(n={len(w)} of {len(df)})", fontsize=14, weight="bold")

ax1 = fig.add_subplot(2, 3, 1)
comp = pd.Series({"All wear/\nmileage": len(w),
                  "Hybrid-cord\n+ wear": int(w["hybrid"].sum()),
                  "Legally\nalive": int((w["legal_status"].str.upper() == "ALIVE").sum()),
                  "≥2015": int((w["year"] >= 2015).sum())})
ax1.bar_label(ax1.bar(comp.index, comp.values,
              color=["#4C72B0", "#C44E52", "#55A868", "#8172B3"]))
ax1.set_title("Cohort composition"); ax1.set_ylabel("patents")

ax2 = fig.add_subplot(2, 3, 2)
ta = w["assignee"].replace("", np.nan).dropna().value_counts().head(8).iloc[::-1]
ta.index = [i[:26] for i in ta.index]
ax2.bar_label(ax2.barh(ta.index, ta.values, color="#4C72B0"), padding=2)
ax2.set_title("Top assignees (wear cohort)")

ax3 = fig.add_subplot(2, 3, 3)
split = pd.Series({"Wear-resistance /\nmileage BENEFIT": int(benefit.sum()),
                   "Fixing cap-ply-induced\nUNEVEN wear": int(defensive.sum()),
                   "Both": int((benefit & defensive).sum())})
ax3.bar_label(ax3.bar(split.index, split.values, color=["#55A868", "#C44E52", "#999999"]))
ax3.set_title("Two wear themes"); ax3.tick_params(axis="x", labelsize=8)

ax4 = fig.add_subplot(2, 3, 4)
yy = w.dropna(subset=["year"]).copy(); yy = yy[yy["year"] >= 2000]; yy["year"] = yy["year"].astype(int)
piv = yy.groupby(["year", "hybrid"]).size().unstack(fill_value=0)
ax4.stackplot(piv.index, [piv.get(False, 0), piv.get(True, 0)],
              labels=["non-hybrid", "hybrid"], colors=["#bdbdbd", "#C44E52"], alpha=0.85)
ax4.legend(loc="upper left", fontsize=8)
ax4.set_title("Wear-cohort filings per year"); ax4.set_xlabel("priority year")

ax5 = fig.add_subplot(2, 3, 5)
ms = pd.Series({k: int(v.sum()) for k, v in mech.items()}).sort_values()
ax5.bar_label(ax5.barh(ms.index, ms.values, color="#8172B3"), padding=2)
ax5.set_title("Claimed wear mechanisms"); ax5.tick_params(axis="y", labelsize=8)

ax6 = fig.add_subplot(2, 3, 6)
ls = w["legal_status"].str.upper().replace("", "UNKNOWN").value_counts()
ax6.pie(ls.values, labels=ls.index, autopct="%1.0f%%",
        colors=["#999999", "#55A868", "#cccccc"], wedgeprops=dict(width=0.42))
ax6.set_title("Legal status")

plt.tight_layout(rect=[0, 0, 1, 0.96])
plt.savefig("output/wear_mileage_overview.png", dpi=150, bbox_inches="tight")
print("saved output/wear_mileage_overview.png  | benefit:", int(benefit.sum()),
      "defensive:", int(defensive.sum()), "both:", int((benefit & defensive).sum()))
