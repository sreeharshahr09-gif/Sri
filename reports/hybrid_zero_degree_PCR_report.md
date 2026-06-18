# Hybrid Zero-Degree Cords in PCR Tires — Technology & Patent Landscape

*Prepared from a PatSeer patent landscape of 1,816 families, screened to 1,172
passenger-car-radial (PCR) zero-degree (cap-ply / overlay / jointless-band)
records. Patent numbers are illustrative examples drawn from that corpus; the
full evidence base and every supporting passage are in the accompanying machine
report (`output/analysis_report.md`) and data tables.*

---

## Executive summary

The zero-degree belt in a passenger tire — the circumferentially wound
**cap ply / overlay / jointless band (JLB)** sitting on top of the steel belt
package — has shifted over the last two decades from a simple **nylon-6.6
overlay** toward **hybrid cords** that combine a high-modulus filament (aramid,
PET, or carbon fiber) with a high-elongation filament (nylon) in a single
co-twisted cord. The engineering objective is constant: **restrain centrifugal
diameter growth at high speed (durability, uniformity) without losing the ride
comfort, flat-spot recovery, and manufacturability** that nylon provides.

Three findings stand out for management:

1. **The hybrid is now a PET-led story, not only aramid.** In the corpus,
   **PET/polyester appears in 599 records and aramid in 473**; the most common
   hybrid pairing by raw co-occurrence is **PET + nylon (332 records)**, ahead of
   **aramid + nylon (219)**. Aramid buys the highest modulus; **PET delivers most
   of the high-speed benefit at materially lower cost**, which is why it
   dominates volume PCR/UHP constructions.
2. **The field is mature and consolidating.** Filing peaked around **2018**, and
   only **~25% of the patents are still legally alive (291 of 1,172)**. The core
   constructions are well-established; differentiation is now in **cord
   architecture (twist, layering, edge vs. full-width) and process control**, not
   in the basic concept.
3. **The competitive center of gravity is Japanese OEMs plus the aramid/PET cord
   makers.** Bridgestone, Sumitomo, and Yokohama lead overall; in the hybrid
   slice specifically, Bridgestone, Goodyear, Yokohama, Sumitomo and **cord
   supplier Hyosung** are most active.

---

## 1. Scope and corpus

| Dimension | Value |
|---|---|
| Families retrieved (PatSeer, zero-degree + tire + CPC) | 1,816 |
| Screened to PCR + zero-degree (truck/OTR/aircraft removed) | **1,172** |
| Explicit hybrid-cord records | 153 (≈219 by aramid+nylon co-occurrence, 332 by PET+nylon) |
| Legally alive / dead | 291 / 881 |
| Priority year ≥ 2015 / < 2015 | 432 / 739 (peak 2018) |

**Caveat on time window:** the analysis was run over the **full** export per
instruction; ~63% of records predate 2015. A 2015-onward cut is available on
request and sharpens the "current technology" picture.

---

## 2. Competitive landscape

**Overall (all 1,172):** Bridgestone (198), Sumitomo (131), Yokohama (124),
Michelin (~150 across entities), Goodyear (79), Pirelli (37), Continental (~49),
Kumho (27).

**Hybrid-cord cohort (153):** Bridgestone (25), Goodyear (17), Yokohama (16),
Sumitomo (13), **Hyosung (9)**, Continental (8), Michelin (7), Pirelli (5),
Kumho (5).

The appearance of **Hyosung** (and, in the broader materials view, Kordsa and
Kolon) confirms that hybrid cap-ply IP is **co-developed between tire makers and
cord/yarn suppliers** — the cord construction and adhesive treatment are as
patent-active as the tire architecture itself.

---

## 3. Technologies and materials

### 3.1 The hybrid cord concept
A hybrid zero-degree cord co-twists **two yarns of different modulus** so the
cord is compliant at low load (good processing, ride, flat-spot recovery) but
stiffens sharply at high load (restrains centrifugal growth). A representative
construction twists a **low-modulus yarn pre-twisted 3–25%** against an
**untwisted high-modulus yarn in the opposite direction** (e.g. CN121002250A),
or builds a **high-modulus sheath spirally wound on a nylon core**
(KR102005184B1: PA66 core 600–900 denier + aramid cover).

### 3.2 Material families observed
| Material | Records | Role |
|---|---|---|
| PET / polyester | 599 | High-modulus, low-cost workhorse; dominant hybrid partner |
| Aramid (para-aramid) | 473 | Highest modulus; premium UHP / high-speed |
| Rayon | 376 | Legacy textile; dimensional stability |
| PEN | 128 | High-modulus polyester alternative |
| Nylon 6.6 | 123 | High-elongation partner / classic overlay |
| Carbon fiber | 87 | Emerging ultra-high-modulus reinforcement |
| PA4.6 | 37 | Heat-resistant nylon for hot side regions |
| Resin-coated / thermoplastic cord | 185 | Emerging coating route (see §9) |

### 3.3 Representative constructions (cited)
- **Kordsa (WO2015137901A1):** PET + nylon-6.6 multifilament hybrid cap ply,
  twist 100–800 (pref. 200–400) twists/m, spirally wound.
- **Continental (EP3912833A1):** **zoned** belt bandage — PET core section at
  120 EPDM with **PA4.6 side sections**, addressing the hotter shoulder regions.
- **Hankook (KR102005184B1):** PA66 core + aramid spiral cover with graded EPI.

---

## 4. Advantages and performance benefits

The recurring, evidence-backed value propositions are:

- **High-speed durability / centrifugal growth control** — the primary driver;
  the high-modulus partner restrains crown expansion at speed (e.g. JP2001163005A,
  Bridgestone: "reduced rolling resistance, improved high-speed durability,
  steering stability, reduced road noise").
- **Uniformity / RFV** — jointless spiral winding removes the splice that causes
  radial force variation.
- **Flat-spot reduction** — explicitly targeted (e.g. WO2022188158A1,
  "anti-flat-spot tire"), a key OEM fitment requirement.
- **Rolling resistance / weight** — lighter, thinner hybrid overlays reduce
  rotating mass and hysteresis, supporting fuel economy.
- **Road-noise reduction** — a notably frequent benefit in the Japanese filings
  (Sumitomo JP2003182307A; Anhui Giti CN107901708A).
- **Handling / steering stability** — crown stiffness improves response.

---

## 5. Challenges and disadvantages

- **Belt-end / edge separation** — the classic failure the cap ply exists to
  prevent; conventional high-elongation nylon edge cap plies are unreinforced at
  the belt center and can still separate at the belt end (KR20110032406A, Kumho).
- **Cord–rubber adhesion under load** — aramid and steel adhesion is degraded by
  centrifugal force and heat, requiring specialized dips (US20110220263A1).
- **Thermal-shrinkage mismatch → cord breakage** — insufficient shrinkage in the
  side regions reduces durability and can break cords at speed (EP2829419B1,
  Continental); the two materials in a hybrid shrink differently and must be tuned.
- **Cost** — aramid content raises cost; this is the structural reason PET-based
  hybrids dominate volume applications.
- **Compression fatigue / stiffness** of aramid, managed via twist optimization.

---

## 6. Design guidelines and parameters (quantified)

The corpus yields concrete, citable design windows — useful as engineering
reference points:

| Parameter | Typical range (cited) | Source |
|---|---|---|
| Cord linear density (aramid) | 420–1,100 dtex | CA2272777A1 |
| Cord linear density (PA66 cap) | 930 / 1,400 / 1,870 / 2,100 dtex/2 | CN107379895A |
| Twist | ~200–400 twists/m (hybrid); 315+ (aramid) | WO2015137901A1; CA2272777A1 |
| Overlay cord density (PET bandage) | 60–120 EPDM (pref. ~80) | DE102010036760A1 |
| Belt cord density | ~38 ends/inch | CA2272777A1 |
| Central : shoulder cord ratio | 0.6–0.8 | EP0565339A1 |
| Circumferential wire initial modulus | < 900 cN/tex | WO2001032446A1 |
| Cap-ply cord thickness | 0.9–1.6 mm | KR20110050216A |

Design levers consistently used: **graded/zoned density** (denser at shoulders
or center to manage stiffness distribution), **edge band vs. full-width
overlay**, **single vs. dual layer**, and **tuned dry-heat shrinkage force**.

---

## 7. Process and manufacturing

- **Spiral (jointless) winding** is the dominant application method; **winding
  pitch and tension** are the controlled variables (Toyo US20180178470A1;
  Sentury CN114474809A — explicit pitch formulas).
- **Zoned winding** — different feed pitch/gaps in central vs. inner/outer
  regions to vary local stiffness (Cheng Shin CN121492526A).
- **Adhesive dipping** of the textile (epoxy pre-dip for aramid, then RFL) is the
  critical adhesion step; unidirectional nylon-sheet cap plies are dip-treated
  before winding (Hankook KR20210098701A).
- **Process challenges:** tension control to avoid cord breakage, splice
  avoidance, and managing the shrinkage behavior of dissimilar materials through
  heat-setting/normalizing.

---

## 8. Trade-offs

The central trade-offs the patents repeatedly try to optimize:

- **High-speed durability ↔ ride comfort / flat-spotting** — more modulus
  restrains growth but stiffens the crown; the hybrid exists precisely to bend
  this curve.
- **Performance ↔ cost** — aramid vs. PET; the dominant industry answer is
  PET-led hybrids reserving aramid for premium UHP.
- **Stiffness ↔ rolling resistance / weight** — thinner high-modulus overlays
  cut mass but raise the adhesion and fatigue burden.
- **Edge reinforcement ↔ centre reinforcement** — zoned designs trade uniform
  coverage for targeted stiffness where separation initiates.

*(Note: the automated trade-off extraction is the noisiest category and was
manually filtered here; treat this section as directional.)*

---

## 9. Emerging directions / white space

- **Resin-coated / thermoplastic-coated cords (185 records)** — a coating route
  distinct from classic RFL-dipped textiles, associated with novel "tire frame"
  constructions; worth a dedicated watch (e.g. WO2021125112A1).
- **Carbon-fiber reinforcement (87 records)** — ultra-high-modulus option
  appearing in newer filings (e.g. WO2019116841A1, Bridgestone).
- **Heat-resistant nylons (PA4.6)** for shoulder zones — addresses the
  thermal-shrinkage failure mode directly.

---

## 10. Method and caveats

- **Relevance** was defined by the PatSeer query; the pipeline additionally
  removed clearly non-PCR (truck/off-road/aircraft) records and organized
  evidence around the six management questions.
- **Technology discovery** used passage-level embeddings and clustering;
  the figures above are corpus counts (high recall — counts include any mention,
  so they indicate prevalence, not exclusive use).
- **Patent numbers are illustrative** of each theme, not exhaustive; every claim
  traces to a passage in `output/analysis_report.md`.
- **Time window:** full corpus (incl. pre-2015); a 2015+ refresh is one switch away.
- **Embeddings** were run in TF-IDF fallback mode in this environment; a semantic
  (PatentSBERTa + BERTopic) re-run via `scripts/run_semantic.sh` will sharpen the
  cluster definitions but does not change the substantive findings above.
