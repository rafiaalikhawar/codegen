"""Business logic shared by the REST API and the HTML interface.

Everything here is driven by ``META`` (the domain model) - there are no
entity-specific code paths.  Business rules are interpreted from a fixed set of
patterns; the rule data never contains executable code.
"""
import re
from datetime import date, datetime

from sqlalchemy import and_, delete as sa_delete, func, insert, or_, select
from sqlalchemy.exc import IntegrityError

from .meta import META
from .models import ASSOCIATION_TABLES, MODELS

ENTITIES = META["entities"]
RELATIONSHIPS = {r["name"]: r for r in META["relationships"]}
RULES = META["rules"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
STRING_LIMIT = 255
TEXT_LIMIT = 20000


class AppError(Exception):
    def __init__(self, status, message, errors=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.errors = errors or {}


# ---------------------------------------------------------------- metadata ---
def entity_by_slug(slug):
    for name, e in ENTITIES.items():
        if e["slug"] == slug:
            return name
    raise AppError(404, f"Unknown resource '{slug}'")


def fields_of(entity):
    return {f["name"]: f for f in ENTITIES[entity]["fields"]}


def require_op(entity, op):
    if op not in ENTITIES[entity]["operations"]:
        raise AppError(405, f"Operation '{op}' is not supported for {ENTITIES[entity]['label']}.")


def relations_of(entity):
    """[(relationship, direction)] where direction is the role of ``entity`` ('from'/'to')."""
    out = []
    for rel in META["relationships"]:
        if rel["from"] == entity:
            out.append((rel, "from"))
        if rel["to"] == entity:
            out.append((rel, "to"))
    return out


def rel_label(rel, direction):
    if rel["kind"] == "m2m" or rel.get("label"):
        base = rel.get("label") or rel["name"].replace("_", " ")
    else:
        base = rel["name"].replace("_", " ")
    return base if direction == "from" else f"{base} (reverse)"


# ----------------------------------------------------------------- coercion ---
def coerce(field, raw):
    """Convert a raw (form string / JSON value) to the field's Python type."""
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.strip()
        if raw == "":
            return None
    t = field["type"]
    if t == "boolean":
        if isinstance(raw, bool):
            return raw
        text = str(raw).lower()
        if text in ("true", "on", "1", "yes"):
            return True
        if text in ("false", "off", "0", "no"):
            return False
        raise ValueError("must be true or false")
    if t == "integer":
        if isinstance(raw, bool) or (isinstance(raw, float) and raw != int(raw)):
            raise ValueError("must be a whole number")
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ValueError("must be a whole number")
    if t == "float":
        if isinstance(raw, bool):
            raise ValueError("must be a number")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ValueError("must be a number")
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("must be a finite number")
        return value
    if t == "date":
        if isinstance(raw, datetime):
            return raw.date()
        if isinstance(raw, date):
            return raw
        try:
            return date.fromisoformat(str(raw))
        except ValueError:
            raise ValueError("must be a date (YYYY-MM-DD)")
    if t == "datetime":
        if isinstance(raw, datetime):
            return raw
        try:
            return datetime.fromisoformat(str(raw))
        except ValueError:
            raise ValueError("must be a date and time (YYYY-MM-DDTHH:MM)")
    text = str(raw)
    if t == "email" and not EMAIL_RE.match(text):
        raise ValueError("must be a valid email address")
    if t == "image" and not re.match(r"^(https?://|/)\S+$", text):
        raise ValueError("must be an image URL (http://, https:// or /path)")
    limit = TEXT_LIMIT if t == "text" else STRING_LIMIT
    if len(text) > limit:
        raise ValueError(f"must be at most {limit} characters")
    if field.get("choices") and text not in field["choices"]:
        raise ValueError("must be one of: " + ", ".join(field["choices"]))
    return text


# --------------------------------------------------------------- validation ---
def clean(session, entity, data, partial=False, current=None):
    """Validate ``data`` and return the column values to write."""
    fields = fields_of(entity)
    errors, values = {}, {}
    model = MODELS[entity]
    for key in data:
        if key not in fields:
            errors[key] = "Unknown field"
        elif fields[key]["pk"]:
            errors[key] = "The identifier is assigned automatically"
    for name, f in fields.items():
        if f["pk"] or name in errors:
            continue
        present = name in data
        if not present and partial:
            continue
        try:
            value = coerce(f, data.get(name)) if present else None
        except ValueError as exc:
            errors[name] = f"{f['label']} {exc}"
            continue
        if value is None and not partial and f["default"] is not None:
            value = coerce(f, f["default"])
        if value is None and f["required"]:
            errors[name] = f"{f['label']} is required"
            continue
        if value is not None and f["fk"]:
            target = MODELS[f["fk"]["target"]]
            if session.get(target, value) is None:
                errors[name] = f"{f['label']}: no {ENTITIES[f['fk']['target']]['label']} with id {value}"
                continue
        if value is not None and f["unique"]:
            query = select(func.count()).select_from(model).where(getattr(model, name) == value)
            if current is not None:
                query = query.where(getattr(model, ENTITIES[entity]["pk"]) != getattr(current, ENTITIES[entity]["pk"]))
            if session.scalar(query):
                errors[name] = f"{f['label']} '{value}' already exists"
                continue
        values[name] = value
    if errors:
        raise AppError(422, "Validation failed", errors)
    return values


def _merged(entity, values, current):
    merged = {}
    if current is not None:
        for f in ENTITIES[entity]["fields"]:
            merged[f["name"]] = getattr(current, f["name"])
    merged.update(values)
    return merged


def _rule_error(rule, status=422):
    text = rule["description"] or rule["pattern"]
    return AppError(status, f"Business rule violated: {text}", {"_rule": rule["id"]})


def check_record_rules(session, entity, values, current):
    """Rules that depend only on the values of one record."""
    merged = _merged(entity, values, current)
    model = MODELS[entity]
    pk = ENTITIES[entity]["pk"]
    for rule in RULES:
        if rule["entity"] != entity:
            continue
        p, pattern = rule["params"], rule["pattern"]
        if pattern == "numeric_range":
            v = merged.get(p["attribute"])
            if v is None:
                continue
            if ("min" in p and v < p["min"]) or ("max" in p and v > p["max"]):
                err = _rule_error(rule)
                err.errors[p["attribute"]] = f"must be between {p.get('min', '-inf')} and {p.get('max', 'inf')}"
                raise err
        elif pattern == "date_order":
            a, b = merged.get(p["earlier"]), merged.get(p["later"])
            if a is not None and b is not None and a > b:
                err = _rule_error(rule)
                err.errors[p["later"]] = f"must not be before {p['earlier']}"
                raise err
        elif pattern == "unique_together":
            if any(merged.get(n) is None for n in p["attributes"]):
                continue
            query = select(func.count()).select_from(model)
            for n in p["attributes"]:
                query = query.where(getattr(model, n) == merged[n])
            if current is not None:
                query = query.where(getattr(model, pk) != getattr(current, pk))
            if session.scalar(query):
                err = _rule_error(rule, 409)
                err.errors[p["attributes"][0]] = "this combination already exists"
                raise err


# -------------------------------------------------------------- relationships ---
def _pair(rel, holder_obj, target_obj):
    """Return (from_obj, to_obj) for a foreign-key relationship."""
    return (holder_obj, target_obj) if rel["holder"] == rel["from"] else (target_obj, holder_obj)


def _count_links(session, rel, side, obj_id):
    """Number of links the record ``obj_id`` has in ``rel`` as its ``side`` end."""
    if obj_id is None:
        return 0
    if rel["kind"] == "m2m":
        table = ASSOCIATION_TABLES[rel["name"]]
        col = table.c[rel["assoc"]["from_col"] if side == "from" else rel["assoc"]["to_col"]]
        return session.scalar(select(func.count()).select_from(table).where(col == obj_id))
    holder_model = MODELS[rel["holder"]]
    if side == rel["holder_side"]:
        row = session.get(holder_model, obj_id)
        return 0 if row is None or getattr(row, rel["column"]) is None else 1
    return session.scalar(select(func.count()).select_from(holder_model)
                          .where(getattr(holder_model, rel["column"]) == obj_id))


def check_link(session, rel, from_obj, to_obj):
    """Raise AppError when a link rule forbids linking from_obj -> to_obj."""
    objs = {"from": from_obj, "to": to_obj}
    for rule in RULES:
        p = rule["params"]
        if p.get("relationship") != rel["name"]:
            continue
        if rule["pattern"] == "link_requires_value":
            if getattr(objs[p["side"]], p["attribute"]) != p["value"]:
                raise _rule_error(rule, 409)
        elif rule["pattern"] == "max_links":
            obj = objs[p["side"]]
            pk = ENTITIES[rel[p["side"]]]["pk"]
            if _count_links(session, rel, p["side"], getattr(obj, pk)) >= p["max"]:
                raise _rule_error(rule, 409)


def apply_link_effects(rel, from_obj, to_obj, linking):
    objs = {"from": from_obj, "to": to_obj}
    key = "set_on_link" if linking else "set_on_unlink"
    for rule in RULES:
        p = rule["params"]
        if rule["pattern"] == "link_requires_value" and p["relationship"] == rel["name"] and key in p:
            setattr(objs[p["side"]], p["attribute"], p[key])


def _endpoint_models(rel):
    return MODELS[rel["from"]], MODELS[rel["to"]]


def _get_or_404(session, entity, item_id):
    obj = session.get(MODELS[entity], item_id)
    if obj is None:
        raise AppError(404, f"{ENTITIES[entity]['label']} {item_id} not found")
    return obj


def _resolve_link(entity, relationship):
    rel = RELATIONSHIPS.get(relationship)
    if rel is None or entity not in (rel["from"], rel["to"]):
        raise AppError(404, f"Unknown relationship '{relationship}' for {ENTITIES[entity]['label']}")
    if rel["kind"] != "m2m":
        raise AppError(405, f"Relationship '{relationship}' is managed through the "
                            f"'{rel['column']}' field of {rel['holder']}.")
    return rel, ("from" if rel["from"] == entity else "to")


def link(session, entity, item_id, relationship, target_id):
    rel, side = _resolve_link(entity, relationship)
    other = rel["to"] if side == "from" else rel["from"]
    me = _get_or_404(session, entity, item_id)
    them = _get_or_404(session, other, target_id)
    from_obj, to_obj = (me, them) if side == "from" else (them, me)
    table = ASSOCIATION_TABLES[rel["name"]]
    from_id = getattr(from_obj, ENTITIES[rel["from"]]["pk"])
    to_id = getattr(to_obj, ENTITIES[rel["to"]]["pk"])
    exists = session.scalar(select(func.count()).select_from(table).where(and_(
        table.c[rel["assoc"]["from_col"]] == from_id, table.c[rel["assoc"]["to_col"]] == to_id)))
    if exists:
        raise AppError(409, "These records are already linked")
    check_link(session, rel, from_obj, to_obj)
    session.execute(insert(table).values({rel["assoc"]["from_col"]: from_id, rel["assoc"]["to_col"]: to_id}))
    apply_link_effects(rel, from_obj, to_obj, True)
    session.commit()


def unlink(session, entity, item_id, relationship, target_id):
    rel, side = _resolve_link(entity, relationship)
    other = rel["to"] if side == "from" else rel["from"]
    me = _get_or_404(session, entity, item_id)
    them = _get_or_404(session, other, target_id)
    from_obj, to_obj = (me, them) if side == "from" else (them, me)
    table = ASSOCIATION_TABLES[rel["name"]]
    from_id = getattr(from_obj, ENTITIES[rel["from"]]["pk"])
    to_id = getattr(to_obj, ENTITIES[rel["to"]]["pk"])
    result = session.execute(sa_delete(table).where(and_(
        table.c[rel["assoc"]["from_col"]] == from_id, table.c[rel["assoc"]["to_col"]] == to_id)))
    if not result.rowcount:
        raise AppError(404, "These records are not linked")
    apply_link_effects(rel, from_obj, to_obj, False)
    session.commit()


def _linked_objs(session, rel, side, item_id):
    other = rel["to"] if side == "from" else rel["from"]
    table = ASSOCIATION_TABLES[rel["name"]]
    mine, theirs = (("from_col", "to_col") if side == "from" else ("to_col", "from_col"))
    model = MODELS[other]
    pk = ENTITIES[other]["pk"]
    rows = session.scalars(
        select(model).join(table, table.c[rel["assoc"][theirs]] == getattr(model, pk))
        .where(table.c[rel["assoc"][mine]] == item_id).order_by(getattr(model, pk))).all()
    return [(other, r) for r in rows]


def linked(session, entity, item_id, relationship):
    """Return [(entity_name, obj)] for records linked to the given one."""
    rel, side = _resolve_link(entity, relationship)
    _get_or_404(session, entity, item_id)
    return _linked_objs(session, rel, side, item_id)


def referencing(session, entity, item_id):
    """Records whose foreign key points at the given record: [(rel, holder_entity, [objs])]."""
    out = []
    for rel in META["relationships"]:
        if rel["kind"] == "fk" and rel["target"] == entity:
            model = MODELS[rel["holder"]]
            rows = session.scalars(select(model).where(getattr(model, rel["column"]) == item_id)
                                   .order_by(getattr(model, ENTITIES[rel["holder"]]["pk"]))).all()
            out.append((rel, rel["holder"], rows))
    return out


# --------------------------------------------------------------------- CRUD ---
def _commit(session):
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise AppError(409, "The change conflicts with existing data (constraint violation)") from exc


def _fk_changes(entity, old, new):
    """Yield (rel, old_target_id, new_target_id) for changed foreign keys of ``entity``."""
    for rel in META["relationships"]:
        if rel["kind"] == "fk" and rel["holder"] == entity:
            col = rel["column"]
            o, n = (old or {}).get(col), new.get(col)
            if col in new and o != n:
                yield rel, o, n


def create(session, entity, data):
    require_op(entity, "create")
    values = clean(session, entity, data)
    check_record_rules(session, entity, values, None)
    obj = MODELS[entity](**values)
    for rel, _, new in _fk_changes(entity, None, values):
        if new is not None:
            target = session.get(MODELS[rel["target"]], new)
            from_obj, to_obj = _pair(rel, obj, target)
            check_link(session, rel, from_obj, to_obj)
    session.add(obj)
    for rel, _, new in _fk_changes(entity, None, values):
        if new is not None:
            target = session.get(MODELS[rel["target"]], new)
            apply_link_effects(rel, *_pair(rel, obj, target), True)
    _commit(session)
    session.refresh(obj)
    return obj


def get(session, entity, item_id):
    require_op(entity, "read")
    return _get_or_404(session, entity, item_id)


def update(session, entity, item_id, data):
    require_op(entity, "update")
    obj = _get_or_404(session, entity, item_id)
    values = clean(session, entity, data, partial=True, current=obj)
    check_record_rules(session, entity, values, obj)
    old = {rel["column"]: getattr(obj, rel["column"]) for rel in META["relationships"]
           if rel["kind"] == "fk" and rel["holder"] == entity}
    changes = list(_fk_changes(entity, old, values))
    for rel, _, new in changes:
        if new is not None:
            target = session.get(MODELS[rel["target"]], new)
            check_link(session, rel, *_pair(rel, obj, target))
    for key, value in values.items():
        setattr(obj, key, value)
    for rel, o, n in changes:
        if o is not None:
            apply_link_effects(rel, *_pair(rel, obj, session.get(MODELS[rel["target"]], o)), False)
        if n is not None:
            apply_link_effects(rel, *_pair(rel, obj, session.get(MODELS[rel["target"]], n)), True)
    _commit(session)
    session.refresh(obj)
    return obj


def delete(session, entity, item_id):
    require_op(entity, "delete")
    obj = _get_or_404(session, entity, item_id)
    for rule in RULES:
        if rule["pattern"] == "forbid_delete_if_linked" and rule["entity"] == entity:
            rel = RELATIONSHIPS[rule["params"]["relationship"]]
            if any(rel[side] == entity and _count_links(session, rel, side, item_id) for side in ("from", "to")):
                raise _rule_error(rule, 409)
    for rel, holder, rows in referencing(session, entity, item_id):
        if rows:
            raise AppError(409, f"Cannot delete: still referenced by {len(rows)} "
                                f"{ENTITIES[holder]['plural']} ({rel['name'].replace('_', ' ')}).")
    for rel in META["relationships"]:
        if rel["kind"] == "fk" and rel["holder"] == entity:
            target_id = getattr(obj, rel["column"])
            if target_id is not None:
                apply_link_effects(rel, *_pair(rel, obj, session.get(MODELS[rel["target"]], target_id)), False)
        elif rel["kind"] == "m2m" and entity in (rel["from"], rel["to"]):
            table = ASSOCIATION_TABLES[rel["name"]]
            conditions = []
            for side in ("from", "to"):
                if rel[side] != entity:
                    continue
                for _, other in _linked_objs(session, rel, side, item_id):
                    apply_link_effects(rel, *((obj, other) if side == "from" else (other, obj)), False)
                conditions.append(table.c[rel["assoc"]["from_col" if side == "from" else "to_col"]] == item_id)
            session.execute(sa_delete(table).where(or_(*conditions)))
    session.delete(obj)
    _commit(session)


def list_records(session, entity, q=None, limit=100, offset=0, filters=None, sort=None, desc=False):
    require_op(entity, "read")
    model = MODELS[entity]
    fields = fields_of(entity)
    query = select(model)
    count_query = select(func.count()).select_from(model)
    for name, raw in (filters or {}).items():
        f = fields.get(name)
        if f is None or raw in (None, ""):
            continue
        try:
            value = coerce(f, raw)
        except ValueError:
            continue
        query = query.where(getattr(model, name) == value)
        count_query = count_query.where(getattr(model, name) == value)
    if q:
        needle = "%" + q.replace("%", r"\%").replace("_", r"\_") + "%"
        cols = [getattr(model, f["name"]) for f in ENTITIES[entity]["fields"]
                if f["type"] in ("string", "text", "email")]
        if cols:
            cond = or_(*[c.ilike(needle, escape="\\") for c in cols])
            query, count_query = query.where(cond), count_query.where(cond)
    total = session.scalar(count_query)
    order_col = getattr(model, sort) if sort in fields else getattr(model, ENTITIES[entity]["pk"])
    rows = session.scalars(query.order_by(order_col.desc() if desc else order_col)
                           .limit(limit).offset(offset)).all()
    return rows, total


def latest(session, entity, n=4):
    model = MODELS[entity]
    pk = getattr(model, ENTITIES[entity]["pk"])
    return session.scalars(select(model).order_by(pk.desc()).limit(n)).all()


def search_all(session, q, per_entity=6):
    """Search every readable entity: [(entity_name, [card, ...], total)]."""
    out = []
    for name, meta in ENTITIES.items():
        if "read" not in meta["operations"]:
            continue
        rows, total = list_records(session, name, q=q, limit=per_entity)
        if rows:
            out.append((name, [card_view(session, name, r) for r in rows], total))
    return out


def counts(session):
    return {name: session.scalar(select(func.count()).select_from(MODELS[name])) for name in ENTITIES}


# -------------------------------------------------------------- presentation ---
def label_of(entity, obj):
    meta = ENTITIES[entity]
    pk = getattr(obj, meta["pk"])
    if meta["display"] == meta["pk"]:
        return f"{meta['label']} #{pk}"
    return f"{getattr(obj, meta['display'])} (#{pk})"


def options(session, entity):
    model = MODELS[entity]
    rows = session.scalars(select(model).order_by(getattr(model, ENTITIES[entity]["pk"]))).all()
    return [(getattr(r, ENTITIES[entity]["pk"]), label_of(entity, r)) for r in rows]


def to_dict(entity, obj):
    out = {}
    for f in ENTITIES[entity]["fields"]:
        v = getattr(obj, f["name"])
        out[f["name"]] = v.isoformat() if isinstance(v, (date, datetime)) else v
    return out


def row_cells(session, entity, obj):
    """Cells for the listing / detail pages, with foreign keys resolved to labels."""
    cells = []
    for f in ENTITIES[entity]["fields"]:
        value = getattr(obj, f["name"])
        cell = {"field": f, "value": value, "link": None, "text": None}
        if f["fk"] and value is not None:
            target = session.get(MODELS[f["fk"]["target"]], value)
            if target is not None:
                cell["text"] = label_of(f["fk"]["target"], target)
                cell["link"] = f"/web/{ENTITIES[f['fk']['target']]['slug']}/{value}"
        elif f["type"] == "boolean" and value is not None:
            cell["text"] = "Yes" if value else "No"
        elif isinstance(value, (date, datetime)):
            cell["text"] = value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
        elif value is not None:
            cell["text"] = str(value)
        cells.append(cell)
    return cells


def card_view(session, entity, obj):
    """Presentation data for one record (cards on list/home/search pages)."""
    meta = ENTITIES[entity]
    ui = meta["ui"]
    fields = fields_of(entity)
    pk = getattr(obj, meta["pk"])
    title = getattr(obj, meta["display"])
    title = str(title) if title is not None and meta["display"] != meta["pk"] else f"{meta['label']} #{pk}"
    image = getattr(obj, ui["image"]) if ui["image"] else None
    price = None
    if ui["price"] and getattr(obj, ui["price"]) is not None:
        price = f"{ui['currency']}{getattr(obj, ui['price']):,.2f}"
    badges = []
    for name in ui["badges"]:
        value, f = getattr(obj, name), fields[name]
        if value is None:
            continue
        if f["type"] == "boolean":
            badges.append({"text": f["label"] if value else "Not " + f["label"].lower(), "kind": "good" if value else "muted"})
        else:
            badges.append({"text": str(value), "kind": "tone%d" % (sum(map(ord, str(value))) % 5)})
    subtitles = []
    for name in ui["subtitles"]:
        value = getattr(obj, name)
        if value not in (None, ""):
            text = value.isoformat() if isinstance(value, (date, datetime)) else str(value)
            subtitles.append({"label": fields[name]["label"], "text": text if len(text) < 90 else text[:87] + "…"})
    for f in meta["fields"]:
        if f["fk"] and getattr(obj, f["name"]) is not None and len(subtitles) < 3:
            target = session.get(MODELS[f["fk"]["target"]], getattr(obj, f["name"]))
            if target is not None:
                subtitles.append({"label": f["label"], "text": label_of(f["fk"]["target"], target)})
    initials = "".join(w[0] for w in title.replace("#", " ").split()[:2]).upper() or meta["label"][:1]
    return {"id": pk, "title": title, "image": image, "price": price, "badges": badges, "subtitles": subtitles,
            "initials": initials, "hue": (pk * 47 + sum(map(ord, meta["name"]))) % 360,
            "url": f"/web/{meta['slug']}/{pk}"}
