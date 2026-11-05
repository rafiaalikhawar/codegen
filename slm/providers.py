"""Local model providers: Ollama, LM Studio (OpenAI-compatible) and a scripted fake for tests."""
import json
import time

import httpx

from slm.base import LLMResponse, Provider, SLMError

OLLAMA_URL = "http://127.0.0.1:11434"
LMSTUDIO_URL = "http://127.0.0.1:1234"


class OllamaProvider(Provider):
    name = "ollama"

    def __init__(self, model, base_url=None, timeout=300, temperature=0.0):
        self.model, self.base_url = model, (base_url or OLLAMA_URL).rstrip("/")
        self.timeout, self.temperature = timeout, temperature

    def generate(self, prompt, system=None, json_mode=True, max_tokens=1024):
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        body = {"model": self.model, "messages": messages, "stream": False,
                "options": {"temperature": self.temperature, "num_predict": max_tokens}}
        if json_mode:
            body["format"] = "json"
        start = time.perf_counter()
        try:
            r = httpx.post(f"{self.base_url}/api/chat", json=body, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise SLMError(f"Ollama request failed: {exc}") from exc
        elapsed = time.perf_counter() - start
        inference = max((data.get("total_duration", 0) - data.get("load_duration", 0)) / 1e9, 0.0)
        return LLMResponse(
            text=data.get("message", {}).get("content", ""),
            input_tokens=data.get("prompt_eval_count", 0), output_tokens=data.get("eval_count", 0),
            latency_s=elapsed, inference_s=inference, model=self.model, provider=self.name)


class LMStudioProvider(Provider):
    name = "lmstudio"

    def __init__(self, model, base_url=None, timeout=300, temperature=0.0):
        self.model, self.base_url = model, (base_url or LMSTUDIO_URL).rstrip("/")
        self.timeout, self.temperature = timeout, temperature

    def generate(self, prompt, system=None, json_mode=True, max_tokens=1024):
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        body = {"model": self.model, "messages": messages, "temperature": self.temperature,
                "max_tokens": max_tokens, "stream": False}
        start = time.perf_counter()
        try:
            r = httpx.post(f"{self.base_url}/v1/chat/completions", json=body, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            text = data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as exc:
            raise SLMError(f"LM Studio request failed: {exc}") from exc
        usage = data.get("usage", {})
        elapsed = time.perf_counter() - start
        return LLMResponse(text=text, input_tokens=usage.get("prompt_tokens", 0),
                           output_tokens=usage.get("completion_tokens", 0), latency_s=elapsed,
                           inference_s=elapsed, model=self.model, provider=self.name)


class FakeProvider(Provider):
    """Deterministic scripted provider for tests: replies are strings or callables(prompt)."""
    name = "fake"

    def __init__(self, replies, model="fake-slm"):
        self.replies = list(replies) if isinstance(replies, (list, tuple)) else replies
        self.model = model
        self.prompts = []

    def generate(self, prompt, system=None, json_mode=True, max_tokens=1024):
        self.prompts.append(prompt)
        reply = self.replies(prompt) if callable(self.replies) else (
            self.replies.pop(0) if self.replies else "{}")
        if isinstance(reply, dict):
            reply = json.dumps(reply)
        return LLMResponse(text=reply, input_tokens=len(prompt.split()), output_tokens=len(reply.split()),
                           latency_s=0.001, inference_s=0.001, model=self.model, provider=self.name)
