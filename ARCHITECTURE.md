# Architecture

```
XML ──parser──► IR (v1.0, JSON dict) ──validator──► validated IR ──► [SLM rule interpreter] ──► template mapping ──► project
NL  ──SLM extractor──► proposal XML ──human review/edit/approve──┘                                   (Jinja2)
```

| Package | Role |
|---|---|
| `parser/` | `xml_parser.py` (XML → IR, records unsupported features), `ir.py` (naming, relationship derivation: FK vs association table, canonical hash), `xml_writer.py` (IR → XML) |
| `validator/` | `model_validator.py` (errors + warnings), `rule_registry.py` (the fixed set of business-rule patterns and their parameter validation) |
| `generator/` | `code_generator.py`: builds template context, two-stage Jinja rendering, syntax-checks every generated `.py`, writes manifest; refuses to overwrite directories it did not create |
| `templates/app/` | stage-1 templates (`*.j2`) and verbatim runtime files (`engine.py`, `web.py`, `main.py`, runtime HTML) |
| `slm/` | provider abstraction (Ollama, LM Studio, scripted fake), environment detection, `rule_interpreter.py` |
| `nl2model/` | `extractor.py` (NL → candidate model), `approval.py` (draft → approved state machine bound to a content hash) |
| `dashboard/` | local FastAPI + Jinja2 + JS UI for the generator |
| `evaluation/` | spec-driven functional test suite and runners |
| `benchmark/` | configurations A / A-oracle / Proposed / B / C, domain splits, CSV/JSON export |
| `legacy` | `templates/legacy/` keeps the original templates; the old `generated/` folder is untouched |

## Two-stage rendering
Stage 1 (generation time) renders `models.py`, `schemas.py`, `meta.py`, `api.py`, `base.html`, `index.html`,
`style.css`, `README.md`. HTML that contains *runtime* Jinja syntax is protected with `{% raw %}` blocks (nav and
index are stage-1; list/form/detail/error pages are copied verbatim so stage 1 never sees their `{{ }}`).
Stage 2 happens inside the generated app (Starlette `Jinja2Templates`, autoescape on).

## Generated application
`app/meta.py` holds the domain as data. `engine.py` is the only place with business logic: coercion,
validation (required, unique, choices, types, foreign keys), CRUD, relationship linking and the rule
interpreter. `api.py` (typed per-entity REST routes + generic link routes) and `web.py` (HTML routes) both call
it, so API and UI behave identically. Errors: `AppError` → JSON (`{"detail", "errors"}`) under `/api`, HTML error
page/redirect with message under `/web`.

Relationship mapping: one-to-many → FK on the *to* entity; many-to-one / one-to-one → FK on the *from*
entity (unique for one-to-one); many-to-many → association table. Link rules also fire when an FK is
set/changed.

## Business-rule patterns (registry)
`numeric_range`, `date_order`, `unique_together`, `link_requires_value` (with optional
`set_on_link`/`set_on_unlink`), `max_links`, `forbid_delete_if_linked`. Rules are **data**; the SLM can only
choose a pattern and fill parameters, which are then validated against the registry and the domain model.
Failed, unsupported or ambiguous interpretations stay listed as *not implemented* in the dashboard, CLI output,
`rules_report.json` and the generated README.

## Safety properties
No model output is executed or written as code. Output directories are guarded by a manifest. Dashboard file
reads are confined to the project's output directory. Forms use CSRF tokens; HTML output is escaped.
