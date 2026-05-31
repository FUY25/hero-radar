from __future__ import annotations

import json
import unittest
from pathlib import Path


class PipelineSourcesTest(unittest.TestCase):
    def test_pipeline_sources_do_not_include_ossinsight(self) -> None:
        import pipeline.run_pipeline as run_pipeline

        adapter_names = [name for name, _collector in run_pipeline.pipeline_adapters()]

        self.assertNotIn("ossinsight_trending_optional", adapter_names)
        self.assertFalse(any("ossinsight" in name.lower() for name in adapter_names))

    def test_default_config_does_not_include_ossinsight(self) -> None:
        config = json.loads(Path("pipeline/config.json").read_text())

        self.assertNotIn("ossinsight", config)


if __name__ == "__main__":
    unittest.main()
