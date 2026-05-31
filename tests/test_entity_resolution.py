import unittest

from pipeline.decision.entity_resolution import (
    extract_keys,
    normalize_name_key,
    resolve_entities,
)


class EntityResolutionTest(unittest.TestCase):
    def test_extracts_github_repo_from_url_and_text(self):
        row = {
            "id": 1,
            "source": "hn_algolia",
            "external_id": "hn-1",
            "name": "Show HN: Demo",
            "url": "https://news.ycombinator.com/item?id=1",
            "description": "Repo https://github.com/Owner/Repo?tab=readme",
            "metadata": {},
        }

        keys = extract_keys(row)

        self.assertIn("github:owner/repo", keys.github_repo_keys)

    def test_shared_domains_are_not_project_domain_keys(self):
        row = {
            "id": 2,
            "source": "product_hunt",
            "external_id": "ph-1",
            "name": "Demo",
            "url": "https://producthunt.com/posts/demo",
            "description": "",
            "metadata": {"website": "https://demo.vercel.app"},
        }

        keys = extract_keys(row)

        self.assertEqual(keys.domain_keys, set())

    def test_specific_domain_key_is_allowed(self):
        row = {
            "id": 3,
            "source": "product_hunt",
            "external_id": "ph-2",
            "name": "Demo",
            "url": "https://producthunt.com/posts/demo",
            "description": "",
            "metadata": {"website": "https://openclaw.dev"},
        }

        keys = extract_keys(row)

        self.assertEqual(keys.domain_keys, {"domain:openclaw.dev"})

    def test_generic_name_key_is_alias_only(self):
        self.assertIsNone(normalize_name_key("agent"))
        self.assertIsNone(normalize_name_key("MCP"))
        self.assertEqual(
            normalize_name_key("Claude Code Router"),
            "name:claude-code-router",
        )

    def test_resolve_entities_unions_by_strong_github_key(self):
        rows = [
            {
                "id": 10,
                "source": "github_trending",
                "external_id": "owner/repo",
                "name": "owner/repo",
                "url": "https://github.com/owner/repo",
                "description": "",
                "metadata": {},
            },
            {
                "id": 11,
                "source": "hn_algolia",
                "external_id": "hn-11",
                "name": "Show HN: Repo",
                "url": "https://github.com/owner/repo",
                "description": "",
                "metadata": {},
            },
        ]

        result = resolve_entities(rows, first_seen="2026-05-31T00:00:00Z")

        self.assertEqual(len(result.entities), 1)
        entity = result.entities[0]
        self.assertEqual(entity.canonical_key, "github:owner/repo")
        self.assertEqual({ref.item_id for ref in entity.source_refs}, {10, 11})

    def test_hn_subdomain_project_does_not_merge_with_parent_domain_article(self):
        rows = [
            {
                "id": 20,
                "source": "hn_algolia",
                "external_id": "hn-article",
                "name": "AI Agent Permissions: The Missing Layer Between Works and Safe",
                "url": "https://scalex.dev/blog/ai-agent-permissions/",
                "description": "",
                "metadata": {},
            },
            {
                "id": 21,
                "source": "hn_algolia",
                "external_id": "hn-game",
                "name": "Show HN: Continue? Y/N: A 60-second game about AI agent permission fatigue",
                "url": "https://llmgame.scalex.dev",
                "description": "",
                "metadata": {},
            },
        ]

        result = resolve_entities(rows, first_seen="2026-05-31T00:00:00Z")

        canonical_keys = {entity.canonical_key for entity in result.entities}
        self.assertEqual(canonical_keys, {"domain:scalex.dev", "domain:llmgame.scalex.dev"})
        self.assertNotEqual(result.item_to_entity[20], result.item_to_entity[21])


if __name__ == "__main__":
    unittest.main()
