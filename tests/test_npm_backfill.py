from __future__ import annotations

import sqlite3
import unittest

from pipeline.decision.entity_resolution import Entity, ResolutionResult
from pipeline.decision.rules import evaluate_entities
from pipeline.decision.schema import init_decision_db


NOW = "2026-05-31T00:00:00Z"


class FakeNpmClient:
    def __init__(self) -> None:
        self.metadata_calls: list[str] = []
        self.download_calls: list[tuple[str, str]] = []

    def package_metadata(self, package: str) -> dict[str, object]:
        self.metadata_calls.append(package)
        return {
            "name": package,
            "repository": {
                "url": "git+https://github.com/Owner/Repo.git",
            },
            "time": {"modified": "2026-05-31T00:00:00.000Z"},
        }

    def downloads(self, package: str, period: str) -> dict[str, object]:
        self.download_calls.append((package, period))
        downloads_by_period = {
            "last-day": 12000,
            "last-week": 70000,
        }
        return {
            "downloads": downloads_by_period[period],
            "package": package,
            "start": "2026-05-30",
            "end": "2026-05-31",
        }


def insert_entity(conn: sqlite3.Connection, entity_id: str = "entity:npm") -> None:
    conn.execute(
        """
        insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json)
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_id,
            "Demo Package",
            "npm:@scope/demo",
            "npm",
            NOW,
            "[]",
            "[]",
        ),
    )


class NpmBackfillTest(unittest.TestCase):
    def test_npm_backfill_writes_download_repo_evidence_and_alias_with_limit(self) -> None:
        from pipeline.decision.npm_backfill import run_npm_backfill

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        insert_entity(conn, "entity:npm")
        insert_entity(conn, "entity:other")
        conn.executemany(
            """
            insert into backfill_jobs(entity_id, run_id, source, reason, status, requested_at)
            values (?, ?, ?, ?, ?, ?)
            """,
            [
                ("entity:npm", "run", "npm_registry", "package_downloads:@scope/demo", "pending", NOW),
                ("entity:other", "run", "npm_registry", "package_downloads:left-pad", "pending", NOW),
            ],
        )
        conn.commit()
        client = FakeNpmClient()

        summary = run_npm_backfill(
            conn,
            run_id="run",
            client=client,
            now=NOW,
            limit=1,
        )

        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(client.metadata_calls, ["@scope/demo"])
        self.assertEqual(
            client.download_calls,
            [("@scope/demo", "last-day"), ("@scope/demo", "last-week")],
        )
        rows = conn.execute(
            """
            select source, family, metric_name, metric_value, alias
            from evidence_rows
            order by metric_name
            """
        ).fetchall()
        self.assertEqual(
            rows,
            [
                ("npm_registry", "package_family", "daily_downloads", "12000", "@scope/demo"),
                ("npm_registry", "package_family", "downloads_7d", "70000", "@scope/demo"),
                ("npm_registry", "package_family", "npm_repository_link", "github:owner/repo", "@scope/demo"),
            ],
        )
        alias = conn.execute(
            """
            select entity_id, source, external_id, alias, confidence, origin, approved
            from alias_links
            """
        ).fetchone()
        self.assertEqual(
            alias,
            (
                "entity:npm",
                "npm_registry",
                "@scope/demo",
                "github:owner/repo",
                "deterministic",
                "npm_registry",
                1,
            ),
        )
        statuses = conn.execute(
            "select reason, status from backfill_jobs order by id"
        ).fetchall()
        self.assertEqual(
            statuses,
            [
                ("package_downloads:@scope/demo", "completed"),
                ("package_downloads:left-pad", "pending"),
            ],
        )

    def test_npm_registry_evidence_promotes_rising_downloads_to_potential(self) -> None:
        entity = Entity(
            entity_id="entity:npm",
            canonical_entity="Demo Package",
            canonical_key="npm:demo-package",
            key_type="npm",
            aliases=("demo-package",),
            source_refs=(),
        )
        result = evaluate_entities(
            [],
            ResolutionResult(entities=[entity], item_to_entity={}),
            run_id="run",
            rule_version="rules-v1",
            now=NOW,
            classifier_evidence=[
                self.npm_evidence(entity, "daily_downloads", "12000"),
                self.npm_evidence(entity, "downloads_7d", "70000"),
            ],
        )

        self.assertEqual(len(result.potential_candidates), 1)
        self.assertEqual(result.potential_candidates[0].level, "potential")
        self.assertEqual(result.potential_candidates[0].fired_families, ("package_family",))
        self.assertEqual(result.evidence_rows[0].source, "npm_registry")
        self.assertEqual(result.evidence_rows[0].family, "package_family")
        self.assertEqual(result.evidence_rows[0].metric_name, "daily_downloads")

    def test_npm_registry_evidence_promotes_large_daily_downloads_to_high(self) -> None:
        entity = Entity(
            entity_id="entity:npm",
            canonical_entity="Demo Package",
            canonical_key="npm:demo-package",
            key_type="npm",
            aliases=("demo-package",),
            source_refs=(),
        )
        result = evaluate_entities(
            [],
            ResolutionResult(entities=[entity], item_to_entity={}),
            run_id="run",
            rule_version="rules-v1",
            now=NOW,
            classifier_evidence=[self.npm_evidence(entity, "daily_downloads", "100000")],
        )

        self.assertEqual(len(result.potential_candidates), 1)
        self.assertEqual(result.potential_candidates[0].level, "high_potential")

    def test_npm_registry_evidence_needs_rising_weekly_average_for_potential(self) -> None:
        entity = Entity(
            entity_id="entity:npm",
            canonical_entity="Demo Package",
            canonical_key="npm:demo-package",
            key_type="npm",
            aliases=("demo-package",),
            source_refs=(),
        )
        result = evaluate_entities(
            [],
            ResolutionResult(entities=[entity], item_to_entity={}),
            run_id="run",
            rule_version="rules-v1",
            now=NOW,
            classifier_evidence=[self.npm_evidence(entity, "daily_downloads", "12000")],
        )

        self.assertEqual(result.potential_candidates, [])
        self.assertEqual(len(result.edge_watch_candidates), 1)

    def npm_evidence(
        self,
        entity: Entity,
        metric_name: str,
        metric_value: str,
    ) -> dict[str, str]:
        return {
            "entity_id": entity.entity_id,
            "canonical_entity": entity.canonical_entity,
            "alias": "demo-package",
            "source": "npm_registry",
            "event_at": NOW,
            "relative_to_reference": "",
            "metric_name": metric_name,
            "metric_value": metric_value,
            "family": "package_family",
            "rule_id": f"npm_registry_{metric_name}",
            "rule_version": "rules-v1",
            "signal_label": "backfill",
            "historical_safety": "as_of_safe",
            "note": "npm registry backfill",
            "raw_url_or_ref": "https://www.npmjs.com/package/demo-package",
            "run_id": "run",
        }


if __name__ == "__main__":
    unittest.main()
