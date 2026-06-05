from __future__ import annotations

import json
import tempfile
import unittest
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
            "source_completed",
            "source_started",
            "source_completed",
            "sources_run_completed",
        ])
        self.assertEqual(events[1]["source"], "ok_source")
        self.assertEqual(events[2]["items"], 2)
        self.assertEqual(events[4]["error"], "RuntimeError: boom")


if __name__ == "__main__":
    unittest.main()
