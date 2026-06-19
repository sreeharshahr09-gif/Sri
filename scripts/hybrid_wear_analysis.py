"""
Wear analysis for the two audited cohorts (General hybrid vs Nylon-Aramid):
comparison chart + per-cohort patent lists with snippets.
Uses cohorts.py (motorcycle excluded, strict hybrid-cord definitions).

Output: output/hybrid_wear_comparison.png,
        output/hybrid_general_wear.csv, output/nylon_aramid_wear.csv
Run   : python scripts/hybrid_wear_analysis.py
"""
import re
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import cohorts as C

plt.rcParams.update({"font.size": 11, "axes.grid": True, "grid.alpha": 0.25,
                     "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 150})
BLUE, RED, GREEN, PURP = "#4C72B0", "#C44E52", "#55A868", "#B0A6C9"

df = C.load(); m = C.masks(df); t = df["_txt"]
WEAR = m["wear"]
def norm(s):
    s = str(s).split(";")[0]; s = re.sub(r"\([A-Z]{2}\)", "", s)
    return re.sub(r"\s+", " ", s).strip(" .,").upper()

def stats(mask):
    s = t[(mask & WEAR).values]
    d = s.str.contains(r"uneven wear|irregular wear|eccentric wear|partial wear", regex=True)
    b = s.str.contains(r"wear resistanc|mileage|tread life|tire life|abrasion resist|wear performance|long.{0,4}wear", regex=True)
    return dict(cohort=int(mask.sum()), wear=len(s), benefit_only=int((b & ~d).sum()),
                both=int((b & d).sum()), defensive_only=int((d & ~b).sum()),
                other=int((~b & ~d).sum()))
names = ["General hybrid", "Nylon-Aramid"]
S = {"General hybrid": stats(m["general"]), "Nylon-Aramid": stats(m["nylon_aramid"])}

fig, ax = plt.subplots(1, 2, figsize=(14, 4.6))
x = np.arange(2); w = 0.38
b1 = ax[0].bar(x - w/2, [S[n]["cohort"] for n in names], w, label="cohort", color="#999999")
b2 = ax[0].bar(x + w/2, [S[n]["wear"] for n in names], w, label="wear", color=RED)
ax[0].bar_label(b1); ax[0].bar_label(b2)
ax[0].set_xticks(x); ax[0].set_xticklabels(names); ax[0].legend()
ax[0].set_title("Cohort size & wear count"); ax[0].set_ylabel("patents")
segk = ["benefit_only", "both", "defensive_only", "other"]; segc = [GREEN, PURP, RED, "#DDDDDD"]
for yi, n in enumerate(names[::-1]):
    tot = S[n]["wear"]; left = 0
    for k, c in zip(segk, segc):
        v = S[n][k]; ax[1].barh(yi, 100*v/tot, left=left, color=c, edgecolor="white")
        if v/tot > 0.06:
            ax[1].text(left + 50*v/tot, yi, f"{v}", ha="center", va="center", fontsize=9,
                       color="white" if c in (GREEN, RED) else "black", fontweight="bold")
        left += 100*v/tot
ax[1].set_yticks(range(2)); ax[1].set_yticklabels([f"{n}\n(n={S[n]['wear']})" for n in names[::-1]])
ax[1].set_xlim(0, 100); ax[1].set_xlabel("% of cohort wear patents"); ax[1].grid(False)
ax[1].set_title("Wear posture (benefit/both/defensive/other)")
ax[1].legend([plt.Rectangle((0, 0), 1, 1, color=c) for c in segc],
             ["benefit", "both", "defensive", "other"], fontsize=8, loc="lower right")
fig.suptitle("Wear analysis — General hybrid vs. Nylon-Aramid (audited)", fontweight="bold")
plt.tight_layout(rect=[0, 0, 1, 0.95])
plt.savefig("output/hybrid_wear_comparison.png", bbox_inches="tight"); plt.close()

belt = re.compile(r"cap ply|overlay|spiral|circumferential|belt|band|cord|hybrid", re.I)
wre = re.compile(C.WEAR_RE, re.I)
def snip(i):
    for f in ["ai_advantages", "ai_method", "abstract", "claims", "ai_problem"]:
        for s in re.split(r"(?<=[.;])\s+", str(df.loc[i, f])):
            if wre.search(s) and belt.search(s) and 45 < len(s) < 320:
                return s.strip()
    return ""
def export(mask, path):
    sub = df[(mask & WEAR).values].copy(); sub["assignee"] = sub["assignee"].map(norm)
    sub["wear_snippet"] = [snip(i) for i in sub.index]
    sub["year"] = pd.to_numeric(sub["year"], errors="coerce").astype("Int64")
    sub[["doc_id", "assignee", "year", "legal_status", "title", "wear_snippet"]]\
        .sort_values("year", ascending=False).to_csv(path, index=False)
    return len(sub)
ng = export(m["general"], "output/hybrid_general_wear.csv")
nn = export(m["nylon_aramid"], "output/nylon_aramid_wear.csv")
print(f"general hybrid wear={ng}, nylon-aramid wear={nn}")
print({n: S[n] for n in names})
