from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


TokenEstimator = Callable[[Any], int]


@dataclass(frozen=True)
class ContextBudget:
    """Component-owned limits for one scoring-investigator request."""

    max_context_tokens: int = 32_000
    output_reserve: int = 3_000
    safety_margin: int = 500
    identity_allocation: int = 800
    evidence_summary_allocation: int = 800
    top_evidence_allocation: int = 2_400
    previous_turn_allocation: int = 800
    tool_observation_allocation: int = 2_400
    recent_raw_tool_result_count: int = 1


@dataclass(frozen=True)
class ContextBuildResult:
    payload: dict[str, Any]
    manifest: dict[str, Any]


class ContextBudgetExceeded(ValueError):
    """Raised before the provider call when mandatory context cannot fit."""


def conservative_token_estimate(value: Any) -> int:
    """Deterministic fallback estimate used when no provider tokenizer exists."""

    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    if not text:
        return 0
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


class ScoringContextBuilder:
    """Sole assembler for the model-facing Layer 2 scoring context."""

    def __init__(
        self,
        *,
        token_estimator: TokenEstimator | None = None,
        context_policy_version: str = "layer2-scoring-context-v1",
    ) -> None:
        self._estimate = token_estimator or conservative_token_estimate
        self.context_policy_version = context_policy_version

    def build(
        self,
        *,
        task: Mapping[str, Any],
        candidate: Mapping[str, Any],
        evidence_rows: Sequence[Mapping[str, Any]],
        observations: Sequence[Mapping[str, Any]],
        previous_turn: Mapping[str, Any] | None,
        decision_state: Mapping[str, Any] | None = None,
        raw_tool_results: Sequence[Mapping[str, Any]],
        active_tools: Sequence[Mapping[str, Any]],
        remaining_budget: Mapping[str, Any],
        system_prompt: str,
        output_schema: Mapping[str, Any],
        budget: ContextBudget | None = None,
    ) -> ContextBuildResult:
        active_budget = budget or ContextBudget()
        identity = dict(candidate.get("identity") or {})
        hard_facts = dict(candidate.get("hard_facts") or {})
        identity_packet = {"identity": identity, "hard_facts": hard_facts}
        identity_tokens = self._estimate(identity_packet)
        if identity_tokens > active_budget.identity_allocation:
            raise ContextBudgetExceeded(
                "mandatory candidate identity exceeds its context allocation: "
                f"estimated={identity_tokens}, "
                f"allocation={active_budget.identity_allocation}"
            )
        context_summary = self._bounded_value(
            candidate.get("context_summary") or "",
            max_tokens=active_budget.evidence_summary_allocation,
        )
        ordered_evidence = sorted(
            (dict(row) for row in evidence_rows),
            key=lambda row: -float(row.get("decision_value") or 0),
        )
        normalized_evidence = [
            self._normalize_evidence(row, index=index)
            for index, row in enumerate(ordered_evidence)
        ]
        (
            top_evidence,
            retrievable_evidence_ids,
            excluded_evidence_ids,
        ) = self._allocate_rows(
            normalized_evidence,
            id_key="evidence_id",
            max_tokens=active_budget.top_evidence_allocation,
        )

        candidate_packet: dict[str, Any] = {
            "identity": identity,
            "hard_facts": hard_facts,
            "context_summary": context_summary,
            "top_evidence": top_evidence,
        }
        omitted_evidence_count = len(retrievable_evidence_ids) + len(
            excluded_evidence_ids
        )
        if omitted_evidence_count:
            candidate_packet["evidence_availability"] = {
                "omitted_count": omitted_evidence_count,
                "retrievable": bool(retrievable_evidence_ids),
            }
        normalized_observations: list[dict[str, Any]] = []
        for index, raw_observation in enumerate(observations):
            observation = dict(raw_observation)
            observation_id = str(
                observation.get("observation_id")
                or observation.get("id")
                or f"observation:{index + 1}"
            )
            observation["observation_id"] = observation_id
            normalized_observations.append(observation)
        (
            included_observations,
            retrievable_observation_ids,
            excluded_observation_ids,
        ) = self._allocate_rows(
            normalized_observations,
            id_key="observation_id",
            max_tokens=active_budget.tool_observation_allocation,
        )
        working_state: dict[str, Any] = {
            "verified_observations": included_observations,
        }
        active_decision_state = dict(decision_state or {})
        if isinstance(active_decision_state.get("information_sufficiency"), Mapping):
            working_state["information_sufficiency"] = dict(
                active_decision_state["information_sufficiency"]
            )
        if isinstance(active_decision_state.get("open_questions"), list) and active_decision_state[
            "open_questions"
        ]:
            working_state["open_questions"] = list(
                active_decision_state["open_questions"]
            )
        if isinstance(active_decision_state.get("used_tool_signatures"), list):
            working_state["used_tool_signatures"] = list(
                active_decision_state["used_tool_signatures"]
            )
        if previous_turn:
            working_state["previous_turn"] = self._bounded_value(
                dict(previous_turn),
                max_tokens=active_budget.previous_turn_allocation,
            )
        recent_count = max(0, int(active_budget.recent_raw_tool_result_count))
        if recent_count:
            working_state["recent_raw_tool_results"] = [
                dict(row) for row in raw_tool_results[-recent_count:]
            ]

        payload: dict[str, Any] = {
            "task": dict(task),
            "candidate": candidate_packet,
            "working_state": working_state,
            "available_tools": [dict(tool) for tool in active_tools],
            "remaining_budget": dict(remaining_budget),
            "output_schema": dict(output_schema),
        }
        section_tokens = {
            "system_prompt": self._estimate(system_prompt),
            "tool_schemas": self._estimate(list(active_tools)),
            "task": self._estimate(payload["task"]),
            "candidate_identity": identity_tokens,
            "candidate_summary": self._estimate(candidate_packet["context_summary"]),
            "evidence": self._estimate(top_evidence),
            "previous_turn": self._estimate(working_state.get("previous_turn", {})),
            "observations": self._estimate(working_state["verified_observations"]),
            "recent_raw_tool_results": self._estimate(
                working_state.get("recent_raw_tool_results", [])
            ),
            "remaining_budget": self._estimate(payload["remaining_budget"]),
            "output_schema": self._estimate(payload["output_schema"]),
        }
        estimated_input_tokens = sum(section_tokens.values())
        maximum_input_tokens = max(
            0,
            int(active_budget.max_context_tokens)
            - int(active_budget.output_reserve)
            - int(active_budget.safety_margin),
        )
        excluded_raw_result_ids: list[str] = []
        if estimated_input_tokens > maximum_input_tokens and working_state.get(
            "recent_raw_tool_results"
        ):
            excluded_raw_result_ids = [
                str(
                    row.get("observation_id")
                    or row.get("tool")
                    or f"raw:{index + 1}"
                )
                for index, row in enumerate(
                    working_state.get("recent_raw_tool_results") or []
                )
                if isinstance(row, Mapping)
            ]
            working_state.pop("recent_raw_tool_results", None)
            section_tokens["recent_raw_tool_results"] = 0
            estimated_input_tokens = sum(section_tokens.values())
        if estimated_input_tokens > maximum_input_tokens:
            raise ContextBudgetExceeded(
                "mandatory scoring context exceeds the model input budget: "
                f"estimated={estimated_input_tokens}, maximum={maximum_input_tokens}"
            )

        included_evidence_ids = [row["evidence_id"] for row in top_evidence]
        manifest = {
            "context_policy_version": self.context_policy_version,
            "estimator": "provider" if self._estimate is not conservative_token_estimate else "conservative",
            "section_tokens": section_tokens,
            "estimated_input_tokens": estimated_input_tokens,
            "maximum_input_tokens": maximum_input_tokens,
            "included_evidence_ids": included_evidence_ids,
            "summarized_evidence_ids": [],
            "retrievable_evidence_ids": retrievable_evidence_ids,
            "excluded_evidence_ids": excluded_evidence_ids,
            "included_observation_ids": [
                row["observation_id"] for row in included_observations
            ],
            "summarized_observation_ids": [],
            "retrievable_observation_ids": retrievable_observation_ids,
            "excluded_observation_ids": excluded_observation_ids,
            "excluded_raw_result_ids": excluded_raw_result_ids,
        }
        return ContextBuildResult(payload=payload, manifest=manifest)

    @staticmethod
    def _normalize_evidence(row: dict[str, Any], *, index: int) -> dict[str, Any]:
        evidence_id = row.get("evidence_id") or row.get("id") or f"evidence:{index + 1}"
        normalized = dict(row)
        normalized_id = str(evidence_id)
        if ":" not in normalized_id:
            normalized_id = f"evidence:{normalized_id}"
        normalized["evidence_id"] = normalized_id
        return normalized

    def _bounded_value(self, value: Any, *, max_tokens: int) -> Any:
        if max_tokens <= 0:
            return {"truncated": True, "excerpt": ""}
        if self._estimate(value) <= max_tokens:
            return value
        serialized = (
            value
            if isinstance(value, str)
            else json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        )
        excerpt = serialized
        bounded = {"truncated": True, "excerpt": excerpt}
        while excerpt and self._estimate(bounded) > max_tokens:
            overflow = self._estimate(bounded) - max_tokens
            excerpt = excerpt[: max(0, len(excerpt) - max(8, overflow * 4))]
            bounded = {"truncated": True, "excerpt": excerpt}
        if self._estimate(bounded) > max_tokens:
            raise ContextBudgetExceeded(
                "context allocation is too small to encode truncation metadata: "
                f"allocation={max_tokens}"
            )
        return bounded

    def _allocate_rows(
        self,
        rows: Sequence[dict[str, Any]],
        *,
        id_key: str,
        max_tokens: int,
    ) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        included: list[dict[str, Any]] = []
        retrievable_ids: list[str] = []
        excluded_ids: list[str] = []
        used_tokens = 0
        for row in rows:
            row_tokens = self._estimate(row)
            if used_tokens + row_tokens <= max_tokens:
                included.append(row)
                used_tokens += row_tokens
            elif bool(row.get("retrievable", True)):
                retrievable_ids.append(str(row[id_key]))
            else:
                excluded_ids.append(str(row[id_key]))
        return included, retrievable_ids, excluded_ids
