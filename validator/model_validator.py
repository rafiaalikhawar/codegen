"""Semantic validation of the parsed IR.

``validate(model)`` returns ``{"errors": [...], "warnings": [...]}``.
``validate_model(model)`` (legacy API) returns only the error list.
Unsupported features are rejected with an explicit error, never ignored.
"""
import keyword
import re
from datetime import date, datetime

from parser.ir import derive_relationships, to_snake
from validator.rule_registry import PATTERNS, normalize_rule

SUPPORTED_TYPES = {"integer", "string", "text", "boolean", "float", "date", "datetime", "email", "image"}
SUPPORTED_RELATIONSHIPS = {"one-to-one", "one-to-many", "many-to-one", "many-to-many"}
SUPPORTED_OPERATIONS = {"create", "read", "update", "delete"}
SUPPORTED_RULE_TYPE = "business-rule"
RESERVED_ATTRIBUTES = {"metadata", "registry", "query", "mro"}
RESERVED_ENTITIES = {"Base", "Session", "Table", "Column", "App", "Meta"}
ENTITY_RE = re.compile(r"[A-Z][A-Za-z0-9]*$")
ATTR_RE = re.compile(r"[a-z][A-Za-z0-9_]*$")
MAX_ENTITIES = 50
MAX_ATTRIBUTES = 60


def _default_error(attr):
    """Return an error text when the attribute default is invalid, else None."""
    raw, t = attr["default"], attr["type"]
    try:
        if t == "boolean":
            if raw.lower() not in ("true", "false"):
                raise ValueError("expected true or false")
        elif t == "integer":
            int(raw)
        elif t == "float":
            float(raw)
        elif t == "date":
            date.fromisoformat(raw)
        elif t == "datetime":
            datetime.fromisoformat(raw)
        elif t == "email":
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", raw):
                raise ValueError("not an email address")
        elif t == "image":
            if not re.fullmatch(r"(https?://|/)\S+", raw):
                raise ValueError("expected an http(s) URL or a /path")
        elif t in ("string", "text") and len(raw) > 255 and t == "string":
            raise ValueError("longer than 255 characters")
    except ValueError as exc:
        return f"default '{raw}' is not valid for type {t} ({exc})"
    if attr.get("choices") and raw not in attr["choices"]:
        return f"default '{raw}' is not one of the allowed choices {attr['choices']}"
    return None


def validate(model):
    errors, warnings = [], []
    errors.extend(model.get("issues", []))

    name = model.get("name")
    if not name:
        errors.append("Domain name is missing (<domain name=\"...\">).")
    elif not re.fullmatch(r"[A-Za-z][A-Za-z0-9_ -]*", name):
        errors.append(f"Domain name '{name}' contains unsupported characters.")

    if not model["entities"]:
        errors.append("The domain must define at least one entity.")
    if len(model["entities"]) > MAX_ENTITIES:
        errors.append(f"Too many entities (limit {MAX_ENTITIES}).")

    entity_names, tables = set(), {}
    for entity in model["entities"]:
        ename = entity["name"]
        if not ename:
            errors.append("Entity name is missing.")
            continue
        if not ENTITY_RE.match(ename) or keyword.iskeyword(ename):
            errors.append(f"Invalid entity name '{ename}': use letters/digits starting with a capital letter.")
        elif ename in RESERVED_ENTITIES:
            errors.append(f"Entity name '{ename}' is reserved by the generator.")
        if ename in entity_names:
            errors.append(f"Duplicate entity: {ename}")
        entity_names.add(ename)
        table = to_snake(ename)
        if table in tables and tables[table] != ename:
            errors.append(f"Entities '{tables[table]}' and '{ename}' map to the same table name '{table}'.")
        tables[table] = ename

        names, pks = set(), []
        if not entity["attributes"]:
            errors.append(f"Entity {ename} has no attributes.")
        if len(entity["attributes"]) > MAX_ATTRIBUTES:
            errors.append(f"Entity {ename} has too many attributes (limit {MAX_ATTRIBUTES}).")
        for attr in entity["attributes"]:
            an = attr["name"]
            if not an:
                errors.append(f"Missing attribute name in {ename}")
                continue
            where = f"{ename}.{an}"
            if not ATTR_RE.match(an) or keyword.iskeyword(an) or an in RESERVED_ATTRIBUTES:
                errors.append(f"Invalid attribute name '{where}': use lowercase letters, digits and "
                              f"underscores, starting with a letter (reserved words not allowed).")
            if an in names:
                errors.append(f"Duplicate attribute: {where}")
            names.add(an)
            if attr["type"] is None:
                errors.append(f"Missing type: {where}")
            elif attr["type"] not in SUPPORTED_TYPES:
                errors.append(f"Unsupported type '{attr['type']}': {where}. "
                              f"Supported: {sorted(SUPPORTED_TYPES)}")
            if attr["primary_key"]:
                pks.append(attr)
                if attr["type"] != "integer":
                    errors.append(f"Primary key {where} must be of type integer "
                                  f"(only auto-increment integer keys are supported).")
                if attr["default"] is not None or attr["unique"] or attr.get("choices"):
                    errors.append(f"Primary key {where} cannot declare default/unique/choices.")
            if attr["default"] is not None and attr["type"] in SUPPORTED_TYPES and not attr["primary_key"]:
                problem = _default_error(attr)
                if problem:
                    errors.append(f"Invalid default for {where}: {problem}.")
            if attr.get("choices") is not None:
                if attr["type"] not in ("string", "text"):
                    errors.append(f"{where}: 'choices' is only supported on string attributes.")
                elif not attr["choices"] or any(not c for c in attr["choices"]) \
                        or len(set(attr["choices"])) != len(attr["choices"]):
                    errors.append(f"{where}: 'choices' must be a list of distinct non-empty values.")
        if len(pks) != 1:
            errors.append(f"{ename} must have exactly one primary key (found {len(pks)}).")

    rel_names = set()
    for rel in model["relationships"]:
        rn = rel["name"]
        if not rn:
            errors.append("Every relationship needs a name.")
        else:
            if not ATTR_RE.match(rn):
                errors.append(f"Invalid relationship name '{rn}': lowercase letters, digits, underscores.")
            if rn in rel_names:
                errors.append(f"Duplicate relationship name: {rn}")
            rel_names.add(rn)
        if rel["from"] not in entity_names:
            errors.append(f"Unknown source entity '{rel['from']}' in relationship '{rn}'.")
        if rel["to"] not in entity_names:
            errors.append(f"Unknown target entity '{rel['to']}' in relationship '{rn}'.")
        if rel["type"] not in SUPPORTED_RELATIONSHIPS:
            errors.append(f"Unsupported relationship type '{rel['type']}' in '{rn}'. "
                          f"Supported: {sorted(SUPPORTED_RELATIONSHIPS)}")
        if rel.get("required") and rel["type"] == "many-to-many":
            errors.append(f"Relationship '{rn}': 'required' is not supported on many-to-many.")

    structure_ok = not errors
    if structure_ok:
        derive_relationships(model)

    seen_ids = set()
    for rule in model["constraints"]:
        rid = rule["id"]
        if rid in seen_ids:
            errors.append(f"Duplicate rule id: {rid}")
        seen_ids.add(rid)
        if rule["type"] != SUPPORTED_RULE_TYPE:
            errors.append(f"Rule '{rid}': unsupported constraint type '{rule['type']}' "
                          f"(only '{SUPPORTED_RULE_TYPE}' is supported).")
            continue
        if rule["entity"] not in entity_names:
            errors.append(f"Unknown constraint entity: {rule['entity']} (rule '{rid}')")
            continue
        if rule.get("pattern"):
            if not structure_ok:
                continue  # parameter checking needs derived relationship info
            _, problems = normalize_rule(model, rule)
            errors.extend(problems)
        else:
            if not rule["description"]:
                errors.append(f"Rule '{rid}' has neither a pattern nor a description.")
            else:
                warnings.append(
                    f"Rule '{rid}' ({rule['entity']}) is described in text only and is NOT implemented "
                    f"until it is mapped to one of the supported patterns {sorted(PATTERNS)}: "
                    f"\"{rule['description']}\"")

    for ename, ops in model["operations"].items():
        if ename not in entity_names:
            errors.append(f"Operations declared for unknown entity '{ename}'.")
        for op in ops:
            if op not in SUPPORTED_OPERATIONS:
                errors.append(f"Unsupported operation '{op}' for {ename}. Supported: {sorted(SUPPORTED_OPERATIONS)}")

    return {"errors": errors, "warnings": warnings}


def validate_model(model):
    return validate(model)["errors"]


if __name__ == "__main__":
    import sys
    from parser.xml_parser import parse_domain_model

    parsed = parse_domain_model(sys.argv[1] if len(sys.argv) > 1 else "domain_models/library.xml")
    report = validate(parsed)
    if report["errors"]:
        print("VALIDATION FAILED")
        for error in report["errors"]:
            print("-", error)
    else:
        print("VALIDATION PASSED")
        print("Domain:", parsed["name"])
        print("Entities:", len(parsed["entities"]))
        print("Relationships:", len(parsed["relationships"]))
        print("Constraints:", len(parsed["constraints"]))
    for warning in report["warnings"]:
        print("WARNING:", warning)
