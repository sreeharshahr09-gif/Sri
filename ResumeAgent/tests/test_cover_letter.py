"""Cover-letter loop tests (fake model, no llama-server needed)."""
import json
import unittest

from engine import Requirement, letter_metrics, optimize_cover_letter, verify_cover_letter
from fakes import CV, JD, LETTER, FakeLLM

DRAFT_RESUME = "# A. Example\n\n## Experience\n\n- Performed finite element analysis (FEA)."


def requirements():
    return [
        Requirement("1", "FEA for durability", "must", ["finite element analysis", "FEA"],
                    "finite element analysis (FEA) for product durability", "supported",
                    "Performed finite element analysis (FEA) to study product durability.", "direct"),
        Requirement("2", "Python scripting", "must", ["Python scripting"],
                    "Python scripting for analysis and automation", "supported",
                    "Automated processing of engineering test data using Python scripting.", "direct"),
        Requirement("3", "C++ development", "preferred", ["C++"],
                    "commercial C++ software development", "unsupported", "", "no C++"),
    ]


class CoverLetterTests(unittest.TestCase):
    def test_end_to_end(self):
        result = optimize_cover_letter(JD, CV, DRAFT_RESUME, requirements(), FakeLLM(),
                                       company="Beta Engineering",
                                       role="Senior Mechanical R&D Engineer", max_rounds=3,
                                       words=220)
        self.assertTrue(result["generated"], result["rejections"])
        self.assertIn("Beta Engineering", result["markdown"])
        self.assertNotIn("C++", result["markdown"])
        self.assertEqual(result["metrics"]["supported_jd_visibility"], 100.0)
        self.assertEqual(len(result["history"]), 1)  # target met in round 1

    def test_blocks_new_numerical_claim(self):
        bad = LETTER.replace("I have also automated", "I saved 99% of test time. I have also automated")
        errors = verify_cover_letter(CV, JD, bad, FakeLLM(), company="Beta Engineering",
                                     requirements=requirements())
        self.assertTrue(any("99%" in e for e in errors), errors)

    def test_blocks_unsupported_skill_even_though_jd_mentions_it(self):
        bad = LETTER.replace("I have also automated", "I am an expert in C++. I have also automated")
        errors = verify_cover_letter(CV, JD, bad, FakeLLM(), requirements=requirements())
        self.assertTrue(any("C++" in e for e in errors), errors)

    def test_rejects_placeholders(self):
        bad = LETTER.replace("Beta Engineering", "[Company Name]")
        errors = verify_cover_letter(CV, JD, bad, FakeLLM(), requirements=requirements())
        self.assertTrue(any("placeholder" in e for e in errors), errors)

    def test_rejection_feedback_reaches_next_draft(self):
        class Learner(FakeLLM):
            def _write_cover_letter(self, prompt):
                if "A PREVIOUS DRAFT WAS REJECTED" in prompt:
                    return LETTER
                return LETTER.replace("I have also automated", "I led 17 engineers. I have also automated")
        llm = Learner()
        result = optimize_cover_letter(JD, CV, DRAFT_RESUME, requirements(), llm,
                                       company="Beta Engineering", max_rounds=3)
        self.assertTrue(result["generated"])
        self.assertIn("17", result["rejections"][0])
        self.assertNotIn("17 engineers", result["markdown"])

    def test_unsupported_requirements_not_scored(self):
        metrics = letter_metrics(LETTER, requirements())
        self.assertEqual(metrics["supported_requirements_total"], 2)
        self.assertEqual(metrics["supported_requirements_visible"], 2)

    def test_fact_auditor_rejection(self):
        llm = FakeLLM(audit_flags={"My work as a Mechanical R&D Engineer at Alpha Motors": "test flag"})
        result = optimize_cover_letter(JD, CV, DRAFT_RESUME, requirements(), llm,
                                       company="Beta Engineering", max_rounds=2)
        self.assertFalse(result["generated"])
        self.assertEqual(len(result["rejections"]), 2)
        self.assertIn("test flag", result["rejections"][0])


if __name__ == "__main__":
    unittest.main()
