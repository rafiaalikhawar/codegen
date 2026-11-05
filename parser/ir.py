"""Helpers that derive generator-ready information from the parsed IR."""
import hashlib
import json
import keyword
import re

LABEL_ACRONYMS = {"id": "ID"}


def to_snake(name):
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s).lower()


def humanize(name):
    words = to_snake(name).split("_")
    return " ".join(LABEL_ACRONYMS.get(w, w.capitalize()) for w in words if w)


def pluralize(word):
    if word.endswith("y") and word[-2:-1] not in "aeiou":
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "ch", "sh")):
        return word + "es"
    return word + "s"


def canonical_json(model):
    return json.dumps(strip_derived(model), sort_keys=True, separators=(",", ":"))


def model_hash(model):
    return hashlib.sha256(canonical_json(model).encode()).hexdigest()


def strip_derived(model):
    """Return the model without generator-derived fields (``fk``/``assoc``)."""
    out = json.loads(json.dumps(model))
    for rel in out.get("relationships", []):
        rel.pop("fk", None)
        rel.pop("assoc", None)
        rel.pop("kind", None)
    return out


def derive_relationships(model):
    """Add ``kind``/``fk``/``assoc`` details to each relationship (in place).

    * one-to-many   : FK column lives on the ``to`` entity, pointing at ``from``
    * many-to-one   : FK column lives on the ``from`` entity, pointing at ``to``
    * one-to-one    : unique FK column on ``from`` pointing at ``to``
    * many-to-many  : association table
    """
    entities = {e["name"]: e for e in model["entities"]}
    used = {e["name"]: {a["name"] for a in e["attributes"]} for e in model["entities"]}
    for rel in model["relationships"]:
        if rel["from"] not in entities or rel["to"] not in entities or not rel.get("name"):
            continue
        rel.pop("fk", None)
        rel.pop("assoc", None)
        if rel["type"] == "many-to-many":
            rel["kind"] = "m2m"
            a, b = to_snake(rel["from"]), to_snake(rel["to"])
            if a == b:
                from_col, to_col = "source_id", "target_id"
            else:
                from_col, to_col = f"{a}_id", f"{b}_id"
            rel["assoc"] = {
                "table": f"{a}_{b}_{to_snake(rel['name'])}",
                "from_col": from_col,
                "to_col": to_col,
            }
            continue
        rel["kind"] = "fk"
        if rel["type"] == "one-to-many":
            holder, target = rel["to"], rel["from"]
        else:
            holder, target = rel["from"], rel["to"]
        column = f"{to_snake(target)}_id"
        if column in used[holder]:
            column = f"{to_snake(rel['name'])}_{to_snake(target)}_id"
        used[holder].add(column)
        rel["fk"] = {
            "holder": holder,
            "target": target,
            "column": column,
            "unique": rel["type"] == "one-to-one",
        }
    return model


def is_identifier(name):
    return bool(name) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) is not None and not keyword.iskeyword(name)
