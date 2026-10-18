"""Instant, model-free keyword matching between a job description and a CV (ATS-style).

Keywords are phrases from the JD's responsibilities/requirements (company blurbs and benefits are
skipped), found with a RAKE-style split at stop words and punctuation. Matching is literal, with
small tolerances (plural/singular, hyphen/space). Everything is deterministic and runs in
milliseconds, so the score can be refreshed after every edit.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

STOP = set("""a about above across after again against all also am an and any are as at be because been
before being below between both but by can could did do does doing down during each either etc
for from further had has have having he her here hers herself him himself his how i if in into is
it its itself just me more most my myself no nor not now of off on once only or other our ours
ourselves out over own same she should so some such than that the their theirs them themselves then
there these they this those through to too under until up upon very via was we were what when where
which while who whom why will with within without would you your yours yourself yourselves us
may might must shall per e.g i.e ie eg like well""".split())

# Words that say nothing about a skill on their own (job-ad boilerplate).
GENERIC = set("""ability abilities able experience experiences experienced year years skill skills
knowledge understanding strong solid good great excellent outstanding proven demonstrated
demonstrate passion passionate team teams teamwork role roles position candidate candidates company
companies work working works job jobs including include includes related relevant preferred
required require requires requirement requirements responsibility responsibilities qualification
qualifications minimum desirable desired ensure ensuring deliver delivering support supporting drive
driving help new high key area areas field fields discipline disciplines level levels various
multiple based part play make need needs way world future today people opportunity opportunities
environment culture business priorities impact expertise excellence plus bonus nice ideal ideally
candidate's highly self motivated detail oriented etc using use used apply applying across early
career professional brilliant minds solve complex challenges shape advancing advance areas
activities activity collaborate collaborating collaboration may also other others least one two
three four five six seven eight nine ten first second order may hands including excellent
innovation innovative passion high-impact degree degrees capability capabilities decisions""".split())
# Fine inside a phrase ("system integration", "machine design") but meaningless alone.
SINGLE_DROP = GENERIC | set("""technology technologies design designs integration solutions solution
systems system development machine machines reviews review structural robotic mechanical precision
process processes engineering engineers engineer analysis analyses testing applications""".split())

LEAD_STRIP = {"strong", "solid", "good", "excellent", "proven", "demonstrated", "deep", "sound",
              "familiarity", "proficiency", "proficient", "expert", "expertise", "experience",
              "knowledge", "understanding", "working", "hands-on", "innovative", "complete",
              "including", "relevant", "related", "key", "critical", "ensure", "ensuring"}
# Imperative verbs that open JD bullets ("Design robotic platforms" -> "robotic platforms").
LEAD_VERBS = {"design", "develop", "drive", "own", "conduct", "define", "manage", "collaborate",
              "support", "deliver", "delivering", "ensure", "perform", "lead", "build", "create",
              "provide", "maintain", "work", "play", "apply", "evaluate", "implement", "establish",
              "identify", "improve", "optimize", "optimise", "coordinate", "partner", "contribute",
              "participate", "prepare", "review", "execute", "plan", "use", "utilize", "bring", "turn"}
# Words that are almost never nouns, so they can be stripped mid-line too ("... and turn test data").
# "design", "review", "plan" etc. stay: "design reviews" is a real keyword.
PURE_VERBS = LEAD_VERBS - {"design", "drive", "own", "support", "lead", "build", "work", "play",
                           "review", "plan", "partner", "use"}

_FOCUS_WORDS = re.compile(
    r"responsibilit|requirement|required|qualification|skills|duties|what you.?ll|what you will|"
    r"you will|your role|the role|the job|position|looking for|bring|must.?have|nice.?to.?have|"
    r"preferred|desired|design challenges|job description|about you|who you are|accountabilit|"
    r"essential|experience|competenc|profile", re.I)
_SKIP_WORDS = re.compile(
    r"about us|about the company|who we are|our company|our team|benefit|perk|what we offer|we offer|"
    r"compensation|salary|pay range|base pay|equal opportunit|\beeo\b|diversity|inclusion|how to apply|"
    r"apply now|our commitment|why join|group/division|division|our culture|life at|"
    r"privacy|disclaimer", re.I)


def section_heading(line: str) -> str | None:
    """'focus' for responsibility/requirement headings, 'skip' for company/benefit headings."""
    text = line.strip().lstrip("#").strip().strip("*_").strip()
    if not text or len(text) > 60 or len(text.split()) > 7 or text[0] in "-•*●▪◦" or \
            text.endswith((".", ",", ";")):
        return None
    if _SKIP_WORDS.search(text) or (re.match(r"(?i)about\b", text) and not re.search(r"(?i)role|you|job|position", text)):
        return "skip"
    if _FOCUS_WORDS.search(text):
        return "focus"
    return None


# kept for callers that test single headings
FOCUS_HEADINGS = re.compile(r"(?!x)x")
SKIP_HEADINGS = re.compile(r"(?!x)x")


_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#./&'-]*[A-Za-z0-9+#]|[A-Za-z0-9]")


@dataclass
class Keyword:
    phrase: str          # display form, as written in the JD
    count: int           # occurrences in the JD
    weight: float = 1.0  # 2 for the model's must-have requirement terms
    source: str = "JD"   # "JD" (extracted) | "requirement" (from the model's analysis) | "you"


def focus_text(jd: str) -> str:
    """The JD's responsibilities/requirements; company blurbs and benefits are dropped."""
    lines = jd.splitlines()
    kinds = [section_heading(l) for l in lines]
    title = next((l for l in lines if l.strip()), "")
    head = title + "\n" if len(title.strip()) <= 80 else ""
    if "focus" in kinds:
        out, keep = [], False
        for line, kind in zip(lines, kinds):
            if kind:
                keep = kind == "focus"
            if keep:
                out.append(line)
        focus = "\n".join(out)
        if len(focus) >= min(200, 0.25 * len(jd)):
            return head + focus
    # No usable structure: everything except company / benefits sections.
    out, keep = [], True
    for line, kind in zip(lines, kinds):
        if kind:
            keep = kind == "focus"
        if keep:
            out.append(line)
    return "\n".join(out)


def _norm(phrase: str) -> str:
    return re.sub(r"\s+", " ", phrase.lower().replace("-", " ")).strip()


def _candidates(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        for i, phrase in enumerate(_line_candidates(line)):
            words = phrase.split()
            verbs = LEAD_VERBS if i == 0 else PURE_VERBS
            while len(words) > 1 and words[0].lower() in verbs:
                words.pop(0)
            if words and not (len(words) == 1 and words[0].lower() in SINGLE_DROP):
                out.append(" ".join(words))
    return out


def _line_candidates(text: str) -> list[str]:
    phrases: list[str] = []
    for fragment in re.split(r"[,;:()\[\]{}!?\"“”•·|\n\t/]+|\.(?=\s|$)|\s[-–—]\s", text):
        current: list[str] = []
        for tok in _TOKEN.findall(fragment):
            low = tok.lower().strip("'")
            if low in STOP or low.isdigit() or "'" in low or "’" in low:
                if current:
                    phrases.append(" ".join(current))
                current = []
            else:
                current.append(tok.strip("'"))
        if current:
            phrases.append(" ".join(current))
    cleaned = []
    for p in phrases:
        words = p.split()
        while len(words) > 1 and words[0].lower() in LEAD_STRIP:
            words.pop(0)
        while words and words[-1].lower() in GENERIC:
            words.pop()
        if not words or len(words) > 4:
            continue
        if all(w.lower() in GENERIC for w in words) or (len(words) == 1 and words[0].lower() in SINGLE_DROP):
            continue
        if len(words) == 1 and (len(words[0]) < 3 and not words[0].isupper()):
            continue
        cleaned.append(" ".join(words))
    return cleaned


_QUALIFICATION_WORDS = re.compile(r"requirement|required|qualification|skills|must.?have|bring|"
                                  r"looking for|about you|who you are|preferred|desired|essential", re.I)


def _qualification_text(jd: str) -> str:
    out, keep = [], False
    for line in jd.splitlines():
        kind = section_heading(line)
        if kind:
            keep = kind == "focus" and bool(_QUALIFICATION_WORDS.search(line))
        if keep:
            out.append(line)
    return "\n".join(out)


def extract_keywords(jd: str, limit: int = 60) -> list[Keyword]:
    focus = focus_text(jd)
    qualification = _qualification_text(jd)
    found: dict[str, Keyword] = {}
    order: dict[str, int] = {}
    for phrase in _candidates(focus):
        key = _norm(phrase)
        if key not in found:
            found[key] = Keyword(phrase, 0)
            order[key] = len(order)
    for key, kw in found.items():
        kw.count = max(1, _count(jd, kw.phrase))
    # Single words that only ever appear inside a kept longer phrase add nothing.
    multi = [k for k in found if " " in k]
    for key in [k for k in found if " " not in k]:
        inside = sum(found[m].count for m in multi if re.search(rf"(?<!\w){re.escape(key)}(?!\w)", m))
        if inside and found[key].count <= inside:
            del found[key]

    def score(item: tuple[str, Keyword]) -> tuple[float, int]:
        key, kw = item
        words = key.split()
        acronym = any(w.isupper() and len(w) >= 2 for w in kw.phrase.split())
        required = 0.8 if qualification and _count(qualification, kw.phrase) else 0
        return (kw.count + (0.6 if 2 <= len(words) <= 3 else 0) + (0.5 if acronym else 0) + required,
                -order[key])

    ranked = sorted(found.items(), key=score, reverse=True)[:limit]
    return [kw for _, kw in sorted(ranked, key=lambda item: order[item[0]])]


def merge_requirement_terms(keywords: list[Keyword], requirements: list[dict]) -> list[Keyword]:
    """Add the model's requirement keywords (must = weight 2) to the extracted list."""
    by_norm = {_norm(k.phrase): k for k in keywords}
    for req in requirements or []:
        weight = 2.0 if req.get("priority") == "must" else 1.5
        for term in req.get("keywords", []):
            key = _norm(term)
            if key in by_norm:
                by_norm[key].weight = max(by_norm[key].weight, weight)
                by_norm[key].source = "requirement"
            else:
                kw = Keyword(term, 1, weight, "requirement")
                keywords.append(kw)
                by_norm[key] = kw
    return keywords


def _variants(phrase: str) -> list[str]:
    p = phrase.strip()
    out = {p, p.replace("-", " "), p.replace("-", "")}
    for v in list(out):
        low = v.lower()
        if low.endswith("ies"):
            out.add(v[:-3] + "y")
        elif low.endswith("s") and not low.endswith("ss"):
            out.add(v[:-1])
        elif low.endswith("y") and len(low) > 2 and low[-2] not in "aeiou":
            out.add(v[:-1] + "ies")
        else:
            out.add(v + "s")
    return [v for v in out if v.strip()]


def _pattern(phrase: str) -> str:
    return r"(?<![\w])" + re.escape(phrase).replace(r"\ ", r"[\s-]+") + r"(?![\w])"


def _count(text: str, phrase: str) -> int:
    """Occurrences of the phrase or a variant; overlapping variant matches count once."""
    best = 0
    for t in {text, re.sub(r"(?<=\w)\.(?=\w)", "", text)}:  # 'Ph.D.' also matches 'PhD'
        spans = set()
        for v in {v.lower() for v in _variants(phrase)}:
            for m in re.finditer(_pattern(v), t, re.I):
                if not any(m.start() < e and s < m.end() for s, e in spans):
                    spans.add((m.start(), m.end()))
        best = max(best, len(spans))
    return best


def status(cv: str, phrase: str) -> str:
    if _count(cv, phrase):
        return "matched"
    words = [w for w in re.findall(r"[A-Za-z0-9+#]+", phrase) if w.lower() not in STOP]
    if len(words) > 1 and all(_count(cv, w) for w in words):
        return "partial"
    return "missing"


def report(keywords: list[Keyword], cv: str) -> dict:
    rows, total, got = [], 0.0, 0.0
    for kw in keywords:
        st = status(cv, kw.phrase)
        credit = {"matched": 1.0, "partial": 0.5, "missing": 0.0}[st]
        total += kw.weight
        got += kw.weight * credit
        rows.append({"keyword": kw.phrase, "status": st, "in_jd": kw.count,
                     "in_cv": _count(cv, kw.phrase), "weight": kw.weight, "source": kw.source})
    stuffed = [r["keyword"] for r in rows if r["in_cv"] > 5]
    return {
        "score": round(100 * got / total, 1) if total else 0.0,
        "matched": [r["keyword"] for r in rows if r["status"] == "matched"],
        "partial": [r["keyword"] for r in rows if r["status"] == "partial"],
        "missing": [r["keyword"] for r in sorted(rows, key=lambda r: (-r["weight"], -r["in_jd"]))
                    if r["status"] == "missing"],
        "rows": rows, "stuffed": stuffed,
    }


COLORS = {"matched": "#bbf7d0", "partial": "#fde68a", "missing": "#fecaca"}


def highlight(text: str, keywords: list[Keyword], cv: str) -> str:
    """HTML of ``text`` with each keyword marked by its status in the CV."""
    spans: list[tuple[int, int, str]] = []
    for kw in sorted(keywords, key=lambda k: -len(k.phrase)):
        st = status(cv, kw.phrase)
        for v in _variants(kw.phrase):
            for m in re.finditer(_pattern(v), text, re.I):
                if all(m.end() <= s or m.start() >= e for s, e, _ in spans):
                    spans.append((m.start(), m.end(), st))
    spans.sort()
    out, pos = [], 0
    for s, e, st in spans:
        out.append(html.escape(text[pos:s]))
        out.append(f'<mark style="background:{COLORS[st]};color:#111;padding:0 2px;border-radius:3px" '
                   f'title="{st} in your CV">{html.escape(text[s:e])}</mark>')
        pos = e
    out.append(html.escape(text[pos:]))
    return "".join(out)
