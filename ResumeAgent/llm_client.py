"""Loopback-only client for an already running llama.cpp ``llama-server``.

The server exposes an OpenAI-compatible API, but nothing here talks to OpenAI or
any other remote service: URLs other than localhost/127.0.0.1/[::1] are refused.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable
from urllib.parse import urlparse

import requests

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
DEFAULT_CONTEXT = 8192


class LLMError(RuntimeError):
    """A local-model failure with a message the user can act on."""


def safe_server(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "http" or parsed.hostname not in LOCAL_HOSTS:
        raise ValueError("For privacy, only a local HTTP inference server "
                         "(localhost / 127.0.0.1 / [::1]) is allowed.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Please supply only a local server URL, e.g. http://127.0.0.1:8080/v1")
    return base if base.endswith("/v1") else base + "/v1"


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)


def strip_thinking(text: str) -> str:
    """Remove Qwen-style reasoning that leaked into the answer text."""
    text = _THINK_BLOCK.sub("", text or "")
    if "</think>" in text.lower():
        # Template opened <think> itself; keep only what follows the last close tag.
        text = re.split(r"</think>", text, flags=re.I)[-1]
    if text.lstrip().lower().startswith("<think>"):
        # Reasoning never finished: there is no answer in this reply.
        return ""
    return text.strip()


def parse_json_object(answer: str) -> dict[str, Any]:
    answer = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer.strip(), flags=re.I)
    try:
        obj = json.loads(answer)
    except json.JSONDecodeError:
        start, end = answer.find("{"), answer.rfind("}")
        if start == -1 or end < start:
            raise ValueError("reply was not JSON")
        try:
            obj = json.loads(answer[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"reply was not valid JSON ({exc.msg})") from exc
    if not isinstance(obj, dict):
        raise ValueError("expected a JSON object")
    return obj


@dataclass
class UsageStats:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    json_retries: int = 0

    def summary(self) -> str:
        return (f"{self.calls} model calls · {self.prompt_tokens:,} prompt + "
                f"{self.completion_tokens:,} output tokens · {self.seconds:.0f}s")


class LocalLLM:
    def __init__(self, base_url: str = "http://127.0.0.1:8080/v1", model: str = "",
                 disable_thinking: bool = True, temperature: float = 0.2,
                 timeout: float = 900):
        self.base_url = safe_server(base_url)
        self.root_url = self.base_url[:-len("/v1")]
        self.model = model.strip()
        self.disable_thinking = disable_thinking
        self.temperature = temperature
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False  # never route localhost through corporate proxies
        self.stats = UsageStats()
        self.last_finish_reason = ""
        self._json_mode_ok = True
        self._n_ctx: int | None = None

    def models(self) -> list[str]:
        r = self.session.get(self.base_url + "/models", timeout=8)
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", []) if m.get("id")]

    def context_size(self) -> int:
        """Per-request context window reported by llama-server (/props)."""
        if self._n_ctx is None:
            n_ctx = 0
            try:
                r = self.session.get(self.root_url + "/props", timeout=8)
                r.raise_for_status()
                data = r.json()
                n_ctx = int((data.get("default_generation_settings") or {}).get("n_ctx")
                            or data.get("n_ctx") or 0)
            except Exception:
                n_ctx = 0
            self._n_ctx = n_ctx if n_ctx >= 1024 else DEFAULT_CONTEXT
        return self._n_ctx

    def _model_id(self) -> str:
        if not self.model:
            ids = self.models()
            if not ids:
                raise LLMError("No model is advertised by the local server. Start llama-server "
                               "with your GGUF model, then retry.")
            self.model = ids[0]
        return self.model

    label = "llama-server"

    def chat(self, system: str, user: str, max_tokens: int = 2000,
             json_mode: bool | None = None) -> str:
        if json_mode is None:
            json_mode = "json" in system.lower()
        payload: dict[str, Any] = {
            "model": self._model_id(),
            "messages": [{"role": "system", "content": self._outgoing(system)},
                         {"role": "user", "content": self._outgoing(user)}],
            "temperature": self.temperature, "max_tokens": int(max_tokens), "stream": False,
        }
        if self.disable_thinking:
            self._thinking_off(payload)
        if json_mode and self._json_mode_ok:
            payload["response_format"] = {"type": "json_object"}

        started = time.monotonic()
        response = self._send(payload)
        if response.status_code == 400 and "response_format" in payload and \
                "context" not in response.text.lower():
            # Some servers/models reject response_format; prompts still ask for JSON.
            self._json_mode_ok = False
            payload.pop("response_format")
            response = self._send(payload)
        if response.status_code >= 400:
            self._raise_http_error(response)

        data = response.json()
        if isinstance(data.get("error"), dict):  # some gateways report errors with HTTP 200
            raise LLMError(f"{self.label} error: {str(data['error'].get('message', data['error']))[:300]}")
        self.stats.calls += 1
        self.stats.seconds += time.monotonic() - started
        usage = data.get("usage") or {}
        self.stats.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.stats.completion_tokens += int(usage.get("completion_tokens") or 0)
        choice = (data.get("choices") or [{}])[0]
        self.last_finish_reason = str(choice.get("finish_reason") or "")
        message = choice.get("message") or {}
        content = strip_thinking(message.get("content") or "")
        if not content:
            if message.get("reasoning_content") or message.get("reasoning") or \
                    self.last_finish_reason == "length":
                raise LLMError(self._thinking_hint())
            raise LLMError(f"The {self.label} model returned an empty answer.")
        return self._incoming(content)

    # --- provider hooks -----------------------------------------------------------------------
    def _thinking_off(self, payload: dict[str, Any]) -> None:
        # Qwen3-family chat templates (with llama-server --jinja) honour this switch.
        payload["chat_template_kwargs"] = {"enable_thinking": False}

    def _thinking_hint(self) -> str:
        return ("The model spent its whole output budget thinking and returned no answer. Keep "
                "'Disable model thinking' on, or start llama-server with --reasoning-budget 0.")

    def _outgoing(self, text: str) -> str:
        return text

    def _incoming(self, text: str) -> str:
        return text

    def _send(self, payload: dict[str, Any]) -> requests.Response:
        return self._post(payload)

    def _raise_http_error(self, response: requests.Response) -> None:
        detail = response.text[:300]
        if "context" in detail.lower() or "too long" in detail.lower():
            raise LLMError("The prompt is larger than the llama-server context window. "
                           "Restart llama-server with a larger --ctx-size (16384 recommended) "
                           f"or shorten the inputs. Server said: {detail}")
        raise LLMError(f"llama-server returned HTTP {response.status_code}: {detail}")

    def _post(self, payload: dict[str, Any]) -> requests.Response:
        try:
            return self.session.post(self.base_url + "/chat/completions", json=payload,
                                     timeout=(15, self.timeout))
        except requests.ConnectionError as exc:
            raise LLMError(f"Cannot reach {self.label} at {self.base_url}. Is it running?") from exc
        except requests.Timeout as exc:
            raise LLMError(f"{self.label} did not answer in time. It may be busy; try again.") from exc


# --- OpenRouter (optional cloud backend) -------------------------------------------------------

OPENROUTER_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-nano-9b-v2:free"
_PLACEHOLDER = re.compile(r"\[\s*((?:NAME|EMAIL|PHONE|LINK|CONTACT)[_ ]\d+)\s*\]", re.I)


class Redactor:
    """Replaces personal details with placeholders before text leaves the PC; restores them after.

    Literal values (name, header contact lines) are registered from the CV; e-mail addresses,
    phone numbers and profile links are also caught by pattern wherever they appear."""

    _PATTERNS = (
        ("EMAIL", re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")),
        ("LINK", re.compile(r"(?i)\b(?:https?://|www\.)[^\s)>\]|]+|\b(?:linkedin|github|researchgate)"
                            r"\.(?:com|net)/[^\s)>\]|]+")),
        ("PHONE", re.compile(r"(?<![\w/])\+?\d[\d ()-]{7,}\d(?![\w/])")),
    )

    def __init__(self) -> None:
        self.values: dict[str, str] = {}   # placeholder -> original
        self._literals: list[tuple[str, str]] = []

    def _token(self, kind: str, value: str) -> str:
        for token, original in self.values.items():
            if original == value:
                return token
        count = sum(1 for t in self.values if t.startswith(f"[{kind}_"))
        token = f"[{kind}_{count + 1}]"
        self.values[token] = value
        return token

    def add_name(self, name: str) -> None:
        name = re.sub(r",?\s*(Ph\.?\s?D\.?|MBA|P\.?E\.?)$", "", name.strip(" ,*#"), flags=re.I).strip()
        if len(name) >= 4:
            self._literals.append((name, "NAME"))

    def add_contact(self, line: str) -> None:
        value = re.sub(r"^\s*(address|location|mobile|phone|tel|email|e-mail)\s*:\s*", "", line.strip(), flags=re.I)
        if len(value) >= 6 and not self._PATTERNS[0][1].fullmatch(value):
            self._literals.append((value, "CONTACT"))

    def redact(self, text: str) -> str:
        for value, kind in sorted(self._literals, key=lambda x: -len(x[0])):
            # Each spelling ("Jane Doe" / "JANE DOE") gets its own placeholder so it comes back unchanged.
            text = re.sub(re.escape(value), lambda m: self._token(kind, m.group(0)), text, flags=re.I)
        for kind, pattern in self._PATTERNS:
            text = pattern.sub(lambda m: self._token(kind, m.group(0)) if self._personal(kind, m.group(0))
                               else m.group(0), text)
        return text

    @staticmethod
    def _personal(kind: str, value: str) -> bool:
        if kind != "PHONE":
            return True
        # '+91-9900434319' or '(555) 123-4567' are phones; '2018 - 2021' or '1200 2009 2011' are not.
        if len(re.sub(r"\D", "", value)) < 9:
            return False
        groups = [re.sub(r"\D", "", g) for g in re.split(r"\s+", value.strip())]
        return value.lstrip().startswith("+") or any(len(g) >= 6 for g in groups)

    def restore(self, text: str) -> str:
        def back(m: re.Match) -> str:
            key = "[" + re.sub(r"\s+", "_", m.group(1).upper()) + "]"
            return self.values.get(key, m.group(0))
        return _PLACEHOLDER.sub(back, text)


class OpenRouterLLM(LocalLLM):
    """OpenRouter's OpenAI-compatible API. Opt-in only: text is sent to openrouter.ai and the
    model provider. Free (':free') models are rate limited and may log prompts."""

    label = "OpenRouter"

    def __init__(self, api_key: str, model: str = DEFAULT_OPENROUTER_MODEL,
                 disable_thinking: bool = True, temperature: float = 0.2, timeout: float = 300,
                 redactor: Redactor | None = None, min_interval: float = 3.2,
                 base_url: str = OPENROUTER_URL):
        if not api_key or not api_key.strip():
            raise ValueError("An OpenRouter API key is needed (openrouter.ai → Keys).")
        parsed = urlparse(base_url)
        if base_url != OPENROUTER_URL and parsed.hostname not in LOCAL_HOSTS:  # loopback only for tests
            raise ValueError("Only the official OpenRouter endpoint is allowed.")
        self.base_url = base_url.rstrip("/")
        self.root_url = self.base_url
        self.model = model.strip() or DEFAULT_OPENROUTER_MODEL
        self.disable_thinking = disable_thinking
        self.temperature = temperature
        self.timeout = timeout
        self.session = requests.Session()  # system proxy settings apply for internet access
        self.session.headers.update({"Authorization": f"Bearer {api_key.strip()}",
                                     "HTTP-Referer": "http://localhost",
                                     "X-Title": "Local Resume Agent"})
        self.stats = UsageStats()
        self.last_finish_reason = ""
        self._json_mode_ok = True
        self._n_ctx = None
        self._reasoning_off_ok = True
        self.redactor = redactor
        self.min_interval = min_interval  # free models allow 20 requests per minute
        self._last_call = 0.0
        self._catalog: list[dict[str, Any]] | None = None

    def __repr__(self) -> str:  # never show the key
        return f"OpenRouterLLM(model={self.model!r})"

    def catalog(self) -> list[dict[str, Any]]:
        if self._catalog is None:
            try:
                r = self.session.get(self.base_url + "/models", timeout=20)
                r.raise_for_status()
            except requests.RequestException as exc:
                raise LLMError(f"Cannot reach OpenRouter: {exc}") from exc
            self._catalog = [m for m in r.json().get("data", []) if m.get("id")]
        return self._catalog

    def models(self) -> list[str]:
        return [m["id"] for m in self.catalog()]

    def free_models(self) -> list[str]:
        def free(m: dict[str, Any]) -> bool:
            price = m.get("pricing") or {}
            return m["id"].endswith(":free") or (str(price.get("prompt")) in ("0", "0.0") and
                                                  str(price.get("completion")) in ("0", "0.0"))
        ids = [m["id"] for m in self.catalog() if free(m) and "embed" not in m["id"]
               and "rerank" not in m["id"] and "safety" not in m["id"]]
        return sorted(ids, key=lambda i: (not i.startswith("nvidia/"), i))

    def context_size(self) -> int:
        if self._n_ctx is None:
            n_ctx = 0
            try:
                entry = next((m for m in self.catalog() if m["id"] == self.model), {})
                n_ctx = int(entry.get("context_length") or 0)
            except Exception:
                n_ctx = 0
            self._n_ctx = n_ctx if n_ctx >= 1024 else 32768
        return self._n_ctx

    def _model_id(self) -> str:
        return self.model

    def _thinking_off(self, payload: dict[str, Any]) -> None:
        if self._reasoning_off_ok:
            payload["reasoning"] = {"effort": "none", "exclude": True}
        else:  # model insists on reasoning: hide it and leave room for it
            payload["reasoning"] = {"exclude": True}
            payload["max_tokens"] = int(payload["max_tokens"] * 2 + 1000)

    def _thinking_hint(self) -> str:
        return ("The OpenRouter model used its whole output budget on reasoning. Try another model, "
                "or keep 'Disable model thinking' on.")

    def _outgoing(self, text: str) -> str:
        return self.redactor.redact(text) if self.redactor else text

    def _incoming(self, text: str) -> str:
        return self.redactor.restore(text) if self.redactor else text

    def _send(self, payload: dict[str, Any]) -> requests.Response:
        for attempt in range(4):
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            response = self._post(payload)
            text = response.text.lower()
            if response.status_code == 400 and "reasoning" in text and \
                    payload.get("reasoning", {}).get("effort") == "none":
                self._reasoning_off_ok = False  # this model cannot switch reasoning off
                self._thinking_off(payload)
                continue
            if response.status_code == 429:
                if "per-day" in text or "per day" in text or "daily" in text:
                    raise LLMError("OpenRouter's daily limit for free models is reached (50 requests/day, "
                                   "or 1,000/day after buying $10 of credits). Switch to your local "
                                   "model or try again tomorrow.")
                retry_after = response.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 5 * 2 ** attempt
                time.sleep(min(delay, 60))
                continue
            if response.status_code in (502, 503) and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            return response
        return response

    def _raise_http_error(self, response: requests.Response) -> None:
        detail = response.text[:300]
        if response.status_code == 401:
            raise LLMError("OpenRouter rejected the API key (HTTP 401). Check the key in the sidebar.")
        if response.status_code == 402:
            raise LLMError("OpenRouter says this model needs credits (HTTP 402). Pick a ':free' model.")
        if response.status_code == 429:
            raise LLMError("OpenRouter is rate limiting requests (HTTP 429) even after waiting. "
                           "Free models allow about 20 requests per minute; try again shortly.")
        if "context" in detail.lower():
            raise LLMError(f"The prompt is too long for this OpenRouter model: {detail}")
        raise LLMError(f"OpenRouter returned HTTP {response.status_code}: {detail}")


def make_llm(provider: str, *, base_url: str = "", model: str = "", disable_thinking: bool = True,
             api_key: str = "", redactor: Redactor | None = None) -> LocalLLM:
    if provider == "openrouter":
        return OpenRouterLLM(api_key, model or DEFAULT_OPENROUTER_MODEL, disable_thinking,
                             redactor=redactor)
    return LocalLLM(base_url or "http://127.0.0.1:8080/v1", model, disable_thinking)


def ask_json(llm: Any, system: str, prompt: str, max_tokens: int,
             validate: Callable[[dict[str, Any]], None] | None = None,
             retries: int = 1) -> dict[str, Any]:
    """Ask for one JSON object; on malformed/invalid output retry with the error shown.

    ``validate`` raises ValueError to reject a structurally wrong object.
    """
    last_error = ""
    request = prompt
    for attempt in range(retries + 1):
        answer = llm.chat(system, request, max_tokens=max_tokens, json_mode=True)
        try:
            obj = parse_json_object(answer)
            if validate:
                validate(obj)
            return obj
        except ValueError as exc:
            last_error = str(exc)
            stats = getattr(llm, "stats", None)
            if stats is not None and attempt < retries:
                stats.json_retries += 1
            if getattr(llm, "last_finish_reason", "") == "length":
                max_tokens = int(max_tokens * 1.5)
            request = (prompt + "\n\nIMPORTANT: your previous reply could not be used ("
                       + last_error + "). Reply again with exactly ONE valid JSON object "
                       "in the requested shape and nothing else.")
    raise LLMError(f"The local model did not return usable JSON after {retries + 1} attempts "
                   f"({last_error}).")


def stats_dict(llm: Any) -> dict[str, Any]:
    stats = getattr(llm, "stats", None)
    return asdict(stats) if stats is not None else {}
