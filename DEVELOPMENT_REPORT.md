# Development report

## Audit of the starting point
Existing: XML parser, basic validator, templates for SQLAlchemy models, Pydantic schemas, FastAPI CRUD, and partial frontend templates.
Issues found and fixed:
1. Relationships and business-rule constraints were parsed but never used in generation.
2. The frontend generator was only connected to the homepage (no list/form/edit/delete routes were served).
3. Schemas made `required` attributes without default mandatory but ignored required+default combos; defaults were never validated.
4. Generated API hard-coded the database path and used a try/except import hack; all code lived in one flat folder (not self-contained).
5. The validator did not check identifiers, defaults, relationship names, rule references; it accepted unsupported XML silently.
6. Primary keys were assumed integer in schemas without checking.
Original templates were moved to `templates/legacy/`; the old `generated/` folder and `generated_app.db` were left untouched.

## Implemented and tested (88 pytest tests, all passing at the end of this session)
- Parser/IR/validator, XML round-trip, invalid and unsupported XML rejection.
- Template engine with two-stage Jinja rendering; self-contained projects; syntax check of every generated file.
- Full-stack apps: REST + HTML (list, search, pagination, create/edit/delete, detail with link/unlink, empty/error states, CSRF, escaping).
- Relationships (FK and association tables) and six business-rule patterns, verified by the functional suite on six domains.
- SLM integration (Ollama, LM Studio, fake for tests), environment detection, rule interpretation with registry validation and repair.
- NL → model extraction, approval gate bound to a content hash, dashboard (API-tested; one visual check of the generated site in a browser; the dashboard UI itself was not visually inspected).
- Benchmark harness, CSV/JSON export; a full run with Mistral 7B is in `BENCHMARKS.md`.

## Not done / pending
- Baseline C: not run (no larger model installed). Reported as unavailable.
- Dashboard UI was not tested in a real browser; its JavaScript has no automated tests.
- No authentication/roles, composite keys, non-SQLite databases, relationship attributes or custom workflows (see README limitations).
- NL extraction is unreliable for small models (1 of 3 prompts invalid after repair) - human review is required by design.
- Memory of the SLM process itself was not measured.

## Notes
- The functional suite and the generator were co-developed; a sensitivity test confirms the suite detects a generator that lacks the rules.
- Nothing was committed to Git, deployed, or connected to external services. The project directory is not a Git repository.
