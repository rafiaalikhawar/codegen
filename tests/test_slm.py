import json

import pytest

from generator.code_generator import generate_project
from parser.xml_parser import parse_domain_model
from slm.base import SLMError, UsageTracker, extract_json
from slm.config import SLMConfig, build_provider, detect_environment, pick_default_model
from slm.providers import FakeProvider, OllamaProvider
from slm.rule_interpreter import applicable, interpret_rules
from validator.model_validator import validate
from tests.conftest import DOMAINS

GOOD = {"status": "mapped", "pattern": "link_requires_value", "reason": "r",
        "params": {"relationship": "borrows", "side": "to", "attribute": "available", "value": True,
                   "set_on_link": False, "set_on_unlink": True}}


def library_model():
    m = parse_domain_model(DOMAINS / "library.xml")
    validate(m)
    return m


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Sure!\n```json\n{"a": {"b": "}"}}\n```') == {"a": {"b": "}"}}
    assert extract_json('<think>{"x":0}</think>{"a": 2}') == {"a": 2}
    with pytest.raises(SLMError):
        extract_json("no json here")


def test_valid_interpretation_is_applied_and_enforced(tmp_path):
    out = interpret_rules(library_model(), FakeProvider([GOOD]))
    res = out["resolutions"]["rule1"]
    assert res["status"] == "mapped" and res["params"]["value"] is True and res["params"]["set_on_link"] is False
    assert out["usage"].calls == 1 and out["usage"].total_tokens > 0
    result = generate_project(DOMAINS / "library.xml", tmp_path / "o", applicable(out["resolutions"]))
    assert len(result.report["implemented_rules"]) == 1 and not result.report["unimplemented_rules"]


def test_invalid_output_is_rejected_after_one_repair():
    bad = {"status": "mapped", "pattern": "link_requires_value",
           "params": {"relationship": "ghost", "side": "to", "attribute": "available", "value": "true"}}
    fake = FakeProvider([bad, bad])
    res = interpret_rules(library_model(), fake)["resolutions"]["rule1"]
    assert res["status"] == "rejected" and "unknown relationship" in res["reason"]
    assert len(fake.prompts) == 2 and "rejected" in fake.prompts[1]            # repair prompt carried the errors
    assert applicable({"rule1": res}) == {}


def test_repair_round_can_recover():
    bad = {"status": "mapped", "pattern": "link_requires_value", "params": {"relationship": "borrows"}}
    res = interpret_rules(library_model(), FakeProvider([bad, GOOD]))["resolutions"]["rule1"]
    assert res["status"] == "mapped" and res["repairs"] == 1


def test_unknown_pattern_and_garbage_rejected():
    r = interpret_rules(library_model(), FakeProvider([{"status": "mapped", "pattern": "rm_rf", "params": {}}] * 2))
    assert r["resolutions"]["rule1"]["status"] == "rejected"
    r = interpret_rules(library_model(), FakeProvider(["not json at all"] * 2))
    assert r["resolutions"]["rule1"]["status"] == "error"


def test_unsupported_and_ambiguous_stay_unimplemented(tmp_path):
    for status in ("unsupported", "ambiguous"):
        out = interpret_rules(library_model(), FakeProvider([{"status": status, "reason": "hm"}]))
        assert out["resolutions"]["rule1"]["status"] == status
        result = generate_project(DOMAINS / "library.xml", tmp_path / status, applicable(out["resolutions"]))
        assert len(result.report["unimplemented_rules"]) == 1


def test_model_never_asked_about_structured_rules():
    fake = FakeProvider([GOOD])
    interpret_rules(parse_domain_model(DOMAINS / "student.xml"), fake)
    assert fake.prompts == []                       # no tokens spent on rules already expressed


def test_prompt_is_compact():
    fake = FakeProvider([GOOD])
    interpret_rules(library_model(), fake)
    assert len(fake.prompts[0].split()) < 450


def test_provider_unreachable_is_reported_not_crash():
    p = OllamaProvider("x", base_url="http://127.0.0.1:9", timeout=1)
    res = interpret_rules(library_model(), p, max_repairs=0)["resolutions"]["rule1"]
    assert res["status"] == "error" and "failed" in res["reason"]


def test_config_and_detection(monkeypatch):
    assert build_provider(SLMConfig()) is None
    assert build_provider(SLMConfig(provider="ollama", model="m")).name == "ollama"
    assert build_provider(SLMConfig(provider="lmstudio", model="m")).name == "lmstudio"
    with pytest.raises(ValueError):
        build_provider(SLMConfig(provider="cloud", model="m"))
    assert pick_default_model(["nomic-embed-text", "deepseek-r1:8b", "mistral:7b"]) == "mistral:7b"
    assert pick_default_model(["nomic-embed-text"]) is None
    env = detect_environment(timeout=0.2)
    assert {"ollama", "lmstudio", "instructions", "recommended"} <= set(env)


def test_usage_tracker():
    u = UsageTracker()
    u.add(FakeProvider(["{}"]).generate("a b c"), "x")
    assert u.as_dict()["calls"] == 1 and u.total_tokens == u.input_tokens + u.output_tokens


@pytest.mark.live_slm
def test_live_model_maps_library_rule():
    env = detect_environment()
    if not env["recommended"]:
        pytest.skip("no local model available")
    rec = env["recommended"]
    prov = build_provider(SLMConfig(provider=rec["provider"], model=rec["model"], timeout=600))
    res = interpret_rules(library_model(), prov)["resolutions"]["rule1"]
    assert res["status"] == "mapped" and res["pattern"] == "link_requires_value"
