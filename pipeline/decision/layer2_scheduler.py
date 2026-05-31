from __future__ import annotations

from dataclasses import dataclass

from pipeline.decision.layer2_models import LEVEL_RANK, CandidateGroup


@dataclass(frozen=True)
class Layer2Schedule:
    score_now: list[CandidateGroup]
    scout_edge_watch: list[CandidateGroup]
    skipped: list[dict[str, str]]
    pending: list[CandidateGroup]


def schedule_layer2_work(
    groups: list[CandidateGroup],
    *,
    previous_hashes: dict[str, str],
    max_edge_watch_scout: int,
    max_scored_candidates: int,
) -> Layer2Schedule:
    score_now: list[CandidateGroup] = []
    edge_watch: list[CandidateGroup] = []
    skipped: list[dict[str, str]] = []
    for group in sorted(groups, key=_priority_key):
        if _previous_hash(previous_hashes, group.group_id) == group.evidence_hash:
            skipped.append(
                {"group_id": group.group_id, "reason": "unchanged_evidence_hash"}
            )
            continue
        if group.level == "edge_watch":
            edge_watch.append(group)
        else:
            score_now.append(group)
    pending = score_now[max_scored_candidates:] + edge_watch[max_edge_watch_scout:]
    return Layer2Schedule(
        score_now=score_now[:max_scored_candidates],
        scout_edge_watch=edge_watch[:max_edge_watch_scout],
        skipped=skipped,
        pending=pending,
    )


def _priority_key(group: CandidateGroup) -> tuple[int, str]:
    return (-LEVEL_RANK.get(group.level, 0), group.canonical_name.lower())


def _previous_hash(previous_hashes: dict[str, str], group_id: str) -> str | None:
    return previous_hashes.get(group_id) or previous_hashes.get(f"group:{group_id}")
