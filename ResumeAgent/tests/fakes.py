"""A deterministic stand-in for llama-server, so tests run fully offline."""
from __future__ import annotations

import json
import re
from pathlib import Path

from llm_client import UsageStats

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
JD = (SAMPLES / "demo_job_description.txt").read_text(encoding="utf-8")
CV = (SAMPLES / "demo_resume.txt").read_text(encoding="utf-8")

# requirement text -> (priority, keywords, JD quote, evidence quote or None, status)
CATALOG = {
    "FEA for product durability": (
        "must", ["finite element analysis", "FEA"], "finite element analysis (FEA) for product durability",
        "Performed finite element analysis (FEA) to study product durability.", "supported"),
    "Python scripting for analysis and automation": (
        "must", ["Python scripting"], "Python scripting for analysis and automation",
        "Automated processing of engineering test data using Python scripting.", "supported"),
    "Technical reporting to stakeholders": (
        "must", ["technical reporting"], "technical reporting to engineering stakeholders",
        "Developed a technical report about structural durability methods.", "partial"),
    "Commercial C++ development": (
        "preferred", ["C++"], "commercial C++ software development",
        "Built a commercial C++ plug-in for test rigs.", "supported"),
    "Invented requirement": ("must", ["teleportation"], "teleportation experience required", None, "supported"),
}

EXPERIENCE_GOOD = """**Mechanical R&D Engineer** — Alpha Motors | 2020-2025
- Performed finite element analysis (FEA) to study product durability.
- Automated processing of engineering test data using Python scripting.
- Developed technical reporting on structural durability methods.

**Graduate Engineer** — Gamma Rail | 2018-2020
- Supported fatigue testing of bogie frames."""

EXPERIENCE_INVENTED = EXPERIENCE_GOOD.replace(
    "using Python scripting.", "using Python scripting, reducing test time by 40%.").replace(
    "durability methods.", "durability methods and shipped C++ tooling.")

SUMMARY = ("Mechanical R&D engineer experienced in finite element analysis (FEA) for product "
           "durability and Python scripting for test-data automation.")
SKILLS = """- **Simulation:** Abaqus, finite element analysis (FEA)
- **Programming:** Python scripting, MATLAB
- **Communication:** technical writing"""

LETTER = """# A. Example

example@research.test

Dear Hiring Manager,

I am applying for the Senior Mechanical R&D Engineer position at Beta Engineering. My work as a Mechanical R&D Engineer at Alpha Motors has centred on engineering durability, including finite element analysis (FEA) and technical documentation. The chance to apply those methods in a new mechanical research environment is particularly attractive to me.

At Alpha Motors I performed finite element analysis (FEA) to study product durability and prepared a technical report about structural durability methods. This work encouraged a structured approach to investigating mechanical performance and to communicating analysis results to technical colleagues. I would welcome the opportunity to apply this experience to the responsibilities described in your posting.

I have also automated processing of engineering test data using Python scripting. This complements my analysis background because test information can be processed with reproducible methods. Together with my Master of Science in Mechanical Engineering, these experiences have prepared me to contribute to practical and analytical engineering work.

I would appreciate the opportunity to discuss how my background in product durability, Python scripting and finite element analysis could support your team. Thank you for considering my application.

Sincerely,

A. Example
"""


def _between(text: str, start: str, end: str) -> str:
    i = text.find(start)
    if i == -1:
        return ""
    i += len(start)
    j = text.find(end, i)
    return text[i:] if j == -1 else text[i:j]


class FakeLLM:
    def __init__(self, n_ctx: int = 8192, rewrites: dict[str, list[str]] | None = None,
                 audit_flags: dict[str, str] | None = None):
        self.n_ctx = n_ctx
        self.calls: list[str] = []
        self.prompts: list[str] = []
        self.stats = UsageStats()
        self.last_finish_reason = "stop"
        # section title -> successive answers (last one repeats)
        self.rewrites = rewrites if rewrites is not None else {
            "EXPERIENCE": [EXPERIENCE_GOOD], "Professional Summary": [SUMMARY], "SKILLS": [SKILLS]}
        self.audit_flags = audit_flags or {}

    def context_size(self) -> int:
        return self.n_ctx

    def chat(self, system: str, user: str, max_tokens: int = 2000, json_mode=None) -> str:
        task = re.search(r"TASK: (\w+)", user).group(1)
        self.calls.append(task)
        self.prompts.append(user)
        self.stats.calls += 1
        return getattr(self, "_" + task.lower())(user)

    def _extract_requirements(self, prompt: str) -> str:
        part = _between(prompt, "<<<JOB DESCRIPTION>>>", "<<<END JOB DESCRIPTION>>>")
        reqs = [{"text": text, "priority": pr, "keywords": kws, "jd_quote": quote}
                for text, (pr, kws, quote, _, _) in CATALOG.items()
                if quote in part or text == "Invented requirement"]
        return json.dumps({"requirements": reqs})

    def _match_evidence(self, prompt: str) -> str:
        reqs = json.loads(_between(prompt, "REQUIREMENTS: ", "\n\n<<<CANDIDATE DOCUMENTS>>>"))
        chunk = _between(prompt, "<<<CANDIDATE DOCUMENTS>>>", "<<<END CANDIDATE DOCUMENTS>>>")
        matches = []
        for r in reqs:
            _, _, _, quote, status = CATALOG.get(r["requirement"], (None, None, None, None, None))
            if quote and quote in chunk:
                matches.append({"id": r["id"], "status": status, "evidence": quote, "rationale": "fake"})
            else:
                matches.append({"id": r["id"], "status": "unsupported", "evidence": "", "rationale": "none"})
        return json.dumps({"matches": matches})

    def _rewrite_section(self, prompt: str) -> str:
        title = re.search(r'Rewrite the "(.+?)" section', prompt).group(1)
        answers = self.rewrites.get(title)
        if not answers:
            original = _between(prompt, "ORIGINAL SECTION (source of truth):\n", "\nVERIFIED FACTS").strip()
            return "" if original.startswith("(no such section") else original
        attempt = sum(1 for p in self.prompts if f'Rewrite the "{title}" section' in p)
        return answers[min(attempt, len(answers)) - 1]

    def _audit_claims(self, prompt: str) -> str:
        draft = _between(prompt, "<<<DRAFT>>>", "<<<END DRAFT>>>")
        flags = [{"draft_quote": q, "reason": why} for q, why in self.audit_flags.items() if q in draft]
        return json.dumps({"unsupported_claims": flags})

    def _write_cover_letter(self, prompt: str) -> str:
        return LETTER

    def _audit_cover_letter(self, prompt: str) -> str:
        letter = _between(prompt, "<<<COVER LETTER>>>", "<<<END>>>")
        flags = [{"draft_quote": q, "reason": why} for q, why in self.audit_flags.items() if q in letter]
        return json.dumps({"unsupported_claims": flags})

    def _review_cover_letter(self, prompt: str) -> str:
        return json.dumps({"improvements": ["Make the Python scripting experience explicit."]})
