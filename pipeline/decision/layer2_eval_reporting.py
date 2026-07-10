from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


class CaseIdentityLike(Protocol):
    case_id: str


class DatasetLike(Protocol):
    version: str
    cases: Sequence[CaseIdentityLike]


class ConfigLike(Protocol):
    prompt_versions: tuple[str, str]
    trials: int
    grader_version: str


class OutputPathsLike(Protocol):
    blind_briefs_jsonl: Path
    blind_mapping_json: Path


def aggregate_results(
    dataset: DatasetLike,
    config: ConfigLike,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    versions: dict[str, Any] = {}
    for version in config.prompt_versions:
        rows = [row for row in results if row["prompt_version"] == version]
        failures = [
            {
                "case_id": row["case_id"],
                "trial": row["trial"],
                "failed_graders": [
                    name
                    for name, grade in row["grades"].items()
                    if not grade.get("passed", False)
                ],
            }
            for row in rows
            if not row["passed"]
        ]
        versions[version] = {
            "result_count": len(rows),
            "passed": sum(bool(row["passed"]) for row in rows),
            "failed": len(failures),
            "failures": failures,
            "mean_score": (
                round(sum(float(row["score"]) for row in rows) / len(rows), 3)
                if rows
                else None
            ),
            "input_tokens": _nullable_metric_sum(rows, "input_tokens"),
            "output_tokens": _nullable_metric_sum(rows, "output_tokens"),
            "latency_ms": sum(int(row["telemetry"]["latency_ms"]) for row in rows),
            "cost": _nullable_cost_sum(rows),
        }
    by_case: dict[str, Any] = {}
    for case in dataset.cases:
        by_case[case.case_id] = {}
        for version in config.prompt_versions:
            rows = [
                row
                for row in results
                if row["case_id"] == case.case_id
                and row["prompt_version"] == version
            ]
            by_case[case.case_id][version] = {
                "passed": sum(bool(row["passed"]) for row in rows),
                "failed": sum(not bool(row["passed"]) for row in rows),
                "mean_score": round(
                    sum(float(row["score"]) for row in rows) / len(rows), 3
                ),
            }
    grader_names = sorted(
        {
            name
            for row in results
            for name in row.get("grades", {})
        }
    )
    by_grader = {
        name: {
            version: {
                "passed": sum(
                    bool(row["grades"][name]["passed"])
                    for row in results
                    if row["prompt_version"] == version
                ),
                "failed": sum(
                    not bool(row["grades"][name]["passed"])
                    for row in results
                    if row["prompt_version"] == version
                ),
            }
            for version in config.prompt_versions
        }
        for name in grader_names
    }
    by_tool_family: dict[str, dict[str, int]] = {}
    by_failure_type: dict[str, int] = {}
    for row in results:
        for tool_row in row.get("tool_trace", []):
            family = str(tool_row.get("eval_family") or tool_row.get("family") or "unknown")
            status = str(tool_row.get("status") or "unknown")
            family_counts = by_tool_family.setdefault(family, {})
            family_counts[status] = family_counts.get(status, 0) + 1
            if status != "ok":
                key = f"tool:{status}"
                by_failure_type[key] = by_failure_type.get(key, 0) + 1
        for grader_name, grade in row.get("grades", {}).items():
            if not grade.get("passed", False):
                key = f"grader:{grader_name}"
                by_failure_type[key] = by_failure_type.get(key, 0) + 1
    return {
        "artifact_version": "layer2-eval-aggregate-v1",
        "dataset_version": dataset.version,
        "grader_version": config.grader_version,
        "case_count": len(dataset.cases),
        "trials": config.trials,
        "versions": versions,
        "by_case": by_case,
        "by_grader": by_grader,
        "by_tool_family": by_tool_family,
        "by_failure_type": by_failure_type,
        "all_passed": all(row["passed"] for row in results),
    }


def _nullable_metric_sum(rows: list[dict[str, Any]], field: str) -> int | None:
    values = [row["telemetry"].get(field) for row in rows]
    present = [int(value) for value in values if value is not None]
    return sum(present) if present else None


def _nullable_cost_sum(rows: list[dict[str, Any]]) -> float | None:
    values = [row["telemetry"]["cost"].get("amount") for row in rows]
    present = [float(value) for value in values if value is not None]
    return round(sum(present), 8) if present else None


def render_report(
    dataset: DatasetLike,
    config: ConfigLike,
    results: list[dict[str, Any]],
    aggregate: dict[str, Any],
) -> str:
    left, right = config.prompt_versions
    lines = [
        "# Layer 2 production-equivalent replay evaluation",
        "",
        "## v1 / v2 case and trial comparison",
        "",
        f"Run `{aggregate.get('run_id', 'unknown')}`.",
        f"Dataset `{dataset.version}`; grader `{config.grader_version}`; {config.trials} uncached trials per version.",
        "Live-provider artifact: not run. Tool replay is deterministic and network-free.",
        "",
        "| Case | Trial | v1 score / pass | v2 score / pass | route v1 / v2 | trajectory v1 / v2 | grounding v1 / v2 | brief v1 / v2 | input tokens | latency ms | cost USD |",
        "| --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    by_key = {
        (row["case_id"], row["trial"], row["prompt_version"]): row
        for row in results
    }
    for case in dataset.cases:
        for trial in range(1, config.trials + 1):
            lrow = by_key[(case.case_id, trial, left)]
            rrow = by_key[(case.case_id, trial, right)]
            lines.append(
                "| {case} | {trial} | {ls:.2f} / {lp} | {rs:.2f} / {rp} | {routes} | {trajectory} | {grounding} | {brief} | {tokens} | {latency} | {cost} |".format(
                    case=case.case_id,
                    trial=trial,
                    ls=float(lrow["score"]),
                    lp="pass" if lrow["passed"] else "FAIL",
                    rs=float(rrow["score"]),
                    rp="pass" if rrow["passed"] else "FAIL",
                    routes=_display_pair(lrow["route"], rrow["route"]),
                    trajectory=_display_pair(
                        _trajectory_display(lrow), _trajectory_display(rrow)
                    ),
                    grounding=_display_pair(
                        _grade_display(lrow, "claim_grounding"),
                        _grade_display(rrow, "claim_grounding"),
                    ),
                    brief=_display_pair(
                        _grade_display(lrow, "brief"),
                        _grade_display(rrow, "brief"),
                    ),
                    tokens=_display_pair(
                        lrow["telemetry"].get("input_tokens"),
                        rrow["telemetry"].get("input_tokens"),
                    ),
                    latency=_display_pair(
                        lrow["telemetry"].get("latency_ms"),
                        rrow["telemetry"].get("latency_ms"),
                    ),
                    cost=_display_pair(
                        lrow["telemetry"]["cost"].get("amount"),
                        rrow["telemetry"]["cost"].get("amount"),
                    ),
                )
            )
    lines.extend(["", "## Case/trial failures", ""])
    failures = [
        (version, failure)
        for version, summary in aggregate["versions"].items()
        for failure in summary["failures"]
    ]
    if failures:
        for version, failure in failures:
            lines.append(
                f"- `{version}` `{failure['case_id']}` trial {failure['trial']}: "
                + ", ".join(failure["failed_graders"])
            )
    else:
        lines.append("- None.")
    lines.extend(["", "## Telemetry", ""])
    for version in config.prompt_versions:
        summary = aggregate["versions"][version]
        lines.append(
            f"- `{version}`: input tokens {_display(summary['input_tokens'])}; "
            f"output tokens {_display(summary['output_tokens'])}; "
            f"latency {summary['latency_ms']} ms; cost {_display(summary['cost'])}."
        )
    return "\n".join(lines) + "\n"


def _display(value: Any) -> str:
    return "missing" if value is None else str(value)


def _display_pair(left: Any, right: Any) -> str:
    return f"{_display(left)} / {_display(right)}"


def _grade_display(row: Mapping[str, Any], grader: str) -> str:
    return "pass" if row["grades"][grader]["passed"] else "FAIL"


def _trajectory_display(row: Mapping[str, Any]) -> str:
    tools = [
        f"{tool_row.get('tool')}:{tool_row.get('status')}"
        for tool_row in row.get("tool_trace", [])
    ]
    return ", ".join(tools) or "none"


def write_blind_briefs(
    paths: OutputPathsLike,
    results: list[dict[str, Any]],
    versions: tuple[str, str],
) -> None:
    packets: list[dict[str, Any]] = []
    mapping: dict[str, Any] = {}
    for row in results:
        if not row.get("brief"):
            continue
        label = "A" if row["prompt_version"] == versions[0] else "B"
        blind_id = f"{row['case_id']}-t{row['trial']}-{label}"
        packets.append(
            {
                "blind_id": blind_id,
                "case_id": row["case_id"],
                "trial": row["trial"],
                "brief": row["brief"]["output"],
            }
        )
        mapping[blind_id] = row["prompt_version"]
    paths.blind_briefs_jsonl.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in packets),
        encoding="utf-8",
    )
    paths.blind_mapping_json.write_text(
        json.dumps(mapping, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
