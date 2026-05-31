from __future__ import annotations

import sqlite3
import unittest

from pipeline.decision.schema import init_decision_db, to_json


class Layer2GroupingTest(unittest.TestCase):
    def make_conn(self) -> sqlite3.Connection:
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
        return conn

    def insert_entity(self, conn, entity_id, name, key, item_ids):
        key_type = key.split(":", 1)[0]
        conn.execute(
            """
            insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_id,
                name,
                key,
                key_type,
                "2026-05-31T00:00:00Z",
                "[]",
                to_json(item_ids),
            ),
        )

    def test_groups_same_canonical_link_without_alias_write(self):
        from pipeline.decision.layer2_grouping import build_candidate_groups

        conn = self.make_conn()
        self.insert_entity(conn, "entity:repo", "owner/repo", "github:owner/repo", [])
        self.insert_entity(conn, "entity:npm", "repo", "npm:repo", [])
        conn.execute(
            """
            insert into alias_links(entity_id, source, external_id, alias, confidence, origin, approved, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:npm",
                "resolver",
                "npm:repo",
                "github:owner/repo",
                "high",
                "resolver",
                1,
                "2026-05-31T00:00:00Z",
            ),
        )
        conn.execute(
            "insert into potential_candidates(entity_id, run_id, level, fired_families_json, first_trigger_at) values (?, ?, ?, ?, ?)",
            (
                "entity:repo",
                "decision-run",
                "potential",
                '["github"]',
                "2026-05-31T00:00:00Z",
            ),
        )
        conn.execute(
            "insert into potential_candidates(entity_id, run_id, level, fired_families_json, first_trigger_at) values (?, ?, ?, ?, ?)",
            (
                "entity:npm",
                "decision-run",
                "potential",
                '["package_family"]',
                "2026-05-31T00:05:00Z",
            ),
        )

        groups = build_candidate_groups(conn, decision_run_id="decision-run")

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].member_entity_ids, ["entity:repo", "entity:npm"])
        self.assertEqual(groups[0].canonical_link, "https://github.com/owner/repo")
        self.assertEqual(
            conn.execute("select count(*) from alias_links").fetchone()[0],
            1,
        )

    def test_keeps_unrelated_name_matches_separate_without_strong_key(self):
        from pipeline.decision.layer2_grouping import build_candidate_groups

        conn = self.make_conn()
        self.insert_entity(conn, "entity:a", "Agent", "name:agent", [])
        self.insert_entity(conn, "entity:b", "Agent", "domain:example.com", [])
        for entity_id in ["entity:a", "entity:b"]:
            conn.execute(
                "insert into edge_watch_candidates(entity_id, run_id, reason_json, source_refs_json, status) values (?, ?, ?, ?, ?)",
                (entity_id, "decision-run", "[]", "[]", "open"),
            )

        groups = build_candidate_groups(conn, decision_run_id="decision-run")

        self.assertEqual(len(groups), 2)
        self.assertEqual(
            sorted(group.canonical_entity_id for group in groups),
            ["entity:a", "entity:b"],
        )
