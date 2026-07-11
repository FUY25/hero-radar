from __future__ import annotations

import argparse
import importlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pipeline.decision.kimi_provider import (
    load_local_kimi_config,
)
from pipeline.decision.layer2_context_builder import ContextBudget
from pipeline.decision.layer2_eval import (
    DatasetContractError,
    Layer2EvalCase,
    Layer2EvalDataset,
    V2EvalConfig,
    load_eval_dataset,
    run_v2_evaluation,
)
from pipeline.decision.layer2_eval_provider import RateLimitedKimiEvalProvider
from pipeline.decision.layer2_scoring_investigator import (
    BRIEF_CONTEXT_POLICY_VERSION,
    BRIEF_OUTPUT_SCHEMA_VERSION,
    DEFAULT_BRIEF_PROMPT_VERSION,
    InvestigatorLimits,
    SCORING_CONTEXT_POLICY_VERSION,
)
from pipeline.decision.rate_limit import StartRateLimiter


EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_EXECUTION = 3
EXIT_GRADING = 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the production-equivalent Layer 2 V2 scorer and Brief Writer "
            "against the fixed deterministic tool replay corpus."
        )
    )
    parser.add_argument(
        "--dataset",
        default="evals/layer2/datasets/scoring_cases.v2.jsonl",
    )
    parser.add_argument("--output-dir")
    parser.add_argument("--pipeline-config", default="pipeline/config.json")
    parser.add_argument("--secrets-file")
    providers = parser.add_mutually_exclusive_group()
    providers.add_argument(
        "--live-kimi",
        action="store_true",
        help="Make real Kimi scorer and Brief Writer API calls.",
    )
    providers.add_argument(
        "--provider-factory",
        help="Test-only provider factory import path using module:callable.",
    )
    parser.add_argument("--model")
    parser.add_argument("--timeout-seconds", type=int)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--retry-backoff-seconds", type=float, default=1.0)
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--starts-per-second", type=float, default=0.5)
    parser.add_argument("--input-cost-per-million", type=float)
    parser.add_argument("--cached-input-cost-per-million", type=float)
    parser.add_argument("--output-cost-per-million", type=float)
    parser.add_argument("--cost-currency", default="USD")
    parser.add_argument("--pricing-revision", default="")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--no-briefs", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-execution-errors", action="store_true")
    parser.add_argument(
        "--allow-code-change-on-resume",
        action="store_true",
        help="Audit and record an intentional bug-fix code revision during resume.",
    )
    parser.add_argument(
        "--allow-provider-profile-change-on-resume",
        action="store_true",
        help="Audit and record an intentional provider-profile revision during resume.",
    )
    parser.add_argument("--skip-handshake", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        dataset = _select_cases(load_eval_dataset(args.dataset), args.case_id)
        production = _production_eval_config(Path(args.pipeline_config))
        config = V2EvalConfig(
            prompt_version=production["prompt_version"],
            trials=args.trials,
            include_briefs=not args.no_briefs,
            limits=production["limits"],
            context_budget=production["context_budget"],
            direct_final_enabled=production["direct_final_enabled"],
            output_schema_version=production["output_schema_version"],
            tool_registry_version=production["tool_registry_version"],
        )
        args.model = args.model or production["model"]
        args.thinking_type = production["thinking_type"]
        args.timeout_seconds = (
            production["timeout_seconds"]
            if args.timeout_seconds is None
            else args.timeout_seconds
        )
        args.max_output_tokens = (
            production["max_output_tokens"]
            if args.max_output_tokens is None
            else args.max_output_tokens
        )
        if args.resume and not args.output_dir:
            raise ValueError("--resume requires --output-dir")
        if (args.input_cost_per_million is None) != (
            args.output_cost_per_million is None
        ):
            raise ValueError(
                "input and output cost rates must both be supplied or both omitted"
            )
        if (
            args.cached_input_cost_per_million is not None
            and args.input_cost_per_million is None
        ):
            raise ValueError("cached input cost requires input and output cost rates")
        _validate_numeric_args(args)
    except (DatasetContractError, OSError, ValueError) as exc:
        print(f"Layer 2 eval configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    if args.validate_only:
        print(
            json.dumps(
                {
                    "ok": True,
                    "dataset_version": dataset.version,
                    "cases": len(dataset.cases),
                    "prompt_version": config.prompt_version,
                    "trials": config.trials,
                    "expected_slots": len(dataset.cases) * config.trials,
                },
                sort_keys=True,
            )
        )
        return EXIT_OK
    if not args.live_kimi and not args.provider_factory:
        print(
            "--live-kimi is required for the real 20-case eval; "
            "--provider-factory is available for deterministic tests only.",
            file=sys.stderr,
        )
        return EXIT_CONFIG
    try:
        provider_factory = (
            _live_kimi_factory(args)
            if args.live_kimi
            else _load_factory(args.provider_factory)
        )
        output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()
        paths = run_v2_evaluation(
            dataset,
            provider_factory=provider_factory,
            output_dir=output_dir,
            config=config,
            resume=args.resume,
            retry_execution_errors=args.retry_execution_errors,
            allow_code_change=args.allow_code_change_on_resume,
            allow_provider_profile_change=(
                args.allow_provider_profile_change_on_resume
            ),
            provider_execution="real_kimi" if args.live_kimi else "test_provider",
        )
        aggregate = json.loads(paths.aggregate_json.read_text(encoding="utf-8"))
    except Exception as exc:
        print(
            f"Layer 2 eval execution error: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return EXIT_EXECUTION
    print(paths.root)
    return EXIT_OK if aggregate.get("all_passed") else EXIT_GRADING


def _select_cases(
    dataset: Layer2EvalDataset,
    case_ids: list[str],
) -> Layer2EvalDataset:
    if not case_ids:
        return dataset
    requested = list(dict.fromkeys(str(case_id) for case_id in case_ids))
    by_id = {case.case_id: case for case in dataset.cases}
    missing = [case_id for case_id in requested if case_id not in by_id]
    if missing:
        raise ValueError(f"unknown case id(s): {', '.join(missing)}")
    return Layer2EvalDataset(
        version=dataset.version,
        cases=tuple(by_id[case_id] for case_id in requested),
    )


def _live_kimi_factory(
    args: argparse.Namespace,
) -> Callable[[int, Layer2EvalCase], RateLimitedKimiEvalProvider]:
    limiter = StartRateLimiter(args.starts_per_second)
    explicit_config = {}
    if args.secrets_file:
        secrets_path = Path(args.secrets_file).expanduser().resolve()
        if not secrets_path.is_file():
            raise ValueError("--secrets-file does not exist")
        if secrets_path.stat().st_mode & 0o077:
            raise ValueError("--secrets-file must not be accessible by group or others")
        explicit_config = load_local_kimi_config(secrets_path)

    def build(_trial: int, _case: Layer2EvalCase) -> RateLimitedKimiEvalProvider:
        return RateLimitedKimiEvalProvider(
            limiter=limiter,
            api_key=explicit_config.get("api_key") or None,
            model=args.model,
            base_url=explicit_config.get("base_url") or None,
            timeout=args.timeout_seconds,
            max_retries=args.max_retries,
            retry_backoff_seconds=args.retry_backoff_seconds,
            max_output_tokens=args.max_output_tokens,
            thinking_type=args.thinking_type,
            input_cost_per_million=args.input_cost_per_million,
            cached_input_cost_per_million=args.cached_input_cost_per_million,
            output_cost_per_million=args.output_cost_per_million,
            cost_currency=args.cost_currency,
            pricing_revision=args.pricing_revision,
        )

    probe = build(0, Layer2EvalCase("probe", {}, {}, {}, {}, {}))
    if not probe.api_key:
        raise ValueError("KIMI_API_KEY or local Kimi credentials are not configured")
    if not args.skip_handshake:
        status = probe.handshake()
        if not status.get("ok"):
            raise RuntimeError(f"Kimi handshake failed: {status}")
    return build


def _production_eval_config(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    layer2 = payload.get("layer2") if isinstance(payload, dict) else None
    scoring = layer2.get("scoring_agent") if isinstance(layer2, dict) else None
    brief = layer2.get("brief_writer") if isinstance(layer2, dict) else None
    tools = layer2.get("tool_runtime") if isinstance(layer2, dict) else None
    if not all(isinstance(value, dict) for value in (scoring, brief, tools)):
        raise ValueError("pipeline config is missing Layer 2 component objects")
    if scoring.get("provider") != "kimi" or brief.get("provider") != "kimi":
        raise ValueError("real eval currently requires Kimi scorer and Brief Writer")
    if scoring.get("model") != brief.get("model"):
        raise ValueError("real eval requires scorer and Brief Writer model parity")
    if not bool(brief.get("enabled")):
        raise ValueError("real eval requires the production Brief Writer to be enabled")
    if int(scoring.get("max_output_tokens") or 0) != int(
        brief.get("max_output_tokens") or 0
    ):
        raise ValueError("real eval requires scorer and Brief Writer output parity")
    if scoring.get("thinking_type") != brief.get("thinking_type"):
        raise ValueError("real eval requires scorer and Brief Writer thinking parity")
    if scoring.get("thinking_type") != "disabled":
        raise ValueError("structured V2 eval requires thinking_type=disabled")
    expected_versions = {
        "scoring_agent.context_policy_version": (
            scoring.get("context_policy_version"),
            SCORING_CONTEXT_POLICY_VERSION,
        ),
        "brief_writer.prompt_version": (
            brief.get("prompt_version"),
            DEFAULT_BRIEF_PROMPT_VERSION,
        ),
        "brief_writer.output_schema_version": (
            brief.get("output_schema_version"),
            BRIEF_OUTPUT_SCHEMA_VERSION,
        ),
        "brief_writer.context_policy_version": (
            brief.get("context_policy_version"),
            BRIEF_CONTEXT_POLICY_VERSION,
        ),
    }
    for name, (actual, expected) in expected_versions.items():
        if actual != expected:
            raise ValueError(f"production config {name} does not match runtime")
    context = scoring.get("context_budget")
    tool_budget = scoring.get("tool_budget")
    if not isinstance(context, dict) or not isinstance(tool_budget, dict):
        raise ValueError("pipeline config is missing scoring context/tool budgets")
    return {
        "provider": "kimi",
        "model": str(scoring.get("model") or ""),
        "prompt_version": str(scoring.get("prompt_version") or ""),
        "output_schema_version": str(scoring.get("output_schema_version") or ""),
        "tool_registry_version": str(tools.get("registry_version") or ""),
        "direct_final_enabled": bool(scoring.get("enable_direct_final")),
        "timeout_seconds": int(scoring.get("timeout_seconds") or 0),
        "max_output_tokens": int(scoring.get("max_output_tokens") or 0),
        "thinking_type": str(scoring.get("thinking_type") or ""),
        "limits": InvestigatorLimits(
            max_investigation_turns=int(scoring.get("max_investigation_turns") or 0),
            max_scoring_attempts=int(scoring.get("max_scoring_attempts") or 0),
            max_tool_calls_per_candidate=int(tool_budget.get("max_calls_per_candidate") or 0),
            max_web_search_calls_per_candidate=int(tool_budget.get("max_web_search_calls_per_candidate") or 0),
            max_github_file_calls_per_candidate=int(tool_budget.get("max_github_file_calls_per_candidate") or 0),
            max_homepage_fetches_per_candidate=int(tool_budget.get("max_homepage_calls_per_candidate") or 0),
            max_tool_result_chars=int(tools.get("max_github_file_chars") or 0),
            max_parallel_tool_calls_per_turn=int(tool_budget.get("max_parallel_calls_per_turn") or 0),
        ),
        "context_budget": ContextBudget(
            max_context_tokens=int(context.get("max_context_tokens") or 0),
            output_reserve=int(context.get("output_reserve") or 0),
            safety_margin=int(context.get("safety_margin") or 0),
            identity_allocation=int(context.get("identity_allocation") or 0),
            evidence_summary_allocation=int(context.get("evidence_summary_allocation") or 0),
            top_evidence_allocation=int(context.get("top_evidence_allocation") or 0),
            previous_turn_allocation=int(context.get("previous_turn_allocation") or 0),
            tool_observation_allocation=int(context.get("tool_observation_allocation") or 0),
            recent_raw_tool_result_count=int(context.get("recent_raw_tool_result_count") or 0),
        ),
    }


def _validate_numeric_args(args: argparse.Namespace) -> None:
    positive = {
        "timeout_seconds": args.timeout_seconds,
        "max_output_tokens": args.max_output_tokens,
        "starts_per_second": args.starts_per_second,
    }
    for name, value in positive.items():
        if isinstance(value, bool) or not math.isfinite(float(value)) or float(value) <= 0:
            raise ValueError(f"--{name.replace('_', '-')} must be finite and positive")
    non_negative = {
        "max_retries": args.max_retries,
        "retry_backoff_seconds": args.retry_backoff_seconds,
        "input_cost_per_million": args.input_cost_per_million,
        "cached_input_cost_per_million": args.cached_input_cost_per_million,
        "output_cost_per_million": args.output_cost_per_million,
    }
    for name, value in non_negative.items():
        if value is None:
            continue
        if isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < 0:
            raise ValueError(f"--{name.replace('_', '-')} must be finite and non-negative")
    if args.max_retries != int(args.max_retries):
        raise ValueError("--max-retries must be an integer")
    if not str(args.cost_currency or "").isalpha():
        raise ValueError("--cost-currency must be alphabetic")


def _load_factory(
    value: str,
) -> Callable[[int, Layer2EvalCase], Any]:
    module_name, separator, attribute = value.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("provider factory must use module:callable syntax")
    factory = getattr(importlib.import_module(module_name), attribute)
    if not callable(factory):
        raise ValueError("provider factory is not callable")
    return factory


def _default_output_dir() -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("evals/layer2/results") / f"v2-real-{timestamp}"


if __name__ == "__main__":
    raise SystemExit(main())
