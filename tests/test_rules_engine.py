import unittest

from pipeline.decision.entity_resolution import Entity, ResolutionResult, resolve_entities
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
                "metadata": {
                    "score": 75,
                    "comments": 12,
                    "list": "topstories",
                    "created_at_unix": 1780185600,
                },
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

    def test_hn_uses_max_points_not_story_count_and_stops_at_watch(self):
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
        self.assertEqual(result.potential_candidates, [])
        self.assertEqual(len(result.edge_watch_candidates), 1)
        self.assertIn(
            "hn_max_points_7d",
            {row.metric_name for row in result.evidence_rows},
        )
        self.assertNotIn(
            "strict_story_count_7d",
            {row.metric_name for row in result.evidence_rows},
        )

    def test_hn_below_points_floor_is_discarded(self):
        rows = [
            {
                "id": 23,
                "source": "hn_algolia",
                "external_id": "7d:agent:low",
                "name": "Show HN: Low signal repo",
                "url": "https://github.com/owner/repo",
                "description": "",
                "metadata": {
                    "window": "7d",
                    "query_label": "agent",
                    "points": 29,
                    "created_at": "2026-05-30T00:00:00Z",
                    "story_id": "low",
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
        self.assertEqual(result.edge_watch_candidates, [])
        self.assertEqual(result.evidence_rows, [])

    def test_hn_classifier_noise_suppresses_hn_only_potential(self):
        rows = [
            {
                "id": 24,
                "source": "hn_firebase",
                "external_id": "hn-24",
                "name": "AI lab policy news",
                "url": "https://github.com/owner/repo",
                "description": "",
                "metadata": {
                    "score": 180,
                    "comments": 55,
                    "list": "topstories",
                    "created_at_unix": 1780185600,
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
            classifier_evidence=[
                {
                    "entity_id": "entity:hn-noise",
                    "source": "hn_llm_classifier",
                    "family": "hn",
                    "metric_name": "hn_projectness",
                    "metric_value": "news_article",
                    "signal_label": "noise",
                    "raw_url_or_ref": "item:24",
                }
            ],
        )

        self.assertEqual(result.potential_candidates, [])
        self.assertNotIn("hn_score", {row.metric_name for row in result.evidence_rows})

    def test_hn_classifier_project_below_breakthrough_stops_at_watch(self):
        rows = [
            {
                "id": 25,
                "source": "hn_firebase",
                "external_id": "hn-25",
                "name": "Show HN: Repo",
                "url": "https://github.com/owner/repo",
                "description": "",
                "metadata": {
                    "score": 180,
                    "comments": 55,
                    "list": "topstories",
                    "created_at_unix": 1780185600,
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
            classifier_evidence=[
                {
                    "entity_id": resolution.entities[0].entity_id,
                    "source": "hn_llm_classifier",
                    "family": "hn",
                    "metric_name": "hn_projectness",
                    "metric_value": "project",
                    "signal_label": "watch",
                    "raw_url_or_ref": "item:25",
                }
            ],
        )

        self.assertEqual(result.potential_candidates, [])
        self.assertEqual(len(result.edge_watch_candidates), 1)
        self.assertIn("hn_max_points_7d", {row.metric_name for row in result.evidence_rows})

    def test_hn_classifier_projectness_allows_hot_self_post_potential(self):
        rows = [
            {
                "id": 26,
                "source": "hn_firebase",
                "external_id": "hn-26",
                "name": "Show HN: Clawdbot",
                "url": "https://news.ycombinator.com/item?id=26",
                "description": "Launch post for a coding-agent review tool.",
                "metadata": {
                    "score": 220,
                    "comments": 55,
                    "list": "topstories",
                    "created_at_unix": 1780185600,
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
            classifier_evidence=[
                {
                    "entity_id": resolution.entities[0].entity_id,
                    "source": "hn_llm_classifier",
                    "family": "hn",
                    "metric_name": "hn_projectness",
                    "metric_value": "project",
                    "signal_label": "watch",
                    "raw_url_or_ref": "item:26",
                }
            ],
        )

        self.assertEqual(result.potential_candidates[0].level, "potential")
        self.assertIn("hn_max_points_7d", {row.metric_name for row in result.evidence_rows})

    def test_hn_self_post_url_is_not_strict_domain_without_classifier(self):
        rows = [
            {
                "id": 28,
                "source": "hn_firebase",
                "external_id": "hn-28",
                "name": "Show HN: Clawdbot",
                "url": "https://news.ycombinator.com/item?id=28",
                "description": "Launch post whose only URL is the HN discussion.",
                "metadata": {
                    "score": 220,
                    "comments": 55,
                    "list": "topstories",
                    "created_at_unix": 1780185600,
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

    def test_hn_classifier_news_suppresses_hot_self_post(self):
        rows = [
            {
                "id": 27,
                "source": "hn_firebase",
                "external_id": "hn-27",
                "name": "AI lab announces a new product policy",
                "url": "https://news.ycombinator.com/item?id=27",
                "description": "Hot discussion, not a concrete launch.",
                "metadata": {
                    "score": 260,
                    "comments": 155,
                    "list": "topstories",
                    "created_at_unix": 1780185600,
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
            classifier_evidence=[
                {
                    "entity_id": resolution.entities[0].entity_id,
                    "source": "hn_llm_classifier",
                    "family": "hn",
                    "metric_name": "hn_projectness",
                    "metric_value": "news_article",
                    "signal_label": "noise",
                    "raw_url_or_ref": "item:27",
                }
            ],
        )

        self.assertEqual(result.potential_candidates, [])
        self.assertEqual(result.edge_watch_candidates, [])

    def test_hn_company_product_on_content_domain_does_not_qualify(self):
        rows = [
            {
                "id": 29,
                "source": "hn_firebase",
                "external_id": "topstories:29",
                "name": "Arm Metis with GPT5.5 Cyber scores 98%",
                "url": "https://newsroom.arm.com/blog/metis-benchmark",
                "description": "",
                "metadata": {
                    "score": 260,
                    "comments": 100,
                    "list": "topstories",
                    "created_at_unix": 1780185600,
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
            classifier_evidence=[
                {
                    "entity_id": resolution.entities[0].entity_id,
                    "source": "hn_llm_classifier",
                    "family": "hn",
                    "metric_name": "hn_projectness",
                    "metric_value": "company_product",
                    "signal_label": "watch",
                    "raw_url_or_ref": "item:29",
                }
            ],
        )

        self.assertEqual(result.potential_candidates, [])
        self.assertEqual(result.edge_watch_candidates, [])
        self.assertEqual(result.evidence_rows, [])

    def test_hn_dedupes_algolia_and_firebase_story_before_max_points(self):
        rows = [
            {
                "id": 32,
                "source": "hn_firebase",
                "external_id": "topstories:123",
                "name": "Show HN: Repo",
                "url": "https://github.com/owner/repo",
                "description": "",
                "metadata": {
                    "score": 210,
                    "comments": 55,
                    "list": "topstories",
                    "created_at_unix": 1780185600,
                    "hn_url": "https://news.ycombinator.com/item?id=123",
                },
                "fetched_at": "2026-05-31T00:00:00Z",
            },
            {
                "id": 33,
                "source": "hn_algolia",
                "external_id": "7d:agent:123",
                "name": "Show HN: Repo",
                "url": "https://github.com/owner/repo",
                "description": "",
                "metadata": {
                    "window": "7d",
                    "points": 205,
                    "created_at": "2026-05-30T00:00:00Z",
                    "story_id": "123",
                    "hn_url": "https://news.ycombinator.com/item?id=123",
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

        hn_rows = [row for row in result.evidence_rows if row.family == "hn"]
        self.assertEqual(len(hn_rows), 1)
        self.assertEqual(hn_rows[0].metric_name, "hn_max_points_7d")
        self.assertEqual(hn_rows[0].metric_value, "210")
        self.assertEqual(result.potential_candidates[0].level, "potential")

    def test_x_social_evidence_can_promote_to_potential(self):
        entity = Entity(
            entity_id="entity:x",
            canonical_entity="owner/repo",
            canonical_key="github:owner/repo",
            key_type="github",
            aliases=("owner/repo",),
            source_refs=(),
        )

        result = evaluate_entities(
            [],
            ResolutionResult(entities=[entity], item_to_entity={}),
            run_id="run-x",
            rule_version="rules-v1",
            now="2026-05-31T00:00:00Z",
            classifier_evidence=[
                {
                    "entity_id": "entity:x",
                    "source": "x_tweets",
                    "family": "x_social",
                    "metric_name": "x_tier",
                    "metric_value": "potential",
                    "signal_label": "potential",
                    "raw_url_or_ref": "tweet:t1,tweet:t2",
                    "note": "Two credible authors cited the same repo.",
                }
            ],
        )

        self.assertEqual(result.potential_candidates[0].level, "potential")
        self.assertEqual(result.evidence_rows[0].family, "x_social")
        self.assertEqual(result.evidence_rows[0].metric_name, "x_tier")

    def test_x_social_uncited_potential_is_ignored(self):
        entity = Entity(
            entity_id="entity:x",
            canonical_entity="owner/repo",
            canonical_key="github:owner/repo",
            key_type="github",
            aliases=("owner/repo",),
            source_refs=(),
        )

        result = evaluate_entities(
            [],
            ResolutionResult(entities=[entity], item_to_entity={}),
            run_id="run-x",
            rule_version="rules-v1",
            now="2026-05-31T00:00:00Z",
            classifier_evidence=[
                {
                    "entity_id": "entity:x",
                    "source": "x_tweets",
                    "family": "x_social",
                    "metric_name": "x_tier",
                    "metric_value": "potential",
                    "signal_label": "potential",
                    "raw_url_or_ref": "",
                    "note": "No cited tweet ids.",
                }
            ],
        )

        self.assertEqual(result.potential_candidates, [])
        self.assertEqual(result.edge_watch_candidates, [])

    def test_npm_search_row_enqueues_registry_backfill_job(self):
        rows = [
            {
                "id": 60,
                "source": "npm_search",
                "external_id": "agent:demo-package",
                "name": "demo-package",
                "url": "https://www.npmjs.com/package/demo-package",
                "description": "demo package",
                "metadata": {
                    "weekly_downloads": 6831,
                    "monthly_downloads": 38818,
                    "repository": "git+https://github.com/owner/repo.git",
                },
                "fetched_at": "2026-05-31T00:00:00Z",
            }
        ]
        resolution = resolve_entities(rows, first_seen="2026-05-31T00:00:00Z")

        result = evaluate_entities(
            rows,
            resolution,
            run_id="run-npm",
            rule_version="rules-v1",
            now="2026-05-31T00:00:00Z",
        )

        self.assertEqual(len(result.backfill_jobs), 1)
        self.assertEqual(result.backfill_jobs[0].source, "npm_registry")
        self.assertEqual(result.backfill_jobs[0].reason, "package_downloads:demo-package")

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
