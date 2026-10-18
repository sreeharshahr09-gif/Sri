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
                  "the routes, materials and design levers used, and which companies use which.",
        "problem_label": "Weight-reduction route",
        "problems": _cats([
            ("Gauge & volume reduction", "thinner tread, sidewall or innerliner gauges; less rubber volume"),
            ("Lighter reinforcement", "steel replaced by aramid, PET, nylon, hybrid or carbon-fibre cords; fewer or thinner cords"),
            ("Bead & apex mass reduction", "lighter bead cores, smaller or no apex"),
            ("Low-density compounds & fillers", "lower-density polymers or fillers, reduced filler loading"),
            ("Ply & belt architecture", "fewer plies, new belt packages, spiral or monoply layers"),
            ("Innerliner & barrier", "thinner or higher-barrier innerliners and films"),
            ("New architectures", "airless or non-pneumatic tyres, integrated tyre-wheel concepts"),
            ("Manufacturing-enabled", "processes that make thinner or more uniform components possible"),
        ]),
        "tech_buckets": _cats([
            ("Reinforcement materials", "cords, fabrics, bead wire and their adhesion"),
            ("Rubber compounds & fillers", "polymers, fillers, oils, resins, cure systems"),
            ("Structural design", "construction, contour, ply and belt layout, gauges"),
            ("Manufacturing process", "building, calendering, curing and other production methods"),
            ("Novel architecture", "non-pneumatic and other new tyre concepts"),
        ]),
        "complexity_levels": _levels("gauge or geometry change within an existing construction",
                                  "material substitution within the existing construction (new cord, compound)",
                                  "new architecture or material system that needs new processes or equipment"),
        "in_scope": "Patents on tyres or tyre components whose purpose or effect includes lower tyre mass. Not in "
                 "scope: wheels, brakes or other vehicle parts; material patents with no tyre application.",
        "extra_fields": _cats([
            ("Weight saving claimed", "the weight saving as stated, with unit (% , g or kg); 'not stated' if none"),
            ("Component lightened", "the tyre component made lighter"),
            ("Material replaced -> by", "e.g. 'steel cord -> aramid cord'; 'not stated' if none"),
            ("Performance kept or traded", "attributes kept or traded off: rolling resistance, durability, handling, noise"),
        ]),
        "analysis_questions": [
            ("For each weight-reduction route, how many patents state a numeric weight saving, and what are the "
             "median and range of the stated percentages?"),
            "Which weight-reduction routes do the five largest assignees use? Show patents per assignee and route.",
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
        "problem_label": "Rolling-resistance lever",
        "problems": _cats([
            ("Tread compound hysteresis", "silica/silane systems, functionalised polymers, resins lowering tan δ at 60 °C"),
            ("Base & under-tread compound", "low-loss base or cap-base designs"),
            ("Sidewall & bead flexing", "lower-loss sidewall, apex and bead compounds or geometry"),
            ("Belt & carcass deformation", "belt and carcass constructions that reduce deformation losses"),
            ("Contour & footprint shape", "profile and footprint optimisation"),
            ("Tread pattern stiffness", "block and rib stiffness, groove volume, tread depth"),
            ("Mass reduction", "lighter tyres lowering rolling loss"),
            ("Air retention", "innerliners and sealing that keep inflation pressure"),
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
            ("For each rolling-resistance lever, how many patents state a numeric improvement, and what are the "
             "typical stated values?"),
            "Which assignees rely mostly on compound levers and which on structural levers?",
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
        "objective": "Map patented routes to renewable and recycled tyre materials: the routes, the materials and "
                  "their claimed content, the performance kept or traded, and who leads.",
        "problem_label": "Sustainability route",
        "problems": _cats([
            ("Renewable polymers", "bio-based rubber: guayule, dandelion, bio-butadiene and others"),
            ("Bio-based fillers & oils", "rice-husk silica, lignin, vegetable oils and resins"),
            ("Recycled rubber", "reclaim, devulcanisation, crumb rubber"),
            ("Recovered carbon black", "pyrolysis and recovered carbon black"),
            ("Recycled or bio reinforcement", "recycled PET, bio-based polyamide and other cords"),
            ("Cleaner processing", "lower-energy or lower-emission production"),
            ("Longer life & retreading", "durability and retreadability that extend tyre life"),
            ("Wear particle reduction", "less tyre and road wear particles"),
        ]),
        "tech_buckets": _cats([
            ("Polymers & elastomers", "base polymers"),
            ("Fillers & additives", "fillers, oils, resins, additives"),
            ("Reinforcement materials", "cords and fabrics"),
            ("Processing & recycling technology", "recycling and production processes"),
            ("Product design", "design for longevity, retreading and recycling"),
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
            "How have renewable versus recycled routes developed per year?",
            "What sustainable content is claimed (as stated) for each route?",
            "Which feedstocks are named most often?",
            "Which assignees lead for each route?",
        ],
        "keywords": ["sustainable", "renewable", "bio-based", "recycled", "devulcanized", "recovered carbon black",
                  "guayule", "dandelion"],
        "classification_codes": ["C08L 7/00 (natural rubber)", "C08J 11/00 (recycling)", "C08K (fillers)",
                              "B60C 1/00 (rubber compositions)", "C09C 1/48 (carbon black)"],
    },
}


def preset_names() -> list[str]:
    return list(PRESETS)


def preset_plan(name: str) -> AnalysisPlan:
    """A fresh, editable plan for the objective."""
    spec = PRESETS[name]
    plan = AnalysisPlan(
        title=spec["title"], objective=spec["objective"], problem_label=spec["problem_label"],
        problems=list(spec["problems"]), tech_buckets=list(spec["tech_buckets"]),
        complexity_levels=list(spec["complexity_levels"]), in_scope=spec["in_scope"],
        extra_fields=list(spec["extra_fields"]), analysis_questions=list(spec["analysis_questions"]),
        keywords=list(spec["keywords"]), classification_codes=list(spec["classification_codes"]),
        report_sections=list(DEFAULT_SECTIONS), source="preset",
    )
    plan.brief = preset_brief(name, plan)
    return plan


def preset_brief(name: str, plan: AnalysisPlan) -> str:
    """The plan as a brief, so the report text has a brief to follow."""
    lines = [f"Patent landscape: {plan.title} (standard objective: {name}).", "", f"Objective: {plan.objective}",
             f"Scope: {plan.in_scope}", "", f"{plan.problem_label} categories:"]
    lines += [f"- {c.name}: {c.description}" for c in plan.problems]
    lines += ["", "Technology groups:"] + [f"- {c.name}: {c.description}" for c in plan.tech_buckets]
    lines += ["", "Specific questions to answer:"] + [f"- {q}" for q in plan.analysis_questions]
    lines += ["", "OUTPUT FORMAT"] + [f"- {s}" for s in plan.report_sections]
    return "\n".join(lines)
