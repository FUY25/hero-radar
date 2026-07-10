from __future__ import annotations

import argparse
import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pipeline.decision.layer2_eval import (
    DatasetContractError,
    Layer2EvalCase,
    PairedEvalConfig,
    load_eval_dataset,
    run_paired_evaluation,
)


EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_EXECUTION = 3
EXIT_GRADING = 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the production-equivalent Layer 2 scorer and Brief Writer "
            "against deterministic replay tools."
        )
    )
    parser.add_argument(
        "--dataset",
        default="evals/layer2/datasets/scoring_cases.v1.jsonl",
    )
    parser.add_argument("--output-dir")
    parser.add_argument("--provider-factory", help="Import path module:callable")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--no-briefs", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        dataset = load_eval_dataset(args.dataset)
        config = PairedEvalConfig(
            trials=args.trials,
            include_briefs=not args.no_briefs,
        )
    except (DatasetContractError, OSError, ValueError) as exc:
        print(f"Layer 2 eval configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    if args.validate_only:
        print(
            json.dumps(
                {"ok": True, "dataset_version": dataset.version, "cases": len(dataset.cases)},
                sort_keys=True,
            )
        )
        return EXIT_OK
    if not args.provider_factory:
        print(
            "--provider-factory is required for production-equivalent execution; "
            "the legacy authored-response evaluator remains schema smoke only.",
            file=sys.stderr,
        )
        return EXIT_CONFIG
    try:
        provider_factory = _load_factory(args.provider_factory)
        output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()
        paths = run_paired_evaluation(
            dataset,
            provider_factory=provider_factory,
            output_dir=output_dir,
            config=config,
        )
        aggregate = json.loads(paths.aggregate_json.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Layer 2 eval execution error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_EXECUTION
    print(paths.root)
    return EXIT_OK if aggregate.get("all_passed") else EXIT_GRADING


def _load_factory(
    value: str,
) -> Callable[[str, int, Layer2EvalCase], Any]:
    module_name, separator, attribute = value.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("provider factory must use module:callable syntax")
    factory = getattr(importlib.import_module(module_name), attribute)
    if not callable(factory):
        raise ValueError("provider factory is not callable")
    return factory


def _default_output_dir() -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("evals/layer2/results") / f"paired-{timestamp}"


if __name__ == "__main__":
    raise SystemExit(main())
