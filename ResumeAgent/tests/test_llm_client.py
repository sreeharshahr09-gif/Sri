"""LocalLLM over real HTTP against a tiny in-process imitation of llama-server."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from llm_client import LLMError, LocalLLM, ask_json, parse_json_object, safe_server, strip_thinking


class FakeLlamaServer(BaseHTTPRequestHandler):
    replies: list = []          # queue of (status, body-dict-or-text)
    requests: list = []

    def log_message(self, *args):
        pass

    def _send(self, status, body):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/v1/models":
            self._send(200, {"data": [{"id": "qwen-test"}]})
        elif self.path == "/props":
            self._send(200, {"default_generation_settings": {"n_ctx": 16384}})
        else:
            self._send(404, "not found")

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        FakeLlamaServer.requests.append(json.loads(self.rfile.read(length)))
        status, body = FakeLlamaServer.replies.pop(0)
        self._send(status, body)


def completion(content, finish="stop", **message):
    return {"choices": [{"finish_reason": finish, "message": {"content": content, **message}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7}}


class LocalLLMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLlamaServer)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeLlamaServer.replies = []
        FakeLlamaServer.requests = []

    def test_url_safety(self):
        self.assertEqual(safe_server("http://127.0.0.1:8080"), "http://127.0.0.1:8080/v1")
        self.assertEqual(safe_server("http://[::1]:8080/v1/"), "http://[::1]:8080/v1")
        for bad in ("https://api.openai.com/v1", "http://192.168.1.5:8080", "http://u:p@localhost:8080",
                    "http://localhost:8080/v1?x=1", "ftp://localhost"):
            with self.assertRaises(ValueError, msg=bad):
                safe_server(bad)

    def test_context_size_and_models(self):
        llm = LocalLLM(self.url)
        self.assertEqual(llm.models(), ["qwen-test"])
        self.assertEqual(llm.context_size(), 16384)

    def test_json_request_shape_thinking_switch_and_stats(self):
        FakeLlamaServer.replies = [(200, completion('<think>hmm</think>{"ok": true}'))]
        llm = LocalLLM(self.url)
        obj = ask_json(llm, "Return JSON only.", "TASK: X", 300)
        self.assertEqual(obj, {"ok": True})
        sent = FakeLlamaServer.requests[0]
        self.assertEqual(sent["model"], "qwen-test")
        self.assertEqual(sent["response_format"], {"type": "json_object"})
        self.assertEqual(sent["chat_template_kwargs"], {"enable_thinking": False})
        self.assertFalse(sent["stream"])
        self.assertEqual(llm.stats.calls, 1)
        self.assertEqual(llm.stats.prompt_tokens, 11)

    def test_markdown_calls_are_not_json_constrained(self):
        FakeLlamaServer.replies = [(200, completion("# Resume"))]
        LocalLLM(self.url, disable_thinking=False).chat("Write Markdown.", "x", json_mode=False)
        sent = FakeLlamaServer.requests[0]
        self.assertNotIn("response_format", sent)
        self.assertNotIn("chat_template_kwargs", sent)

    def test_response_format_fallback_for_older_servers(self):
        FakeLlamaServer.replies = [(400, "unsupported param response_format"), (200, completion('{"a": 1}'))]
        llm = LocalLLM(self.url)
        self.assertEqual(ask_json(llm, "JSON", "x", 100), {"a": 1})
        self.assertNotIn("response_format", FakeLlamaServer.requests[1])

    def test_context_overflow_message(self):
        FakeLlamaServer.replies = [(400, "the request exceeds the available context size")]
        with self.assertRaisesRegex(LLMError, "ctx-size"):
            LocalLLM(self.url).chat("JSON", "x")

    def test_thinking_exhausted_budget_is_explained(self):
        FakeLlamaServer.replies = [(200, completion("", finish="length", reasoning_content="..."))]
        with self.assertRaisesRegex(LLMError, "thinking"):
            LocalLLM(self.url).chat("Markdown", "x", json_mode=False)

    def test_unreachable_server(self):
        with self.assertRaisesRegex(LLMError, "Cannot reach"):
            LocalLLM("http://127.0.0.1:9", model="m").chat("s", "u")


class ParsingTests(unittest.TestCase):
    def test_strip_thinking(self):
        self.assertEqual(strip_thinking("<think>a\nb</think>\nAnswer"), "Answer")
        self.assertEqual(strip_thinking("reasoning…</think>Answer"), "Answer")
        self.assertEqual(strip_thinking("<think>never closed"), "")

    def test_parse_json_object(self):
        self.assertEqual(parse_json_object('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_json_object('Here: {"a": [1]} done'), {"a": [1]})
        with self.assertRaises(ValueError):
            parse_json_object("[1, 2]")


if __name__ == "__main__":
    unittest.main()
