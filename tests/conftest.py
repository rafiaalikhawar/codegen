import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from evaluation.runner import load_app
from generator.code_generator import generate_project

DOMAINS = Path(__file__).resolve().parent.parent / "domain_models"
ALL_DOMAINS = ["library_structured", "hospital", "student", "ecommerce", "inventory", "hotel"]


@pytest.fixture(scope="session")
def generated(tmp_path_factory):
    """Generate every demo domain once per session: name -> GenerationResult."""
    base = tmp_path_factory.mktemp("generated")
    return {name: generate_project(DOMAINS / f"{name}.xml", base / name) for name in ALL_DOMAINS}


@pytest.fixture
def make_client(generated, tmp_path):
    clients = []

    def factory(domain):
        app = load_app(generated[domain].output_dir, tmp_path / f"{domain}.db")
        client = TestClient(app)
        client.__enter__()
        clients.append((client, app))
        return client

    yield factory
    for client, app in clients:
        client.__exit__(None, None, None)
        app.state.db.dispose()


@pytest.fixture
def library(make_client):
    return make_client("library_structured")


def csrf(client, path):
    html = client.get(path).text
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
