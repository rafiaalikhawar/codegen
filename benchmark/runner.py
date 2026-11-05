"""Reproducible benchmark: Baseline A / A-oracle / Proposed / Baseline B / Baseline C.

Every configuration receives the same input - the domain model with business rules written as
free text only.  Results are always checked against the *structured* ground-truth model.
Unavailable configurations are reported as unavailable, never fabricated.
"""
import copy
import csv
import json
import os
try:
    import resource  # Unix only
except ImportError:  # Windows
    resource = None
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

from evaluation.functional import checks_to_dicts, run_functional_suite, summarize
from generator.code_generator import GenerationError, generate_project, prepare_model
from parser.ir import derive_relationships, to_snake
from parser.xml_parser import parse_domain_model
from parser.xml_writer import model_to_xml
from slm.base import SLMError, UsageTracker
from slm.config import build_provider, detect_environment, pick_default_model, SLMConfig
from slm.rule_interpreter import applicable, interpret_rules

ROOT = Path(__file__).resolve().parent.parent
DOMAIN_DIR = ROOT / "domain_models"
SPLITS = json.loads((Path(__file__).parent / "splits.json").read_text())
CONFIGS = ["A_deterministic", "A_oracle", "Proposed", "B_direct_slm", "C_large_llm"]
CSV_FIELDS = ["config", "domain", "split", "model", "status", "input_tokens", "output_tokens", "total_tokens",
              "generation_latency_s", "model_inference_latency_s", "server_memory_mb", "generator_peak_rss_mb",
              "syntax_success", "startup_success", "functional_pass_rate", "requirement_coverage",
              "relationship_correctness", "business_rule_correctness", "human_correction_iterations",
              "auto_repair_rounds", "rules_total", "rules_implemented", "notes"]


def strip_patterns(model):
    """Same model, but with every business rule reduced to its English description."""
    m = copy.deepcopy(model)
    for c in m["constraints"]:
        c["pattern"], c["params"] = None, {}
    return m


def _windows_peak_mb():
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    c = Counters()
    c.cb = ctypes.sizeof(c)
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    ok = ctypes.windll.psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return round(c.PeakWorkingSetSize / (1024 * 1024), 1) if ok else 0.0


def peak_rss_mb():
    if resource is None:
        return _windows_peak_mb()
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024, 1)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def syntax_ok(app_dir):
    files = [p for p in Path(app_dir).rglob("*.py") if ".venv" not in p.parts]
    if not files:
        return 0.0
    good = 0
    for f in files:
        try:
            compile(f.read_text(encoding="utf-8"), str(f), "exec")
            good += 1
        except (SyntaxError, ValueError, UnicodeDecodeError):
            pass
    return good / len(files)


def _process_rss_mb(pid):
    """Resident memory of another process in MB, or None if it cannot be read."""
    try:
        if sys.platform == "win32":
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                               capture_output=True, text=True)
            fields = next(csv.reader([r.stdout.strip().splitlines()[0]]))
            kb = "".join(ch for ch in fields[4] if ch.isdigit())  # e.g. "45,312 K"
            return round(int(kb) / 1024, 1)
        r = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True)
        return round(int(r.stdout) / 1024, 1) if r.stdout.strip().isdigit() else None
    except (OSError, IndexError, ValueError, StopIteration):
        return None


def evaluate_app(app_dir, truth_model, startup_timeout=25):
    """Start the app with uvicorn on a temp database, run the functional suite over HTTP."""
    out = {"startup_success": False, "server_memory_mb": None, "summary": None, "checks": [], "error": ""}
    port = _free_port()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        env = dict(os.environ, DATABASE_URL=f"sqlite:///{tmp}/bench.db")
        log = open(Path(tmp) / "server.log", "w")
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                 "--port", str(port), "--log-level", "warning"],
                                cwd=app_dir, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.time() + startup_timeout
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15) as client:
                while time.time() < deadline and proc.poll() is None:
                    try:
                        if client.get("/openapi.json").status_code == 200:
                            out["startup_success"] = True
                            break
                    except httpx.HTTPError:
                        time.sleep(0.3)
                if out["startup_success"]:
                    checks = run_functional_suite(client, copy.deepcopy(truth_model))
                    out.update(checks=checks_to_dicts(checks), summary=summarize(checks))
                    out["server_memory_mb"] = _process_rss_mb(proc.pid)
                else:
                    log.flush()
                    out["error"] = (Path(tmp) / "server.log").read_text()[-300:]
        finally:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
            log.close()
    return out


def _row(config, domain, split, model="", **kw):
    row = {k: None for k in CSV_FIELDS}
    row.update(config=config, domain=domain, split=split, model=model, status="ok",
               human_correction_iterations=0, auto_repair_rounds=0, notes="")
    row.update(kw)
    return row


def _fill_eval(row, ev, app_dir):
    row["syntax_success"] = round(syntax_ok(app_dir), 3)
    row["startup_success"] = int(ev["startup_success"])
    row["server_memory_mb"] = ev["server_memory_mb"]
    s = ev["summary"]
    if s:
        row["functional_pass_rate"] = round(s["functional_pass_rate"], 3)
        row["requirement_coverage"] = round(s["requirement_coverage"], 3)
        row["relationship_correctness"] = None if s["relationship_correctness"] is None else round(s["relationship_correctness"], 3)
        row["business_rule_correctness"] = None if s["business_rule_correctness"] is None else round(s["business_rule_correctness"], 3)
    else:
        for k in ("functional_pass_rate", "requirement_coverage"):
            row[k] = 0.0
        row["notes"] = (row["notes"] + " app did not start: " + ev["error"]).strip()
    return row


def run_engine_config(config, domain, split, truth, text_model, work, provider):
    """A_deterministic / A_oracle / Proposed - the template pipeline."""
    app_dir = work / config / domain
    model_name = provider.model if (config == "Proposed" and provider) else ""
    row = _row(config, domain, split, model_name)
    start = time.perf_counter()
    usage = UsageTracker()
    resolutions = None
    source = model_to_xml(truth if config == "A_oracle" else text_model)
    if config == "Proposed":
        if provider is None:
            return _row(config, domain, split, status="unavailable", notes="no local SLM configured/available")
        pm, _ = prepare_model(source)
        outcome = interpret_rules(pm, provider, usage)
        resolutions = applicable(outcome["resolutions"])
        row["auto_repair_rounds"] = sum(r.get("repairs", 0) for r in outcome["resolutions"].values())
    result = generate_project(source, app_dir, resolutions)
    row["generation_latency_s"] = round(time.perf_counter() - start, 3)
    row.update(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, total_tokens=usage.total_tokens,
               model_inference_latency_s=round(usage.inference_s, 3), generator_peak_rss_mb=peak_rss_mb(),
               rules_total=len(truth["constraints"]), rules_implemented=len(result.report["implemented_rules"]))
    return _fill_eval(row, evaluate_app(app_dir, truth), app_dir)


DIRECT_PROMPT = """Write a complete web application as ONE Python file (main.py) using FastAPI and SQLite for this domain model.

DOMAIN MODEL (XML):
{xml}

REQUIREMENTS
- The FastAPI instance must be named `app`. Read the database location from the DATABASE_URL environment variable (sqlite:///path), default sqlite:///app.db. Create tables at startup.
- For every entity: REST endpoints under /api/<snake_case_entity>: POST (201, JSON body), GET list, GET /{{id}}, PUT /{{id}} (partial update), DELETE /{{id}}; unknown id -> 404; invalid input -> 422 (or 400).
- Enforce required attributes, unique attributes, default values, allowed choices, and type validation.
- Relationships: many-to-one / one-to-one / one-to-many are integer foreign-key columns named <target_snake>_id on the holding entity (many-to-one and one-to-one: on the "from" entity; one-to-many: on the "to" entity). Many-to-many: endpoints POST/GET /api/<entity>/<id>/links/<relationship_name> (body {{"target_id": n}}) and DELETE /api/<entity>/<id>/links/<relationship_name>/<target_id>, in both directions.
- Implement the business rules described in the constraints (return 409 or 422 when violated).
- GET /health returns {{"status":"ok"}}; GET / and GET /web/<snake_case_entity> return HTML pages listing records.
Return ONLY the code inside a single ```python block."""


def _extract_code(text):
    import re
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.S)
    if blocks:
        return max(blocks, key=len)
    if "FastAPI" in text and "def " in text:
        return text
    return None


def run_direct_config(config, domain, split, truth, text_model, work, provider, max_tokens):
    row = _row(config, domain, split, provider.model if provider else "")
    if provider is None:
        return _row(config, domain, split, status="unavailable",
                    notes="no suitable large model/provider configured" if config == "C_large_llm" else "no local SLM available")
    app_dir = work / config / domain / "app"
    start = time.perf_counter()
    usage = UsageTracker()
    try:
        response = provider.generate(DIRECT_PROMPT.format(xml=model_to_xml(text_model)), json_mode=False,
                                     max_tokens=max_tokens)
        usage.add(response, "direct_generation")
    except SLMError as exc:
        row.update(status="error", notes=str(exc))
        return row
    row["generation_latency_s"] = round(time.perf_counter() - start, 3)
    row.update(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, total_tokens=usage.total_tokens,
               model_inference_latency_s=round(usage.inference_s, 3), generator_peak_rss_mb=peak_rss_mb(),
               rules_total=len(truth["constraints"]), rules_implemented=None)
    code = _extract_code(response.text)
    if code is None:
        row.update(syntax_success=0.0, startup_success=0, functional_pass_rate=0.0, requirement_coverage=0.0,
                   notes="no code block in model output")
        return row
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "__init__.py").write_text("")
    (app_dir / "main.py").write_text(code)
    return _fill_eval(row, evaluate_app(app_dir.parent, truth), app_dir)


def _mean(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.mean(values), 3) if values else None


def summarize_rows(rows):
    out = []
    for split in ("dev", "eval"):
        for config in CONFIGS:
            sel = [r for r in rows if r["config"] == config and r["split"] == split]
            if not sel:
                continue
            ok = [r for r in sel if r["status"] == "ok"]
            item = {"config": config, "split": split, "domains": len(sel), "completed": len(ok),
                    "status": "ok" if ok else sel[0]["status"], "notes": "" if ok else sel[0]["notes"]}
            for k in ("input_tokens", "output_tokens", "total_tokens", "generation_latency_s",
                      "model_inference_latency_s", "server_memory_mb", "syntax_success", "startup_success",
                      "functional_pass_rate", "requirement_coverage", "relationship_correctness",
                      "business_rule_correctness", "human_correction_iterations"):
                item[k] = _mean([r[k] for r in ok])
            out.append(item)
    return out


def run_benchmark(models=None, out_dir="benchmark_results", domains=None, large_model=None, skip_slm=False,
                  max_tokens=3500, progress=print):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    env = detect_environment()
    model_name = (models or [None])[0] or (env["recommended"] or {}).get("model")
    provider = None
    if not skip_slm and model_name:
        prov_name = (env["recommended"] or {}).get("provider", "ollama")
        provider = build_provider(SLMConfig(provider=prov_name, model=model_name, timeout=900))
    large = None
    if large_model:
        installed = env["ollama"]["models"]
        if large_model in installed and env["ollama"]["running"]:
            large = build_provider(SLMConfig(provider="ollama", model=large_model, timeout=1800))
    work = Path(tempfile.mkdtemp(prefix="slmcg_bench_"))
    selected = {"dev": [d for d in SPLITS["dev"]], "eval": [d for d in SPLITS["eval"]]}
    rows = []
    for split, names in selected.items():
        for domain in names:
            if domains and domain not in domains:
                continue
            truth = parse_domain_model(DOMAIN_DIR / f"{domain}.xml")
            derive_relationships(truth)
            text_model = strip_patterns(truth)
            for config in CONFIGS:
                progress(f"[{split}] {domain} / {config}")
                try:
                    if config in ("A_deterministic", "A_oracle", "Proposed"):
                        row = run_engine_config(config, domain, split, truth, text_model, work, provider)
                    elif config == "B_direct_slm":
                        row = run_direct_config(config, domain, split, truth, text_model, work, provider, max_tokens)
                    else:
                        row = run_direct_config(config, domain, split, truth, text_model, work, large, max_tokens)
                except Exception as exc:  # a failed run is a result, not a crash
                    row = _row(config, domain, split, status="error", notes=f"{type(exc).__name__}: {exc}")
                rows.append(row)
    nl_rows = run_nl_benchmark(provider, progress) if provider else []
    payload = {
        "environment": {"slm_model": model_name if provider else None, "ollama_models": env["ollama"]["models"],
                        "large_model_requested": large_model, "large_model_available": large is not None,
                        "platform": sys.platform, "python": sys.version.split()[0]},
        "splits": SPLITS, "runs": rows, "summary": summarize_rows(rows), "nl_to_model": nl_rows,
    }
    (out_dir / "results.json").write_text(json.dumps(payload, indent=2))
    with open(out_dir / "results.csv", "w", newline="") as fh:
        writer = csv.DictWriter(fh, CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    summary = payload["summary"]
    if summary:
        with open(out_dir / "summary.csv", "w", newline="") as fh:
            writer = csv.DictWriter(fh, list(summary[0].keys()))
            writer.writeheader()
            writer.writerows(summary)
    progress(f"Results written to {out_dir}")
    return payload


def run_nl_benchmark(provider, progress=print):
    from nl2model.extractor import extract_domain_model
    prompts = json.loads((Path(__file__).parent / "nl_prompts.json").read_text())
    rows = []
    for split, items in prompts.items():
        for item in items:
            progress(f"[{split}] NL extraction {item['id']}")
            start = time.perf_counter()
            try:
                res = extract_domain_model(item["text"], provider)
            except Exception as exc:
                rows.append({"id": item["id"], "split": split, "status": "error", "notes": str(exc)})
                continue
            names = {e["name"] for e in (res["ir"] or {}).get("entities", [])}
            expected = set(item["expected_entities"])
            rows.append({"id": item["id"], "split": split, "status": "ok", "valid_model": not res["errors"],
                         "entity_recall": round(len(names & expected) / len(expected), 3),
                         "entities_found": sorted(names), "validation_errors": res["errors"],
                         "repair_rounds": res["repair_rounds"], "human_correction_iterations": None,
                         "latency_s": round(time.perf_counter() - start, 3), **res["usage"]})
    return rows
