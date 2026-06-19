"""
PET-Nylon (polyester-nylon) solution approaches vs. baseline (enrichment),
plus a head-to-head comparison against Nylon-Aramid — the same study already
done for Nylon-Aramid, replicated for PET-Nylon.

Uses the audited cohort definitions in cohorts.py (single source of truth).

Outputs:
  output/pet_nylon_solutions.png        — PET-Nylon vs baseline (enrichment)
  output/pet_vs_nylonaramid_solutions.png — PET-Nylon vs Nylon-Aramid (enrichment)
  output/pet_nylon_solutions.csv        — patent -> approach matrix
Run: python scripts/pet_nylon_solutions.py
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
RED, GREY, BLUE = "#C44E52", "#999999", "#4C72B0"

df = C.load(); m = C.masks(df); t = df["_txt"]
PN = m["pet_nylon"]; NA = m["nylon_aramid"]
N_PN = int(PN.sum()); N_NA = int(NA.sum()); BASE = len(df)

# Same solution taxonomy used for Nylon-Aramid, so the two studies are comparable.
SOL = {
    "Cord twist & geometry": r"twist|denier|dtex|filament count|cord diameter|cord thickness",
    "Dual-modulus cord (two stiffnesses)": r"(high|low|first|second|different).{0,20}modulus|graded modulus|elastic modulus",
    "Cord-to-rubber adhesion (dip)": r"adhesion|adhesive|\brfl\b|dip|epoxy|tackif",
    "Core-sheath cord (2 materials, 1 cord)": r"core.{0,15}(sheath|wrap|cover)|sheath|wound around.{0,15}core|wrap yarn",
    "Layered / edge-cover bandage": r"two layer|double layer|dual layer|edge (band|cover|cap|layer)",
    "Cord position under grooves": r"(below|beneath|under).{0,25}(groove|land)|land portion",
    "Center-vs-edge cord layout (zoned)": r"(center|centre|shoulder|edge|side section|side portion).{0,60}(density|spacing|epdm|ends per)",
}
cohPN = t[PN.values]; cohNA = t[NA.values]


def enr(coh):
    return [(k, coh.str.contains(p, regex=True).mean(), t.str.contains(p, regex=True).mean())
            for k, p in SOL.items()]


# ── chart 1: PET-Nylon vs baseline (enrichment) ─────────────────────────────
rows = sorted(enr(cohPN), key=lambda r: (r[1] / r[2]) if r[2] else 0)
fig, ax = plt.subplots(figsize=(10.4, 5.6))
y = np.arange(len(rows)); h = 0.4
b1 = ax.barh(y + h/2, [100*r[1] for r in rows], h, color=BLUE, label=f"PET-Nylon (n={N_PN})")
ax.barh(y - h/2, [100*r[2] for r in rows], h, color=GREY, label=f"All PCR baseline (n={BASE})")
ax.bar_label(b1, labels=[f"  {r[1]/r[2]:.1f}x" for r in rows], fontsize=10, fontweight="bold")
ax.set_yticks(y); ax.set_yticklabels([r[0] for r in rows]); ax.legend()
ax.set_xlabel("% of patents mentioning the approach")
ax.set_title("How PET-Nylon patents differ from baseline\n"
             "(enrichment x; PET-Nylon's distinctive wear lever is the center-vs-edge cord "
             "layout — spatial zoning, not one composite cord)",
             fontweight="bold", fontsize=11)
plt.tight_layout(); plt.savefig("output/pet_nylon_solutions.png", bbox_inches="tight"); plt.close()

# ── chart 2: PET-Nylon vs Nylon-Aramid (enrichment head-to-head) ────────────
order = list(SOL)                       # fixed order, both cohorts
ePN = {k: c/b if b else 0 for k, c, b in enr(cohPN)}
eNA = {k: c/b if b else 0 for k, c, b in enr(cohNA)}
order = sorted(order, key=lambda k: max(ePN[k], eNA[k]))
fig, ax = plt.subplots(figsize=(10.8, 5.8))
y = np.arange(len(order)); h = 0.4
bP = ax.barh(y + h/2, [ePN[k] for k in order], h, color=BLUE, label=f"PET-Nylon (n={N_PN})")
bN = ax.barh(y - h/2, [eNA[k] for k in order], h, color=RED, label=f"Nylon-Aramid (n={N_NA})")
ax.bar_label(bP, labels=[f" {ePN[k]:.1f}x" for k in order], fontsize=9, fontweight="bold")
ax.bar_label(bN, labels=[f" {eNA[k]:.1f}x" for k in order], fontsize=9, fontweight="bold")
ax.axvline(1.0, color="#444444", lw=1, ls="--")
ax.text(1.02, len(order)-0.4, "baseline (1x)", fontsize=8, color="#444444")
ax.set_yticks(y); ax.set_yticklabels(order); ax.legend(loc="lower right")
ax.set_xlabel("enrichment vs. all-PCR baseline  (x)")
_zx = ePN["Center-vs-edge cord layout (zoned)"]; _cx = eNA["Core-sheath cord (2 materials, 1 cord)"]
ax.set_title("How each hybrid tackles wear — PET-Nylon vs. Nylon-Aramid\n"
             f"PET-Nylon -> center-vs-edge layout ({_zx:.1f}x); Nylon-Aramid -> the CORD itself "
             f"(core-sheath {_cx:.1f}x). Dual-modulus & twist are shared.",
             fontweight="bold", fontsize=11)
plt.tight_layout(); plt.savefig("output/pet_vs_nylonaramid_solutions.png", bbox_inches="tight"); plt.close()


# ── CSV: patent -> approach matrix for the PET-Nylon cohort ──────────────────
def norm(s):
    s = str(s).split(";")[0]; s = re.sub(r"\([A-Z]{2}\)", "", s)
    return re.sub(r"\s+", " ", s).strip(" .,").upper()
out = df[PN.values].copy(); out["assignee"] = out["assignee"].map(norm)
out["wear"] = m["wear"][PN.values].values
out["also_nylon_aramid"] = NA[PN.values].values
for k, p in SOL.items():
    out[k] = cohPN.str.contains(p, regex=True).values
out["year"] = pd.to_numeric(out["year"], errors="coerce").astype("Int64")
out[["doc_id", "assignee", "year", "legal_status", "wear", "also_nylon_aramid"] + list(SOL)].to_csv(
    "output/pet_nylon_solutions.csv", index=False)

print(f"PET-Nylon n={N_PN}, wear={int(out['wear'].sum())}, overlap w/ Nylon-Aramid={int(NA[PN.values].sum())}")
print(f"{'approach':30s} {'PET-Ny':>8} {'NylAr':>8}")
for k in order[::-1]:
    print(f"  {k:28s} {ePN[k]:6.1f}x {eNA[k]:6.1f}x")
