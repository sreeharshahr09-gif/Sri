"""Model-free keyword workbench: extraction, matching, scoring and highlighting."""
import unittest

from fakes import CV, JD
from keywords import Keyword, extract_keywords, focus_text, highlight, merge_requirement_terms, report, status

LONG_JD = """Senior Robotics Engineer
We make the chips that make electronics. Join brilliant minds and shape the future!

Responsibilities:
Design robotic platforms, precision mechanisms, automation equipment, and sensor-integrated mechanical systems.
Own requirements definition, tolerance analysis, design reviews, prototyping and system integration.
Conduct structural, thermal and vibration analyses.

Requirements:
Solid understanding of machine design, tolerance analysis and manufacturing processes.
Experience in robotics or precision machinery is desirable.
Masters/PhD degree in Mechanical Engineering or Mechatronics.

Benefits
Free snacks, a friendly team and flexible hours."""


class ExtractionTests(unittest.TestCase):
    def test_skips_company_blurb_and_benefits(self):
        focus = focus_text(LONG_JD)
        self.assertNotIn("chips", focus)
        self.assertNotIn("snacks", focus)
        self.assertIn("tolerance analysis", focus)

    def test_keywords_are_skill_phrases(self):
        kws = [k.phrase for k in extract_keywords(LONG_JD)]
        for wanted in ("robotic platforms", "precision mechanisms", "tolerance analysis", "design reviews",
                       "system integration", "machine design", "manufacturing processes", "PhD",
                       "Mechanical Engineering", "Mechatronics"):
            self.assertIn(wanted, kws)
        for junk in ("Design robotic platforms", "chips", "snacks", "Solid understanding", "you'll",
                     "design", "technology", "structural"):
            self.assertNotIn(junk, kws)
        tol = next(k for k in extract_keywords(LONG_JD) if k.phrase == "tolerance analysis")
        self.assertEqual(tol.count, 2)

    def test_mid_line_verbs_are_stripped(self):
        kws = [k.phrase for k in extract_keywords(
            "Responsibilities\nYou will evaluate durability with simulations and turn test data into decisions.")]
        self.assertIn("test data", kws)
        self.assertNotIn("turn test data", kws)

    def test_requirement_terms_are_weighted(self):
        kws = merge_requirement_terms(extract_keywords(JD), [
            {"priority": "must", "keywords": ["finite element analysis", "FEA"]},
            {"priority": "preferred", "keywords": ["C++"]}])
        fea = next(k for k in kws if k.phrase == "FEA")
        self.assertEqual((fea.weight, fea.source), (2.0, "requirement"))


class MatchingTests(unittest.TestCase):
    def test_tolerant_matching(self):
        self.assertEqual(status("Built opto mechanical assemblies", "opto-mechanical assembly"), "matched")
        self.assertEqual(status("Doctor of Philosophy (Ph.D.)", "PhD"), "matched")
        self.assertEqual(status("Bachelor of Engineering (Mechanical)", "Mechanical Engineering"), "partial")
        self.assertEqual(status("Thermal analysis of parts", "vibration control"), "missing")

    def test_score_rises_as_the_cv_is_edited(self):
        kws = [Keyword("tolerance analysis", 2), Keyword("machine design", 1), Keyword("PhD", 1, 2.0, "requirement")]
        before = report(kws, "Mechanical engineer with a PhD.")
        after = report(kws, "Mechanical engineer with a PhD. Performed tolerance analysis and machine design.")
        self.assertEqual(before["score"], 50.0)   # PhD weight 2 of 4
        self.assertEqual(after["score"], 100.0)
        self.assertEqual(before["missing"], ["tolerance analysis", "machine design"])

    def test_stuffing_is_flagged(self):
        r = report([Keyword("FEA", 1)], "FEA " * 7)
        self.assertEqual(r["stuffed"], ["FEA"])

    def test_highlight_marks_status_and_escapes_html(self):
        out = highlight("Need FEA <b>and</b> C++ skills", [Keyword("FEA", 1), Keyword("C++", 1)], "I use FEA")
        self.assertIn('background:#bbf7d0', out)  # FEA matched
        self.assertIn('background:#fecaca', out)  # C++ missing
        self.assertIn("&lt;b&gt;", out)
        self.assertEqual(out.count("<mark"), 2)


if __name__ == "__main__":
    unittest.main()
