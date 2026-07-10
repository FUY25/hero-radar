from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch


class PipelineSourcesTest(unittest.TestCase):
    def test_pipeline_sources_do_not_include_ossinsight(self) -> None:
        import pipeline.run_pipeline as run_pipeline

        adapter_names = [name for name, _collector in run_pipeline.pipeline_adapters()]

        self.assertNotIn("ossinsight_trending_optional", adapter_names)
        self.assertFalse(any("ossinsight" in name.lower() for name in adapter_names))

    def test_default_config_does_not_include_ossinsight(self) -> None:
        config = json.loads(Path("pipeline/config.json").read_text())

        self.assertNotIn("ossinsight", config)

    def test_default_config_keeps_apify_gated_and_daily_layer2_enabled(self) -> None:
        config = json.loads(Path("pipeline/config.json").read_text())

        self.assertFalse(config["apify"]["enabled"])
        self.assertTrue(config["layer2"]["enabled"])
        self.assertEqual(config["layer2"]["max_scored_candidates"], 0)

    def test_run_pipeline_writes_per_source_structured_log(self) -> None:
        import pipeline.run_pipeline as run_pipeline

        def collect_ok(config, fetched_at):
            return [object(), object()], None

        def collect_error(config, fetched_at):
            raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "sources.jsonl"
            db_path = Path(tmpdir) / "hero.sqlite"
            with (
                patch.object(run_pipeline, "DB_PATH", db_path),
                patch.object(run_pipeline, "load_dotenv"),
                patch.object(run_pipeline, "ensure_dirs"),
                patch.object(run_pipeline, "read_config", return_value={}),
                patch.object(
                    run_pipeline,
                    "pipeline_adapters",
                    return_value=[("ok_source", collect_ok), ("error_source", collect_error)],
                ),
                patch.object(run_pipeline, "init_db"),
                patch.object(run_pipeline, "insert_source_items", side_effect=[[1, 2], []]),
                patch.object(run_pipeline, "rank_score", return_value=[]),
                patch.object(run_pipeline, "export_latest"),
            ):
                result = run_pipeline.run_pipeline(log_path=log_path)

            events = [json.loads(line) for line in log_path.read_text().splitlines()]

        self.assertEqual(result, 0)
        self.assertEqual([event["event"] for event in events], [
            "sources_run_started",
            "source_started",
            "source_started",
            "source_completed",
            "source_completed",
            "sources_run_completed",
        ])
        self.assertEqual(events[1]["source"], "ok_source")
        self.assertEqual(events[3]["items"], 2)
        self.assertEqual(events[4]["error"], "RuntimeError: boom")

    def test_parallel_collection_persists_on_main_thread_in_adapter_order(self) -> None:
        import pipeline.run_pipeline as run_pipeline

        release_first = threading.Event()
        second_started = threading.Event()
        main_thread = threading.get_ident()
        persist_calls: list[tuple[str, int]] = []

        def collect_first(config, fetched_at):
            self.assertTrue(second_started.wait(timeout=1))
            release_first.wait(timeout=1)
            return ["first-item"], None

        def collect_second(config, fetched_at):
            second_started.set()
            release_first.set()
            return ["second-item"], None

        def persist(conn, *, source, **kwargs):
            persist_calls.append((source, threading.get_ident()))
            return [len(persist_calls)]

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            with (
                patch.object(run_pipeline, "DB_PATH", db_path),
                patch.object(run_pipeline, "load_dotenv"),
                patch.object(run_pipeline, "ensure_dirs"),
                patch.object(
                    run_pipeline,
                    "read_config",
                    return_value={"source_collection": {"max_workers": 2}},
                ),
                patch.object(
                    run_pipeline,
                    "pipeline_adapters",
                    return_value=[
                        ("first", collect_first),
                        ("second", collect_second),
                    ],
                ),
                patch.object(run_pipeline, "init_db"),
                patch.object(run_pipeline, "insert_source_items", side_effect=persist),
                patch.object(run_pipeline, "rank_score", return_value=[]),
                patch.object(run_pipeline, "export_latest"),
            ):
                result = run_pipeline.run_pipeline()

        self.assertEqual(result, 0)
        self.assertEqual(persist_calls, [("first", main_thread), ("second", main_thread)])

    def test_x_tweet_collection_finishes_before_snapshot_writer_starts(self) -> None:
        import pipeline.run_pipeline as run_pipeline

        x_collecting = threading.Event()
        release_x = threading.Event()
        overlap_detected = False

        def collect_network(config, fetched_at):
            self.assertTrue(x_collecting.wait(timeout=1))
            release_x.set()
            return ["network-item"], None

        def collect_x(config, fetched_at):
            x_collecting.set()
            release_x.wait(timeout=1)
            x_collecting.clear()
            return ["x-item"], None

        def persist(conn, **kwargs):
            nonlocal overlap_detected
            overlap_detected = overlap_detected or x_collecting.is_set()
            return [1]

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            with (
                patch.object(run_pipeline, "DB_PATH", db_path),
                patch.object(run_pipeline, "load_dotenv"),
                patch.object(run_pipeline, "ensure_dirs"),
                patch.object(
                    run_pipeline,
                    "read_config",
                    return_value={"source_collection": {"max_workers": 2}},
                ),
                patch.object(
                    run_pipeline,
                    "pipeline_adapters",
                    return_value=[
                        ("network", collect_network),
                        ("x_tweets", collect_x),
                    ],
                ),
                patch.object(run_pipeline, "init_db"),
                patch.object(run_pipeline, "insert_source_items", side_effect=persist),
                patch.object(run_pipeline, "rank_score", return_value=[]),
                patch.object(run_pipeline, "export_latest"),
            ):
                result = run_pipeline.run_pipeline()

        self.assertEqual(result, 0)
        self.assertFalse(overlap_detected)

    def test_hn_firebase_keeps_configured_list_and_rank_order(self) -> None:
        import pipeline.run_pipeline as run_pipeline

        release_first = threading.Event()
        second_started = threading.Event()

        def request_json(url, **kwargs):
            if url.endswith("/topstories.json"):
                return [1, 2]
            if url.endswith("/newstories.json"):
                return [3]
            if url.endswith("/item/1.json"):
                self.assertTrue(second_started.wait(timeout=1))
                release_first.wait(timeout=1)
                return {"id": 1, "title": "one"}
            if url.endswith("/item/2.json"):
                second_started.set()
                release_first.set()
                return {"id": 2, "title": "two"}
            if url.endswith("/item/3.json"):
                return {"id": 3, "title": "three"}
            self.fail(f"unexpected URL: {url}")

        config = {
            "hn": {
                "firebase_lists": ["topstories", "newstories"],
                "firebase_limit": 2,
                "firebase_workers": 3,
            }
        }
        with patch.object(run_pipeline, "request_json", side_effect=request_json):
            items, error = run_pipeline.collect_hn_firebase(config, "2026-07-10T00:00:00Z")

        self.assertIsNone(error)
        self.assertEqual(
            [item.external_id for item in items],
            ["topstories:1", "topstories:2", "newstories:3"],
        )

    def test_hn_algolia_queries_merge_stably_and_isolate_errors(self) -> None:
        import pipeline.run_pipeline as run_pipeline

        first_started = threading.Event()
        second_started = threading.Event()
        release_first = threading.Event()

        def request_json(url, **kwargs):
            if "query=first" in url:
                first_started.set()
                self.assertTrue(second_started.wait(timeout=1))
                release_first.wait(timeout=1)
                return {"hits": [{"objectID": "1", "title": "one"}]}
            if "query=second" in url:
                second_started.set()
                release_first.set()
                return {"hits": [{"objectID": "2", "title": "two"}]}
            if "query=broken" in url:
                raise RuntimeError("unavailable")
            self.fail(f"unexpected URL: {url}")

        config = {
            "hn": {
                "algolia_queries": ["first", "second", "broken"],
                "algolia_windows": {"24h": 1},
                "algolia_hits_per_page": 20,
                "algolia_workers": 3,
                "algolia_request_interval_seconds": 0,
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(run_pipeline, "ROOT", Path(tmpdir)),
                patch.object(run_pipeline, "RAW_DIR", Path(tmpdir)),
                patch.object(run_pipeline, "request_json", side_effect=request_json),
            ):
                items, error = run_pipeline.collect_hn_algolia(
                    config,
                    "2026-07-10T00:00:00Z",
                )

        self.assertTrue(first_started.is_set())
        self.assertEqual([item.external_id for item in items], ["24h:first:1", "24h:second:2"])
        self.assertEqual(error, "24h/broken: RuntimeError: unavailable")

    def test_npm_queries_merge_stably_and_isolate_errors(self) -> None:
        import pipeline.run_pipeline as run_pipeline

        second_started = threading.Event()
        release_first = threading.Event()

        def npm_payload(name: str) -> dict:
            return {
                "objects": [
                    {
                        "package": {"name": name, "links": {}},
                        "score": {"final": 0.5, "detail": {}},
                    }
                ]
            }

        def request_json(url, **kwargs):
            if "text=first" in url:
                self.assertTrue(second_started.wait(timeout=1))
                release_first.wait(timeout=1)
                return npm_payload("first-package")
            if "text=second" in url:
                second_started.set()
                release_first.set()
                return npm_payload("second-package")
            if "text=broken" in url:
                raise RuntimeError("registry down")
            self.fail(f"unexpected URL: {url}")

        config = {
            "npm": {
                "enabled": True,
                "queries": ["first", "second", "broken"],
                "size": 10,
                "query_workers": 3,
                "sleep_seconds": 0,
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(run_pipeline, "ROOT", Path(tmpdir)),
                patch.object(run_pipeline, "RAW_DIR", Path(tmpdir)),
                patch.object(run_pipeline, "request_json", side_effect=request_json),
            ):
                items, error = run_pipeline.collect_npm_search(config, "2026-07-10T00:00:00Z")

        self.assertEqual(
            [item.external_id for item in items],
            ["first:first-package", "second:second-package"],
        )
        self.assertEqual(error, "broken: RuntimeError: registry down")

    def test_huggingface_resources_collect_concurrently_and_merge_stably(self) -> None:
        import pipeline.run_pipeline as run_pipeline

        spaces_started = threading.Event()
        release_models = threading.Event()

        def request_json(url, **kwargs):
            if "/api/models?" in url:
                self.assertTrue(spaces_started.wait(timeout=1))
                release_models.wait(timeout=1)
                return [{"id": "owner/model"}]
            if "/api/spaces?" in url:
                spaces_started.set()
                release_models.set()
                return [{"id": "owner/space"}]
            self.fail(f"unexpected URL: {url}")

        config = {
            "huggingface": {
                "resources": ["models", "spaces"],
                "limit": 10,
                "card_enrich_limit": 0,
                "resource_workers": 2,
                "request_interval_seconds": 0,
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(run_pipeline, "RAW_DIR", Path(tmpdir)),
                patch.object(run_pipeline, "request_json", side_effect=request_json),
            ):
                items, error = run_pipeline.collect_huggingface(
                    config,
                    "2026-07-10T00:00:00Z",
                )

        self.assertIsNone(error)
        self.assertEqual(
            [(item.source, item.external_id) for item in items],
            [
                ("huggingface_models", "owner/model"),
                ("huggingface_spaces", "owner/space"),
            ],
        )

    def test_huggingface_card_enrichment_is_bounded_stable_and_fault_isolated(self) -> None:
        import pipeline.run_pipeline as run_pipeline

        lock = threading.Lock()
        active = 0
        peak = 0
        second_started = threading.Event()
        release_first = threading.Event()

        items = [
            run_pipeline.SourceItem(
                source="huggingface_models",
                external_id=f"owner/{name}",
                name=name,
                url=f"https://huggingface.co/owner/{name}",
                raw={"trendingScore": score},
            )
            for name, score in [("first", 3), ("second", 2), ("broken", 1)]
        ]

        def request_text(url, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                if url.endswith("owner/first/raw/main/README.md"):
                    self.assertTrue(second_started.wait(timeout=1))
                    release_first.wait(timeout=1)
                    return "https://github.com/demo/first"
                if url.endswith("owner/second/raw/main/README.md"):
                    second_started.set()
                    release_first.set()
                    return "https://github.com/demo/second"
                if url.endswith("owner/broken/raw/main/README.md"):
                    raise RuntimeError("missing card")
                self.fail(f"unexpected URL: {url}")
            finally:
                with lock:
                    active -= 1

        gate = run_pipeline.RateGate(max_in_flight=2)
        with patch.object(run_pipeline, "request_text", side_effect=request_text):
            run_pipeline.enrich_huggingface_card_links(
                "models",
                items,
                3,
                request_gate=gate,
                card_workers=2,
            )

        self.assertEqual([item.name for item in items], ["first", "second", "broken"])
        self.assertEqual(items[0].metadata["repository"], "https://github.com/demo/first")
        self.assertEqual(items[1].metadata["repository"], "https://github.com/demo/second")
        self.assertNotIn("repository", items[2].metadata)
        self.assertEqual(peak, 2)

    def test_pypi_feeds_collect_concurrently_and_merge_stably(self) -> None:
        import pipeline.run_pipeline as run_pipeline

        updates_started = threading.Event()
        release_newest = threading.Event()

        def rss(title: str, link: str) -> str:
            return (
                "<rss><channel><item>"
                f"<title>{title}</title><link>{link}</link>"
                "<description>demo</description><pubDate>Thu, 10 Jul 2026 00:00:00 GMT</pubDate>"
                "</item></channel></rss>"
            )

        def request_text(url, **kwargs):
            if url.endswith("packages.xml"):
                self.assertTrue(updates_started.wait(timeout=1))
                release_newest.wait(timeout=1)
                return rss("first added to PyPI", "https://pypi.org/project/first/")
            if url.endswith("updates.xml"):
                updates_started.set()
                release_newest.set()
                return rss("second 1.0", "https://pypi.org/project/second/1.0/")
            self.fail(f"unexpected URL: {url}")

        config = {
            "pypi": {
                "enabled": True,
                "feeds": ["newest", "updates"],
                "limit_per_feed": 10,
                "json_enrich_limit_per_feed": 0,
                "feed_workers": 2,
                "json_sleep_seconds": 0,
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(run_pipeline, "ROOT", Path(tmpdir)),
                patch.object(run_pipeline, "RAW_DIR", Path(tmpdir)),
                patch.object(run_pipeline, "request_text", side_effect=request_text),
            ):
                items, error = run_pipeline.collect_pypi_feeds(
                    config,
                    "2026-07-10T00:00:00Z",
                )

        self.assertIsNone(error)
        self.assertEqual(
            [item.external_id for item in items],
            ["newest:first:", "updates:second:1.0"],
        )

    def test_pypi_json_enrichment_is_bounded_stable_and_fault_isolated(self) -> None:
        import pipeline.run_pipeline as run_pipeline

        lock = threading.Lock()
        active = 0
        peak = 0
        second_started = threading.Event()
        release_first = threading.Event()

        xml = (
            "<rss><channel>"
            "<item><title>first added to PyPI</title><link>https://pypi.org/project/first/</link>"
            "<description>rss first</description></item>"
            "<item><title>second added to PyPI</title><link>https://pypi.org/project/second/</link>"
            "<description>rss second</description></item>"
            "<item><title>broken added to PyPI</title><link>https://pypi.org/project/broken/</link>"
            "<description>rss broken</description></item>"
            "</channel></rss>"
        )

        def request_json(url, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                if "/first/json" in url:
                    self.assertTrue(second_started.wait(timeout=1))
                    release_first.wait(timeout=1)
                    return {"info": {"summary": "json first"}}
                if "/second/json" in url:
                    second_started.set()
                    release_first.set()
                    return {"info": {"summary": "json second"}}
                if "/broken/json" in url:
                    raise RuntimeError("missing json")
                self.fail(f"unexpected URL: {url}")
            finally:
                with lock:
                    active -= 1

        config = {
            "pypi": {
                "enabled": True,
                "feeds": ["newest"],
                "limit_per_feed": 3,
                "json_enrich_limit_per_feed": 3,
                "feed_workers": 1,
                "json_enrich_workers": 2,
                "json_sleep_seconds": 0,
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(run_pipeline, "ROOT", Path(tmpdir)),
                patch.object(run_pipeline, "RAW_DIR", Path(tmpdir)),
                patch.object(run_pipeline, "request_text", return_value=xml),
                patch.object(run_pipeline, "request_json", side_effect=request_json),
            ):
                items, error = run_pipeline.collect_pypi_feeds(
                    config,
                    "2026-07-10T00:00:00Z",
                )

        self.assertIsNone(error)
        self.assertEqual([item.name for item in items], ["first", "second", "broken"])
        self.assertEqual([item.source_rank for item in items], [1, 2, 3])
        self.assertEqual(items[0].description, "rss first")
        self.assertEqual(items[0].metadata["summary"], "json first")
        self.assertEqual(items[1].metadata["summary"], "json second")
        self.assertIsNone(items[2].metadata["summary"])
        self.assertEqual(items[2].description, "rss broken")
        self.assertEqual(peak, 2)

    def test_github_trending_scopes_collect_concurrently_in_config_order(self) -> None:
        import pipeline.run_pipeline as run_pipeline

        python_started = threading.Event()
        release_all = threading.Event()

        def html_for(repo: str) -> str:
            return (
                '<article class="Box-row"><h2 class="h3 lh-condensed">'
                f'<a href="/owner/{repo}">owner/{repo}</a></h2></article>'
            )

        def request_text(url, **kwargs):
            if url.endswith("/trending?since=daily"):
                self.assertTrue(python_started.wait(timeout=1))
                release_all.wait(timeout=1)
                return html_for("all-repo")
            if "/trending/python?" in url:
                python_started.set()
                release_all.set()
                return html_for("python-repo")
            if "/trending/rust?" in url:
                raise RuntimeError("blocked")
            self.fail(f"unexpected URL: {url}")

        config = {
            "github_trending": {
                "periods": ["daily"],
                "languages": ["", "python", "rust"],
                "scope_workers": 3,
                "request_interval_seconds": 0,
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(run_pipeline, "ROOT", Path(tmpdir)),
                patch.object(run_pipeline, "RAW_DIR", Path(tmpdir)),
                patch.object(run_pipeline, "request_text", side_effect=request_text),
            ):
                items, error = run_pipeline.collect_github_trending(
                    config,
                    "2026-07-10T00:00:00Z",
                )

        self.assertEqual(
            [item.external_id for item in items],
            ["daily:owner/all-repo", "daily:owner/python-repo"],
        )
        self.assertEqual(error, "daily/rust: RuntimeError: blocked")

    def test_github_mover_providers_collect_concurrently_in_stable_order(self) -> None:
        import pipeline.run_pipeline as run_pipeline

        repofomo_started = threading.Event()
        release_trending = threading.Event()

        def collect_trending(settings, fetched_at):
            self.assertTrue(repofomo_started.wait(timeout=1))
            release_trending.wait(timeout=1)
            return ["trending"]

        def collect_repofomo(settings, fetched_at):
            repofomo_started.set()
            release_trending.set()
            return ["repofomo"]

        config = {
            "github_movers": {
                "enabled": True,
                "provider_workers": 2,
                "trending_repos": {"enabled": True},
                "repofomo": {"enabled": True},
            }
        }
        with (
            patch.object(run_pipeline, "collect_trending_repos_movers", side_effect=collect_trending),
            patch.object(run_pipeline, "collect_repofomo_movers", side_effect=collect_repofomo),
        ):
            items, error = run_pipeline.collect_github_movers(config, "2026-07-10T00:00:00Z")

        self.assertEqual(items, ["trending", "repofomo"])
        self.assertIsNone(error)

    def test_github_search_queries_run_concurrently_but_merge_stably(self) -> None:
        import pipeline.run_pipeline as run_pipeline

        second_started = threading.Event()
        release_first = threading.Event()

        def repo(name: str) -> dict:
            return {
                "full_name": f"owner/{name}",
                "html_url": f"https://github.com/owner/{name}",
            }

        def request_json(url, **kwargs):
            if "q=first" in url:
                self.assertTrue(second_started.wait(timeout=1))
                release_first.wait(timeout=1)
                return {"items": [repo("first-repo")]}
            if "q=second" in url:
                second_started.set()
                release_first.set()
                return {"items": [repo("second-repo")]}
            if "q=broken" in url:
                raise RuntimeError("rate limited")
            self.fail(f"unexpected URL: {url}")

        config = {
            "github_search": {
                "queries": ["first", "second", "broken"],
                "per_page": 1,
                "max_results_per_query": 1,
                "query_workers": 3,
                "request_interval_seconds": 0,
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(run_pipeline, "ROOT", Path(tmpdir)),
                patch.object(run_pipeline, "RAW_DIR", Path(tmpdir)),
                patch.object(run_pipeline, "request_json", side_effect=request_json),
            ):
                items, error = run_pipeline.collect_github_search(
                    config,
                    "2026-07-10T00:00:00Z",
                )

        self.assertEqual(
            [item.external_id for item in items],
            ["first:owner/first-repo", "second:owner/second-repo"],
        )
        self.assertEqual(error, "broken: RuntimeError: rate limited")

    def test_github_search_keeps_pages_serial_within_each_query(self) -> None:
        import pipeline.run_pipeline as run_pipeline

        requested_pages: list[str] = []

        def request_json(url, **kwargs):
            page = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["page"][0]
            requested_pages.append(page)
            return {
                "items": [
                    {
                        "full_name": f"owner/repo-{page}",
                        "html_url": f"https://github.com/owner/repo-{page}",
                    }
                ]
            }

        config = {
            "github_search": {
                "queries": ["agent"],
                "per_page": 1,
                "max_results_per_query": 2,
                "query_workers": 3,
                "request_interval_seconds": 0,
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(run_pipeline, "ROOT", Path(tmpdir)),
                patch.object(run_pipeline, "RAW_DIR", Path(tmpdir)),
                patch.object(run_pipeline, "request_json", side_effect=request_json),
            ):
                items, error = run_pipeline.collect_github_search(
                    config,
                    "2026-07-10T00:00:00Z",
                )

        self.assertIsNone(error)
        self.assertEqual(requested_pages, ["1", "2"])
        self.assertEqual([item.source_rank for item in items], [1, 2])


if __name__ == "__main__":
    unittest.main()
