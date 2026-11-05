import json

import pytest
from fastapi.testclient import TestClient

from benchmark.runner import CSV_FIELDS, run_benchmark, strip_patterns
from dashboard.app import create_app
from dashboard.store import ProjectStore
from slm.providers import FakeProvider
from tests.conftest import DOMAINS
from tests.test_nl_and_approval import HOSPITAL

LIB = (DOMAINS / "library_structured.xml").read_text()


@pytest.fixture
def dash(tmp_path):
    fake = FakeProvider(lambda prompt: HOSPITAL)
    app = create_app(ProjectStore(tmp_path / "ws"), provider_factory=lambda cfg: fake)
    with TestClient(app) as c:
        yield c


def test_pages_render(dash):
    assert dash.get("/").status_code == 200
    pid = dash.post("/api/projects", json={"name": "L", "mode": "xml", "xml": LIB}).json()["id"]
    assert dash.get(f"/projects/{pid}").status_code == 200
    assert dash.get("/static/project.js").status_code == 200
    assert dash.get("/projects/zzzzzzzzzz").status_code == 404


def test_xml_project_generate_and_files(dash):
    p = dash.post("/api/projects", json={"name": "L", "mode": "xml", "xml": LIB}).json()
    assert p["validation"]["errors"] == [] and p["can_generate"]
    g = dash.post(f"/api/projects/{p['id']}/generate", json={}).json()
    assert "app/main.py" in g["generation"]["files"]
    assert g["generation"]["metrics"]["slm"]["total_tokens"] == 0
    f = dash.get(f"/api/projects/{p['id']}/file", params={"path": "app/models.py"}).json()
    assert "class Book" in f["content"]
    assert dash.get(f"/api/projects/{p['id']}/file", params={"path": "../project.json"}).status_code == 404
    assert dash.get(f"/api/projects/{p['id']}/file", params={"path": "../../../../etc/passwd"}).status_code == 404


def test_invalid_xml_blocks_generation(dash):
    p = dash.post("/api/projects", json={"name": "bad", "mode": "xml", "xml": "<domain"}).json()
    assert p["validation"]["errors"] and not p["can_generate"]
    assert dash.post(f"/api/projects/{p['id']}/generate", json={}).status_code == 422


def test_natural_language_requires_approval(dash):
    p = dash.post("/api/projects", json={"name": "H", "mode": "nl", "requirements": "hospital"}).json()
    pid = p["id"]
    p = dash.post(f"/api/projects/{pid}/extract", json={"requirements": "hospital site"}).json()
    assert "Patient" in p["xml"] and p["status"] == "draft" and not p["can_generate"]
    assert dash.post(f"/api/projects/{pid}/generate", json={}).status_code == 403     # gate
    assert dash.post(f"/api/projects/{pid}/approve").json()["status"] == "approved"
    edited = p["xml"].replace("Hospital", "CityHospital")
    after = dash.put(f"/api/projects/{pid}/xml", json={"xml": edited}).json()
    assert after["status"] == "draft" and after["human_edits"] == 1                    # edit withdraws approval
    assert dash.post(f"/api/projects/{pid}/generate", json={}).status_code == 403
    dash.post(f"/api/projects/{pid}/approve")
    ok = dash.post(f"/api/projects/{pid}/generate", json={"use_slm": False})
    assert ok.status_code == 200 and ok.json()["generation"]["files"]


def test_extract_without_slm_gives_setup_help(tmp_path):
    app = create_app(ProjectStore(tmp_path / "w"), provider_factory=lambda cfg: None)
    with TestClient(app) as c:
        pid = c.post("/api/projects", json={"name": "H", "mode": "nl"}).json()["id"]
        r = c.post(f"/api/projects/{pid}/extract", json={"requirements": "x"})
        assert r.status_code == 409 and "No SLM configured" in r.json()["detail"]


def test_generate_with_slm_records_tokens(tmp_path):
    good = {"status": "mapped", "pattern": "link_requires_value", "params": {
        "relationship": "borrows", "side": "to", "attribute": "available", "value": True}}
    app = create_app(ProjectStore(tmp_path / "w"), provider_factory=lambda cfg: FakeProvider([good]))
    with TestClient(app) as c:
        xml = (DOMAINS / "library.xml").read_text()
        pid = c.post("/api/projects", json={"name": "L", "mode": "xml", "xml": xml}).json()["id"]
        g = c.post(f"/api/projects/{pid}/generate", json={"use_slm": True}).json()["generation"]
        assert g["metrics"]["slm"]["calls"] == 1 and g["metrics"]["slm"]["total_tokens"] > 0
        assert len(g["report"]["implemented_rules"]) == 1


def test_dashboard_runs_tests_on_generated_app(dash):
    pid = dash.post("/api/projects", json={"name": "L", "mode": "xml", "xml": LIB}).json()["id"]
    dash.post(f"/api/projects/{pid}/generate", json={})
    t = dash.post(f"/api/projects/{pid}/test").json()["tests"]
    assert t["startup_success"] and t["summary"]["functional_pass_rate"] == 1.0


def test_slm_settings_endpoint(dash, monkeypatch, tmp_path):
    import slm.config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "slm.json")
    assert dash.put("/api/slm", json={"provider": "cloud"}).status_code == 422
    assert dash.put("/api/slm", json={"provider": "ollama", "model": "m"}).json()["model"] == "m"
    assert "environment" in dash.get("/api/slm").json()


# --------------------------------------------------------------- benchmark ---
def test_benchmark_collects_metrics_and_reports_unavailable(tmp_path):
    out = tmp_path / "res"
    data = run_benchmark(out_dir=out, domains=["student"], skip_slm=True, progress=lambda m: None)
    runs = {r["config"]: r for r in data["runs"]}
    assert runs["A_deterministic"]["status"] == "ok" and runs["A_oracle"]["status"] == "ok"
    a, o = runs["A_deterministic"], runs["A_oracle"]
    assert a["startup_success"] == 1 and a["syntax_success"] == 1.0
    assert a["total_tokens"] == 0 and a["generation_latency_s"] > 0 and a["server_memory_mb"]
    assert o["business_rule_correctness"] == 1.0 and a["business_rule_correctness"] < o["business_rule_correctness"]
    for c in ("Proposed", "B_direct_slm", "C_large_llm"):
        assert runs[c]["status"] == "unavailable" and runs[c]["functional_pass_rate"] is None   # not fabricated
    assert (out / "results.json").exists() and (out / "results.csv").exists()
    header = (out / "results.csv").read_text().splitlines()[0].split(",")
    assert header == CSV_FIELDS
    assert json.loads((out / "results.json").read_text())["splits"]["eval"]


def test_benchmark_splits_are_disjoint():
    from benchmark.runner import SPLITS
    assert not set(SPLITS["dev"]) & set(SPLITS["eval"])


def test_proposed_method_with_fake_slm(tmp_path, monkeypatch):
    import benchmark.runner as br
    good = lambda prompt: {"status": "mapped", "pattern": "numeric_range",
                           "params": {"attribute": "gpa", "min": 0, "max": 4}}
    monkeypatch.setattr(br, "build_provider", lambda cfg: FakeProvider(good))
    monkeypatch.setattr(br, "detect_environment", lambda: {"recommended": {"provider": "ollama", "model": "fake"},
                                                           "ollama": {"models": [], "running": False}})
    monkeypatch.setattr(br, "run_nl_benchmark", lambda p, progress=None: [])
    data = br.run_benchmark(out_dir=tmp_path / "r", domains=["student"], progress=lambda m: None)
    prop = next(r for r in data["runs"] if r["config"] == "Proposed")
    assert prop["status"] == "ok" and prop["total_tokens"] > 0 and prop["model"] == "fake-slm"
    assert prop["rules_implemented"] >= 1
