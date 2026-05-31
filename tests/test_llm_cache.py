from __future__ import annotations

import sqlite3
import unittest

from pipeline.decision.schema import init_decision_db


class LlmCacheTest(unittest.TestCase):
    def test_cache_key_is_stable_and_prompt_version_scoped(self) -> None:
        from pipeline.decision.llm_cache import cache_key_for

        key_a = cache_key_for(
            provider="deepseek",
            model="deepseek-v4-flash",
            prompt_version="x-v1",
            task="x_stage1",
            input_payload={"b": 2, "a": 1},
        )
        key_b = cache_key_for(
            provider="deepseek",
            model="deepseek-v4-flash",
            prompt_version="x-v1",
            task="x_stage1",
            input_payload={"a": 1, "b": 2},
        )
        key_c = cache_key_for(
            provider="deepseek",
            model="deepseek-v4-flash",
            prompt_version="x-v2",
            task="x_stage1",
            input_payload={"a": 1, "b": 2},
        )

        self.assertEqual(key_a, key_b)
        self.assertNotEqual(key_a, key_c)

    def test_get_or_store_llm_cache_round_trips_json(self) -> None:
        from pipeline.decision.llm_cache import get_cached_response, store_cached_response

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)

        key = store_cached_response(
            conn,
            provider="deepseek",
            model="deepseek-v4-flash",
            prompt_version="hn-v1",
            task="hn_classifier",
            input_payload={"item_id": 1},
            request_payload={"messages": []},
            response_payload={"projectness": "project"},
            status="ok",
        )

        cached = get_cached_response(conn, key)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["response_json"]["projectness"], "project")
        self.assertEqual(cached["request_json"]["messages"], [])
        self.assertEqual(cached["prompt_version"], "hn-v1")
        self.assertEqual(cached["status"], "ok")

    def test_store_updates_status_and_error_for_same_key(self) -> None:
        from pipeline.decision.llm_cache import get_cached_response, store_cached_response

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)

        key = store_cached_response(
            conn,
            provider="deepseek",
            model="deepseek-v4-flash",
            prompt_version="x-stage1-v1",
            task="x_stage1",
            input_payload={"batch": [1]},
            request_payload={"messages": ["old"]},
            response_payload={"triage": []},
            status="ok",
        )
        same_key = store_cached_response(
            conn,
            provider="deepseek",
            model="deepseek-v4-flash",
            prompt_version="x-stage1-v1",
            task="x_stage1",
            input_payload={"batch": [1]},
            request_payload={"messages": ["old"]},
            response_payload={"error": "bad json"},
            status="error",
            error="bad json",
        )

        self.assertEqual(key, same_key)
        cached = get_cached_response(conn, key)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["status"], "error")
        self.assertEqual(cached["error"], "bad json")
        self.assertEqual(cached["response_json"]["error"], "bad json")


if __name__ == "__main__":
    unittest.main()
