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

    def test_repofomo_flat_growth_is_discarded_even_above_floor(self):
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
        self.assertEqual(result.edge_watch_candidates, [])
        self.assertEqual(result.evidence_rows, [])

    def test_repofomo_new_forks_do_not_promote_without_star_momentum(self):
        rows = [
            {
                "id": 4,
                "source": "github_movers_repofomo",
                "external_id": "repofomo:owner/repo",
                "name": "owner/repo",
                "url": "https://github.com/owner/repo",
                "description": "",
                "metadata": {
                    "stars_7d": 0,
                    "stars_30d": 6028,
                    "stars_60d": 15962,
                    "stars_total": 156152,
                    "new_forks": 647,
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

    def test_repofomo_uses_acceleration_ratio_for_high_potential(self):
        rows = [
            {
                "id": 5,
                "source": "github_movers_repofomo",
                "external_id": "repofomo:owner/repo",
                "name": "owner/repo",
                "url": "https://github.com/owner/repo",
                "description": "",
                "metadata": {
                    "stars_7d": 210,
                    "stars_30d": 300,
                    "stars_60d": 2000,
                    "stars_total": 2500,
                },
                "fetched_at": "2026-05-31T00:00:00Z",
            }
        ]
        resolution = resolve_entities(rows, first_seen="2026-05-31T00:00:00Z")

        result = evaluate_entities(
            rows,
            resolution,
            run_id="run-1",
            rule_version="rules-v2",
            now="2026-05-31T00:00:00Z",
        )

        self.assertEqual(result.potential_candidates[0].level, "high_potential")
        self.assertEqual(result.evidence_rows[0].metric_name, "stars_accel_7d_vs_30d")
        self.assertEqual(result.evidence_rows[0].metric_value, "3")
        self.assertEqual(result.evidence_rows[0].rule_id, "repofomo_stars_accel_high_potential")

    def test_repofomo_missing_baseline_caps_at_watch(self):
        rows = [
            {
                "id": 6,
                "source": "github_movers_repofomo",
                "external_id": "repofomo:owner/repo",
                "name": "owner/repo",
                "url": "https://github.com/owner/repo",
                "description": "",
                "metadata": {
                    "stars_7d": 100,
                    "stars_30d": 0,
                    "stars_60d": 0,
                    "stars_total": 100,
                },
                "fetched_at": "2026-05-31T00:00:00Z",
            }
        ]
        resolution = resolve_entities(rows, first_seen="2026-05-31T00:00:00Z")

        result = evaluate_entities(
            rows,
            resolution,
            run_id="run-1",
            rule_version="rules-v2",
            now="2026-05-31T00:00:00Z",
        )

        self.assertEqual(result.potential_candidates, [])
        self.assertEqual(len(result.edge_watch_candidates), 1)
        self.assertEqual(result.evidence_rows[0].rule_id, "repofomo_missing_baseline_watch")

    def test_trending_repos_rising_daily_row_is_watch_only(self):
        rows = [
            {
                "id": 7,
                "source": "github_movers_trending_repos",
                "external_id": "daily:owner/repo",
                "name": "owner/repo",
                "url": "https://github.com/owner/repo",
                "description": "",
                "metadata": {
                    "period": "daily",
                    "stars_velocity": 1800,
                    "forks_velocity": 120,
                    "sparkline": [31, 33, 37, 23, 29, 190, 410],
                },
                "fetched_at": "2026-05-31T00:00:00Z",
            }
        ]
        resolution = resolve_entities(rows, first_seen="2026-05-31T00:00:00Z")

        result = evaluate_entities(
            rows,
            resolution,
            run_id="run-1",
            rule_version="rules-v2",
            now="2026-05-31T00:00:00Z",
        )

        self.assertEqual(result.potential_candidates, [])
        self.assertEqual(len(result.edge_watch_candidates), 1)
        self.assertEqual(result.evidence_rows[0].rule_id, "trending_repos_direction_watch")
        self.assertEqual(result.backfill_jobs[0].source, "github_stargazers")
        self.assertEqual(result.backfill_jobs[0].reason, "trending_repos_rising_watch")

    def test_trending_repos_short_sparkline_is_excluded(self):
        rows = [
            {
                "id": 8,
                "source": "github_movers_trending_repos",
                "external_id": "daily:owner/repo",
                "name": "owner/repo",
                "url": "https://github.com/owner/repo",
                "description": "",
                "metadata": {
                    "period": "daily",
                    "stars_velocity": 1591,
                    "forks_velocity": 94,
                    "sparkline": [1591],
                },
                "fetched_at": "2026-05-31T00:00:00Z",
            }
        ]
        resolution = resolve_entities(rows, first_seen="2026-05-31T00:00:00Z")

        result = evaluate_entities(
            rows,
            resolution,
            run_id="run-1",
            rule_version="rules-v2",
            now="2026-05-31T00:00:00Z",
        )

        self.assertEqual(result.potential_candidates, [])
        self.assertEqual(result.edge_watch_candidates, [])
        self.assertEqual(result.backfill_jobs, [])
        self.assertEqual(result.evidence_rows, [])

    def test_trending_repos_backfill_is_skipped_when_repofomo_row_exists(self):
        rows = [
            {
                "id": 9,
                "source": "github_movers_trending_repos",
                "external_id": "daily:owner/repo",
                "name": "owner/repo",
                "url": "https://github.com/owner/repo",
                "description": "",
                "metadata": {
                    "period": "daily",
                    "stars_velocity": 1800,
                    "forks_velocity": 120,
                    "sparkline": [31, 33, 37, 23, 29, 190, 410],
                },
                "fetched_at": "2026-05-31T00:00:00Z",
            },
            {
                "id": 10,
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
            },
        ]
        resolution = resolve_entities(rows, first_seen="2026-05-31T00:00:00Z")

        result = evaluate_entities(
            rows,
            resolution,
            run_id="run-1",
            rule_version="rules-v2",
            now="2026-05-31T00:00:00Z",
        )

        self.assertEqual(len(result.edge_watch_candidates), 1)
        self.assertEqual(result.backfill_jobs, [])

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

    def test_hn_company_product_on_article_path_does_not_use_domain_bypass(self):
        rows = [
            {
                "id": 30,
                "source": "hn_firebase",
                "external_id": "beststories:48318174",
                "name": "Claude Code – Everything you can configure that the docs don't tell you",
                "url": "https://buildingbetter.tech/p/i-read-the-claude-code-source-code",
                "description": "",
                "metadata": {
                    "score": 324,
                    "comments": 64,
                    "list": "beststories",
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
                    "signal_label": "context",
                    "raw_url_or_ref": "item:30",
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

    def test_npm_registry_uses_rising_ratio_for_high_potential(self):
        entity = Entity(
            entity_id="entity:npm",
            canonical_entity="demo-package",
            canonical_key="npm:demo-package",
            key_type="npm",
            aliases=("demo-package",),
            source_refs=(),
        )

        result = evaluate_entities(
            [],
            ResolutionResult(entities=[entity], item_to_entity={}),
            run_id="run-npm",
            rule_version="rules-v2",
            now="2026-05-31T00:00:00Z",
            classifier_evidence=[
                {
                    "entity_id": "entity:npm",
                    "source": "npm_registry",
                    "family": "package_family",
                    "metric_name": "daily_downloads",
                    "metric_value": "40000",
                    "alias": "demo-package",
                    "event_at": "2026-05-31T00:00:00Z",
                },
                {
                    "entity_id": "entity:npm",
                    "source": "npm_registry",
                    "family": "package_family",
                    "metric_name": "downloads_7d",
                    "metric_value": "70000",
                    "alias": "demo-package",
                    "event_at": "2026-05-31T00:00:00Z",
                },
            ],
        )

        self.assertEqual(result.potential_candidates[0].level, "high_potential")
        self.assertEqual(result.evidence_rows[0].metric_name, "daily_downloads_rising_ratio")
        self.assertEqual(result.evidence_rows[0].metric_value, "4")
        self.assertEqual(result.evidence_rows[0].rule_id, "npm_registry_daily_downloads_high_potential")

    def test_npm_registry_flat_large_package_is_discarded(self):
        entity = Entity(
            entity_id="entity:npm",
            canonical_entity="demo-package",
            canonical_key="npm:demo-package",
            key_type="npm",
            aliases=("demo-package",),
            source_refs=(),
        )

        result = evaluate_entities(
            [],
            ResolutionResult(entities=[entity], item_to_entity={}),
            run_id="run-npm",
            rule_version="rules-v2",
            now="2026-05-31T00:00:00Z",
            classifier_evidence=[
                {
                    "entity_id": "entity:npm",
                    "source": "npm_registry",
                    "family": "package_family",
                    "metric_name": "daily_downloads",
                    "metric_value": "120000",
                    "alias": "demo-package",
                    "event_at": "2026-05-31T00:00:00Z",
                },
                {
                    "entity_id": "entity:npm",
                    "source": "npm_registry",
                    "family": "package_family",
                    "metric_name": "downloads_7d",
                    "metric_value": "840000",
                    "alias": "demo-package",
                    "event_at": "2026-05-31T00:00:00Z",
                },
            ],
        )

        self.assertEqual(result.potential_candidates, [])
        self.assertEqual(result.edge_watch_candidates, [])
        self.assertEqual(result.evidence_rows, [])

    def test_npm_registry_missing_baseline_caps_at_watch(self):
        entity = Entity(
            entity_id="entity:npm",
            canonical_entity="demo-package",
            canonical_key="npm:demo-package",
            key_type="npm",
            aliases=("demo-package",),
            source_refs=(),
        )

        result = evaluate_entities(
            [],
            ResolutionResult(entities=[entity], item_to_entity={}),
            run_id="run-npm",
            rule_version="rules-v2",
            now="2026-05-31T00:00:00Z",
            classifier_evidence=[
                {
                    "entity_id": "entity:npm",
                    "source": "npm_registry",
                    "family": "package_family",
                    "metric_name": "daily_downloads",
                    "metric_value": "120000",
                    "alias": "demo-package",
                    "event_at": "2026-05-31T00:00:00Z",
                },
            ],
        )

        self.assertEqual(result.potential_candidates, [])
        self.assertEqual(len(result.edge_watch_candidates), 1)
        self.assertEqual(result.evidence_rows[0].rule_id, "npm_registry_daily_downloads_watch")

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

    def test_two_huggingface_resources_without_github_link_cap_at_watch(self):
        rows = [
            {
                "id": 32,
                "source": "huggingface_spaces",
                "external_id": "user/clawdbot-demo",
                "name": "Clawdbot Demo",
                "url": "https://huggingface.co/spaces/user/clawdbot-demo",
                "description": "",
                "metadata": {
                    "created_at": "2026-05-30T10:00:00Z",
                    "likes": 5,
                },
                "fetched_at": "2026-05-31T00:00:00Z",
            },
            {
                "id": 33,
                "source": "huggingface_models",
                "external_id": "lab/clawdbot-demo",
                "name": "Clawdbot Demo",
                "url": "https://huggingface.co/lab/clawdbot-demo",
                "description": "",
                "metadata": {
                    "created_at": "2026-05-30T18:00:00Z",
                    "likes": 2,
                },
                "fetched_at": "2026-05-31T00:00:00Z",
            },
        ]
        resolution = resolve_entities(rows, first_seen="2026-05-31T00:00:00Z")

        result = evaluate_entities(
            rows,
            resolution,
            run_id="run-1",
            rule_version="rules-v2",
            now="2026-05-31T00:00:00Z",
        )

        self.assertEqual(len(resolution.entities), 1)
        self.assertEqual(result.potential_candidates, [])
        self.assertEqual(len(result.edge_watch_candidates), 1)
        self.assertEqual(result.evidence_rows[0].rule_id, "huggingface_resources_48h_watch")


if __name__ == "__main__":
    unittest.main()
