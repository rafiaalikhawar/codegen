"""Predefined registry of supported business-rule patterns.

Only patterns listed here can ever be implemented in a generated application.
Rules are *data* (a pattern id + validated parameters); the generated app
interprets them with fixed, reviewed code.  Nothing produced by a language
model is ever executed.
"""

PATTERNS = {
    "numeric_range": {
        "summary": "A numeric attribute must lie within [min, max].",
        "params": {"attribute": "attribute:number", "min": "number?", "max": "number?"},
        "example": {"attribute": "price", "min": 0},
    },
    "date_order": {
        "summary": "One date attribute must not be later than another on the same record.",
        "params": {"earlier": "attribute:date", "later": "attribute:date"},
        "example": {"earlier": "start_date", "later": "end_date"},
    },
    "unique_together": {
        "summary": "A combination of attributes must be unique across records.",
        "params": {"attributes": "attributes"},
        "example": {"attributes": "doctor_id,slot"},
    },
    "link_requires_value": {
        "summary": ("A relationship link may only be created when an attribute of one "
                    "end has a given value; optionally set it on link / unlink."),
        "params": {"relationship": "relationship", "side": "side", "attribute": "attribute",
                   "value": "value", "set_on_link": "value?", "set_on_unlink": "value?"},
        "example": {"relationship": "borrows", "side": "to", "attribute": "available",
                    "value": "true", "set_on_link": "false", "set_on_unlink": "true"},
    },
    "max_links": {
        "summary": "A record may take part in at most N links of a relationship.",
        "params": {"relationship": "relationship", "side": "side", "max": "integer"},
        "example": {"relationship": "borrows", "side": "from", "max": 5},
    },
    "forbid_delete_if_linked": {
        "summary": "A record cannot be deleted while it has links in a relationship.",
        "params": {"relationship": "relationship"},
        "example": {"relationship": "borrows"},
    },
}


def _entity(model, name):
    for e in model["entities"]:
        if e["name"] == name:
            return e
    return None


def _attr(entity, name):
    for a in entity["attributes"]:
        if a["name"] == name:
            return a
    return None


def _all_attr_names(model, entity):
    """Attribute names plus the foreign-key columns the generator will add."""
    names = {a["name"]: a for a in entity["attributes"]}
    for rel in model.get("relationships", []):
        fk = rel.get("fk")
        if fk and fk["holder"] == entity["name"]:
            names[fk["column"]] = {"name": fk["column"], "type": "integer"}
    return names


def _coerce_value(attr, raw, label, errors):
    if isinstance(raw, bool):
        raw = "true" if raw else "false"
    text = str(raw).strip()
    t = attr["type"]
    try:
        if t == "boolean":
            if text.lower() not in ("true", "false"):
                raise ValueError
            return text.lower() == "true"
        if t == "integer":
            return int(text)
        if t == "float":
            return float(text)
    except ValueError:
        errors.append(f"{label}: '{text}' is not a valid {t} value.")
        return None
    if attr.get("choices") and text not in attr["choices"]:
        errors.append(f"{label}: '{text}' is not one of {attr['choices']}.")
    return text


def _number(raw, label, errors, integer=False):
    try:
        if isinstance(raw, bool):
            raise ValueError
        value = float(raw)
        if integer:
            if value != int(value):
                raise ValueError
            return int(value)
        return int(value) if value == int(value) else value
    except (TypeError, ValueError):
        errors.append(f"{label}: '{raw}' is not a valid {'integer' if integer else 'number'}.")
        return None


def normalize_rule(model, rule):
    """Validate ``rule['pattern']``/``rule['params']`` against the registry.

    Returns ``(clean_params, errors)``.  Never raises on bad input.
    """
    errors = []
    pattern = rule.get("pattern")
    where = f"Rule '{rule.get('id')}'"
    spec = PATTERNS.get(pattern)
    if spec is None:
        return {}, [f"{where}: unknown rule pattern '{pattern}'. Supported: {sorted(PATTERNS)}."]
    entity = _entity(model, rule.get("entity"))
    if entity is None:
        return {}, [f"{where}: unknown entity '{rule.get('entity')}'."]
    params = dict(rule.get("params") or {})
    for key in params:
        if key not in spec["params"]:
            errors.append(f"{where}: unexpected parameter '{key}' for pattern '{pattern}'.")
    clean = {}
    attrs = _all_attr_names(model, entity)
    rels = {r["name"]: r for r in model.get("relationships", [])}

    for key, kind in spec["params"].items():
        optional = kind.endswith("?")
        base = kind.rstrip("?")
        if key not in params or params[key] in (None, ""):
            if not optional:
                errors.append(f"{where}: missing parameter '{key}' for pattern '{pattern}'.")
            continue
        raw = params[key]
        label = f"{where} parameter '{key}'"
        if base.startswith("attribute") and base != "attributes":
            attr = attrs.get(raw)
            if attr is None:
                errors.append(f"{label}: '{raw}' is not an attribute of {entity['name']}.")
                continue
            need = base.partition(":")[2]
            if need == "number" and attr["type"] not in ("integer", "float"):
                errors.append(f"{label}: '{raw}' must be a numeric attribute.")
                continue
            if need == "date" and attr["type"] not in ("date", "datetime"):
                errors.append(f"{label}: '{raw}' must be a date attribute.")
                continue
            clean[key] = raw
        elif base == "attributes":
            names = raw if isinstance(raw, list) else [s.strip() for s in str(raw).split(",") if s.strip()]
            bad = [n for n in names if n not in attrs]
            if bad or len(names) < 2:
                errors.append(f"{label}: needs at least two attributes of {entity['name']}"
                              f"{' (unknown: ' + ', '.join(bad) + ')' if bad else ''}.")
                continue
            clean[key] = names
        elif base == "relationship":
            rel = rels.get(raw)
            if rel is None:
                errors.append(f"{label}: unknown relationship '{raw}'.")
            elif entity["name"] not in (rel["from"], rel["to"]):
                errors.append(f"{label}: relationship '{raw}' does not involve {entity['name']}.")
            else:
                clean[key] = raw
        elif base == "side":
            if raw not in ("from", "to"):
                errors.append(f"{label}: must be 'from' or 'to'.")
            else:
                clean[key] = raw
        elif base == "number":
            v = _number(raw, label, errors)
            if v is not None:
                clean[key] = v
        elif base == "integer":
            v = _number(raw, label, errors, integer=True)
            if v is not None and v < 1:
                errors.append(f"{label}: must be >= 1.")
            elif v is not None:
                clean[key] = v
        elif base == "value":
            clean[key] = raw  # coerced below once the attribute is known

    if pattern == "numeric_range":
        if "min" not in clean and "max" not in clean:
            errors.append(f"{where}: numeric_range needs 'min' and/or 'max'.")
        elif "min" in clean and "max" in clean and clean["min"] > clean["max"]:
            errors.append(f"{where}: min is greater than max.")
    if pattern == "date_order" and clean.get("earlier") and clean.get("earlier") == clean.get("later"):
        errors.append(f"{where}: 'earlier' and 'later' must be different attributes.")
    if pattern in ("link_requires_value", "max_links") and "relationship" in clean and "side" in clean:
        rel = rels[clean["relationship"]]
        if rel[clean["side"]] != entity["name"]:
            errors.append(f"{where}: side '{clean['side']}' of relationship '{rel['name']}' is "
                          f"{rel[clean['side']]}, not {entity['name']}.")
    if pattern == "link_requires_value" and "attribute" in clean:
        attr = attrs[clean["attribute"]]
        for key in ("value", "set_on_link", "set_on_unlink"):
            if key in clean:
                clean[key] = _coerce_value(attr, clean[key], f"{where} parameter '{key}'", errors)
    errors = list(dict.fromkeys(errors))
    return (clean if not errors else {}), errors


def registry_prompt_spec():
    """Compact text description of the registry for use in SLM prompts."""
    lines = []
    for pid, spec in PATTERNS.items():
        lines.append(f'- {pid}: {spec["summary"]} params={list(spec["params"])} example={spec["example"]}')
    return "\n".join(lines)
