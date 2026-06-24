#!/usr/bin/env python3
"""
Conventional vs WAVY (undulating) zero-degree (0deg) belt — TBR focus.
Technical deck: process routes, mathematics, parameter influence, comparison,
belt layout, and design-window recommendation.

Grounding (sampled patents / papers):
  US3956546/US3900062/US4050973  - 'high soft-stretch' undulated belt tape, single-stage flat build
  US6659147 (crown reinforcement) - corrugation amplitude/wavelength 2-15%, shaping-vs-endurance
  US8079392                       - alternating straight/wavy reinforcement, A/lam 0.015-0.06
  US5054532                       - wavy/zigzag cord ply, A/lam 0.015-0.06
  US8720175                       - crimped flat-wire core, pitch 3-15 mm, amplitude 0.1-5 mm
  US20160152082A1                 - high-elongation (HE) steel cord, eps_break >= 5%
  Procedia Manuf. (cap-ply cord tension) - +unwind tension -> -6% LFV pk-pk, -4% RFV 1st harmonic
  Green-to-cure crown expansion typically 0.5-3%; belt-cord working elong. 1.0-2.5% @ 100-300 N
Values for ratings/heatmaps are engineering-judgement, indicative.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle, Ellipse
from matplotlib.lines import Line2D
import numpy as np

OUT="/home/user/Sri/patent_analysis"
plt.rcParams.update({"font.size":10,"axes.titlesize":12.5,"axes.titleweight":"bold","figure.dpi":135})
NAVY="#1f3b5c"; TEAL="#2a9d8f"; AMBER="#e9a227"; RUST="#c1492b"; SLATE="#5b6b7b"
LGREY="#e6eaed"; GREEN="#3a7d44"; PURP="#6a4c93"
trap=getattr(np,'trapezoid',getattr(np,'trapz',None))

def eps_exact(r):
    x=np.linspace(0,1,20000); yp=r*2*np.pi*np.cos(2*np.pi*x)
    return trap(np.sqrt(1+yp**2),x)-1

# ============================================================= FIG 1  MATH: elongation vs A/lambda
def fig_math_elong():
    r=np.linspace(0,0.16,400)
    eps=np.array([eps_exact(v) for v in r])*100
    approx=(np.pi**2*r**2)*100
    fig,ax=plt.subplots(figsize=(10.4,6.4))
    # TBR cure-lift band 0.5-3%
    ax.axhspan(0.5,3.0,color=AMBER,alpha=0.18,zorder=0)
    ax.text(0.004,3.05,"TBR green→cure crown expansion band (0.5–3%)  +  belt-cord working elong. 1.0–2.5%",
            fontsize=8.6,color="#7a5a00")
    ax.plot(r*100,eps,color=NAVY,lw=2.8,zorder=4,
            label=r"Exact: $\varepsilon=\frac{1}{\lambda}\!\int_0^\lambda\!\sqrt{1+y'^2}\,dx-1$")
    ax.plot(r*100,approx,"--",color=RUST,lw=1.8,zorder=3,
            label=r"Approx: $\varepsilon\approx\pi^2(A/\lambda)^2$")
    # patent windows
    ax.axvspan(1.5,6.0,color=TEAL,alpha=0.12,zorder=1)
    ax.axvspan(2.0,15.0,color=PURP,alpha=0.06,zorder=0)
    ax.text(3.75,9.2,"Wavy/zig-zag\npatents\nA/λ = 1.5–6%",ha="center",fontsize=8.4,color=TEAL,fontweight="bold")
    ax.text(11.5,11.5,"Crown-corrugation\npatents 2–15%\n(soft-stretch / flat build)",ha="center",fontsize=8.2,color=PURP)
    # TBR recommended window
    ax.axvspan(3.0,5.0,color=GREEN,alpha=0.22,zorder=2)
    ax.annotate("TBR sweet spot\nA/λ ≈ 3–5%  →  ε ≈ 0.9–2.4%",xy=(4,1.56),xytext=(6.4,3.0),
                arrowprops=dict(arrowstyle="->",color=GREEN,lw=1.6),color=GREEN,fontweight="bold",fontsize=9.2)
    ax.set_xlabel("Wave amplitude / wavelength ratio  A/λ   (%)")
    ax.set_ylabel("Available geometric elongation  ε  (%)  [cord straightens before going taut]")
    ax.set_title("MATH — how much 'free stretch' a wavy 0° cord provides vs its A/λ ratio")
    ax.set_xlim(0,16); ax.set_ylim(0,13); ax.grid(color=LGREY,zorder=0)
    for s in ["top","right"]: ax.spines[s].set_visible(False)
    ax.legend(loc="upper left",frameon=False,fontsize=10)
    fig.text(0.5,0.005,"Inset model: sinusoidal cord path y=A·sin(2πx/λ). The wave gives elongation WITHOUT straining the steel — "
             "it just straightens.",ha="center",fontsize=8,color=SLATE)
    fig.tight_layout(rect=[0,0.02,1,1]); fig.savefig(f"{OUT}/wavyZD_1_math_elongation.png"); plt.close(fig)

# ============================================================= FIG 2  Load-elongation bilinear
def fig_load_elong():
    fig,ax=plt.subplots(figsize=(10.4,6.4))
    e=np.linspace(0,3.2,400)
    # conventional straight steel: stiff linear from 0
    conv=np.clip(e*900,0,None)
    # wavy steel: low slope until knee (eps_geo~1.6%), then steel slope
    knee=1.6; low=120; high=900
    wavy=np.where(e<knee, low*e, low*knee+high*(e-knee))
    # HE steel cord: smooth concave (structural elong) then steel
    he=320*(1-np.exp(-e/1.1))*np.where(e<1.6,1,1)+np.where(e>1.6,650*(e-1.6),0)
    ax.axvspan(0.5,3.0,color=AMBER,alpha=0.16,zorder=0)
    ax.text(0.55,2350,"green→cure /\nshaping strain\n(0.5–3%)",fontsize=8.2,color="#7a5a00")
    ax.plot(e,conv,color=RUST,lw=2.8,label="Conventional straight 0° steel — stiff from ε=0")
    ax.plot(e,wavy,color=NAVY,lw=2.8,label="Wavy 0° steel — bilinear (knee at ε_geo)")
    ax.plot(e,he,color=TEAL,lw=2.8,ls=(0,(4,2)),label="HE steel cord — smooth low→high modulus")
    ax.scatter([knee],[low*knee],s=60,color=NAVY,zorder=5)
    ax.annotate("knee = ε_geo\n(wave straightened)",xy=(knee,low*knee),xytext=(2.0,520),
                arrowprops=dict(arrowstyle="->",color=NAVY),fontsize=8.4,color=NAVY)
    ax.annotate("conventional cord sees\nHUGE force during shaping\n→ can't build flat / over-tension",
                xy=(1.3,1170),xytext=(0.05,1650),arrowprops=dict(arrowstyle="->",color=RUST),
                fontsize=8.4,color=RUST,fontweight="bold")
    ax.annotate("wavy/HE stay SOFT through\nthe lift band, then go STIFF\nfor service rigidity",
                xy=(2.6,high*(2.6-knee)+low*knee),xytext=(1.45,150),
                arrowprops=dict(arrowstyle="->",color=NAVY),fontsize=8.4,color=NAVY,fontweight="bold")
    ax.set_xlabel("Circumferential strain ε (%)"); ax.set_ylabel("Cord tension (relative)")
    ax.set_title("MATH — the 'step / bilinear modulus' that makes wavy & HE 0° belts buildable AND durable")
    ax.set_xlim(0,3.2); ax.set_ylim(0,2700); ax.grid(color=LGREY,zorder=0)
    for s in ["top","right"]: ax.spines[s].set_visible(False)
    ax.legend(loc="upper left",frameon=False,fontsize=9.4)
    fig.tight_layout(); fig.savefig(f"{OUT}/wavyZD_2_load_elongation.png"); plt.close(fig)

# ============================================================= FIG 3  Process routes infographic
def fig_process():
    fig,ax=plt.subplots(figsize=(12.6,7.4)); ax.axis("off"); ax.set_xlim(0,12.6); ax.set_ylim(0,8)
    ax.text(6.3,7.7,"PROCESS — four ways to put 'waviness' into a 0° belt (and how parameters are set)",
            ha="center",fontsize=13.5,fontweight="bold",color=NAVY)
    def panel(x,title,c):
        ax.add_patch(FancyBboxPatch((x,0.5),2.85,6.6,boxstyle="round,pad=0.05,rounding_size=0.12",
                     fc="white",ec=c,lw=2.2))
        ax.text(x+1.42,6.78,title,ha="center",color="white",fontsize=8.7,fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.28",fc=c,ec="none"))
    xs=[0.3,3.4,6.5,9.6]
    cols=[TEAL,NAVY,AMBER,PURP]
    titles=["1 · PRE-CRIMP CORD","2 · OSCILLATING HEAD","3 · SOFT-STRETCH TAPE","4 · HE STEEL CORD"]
    for x,t,c in zip(xs,titles,cols): panel(x,t,c)
    # --- panel 1: crimping rollers + waveform
    x=xs[0]+1.42
    ax.add_patch(plt.Circle((x-0.55,5.6),0.28,fc=TEAL,ec="k",lw=0.6))
    ax.add_patch(plt.Circle((x+0.55,5.6),0.28,fc=TEAL,ec="k",lw=0.6))
    ax.text(x,6.05,"toothed/gear rolls",ha="center",fontsize=7.2)
    xx=np.linspace(x-1.05,x+1.05,200); ax.plot(xx,5.0+0.12*np.sin((xx-x)*16),color="k",lw=1.4)
    ax.text(x,4.5,"round wire →\ncrimp → flatten\n(US8720175)",ha="center",fontsize=7.6)
    ax.text(x,3.45,"SETS:\n• amplitude A (roll depth)\n• wavelength λ (tooth pitch)\nout-of-plane / in-plane",
            ha="center",fontsize=7.4,color=TEAL)
    ax.text(x,1.7,"pitch 3–15 mm\nampl. 0.1–5 mm",ha="center",fontsize=7.6,fontweight="bold")
    # --- panel 2: oscillating head
    x=xs[1]+1.42
    ax.add_patch(FancyBboxPatch((x-0.4,5.5),0.8,0.45,boxstyle="round,pad=0.02",fc=NAVY,ec="k"))
    ax.add_patch(FancyArrowPatch((x-0.9,5.72),(x+0.9,5.72),arrowstyle="<->",mutation_scale=12,color=RUST,lw=1.8))
    ax.text(x,6.1,"servo guide traverses ↔",ha="center",fontsize=7.2)
    xx=np.linspace(x-1.05,x+1.05,300); ax.plot(xx,4.85+0.18*np.sin((xx-x)*10),color=NAVY,lw=1.6)
    ax.annotate("",xy=(x+1.05,4.85),xytext=(x-1.05,4.85),arrowprops=dict(arrowstyle="->",color=SLATE,lw=0.8))
    ax.text(x,4.25,"cord laid in-plane\nas it spirals on drum",ha="center",fontsize=7.6)
    ax.text(x,3.25,"SETS:\n• A = traverse stroke\n• λ = traverse freq ÷ feed\n• best uniformity control",
            ha="center",fontsize=7.4,color=NAVY)
    ax.text(x,1.7,"decouples wave\nfrom metallurgy",ha="center",fontsize=7.6,fontweight="bold")
    # --- panel 3: undulated tape on-end
    x=xs[2]+1.42
    yy=np.linspace(4.4,5.9,200); ax.plot(x-0.5+0.18*np.sin(yy*12),yy,color=AMBER,lw=1.5)
    ax.plot(x+0.0+0.18*np.sin(yy*12+1.5),yy,color=AMBER,lw=1.5)
    ax.plot(x+0.5+0.18*np.sin(yy*12+3.0),yy,color=AMBER,lw=1.5)
    ax.text(x,6.1,"undulations 'on end'",ha="center",fontsize=7.2)
    ax.text(x,4.05,"sinus. cords + frangible\nstitch, wound helically\n(US3956546/US3900062)",
            ha="center",fontsize=7.4)
    ax.text(x,2.95,"SETS:\n• big soft-stretch\n• enables SINGLE-STAGE\n  flat-band building",
            ha="center",fontsize=7.4,color="#7a5a00")
    ax.text(x,1.55,"ampl ~4.8 mm\npitch ~7.9 mm",ha="center",fontsize=7.6,fontweight="bold")
    # --- panel 4: HE cord
    x=xs[3]+1.42
    th=np.linspace(0,6*np.pi,300); ax.plot(x+0.22*np.sin(th),4.5+(th/(6*np.pi))*1.4,color=PURP,lw=1.6)
    ax.text(x,6.1,"open / high-helix\nfilament construction",ha="center",fontsize=7.2)
    ax.text(x,4.05,"structural elongation\nin the cord itself\n(US2016/0152082)",ha="center",fontsize=7.4)
    ax.text(x,2.95,"SETS:\n• ε_break ≥ 5%\n• drop-in: no oscillation\n• best uniformity",ha="center",fontsize=7.4,color=PURP)
    ax.text(x,1.55,"no geometric wave →\nlower gauge",ha="center",fontsize=7.6,fontweight="bold")
    # common bottom band
    ax.add_patch(FancyBboxPatch((0.3,0.05),11.95,0.55,boxstyle="round,pad=0.02",fc=LGREY,ec="none"))
    ax.text(6.3,0.32,"Shared lever — WINDING / UNWIND TENSION: higher tension partially pulls the wave out "
            "(↓ available ε, ↑ residual hoop tension) but improves uniformity (−6% LFV pk-pk, −4% RFV 1st harmonic). "
            "Use closed-loop tension control.",ha="center",fontsize=8.2,color=NAVY,style="italic")
    fig.savefig(f"{OUT}/wavyZD_3_process_routes.png"); plt.close(fig)

# ============================================================= FIG 4  Parameter influence heatmap
def fig_params():
    # All columns framed so MORE = BETTER, so green = favourable everywhere.
    levers=["↑ A/λ ratio","↑ Winding tension","↑ Traverse speed (→ shorter λ)","↑ Ends per 50 mm",
            "Wavy → HE-cord swap","Edge-cover only (vs full 0°)"]
    outcomes=["Cure-lift\nheadroom","Service\nrigidity\n(endurance)","Belt-edge\nshear relief",
              "Uniformity\n(RFV/LFV)","Residual-\ntension\ncontrol","Lightness\n(low gauge)","Process\neconomy"]
    F=np.array([
        [ 3, -1,  2, -1,  1, -2, -1],   # ↑ A/lambda
        [-2,  1,  0,  2, -2,  0,  0],   # ↑ winding tension
        [ 1,  1,  1,  1,  0,  0,  0],   # ↑ traverse speed (shorter lambda)
        [ 0,  2,  1,  1,  1, -1, -1],   # ↑ ends/50mm
        [ 1,  1,  1,  2,  1,  2,  1],   # wavy -> HE cord
        [ 0, -1,  2,  1,  1,  2,  1],   # edge-cover only vs full
    ],dtype=float)
    fig,ax=plt.subplots(figsize=(11.8,6.3))
    im=ax.imshow(F,cmap="RdYlGn",vmin=-3,vmax=3,aspect="auto")
    ax.set_xticks(range(len(outcomes))); ax.set_xticklabels(outcomes,fontsize=8.6)
    ax.set_yticks(range(len(levers))); ax.set_yticklabels(levers,fontsize=9.4)
    for i in range(F.shape[0]):
        for j in range(F.shape[1]):
            v=F[i,j]
            s={3:"+++",2:"++",1:"+",0:"o",-1:"–",-2:"– –",-3:"– – –"}.get(int(v),"o")
            ax.text(j,i,s,ha="center",va="center",fontsize=12.5,
                    color="white" if abs(v)>=2 else "black",fontweight="bold")
    ax.set_title("PARAMETER INFLUENCE — favourability of each process lever (green = better, all columns 'more = better')")
    cb=fig.colorbar(im,ax=ax,shrink=0.8); cb.set_label("penalty  ←      →  benefit",fontsize=8.6)
    fig.text(0.5,0.01,"Cell = net favourability of that lever on that outcome (+ benefit / – penalty). "
             "Columns are framed so higher is always better. Indicative engineering judgement.",
             ha="center",fontsize=8,color=SLATE)
    fig.tight_layout(rect=[0,0.03,1,1]); fig.savefig(f"{OUT}/wavyZD_4_parameters.png"); plt.close(fig)

# ============================================================= FIG 5  Radar comparison
def fig_radar():
    metrics=["Cure-lift\naccommodation","Single-stage\nflat build","High-speed\ndurability",
             "Belt-edge /\nshoulder endurance","Uniformity\n(RFV/LFV)","Low rolling\nresistance",
             "Low weight\n/ gauge","Process\nsimplicity","Low cost","Retread /\nmulti-life"]
    conv=[1,1,4,3,4.5,4,4,5,5,3]
    wavy=[5,5,4,4.5,3,3,3,2.5,3,4]
    he  =[4.5,4,4,4,4,4,4,4,3,4]
    N=len(metrics); ang=np.linspace(0,2*np.pi,N,endpoint=False).tolist(); ang+=ang[:1]
    fig,ax=plt.subplots(figsize=(9.2,8.4),subplot_kw=dict(polar=True))
    for vals,c,lab in [(conv,RUST,"Conventional straight 0° steel"),
                       (wavy,NAVY,"Wavy / undulating 0° steel"),
                       (he,TEAL,"HE steel cord 0°")]:
        v=vals+vals[:1]; ax.plot(ang,v,color=c,lw=2.4,label=lab); ax.fill(ang,v,color=c,alpha=0.12)
    ax.set_xticks(ang[:-1]); ax.set_xticklabels(metrics,fontsize=8.6)
    ax.set_ylim(0,5); ax.set_yticks([1,2,3,4,5]); ax.set_yticklabels(["1","2","3","4","5"],fontsize=7.5)
    ax.set_title("COMPARISON — conventional vs wavy vs HE-cord 0° belt (5 = better)",y=1.09,fontsize=12.5)
    ax.legend(loc="upper center",bbox_to_anchor=(0.5,-0.06),ncol=1,frameon=False,fontsize=9.6)
    fig.tight_layout(); fig.savefig(f"{OUT}/wavyZD_5_radar.png",bbox_inches="tight"); plt.close(fig)

# ============================================================= FIG 6  TBR belt layout schematic
def fig_layout():
    fig,(a1,a2)=plt.subplots(1,2,figsize=(12.6,5.8));
    for ax in (a1,a2): ax.axis("off"); ax.set_xlim(0,10); ax.set_ylim(0,7)
    def package(ax,wavy):
        # tread
        ax.add_patch(FancyBboxPatch((1,5.2),8,0.9,boxstyle="round,pad=0.02",fc="#3b3b3b",ec="none"))
        ax.text(5,5.65,"TREAD",ha="center",color="white",fontsize=9,fontweight="bold")
        # belts B4 protective, B2/B3 working, B1 transition
        def belt(y,x0,x1,c,lab,angle=None):
            ax.add_patch(Rectangle((x0,y),x1-x0,0.34,fc=c,ec="white",lw=1))
            ax.text(x1+0.15,y+0.17,lab,va="center",fontsize=8,color=c if c!=AMBER else "#7a5a00")
        belt(4.75,1.4,8.6,SLATE,"B4 protective")
        belt(4.30,1.1,8.9,NAVY,"B2/B3 working (~18–22°)")
        belt(3.95,1.1,8.9,NAVY,"")
        belt(3.40,2.0,8.0,TEAL,"B1 transition (narrow)")
        # 0-degree layer (edge cover at shoulders) - draw cords
        for (xs,xe) in [(1.2,3.2),(6.8,8.8)]:
            xx=np.linspace(xs,xe,200)
            yy=5.02+(0.07*np.sin((xx-xs)*14) if wavy else 0*xx)
            ax.plot(xx,yy,color=RUST,lw=1.6)
        ax.text(2.2,5.28,"0° edge cover",ha="center",fontsize=7.6,color=RUST,fontweight="bold")
        ax.text(7.8,5.28,"0° edge cover",ha="center",fontsize=7.6,color=RUST,fontweight="bold")
        # optional full 0 cover dashed
        xx=np.linspace(3.4,6.6,200); yy=5.02+(0.07*np.sin((xx-3.4)*14) if wavy else 0*xx)
        ax.plot(xx,yy,color=RUST,lw=1.2,ls=":")
        ax.text(5,5.28,"(opt. full 0° cover)",ha="center",fontsize=7,color=RUST)
        # carcass
        ax.add_patch(FancyArrowPatch((1.0,3.3),(1.0,1.0),arrowstyle="-",color=GREEN,lw=2))
        ax.add_patch(FancyArrowPatch((9.0,3.3),(9.0,1.0),arrowstyle="-",color=GREEN,lw=2))
        ax.text(0.7,2.1,"carcass",rotation=90,va="center",fontsize=8,color=GREEN)
        # bead
        for bx in (1.0,9.0):
            ax.add_patch(plt.Circle((bx,0.8),0.22,fc=AMBER,ec="k"))
        ax.text(5,0.55,"BEAD / turn-up (durability-limiting site)",ha="center",fontsize=8,color="#7a5a00")
        # shoulder stress arrows
        for sx in (3.0,7.0):
            ax.add_patch(FancyArrowPatch((sx,4.9),(sx,3.4),arrowstyle="-|>",color=RUST,lw=1.4,mutation_scale=10))
        ax.text(5,2.7,"belt-edge / shoulder shear → routed to bead",ha="center",fontsize=7.6,color=RUST,style="italic")
    package(a1,False); a1.set_title("Conventional straight 0° belt",color=RUST)
    package(a2,True);  a2.set_title("Wavy 0° belt (compliant edge)",color=NAVY)
    a2.text(5,1.7,"wave relieves abrupt edge stiffness step\n→ lower shoulder shear & cure-lift stress",
            ha="center",fontsize=8,color=NAVY,bbox=dict(boxstyle="round",fc=LGREY,ec="none"))
    fig.suptitle("LAYOUT — where the 0° belt sits in a TBR all-steel package (crown section, half-width)",
                 fontsize=12.5,fontweight="bold",color=NAVY,y=1.0)
    fig.tight_layout(rect=[0,0,1,0.96]); fig.savefig(f"{OUT}/wavyZD_6_layout.png"); plt.close(fig)

# ============================================================= FIG 7  Recommendation one-pager
def fig_reco():
    fig,ax=plt.subplots(figsize=(11.8,7.6)); ax.axis("off"); ax.set_xlim(0,12); ax.set_ylim(0,10)
    ax.text(6,9.6,"RECOMMENDATIONS — wavy 0° belt for TBR (differentiation playbook)",
            ha="center",fontsize=13.5,fontweight="bold",color=NAVY)
    cards=[
        ("DESIGN WINDOW",TEAL,
         "• Target A/λ ≈ 3–5% → ε_avail ≈ 0.9–2.4%\n"
         "• Covers TBR cure-lift (0.5–3%) + working\n  elongation spec (1.0–2.5% @ 100–300 N)\n"
         "• Avoid >6% (gauge/RR/soft) & <2% (build defects)\n"
         "• Ends 17–30 / 50 mm; pitch 3–15 mm"),
        ("PROCESS CHOICE",NAVY,
         "• Servo OSCILLATING applicator = best control\n  (decouples wave from metallurgy, good uniformity)\n"
         "• Or HE steel cord = drop-in, lowest gauge\n"
         "• Closed-loop WINDING TENSION (uniformity vs\n  residual hoop trade-off: −6% LFV, −4% RFV)\n"
         "• Consider hybrid: mild wave + HE cord"),
        ("WHERE TO USE",AMBER,
         "• Primary: 0° EDGE cover at shoulders → cut\n  belt-edge shear & shoulder separation\n"
         "• Full 0° cover only for high-speed coach/bus\n"
         "• Keeps crown centre from over-stiffening\n  (avoids uneven wear) → protects bead/turn-up"),
        ("DIFFERENTIATE / IP",RUST,
         "• Amplitude-GRADED 0° (more wave at shoulder,\n  less at centre) = tailored crown stiffness (white space)\n"
         "• Tune bilinear 'knee' just past cure-lift →\n  early service rigidity, no build penalty\n"
         "• Couple to retread / multi-life casing model\n"
         "• 6PPD-free coat compound for the 0° layer"),
    ]
    pos=[(0.4,5.0),(6.2,5.0),(0.4,0.5),(6.2,0.5)]
    for (t,c,body),(x,y) in zip(cards,pos):
        ax.add_patch(FancyBboxPatch((x,y),5.4,4.0,boxstyle="round,pad=0.05,rounding_size=0.12",fc="white",ec=c,lw=2.4))
        ax.text(x+0.25,y+3.6,t,fontsize=11,fontweight="bold",color="white",
                bbox=dict(boxstyle="round,pad=0.3",fc=c,ec="none"))
        ax.text(x+0.25,y+1.7,body,fontsize=9.0,color="#222",va="center")
    fig.tight_layout(); fig.savefig(f"{OUT}/wavyZD_7_recommendations.png"); plt.close(fig)

for f in [fig_math_elong,fig_load_elong,fig_process,fig_params,fig_radar,fig_layout,fig_reco]:
    f(); print("done:",f.__name__)
print("WAVY-ZD DECK WRITTEN ->",OUT)
