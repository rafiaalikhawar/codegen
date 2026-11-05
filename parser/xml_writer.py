"""Serialise the canonical IR back to domain-model XML (used by the NL phase and dashboard)."""
import xml.etree.ElementTree as ET


def _s(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return str(value)


def model_to_xml(model):
    root = ET.Element("domain", name=model["name"])
    entities = ET.SubElement(root, "entities")
    for e in model["entities"]:
        el = ET.SubElement(entities, "entity", name=e["name"])
        for key in ("label", "plural"):
            if e.get(key):
                el.set(key, e[key])
        for a in e["attributes"]:
            at = ET.SubElement(el, "attribute", name=a["name"], type=a["type"])
            for flag, xml_name in (("primary_key", "primaryKey"), ("required", "required"), ("unique", "unique")):
                if a.get(flag):
                    at.set(xml_name, "true")
            if a.get("default") is not None:
                at.set("default", _s(a["default"]))
            if a.get("choices"):
                at.set("choices", ",".join(a["choices"]))
            if a.get("label"):
                at.set("label", a["label"])
    if model.get("relationships"):
        rels = ET.SubElement(root, "relationships")
        for r in model["relationships"]:
            el = ET.SubElement(rels, "relationship", {"from": r["from"], "to": r["to"], "type": r["type"],
                                                      "name": r["name"]})
            if r.get("required"):
                el.set("required", "true")
            if r.get("label"):
                el.set("label", r["label"])
    if model.get("constraints"):
        cons = ET.SubElement(root, "constraints")
        for c in model["constraints"]:
            el = ET.SubElement(cons, "constraint", id=c["id"], entity=c["entity"], type=c.get("type") or "business-rule")
            if c.get("pattern"):
                el.set("pattern", c["pattern"])
                for k, v in (c.get("params") or {}).items():
                    el.set(k, _s(v))
            el.text = c.get("description") or ""
    if model.get("operations"):
        ops = ET.SubElement(root, "operations")
        for name, allowed in model["operations"].items():
            ET.SubElement(ops, "operation", entity=name, allow=",".join(allowed))
    ET.indent(root, space="    ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
