from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.decision.backfill import run_backfill_jobs
from pipeline.decision.npm_backfill import run_npm_backfill
from pipeline.decision.readme_enrichment import enrich_candidate_readmes
from pipeline.decision.rate_limit import StartRateLimiter
from pipeline.decision.resolver import enrich_classifier_candidates
from pipeline.decision.run_decision import run_decision
from pipeline.decision.schema import init_decision_db
from pipeline.decision.x_classifier import run_x_stage1


NOW = "2026-05-31T00:00:00Z"


class _OverlapGate:
    def __init__(self, parties: int = 2) -> None:
        self.barrier = threading.Barrier(parties, timeout=2)
        self.max_active = 0
        self.active = 0
        self.lock = threading.Lock()

    def enter(self) -> None:
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.barrier.wait()
        finally:
            with self.lock:
                self.active -= 1


def _insert_entity(conn: sqlite3.Connection, entity_id: str, key: str) -> None:
    conn.execute(
        """
        insert into entities(entity_id, canonical_entity, canonical_key, key_type,
                             first_seen, aliases_json, source_item_ids_json)
        values (?, ?, ?, ?, ?, '[]', '[]')
        """,
        (entity_id, key.split(":", 1)[-1], key, key.split(":", 1)[0], NOW),
    )


class DecisionParallelismTest(unittest.TestCase):
    def test_rate_limiter_spaces_concurrent_pool_starts(self) -> None:
        limiter = StartRateLimiter(2)
        with (
            patch("pipeline.decision.rate_limit.time.monotonic", side_effect=[0.0, 0.0]),
            patch("pipeline.decision.rate_limit.time.sleep") as sleep,
        ):
            limiter.wait()
            limiter.wait()

        sleep.assert_called_once_with(0.5)

    def test_github_backfill_jobs_overlap_but_persist_all_results(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)
        for index in range(2):
            entity_id = f"entity:{index}"
            repo = f"owner/repo-{index}"
            _insert_entity(conn, entity_id, f"github:{repo}")
            conn.execute(
                """
                insert into backfill_jobs(entity_id, run_id, source, reason, status, requested_at)
                values (?, 'run', 'github_stargazers', 'potential_candidate', 'pending', ?)
                """,
                (entity_id, NOW),
            )
        conn.commit()
        gate = _OverlapGate()

        class Client:
            def repo_metadata(self, full_name):
                gate.enter()
                return {"stargazers_count": 100, "forks_count": 2}

            def stargazers_since(self, full_name, since_iso):
                return []

        summary = run_backfill_jobs(
            conn,
            run_id="run",
            github_client=Client(),
            now=NOW,
            concurrency=2,
        )

        self.assertEqual(summary["completed"], 2)
        self.assertEqual(gate.max_active, 2)
        self.assertEqual(
            conn.execute("select count(*) from evidence_rows").fetchone()[0],
            6,
        )

    def test_npm_backfill_jobs_overlap_and_isolate_failures(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)
        for index, package in enumerate(("good-one", "good-two")):
            entity_id = f"entity:{index}"
            _insert_entity(conn, entity_id, f"npm:{package}")
            conn.execute(
                """
                insert into backfill_jobs(entity_id, run_id, source, reason, status, requested_at)
                values (?, 'run', 'npm_registry', ?, 'pending', ?)
                """,
                (entity_id, f"package_downloads:{package}", NOW),
            )
        conn.commit()
        gate = _OverlapGate()

        class Client:
            def package_metadata(self, package):
                gate.enter()
                return {"name": package}

            def downloads(self, package, period):
                return {"downloads": 10}

        summary = run_npm_backfill(
            conn,
            run_id="run",
            client=Client(),
            now=NOW,
            limit=2,
            concurrency=2,
        )

        self.assertEqual(summary["completed"], 2)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(gate.max_active, 2)

    def test_readme_fetches_overlap_and_are_cached_by_the_writer(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)
        for index in range(2):
            entity_id = f"entity:{index}"
            _insert_entity(conn, entity_id, f"github:owner/repo-{index}")
            conn.execute(
                """
                insert into potential_candidates(entity_id, run_id, level,
                                                 fired_families_json, first_trigger_at)
                values (?, 'run', 'potential', '[]', ?)
                """,
                (entity_id, NOW),
            )
        conn.commit()
        gate = _OverlapGate()

        class Client:
            def get_readme_text(self, repo_key):
                gate.enter()
                return f"# {repo_key}"

        summary = enrich_candidate_readmes(
            conn,
            run_id="run",
            client=Client(),
            limit=2,
            concurrency=2,
        )

        self.assertEqual(summary, {"fetched": 2, "cached": 0, "skipped": 0})
        self.assertEqual(gate.max_active, 2)
        self.assertEqual(conn.execute("select count(*) from api_cache").fetchone()[0], 2)

    def test_resolver_failure_is_scoped_to_one_candidate(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)
        for index, name in enumerate(("broken", "working")):
            conn.execute(
                """
                insert into evidence_rows(
                    entity_id, canonical_entity, alias, source, event_at,
                    relative_to_reference, metric_name, metric_value, family, rule_id,
                    rule_version, signal_label, historical_safety, note, raw_url_or_ref,
                    run_id
                ) values (?, ?, ?, 'x_tweets', ?, null, 'x_tier', 'watch',
                          'x_social', 'x_social_x_tier', 'x-stage2-v2', 'watch',
                          'llm_source_classifier', 'accepted', ?, 'run')
                """,
                (f"entity:{index}", f"name:{name}", f"name:{name}", NOW, f"tweet:{index}"),
            )
        conn.commit()

        class Search:
            def search(self, query, *, limit):
                if query == "broken":
                    raise RuntimeError("blocked")
                return [
                    {
                        "type": "github",
                        "key": "github:owner/working",
                        "url": "https://github.com/owner/working",
                        "confidence": 0.9,
                    }
                ]

        summary = enrich_classifier_candidates(
            conn,
            run_id="run",
            search_client=Search(),
            max_searches_per_candidate=1,
            now=NOW,
        )

        self.assertEqual(summary["enriched"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(len(summary["errors"]), 1)
        self.assertEqual(summary["errors"][0]["entity_key"], "name:broken")
        self.assertEqual(
            conn.execute("select alias from alias_links where origin='resolver'").fetchall(),
            [("github:owner/working",)],
        )

    def test_resolver_candidates_overlap_and_persist_in_candidate_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "resolver.sqlite"
            conn = sqlite3.connect(db_path)
            self.addCleanup(conn.close)
            init_decision_db(conn)
            for index, name in enumerate(("first", "second")):
                conn.execute(
                    """
                    insert into evidence_rows(
                        entity_id, canonical_entity, alias, source, event_at,
                        relative_to_reference, metric_name, metric_value, family, rule_id,
                        rule_version, signal_label, historical_safety, note, raw_url_or_ref,
                        run_id
                    ) values (?, ?, ?, 'x_tweets', ?, null, 'x_tier', 'watch',
                              'x_social', 'x_social_x_tier', 'x-stage2-v2', 'watch',
                              'llm_source_classifier', 'accepted', ?, 'run')
                    """,
                    (f"entity:{index}", f"name:{name}", f"name:{name}", NOW, f"tweet:{index}"),
                )
            conn.commit()
            gate = _OverlapGate()

            class Search:
                def search(self, query, *, limit):
                    gate.enter()
                    return [
                        {
                            "type": "github",
                            "key": f"github:owner/{query}",
                            "url": f"https://github.com/owner/{query}",
                            "confidence": 0.9,
                        }
                    ]

            summary = enrich_classifier_candidates(
                conn,
                run_id="run",
                search_client=Search(),
                max_searches_per_candidate=1,
                now=NOW,
                concurrency=2,
            )

            self.assertEqual(summary["enriched"], 2)
            self.assertEqual(gate.max_active, 2)
            self.assertEqual(
                conn.execute(
                    "select external_id, alias from alias_links where origin='resolver' order by id"
                ).fetchall(),
                [
                    ("name:first", "github:owner/first"),
                    ("name:second", "github:owner/second"),
                ],
            )

    def test_x_stage1_batches_share_the_bounded_parallel_budget(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)
        conn.execute(
            """
            create table x_tweets_store (
                tweet_id text primary key, author_username text not null,
                text text not null, url text, created_at text not null,
                imported_at text not null, raw_json text not null
            )
            """
        )
        for index in range(2):
            conn.execute(
                "insert into x_tweets_store values (?, ?, ?, ?, ?, ?, '{}')",
                (
                    f"t{index}",
                    f"author{index}",
                    f"Try project-{index}",
                    f"https://x.com/a/status/t{index}",
                    "2026-05-31T01:00:00Z",
                    "2026-05-31T02:00:00Z",
                ),
            )
        conn.commit()
        gate = _OverlapGate()

        class Provider:
            provider_name = "fake"
            model = "fake"

            def complete_json(self, **kwargs):
                tweet = kwargs["input_payload"]["tweets"][0]
                gate.enter()
                return {
                    "triage": [
                        {
                            "tweet_id": tweet["tweet_id"],
                            "about_concrete_project": False,
                            "closer_look": False,
                            "product_names": [],
                            "product_links": [],
                            "project_refs": [],
                            "expression_strength": "neutral",
                            "evidence_quote": "",
                            "reason": "No concrete project.",
                        }
                    ]
                }

        summary = run_x_stage1(
            conn,
            run_id="run",
            provider=Provider(),
            credible_handles=set(),
            now="2026-05-31T04:00:00Z",
            limit=2,
            batch_size=1,
            llm_concurrency=2,
        )

        self.assertEqual(summary["triaged"], 2)
        self.assertEqual(gate.max_active, 2)

    def test_post_pass1_github_and_hn_branches_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            export_path = Path(tmpdir) / "candidates.json"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                create table snapshots (
                    id integer primary key autoincrement, run_id text, source text,
                    fetched_at text, status text, item_count integer, error text
                );
                create table items (
                    id integer primary key autoincrement, run_id text, snapshot_id integer,
                    source text, external_id text, name text, url text, fetched_at text,
                    heat real, velocity real, acceleration real, source_rank integer,
                    description text, metadata_json text, raw_json text
                );
                """
            )
            conn.execute(
                "insert into snapshots(run_id,source,fetched_at,status,item_count) values ('s','github_trending',?,'ok',1)",
                (NOW,),
            )
            snapshot_id = conn.execute("select id from snapshots").fetchone()[0]
            conn.execute(
                """
                insert into items(run_id,snapshot_id,source,external_id,name,url,fetched_at,
                                  metadata_json,raw_json)
                values ('s',?,'github_trending','owner/repo','owner/repo',
                        'https://github.com/owner/repo',?,?,'{}')
                """,
                (
                    snapshot_id,
                    NOW,
                    json.dumps({"period": "daily", "window": "24h", "period_stars": 1200}),
                ),
            )
            init_decision_db(conn)
            conn.commit()
            conn.close()
            gate = _OverlapGate()

            def github_branch(*args, **kwargs):
                gate.enter()
                return {"signals": {}, "completed": 0, "failed": 0}

            def hn_branch(*args, **kwargs):
                gate.enter()
                return {"classified": 0}

            with (
                patch("pipeline.decision.backfill.run_backfill_jobs", side_effect=github_branch),
                patch("pipeline.decision.hn_classifier.run_hn_classifier", side_effect=hn_branch),
            ):
                run_decision(
                    db_path=db_path,
                    run_id="run",
                    export_json_path=export_path,
                    now=NOW,
                    github_client=object(),
                    hn_llm_provider=object(),
                    hn_classifier_limit=1,
                )

            self.assertEqual(gate.max_active, 2)

    def test_resolver_partial_failure_marks_decision_ok_with_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            export_path = Path(tmpdir) / "candidates.json"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                create table snapshots (
                    id integer primary key autoincrement, run_id text, source text,
                    fetched_at text, status text, item_count integer, error text
                );
                create table items (
                    id integer primary key autoincrement, run_id text, snapshot_id integer,
                    source text, external_id text, name text, url text, fetched_at text,
                    heat real, velocity real, acceleration real, source_rank integer,
                    description text, metadata_json text, raw_json text
                );
                """
            )
            init_decision_db(conn)
            conn.close()

            with (
                patch(
                    "pipeline.decision.hn_classifier.run_hn_classifier",
                    return_value={"classified": 1},
                ),
                patch(
                    "pipeline.decision.resolver.enrich_classifier_candidates",
                    return_value={
                        "enriched": 0,
                        "aliases": 0,
                        "proposals": 0,
                        "researched": 0,
                        "failed": 1,
                        "errors": [
                            {
                                "entity_id": "entity:broken",
                                "entity_key": "name:broken",
                                "error_type": "RuntimeError",
                                "error": "blocked",
                            }
                        ],
                    },
                ),
            ):
                summary = run_decision(
                    db_path=db_path,
                    run_id="run",
                    export_json_path=export_path,
                    now=NOW,
                    hn_llm_provider=object(),
                    hn_classifier_limit=1,
                )

            conn = sqlite3.connect(db_path)
            self.addCleanup(conn.close)
            status, note = conn.execute(
                "select status, note from decision_runs where run_id='run'"
            ).fetchone()
            self.assertEqual(summary["resolver_failed"], 1)
            self.assertEqual(status, "ok_with_errors")
            self.assertEqual(json.loads(note)["resolver_failed"], 1)


if __name__ == "__main__":
    unittest.main()
