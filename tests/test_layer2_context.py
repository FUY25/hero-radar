from __future__ import annotations

import sqlite3
import unittest

from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.schema import init_decision_db


class Layer2ContextTest(unittest.TestCase):
    def test_context_includes_group_members_evidence_and_hash(self):
        from pipeline.decision.layer2_context import assemble_group_context

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        conn.executescript(
            """
            create table items (
                id integer primary key,
                source text not null,
                name text not null,
                url text,
                description text,
                metadata_json text not null,
                raw_json text not null
            );
            """
        )
        conn.execute(
            "insert into items(id, source, name, url, description, metadata_json, raw_json) values (?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                "github_trending",
                "owner/repo",
                "https://github.com/owner/repo",
                "Repo description",
                "{}",
                "{}",
            ),
        )
        conn.execute(
            """
            insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:repo",
                "owner/repo",
                "github:owner/repo",
                "github",
                "2026-05-31T00:00:00Z",
                "[]",
                "[1]",
            ),
        )
        conn.execute(
            """
            insert into evidence_rows(entity_id, canonical_entity, alias, source, event_at, metric_name, metric_value, family, rule_id, rule_version, signal_label, historical_safety, note, raw_url_or_ref, run_id)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:repo",
                "owner/repo",
                "owner/repo",
                "github_trending",
                "2026-05-31T00:00:00Z",
                "stars_today",
                "321",
                "github",
                "github_daily",
                "rules-v1",
                "potential",
                "snapshot_only",
                "passed",
                "item:1",
                "decision-run",
            ),
        )
        group = CandidateGroup(
            group_id="group:repo",
            canonical_entity_id="entity:repo",
            canonical_name="owner/repo",
            canonical_key="github:owner/repo",
            canonical_link="https://github.com/owner/repo",
            member_entity_ids=["entity:repo"],
            level="potential",
            source_families=["github"],
        )

        enriched = assemble_group_context(
            conn, decision_run_id="decision-run", group=group
        )

        self.assertEqual(enriched.context["canonical_name"], "owner/repo")
        self.assertEqual(enriched.context["members"][0]["entity_id"], "entity:repo")
        self.assertEqual(
            enriched.context["evidence_rows"][0]["metric_name"], "stars_today"
        )
        self.assertTrue(enriched.evidence_hash)
