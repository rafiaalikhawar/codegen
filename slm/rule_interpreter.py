"""SLM-assisted semantic interpretation of free-text business rules.

The model only *selects a pattern and fills parameters*; the answer is validated
against the predefined registry and the domain model.  Anything that fails
validation, or that the model declares unsupported/ambiguous, stays
**not implemented** and is reported as such.
"""
from parser.ir import derive_relationships
from slm.base import SLMError, UsageTracker, extract_json
from validator.rule_registry import PATTERNS, normalize_rule, registry_prompt_spec

SYSTEM = ("You map one business rule, written in English, onto exactly one pattern from a fixed registry. "
          "Reply with a single JSON object and nothing else.")


def _context(model, entity_name):
    entities = {e["name"]: e for e in model["entities"]}

    def describe(e):
        attrs = ", ".join(f"{a['name']}({a['type']}{'; choices ' + '/'.join(a['choices']) if a.get('choices') else ''})"
                          for a in e["attributes"] if not a["primary_key"])
        for r in model["relationships"]:
            fk = r.get("fk")
            if fk and fk["holder"] == e["name"]:
                attrs += f", {fk['column']}(integer; foreign key to {fk['target']})"
        return f"{e['name']}: {attrs}"

    lines = [describe(entities[entity_name])]
    rels = []
    for r in model["relationships"]:
        if entity_name in (r["from"], r["to"]):
            rels.append(f"{r['name']}: {r['from']} -> {r['to']} ({r['type']}); side 'from'={r['from']}, side 'to'={r['to']}")
            other = r["to"] if r["from"] == entity_name else r["from"]
            if other != entity_name:
                lines.append(describe(entities[other]))
    return "\n".join(dict.fromkeys(lines)), "\n".join(rels) or "(none)"


def build_prompt(model, rule, errors=None):
    attrs, rels = _context(model, rule["entity"])
    prompt = (f"PATTERN REGISTRY:\n{registry_prompt_spec()}\n\n"
              f"DOMAIN ENTITIES:\n{attrs}\n\nRELATIONSHIPS:\n{rels}\n\n"
              f"RULE on entity {rule['entity']}: \"{rule['description']}\"\n\n"
              'Answer with JSON: {"status": "mapped" | "unsupported" | "ambiguous", "pattern": "<pattern id>", '
              '"params": {...}, "reason": "<one short sentence>"}.\n'
              'Use "unsupported" when no pattern expresses the rule exactly, "ambiguous" when it has several '
              "plausible readings. Use only attribute and relationship names listed above; never invent names.")
    if errors:
        prompt += "\n\nYour previous answer was rejected:\n" + "\n".join(f"- {e}" for e in errors) + \
                  "\nReturn a corrected JSON object."
    return prompt


def interpret_rule(model, rule, provider, usage, max_repairs=1):
    errors, last = None, None
    for attempt in range(max_repairs + 1):
        prompt = build_prompt(model, rule, errors)
        try:
            response = provider.generate(prompt, system=SYSTEM, json_mode=True, max_tokens=300)
            usage.add(response, purpose=f"interpret_rule:{rule['id']}")
            answer = extract_json(response.text)
        except SLMError as exc:
            errors, last = [str(exc)], {"status": "error", "reason": str(exc)}
            continue
        status = answer.get("status")
        if status in ("unsupported", "ambiguous"):
            return {"status": status, "pattern": None, "params": {}, "reason": str(answer.get("reason", "")),
                    "repairs": attempt}
        pattern, params = answer.get("pattern"), answer.get("params")
        if status != "mapped" or pattern not in PATTERNS or not isinstance(params, dict):
            errors = [f"status must be mapped/unsupported/ambiguous and pattern one of {sorted(PATTERNS)} "
                      f"with an object 'params' (got status={status!r}, pattern={pattern!r})."]
            last = {"status": "rejected", "reason": errors[0]}
            continue
        candidate = {"id": rule["id"], "entity": rule["entity"], "pattern": pattern, "params": params}
        clean, problems = normalize_rule(model, candidate)
        if problems:
            errors, last = problems, {"status": "rejected", "pattern": pattern, "params": params,
                                      "reason": "; ".join(problems)}
            continue
        return {"status": "mapped", "pattern": pattern, "params": clean,
                "reason": str(answer.get("reason", "")), "repairs": attempt}
    last = dict(last or {"status": "error", "reason": "no answer"})
    last.setdefault("pattern", None)
    last.setdefault("params", {})
    last["repairs"] = max_repairs
    return last


def interpret_rules(model, provider, usage=None, max_repairs=1):
    """Interpret every text-only rule in ``model``.

    Returns ``{"resolutions": {rule_id: result}, "usage": UsageTracker}`` where only results with
    ``status == "mapped"`` carry an implementable pattern.
    """
    usage = usage or UsageTracker()
    derive_relationships(model)
    resolutions = {}
    for rule in model["constraints"]:
        if rule.get("pattern") or not rule.get("description"):
            continue
        result = interpret_rule(model, rule, provider, usage, max_repairs)
        result["resolved_by"] = f"slm:{provider.name}:{provider.model}"
        resolutions[rule["id"]] = result
    return {"resolutions": resolutions, "usage": usage}


def applicable(resolutions):
    """Subset of resolutions that may be applied to the generator."""
    return {rid: r for rid, r in resolutions.items() if r.get("status") == "mapped"}
