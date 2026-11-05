# Research methodology

**Question.** Can structured domain-model decomposition and deterministic template mapping reduce the inference
cost of small local language models while preserving the functional correctness of generated web applications?

## Configurations
| Id | Description |
|---|---|
| `A_deterministic` | Templates only. Free-text rules cannot be implemented (reported, not hidden). |
| `A_oracle` | Templates fed the *structured* rules - upper bound / reference for the pipeline. |
| `Proposed` | SLM maps free-text rules to registry patterns (one short call per rule), templates do the rest. |
| `B_direct_slm` | The SLM writes the whole application as one Python file from the same XML. |
| `C_large_llm` | Same as B with a larger model; **only run if explicitly requested and installed** (`--large-model`), otherwise reported `unavailable`. |

All configurations receive identical input: the domain model with business rules reduced to English text.
Ground truth is the structured version of the same model.

## Data separation
`benchmark/splits.json`: **dev** (`library_structured`, `hospital`) may be used to tune templates, prompts and the
registry; **eval** (`student`, `ecommerce`, `inventory`, `hotel`) is held out. Summaries are reported per split.
NL-to-model prompts are split the same way (`benchmark/nl_prompts.json`). HumanEval/MBPP are not used: they measure
function-level synthesis, not full-stack behaviour.

## Metrics
Input/output/total tokens and model inference latency (from the provider's own counters), end-to-end generation
latency, server RSS memory (`ps`) and generator peak RSS, syntax success (fraction of `.py` files that compile),
startup success (a real `uvicorn` process answers `/openapi.json`), functional pass rate, requirement coverage,
relationship correctness, business-rule correctness, human correction iterations (0 in automated runs; the
dashboard records human edits; automatic SLM repair rounds are reported separately).

## Functional suite (`evaluation/functional.py`)
Generated from the ground-truth model, not from the generated code, and run over HTTP against `/api/<entity>`:
CRUD, required/default/unique/choice/type validation, FK integrity, many-to-many link/unlink/duplicate, every
registered rule (violation refused *and* boundary accepted, link side effects), plus home/list pages. **Requirement
coverage** = fraction of requirement items (entities, attributes, relationships, rules, UI pages) whose checks
all pass.

## Threats to validity
The suite and the engine were developed together (the engine passes 100 % by design; `test_suite_detects_missing_business_rules`
checks the suite is not vacuous). Baseline B/C are scored through the same REST contract, which is stated in its
prompt; apps that choose other routes fail. Single run per cell at temperature 0; models differ in speed on the
test machine (8 GB Apple laptop). Domains are small. Token counts are the server's tokenizer counts.

## Reproduce
```bash
python main.py benchmark --models mistral:latest --out benchmark_results
```
Outputs `results.json`, `results.csv` (per run) and `summary.csv` (per config × split).
