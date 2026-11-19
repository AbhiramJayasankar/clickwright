"""OpenAI-compatible chat client (stdlib only) with retries and cost tracking.

Works with any server that speaks /v1/chat/completions: Token Harbor,
OpenRouter, DeepSeek, Kimi, DashScope, vLLM, llama.cpp, Ollama...
"""
import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

USER_AGENT = "pixel-agent/0.1"
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529}


class ModelError(RuntimeError):
    def __init__(self, message, status=None, body=None, retryable=False):
        super().__init__(message)
        self.status = status
        self.body = body
        self.retryable = retryable


@dataclass
class Completion:
    content: str
    tool_calls: list
    reasoning: str
    finish_reason: str
    usage: dict
    latency: float
    attempts: int
    raw_message: dict = field(default_factory=dict)
    cost: float | None = None

    @property
    def prompt_tokens(self):
        return int(self.usage.get("prompt_tokens") or 0)

    @property
    def completion_tokens(self):
        return int(self.usage.get("completion_tokens") or 0)

    @property
    def cached_tokens(self):
        u = self.usage
        details = u.get("prompt_tokens_details") or {}
        return int(details.get("cached_tokens") or u.get("prompt_cache_hit_tokens") or u.get("cache_read_input_tokens") or 0)


_PRICE_CACHE = {}


class ChatClient:
    def __init__(self, base_url, api_key, model, timeout=240, max_retries=5, extra_body=None,
                 price_in=None, price_out=None):
        if not base_url:
            raise ModelError("no API base URL (check the profile's api_base and your .env)")
        if not api_key:
            raise ModelError("no API key (check the profile's api_key and your .env)")
        self.base_url = base_url.rstrip("/")
        self._key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.extra_body = extra_body or {}
        self._price = (price_in, price_out) if price_in is not None and price_out is not None else None
        self.total = {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "cost_usd": 0.0,
                      "calls": 0, "latency": 0.0}

    @classmethod
    def from_profile(cls, profile, **kw):
        return cls(profile.base_url, profile.key, profile.model, extra_body=profile.extra_body,
                   price_in=profile.price_in, price_out=profile.price_out, **kw)

    # --- HTTP -------------------------------------------------------------
    def _request(self, method, path, body=None, timeout=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method, headers={
            "Authorization": "Bearer " + self._key, "Content-Type": "application/json",
            "Accept": "application/json", "User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", errors="replace")
            retry_after = e.headers.get("Retry-After") if e.headers else None
            err = ModelError(f"HTTP {e.code}: {_short_error(text)}", e.code, text, e.code in RETRY_STATUS)
            err.retry_after = retry_after
            raise err from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            raise ModelError(f"network error: {e}", retryable=True) from None
        except json.JSONDecodeError as e:
            raise ModelError(f"bad JSON from server: {e}", retryable=True) from None

    def models(self):
        data = self._request("GET", "/models", timeout=60)
        return data.get("data", data) if isinstance(data, dict) else data

    def price(self):
        """(input, output, cache_read) USD per 1M tokens, or None if unknown."""
        if self._price:
            return (*self._price, None)
        key = (self.base_url, self.model)
        if key not in _PRICE_CACHE:
            _PRICE_CACHE[key] = None
            try:
                for m in self.models():
                    if isinstance(m, dict) and m.get("id") == self.model:
                        p = m.get("pricing") or {}
                        pin = p.get("input_usd_per_1m", p.get("prompt"))
                        pout = p.get("output_usd_per_1m", p.get("completion"))
                        if pin is not None and pout is not None:
                            pin, pout = float(pin), float(pout)
                            if "prompt" in p and "input_usd_per_1m" not in p:  # OpenRouter: per token
                                pin, pout = pin * 1e6, pout * 1e6
                            cache = p.get("cache_read_usd_per_1m")
                            _PRICE_CACHE[key] = (pin, pout, float(cache) if cache is not None else None)
                        elif self.model.endswith(":free"):
                            _PRICE_CACHE[key] = (0.0, 0.0, 0.0)
                        break
            except ModelError:
                pass
        return _PRICE_CACHE[key]

    def cost_of(self, comp):
        p = self.price()
        if not p:
            return None
        pin, pout, pcache = p
        cached = comp.cached_tokens
        fresh = max(0, comp.prompt_tokens - cached)
        cache_rate = pcache if pcache is not None else pin
        return (fresh * pin + cached * cache_rate + comp.completion_tokens * pout) / 1e6

    # --- chat -------------------------------------------------------------
    def complete(self, messages, tools=None, max_tokens=4096, temperature=None, tool_choice=None):
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens}
        if temperature is not None:
            body["temperature"] = temperature
        if tools:
            body["tools"] = tools
            if tool_choice:
                body["tool_choice"] = tool_choice
        body.update(self.extra_body)
        attempt, t0 = 0, time.time()
        while True:
            attempt += 1
            try:
                data = self._request("POST", "/chat/completions", body)
                if "choices" not in data or not data["choices"]:
                    raise ModelError(f"no choices in response: {json.dumps(data)[:300]}", retryable=True)
                break
            except ModelError as e:
                if not e.retryable or attempt > self.max_retries:
                    raise
                wait = _backoff(attempt, getattr(e, "retry_after", None))
                time.sleep(wait)
        choice = data["choices"][0]
        msg = choice.get("message") or {}
        content = msg.get("content") or ""
        if isinstance(content, list):  # some servers return content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        comp = Completion(
            content=content,
            tool_calls=msg.get("tool_calls") or [],
            reasoning=msg.get("reasoning_content") or msg.get("reasoning") or "",
            finish_reason=choice.get("finish_reason") or "",
            usage=data.get("usage") or {},
            latency=time.time() - t0,
            attempts=attempt,
            raw_message=msg,
        )
        self.total["calls"] += 1
        self.total["prompt_tokens"] += comp.prompt_tokens
        self.total["completion_tokens"] += comp.completion_tokens
        self.total["cached_tokens"] += comp.cached_tokens
        self.total["latency"] += comp.latency
        c = self.cost_of(comp)
        if c is not None:
            self.total["cost_usd"] += c
        comp.cost = c
        return comp


def _backoff(attempt, retry_after=None):
    if retry_after:
        try:
            return min(60.0, float(retry_after))
        except ValueError:
            pass
    return min(30.0, 1.5 * 2 ** (attempt - 1)) * (0.75 + random.random() / 2)


def _short_error(text):
    try:
        data = json.loads(text)
        err = data.get("error", data)
        if isinstance(err, dict):
            return str(err.get("message") or err)[:400]
        return str(err)[:400]
    except (json.JSONDecodeError, AttributeError):
        return text[:400]


def image_part(png_bytes):
    import base64
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png_bytes).decode()}}
