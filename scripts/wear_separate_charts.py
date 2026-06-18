"""
Three standalone wear/mileage charts for the PCR zero-degree corpus.

Input : data/interim/screened.csv
Output: output/wear_mechanisms.png, output/wear_themes.png, output/wear_assignees.png
Run   : python scripts/wear_separate_charts.py
"""
import re
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams.update({"font.size": 12, "axes.grid": True, "grid.alpha": 0.25,
                     "axes.spines.top": False, "axes.spines.right": False,
                     "figure.dpi": 150})


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
wt = txt[w.index]
N = len(w)


# ── 1. Wear mechanisms ──────────────────────────────────────────────────────
mech = {
    "Groove / tread pattern\n× belt": r"groove|land portion|tread pattern",
    "Cord modulus / material": r"modulus|aramid|hybrid|nylon|pet|denier",
    "Growth / profile control": r"growth|profile|curvature|flatten",
    "Footprint / contact pressure": r"contact (area|pressure|patch)|ground contact|footprint",
    "Circumferential / crown rigidity": r"circumferential rigid|crown.{0,15}(rigid|stiff)|hoop",
}
ms = pd.Series({k: int(wt.str.contains(p, regex=True).sum()) for k, p in mech.items()}).sort_values()
fig, ax = plt.subplots(figsize=(10, 5.5))
bars = ax.barh(ms.index, ms.values, color="#8172B3")
ax.bar_label(bars, padding=3, fmt="%d")
ax.bar_label(bars, labels=[f"{100*v/N:.0f}%" for v in ms.values],
             padding=-38, color="white", fontweight="bold")
ax.set_xlabel("patents (of %d wear/mileage patents)" % N)
ax.set_title("Claimed wear mechanisms — PCR zero-degree belt patents",
             fontweight="bold")
plt.tight_layout(); plt.savefig("output/wear_mechanisms.png", bbox_inches="tight")
plt.close()


# ── 2. Wear themes (benefit vs defensive) ───────────────────────────────────
defensive = wt.str.contains(r"uneven wear|irregular wear|eccentric wear|partial wear", regex=True)
benefit = wt.str.contains(
    r"wear resistanc|mileage|tread life|tire life|abrasion resist|wear performance|long.{0,4}wear",
    regex=True)
only_b = int((benefit & ~defensive).sum())
only_d = int((defensive & ~benefit).sum())
both = int((benefit & defensive).sum())
neither = N - only_b - only_d - both

fig, ax = plt.subplots(figsize=(9, 6))
cats = ["Wear-resistance /\nmileage BENEFIT only",
        "Both (claims a benefit\n& fixes uneven wear)",
        "Fixing cap-ply-induced\nUNEVEN wear only",
        "Other wear\nmention"]
vals = [only_b, both, only_d, neither]
colors = ["#55A868", "#999999", "#C44E52", "#dddddd"]
bars = ax.bar(cats, vals, color=colors)
ax.bar_label(bars, padding=3, fmt="%d")
ax.bar_label(bars, labels=[f"{100*v/N:.0f}%" for v in vals], padding=-22,
             color="black", fontsize=10)
ax.set_ylabel("patents")
ax.set_title("Two wear themes in the zero-degree corpus\n"
             "(benefit vs. fixing cap-ply-induced uneven wear)", fontweight="bold")
ax.tick_params(axis="x", labelsize=10)
plt.tight_layout(); plt.savefig("output/wear_themes.png", bbox_inches="tight")
plt.close()


# ── 3. Top assignees ────────────────────────────────────────────────────────
ta_all = w["assignee"].replace("", np.nan).dropna()
top = ta_all.value_counts().head(10)
# split hybrid vs non-hybrid within each assignee
w["hybrid"] = w["hybrid_hits"].astype(float) > 0
hyb = w[w["hybrid"]]["assignee"].value_counts()
order = top.index[::-1]
non_h = [top[a] - int(hyb.get(a, 0)) for a in order]
h = [int(hyb.get(a, 0)) for a in order]
fig, ax = plt.subplots(figsize=(10, 6))
y = np.arange(len(order))
b1 = ax.barh(y, non_h, color="#4C72B0", label="non-hybrid")
b2 = ax.barh(y, h, left=non_h, color="#C44E52", label="hybrid-cord")
ax.set_yticks(y); ax.set_yticklabels([a[:30] for a in order])
ax.bar_label(b2, labels=[f"{int(top[a])}" for a in order], padding=3, fontweight="bold")
ax.set_xlabel("patents mentioning wear / mileage")
ax.set_title("Top assignees — wear/mileage in zero-degree PCR patents",
             fontweight="bold")
ax.legend(loc="lower right")
plt.tight_layout(); plt.savefig("output/wear_assignees.png", bbox_inches="tight")
plt.close()

print("saved: output/wear_mechanisms.png, output/wear_themes.png, output/wear_assignees.png")
print(f"N={N} | benefit_only={only_b} both={both} defensive_only={only_d} other={neither}")
