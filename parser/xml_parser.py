"""Deterministic XML -> canonical intermediate representation (IR) parser.

The IR is a plain, JSON-serialisable dict (``IR_VERSION``).  The parser never
guesses: anything it does not understand is recorded in ``model["issues"]`` so
that the validator can reject it explicitly instead of silently ignoring it.
"""
import xml.etree.ElementTree as ET
from pathlib import Path

IR_VERSION = "1.0"
CONSTRAINT_RESERVED = {"id", "entity", "type", "pattern"}
ALL_OPERATIONS = ["create", "read", "update", "delete"]

ALLOWED = {
    "domain": {"name", "version"},
    "entity": {"name", "label", "plural"},
    "attribute": {"name", "type", "primaryKey", "required", "unique",
                  "default", "choices", "label"},
    "relationship": {"from", "to", "type", "name", "required", "label"},
    "operation": {"entity", "allow"},
}
ALLOWED_CHILDREN = {
    "domain": {"entities", "relationships", "constraints", "operations"},
    "entities": {"entity"},
    "entity": {"attribute"},
    "relationships": {"relationship"},
    "constraints": {"constraint"},
    "operations": {"operation"},
}


class DomainParseError(ValueError):
    """Raised when the XML cannot be parsed into a domain model at all."""


def _flag(elem, attr, issues, where):
    raw = elem.get(attr)
    if raw is None:
        return False
    if raw.strip().lower() in ("true", "false"):
        return raw.strip().lower() == "true"
    issues.append(f"{where}: attribute '{attr}' must be 'true' or 'false', got '{raw}'.")
    return False


def _check_attrs(elem, kind, where, issues):
    for key in elem.attrib:
        if key not in ALLOWED[kind]:
            issues.append(f"{where}: unsupported XML attribute '{key}'.")


def _check_children(elem, where, issues):
    allowed = ALLOWED_CHILDREN.get(elem.tag, set())
    for child in elem:
        if not isinstance(child.tag, str):
            continue
        if child.tag not in allowed:
            issues.append(f"{where}: unsupported XML element <{child.tag}>.")


def parse_domain_string(xml_text):
    if "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:
        raise DomainParseError("DOCTYPE/ENTITY declarations are not allowed in domain models.")
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError as exc:
        raise DomainParseError(f"Invalid XML: {exc}") from exc
    return _parse_root(root)


def parse_domain_model(file_path):
    path = Path(file_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DomainParseError(f"Cannot read domain model file: {exc}") from exc
    return parse_domain_string(text)


def _parse_root(root):
    if root.tag != "domain":
        raise DomainParseError("Root element must be <domain>")

    issues = []
    _check_attrs(root, "domain", "<domain>", issues)
    _check_children(root, "<domain>", issues)

    model = {
        "ir_version": IR_VERSION,
        "name": root.get("name"),
        "entities": [],
        "relationships": [],
        "constraints": [],
        "operations": {},
        "issues": issues,
    }

    for entity in root.findall("./entities/entity"):
        ename = entity.get("name")
        where = f"Entity '{ename}'"
        _check_attrs(entity, "entity", where, issues)
        _check_children(entity, where, issues)
        attributes = []
        for attr in entity.findall("attribute"):
            awhere = f"{ename}.{attr.get('name')}"
            _check_attrs(attr, "attribute", awhere, issues)
            choices = attr.get("choices")
            attributes.append({
                "name": attr.get("name"),
                "type": attr.get("type"),
                "primary_key": _flag(attr, "primaryKey", issues, awhere),
                "required": _flag(attr, "required", issues, awhere),
                "unique": _flag(attr, "unique", issues, awhere),
                "default": attr.get("default"),
                "choices": [c.strip() for c in choices.split(",")] if choices is not None else None,
                "label": attr.get("label"),
            })
        model["entities"].append({
            "name": ename,
            "label": entity.get("label"),
            "plural": entity.get("plural"),
            "attributes": attributes,
        })

    for relation in root.findall("./relationships/relationship"):
        where = f"Relationship '{relation.get('name')}'"
        _check_attrs(relation, "relationship", where, issues)
        model["relationships"].append({
            "from": relation.get("from"),
            "to": relation.get("to"),
            "type": relation.get("type"),
            "name": relation.get("name"),
            "required": _flag(relation, "required", issues, where),
            "label": relation.get("label"),
        })

    for index, constraint in enumerate(root.findall("./constraints/constraint"), 1):
        params = {k: v for k, v in constraint.attrib.items() if k not in CONSTRAINT_RESERVED}
        model["constraints"].append({
            "id": constraint.get("id") or f"rule{index}",
            "entity": constraint.get("entity"),
            "type": constraint.get("type"),
            "description": (constraint.text or "").strip(),
            "pattern": constraint.get("pattern"),
            "params": params,
        })

    for op in root.findall("./operations/operation"):
        where = f"Operation for '{op.get('entity')}'"
        _check_attrs(op, "operation", where, issues)
        allow = op.get("allow")
        allowed = [a.strip() for a in allow.split(",") if a.strip()] if allow is not None else list(ALL_OPERATIONS)
        if op.get("entity") in model["operations"]:
            issues.append(f"{where}: operations declared more than once.")
        model["operations"][op.get("entity")] = allowed

    return model


if __name__ == "__main__":
    import json
    import sys

    result = parse_domain_model(sys.argv[1] if len(sys.argv) > 1 else "domain_models/library.xml")
    print(json.dumps(result, indent=2))
