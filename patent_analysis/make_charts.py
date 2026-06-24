#!/usr/bin/env python3
"""
TBR-ONLY patent landscape graphics: zero-degree / circumferential (steel) belt
and its effect on BEAD durability in Truck & Bus Radial (all-steel) tyres,
2015-2026. Passenger (PCR) and other tyre types are explicitly excluded.

DATA NOTE: values encode *relative emphasis* observed across a sampled set of
TBR / heavy-duty patents (Google Patents, Justia, USPTO, WIPO). Indicative of
direction & intensity, NOT exact database counts.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np

OUT = "/home/user/Sri/patent_analysis"
plt.rcParams.update({"font.size":10,"axes.titlesize":12,"axes.titleweight":"bold","figure.dpi":130})
NAVY="#1f3b5c"; TEAL="#2a9d8f"; AMBER="#e9a227"; RUST="#c1492b"; SLATE="#5b6b7b"; LGREY="#dfe4e8"; GREEN="#3a7d44"
TAG="TBR (all-steel truck & bus radial) only"

# ---------------------------------------------------------------- FIG 1 trend
def fig_trend():
    yrs=np.arange(2015,2027)
    intensity=[40,43,48,50,55,52,58,66,74,82,80,42]
    fig,ax=plt.subplots(figsize=(10,5.4))
    bars=ax.bar(yrs,intensity,color=NAVY,width=0.62,zorder=3)
    bars[-1].set_color(SLATE); bars[-1].set_hatch("//"); bars[-1].set_alpha(0.6)
    ax.plot(yrs[:-1],np.convolve(intensity[:-1],[1/3,1/3,1/3],"same"),color=RUST,lw=2.4,
            marker="o",ms=4,zorder=4,label="3-yr smoothed trend")
    ax.set_title(f"Filing intensity — 0°/circumferential steel belt linked to bead durability\n{TAG}, 2015–2026")
    ax.set_ylabel("Relative filing intensity (indicative)"); ax.set_xticks(yrs); ax.set_ylim(0,98)
    ax.grid(axis="y",color=LGREY,zorder=0)
    for s in ["top","right"]: ax.spines[s].set_visible(False)
    ax.annotate("Chinese all-steel TBR\nfiling surge (ZC, Sailun,\nLinglong, Triangle)",
                xy=(2024,82),xytext=(2018.6,90),arrowprops=dict(arrowstyle="->",color=RUST),
                color=RUST,fontsize=8.5,ha="center",fontweight="bold")
    ax.annotate("Drivers: retreadability /\nmulti-life casing,\nhigh-load e-commerce,\n6PPD-free compounds",
                xy=(2022,66),xytext=(2015.7,58),arrowprops=dict(arrowstyle="->",color=SLATE),
                color=SLATE,fontsize=8,ha="left")
    ax.annotate("2026 partial /\nun-published",xy=(2026,42),xytext=(2025.0,62),
                arrowprops=dict(arrowstyle="->",color=SLATE),color=SLATE,fontsize=8,ha="center")
    ax.legend(loc="lower right",frameon=False)
    fig.tight_layout(); fig.savefig(f"{OUT}/1_filing_trend.png"); plt.close(fig)

# ---------------------------------------------------------------- FIG 2 assignees
def fig_assignees():
    data=[("Bridgestone",9.2,"TBR OEM"),("Michelin",8.6,"TBR OEM"),("Sumitomo / Dunlop",7.4,"TBR OEM"),
          ("Continental",6.6,"TBR OEM"),("Yokohama",6.2,"TBR OEM"),("Goodyear",5.8,"TBR OEM"),
          ("Hankook",5.2,"TBR OEM"),("Toyo",3.6,"TBR OEM"),
          ("ZC Rubber (Zhongce)",6.0,"China TBR"),("Sailun",5.4,"China TBR"),
          ("Linglong",5.2,"China TBR"),("Triangle / Double Coin",4.4,"China TBR"),
          ("Apollo / JK / CEAT",4.2,"India TBR"),
          ("Bekaert / Kiswire (steel cord)",4.6,"Steel-cord")]
    data.sort(key=lambda x:x[1])
    names=[d[0] for d in data]; vals=[d[1] for d in data]
    cmap={"TBR OEM":NAVY,"China TBR":RUST,"India TBR":AMBER,"Steel-cord":TEAL}
    cols=[cmap[d[2]] for d in data]
    fig,ax=plt.subplots(figsize=(10.5,6.2))
    ax.barh(names,vals,color=cols,zorder=3)
    ax.set_title(f"Leading assignees — 0°-steel-belt / crown & bead durability\n{TAG}")
    ax.set_xlabel("Relative activity in theme (indicative)")
    ax.grid(axis="x",color=LGREY,zorder=0)
    for s in ["top","right"]: ax.spines[s].set_visible(False)
    h=[plt.Rectangle((0,0),1,1,color=c) for c in cmap.values()]
    ax.legend(h,cmap.keys(),loc="lower right",frameon=False,title="Assignee type")
    fig.tight_layout(); fig.savefig(f"{OUT}/2_assignees.png"); plt.close(fig)

# ---------------------------------------------------------------- FIG 3 mechanism
def fig_mechanism():
    fig,ax=plt.subplots(figsize=(11.8,6.8)); ax.axis("off"); ax.set_xlim(0,12); ax.set_ylim(0,8)
    def box(x,y,w,h,txt,fc,tc="white",fs=9.3):
        ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle="round,pad=0.08,rounding_size=0.12",fc=fc,ec="white",lw=1.5))
        ax.text(x+w/2,y+h/2,txt,ha="center",va="center",color=tc,fontsize=fs,fontweight="bold")
    def arr(x1,y1,x2,y2,c=SLATE):
        ax.add_patch(FancyArrowPatch((x1,y1),(x2,y2),arrowstyle="-|>",mutation_scale=15,color=c,lw=2))
    ax.text(6,7.6,"TBR: how a crown 0°/circumferential STEEL belt reaches the BEAD",ha="center",fontsize=13,fontweight="bold",color=NAVY)
    box(0.3,5.5,3.0,1.4,"0° / circumferential\nSTEEL belt or protective\nbelt over working belts\n(4-belt all-steel package)",NAVY,fs=8.6)
    box(4.2,6.4,3.5,1.0,"Strong hoop restraint →\nflatter, stiffer crown",TEAL)
    box(4.2,5.0,3.5,1.0,"Belt-edge inter-ply shear\n(working ↔ 0° belt)",TEAL)
    box(4.2,3.6,3.5,1.0,"High inflation pressure +\nheavy axle load",TEAL)
    box(4.2,2.2,3.5,1.0,"Repeated retread / multi-\nlife casing fatigue",TEAL)
    box(8.6,4.2,3.1,1.5,"BEAD region:\n↑ carcass tension &\nstrain at TURN-UP END\n→ crack / ply loosening,\nbead-rim chafing",RUST,fs=8.8)
    box(8.6,2.2,3.1,1.0,"Belt-edge / shoulder\nseparation (adjacent)",AMBER,tc="#3a2a00")
    arr(3.3,6.1,4.2,6.9); arr(3.3,5.9,4.2,5.5)
    arr(3.3,5.8,4.2,4.1); arr(3.3,5.6,4.2,2.7)
    arr(7.7,6.9,8.6,5.4); arr(7.7,5.5,8.6,5.1)
    arr(7.7,4.1,8.6,4.8); arr(7.7,2.7,8.6,4.5)
    arr(7.7,5.3,8.6,2.7,c=AMBER)
    ax.text(6,0.7,"TBR specifics: the casing must survive 2–3 retread lives at high pressure, so the turn-up end &\n"
            "bead-rim seat — not the steel 0° belt itself — set the durability ceiling.",
            ha="center",fontsize=9.3,style="italic",color=NAVY,bbox=dict(boxstyle="round",fc=LGREY,ec="none"))
    fig.tight_layout(); fig.savefig(f"{OUT}/3_mechanism_map.png"); plt.close(fig)

# ---------------------------------------------------------------- FIG 4 levers
def fig_levers():
    groups={
      "STEEL CORD / COMPOUND":[("HE/HT steel belt cord, m+n twist",8),
                               ("Cooler 0°-belt coat compound",7),
                               ("Steel chafer / bead-reinf. cord",8),
                               ("6PPD-free / sustainable rubber",4)],
      "BELT STRUCTURE (crown)":[("0° steel belt width < working belts",8),
                                ("Belt-edge wedge / cushion gauge",9),
                                ("4-belt package & belt radius decouple",7),
                                ("Triangulated / split protective belt",5)],
      "BEAD / CARCASS GEOMETRY":[("Steel-cord bead reinforcing ply",9),
                                 ("L-shaped dual-modulus apex",8),
                                 ("Carcass turn-up height / arrangement",8),
                                 ("Flipper-chipper, gum strip at turn-up",7),
                                 ("Bead toe / 15° rim-seat & rim cushion",6)],
      "DESIGN / LIFECYCLE":[("FEA strain at turn-up end",7),
                            ("Retread / multi-life casing design",8),
                            ("Lightweight all-steel w/o bead penalty",6)],
    }
    fig,axes=plt.subplots(2,2,figsize=(12.5,8.4)); fam=[TEAL,NAVY,RUST,AMBER]
    for ax,(name,items),c in zip(axes.ravel(),groups.items(),fam):
        labs=[i[0] for i in items][::-1]; vals=[i[1] for i in items][::-1]
        ax.barh(labs,vals,color=c,zorder=3); ax.set_title(name,color=c)
        ax.set_xlim(0,10); ax.grid(axis="x",color=LGREY,zorder=0); ax.tick_params(labelsize=8.3)
        for s in ["top","right"]: ax.spines[s].set_visible(False)
    fig.suptitle(f"Engineering levers protecting BEAD durability with a 0° steel belt — {TAG}",
                 fontsize=12.5,fontweight="bold",color=NAVY,y=0.995)
    fig.text(0.5,0.005,"Bar length = relative frequency / emphasis across sampled TBR patents (indicative)",
             ha="center",fontsize=8,color=SLATE)
    fig.tight_layout(rect=[0,0.02,1,0.97]); fig.savefig(f"{OUT}/4_levers.png"); plt.close(fig)

# ---------------------------------------------------------------- FIG 5 opportunity
def fig_opportunity():
    items=[("Belt-edge wedge compound tuning",8.0,6.2,"O",650),
           ("Steel chafer / turn-up geom. rules",8.2,6.8,"O",750),
           ("L-shaped dual-modulus apex",7.5,7.0,"O",700),
           ("Retread-optimised multi-life casing",4.2,9.0,"O",1150),
           ("Lightweight all-steel, no bead penalty",3.6,7.8,"O",900),
           ("0° steel protective-belt edge design",5.2,7.0,"O",700),
           ("Sensorised casing / RFID for retread",2.2,6.2,"O",600),
           ("6PPD-free durable bead compounds",2.6,6.8,"O",650),
           ("Turn-up end fatigue at high pressure",5.8,8.8,"C",1050),
           ("Casing fatigue across 2-3 retreads",4.6,8.2,"C",900),
           ("Bead-rim chafing / 15° seat heat",5.4,7.2,"C",750),
           ("Heat build-up at high axle load",5.0,7.6,"C",800),
           ("Steel-cord cost & corrosion (bead/belt)",6.6,5.4,"C",600)]
    fig,ax=plt.subplots(figsize=(11.4,7.6))
    for lab,x,y,t,s in items:
        c=GREEN if t=="O" else RUST
        ax.scatter(x,y,s=s,color=c,alpha=0.55,edgecolor="white",lw=1.5,zorder=3)
        ax.text(x,y,lab,fontsize=7.7,ha="center",va="center",zorder=4)
    ax.axvline(5,color=LGREY,zorder=0); ax.axhline(6.9,color=LGREY,zorder=0)
    ax.set_xlim(1,10); ax.set_ylim(4.5,9.6)
    ax.set_xlabel("Technology maturity  →  (emerging ............ established)")
    ax.set_ylabel("Impact on TBR bead-durability problem  →")
    ax.set_title(f"Opportunity (green) vs Challenge (red) — {TAG}\nbubble size = activity")
    for s in ["top","right"]: ax.spines[s].set_visible(False)
    ax.text(2.5,9.35,"HIGH-IMPACT / EMERGING\n(white space)",color=GREEN,fontsize=9,fontweight="bold",ha="center",alpha=0.85)
    ax.text(8.3,9.35,"HIGH-IMPACT / MATURE\n(crowded)",color=NAVY,fontsize=9,fontweight="bold",ha="center",alpha=0.85)
    h=[plt.Line2D([0],[0],marker="o",color="w",markerfacecolor=GREEN,markersize=12,label="Opportunity"),
       plt.Line2D([0],[0],marker="o",color="w",markerfacecolor=RUST,markersize=12,label="Challenge")]
    ax.legend(handles=h,loc="lower left",frameon=False)
    fig.tight_layout(); fig.savefig(f"{OUT}/5_opportunity_matrix.png"); plt.close(fig)

# ---------------------------------------------------------------- FIG 6 direction
def fig_direction():
    fig,ax=plt.subplots(figsize=(11.8,4.6)); ax.axis("off"); ax.set_xlim(0,12); ax.set_ylim(0,5)
    phases=[(0.4,"2015-18","Bead geometry","Steel chafer, apex,\nturn-up arrangement",NAVY),
            (3.4,"2018-21","Belt-edge focus","0° steel-belt width &\nedge wedge/cushion",TEAL),
            (6.4,"2021-24","Casing co-design","Crown+bead together,\nFEA, retread life",AMBER),
            (9.4,"2024-26","Load & sustain.","High-load/EV axles,\nlightweight, 6PPD-free",RUST)]
    for x,yr,t,d,c in phases:
        ax.add_patch(FancyBboxPatch((x,1.4),2.4,2.2,boxstyle="round,pad=0.08,rounding_size=0.15",fc=c,ec="white",lw=2))
        ax.text(x+1.2,3.15,yr,ha="center",color="white",fontsize=10,fontweight="bold")
        ax.text(x+1.2,2.55,t,ha="center",color="white",fontsize=10,fontweight="bold")
        ax.text(x+1.2,1.9,d,ha="center",color="white",fontsize=8)
    for x in [2.8,5.8,8.8]:
        ax.add_patch(FancyArrowPatch((x,2.5),(x+0.6,2.5),arrowstyle="-|>",mutation_scale=20,color=SLATE,lw=2.5))
    ax.text(6,4.4,f"Direction of travel — {TAG}",ha="center",fontsize=12,fontweight="bold",color=NAVY)
    ax.text(6,0.7,"Retreadability (multi-life casing) is the constant that makes bead durability the central TBR constraint.",
            ha="center",fontsize=9.3,style="italic",color=SLATE)
    fig.tight_layout(); fig.savefig(f"{OUT}/6_direction.png"); plt.close(fig)

for f in [fig_trend,fig_assignees,fig_mechanism,fig_levers,fig_opportunity,fig_direction]:
    f(); print("done:",f.__name__)
print("TBR CHARTS WRITTEN ->",OUT)
