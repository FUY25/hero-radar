from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from typing import Any

from pipeline.decision.candidate_context import context_bundle_for_entity
from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.schema import to_json
from pipeline.server import query_evidence


def assemble_group_context(
    conn: sqlite3.Connection,
    *,
    decision_run_id: str,
    group: CandidateGroup,
) -> CandidateGroup:
    members: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    for entity_id in group.member_entity_ids:
        bundle = context_bundle_for_entity(conn, entity_id=entity_id, run_id=decision_run_id)
        members.append({"entity_id": entity_id, **bundle})
        evidence_rows.extend(query_evidence(conn, entity_id, decision_run_id))
    evidence_rows.sort(
        key=lambda row: (row.get("event_at") or "", row.get("id") or 0),
        reverse=True,
    )
    context = {
        "group_id": group.group_id,
        "canonical_name": group.canonical_name,
        "canonical_key": group.canonical_key,
        "canonical_link": group.canonical_link,
        "level": group.level,
        "source_families": group.source_families,
        "members": members,
        "evidence_rows": evidence_rows[:80],
    }
    evidence_hash = hashlib.sha256(
        to_json(
            {
                "member_entity_ids": group.member_entity_ids,
                "evidence": [
                    [
                        row.get("id"),
                        row.get("event_at"),
                        row.get("metric_name"),
                        row.get("metric_value"),
                        row.get("note"),
                    ]
                    for row in evidence_rows
                ],
            }
        ).encode("utf-8")
    ).hexdigest()
    return replace(group, context=context, evidence_hash=evidence_hash)
