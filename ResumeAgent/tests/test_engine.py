"""Engine tests against a scripted fake model (no llama-server needed)."""
import copy
import unittest

from engine import (
    Requirement, analyze, deterministic_checks, extract_requirements, match_evidence, optimize,
    requirements_from, score_resume, structure_checks, tailor_resume,
)
from fakes import CV, EXPERIENCE_GOOD, EXPERIENCE_INVENTED, JD, SKILLS, SUMMARY, FakeLLM
from llm_client import LLMError, ask_json

PUBLICATION = ("Example, A., Doe, J. (2023). Fatigue of welded joints under variable loading. "
               "Journal of Example Engineering, 12(3), 45-60.")


def reqs():
    return [
        Requirement("1", "FEA", "must", ["finite element analysis", "FEA"], "q", "supported",
                    "Performed finite element analysis (FEA) to study product durability."),
        Requirement("2", "Python", "must", ["Python scripting"], "q", "supported",
                    "Automated processing of engineering test data using Python scripting."),
        Requirement("3", "C++", "preferred", ["C++"], "q", "unsupported"),
    ]


class AnalysisTests(unittest.TestCase):
    def test_requirements_need_literal_jd_quotes(self):
        found = extract_requirements(JD, FakeLLM())
        texts = [r.text for r in found]
        self.assertNotIn("Invented requirement", texts)  # its quote is not in the JD
        self.assertEqual(len(found), 4)
        self.assertEqual(found[3].priority, "preferred")

    def test_evidence_audit_against_original(self):
        result = analyze(JD, CV, FakeLLM())
        by_text = {r["text"]: r for r in result["requirements"]}
        self.assertEqual(by_text["FEA for product durability"]["status"], "supported")
        self.assertEqual(by_text["Technical reporting to stakeholders"]["status"], "partial")
        self.assertEqual(by_text["Commercial C++ development"]["status"], "unsupported")
        self.assertEqual(by_text["Commercial C++ development"]["evidence"], "")
        # weights 3+3+3+1; evidence 3+3+1.5 -> 75 %
        self.assertEqual(result["baseline"]["qualification_fit"], 75.0)

    def test_non_verbatim_evidence_is_downgraded(self):
        class Paraphraser(FakeLLM):
            def _match_evidence(self, prompt):
                return '{"matches":[{"id":"1","status":"supported","evidence":"Did lots of FEA work","rationale":"x"}]}'
        items = reqs()[:1]
        match_evidence(CV, items, Paraphraser())
        self.assertEqual(items[0].status, "unsupported")

    def test_long_cv_evidence_beyond_old_cutoff_is_found(self):
        filler = "\n\n".join(f"Project {i}: maintained gearbox test rigs and wrote weekly logs for the "
                             f"workshop team, including calibration notes." for i in range(400))
        long_cv = CV.replace("SKILLS", filler + "\n\nSKILLS")
        self.assertGreater(len(long_cv), 30000)
        late_quote = "Automated processing of engineering test data using Python scripting."
        long_cv = long_cv.replace(late_quote + "\n", "") + "\n" + late_quote + "\n"
        self.assertGreater(long_cv.index(late_quote), 30000)
        llm = FakeLLM(n_ctx=4096)
        result = analyze(JD, long_cv, llm)
        self.assertGreater(llm.calls.count("MATCH_EVIDENCE"), 3)
        python = next(r for r in result["requirements"] if r["keywords"] == ["Python scripting"])
        self.assertEqual(python["status"], "supported")

    def test_long_jd_is_read_in_parts(self):
        padding = "\n\n".join(f"Our team value {i} is collaboration and curiosity in everything we do."
                              for i in range(500))
        long_jd = JD.replace("Preferred qualifications", padding + "\n\nPreferred qualifications")
        llm = FakeLLM(n_ctx=4096)
        found = extract_requirements(long_jd, llm)
        self.assertGreater(llm.calls.count("EXTRACT_REQUIREMENTS"), 2)
        self.assertIn("Commercial C++ development", [r.text for r in found])

    def test_confirmed_fact_raises_evidence_fit(self):
        facts = "- Built a commercial C++ plug-in for test rigs."
        result = analyze(JD, CV, FakeLLM(), facts=facts)
        cpp = next(r for r in result["requirements"] if r["keywords"] == ["C++"])
        self.assertEqual(cpp["status"], "supported")
        self.assertEqual(cpp["evidence_origin"], "confirmed_fact")
        self.assertEqual(result["baseline"]["qualification_fit"], 85.0)

    def test_malformed_json_is_retried(self):
        class Flaky(FakeLLM):
            bad = True

            def chat(self, system, user, max_tokens=2000, json_mode=None):
                if self.bad:
                    self.bad = False
                    return "Sure! Here are the requirements: (not json)"
                return super().chat(system, user, max_tokens, json_mode)
        llm = Flaky()
        self.assertEqual(len(extract_requirements(JD, llm)), 4)
        self.assertEqual(llm.stats.json_retries, 1)

    def test_json_gives_up_after_retries(self):
        class Broken(FakeLLM):
            def chat(self, *a, **k):
                return "never json"
        with self.assertRaises(LLMError):
            ask_json(Broken(), "json", "TASK: X", 100)


class ScoringTests(unittest.TestCase):
    def test_scores_and_denominators(self):
        items = reqs()
        s = score_resume(items, "finite element analysis and Python scripting")
        self.assertEqual(s["qualification_fit"], round(100 * 6 / 7, 1))
        self.assertEqual(s["truthful_match"], 100.0)
        self.assertEqual(s["must_missing"], [])
        s = score_resume(items, "finite element analysis only")
        self.assertEqual(s["truthful_match"], 50.0)
        self.assertEqual(score_resume([], "x")["qualification_fit"], 0.0)

    def test_keyword_stuffing_cannot_raise_fit(self):
        items = reqs()
        stuffed = "C++ " * 20 + "finite element analysis Python scripting"
        self.assertEqual(score_resume(items, stuffed)["qualification_fit"],
                         score_resume(items, "")["qualification_fit"])


class CheckTests(unittest.TestCase):
    def test_invented_numbers_terms_and_contacts(self):
        draft = "- Cut cost by 37% using Kubernetes and C++. Contact a@b.com"
        errors = " ".join(deterministic_checks(CV, draft, reqs()))
        self.assertIn("37%", errors)
        self.assertIn("Kubernetes", errors)
        self.assertIn('"C++"', errors)
        self.assertIn("a@b.com", errors)

    def test_sourced_numbers_terms_and_plurals_pass(self):
        draft = "- Used Abaqus and MATLAB from 2020 to 2025 for FEA studies (FEAs)."
        self.assertEqual(deterministic_checks(CV, draft, reqs()), [])

    def test_month_spelling_is_not_an_invented_name(self):
        self.assertEqual(deterministic_checks(CV, "- Alpha Motors, Jan 2020 to Present", reqs()), [])

    def test_model_commentary_is_removed(self):
        from engine import _clean_output
        raw = "Here is the section:\n## EXPERIENCE\n- Did FEA.\n\nNote: I removed the 2019 role."
        self.assertEqual(_clean_output(raw, "EXPERIENCE", "experience"), "- Did FEA.")

    def test_structure_keeps_dates_and_roles(self):
        original = "Graduate Engineer, Gamma Rail, 2018-2020\n- Supported fatigue testing of bogie frames."
        self.assertTrue(any("2018" in e for e in structure_checks(original, "- Did testing", "experience", .5)))
        self.assertTrue(any("Role/employer" in e for e in structure_checks(
            original, "**Engineer** — Delta Rail | 2018-2020\n- Testing", "experience", .5)))
        self.assertEqual(structure_checks(
            original, "**Graduate Engineer** — Gamma Rail | 2018-2020\n- Supported fatigue tests.",
            "experience", .5), [])


class TailorLoopTests(unittest.TestCase):
    def run_loop(self, llm, facts=""):
        analysis = analyze(JD, CV, llm, facts=facts)
        return optimize(JD, CV, llm, analysis=analysis, facts=facts)

    def test_loop_reaches_truthful_100_without_inventing(self):
        result = self.run_loop(FakeLLM())
        self.assertEqual(result["final"]["truthful_match"], 100.0)
        self.assertEqual(result["final"]["qualification_fit"], result["baseline"]["qualification_fit"])
        md = result["markdown"]
        self.assertNotIn("C++", md)
        self.assertIn(PUBLICATION, md)  # publications are reproduced exactly
        self.assertIn("Master of Science in Mechanical Engineering, University of Example, 2018", md)
        self.assertIn("## Professional Summary", md)
        self.assertTrue(md.startswith("# A. Example\n"))
        self.assertTrue(result["tailored_draft_accepted"])

    def test_rejected_draft_is_fixed_using_feedback(self):
        llm = FakeLLM(rewrites={"EXPERIENCE": [EXPERIENCE_INVENTED, EXPERIENCE_GOOD],
                                "Professional Summary": [SUMMARY], "SKILLS": [SKILLS]})
        result = self.run_loop(llm)
        joined = " ".join(result["rejections"])
        self.assertIn("40%", joined)
        self.assertIn("C++", joined)
        retry = next(p for p in llm.prompts if "A PREVIOUS DRAFT WAS REJECTED" in p)
        self.assertIn("40%", retry)
        self.assertNotIn("40%", result["markdown"])
        self.assertIn("technical reporting", result["markdown"])

    def test_llm_auditor_flags_block_acceptance(self):
        llm = FakeLLM(audit_flags={"Developed technical reporting": "upgraded claim"})
        result = self.run_loop(llm)
        self.assertTrue(any("upgraded claim" in x for x in result["rejections"]))
        self.assertNotIn("Developed technical reporting", result["markdown"])
        self.assertIn("Developed a technical report about structural durability methods.", result["markdown"])

    def test_keyword_patch_never_adds_partial_terms(self):
        echo = FakeLLM(rewrites={})  # every rewrite returns the original text
        result = self.run_loop(echo)
        self.assertEqual(result["keyword_patch"], [])
        self.assertNotIn("technical reporting", result["markdown"])  # partial evidence: not patched
        self.assertEqual(result["final"]["truthful_match"], 80.0)
        self.assertNotIn("C++", result["markdown"])

    def test_patch_makes_supported_terms_visible(self):
        hidden = copy.deepcopy(reqs())
        hidden[1].keywords = ["Python automation"]  # supported, but worded differently in the CV
        result = tailor_resume(JD, CV, hidden, FakeLLM(rewrites={}), max_rounds=1)
        self.assertEqual(result["keyword_patch"], ["Python automation"])
        self.assertIn("Additional verified skills:** Python automation", result["markdown"])
        self.assertEqual(result["final"]["truthful_match"], 100.0)

    def test_unstructured_resume_uses_document_mode(self):
        flat = ("Jane Doe\njane@example.test\nPerformed finite element analysis (FEA) to study product "
                "durability at Alpha Motors 2020-2025. Automated processing of engineering test data using "
                "Python scripting.")
        items = reqs()
        result = tailor_resume(JD, flat, items, FakeLLM(rewrites={}), max_rounds=1)
        self.assertEqual(result["sections"][0]["kind"], "document")
        self.assertIn("Jane Doe", result["markdown"])

    def test_requirements_round_trip(self):
        rows = analyze(JD, CV, FakeLLM())["requirements"]
        self.assertEqual([r.text for r in requirements_from(rows)], [r["text"] for r in rows])



class TolerantEvidenceParsingTests(unittest.TestCase):
    def test_alternative_keys_statuses_and_quote_styles(self):
        class Varied(FakeLLM):
            def _match_evidence(self, prompt):
                import json
                return json.dumps({"results": [
                    {"requirement_id": 1, "status": "Fully Supported",
                     "evidence_quote": "“Performed finite element analysis (FEA) to study product durability”"},
                    {"id": "R2", "status": "partially_supported",
                     "quote": "Automated processing of engineering test data using Python  scripting"},
                    {"id": 3, "status": "Not supported", "evidence": ""},
                ]})
        items = reqs()
        from textutils import Budget
        budget = Budget()
        match_evidence(CV, items, Varied(), budget)
        self.assertEqual([r.status for r in items], ["supported", "partial", "unsupported"])
        self.assertIn("3 answers for 3 requirements", budget.notes[-1])

    def test_unmatched_answer_is_an_error_not_a_silent_zero(self):
        class Garbled(FakeLLM):
            def _match_evidence(self, prompt):
                return '{"matches": [{"id": "x99", "status": "supported", "evidence": "abc"}]}'
        with self.assertRaises(LLMError):
            match_evidence(CV, reqs(), Garbled())

    def test_unfound_quote_is_explained(self):
        class Inventive(FakeLLM):
            def _match_evidence(self, prompt):
                return ('{"matches": [{"id": "1", "status": "supported", '
                        '"evidence": "Designed six-axis robots for wafer handling"}]}')
        items = reqs()[:1]
        match_evidence(CV, items, Inventive())
        self.assertEqual(items[0].status, "unsupported")
        self.assertIn("could not be found in your CV", items[0].rationale)


if __name__ == "__main__":
    unittest.main()
