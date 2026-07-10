import sqlite3
import unittest

from pipeline.decision.schema import init_decision_db


NOW = "2026-05-31T00:00:00Z"


class FakeGitHubReadmeClient:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def get_readme_text(self, repo_key):
        self.calls.append(repo_key)
        return self.text


class ReadmeEnrichmentTest(unittest.TestCase):
    @staticmethod
    def insert_candidate(
        conn,
        *,
        entity_id,
        canonical_entity,
        canonical_key,
        key_type="name",
    ):
        conn.execute(
            """
            insert into entities(
                entity_id, canonical_entity, canonical_key, key_type, first_seen,
                aliases_json, source_item_ids_json
            ) values (?, ?, ?, ?, ?, '[]', '[]')
            """,
            (entity_id, canonical_entity, canonical_key, key_type, NOW),
        )
        conn.execute(
            """
            insert into potential_candidates(
                entity_id, run_id, level, fired_families_json, first_trigger_at
            ) values (?, 'run-1', 'potential', '[]', ?)
            """,
            (entity_id, NOW),
        )

    @staticmethod
    def insert_resolver_alias(conn, *, entity_id, external_id, alias, approved=1):
        conn.execute(
            """
            insert into alias_links(
                entity_id, source, external_id, alias, confidence, origin,
                approved, created_at
            ) values (?, 'resolver', ?, ?, ?, 'resolver', ?, ?)
            """,
            (
                entity_id,
                external_id,
                alias,
                "deterministic" if approved else "low",
                approved,
                NOW,
            ),
        )

    def test_parse_github_repo_from_url_and_key(self):
        from pipeline.decision.readme_enrichment import github_repo_key_from_link

        self.assertEqual(github_repo_key_from_link("github:Owner/Repo"), "owner/repo")
        self.assertEqual(
            github_repo_key_from_link("https://github.com/Owner/Repo?tab=readme"),
            "owner/repo",
        )
        self.assertIsNone(github_repo_key_from_link("https://example.com"))

    def test_fetches_bounds_and_caches_readme_excerpt(self):
        from pipeline.decision.readme_enrichment import (
            fetch_and_cache_readme_excerpt,
            read_cached_readme_excerpt,
        )

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)
        client = FakeGitHubReadmeClient("# Title\n" + ("A" * 9000))

        response = fetch_and_cache_readme_excerpt(conn, client=client, repo_key="owner/repo")
        cached = read_cached_readme_excerpt(conn, repo_key="owner/repo")

        self.assertEqual(client.calls, ["owner/repo"])
        self.assertEqual(response["repo_key"], "owner/repo")
        self.assertEqual(len(response["excerpt"]), 8000)
        self.assertEqual(len(response["preview"]), 1000)
        self.assertEqual(cached["excerpt"], response["excerpt"])

    def test_cache_prevents_second_fetch(self):
        from pipeline.decision.readme_enrichment import fetch_and_cache_readme_excerpt

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)
        client = FakeGitHubReadmeClient("hello readme")

        first = fetch_and_cache_readme_excerpt(conn, client=client, repo_key="owner/repo")
        second = fetch_and_cache_readme_excerpt(conn, client=client, repo_key="owner/repo")

        self.assertEqual(first, second)
        self.assertEqual(client.calls, ["owner/repo"])

    def test_approved_resolver_github_alias_makes_name_candidate_eligible(self):
        from pipeline.decision.candidate_context import context_bundle_for_entity
        from pipeline.decision.readme_enrichment import enrich_candidate_readmes

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)
        self.insert_candidate(
            conn,
            entity_id="entity:claw",
            canonical_entity="Claw",
            canonical_key="name:claw",
        )
        self.insert_resolver_alias(
            conn,
            entity_id="entity:claw",
            external_id="name:claw",
            alias="github:Owner/Claw",
        )
        conn.commit()
        client = FakeGitHubReadmeClient("# Claw\nFirst-party project context.")

        summary = enrich_candidate_readmes(
            conn,
            run_id="run-1",
            client=client,
            limit=10,
        )

        self.assertEqual(summary, {"fetched": 1, "cached": 0, "skipped": 0})
        self.assertEqual(client.calls, ["owner/claw"])
        context = context_bundle_for_entity(
            conn,
            entity_id="entity:claw",
            run_id="run-1",
        )
        self.assertEqual(context["canonical_link"].lower(), "https://github.com/owner/claw")
        self.assertEqual(context["binding_confidence"], "resolved")
        self.assertTrue(context["readme_excerpt_available"])
        self.assertEqual(context["context_preview"], "Claw First-party project context.")

    def test_approved_github_alias_is_used_when_domain_alias_was_recorded_first(self):
        from pipeline.decision.readme_enrichment import enrich_candidate_readmes

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)
        self.insert_candidate(
            conn,
            entity_id="entity:product",
            canonical_entity="Product",
            canonical_key="name:product",
        )
        for alias in ("domain:product.example", "github:Owner/Product"):
            self.insert_resolver_alias(
                conn,
                entity_id="entity:product",
                external_id="name:product",
                alias=alias,
            )
        conn.commit()
        client = FakeGitHubReadmeClient("# Product")

        summary = enrich_candidate_readmes(
            conn,
            run_id="run-1",
            client=client,
            limit=10,
        )

        self.assertEqual(summary["fetched"], 1)
        self.assertEqual(client.calls, ["owner/product"])

    def test_canonical_and_alias_repo_forms_are_normalized_before_fetch(self):
        from pipeline.decision.candidate_context import context_bundle_for_entity
        from pipeline.decision.readme_enrichment import enrich_candidate_readmes

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)
        entities = (
            ("entity:canonical", "Repo", "github:Owner/Repo.git", "github"),
            ("entity:alias", "Repo alias", "name:repo", "name"),
        )
        for entity_id, name, canonical_key, key_type in entities:
            self.insert_candidate(
                conn,
                entity_id=entity_id,
                canonical_entity=name,
                canonical_key=canonical_key,
                key_type=key_type,
            )
        self.insert_resolver_alias(
            conn,
            entity_id="entity:alias",
            external_id="name:repo",
            alias="github:owner/repo",
        )
        conn.commit()
        client = FakeGitHubReadmeClient("# Repo")

        summary = enrich_candidate_readmes(
            conn,
            run_id="run-1",
            client=client,
            limit=10,
        )

        self.assertEqual(summary, {"fetched": 1, "cached": 0, "skipped": 0})
        self.assertEqual(client.calls, ["owner/repo"])
        for entity_id in ("entity:canonical", "entity:alias"):
            context = context_bundle_for_entity(
                conn,
                entity_id=entity_id,
                run_id="run-1",
            )
            self.assertTrue(context["readme_excerpt_available"])
            self.assertEqual(context["context_preview"], "Repo")

    def test_unapproved_alias_and_low_confidence_proposal_do_not_trigger_fetch(self):
        from pipeline.decision.readme_enrichment import enrich_candidate_readmes

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)
        self.insert_candidate(
            conn,
            entity_id="entity:uncertain",
            canonical_entity="Uncertain",
            canonical_key="name:uncertain",
        )
        self.insert_resolver_alias(
            conn,
            entity_id="entity:uncertain",
            external_id="name:uncertain",
            alias="github:wrong/uncertain",
            approved=0,
        )
        conn.execute(
            """
            insert into entity_merge_proposals(
                run_id, orphan, target_entity_id, confidence, reason, status, created_at
            ) values ('run-1', 'github:maybe/uncertain', 'entity:uncertain',
                      0.62, 'resolver proposed link', 'open', ?)
            """,
            (NOW,),
        )
        conn.commit()
        client = FakeGitHubReadmeClient("must not be fetched")

        summary = enrich_candidate_readmes(
            conn,
            run_id="run-1",
            client=client,
            limit=10,
        )

        self.assertEqual(summary, {"fetched": 0, "cached": 0, "skipped": 0})
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
