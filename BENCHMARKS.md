# Benchmarks

Run on a MacBook with 8 GB RAM, Python 3.11.4, Ollama with `mistral:latest` (7B) as the SLM,
temperature 0, one run per cell. Raw data: `benchmark_results/results.json`, `results.csv`, `summary.csv`
(reproduce with `python main.py benchmark --models mistral:latest`). Method: see `RESEARCH_METHODOLOGY.md`.

Input to every configuration: the domain model with business rules as English text only. Ground truth: the structured rules.
Values are means over domains; **eval** = held-out domains (student, ecommerce, inventory, hotel, n=4); **dev** = library, hospital (n=2).

## Results (eval split)

| Config | Total tokens | Generation latency | Syntax OK | Startup OK | Functional pass | Requirement coverage | Relationship correctness | Business-rule correctness |
|---|---|---|---|---|---|---|---|---|
| A_deterministic (templates only) | 0 | 0.05 s | 100 % | 100 % | 94.6 % | 86.8 % | 100 % | 54.2 % |
| A_oracle (templates + structured rules) | 0 | 0.04 s | 100 % | 100 % | 100 % | 100 % | 100 % | 100 % |
| **Proposed** (SLM rule mapping + templates) | 2 303 | 21.0 s | 100 % | 100 % | **98.2 %** | **95.2 %** | 100 % | **84.3 %** |
| B_direct_slm (SLM writes whole app) | 2 728 | 184.1 s | 87.5 % | 25 % | 2.2 % | 0 % | 33 % (n=1 app that started) | 0 % |
| C_large_llm | **unavailable** - no larger model installed; not run | | | | | | | |

Dev split: A 93.9 % / Proposed 99.3 % / B 0 % functional pass (B: 0 of 2 apps started).
Server memory of generated apps was 54–66 MB. Human correction iterations are 0 in these automated runs.

## Natural language → domain model (mistral, one run each)
| Prompt | Valid model | Entity recall | Tokens | Latency | Auto-repairs |
|---|---|---|---|---|---|
| hospital (dev) | yes | 3/3 (+Admin, PatientRecord, AppointmentDoctor extras) | 1 037 | 204 s | 0 |
| school (eval) | **no** (invalid type, rule bound to a made-up entity) | 3/3 | 2 732 | 143 s | 1 |
| shop (eval) | yes | 3/3 | 998 | 58 s | 0 |

The human review step exists because of cases like these: the model adds extra entities and one proposal stayed invalid after repair.

## Reading the results honestly
- The deterministic pipeline reaches 100 % when rules are structured. The only thing the SLM adds is rule interpretation: it lifted
  business-rule correctness from 54 % to 84 % (eval) without breaking anything else. It is not perfect: the SLM mapped 15 of 17 rules
  (inventory and hotel each lost one: rejected or unsupported), and several mapped rules had parameters that differed from the
  intended ones (per-domain rule correctness 83-100 %). Those are failures the human review of the rules report is meant to catch.
- **Token cost is not dramatically lower.** Proposed used ~16 % fewer total tokens than B here, and B's output was capped by
  `max_tokens=3500` (its tokens would grow with application size, while Proposed's grow with the number of free-text rules).
  The clear difference is **time** (about 9x) and **correctness** (B's apps mostly did not start).
- B's failures were generated-code problems (import/validation errors at startup), not an unfair harness; but B is also scored on the
  stated REST contract, which favours the engine's own conventions.
- n is small (6 domains, one run, one 7B model). No significance claims are made. Baseline C was **not** run.
