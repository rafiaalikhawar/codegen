"""Local dashboard for the generator itself (not part of the generated websites)."""
import socket
import subprocess
import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from benchmark.runner import evaluate_app
from dashboard.store import ProjectStore
from generator.code_generator import GenerationError, generate_project, prepare_model
from nl2model.approval import ApprovalError, Proposal
from nl2model.extractor import extract_domain_model
from parser.ir import derive_relationships
from parser.xml_parser import DomainParseError, parse_domain_string
from slm.base import SLMError, UsageTracker
from slm.config import SLMConfig, build_provider, detect_environment, load_config, save_config
from slm.rule_interpreter import applicable, interpret_rules
from validator.model_validator import validate

BASE = Path(__file__).resolve().parent
MAX_FILE_BYTES = 200_000
MAX_XML_CHARS = 200_000


def create_app(store=None, provider_factory=None):
    """``provider_factory(config) -> Provider|None`` is injectable so tests can use a fake SLM."""
    app = FastAPI(title="SLM-CodeGen Dashboard")
    app.state.store = store or ProjectStore()
    app.state.procs = {}
    app.state.provider_factory = provider_factory or build_provider
    templates = Jinja2Templates(directory=str(BASE / "templates"))
    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

    def project_or_404(pid):
        try:
            return app.state.store.load(pid)
        except KeyError:
            raise HTTPException(404, "Project not found")

    def validation_of(xml):
        try:
            model = parse_domain_string(xml)
        except DomainParseError as exc:
            return None, {"errors": [str(exc)], "warnings": []}
        return model, validate(model)

    def provider():
        return app.state.provider_factory(load_config())

    def public(project):
        model, report = validation_of(project["xml"]) if project["xml"].strip() else (None, {"errors": ["No domain model yet."], "warnings": []})
        summary = None
        if model and not report["errors"]:
            summary = {"name": model["name"], "entities": [
                {"name": e["name"], "attributes": [{"name": a["name"], "type": a["type"], "pk": a["primary_key"],
                                                    "required": a["required"], "unique": a["unique"]} for a in e["attributes"]]}
                for e in model["entities"]],
                "relationships": model["relationships"], "rules": model["constraints"]}
        return {**project, "validation": report, "summary": summary,
                "requires_approval": project["origin"] == "nl",
                "can_generate": not report["errors"] and (project["origin"] != "nl" or project["status"] == "approved")}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {"projects": app.state.store.list()})

    @app.get("/projects/{pid}", response_class=HTMLResponse)
    def project_page(request: Request, pid: str):
        project_or_404(pid)
        return templates.TemplateResponse(request, "project.html", {"pid": pid})

    class NewProject(BaseModel):
        name: str
        mode: str = "xml"
        xml: str = ""
        requirements: str = ""

    @app.post("/api/projects", status_code=201)
    def create_project(body: NewProject):
        if body.mode not in ("xml", "nl"):
            raise HTTPException(422, "mode must be 'xml' or 'nl'")
        if len(body.xml) > MAX_XML_CHARS:
            raise HTTPException(413, "XML too large")
        return public(app.state.store.create(body.name, body.mode, body.xml, body.requirements))

    @app.get("/api/projects")
    def list_projects():
        return app.state.store.list()

    @app.get("/api/projects/{pid}")
    def get_project(pid: str):
        return public(project_or_404(pid))

    class XmlBody(BaseModel):
        xml: str

    @app.put("/api/projects/{pid}/xml")
    def edit_xml(pid: str, body: XmlBody):
        if len(body.xml) > MAX_XML_CHARS:
            raise HTTPException(413, "XML too large")
        project = project_or_404(pid)
        proposal = Proposal.from_dict(project)
        proposal.edit(body.xml)
        project.update(xml=proposal.xml, status=proposal.status, approved_hash=None, approved_at=None,
                       human_edits=proposal.human_edits)
        app.state.store.save(project)
        return public(project)

    class ReqBody(BaseModel):
        requirements: str

    @app.post("/api/projects/{pid}/extract")
    def extract(pid: str, body: ReqBody):
        project = project_or_404(pid)
        prov = provider()
        if prov is None:
            raise HTTPException(409, "No SLM configured. Choose a provider/model in the SLM panel "
                                     "(see GET /api/slm for setup instructions).")
        try:
            res = extract_domain_model(body.requirements, prov)
        except (SLMError, ValueError) as exc:
            raise HTTPException(502, str(exc))
        project.update(requirements=body.requirements, xml=res["xml"], status="draft", approved_hash=None,
                       approved_at=None, origin="nl",
                       extraction={"usage": res["usage"], "errors": res["errors"], "warnings": res["warnings"],
                                   "repair_rounds": res["repair_rounds"], "model": prov.model})
        app.state.store.save(project)
        return public(project)

    @app.post("/api/projects/{pid}/approve")
    def approve(pid: str):
        project = project_or_404(pid)
        proposal = Proposal.from_dict(project)
        try:
            proposal.approve()
        except ApprovalError as exc:
            raise HTTPException(422, str(exc))
        project.update(status=proposal.status, approved_hash=proposal.approved_hash, approved_at=proposal.approved_at)
        app.state.store.save(project)
        return public(project)

    class GenBody(BaseModel):
        use_slm: bool = False

    @app.post("/api/projects/{pid}/generate")
    def generate(pid: str, body: GenBody):
        project = project_or_404(pid)
        proposal = Proposal.from_dict(project)
        if project["origin"] == "nl":
            try:
                proposal.require_approved()
            except ApprovalError as exc:
                raise HTTPException(403, str(exc))
        usage, resolutions, interp = UsageTracker(), None, {}
        started = time.perf_counter()
        try:
            model, _ = prepare_model(project["xml"])
            if body.use_slm:
                prov = provider()
                if prov is None:
                    raise HTTPException(409, "No SLM configured; untick 'use SLM' or configure one.")
                out = interpret_rules(model, prov, usage)
                interp = out["resolutions"]
                resolutions = applicable(interp)
            result = generate_project(project["xml"], app.state.store.output_dir(pid), resolutions)
        except GenerationError as exc:
            raise HTTPException(422, str(exc))
        project["generation"] = {
            "files": result.files, "report": result.report, "rule_interpretation": interp,
            "metrics": {"generation_seconds": round(time.perf_counter() - started, 3),
                        "template_seconds": round(result.seconds, 3), "slm": usage.as_dict(), "used_slm": body.use_slm},
            "generated_at": time.time()}
        project["tests"] = None
        app.state.store.save(project)
        return public(project)

    def safe_file(pid, rel):
        base = app.state.store.output_dir(pid).resolve()
        target = (base / rel).resolve()
        if base not in target.parents or not target.is_file():
            raise HTTPException(404, "File not found")
        return target

    @app.get("/api/projects/{pid}/file")
    def read_file(pid: str, path: str):
        project_or_404(pid)
        target = safe_file(pid, path)
        if target.stat().st_size > MAX_FILE_BYTES:
            raise HTTPException(413, "File too large to display")
        return {"path": path, "content": target.read_text(encoding="utf-8", errors="replace")}

    @app.post("/api/projects/{pid}/test")
    def run_tests(pid: str):
        project = project_or_404(pid)
        if not project.get("generation"):
            raise HTTPException(409, "Generate the website first.")
        model, _ = prepare_model(project["xml"])
        derive_relationships(model)
        # rules actually implemented in this build are the ground truth for the dashboard test run
        implemented = {r["id"]: r for r in project["generation"]["report"]["implemented_rules"]}
        for c in model["constraints"]:
            if c["id"] in implemented:
                c["pattern"], c["params"] = implemented[c["id"]]["pattern"], implemented[c["id"]]["params"]
            else:
                c["pattern"], c["params"] = None, {}
        ev = evaluate_app(app.state.store.output_dir(pid), model)
        project["tests"] = {"startup_success": ev["startup_success"], "summary": ev["summary"],
                            "failures": [c for c in ev["checks"] if not c["passed"]], "total": len(ev["checks"]),
                            "error": ev["error"], "memory_mb": ev["server_memory_mb"], "run_at": time.time()}
        app.state.store.save(project)
        return public(project)

    def _free_port():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    @app.post("/api/projects/{pid}/launch")
    def launch(pid: str):
        project = project_or_404(pid)
        if not project.get("generation"):
            raise HTTPException(409, "Generate the website first.")
        old = app.state.procs.get(pid)
        if old and old["proc"].poll() is None:
            return {"url": old["url"], "running": True}
        port = _free_port()
        out_dir = app.state.store.output_dir(pid)
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                 "--port", str(port), "--log-level", "warning"], cwd=out_dir,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        app.state.procs[pid] = {"proc": proc, "url": f"http://127.0.0.1:{port}"}
        time.sleep(1.5)
        return {"url": f"http://127.0.0.1:{port}", "running": proc.poll() is None}

    @app.post("/api/projects/{pid}/stop")
    def stop(pid: str):
        entry = app.state.procs.pop(pid, None)
        if entry:
            entry["proc"].terminate()
        return {"running": False}

    @app.on_event("shutdown")
    def _cleanup():
        for entry in app.state.procs.values():
            entry["proc"].terminate()

    @app.delete("/api/projects/{pid}")
    def delete_project(pid: str):
        project_or_404(pid)
        stop(pid)
        app.state.store.delete(pid)
        return {"deleted": pid}

    @app.get("/api/slm")
    def slm_status():
        return {"config": load_config().to_dict(), "environment": detect_environment()}

    class SlmBody(BaseModel):
        provider: str
        model: str = ""
        base_url: str = ""

    @app.put("/api/slm")
    def slm_config(body: SlmBody):
        if body.provider not in ("ollama", "lmstudio", "none"):
            raise HTTPException(422, "provider must be ollama, lmstudio or none")
        cfg = SLMConfig(provider=body.provider, model=body.model, base_url=body.base_url)
        save_config(cfg)
        return cfg.to_dict()

    return app


app = create_app()
