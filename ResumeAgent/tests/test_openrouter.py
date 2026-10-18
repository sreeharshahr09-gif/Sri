"""OpenRouter client tests against an in-process imitation of the OpenRouter API (no internet)."""
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from llm_client import LLMError, OpenRouterLLM, Redactor, ask_json, make_llm

KEY = "sk-or-test-123"


class FakeOpenRouter(BaseHTTPRequestHandler):
    replies: list = []      # queue of (status, body, headers)
    requests: list = []
    auth: list = []

    def log_message(self, *args):
        pass

    def _send(self, status, body, headers=None):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        FakeOpenRouter.auth.append(self.headers.get("Authorization"))
        self._send(200, {"data": [
            {"id": "openai/gpt-x", "pricing": {"prompt": "0.000002", "completion": "0.000008"},
             "context_length": 128000},
            {"id": "meta/llama-free:free", "pricing": {"prompt": "0", "completion": "0"}, "context_length": 8192},
            {"id": "nvidia/nemotron-nano-9b-v2:free", "pricing": {"prompt": "0", "completion": "0"},
             "context_length": 131072},
            {"id": "nvidia/nemotron-embed-1b:free", "pricing": {"prompt": "0", "completion": "0"}},
        ]})

    def do_POST(self):
        FakeOpenRouter.auth.append(self.headers.get("Authorization"))
        FakeOpenRouter.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        status, body, headers = FakeOpenRouter.replies.pop(0)
        self._send(status, body, headers)


def ok(content):
    return (200, {"choices": [{"finish_reason": "stop", "message": {"content": content}}],
                  "usage": {"prompt_tokens": 5, "completion_tokens": 3}}, {})


class OpenRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenRouter)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/api/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeOpenRouter.replies, FakeOpenRouter.requests, FakeOpenRouter.auth = [], [], []

    def client(self, **kw):
        return OpenRouterLLM(KEY, "nvidia/nemotron-nano-9b-v2:free", min_interval=0, base_url=self.url, **kw)

    def test_only_official_endpoint_or_loopback(self):
        with self.assertRaises(ValueError):
            OpenRouterLLM(KEY, base_url="https://evil.example.com/api/v1")
        with self.assertRaises(ValueError):
            OpenRouterLLM("")
        self.assertIsInstance(make_llm("openrouter", api_key=KEY), OpenRouterLLM)
        self.assertNotIn(KEY, repr(make_llm("openrouter", api_key=KEY)))

    def test_free_models_and_context(self):
        llm = self.client()
        self.assertEqual(llm.free_models(), ["nvidia/nemotron-nano-9b-v2:free", "meta/llama-free:free"])
        self.assertEqual(llm.context_size(), 131072)
        self.assertEqual(FakeOpenRouter.auth[0], f"Bearer {KEY}")

    def test_request_shape_reasoning_off_and_json(self):
        FakeOpenRouter.replies = [ok('{"ok": true}')]
        self.assertEqual(ask_json(self.client(), "JSON only", "x", 100), {"ok": True})
        sent = FakeOpenRouter.requests[0]
        self.assertEqual(sent["model"], "nvidia/nemotron-nano-9b-v2:free")
        self.assertEqual(sent["reasoning"], {"effort": "none", "exclude": True})
        self.assertEqual(sent["response_format"], {"type": "json_object"})
        self.assertNotIn("chat_template_kwargs", sent)

    def test_model_that_cannot_disable_reasoning(self):
        FakeOpenRouter.replies = [(400, '{"error":{"message":"Reasoning is mandatory for this endpoint"}}', {}),
                                  ok("Answer")]
        llm = self.client()
        self.assertEqual(llm.chat("Markdown", "x", max_tokens=100, json_mode=False), "Answer")
        retry = FakeOpenRouter.requests[1]
        self.assertEqual(retry["reasoning"], {"exclude": True})
        self.assertGreater(retry["max_tokens"], 100)  # room for the hidden reasoning

    def test_rate_limit_waits_and_retries(self):
        FakeOpenRouter.replies = [(429, '{"error":{"message":"Rate limit exceeded: free-models-per-min"}}',
                                   {"Retry-After": "0"}), ok("Done")]
        started = time.monotonic()
        self.assertEqual(self.client().chat("s", "u", json_mode=False), "Done")
        self.assertLess(time.monotonic() - started, 5)

    def test_daily_limit_is_explained(self):
        FakeOpenRouter.replies = [(429, '{"error":{"message":"Rate limit exceeded: free-models-per-day"}}', {})]
        with self.assertRaisesRegex(LLMError, "daily limit"):
            self.client().chat("s", "u", json_mode=False)

    def test_bad_key_and_error_in_200_body(self):
        FakeOpenRouter.replies = [(401, '{"error":{"message":"No auth credentials found"}}', {})]
        with self.assertRaisesRegex(LLMError, "API key"):
            self.client().chat("s", "u", json_mode=False)
        FakeOpenRouter.replies = [(200, {"error": {"message": "Provider returned error", "code": 502}}, {})]
        with self.assertRaisesRegex(LLMError, "Provider returned error"):
            self.client().chat("s", "u", json_mode=False)

    def test_personal_details_never_leave_and_come_back(self):
        redactor = Redactor()
        redactor.add_name("JANE DOE, Ph.D.")
        redactor.add_contact("Address: 12 Example Street, Springfield")
        cv = ("JANE DOE, Ph.D.\nAddress: 12 Example Street, Springfield\nMobile: +1-555-010-9999\n"
              "jane.doe@example.test | www.linkedin.com/in/jane-doe\nFEA Engineer, 2019 - 2023")
        FakeOpenRouter.replies = [ok("# [NAME_1]\n\n[EMAIL_1] | [PHONE_1]\n\nDear Hiring Manager, ... [NAME_1]")]
        reply = self.client(redactor=redactor).chat("Write", cv, json_mode=False)
        sent = json.dumps(FakeOpenRouter.requests[0])
        for secret in ("JANE DOE", "Example Street", "555-010-9999", "jane.doe@example.test", "linkedin.com/in/jane"):
            self.assertNotIn(secret, sent)
        self.assertIn("2019 - 2023", sent)  # dates are not personal data and stay visible
        self.assertEqual(reply, "# JANE DOE\n\njane.doe@example.test | +1-555-010-9999\n\n"
                                "Dear Hiring Manager, ... JANE DOE")


if __name__ == "__main__":
    unittest.main()
