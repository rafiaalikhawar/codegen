import pytest

from benchmark.runner import strip_patterns
from evaluation.functional import run_functional_suite, summarize
from parser.xml_parser import parse_domain_model
from parser.ir import derive_relationships
from tests.conftest import ALL_DOMAINS, DOMAINS


@pytest.mark.parametrize("domain", ALL_DOMAINS)
def test_functional_suite_passes_for_every_domain(make_client, domain):
    truth = parse_domain_model(DOMAINS / f"{domain}.xml")
    checks = run_functional_suite(make_client(domain), truth)
    failed = [(c.name, c.detail) for c in checks if not c.passed]
    assert not failed, failed
    s = summarize(checks)
    assert s["by_category"]["rule"]["total"] > 0 and s["requirement_coverage"] == 1.0


def test_suite_detects_missing_business_rules(make_client, tmp_path):
    """Sensitivity check: an app generated without the rules must fail the rule checks."""
    from evaluation.runner import load_app
    from fastapi.testclient import TestClient
    from generator.code_generator import generate_project
    from parser.xml_writer import model_to_xml
    truth = parse_domain_model(DOMAINS / "student.xml")
    res = generate_project(model_to_xml(strip_patterns(truth)), tmp_path / "g")
    app = load_app(res.output_dir, tmp_path / "d.db")
    with TestClient(app) as c:
        s = summarize(run_functional_suite(c, truth))
    assert s["business_rule_correctness"] < 0.7
    assert s["by_category"]["crud"]["rate"] == 1.0


def test_interfaces_models_routes_differ(generated):
    import re
    routes = {n: set(re.findall(r'"/(\w+)"', (r.output_dir / "app/api.py").read_text())) for n, r in generated.items()}
    assert "patient" in routes["hospital"] and "patient" not in routes["library_structured"]
    assert "course" in routes["student"] and "course" not in routes["hospital"]
