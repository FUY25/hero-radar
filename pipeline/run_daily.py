#!/usr/bin/env python3
"""Run the full daily Hero Radar pipeline.

This orchestrates the source collection step and the pre-Layer2 decision step.
Cron should call this file, not the individual stage scripts.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.run_logging import JsonlRunLogger

PYTHON = sys.executable or "python3"
DEFAULT_TIMEOUT_SECONDS = 3600
CONFIG_RELATIVE_PATH = Path("pipeline") / "config.json"
DB_RELATIVE_PATH = Path("data") / "hero_radar.sqlite"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_run_id(now: str | None = None) -> str:
    value = now or utc_now()
    compact = value.replace("-", "").replace(":", "").replace("+00:00", "Z")
    return f"decision_daily_{compact.rstrip('Z')}"


def read_pipeline_config(root: Path) -> dict[str, Any]:
    config_path = Path(root) / CONFIG_RELATIVE_PATH
    if not config_path.exists():
        return {}
    return json.loads(config_path.read_text())


def _layer2_config(config: dict[str, Any]) -> dict[str, Any]:
    layer2 = config.get("layer2", {})
    return layer2 if isinstance(layer2, dict) else {}


def _decision_config(config: dict[str, Any]) -> dict[str, Any]:
    decision = config.get("decision", {})
    return decision if isinstance(decision, dict) else {}


def _configured_bool(
    explicit: bool | None,
    cfg: dict[str, Any],
    key: str,
    fallback: bool,
) -> bool:
    if explicit is not None:
        return bool(explicit)
    return bool(cfg.get(key, fallback))


def _configured_int(
    explicit: int | None,
    cfg: dict[str, Any],
    key: str,
    fallback: int,
) -> int:
    if explicit is not None:
        return int(explicit)
    value = cfg.get(key, fallback)
    return int(value)


def _configured_optional_int(
    explicit: int | None,
    cfg: dict[str, Any],
    key: str,
) -> int | None:
    if explicit is not None:
        return int(explicit)
    value = cfg.get(key)
    if value is None or value == "":
        return None
    return int(value)


def _configured_optional_float(
    explicit: float | None,
    cfg: dict[str, Any],
    key: str,
) -> float | None:
    if explicit is not None:
        return float(explicit)
    value = cfg.get(key)
    if value is None or value == "":
        return None
    return float(value)


def _configured_optional_str(
    explicit: str | None,
    cfg: dict[str, Any],
    key: str,
) -> str | None:
    if explicit:
        return explicit
    value = cfg.get(key)
    if value is None or value == "":
        return None
    return str(value)


def completed_stages_from_log(log_path: Path) -> set[str]:
    if not log_path.exists():
        return set()
    completed: set[str] = set()
    for line in log_path.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            event.get("event") == "stage_completed"
            and int(event.get("returncode", 1)) == 0
            and event.get("stage")
        ):
            completed.add(str(event["stage"]))
    return completed


def decision_run_is_complete(root: Path, run_id: str) -> bool:
    db_path = Path(root) / DB_RELATIVE_PATH
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "select status from decision_runs where run_id = ?",
            (run_id,),
        ).fetchone()
    except sqlite3.Error:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return bool(row and row[0] in {"ok", "ok_with_errors"})


def layer2_run_is_complete(root: Path, decision_run_id: str) -> bool:
    db_path = Path(root) / DB_RELATIVE_PATH
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            """
            select status
            from l2_feed_runs
            where decision_run_id = ? and status in ('ok', 'ok_with_errors')
            order by coalesce(completed_at, started_at) desc
            limit 1
            """,
            (decision_run_id,),
        ).fetchone()
    except sqlite3.Error:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return bool(row)


def stale_lock_matches_run(lock_path: Path, run_id: str) -> bool:
    try:
        payload = json.loads(lock_path.read_text())
    except Exception:
        return False
    if payload.get("run_id") != run_id:
        return False
    pid = payload.get("pid")
    if isinstance(pid, int) and pid > 0 and pid != os.getpid():
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        return False
    return True


def source_command(
    *,
    root: Path,
    python: str,
    only_sources: list[str] | None = None,
    log_path: Path | str | None = None,
) -> list[str]:
    cmd = [python, str(root / "pipeline" / "run_pipeline.py")]
    if only_sources:
        cmd.extend(["--only", ",".join(only_sources)])
    if log_path:
        cmd.extend(["--log-path", str(log_path)])
    return cmd


def decision_command(
    *,
    python: str,
    run_id: str,
    now: str,
    backfill: bool,
    classify_hn_limit: int,
    classify_x_limit: int,
    llm_concurrency: int,
    io_concurrency: int | None = None,
    io_rate_limit_per_second: float | None = None,
    resolver_search_limit: int,
    resolver_research_limit: int,
    resolver_research_rounds: int,
    npm_backfill_limit: int,
    enrich_readme_limit: int,
    log_path: Path | str | None = None,
) -> list[str]:
    cmd = [
        python,
        "-m",
        "pipeline.decision.run_decision",
        "--run-id",
        run_id,
        "--now",
        now,
    ]
    if backfill:
        cmd.append("--backfill")
    if classify_hn_limit > 0:
        cmd.extend(["--classify-hn-limit", str(classify_hn_limit)])
    if classify_x_limit > 0:
        cmd.extend(["--classify-x-limit", str(classify_x_limit)])
    cmd.extend(["--llm-concurrency", str(max(1, llm_concurrency))])
    if io_concurrency is not None:
        cmd.extend(["--io-concurrency", str(max(1, io_concurrency))])
    if io_rate_limit_per_second is not None:
        cmd.extend(
            [
                "--io-rate-limit-per-second",
                str(max(0.0, io_rate_limit_per_second)),
            ]
        )
    if resolver_search_limit > 0:
        cmd.extend(["--resolver-search-limit", str(resolver_search_limit)])
    if resolver_research_limit > 0:
        cmd.extend(["--resolver-research-limit", str(resolver_research_limit)])
        cmd.extend(["--resolver-research-rounds", str(max(0, resolver_research_rounds))])
    if npm_backfill_limit > 0:
        cmd.extend(["--npm-backfill-limit", str(npm_backfill_limit)])
    if enrich_readme_limit > 0:
        cmd.extend(["--enrich-readme-limit", str(enrich_readme_limit)])
    if log_path:
        cmd.extend(["--log-path", str(log_path)])
    return cmd


def layer2_command(
    *,
    python: str,
    decision_run_id: str,
    now: str,
    scout_limit: int,
    scoring_limit: int,
    deepdive_limit: int,
    deepdive_min_l2_score: float | None = None,
    scout_model: str | None = None,
    scoring_model: str | None = None,
    deepdive_model: str | None = None,
    enable_kimi_web_search: bool = False,
    max_tool_calls: int | None = None,
    max_web_search_calls: int | None = None,
    max_repo_files: int | None = None,
    max_pages: int | None = None,
    max_hn_thread_fetches: int | None = None,
    max_x_context_fetches: int | None = None,
    scoring_concurrency: int | None = None,
    brief_concurrency: int | None = None,
    max_parallel_tool_calls: int | None = None,
    github_tool_concurrency: int | None = None,
    homepage_tool_concurrency: int | None = None,
    web_search_tool_concurrency: int | None = None,
    github_tool_rate_limit_per_second: float | None = None,
    homepage_tool_rate_limit_per_second: float | None = None,
    web_search_tool_rate_limit_per_second: float | None = None,
    finalize_stale_running_before: str | None = None,
) -> list[str]:
    cmd = [
        python,
        "-m",
        "pipeline.decision.run_layer2_feed",
        "--decision-run-id",
        decision_run_id,
        "--now",
        now,
        "--edge-scout-limit",
        str(scout_limit),
        "--scoring-limit",
        str(scoring_limit),
        "--deepdive-limit",
        str(deepdive_limit),
    ]
    if deepdive_min_l2_score is not None:
        cmd.extend(["--deepdive-min-l2-score", str(deepdive_min_l2_score)])
    if scout_model:
        cmd.extend(["--scout-model", scout_model])
    if scoring_model:
        cmd.extend(["--scoring-model", scoring_model])
    if deepdive_model:
        cmd.extend(["--deepdive-model", deepdive_model])
    if enable_kimi_web_search:
        cmd.append("--enable-kimi-web-search")
    if max_tool_calls is not None:
        cmd.extend(["--max-tool-calls-per-candidate", str(max_tool_calls)])
    if max_web_search_calls is not None:
        cmd.extend(["--max-web-search-calls-per-candidate", str(max_web_search_calls)])
    if max_repo_files is not None:
        cmd.extend(["--max-repo-files-per-candidate", str(max_repo_files)])
    if max_pages is not None:
        cmd.extend(["--max-pages-per-candidate", str(max_pages)])
    if max_hn_thread_fetches is not None:
        cmd.extend(["--max-hn-thread-fetches-per-candidate", str(max_hn_thread_fetches)])
    if max_x_context_fetches is not None:
        cmd.extend(["--max-x-context-fetches-per-candidate", str(max_x_context_fetches)])
    concurrency_options = (
        ("--scoring-concurrency", scoring_concurrency),
        ("--brief-concurrency", brief_concurrency),
        ("--max-parallel-tool-calls-per-turn", max_parallel_tool_calls),
        ("--github-tool-concurrency", github_tool_concurrency),
        ("--homepage-tool-concurrency", homepage_tool_concurrency),
        ("--web-search-tool-concurrency", web_search_tool_concurrency),
    )
    for flag, value in concurrency_options:
        if value is not None:
            cmd.extend([flag, str(max(1, value))])
    rate_options = (
        ("--github-tool-rate-limit-per-second", github_tool_rate_limit_per_second),
        ("--homepage-tool-rate-limit-per-second", homepage_tool_rate_limit_per_second),
        ("--web-search-tool-rate-limit-per-second", web_search_tool_rate_limit_per_second),
    )
    for flag, value in rate_options:
        if value is not None:
            cmd.extend([flag, str(max(0.0, value))])
    if finalize_stale_running_before:
        cmd.extend(["--finalize-stale-running-before", finalize_stale_running_before])
    return cmd


def _run_stage(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    cmd: list[str],
    cwd: Path,
    timeout: int,
) -> dict[str, Any]:
    started = time.monotonic()
    result = runner(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return {
        "cmd": cmd,
        "returncode": int(result.returncode),
        "stdout": str(result.stdout or "")[-4000:],
        "stderr": str(result.stderr or "")[-8000:],
        "duration_seconds": round(time.monotonic() - started, 3),
    }


def _run_logged_stage(
    *,
    logger: JsonlRunLogger,
    name: str,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    cmd: list[str],
    cwd: Path,
    timeout: int,
) -> dict[str, Any]:
    logger.event("stage_started", stage=name, cmd=cmd, timeout=timeout)
    try:
        stage = {
            "name": name,
            **_run_stage(runner=runner, cmd=cmd, cwd=cwd, timeout=timeout),
        }
    except Exception as exc:
        logger.event("stage_failed", stage=name, error=type(exc).__name__, message=str(exc))
        raise
    event = "stage_completed" if stage["returncode"] == 0 else "stage_failed"
    logger.event(
        event,
        stage=name,
        returncode=stage["returncode"],
        duration_seconds=stage["duration_seconds"],
        stdout_tail=stage["stdout"],
        stderr_tail=stage["stderr"],
    )
    return stage


def run_daily(
    *,
    root: Path = ROOT,
    python: str = PYTHON,
    run_id: str | None = None,
    now: str | None = None,
    only_sources: list[str] | None = None,
    skip_sources: bool = False,
    skip_decision: bool = False,
    backfill: bool = True,
    classify_hn_limit: int = 200,
    classify_x_limit: int = 300,
    llm_concurrency: int = 4,
    decision_io_concurrency: int | None = None,
    decision_io_rate_limit_per_second: float | None = None,
    resolver_search_limit: int = 100,
    resolver_research_limit: int = 50,
    resolver_research_rounds: int = 3,
    npm_backfill_limit: int = 40,
    enrich_readme_limit: int = 100,
    run_layer2: bool | None = None,
    layer2_scout_limit: int | None = None,
    layer2_scoring_limit: int | None = None,
    layer2_deepdive_limit: int | None = None,
    layer2_deepdive_min_l2_score: float | None = None,
    layer2_scout_model: str | None = None,
    layer2_scoring_model: str | None = None,
    layer2_deepdive_model: str | None = None,
    layer2_enable_kimi_web_search: bool | None = None,
    layer2_max_tool_calls: int | None = None,
    layer2_max_web_search_calls: int | None = None,
    layer2_max_repo_files: int | None = None,
    layer2_max_pages: int | None = None,
    layer2_max_hn_thread_fetches: int | None = None,
    layer2_max_x_context_fetches: int | None = None,
    layer2_scoring_concurrency: int | None = None,
    layer2_brief_concurrency: int | None = None,
    layer2_max_parallel_tool_calls: int | None = None,
    layer2_github_tool_concurrency: int | None = None,
    layer2_homepage_tool_concurrency: int | None = None,
    layer2_web_search_tool_concurrency: int | None = None,
    layer2_github_tool_rate_limit_per_second: float | None = None,
    layer2_homepage_tool_rate_limit_per_second: float | None = None,
    layer2_web_search_tool_rate_limit_per_second: float | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    resume: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    active_root = Path(root)
    active_now = now or utc_now()
    active_run_id = run_id or default_run_id(active_now)
    config = read_pipeline_config(active_root)
    decision_cfg = _decision_config(config)
    layer2_cfg = _layer2_config(config)
    active_decision_io_concurrency = _configured_optional_int(
        decision_io_concurrency, decision_cfg, "io_concurrency"
    )
    active_decision_io_rate_limit_per_second = _configured_optional_float(
        decision_io_rate_limit_per_second,
        decision_cfg,
        "io_rate_limit_per_second",
    )
    active_run_layer2 = _configured_bool(run_layer2, layer2_cfg, "enabled", False)
    active_layer2_scout_limit = _configured_int(
        layer2_scout_limit, layer2_cfg, "max_edge_watch_scout", 50
    )
    active_layer2_scoring_limit = _configured_int(
        layer2_scoring_limit, layer2_cfg, "max_scored_candidates", 150
    )
    active_layer2_deepdive_limit = _configured_int(
        layer2_deepdive_limit, layer2_cfg, "max_deepdives_per_run", 10
    )
    active_layer2_deepdive_min_l2_score = _configured_optional_float(
        layer2_deepdive_min_l2_score, layer2_cfg, "deepdive_min_l2_score"
    )
    active_layer2_scout_model = _configured_optional_str(
        layer2_scout_model, layer2_cfg, "edge_scout_model"
    )
    active_layer2_scoring_model = _configured_optional_str(
        layer2_scoring_model, layer2_cfg, "scoring_model"
    )
    active_layer2_deepdive_model = _configured_optional_str(
        layer2_deepdive_model, layer2_cfg, "deepdive_model"
    )
    active_layer2_enable_kimi_web_search = _configured_bool(
        layer2_enable_kimi_web_search,
        layer2_cfg,
        "enable_kimi_web_search",
        False,
    )
    active_layer2_max_tool_calls = _configured_optional_int(
        layer2_max_tool_calls, layer2_cfg, "max_tool_calls_per_candidate"
    )
    active_layer2_max_web_search_calls = _configured_optional_int(
        layer2_max_web_search_calls,
        layer2_cfg,
        "max_web_search_calls_per_candidate",
    )
    active_layer2_max_repo_files = _configured_optional_int(
        layer2_max_repo_files, layer2_cfg, "max_repo_files_per_candidate"
    )
    active_layer2_max_pages = _configured_optional_int(
        layer2_max_pages, layer2_cfg, "max_pages_per_candidate"
    )
    active_layer2_max_hn_thread_fetches = _configured_optional_int(
        layer2_max_hn_thread_fetches,
        layer2_cfg,
        "max_hn_thread_fetches_per_candidate",
    )
    active_layer2_max_x_context_fetches = _configured_optional_int(
        layer2_max_x_context_fetches,
        layer2_cfg,
        "max_x_context_fetches_per_candidate",
    )
    active_layer2_scoring_concurrency = _configured_optional_int(
        layer2_scoring_concurrency, layer2_cfg, "scoring_concurrency"
    )
    active_layer2_brief_concurrency = _configured_optional_int(
        layer2_brief_concurrency, layer2_cfg, "brief_concurrency"
    )
    active_layer2_max_parallel_tool_calls = _configured_optional_int(
        layer2_max_parallel_tool_calls,
        layer2_cfg,
        "max_parallel_tool_calls_per_turn",
    )
    active_layer2_github_tool_concurrency = _configured_optional_int(
        layer2_github_tool_concurrency, layer2_cfg, "github_tool_concurrency"
    )
    active_layer2_homepage_tool_concurrency = _configured_optional_int(
        layer2_homepage_tool_concurrency, layer2_cfg, "homepage_tool_concurrency"
    )
    active_layer2_web_search_tool_concurrency = _configured_optional_int(
        layer2_web_search_tool_concurrency, layer2_cfg, "web_search_tool_concurrency"
    )
    active_layer2_github_tool_rate_limit_per_second = _configured_optional_float(
        layer2_github_tool_rate_limit_per_second,
        layer2_cfg,
        "github_tool_rate_limit_per_second",
    )
    active_layer2_homepage_tool_rate_limit_per_second = _configured_optional_float(
        layer2_homepage_tool_rate_limit_per_second,
        layer2_cfg,
        "homepage_tool_rate_limit_per_second",
    )
    active_layer2_web_search_tool_rate_limit_per_second = _configured_optional_float(
        layer2_web_search_tool_rate_limit_per_second,
        layer2_cfg,
        "web_search_tool_rate_limit_per_second",
    )
    lock_path = active_root / "data" / "run_daily.lock"
    log_path = active_root / "data" / "logs" / "run_daily" / f"{active_run_id}.jsonl"
    sources_log_path = log_path.parent / f"{active_run_id}.sources.jsonl"
    decision_log_path = log_path.parent / f"{active_run_id}.decision.jsonl"
    logger = JsonlRunLogger(log_path, run_id=active_run_id)
    if lock_path.exists():
        if resume and stale_lock_matches_run(lock_path, active_run_id):
            lock_path.unlink()
        else:
            raise RuntimeError(f"daily pipeline lock exists: {lock_path}")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        json.dumps(
            {"run_id": active_run_id, "started_at": active_now, "pid": os.getpid()}
        )
        + "\n"
    )
    stages: list[dict[str, Any]] = []
    completed_stages = completed_stages_from_log(log_path) if resume else set()
    resume_skip_sources = resume and "sources" in completed_stages
    resume_skip_decision = resume and decision_run_is_complete(active_root, active_run_id)
    resume_skip_layer2 = (
        resume and active_run_layer2 and layer2_run_is_complete(active_root, active_run_id)
    )
    logger.event(
        "run_started",
        now=active_now,
        skip_sources=skip_sources,
        skip_decision=skip_decision,
        run_layer2=active_run_layer2,
        only_sources=only_sources or [],
        resume=resume,
        resume_completed_stages=sorted(completed_stages),
    )
    try:
        if skip_sources or resume_skip_sources:
            if resume_skip_sources and not skip_sources:
                logger.event("stage_skipped", stage="sources", reason="resume_completed_stage")
        else:
            stages.append(
                _run_logged_stage(
                    logger=logger,
                    name="sources",
                    runner=runner,
                    cmd=source_command(
                        root=active_root,
                        python=python,
                        only_sources=only_sources,
                        log_path=sources_log_path,
                    ),
                    cwd=active_root,
                    timeout=timeout,
                )
            )
            if stages[-1]["returncode"] != 0:
                summary = {
                    "ok": False,
                    "returncode": stages[-1]["returncode"],
                    "run_id": active_run_id,
                    "stages": stages,
                    "log_path": str(log_path),
                }
                logger.event("run_failed", returncode=summary["returncode"], failed_stage="sources")
                return summary

        if skip_decision or resume_skip_decision:
            if resume_skip_decision and not skip_decision:
                logger.event("stage_skipped", stage="decision", reason="resume_completed_run")
        else:
            stages.append(
                _run_logged_stage(
                    logger=logger,
                    name="decision",
                    runner=runner,
                    cmd=decision_command(
                        python=python,
                        run_id=active_run_id,
                        now=active_now,
                        backfill=backfill,
                        classify_hn_limit=classify_hn_limit,
                        classify_x_limit=classify_x_limit,
                        llm_concurrency=llm_concurrency,
                        io_concurrency=active_decision_io_concurrency,
                        io_rate_limit_per_second=active_decision_io_rate_limit_per_second,
                        resolver_search_limit=resolver_search_limit,
                        resolver_research_limit=resolver_research_limit,
                        resolver_research_rounds=resolver_research_rounds,
                        npm_backfill_limit=npm_backfill_limit if backfill else 0,
                        enrich_readme_limit=enrich_readme_limit,
                        log_path=decision_log_path,
                    ),
                    cwd=active_root,
                    timeout=timeout,
                )
            )
            if stages[-1]["returncode"] != 0:
                summary = {
                    "ok": False,
                    "returncode": stages[-1]["returncode"],
                    "run_id": active_run_id,
                    "stages": stages,
                    "log_path": str(log_path),
                }
                logger.event("run_failed", returncode=summary["returncode"], failed_stage="decision")
                return summary

        if active_run_layer2 and resume_skip_layer2:
            logger.event("stage_skipped", stage="layer2", reason="resume_completed_run")
        elif active_run_layer2:
            stages.append(
                _run_logged_stage(
                    logger=logger,
                    name="layer2",
                    runner=runner,
                    cmd=layer2_command(
                        python=python,
                        decision_run_id=active_run_id,
                        now=active_now,
                        scout_limit=active_layer2_scout_limit,
                        scoring_limit=active_layer2_scoring_limit,
                        deepdive_limit=active_layer2_deepdive_limit,
                        deepdive_min_l2_score=active_layer2_deepdive_min_l2_score,
                        scout_model=active_layer2_scout_model,
                        scoring_model=active_layer2_scoring_model,
                        deepdive_model=active_layer2_deepdive_model,
                        enable_kimi_web_search=active_layer2_enable_kimi_web_search,
                        max_tool_calls=active_layer2_max_tool_calls,
                        max_web_search_calls=active_layer2_max_web_search_calls,
                        max_repo_files=active_layer2_max_repo_files,
                        max_pages=active_layer2_max_pages,
                        max_hn_thread_fetches=active_layer2_max_hn_thread_fetches,
                        max_x_context_fetches=active_layer2_max_x_context_fetches,
                        scoring_concurrency=active_layer2_scoring_concurrency,
                        brief_concurrency=active_layer2_brief_concurrency,
                        max_parallel_tool_calls=active_layer2_max_parallel_tool_calls,
                        github_tool_concurrency=active_layer2_github_tool_concurrency,
                        homepage_tool_concurrency=active_layer2_homepage_tool_concurrency,
                        web_search_tool_concurrency=active_layer2_web_search_tool_concurrency,
                        github_tool_rate_limit_per_second=active_layer2_github_tool_rate_limit_per_second,
                        homepage_tool_rate_limit_per_second=active_layer2_homepage_tool_rate_limit_per_second,
                        web_search_tool_rate_limit_per_second=active_layer2_web_search_tool_rate_limit_per_second,
                        finalize_stale_running_before=active_now,
                    ),
                    cwd=active_root,
                    timeout=timeout,
                )
            )
            if stages[-1]["returncode"] != 0:
                summary = {
                    "ok": False,
                    "returncode": stages[-1]["returncode"],
                    "run_id": active_run_id,
                    "stages": stages,
                    "log_path": str(log_path),
                }
                logger.event("run_failed", returncode=summary["returncode"], failed_stage="layer2")
                return summary

        summary = {
            "ok": True,
            "returncode": 0,
            "run_id": active_run_id,
            "stages": stages,
            "log_path": str(log_path),
        }
        logger.event("run_completed", ok=True, returncode=0)
        return summary
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def parse_csv(values: list[str]) -> list[str]:
    items: list[str] = []
    for value in values:
        for item in str(value or "").split(","):
            item = item.strip()
            if item:
                items.append(item)
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the daily Hero Radar pipeline")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--now", default=None)
    parser.add_argument("--only-source", action="append", default=[])
    parser.add_argument("--skip-sources", action="store_true")
    parser.add_argument("--skip-decision", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--no-backfill", action="store_true")
    parser.add_argument("--classify-hn-limit", type=int, default=200)
    parser.add_argument("--classify-x-limit", type=int, default=300)
    parser.add_argument("--llm-concurrency", type=int, default=4)
    parser.add_argument("--decision-io-concurrency", type=int, default=None)
    parser.add_argument("--decision-io-rate-limit-per-second", type=float, default=None)
    parser.add_argument("--resolver-search-limit", type=int, default=100)
    parser.add_argument("--resolver-research-limit", type=int, default=50)
    parser.add_argument("--resolver-research-rounds", type=int, default=3)
    parser.add_argument("--npm-backfill-limit", type=int, default=40)
    parser.add_argument("--enrich-readme-limit", type=int, default=100)
    parser.add_argument("--run-layer2", dest="run_layer2", action="store_true", default=None)
    parser.add_argument("--no-layer2", dest="run_layer2", action="store_false")
    parser.add_argument("--layer2-scout-limit", type=int, default=None)
    parser.add_argument("--layer2-scoring-limit", type=int, default=None)
    parser.add_argument("--layer2-deepdive-limit", type=int, default=None)
    parser.add_argument("--layer2-deepdive-min-l2-score", type=float, default=None)
    parser.add_argument("--layer2-scout-model", default=None)
    parser.add_argument("--layer2-scoring-model", default=None)
    parser.add_argument("--layer2-deepdive-model", default=None)
    parser.add_argument(
        "--layer2-enable-kimi-web-search",
        dest="layer2_enable_kimi_web_search",
        action="store_true",
        default=None,
    )
    parser.add_argument(
        "--no-layer2-kimi-web-search",
        dest="layer2_enable_kimi_web_search",
        action="store_false",
    )
    parser.add_argument("--layer2-max-tool-calls", type=int, default=None)
    parser.add_argument("--layer2-max-web-search-calls", type=int, default=None)
    parser.add_argument("--layer2-max-repo-files", type=int, default=None)
    parser.add_argument("--layer2-max-pages", type=int, default=None)
    parser.add_argument("--layer2-max-hn-thread-fetches", type=int, default=None)
    parser.add_argument("--layer2-max-x-context-fetches", type=int, default=None)
    parser.add_argument("--layer2-scoring-concurrency", type=int, default=None)
    parser.add_argument("--layer2-brief-concurrency", type=int, default=None)
    parser.add_argument("--layer2-max-parallel-tool-calls", type=int, default=None)
    parser.add_argument("--layer2-github-tool-concurrency", type=int, default=None)
    parser.add_argument("--layer2-homepage-tool-concurrency", type=int, default=None)
    parser.add_argument("--layer2-web-search-tool-concurrency", type=int, default=None)
    parser.add_argument("--layer2-github-tool-rate-limit-per-second", type=float, default=None)
    parser.add_argument("--layer2-homepage-tool-rate-limit-per-second", type=float, default=None)
    parser.add_argument("--layer2-web-search-tool-rate-limit-per-second", type=float, default=None)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args()

    summary = run_daily(
        run_id=args.run_id,
        now=args.now,
        only_sources=parse_csv(args.only_source),
        skip_sources=args.skip_sources,
        skip_decision=args.skip_decision,
        backfill=not args.no_backfill,
        classify_hn_limit=args.classify_hn_limit,
        classify_x_limit=args.classify_x_limit,
        llm_concurrency=args.llm_concurrency,
        decision_io_concurrency=args.decision_io_concurrency,
        decision_io_rate_limit_per_second=args.decision_io_rate_limit_per_second,
        resolver_search_limit=args.resolver_search_limit,
        resolver_research_limit=args.resolver_research_limit,
        resolver_research_rounds=args.resolver_research_rounds,
        npm_backfill_limit=args.npm_backfill_limit,
        enrich_readme_limit=args.enrich_readme_limit,
        run_layer2=args.run_layer2,
        layer2_scout_limit=args.layer2_scout_limit,
        layer2_scoring_limit=args.layer2_scoring_limit,
        layer2_deepdive_limit=args.layer2_deepdive_limit,
        layer2_deepdive_min_l2_score=args.layer2_deepdive_min_l2_score,
        layer2_scout_model=args.layer2_scout_model,
        layer2_scoring_model=args.layer2_scoring_model,
        layer2_deepdive_model=args.layer2_deepdive_model,
        layer2_enable_kimi_web_search=args.layer2_enable_kimi_web_search,
        layer2_max_tool_calls=args.layer2_max_tool_calls,
        layer2_max_web_search_calls=args.layer2_max_web_search_calls,
        layer2_max_repo_files=args.layer2_max_repo_files,
        layer2_max_pages=args.layer2_max_pages,
        layer2_max_hn_thread_fetches=args.layer2_max_hn_thread_fetches,
        layer2_max_x_context_fetches=args.layer2_max_x_context_fetches,
        layer2_scoring_concurrency=args.layer2_scoring_concurrency,
        layer2_brief_concurrency=args.layer2_brief_concurrency,
        layer2_max_parallel_tool_calls=args.layer2_max_parallel_tool_calls,
        layer2_github_tool_concurrency=args.layer2_github_tool_concurrency,
        layer2_homepage_tool_concurrency=args.layer2_homepage_tool_concurrency,
        layer2_web_search_tool_concurrency=args.layer2_web_search_tool_concurrency,
        layer2_github_tool_rate_limit_per_second=args.layer2_github_tool_rate_limit_per_second,
        layer2_homepage_tool_rate_limit_per_second=args.layer2_homepage_tool_rate_limit_per_second,
        layer2_web_search_tool_rate_limit_per_second=args.layer2_web_search_tool_rate_limit_per_second,
        timeout=args.timeout,
        resume=args.resume,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(summary["returncode"])


if __name__ == "__main__":
    raise SystemExit(main())
