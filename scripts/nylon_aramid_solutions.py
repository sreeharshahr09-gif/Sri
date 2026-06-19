"""
Nylon-Aramid solution approaches vs. baseline (enrichment) + patent->approach CSV.
Uses the audited cohort definitions in cohorts.py.

Output: output/nylon_aramid_solutions.png, output/nylon_aramid_solutions.csv
Run   : python scripts/nylon_aramid_solutions.py
"""
import re
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import cohorts as C

plt.rcParams.update({"font.size": 12, "axes.grid": True, "grid.alpha": 0.25,
                     "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 150})

df = C.load(); m = C.masks(df); t = df["_txt"]
NA = m["nylon_aramid"]; N = int(NA.sum()); BASE = len(df)

SOL = {
    "Twist / cord geometry": r"twist|denier|dtex|filament count|cord diameter|cord thickness",
    "Graded / dual-modulus": r"(high|low|first|second|different).{0,20}modulus|graded modulus|elastic modulus",
    "Adhesion / dip system": r"adhesion|adhesive|\brfl\b|dip|epoxy|tackif",
    "Core-sheath / wrap": r"core.{0,15}(sheath|wrap|cover)|sheath|wound around.{0,15}core|wrap yarn",
    "Two-layer / edge-cover": r"two layer|double layer|dual layer|edge (band|cover|cap|layer)",
    "Position vs grooves": r"(below|beneath|under).{0,25}(groove|land)|land portion",
    "Zoned density (center/edge)": r"(center|centre|shoulder|edge|side section|side portion).{0,60}(density|spacing|epdm|ends per)",
}
coh = t[NA.values]
rows = [(k, coh.str.contains(p, regex=True).mean(), t.str.contains(p, regex=True).mean()) for k, p in SOL.items()]
rows.sort(key=lambda r: (r[1] / r[2]) if r[2] else 0)

fig, ax = plt.subplots(figsize=(10.4, 5.6))
y = np.arange(len(rows)); h = 0.4
b1 = ax.barh(y + h/2, [100*r[1] for r in rows], h, color="#C44E52", label=f"Nylon-Aramid (n={N})")
ax.barh(y - h/2, [100*r[2] for r in rows], h, color="#999999", label=f"All PCR baseline (n={BASE})")
ax.bar_label(b1, labels=[f"  {r[1]/r[2]:.1f}x" for r in rows], fontsize=10, fontweight="bold")
ax.set_yticks(y); ax.set_yticklabels([r[0] for r in rows]); ax.legend()
ax.set_xlabel("% of patents mentioning the approach")
ax.set_title("How Nylon-Aramid patents differ from baseline\n"
             "(enrichment x; the cord itself — core-sheath / dual-modulus / twist — is what's distinctive)",
             fontweight="bold", fontsize=12)
plt.tight_layout(); plt.savefig("output/nylon_aramid_solutions.png", bbox_inches="tight"); plt.close()

def norm(s):
    s = str(s).split(";")[0]; s = re.sub(r"\([A-Z]{2}\)", "", s)
    return re.sub(r"\s+", " ", s).strip(" .,").upper()
out = df[NA.values].copy(); out["assignee"] = out["assignee"].map(norm)
out["wear"] = m["wear"][NA.values].values
for k, p in SOL.items():
    out[k] = coh.str.contains(p, regex=True).values
out["year"] = pd.to_numeric(out["year"], errors="coerce").astype("Int64")
out[["doc_id", "assignee", "year", "legal_status", "wear"] + list(SOL)].to_csv(
    "output/nylon_aramid_solutions.csv", index=False)
print(f"saved chart + csv | Nylon-Aramid n={N}, wear={int(out['wear'].sum())}")
for k, c, b in rows[::-1]:
    print(f"  {k:28s} {100*c:5.0f}% vs {100*b:4.0f}%  ({c/b:.1f}x)")
