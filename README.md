# SLM-CodeGen

Domain-model-guided web application generation using small language models (SLMs) and deterministic
template mapping. Final Year Project: *Domain Model-Guided Web Application Code Generation Using Small
Language Models and Deterministic Template Mapping*.

An XML domain model (written by hand, or proposed by a local SLM from natural-language requirements and
**approved by a human**) is parsed, validated, and turned into a complete, self-contained FastAPI + SQLite +
HTML website. The SLM is used only where semantic interpretation is needed (free-text business rules,
natural-language requirements); everything structured is handled deterministically.

## Setup

```bash
cd /Users/rafiaali/codegen
source .venv/bin/activate
pip install -r requirements.txt
```

## Quick start

```bash
# 1. validate a model
python main.py validate domain_models/hospital.xml

# 2. generate a website (deterministic, no model needed)
python main.py generate domain_models/hospital.xml output/hospital

# 3. run it  ->  http://127.0.0.1:8000/   (REST docs: /docs)
python main.py serve output/hospital --port 8000
```

Demo domains (all produced by the same engine): `library_structured.xml`, `hospital.xml`, `student.xml`
(plus `ecommerce.xml`, `inventory.xml`, `hotel.xml`). `library.xml` is the original example whose business
rule is plain English - generating it without an SLM reports the rule as **not implemented**.

## Generator dashboard

```bash
python main.py dashboard            # http://127.0.0.1:8765/
```
Create a project from pasted/uploaded XML or from natural-language requirements, review validation and the
model preview, edit and **approve** the model, pick the SLM provider/model, generate, browse the generated
files, launch the app, run the functional tests, and see token / latency metrics.

## Local SLM (optional)

```bash
python main.py slm-status           # detects Ollama / LM Studio and prints setup help
python main.py generate domain_models/library.xml output/library_slm --slm --model mistral:latest
```
Configure via the dashboard, `slm_config.json`, or env vars `SLM_PROVIDER` (`ollama`|`lmstudio`|`none`),
`SLM_MODEL`, `SLM_BASE_URL`. Without a model everything except natural-language input and text-rule
interpretation still works. Ollama: <https://ollama.com> (`ollama serve`, `ollama pull mistral`).
LM Studio: start the local server in the Developer tab (port 1234).

## Tests and benchmark

```bash
python -m pytest                    # 88 tests; the live-SLM test skips if no model is running
python main.py benchmark --models mistral:latest --out benchmark_results
python main.py benchmark --no-slm   # deterministic configurations only
```
See [BENCHMARKS.md](BENCHMARKS.md) and [RESEARCH_METHODOLOGY.md](RESEARCH_METHODOLOGY.md).

## XML format (summary)

```xml
<domain name="Hospital">
  <entities>
    <entity name="Patient" label="Patient" plural="Patients">
      <attribute name="id" type="integer" primaryKey="true"/>
      <attribute name="email" type="email" required="true" unique="true" default="…" choices="a,b" label="…"/>
    </entity>
  </entities>
  <relationships><relationship from="Appointment" to="Patient" type="many-to-one" name="booked_by" required="true"/></relationships>
  <constraints>
    <constraint id="r1" entity="Appointment" type="business-rule" pattern="numeric_range" attribute="fee" min="0">English description</constraint>
    <constraint entity="Book" type="business-rule">Text-only rule (needs the SLM to be implemented)</constraint>
  </constraints>
  <operations><operation entity="Appointment" allow="create,read,update,delete"/></operations>
</domain>
```
Types: `integer string text boolean float date datetime email`. Relationships: `one-to-one one-to-many
many-to-one many-to-many`. Rule patterns: `numeric_range date_order unique_together link_requires_value
max_links forbid_delete_if_linked` (see [ARCHITECTURE.md](ARCHITECTURE.md)).

## Limitations / not supported

- Only integer auto-increment primary keys; one primary key per entity (no composite keys).
- SQLite only. No authentication, authorisation or user roles in generated apps (a prompt such as
  "administrators can view patient records" yields entities, not access control).
- Business rules are limited to the six registered patterns; anything else is reported as not implemented.
- Relationships have no payload attributes (an association with its own fields must be modelled as an entity).
- Generated UI is generic CRUD (list/detail/forms); no custom pages, file uploads or workflows beyond link/unlink.
- Foreign-key deletion is *restrict* (a referenced record cannot be deleted); m2m links are removed with the record.
- CSRF protection uses a double-submit cookie; it is meant for local prototypes, not production hardening.
- The dashboard is a local, single-user tool (binds to 127.0.0.1; launching a project runs generated code locally).
- Baseline C (larger LLM) is only evaluated if a suitable model is installed - otherwise it is reported unavailable.
