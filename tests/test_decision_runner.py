import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from pipeline.decision.llm_provider import FakeLLMProvider
from pipeline.decision.run_decision import run_decision
from pipeline.decision.schema import init_decision_db


class FakeGitHubClient:
    def repo_metadata(self, full_name):
        return {"full_name": full_name, "stargazers_count": 1500, "forks_count": 120}

    def stargazers_since(self, full_name, since_iso):
        return [
            {"user": "a", "starred_at": "2026-05-30T12:00:00Z"},
            {"user": "b", "starred_at": "2026-05-30T13:00:00Z"},
        ]


class FakeGitHubReadmeClient:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def get_readme_text(self, repo_key):
        self.calls.append(repo_key)
        return self.text


class FakeNpmClient:
    def package_metadata(self, package):
        return {"name": package, "repository": {"url": "git+https://github.com/owner/repo.git"}}

    def downloads(self, package, period):
        downloads_by_period = {"last-day": 12000, "last-week": 70000}
        return {"downloads": downloads_by_period[period], "package": package}


class FakeSearchClient:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, query, *, limit):
        self.calls.append({"query": query, "limit": limit})
        return self.results[:limit]


def seed_source_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table snapshots (
            id integer primary key autoincrement,
            run_id text not null,
            source text not null,
            fetched_at text not null,
            status text not null,
            item_count integer not null,
            error text
        );
        create table items (
            id integer primary key autoincrement,
            run_id text not null,
            snapshot_id integer not null,
            source text not null,
            external_id text not null,
            name text not null,
            url text,
            fetched_at text not null,
            heat real,
            velocity real,
            acceleration real,
            source_rank integer,
            description text,
            metadata_json text not null,
            raw_json text not null
        );
        """
    )
    conn.execute(
        "insert into snapshots(run_id, source, fetched_at, status, item_count, error) values (?, ?, ?, ?, ?, ?)",
        ("source-run-1", "github_trending", "2026-05-31T00:00:00Z", "ok", 1, None),
    )
    snapshot_id = conn.execute("select id from snapshots").fetchone()[0]
    conn.execute(
        """
        insert into items(run_id, snapshot_id, source, external_id, name, url, fetched_at, metadata_json, raw_json)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "source-run-1",
            snapshot_id,
            "github_trending",
            "owner/repo",
            "owner/repo",
            "https://github.com/owner/repo",
            "2026-05-31T00:00:00Z",
            json.dumps(
                {
                    "period": "daily",
                    "window": "24h",
                    "period_stars": 1200,
                    "stars_total": 3000,
                }
            ),
            "{}",
        ),
    )
    conn.commit()


def seed_hn_source_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table snapshots (
            id integer primary key autoincrement,
            run_id text not null,
            source text not null,
            fetched_at text not null,
            status text not null,
            item_count integer not null,
            error text
        );
        create table items (
            id integer primary key autoincrement,
            run_id text not null,
            snapshot_id integer not null,
            source text not null,
            external_id text not null,
            name text not null,
            url text,
            fetched_at text not null,
            heat real,
            velocity real,
            acceleration real,
            source_rank integer,
            description text,
            metadata_json text not null,
            raw_json text not null
        );
        """
    )
    conn.execute(
        "insert into snapshots(run_id, source, fetched_at, status, item_count, error) values (?, ?, ?, ?, ?, ?)",
        ("source-run-hn", "hn_firebase", "2026-05-31T00:00:00Z", "ok", 1, None),
    )
    snapshot_id = conn.execute("select id from snapshots").fetchone()[0]
    conn.execute(
        """
        insert into items(run_id, snapshot_id, source, external_id, name, url, fetched_at, metadata_json, raw_json)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "source-run-hn",
            snapshot_id,
            "hn_firebase",
            "hn-news",
            "AI lab announces policy news",
            "https://github.com/owner/repo",
            "2026-05-31T00:00:00Z",
            json.dumps({"score": 220, "comments": 55, "list": "topstories", "created_at_unix": 1780185600}),
            "{}",
        ),
    )
    conn.commit()


def seed_x_source_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table snapshots (
            id integer primary key autoincrement,
            run_id text not null,
            source text not null,
            fetched_at text not null,
            status text not null,
            item_count integer not null,
            error text
        );
        create table items (
            id integer primary key autoincrement,
            run_id text not null,
            snapshot_id integer not null,
            source text not null,
            external_id text not null,
            name text not null,
            url text,
            fetched_at text not null,
            heat real,
            velocity real,
            acceleration real,
            source_rank integer,
            description text,
            metadata_json text not null,
            raw_json text not null
        );
        create table x_tweets_store (
            tweet_id text primary key,
            author_username text not null,
            text text not null,
            url text,
            created_at text not null,
            imported_at text not null,
            raw_json text not null
        );
        """
    )
    conn.execute(
        "insert into snapshots(run_id, source, fetched_at, status, item_count, error) values (?, ?, ?, ?, ?, ?)",
        ("source-run-x", "x_tweets", "2026-05-31T00:00:00Z", "ok", 1, None),
    )
    snapshot_id = conn.execute("select id from snapshots").fetchone()[0]
    conn.execute(
        """
        insert into items(run_id, snapshot_id, source, external_id, name, url, fetched_at, description, metadata_json, raw_json)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "source-run-x",
            snapshot_id,
            "x_tweets",
            "t1",
            "New repo https://github.com/owner/repo is useful",
            "https://x.com/credible1/status/t1",
            "2026-05-31T01:00:00Z",
            "New repo https://github.com/owner/repo is useful",
            json.dumps({"window": "24h", "author": "credible1", "created_at": "2026-05-31T01:00:00Z"}),
            "{}",
        ),
    )
    conn.executemany(
        """
        insert into x_tweets_store(tweet_id, author_username, text, url, created_at, imported_at, raw_json)
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("t1", "credible1", "New repo https://github.com/owner/repo is useful", "https://x.com/credible1/status/t1", "2026-05-31T01:00:00Z", "2026-05-31T02:00:00Z", "{}"),
            ("t2", "credible2", "Trying owner/repo for agents", "https://x.com/credible2/status/t2", "2026-05-31T03:00:00Z", "2026-05-31T04:00:00Z", "{}"),
        ],
    )
    conn.commit()


def seed_npm_source_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table snapshots (
            id integer primary key autoincrement,
            run_id text not null,
            source text not null,
            fetched_at text not null,
            status text not null,
            item_count integer not null,
            error text
        );
        create table items (
            id integer primary key autoincrement,
            run_id text not null,
            snapshot_id integer not null,
            source text not null,
            external_id text not null,
            name text not null,
            url text,
            fetched_at text not null,
            heat real,
            velocity real,
            acceleration real,
            source_rank integer,
            description text,
            metadata_json text not null,
            raw_json text not null
        );
        """
    )
    conn.execute(
        "insert into snapshots(run_id, source, fetched_at, status, item_count, error) values (?, ?, ?, ?, ?, ?)",
        ("source-run-npm", "npm_search", "2026-05-31T00:00:00Z", "ok", 1, None),
    )
    snapshot_id = conn.execute("select id from snapshots").fetchone()[0]
    conn.execute(
        """
        insert into items(run_id, snapshot_id, source, external_id, name, url, fetched_at, metadata_json, raw_json)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "source-run-npm",
            snapshot_id,
            "npm_search",
            "agent:demo-package",
            "demo-package",
            "https://www.npmjs.com/package/demo-package",
            "2026-05-31T00:00:00Z",
            json.dumps(
                {
                    "weekly_downloads": 6831,
                    "monthly_downloads": 38818,
                    "repository": "git+https://github.com/owner/repo.git",
                }
            ),
            "{}",
        ),
    )
    conn.commit()


class DecisionRunnerTest(unittest.TestCase):
    def test_cli_help_exposes_bounded_llm_classifier_flags(self):
        result = subprocess.run(
            [sys.executable, "-m", "pipeline.decision.run_decision", "--help"],
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--classify-hn-limit", result.stdout)
        self.assertIn("--classify-x-limit", result.stdout)
        self.assertIn("--llm-model", result.stdout)
        self.assertIn("--llm-concurrency", result.stdout)
        self.assertIn("--x-credible-handles", result.stdout)
        self.assertIn("--resolver-research-limit", result.stdout)
        self.assertIn("--resolver-research-rounds", result.stdout)
        self.assertIn("--enrich-readme-limit", result.stdout)

    def test_run_from_args_wires_llm_provider_only_when_limits_are_explicit(self):
        from pipeline.decision.run_decision import run_from_args

        calls = []
        provider = object()

        def fake_runner(**kwargs):
            calls.append(kwargs)
            return {
                "entities": 0,
                "potential_candidates": 0,
                "edge_watch_candidates": 0,
                "backfill_jobs": 0,
                "export": str(kwargs["export_json_path"]),
            }

        args = Namespace(
            db=Path("db.sqlite"),
            run_id="run",
            export_json=Path("out.json"),
            now="2026-05-31T00:00:00Z",
            backfill=False,
            classify_hn_limit=2,
            classify_x_limit=3,
            llm_model="deepseek-v4-flash",
            llm_concurrency=3,
            x_stage1_batch_size=4,
            x_credible_handles="credible1, credible2",
        )

        summary = run_from_args(
            args,
            decision_runner=fake_runner,
            llm_provider_builder=lambda parsed: provider,
            github_client_builder=lambda: None,
        )

        self.assertEqual(summary["entities"], 0)
        self.assertIs(calls[0]["hn_llm_provider"], provider)
        self.assertIs(calls[0]["x_llm_provider"], provider)
        self.assertEqual(calls[0]["hn_classifier_limit"], 2)
        self.assertEqual(calls[0]["x_classifier_limit"], 3)
        self.assertEqual(calls[0]["llm_concurrency"], 3)
        self.assertEqual(calls[0]["x_stage1_batch_size"], 4)
        self.assertEqual(calls[0]["x_credible_handles"], {"credible1", "credible2"})

    def test_run_from_args_builds_llm_provider_for_agentic_research_limit(self):
        from pipeline.decision.run_decision import run_from_args

        calls = []
        provider = object()

        def fake_runner(**kwargs):
            calls.append(kwargs)
            return {
                "entities": 0,
                "potential_candidates": 0,
                "edge_watch_candidates": 0,
                "backfill_jobs": 0,
                "export": str(kwargs["export_json_path"]),
            }

        args = Namespace(
            db=Path("db.sqlite"),
            run_id="run",
            export_json=Path("out.json"),
            now="2026-05-31T00:00:00Z",
            backfill=False,
            classify_hn_limit=0,
            classify_x_limit=0,
            llm_model="deepseek-v4-flash",
            llm_concurrency=1,
            x_stage1_batch_size=100,
            x_credible_handles="",
            resolver_search_limit=0,
            resolver_research_limit=5,
            resolver_research_rounds=3,
            enrich_readme_limit=0,
        )

        run_from_args(
            args,
            decision_runner=fake_runner,
            llm_provider_builder=lambda parsed: provider,
            github_client_builder=lambda: None,
        )

        self.assertIs(calls[0]["resolver_research_provider"], provider)
        self.assertEqual(calls[0]["resolver_research_limit"], 5)
        self.assertEqual(calls[0]["resolver_research_rounds"], 3)

    def test_run_from_args_builds_search_client_for_agentic_research_limit(self):
        from pipeline.decision.run_decision import run_from_args

        calls = []
        search_client = object()

        def fake_runner(**kwargs):
            calls.append(kwargs)
            return {
                "entities": 0,
                "potential_candidates": 0,
                "edge_watch_candidates": 0,
                "backfill_jobs": 0,
                "export": str(kwargs["export_json_path"]),
            }

        args = Namespace(
            db=Path("db.sqlite"),
            run_id="run",
            export_json=Path("out.json"),
            now="2026-05-31T00:00:00Z",
            backfill=False,
            classify_hn_limit=0,
            classify_x_limit=0,
            llm_model="deepseek-v4-flash",
            llm_concurrency=1,
            x_stage1_batch_size=100,
            x_credible_handles="",
            resolver_search_limit=0,
            resolver_research_limit=5,
            resolver_research_rounds=3,
            enrich_readme_limit=0,
        )

        run_from_args(
            args,
            decision_runner=fake_runner,
            llm_provider_builder=lambda parsed: object(),
            github_client_builder=lambda: None,
            resolver_search_client_builder=lambda parsed: search_client,
        )

        self.assertIs(calls[0]["resolver_search_client"], search_client)

    def test_run_from_args_does_not_build_llm_provider_when_limits_are_zero(self):
        from pipeline.decision.run_decision import run_from_args

        def fail_builder(_args):
            raise AssertionError("llm provider should not be built")

        captured = {}

        def fake_runner(**kwargs):
            captured.update(kwargs)
            return {
                "entities": 0,
                "potential_candidates": 0,
                "edge_watch_candidates": 0,
                "backfill_jobs": 0,
                "export": str(kwargs["export_json_path"]),
            }

        args = Namespace(
            db=Path("db.sqlite"),
            run_id="run",
            export_json=Path("out.json"),
            now="2026-05-31T00:00:00Z",
            backfill=False,
            classify_hn_limit=0,
            classify_x_limit=0,
            llm_model=None,
            llm_concurrency=1,
            x_stage1_batch_size=100,
            x_credible_handles="",
        )

        run_from_args(
            args,
            decision_runner=fake_runner,
            llm_provider_builder=fail_builder,
            github_client_builder=lambda: None,
        )

        self.assertIsNone(captured["hn_llm_provider"])
        self.assertIsNone(captured["x_llm_provider"])

    def test_run_from_args_builds_readme_client_only_when_limit_is_set(self):
        from pipeline.decision.run_decision import run_from_args

        captured = {}
        readme_client = object()

        def fake_runner(**kwargs):
            captured.update(kwargs)
            return {
                "entities": 0,
                "potential_candidates": 0,
                "edge_watch_candidates": 0,
                "backfill_jobs": 0,
                "export": str(kwargs["export_json_path"]),
            }

        args = Namespace(
            db=Path("db.sqlite"),
            run_id="run",
            export_json=Path("out.json"),
            now="2026-05-31T00:00:00Z",
            backfill=False,
            classify_hn_limit=0,
            classify_x_limit=0,
            llm_model=None,
            llm_concurrency=1,
            x_stage1_batch_size=100,
            x_credible_handles="",
            resolver_search_limit=0,
            resolver_research_limit=0,
            resolver_research_rounds=3,
            enrich_readme_limit=2,
        )

        run_from_args(
            args,
            decision_runner=fake_runner,
            llm_provider_builder=lambda parsed: None,
            github_client_builder=lambda: None,
            github_readme_client_builder=lambda parsed: readme_client,
        )

        self.assertIs(captured["readme_client"], readme_client)
        self.assertEqual(captured["enrich_readme_limit"], 2)

    def test_enrich_readmes_for_candidates_uses_verified_github_links(self):
        from pipeline.decision.readme_enrichment import enrich_candidate_readmes

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)
        conn.execute(
            """
            insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:repo",
                "owner/repo",
                "github:Owner/Repo",
                "github",
                "2026-05-31T00:00:00Z",
                "[]",
                "[]",
            ),
        )
        conn.execute(
            """
            insert into potential_candidates(entity_id, run_id, level, fired_families_json, first_trigger_at)
            values (?, ?, ?, ?, ?)
            """,
            ("entity:repo", "run-1", "potential", "[]", "2026-05-31T00:00:00Z"),
        )
        conn.commit()
        client = FakeGitHubReadmeClient("hello readme")

        summary = enrich_candidate_readmes(conn, run_id="run-1", client=client, limit=10)

        self.assertEqual(summary["fetched"], 1)
        self.assertEqual(summary["cached"], 0)
        self.assertEqual(client.calls, ["owner/repo"])

    def test_runner_writes_entities_candidates_and_export(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            export_path = Path(tmpdir) / "candidates.json"
            conn = sqlite3.connect(db_path)
            seed_source_tables(conn)
            init_decision_db(conn)
            conn.close()

            summary = run_decision(
                db_path=db_path,
                run_id="decision-run-1",
                export_json_path=export_path,
                now="2026-05-31T00:00:00Z",
            )

            self.assertEqual(summary["potential_candidates"], 1)
            self.assertEqual(summary["x_time_basis"], "x_tweets_store.created_at")
            self.assertEqual(summary["x_classifier_candidates_7d"], 0)
            self.assertEqual(summary["hn_classifier_units"], 0)
            self.assertTrue(export_path.exists())

            payload = json.loads(export_path.read_text())
            self.assertEqual(payload["run_id"], "decision-run-1")
            self.assertEqual(payload["candidates"][0]["level"], "potential")

    def test_reconcile_entity_ids_does_not_revive_stale_title_alias_merge(self):
        from pipeline.decision.entity_resolution import Entity, ResolutionResult, entity_id_for_key
        from pipeline.decision.run_decision import reconcile_entity_ids

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        stale_title = "Show HN: Continue? Y/N: A 60-second game about AI agent permission fatigue"
        conn.execute(
            """
            insert into alias_links(entity_id, source, external_id, alias, confidence, origin, approved, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_id_for_key("domain:scalex.dev"),
                "decision",
                "domain:scalex.dev",
                stale_title,
                "deterministic",
                "stage_a",
                1,
                "2026-05-30T00:00:00Z",
            ),
        )
        new_entity_id = entity_id_for_key("domain:llmgame.scalex.dev")
        resolution = ResolutionResult(
            entities=[
                Entity(
                    entity_id=new_entity_id,
                    canonical_entity=stale_title,
                    canonical_key="domain:llmgame.scalex.dev",
                    key_type="domain",
                    aliases=(stale_title,),
                    source_refs=(),
                )
            ],
            item_to_entity={1: new_entity_id},
        )

        reconciled = reconcile_entity_ids(conn, resolution)

        self.assertEqual(reconciled, {new_entity_id: new_entity_id})

    def test_reconcile_entity_ids_does_not_reuse_stale_structured_alias(self):
        from pipeline.decision.entity_resolution import Entity, ResolutionResult, entity_id_for_key
        from pipeline.decision.run_decision import reconcile_entity_ids

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        stale_name_entity_id = entity_id_for_key("name:firecrawl")
        repo_key = "github:nickscamara/open-deep-research"
        conn.execute(
            """
            insert into alias_links(entity_id, source, external_id, alias, confidence, origin, approved, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stale_name_entity_id,
                "decision",
                repo_key,
                repo_key,
                "deterministic",
                "stage_a",
                1,
                "2026-05-30T00:00:00Z",
            ),
        )
        repo_entity_id = entity_id_for_key(repo_key)
        resolution = ResolutionResult(
            entities=[
                Entity(
                    entity_id=repo_entity_id,
                    canonical_entity="nickscamara/open-deep-research",
                    canonical_key=repo_key,
                    key_type="github",
                    aliases=("nickscamara/open-deep-research",),
                    source_refs=(),
                )
            ],
            item_to_entity={1: repo_entity_id},
        )

        reconciled = reconcile_entity_ids(conn, resolution)

        self.assertEqual(reconciled, {repo_entity_id: repo_entity_id})

    def test_classifier_only_entities_can_enter_final_resolution_without_link(self):
        from pipeline.decision.entity_resolution import ResolutionResult
        from pipeline.decision.run_decision import add_classifier_entities_to_resolution

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        conn.execute(
            """
            insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:repoprompt",
                "RepoPrompt",
                "name:repoprompt",
                "name",
                "2026-05-31T00:00:00Z",
                '["name:repoprompt"]',
                "[]",
            ),
        )
        evidence = [
            {
                "entity_id": "entity:repoprompt",
                "canonical_entity": "name:repoprompt",
                "alias": "name:repoprompt",
                "source": "x_tweets",
                "family": "x_social",
            }
        ]
        resolution = ResolutionResult(entities=[], item_to_entity={})

        augmented = add_classifier_entities_to_resolution(conn, resolution, evidence)

        self.assertEqual(len(augmented.entities), 1)
        self.assertEqual(augmented.entities[0].entity_id, "entity:repoprompt")
        self.assertEqual(augmented.entities[0].canonical_key, "name:repoprompt")

    def test_runner_does_not_reset_completed_backfill_jobs_to_pending(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            export_path = Path(tmpdir) / "candidates.json"
            conn = sqlite3.connect(db_path)
            seed_source_tables(conn)
            init_decision_db(conn)
            conn.close()

            run_decision(
                db_path=db_path,
                run_id="decision-run-1",
                export_json_path=export_path,
                now="2026-05-31T00:00:00Z",
                github_client=FakeGitHubClient(),
            )

            conn = sqlite3.connect(db_path)
            statuses = conn.execute(
                "select status from backfill_jobs where run_id = ?",
                ("decision-run-1",),
            ).fetchall()
            conn.close()
            self.assertEqual(statuses, [("completed",)])

    def test_runner_invokes_hn_classifier_before_final_rules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            export_path = Path(tmpdir) / "candidates.json"
            conn = sqlite3.connect(db_path)
            seed_hn_source_tables(conn)
            init_decision_db(conn)
            conn.close()
            provider = FakeLLMProvider(
                [
                    {
                        "item_id": 1,
                        "projectness": "news_article",
                        "confidence": 0.9,
                        "canonical_name": "",
                        "deterministic_links": [],
                        "proposed_links": [],
                        "summary": "News article, not a project launch.",
                    }
                ]
            )

            summary = run_decision(
                db_path=db_path,
                run_id="decision-run-hn",
                export_json_path=export_path,
                now="2026-05-31T00:00:00Z",
                hn_llm_provider=provider,
                hn_classifier_limit=1,
            )

            self.assertEqual(summary["hn_classified"], 1)
            self.assertEqual(summary["potential_candidates"], 0)
            conn = sqlite3.connect(db_path)
            noise = conn.execute(
                "select metric_value, signal_label from evidence_rows where source = 'hn_llm_classifier'"
            ).fetchone()
            conn.close()
            self.assertEqual(noise, ("news_article", "noise"))

    def test_runner_passes_current_pass_candidate_impacts_to_hn_classifier(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            export_path = Path(tmpdir) / "candidates.json"
            conn = sqlite3.connect(db_path)
            seed_hn_source_tables(conn)
            init_decision_db(conn)
            conn.close()

            with patch("pipeline.decision.hn_classifier.run_hn_classifier") as classifier:
                classifier.return_value = {"classified": 0}
                run_decision(
                    db_path=db_path,
                    run_id="decision-run-hn-impact",
                    export_json_path=export_path,
                    now="2026-05-31T00:00:00Z",
                    hn_llm_provider=object(),
                    hn_classifier_limit=1,
                )

            kwargs = classifier.call_args.kwargs
            self.assertEqual(kwargs["potential_item_ids"], {1})
            self.assertEqual(kwargs["edge_item_ids"], set())

    def test_runner_invokes_x_classifier_before_final_rules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            export_path = Path(tmpdir) / "candidates.json"
            conn = sqlite3.connect(db_path)
            seed_x_source_tables(conn)
            init_decision_db(conn)
            conn.close()
            provider = FakeLLMProvider(
                [
                    {
                        "triage": [
                                {
                                    "tweet_id": "t1",
                                    "about_concrete_project": True,
                                    "closer_look": True,
                                    "product_names": ["owner/repo"],
                                    "product_links": ["https://github.com/owner/repo"],
                                    "project_refs": [
                                    {
                                        "entity_key": "github:owner/repo",
                                        "entity_name": "owner/repo",
                                        "entity_confidence": "linked",
                                        "confidence": 0.9,
                                    }
                                ],
                                "expression_strength": "recommendation",
                                "evidence_quote": "New repo",
                                "reason": "Links a concrete repo.",
                            },
                                {
                                    "tweet_id": "t2",
                                    "about_concrete_project": True,
                                    "closer_look": True,
                                    "product_names": ["owner/repo"],
                                    "product_links": [],
                                    "project_refs": [
                                    {
                                        "entity_key": "github:owner/repo",
                                        "entity_name": "owner/repo",
                                        "entity_confidence": "exact_handle",
                                        "confidence": 0.8,
                                    }
                                ],
                                "expression_strength": "adoption_or_usage",
                                "evidence_quote": "Trying owner/repo",
                                "reason": "Mentions trying the same repo.",
                            },
                        ]
                    },
                    {
                        "entity_key": "github:owner/repo",
                        "x_tier": "potential",
                        "entity_confidence": "linked",
                        "x_expression_strength": "recommendation",
                        "cited_tweet_ids": ["t1", "t2"],
                        "rationale": "Two credible authors cited the same repo.",
                        "cross_source_notes": [],
                    },
                ]
            )

            summary = run_decision(
                db_path=db_path,
                run_id="decision-run-x",
                export_json_path=export_path,
                now="2026-05-31T04:00:00Z",
                x_llm_provider=provider,
                x_classifier_limit=10,
                x_credible_handles={"credible1", "credible2"},
            )

            self.assertEqual(summary["x_stage1_mentions"], 2)
            self.assertEqual(summary["x_stage2_tiered"], 1)
            self.assertEqual(summary["resolver_enriched"], 1)
            self.assertEqual(summary["potential_candidates"], 1)
            payload = json.loads(export_path.read_text())
            self.assertEqual(payload["candidates"][0]["fired_families"], ["x_social"])
            conn = sqlite3.connect(db_path)
            resolver_alias = conn.execute(
                "select alias from alias_links where origin = 'resolver'"
            ).fetchone()
            conn.close()
            self.assertEqual(resolver_alias, ("github:owner/repo",))

    def test_runner_promotes_name_only_x_candidate_with_resolver_link(self):
        from pipeline.decision.candidate_context import context_bundle_for_entity
        from pipeline.decision.entity_resolution import entity_id_for_key

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            export_path = Path(tmpdir) / "candidates.json"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                create table snapshots (
                    id integer primary key autoincrement,
                    run_id text not null,
                    source text not null,
                    fetched_at text not null,
                    status text not null,
                    item_count integer not null,
                    error text
                );
                create table items (
                    id integer primary key autoincrement,
                    run_id text not null,
                    snapshot_id integer not null,
                    source text not null,
                    external_id text not null,
                    name text not null,
                    url text,
                    fetched_at text not null,
                    heat real,
                    velocity real,
                    acceleration real,
                    source_rank integer,
                    description text,
                    metadata_json text not null,
                    raw_json text not null
                );
                create table x_tweets_store (
                    tweet_id text primary key,
                    author_username text not null,
                    text text not null,
                    url text,
                    created_at text not null,
                    imported_at text not null,
                    raw_json text not null
                );
                """
            )
            conn.execute(
                "insert into snapshots(run_id, source, fetched_at, status, item_count, error) values (?, ?, ?, ?, ?, ?)",
                ("source-run-x", "x_tweets", "2026-05-31T00:00:00Z", "ok", 1, None),
            )
            snapshot_id = conn.execute("select id from snapshots").fetchone()[0]
            conn.execute(
                """
                insert into items(run_id, snapshot_id, source, external_id, name, url, fetched_at, description, metadata_json, raw_json)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "source-run-x",
                    snapshot_id,
                    "x_tweets",
                    "t-fire",
                    "Firecrawl launched /monitor for agents",
                    "https://x.com/credible1/status/t-fire",
                    "2026-05-31T01:00:00Z",
                    "Firecrawl launched /monitor for agents",
                    json.dumps({"author": "credible1", "created_at": "2026-05-31T01:00:00Z"}),
                    "{}",
                ),
            )
            conn.execute(
                """
                insert into x_tweets_store(tweet_id, author_username, text, url, created_at, imported_at, raw_json)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "t-fire",
                    "credible1",
                    "Firecrawl launched /monitor for agents.",
                    "https://x.com/credible1/status/t-fire",
                    "2026-05-31T01:00:00Z",
                    "2026-05-31T02:00:00Z",
                    "{}",
                ),
            )
            init_decision_db(conn)
            conn.close()
            provider = FakeLLMProvider(
                [
                    {
                        "triage": [
                            {
                                "tweet_id": "t-fire",
                                "about_concrete_project": True,
                                "closer_look": True,
                                "product_names": ["Firecrawl"],
                                "product_links": [],
                                "project_refs": [
                                    {
                                        "entity_key": "name:firecrawl",
                                        "entity_name": "Firecrawl",
                                        "entity_confidence": "fuzzy_name",
                                        "confidence": 0.8,
                                    }
                                ],
                                "expression_strength": "adoption_or_usage",
                                "evidence_quote": "Firecrawl launched /monitor",
                                "reason": "Concrete product feature launch.",
                            }
                        ]
                    },
                    {
                        "entity_key": "name:firecrawl",
                        "x_tier": "potential",
                        "entity_confidence": "fuzzy_name",
                        "x_expression_strength": "adoption_or_usage",
                        "cited_tweet_ids": ["t-fire"],
                        "rationale": "One credible author describes using Firecrawl /monitor.",
                        "cross_source_notes": [],
                    },
                ]
            )
            search_client = FakeSearchClient(
                [
                    {
                        "type": "domain",
                        "key": "domain:firecrawl.dev",
                        "url": "https://firecrawl.dev",
                        "confidence": 0.9,
                    }
                ]
            )

            summary = run_decision(
                db_path=db_path,
                run_id="decision-run-x-name",
                export_json_path=export_path,
                now="2026-05-31T04:00:00Z",
                x_llm_provider=provider,
                x_classifier_limit=10,
                x_credible_handles={"credible1"},
                resolver_search_client=search_client,
                resolver_search_limit=1,
            )

            self.assertEqual(summary["edge_watch_candidates"], 1)
            entity_id = entity_id_for_key("name:firecrawl")
            payload = json.loads(export_path.read_text())
            self.assertEqual(payload["edge_watch"][0]["entity_id"], entity_id)
            self.assertEqual(payload["edge_watch"][0]["canonical_key"], "name:firecrawl")
            conn = sqlite3.connect(db_path)
            bundle = context_bundle_for_entity(conn, entity_id=entity_id, run_id="decision-run-x-name")
            conn.close()
            self.assertEqual(bundle["canonical_link"], "https://firecrawl.dev")
            self.assertEqual(bundle["binding_confidence"], "resolved")

    def test_runner_invokes_npm_backfill_before_final_rules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            export_path = Path(tmpdir) / "candidates.json"
            conn = sqlite3.connect(db_path)
            seed_npm_source_tables(conn)
            init_decision_db(conn)
            conn.close()

            summary = run_decision(
                db_path=db_path,
                run_id="decision-run-npm",
                export_json_path=export_path,
                now="2026-05-31T00:00:00Z",
                npm_client=FakeNpmClient(),
                npm_backfill_limit=1,
            )

            self.assertEqual(summary["npm_backfill_completed"], 1)
            self.assertEqual(summary["potential_candidates"], 1)
            payload = json.loads(export_path.read_text())
            self.assertEqual(payload["candidates"][0]["fired_families"], ["package_family"])
            self.assertEqual(payload["backfill_jobs"][0]["source"], "npm_registry")
            self.assertEqual(payload["backfill_jobs"][0]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
