"""
Builds the Kaggle notebook: PatentBERT_ZeroDegree_PCR.ipynb

Run:  python scripts/build_notebook.py
Output: notebooks/PatentBERT_ZeroDegree_PCR.ipynb
"""
import nbformat as nbf
from pathlib import Path

nb = nbf.v4.new_notebook()
cells = []
def md(t): cells.append(nbf.v4.new_markdown_cell(t))
def code(t): cells.append(nbf.v4.new_code_cell(t))

# ───────────────────────────────────────────────────────── intro
md("""# Hybrid Zero-Degree Cords in PCR Tires — PatentBERT Technology Map

End-to-end analysis of a PatSeer zero-degree-belt patent export, using
**PatentBERT** (`anferico/bert-for-patents`) embeddings and a **UMAP technology
map**.

**Pipeline:** data acquisition → cleaning & imputation → standardization →
PCR screening → passage building → PatentBERT embeddings → UMAP + HDBSCAN
clustering → **technologies UMAP** → cluster characterization → category &
material analytics → exports.

### How to run on Kaggle
1. **Add your data:** *Add Input → Upload* the PatSeer `.xlsx` (or create a
   Dataset from it). The notebook auto-discovers any `.xlsx`/`.csv` under
   `/kaggle/input/`.
2. **Enable internet:** *Settings → Internet → On* (needed to download PatentBERT).
3. **Use a GPU:** *Settings → Accelerator → GPU* (PatentBERT is BERT-large).
4. Run all cells.
""")

# ───────────────────────────────────────────────────────── install
md("## 0. Setup")
code("""# Kaggle ships torch / transformers / sklearn / pandas / matplotlib.
# We add UMAP, HDBSCAN, Plotly and the Excel reader.
!pip install -q umap-learn hdbscan plotly openpyxl
""")

code(r"""import os, re, glob, warnings, math
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

import torch
from transformers import AutoTokenizer, AutoModel
import umap
import hdbscan
from sklearn.feature_extraction.text import TfidfVectorizer, CountVectorizer

plt.rcParams.update({"figure.dpi": 120, "font.size": 10, "axes.grid": True,
                     "grid.alpha": 0.25, "axes.spines.top": False,
                     "axes.spines.right": False})
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
print("Torch", torch.__version__, "| device:", DEVICE)
""")

# ───────────────────────────────────────────────────────── config
md("## 1. Configuration")
code(r"""CONFIG = dict(
    model_name      = "anferico/bert-for-patents",  # PatentBERT (BERT-large)
    max_length      = 256,        # tokens per passage
    batch_size      = 64,         # lower to 32 if GPU OOM
    passage_chars   = 1200,       # max chars per passage chunk
    # UMAP
    umap_neighbors  = 15,
    umap_min_dist   = 0.0,
    umap_clust_dims = 10,         # dims for the clustering UMAP
    # HDBSCAN
    min_cluster_size = 60,        # raise for fewer/larger tech themes
    min_samples      = 10,
    random_state     = 42,
    apply_year_filter = False,    # True -> keep only >= year_start
    year_start       = 2015,
)
CONFIG
""")

# ───────────────────────────────────────────────────────── domain
md("""## 2. Domain knowledge (editable)

The quality of screening and labeling depends far more on this tire-domain
vocabulary than on the model. Edit freely.""")
code(r'''SEGMENT_LEXICON = {
    "PCR": ["passenger car","passenger vehicle","passenger tire","passenger tyre",
            "pcr","passenger car radial","high performance tire","uhp",
            "ultra high performance","summer tire","sport tire","sedan",
            "speed rating","speed symbol","v-rated","w-rated","y-rated",
            "h-rated","high speed durability"],
    "TBR": ["truck","bus","commercial vehicle","truck and bus","tbr","heavy duty",
            "load index","regroovable","steer axle","drive axle"],
    "OTR": ["off-the-road","off the road","otr","earthmover","mining tire",
            "agricultural","construction equipment","loader"],
    "AIRCRAFT": ["aircraft","aeroplane","airplane","aviation tire","landing"],
    "MOTORCYCLE": ["motorcycle","motorbike","two-wheel","two wheeler","scooter"],
}
ZERO_DEGREE_LEXICON = ["zero degree","zero-degree","0 degree","0-degree","0°",
    "cap ply","capply","cap-ply","overlay","jointless band","jointless cap",
    "jlb","spirally wound","spiral wound","circumferential belt","band ply",
    "reinforcing band","restricting layer","nylon overlay"]
HYBRID_LEXICON = ["hybrid cord","hybrid tire cord","hybrid yarn","composite cord",
    "aramid nylon","aramid-nylon","nylon aramid","co-twisted","cabled cord",
    "two-component cord","dual modulus","different modulus"]
MATERIALS = {
    "aramid":      r"aramid|kevlar|twaron|technora",
    "nylon66":     r"nylon 6\.6|nylon 66|nylon6\.6|pa 6\.6|pa66|polyamide 6\.6|polyamide 66",
    "nylon6":      r"nylon 6\b|polyamide 6\b|pa 6\b",
    "PET":         r"polyethylene terephthalate|polyester|\bpet\b",
    "PEN":         r"polyethylene naphthalate|\bpen\b",
    "rayon":       r"rayon",
    "PA46":        r"pa4\.6|pa 4\.6|pa46|polyamide 4\.6",
    "carbon_fiber":r"carbon fiber|carbon fibre",
    "resin_coated":r"resin.{0,5}coated|coated cord|thermoplastic",
}
CATEGORY_FRAMEWORK = {
    "technologies": ["aramid","nylon","polyester","pet","pen","rayon","twist",
        "denier","dtex","ends per","epdm","rfl","resorcinol","dip","epoxy",
        "single layer","dual layer","filament","cabled","hybrid"],
    "advantages": ["high speed durability","centrifugal","growth","uniformity",
        "rfv","flat spot","handling","steering","weight reduction","lightweight",
        "rolling resistance","fuel economy","ride comfort","noise","durability"],
    "challenges": ["adhesion","delamination","separation","compression fatigue",
        "brittle","stiffness","cost","shrinkage mismatch","thermal shrinkage",
        "dimensional stability","tension control","creep","cord breakage"],
    "design_guidelines": ["ends per dm","epdm","cord density","winding tension",
        "modulus","elongation","shrinkage force","dry heat shrinkage","gauge",
        "ply count","winding pitch","preferably","in the range of"],
    "process": ["dipping","heat setting","normalizing","stretching","winding",
        "calendering","extrusion","splice","creel","vulcanization","curing"],
    "tradeoffs": ["trade-off","tradeoff","balance between","compromise",
        "at the expense of","while maintaining","without sacrificing","however"],
}
''')

# ───────────────────────────────────────────────────────── acquisition
md("## 3. Data acquisition")
code(r"""def find_export():
    paths = (glob.glob("/kaggle/input/**/*.xlsx", recursive=True)
             + glob.glob("/kaggle/input/**/*.csv",  recursive=True))
    # local fallback if running outside Kaggle
    paths += glob.glob("*.xlsx") + glob.glob("data/raw/*.xlsx")
    if not paths:
        raise FileNotFoundError("Upload the PatSeer export under /kaggle/input/")
    print("Found:", paths[0]); return paths[0]

SRC = find_export()
raw = pd.read_excel(SRC, sheet_name=0) if SRC.endswith("xlsx") else pd.read_csv(SRC)
print("Raw shape:", raw.shape)
raw.head(3)
""")

# ───────────────────────────────────────────────────────── standardize cols
md("""## 4. Standardization — map vendor columns to a canonical schema

PatSeer / Lens / Espacenet name columns differently; we map them onto one schema.""")
code(r"""ALIASES = {
    "doc_id": ["record number","publication number","publication","patent number","id"],
    "title": ["title","invention title"],
    "abstract": ["abstract"],
    "claims": ["claims","claim"],
    "description": ["description","detailed description","full text"],
    "ai_advantages": ["advantages (ai sum.)","advantages"],
    "ai_method": ["method used (ai sum.)","method used","method"],
    "ai_problem": ["problem being solved (ai sum.)","problem being solved","problem"],
    "assignee": ["assignee","assignees","applicant","applicants"],
    "pub_date": ["publication/issue date","publication date","publication/ issue date"],
    "priority_year": ["priority year"],
    "cpc": ["cpc","cpc classifications"],
    "legal_status": ["legal status (dead/alive)","legal status"],
    "country": ["publication country","assignee country"],
}
rev = {a: k for k, al in ALIASES.items() for a in al}
df = pd.DataFrame()
for col in raw.columns:
    key = rev.get(str(col).strip().lower())
    if key and key not in df.columns:
        df[key] = raw[col]
for k in ALIASES:
    if k not in df.columns: df[k] = ""
print("Standardized columns:", list(df.columns))
df.shape
""")

# ───────────────────────────────────────────────────────── clean + impute
md("""## 5. Cleaning & imputation

- Blank literal `None`/`nan`/`null` tokens that PatSeer writes for empty fields.
- **Impute the analysis year**: use *Priority Year*; if missing/zero, fall back to
  the year parsed from *Publication Date*.
- Standardize the assignee (drop country tags, take the primary applicant).
- Normalize legal status.""")
code(r"""NULLS = {"none","nan","null","n/a","na",""}
def blank_nulls(s):
    # fillna("") first: pandas' new `str` dtype keeps missing as float NaN,
    # which .astype(str) does NOT coerce — so empty AI-summary cells would stay
    # float and break the text join later.
    s = s.fillna("").astype(str).str.strip()
    return s.where(~s.str.lower().isin(NULLS), "")

for c in ["title","abstract","claims","description","ai_advantages","ai_method",
          "ai_problem","assignee","cpc","legal_status","country"]:
    df[c] = blank_nulls(df[c])

def parse_year(v):
    m = re.search(r"(19|20)\d{2}", str(v));  return int(m.group(0)) if m else np.nan

py  = df["priority_year"].map(parse_year)
pdy = df["pub_date"].map(parse_year)
df["year"] = py.fillna(pdy)
n_imputed = int(py.isna().sum() - df["year"].isna().sum())
print(f"Year present from priority: {int(py.notna().sum())}; "
      f"imputed from publication date: {n_imputed}; "
      f"still missing: {int(df['year'].isna().sum())}")

def norm_assignee(name):
    s = str(name).split(";")[0]
    s = re.sub(r"\([A-Z]{2}\)", "", s)
    return re.sub(r"\s+", " ", s).strip(" .,").upper()
df["assignee_norm"] = df["assignee"].map(norm_assignee)
df["legal_status"]  = df["legal_status"].str.upper().str.strip()
df[["doc_id","assignee_norm","year","legal_status"]].head()
""")

md("### Missingness overview (post-imputation)")
code(r"""miss = (df.replace("", np.nan).isna().mean()*100).round(1).sort_values(ascending=False)
ax = miss[miss>0].plot(kind="barh", figsize=(7,5), color="#4C72B0")
ax.set_title("% missing per field (after imputation)"); ax.set_xlabel("% missing")
plt.tight_layout(); plt.show()
df["full_text"] = (df[["title","abstract","claims","description",
                       "ai_method","ai_advantages","ai_problem"]]
                   .fillna("").astype(str).agg(" ".join, axis=1)).str.strip()
""")

# ───────────────────────────────────────────────────────── screening
md("""## 6. PCR screening & tagging

Keep passenger-car-relevant records: a patent stays unless it has a clear
truck/off-road/aircraft/motorcycle signal **and no passenger signal at all**.
We also tag zero-degree, hybrid and material flags.""")
code(r"""def count_hits(text, terms):
    t = text.lower(); return sum(t.count(x) for x in terms)

blob = df["full_text"].str.lower()
for seg, terms in SEGMENT_LEXICON.items():
    df[f"seg_{seg}"] = blob.map(lambda t, tr=terms: count_hits(t, tr))
df["zero_degree_hits"] = blob.map(lambda t: count_hits(t, ZERO_DEGREE_LEXICON))
df["hybrid_hits"]      = blob.map(lambda t: count_hits(t, HYBRID_LEXICON))
for m, pat in MATERIALS.items():
    df[f"mat_{m}"] = blob.str.contains(pat, regex=True)

def segment(r):
    if r["seg_PCR"] > 0: return "PCR"
    off = {s: r[f"seg_{s}"] for s in SEGMENT_LEXICON if s != "PCR"}
    return "UNKNOWN" if max(off.values()) == 0 else max(off, key=off.get)
df["segment"] = df.apply(segment, axis=1)

keep = ~df["segment"].isin(["TBR","OTR","AIRCRAFT","MOTORCYCLE"])
if CONFIG["apply_year_filter"]:
    keep &= df["year"].fillna(0).ge(CONFIG["year_start"])
pcr = df[keep].reset_index(drop=True)
print(f"{len(df)} -> {len(pcr)} PCR records | hybrid: {int((pcr['hybrid_hits']>0).sum())} "
      f"| alive: {int((pcr['legal_status']=='ALIVE').sum())}")
print(df["segment"].value_counts().to_string())
""")

# ───────────────────────────────────────────────────────── EDA charts
md("## 7. Exploratory charts")
code(r"""fig, ax = plt.subplots(1, 2, figsize=(13,4))
yr = pcr["year"].dropna().astype(int); yr = yr[yr>=1995]
yr.value_counts().sort_index().plot(ax=ax[0], marker="o", color="#C44E52")
ax[0].set_title("PCR zero-degree filings per year"); ax[0].set_xlabel("priority year")
pcr["assignee_norm"].replace("", np.nan).dropna().value_counts().head(12)\
   .iloc[::-1].plot(kind="barh", ax=ax[1], color="#4C72B0")
ax[1].set_title("Top assignees"); plt.tight_layout(); plt.show()
""")
code(r"""fig, ax = plt.subplots(1, 2, figsize=(13,4))
mat_counts = {m: int(pcr[f"mat_{m}"].sum()) for m in MATERIALS}
pd.Series(mat_counts).sort_values().plot(kind="barh", ax=ax[0], color="#55A868")
ax[0].set_title("Material mentions (patent count)")
pcr["legal_status"].replace("", "UNKNOWN").value_counts()\
   .plot(kind="bar", ax=ax[1], color=["#4C72B0","#C44E52","#999999"])
ax[1].set_title("Legal status"); plt.tight_layout(); plt.show()
""")

# ───────────────────────────────────────────────────────── passages
md("""## 8. Build passages

A patent is too long to embed as one vector, so we split into passages. High-signal
sections (abstract, claims, AI summaries) are kept in full; the verbose description
is capped. We carry provenance and dedup near-identical text.""")
code(r"""SECTION_CAPS = {"abstract":None,"claims":4,"ai_advantages":None,
                "ai_method":None,"ai_problem":None,"description":3}
def split_text(text, max_chars):
    text = re.sub(r"\s+"," ", str(text)).strip()
    if not text: return []
    sents = re.split(r"(?<=[.;])\s+", text)
    chunks, cur = [], ""
    for s in sents:
        if len(cur)+len(s)+1 <= max_chars: cur = (cur+" "+s).strip()
        else:
            if cur: chunks.append(cur)
            cur = s[:max_chars]
    if cur: chunks.append(cur)
    return chunks

rows = []
for _, r in pcr.iterrows():
    for sec, cap in SECTION_CAPS.items():
        ch = split_text(r.get(sec,""), CONFIG["passage_chars"])
        if cap: ch = ch[:cap]
        for c in ch:
            if len(c) < 40: continue
            rows.append(dict(doc_id=r["doc_id"], assignee=r["assignee_norm"],
                             year=r["year"], segment=r["segment"], section=sec,
                             hybrid=int(r["hybrid_hits"]>0), passage=c))
passages = pd.DataFrame(rows)
passages["_k"] = passages["doc_id"].astype(str)+"|"+passages["passage"].str.slice(0,200).str.lower()
passages = passages.drop_duplicates("_k").drop(columns="_k").reset_index(drop=True)
print("Passages:", len(passages)); passages["section"].value_counts()
""")

# ───────────────────────────────────────────────────────── PatentBERT
md("""## 9. PatentBERT embeddings

Load `anferico/bert-for-patents` and mean-pool token embeddings into one vector
per passage (PatentBERT is a masked-LM BERT, so we pool rather than take [CLS]).""")
code(r"""tok = AutoTokenizer.from_pretrained(CONFIG["model_name"])
model = AutoModel.from_pretrained(CONFIG["model_name"]).to(DEVICE).eval()

@torch.no_grad()
def embed(texts, bs, max_len):
    out = []
    use_amp = (DEVICE == "cuda")
    for i in range(0, len(texts), bs):
        batch = texts[i:i+bs]
        enc = tok(batch, padding=True, truncation=True, max_length=max_len,
                  return_tensors="pt").to(DEVICE)
        with torch.autocast("cuda", enabled=use_amp):
            hs = model(**enc).last_hidden_state
        mask = enc["attention_mask"].unsqueeze(-1).float()
        emb = (hs*mask).sum(1) / mask.sum(1).clamp(min=1e-9)
        emb = torch.nn.functional.normalize(emb, p=2, dim=1)
        out.append(emb.float().cpu().numpy())
        if (i//bs) % 10 == 0: print(f"  {i+len(batch)}/{len(texts)}", end="\r")
    return np.vstack(out)

emb = embed(passages["passage"].tolist(), CONFIG["batch_size"], CONFIG["max_length"])
print("\nEmbeddings:", emb.shape)
np.save("passage_embeddings.npy", emb)
""")

# ───────────────────────────────────────────────────────── UMAP + cluster
md("""## 10. UMAP + HDBSCAN — the technology map

Two UMAP projections from the same PatentBERT space:
- a **2-D** projection for visualization (the technologies map), and
- a **higher-dim** projection that HDBSCAN clusters into technology themes.""")
code(r"""reducer_2d = umap.UMAP(n_neighbors=CONFIG["umap_neighbors"],
                       min_dist=CONFIG["umap_min_dist"], n_components=2,
                       metric="cosine", random_state=CONFIG["random_state"])
xy = reducer_2d.fit_transform(emb)
passages["x"], passages["y"] = xy[:,0], xy[:,1]

reducer_nd = umap.UMAP(n_neighbors=CONFIG["umap_neighbors"], min_dist=0.0,
                       n_components=CONFIG["umap_clust_dims"], metric="cosine",
                       random_state=CONFIG["random_state"])
emb_nd = reducer_nd.fit_transform(emb)

clusterer = hdbscan.HDBSCAN(min_cluster_size=CONFIG["min_cluster_size"],
                            min_samples=CONFIG["min_samples"],
                            metric="euclidean", cluster_selection_method="eom")
passages["cluster"] = clusterer.fit_predict(emb_nd)
n_clusters = passages["cluster"].nunique() - (1 if -1 in passages["cluster"].values else 0)
print(f"{n_clusters} technology clusters | noise: {int((passages['cluster']==-1).sum())}")
""")

md("### Label clusters with class-based TF-IDF (c-TF-IDF)")
code(r"""def ctfidf_terms(texts, labels, top_n=12):
    g = pd.DataFrame({"t":texts,"l":labels}).groupby("l")["t"].apply(" ".join)
    cv = CountVectorizer(stop_words="english", ngram_range=(1,2), min_df=2)
    X = cv.fit_transform(g.values); words = np.array(cv.get_feature_names_out())
    tf = X.toarray().astype(float); tf /= tf.sum(1, keepdims=True)+1e-9
    idf = np.log(1 + X.shape[0] / (1 + (X>0).sum(0))); idf = np.asarray(idf).ravel()
    c = tf*idf
    return {lab: words[c[i].argsort()[::-1][:top_n]].tolist()
            for i, lab in enumerate(g.index)}

terms = ctfidf_terms(passages["passage"], passages["cluster"])
def short_label(cid):
    if cid == -1: return "noise"
    ws = [w for w in terms[cid] if w not in ("tire","tyre","pneumatic","invention")]
    return ", ".join(ws[:3])
for cid in sorted(t for t in terms if t != -1):
    print(f"Cluster {cid:>2} (n={int((passages['cluster']==cid).sum()):>4}): "
          f"{', '.join(terms[cid][:10])}")
""")

# ───────────────────────────────────────────────────────── the UMAP plot
md("## 11. 🗺️ Technologies UMAP")
code(r"""fig, ax = plt.subplots(figsize=(13,9))
noise = passages[passages["cluster"]==-1]
ax.scatter(noise["x"], noise["y"], s=4, c="#dddddd", alpha=0.5, label="noise")
cmap = plt.get_cmap("tab20", max(n_clusters,1))  # portable across mpl versions
for i, cid in enumerate(sorted(c for c in passages["cluster"].unique() if c!=-1)):
    sub = passages[passages["cluster"]==cid]
    ax.scatter(sub["x"], sub["y"], s=7, color=cmap(i), alpha=0.7)
    cx, cy = sub["x"].median(), sub["y"].median()
    ax.text(cx, cy, f"{cid}: {short_label(cid)}", fontsize=8, weight="bold",
            ha="center", va="center",
            bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=cmap(i), alpha=0.85))
ax.set_title("PatentBERT technology map — zero-degree cords in PCR tires",
             fontsize=13, weight="bold")
ax.set_xlabel("UMAP-1"); ax.set_ylabel("UMAP-2")
plt.tight_layout(); plt.savefig("technologies_umap.png", dpi=150); plt.show()
""")

md("""### Interactive UMAP (hover for patent + snippet)
Useful for exploring exactly which patents sit in each technology island.""")
code(r"""import plotly.express as px
plot_df = passages.copy()
plot_df["label"] = plot_df["cluster"].map(lambda c: f"{c}: {short_label(c)}" if c!=-1 else "noise")
plot_df["snippet"] = plot_df["passage"].str.slice(0,160)+"…"
fig = px.scatter(plot_df, x="x", y="y", color="label",
                 hover_data={"doc_id":True,"assignee":True,"section":True,
                             "snippet":True,"x":False,"y":False},
                 opacity=0.7, title="PatentBERT technology map (interactive)")
fig.update_traces(marker=dict(size=5)); fig.update_layout(height=720)
fig.show()
""")

md("""### Highlight: hybrid-cord passages on the map
See where the *hybrid* (aramid/PET + nylon) technology lives relative to everything else.""")
code(r"""fig, ax = plt.subplots(figsize=(12,8))
ax.scatter(passages["x"], passages["y"], s=5, c="#dddddd", alpha=0.5)
h = passages[passages["hybrid"]==1]
ax.scatter(h["x"], h["y"], s=14, c="#C44E52", alpha=0.8, label="hybrid-cord passage")
ax.legend(); ax.set_title("Hybrid-cord passages within the technology map")
ax.set_xlabel("UMAP-1"); ax.set_ylabel("UMAP-2")
plt.tight_layout(); plt.show()
""")

# ───────────────────────────────────────────────────────── cluster table
md("## 12. Cluster characterization")
code(r"""recs = []
for cid in sorted(c for c in passages["cluster"].unique() if c!=-1):
    sub = passages[passages["cluster"]==cid]
    yrs = sub["year"].dropna().astype(int)
    recs.append(dict(cluster=cid, passages=len(sub),
                     patents=sub["doc_id"].nunique(),
                     hybrid_share=round(sub["hybrid"].mean(),2),
                     yr_median=int(yrs.median()) if len(yrs) else None,
                     top_assignee=sub["assignee"].replace("",np.nan).dropna().mode().iloc[0]
                       if sub["assignee"].replace("",np.nan).notna().any() else "",
                     top_terms=", ".join(terms[cid][:8])))
cluster_summary = pd.DataFrame(recs).sort_values("passages", ascending=False)
cluster_summary
""")

# ───────────────────────────────────────────────────────── categories
md("""## 13. Map clusters to the analysis questions

Weak-supervision tagging of each passage into the six management questions, then
a cluster × category heatmap so you can see which technology islands are about
advantages vs. challenges vs. process, etc.""")
code(r"""low = passages["passage"].str.lower()
for cat, phrases in CATEGORY_FRAMEWORK.items():
    passages[f"cat_{cat}"] = sum(low.str.count(p) for p in phrases)

cat_cols = [f"cat_{c}" for c in CATEGORY_FRAMEWORK]
heat = passages.groupby("cluster")[cat_cols].apply(lambda d:(d>0).mean())
heat = heat[heat.index!=-1]
heat.columns = [c.replace("cat_","") for c in heat.columns]

fig, ax = plt.subplots(figsize=(9, 0.5*len(heat)+2))
im = ax.imshow(heat.values, cmap="viridis", aspect="auto")
ax.set_xticks(range(len(heat.columns))); ax.set_xticklabels(heat.columns, rotation=35, ha="right")
ax.set_yticks(range(len(heat.index)))
ax.set_yticklabels([f"{c}: {short_label(c)}" for c in heat.index], fontsize=8)
ax.set_title("Cluster × question (share of passages)"); fig.colorbar(im, shrink=0.7)
plt.tight_layout(); plt.show()
""")

code(r"""# Overall category prevalence
prev = {c: int((passages[f"cat_{c}"]>0).sum()) for c in CATEGORY_FRAMEWORK}
pd.Series(prev).sort_values().plot(kind="barh", figsize=(7,3.5), color="#8172B3")
plt.title("Passages matching each analysis question"); plt.tight_layout(); plt.show()
""")

# ───────────────────────────────────────────────────────── exports
md("## 14. Exports")
code(r"""passages.to_csv("passages_clustered.csv", index=False)
cluster_summary.to_csv("cluster_summary.csv", index=False)
pcr.to_csv("pcr_patents.csv", index=False)
print("Saved: technologies_umap.png, passage_embeddings.npy,")
print("       passages_clustered.csv, cluster_summary.csv, pcr_patents.csv")
""")
md("""---
**Tuning tips**
- Fewer, broader technology themes → raise `min_cluster_size`.
- Tighter islands → lower `umap_min_dist` / `umap_neighbors`.
- Focus on recent tech → set `apply_year_filter=True`.
- Cluster patents instead of passages → average embeddings per `doc_id` first.
""")

nb["cells"] = cells
nb["metadata"] = {
    "kernelspec": {"display_name":"Python 3","language":"python","name":"python3"},
    "language_info": {"name":"python"},
}
out = Path("notebooks/PatentBERT_ZeroDegree_PCR.ipynb")
out.parent.mkdir(exist_ok=True)
nbf.write(nb, str(out))
print("Wrote", out, "with", len(cells), "cells")
