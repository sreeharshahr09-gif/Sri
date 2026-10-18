"""Deterministic text helpers: ingestion, sections, chunking, matching."""
import io
import unittest

from docx import Document

from fakes import CV
from textutils import (
    chunk_text, contains_phrase, format_verbatim, heading_kind, novel_numbers, proper_nouns,
    read_file, split_sections, technical_terms,
)


class MatchingTests(unittest.TestCase):
    def test_phrases(self):
        self.assertTrue(contains_phrase("Wrote C++ and Python", "C++"))
        self.assertTrue(contains_phrase("finite   element\nanalysis", "finite element analysis"))
        self.assertFalse(contains_phrase("Pythonic", "Python"))

    def test_numbers(self):
        self.assertEqual(novel_numbers("Budget 1,200 and 40%", "Budget 1200, 40 and 99%"), ["99%"])

    def test_technical_terms_and_names(self):
        self.assertEqual(technical_terms("Used MATLAB, C++, C#, Python3 and plain words in 2021"),
                         ["MATLAB", "C++", "C#", "Python3"])
        self.assertEqual(proper_nouns("Led tests. Used Kubernetes with the Senior team.\n- Built rigs"),
                         ["Kubernetes", "Senior"])
        self.assertEqual(proper_nouns("- **Programming:** Python, Abaqus"), ["Abaqus"])


class SectionTests(unittest.TestCase):
    def test_headings(self):
        self.assertEqual(heading_kind("EXPERIENCE"), "experience")
        self.assertEqual(heading_kind("## Selected Publications"), "verbatim")
        self.assertEqual(heading_kind("Technical Skills:"), "skills")
        self.assertIsNone(heading_kind("SKILLS: Python, MATLAB"))
        self.assertIsNone(heading_kind("Performed finite element analysis"))

    def test_split_sample(self):
        sections = split_sections(CV)
        self.assertEqual([s.kind for s in sections],
                         ["header", "experience", "education", "skills", "verbatim"])
        self.assertTrue(sections[0].body.startswith("A. Example"))
        self.assertFalse(sections[2].rewritable)  # education is preserved exactly
        self.assertFalse(sections[4].rewritable)

    def test_unstructured_fallback(self):
        self.assertEqual(split_sections("Jane\nDid things\nMore things")[0].kind, "document")

    def test_verbatim_format(self):
        self.assertEqual(format_verbatim("• One\n  ● Two\n# Hash"), "- One\n- Two\n\\# Hash")


class ChunkTests(unittest.TestCase):
    def test_chunks_respect_size_and_keep_all_text(self):
        text = "\n\n".join(f"Paragraph {i} " + "word " * 40 for i in range(60))
        chunks = chunk_text(text, 1500)
        self.assertGreater(len(chunks), 5)
        self.assertTrue(all(len(c) <= 1500 for c in chunks))
        for i in range(60):
            self.assertTrue(any(f"Paragraph {i} " in c for c in chunks))

    def test_huge_single_line(self):
        chunks = chunk_text("x" * 5000, 1000)
        self.assertEqual("".join(chunks), "x" * 5000)


class ReadFileTests(unittest.TestCase):
    def test_txt_and_docx(self):
        self.assertIn("Alpha Motors", read_file("cv.txt", CV.encode("utf-8-sig")))
        doc = Document()
        doc.add_paragraph("Jane Doe")
        table = doc.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "Python"
        table.cell(0, 1).text = "Finite element analysis for durability studies"
        doc.add_paragraph("Final paragraph with enough text to pass the minimum length check.")
        buf = io.BytesIO()
        doc.save(buf)
        text = read_file("cv.docx", buf.getvalue())
        self.assertLess(text.index("Jane Doe"), text.index("Python | Finite"))
        self.assertLess(text.index("Python | Finite"), text.index("Final paragraph"))

    def test_rejects_unknown_and_empty(self):
        with self.assertRaises(ValueError):
            read_file("cv.rtf", b"abc")
        with self.assertRaises(ValueError):
            read_file("cv.txt", b"short")


if __name__ == "__main__":
    unittest.main()
