from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from pipeline.decision.layer2_context_builder import ContextBudget
from pipeline.decision.layer2_eval_grading import grade_eval_artifact
from pipeline.decision.layer2_eval_io import atomic_write_text
from pipeline.decision.layer2_eval_reporting import (
    aggregate_results,
    render_report,
    write_blind_briefs,
)
from pipeline.decision.layer2_harness import TelemetryLLMProvider, sanitize_text
from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.layer2_scoring_investigator import (
    BRIEF_CONTEXT_POLICY_VERSION,
    BRIEF_OUTPUT_SCHEMA_VERSION,
    DEFAULT_BRIEF_PROMPT_VERSION,
    DEFAULT_INVESTIGATOR_PROMPT_VERSION,
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


CASE_CONTRACT_VERSION = "layer2-eval-case-v2"
SUPPORTED_CASE_CONTRACT_VERSIONS = {
    "layer2-eval-case-v1",
    CASE_CONTRACT_VERSION,
}
REPLAY_MATCHER_VERSION = "layer2-replay-match-v2"
RELEASE_DATASET_VERSION = "layer2-scoring-cases-v2"
RELEASE_DATASET_FINGERPRINT = (
    "27007e0e489a46e40399e43fe5eb978490324ad9bab5d6875ad80c0904f622a4"
)
RELEASE_GRADER_VERSION = "layer2-graders-v2"
RELEASE_CASE_IDS = (
    "openclaw",
    "hermes-agent",
    "heyclicky",
    "generic-ai-chatbot",
    "funding-acquisition-news",
    "standalone-model-release",
    "tutorial-resource-list",
    "ordinary-dashboard-utility",
    "screen-aware-spreadsheet-operator",
    "readme-gated-workflow-engine",
    "manifest-gated-mcp-runner",
    "unresolved-project-atlas",
    "independent-adoption-evidence-needed",
    "readme-prompt-injection-repository",
    "homepage-prompt-injection-product",
    "search-result-prompt-injection",
    "missing-manifest-returns-404",
    "private-repository-returns-403",
    "homepage-fetch-rate-limited",
    "viral-ai-wrapper-launch",
)


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
class V2EvalConfig:
    prompt_version: str = DEFAULT_INVESTIGATOR_PROMPT_VERSION
    trials: int = 3
    include_briefs: bool = True
    limits: InvestigatorLimits = InvestigatorLimits()
    context_budget: ContextBudget = ContextBudget()
    direct_final_enabled: bool = False
    output_schema_version: str = SCORING_OUTPUT_SCHEMA_VERSION
    tool_registry_version: str = TOOL_REGISTRY_VERSION
    grader_version: str = RELEASE_GRADER_VERSION

    def __post_init__(self) -> None:
        if self.prompt_version != DEFAULT_INVESTIGATOR_PROMPT_VERSION:
            raise ValueError(
                "the production evaluation supports the current V2 prompt only"
            )
        if int(self.trials) < 1:
            raise ValueError("evaluation requires at least one uncached trial")
        if self.direct_final_enabled:
            raise ValueError(
                "production V2 evaluation requires direct_final_enabled=false"
            )


@dataclass(frozen=True)
class EvalOutputPaths:
    root: Path
    results_jsonl: Path
    attempts_jsonl: Path
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
        self._authorized_defaults: dict[str, dict[str, Any]] = {}
        self._authorized_query_terms = _candidate_query_terms(case)
        for recording in case.replay["tools"]:
            if not isinstance(recording, dict):
                raise DatasetContractError(
                    f"{case.case_id}: replay tool recording must be an object"
                )
            _require_exact_keys(
                recording,
                {"tool", "arguments", "result", "match"}
                if "match" in recording
                else {"tool", "arguments", "result"},
                f"{case.case_id}:replay.tools",
            )
            tool_name = str(recording["tool"])
            arguments = sanitize_contract_value(recording["arguments"])
            result = sanitize_contract_value(recording["result"])
            match = str(recording.get("match") or "exact")
            if match == "authorized_case_default":
                if tool_name != "web_search":
                    raise DatasetContractError(
                        f"{case.case_id}: only web_search may use an authorized case default"
                    )
                if tool_name in self._authorized_defaults:
                    raise DatasetContractError(
                        f"{case.case_id}: duplicate authorized default for {tool_name}"
                    )
                self._authorized_defaults[tool_name] = result
                continue
            if match != "exact":
                raise DatasetContractError(
                    f"{case.case_id}: unsupported replay match policy {match!r}"
                )
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
        if result is None and (
            tool_name != "web_search"
            or _query_is_candidate_bound(
                str(arguments.get("query") or ""), self._authorized_query_terms
            )
        ):
            result = self._authorized_defaults.get(tool_name)
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
        response: dict[str, Any] | None = None
        try:
            response = self._provider.complete_json(
                task=task,
                prompt_version=prompt_version,
                input_payload=input_payload,
                system_prompt=system_prompt,
            )
            return response
        finally:
            usage = getattr(self._provider, "last_usage", None)
            if usage is not None:
                record["usage"] = sanitize_contract_value(usage)
            attempts = getattr(self._provider, "last_attempts", None)
            if attempts is not None:
                record["provider_attempts"] = sanitize_contract_value(attempts)
            reported_cost = getattr(self._provider, "last_cost", None)
            if reported_cost is None and isinstance(response, Mapping):
                reported_cost = response.get("_cost") or response.get("cost")
            if reported_cost is not None:
                record["cost"] = sanitize_contract_value(reported_cost)


class _UnavailableEvalProvider:
    provider_name = "unavailable"
    model = ""
    eval_cache_mode = "disabled"
    actual_temperature = None
    max_output_tokens = None
    response_format = None


def run_eval_case(
    case: Layer2EvalCase,
    *,
    provider: Any,
    prompt_version: str,
    trial: int,
    include_brief: bool = True,
    limits: InvestigatorLimits | None = None,
    context_budget: ContextBudget | None = None,
    direct_final_enabled: bool = False,
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
    tool_policy = str(case.grader.get("tool_policy") or "required")
    if tool_policy == "forbidden":
        active_tools: dict[str, Any] = {}
        active_specs: dict[str, ToolSpec] = {}
    else:
        allowed_names = set(case.gold.get("allowed_tool_names") or replay.tools)
        active_tools = {
            name: tool for name, tool in replay.tools.items() if name in allowed_names
        }
        active_specs = {
            name: spec for name, spec in replay.specs.items() if name in allowed_names
        }
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
    result: dict[str, Any] | None = None
    scoring_error: dict[str, Any] | None = None
    try:
        try:
            result = score_with_investigator(
                conn,
                feed_run_id=feed_run_id,
                groups=[_candidate_group(case)],
                provider=telemetry_provider,
                tools=active_tools,
                tool_specs=active_specs,
                limits=limits or InvestigatorLimits(),
                context_budget=context_budget or ContextBudget(),
                direct_final_enabled=direct_final_enabled,
                prompt_version=prompt_version,
                output_schema_version=output_schema_version,
                tool_registry_version=tool_registry_version,
            )[0]
        except Exception as exc:
            scoring_error = _eval_error("scoring", exc)
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
        investigation = _investigation_artifact(conn, feed_run_id, case.case_id)
        trace = (
            list(result.get("trace") or [])
            if result is not None
            else list(investigation.get("trace") or [])
        )
        tool_trace = (
            list(result.get("tool_trace") or [])
            if result is not None
            else list(investigation.get("tool_trace") or [])
        )
        observations = (
            list(result.get("observations") or [])
            if result is not None
            else list(investigation.get("observations") or [])
        )
        context_manifests = (
            list(result.get("context_manifests") or [])
            if result is not None
            else list(investigation.get("context_manifests") or [])
        )
        brief_artifact = None
        brief_error: dict[str, Any] | None = None
        if (
            result is not None
            and include_brief
            and bool(case.model_input["requires_brief"])
        ):
            before = len(captured.calls)
            try:
                brief_result = build_deepdive_brief(
                    row=result,
                    provider=telemetry_provider,
                    prompt_version=brief_prompt_version,
                )
                brief_call = captured.calls[before]
                brief_artifact = {
                    "input": brief_call["input_payload"],
                    "output": brief_result["brief"],
                    "output_valid": True,
                    "prompt_version": brief_prompt_version,
                    "output_schema_version": BRIEF_OUTPUT_SCHEMA_VERSION,
                    "context_policy_version": BRIEF_CONTEXT_POLICY_VERSION,
                    "provider": captured.provider_name,
                    "model": captured.model,
                    "cache_key": brief_result["cache_key"],
                    "error": None,
                }
            except Exception as exc:
                brief_error = _eval_error("brief", exc)
                brief_call = (
                    captured.calls[before]
                    if before < len(captured.calls)
                    else {"input_payload": {}}
                )
                brief_artifact = {
                    "input": brief_call.get("input_payload") or {},
                    "output": None,
                    "output_valid": False,
                    "prompt_version": brief_prompt_version,
                    "output_schema_version": BRIEF_OUTPUT_SCHEMA_VERSION,
                    "context_policy_version": BRIEF_CONTEXT_POLICY_VERSION,
                    "provider": captured.provider_name,
                    "model": captured.model,
                    "cache_key": "",
                    "error": brief_error,
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
        execution_error = scoring_error or brief_error
        final_output_valid = result is not None
        artifact = {
            "artifact_version": "layer2-eval-result-v2",
            "case_id": case.case_id,
            "trial": int(trial),
            "prompt_version": prompt_version,
            "provider": captured.provider_name,
            "model": captured.model,
            "dataset_version": RELEASE_DATASET_VERSION,
            "recording_version": replay.recording_version,
            "replay_matcher_version": REPLAY_MATCHER_VERSION,
            "grader_version": RELEASE_GRADER_VERSION,
            "output_schema_version": output_schema_version,
            "context_policy_version": SCORING_CONTEXT_POLICY_VERSION,
            "tool_registry_version": tool_registry_version,
            "preflight_mode": preflight_mode,
            "route": (
                classify_scored_route(result)
                if result is not None
                else "candidate_error"
            ),
            "score": result.get("l2_score") if result is not None else None,
            "result": _json_safe_scoring_result(result) if result is not None else {},
            "trace": trace,
            "tool_trace": [
                {**row, "eval_family": _tool_family(str(row.get("tool") or ""))}
                for row in tool_trace
            ],
            "raw_tool_results": list(investigation.get("raw_tool_results") or []),
            "observations": observations,
            "evidence_catalog": _evidence_catalog(scoring_calls),
            "context_manifests": context_manifests,
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
            "execution_status": "error" if execution_error else "ok",
            "final_output_valid": final_output_valid,
            "brief_output_valid": (
                None
                if not include_brief or not bool(case.model_input["requires_brief"])
                else brief_error is None and brief_artifact is not None
            ),
            "error": execution_error,
            "request_fingerprints": [row["request_fingerprint"] for row in model_calls],
            "request_records": captured.calls,
            "model_calls": model_calls,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "brief": brief_artifact,
        }
        artifact["telemetry"] = _aggregate_telemetry(model_calls, captured.calls)
        try:
            artifact["grades"] = grade_eval_artifact(case, artifact)
        except Exception as exc:
            grading_error = _eval_error("grading", exc)
            artifact["execution_status"] = "error"
            artifact["error"] = artifact.get("error") or grading_error
            artifact["grades"] = {
                "grading_execution": {
                    "passed": False,
                    "error": grading_error,
                }
            }
        artifact["grades"]["execution"] = {
            "passed": artifact["execution_status"] == "ok",
            "status": artifact["execution_status"],
            "error": artifact.get("error"),
        }
        artifact["passed"] = all(
            grade.get("passed", False)
            or grade.get("release_blocking") is False
            for grade in artifact["grades"].values()
        )
        return sanitize_contract_value(artifact)
    finally:
        conn.close()


def run_v2_evaluation(
    dataset: Layer2EvalDataset,
    *,
    provider_factory: Callable[[int, Layer2EvalCase], Any],
    output_dir: str | Path,
    config: V2EvalConfig | None = None,
    resume: bool = False,
    retry_execution_errors: bool = False,
    allow_code_change: bool = False,
    allow_provider_profile_change: bool = False,
    provider_execution: str = "test_provider",
) -> EvalOutputPaths:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    with _output_lock(root):
        return _run_v2_evaluation_locked(
            dataset,
            provider_factory=provider_factory,
            output_dir=root,
            config=config,
            resume=resume,
            retry_execution_errors=retry_execution_errors,
            allow_code_change=allow_code_change,
            allow_provider_profile_change=allow_provider_profile_change,
            provider_execution=provider_execution,
        )


def _run_v2_evaluation_locked(
    dataset: Layer2EvalDataset,
    *,
    provider_factory: Callable[[int, Layer2EvalCase], Any],
    output_dir: str | Path,
    config: V2EvalConfig | None = None,
    resume: bool = False,
    retry_execution_errors: bool = False,
    allow_code_change: bool = False,
    allow_provider_profile_change: bool = False,
    provider_execution: str = "test_provider",
) -> EvalOutputPaths:
    """Run the V2 production component profile with per-slot checkpoints.

    A slot is one ``case_id`` and trial pair. Successful and failed slots are
    both terminal artifacts, so one provider or validation failure cannot erase
    earlier spend or stop later cases. ``retry_execution_errors`` replaces only
    previously checkpointed execution-error slots during a resumed run.
    """

    active = config or V2EvalConfig()
    if provider_execution not in {"real_kimi", "test_provider"}:
        raise ValueError("provider_execution must be real_kimi or test_provider")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths = EvalOutputPaths(
        root=root,
        results_jsonl=root / "results.v1.jsonl",
        attempts_jsonl=root / "attempts.v2.jsonl",
        aggregate_json=root / "aggregate.v1.json",
        report_markdown=root / "report.md",
        run_metadata_json=root / "run-metadata.v1.json",
        blind_briefs_jsonl=root / "brief-review.v2.jsonl",
        blind_mapping_json=root / "brief-review-mapping.v2.json",
    )
    git_sha = _git_sha()
    code_fingerprint = _eval_code_fingerprint()
    run_started = datetime.now(timezone.utc).isoformat()
    dataset_fingerprint = _stable_hash(
        {
            "version": dataset.version,
            "cases": [
                {
                    "case_id": case.case_id,
                    "model_input": case.model_input,
                    "replay": case.replay,
                    "gold": case.gold,
                    "grader": case.grader,
                    "human_review": case.human_review,
                }
                for case in dataset.cases
            ],
        }
    )
    config_fingerprint = _stable_hash(
        {
            "prompt_version": active.prompt_version,
            "trials": active.trials,
            "include_briefs": active.include_briefs,
            "direct_final_enabled": active.direct_final_enabled,
            "output_schema_version": active.output_schema_version,
            "tool_registry_version": active.tool_registry_version,
            "grader_version": active.grader_version,
            "budgets": _budget_metadata(active),
            "provider_execution": provider_execution,
            "replay_matcher_version": REPLAY_MATCHER_VERSION,
        }
    )
    release_eligible = (
        provider_execution == "real_kimi"
        and dataset.version == RELEASE_DATASET_VERSION
        and dataset_fingerprint == RELEASE_DATASET_FINGERPRINT
        and tuple(case.case_id for case in dataset.cases) == RELEASE_CASE_IDS
        and active == V2EvalConfig()
    )
    run_scope = (
        "release_full"
        if release_eligible
        else "partial_debug"
        if provider_execution == "real_kimi"
        else "test"
    )
    existing_metadata: dict[str, Any] = {}
    results: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    if resume:
        if not paths.run_metadata_json.exists():
            raise ValueError("--resume requires an existing run-metadata.v1.json")
        existing_metadata = json.loads(
            paths.run_metadata_json.read_text(encoding="utf-8")
        )
        if existing_metadata.get("dataset_fingerprint") != dataset_fingerprint:
            raise ValueError("resume dataset fingerprint does not match checkpoint")
        if existing_metadata.get("config_fingerprint") != config_fingerprint:
            raise ValueError("resume config fingerprint does not match checkpoint")
        previous_code_fingerprint = str(
            existing_metadata.get("code_fingerprint") or ""
        )
        if previous_code_fingerprint != code_fingerprint and not allow_code_change:
            raise ValueError(
                "resume code fingerprint does not match checkpoint; use the explicit "
                "allow-code-change override only for an audited bug fix"
            )
        if paths.results_jsonl.exists():
            results = _read_jsonl(paths.results_jsonl)
        if paths.attempts_jsonl.exists():
            attempts = _read_jsonl(paths.attempts_jsonl)
        elif results:
            raise ValueError("resume checkpoint is missing attempts.v2.jsonl")
    elif paths.results_jsonl.exists() or paths.run_metadata_json.exists():
        raise ValueError("output directory already contains an eval; use --resume")
    run_id = str(existing_metadata.get("run_id") or "") or (
        "l2eval-v2-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + config_fingerprint[:8]
    )
    run_started = str(existing_metadata.get("started_at") or run_started)
    if resume:
        _validate_resume_artifacts(
            dataset,
            active,
            results=results,
            attempts=attempts,
            metadata=existing_metadata,
        )
    expected_slots = len(dataset.cases) * int(active.trials)
    code_revisions = list(existing_metadata.get("code_revisions") or [])
    if not code_revisions:
        code_revisions = [
            {
                "code_fingerprint": code_fingerprint,
                "git_sha": git_sha,
                "recorded_at": run_started,
                "reason": "initial_run",
            }
        ]
    elif str(existing_metadata.get("code_fingerprint") or "") != code_fingerprint:
        code_revisions.append(
            {
                "code_fingerprint": code_fingerprint,
                "git_sha": git_sha,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "reason": "audited_bug_fix_resume",
            }
        )
    metadata = {
        "artifact_version": "layer2-eval-run-v2",
        "run_id": run_id,
        "status": "running",
        "started_at": run_started,
        "completed_at": None,
        "dataset_version": dataset.version,
        "dataset_fingerprint": dataset_fingerprint,
        "config_fingerprint": config_fingerprint,
        "grader_version": active.grader_version,
        "git_sha": git_sha,
        "code_fingerprint": code_fingerprint,
        "code_revisions": code_revisions,
        "prompt_version": active.prompt_version,
        "output_schema_version": active.output_schema_version,
        "context_policy_version": SCORING_CONTEXT_POLICY_VERSION,
        "tool_registry_version": active.tool_registry_version,
        "recording_versions": sorted(
            {str(case.replay["recording_version"]) for case in dataset.cases}
        ),
        "replay_matcher_version": REPLAY_MATCHER_VERSION,
        "trials": active.trials,
        "expected_slots": expected_slots,
        "completed_slots": len(results),
        "attempt_count": len(attempts),
        "budgets": _budget_metadata(active),
        "cache_isolation": "provider-declared plus isolated in-memory database",
        "provider_execution": provider_execution,
        "run_scope": run_scope,
        "release_eligible": release_eligible,
        "provider_profile": existing_metadata.get("provider_profile"),
        "provider_profile_revisions": list(
            existing_metadata.get("provider_profile_revisions") or []
        ),
    }
    _atomic_write_json(paths.run_metadata_json, metadata)
    provider_instances: list[Any] = []
    cache_namespaces: set[str] = {
        str(row.get("cache_isolation", {}).get("namespace"))
        for row in attempts
        if str(row.get("cache_isolation", {}).get("namespace") or "")
    }
    expected_provider_profile = (
        existing_metadata.get("provider_profile")
        if isinstance(existing_metadata.get("provider_profile"), dict)
        else None
    )
    completed = {
        (str(row.get("case_id")), int(row.get("trial") or 0)): row
        for row in results
    }
    for case in dataset.cases:
        case_fingerprint = _stable_hash(case.model_input)
        replay_fingerprint = _stable_hash(case.replay)
        for trial in range(1, int(active.trials) + 1):
            previous = completed.get((case.case_id, trial))
            if previous is not None and not (
                retry_execution_errors
                and str(previous.get("execution_status") or "") != "ok"
            ):
                continue
            try:
                provider = provider_factory(trial, case)
            except Exception as exc:
                provider = _UnavailableEvalProvider()
                cache_isolation = {
                    "provider_cache": "unavailable",
                    "database": "not_started",
                }
                provider_profile = _provider_profile(provider)
                artifact = _runner_error_artifact(
                    case,
                    provider=provider,
                    trial=trial,
                    prompt_version=active.prompt_version,
                    error=exc,
                    stage="provider_factory",
                )
            else:
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
                    if resume and allow_provider_profile_change:
                        revisions = list(metadata["provider_profile_revisions"])
                        if not revisions:
                            revisions.append(
                                {
                                    "provider_profile": expected_provider_profile,
                                    "recorded_at": run_started,
                                    "reason": "initial_run",
                                }
                            )
                        revisions.append(
                            {
                                "provider_profile": provider_profile,
                                "recorded_at": datetime.now(timezone.utc).isoformat(),
                                "reason": "audited_bug_fix_resume",
                            }
                        )
                        metadata["provider_profile_revisions"] = revisions
                        expected_provider_profile = provider_profile
                    else:
                        raise ValueError(
                            "V2 evaluation provider/model/output settings must be identical"
                        )
                metadata["provider_profile"] = expected_provider_profile
                _atomic_write_json(paths.run_metadata_json, metadata)
                try:
                    artifact = run_eval_case(
                        case,
                        provider=provider,
                        prompt_version=active.prompt_version,
                        trial=trial,
                        include_brief=active.include_briefs,
                        limits=active.limits,
                        context_budget=active.context_budget,
                        direct_final_enabled=active.direct_final_enabled,
                        output_schema_version=active.output_schema_version,
                        tool_registry_version=active.tool_registry_version,
                    )
                except Exception as exc:
                    artifact = _runner_error_artifact(
                        case,
                        provider=provider,
                        trial=trial,
                        prompt_version=active.prompt_version,
                        error=exc,
                    )
            artifact.update(
                {
                    "dataset_version": dataset.version,
                    "dataset_fingerprint": dataset_fingerprint,
                    "config_fingerprint": config_fingerprint,
                    "grader_version": active.grader_version,
                    "output_schema_version": active.output_schema_version,
                    "tool_registry_version": active.tool_registry_version,
                    "replay_matcher_version": REPLAY_MATCHER_VERSION,
                    "git_sha": git_sha,
                    "code_fingerprint": code_fingerprint,
                    "run_id": run_id,
                    "case_input_fingerprint": case_fingerprint,
                    "replay_fingerprint": replay_fingerprint,
                    "cache_isolation": cache_isolation,
                    "provider_profile": provider_profile,
                    "budgets": _budget_metadata(active),
                }
            )
            prior_attempts = [
                row
                for row in attempts
                if row.get("case_id") == case.case_id
                and int(row.get("trial") or 0) == trial
            ]
            artifact["execution_attempt"] = len(prior_attempts) + 1
            attempts.append(artifact)
            completed[(case.case_id, trial)] = artifact
            results = _ordered_results(dataset, active, completed)
            _atomic_write_results(paths.attempts_jsonl, attempts)
            _atomic_write_results(paths.results_jsonl, results)
            metadata["completed_slots"] = len(results)
            metadata["attempt_count"] = len(attempts)
            metadata["provider_profile"] = expected_provider_profile
            metadata["last_completed"] = {
                "case_id": case.case_id,
                "trial": trial,
                "execution_status": artifact.get("execution_status"),
            }
            _atomic_write_json(paths.run_metadata_json, metadata)
    aggregate = aggregate_results(dataset, active, results, attempts=attempts)
    aggregate["run_id"] = run_id
    aggregate["provider_execution"] = provider_execution
    aggregate["run_scope"] = run_scope
    aggregate["release_eligible"] = release_eligible
    _atomic_write_json(paths.aggregate_json, aggregate)
    atomic_write_text(
        paths.report_markdown, render_report(dataset, active, results, aggregate)
    )
    write_blind_briefs(paths, results)
    metadata.update(
        {
            "status": "complete",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "completed_slots": len(results),
            "execution_failures": aggregate["execution_failures"],
            "all_passed": aggregate["all_passed"],
            "provider_profile": expected_provider_profile,
        }
    )
    _atomic_write_json(paths.run_metadata_json, metadata)
    return paths


def _eval_error(stage: str, exc: BaseException) -> dict[str, str]:
    return {
        "stage": str(stage),
        "type": type(exc).__name__,
        "message": sanitize_text(exc, max_chars=800),
    }


def _investigation_artifact(
    conn: sqlite3.Connection,
    feed_run_id: str,
    group_id: str,
) -> dict[str, Any]:
    row = conn.execute(
        """
        select status, trace_json, tool_trace_json, observation_trace_json,
               context_manifests_json, raw_tool_results_json
        from l2_scoring_investigations
        where feed_run_id = ? and group_id = ?
        """,
        (feed_run_id, group_id),
    ).fetchone()
    if row is None:
        return {
            "status": "missing",
            "trace": [],
            "tool_trace": [],
            "observations": [],
            "context_manifests": [],
            "raw_tool_results": [],
        }
    return {
        "status": str(row[0]),
        "trace": _json_list(row[1]),
        "tool_trace": _json_list(row[2]),
        "observations": _json_list(row[3]),
        "context_manifests": _json_list(row[4]),
        "raw_tool_results": _json_list(row[5]),
    }


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _runner_error_artifact(
    case: Layer2EvalCase,
    *,
    provider: Any,
    trial: int,
    prompt_version: str,
    error: BaseException,
    stage: str = "runner",
) -> dict[str, Any]:
    execution_error = _eval_error(stage, error)
    artifact: dict[str, Any] = {
        "artifact_version": "layer2-eval-result-v2",
        "case_id": case.case_id,
        "trial": int(trial),
        "prompt_version": prompt_version,
        "provider": str(getattr(provider, "provider_name", "")),
        "model": str(getattr(provider, "model", "")),
        "recording_version": str(case.replay.get("recording_version") or ""),
        "replay_matcher_version": REPLAY_MATCHER_VERSION,
        "preflight_mode": "runner_error",
        "route": "candidate_error",
        "score": None,
        "result": {},
        "trace": [],
        "tool_trace": [],
        "raw_tool_results": [],
        "observations": [],
        "evidence_catalog": {},
        "context_manifests": [],
        "repair_count": 0,
        "turns": 0,
        "must_finalize_turns": [],
        "execution_status": "error",
        "final_output_valid": False,
        "brief_output_valid": None,
        "error": execution_error,
        "request_fingerprints": [],
        "request_records": [],
        "model_calls": [],
        "latency_ms": 0,
        "brief": None,
        "telemetry": _aggregate_telemetry([], []),
    }
    try:
        artifact["grades"] = grade_eval_artifact(case, artifact)
    except Exception as exc:
        artifact["grades"] = {
            "grading_execution": {
                "passed": False,
                "error": _eval_error("grading", exc),
            }
        }
    artifact["grades"]["execution"] = {
        "passed": False,
        "status": "error",
        "error": execution_error,
    }
    artifact["passed"] = False
    return sanitize_contract_value(artifact)


def _ordered_results(
    dataset: Layer2EvalDataset,
    config: V2EvalConfig,
    completed: Mapping[tuple[str, int], dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        completed[(case.case_id, trial)]
        for case in dataset.cases
        for trial in range(1, int(config.trials) + 1)
        if (case.case_id, trial) in completed
    ]


def _atomic_write_results(path: Path, rows: list[dict[str, Any]]) -> None:
    atomic_write_text(
        path,
        "".join(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
            for row in rows
        ),
    )


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    atomic_write_text(
        path,
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
    )


@contextmanager
def _output_lock(root: Path):
    import fcntl

    lock_path = root / ".eval.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another eval process is using this output directory") from exc
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"corrupt checkpoint {path.name}:{line_number}: {exc.msg}"
            ) from exc
        if not isinstance(row, dict):
            raise ValueError(
                f"corrupt checkpoint {path.name}:{line_number}: expected object"
            )
        rows.append(row)
    return rows


def _validate_resume_artifacts(
    dataset: Layer2EvalDataset,
    config: V2EvalConfig,
    *,
    results: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    metadata: Mapping[str, Any],
) -> None:
    by_case = {case.case_id: case for case in dataset.cases}
    run_id = str(metadata.get("run_id") or "")
    provider_profile = metadata.get("provider_profile")
    allowed_provider_profiles = [provider_profile]
    allowed_provider_profiles.extend(
        row.get("provider_profile")
        for row in metadata.get("provider_profile_revisions", [])
        if isinstance(row, Mapping)
    )
    allowed_code_fingerprints = {
        str(row.get("code_fingerprint") or "")
        for row in metadata.get("code_revisions", [])
        if isinstance(row, Mapping)
    }
    allowed_code_fingerprints.add(str(metadata.get("code_fingerprint") or ""))
    attempt_keys: set[tuple[str, int, int]] = set()
    cache_namespaces: set[str] = set()
    attempts_by_slot: dict[tuple[str, int], list[dict[str, Any]]] = {}

    def validate_common(row: Mapping[str, Any], *, location: str) -> tuple[str, int]:
        case_id = str(row.get("case_id") or "")
        trial = int(row.get("trial") or 0)
        case = by_case.get(case_id)
        if case is None or not 1 <= trial <= int(config.trials):
            raise ValueError(f"{location}: unknown case/trial slot")
        expected = {
            "artifact_version": "layer2-eval-result-v2",
            "run_id": run_id,
            "prompt_version": config.prompt_version,
            "dataset_version": dataset.version,
            "dataset_fingerprint": metadata.get("dataset_fingerprint"),
            "config_fingerprint": metadata.get("config_fingerprint"),
            "grader_version": config.grader_version,
            "output_schema_version": config.output_schema_version,
            "tool_registry_version": config.tool_registry_version,
            "replay_matcher_version": REPLAY_MATCHER_VERSION,
            "case_input_fingerprint": _stable_hash(case.model_input),
            "replay_fingerprint": _stable_hash(case.replay),
        }
        for field, value in expected.items():
            if row.get(field) != value:
                raise ValueError(f"{location}: mismatched {field}")
        row_error = row.get("error") if isinstance(row.get("error"), Mapping) else {}
        if (
            provider_profile is not None
            and row.get("provider_profile") not in allowed_provider_profiles
            and row_error.get("stage") != "provider_factory"
        ):
            raise ValueError(f"{location}: mismatched provider profile")
        if str(row.get("code_fingerprint") or "") not in allowed_code_fingerprints:
            raise ValueError(f"{location}: unknown code fingerprint")
        return case_id, trial

    for index, row in enumerate(attempts, start=1):
        case_id, trial = validate_common(row, location=f"attempts row {index}")
        execution_attempt = int(row.get("execution_attempt") or 0)
        key = (case_id, trial, execution_attempt)
        if execution_attempt < 1 or key in attempt_keys:
            raise ValueError(f"attempts row {index}: invalid execution_attempt")
        attempt_keys.add(key)
        isolation = row.get("cache_isolation")
        if not isinstance(isolation, Mapping):
            raise ValueError(f"attempts row {index}: missing cache isolation proof")
        cache_mode = str(isolation.get("provider_cache") or "")
        if cache_mode not in {"disabled", "isolated_namespace", "unavailable"}:
            raise ValueError(f"attempts row {index}: invalid cache isolation proof")
        if cache_mode == "isolated_namespace":
            namespace = str(isolation.get("namespace") or "")
            if not namespace or namespace in cache_namespaces:
                raise ValueError(f"attempts row {index}: reused cache namespace")
            cache_namespaces.add(namespace)
        attempts_by_slot.setdefault((case_id, trial), []).append(row)
    for slot, rows in attempts_by_slot.items():
        numbers = sorted(int(row["execution_attempt"]) for row in rows)
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError(f"attempt history is non-contiguous for {slot}")

    result_slots: set[tuple[str, int]] = set()
    for index, row in enumerate(results, start=1):
        slot = validate_common(row, location=f"results row {index}")
        if slot in result_slots:
            raise ValueError(f"results row {index}: duplicate case/trial slot")
        result_slots.add(slot)
        slot_attempts = attempts_by_slot.get(slot, [])
        if not slot_attempts:
            raise ValueError(f"results row {index}: missing attempt history")
        latest = max(slot_attempts, key=lambda value: int(value["execution_attempt"]))
        if canonical_json(row) != canonical_json(latest):
            raise ValueError(f"results row {index}: not the latest attempt artifact")


def _eval_code_fingerprint() -> str:
    directory = Path(__file__).resolve().parent
    return _stable_hash(
        {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted(directory.glob("*.py"))
        }
    )


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
        contract_version = str(row["contract_version"])
        if contract_version not in SUPPORTED_CASE_CONTRACT_VERSIONS:
            raise DatasetContractError(
                f"{source}:{line_number}: unsupported contract_version"
            )
        if contract_version == "layer2-eval-case-v1":
            _upgrade_v1_case_contract(row)
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
        _validate_case_alignment(row, source, line_number)
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


def _upgrade_v1_case_contract(row: dict[str, Any]) -> None:
    """Keep schema_smoke and archived V1 corpora readable without mutating files."""

    gold = row["gold"]
    grader = row["grader"]
    gold.setdefault("candidate_relevance", gold.get("score_band", "low"))
    if gold.get("should_print"):
        readiness = "ready"
    elif gold.get("is_product_or_repo"):
        readiness = "insufficient_evidence"
    else:
        readiness = "not_applicable"
    gold.setdefault("publication_readiness", readiness)
    grader.setdefault(
        "tool_policy",
        "required" if gold.get("required_tool_names") else "optional",
    )
    grader.setdefault("forbidden_output_substrings", [])


def _validate_case_alignment(
    row: Mapping[str, Any], source: Path, line_number: int
) -> None:
    if str(row.get("contract_version")) != CASE_CONTRACT_VERSION:
        return
    gold = row["gold"]
    grader = row["grader"]
    replay = row["replay"]
    location = f"{source}:{line_number}"
    policy = grader["tool_policy"]
    allowed = set(gold["allowed_tool_names"])
    required = set(gold["required_tool_names"])
    recorded = {
        str(recording.get("tool"))
        for recording in replay["tools"]
        if isinstance(recording, Mapping)
    }
    if policy == "required" and (not required or not required.issubset(recorded)):
        raise DatasetContractError(
            f"{location}: required tool policy needs a recording for every required tool"
        )
    if policy == "forbidden" and (allowed or required):
        raise DatasetContractError(
            f"{location}: forbidden tool policy cannot allow or require tools"
        )
    if policy == "optional" and required:
        raise DatasetContractError(
            f"{location}: optional tool policy cannot require tools"
        )
    readiness = gold["publication_readiness"]
    if bool(gold["should_print"]) != (readiness == "ready"):
        raise DatasetContractError(
            f"{location}: should_print must agree with publication_readiness"
        )


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
    seen: set[tuple[str, str, str]] = set()
    default_tools: set[str] = set()
    allowed_tools = set(_replay_tool_definitions())
    for index, recording in enumerate(value["tools"]):
        location = f"{source}:{line_number}:replay.tools[{index}]"
        _require_exact_keys(
            recording,
            {"tool", "arguments", "result", "match"}
            if isinstance(recording, dict) and "match" in recording
            else {"tool", "arguments", "result"},
            location,
        )
        if recording["tool"] not in allowed_tools:
            raise DatasetContractError(f"{location}: unsupported tool")
        if not isinstance(recording["arguments"], dict) or not isinstance(
            recording["result"], dict
        ):
            raise DatasetContractError(f"{location}: arguments and result must be objects")
        match = recording.get("match", "exact")
        if match not in {"exact", "authorized_case_default"}:
            raise DatasetContractError(f"{location}: unsupported match policy")
        if match == "authorized_case_default":
            if recording["tool"] != "web_search":
                raise DatasetContractError(
                    f"{location}: authorized case default is web_search-only"
                )
            if recording["tool"] in default_tools:
                raise DatasetContractError(f"{location}: duplicate default recording")
            default_tools.add(recording["tool"])
            if str(recording["arguments"].get("query") or "").startswith("__fixture_"):
                raise DatasetContractError(
                    f"{location}: failure fixture cannot be a default recording"
                )
        key = (
            recording["tool"],
            canonical_json(recording["arguments"]),
            str(match),
        )
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
            "candidate_relevance",
            "publication_readiness",
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
    if value["candidate_relevance"] not in {"low", "medium", "high"}:
        raise DatasetContractError(f"{source}:{line_number}: invalid candidate relevance")
    if value["publication_readiness"] not in {
        "ready",
        "insufficient_evidence",
        "not_applicable",
    }:
        raise DatasetContractError(
            f"{source}:{line_number}: invalid publication readiness"
        )
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
            "tool_policy",
            "allowed_tool_families",
            "forbidden_tool_families",
            "forbidden_output_substrings",
            "expected_tool_outcome",
            "require_known_gap_after_failure",
            "max_repairs",
            "max_turns",
        },
        f"{source}:{line_number}:grader",
    )
    allowed_families = {definition.family for definition in _replay_tool_definitions().values()}
    if value["tool_policy"] not in {"forbidden", "optional", "required"}:
        raise DatasetContractError(f"{source}:{line_number}: invalid tool policy")
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
    forbidden_substrings = value["forbidden_output_substrings"]
    if not isinstance(forbidden_substrings, list) or not all(
        isinstance(item, str) and item for item in forbidden_substrings
    ):
        raise DatasetContractError(
            f"{source}:{line_number}: forbidden_output_substrings must be a string array"
        )
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
    explicitly_unresolved = str(candidate.get("identity_status") or "") == "unresolved"
    name = "" if explicitly_unresolved else str(candidate.get("name") or case.case_id)
    link = str(candidate.get("canonical_link") or "")
    repo_key = _repo_key_from_url(link)
    domain = re.sub(r"^www\.", "", re.sub(r"^https?://", "", link).split("/", 1)[0])
    canonical_key = (
        ""
        if explicitly_unresolved
        else f"github:{repo_key}"
        if repo_key
        else (f"domain:{domain}" if domain and "." in domain else f"name:{case.case_id}")
    )
    entity_id = "" if explicitly_unresolved else f"eval:{case.case_id}"
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
        member_entity_ids=[] if explicitly_unresolved else [entity_id],
        level="potential",
        source_families=sorted(set(source_families)),
        evidence_hash=case.case_id,
        context={
            "members": (
                []
                if explicitly_unresolved
                else [
                    {
                        "entity_id": entity_id,
                        "name": name,
                        "canonical_url": link,
                        "context_preview": context_preview,
                        "binding_confidence": "high",
                    }
                ]
            ),
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
               temperature, max_output_tokens, error_type, error
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
        "error_type",
        "error",
    )
    return [dict(zip(keys, row)) for row in rows]


def _aggregate_telemetry(
    model_calls: list[dict[str, Any]],
    request_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    call_count = len(model_calls)

    def measured_sum(field: str) -> tuple[int | None, int]:
        values = [row.get(field) for row in model_calls]
        present = [int(value) for value in values if value is not None]
        if call_count == 0:
            return 0, 0
        return (sum(present) if len(present) == call_count else None), sum(present)

    input_tokens, known_input_tokens = measured_sum("input_tokens")
    output_tokens, known_output_tokens = measured_sum("output_tokens")
    cached_input_tokens, known_cached_input_tokens = measured_sum(
        "cached_input_tokens"
    )
    total_tokens, known_total_tokens = measured_sum("total_tokens")
    usage_reported_call_count = sum(
        row.get("input_tokens") is not None
        and row.get("output_tokens") is not None
        and row.get("total_tokens") is not None
        for row in model_calls
    )
    usage_missing_call_count = call_count - usage_reported_call_count

    costs = [
        _cost_amount(record.get("cost"))
        for record in request_records or []
        if record.get("cost") is not None
    ]
    present_costs = [value for value in costs if value is not None]
    cost_reported_call_count = len(present_costs)
    cost_sources = {
        str(record["cost"].get("source"))
        for record in request_records or []
        if isinstance(record.get("cost"), Mapping)
        and record["cost"].get("source")
    }
    cost_source = (
        next(iter(cost_sources))
        if len(cost_sources) == 1
        else "mixed"
        if cost_sources
        else "provider_reported"
        if present_costs
        else "missing"
    )
    currencies = {
        str(record["cost"].get("currency") or "")
        for record in request_records or []
        if isinstance(record.get("cost"), Mapping)
        and record["cost"].get("currency")
    }
    provider_attempts = [
        attempt
        for record in request_records or []
        for attempt in (
            record.get("provider_attempts")
            if isinstance(record.get("provider_attempts"), list)
            else [{}]
        )
        if isinstance(attempt, Mapping)
    ]
    if call_count == 0:
        cost_amount: float | None = 0.0
        known_partial_cost = 0.0
        cost_source = "measured_no_model_calls"
        cost_complete = True
        currency = "USD"
    else:
        cost_complete = cost_reported_call_count == call_count
        cost_amount = round(sum(present_costs), 8) if cost_complete else None
        known_partial_cost = round(sum(present_costs), 8)
        currency = next(iter(currencies)) if len(currencies) == 1 else "mixed" if currencies else "USD"
    return {
        "logical_call_count": call_count,
        "provider_attempt_count": len(provider_attempts),
        "failed_provider_attempt_count": sum(
            str(row.get("status") or "") == "error" for row in provider_attempts
        ),
        "usage_reported_call_count": usage_reported_call_count,
        "usage_missing_call_count": usage_missing_call_count,
        "usage_complete": usage_missing_call_count == 0,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached_input_tokens,
        "total_tokens": total_tokens,
        "known_partial_input_tokens": known_input_tokens,
        "known_partial_output_tokens": known_output_tokens,
        "known_partial_cached_input_tokens": known_cached_input_tokens,
        "known_partial_total_tokens": known_total_tokens,
        "latency_ms": sum(int(row.get("latency_ms") or 0) for row in model_calls),
        "cost": {
            "amount": cost_amount,
            "known_partial_amount": known_partial_cost,
            "currency": currency,
            "source": cost_source,
            "reported_call_count": cost_reported_call_count,
            "missing_call_count": call_count - cost_reported_call_count,
            "complete": cost_complete,
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


def _candidate_query_terms(case: Layer2EvalCase) -> set[str]:
    candidate = case.model_input.get("candidate")
    if not isinstance(candidate, Mapping):
        return set()
    identity_text = " ".join(
        str(candidate.get(key) or "")
        for key in ("name", "canonical_link")
    )
    stop_words = {
        "about",
        "agent",
        "evidence",
        "example",
        "https",
        "needed",
        "product",
        "project",
        "workflow",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9]+", identity_text.lower())
        if len(token) >= 3 and token not in stop_words
    }


def _query_is_candidate_bound(query: str, identity_terms: set[str]) -> bool:
    query_terms = set(re.findall(r"[a-z0-9]+", str(query).lower()))
    return len(query_terms & identity_terms) >= min(2, len(identity_terms)) and bool(
        identity_terms
    )


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


def _budget_metadata(config: V2EvalConfig) -> dict[str, Any]:
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
            "endpoint": _safe_endpoint_identity(
                str(getattr(provider, "base_url", ""))
            ),
            "actual_temperature": getattr(provider, "actual_temperature", None),
            "max_output_tokens": getattr(provider, "max_output_tokens", None),
            "response_format": getattr(provider, "response_format", None),
            "request_options": getattr(provider, "request_options", None),
            "thinking_type": getattr(provider, "thinking_type", None),
            "timeout_seconds": getattr(provider, "timeout", None),
            "max_retries": getattr(provider, "max_retries", None),
            "retry_backoff_seconds": getattr(
                provider, "retry_backoff_seconds", None
            ),
            "input_cost_per_million": getattr(
                provider, "input_cost_per_million", None
            ),
            "cached_input_cost_per_million": getattr(
                provider, "cached_input_cost_per_million", None
            ),
            "output_cost_per_million": getattr(
                provider, "output_cost_per_million", None
            ),
            "cost_currency": getattr(provider, "cost_currency", None),
            "pricing_revision": getattr(provider, "pricing_revision", None),
        }
    )


def _safe_endpoint_identity(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.hostname:
        return ""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme.lower()}://{parsed.hostname.lower()}{port}{parsed.path.rstrip('/')}"


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
