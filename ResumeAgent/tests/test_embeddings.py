"""Optional local embeddings: client, retrieval, second look (fake embedding server over HTTP)."""
import hashlib
import json
import math
import re
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from embeddings import EmbeddingClient, Retriever, evidence_lines
from engine import Requirement, analyze, second_look
from fakes import CV, JD, FakeLLM
from llm_client import LLMError

DIM = 256


def bag_of_words(text: str) -> list[float]:
    """Deterministic stand-in for a semantic model: shared word stems => similar vectors."""
    vec = [0.0] * DIM
    for word in re.findall(r"[a-z+#]{3,}", text.lower()):
        vec[int(hashlib.md5(word[:5].encode()).hexdigest(), 16) % DIM] += 1.0
    return vec


class FakeEmbedServer(BaseHTTPRequestHandler):
    batches: list = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeEmbedServer.batches.append(len(body["input"]))
        data = [{"index": i, "embedding": bag_of_words(t)} for i, t in enumerate(body["input"])]
        out = json.dumps({"data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class EmbeddingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeEmbedServer)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeEmbedServer.batches = []

    def test_client_batches_caches_normalises_and_is_local_only(self):
        client = EmbeddingClient(self.url, batch=2)
        vecs = client.embed(["alpha beta", "gamma", "delta", "alpha beta", "eps zeta"])
        self.assertEqual(FakeEmbedServer.batches, [2, 2])  # 4 unique texts in batches of 2
        self.assertAlmostEqual(math.sqrt(sum(v * v for v in vecs[0])), 1.0, places=6)
        client.embed(["gamma"])
        self.assertEqual(len(FakeEmbedServer.batches), 2)  # cached
        with self.assertRaises(ValueError):
            EmbeddingClient("http://example.com:8081/v1")
        with self.assertRaises(LLMError):
            EmbeddingClient("http://127.0.0.1:9/v1").embed(["x"])
        self.assertFalse(EmbeddingClient("http://127.0.0.1:9/v1").available())

    def test_retriever_ranks_relevant_lines_first(self):
        retriever = Retriever(EmbeddingClient(self.url), CV)
        top_line = retriever.top("finite element analysis for product durability", 3)[0][0]
        self.assertIn("finite element analysis", top_line)
        self.assertIn("- Supported fatigue testing of bogie frames.", evidence_lines(CV))
        self.assertFalse(any("@" in line or "567890" in line for line in evidence_lines(CV)))

    def test_second_look_upgrades_only_with_verified_quote(self):
        reqs = [Requirement("1", "Fatigue testing of structures", "must", ["fatigue testing"], "q"),
                Requirement("2", "Commercial C++ development", "preferred", ["C++"], "q")]

        class Confirms(FakeLLM):
            def _second_look(self, prompt):
                return json.dumps({"matches": [
                    {"id": "1", "status": "supported", "evidence": "Supported fatigue testing of bogie frames.",
                     "rationale": "direct"},
                    {"id": "2", "status": "supported", "evidence": "Wrote C++ plug-ins for rigs."}]})
        retriever = Retriever(EmbeddingClient(self.url), CV)
        upgraded = second_look(CV, reqs, Confirms(), retriever)
        self.assertEqual(upgraded, 1)
        self.assertEqual(reqs[0].status, "supported")
        self.assertTrue(reqs[0].rationale.startswith("Second look"))
        self.assertEqual(reqs[1].status, "unsupported")  # quote not in the CV: not counted

    def test_analyze_with_embeddings_adds_related_lines_and_notes(self):
        result = analyze(JD, CV, FakeLLM(), embedder=EmbeddingClient(self.url))
        cpp = next(r for r in result["requirements"] if r["keywords"] == ["C++"])
        self.assertEqual(cpp["status"], "unsupported")  # similarity never counts as evidence
        self.assertIn(cpp["id"], result["related"])
        self.assertEqual(len(result["related"][cpp["id"]]), 3)
        self.assertTrue(any("Second look" in n for n in result["notes"]))

    def test_long_cv_uses_fewer_calls_with_retrieval(self):
        filler = "\n\n".join(f"Project {i}: maintained gearbox rigs and wrote weekly workshop logs "
                             f"with calibration notes." for i in range(400))
        long_cv = CV.replace("SKILLS", filler + "\n\nSKILLS")
        plain, semantic = FakeLLM(n_ctx=4096), FakeLLM(n_ctx=4096)
        base = analyze(JD, long_cv, plain)
        fast = analyze(JD, long_cv, semantic, embedder=EmbeddingClient(self.url))
        self.assertLess(semantic.calls.count("MATCH_EVIDENCE"), plain.calls.count("MATCH_EVIDENCE"))
        status = lambda res: {r["text"]: r["status"] for r in res["requirements"]}
        self.assertEqual(status(fast), status(base))  # same verdicts, fewer calls

    def test_embedding_server_down_is_not_fatal(self):
        result = analyze(JD, CV, FakeLLM(), embedder=EmbeddingClient("http://127.0.0.1:9/v1"))
        self.assertTrue(any("Embedding server not used" in n for n in result["notes"]))
        self.assertEqual(result["related"], {})


if __name__ == "__main__":
    unittest.main()
