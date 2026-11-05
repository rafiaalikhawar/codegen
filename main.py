"""Command line entry point.

  python main.py validate domain_models/hospital.xml
  python main.py generate domain_models/hospital.xml output/hospital [--slm]
  python main.py serve output/hospital --port 8000
  python main.py dashboard [--port 8765]
  python main.py benchmark [--models mistral:latest] [--out benchmark_results]
  python main.py slm-status
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_VENV_PYTHON = Path(__file__).resolve().parent / ".venv" / "bin" / "python"
if _VENV_PYTHON.exists() and Path(sys.prefix).resolve() != (_VENV_PYTHON.parent.parent).resolve() \
        and os.environ.get("SLMCG_NO_REEXEC") != "1":
    # Always run inside the project's virtual environment (system Python has mismatched packages).
    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON), *sys.argv])


def cmd_validate(args):
    from parser.xml_parser import DomainParseError, parse_domain_model
    from validator.model_validator import validate
    try:
        report = validate(parse_domain_model(args.xml))
    except DomainParseError as exc:
        print("INVALID XML:", exc)
        return 1
    for w in report["warnings"]:
        print("WARNING:", w)
    if report["errors"]:
        print("VALIDATION FAILED")
        for e in report["errors"]:
            print(" -", e)
        return 1
    print("VALIDATION PASSED")
    return 0


def cmd_generate(args):
    from generator.code_generator import GenerationError, generate_project, prepare_model
    from slm.config import SLMConfig, build_provider, load_config
    resolutions = None
    if args.slm:
        from slm.rule_interpreter import applicable, interpret_rules
        cfg = load_config()
        if args.provider:
            cfg = SLMConfig(provider=args.provider, model=args.model or cfg.model)
        elif args.model:
            cfg.model = args.model
            if cfg.provider in ("", "none"):
                cfg.provider = "ollama"
        provider = build_provider(cfg)
        if provider is None:
            print("No SLM configured; run `python main.py slm-status` for setup help. Generating without the SLM.")
        else:
            model, _ = prepare_model(args.xml)
            out = interpret_rules(model, provider)
            for rid, res in out["resolutions"].items():
                print(f"SLM rule {rid}: {res['status']} {res.get('pattern') or ''} {res.get('reason', '')}")
            print("SLM usage:", out["usage"].as_dict())
            resolutions = applicable(out["resolutions"])
    try:
        result = generate_project(args.xml, args.output, resolutions)
    except GenerationError as exc:
        print(exc)
        return 1
    print(f"Generated {len(result.files)} files in {result.output_dir} ({result.seconds:.2f}s)")
    for r in result.report["unimplemented_rules"]:
        print(f"NOT IMPLEMENTED rule {r['id']} ({r['entity']}): {r['description']}")
    if args.sample_data:
        env = dict(os.environ, SLMCG_NO_REEXEC="1")
        subprocess.run([sys.executable, "-m", "app.seed"], cwd=args.output, env=env, check=False)
    print(f"Run it:  python main.py serve {args.output}")
    return 0


def cmd_serve(args):
    return subprocess.call([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                            "--port", str(args.port)], cwd=args.directory)


def cmd_dashboard(args):
    import uvicorn
    uvicorn.run("dashboard.app:app", host="127.0.0.1", port=args.port)
    return 0


def cmd_benchmark(args):
    from benchmark.runner import run_benchmark
    run_benchmark(models=args.models, out_dir=args.out, domains=args.domains, large_model=args.large_model,
                  skip_slm=args.no_slm)
    return 0


def cmd_slm_status(args):
    from slm.config import detect_environment, load_config
    print(json.dumps({"config": load_config().to_dict(), "environment": detect_environment()}, indent=2))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="SLM-CodeGen")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("validate"); s.add_argument("xml"); s.set_defaults(fn=cmd_validate)
    s = sub.add_parser("generate"); s.add_argument("xml"); s.add_argument("output")
    s.add_argument("--slm", action="store_true", help="use the configured local SLM to interpret text-only rules")
    s.add_argument("--sample-data", action="store_true", help="fill the new site's database with demo records")
    s.add_argument("--provider"); s.add_argument("--model"); s.set_defaults(fn=cmd_generate)
    s = sub.add_parser("serve"); s.add_argument("directory"); s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("dashboard"); s.add_argument("--port", type=int, default=8765); s.set_defaults(fn=cmd_dashboard)
    s = sub.add_parser("benchmark"); s.add_argument("--models", nargs="*", default=None)
    s.add_argument("--domains", nargs="*"); s.add_argument("--out", default="benchmark_results")
    s.add_argument("--large-model", default=None, help="model name for Baseline C (must be configured explicitly)")
    s.add_argument("--no-slm", action="store_true"); s.set_defaults(fn=cmd_benchmark)
    s = sub.add_parser("slm-status"); s.set_defaults(fn=cmd_slm_status)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
