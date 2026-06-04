from __future__ import annotations

import sqlite3
import unittest

from pipeline.decision.schema import init_decision_db


class FakeReadmeClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[str] = []

    def get_readme_text(self, repo_key: str) -> str:
        self.calls.append(repo_key)
        return self.text


class FakeGitHubFileClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[tuple[str, str]] = []

    def get_file_text(self, repo_key: str, path: str) -> str:
        self.calls.append((repo_key, path))
        return self.text


class FakePageClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[str] = []

    def fetch_text(self, url: str) -> str:
        self.calls.append(url)
        return self.text


class FakeSearchClient:
    def __init__(self, results: list[dict[str, str]]) -> None:
        self.results = results
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        self.calls.append((query, limit))
        return self.results[:limit]


class InvestigatorToolsTest(unittest.TestCase):
    def make_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        self.addCleanup(conn.close)
        return conn

    def test_tool_registry_exposes_only_primitive_tools(self) -> None:
        from pipeline.decision.layer2_investigator_tools import (
            ScoringInvestigatorTools,
        )

        conn = self.make_conn()
        tools = ScoringInvestigatorTools(conn, decision_run_id="decision-run")

        self.assertEqual(
            sorted(tools.available_tools()),
            [
                "fetch_github_file",
                "fetch_github_readme",
                "fetch_homepage_or_docs",
                "read_evidence_rows",
                "web_search",
            ],
        )

    def test_read_evidence_rows_returns_bounded_rows(self) -> None:
        from pipeline.decision.layer2_investigator_tools import (
            ScoringInvestigatorTools,
        )

        conn = self.make_conn()
        for index in range(3):
            conn.execute(
                """
                insert into evidence_rows(
                  entity_id, canonical_entity, alias, source, event_at,
                  relative_to_reference, metric_name, metric_value, family,
                  rule_id, rule_version, signal_label, historical_safety,
                  note, raw_url_or_ref, run_id
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "entity:repo",
                    "owner/repo",
                    None,
                    "github",
                    f"2026-06-01T00:00:0{index}Z",
                    None,
                    "stars_today",
                    str(100 + index),
                    "github",
                    "rule",
                    "v1",
                    "GitHub momentum",
                    "safe",
                    "note",
                    "https://github.com/owner/repo",
                    "decision-run",
                ),
            )
        conn.commit()

        tools = ScoringInvestigatorTools(conn, decision_run_id="decision-run")
        result = tools.available_tools()["read_evidence_rows"](
            {"entity_id": "entity:repo"}
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["rows"]), 3)
        self.assertEqual(result["rows"][0]["metric_name"], "stars_today")

    def test_fetch_github_readme_uses_existing_cache(self) -> None:
        from pipeline.decision.layer2_investigator_tools import (
            ScoringInvestigatorTools,
        )

        conn = self.make_conn()
        client = FakeReadmeClient("README " + ("A" * 9000))
        tools = ScoringInvestigatorTools(
            conn, decision_run_id="decision-run", readme_client=client
        )

        first = tools.available_tools()["fetch_github_readme"](
            {"repo_key": "Owner/Repo"}
        )
        second = tools.available_tools()["fetch_github_readme"](
            {"repo_key": "owner/repo"}
        )

        self.assertEqual(first["status"], "ok")
        self.assertEqual(first, second)
        self.assertEqual(first["repo_key"], "owner/repo")
        self.assertLessEqual(len(first["excerpt"]), 8000)
        self.assertEqual(client.calls, ["owner/repo"])

    def test_fetch_github_file_is_path_bounded_and_cached(self) -> None:
        from pipeline.decision.layer2_investigator_tools import (
            ScoringInvestigatorTools,
        )

        conn = self.make_conn()
        client = FakeGitHubFileClient("package json " + ("B" * 9000))
        tools = ScoringInvestigatorTools(
            conn, decision_run_id="decision-run", github_file_client=client
        )

        rejected = tools.available_tools()["fetch_github_file"](
            {"repo_key": "owner/repo", "path": "../secrets"}
        )
        first = tools.available_tools()["fetch_github_file"](
            {"repo_key": "owner/repo", "path": "package.json"}
        )
        second = tools.available_tools()["fetch_github_file"](
            {"repo_key": "owner/repo", "path": "package.json"}
        )

        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(first["status"], "ok")
        self.assertEqual(first, second)
        self.assertLessEqual(len(first["excerpt"]), 6000)
        self.assertEqual(client.calls, [("owner/repo", "package.json")])

    def test_fetch_homepage_rejects_private_urls_and_caches_public_pages(self) -> None:
        from pipeline.decision.layer2_investigator_tools import (
            ScoringInvestigatorTools,
        )

        conn = self.make_conn()
        client = FakePageClient("Homepage " + ("C" * 9000))
        tools = ScoringInvestigatorTools(
            conn, decision_run_id="decision-run", page_client=client
        )

        rejected = tools.available_tools()["fetch_homepage_or_docs"](
            {"url": "http://127.0.0.1:8000/admin"}
        )
        first = tools.available_tools()["fetch_homepage_or_docs"](
            {"url": "https://example.com/docs"}
        )
        second = tools.available_tools()["fetch_homepage_or_docs"](
            {"url": "https://example.com/docs"}
        )

        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(first["status"], "ok")
        self.assertEqual(first, second)
        self.assertLessEqual(len(first["excerpt"]), 6000)
        self.assertEqual(client.calls, ["https://example.com/docs"])

    def test_web_search_is_bounded_and_cached(self) -> None:
        from pipeline.decision.layer2_investigator_tools import (
            ScoringInvestigatorTools,
        )

        conn = self.make_conn()
        search = FakeSearchClient(
            [
                {"title": "One", "url": "https://example.com/1"},
                {"title": "Two", "url": "https://example.com/2"},
            ]
        )
        tools = ScoringInvestigatorTools(
            conn, decision_run_id="decision-run", web_search_client=search
        )

        first = tools.available_tools()["web_search"](
            {"query": "openclaw release harness", "limit": 5}
        )
        second = tools.available_tools()["web_search"](
            {"query": "openclaw release harness", "limit": 5}
        )

        self.assertEqual(first["status"], "ok")
        self.assertEqual(first, second)
        self.assertEqual(len(first["results"]), 2)
        self.assertEqual(search.calls, [("openclaw release harness", 5)])


if __name__ == "__main__":
    unittest.main()
