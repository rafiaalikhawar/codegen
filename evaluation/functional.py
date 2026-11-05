"""Specification-driven functional test suite.

The checks are derived from the *domain model* (IR), not from the generated code,
so the same suite can evaluate any generated application (engine output or a
baseline's output) through the REST contract ``/api/<snake_case_entity>``.

``client`` is anything with ``request(method, url, json=None)`` returning an
object with ``status_code`` and ``json()`` - a Starlette TestClient or httpx.Client.
"""
from dataclasses import dataclass, asdict
from datetime import date, timedelta

from parser.ir import derive_relationships, to_snake
from validator.rule_registry import normalize_rule

CATEGORIES = ("crud", "validation", "relationship", "rule", "ui")


@dataclass
class Check:
    category: str
    name: str
    passed: bool
    detail: str = ""
    requirement: str = ""   # requirement item this check contributes to


def _all_ops():
    return ["create", "read", "update", "delete"]


class Harness:
    def __init__(self, client, model, api="/api"):
        self.c = client
        self.api = api
        derive_relationships(model)
        self.model = model
        self.entities = {e["name"]: e for e in model["entities"]}
        self.rels = {r["name"]: r for r in model["relationships"]}
        self.rules = []
        for r in model["constraints"]:
            if r.get("pattern"):
                params, errors = normalize_rule(model, r)
                if not errors:
                    self.rules.append({**r, "params": params})
        self.checks = []
        self._cache = {}
        self._tag = 1000

    # ------------------------------------------------------------ plumbing ---
    def req(self, method, path, body=None):
        try:
            r = self.c.request(method, path, json=body) if body is not None else self.c.request(method, path)
        except Exception as exc:  # network failure / crash counts as a failed check
            return _Resp(-1, {"error": f"{type(exc).__name__}: {exc}"})
        try:
            data = r.json()
        except Exception:
            data = None
        return _Resp(r.status_code, data)

    def url(self, entity, *parts):
        return "/".join([self.api, to_snake(entity), *[str(p) for p in parts]])

    def ops(self, entity):
        return self.model["operations"].get(entity, _all_ops())

    def add(self, category, name, passed, detail="", requirement=""):
        self.checks.append(Check(category, name, bool(passed), "" if passed else str(detail)[:300], requirement))

    def fresh(self):
        self._tag += 1
        return self._tag

    # ------------------------------------------------------------- payloads ---
    def _rule_values(self, entity):
        """Constraint-aware values for ``entity``: attr -> value."""
        values = {}
        for r in self.rules:
            p = r["params"]
            if r["pattern"] == "numeric_range" and r["entity"] == entity:
                lo, hi = p.get("min"), p.get("max")
                if lo is not None and hi is not None:
                    values[p["attribute"]] = (lo + hi) / 2
                elif lo is not None:
                    values[p["attribute"]] = lo + 1
                else:
                    values[p["attribute"]] = hi - 1
            elif r["pattern"] == "link_requires_value":
                rel = self.rels[p["relationship"]]
                if rel[p["side"]] == entity and p["value"] is not None:
                    values[p["attribute"]] = p["value"]
        return values

    def _attr_type(self, entity, name):
        for a in self.entities[entity]["attributes"]:
            if a["name"] == name:
                return a["type"]
        return "integer"

    def payload(self, entity, n, overrides=None, skip=()):
        data = {}
        rule_vals = self._rule_values(entity)
        later = {r["params"]["later"] for r in self.rules
                 if r["pattern"] == "date_order" and r["entity"] == entity}
        for a in self.entities[entity]["attributes"]:
            if a["primary_key"] or a["name"] in skip:
                continue
            name, t = a["name"], a["type"]
            if name in rule_vals:
                v = rule_vals[name]
                data[name] = int(v) if t == "integer" else float(v) if t == "float" else v
            elif a["choices"]:
                data[name] = a["choices"][n % len(a["choices"])] if a["unique"] else (a["default"] or a["choices"][0])
            elif t in ("string", "text"):
                data[name] = f"{name}-{n}"
            elif t == "email":
                data[name] = f"user{n}@example.com"
            elif t == "image":
                data[name] = f"/static/img/p{n % 8 + 1}.svg"
            elif t == "integer":
                data[name] = n
            elif t == "float":
                data[name] = n + 0.5
            elif t == "boolean":
                data[name] = True if a["default"] is None else a["default"].lower() == "true"
            elif t == "date":
                data[name] = (date(2024, 1, 1) + timedelta(days=n % 3000 + (7 if name in later else 0))).isoformat()
            elif t == "datetime":
                data[name] = (date(2024, 1, 1) + timedelta(days=n % 3000 + (7 if name in later else 0))).isoformat() + "T10:00:00"
        for rel in self.model["relationships"]:
            if rel["kind"] == "fk" and rel["fk"]["holder"] == entity:
                col = rel["fk"]["column"]
                if col in skip:
                    continue
                target = rel["fk"]["target"]
                if target == entity:
                    continue
                data[col] = self.instance(target, n)
        data.update(overrides or {})
        for name in skip:
            data.pop(name, None)
        return data

    def instance(self, entity, n):
        key = (entity, n)
        if key not in self._cache:
            r = self.req("POST", self.url(entity), self.payload(entity, n))
            ident = (r.json or {}).get(self.pk(entity)) if r.status == 201 and isinstance(r.json, dict) else None
            self._cache[key] = ident
        return self._cache[key]

    def pk(self, entity):
        return next(a["name"] for a in self.entities[entity]["attributes"] if a["primary_key"])

    def create(self, entity, n, overrides=None, skip=()):
        return self.req("POST", self.url(entity), self.payload(entity, n, overrides, skip))

    def _id(self, resp, entity):
        return (resp.json or {}).get(self.pk(entity)) if isinstance(resp.json, dict) else None

    # --------------------------------------------------------------- checks ---
    def run(self):
        self.check_ui()
        for entity in self.entities:
            self.check_crud(entity)
            self.check_validation(entity)
        for rel in self.model["relationships"]:
            if rel["kind"] == "fk":
                self.check_fk(rel)
            else:
                self.check_m2m(rel)
        for rule in self.rules:
            getattr(self, "rule_" + rule["pattern"])(rule)
        return self.checks

    def check_ui(self):
        r = self.c.request("GET", "/") if self._safe() else None
        self.add("ui", "home page", r is not None and r.status_code == 200, getattr(r, "status_code", "no response"),
                 "ui:home")
        for entity in self.entities:
            try:
                r = self.c.request("GET", f"/web/{to_snake(entity)}")
                ok, detail = r.status_code == 200, r.status_code
            except Exception as exc:
                ok, detail = False, repr(exc)
            self.add("ui", f"{entity} list page", ok, detail, f"ui:{entity}")

    def _safe(self):
        return True

    def check_crud(self, entity):
        ops = self.ops(entity)
        req = f"entity:{entity}"
        n = 1
        r = self.create(entity, n)
        data = r.json if isinstance(r.json, dict) else {}
        if (entity, n) not in self._cache and r.status == 201:
            self._cache[(entity, n)] = self._id(r, entity)   # parents of later entities reuse it
        if "create" in ops:
            self.add("crud", f"{entity}: create", r.status == 201, f"{r.status} {r.json}", req)
            sent = self.payload(entity, n)
            missing = [k for k in sent if k not in data]
            for a in self.entities[entity]["attributes"]:
                if not a["primary_key"]:
                    self.add("crud", f"{entity}: attribute '{a['name']}' stored", a["name"] not in missing,
                             f"missing in response: {a['name']}", f"attr:{entity}.{a['name']}")
        ident = self._id(r, entity)
        if "read" in ops:
            g = self.req("GET", self.url(entity, ident)) if ident is not None else _Resp(0, None)
            self.add("crud", f"{entity}: read one", g.status == 200 and isinstance(g.json, dict)
                     and g.json.get(self.pk(entity)) == ident, f"{g.status}", req)
            lst = self.req("GET", self.url(entity))
            self.add("crud", f"{entity}: list", lst.status == 200 and isinstance(lst.json, list)
                     and any(isinstance(x, dict) and x.get(self.pk(entity)) == ident for x in lst.json), f"{lst.status}", req)
            nf = self.req("GET", self.url(entity, 999999))
            self.add("crud", f"{entity}: unknown id returns 404", nf.status == 404, f"{nf.status}", req)
        if "update" in ops and ident is not None:
            field = next((a for a in self.entities[entity]["attributes"]
                          if not a["primary_key"] and not a["unique"] and a["type"] in ("string", "text")
                          and not a["choices"]), None)
            if field:
                u = self.req("PUT", self.url(entity, ident), {field["name"]: "updated value"})
                self.add("crud", f"{entity}: update", u.status == 200 and isinstance(u.json, dict)
                         and u.json.get(field["name"]) == "updated value", f"{u.status} {u.json}", req)
        if "delete" in ops:
            fresh = self.fresh()
            d = self.create(entity, fresh)
            did = self._id(d, entity)
            if did is not None:
                x = self.req("DELETE", self.url(entity, did))
                g = self.req("GET", self.url(entity, did))
                self.add("crud", f"{entity}: delete", x.status == 200 and g.status == 404, f"{x.status}/{g.status}", req)
            else:
                self.add("crud", f"{entity}: delete", False, "could not create record to delete", req)

    def check_validation(self, entity):
        fks = {r["fk"]["column"]: r for r in self.model["relationships"]
               if r["kind"] == "fk" and r["fk"]["holder"] == entity}
        for a in self.entities[entity]["attributes"]:
            name = a["name"]
            if a["primary_key"]:
                continue
            req = f"attr:{entity}.{name}"
            if a["required"] and a["default"] is None:
                r = self.create(entity, self.fresh(), skip=(name,))
                self.add("validation", f"{entity}.{name}: required field enforced", r.status in (400, 409, 422),
                         f"status {r.status}", req)
            if a["default"] is not None:
                r = self.create(entity, self.fresh(), skip=(name,))
                got = (r.json or {}).get(name) if isinstance(r.json, dict) else None
                self.add("validation", f"{entity}.{name}: default applied", r.status == 201 and _same(a, got, a["default"]),
                         f"status {r.status}, got {got!r}, want {a['default']!r}", req)
            if a["unique"]:
                n = self.fresh()
                first = self.create(entity, n)
                value = (first.json or {}).get(name) if isinstance(first.json, dict) else None
                dup = self.create(entity, self.fresh(), overrides={name: value})
                self.add("validation", f"{entity}.{name}: unique constraint", first.status == 201 and dup.status in (409, 422),
                         f"first {first.status}, duplicate {dup.status}", req)
            if a["choices"]:
                r = self.create(entity, self.fresh(), overrides={name: "__not_a_choice__"})
                self.add("validation", f"{entity}.{name}: invalid choice rejected", r.status in (400, 422),
                         f"status {r.status}", req)
            if a["type"] in ("integer", "float"):
                r = self.create(entity, self.fresh(), overrides={name: "not-a-number"})
                self.add("validation", f"{entity}.{name}: wrong type rejected", r.status in (400, 422),
                         f"status {r.status}", req)
        for col, rel in fks.items():
            if rel.get("required"):
                r = self.create(entity, self.fresh(), skip=(col,))
                self.add("validation", f"{entity}.{col}: required relationship enforced", r.status in (400, 422),
                         f"status {r.status}", f"rel:{rel['name']}")

    def check_fk(self, rel):
        holder, target, col = rel["fk"]["holder"], rel["fk"]["target"], rel["fk"]["column"]
        req = f"rel:{rel['name']}"
        if target == holder:
            return self.add("relationship", f"{rel['name']}: self reference skipped", True, "", req)
        n = self.fresh()
        tid = self.instance(target, n)
        r = self.create(holder, n)
        ok = r.status == 201 and isinstance(r.json, dict) and r.json.get(col) == tid and tid is not None
        self.add("relationship", f"{rel['name']}: {holder}.{col} references {target}", ok, f"{r.status} {r.json}", req)
        bad = self.create(holder, self.fresh(), overrides={col: 999999})
        self.add("relationship", f"{rel['name']}: dangling reference rejected", bad.status in (400, 404, 409, 422),
                 f"status {bad.status}", req)

    def _link_url(self, rel, side, ident):
        entity = rel[side]
        return self.url(entity, ident, "links", rel["name"])

    def check_m2m(self, rel):
        req = f"rel:{rel['name']}"
        n = self.fresh()
        a, b = self.instance(rel["from"], n), self.instance(rel["to"], n)
        if a is None or b is None:
            return self.add("relationship", f"{rel['name']}: setup", False, "could not create linked records", req)
        r = self.req("POST", self._link_url(rel, "from", a), {"target_id": b})
        self.add("relationship", f"{rel['name']}: link records", r.status == 201, f"{r.status} {r.json}", req)
        fwd = self.req("GET", self._link_url(rel, "from", a))
        self.add("relationship", f"{rel['name']}: linked record listed", fwd.status == 200 and isinstance(fwd.json, list)
                 and any(x.get(self.pk(rel["to"])) == b for x in fwd.json), f"{fwd.status}", req)
        back = self.req("GET", self._link_url(rel, "to", b))
        ok_back = back.status == 200 and isinstance(back.json, list) and any(
            x.get(self.pk(rel["from"])) == a for x in back.json)
        self.add("relationship", f"{rel['name']}: reverse direction listed", ok_back, f"{back.status}", req)
        dup = self.req("POST", self._link_url(rel, "from", a), {"target_id": b})
        self.add("relationship", f"{rel['name']}: duplicate link rejected", dup.status in (409, 422), f"{dup.status}", req)
        un = self.req("DELETE", self._link_url(rel, "from", a) + f"/{b}")
        gone = self.req("GET", self._link_url(rel, "from", a))
        self.add("relationship", f"{rel['name']}: unlink", un.status == 200 and gone.status == 200 and gone.json == [],
                 f"{un.status}/{gone.json}", req)

    # ------------------------------------------------------------- rule checks ---
    def rule_numeric_range(self, rule):
        e, p, req = rule["entity"], rule["params"], f"rule:{rule['id']}"
        attr = p["attribute"]
        if "min" in p:
            r = self.create(e, self.fresh(), overrides={attr: p["min"] - 1})
            self.add("rule", f"{rule['id']}: below minimum rejected", r.status in (400, 409, 422), f"{r.status}", req)
            ok = self.create(e, self.fresh(), overrides={attr: p["min"]})
            self.add("rule", f"{rule['id']}: minimum accepted", ok.status == 201, f"{ok.status} {ok.json}", req)
        if "max" in p:
            r = self.create(e, self.fresh(), overrides={attr: p["max"] + 1})
            self.add("rule", f"{rule['id']}: above maximum rejected", r.status in (400, 409, 422), f"{r.status}", req)
            ok = self.create(e, self.fresh(), overrides={attr: p["max"]})
            self.add("rule", f"{rule['id']}: maximum accepted", ok.status == 201, f"{ok.status} {ok.json}", req)

    def rule_date_order(self, rule):
        e, p, req = rule["entity"], rule["params"], f"rule:{rule['id']}"
        n = self.fresh()
        r = self.create(e, n, overrides={p["earlier"]: "2024-06-10", p["later"]: "2024-06-01"})
        self.add("rule", f"{rule['id']}: wrong order rejected", r.status in (400, 409, 422), f"{r.status}", req)
        ok = self.create(e, self.fresh(), overrides={p["earlier"]: "2024-06-01", p["later"]: "2024-06-10"})
        self.add("rule", f"{rule['id']}: correct order accepted", ok.status == 201, f"{ok.status} {ok.json}", req)

    def rule_unique_together(self, rule):
        e, p, req = rule["entity"], rule["params"], f"rule:{rule['id']}"
        n = self.fresh()
        first = self.create(e, n)
        # a second record that repeats the combination but differs in every unique attribute
        combo = {k: first.json.get(k) for k in p["attributes"]} if isinstance(first.json, dict) else {}
        second = self.create(e, self.fresh(), overrides=combo)
        self.add("rule", f"{rule['id']}: duplicate combination rejected",
                 first.status == 201 and second.status in (409, 422), f"{first.status}/{second.status}", req)
        other = self.fresh()
        third = self.create(e, other)
        self.add("rule", f"{rule['id']}: distinct combination accepted", third.status == 201, f"{third.status} {third.json}", req)

    def _violating(self, entity, attr, value):
        t = self._attr_type(entity, attr)
        a = next((x for x in self.entities[entity]["attributes"] if x["name"] == attr), None)
        if t == "boolean":
            return not value
        if a and a.get("choices"):
            return next(c for c in a["choices"] if c != value)
        if t in ("integer", "float"):
            return value + 1
        return f"not-{value}"

    def _try_link(self, rel, over_from=None, over_to=None):
        """Attempt one link; return (status, from_id, to_id)."""
        n = self.fresh()
        if rel["kind"] == "m2m":
            a = self.create(rel["from"], n, over_from)
            b = self.create(rel["to"], n, over_to)
            ia, ib = self._id(a, rel["from"]), self._id(b, rel["to"])
            if ia is None or ib is None:
                return -2, ia, ib
            r = self.req("POST", self._link_url(rel, "from", ia), {"target_id": ib})
            return r.status, ia, ib
        holder, target, col = rel["fk"]["holder"], rel["fk"]["target"], rel["fk"]["column"]
        over_target, over_holder = (over_to, over_from) if holder == rel["from"] else (over_from, over_to)
        t = self.create(target, n, over_target)
        tid = self._id(t, target)
        if tid is None:
            return -2, None, None
        overrides = dict(over_holder or {})
        overrides[col] = tid
        h = self.create(holder, n, overrides)
        hid = self._id(h, holder)
        return (h.status, (hid, tid)[0] if holder == rel["from"] else tid, tid if holder == rel["from"] else hid)

    def _unlink(self, rel, ia, ib):
        if rel["kind"] == "m2m":
            return self.req("DELETE", self._link_url(rel, "from", ia) + f"/{ib}").status
        holder = rel["fk"]["holder"]
        hid = ia if holder == rel["from"] else ib
        return self.req("DELETE", self.url(holder, hid)).status

    def rule_link_requires_value(self, rule):
        p, req = rule["params"], f"rule:{rule['id']}"
        rel = self.rels[p["relationship"]]
        side = p["side"]
        entity = rel[side]
        bad_value = self._violating(entity, p["attribute"], p["value"])
        bad = {p["attribute"]: bad_value}
        kwargs = {"over_from": bad} if side == "from" else {"over_to": bad}
        status, _, _ = self._try_link(rel, **kwargs)
        self.add("rule", f"{rule['id']}: link refused when {p['attribute']} != {p['value']}", status == 409 or status in (400, 422),
                 f"status {status}", req)
        status, ia, ib = self._try_link(rel)
        self.add("rule", f"{rule['id']}: link allowed when {p['attribute']} == {p['value']}", status == 201, f"status {status}", req)
        if status != 201:
            return
        ident = ia if side == "from" else ib
        if "set_on_link" in p:
            g = self.req("GET", self.url(entity, ident))
            self.add("rule", f"{rule['id']}: {p['attribute']} set to {p['set_on_link']} on link",
                     isinstance(g.json, dict) and g.json.get(p["attribute"]) == p["set_on_link"], f"{g.json}", req)
        if "set_on_unlink" in p:
            u = self._unlink(rel, ia, ib)
            holder_deleted = rel["kind"] == "fk" and rel["fk"]["holder"] == entity
            if holder_deleted:
                return
            g = self.req("GET", self.url(entity, ident))
            self.add("rule", f"{rule['id']}: {p['attribute']} set to {p['set_on_unlink']} on unlink",
                     u == 200 and isinstance(g.json, dict) and g.json.get(p["attribute"]) == p["set_on_unlink"],
                     f"{u} {g.json}", req)

    def rule_max_links(self, rule):
        p, req = rule["params"], f"rule:{rule['id']}"
        rel = self.rels[p["relationship"]]
        side, limit = p["side"], p["max"]
        entity = rel[side]
        if rel["kind"] == "fk" and side == rel["fk"]["holder"] and entity == rel["fk"]["holder"] and \
                (rel["from"] == rel["fk"]["holder"]) == (side == "from"):
            return self.add("rule", f"{rule['id']}: not applicable to the foreign-key holder", True, "", req)
        n = self.fresh()
        subject = self.create(entity, n)
        sid = self._id(subject, entity)
        results = []
        for i in range(limit + 1):
            m = self.fresh()
            if rel["kind"] == "m2m":
                other = rel["to"] if side == "from" else rel["from"]
                oid = self._id(self.create(other, m), other)
                url = self._link_url(rel, side, sid)
                results.append(self.req("POST", url, {"target_id": oid}).status)
            else:
                holder, col = rel["fk"]["holder"], rel["fk"]["column"]
                results.append(self.create(holder, m, {col: sid}).status)
        self.add("rule", f"{rule['id']}: first {limit} links accepted", all(s == 201 for s in results[:limit]), f"{results}", req)
        self.add("rule", f"{rule['id']}: link number {limit + 1} refused", results[-1] in (400, 409, 422), f"{results}", req)

    def rule_forbid_delete_if_linked(self, rule):
        p, req = rule["params"], f"rule:{rule['id']}"
        rel = self.rels[p["relationship"]]
        entity = rule["entity"]
        status, ia, ib = self._try_link(rel)
        if status != 201:
            return self.add("rule", f"{rule['id']}: setup link", False, f"status {status}", req)
        ident = ia if rel["from"] == entity else ib
        d = self.req("DELETE", self.url(entity, ident))
        self.add("rule", f"{rule['id']}: delete refused while linked", d.status == 409, f"{d.status}", req)
        holder_is_entity = rel["kind"] == "fk" and rel["fk"]["holder"] == entity
        if not holder_is_entity:
            self._unlink(rel, ia, ib)
            d2 = self.req("DELETE", self.url(entity, ident))
            self.add("rule", f"{rule['id']}: delete allowed after unlink", d2.status == 200, f"{d2.status}", req)


@dataclass
class _Resp:
    status: int
    json: object


def _same(attr, got, want):
    t = attr["type"]
    try:
        if t == "boolean":
            return got is want or got == (want.lower() == "true")
        if t == "integer":
            return int(got) == int(want)
        if t == "float":
            return float(got) == float(want)
    except (TypeError, ValueError):
        return False
    return str(got) == str(want)


def run_functional_suite(client, model, api="/api"):
    return Harness(client, model, api).run()


def summarize(checks):
    """Aggregate metrics from a list of Check objects (see benchmark metrics)."""
    total = len(checks)
    passed = sum(c.passed for c in checks)
    by_cat = {}
    for cat in CATEGORIES:
        sub = [c for c in checks if c.category == cat]
        by_cat[cat] = {"passed": sum(c.passed for c in sub), "total": len(sub),
                       "rate": (sum(c.passed for c in sub) / len(sub)) if sub else None}
    reqs = {}
    for c in checks:
        if c.requirement:
            reqs.setdefault(c.requirement, []).append(c.passed)
    covered = sum(all(v) for v in reqs.values())
    return {
        "checks_total": total, "checks_passed": passed,
        "functional_pass_rate": passed / total if total else 0.0,
        "by_category": by_cat,
        "requirement_coverage": covered / len(reqs) if reqs else 0.0,
        "requirements_total": len(reqs), "requirements_covered": covered,
        "relationship_correctness": by_cat["relationship"]["rate"],
        "business_rule_correctness": by_cat["rule"]["rate"],
    }


def checks_to_dicts(checks):
    return [asdict(c) for c in checks]
