from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from pipeline.decision.layer2_context_builder import ContextBudget
from pipeline.decision.layer2_eval_grading import grade_eval_artifact
from pipeline.decision.layer2_eval_reporting import (
    aggregate_results,
    render_report,
    write_blind_briefs,
)
from pipeline.decision.layer2_harness import TelemetryLLMProvider
from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.layer2_scoring_investigator import (
    BRIEF_CONTEXT_POLICY_VERSION,
    BRIEF_OUTPUT_SCHEMA_VERSION,
    DEFAULT_BRIEF_PROMPT_VERSION,
    InvestigatorLimits,
    SCORING_CONTEXT_POLICY_VERSION,
    SCORING_OUTPUT_SCHEMA_VERSION,
    TOOL_REGISTRY_VERSION,
    build_deepdive_brief,
    classify_scored_route,
    score_with_investigator,
)
from pipeline.decision.layer2_tool_registry import ToolCandidateContext, ToolSpec
from pipeline.decision.request_contract import canonical_json, sanitize_contract_value
from pipeline.decision.schema import init_decision_db


CASE_CONTRACT_VERSION = "layer2-eval-case-v1"


class DatasetContractError(ValueError):
    pass


@dataclass(frozen=True)
class Layer2EvalCase:
    case_id: str
    model_input: Mapping[str, Any]
    replay: Mapping[str, Any]
    gold: Mapping[str, Any]
    grader: Mapping[str, Any]
    human_review: Mapping[str, Any]


@dataclass(frozen=True)
class Layer2EvalDataset:
    version: str
    cases: tuple[Layer2EvalCase, ...]


@dataclass(frozen=True)
class PairedEvalConfig:
    prompt_versions: tuple[str, str] = (
        "layer2-scoring-investigator-v1",
        "layer2-scoring-investigator-v2",
    )
    trials: int = 3
    include_briefs: bool = True
    limits: InvestigatorLimits = InvestigatorLimits()
    context_budget: ContextBudget = ContextBudget()
    direct_final_enabled: bool = True
    output_schema_version: str = SCORING_OUTPUT_SCHEMA_VERSION
    tool_registry_version: str = TOOL_REGISTRY_VERSION
    grader_version: str = "layer2-graders-v1"

    def __post_init__(self) -> None:
        if len(self.prompt_versions) != 2:
            raise ValueError("paired evaluation requires exactly two prompt versions")
        if int(self.trials) < 3:
            raise ValueError("paired evaluation requires at least three uncached trials")


@dataclass(frozen=True)
class EvalOutputPaths:
    root: Path
    results_jsonl: Path
    aggregate_json: Path
    report_markdown: Path
    run_metadata_json: Path
    blind_briefs_jsonl: Path
    blind_mapping_json: Path


@dataclass(frozen=True)
class ReplayToolDefinition:
    family: str
    input_schema: Mapping[str, Any]
    availability: Callable[[ToolCandidateContext], bool]
    authorizer: Callable[[ToolCandidateContext, dict[str, Any]], bool]


class ReplayToolRegistry:
    """Network-free ToolSpecs backed only by one case's sanitized recordings."""

    def __init__(self, case: Layer2EvalCase) -> None:
        self.recording_version = str(case.replay["recording_version"])
        self._recordings: dict[tuple[str, str], dict[str, Any]] = {}
        for recording in case.replay["tools"]:
            if not isinstance(recording, dict):
                raise DatasetContractError(
                    f"{case.case_id}: replay tool recording must be an object"
                )
            _require_exact_keys(
                recording,
                {"tool", "arguments", "result"},
                f"{case.case_id}:replay.tools",
            )
            tool_name = str(recording["tool"])
            arguments = sanitize_contract_value(recording["arguments"])
            result = sanitize_contract_value(recording["result"])
            key = (tool_name, canonical_json(arguments))
            if key in self._recordings:
                raise DatasetContractError(
                    f"{case.case_id}: duplicate replay recording for {tool_name}"
                )
            self._recordings[key] = result
        self.specs = self._build_specs()
        self.tools = {name: spec.execute for name, spec in self.specs.items()}

    def _execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        key = (tool_name, canonical_json(sanitize_contract_value(arguments)))
        result = self._recordings.get(key)
        if result is None:
            return {
                "status": "unavailable",
                "error": "recording_not_found",
                "recording_version": self.recording_version,
            }
        return json.loads(json.dumps(result, ensure_ascii=False))

    def _build_specs(self) -> dict[str, ToolSpec]:
        definitions = _replay_tool_definitions()
        specs: dict[str, ToolSpec] = {}
        for name, definition in definitions.items():
            family = definition.family
            specs[name] = ToolSpec(
                name=name,
                version=f"replay-{self.recording_version}",
                description=f"Replay the recorded {name} result for this evaluation case.",
                input_schema=definition.input_schema,
                family=family,
                cost="recorded_zero_network",
                executor=lambda arguments, tool_name=name: self._execute(tool_name, arguments),
                availability=definition.availability,
                timeout_seconds=1,
                max_result_tokens=2_000,
                cache_policy="immutable_recording",
                concurrency_key=f"replay_{family}",
                max_in_flight=8,
                starts_per_second=1000.0,
                result_projector=lambda result, observation_id, arguments, tool_name=name, tool_family=family: _project_replay_result(
                    tool_name, tool_family, result, observation_id, arguments
                ),
                argument_authorizer=definition.authorizer,
            )
        return specs


class _RequestCaptureProvider:
    def __init__(self, provider: Any) -> None:
        self._provider = provider
        self.calls: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    @property
    def provider_name(self) -> str:
        return str(getattr(self._provider, "provider_name", ""))

    @property
    def model(self) -> str:
        return str(getattr(self._provider, "model", ""))

    def complete_json(
        self,
        *,
        task: str,
        prompt_version: str,
        input_payload: dict[str, Any],
        system_prompt: str = "",
    ) -> dict[str, Any]:
        record = {
            "task": task,
            "prompt_version": prompt_version,
            "input_payload": sanitize_contract_value(input_payload),
            "system_prompt": sanitize_contract_value(system_prompt),
        }
        self.calls.append(record)
        response = self._provider.complete_json(
            task=task,
            prompt_version=prompt_version,
            input_payload=input_payload,
            system_prompt=system_prompt,
        )
        usage = getattr(self._provider, "last_usage", None)
        if usage is not None:
            record["usage"] = sanitize_contract_value(usage)
        reported_cost = getattr(self._provider, "last_cost", None)
        if reported_cost is None and isinstance(response, Mapping):
            reported_cost = response.get("_cost") or response.get("cost")
        if reported_cost is not None:
            record["cost"] = sanitize_contract_value(reported_cost)
        return response


def run_eval_case(
    case: Layer2EvalCase,
    *,
    provider: Any,
    prompt_version: str,
    trial: int,
    include_brief: bool = True,
    limits: InvestigatorLimits | None = None,
    context_budget: ContextBudget | None = None,
    direct_final_enabled: bool = True,
    output_schema_version: str = SCORING_OUTPUT_SCHEMA_VERSION,
    tool_registry_version: str = TOOL_REGISTRY_VERSION,
    brief_prompt_version: str = DEFAULT_BRIEF_PROMPT_VERSION,
) -> dict[str, Any]:
    """Run one case through the production scorer and optional Brief Writer."""

    if int(trial) < 1:
        raise ValueError("trial must be at least 1")
    conn = sqlite3.connect(":memory:")
    init_decision_db(conn)
    replay = ReplayToolRegistry(case)
    captured = _RequestCaptureProvider(provider)
    feed_run_id = f"eval:{case.case_id}:{prompt_version}:trial-{int(trial)}"
    telemetry_provider = TelemetryLLMProvider(
        captured,
        conn=conn,
        feed_run_id=feed_run_id,
        group_id=case.case_id,
        stage="scoring_agent",
    )
    started = time.monotonic()
    try:
        result = score_with_investigator(
            conn,
            feed_run_id=feed_run_id,
            groups=[_candidate_group(case)],
            provider=telemetry_provider,
            tools=replay.tools,
            tool_specs=replay.specs,
            limits=limits or InvestigatorLimits(),
            context_budget=context_budget or ContextBudget(),
            direct_final_enabled=direct_final_enabled,
            prompt_version=prompt_version,
            output_schema_version=output_schema_version,
            tool_registry_version=tool_registry_version,
        )[0]
        scoring_calls = [
            call
            for call in captured.calls
            if call["task"] == "layer2_scoring_investigator_turn"
        ]
        preflight_mode = (
            str(scoring_calls[0]["input_payload"]["task"]["mode"])
            if scoring_calls
            else "cannot_score"
        )
        brief_artifact = None
        if include_brief and bool(case.model_input["requires_brief"]):
            before = len(captured.calls)
            brief_result = build_deepdive_brief(
                row=result,
                provider=telemetry_provider,
                prompt_version=brief_prompt_version,
            )
            brief_call = captured.calls[before]
            brief_artifact = {
                "input": brief_call["input_payload"],
                "output": brief_result["brief"],
                "prompt_version": brief_prompt_version,
                "output_schema_version": BRIEF_OUTPUT_SCHEMA_VERSION,
                "context_policy_version": BRIEF_CONTEXT_POLICY_VERSION,
                "provider": captured.provider_name,
                "model": captured.model,
                "cache_key": brief_result["cache_key"],
            }
        model_calls = _model_call_rows(conn, feed_run_id)
        if brief_artifact is not None:
            brief_calls = [
                row
                for row in model_calls
                if row["task"] == "layer2_scoring_investigator_brief"
            ]
            brief_artifact["telemetry"] = _aggregate_telemetry(
                brief_calls,
                [
                    call
                    for call in captured.calls
                    if call["task"] == "layer2_scoring_investigator_brief"
                ],
            )
            brief_artifact["request_fingerprint"] = (
                brief_calls[0]["request_fingerprint"] if brief_calls else None
            )
        artifact = {
            "artifact_version": "layer2-eval-result-v1",
            "case_id": case.case_id,
            "trial": int(trial),
            "prompt_version": prompt_version,
            "provider": captured.provider_name,
            "model": captured.model,
            "dataset_version": "layer2-scoring-cases-v1",
            "recording_version": replay.recording_version,
            "grader_version": "layer2-graders-v1",
            "output_schema_version": output_schema_version,
            "context_policy_version": SCORING_CONTEXT_POLICY_VERSION,
            "tool_registry_version": tool_registry_version,
            "preflight_mode": preflight_mode,
            "route": classify_scored_route(result),
            "score": result["l2_score"],
            "result": _json_safe_scoring_result(result),
            "trace": result["trace"],
            "tool_trace": [
                {**row, "eval_family": _tool_family(str(row.get("tool") or ""))}
                for row in result["tool_trace"]
            ],
            "observations": result["observations"],
            "evidence_catalog": _evidence_catalog(scoring_calls),
            "context_manifests": result["context_manifests"],
            "repair_count": sum(
                call["task"] == "layer2_scoring_investigator_repair"
                for call in captured.calls
            ),
            "turns": len(scoring_calls),
            "must_finalize_turns": [
                int(call["input_payload"]["task"]["turn_index"])
                for call in scoring_calls
                if call["input_payload"]["task"].get("must_finalize")
            ],
            "final_output_valid": True,
            "request_fingerprints": [row["request_fingerprint"] for row in model_calls],
            "request_records": captured.calls,
            "model_calls": model_calls,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "brief": brief_artifact,
        }
        artifact["telemetry"] = _aggregate_telemetry(model_calls, captured.calls)
        artifact["grades"] = grade_eval_artifact(case, artifact)
        artifact["passed"] = all(
            grade.get("passed", False) for grade in artifact["grades"].values()
        )
        return sanitize_contract_value(artifact)
    finally:
        conn.close()


def run_paired_evaluation(
    dataset: Layer2EvalDataset,
    *,
    provider_factory: Callable[[str, int, Layer2EvalCase], Any],
    output_dir: str | Path,
    config: PairedEvalConfig | None = None,
) -> EvalOutputPaths:
    active = config or PairedEvalConfig()
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths = EvalOutputPaths(
        root=root,
        results_jsonl=root / "results.v1.jsonl",
        aggregate_json=root / "aggregate.v1.json",
        report_markdown=root / "report.md",
        run_metadata_json=root / "run-metadata.v1.json",
        blind_briefs_jsonl=root / "blind-briefs.v1.jsonl",
        blind_mapping_json=root / "blind-brief-mapping.v1.json",
    )
    git_sha = _git_sha()
    run_started = datetime.now(timezone.utc).isoformat()
    run_id = (
        "l2eval-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + _stable_hash(
            {"dataset": dataset.version, "versions": active.prompt_versions}
        )[:8]
    )
    results: list[dict[str, Any]] = []
    provider_instances: list[Any] = []
    cache_namespaces: set[str] = set()
    expected_provider_profile: dict[str, Any] | None = None
    for case in dataset.cases:
        case_fingerprint = _stable_hash(case.model_input)
        replay_fingerprint = _stable_hash(case.replay)
        for version in active.prompt_versions:
            for trial in range(1, int(active.trials) + 1):
                provider = provider_factory(version, trial, case)
                if any(provider is existing for existing in provider_instances):
                    raise ValueError(
                        "provider_factory must return an isolated uncached provider instance per trial"
                    )
                provider_instances.append(provider)
                cache_isolation = _provider_cache_isolation(
                    provider, cache_namespaces=cache_namespaces
                )
                provider_profile = _provider_profile(provider)
                if expected_provider_profile is None:
                    expected_provider_profile = provider_profile
                elif provider_profile != expected_provider_profile:
                    raise ValueError(
                        "paired evaluation provider/model/output settings must be identical"
                    )
                artifact = run_eval_case(
                    case,
                    provider=provider,
                    prompt_version=version,
                    trial=trial,
                    include_brief=active.include_briefs,
                    limits=active.limits,
                    context_budget=active.context_budget,
                    direct_final_enabled=active.direct_final_enabled,
                    output_schema_version=active.output_schema_version,
                    tool_registry_version=active.tool_registry_version,
                )
                artifact.update(
                    {
                        "dataset_version": dataset.version,
                        "grader_version": active.grader_version,
                        "git_sha": git_sha,
                        "run_id": run_id,
                        "case_input_fingerprint": case_fingerprint,
                        "replay_fingerprint": replay_fingerprint,
                        "cache_isolation": cache_isolation,
                        "provider_profile": provider_profile,
                        "budgets": _budget_metadata(active),
                    }
                )
                results.append(artifact)
    paths.results_jsonl.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for row in results
        ),
        encoding="utf-8",
    )
    aggregate = aggregate_results(dataset, active, results)
    aggregate["run_id"] = run_id
    paths.aggregate_json.write_text(
        json.dumps(aggregate, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    paths.report_markdown.write_text(
        render_report(dataset, active, results, aggregate), encoding="utf-8"
    )
    metadata = {
        "artifact_version": "layer2-eval-run-v1",
        "run_id": run_id,
        "started_at": run_started,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "dataset_version": dataset.version,
        "grader_version": active.grader_version,
        "git_sha": git_sha,
        "prompt_versions": list(active.prompt_versions),
        "output_schema_version": active.output_schema_version,
        "context_policy_version": SCORING_CONTEXT_POLICY_VERSION,
        "tool_registry_version": active.tool_registry_version,
        "recording_versions": sorted(
            {str(case.replay["recording_version"]) for case in dataset.cases}
        ),
        "trials": active.trials,
        "budgets": _budget_metadata(active),
        "cache_isolation": "provider-declared plus isolated in-memory database",
        "live_provider_artifact": "not_run",
    }
    paths.run_metadata_json.write_text(
        json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    write_blind_briefs(paths, results, active.prompt_versions)
    return paths


def load_eval_dataset(path: str | Path) -> Layer2EvalDataset:
    source = Path(path)
    cases: list[Layer2EvalCase] = []
    dataset_version = ""
    seen: set[str] = set()
    for line_number, raw_line in enumerate(
        source.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise DatasetContractError(
                f"{source}:{line_number}: invalid JSON: {exc.msg}"
            ) from exc
        _require_exact_keys(
            row,
            {
                "contract_version",
                "dataset_version",
                "case_id",
                "model_input",
                "replay",
                "gold",
                "grader",
                "human_review",
            },
            f"{source}:{line_number}",
        )
        if row["contract_version"] != CASE_CONTRACT_VERSION:
            raise DatasetContractError(
                f"{source}:{line_number}: unsupported contract_version"
            )
        current_version = _required_string(row, "dataset_version", line_number)
        if dataset_version and current_version != dataset_version:
            raise DatasetContractError(
                f"{source}:{line_number}: mixed dataset versions"
            )
        dataset_version = current_version
        case_id = _required_string(row, "case_id", line_number)
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", case_id):
            raise DatasetContractError(
                f"{source}:{line_number}: invalid case_id {case_id!r}"
            )
        if case_id in seen:
            raise DatasetContractError(
                f"{source}:{line_number}: duplicate case_id {case_id!r}"
            )
        seen.add(case_id)
        for field in ("model_input", "replay", "gold", "grader", "human_review"):
            if not isinstance(row[field], dict):
                raise DatasetContractError(
                    f"{source}:{line_number}: {field} must be an object"
                )
        _validate_model_input(row["model_input"], source, line_number)
        _validate_replay(row["replay"], source, line_number)
        _validate_gold(row["gold"], source, line_number)
        _validate_grader(row["grader"], source, line_number)
        _validate_human_review(row["human_review"], source, line_number)
        cases.append(
            Layer2EvalCase(
                case_id=case_id,
                model_input=row["model_input"],
                replay=row["replay"],
                gold=row["gold"],
                grader=row["grader"],
                human_review=row["human_review"],
            )
        )
    if not cases:
        raise DatasetContractError(f"{source}: dataset is empty")
    return Layer2EvalDataset(version=dataset_version, cases=tuple(cases))


def legacy_schema_smoke_cases(path: str | Path) -> list[dict[str, Any]]:
    """Compatibility projection for the authored-response schema smoke only."""

    rows: list[dict[str, Any]] = []
    for case in load_eval_dataset(path).cases:
        axes = dict(case.gold["axes"])
        object_type = str(case.gold["object_type"])
        minimum_claims = int(case.gold.get("minimum_attributable_claims") or 0)
        summary = str(
            case.model_input["candidate"].get("summary")
            or case.human_review.get("display_name")
            or case.case_id
        )
        rows.append(
            {
                "name": str(case.human_review.get("display_name") or case.case_id),
                "expected_band": str(case.gold["score_band"]),
                "expected_route": str(case.gold["preflight_mode"]),
                "expected_tool_need": list(case.gold.get("required_tool_names", [])),
                "evidence_expectations": {
                    "required_families": list(
                        case.gold.get("required_evidence_families", [])
                    ),
                    "minimum_attributable_claims": minimum_claims,
                    "external_content_untrusted": True,
                    "expected_tool_outcome": case.grader.get("expected_tool_outcome"),
                },
                "scenario_tags": list(case.human_review.get("scenario_tags", [])),
                "candidate": dict(case.model_input["candidate"]),
                "response": {
                    "action": "final",
                    "information_sufficiency": {
                        "identity": "weak" if object_type == "unknown" else "strong",
                        "workflow_shift": _sufficiency(axes.get("workflow_shift")),
                        "technical_substance": _sufficiency(
                            axes.get("technical_substance")
                        ),
                        "product_market_fit": _sufficiency(
                            axes.get("product_market_fit")
                        ),
                        "momentum": _sufficiency(axes.get("momentum")),
                    },
                    "score": {
                        "object_type": object_type,
                        "is_product_or_repo": bool(case.gold["is_product_or_repo"]),
                        "axes": axes,
                        "supporting_evidence": [
                            {
                                "claim": f"{summary[:800]} [recorded schema smoke]",
                                "evidence_refs": ["eval:candidate"],
                                "supports_axes": [
                                    (
                                        "workflow_shift",
                                        "technical_substance",
                                        "product_market_fit",
                                    )[index % 3]
                                ],
                                "claim_type": "observed",
                            }
                            for index in range(minimum_claims)
                        ],
                        "negative_evidence": [],
                        "known_gaps": (
                            ["Candidate identity remains unresolved."]
                            if object_type == "unknown"
                            else []
                        ),
                        "primary_reason": summary[:80] or "Schema smoke",
                        "rationale_short": summary[:1000] or "Schema smoke",
                        "topic_tags": [],
                        "caveats": [],
                        "should_print": bool(case.gold["should_print"]),
                    },
                },
            }
        )
    return rows


def _validate_model_input(value: dict[str, Any], source: Path, line_number: int) -> None:
    _require_exact_keys(
        value,
        {"candidate", "requires_brief"},
        f"{source}:{line_number}:model_input",
    )
    if not isinstance(value["candidate"], dict):
        raise DatasetContractError(
            f"{source}:{line_number}: model_input.candidate must be an object"
        )
    if not isinstance(value["requires_brief"], bool):
        raise DatasetContractError(
            f"{source}:{line_number}: model_input.requires_brief must be boolean"
        )
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True).lower()
    forbidden = ("expected_", '"gold"', '"response"')
    if any(marker in serialized for marker in forbidden):
        raise DatasetContractError(
            f"{source}:{line_number}: model input contains grader-owned fields"
        )


def _sufficiency(value: Any) -> str:
    score = float(value or 0)
    if score >= 70:
        return "strong"
    if score >= 40:
        return "medium"
    return "weak"


def _validate_replay(value: dict[str, Any], source: Path, line_number: int) -> None:
    _require_exact_keys(
        value,
        {"recording_version", "tools"},
        f"{source}:{line_number}:replay",
    )
    if not isinstance(value["recording_version"], str) or not value["recording_version"]:
        raise DatasetContractError(
            f"{source}:{line_number}: replay.recording_version is required"
        )
    if not isinstance(value["tools"], list):
        raise DatasetContractError(
            f"{source}:{line_number}: replay.tools must be an array"
        )
    seen: set[tuple[str, str]] = set()
    allowed_tools = set(_replay_tool_definitions())
    for index, recording in enumerate(value["tools"]):
        location = f"{source}:{line_number}:replay.tools[{index}]"
        _require_exact_keys(recording, {"tool", "arguments", "result"}, location)
        if recording["tool"] not in allowed_tools:
            raise DatasetContractError(f"{location}: unsupported tool")
        if not isinstance(recording["arguments"], dict) or not isinstance(
            recording["result"], dict
        ):
            raise DatasetContractError(f"{location}: arguments and result must be objects")
        key = (recording["tool"], canonical_json(recording["arguments"]))
        if key in seen:
            raise DatasetContractError(f"{location}: duplicate recording")
        seen.add(key)
        if sanitize_contract_value(recording) != recording:
            raise DatasetContractError(f"{location}: recording contains secret material")


def _validate_gold(value: dict[str, Any], source: Path, line_number: int) -> None:
    _require_exact_keys(
        value,
        {
            "score_band",
            "score_interval",
            "preflight_mode",
            "allowed_tool_names",
            "required_tool_names",
            "object_type",
            "is_product_or_repo",
            "axes",
            "should_print",
            "minimum_attributable_claims",
            "required_evidence_families",
        },
        f"{source}:{line_number}:gold",
    )
    if value["score_band"] not in {"low", "medium", "high"}:
        raise DatasetContractError(f"{source}:{line_number}: invalid score band")
    if value["preflight_mode"] not in {
        "score_from_context",
        "investigate",
        "cannot_score",
    }:
        raise DatasetContractError(f"{source}:{line_number}: invalid preflight mode")
    interval = value["score_interval"]
    if not (
        isinstance(interval, list)
        and len(interval) == 2
        and all(isinstance(item, (int, float)) for item in interval)
        and 0 <= interval[0] <= interval[1] <= 100
    ):
        raise DatasetContractError(f"{source}:{line_number}: invalid score interval")
    if not isinstance(value["axes"], dict):
        raise DatasetContractError(f"{source}:{line_number}: axes must be an object")
    axis_limits = {
        "workflow_shift": 100,
        "technical_substance": 100,
        "product_market_fit": 100,
        "momentum": 100,
        "confidence": 100,
        "risk_penalty": 25,
        "derivative_news_penalty": 25,
    }
    if set(value["axes"]) != set(axis_limits):
        raise DatasetContractError(f"{source}:{line_number}: axes contract is incomplete")
    for axis, maximum in axis_limits.items():
        score = value["axes"][axis]
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= maximum:
            raise DatasetContractError(f"{source}:{line_number}: invalid axis {axis}")
    tool_names = set(_replay_tool_definitions())
    for field in ("allowed_tool_names", "required_tool_names"):
        names = value[field]
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise DatasetContractError(f"{source}:{line_number}: {field} must be a string array")
        if len(names) != len(set(names)) or not set(names).issubset(tool_names):
            raise DatasetContractError(f"{source}:{line_number}: invalid {field}")
    if not set(value["required_tool_names"]).issubset(value["allowed_tool_names"]):
        raise DatasetContractError(
            f"{source}:{line_number}: required tools must be allowed"
        )
    if not isinstance(value["required_evidence_families"], list) or not all(
        isinstance(name, str) for name in value["required_evidence_families"]
    ):
        raise DatasetContractError(
            f"{source}:{line_number}: required_evidence_families must be a string array"
        )
    if not isinstance(value["object_type"], str) or not value["object_type"]:
        raise DatasetContractError(f"{source}:{line_number}: object_type is required")
    for field in ("is_product_or_repo", "should_print"):
        if not isinstance(value[field], bool):
            raise DatasetContractError(f"{source}:{line_number}: {field} must be boolean")
    minimum_claims = value["minimum_attributable_claims"]
    if isinstance(minimum_claims, bool) or not isinstance(minimum_claims, int) or minimum_claims < 0:
        raise DatasetContractError(
            f"{source}:{line_number}: minimum_attributable_claims must be non-negative"
        )


def _validate_grader(value: dict[str, Any], source: Path, line_number: int) -> None:
    _require_exact_keys(
        value,
        {
            "allowed_tool_families",
            "forbidden_tool_families",
            "expected_tool_outcome",
            "require_known_gap_after_failure",
            "max_repairs",
            "max_turns",
        },
        f"{source}:{line_number}:grader",
    )
    allowed_families = {definition.family for definition in _replay_tool_definitions().values()}
    for field in ("allowed_tool_families", "forbidden_tool_families"):
        families = value[field]
        if not isinstance(families, list) or not all(isinstance(name, str) for name in families):
            raise DatasetContractError(f"{source}:{line_number}: {field} must be a string array")
        if len(families) != len(set(families)) or not set(families).issubset(allowed_families):
            raise DatasetContractError(f"{source}:{line_number}: invalid {field}")
    if set(value["allowed_tool_families"]) & set(value["forbidden_tool_families"]):
        raise DatasetContractError(f"{source}:{line_number}: tool families overlap")
    outcome = value["expected_tool_outcome"]
    if outcome is not None and not isinstance(outcome, str):
        raise DatasetContractError(f"{source}:{line_number}: expected_tool_outcome must be string or null")
    if not isinstance(value["require_known_gap_after_failure"], bool):
        raise DatasetContractError(f"{source}:{line_number}: gap requirement must be boolean")
    for field in ("max_repairs", "max_turns"):
        number = value[field]
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            raise DatasetContractError(f"{source}:{line_number}: {field} must be non-negative integer")


def _validate_human_review(
    value: dict[str, Any], source: Path, line_number: int
) -> None:
    _require_exact_keys(
        value,
        {"display_name", "scenario_tags", "blind_brief"},
        f"{source}:{line_number}:human_review",
    )
    if not isinstance(value["display_name"], str) or not value["display_name"]:
        raise DatasetContractError(f"{source}:{line_number}: display_name is required")
    if not isinstance(value["scenario_tags"], list) or not all(
        isinstance(tag, str) and tag for tag in value["scenario_tags"]
    ):
        raise DatasetContractError(f"{source}:{line_number}: scenario_tags must be a string array")
    if not isinstance(value["blind_brief"], bool):
        raise DatasetContractError(f"{source}:{line_number}: blind_brief must be boolean")


def _require_exact_keys(value: Any, expected: set[str], location: str) -> None:
    if not isinstance(value, dict):
        raise DatasetContractError(f"{location}: expected object")
    unknown = sorted(set(value) - expected)
    missing = sorted(expected - set(value))
    if unknown or missing:
        details = []
        if unknown:
            details.append(f"unknown fields: {', '.join(unknown)}")
        if missing:
            details.append(f"missing fields: {', '.join(missing)}")
        raise DatasetContractError(f"{location}: {'; '.join(details)}")


def _required_string(row: dict[str, Any], key: str, line_number: int) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DatasetContractError(f"line {line_number}: {key} must be a non-empty string")
    return value.strip()


def _repo_schema(*, with_path: bool) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "repo_key": {
            "type": "string",
            "minLength": 3,
            "pattern": r"^[^/\s]+/[^/\s]+$",
        }
    }
    required = ["repo_key"]
    if with_path:
        properties["path"] = {"type": "string", "minLength": 1, "maxLength": 300}
        required.append("path")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _repo_authorized(
    candidate: ToolCandidateContext, arguments: dict[str, Any]
) -> bool:
    return arguments.get("repo_key") == candidate.repo_key


def _replay_tool_definitions() -> dict[str, ReplayToolDefinition]:
    """Single catalog for replay validation, ToolSpecs, and trajectory families."""

    return {
        "read_evidence_rows": ReplayToolDefinition(
            family="evidence",
            input_schema={
                "type": "object",
                "properties": {"entity_id": {"type": "string", "minLength": 1}},
                "required": ["entity_id"],
                "additionalProperties": False,
            },
            availability=lambda candidate: candidate.has_retrievable_evidence,
            authorizer=lambda candidate, arguments: arguments.get("entity_id")
            in candidate.entity_ids,
        ),
        "fetch_github_readme": ReplayToolDefinition(
            family="github",
            input_schema=_repo_schema(with_path=False),
            availability=lambda candidate: bool(candidate.repo_key),
            authorizer=_repo_authorized,
        ),
        "fetch_github_file": ReplayToolDefinition(
            family="github",
            input_schema=_repo_schema(with_path=True),
            availability=lambda candidate: bool(candidate.repo_key),
            authorizer=_repo_authorized,
        ),
        "fetch_homepage_or_docs": ReplayToolDefinition(
            family="homepage",
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string", "minLength": 8}},
                "required": ["url"],
                "additionalProperties": False,
            },
            availability=lambda candidate: bool(candidate.canonical_url),
            authorizer=lambda candidate, arguments: arguments.get("url")
            == candidate.canonical_url,
        ),
        "web_search": ReplayToolDefinition(
            family="web",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string", "minLength": 1}},
                "required": ["query"],
                "additionalProperties": False,
            },
            availability=lambda _candidate: True,
            authorizer=lambda _candidate, _arguments: True,
        ),
    }


def _project_replay_result(
    tool_name: str,
    family: str,
    result: dict[str, Any],
    observation_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    status = str(result.get("status") or "error")
    text = str(
        result.get("excerpt")
        or result.get("snippet")
        or result.get("error")
        or ""
    )
    if isinstance(result.get("results"), list):
        text = " ".join(
            str(row.get("snippet") or row.get("title") or "")
            for row in result["results"]
            if isinstance(row, dict)
        )
    return {
        "observation_id": observation_id,
        "tool": tool_name,
        "family": family,
        "status": status,
        "trust": "external_untrusted",
        "provenance": sanitize_contract_value(arguments),
        "facts": {
            "http_status": result.get("http_status"),
            "error": result.get("error"),
        },
        "excerpt": text[:2_000],
        "truncated": len(text) > 2_000,
        "relevant_axes": [
            "workflow_shift",
            "technical_substance",
            "product_market_fit",
            "momentum",
            "confidence",
        ],
    }


def _candidate_group(case: Layer2EvalCase) -> CandidateGroup:
    candidate = dict(case.model_input["candidate"])
    name = str(candidate.get("name") or case.case_id)
    link = str(candidate.get("canonical_link") or "")
    repo_key = _repo_key_from_url(link)
    domain = re.sub(r"^www\.", "", re.sub(r"^https?://", "", link).split("/", 1)[0])
    canonical_key = (
        f"github:{repo_key}"
        if repo_key
        else (f"domain:{domain}" if domain and "." in domain else f"name:{case.case_id}")
    )
    entity_id = f"eval:{case.case_id}"
    evidence_rows: list[dict[str, Any]] = []
    for index, row in enumerate(candidate.get("evidence_rows") or (), start=1):
        if not isinstance(row, dict):
            continue
        evidence_rows.append(
            {
                "id": row.get("id") or index,
                "evidence_ref": f"evidence:{case.case_id}:{index}",
                "entity_id": entity_id,
                "source": row.get("source") or row.get("family") or "eval",
                "family": row.get("family") or "eval",
                "metric_name": row.get("metric_name") or row.get("metric") or "fact",
                "metric_value": row.get("metric_value") or row.get("value"),
                "label": row.get("label") or "Recorded candidate evidence",
                "source_url": row.get("source_url") or link,
                "trust": "external_untrusted",
            }
        )
    context_parts = [
        str(candidate.get(key) or "")
        for key in (
            "summary",
            "readme_context",
            "homepage_context",
            "description",
        )
    ]
    workflow = candidate.get("workflow_evidence")
    if isinstance(workflow, list):
        context_parts.extend(str(item) for item in workflow)
    context_preview = "\n".join(part for part in context_parts if part)
    source_families = sorted(
        {
            str(row.get("family") or "")
            for row in evidence_rows
            if str(row.get("family") or "")
        }
    )
    if repo_key:
        source_families.append("github")
    return CandidateGroup(
        group_id=case.case_id,
        canonical_entity_id=entity_id,
        canonical_name=name,
        canonical_key=canonical_key,
        canonical_link=link,
        member_entity_ids=[entity_id],
        level="potential",
        source_families=sorted(set(source_families)),
        evidence_hash=case.case_id,
        context={
            "members": [
                {
                    "entity_id": entity_id,
                    "name": name,
                    "canonical_url": link,
                    "context_preview": context_preview,
                    "binding_confidence": "high",
                }
            ],
            "evidence_rows": evidence_rows,
            "candidate_input": candidate,
            "source_families": sorted(set(source_families)),
            "needs_momentum_verification": bool(
                candidate.get("needs_momentum_verification")
            ),
        },
    )


def _repo_key_from_url(url: str) -> str | None:
    match = re.match(r"^https?://(?:www\.)?github\.com/([^/]+)/([^/#?]+)", url, re.I)
    if not match:
        return None
    return f"{match.group(1)}/{match.group(2).removesuffix('.git')}"


def _json_safe_scoring_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: sanitize_contract_value(value)
        for key, value in result.items()
        if key != "group"
    }


def _evidence_catalog(scoring_calls: list[dict[str, Any]]) -> dict[str, str]:
    catalog: dict[str, str] = {}
    for call in scoring_calls:
        candidate = call.get("input_payload", {}).get("candidate", {})
        for row in candidate.get("top_evidence", []):
            if not isinstance(row, Mapping):
                continue
            reference = str(row.get("evidence_id") or row.get("evidence_ref") or "")
            if not reference:
                continue
            catalog[reference] = " ".join(
                str(row.get(key) or "")
                for key in ("label", "metric_name", "metric_value", "source", "family")
            ).strip()
    return catalog


def _model_call_rows(conn: sqlite3.Connection, feed_run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select component, task, turn_index, attempt, provider, model,
               request_fingerprint, prompt_version, output_schema_version,
               tool_registry_version, context_policy_version, status, latency_ms,
               prompt_tokens, completion_tokens, cached_input_tokens, total_tokens,
               temperature, max_output_tokens
        from l2_model_calls
        where feed_run_id = ?
        order by id
        """,
        (feed_run_id,),
    ).fetchall()
    keys = (
        "component",
        "task",
        "turn_index",
        "attempt",
        "provider",
        "model",
        "request_fingerprint",
        "prompt_version",
        "output_schema_version",
        "tool_registry_version",
        "context_policy_version",
        "status",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "total_tokens",
        "temperature",
        "max_output_tokens",
    )
    return [dict(zip(keys, row)) for row in rows]


def _aggregate_telemetry(
    model_calls: list[dict[str, Any]],
    request_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    def nullable_sum(field: str) -> int | None:
        values = [row.get(field) for row in model_calls]
        present = [int(value) for value in values if value is not None]
        return sum(present) if present else None

    costs = [
        _cost_amount(record.get("cost"))
        for record in request_records or []
        if record.get("cost") is not None
    ]
    present_costs = [value for value in costs if value is not None]
    return {
        "input_tokens": nullable_sum("input_tokens"),
        "output_tokens": nullable_sum("output_tokens"),
        "cached_input_tokens": nullable_sum("cached_input_tokens"),
        "total_tokens": nullable_sum("total_tokens"),
        "latency_ms": sum(int(row.get("latency_ms") or 0) for row in model_calls),
        "cost": {
            "amount": round(sum(present_costs), 8) if present_costs else None,
            "currency": "USD",
            "source": "provider_reported" if present_costs else "missing",
        },
    }


def _cost_amount(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, Mapping):
        for key in ("amount", "cost", "total_cost", "usd"):
            if value.get(key) is not None:
                try:
                    return float(value[key])
                except (TypeError, ValueError):
                    return None
    return None


def _tool_family(name: str) -> str:
    definition = _replay_tool_definitions().get(name)
    return definition.family if definition else "unknown"


def _stable_hash(value: Any) -> str:
    import hashlib

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _budget_metadata(config: PairedEvalConfig) -> dict[str, Any]:
    return {
        "max_investigation_turns": config.limits.max_investigation_turns,
        "max_scoring_attempts": config.limits.max_scoring_attempts,
        "max_tool_calls_per_candidate": config.limits.max_tool_calls_per_candidate,
        "max_web_search_calls_per_candidate": config.limits.max_web_search_calls_per_candidate,
        "max_github_file_calls_per_candidate": config.limits.max_github_file_calls_per_candidate,
        "max_homepage_fetches_per_candidate": config.limits.max_homepage_fetches_per_candidate,
        "max_tool_result_chars": config.limits.max_tool_result_chars,
        "max_parallel_tool_calls_per_turn": config.limits.max_parallel_tool_calls_per_turn,
        "max_context_tokens": config.context_budget.max_context_tokens,
        "output_reserve": config.context_budget.output_reserve,
        "safety_margin": config.context_budget.safety_margin,
        "identity_allocation": config.context_budget.identity_allocation,
        "evidence_summary_allocation": config.context_budget.evidence_summary_allocation,
        "top_evidence_allocation": config.context_budget.top_evidence_allocation,
        "previous_turn_allocation": config.context_budget.previous_turn_allocation,
        "tool_observation_allocation": config.context_budget.tool_observation_allocation,
        "recent_raw_tool_result_count": config.context_budget.recent_raw_tool_result_count,
    }


def _provider_profile(provider: Any) -> dict[str, Any]:
    return sanitize_contract_value(
        {
            "provider": str(getattr(provider, "provider_name", "")),
            "model": str(getattr(provider, "model", "")),
            "actual_temperature": getattr(provider, "actual_temperature", None),
            "max_output_tokens": getattr(provider, "max_output_tokens", None),
            "response_format": getattr(provider, "response_format", None),
        }
    )


def _provider_cache_isolation(
    provider: Any, *, cache_namespaces: set[str]
) -> dict[str, str]:
    mode = str(getattr(provider, "eval_cache_mode", "")).strip().lower()
    if mode == "disabled":
        return {"provider_cache": "disabled", "database": "isolated_in_memory"}
    namespace = str(getattr(provider, "eval_cache_namespace", "")).strip()
    if mode == "isolated_namespace" and namespace:
        if namespace in cache_namespaces:
            raise ValueError("eval_cache_namespace must be unique for every trial")
        cache_namespaces.add(namespace)
        return {
            "provider_cache": "isolated_namespace",
            "namespace": namespace,
            "database": "isolated_in_memory",
        }
    raise ValueError(
        "provider must declare eval_cache_mode='disabled' or a unique "
        "eval_cache_mode='isolated_namespace' with eval_cache_namespace"
    )
