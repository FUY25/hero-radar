import unittest

from pipeline.decision.entity_resolution import resolve_entities
from pipeline.decision.rules import evaluate_entities


class RulesEngineTest(unittest.TestCase):
    def test_github_trending_daily_potential(self):
        rows = [
            {
                "id": 1,
                "source": "github_trending",
                "external_id": "owner/repo",
                "name": "owner/repo",
                "url": "https://github.com/owner/repo",
                "description": "repo",
                "metadata": {
                    "period": "daily",
                    "window": "24h",
                    "period_stars": 1200,
                    "stars_total": 3000,
                },
                "fetched_at": "2026-05-31T00:00:00Z",
            }
        ]
        resolution = resolve_entities(rows, first_seen="2026-05-31T00:00:00Z")

        result = evaluate_entities(
            rows,
            resolution,
            run_id="run-1",
            rule_version="rules-v1",
            now="2026-05-31T00:00:00Z",
        )

        self.assertEqual(len(result.potential_candidates), 1)
        self.assertEqual(result.potential_candidates[0].level, "potential")
        self.assertEqual(result.evidence_rows[0].metric_name, "stars_today")

    def test_two_verified_weak_signals_create_potential(self):
        rows = [
            {
                "id": 1,
                "source": "hn_firebase",
                "external_id": "hn-1",
                "name": "Repo on HN",
                "url": "https://github.com/owner/repo",
                "description": "",
                "metadata": {"score": 75, "comments": 12, "list": "topstories"},
                "fetched_at": "2026-05-31T00:00:00Z",
            },
            {
                "id": 2,
                "source": "product_hunt",
                "external_id": "ph-1",
                "name": "Repo",
                "url": "https://producthunt.com/posts/repo",
                "description": "",
                "metadata": {
                    "daily_rank": 8,
                    "website": "https://github.com/owner/repo",
                },
                "fetched_at": "2026-05-31T00:00:00Z",
            },
        ]
        resolution = resolve_entities(rows, first_seen="2026-05-31T00:00:00Z")

        result = evaluate_entities(
            rows,
            resolution,
            run_id="run-1",
            rule_version="rules-v1",
            now="2026-05-31T00:00:00Z",
        )

        self.assertEqual(result.potential_candidates[0].level, "potential")
        evidence_rule_ids = {row.rule_id for row in result.evidence_rows}
        self.assertIn("verified_cross_source_two_weak_48h", evidence_rule_ids)

    def test_repofomo_watch_without_acceleration_becomes_edge_watch(self):
        rows = [
            {
                "id": 3,
                "source": "github_movers_repofomo",
                "external_id": "repofomo:owner/repo",
                "name": "owner/repo",
                "url": "https://github.com/owner/repo",
                "description": "",
                "metadata": {
                    "stars_7d": 350,
                    "stars_30d": 2000,
                    "stars_60d": 4500,
                    "stars_total": 9000,
                },
                "fetched_at": "2026-05-31T00:00:00Z",
            }
        ]
        resolution = resolve_entities(rows, first_seen="2026-05-31T00:00:00Z")

        result = evaluate_entities(
            rows,
            resolution,
            run_id="run-1",
            rule_version="rules-v1",
            now="2026-05-31T00:00:00Z",
        )

        self.assertEqual(result.potential_candidates, [])
        self.assertEqual(len(result.edge_watch_candidates), 1)
        self.assertEqual(
            result.edge_watch_candidates[0].entity_id,
            resolution.entities[0].entity_id,
        )

    def test_three_strict_hn_stories_create_potential(self):
        rows = [
            {
                "id": 20 + i,
                "source": "hn_algolia",
                "external_id": f"7d:agent:{1000 + i}",
                "name": f"Show HN: Repo story {i}",
                "url": "https://github.com/owner/repo",
                "description": "",
                "metadata": {
                    "window": "7d",
                    "query_label": "agent",
                    "points": 60,
                    "created_at": "2026-05-28T00:00:00Z",
                    "story_id": str(1000 + i),
                },
                "fetched_at": "2026-05-31T00:00:00Z",
            }
            for i in range(3)
        ]
        resolution = resolve_entities(rows, first_seen="2026-05-31T00:00:00Z")

        result = evaluate_entities(
            rows,
            resolution,
            run_id="run-1",
            rule_version="rules-v1",
            now="2026-05-31T00:00:00Z",
        )

        self.assertEqual(len(resolution.entities), 1)
        self.assertEqual(result.potential_candidates[0].level, "potential")
        self.assertIn(
            "strict_story_count_7d",
            {row.metric_name for row in result.evidence_rows},
        )

    def test_two_huggingface_resources_48h_create_potential(self):
        rows = [
            {
                "id": 30,
                "source": "huggingface_spaces",
                "external_id": "user/clawdbot-demo",
                "name": "user/clawdbot-demo",
                "url": "https://huggingface.co/spaces/user/clawdbot-demo",
                "description": "",
                "metadata": {
                    "created_at": "2026-05-30T10:00:00Z",
                    "likes": 5,
                    "repository": "https://github.com/owner/repo",
                },
                "fetched_at": "2026-05-31T00:00:00Z",
            },
            {
                "id": 31,
                "source": "huggingface_models",
                "external_id": "lab/clawdbot-demo",
                "name": "lab/clawdbot-demo",
                "url": "https://huggingface.co/lab/clawdbot-demo",
                "description": "",
                "metadata": {
                    "created_at": "2026-05-30T18:00:00Z",
                    "likes": 2,
                    "repository": "https://github.com/owner/repo",
                },
                "fetched_at": "2026-05-31T00:00:00Z",
            },
        ]
        resolution = resolve_entities(rows, first_seen="2026-05-31T00:00:00Z")

        result = evaluate_entities(
            rows,
            resolution,
            run_id="run-1",
            rule_version="rules-v1",
            now="2026-05-31T00:00:00Z",
        )

        self.assertEqual(len(resolution.entities), 1)
        self.assertEqual(result.potential_candidates[0].level, "potential")
        self.assertIn(
            "hf_resources_48h",
            {row.metric_name for row in result.evidence_rows},
        )


if __name__ == "__main__":
    unittest.main()
