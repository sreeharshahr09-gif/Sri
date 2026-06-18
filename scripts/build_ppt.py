"""
Build a PowerPoint deck for the hybrid-cord wear/mileage analysis
(General hybrid vs Nylon-Aramid).

Input : data/interim/screened.csv
Output: deck-quality charts in output/, and reports/Hybrid_Cord_Wear_Analysis.pptx
Run   : python scripts/build_ppt.py
"""
import re
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

plt.rcParams.update({"font.size": 13, "axes.grid": True, "grid.alpha": 0.25,
                     "axes.spines.top": False, "axes.spines.right": False,
                     "figure.dpi": 150})
NAVY, BLUE, RED, GREEN, GREY = "#1F3B57", "#4C72B0", "#C44E52", "#55A868", "#999999"

# ── analysis ────────────────────────────────────────────────────────────────
df = pd.read_csv("data/interim/screened.csv").fillna("")
fields = ["ai_advantages", "ai_method", "abstract", "claims", "ai_problem",
          "description", "title"]
txt = df[fields].agg(" ".join, axis=1).str.lower()

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
    "Cord modulus /\nmaterial": r"modulus|aramid|hybrid|nylon|pet|denier",
    "Groove /\ntread pattern": r"groove|land portion|tread pattern",
    "Growth /\nprofile control": r"growth|profile|curvature|flatten",
    "Footprint /\ncontact pressure": r"contact (area|pressure|patch)|ground contact|footprint",
    "Circumferential\nrigidity / shear": r"circumferential rigid|crown.{0,15}(rigid|stiff)|hoop|shear modulus",
}
mech_mask = {k: txt.str.contains(p, regex=True) for k, p in MECH.items()}

def norm(s):
    s = str(s).split(";")[0]; s = re.sub(r"\([A-Z]{2}\)", "", s)
    return re.sub(r"\s+", " ", s).strip(" .,").upper()

def st(mask):
    m = mask & WEAR
    return dict(cohort=int(mask.sum()), wear=int(m.sum()),
               benefit_only=int((m & benefit & ~defensive).sum()),
               both=int((m & benefit & defensive).sum()),
               defensive_only=int((m & defensive & ~benefit).sum()),
               mech={k: int((m & v).sum()) for k, v in mech_mask.items()})
SG, SN = st(GENERAL), st(NYLON_ARAMID)
names = ["General hybrid", "Nylon-Aramid"]
S = {"General hybrid": SG, "Nylon-Aramid": SN}

# ── deck charts ─────────────────────────────────────────────────────────────
def save(fig, path):
    fig.tight_layout(); fig.savefig(path, bbox_inches="tight", facecolor="white"); plt.close(fig)

# chart 1: cohort & wear share
fig, ax = plt.subplots(figsize=(8.5, 4.6))
x = np.arange(2); w = 0.38
b1 = ax.bar(x - w/2, [S[n]["cohort"] for n in names], w, label="cohort size", color=GREY)
b2 = ax.bar(x + w/2, [S[n]["wear"] for n in names], w, label="mention wear/mileage", color=RED)
ax.bar_label(b1); ax.bar_label(b2)
for i, n in enumerate(names):
    ax.text(i + w/2, S[n]["wear"] + 8, f"{100*S[n]['wear']/S[n]['cohort']:.0f}%",
            ha="center", color=RED, fontweight="bold")
ax.set_xticks(x); ax.set_xticklabels(names); ax.legend(); ax.set_ylabel("patents")
ax.set_title("Cohort size & wear/mileage share", fontweight="bold")
save(fig, "output/deck_cohorts.png")

# chart 2: themes — 100% stacked horizontal bar per cohort (posture)
fig, ax = plt.subplots(figsize=(9.2, 4.2))
seg_keys = ["benefit_only", "both", "defensive_only"]
seg_lbl = ["Benefit only (claims a wear/mileage win)",
           "Both (a win + fixes uneven wear)",
           "Defensive only (fixes uneven wear)",
           "Other wear mention"]
seg_col = [GREEN, "#B0A6C9", RED, "#DDDDDD"]
rows = names[::-1]  # Nylon-Aramid on top
for yi, n in enumerate(rows):
    tot = S[n]["wear"]
    vals = [S[n][k] for k in seg_keys]
    vals.append(tot - sum(vals))            # "other"
    left = 0
    for v, c in zip(vals, seg_col):
        ax.barh(yi, 100*v/tot, left=left, color=c, edgecolor="white")
        if v/tot > 0.05:
            ax.text(left + 50*v/tot, yi, f"{v}\n{100*v/tot:.0f}%",
                    ha="center", va="center", fontsize=10,
                    color="white" if c in (GREEN, RED) else "black", fontweight="bold")
        left += 100*v/tot
ax.set_yticks(range(len(rows)))
ax.set_yticklabels([f"{n}\n(n={S[n]['wear']} wear patents)" for n in rows], fontsize=11)
ax.set_xlim(0, 100); ax.set_xlabel("share of the cohort's wear/mileage patents (%)")
ax.set_title("Wear posture: claiming a benefit vs. fixing uneven wear", fontweight="bold")
handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in seg_col]
ax.legend(handles, seg_lbl, loc="upper center", bbox_to_anchor=(0.5, -0.22),
          ncol=2, fontsize=9, frameon=False)
ax.grid(False)
save(fig, "output/deck_themes.png")

# chart 3: mechanisms
fig, ax = plt.subplots(figsize=(8.5, 4.8))
mk = list(MECH); y = np.arange(len(mk)); h = 0.38
for i, (n, c) in enumerate(zip(names, [BLUE, RED])):
    bb = ax.barh(y + (i-0.5)*h, [S[n]["mech"][k] for k in mk], h, label=n, color=c)
    ax.bar_label(bb, fontsize=9, padding=2)
ax.set_yticks(y); ax.set_yticklabels(mk, fontsize=10); ax.legend()
ax.set_xlabel("patents"); ax.set_title("Claimed wear mechanisms", fontweight="bold")
save(fig, "output/deck_mechanisms.png")

# chart 4: nylon-aramid top assignees
fig, ax = plt.subplots(figsize=(8.5, 4.6))
naw = df[(NYLON_ARAMID & WEAR).values].copy(); naw["a"] = naw["assignee"].map(norm)
top = naw["a"].replace("", np.nan).dropna().value_counts().head(8).iloc[::-1]
bb = ax.barh([i[:28] for i in top.index], top.values, color=RED)
ax.bar_label(bb, padding=2)
ax.set_xlabel("patents"); ax.set_title("Top assignees — Nylon-Aramid wear patents", fontweight="bold")
save(fig, "output/deck_assignees.png")

# representative nylon-aramid patents
belt_re = re.compile(r"cap ply|cap-ply|overlay|jointless|spiral|circumferential|belt|band|cord|reinforc", re.I)
def snippet(i):
    for f in ["ai_advantages", "ai_method", "abstract", "claims", "ai_problem"]:
        for s in re.split(r"(?<=[.;])\s+", str(df.loc[i, f])):
            if wear_re.search(s) and belt_re.search(s) and 40 < len(s) < 240:
                return s.strip()
    return ""
naw["snip"] = [snippet(i) for i in naw.index]
naw["year"] = pd.to_numeric(naw["year"], errors="coerce")
key = naw[(naw["snip"].str.len() > 40)].sort_values(
    ["legal_status", "year"], ascending=[True, False])
KEY_PATENTS = [(r["doc_id"], norm(r["assignee"])[:26], int(r["year"]) if pd.notna(r["year"]) else "",
                r["legal_status"], r["snip"]) for _, r in key.head(6).iterrows()]

# ── PPTX ────────────────────────────────────────────────────────────────────
prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
BLANK = prs.slide_layouts[6]
NAVY_RGB, RED_RGB, GREY_RGB = RGBColor(0x1F, 0x3B, 0x57), RGBColor(0xC4, 0x4E, 0x52), RGBColor(0x55, 0x55, 0x55)

def textbox(slide, l, t, w, h):
    tb = slide.shapes.add_textbox(Inches(l), Inches(t), Inches(w), Inches(h))
    tb.text_frame.word_wrap = True
    return tb.text_frame

def bar(slide, color=NAVY_RGB, h=0.18, t=0.0):
    sp = slide.shapes.add_shape(1, Inches(0), Inches(t), prs.slide_width, Inches(h))
    sp.fill.solid(); sp.fill.fore_color.rgb = color; sp.line.fill.background()
    return sp

def title_slide(title, subtitle):
    s = prs.slides.add_slide(BLANK)
    bg = s.background.fill; bg.solid(); bg.fore_color.rgb = NAVY_RGB
    tf = textbox(s, 0.9, 2.5, 11.5, 2.5)
    p = tf.paragraphs[0]; p.text = title
    p.font.size = Pt(40); p.font.bold = True; p.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    p2 = tf.add_paragraph(); p2.text = subtitle
    p2.font.size = Pt(20); p2.font.color.rgb = RGBColor(0xBF, 0xD3, 0xE6)
    return s

def content_slide(title):
    s = prs.slides.add_slide(BLANK)
    bar(s)
    tf = textbox(s, 0.5, 0.32, 12.3, 0.9)
    p = tf.paragraphs[0]; p.text = title
    p.font.size = Pt(26); p.font.bold = True; p.font.color.rgb = NAVY_RGB
    return s

def bullets(tf, items, size=16):
    for i, (txt_, lvl) in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = ("• " if lvl == 0 else "   – ") + txt_
        p.font.size = Pt(size - (2 if lvl else 0))
        p.font.color.rgb = GREY_RGB if lvl else NAVY_RGB
        p.space_after = Pt(5)

def add_img(slide, path, l, t, w):
    slide.shapes.add_picture(path, Inches(l), Inches(t), width=Inches(w))

# 1 title
title_slide("Hybrid Cord Patents — Wear / Mileage Analysis",
            "Zero-degree belt cords in PCR tires · General hybrid vs. Nylon-Aramid · "
            "PatSeer landscape, 1,172 PCR patents")

# 2 scope
s = content_slide("Scope & Method")
tf = textbox(s, 0.6, 1.4, 12.1, 5.6)
bullets(tf, [
    ("Source: PatSeer zero-degree belt landscape — 1,816 families, screened to 1,172 passenger-car-radial (PCR) zero-degree (cap-ply / overlay / jointless-band) patents.", 0),
    ("Two hybrid-cord cohorts defined from patent text:", 0),
    (f"General hybrid cords — explicit hybrid wording or any two-material cord co-occurrence:  {SG['cohort']} patents", 1),
    (f"Nylon-Aramid cords (focus) — aramid + nylon hybrid:  {SN['cohort']} patents", 1),
    ("Wear/mileage detection: lexicon over title, abstract, claims, description and PatSeer AI summaries.", 0),
    ("Theme tagging: BENEFIT (wear resistance / mileage / tread life) vs. DEFENSIVE (fixing uneven / irregular wear).", 0),
    ("Caveat: keyword + co-occurrence based — high recall; verify borderline cases against claims.", 0),
], size=16)

# 3 headline finding
s = content_slide("Headline Finding")
tf = textbox(s, 0.6, 1.5, 6.0, 5.2)
bullets(tf, [
    (f"General hybrid: {SG['wear']} of {SG['cohort']} ({100*SG['wear']/SG['cohort']:.0f}%) make a wear/mileage claim.", 0),
    (f"Nylon-Aramid: {SN['wear']} of {SN['cohort']} ({100*SN['wear']/SN['cohort']:.0f}%) make a wear/mileage claim.", 0),
    ("The zero-degree cord is NOT primarily a mileage technology — its wear effect is indirect (crown stiffness & footprint uniformity).", 0),
    ("Key difference: Nylon-Aramid skews more DEFENSIVE — a larger share fixes uneven wear the stiff aramid cap ply introduces, rather than claiming a clean mileage win.", 0),
], size=17)
add_img(s, "output/deck_cohorts.png", 6.7, 1.6, 6.2)

# 4 themes
s = content_slide("Wear Posture — Claiming a Benefit vs. Fixing Uneven Wear")
add_img(s, "output/deck_themes.png", 0.7, 1.5, 8.4)
tf = textbox(s, 9.1, 1.5, 3.9, 5.4)
bullets(tf, [
    ("Each bar = 100% of that cohort's wear patents, split by what they claim.", 0),
    ("Benefit only = a wear/mileage win, no problem mentioned.", 1),
    ("Defensive = fixes uneven / irregular wear.", 1),
    (f"Nylon-Aramid is more defensive: {SN['defensive_only']} defensive-only vs only {SN['both']} 'both'.", 0),
    (f"General hybrid is the reverse: {SG['both']} 'both' vs {SG['defensive_only']} defensive-only.", 0),
    ("Why: stiff aramid helps footprint uniformity, but its stiffness step can trigger uneven wear that must be engineered out.", 0),
], size=14)

# 5 mechanisms
s = content_slide("Claimed Wear Mechanisms")
add_img(s, "output/deck_mechanisms.png", 6.5, 1.5, 6.5)
tf = textbox(s, 0.6, 1.7, 5.7, 4.8)
bullets(tf, [
    ("Wear effects attributed mainly to cord modulus / material and circumferential rigidity / in-plane shear.", 0),
    ("Then growth & tread-profile control and footprint / contact-pressure uniformity.", 0),
    ("Confirms mechanism is structural (stiffness, footprint) — not tread-compound abrasion chemistry.", 0),
    ("Patents may count under several mechanisms.", 0),
], size=16)

# 6 assignees
s = content_slide("Who Owns the Nylon-Aramid Wear IP")
add_img(s, "output/deck_assignees.png", 6.5, 1.5, 6.5)
tf = textbox(s, 0.6, 1.7, 5.7, 4.8)
bullets(tf, [
    ("Sumitomo, Bridgestone, Goodyear lead; Continental, AlliedSignal (Honeywell) and Kumho also active.", 0),
    ("Several Continental and Kumho entries are legally ALIVE — current freedom-to-operate relevance.", 0),
    ("AlliedSignal patents frame treadwear via in-plane shear modulus of the fiber belt.", 0),
], size=16)

# 7 key patents
s = content_slide("Representative Nylon-Aramid Wear Patents")
tf = textbox(s, 0.5, 1.35, 12.4, 5.7)
items = []
for doc, asg, yr, ls, snip in KEY_PATENTS:
    items.append((f"{doc}  —  {asg}  ({yr}, {ls})", 0))
    items.append((snip, 1))
bullets(tf, items, size=14)

# 8 takeaways
s = content_slide("Takeaways & Recommendations")
tf = textbox(s, 0.6, 1.4, 12.1, 5.6)
bullets(tf, [
    ("Wear/mileage is a SECONDARY, indirect benefit of hybrid zero-degree cords — led by stiffness and footprint control.", 0),
    ("For Nylon-Aramid specifically, manage the uneven-wear risk: the stiffness step at the cap-ply edge/over the reinforcing layer drives irregular wear.", 0),
    ("Design levers seen in the IP: zoned / variable-pitch bandage, side vs. centre cord density, modulus tuning, positioning relative to grooves.", 0),
    ("Watch the ALIVE Continental / Kumho / Sumitomo families for current art.", 0),
    ("Next: tighten the Nylon-Aramid set to explicit co-twisted constructions; add rolling-resistance and high-speed-durability axes.", 0),
], size=16)

out = "reports/Hybrid_Cord_Wear_Analysis.pptx"
prs.save(out)
print("saved", out, "|", len(prs.slides._sldIdLst), "slides")
print("General wear", SG["wear"], "Nylon-Aramid wear", SN["wear"])
