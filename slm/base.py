"""Provider-independent types for local language-model access."""
import json
import re
import time
from dataclasses import dataclass, field


class SLMError(RuntimeError):
    """The model could not be reached or produced unusable output."""


@dataclass
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0          # wall-clock time of the whole call
    inference_s: float = 0.0        # model-side time as reported by the server (0 if unknown)
    model: str = ""
    provider: str = ""

    @property
    def total_tokens(self):
        return self.input_tokens + self.output_tokens


@dataclass
class UsageTracker:
    """Accumulates token/latency usage over several calls."""
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    inference_s: float = 0.0
    log: list = field(default_factory=list)

    def add(self, response, purpose=""):
        self.calls += 1
        self.input_tokens += response.input_tokens
        self.output_tokens += response.output_tokens
        self.latency_s += response.latency_s
        self.inference_s += response.inference_s
        self.log.append({"purpose": purpose, "input_tokens": response.input_tokens,
                         "output_tokens": response.output_tokens, "latency_s": round(response.latency_s, 3),
                         "model": response.model})

    @property
    def total_tokens(self):
        return self.input_tokens + self.output_tokens

    def as_dict(self):
        return {"calls": self.calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "total_tokens": self.total_tokens, "latency_s": round(self.latency_s, 3),
                "inference_s": round(self.inference_s, 3)}


class Provider:
    name = "base"
    model = ""

    def generate(self, prompt, system=None, json_mode=True, max_tokens=1024):  # pragma: no cover - interface
        raise NotImplementedError


def timed(fn):
    start = time.perf_counter()
    result = fn()
    return result, time.perf_counter() - start


_THINK = re.compile(r"<think>.*?</think>", re.S)


def extract_json(text):
    """Return the first JSON object found in ``text`` (models often wrap it in prose/fences)."""
    cleaned = _THINK.sub("", text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.M).strip()
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(cleaned[start:i + 1])
                        if isinstance(value, dict):
                            return value
                    except json.JSONDecodeError:
                        pass
                    break
        start = cleaned.find("{", start + 1)
    raise SLMError("The model did not return a valid JSON object.")
