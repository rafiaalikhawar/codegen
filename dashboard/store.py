"""File-based project storage for the generator dashboard (workspace/projects/<id>/)."""
import json
import re
import secrets
import time
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent / "workspace" / "projects"
ID_RE = re.compile(r"^[0-9a-f]{10}$")


class ProjectStore:
    def __init__(self, root=None):
        self.root = Path(root) if root else WORKSPACE
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, pid):
        if not ID_RE.match(pid):
            raise KeyError(pid)
        return self.root / pid

    def create(self, name, origin, xml="", requirements=""):
        pid = secrets.token_hex(5)
        project = {"id": pid, "name": name.strip()[:80] or "Untitled", "origin": origin, "xml": xml,
                   "requirements": requirements, "status": "draft", "approved_hash": None, "approved_at": None,
                   "human_edits": 0, "created": time.time(), "slm": {"use_for_rules": False},
                   "extraction": None, "generation": None, "tests": None, "events": []}
        self._dir(pid).mkdir(parents=True)
        self.save(project)
        return project

    def load(self, pid):
        path = self._dir(pid) / "project.json"
        if not path.exists():
            raise KeyError(pid)
        return json.loads(path.read_text())

    def save(self, project):
        (self._dir(project["id"]) / "project.json").write_text(json.dumps(project, indent=2))

    def list(self):
        items = []
        for p in sorted(self.root.glob("*/project.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            d = json.loads(p.read_text())
            items.append({k: d[k] for k in ("id", "name", "origin", "status", "created")})
        return items

    def output_dir(self, pid):
        return self._dir(pid) / "app_output"

    def delete(self, pid):
        import shutil
        shutil.rmtree(self._dir(pid))
