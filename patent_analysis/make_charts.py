#!/usr/bin/env python3
"""
Patent landscape graphics: Zero-degree / circumferential belt and its effect on
bead (and adjacent crown/belt-edge) durability, 2015-2026.

NOTE ON DATA: values encode the *relative emphasis* observed across a sampled set
of patents/applications from Google Patents, Justia, USPTO and WIPO surfaced in
the research. They are indicative of direction and intensity, NOT exact database
counts. Use as a qualitative landscape, not a statistical census.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np

OUT = "/home/user/Sri/patent_analysis"
plt.rcParams.update({
    "font.size": 10,
    "axes.titlesize": 12,
    "axes.titleweight": "bold",
    "figure.dpi": 130,
})

NAVY = "#1f3b5c"; TEAL = "#2a9d8f"; AMBER = "#e9a227"; RUST = "#c1492b"
SLATE = "#5b6b7b"; LGREY = "#dfe4e8"; GREEN = "#3a7d44"

# ----------------------------------------------------------------------------
# FIG 1 -- Filing-intensity trend 2015-2026 + thematic shift
# ----------------------------------------------------------------------------
def fig_trend():
    yrs = np.arange(2015, 2027)
    # indicative relative filing intensity for "0deg-belt + durability" theme
    intensity = [42,46,50,55,58,54,60,68,75,80,77,40]   # 2026 partial year
    fig, ax = plt.subplots(figsize=(10, 5.2))
    bars = ax.bar(yrs, intensity, color=NAVY, width=0.62, zorder=3)
    bars[-1].set_color(SLATE); bars[-1].set_hatch("//"); bars[-1].set_alpha(0.6)
    # rolling trend line
    ax.plot(yrs[:-1], np.convolve(intensity[:-1],[1/3,1/3,1/3],"same"),
            color=RUST, lw=2.4, marker="o", ms=4, zorder=4, label="3-yr smoothed trend")
    ax.set_title("Filing intensity — 0°/circumferential belt linked to durability (2015–2026)")
    ax.set_ylabel("Relative filing intensity (indicative)")
    ax.set_xticks(yrs); ax.set_ylim(0, 95)
    ax.grid(axis="y", color=LGREY, zorder=0)
    for s in ["top","right"]: ax.spines[s].set_visible(False)
    ax.annotate("EV / high-load mass\n→ bead becomes\nlimiting factor",
                xy=(2023, 75), xytext=(2018.4, 86),
                arrowprops=dict(arrowstyle="->", color=RUST), color=RUST,
                fontsize=9, ha="center", fontweight="bold")
    ax.annotate("2026 = partial /\nun-published",
                xy=(2026, 40), xytext=(2025.0, 60),
                arrowprops=dict(arrowstyle="->", color=SLATE), color=SLATE,
                fontsize=8, ha="center")
    ax.legend(loc="lower right", frameon=False)
    fig.tight_layout(); fig.savefig(f"{OUT}/1_filing_trend.png"); plt.close(fig)

# ----------------------------------------------------------------------------
# FIG 2 -- Leading assignees (relative activity, split by play)
# ----------------------------------------------------------------------------
def fig_assignees():
    data = [
        ("Bridgestone",      9.0, "OEM"),
        ("Michelin",         8.3, "OEM"),
        ("Sumitomo / Falken",7.2, "OEM"),
        ("Goodyear",         6.8, "OEM"),
        ("Continental",      6.3, "OEM"),
        ("Yokohama",         5.4, "OEM"),
        ("Hankook",          4.8, "OEM"),
        ("Pirelli",          3.6, "OEM"),
        ("Toyo",             3.0, "OEM"),
        ("Kolon Industries", 4.4, "Cord/Material"),
        ("Hyosung",          3.8, "Cord/Material"),
        ("Chinese OEMs*",    5.0, "Regional"),
    ]
    data.sort(key=lambda x: x[1])
    names = [d[0] for d in data]; vals=[d[1] for d in data]
    cmap = {"OEM":NAVY, "Cord/Material":TEAL, "Regional":AMBER}
    cols = [cmap[d[2]] for d in data]
    fig, ax = plt.subplots(figsize=(10, 5.6))
    ax.barh(names, vals, color=cols, zorder=3)
    ax.set_title("Leading assignees — 0°-belt / crown-reinforcement & bead-durability space")
    ax.set_xlabel("Relative activity in theme (indicative)")
    ax.grid(axis="x", color=LGREY, zorder=0)
    for s in ["top","right"]: ax.spines[s].set_visible(False)
    handles=[plt.Rectangle((0,0),1,1,color=c) for c in cmap.values()]
    ax.legend(handles, cmap.keys(), loc="lower right", frameon=False, title="Assignee type")
    ax.text(0, -1.4, "*Chinese OEMs incl. Sailun, Linglong, ZC Rubber, Triangle — strong on all-steel 0°-belt structure & compounds",
            fontsize=7.5, color=SLATE, transform=ax.get_yaxis_transform())
    fig.tight_layout(); fig.savefig(f"{OUT}/2_assignees.png"); plt.close(fig)

# ----------------------------------------------------------------------------
# FIG 3 -- Causal mechanism map: 0deg belt -> bead durability
# ----------------------------------------------------------------------------
def fig_mechanism():
    fig, ax = plt.subplots(figsize=(11.5, 6.6)); ax.axis("off")
    ax.set_xlim(0,12); ax.set_ylim(0,8)
    def box(x,y,w,h,txt,fc,tc="white",fs=9.5):
        ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle="round,pad=0.08,rounding_size=0.12",
                    fc=fc,ec="white",lw=1.5))
        ax.text(x+w/2,y+h/2,txt,ha="center",va="center",color=tc,fontsize=fs,
                fontweight="bold",wrap=True)
    def arrow(x1,y1,x2,y2,c=SLATE):
        ax.add_patch(FancyArrowPatch((x1,y1),(x2,y2),arrowstyle="-|>",
                    mutation_scale=16,color=c,lw=2))
    ax.text(6,7.6,"Why a crown-region 0°/circumferential belt reaches down to the BEAD",
            ha="center",fontsize=13,fontweight="bold",color=NAVY)
    # source
    box(0.3,5.6,3.0,1.2,"0° / circumferential\nbelt (cap ply, spiral,\nfull/edge cover)",NAVY)
    # intermediate mechanisms
    box(4.3,6.4,3.4,1.0,"Strong hoop effect →\nrestrains crown growth",TEAL)
    box(4.3,5.0,3.4,1.0,"Stiffer, flatter crown →\naltered carcass line",TEAL)
    box(4.3,3.6,3.4,1.0,"Belt-edge shear /\nscissoring concentrated",TEAL)
    box(4.3,2.2,3.4,1.0,"Higher loads carried\n(EV / truck mass)",TEAL)
    # bead consequence
    box(8.6,4.3,3.1,1.4,"BEAD region:\n↑ carcass tension &\nbending at TURN-UP END\n→ fatigue / separation",RUST)
    box(8.6,2.2,3.1,1.0,"Crown / belt-edge\nseparation (adjacent)",AMBER,tc="#3a2a00")
    arrow(3.3,6.2,4.3,6.9); arrow(3.3,6.0,4.3,5.5)
    arrow(3.3,5.8,4.3,4.1); arrow(3.3,5.7,4.3,2.7)
    arrow(7.7,6.9,8.6,5.4); arrow(7.7,5.5,8.6,5.2)
    arrow(7.7,4.1,8.6,4.9); arrow(7.7,2.7,8.6,4.6)
    arrow(7.7,3.9,8.6,2.7,c=AMBER)
    ax.text(6,0.7,"Net effect: the 0° belt rarely fails itself — it RE-ROUTES load/tension so the\n"
                  "turn-up end & bead apex become the durability-limiting site, especially under high mass.",
            ha="center",fontsize=9.5,style="italic",color=NAVY,
            bbox=dict(boxstyle="round",fc=LGREY,ec="none"))
    fig.tight_layout(); fig.savefig(f"{OUT}/3_mechanism_map.png"); plt.close(fig)

# ----------------------------------------------------------------------------
# FIG 4 -- Mitigation levers grouped by family
# ----------------------------------------------------------------------------
def fig_levers():
    groups = {
        "MATERIAL / CORD": [
            ("Nylon-aramid hybrid cap-ply cord", 9),
            ("Bi-elastic / high-modulus cords", 7),
            ("Cooler-running 0°-belt compound", 6),
            ("Resin / adhesion-tuned coat rubber", 5),
        ],
        "BELT STRUCTURE": [
            ("0° ply narrower than working plies", 8),
            ("Belt-edge cushion / insulation", 8),
            ("Full-cover + edge-cover combo", 7),
            ("Continuous / zig-zag crown reinf.", 5),
        ],
        "BEAD / CARCASS GEOMETRY": [
            ("Steel-cord bead reinforcing ply", 8),
            ("Apex stiffness gradient / soft apex", 7),
            ("Carcass turn-up height tuning", 7),
            ("1st/2nd turn-up pads", 5),
        ],
        "PROCESS / MODELLING": [
            ("Spiral winding pitch control", 6),
            ("FEA strain-energy optimisation", 6),
            ("Lightweighting w/o durability loss", 5),
        ],
    }
    fig, axes = plt.subplots(2,2, figsize=(12, 8))
    fam_col = [TEAL, NAVY, RUST, AMBER]
    for ax,(name,items),c in zip(axes.ravel(), groups.items(), fam_col):
        labs=[i[0] for i in items][::-1]; vals=[i[1] for i in items][::-1]
        ax.barh(labs, vals, color=c, zorder=3)
        ax.set_title(name, color=c)
        ax.set_xlim(0,10); ax.grid(axis="x", color=LGREY, zorder=0)
        ax.tick_params(labelsize=8.5)
        for s in ["top","right"]: ax.spines[s].set_visible(False)
    fig.suptitle("Engineering levers used to protect bead/crown durability when adding a 0° belt",
                 fontsize=13, fontweight="bold", color=NAVY, y=0.99)
    fig.text(0.5,0.005,"Bar length = relative frequency / emphasis across sampled patents (indicative)",
             ha="center", fontsize=8, color=SLATE)
    fig.tight_layout(rect=[0,0.02,1,0.97]); fig.savefig(f"{OUT}/4_levers.png"); plt.close(fig)

# ----------------------------------------------------------------------------
# FIG 5 -- Opportunity vs Challenge bubble matrix (impact vs maturity)
# ----------------------------------------------------------------------------
def fig_opportunity():
    # (label, maturity 0-10, impact 0-10, type O/C, size)
    items = [
        ("Nylon-aramid hybrid cap ply",      8.5, 7.5, "O", 900),
        ("0°-ply edge geometry rules",       8.0, 6.5, "O", 700),
        ("Belt-edge cushion compounds",      7.5, 6.0, "O", 600),
        ("EV high-load bead+crown co-design", 3.5, 9.0, "O", 1200),
        ("Lightweight 0° belt (sustain.)",   3.4, 7.9, "O", 850),
        ("Continuous/zig-zag crown reinf.",  3.0, 6.5, "O", 650),
        ("Sensorised / SHM durability",      2.0, 5.5, "O", 500),
        ("Turn-up end fatigue under hoop",   6.0, 8.5, "C", 1000),
        ("Heat build-up at high mass",       5.3, 7.2, "C", 800),
        ("0°/steel-belt edge shear coupling",5.5, 7.0, "C", 700),
        ("Recyclability vs hybrid cords",    3.0, 6.0, "C", 600),
        ("Cost of aramid / new cords",       6.5, 5.0, "C", 550),
    ]
    fig, ax = plt.subplots(figsize=(11, 7.4))
    for lab,x,y,t,s in items:
        c = GREEN if t=="O" else RUST
        ax.scatter(x,y,s=s,color=c,alpha=0.55,edgecolor="white",lw=1.5,zorder=3)
        ax.text(x,y,lab,fontsize=7.8,ha="center",va="center",zorder=4,wrap=True)
    ax.axvline(5,color=LGREY,zorder=0); ax.axhline(6.7,color=LGREY,zorder=0)
    ax.set_xlim(1,10); ax.set_ylim(4,10)
    ax.set_xlabel("Technology maturity  →  (emerging ............ established)")
    ax.set_ylabel("Impact on durability problem  →")
    ax.set_title("Opportunity (green) vs Challenge (red) landscape — bubble size = activity")
    for s in ["top","right"]: ax.spines[s].set_visible(False)
    ax.text(2.6,9.6,"HIGH-IMPACT /\nEMERGING\n(white space)",color=GREEN,fontsize=9,
            fontweight="bold",ha="center",alpha=0.8)
    ax.text(8.4,9.6,"HIGH-IMPACT /\nMATURE\n(crowded)",color=NAVY,fontsize=9,
            fontweight="bold",ha="center",alpha=0.8)
    h=[plt.Line2D([0],[0],marker="o",color="w",markerfacecolor=GREEN,markersize=12,label="Opportunity"),
       plt.Line2D([0],[0],marker="o",color="w",markerfacecolor=RUST,markersize=12,label="Challenge")]
    ax.legend(handles=h,loc="lower left",frameon=False)
    fig.tight_layout(); fig.savefig(f"{OUT}/5_opportunity_matrix.png"); plt.close(fig)

# ----------------------------------------------------------------------------
# FIG 6 -- Direction of travel timeline (theme evolution 2015->2026)
# ----------------------------------------------------------------------------
def fig_direction():
    fig, ax = plt.subplots(figsize=(11.5, 4.4)); ax.axis("off")
    ax.set_xlim(0,12); ax.set_ylim(0,5)
    phases = [
        (0.4,"2015-18","Structure first","0°-ply width, belt-edge\ncushion, damper layers",NAVY),
        (3.4,"2018-21","Material turn","Nylon→ hybrid/aramid &\nbi-elastic cap-ply cords",TEAL),
        (6.4,"2021-24","System co-design","Crown + bead treated\ntogether; FEA-driven",AMBER),
        (9.4,"2024-26","Mass & sustain.","EV/high-load bead limit;\nlightweight + recyclable",RUST),
    ]
    for x,yr,t,d,c in phases:
        ax.add_patch(FancyBboxPatch((x,1.4),2.4,2.2,boxstyle="round,pad=0.08,rounding_size=0.15",
                     fc=c,ec="white",lw=2))
        ax.text(x+1.2,3.15,yr,ha="center",color="white",fontsize=10,fontweight="bold")
        ax.text(x+1.2,2.55,t,ha="center",color="white",fontsize=10,fontweight="bold")
        ax.text(x+1.2,1.9,d,ha="center",color="white",fontsize=8)
    for x in [2.8,5.8,8.8]:
        ax.add_patch(FancyArrowPatch((x,2.5),(x+0.6,2.5),arrowstyle="-|>",
                     mutation_scale=20,color=SLATE,lw=2.5))
    ax.text(6,4.4,"Direction of travel: from belt geometry  →  cords  →  whole-casing co-design  →  mass/sustainability",
            ha="center",fontsize=12,fontweight="bold",color=NAVY)
    ax.text(6,0.7,"Bead durability moves from a side-effect to a primary design constraint as vehicle mass rises.",
            ha="center",fontsize=9.5,style="italic",color=SLATE)
    fig.tight_layout(); fig.savefig(f"{OUT}/6_direction.png"); plt.close(fig)

for f in [fig_trend, fig_assignees, fig_mechanism, fig_levers, fig_opportunity, fig_direction]:
    f(); print("done:", f.__name__)
print("ALL CHARTS WRITTEN ->", OUT)
