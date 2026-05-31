import sqlite3
import unittest

from pipeline.decision.llm_provider import FakeLLMProvider
from pipeline.decision.schema import init_decision_db


class FakeSearchClient:
    def __init__(self, results_by_query):
        self.results_by_query = results_by_query
        self.calls = []

    def search(self, query, limit=5):
        self.calls.append({"query": query, "limit": limit})
        return self.results_by_query.get(query, [])


class WebResearchTest(unittest.TestCase):
    def test_agentic_research_searches_then_finalizes_valid_github_link(self):
        from pipeline.decision.web_research import research_candidate_link

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)
        provider = FakeLLMProvider(
            [
                {
                    "action": "search",
                    "query": "Clawdbot GitHub repo",
                    "selected": None,
                    "reason": "need repo",
                },
                {
                    "action": "final",
                    "query": "",
                    "selected": {
                        "type": "github",
                        "key": "github:owner/clawdbot",
                        "url": "https://github.com/owner/clawdbot",
                        "confidence": 0.91,
                    },
                    "reason": "official repo result",
                },
            ]
        )
        search = FakeSearchClient(
            {
                "Clawdbot GitHub repo": [
                    {"title": "Clawdbot", "url": "https://github.com/owner/clawdbot"}
                ]
            }
        )

        result = research_candidate_link(
            conn,
            entity_key="name:clawdbot",
            evidence_context={"canonical_entity": "Clawdbot", "evidence": ["X potential"]},
            provider=provider,
            search_client=search,
            max_rounds=3,
            max_results=5,
        )

        self.assertEqual(result["resolved_links"][0]["key"], "github:owner/clawdbot")
        self.assertEqual(result["rounds"], 2)
        self.assertEqual(len(search.calls), 1)

    def test_agentic_research_stops_at_max_rounds_and_caches(self):
        from pipeline.decision.web_research import research_candidate_link

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)
        provider = FakeLLMProvider(
            [
                {"action": "search", "query": "first", "selected": None, "reason": "try"},
                {
                    "action": "search",
                    "query": "second",
                    "selected": None,
                    "reason": "try again",
                },
                {
                    "action": "search",
                    "query": "third",
                    "selected": None,
                    "reason": "last try",
                },
            ]
        )
        search = FakeSearchClient({"first": [], "second": [], "third": []})

        first = research_candidate_link(
            conn,
            entity_key="name:nope",
            evidence_context={},
            provider=provider,
            search_client=search,
            max_rounds=3,
        )
        second = research_candidate_link(
            conn,
            entity_key="name:nope",
            evidence_context={},
            provider=provider,
            search_client=search,
            max_rounds=3,
        )

        self.assertEqual(first["resolved_links"], [])
        self.assertEqual(first["source"], "agentic_link_research")
        self.assertEqual(first, second)
        self.assertEqual(len(search.calls), 3)


if __name__ == "__main__":
    unittest.main()
