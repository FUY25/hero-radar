import sqlite3
import unittest

from pipeline.decision.schema import init_decision_db


class FakeGitHubReadmeClient:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def get_readme_text(self, repo_key):
        self.calls.append(repo_key)
        return self.text


class ReadmeEnrichmentTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
