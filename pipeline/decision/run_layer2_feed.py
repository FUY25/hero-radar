from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from pipeline.decision.kimi_provider import (
    DEFAULT_KIMI_DEEPDIVE_MODEL,
    DEFAULT_KIMI_SCORING_MODEL,
    KimiProvider,
    KimiWebSearchClient,
)
from pipeline.decision.layer2_context import assemble_group_context
from pipeline.decision.layer2_deepdive import (
    DeepdiveLimits,
    default_deepdive_tools,
    run_deepdives,
    select_deepdives,
)
from pipeline.decision.layer2_grouping import (
    build_candidate_groups,
    persist_candidate_groups,
)
from pipeline.decision.layer2_harness import (
    TelemetryLLMProvider,
    final_run_status,
    record_stage_event,
    stage_summary,
)
from pipeline.decision.layer2_scheduler import schedule_layer2_work
from pipeline.decision.layer2_scoring import score_candidate_groups
from pipeline.decision.layer2_scout import scout_edge_watch_groups
from pipeline.decision.schema import init_decision_db, to_json, utc_now


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "hero_radar.sqlite"


def default_feed_run_id(now: str | None = None) -> str:
    value = now or utc_now()
    compact = value.replace("-", "").replace(":", "").rstrip("Z")
    return f"l2_{compact}"


def latest_decision_run(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        """
        select run_id
        from decision_runs
        where status = 'ok'
        order by coalesce(completed_at, started_at) desc
        limit 1
        """
    ).fetchone()
    if not row:
        raise RuntimeError("no successful decision run found")
    return str(row[0])


def finalize_stale_running_runs(
    conn: sqlite3.Connection,
    *,
    before_started_at: str,
    completed_at: str,
) -> int:
    note = to_json(
        {
            "reason": "stale running run finalized before starting new Layer 2 run",
            "stale_before": before_started_at,
        }
    )
    cursor = conn.execute(
        """
        update l2_feed_runs
        set completed_at = ?, status = ?, note = ?
        where status = 'running' and started_at < ?
        """,
        (completed_at, "error", note, before_started_at),
    )
    return int(cursor.rowcount or 0)


def previous_group_hashes(
    conn: sqlite3.Connection, decision_run_id: str
) -> dict[str, str]:
    row = conn.execute(
        """
        select feed_run_id
        from l2_feed_runs
        where decision_run_id = ? and status = 'ok'
        order by coalesce(completed_at, started_at) desc
        limit 1
        """,
        (decision_run_id,),
    ).fetchone()
    if not row:
        return {}
    return {
        str(group_id): str(evidence_hash)
        for group_id, evidence_hash in conn.execute(
            """
            select group_id, evidence_hash
            from l2_candidate_groups
            where feed_run_id = ?
            """,
            (row[0],),
        ).fetchall()
    }


def run_layer2_feed(
    *,
    db_path: Path = DB_PATH,
    decision_run_id: str | None = None,
    feed_run_id: str | None = None,
    now: str | None = None,
    provider: Any | None = None,
    deepdive_provider: Any | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config or {}
    active_now = now or utc_now()
    active_feed_run_id = feed_run_id or default_feed_run_id(active_now)
    conn = sqlite3.connect(db_path)
    init_decision_db(conn)
    try:
        active_decision_run_id = decision_run_id or latest_decision_run(conn)
        stale_before = cfg.get("finalize_stale_running_before")
        if stale_before:
            finalize_stale_running_runs(
                conn,
                before_started_at=str(stale_before),
                completed_at=active_now,
            )
            conn.commit()
        scout_provider = provider or KimiProvider(
            model=str(cfg.get("edge_scout_model") or DEFAULT_KIMI_SCORING_MODEL)
        )
        scoring_provider = provider or KimiProvider(
            model=str(cfg.get("scoring_model") or DEFAULT_KIMI_SCORING_MODEL)
        )
        active_deepdive_provider = deepdive_provider or provider or KimiProvider(
            model=str(cfg.get("deepdive_model") or DEFAULT_KIMI_DEEPDIVE_MODEL)
        )
        model_profile = {
            "scout": getattr(scout_provider, "model", ""),
            "scoring": getattr(scoring_provider, "model", ""),
            "deepdive": getattr(active_deepdive_provider, "model", ""),
        }
        conn.execute(
            """
            insert or replace into l2_feed_runs(
              feed_run_id, decision_run_id, started_at, status,
              config_hash, model_profile_json, note
            )
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                active_feed_run_id,
                active_decision_run_id,
                active_now,
                "running",
                "manual",
                to_json(model_profile),
                "",
            ),
        )
        raw_groups = build_candidate_groups(
            conn, decision_run_id=active_decision_run_id
        )
        groups = [
            assemble_group_context(
                conn, decision_run_id=active_decision_run_id, group=group
            )
            for group in raw_groups
        ]
        persist_candidate_groups(conn, feed_run_id=active_feed_run_id, groups=groups)
        schedule = schedule_layer2_work(
            groups,
            previous_hashes=previous_group_hashes(conn, active_decision_run_id),
            max_edge_watch_scout=int(
                cfg.get("max_edge_watch_scout", cfg.get("edge_scout_limit", 50))
            ),
            max_scored_candidates=int(
                cfg.get("max_scored_candidates", cfg.get("scoring_limit", 150))
            ),
        )
        for skipped in schedule.skipped:
            record_stage_event(
                conn,
                feed_run_id=active_feed_run_id,
                group_id=skipped["group_id"],
                stage="schedule",
                status="skipped_unchanged",
                metadata=skipped,
            )
        for group in schedule.pending:
            record_stage_event(
                conn,
                feed_run_id=active_feed_run_id,
                group_id=group.group_id,
                stage="schedule",
                status="pending_budget",
        )
        scouted = []
        if bool(cfg.get("enable_edge_scout", False)):
            for group in schedule.scout_edge_watch:
                try:
                    active_scout_provider = TelemetryLLMProvider(
                        scout_provider,
                        conn=conn,
                        feed_run_id=active_feed_run_id,
                        group_id=group.group_id,
                        stage="scout",
                        timeout_seconds=_optional_int(cfg.get("scout_timeout_seconds")),
                    )
                    result = scout_edge_watch_groups(
                        conn,
                        feed_run_id=active_feed_run_id,
                        groups=[group],
                        provider=active_scout_provider,
                    )
                    record_stage_event(
                        conn,
                        feed_run_id=active_feed_run_id,
                        group_id=group.group_id,
                        stage="scout",
                        status="scout_ok" if result else "scout_filtered",
                    )
                    scouted.extend(result)
                except Exception as exc:
                    record_stage_event(
                        conn,
                        feed_run_id=active_feed_run_id,
                        group_id=group.group_id,
                        stage="scout",
                        status="scout_error",
                        error=exc,
                    )
        else:
            for group in schedule.scout_edge_watch:
                record_stage_event(
                    conn,
                    feed_run_id=active_feed_run_id,
                    group_id=group.group_id,
                    stage="scout",
                    status="scout_disabled",
                    metadata={"reason": "enable_edge_scout=false"},
                )
        scored = []
        scoring_candidates = [*schedule.score_now, *scouted]
        max_total_scoring = cfg.get("max_total_scoring_candidates")
        if max_total_scoring is not None:
            cap = max(0, int(max_total_scoring))
            deferred = scoring_candidates[cap:]
            scoring_candidates = scoring_candidates[:cap]
            for group in deferred:
                record_stage_event(
                    conn,
                    feed_run_id=active_feed_run_id,
                    group_id=group.group_id,
                    stage="scoring",
                    status="pending_budget",
                    metadata={"reason": "max_total_scoring_candidates"},
                )
        for group in scoring_candidates:
            try:
                active_scoring_provider = TelemetryLLMProvider(
                    scoring_provider,
                    conn=conn,
                    feed_run_id=active_feed_run_id,
                    group_id=group.group_id,
                    stage="scoring",
                    timeout_seconds=_optional_int(cfg.get("scoring_timeout_seconds")),
                )
                result = score_candidate_groups(
                    conn,
                    feed_run_id=active_feed_run_id,
                    groups=[group],
                    provider=active_scoring_provider,
                )
                scored.extend(result)
                record_stage_event(
                    conn,
                    feed_run_id=active_feed_run_id,
                    group_id=group.group_id,
                    stage="scoring",
                    status="scoring_ok",
                )
            except Exception as exc:
                record_stage_event(
                    conn,
                    feed_run_id=active_feed_run_id,
                    group_id=group.group_id,
                    stage="scoring",
                    status="scoring_error",
                    error=exc,
                )
        for rank, row in enumerate(
            sorted(scored, key=lambda item: -float(item["l2_score"])), start=1
        ):
            conn.execute(
                """
                insert or replace into l2_feed_items(
                  feed_run_id, group_id, section, rank, deepdive_status
                )
                values (?, ?, ?, ?, ?)
                """,
                (
                    active_feed_run_id,
                    row["group"].group_id,
                    "scored",
                    rank,
                    "not_deepdived",
                ),
            )
        web_search_timeout = _optional_int(cfg.get("web_search_timeout_seconds"))
        if web_search_timeout is not None and hasattr(active_deepdive_provider, "timeout"):
            setattr(active_deepdive_provider, "timeout", web_search_timeout)
        web_search_client = (
            KimiWebSearchClient(provider=active_deepdive_provider)
            if bool(cfg.get("enable_kimi_web_search", False))
            else None
        )
        active_tools = default_deepdive_tools(
            conn,
            decision_run_id=active_decision_run_id,
            enable_kimi_web_search=bool(cfg.get("enable_kimi_web_search", False)),
            web_search_client=web_search_client,
        )
        active_limits = DeepdiveLimits(
            max_tool_calls=int(
                cfg.get(
                    "max_tool_calls_per_candidate",
                    (
                        int(cfg.get("max_web_search_calls_per_candidate", 3))
                        + int(cfg.get("max_repo_files_per_candidate", 8))
                        + int(cfg.get("max_pages_per_candidate", 6))
                    ),
                )
            ),
            max_web_search_calls=int(cfg.get("max_web_search_calls_per_candidate", 3)),
            max_repo_file_calls=int(cfg.get("max_repo_files_per_candidate", 8)),
            max_page_fetch_calls=int(cfg.get("max_pages_per_candidate", 6)),
            max_hn_thread_calls=int(
                cfg.get("max_hn_thread_fetches_per_candidate", 3)
            ),
            max_x_context_calls=int(cfg.get("max_x_context_fetches_per_candidate", 5)),
        )
        selected_for_deepdive = select_deepdives(
            scored,
            max_deepdives=int(cfg.get("max_deepdives_per_run", 10)),
            min_l2_score=float(cfg.get("deepdive_min_l2_score", 70)),
        )
        reports = []
        for row in selected_for_deepdive:
            group = row["group"]
            record_stage_event(
                conn,
                feed_run_id=active_feed_run_id,
                group_id=group.group_id,
                stage="deepdive",
                status="deepdive_selected",
            )
            try:
                active_deepdive_stage_provider = TelemetryLLMProvider(
                    active_deepdive_provider,
                    conn=conn,
                    feed_run_id=active_feed_run_id,
                    group_id=group.group_id,
                    stage="deepdive",
                    timeout_seconds=_optional_int(cfg.get("deepdive_timeout_seconds")),
                )
                reports.extend(
                    run_deepdives(
                        conn,
                        feed_run_id=active_feed_run_id,
                        scored=[row],
                        provider=active_deepdive_stage_provider,
                        max_deepdives=1,
                        min_l2_score=0,
                        tools=active_tools,
                        limits=active_limits,
                    )
                )
                record_stage_event(
                    conn,
                    feed_run_id=active_feed_run_id,
                    group_id=group.group_id,
                    stage="deepdive",
                    status="deepdive_ok",
                )
            except Exception as exc:
                record_stage_event(
                    conn,
                    feed_run_id=active_feed_run_id,
                    group_id=group.group_id,
                    stage="deepdive",
                    status="deepdive_error",
                    error=exc,
                )
        telemetry = stage_summary(conn, active_feed_run_id)
        status = final_run_status(telemetry)
        note = {
            "scored": len(scored),
            "deepdives": len(reports),
            "stage_counts": telemetry["stage_counts"],
            "error_counts": telemetry["error_counts"],
            "success_total": telemetry["success_total"],
            "error_total": telemetry["error_total"],
        }
        conn.execute(
            """
            update l2_feed_runs
            set completed_at = ?, status = ?, note = ?
            where feed_run_id = ?
            """,
            (
                utc_now(),
                status,
                to_json(note),
                active_feed_run_id,
            ),
        )
        conn.commit()
        return {
            "ok": True,
            "feed_run_id": active_feed_run_id,
            "decision_run_id": active_decision_run_id,
            "groups": len(groups),
            "scored": len(scored),
            "deepdives": len(reports),
            "status": status,
            "errors": telemetry["error_total"],
        }
    except Exception as exc:
        conn.execute(
            """
            update l2_feed_runs
            set completed_at = ?, status = ?, note = ?
            where feed_run_id = ?
            """,
            (utc_now(), "error", f"{type(exc).__name__}: {exc}", active_feed_run_id),
        )
        conn.commit()
        raise
    finally:
        conn.close()


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Layer 2 Kimi Feed")
    parser.add_argument("--decision-run-id", default=None)
    parser.add_argument("--feed-run-id", default=None)
    parser.add_argument("--now", default=None)
    parser.add_argument("--edge-scout-limit", type=int, default=50)
    parser.add_argument("--scoring-limit", type=int, default=150)
    parser.add_argument("--deepdive-limit", type=int, default=10)
    parser.add_argument("--no-deepdive", action="store_true")
    parser.add_argument("--deepdive-min-l2-score", type=float, default=70)
    parser.add_argument("--enable-edge-scout", action="store_true")
    parser.add_argument("--scout-model", default=DEFAULT_KIMI_SCORING_MODEL)
    parser.add_argument("--scoring-model", default=DEFAULT_KIMI_SCORING_MODEL)
    parser.add_argument("--deepdive-model", default=DEFAULT_KIMI_DEEPDIVE_MODEL)
    parser.add_argument("--enable-kimi-web-search", action="store_true")
    parser.add_argument("--max-total-scoring-candidates", type=int, default=None)
    parser.add_argument("--scout-timeout-seconds", type=int, default=None)
    parser.add_argument("--scoring-timeout-seconds", type=int, default=None)
    parser.add_argument("--deepdive-timeout-seconds", type=int, default=None)
    parser.add_argument("--web-search-timeout-seconds", type=int, default=None)
    parser.add_argument("--finalize-stale-running-before", default=None)
    parser.add_argument("--max-tool-calls-per-candidate", type=int, default=20)
    parser.add_argument("--max-web-search-calls-per-candidate", type=int, default=3)
    parser.add_argument("--max-repo-files-per-candidate", type=int, default=8)
    parser.add_argument("--max-pages-per-candidate", type=int, default=6)
    parser.add_argument("--max-hn-thread-fetches-per-candidate", type=int, default=3)
    parser.add_argument("--max-x-context-fetches-per-candidate", type=int, default=5)
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_arg_parser().parse_args(argv)


def config_from_args(args: argparse.Namespace) -> dict[str, Any]:
    deepdive_limit = 0 if args.no_deepdive else args.deepdive_limit
    return {
        "max_edge_watch_scout": args.edge_scout_limit,
        "max_scored_candidates": args.scoring_limit,
        "max_deepdives_per_run": deepdive_limit,
        "deepdive_min_l2_score": args.deepdive_min_l2_score,
        "enable_edge_scout": args.enable_edge_scout,
        "edge_scout_model": args.scout_model,
        "scoring_model": args.scoring_model,
        "deepdive_model": args.deepdive_model,
        "enable_kimi_web_search": args.enable_kimi_web_search,
        "max_total_scoring_candidates": args.max_total_scoring_candidates,
        "scout_timeout_seconds": args.scout_timeout_seconds,
        "scoring_timeout_seconds": args.scoring_timeout_seconds,
        "deepdive_timeout_seconds": args.deepdive_timeout_seconds,
        "web_search_timeout_seconds": args.web_search_timeout_seconds,
        "finalize_stale_running_before": args.finalize_stale_running_before,
        "max_tool_calls_per_candidate": args.max_tool_calls_per_candidate,
        "max_web_search_calls_per_candidate": args.max_web_search_calls_per_candidate,
        "max_repo_files_per_candidate": args.max_repo_files_per_candidate,
        "max_pages_per_candidate": args.max_pages_per_candidate,
        "max_hn_thread_fetches_per_candidate": args.max_hn_thread_fetches_per_candidate,
        "max_x_context_fetches_per_candidate": args.max_x_context_fetches_per_candidate,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary = run_layer2_feed(
        decision_run_id=args.decision_run_id,
        feed_run_id=args.feed_run_id,
        now=args.now,
        config=config_from_args(args),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
