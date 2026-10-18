"""Synthetic patent export in the column format of the user's database export (for tests and demos)."""

from __future__ import annotations

import json
import random
import re

import pandas as pd

THEMES = {
    "geometry": {
        "titles": ["Pneumatic tire with shoulder groove geometry for uniform wear",
                   "Truck tire tread pattern suppressing shoulder step wear",
                   "Tire with chamfered rib edges reducing heel and toe wear",
                   "Tread with three-dimensional sipes for even contact pressure"],
        "abstract": "A tread pattern with {a} shoulder grooves and sipes of depth ratio {b} distributes ground contact "
                    "pressure evenly across the rib, suppressing uneven wear such as shoulder wear and cupping.",
        "claim": "A pneumatic tire comprising a tread portion having circumferential main grooves and shoulder "
                 "lateral grooves, wherein the groove depth ratio is {b} to reduce uneven wear.",
        "problem": "Shoulder wear and river wear in heavy duty tires.",
        "method": "Optimised groove and sipe geometry in the shoulder region.",
    },
    "belt": {
        "titles": ["Heavy duty radial tire with belt package controlling crown deformation",
                   "Tire with circumferential reinforcing belt layer for even wear",
                   "Belt structure with zero degree cord layer reducing shoulder wear"],
        "abstract": "A belt package with a {a} degree cord angle and a circumferential reinforcing layer restrains "
                    "radial growth of the crown, preventing uneven wear such as shoulder and center wear.",
        "claim": "A heavy duty pneumatic tire comprising a carcass and a belt layer including steel cords inclined "
                 "at {a} degrees relative to the tire circumferential direction.",
        "problem": "Crown growth causing uneven wear in TBR tires.",
        "method": "Belt cord angle and circumferential belt layer design.",
    },
    "compound": {
        "titles": ["Tread rubber composition with silica for wear resistance",
                   "Rubber composition containing functionalised SBR and silica for tread",
                   "Tire tread compound with high dispersion silica and resin"],
        "abstract": "A tread rubber composition comprising {a} phr of silica, a silane coupling agent and a "
                    "functionalised styrene butadiene rubber improves abrasion resistance and reduces irregular wear.",
        "claim": "A rubber composition for a tire tread comprising 100 parts by mass of a diene rubber and {a} "
                 "parts by mass of silica having a BET surface area of {b} m2/g.",
        "problem": "Poor abrasion resistance leading to irregular wear.",
        "method": "Silica filled tread compound with coupling agent.",
    },
    "process": {
        "titles": ["Method for curing a tire with uniform tread thickness",
                   "Tire manufacturing method controlling green tire uniformity",
                   "Mixing process for consistent tread compound properties"],
        "abstract": "A manufacturing method controls curing temperature and press pressure so that tread gauge "
                    "variation stays below {b} mm, which prevents flat spotting and uneven wear in service.",
        "claim": "A method for manufacturing a pneumatic tire comprising vulcanising a green tire in a mold at a "
                 "temperature of {a} degrees C so that tread gauge variation is reduced.",
        "problem": "Manufacturing non-uniformity causing uneven wear.",
        "method": "Controlled curing and building process.",
    },
    "sensor": {
        "titles": ["System for predicting tread wear using tire mounted sensors",
                   "Tire wear estimation with machine learning and TPMS data",
                   "Method of detecting irregular wear from tire acceleration signals"],
        "abstract": "An in-tire sensor measures acceleration and pressure; a machine learning model estimates tread "
                    "depth and detects irregular wear patterns such as cupping {a} days before visual detection.",
        "claim": "A tire wear estimation system comprising a sensor mounted on an inner liner, a processor and a "
                 "model trained to estimate tread wear state from acceleration signals.",
        "problem": "Late detection of irregular tire wear in fleets.",
        "method": "Sensor data with machine learning wear prediction.",
    },
    "noise": {
        "titles": ["Conductive rubber strip for tire static discharge",
                   "Bead core structure for easy rim mounting",
                   "Inner liner composition with low air permeability"],
        "abstract": "A conductive strip of {a} mm width discharges static electricity from the vehicle through the "
                    "tread to the road surface.",
        "claim": "A tire comprising a conductive rubber member extending from the bead to the tread surface.",
        "problem": "Static electricity build up.",
        "method": "Conductive rubber path.",
    },
}
WEIGHTS = {"geometry": 0.26, "belt": 0.18, "compound": 0.2, "process": 0.1, "sensor": 0.14, "noise": 0.12}
ASSIGNEES = [
    "Compagnie Generale Des Etablissements Michelin", "MICHELIN RECHERCHE ET TECHNIQUE S.A.",
    "Bridgestone Corporation", "BRIDGESTONE CORP", "The Goodyear Tire & Rubber Company",
    "Continental Reifen Deutschland GmbH", "Sumitomo Rubber Industries, Ltd.", "Yokohama Rubber Co Ltd",
    "Toyo Tire Corporation", "Hankook Tire & Technology Co., Ltd.", "Apollo Tyres Ltd",
    "Pirelli Tyre S.p.A.", "Zhongce Rubber Group Co Ltd",
]
OFFICES = ["EP", "US", "CN", "JP", "WO", "KR"]


def make_export(n: int = 300, seed: int = 0, with_family: bool = False, duplicate_share: float = 0.08) -> pd.DataFrame:
    rng = random.Random(seed)
    themes = list(WEIGHTS)
    rows = []
    for i in range(n):
        theme = rng.choices(themes, weights=[WEIGHTS[t] for t in themes])[0]
        t = THEMES[theme]
        a, b = rng.randint(5, 80), round(rng.uniform(0.2, 0.9), 2)
        year = rng.randint(2012, 2025)
        if theme == "sensor":  # electronic solutions grow over time
            year = rng.randint(2018, 2025)
        month, day = rng.randint(1, 12), rng.randint(1, 28)
        style = i % 3
        date = (f"{year}-{month:02d}-{day:02d}" if style == 0 else
                f"{year}{month:02d}{day:02d}" if style == 1 else
                pd.Timestamp(year, month, day).strftime("%B %d, %Y"))
        office = rng.choice(OFFICES)
        number = f"{office}{rng.randint(1000000, 9999999)}{'B2' if office == 'US' else 'A1'}"
        assignee = rng.choice(ASSIGNEES)
        if rng.random() < 0.08:
            assignee += "; Technical University of Munich"
        segment = rng.choice(["heavy duty truck and bus", "passenger car", "pneumatic"])
        rows.append({
            "Record Number": number,
            "Publication/Issue Date": date,
            "Title": rng.choice(t["titles"]),
            "Abstract": t["abstract"].format(a=a, b=b) + f" The tire is intended for {segment} applications.",
            "Assignee": assignee,
            "Problem Being Solved (AI Sum.)": t["problem"],
            "Method Used (AI Sum.)": t["method"],
            "Advantages (AI Sum.)": "Improved uniformity of wear and longer tread life.",
            "Claims": "1. " + t["claim"].format(a=a, b=b) + " 2. The tire according to claim 1, wherein the "
                      "ratio is between 0.3 and 0.7. 3. The tire according to claim 1, further comprising a sensor.",
            "_theme": theme,
        })
        if with_family:
            rows[-1]["Family ID"] = f"F{i:05d}"
    df = pd.DataFrame(rows)
    # Same invention published again (another office or a re-publication).
    dups = df.sample(frac=duplicate_share, random_state=seed).copy()
    if with_family:
        dups["Record Number"] = dups["Record Number"].str.replace(r"^[A-Z]{2}", "US", regex=True)
    df = pd.concat([df, dups], ignore_index=True)
    return df


def name_clusters_reply(prompt: str, groups: list[str]) -> str:
    """What a well-behaved model answers to a cluster-naming prompt: one named object per cluster."""
    ids = [int(line.split()[1]) for line in prompt.splitlines() if line.startswith("Cluster ")]
    keywords = {int(line.split()[1]): line.split("keywords:", 1)[1].strip()
                for line in prompt.splitlines() if line.startswith("Cluster ")}
    out = []
    for i in ids:
        words = keywords[i]
        group = (groups[3] if "sensor" in words or "acceleration" in words else
                 groups[1] if "silica" in words or "rubber" in words else
                 groups[2] if "curing" in words or "manufacturing" in words else groups[0])
        out.append({"id": i, "label": f"Topic {i}: {words.split(',')[0]}", "description": f"Patents about {words}.",
                    "tech_group": group, "relevance": "high" if group != groups[0] or "wear" in words else "partial"})
    return json.dumps(out)


_EXTRACT_RULES = [  # (words in the patent text, theme)
    (("sensor", "acceleration", "machine learning", "tpms"), "sensor"),
    (("conductive", "static", "rim mounting", "air permeability"), "noise"),
    (("silica", "rubber composition", "compound"), "compound"),
    (("curing", "manufacturing", "mixing process", "vulcanis"), "process"),
    (("belt", "cord angle", "crown"), "belt"),
    (("groove", "sipe", "rib", "shoulder"), "geometry"),
]
_THEME_ANSWERS = {  # (technology group position, complexity position, problem words, component, lever)
    "geometry": (0, 0, "Shoulder wear", "tread pattern", "groove depth ratio"),
    "belt": (0, 1, "Shoulder wear; Cupping", "belt package", "belt cord angle"),
    "compound": (1, 1, "Chip-and-flake", "tread compound", "silica content"),
    "process": (2, 1, "Flat-spotting", "curing process", "curing temperature"),
    "sensor": (3, 2, "Cupping", "in-tire sensor", "wear prediction model"),
    "noise": (0, 0, "Not specified", "conductive strip", "conductive path"),
}


def choices_in(system: str) -> dict[str, list[str]]:
    """The allowed values per field, read back from an extraction system prompt."""
    out = {}
    for line in system.splitlines():
        m = re.match(r'- "(\w+)": .*?(?:Exactly one of|One or more of): (.*)$', line)
        if m:
            body = m.group(2).split(' (separate several')[0]
            out[m.group(1)] = [re.sub(r"\s*\(.*$", "", o).strip() for o in body.split("; ")]
    return out


def extract_reply(system: str, user: str, drop: set[int] = frozenset()) -> str:
    """What a well-behaved model answers to an extraction prompt (patents in `drop` are left out)."""
    opts = choices_in(system)
    blocks = re.split(r"\n\n(?=P\d+ \| )", user.strip())
    out = []
    for block in blocks:
        ref = int(re.match(r"P(\d+)", block).group(1))
        if ref in drop:
            continue
        low = block.lower()
        theme = next((t for words, t in _EXTRACT_RULES if any(w in low for w in words)), "geometry")
        group, level, problem, component, lever = _THEME_ANSWERS[theme]
        segment = "TBR" if "truck" in low else "PCR" if "passenger" in low else "Not specified"
        item = {"ref": f"P{ref}", "topic": f"{component} for even wear",
                "segment": segment if segment in opts.get("segment", [segment]) else "Not specified",
                "problem": problem, "tech_group": opts.get("tech_group", ["?"] * 4)[group],
                "complexity": opts.get("complexity", ["?"] * 3)[min(level, len(opts.get("complexity", [0])) - 1)],
                "component": component, "performance_target": "uniform wear" if theme != "noise" else "static discharge",
                "design_lever": lever, "mechanism": f"Changing the {lever} evens out contact pressure.",
                "core_claim": f"A tire whose {component} has a defined {lever}.",
                "key_parameters": (re.findall(r"\d+(?:\.\d+)? (?:phr|degrees|mm)", block) or ["none"])[0],
                "trade_offs": "not stated", "relevance": "Low" if theme == "noise" else "High",
                "confidence": "High"}
        for key in re.findall(r'- "(x_\w+)":', system):
            item[key] = (f"{5 + (ref * 7) % 20} %" if "claimed" in key and theme != "noise" else
                         component if "component" in key else "not stated")
        out.append(item)
    return json.dumps(out)


def narrative_reply(system: str, user: str, invent: bool = False) -> str:
    """A section text that quotes numbers from the facts it was given (and one made-up number if asked)."""
    heading = re.search(r"Section to write: (.*)", user).group(1).strip()
    facts = user.split("Facts:\n", 1)[1]
    found = re.findall(r"(?<![\w.,])\d+(?:\.\d+)?(?![\w])", facts)
    numbers = [n for n in found if float(n) > 10][:2] or found[:1] or ["several"]
    lines = [f"## {heading}", f"- **Main finding:** the facts show {numbers[0]} for the leading item.",
             f"- **Second finding:** another figure is {numbers[-1]}."]
    if invent:
        lines.append("- **Unsupported:** 987.5 patents mention it.")
    closing = "**Recommended R&D directions:**\n- Look at the gaps." if "executive summary" in system else \
        "**Implication for R&D:** focus where the gaps are."
    return "\n".join(lines + [closing])


def agent_reply(messages: list[dict]) -> str:
    """The data agent's turn: count patents per technology group with a chart, then answer from the output."""
    last = messages[-1]["content"]
    if "Status: success" in last:
        rows = re.findall(r"^(\S[^\n]*?)\s{2,}(\d+)$", last, re.MULTILINE)
        name, count = rows[0] if rows else ("n/a", "0")
        return f"Final Answer: {name.strip()} has the most relevant patents, {count}."
    return ("Counting relevant patents per technology group.\n```python\n"
            "d = df[df['Relevance'] != 'Low']\n"
            "t = d['Technology group'].value_counts()\n"
            "import plotly.express as px\nshow(px.bar(t, title='Relevant patents per technology group'))\n"
            "print(t.to_string())\n```")
