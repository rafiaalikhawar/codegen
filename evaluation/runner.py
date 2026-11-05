"""Run the functional suite against a generated app directory in-process."""
import importlib
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from evaluation.functional import run_functional_suite, summarize


def load_app(app_dir, db_path):
    """Import ``app.main`` from ``app_dir`` (fresh module state) and build an app on ``db_path``."""
    app_dir = str(Path(app_dir).resolve())
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        del sys.modules[name]
    sys.path.insert(0, app_dir)
    try:
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
        main = importlib.import_module("app.main")
        return main.create_app(f"sqlite:///{db_path}")
    finally:
        sys.path.remove(app_dir)
        os.environ.pop("DATABASE_URL", None)


def evaluate_in_process(app_dir, model):
    with tempfile.TemporaryDirectory() as tmp:
        app = load_app(app_dir, Path(tmp) / "eval.db")
        with TestClient(app) as client:
            checks = run_functional_suite(client, model)
        app.state.db.dispose()
    return checks, summarize(checks)
