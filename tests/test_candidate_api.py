import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pipeline.decision.schema import init_decision_db


class CandidateApiShapeTest(unittest.TestCase):
    def test_candidate_query_shape_from_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            conn = sqlite3.connect(db_path)
            init_decision_db(conn)
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
                    json.dumps(["owner/repo"]),
                    "[]",
                ),
            )
            conn.execute(
                """
                insert into potential_candidates(entity_id, run_id, level, fired_families_json, first_trigger_at)
                values (?, ?, ?, ?, ?)
                """,
                (
                    "entity:repo",
                    "run-1",
                    "potential",
                    json.dumps(["github"]),
                    "2026-05-31T00:00:00Z",
                ),
            )
            conn.commit()

            rows = conn.execute(
                """
                select pc.entity_id, e.canonical_entity, pc.level, pc.fired_families_json
                from potential_candidates pc
                join entities e on e.entity_id = pc.entity_id
                where pc.run_id = ?
                """,
                ("run-1",),
            ).fetchall()

            payload = [
                {
                    "entity_id": row[0],
                    "canonical_entity": row[1],
                    "level": row[2],
                    "fired_families": json.loads(row[3]),
                }
                for row in rows
            ]

            self.assertEqual(payload[0]["canonical_entity"], "owner/repo")
            self.assertEqual(payload[0]["level"], "potential")


if __name__ == "__main__":
    unittest.main()
