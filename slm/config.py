"""Provider configuration and environment detection (nothing is assumed to be installed)."""
import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from slm.providers import (LMSTUDIO_URL, OLLAMA_URL, LMStudioProvider,
                           OllamaProvider)

CONFIG_FILE = Path(__file__).resolve().parent.parent / "slm_config.json"
# Order of preference for instruction-following small models; embedding and
# reasoning models are skipped when picking a default automatically.
PREFERRED = ("qwen2.5", "qwen3", "llama3", "gemma", "mistral", "phi3", "phi4", "phi")
SKIP_FRAGMENTS = ("embed", "deepseek-r1", "r1", "rerank")

SETUP_INSTRUCTIONS = {
    "ollama": ("Install Ollama from https://ollama.com, start it (`ollama serve`) and pull a small model, "
               "e.g. `ollama pull qwen2.5:3b` or `ollama pull mistral`."),
    "lmstudio": ("Install LM Studio, download a small instruct model, open the 'Developer' tab and "
                 "start the local server (default http://127.0.0.1:1234)."),
}


@dataclass
class SLMConfig:
    provider: str = "none"           # ollama | lmstudio | none
    model: str = ""
    base_url: str = ""
    timeout: int = 300
    temperature: float = 0.0

    def to_dict(self):
        return asdict(self)


def load_config():
    cfg = SLMConfig()
    if CONFIG_FILE.exists():
        try:
            cfg = SLMConfig(**{k: v for k, v in json.loads(CONFIG_FILE.read_text()).items()
                               if k in SLMConfig.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            pass
    cfg.provider = os.environ.get("SLM_PROVIDER", cfg.provider)
    cfg.model = os.environ.get("SLM_MODEL", cfg.model)
    cfg.base_url = os.environ.get("SLM_BASE_URL", cfg.base_url)
    return cfg


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg.to_dict(), indent=2))


def build_provider(cfg):
    """Return a Provider for ``cfg`` or None when no SLM is configured."""
    if cfg is None or cfg.provider in ("", "none") or not cfg.model:
        return None
    if cfg.provider == "ollama":
        return OllamaProvider(cfg.model, cfg.base_url or None, cfg.timeout, cfg.temperature)
    if cfg.provider == "lmstudio":
        return LMStudioProvider(cfg.model, cfg.base_url or None, cfg.timeout, cfg.temperature)
    raise ValueError(f"Unknown SLM provider '{cfg.provider}' (use ollama, lmstudio or none)")


def pick_default_model(models):
    usable = [m for m in models if not any(s in m.lower() for s in SKIP_FRAGMENTS)]
    for pref in PREFERRED:
        for m in usable:
            if m.lower().startswith(pref):
                return m
    return usable[0] if usable else None


def detect_environment(timeout=2.0):
    """Probe for Ollama / LM Studio and list their models. Never raises."""
    env = {"ollama": {"installed": shutil.which("ollama") is not None, "running": False, "models": [],
                      "url": OLLAMA_URL},
           "lmstudio": {"running": False, "models": [], "url": LMSTUDIO_URL}}
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=timeout)
        env["ollama"]["running"] = r.status_code == 200
        env["ollama"]["models"] = [m["name"] for m in r.json().get("models", [])]
    except (httpx.HTTPError, ValueError):
        pass
    try:
        r = httpx.get(f"{LMSTUDIO_URL}/v1/models", timeout=timeout)
        env["lmstudio"]["running"] = r.status_code == 200
        env["lmstudio"]["models"] = [m["id"] for m in r.json().get("data", [])]
    except (httpx.HTTPError, ValueError):
        pass
    env["recommended"] = None
    for prov in ("ollama", "lmstudio"):
        model = pick_default_model(env[prov]["models"])
        if env[prov]["running"] and model:
            env["recommended"] = {"provider": prov, "model": model}
            break
    env["instructions"] = [] if env["recommended"] else [
        "No usable local model detected. The generator still works without a model (deterministic baseline).",
        SETUP_INSTRUCTIONS["ollama"], "Or: " + SETUP_INSTRUCTIONS["lmstudio"]]
    return env
