from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from pipeline.decision.layer2_eval_io import atomic_write_text


class CaseIdentityLike(Protocol):
    case_id: str


class DatasetLike(Protocol):
    version: str
    cases: Sequence[CaseIdentityLike]


class ConfigLike(Protocol):
    prompt_version: str
    trials: int
    grader_version: str


class OutputPathsLike(Protocol):
    blind_briefs_jsonl: Path
    blind_mapping_json: Path


def aggregate_results(
    dataset: DatasetLike,
    config: ConfigLike,
    results: list[dict[str, Any]],
    *,
    attempts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    spend_rows = attempts if attempts is not None else results
    failures = [
        {
            "case_id": row["case_id"],
            "trial": row["trial"],
            "execution_status": row.get("execution_status"),
            "error": row.get("error"),
            "failed_graders": [
                name
                for name, grade in row.get("grades", {}).items()
                if not grade.get("passed", False)
            ],
        }
        for row in results
        if not row.get("passed", False)
    ]
    scored_rows = [row for row in results if row.get("score") is not None]
    execution_failures = sum(
        str(row.get("execution_status") or "") != "ok" for row in results
    )
    historical_execution_failures = sum(
        str(row.get("execution_status") or "") != "ok" for row in spend_rows
    )
    telemetry_summary = _run_telemetry_summary(spend_rows)
    summary = {
        "prompt_version": config.prompt_version,
        "result_count": len(results),
        "expected_result_count": len(dataset.cases) * int(config.trials),
        "attempt_count": len(spend_rows),
        "passed": sum(bool(row.get("passed")) for row in results),
        "failed": len(failures),
        "execution_failures": execution_failures,
        "historical_execution_failures": historical_execution_failures,
        "failures": failures,
        "mean_score": (
            round(
                sum(float(row["score"]) for row in scored_rows) / len(scored_rows),
                3,
            )
            if scored_rows
            else None
        ),
        **telemetry_summary,
    }
    by_case: dict[str, Any] = {}
    for case in dataset.cases:
        rows = [row for row in results if row["case_id"] == case.case_id]
        case_scored_rows = [row for row in rows if row.get("score") is not None]
        by_case[case.case_id] = {
            "result_count": len(rows),
            "passed": sum(bool(row.get("passed")) for row in rows),
            "failed": sum(not bool(row.get("passed")) for row in rows),
            "execution_failures": sum(
                str(row.get("execution_status") or "") != "ok" for row in rows
            ),
            "mean_score": (
                round(
                    sum(float(row["score"]) for row in case_scored_rows)
                    / len(case_scored_rows),
                    3,
                )
                if case_scored_rows
                else None
            ),
        }
    grader_names = sorted(
        {name for row in results for name in row.get("grades", {})}
    )
    by_grader = {
        name: {
            "passed": sum(
                bool(row.get("grades", {}).get(name, {}).get("passed"))
                for row in results
            ),
            "failed": sum(
                not bool(row.get("grades", {}).get(name, {}).get("passed"))
                for row in results
            ),
        }
        for name in grader_names
    }
    by_tool_family: dict[str, dict[str, int]] = {}
    by_failure_type: dict[str, int] = {}
    for row in spend_rows:
        for tool_row in row.get("tool_trace", []):
            family = str(
                tool_row.get("eval_family") or tool_row.get("family") or "unknown"
            )
            status = str(tool_row.get("status") or "unknown")
            family_counts = by_tool_family.setdefault(family, {})
            family_counts[status] = family_counts.get(status, 0) + 1
            if status != "ok":
                key = f"tool:{status}"
                by_failure_type[key] = by_failure_type.get(key, 0) + 1
        error = row.get("error")
        if isinstance(error, Mapping):
            key = f"execution:{error.get('stage', 'unknown')}:{error.get('type', 'Error')}"
            by_failure_type[key] = by_failure_type.get(key, 0) + 1
    for row in results:
        for grader_name, grade in row.get("grades", {}).items():
            if not grade.get("passed", False):
                key = f"grader:{grader_name}"
                by_failure_type[key] = by_failure_type.get(key, 0) + 1
    expected_results = len(dataset.cases) * int(config.trials)
    return {
        "artifact_version": "layer2-eval-aggregate-v2",
        "dataset_version": dataset.version,
        "grader_version": config.grader_version,
        "prompt_version": config.prompt_version,
        "case_count": len(dataset.cases),
        "trials": config.trials,
        "summary": summary,
        "execution_failures": execution_failures,
        "by_case": by_case,
        "by_grader": by_grader,
        "by_tool_family": by_tool_family,
        "by_failure_type": by_failure_type,
        "all_passed": (
            len(results) == expected_results
            and execution_failures == 0
            and all(bool(row.get("passed")) for row in results)
        ),
    }


def _run_telemetry_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    telemetry = [
        row.get("telemetry", {})
        if isinstance(row.get("telemetry"), Mapping)
        else {}
        for row in rows
    ]
    usage_complete = all(bool(row.get("usage_complete")) for row in telemetry)

    def metric(field: str) -> tuple[int | None, int]:
        values = [row.get(field) for row in telemetry]
        complete = usage_complete and all(value is not None for value in values)
        known = sum(
            int(
                row.get(f"known_partial_{field}")
                if row.get(f"known_partial_{field}") is not None
                else row.get(field)
                or 0
            )
            for row in telemetry
        )
        return (known if complete else None), known

    input_tokens, known_input = metric("input_tokens")
    output_tokens, known_output = metric("output_tokens")
    total_tokens, known_total = metric("total_tokens")
    cost_rows = [
        row.get("cost", {}) if isinstance(row.get("cost"), Mapping) else {}
        for row in telemetry
    ]
    cost_complete = all(bool(row.get("complete")) for row in cost_rows)
    known_cost = round(
        sum(
            float(
                row.get("known_partial_amount")
                if row.get("known_partial_amount") is not None
                else row.get("amount")
                or 0
            )
            for row in cost_rows
        ),
        8,
    )
    currencies = {
        str(row.get("currency")) for row in cost_rows if row.get("currency")
    }
    return {
        "logical_call_count": sum(
            int(row.get("logical_call_count") or 0) for row in telemetry
        ),
        "provider_attempt_count": sum(
            int(row.get("provider_attempt_count") or 0) for row in telemetry
        ),
        "failed_provider_attempt_count": sum(
            int(row.get("failed_provider_attempt_count") or 0) for row in telemetry
        ),
        "usage_reported_call_count": sum(
            int(row.get("usage_reported_call_count") or 0) for row in telemetry
        ),
        "usage_missing_call_count": sum(
            int(row.get("usage_missing_call_count") or 0) for row in telemetry
        ),
        "usage_complete": usage_complete,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "known_partial_input_tokens": known_input,
        "known_partial_output_tokens": known_output,
        "known_partial_total_tokens": known_total,
        "latency_ms": sum(int(row.get("latency_ms") or 0) for row in telemetry),
        "cost": known_cost if cost_complete else None,
        "known_partial_cost": known_cost,
        "cost_complete": cost_complete,
        "cost_currency": (
            next(iter(currencies)) if len(currencies) == 1 else "mixed"
        ),
    }


def render_report(
    dataset: DatasetLike,
    config: ConfigLike,
    results: list[dict[str, Any]],
    aggregate: dict[str, Any],
) -> str:
    summary = aggregate["summary"]
    provider_execution = str(
        aggregate.get("provider_execution") or "test_provider"
    )
    run_scope = str(aggregate.get("run_scope") or "test")
    title = (
        "# Layer 2 V2 release evaluation"
        if run_scope == "release_full"
        else "# Layer 2 V2 partial debug evaluation"
        if run_scope == "partial_debug"
        else "# Layer 2 V2 test-provider evaluation"
    )
    execution_description = (
        "The scorer and selected Brief Writer calls used real Kimi; primitive tools used deterministic, network-free replay."
        if provider_execution == "real_kimi"
        else "This artifact used a test provider and is not release-quality real-model evidence; primitive tools used deterministic, network-free replay."
    )
    lines = [
        title,
        "",
        "## V2 case and trial results",
        "",
        f"Run `{aggregate.get('run_id', 'unknown')}`.",
        (
            f"Dataset `{dataset.version}`; prompt `{config.prompt_version}`; "
            f"grader `{config.grader_version}`; {config.trials} uncached trials per case."
        ),
        execution_description,
        "",
        "| Case | Trial | Execution | Final valid | Score / pass | Preflight | Route | Tools | Repairs | Grounding | Brief | Total tokens | Latency ms | Cost USD | Error |",
        "| --- | ---: | --- | --- | --- | --- | --- | --- | ---: | --- | --- | ---: | ---: | ---: | --- |",
    ]
    by_key = {(row["case_id"], row["trial"]): row for row in results}
    for case in dataset.cases:
        for trial in range(1, int(config.trials) + 1):
            row = by_key.get((case.case_id, trial))
            if row is None:
                lines.append(
                    f"| {case.case_id} | {trial} | missing | no | missing / FAIL | missing | missing | missing | 0 | missing | missing | missing | missing | missing | checkpoint incomplete |"
                )
                continue
            error = row.get("error") if isinstance(row.get("error"), Mapping) else {}
            error_text = (
                f"{error.get('stage')}:{error.get('type')}:{error.get('message')}"
                if error
                else ""
            )
            score = "missing" if row.get("score") is None else f"{float(row['score']):.2f}"
            lines.append(
                "| {case} | {trial} | {execution} | {valid} | {score} / {passed} | {preflight} | {route} | {tools} | {repairs} | {grounding} | {brief} | {tokens} | {latency} | {cost} | {error} |".format(
                    case=case.case_id,
                    trial=trial,
                    execution=_display(row.get("execution_status")),
                    valid="yes" if row.get("final_output_valid") else "no",
                    score=score,
                    passed="pass" if row.get("passed") else "FAIL",
                    preflight=_display(row.get("preflight_mode")),
                    route=_display(row.get("route")),
                    tools=_trajectory_display(row),
                    repairs=int(row.get("repair_count") or 0),
                    grounding=_grade_display(row, "claim_grounding"),
                    brief=_grade_display(row, "brief"),
                    tokens=_display(row.get("telemetry", {}).get("total_tokens")),
                    latency=_display(row.get("telemetry", {}).get("latency_ms")),
                    cost=_display(row.get("telemetry", {}).get("cost", {}).get("amount")),
                    error=_escape_table(error_text),
                )
            )
    lines.extend(["", "## Case/trial failures", ""])
    if summary["failures"]:
        for failure in summary["failures"]:
            error = failure.get("error") or {}
            error_suffix = (
                f"; {error.get('stage')}:{error.get('type')}:{error.get('message')}"
                if error
                else ""
            )
            lines.append(
                f"- `{failure['case_id']}` trial {failure['trial']}: "
                + ", ".join(failure["failed_graders"])
                + error_suffix
            )
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Telemetry",
            "",
            f"- Results: {summary['result_count']} / {summary['expected_result_count']}; provider attempts: {summary['attempt_count']}; current execution failures: {summary['execution_failures']}; historical failed attempts: {summary['historical_execution_failures']}.",
            f"- Logical calls: {summary['logical_call_count']}; HTTP/provider attempts: {summary['provider_attempt_count']}; failed attempts: {summary['failed_provider_attempt_count']}; usage reported/missing calls: {summary['usage_reported_call_count']}/{summary['usage_missing_call_count']}.",
            f"- Input tokens: {_display(summary['input_tokens'])}; output tokens: {_display(summary['output_tokens'])}; total tokens: {_display(summary['total_tokens'])}; known partial total: {summary['known_partial_total_tokens']}.",
            f"- Provider latency: {summary['latency_ms']} ms; cost: {_display(summary['cost'])} {summary['cost_currency']}; known partial cost: {summary['known_partial_cost']}; complete: {summary['cost_complete']}.",
        ]
    )
    return "\n".join(lines) + "\n"


def _display(value: Any) -> str:
    return "missing" if value is None else str(value)


def _grade_display(row: Mapping[str, Any], grader: str) -> str:
    grade = row.get("grades", {}).get(grader)
    if not isinstance(grade, Mapping):
        return "missing"
    return "pass" if grade.get("passed") else "FAIL"


def _trajectory_display(row: Mapping[str, Any]) -> str:
    tools = [
        f"{tool_row.get('tool')}:{tool_row.get('status')}"
        for tool_row in row.get("tool_trace", [])
    ]
    return ", ".join(tools) or "none"


def _escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")[:240]


def write_blind_briefs(
    paths: OutputPathsLike,
    results: list[dict[str, Any]],
) -> None:
    packets: list[dict[str, Any]] = []
    mapping: dict[str, Any] = {}
    for row in results:
        brief = row.get("brief")
        if not isinstance(brief, Mapping) or not brief.get("output_valid"):
            continue
        blind_id = "brief-" + _opaque_brief_id(row)
        packets.append(
            {
                "blind_id": blind_id,
                "brief": brief["output"],
            }
        )
        mapping[blind_id] = {
            "prompt_version": row["prompt_version"],
            "case_id": row["case_id"],
            "trial": row["trial"],
        }
    atomic_write_text(
        paths.blind_briefs_jsonl,
        "".join(
            json.dumps(
                row, ensure_ascii=False, sort_keys=True, allow_nan=False
            )
            + "\n"
            for row in packets
        ),
    )
    atomic_write_text(
        paths.blind_mapping_json,
        json.dumps(
            mapping,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
    )


def _opaque_brief_id(row: Mapping[str, Any]) -> str:
    import hashlib

    material = "|".join(
        (
            str(row.get("run_id") or ""),
            str(row.get("case_id") or ""),
            str(row.get("trial") or ""),
            str(row.get("request_fingerprints") or ""),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
