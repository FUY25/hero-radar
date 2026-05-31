from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


LEVEL_RANK = {"edge_watch": 0, "watch": 1, "potential": 2, "high_potential": 3}


@dataclass(frozen=True)
class CandidateGroup:
    group_id: str
    canonical_entity_id: str
    canonical_name: str
    canonical_key: str
    canonical_link: str
    member_entity_ids: list[str]
    level: str
    source_families: list[str]
    evidence_hash: str = ""
    grouping_reason: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
