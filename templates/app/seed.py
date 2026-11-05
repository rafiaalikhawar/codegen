"""Fill the database with realistic sample records:  python -m app.seed [--count 8]

Uses the same engine as the web app, so every rule and constraint is respected
(records a rule refuses are simply skipped).
"""
import argparse
import random
from datetime import date, timedelta

from .database import Database
from sqlalchemy import select

from .engine import AppError, ENTITIES, create, link
from .models import MODELS
from .meta import META

PEOPLE = ["Amelia Khan", "Noah Ahmed", "Sofia Rossi", "Liam Chen", "Zara Malik", "Ethan Brooks", "Mia Fernandez",
          "Omar Haddad", "Chloe Dubois", "Lucas Silva", "Aisha Noor", "Daniel Kim", "Priya Patel", "Hugo Martin"]
ADJECTIVES = ["Aurora", "Nimbus", "Solstice", "Ember", "Atlas", "Willow", "Zenith", "Coastal", "Velvet", "Summit", "Orbit"]
NOUNS = ["Wireless Headphones", "Travel Backpack", "Desk Lamp", "Smart Watch", "Ceramic Mug", "Running Sneakers",
         "Mechanical Keyboard", "Leather Wallet", "Yoga Mat", "Bluetooth Speaker", "Notebook Set", "Sunglasses"]
SENTENCES = ["Thoughtfully designed and built to last.", "A customer favourite this season.", "Lightweight, durable and easy to use.",
             "Comes with a two-year warranty.", "Crafted from responsibly sourced materials.", "Perfect for everyday use."]
PERSON_WORDS = ("patient", "doctor", "student", "customer", "member", "guest", "teacher", "client", "employee", "person", "user")


def _sample(entity, field, i, rng, ids):
    meta, t, name = ENTITIES[entity], field["type"], field["name"]
    person = any(w in entity.lower() for w in PERSON_WORDS)
    if field["choices"]:
        return field["choices"][i % len(field["choices"])]
    if field["fk"]:
        pool = ids.get(field["fk"]["target"]) or []
        return pool[i % len(pool)] if pool else None
    if t == "image":
        return f"/static/img/p{i % 8 + 1}.svg"
    if t == "email":
        return f"user{i + 1}.{entity.lower()}@example.com"
    if t == "boolean":
        return (i % 4 != 3) if field["default"] in (None, "true") else (i % 4 == 3)
    if t == "integer":
        return rng.randint(5, 120) if any(w in name for w in ("stock", "quantity", "qty")) else rng.randint(1, 60)
    if t == "float":
        if any(w in name for w in ("price", "cost", "fee", "rate", "amount", "total", "salary")):
            return round(rng.uniform(9, 240), 2)
        return round(rng.uniform(1, 4), 1)
    if t == "date":
        return (date.today() + timedelta(days=rng.randint(-60, 60))).isoformat()
    if t == "datetime":
        return (date.today() + timedelta(days=rng.randint(-30, 30))).isoformat() + "T10:30:00"
    if t == "text":
        return " ".join(rng.sample(SENTENCES, 3))
    if any(w in name for w in ("sku", "code", "number")):
        return f"{entity[:3].upper()}-{1000 + i}"
    if any(w in name for w in ("name", "title", "company")) and person and "company" not in name:
        return PEOPLE[i % len(PEOPLE)]
    if any(w in name for w in ("name", "title", "company", "description")):
        return f"{ADJECTIVES[(i + len(entity)) % len(ADJECTIVES)]} {NOUNS[(i * 3 + len(entity)) % len(NOUNS)]}"
    if "phone" in name:
        return f"+1 555 01{i:02d}"
    return f"{field['label']} {i + 1}"


def seed(db, count=8, seed_value=42):
    rng = random.Random(seed_value)
    ids, created = {}, {}
    with db.session_factory() as session:          # re-running never duplicates: existing tables are left alone
        for entity, meta in ENTITIES.items():
            existing = session.scalars(select(getattr(MODELS[entity], meta["pk"]))).all()
            if existing:
                ids[entity] = list(existing)
    skip = set(ids)
    order = sorted(ENTITIES, key=lambda e: sum(1 for f in ENTITIES[e]["fields"] if f["fk"]))
    for _ in range(2):                              # two passes so FK targets exist for self-dependent orderings
        for entity in order:
            if entity in skip or (ids.get(entity) and _ == 1):
                continue
            for i in range(count):
                data = {}
                for f in ENTITIES[entity]["fields"]:
                    if f["pk"]:
                        continue
                    value = _sample(entity, f, i, rng, ids)
                    if value is not None:
                        data[f["name"]] = value
                with db.session_factory() as session:
                    try:
                        obj = create(session, entity, data)
                        ids.setdefault(entity, []).append(getattr(obj, ENTITIES[entity]["pk"]))
                        created[entity] = created.get(entity, 0) + 1
                    except AppError:
                        pass
    for rel in META["relationships"]:
        if rel["kind"] != "m2m":
            continue
        sources, targets = ids.get(rel["from"], []), ids.get(rel["to"], [])
        for i, s in enumerate(sources):
            for k in range(2):
                if targets:
                    with db.session_factory() as session:
                        try:
                            link(session, rel["from"], s, rel["name"], targets[(i + k * 3) % len(targets)])
                        except AppError:
                            pass
    return created


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=8)
    args = parser.parse_args()
    db = Database()
    print("Created:", seed(db, args.count))


if __name__ == "__main__":
    main()
