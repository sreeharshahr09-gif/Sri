"""Ready-made analysis plans for common tyre R&D objectives.

Each preset is a full plan: the problem dimension and its categories, technology groups, complexity
levels described for the topic, scope, extra CSV columns, the objective's own analysis questions, and
suggested search terms for the database export. Choosing one fills the plan form; everything stays
editable, and a pasted brief can adapt it further.
"""

from __future__ import annotations

from .plan import DEFAULT_SECTIONS, AnalysisPlan, Category


def _cats(items: list[tuple[str, str]]) -> list[Category]:
    return [Category(name, desc) for name, desc in items]


def _levels(simple: str, intermediate: str, complex_: str) -> list[Category]:
    return _cats([("Simple", simple), ("Intermediate", intermediate), ("Complex", complex_)])


PRESETS: dict[str, dict] = {
    "Lightweighting": {
        "title": "Lightweight tyre technologies",
        "objective": "Map how patents reduce tyre weight while keeping rolling resistance, durability and handling: "
                  "which components are made lighter, by which kind of solution, and which companies use which.",
        # Where the weight is saved (rows) and how (technology groups): two independent questions, so the
        # combinations - and the gaps among them - mean something.
        "problem_label": "Component lightened",
        "problems": _cats([
            ("Tread & under-tread", "tread cap, base and under-tread rubber"),
            ("Sidewall", "sidewall rubber and its gauge"),
            ("Belt & cap ply", "steel belts, cap and overlay plies"),
            ("Carcass ply", "body plies and turn-ups"),
            ("Bead & apex", "bead core, apex, bead filler and chafer"),
            ("Innerliner", "innerliner and air barrier"),
            ("Whole tyre", "the tyre as a whole: overall construction, non-pneumatic or other new tyre concepts"),
        ]),
        "tech_buckets": _cats([
            ("Lighter reinforcement", ("steel replaced by aramid, PET, nylon, hybrid or carbon-fibre cords; fewer "
                                       "or thinner cords and filaments")),
            ("Lower-density compounds", "lower-density polymers or fillers, less filler, light or hollow fillers"),
            ("Gauge & structural design", ("thinner gauges, fewer plies, new ply, belt, bead or contour layouts "
                                           "within a pneumatic tyre")),
            ("Manufacturing process", "building, calendering, curing and other processes that allow lighter parts"),
            ("Novel architecture", ("non-pneumatic, airless or other fundamentally new tyre concepts; not a new "
                                    "ply, belt or bead layout of a pneumatic tyre")),
        ]),
        "complexity_levels": _levels("gauge or geometry change within an existing construction",
                                  "material substitution within the existing construction (new cord, compound)",
                                  "new architecture or material system that needs new processes or equipment"),
        "in_scope": "Patents on tyres or tyre components whose purpose or effect includes lower tyre mass. Not in "
                 "scope: wheels, brakes or other vehicle parts; material patents with no tyre application.",
        "extra_fields": _cats([
            ("Weight saving claimed", "the weight saving as stated, with unit (% , g or kg); 'not stated' if none"),
            ("Material replaced -> by", "e.g. 'steel cord -> aramid cord'; 'not stated' if none"),
            ("Performance kept or traded", "attributes kept or traded off: rolling resistance, durability, handling, noise"),
        ]),
        "analysis_questions": [
            ("For each component lightened, how many patents state a numeric weight saving, and what are the "
             "median and range of the stated percentages?"),
            ("Which components and which technology groups do the five largest assignees work on? Show patents per "
             "assignee, component and technology group."),
            "Which performance attributes are most often kept or traded off alongside weight reduction?",
            ("How has the number of patents using lighter reinforcement (aramid, PET, nylon, hybrid, carbon fibre) "
             "developed per year compared with all patents?"),
        ],
        "keywords": ["lightweight", "weight reduction", "mass reduction", "reduced weight", "light weight", "low weight"],
        "classification_codes": ["B60C 9/00 (reinforcements)", "B60C 1/00 (rubber compositions)", "B60C 15/00 (beads)",
                              "B60C 13/00 (sidewalls)", "C08L 21/00", "B29D 30/00 (tyre manufacturing)"],
    },
    "Low rolling resistance": {
        "title": "Low rolling resistance tyres",
        "objective": "Map how patents lower rolling resistance (energy loss) without losing wet grip, wear life or "
                  "handling: the levers used, the trade-offs managed and the companies behind them.",
        # Where the energy is lost (rows) and how it is reduced (technology groups): independent questions.
        "problem_label": "Loss source",
        "problems": _cats([
            ("Tread", "hysteresis and deformation of the tread cap"),
            ("Base & under-tread", "base, cap-base and under-tread rubber"),
            ("Sidewall & bead area", "flexing of sidewall, apex and bead area"),
            ("Belt & carcass", "deformation of belt and carcass"),
            ("Footprint & contour", "deformation set by profile and footprint shape"),
            ("Tyre mass", "overall tyre mass"),
            ("Inflation pressure", "pressure lost through the innerliner, valve or seals"),
        ]),
        "tech_buckets": _cats([
            ("Compound & materials", "polymers, fillers, coupling agents, resins, oils"),
            ("Structural design", "construction, contour, belt and carcass layout"),
            ("Tread pattern", "pattern, sipes, groove geometry"),
            ("Manufacturing process", "mixing, building, curing"),
            ("Monitoring & systems", "pressure monitoring and other systems that keep rolling resistance low"),
        ]),
        "complexity_levels": _levels("pattern, gauge or geometry change",
                                  "compound or material change within the existing construction",
                                  "new polymer systems, architectures or active systems"),
        "in_scope": "Patents whose aim or effect includes lower rolling resistance, fuel or energy efficiency of tyres. "
                 "Not in scope: drivetrain or vehicle efficiency patents not about the tyre.",
        "extra_fields": _cats([
            ("RR improvement claimed", "as stated: %, rolling resistance coefficient, tan δ change; 'not stated' if none"),
            ("Trade-off managed", "attribute balanced against rolling resistance: wet grip, wear, handling, noise"),
            ("Key compound ingredient", "main polymer, filler or resin named; 'not stated' if none"),
            ("Test or metric", "e.g. ISO 28580, tan δ at 60 °C, drum test"),
        ]),
        "analysis_questions": [
            "How often is the wet grip versus rolling resistance trade-off addressed, and in which technology groups?",
            ("Which key compound ingredients appear most often, and how has that changed between the first and second "
             "half of the period?"),
            ("For each loss source, how many patents state a numeric improvement, and what are the typical stated "
             "values?"),
            "Which assignees rely mostly on compound solutions and which on structural design?",
        ],
        "keywords": ["rolling resistance", "low rolling resistance", "fuel efficiency", "hysteresis", "tan delta",
                  "energy loss", "low heat build-up"],
        "classification_codes": ["B60C 1/00 (rubber compositions)", "C08L 9/06 (SBR)", "C08K 3/36 (silica)",
                              "C08L 21/00", "B60C 11/00 (treads)", "B60C 9/18 (belts)"],
    },
    "Durability": {
        "title": "Tyre durability",
        "objective": "Map how patents prevent tyre damage and failure: the failure modes addressed, the solutions and "
                  "their complexity, and who leads per failure mode.",
        "problem_label": "Failure mode",
        "problems": _cats([
            ("Belt-edge separation", "separation or cracking at belt or ply ends"),
            ("Bead damage", "bead and apex separation, rim chafing, toe tearing"),
            ("Sidewall cracking & ozone", "flex cracking, ozone and weathering"),
            ("Fatigue & heat build-up", "fatigue failure from heat generation under load or speed"),
            ("Cut, chip & puncture", "cut and chip damage, puncture resistance and sealing"),
            ("Groove cracking", "cracks at groove bottoms"),
            ("Cord corrosion & adhesion loss", "loss of rubber-to-cord adhesion, corrosion of steel cords"),
            ("Tread separation & retreadability", "tread-casing separation, casing life for retreading"),
        ]),
        "tech_buckets": _cats([
            ("Reinforcement & adhesion", "cords, adhesion systems, bead wire"),
            ("Rubber compounds", "fatigue-, crack- and heat-resistant compounds"),
            ("Structural design", "construction, layer ends, contour, gauges"),
            ("Manufacturing & curing", "building and curing methods"),
            ("Monitoring & prediction", "sensors and models that detect or predict damage"),
        ]),
        "complexity_levels": _levels("change of geometry, gauge or placement of layer ends",
                                  "new compound, cord or adhesion system within the construction",
                                  "new construction, material system or monitoring system"),
        "in_scope": "Patents whose aim includes preventing tyre damage, failure or premature end of life. Not in "
                 "scope: wear-rate patents with no damage or failure aspect.",
        "extra_fields": _cats([
            ("Service condition", "load or use condition: heavy load, high speed, off-road, run-flat"),
            ("Durability test or metric", "drum test, step-load test, fatigue test, as stated"),
            ("Durability gain claimed", "as stated; 'not stated' if none"),
        ]),
        "analysis_questions": [
            "Which failure modes are addressed most for each segment (e.g. TBR versus PCR)?",
            "Which technology groups are used against each failure mode? Show patents per failure mode and group.",
            "How has the share of monitoring and prediction solutions developed over the years?",
            "Which assignees lead for each of the three most frequent failure modes?",
        ],
        "keywords": ["durability", "separation", "crack", "fatigue", "heat build-up", "bead damage", "puncture"],
        "classification_codes": ["B60C 9/00 (reinforcements)", "B60C 15/00 (beads)", "B60C 13/00 (sidewalls)",
                              "B60C 9/18 (belts)", "C08L 21/00", "B60C 23/00 (monitoring)"],
    },
    "Uneven wear": {
        "title": "Uneven wear in tyres",
        "objective": "Map the patented solutions to uneven and irregular wear: wear types, their causes, solution "
                  "routes ranked by complexity, competitors' key patents and research gaps.",
        "problem_label": "Wear type",
        "problems": _cats([
            ("Shoulder wear", "excess wear at the shoulders, step or one-sided shoulder wear"),
            ("Center wear", "excess wear in the tread centre"),
            ("Camber wear", "one-sided wear from camber"),
            ("Cupping", "scalloped wear around the circumference"),
            ("Feathering", "toe-related saw-tooth wear across ribs"),
            ("Heel & toe wear", "block edges wearing unevenly front to back"),
            ("River & rib-edge wear", "wear along groove or rib edges"),
            ("Flat-spotting & chip/chunk", "flat spots, chipping and chunking of the tread"),
        ]),
        "tech_buckets": _cats([
            ("Structural / design", "tread pattern geometry, belt package, carcass and bead construction"),
            ("Material science", "compounds, fillers, polymers, reinforcements"),
            ("Process / manufacturing", "building, curing, mixing and uniformity"),
            ("Active / electronic", "sensors, TPMS, wear prediction algorithms, connected systems"),
        ]),
        "complexity_levels": _levels("geometric or dimensional changes", "material or construction changes",
                                  "electronic, IoT or active systems"),
        "in_scope": "Patents that address uneven, irregular or abnormal tread wear of tyres. Not in scope: overall "
                 "wear-rate (abrasion) patents with no unevenness aspect.",
        "extra_fields": _cats([
            ("Wear cause", "the cause named: alignment, load, pressure, footprint pressure, slip"),
            ("Axle position", "steer, drive, trailer or all positions; 'not stated' if none"),
            ("Wear test or metric", "as stated; 'not stated' if none"),
        ]),
        "analysis_questions": [
            "Which wear types are addressed most for TBR and for PCR tyres?",
            "Which wear causes are named most often, and which technology groups address each?",
            "How has the number of sensor- or algorithm-based (Active / electronic) solutions developed per year?",
            "Which assignees lead for each of the three most frequent wear types?",
        ],
        "keywords": ["uneven wear", "irregular wear", "abnormal wear", "shoulder wear", "partial wear", "wear resistance"],
        "classification_codes": ["B60C 11/00 (treads)", "B60C 9/18 (belts)", "B60C 11/24 (wear indicators)",
                              "B60C 23/00 (monitoring)", "G01M 17/02 (tyre testing)"],
    },
    "Smart / connected tyres": {
        "title": "Smart and connected tyres",
        "objective": "Map patented intelligent-tyre technologies: what is sensed or enabled, how sensors are "
                  "integrated, the algorithms and services built on them, and who leads.",
        "problem_label": "Function sensed or enabled",
        "problems": _cats([
            ("Pressure & temperature", "TPMS and temperature sensing"),
            ("Tread wear estimation", "tread depth and wear state estimation"),
            ("Load & vehicle dynamics", "load, slip, friction, forces"),
            ("Road condition & grip", "road surface and grip estimation"),
            ("Tyre identification", "RFID, digital ID, traceability"),
            ("Damage & failure detection", "detection of damage, punctures, failures"),
            ("Energy harvesting & power", "powering in-tyre electronics"),
            ("Data & fleet services", "data platforms, fleet management, predictive maintenance"),
        ]),
        "tech_buckets": _cats([
            ("Sensors & hardware", "sensing elements and electronics"),
            ("Sensor integration", "mounting or embedding in or on the tyre"),
            ("Signal processing & algorithms", "estimation methods, machine learning"),
            ("Communication & power", "wireless links, energy harvesting, batteries"),
            ("Vehicle & fleet systems", "integration into vehicle control or fleet services"),
        ]),
        "complexity_levels": _levels("passive tags or reuse of existing TPMS signals",
                                  "new in-tyre sensors or new integration",
                                  "algorithms, machine learning or vehicle-system integration"),
        "in_scope": "Patents on sensing, identification or data functions in or on tyres and the systems built on "
                 "them. Not in scope: general vehicle electronics with no tyre-specific element.",
        "extra_fields": _cats([
            ("Sensor type", "e.g. accelerometer, pressure, strain, RFID; 'not stated' if none"),
            ("Mounting location", "innerliner, embedded, rim, valve, vehicle; 'not stated' if none"),
            ("Data use", "driver warning, vehicle control, fleet service, manufacturing"),
            ("Power source", "battery, energy harvesting, passive; 'not stated' if none"),
        ]),
        "analysis_questions": [
            "How has each sensed function developed per year?",
            "Which sensor types are used for which functions? Show patents per sensor type and function.",
            "What share of patents come from tyre makers versus other companies (electronics, OEMs, others)?",
            "How has the share of algorithm and machine-learning based patents changed over time?",
        ],
        "keywords": ["tire sensor", "intelligent tire", "smart tire", "TPMS", "RFID", "tire monitoring", "wear estimation"],
        "classification_codes": ["B60C 23/00 (pressure and temperature monitoring)", "B60C 23/04", "G01L 17/00",
                              "G06K 19/07 (RFID)", "B60C 19/00"],
    },
    "NVH (noise, vibration, harshness)": {
        "title": "Tyre noise, vibration and harshness",
        "objective": "Map how patents reduce tyre noise and vibration: the noise sources addressed, acoustic and "
                  "design solutions, trade-offs, and who leads, including EV-specific noise.",
        "problem_label": "Noise / vibration source",
        "problems": _cats([
            ("Pattern noise", "pitch noise from tread elements"),
            ("Air pumping & groove resonance", "air pumping, pipe resonance in grooves"),
            ("Cavity resonance", "air cavity resonance inside the tyre"),
            ("Road & structure-borne noise", "vibration transmitted through the tyre structure"),
            ("Pass-by noise", "exterior noise regulations"),
            ("Ride harshness & impact", "impact harshness, ride comfort"),
            ("Uniformity vibration", "force variation, imbalance, runout"),
        ]),
        "tech_buckets": _cats([
            ("Tread pattern design", "pitch sequencing, groove and sipe geometry"),
            ("Acoustic devices", "sound absorbers, foams, resonators in or on the tyre"),
            ("Structural design", "belt, sidewall, contour, stiffness"),
            ("Compound & materials", "damping compounds and materials"),
            ("Manufacturing & uniformity", "processes that improve uniformity"),
        ]),
        "complexity_levels": _levels("pitch sequence or groove geometry change",
                                  "absorber materials, compound or structural change",
                                  "active or adaptive systems, new architectures"),
        "in_scope": "Patents whose aim includes lower tyre noise, vibration or harshness. Not in scope: vehicle "
                 "cabin acoustics with no tyre element.",
        "extra_fields": _cats([
            ("Noise metric or frequency", "dB, frequency range or test as stated"),
            ("Noise reduction claimed", "as stated; 'not stated' if none"),
            ("Noise path", "interior, exterior or both"),
        ]),
        "analysis_questions": [
            ("How has the use of acoustic devices (absorbers, foams, resonators) developed per year compared with "
             "tread-pattern solutions?"),
            "Which noise sources are addressed for interior versus exterior noise?",
            "Which noise solutions mention a trade-off with rolling resistance or weight?",
            "Which assignees lead for each noise source?",
        ],
        "keywords": ["tire noise", "cavity resonance", "sound absorber", "noise reduction", "pattern noise",
                  "vibration", "pass-by noise"],
        "classification_codes": ["B60C 19/00 (incl. noise damping)", "B60C 11/03 (tread patterns)",
                              "B60C 11/00 (treads)", "G10K 11/16 (sound absorbing)"],
    },
    "Wet / dry traction": {
        "title": "Wet and dry traction of tyres",
        "objective": "Map how patents improve grip on wet, dry and winter roads: grip conditions addressed, compound "
                  "and pattern solutions, trade-offs with rolling resistance and wear, and who leads.",
        "problem_label": "Grip condition",
        "problems": _cats([
            ("Wet braking & grip", "braking and grip on wet roads"),
            ("Aquaplaning", "hydroplaning resistance, water evacuation"),
            ("Dry braking & grip", "braking and grip on dry roads"),
            ("Dry handling & cornering", "cornering, steering response"),
            ("Snow traction", "snow performance, winter and all-season"),
            ("Ice grip", "grip on ice"),
            ("Grip when worn", "keeping grip as the tread wears"),
        ]),
        "tech_buckets": _cats([
            ("Compound & materials", "polymers, silica, resins, oils"),
            ("Tread pattern & sipes", "pattern, sipes, groove geometry"),
            ("Structural design", "contour, belt, footprint"),
            ("Manufacturing process", "mixing, building, curing"),
            ("Active / electronic", "grip estimation and adaptive systems"),
        ]),
        "complexity_levels": _levels("pattern, sipe or groove change", "compound or construction change",
                                  "new material systems, adaptive or sensor-based solutions"),
        "in_scope": "Patents whose aim includes better tyre grip, braking, traction or handling on any road. Not in "
                 "scope: vehicle braking systems with no tyre element.",
        "extra_fields": _cats([
            ("Grip improvement claimed", "as stated; 'not stated' if none"),
            ("Trade-off managed", "rolling resistance, wear, noise or other"),
            ("Season or use", "summer, winter, all-season, UHP; 'not stated' if none"),
            ("Key compound ingredient", "main polymer, filler or resin named; 'not stated' if none"),
        ]),
        "analysis_questions": [
            "How often is grip balanced against rolling resistance or wear (the magic triangle), per grip condition?",
            "How has the number of all-season and winter patents developed per year?",
            "Which grip conditions are addressed by compound solutions and which by tread pattern solutions?",
            "Which assignees lead for wet grip and for snow traction?",
        ],
        "keywords": ["wet grip", "traction", "braking", "aquaplaning", "hydroplaning", "snow", "ice", "all-season"],
        "classification_codes": ["B60C 11/03 (tread patterns)", "B60C 11/12 (sipes)", "B60C 1/00 (rubber compositions)",
                              "C08K 3/36 (silica)", "B60C 11/14 (anti-skid)"],
    },
    "Dynamic growth": {
        "title": "Dynamic growth control in tyres",
        "objective": "Map how patents control tyre dimensional growth under inflation, speed, heat and time: the "
                  "growth types, belt and cord solutions, trade-offs, and who leads.",
        "problem_label": "Growth type",
        "problems": _cats([
            ("Inflation growth", "diameter and width increase with inflation pressure"),
            ("Centrifugal growth", "growth at high speed"),
            ("Heat & creep growth", "growth from heat and over time"),
            ("Crown & shoulder lift", "uneven growth across the width"),
            ("Belt-edge movement", "belt-edge strains and separation from growth"),
            ("Growth-driven wear or noise", "wear or noise caused by growth"),
        ]),
        "tech_buckets": _cats([
            ("Belt & cap-ply design", "0° layers, jointless cap plies, belt packages"),
            ("Cord materials", "high-modulus, hybrid, aramid cords"),
            ("Carcass & bead design", "carcass line, bead and turn-up design"),
            ("Compound & materials", "compounds that limit creep and heat"),
            ("Manufacturing & curing", "building and curing methods"),
        ]),
        "complexity_levels": _levels("geometry, gauge or cord-angle change", "new cord material or cap-ply construction",
                                  "new belt architecture or manufacturing method"),
        "in_scope": "Patents whose aim includes limiting dimensional growth or deformation of tyres in service. Not "
                 "in scope: static dimensions of tyres or wheels.",
        "extra_fields": _cats([
            ("Speed or load condition", "as stated; 'not stated' if none"),
            ("Growth reduction claimed", "as stated; 'not stated' if none"),
            ("Cord material", "material of the growth-restraining layer; 'not stated' if none"),
        ]),
        "analysis_questions": [
            "How has the use of cap-ply or 0° layer solutions developed per year compared with new cord materials?",
            "Which cord materials are used against each growth type?",
            "Which growth solutions also mention weight or rolling resistance?",
            "Which assignees lead for each growth type?",
        ],
        "keywords": ["tire growth", "dimensional growth", "cap ply", "zero degree belt", "circumferential layer",
                  "high speed durability"],
        "classification_codes": ["B60C 9/18 (belts)", "B60C 9/22 (cap plies)", "B60C 9/00 (reinforcements)",
                              "D07B 1/06 (cords)"],
    },
    "High performance": {
        "title": "High-performance tyres",
        "objective": "Map patented solutions for high-performance and ultra-high-performance tyres, including EV "
                  "performance: the attributes improved, the technologies, trade-offs, and who leads.",
        "problem_label": "Performance attribute",
        "problems": _cats([
            ("Dry handling & cornering", "cornering stiffness, lateral grip"),
            ("High-speed durability", "durability at high speed"),
            ("Steering response", "steering precision and response"),
            ("Wet performance at speed", "wet grip and aquaplaning at high speed"),
            ("Heat management", "heat generation and dissipation"),
            ("Ride comfort", "comfort at high performance"),
            ("EV high-torque performance", "torque, weight and wear demands of EVs"),
            ("Track & motorsport", "track and racing use"),
        ]),
        "tech_buckets": _cats([
            ("Compound & materials", "high-grip and heat-resistant compounds"),
            ("Tread pattern", "pattern, block stiffness, grooves"),
            ("Structural design", "belt, cap ply, bead and apex, contour"),
            ("Manufacturing process", "building and curing"),
            ("Electronics & systems", "sensors and vehicle integration"),
        ]),
        "complexity_levels": _levels("pattern, gauge or geometry change", "compound or construction change",
                                  "new material systems, architectures or electronics"),
        "in_scope": "Patents on tyres for high-performance, ultra-high-performance, sports or EV performance use. Not "
                 "in scope: general-purpose tyres with no performance aspect.",
        "extra_fields": _cats([
            ("Speed rating or use", "UHP, track, EV, sports; 'not stated' if none"),
            ("Performance gain claimed", "as stated; 'not stated' if none"),
            ("Trade-off managed", "comfort, rolling resistance, wear, noise or other"),
        ]),
        "analysis_questions": [
            "Which technology groups are used for each performance attribute?",
            "How has the number of EV-related high-performance patents developed per year?",
            "Which trade-offs are managed most often?",
            "Which assignees lead for each of the three most frequent performance attributes?",
        ],
        "keywords": ["high performance tire", "UHP", "handling", "cornering", "high speed", "steering stability"],
        "classification_codes": ["B60C 9/18 (belts)", "B60C 11/00 (treads)", "B60C 1/00 (rubber compositions)",
                              "B60C 15/06 (apex)"],
    },
    "Sustainable materials": {
        "title": "Sustainable tyre materials",
        "objective": "Map patented routes to renewable and recycled tyre materials: the sustainability levers, the "
                  "materials and their claimed content, the performance kept or traded, and who leads.",
        # Which sustainability lever (rows) and in which kind of material or design (technology groups).
        "problem_label": "Sustainability lever",
        "problems": _cats([
            ("Bio-based / renewable", ("renewable feedstocks: guayule, dandelion, bio-butadiene, rice-husk silica, "
                                       "vegetable oils, bio-based cords")),
            ("Recycled content", "reclaim, devulcanised or crumb rubber, recycled PET or steel"),
            ("Recovered materials", "recovered carbon black, pyrolysis oil and other recovered streams"),
            ("Cleaner processing", "lower-energy, lower-emission or solvent-free production"),
            ("Longer life & retreading", "durability and retreadability that extend tyre life"),
            ("Lower wear particles", "less tyre and road wear particles"),
            ("End-of-life recyclability", "materials or designs that make tyres easier to recycle"),
        ]),
        "tech_buckets": _cats([
            ("Polymers & elastomers", "base polymers"),
            ("Fillers, oils & additives", "fillers, oils, resins, additives"),
            ("Reinforcement materials", "cords and fabrics"),
            ("Process technology", "mixing, curing and recycling processes"),
            ("Tyre design", "design for longevity, retreading and recycling"),
        ]),
        "complexity_levels": _levels("drop-in replacement of an ingredient", "new material needing compound redesign",
                                  "new material system or recycling process at industrial scale"),
        "in_scope": "Patents whose aim includes renewable, recycled or lower-impact tyre materials or longer tyre "
                 "life. Not in scope: general recycling patents with no tyre application.",
        "extra_fields": _cats([
            ("Sustainable content claimed", "renewable or recycled share as stated; 'not stated' if none"),
            ("Feedstock or source", "e.g. guayule, rice husk, end-of-life tyres; 'not stated' if none"),
            ("Performance kept or traded", "attributes kept or traded off"),
        ]),
        "analysis_questions": [
            "How have bio-based versus recycled levers developed per year?",
            "What sustainable content is claimed (as stated) for each lever?",
            "Which feedstocks are named most often?",
            "Which assignees lead for each lever?",
        ],
        "keywords": ["sustainable", "renewable", "bio-based", "recycled", "devulcanized", "recovered carbon black",
                  "guayule", "dandelion"],
        "classification_codes": ["C08L 7/00 (natural rubber)", "C08J 11/00 (recycling)", "C08K (fillers)",
                              "B60C 1/00 (rubber compositions)", "C09C 1/48 (carbon black)"],
    },
}

# The study template TYRE-LAYER-LANDSCAPE-01 for an R&D team: a construction layer (placement and coverage) with
# eligibility rules, a focus, a white-space rule and a fixed summary order. Copy it for another layer or component:
# change the scope, the objectives, the groups and the Placement / Coverage values; keep the rules.
_OBJECTIVES = ["Footprint shape", "Uniform contact pressure", "Tyre uniformity correction", "Uneven wear",
               "Crown/belt profile & radial growth", "Belt durability", "Heat/rolling resistance", "Ride/NVH",
               "Manufacturing", "Other"]
_UNDER_BELT_FOCUS = ["Footprint shape", "Uniform contact pressure", "Tyre uniformity correction"]

UNDER_BELT_LAYER = {
    "title": "Under-belt rubber layer (belt package–carcass interlayer)",
    "objective": ("Map patents on a rubber layer between the belt package and the carcass, whole-width or segmented, "
                  "and how it is used to control footprint shape, contact-pressure uniformity and tyre uniformity "
                  "(RFV, LFV, conicity, runout), for design direction and freedom-to-operate screening in TBR."),
    "problem_label": "Objective",
    "problems": _cats([
        ("Footprint shape", "contact patch shape and length/width, footprint ratio"),
        ("Uniform contact pressure", "even ground contact pressure across the tread width"),
        ("Tyre uniformity correction", "RFV, LFV, conicity or runout corrected by the layer (segmented layers in "
                                       "particular)"),
        ("Uneven wear", "irregular wear such as shoulder or centre wear"),
        ("Crown/belt profile & radial growth", "crown and belt profile, carcass line, radial growth in use"),
        ("Belt durability", "belt-edge separation and belt package durability"),
        ("Heat/rolling resistance", "heat build-up and rolling resistance"),
        ("Ride/NVH", "ride comfort, noise and vibration"),
        ("Manufacturing", "building, shaping or curing of the layer; productivity"),
    ]),
    "tech_buckets": _cats([
        ("Whole-width uniform layer", "continuous layer of constant thickness"),
        ("Whole-width profiled layer", "continuous layer whose thickness varies across the width (e.g. thicker at "
                                       "the shoulders or the centre)"),
        ("Segmented layer", "separate pieces across the width or around the circumference"),
        ("Multi-compound / graded layer", "two or more rubbers or layers with different modulus, hardness or tanδ"),
        ("Layer + belt package co-design", "layer claimed together with the belt construction (zero-degree or "
                                           "circumferential layer, belt widths or angles)"),
        ("Layer + profile co-design", "layer claimed as the means to set the carcass line against the belt line, "
                                      "or the tread radius"),
        ("Material-led layer", "the invention lies mainly in the layer compound, with standard geometry"),
    ]),
    "complexity_levels": _cats([
        ("Basic", "level 1, judged from the independent claims: presence and location of the layer only, no numeric "
                  "window. When the claims leave the level unclear, take the lower level and set confidence Low"),
        ("Parametrised", "level 2: one parameter window (thickness, width against the belt, segment gap, modulus, "
                         "hardness or tanδ)"),
        ("Multi-parameter", "level 3: two or more coupled windows (e.g. thickness distribution + width ratio, or "
                            "geometry + material property) linked to an objective"),
        ("System-level", "level 4: layer defined with other components through cross-component relations, "
                         "multi-compound or graded layers, or claims defined by performance outputs (footprint "
                         "ratio, contact pressure ratio, uniformity values)"),
    ]),
    "segments": ["TBR", "PCR", "LT", "OTR"],
    "in_scope": ("A rubber or elastomer layer placed radially between the belt package (innermost belt, crown "
                 "reinforcement) and the body ply or carcass, whole-width (continuous across the belt package "
                 "width) or segmented (two or more pieces across the width, or divided laterally or "
                 "circumferentially). Also called cushion (rubber), under-belt cushion or pad, belt under cushion, "
                 "pad rubber, buffer rubber or layer, squeegee, gum strip, intermediate rubber layer, interlayer, "
                 "decoupling layer. Not in scope: rubber between belt layers (inter-belt rubber); rubber between "
                 "belt and tread (undertread, tread base, tread cushion); rubber between carcass and inner liner "
                 "(tie gum); conventional belt-edge cushions or shoulder pads whose only purpose is belt-edge "
                 "durability; retread cushion gum, cavity sound absorbers, sealants, run-flat sidewall inserts, bead "
                 "fillers."),
    "relevance_rule": ("High = eligible: Placement BELT-CARCASS and Coverage WHOLE_WIDTH or SEGMENTED. Partial = to "
                       "check by hand: Placement or Coverage UNCLEAR, Coverage CENTRE_ONLY, or EDGE_ONLY with an "
                       "objective other than belt durability. Low = out: EDGE_ONLY for belt durability only, Placement "
                       "INTER-BELT, BELT-TREAD or CARCASS-LINER, or no such layer."),
    "check_partial": True,
    "max_problems": 3,  # the primary objective and up to two secondary ones
    "focus": _UNDER_BELT_FOCUS,
    "white_space_support": 5,
    "long_text": True,  # claimed windows and complexity come from the independent claims: read them whole
    "extra_fields": _cats([
        ("Placement", "Where the layer sits radially. One of: BELT-CARCASS = between the belt package and the "
                      "carcass or body ply | INTER-BELT = between belt layers | BELT-TREAD = between belt and tread "
                      "(undertread, tread base, cushion) | CARCASS-LINER = between carcass and inner liner (tie gum) | "
                      "UNCLEAR = not stated"),
        ("Coverage", "Extent of the layer across the belt width. One of: WHOLE_WIDTH = continuous across the belt "
                     "package width and crossing the equator (entire, whole or full width, from one belt end to the "
                     "other, at least a belt width); the thickness may vary | SEGMENTED = two or more separate pieces "
                     "across the width (e.g. centre and shoulder pieces, pieces with gaps), or divided laterally or "
                     "circumferentially | CENTRE_ONLY = a single piece in the crown centre | EDGE_ONLY = pieces only "
                     "at the belt edges | UNCLEAR = extent not stated"),
        ("Primary objective", "The main objective of the layer. One of: " + " | ".join(_OBJECTIVES)),
        ("Thickness profile", "Thickness of the layer across the width. One of: Uniform | Varies across width | "
                              "Not stated"),
        ("Segment details", "For a segmented layer: number of segments, positions, gaps, circumferential division, "
                            "verbatim where possible; 'not stated' otherwise"),
    ]),
    "analysis_questions": [
        ("How many families are analysed, and how do they split between Coverage WHOLE_WIDTH and SEGMENTED over "
         "time? Chart: families per 3-year bucket of 'Year' (priority year), stacked by Coverage, other values kept "
         "as their own category."),
        ("Who are the top 15 assignees, and how many of their families are alive? Chart: families per 'Assignee "
         "(normalised)' stacked by 'Alive (from legal status)'; table with families, alive families and families "
         "with TBR in 'Segment' per assignee."),
        ("Which assignees are new entrants (first family in this set in the last 5 complete priority years), and "
         "with how many families? Also a heatmap of the top 10 assignees by 3-year priority-year bucket."),
        ("Which objectives drive WHOLE_WIDTH and which drive SEGMENTED layers? Table of Coverage × 'Objective' (split "
         "on '; ') and Coverage × 'Primary objective'. How many SEGMENTED families have Tyre uniformity correction "
         "as primary objective, and how many name it at all?"),
        ("Which segment layouts are claimed? Table of the SEGMENTED families: Patent ID, assignee, Year, alive, "
         "'Segment details', 'Thickness profile'. Group the layouts (number of pieces, centre and shoulder pieces, "
         "gaps, circumferential division) and count them, overall and among alive families."),
        ("Which parameter windows are claimed? Table of each family whose 'Key parameters' states a value: Patent "
         "ID, assignee, Coverage, alive and the window verbatim (no unit conversion). Type each window by keywords "
         "(layer thickness, centre vs shoulder thickness, width vs belt width, segment width or gap, modulus, "
         "hardness, tanδ, other). Chart: families per type, WHOLE_WIDTH vs SEGMENTED. Where do windows overlap?"),
        ("Which technology groups and complexity levels serve the footprint and contact-pressure objectives? Counts "
         "for families whose 'Objective' includes Footprint shape or Uniform contact pressure; chart of all families "
         "per 3-year priority-year bucket by Complexity, from Basic to System-level."),
        ("Which alive families matter most for a TBR under-belt layer? Shortlist of up to 15 for footprint, pressure "
         "uniformity or uniformity correction (prefer TBR, these objectives, higher complexity, newer): Patent ID, "
         "assignee, Year, Coverage, Complexity, 'Key parameters', and one line on why it matters from 'Core claim'."),
        ("Which families expire within 3 years? Use an expiry or expected-expiration date column of the export (if "
         "there is none, say so). Also list the families with a priority or publication date before 2005."),
        ("What share of families has 'Segment' Not specified? If above 30 %, re-derive the tyre category from the "
         "export's CPC codes (heavy-duty or construction-vehicle indexing), tyre sizes in the text (e.g. "
         "295/80R22.5, 11R22.5, 315/80R22.5) and terms (truck, bus, heavy load, construction vehicle, passenger "
         "car), and report the share before and after. Chart: families by tyre category × Coverage."),
        ("How does the share of each objective change over time? Per 5-year priority-year bucket, the share of "
         "families naming each objective in 'Objective' (split on '; '), with n per bucket, as a chart."),
        ("How do families divide by publication authority ('Office')? Overall and for the top 5 assignees, as a "
         "table and a chart."),
    ],
    "analysis_rules": (
        "- Count patent families: one row is one family. Put n (families counted) in every chart title and table "
        "caption, and label both axes.\n"
        "- Keep Not stated, Not specified and UNCLEAR as their own categories; never drop them.\n"
        "- 'Objective' holds the primary and secondary objectives separated by '; ': split before counting; a "
        "family counts under each.\n"
        "- Priority-year buckets of 3 years unless the question says otherwise; the last 18 months of priority years "
        "are incomplete (publication lag): say so, never read them as a decline.\n"
        "- Alive means 'Alive (from legal status)' = Yes; without that column, say alive counts are not possible.\n"
        "- Quote claimed values verbatim; convert units only when unambiguous.\n"
        "- Mark any finding that rests on fewer than 10 families as small n.\n"
        "- Name patents by their Patent ID."),
    "summary_format": (
        "1. **Focus findings** (5 to 7 bullets) on the focus set, the patents whose objectives include Footprint "
        "shape, Uniform contact pressure or Tyre uniformity correction: its size and share; the leading assignees "
        "with their alive counts; the whole-width vs segmented split; the key claimed parameter windows; the most "
        "relevant alive patents by Patent ID.\n"
        "2. **Landscape context** (at most 3 bullets): overall size, trend and players.\n"
        "3. **White space** (at most 3 bullets): only combinations the facts list as white space for the focus "
        "objectives; write \"insufficient data\" where the facts say so; never present a zero cell as an "
        "opportunity.\n"
        "4. **Caveats** (at most 3 lines).\n"
        "Rules: lead with the answer to the topic question, not with whichever category is most numerous. Patents "
        "set aside to check and off-topic patents do not shape the findings. Make no segment-level statement (e.g. "
        "\"X dominates TBR\") when more than 30 % of the patents name no segment. Cite publication numbers."),
    "brief_notes": (
        "Role: patent landscape analyst with tyre construction expertise, writing for the tyre designers and R&D "
        "managers of a TBR predevelopment team (design direction, freedom-to-operate screening). Direct, technical "
        "language, no promotional wording; every finding traces to publication numbers; landscape analysis, not a "
        "legal opinion.\nCounting: patent families; n on every chart; Not stated / UNCLEAR kept as categories; "
        "findings on fewer than 10 records flagged as small n; the last 18 months of priority years are incomplete."),
    "questions": ["Search queries: which database queries produced this export? (They are listed in the method "
                  "section; say whether one query targeted a sub-topic, which may then be over-represented.)"],
    "report_sections": ["📊 Executive summary", "📈 Filing trends and geography", "🏢 Assignees and legal status",
                        "🧠 Technology groups, complexity and topic clusters", "🔮 White-space assessment"],
    "keywords": ["cushion rubber", "under-belt cushion", "belt under cushion", "pad rubber", "buffer rubber",
                 "buffer layer", "squeegee", "gum strip", "intermediate rubber layer", "interlayer",
                 "decoupling rubber", "between the carcass reinforcement and the crown reinforcement"],
    "classification_codes": ["B60C 9/18 (belts, breakers, crown-reinforcing or cushioning layers)",
                             "B60C 9/20 (belts built up from superimposed layers)", "B60C 9/02 (carcass)",
                             "B60C 2200/06 (heavy-duty vehicle tyres)"],
}
PRESETS["Under-belt layer (belt–carcass interlayer)"] = UNDER_BELT_LAYER


def preset_names() -> list[str]:
    return list(PRESETS)


# Vehicle segments for every objective: without a list the model fills "Segment" freely, and once wrote tyre
# components there (belt, tread) for a lightweighting study.
DEFAULT_SEGMENTS = ["Passenger car", "SUV & light truck", "Truck & bus", "Off-road & agricultural", "Two-wheeler"]


def preset_plan(name: str) -> AnalysisPlan:
    """A fresh, editable plan for the objective."""
    spec = PRESETS[name]
    plan = AnalysisPlan(
        title=spec["title"], objective=spec["objective"], problem_label=spec["problem_label"],
        segments=list(spec.get("segments", DEFAULT_SEGMENTS)),
        problems=list(spec["problems"]), tech_buckets=list(spec["tech_buckets"]),
        complexity_levels=list(spec["complexity_levels"]), in_scope=spec["in_scope"],
        extra_fields=list(spec["extra_fields"]), analysis_questions=list(spec["analysis_questions"]),
        keywords=list(spec["keywords"]), classification_codes=list(spec["classification_codes"]),
        report_sections=list(spec.get("report_sections", DEFAULT_SECTIONS)), source="preset",
        questions=list(spec.get("questions", [])), focus=list(spec.get("focus", [])),
        max_problems=spec.get("max_problems", 0), relevance_rule=spec.get("relevance_rule", ""),
        check_partial=spec.get("check_partial", False), white_space_support=spec.get("white_space_support", 0),
        summary_format=spec.get("summary_format", ""), analysis_rules=spec.get("analysis_rules", ""),
        long_text=spec.get("long_text", False),
    )
    plan.brief = preset_brief(name, plan)
    return plan


def preset_brief(name: str, plan: AnalysisPlan) -> str:
    """The plan as a brief, so the report text has a brief to follow."""
    notes = PRESETS.get(name, {}).get("brief_notes", "")
    lines = [f"Patent landscape: {plan.title} (standard objective: {name}).", "", f"Objective: {plan.objective}",
             f"Scope: {plan.in_scope}", f"Segments: {', '.join(plan.segments)}", "",
             f"{plan.problem_label} categories:"]
    if notes:
        lines[1:1] = ["", notes]
    lines += [f"- {c.name}: {c.description}" for c in plan.problems]
    if plan.focus:
        lines.append(f"Focus {plan.problem_label.lower()}s: {', '.join(plan.focus)}")
    lines += ["", "Technology groups:"] + [f"- {c.name}: {c.description}" for c in plan.tech_buckets]
    # The relevance rule reaches the model that reads the patents and the summary's structure the summary's writer,
    # both directly: the brief, cut at 5,000 characters for the report text, leaves them out.
    if plan.check_partial:
        lines += ["", "Patents rated Partial are set aside for a manual check and not counted."]
    questions = [f"- {q}" for q in plan.analysis_questions]
    tail = ["", "OUTPUT FORMAT"] + [f"- {s}" for s in plan.report_sections]
    if len("\n".join(lines + questions + tail)) > BRIEF_CHARS:  # the report text reads the first 5,000 characters
        questions = [f"- {_first_sentence(q)}" for q in plan.analysis_questions]
    return "\n".join(lines + ["", "Specific questions to answer:"] + questions + tail)


BRIEF_CHARS = 5000  # what narrative.write_sections passes on of a brief


def _first_sentence(text: str) -> str:
    """A question up to its first question mark or full stop (the data agent gets the whole question)."""
    for i, char in enumerate(text):
        if char in "?." and (i + 1 == len(text) or text[i + 1] == " ") and i > 20:
            return text[: i + 1]
    return text
