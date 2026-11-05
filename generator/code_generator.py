"""Deterministic generator: validated IR -> self-contained FastAPI + HTML project.

Two-stage Jinja2 rendering:
  stage 1 (generation time) - ``templates/app/**/*.j2`` are rendered with the domain model;
  stage 2 (run time)        - the generated app renders its own ``templates/*.html``.
Runtime templates are copied verbatim (they are never seen by stage 1) and the
``.j2`` HTML files wrap runtime syntax in ``{% raw %}`` blocks.
"""
import ast
import hashlib
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from parser.ir import (derive_relationships, humanize, model_hash, pluralize,
                       strip_derived, to_snake)
from parser.xml_parser import DomainParseError, parse_domain_model, parse_domain_string
from validator.model_validator import validate
from validator.rule_registry import normalize_rule

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_ROOT = PROJECT_ROOT / "templates" / "app"
MANIFEST = ".slm_codegen_manifest.json"

SA_TYPES = {"integer": "Integer", "string": "String(255)", "text": "Text", "boolean": "Boolean",
            "float": "Float", "date": "Date", "datetime": "DateTime", "email": "String(255)", "image": "String(500)"}
PY_TYPES = {"integer": "int", "string": "str", "text": "str", "boolean": "bool", "float": "float",
            "date": "date", "datetime": "datetime", "email": "str", "image": "str"}
INPUT_TYPES = {"integer": "number", "float": "number", "date": "date", "datetime": "datetime-local",
               "email": "email", "text": "textarea", "boolean": "checkbox", "string": "text", "image": "url"}
# (source relative to templates/app, destination relative to the output directory)
STAGE1_FILES = {
    "models.py.j2": "app/models.py",
    "schemas.py.j2": "app/schemas.py",
    "meta.py.j2": "app/meta.py",
    "api.py.j2": "app/api.py",
    "templates/base.html.j2": "app/templates/base.html",
    "static/style.css.j2": "app/static/style.css",
    "requirements.txt.j2": "requirements.txt",
    "README.md.j2": "README.md",
}
COPIED_FILES = [
    "__init__.py", "database.py", "engine.py", "main.py", "web.py",
    "templates/list.html", "templates/form.html", "templates/detail.html", "templates/error.html",
    "templates/index.html", "templates/search.html", "templates/_cards.html", "static/app.js", "seed.py",
]


class GenerationError(ValueError):
    pass


@dataclass
class GenerationResult:
    output_dir: Path
    files: list = field(default_factory=list)
    report: dict = field(default_factory=dict)
    seconds: float = 0.0
    model: dict = field(default_factory=dict)


# ------------------------------------------------------------------ helpers ---
def _py_default(attr):
    raw, t = attr["default"], attr["type"]
    if raw is None:
        return None
    if t == "boolean":
        return "True" if raw.lower() == "true" else "False"
    if t == "integer":
        return repr(int(raw))
    if t == "float":
        return repr(float(raw))
    if t == "date":
        return f"date.fromisoformat({raw!r})"
    if t == "datetime":
        return f"datetime.fromisoformat({raw!r})"
    return repr(raw)


def _annotation(attr, required_text=False):
    if attr.get("choices"):
        return "Literal[" + ", ".join(repr(c) for c in attr["choices"]) + "]"
    t = attr["type"]
    if t == "string":
        return f"Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]"
    if t == "text":
        return "Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)]"
    if t == "email":
        return "Annotated[str, StringConstraints(strip_whitespace=True, max_length=255, pattern=EMAIL_PATTERN)]"
    if t == "image":
        return "Annotated[str, StringConstraints(strip_whitespace=True, max_length=500, pattern=r'^(https?://|/)\\S+$')]"
    return PY_TYPES[t]


def _hue(name):
    return int(hashlib.sha256(name.encode()).hexdigest()[:6], 16) % 360


ICONS = {   # 24x24 stroke icons (Lucide-style paths)
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-7 8-7s8 3 8 7"/>',
    "tag": '<path d="M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9z"/><circle cx="8" cy="8" r="1.5"/>',
    "bag": '<path d="M5 8h14l-1 12H6z"/><path d="M9 8a3 3 0 0 1 6 0"/>',
    "book": '<path d="M4 4h10a4 4 0 0 1 4 4v12H8a4 4 0 0 1-4-4z"/><path d="M8 4v12"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "building": '<rect x="5" y="3" width="14" height="18" rx="1"/><path d="M9 7h2M13 7h2M9 11h2M13 11h2M10 21v-4h4v4"/>',
    "box": '<path d="M3 7l9-4 9 4-9 4z"/><path d="M3 7v10l9 4 9-4V7M12 11v10"/>',
    "truck": '<path d="M2 6h12v10H2zM14 10h4l3 3v3h-7"/><circle cx="6" cy="18" r="2"/><circle cx="17" cy="18" r="2"/>',
    "grid": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    "heart": '<path d="M12 21s-8-5-8-11a4.5 4.5 0 0 1 8-2.5A4.5 4.5 0 0 1 20 10c0 6-8 11-8 11z"/>',
}
ICON_KEYWORDS = [
    (("patient", "user", "customer", "student", "guest", "member", "doctor", "teacher", "client", "employee", "person", "author"), "user"),
    (("order", "purchase", "cart", "basket", "sale", "invoice"), "bag"),
    (("product", "item", "goods", "article", "sku"), "tag"),
    (("book", "course", "class", "lesson", "library", "subject"), "book"),
    (("appointment", "reservation", "booking", "event", "schedule", "visit"), "calendar"),
    (("department", "warehouse", "room", "hotel", "branch", "building", "school"), "building"),
    (("supplier", "shipment", "delivery", "vendor"), "truck"),
]
MONEY_WORDS = ("price", "cost", "fee", "rate", "amount", "total", "salary", "budget", "balance", "charge")


def _icon_for(name):
    low = name.lower()
    for words, icon in ICON_KEYWORDS:
        if any(w in low for w in words):
            return icon
    return "box"


def ui_hints(entity, attrs, fields):
    """Presentation hints derived only from the model (no entity names are hard-coded)."""
    non_pk = [a for a in attrs if not a["primary_key"]]
    image = next((a["name"] for a in non_pk if a["type"] == "image"), None)
    price = next((a["name"] for a in non_pk if a["type"] in ("float", "integer")
                  and any(w in a["name"].lower() for w in MONEY_WORDS)), None)
    badges = [a["name"] for a in non_pk if a.get("choices")][:2]
    badges += [a["name"] for a in non_pk if a["type"] == "boolean"][:max(0, 3 - len(badges))]
    shown = {image, price, *badges, display_attribute(entity)}
    subtitles = [a["name"] for a in non_pk if a["name"] not in shown
                 and a["type"] in ("string", "email", "date", "datetime", "float", "integer")][:3]
    filters = [a["name"] for a in non_pk if a.get("choices")][:1] + \
              [a["name"] for a in non_pk if a["type"] == "boolean"][:1]
    sortable = [a["name"] for a in attrs if a["type"] in ("string", "email", "integer", "float", "date", "datetime")]
    icon = _icon_for(entity["name"])
    return {"image": image, "price": price, "currency": "$", "badges": badges, "subtitles": subtitles,
            "filters": filters, "sortable": sortable, "icon": icon, "icon_svg": ICONS[icon]}


def display_attribute(entity):
    attrs = [a for a in entity["attributes"] if not a["primary_key"]]
    for a in attrs:
        if a["type"] in ("string", "email") and a["required"]:
            return a["name"]
    for a in attrs:
        if a["type"] in ("string", "email", "text"):
            return a["name"]
    if attrs:
        return attrs[0]["name"]
    return next(a["name"] for a in entity["attributes"] if a["primary_key"])


def build_context(model):
    """Turn a validated, relationship-derived IR into template context."""
    entities_by_name = {e["name"]: e for e in model["entities"]}
    rels = model["relationships"]
    out_entities = []
    for entity in model["entities"]:
        name = entity["name"]
        label = entity.get("label") or humanize(name)
        plural = entity.get("plural") or pluralize(label)
        pk = next(a["name"] for a in entity["attributes"] if a["primary_key"])
        fields, columns, inputs = [], [], []
        fk_rels = [r for r in rels if r["kind"] == "fk" and r["fk"]["holder"] == name]
        target_counts = {}
        for r in fk_rels:
            target_counts[r["fk"]["target"]] = target_counts.get(r["fk"]["target"], 0) + 1

        for attr in entity["attributes"]:
            t = attr["type"]
            is_pk = attr["primary_key"]
            ann = _annotation(attr)
            required = attr["required"] or is_pk
            fields.append({
                "name": attr["name"], "label": attr.get("label") or humanize(attr["name"]), "type": t,
                "required": required and not is_pk, "unique": attr["unique"], "default": attr["default"],
                "choices": attr.get("choices"), "pk": is_pk, "fk": None,
                "input_type": INPUT_TYPES[t],
            })
            columns.append({
                "name": attr["name"], "sa_type": SA_TYPES[t], "nullable": not required, "unique": attr["unique"],
                "py_default": _py_default(attr), "fk": None, "pk": is_pk, "read_annotation": "int" if is_pk else f"Optional[{PY_TYPES[t]}]",
            })
            if not is_pk:
                needs_value = attr["required"] and attr["default"] is None
                inputs.append({
                    "name": attr["name"],
                    "create_annotation": ann if needs_value else f"Optional[{ann}]",
                    "create_default": None if needs_value else "None",
                    "update_annotation": ann,
                })
        for r in fk_rels:
            fk = r["fk"]
            target = entities_by_name[fk["target"]]
            target_pk = next(a["name"] for a in target["attributes"] if a["primary_key"])
            lab = r.get("label") or humanize(r["name"] if target_counts[fk["target"]] > 1 else fk["target"])
            fields.append({
                "name": fk["column"], "label": lab, "type": "integer", "required": bool(r.get("required")),
                "unique": fk["unique"], "default": None, "choices": None, "pk": False,
                "fk": {"rel": r["name"], "target": fk["target"]}, "input_type": "number",
            })
            columns.append({
                "name": fk["column"], "sa_type": "Integer", "nullable": not r.get("required"), "unique": fk["unique"],
                "py_default": None, "pk": False, "read_annotation": "Optional[int]",
                "fk": {"target_table": to_snake(fk["target"]), "target_pk": target_pk},
            })
            required_fk = bool(r.get("required"))
            inputs.append({
                "name": fk["column"],
                "create_annotation": "int" if required_fk else "Optional[int]",
                "create_default": None if required_fk else "None",
                "update_annotation": "int",
            })
        allowed = model["operations"].get(name)
        ui = ui_hints(entity, entity["attributes"], fields)
        out_entities.append({"ui": ui,
            "name": name, "table": to_snake(name), "slug": to_snake(name), "label": label, "plural": plural,
            "pk": pk, "display": display_attribute(entity), "fields": fields, "columns": columns,
            "inputs": inputs, "operations": allowed if allowed is not None else ["create", "read", "update", "delete"],
            "relation_summary": ", ".join(sorted({entities_by_name[r["to"] if r["from"] == name else r["from"]]["name"]
                                                  for r in rels if name in (r["from"], r["to"])})),
        })

    for e in out_entities:
        e["relation_summary"] = ("links to " + e["relation_summary"]) if e["relation_summary"] else ""

    meta_rels, m2m = [], []
    for r in rels:
        item = {"name": r["name"], "from": r["from"], "to": r["to"], "type": r["type"], "kind": r["kind"],
                "label": r.get("label"), "required": bool(r.get("required"))}
        if r["kind"] == "m2m":
            item["assoc"] = r["assoc"]
            m2m.append({**item, "from_table": to_snake(r["from"]), "to_table": to_snake(r["to"]),
                        "from_pk": next(e["pk"] for e in out_entities if e["name"] == r["from"]),
                        "to_pk": next(e["pk"] for e in out_entities if e["name"] == r["to"])})
        else:
            item.update(holder=r["fk"]["holder"], target=r["fk"]["target"], column=r["fk"]["column"],
                        holder_side="from" if r["fk"]["holder"] == r["from"] else "to")
        meta_rels.append(item)

    rules, unresolved = [], []
    for c in model["constraints"]:
        if c.get("pattern"):
            params, errs = normalize_rule(model, c)
            if errs:
                raise GenerationError("; ".join(errs))
            rules.append({"id": c["id"], "entity": c["entity"], "pattern": c["pattern"], "params": params,
                          "description": c["description"]})
        else:
            unresolved.append({"id": c["id"], "entity": c["entity"], "description": c["description"]})

    domain_title = humanize(model["name"]) if "_" not in model["name"] and " " not in model["name"] else model["name"]
    meta = {
        "ir_version": model.get("ir_version"), "domain": model["name"], "title": domain_title,
        "entities": {e["name"]: {k: e[k] for k in ("name", "slug", "table", "label", "plural", "pk", "display",
                                                  "operations", "fields", "ui")} for e in out_entities},
        "relationships": meta_rels, "rules": rules, "unresolved_rules": unresolved,
    }
    meta_json = json.dumps(meta, indent=1, ensure_ascii=True).replace("'", "\\u0027")
    return {
        "domain_name": model["name"], "domain_title": domain_title, "entities": out_entities,
        "relationships": meta_rels, "m2m_relationships": m2m, "rules": rules, "unresolved": unresolved,
        "meta_json": meta_json, "hue": _hue(model["name"]),
        "db_name": to_snake(model["name"].replace(" ", "_").replace("-", "_")) + ".db",
    }


# --------------------------------------------------------------- generation ---
def load_model(source):
    """Accept an XML path, XML text, or an already-parsed IR dict."""
    if isinstance(source, dict):
        return json.loads(json.dumps(source))
    text = str(source)
    if text.lstrip().startswith("<"):
        return parse_domain_string(text)
    return parse_domain_model(text)


def apply_rule_resolutions(model, resolutions):
    """Attach validated pattern/params (from the SLM or a human) to text-only rules."""
    for rule in model["constraints"]:
        res = (resolutions or {}).get(rule["id"])
        if res and not rule.get("pattern"):
            rule["pattern"], rule["params"] = res["pattern"], res["params"]
            rule["resolved_by"] = res.get("resolved_by", "external")
    return model


def prepare_model(source, rule_resolutions=None):
    try:
        model = load_model(source)
    except DomainParseError as exc:
        raise GenerationError(str(exc)) from exc
    apply_rule_resolutions(model, rule_resolutions)
    report = validate(model)
    if report["errors"]:
        raise GenerationError("Invalid domain model:\n" + "\n".join(f"- {e}" for e in report["errors"]))
    derive_relationships(model)
    return model, report


def generate_project(source, output_dir, rule_resolutions=None):
    """Generate a complete application into ``output_dir`` and return a GenerationResult."""
    started = time.perf_counter()
    model, report = prepare_model(source, rule_resolutions)
    context = build_context(model)

    out = Path(output_dir).resolve()
    manifest_path = out / MANIFEST
    previous = []
    if out.exists() and any(out.iterdir()):
        if not manifest_path.exists():
            raise GenerationError(f"{out} exists and was not created by SLM-CodeGen; "
                                  f"refusing to overwrite it. Choose another output directory.")
        previous = json.loads(manifest_path.read_text()).get("files", [])
    out.mkdir(parents=True, exist_ok=True)

    env = Environment(loader=FileSystemLoader(TEMPLATE_ROOT), undefined=StrictUndefined,
                      keep_trailing_newline=True, trim_blocks=True, lstrip_blocks=True, autoescape=False)
    written = {}
    for src, dest in STAGE1_FILES.items():
        written[dest] = env.get_template(src).render(**context)
    for rel in COPIED_FILES:
        written["app/" + rel] = (TEMPLATE_ROOT / rel).read_text(encoding="utf-8")
    for i in range(8):                      # local placeholder product images (no network needed)
        written[f"app/static/img/p{i + 1}.svg"] = _placeholder_svg((context["hue"] + i * 43) % 360, i)
    written["domain_model.json"] = json.dumps(strip_derived(model), indent=2)
    written["rules_report.json"] = json.dumps({
        "implemented": context["rules"], "not_implemented": context["unresolved"],
        "warnings": report["warnings"]}, indent=2)
    written["run.sh"] = "#!/bin/sh\ncd \"$(dirname \"$0\")\"\nexec uvicorn app.main:app --port \"${PORT:-8000}\"\n"

    for dest, content in written.items():
        if dest.endswith(".py"):
            try:
                ast.parse(content, filename=dest)
            except SyntaxError as exc:
                raise GenerationError(f"Generated file {dest} has a syntax error: {exc}") from exc
    for stale in set(previous) - set(written):
        (out / stale).unlink(missing_ok=True)
    for dest, content in written.items():
        target = out / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (out / "run.sh").chmod(0o755)
    manifest_path.write_text(json.dumps({"files": sorted(written), "model_hash": model_hash(model)}, indent=2))

    elapsed = time.perf_counter() - started
    return GenerationResult(
        output_dir=out, files=sorted(written), seconds=elapsed, model=model,
        report={"implemented_rules": context["rules"], "unimplemented_rules": context["unresolved"],
                "warnings": report["warnings"], "model_hash": model_hash(model)})


def _placeholder_svg(hue, i):
    shapes = ['<circle cx="300" cy="210" r="80" fill="#fff" fill-opacity=".35"/>',
              '<rect x="220" y="130" width="160" height="160" rx="28" fill="#fff" fill-opacity=".35"/>',
              '<path d="M300 120l90 160H210z" fill="#fff" fill-opacity=".35"/>',
              '<circle cx="250" cy="200" r="60" fill="#fff" fill-opacity=".3"/><circle cx="350" cy="220" r="60" fill="#fff" fill-opacity=".3"/>']
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 400"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="hsl({hue} 70% 62%)"/><stop offset="1" stop-color="hsl({(hue + 40) % 360} 65% 42%)"/>'
            f'</linearGradient></defs><rect width="600" height="400" fill="url(#g)"/>{shapes[i % 4]}</svg>\n')


def generate_models(xml_path, output_dir=None):
    """Backwards-compatible entry point (previously generated models/schemas/api only)."""
    model = load_model(xml_path)
    target = output_dir or PROJECT_ROOT / "output" / to_snake(model.get("name") or "app")
    return generate_project(xml_path, target).output_dir / "app" / "models.py"


if __name__ == "__main__":
    import sys
    result = generate_project(sys.argv[1] if len(sys.argv) > 1 else "domain_models/library_structured.xml",
                              sys.argv[2] if len(sys.argv) > 2 else "output/library")
    print("GENERATION SUCCESSFUL:", result.output_dir, f"({len(result.files)} files, {result.seconds:.3f}s)")
