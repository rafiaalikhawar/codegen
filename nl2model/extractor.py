"""Phase 2: natural-language requirements -> candidate domain model (never auto-approved)."""
import re

from parser.ir import to_snake
from parser.xml_parser import IR_VERSION
from parser.xml_writer import model_to_xml
from slm.base import SLMError, UsageTracker, extract_json
from validator.model_validator import SUPPORTED_RELATIONSHIPS, SUPPORTED_TYPES, validate

SYSTEM = "You are a software analyst. You convert website requirements into a JSON domain model. Reply with JSON only."
TYPE_SYNONYMS = {"int": "integer", "number": "integer", "str": "string", "varchar": "string", "bool": "boolean",
                 "double": "float", "decimal": "float", "money": "float", "timestamp": "datetime",
                 "long_text": "text", "description": "text"}
REL_SYNONYMS = {"one_to_many": "one-to-many", "many_to_one": "many-to-one", "one_to_one": "one-to-one",
                "many_to_many": "many-to-many", "1-n": "one-to-many", "n-1": "many-to-one", "n-n": "many-to-many",
                "1-1": "one-to-one", "m2m": "many-to-many"}

SCHEMA_HINT = """{
 "name": "DomainName",
 "entities": [{"name": "Patient", "attributes": [
     {"name": "full_name", "type": "string", "required": true, "unique": false, "default": null},
     {"name": "status", "type": "string", "choices": ["active", "discharged"], "default": "active"}]}],
 "relationships": [{"from": "Appointment", "to": "Patient", "type": "many-to-one", "name": "booked_by"}],
 "rules": [{"entity": "Appointment", "description": "A patient cannot have two appointments on the same day."}]
}"""


def build_prompt(requirements, errors=None, previous=None):
    prompt = (f"REQUIREMENTS:\n{requirements.strip()}\n\n"
              f"Supported attribute types: {sorted(SUPPORTED_TYPES)}. "
              f"Supported relationship types: {sorted(SUPPORTED_RELATIONSHIPS)}.\n"
              "Do NOT include id/primary-key attributes (added automatically). Entity names are singular PascalCase, "
              "attribute and relationship names are snake_case. A many-to-one relationship means 'from' holds a "
              "reference to 'to'. Put business rules as short English sentences in 'rules'.\n"
              f"Answer with one JSON object shaped like:\n{SCHEMA_HINT}")
    if errors:
        prompt += (f"\n\nYour previous answer was:\n{previous}\n\nIt failed validation:\n" +
                   "\n".join(f"- {e}" for e in errors) + "\nReturn the corrected full JSON object.")
    return prompt


def _name(value):
    return re.sub(r"[^A-Za-z0-9]", "", "".join(w[:1].upper() + w[1:] for w in re.split(r"[\s_\-]+", str(value or ""))))


def _snake(value):
    text = to_snake(re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip()))
    return re.sub(r"_+", "_", text).strip("_")


def _bool(value):
    return value is True or str(value).lower() == "true"


def proposal_to_ir(data):
    """Deterministically normalise the model's JSON into IR (adds primary keys; maps type synonyms)."""
    entities = []
    for e in data.get("entities") or []:
        if not isinstance(e, dict):
            continue
        attrs = [{"name": "id", "type": "integer", "primary_key": True, "required": False, "unique": False,
                  "default": None, "choices": None, "label": None}]
        for a in e.get("attributes") or []:
            if not isinstance(a, dict) or _snake(a.get("name")) in ("id", ""):
                continue
            t = str(a.get("type", "string")).lower()
            default = a.get("default")
            choices = a.get("choices")
            attrs.append({
                "name": _snake(a.get("name")), "type": TYPE_SYNONYMS.get(t, t), "primary_key": False,
                "required": _bool(a.get("required")), "unique": _bool(a.get("unique")),
                "default": None if default in (None, "") else str(default).lower() if isinstance(default, bool) else str(default),
                "choices": [str(c) for c in choices] if isinstance(choices, list) and choices else None,
                "label": None})
        entities.append({"name": _name(e.get("name")), "label": None, "plural": None, "attributes": attrs})
    rels, used = [], set()
    for r in data.get("relationships") or []:
        if not isinstance(r, dict):
            continue
        rtype = str(r.get("type", "")).lower().replace(" ", "-")
        rtype = REL_SYNONYMS.get(rtype, rtype)
        name = _snake(r.get("name")) or f"{_snake(r.get('from'))}_{_snake(r.get('to'))}"
        base, i = name, 2
        while name in used:
            name, i = f"{base}_{i}", i + 1
        used.add(name)
        rels.append({"from": _name(r.get("from")), "to": _name(r.get("to")), "type": rtype, "name": name,
                     "required": _bool(r.get("required")), "label": None})
    rules = []
    for i, c in enumerate(data.get("rules") or [], 1):
        if isinstance(c, dict) and c.get("description"):
            rules.append({"id": f"rule{i}", "entity": _name(c.get("entity")), "type": "business-rule",
                          "description": str(c["description"]).strip(), "pattern": None, "params": {}})
    return {"ir_version": IR_VERSION, "name": _name(data.get("name")) or "GeneratedApp", "entities": entities,
            "relationships": rels, "constraints": rules, "operations": {}, "issues": []}


def extract_domain_model(requirements, provider, max_repairs=1):
    """Ask the SLM for a domain model; auto-repair at most ``max_repairs`` times from validation errors.

    Returns a dict with ``ir``, ``xml``, ``errors``, ``warnings``, ``usage`` and ``repair_rounds``.
    The result is only a *proposal*: it must be approved by a human before generation.
    """
    if not requirements or not requirements.strip():
        raise ValueError("Requirements text is empty.")
    if len(requirements) > 6000:
        raise ValueError("Requirements text is too long (limit 6000 characters).")
    usage, errors, previous, ir, report = UsageTracker(), None, None, None, {"errors": [], "warnings": []}
    for attempt in range(max_repairs + 1):
        response = provider.generate(build_prompt(requirements, errors, previous), system=SYSTEM,
                                     json_mode=True, max_tokens=1500)
        usage.add(response, purpose="extract_domain_model")
        try:
            data = extract_json(response.text)
        except SLMError as exc:
            errors, previous, ir = [str(exc)], response.text[:500], None
            report = {"errors": errors, "warnings": []}
            continue
        ir = proposal_to_ir(data)
        report = validate(ir)
        if not report["errors"]:
            return _result(ir, report, usage, attempt)
        errors, previous = report["errors"], response.text[:1500]
    return _result(ir, report, usage, max_repairs)


def _result(ir, report, usage, rounds):
    xml = model_to_xml(ir) if ir else ""
    return {"ir": ir, "xml": xml, "errors": report["errors"], "warnings": report["warnings"],
            "usage": usage.as_dict(), "repair_rounds": rounds}
