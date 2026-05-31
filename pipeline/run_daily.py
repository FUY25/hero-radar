#!/usr/bin/env python3
"""Run the full daily Hero Radar pipeline.

This orchestrates the source collection step and the pre-Layer2 decision step.
Cron should call this file, not the individual stage scripts.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable or "python3"
DEFAULT_TIMEOUT_SECONDS = 3600


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_run_id(now: str | None = None) -> str:
    value = now or utc_now()
    compact = value.replace("-", "").replace(":", "").replace("+00:00", "Z")
    return f"decision_daily_{compact.rstrip('Z')}"


def source_command(*, root: Path, python: str, only_sources: list[str] | None = None) -> list[str]:
    cmd = [python, str(root / "pipeline" / "run_pipeline.py")]
    if only_sources:
        cmd.extend(["--only", ",".join(only_sources)])
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
    resolver_search_limit: int,
    resolver_research_limit: int,
    resolver_research_rounds: int,
    enrich_readme_limit: int,
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
    if resolver_search_limit > 0:
        cmd.extend(["--resolver-search-limit", str(resolver_search_limit)])
    if resolver_research_limit > 0:
        cmd.extend(["--resolver-research-limit", str(resolver_research_limit)])
        cmd.extend(["--resolver-research-rounds", str(max(0, resolver_research_rounds))])
    if enrich_readme_limit > 0:
        cmd.extend(["--enrich-readme-limit", str(enrich_readme_limit)])
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
    return cmd


def _run_stage(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    cmd: list[str],
    cwd: Path,
    timeout: int,
) -> dict[str, Any]:
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
    }


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
    resolver_search_limit: int = 100,
    resolver_research_limit: int = 50,
    resolver_research_rounds: int = 3,
    enrich_readme_limit: int = 100,
    run_layer2: bool = False,
    layer2_scout_limit: int = 50,
    layer2_scoring_limit: int = 150,
    layer2_deepdive_limit: int = 10,
    layer2_deepdive_min_l2_score: float | None = None,
    layer2_scout_model: str | None = None,
    layer2_scoring_model: str | None = None,
    layer2_deepdive_model: str | None = None,
    layer2_enable_kimi_web_search: bool = False,
    layer2_max_tool_calls: int | None = None,
    layer2_max_web_search_calls: int | None = None,
    layer2_max_repo_files: int | None = None,
    layer2_max_pages: int | None = None,
    layer2_max_hn_thread_fetches: int | None = None,
    layer2_max_x_context_fetches: int | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    active_root = Path(root)
    active_now = now or utc_now()
    active_run_id = run_id or default_run_id(active_now)
    lock_path = active_root / "data" / "run_daily.lock"
    if lock_path.exists():
        raise RuntimeError(f"daily pipeline lock exists: {lock_path}")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({"run_id": active_run_id, "started_at": active_now}) + "\n")
    stages: list[dict[str, Any]] = []
    try:
        if not skip_sources:
            stages.append(
                {
                    "name": "sources",
                    **_run_stage(
                        runner=runner,
                        cmd=source_command(root=active_root, python=python, only_sources=only_sources),
                        cwd=active_root,
                        timeout=timeout,
                    ),
                }
            )
            if stages[-1]["returncode"] != 0:
                return {"ok": False, "returncode": stages[-1]["returncode"], "run_id": active_run_id, "stages": stages}

        if not skip_decision:
            stages.append(
                {
                    "name": "decision",
                    **_run_stage(
                        runner=runner,
                        cmd=decision_command(
                            python=python,
                            run_id=active_run_id,
                            now=active_now,
                            backfill=backfill,
                            classify_hn_limit=classify_hn_limit,
                            classify_x_limit=classify_x_limit,
                            llm_concurrency=llm_concurrency,
                            resolver_search_limit=resolver_search_limit,
                            resolver_research_limit=resolver_research_limit,
                            resolver_research_rounds=resolver_research_rounds,
                            enrich_readme_limit=enrich_readme_limit,
                        ),
                        cwd=active_root,
                        timeout=timeout,
                    ),
                }
            )
            if stages[-1]["returncode"] != 0:
                return {"ok": False, "returncode": stages[-1]["returncode"], "run_id": active_run_id, "stages": stages}

        if run_layer2:
            stages.append(
                {
                    "name": "layer2",
                    **_run_stage(
                        runner=runner,
                        cmd=layer2_command(
                            python=python,
                            decision_run_id=active_run_id,
                            now=active_now,
                            scout_limit=layer2_scout_limit,
                            scoring_limit=layer2_scoring_limit,
                            deepdive_limit=layer2_deepdive_limit,
                            deepdive_min_l2_score=layer2_deepdive_min_l2_score,
                            scout_model=layer2_scout_model,
                            scoring_model=layer2_scoring_model,
                            deepdive_model=layer2_deepdive_model,
                            enable_kimi_web_search=layer2_enable_kimi_web_search,
                            max_tool_calls=layer2_max_tool_calls,
                            max_web_search_calls=layer2_max_web_search_calls,
                            max_repo_files=layer2_max_repo_files,
                            max_pages=layer2_max_pages,
                            max_hn_thread_fetches=layer2_max_hn_thread_fetches,
                            max_x_context_fetches=layer2_max_x_context_fetches,
                        ),
                        cwd=active_root,
                        timeout=timeout,
                    ),
                }
            )
            if stages[-1]["returncode"] != 0:
                return {"ok": False, "returncode": stages[-1]["returncode"], "run_id": active_run_id, "stages": stages}

        return {"ok": True, "returncode": 0, "run_id": active_run_id, "stages": stages}
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
    parser.add_argument("--no-backfill", action="store_true")
    parser.add_argument("--classify-hn-limit", type=int, default=200)
    parser.add_argument("--classify-x-limit", type=int, default=300)
    parser.add_argument("--llm-concurrency", type=int, default=4)
    parser.add_argument("--resolver-search-limit", type=int, default=100)
    parser.add_argument("--resolver-research-limit", type=int, default=50)
    parser.add_argument("--resolver-research-rounds", type=int, default=3)
    parser.add_argument("--enrich-readme-limit", type=int, default=100)
    parser.add_argument("--run-layer2", action="store_true")
    parser.add_argument("--layer2-scout-limit", type=int, default=50)
    parser.add_argument("--layer2-scoring-limit", type=int, default=150)
    parser.add_argument("--layer2-deepdive-limit", type=int, default=10)
    parser.add_argument("--layer2-deepdive-min-l2-score", type=float, default=None)
    parser.add_argument("--layer2-scout-model", default=None)
    parser.add_argument("--layer2-scoring-model", default=None)
    parser.add_argument("--layer2-deepdive-model", default=None)
    parser.add_argument("--layer2-enable-kimi-web-search", action="store_true")
    parser.add_argument("--layer2-max-tool-calls", type=int, default=None)
    parser.add_argument("--layer2-max-web-search-calls", type=int, default=None)
    parser.add_argument("--layer2-max-repo-files", type=int, default=None)
    parser.add_argument("--layer2-max-pages", type=int, default=None)
    parser.add_argument("--layer2-max-hn-thread-fetches", type=int, default=None)
    parser.add_argument("--layer2-max-x-context-fetches", type=int, default=None)
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
        resolver_search_limit=args.resolver_search_limit,
        resolver_research_limit=args.resolver_research_limit,
        resolver_research_rounds=args.resolver_research_rounds,
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
        timeout=args.timeout,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(summary["returncode"])


if __name__ == "__main__":
    raise SystemExit(main())
