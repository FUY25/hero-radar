from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable

from pipeline.decision.kimi_provider import (
    DEFAULT_KIMI_DEEPDIVE_MODEL,
    DEFAULT_KIMI_SCORING_MODEL,
    KimiProvider,
    KimiWebSearchClient,
)
from pipeline.decision.layer2_context import assemble_group_context
from pipeline.decision.layer2_context_builder import ContextBudget, ScoringContextBuilder
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
    CachedTelemetryLLMProvider,
    TelemetryLLMProvider,
    final_run_status,
    record_stage_event,
    stage_summary,
)
from pipeline.decision.layer2_investigator_tools import (
    GitHubFileClient,
    InvestigatorToolLimits,
    PageFetchClient,
    ScoringInvestigatorTools,
)
from pipeline.decision.layer2_scheduler import schedule_layer2_work
from pipeline.decision.layer2_scoring_investigator import (
    DEFAULT_BRIEF_PROMPT_VERSION,
    DEFAULT_INVESTIGATOR_PROMPT_VERSION,
    InvestigatorLimits,
    ROUTE_CANDIDATE_ERROR,
    ROUTE_SCORE_ONLY,
    ROUTE_SCORE_PLUS_DEEPDIVE,
    ROUTE_SUPPRESS_OR_LOW,
    build_deepdive_brief,
    classify_scored_route,
    major_company_label_for_row,
    persist_deepdive_brief,
    score_with_investigator,
    select_deepdive_brief_candidates,
)
from pipeline.decision.layer2_scout import scout_edge_watch_groups
from pipeline.decision.readme_enrichment import GitHubReadmeClient
from pipeline.decision.rate_limit import CallRateLimiter
from pipeline.decision.schema import init_decision_db, to_json, utc_now


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "hero_radar.sqlite"
DEFAULT_KNOWN_PARADIGM_KEYS = frozenset({"github:nousresearch/hermes-agent"})
LAYER2_CONFIG_OWNERS = frozenset(
    {
        "routing",
        "scoring_agent",
        "brief_writer",
        "tool_runtime",
        "edge_scout",
        "legacy_deepdive",
    }
)


def validate_layer2_config(config: dict[str, Any] | None) -> dict[str, Any]:
    cfg = config or {}
    if not isinstance(cfg, dict):
        raise ValueError("Layer 2 config must be an object")
    invalid = sorted(set(cfg) - LAYER2_CONFIG_OWNERS - {"enabled"})
    if invalid:
        raise ValueError(
            "Layer 2 uses canonical nested component config; "
            f"move flat keys under their owner: {', '.join(invalid)}"
        )
    for owner in LAYER2_CONFIG_OWNERS:
        value = cfg.get(owner, {})
        if not isinstance(value, dict):
            raise ValueError(f"layer2.{owner} must be an object")
    return cfg


def _component_config(cfg: dict[str, Any], owner: str) -> dict[str, Any]:
    value = cfg.get(owner, {})
    return value if isinstance(value, dict) else {}


def _kimi_provider_factory(
    component_cfg: dict[str, Any],
    *,
    component: str,
    default_model: str,
    default_timeout: int = 90,
    default_max_output_tokens: int | None = None,
) -> Callable[[], KimiProvider]:
    provider_name = str(component_cfg.get("provider") or "kimi").strip().lower()
    if provider_name != "kimi":
        raise ValueError(
            f"unsupported Layer 2 {component} provider {provider_name!r}; "
            "inject a provider factory for tests or configure 'kimi'"
        )
    model = str(component_cfg.get("model") or default_model)
    configured_timeout = component_cfg.get("timeout_seconds")
    timeout = int(
        default_timeout if configured_timeout is None else configured_timeout
    )
    max_output_tokens = component_cfg.get(
        "max_output_tokens", default_max_output_tokens
    )

    return lambda: KimiProvider(
        model=model,
        timeout=timeout,
        max_output_tokens=(
            None if max_output_tokens is None else int(max_output_tokens)
        ),
    )


def default_feed_run_id(now: str | None = None) -> str:
    value = now or utc_now()
    compact = value.replace("-", "").replace(":", "").rstrip("Z")
    return f"l2_{compact}"


def route_react_smoke_run_id(now: str | None = None) -> str:
    return f"l2_route_react_smoke_{_compact_utc_timestamp(now)}"


def bounded_layer2_run_id(candidate_count: int = 30, now: str | None = None) -> str:
    return f"l2_bounded_{int(candidate_count)}_{_compact_utc_timestamp(now)}"


def backup_sqlite_db(*, db_path: Path = DB_PATH, now: str | None = None) -> Path:
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {db_path}")
    backup_path = db_path.with_name(
        f"{db_path.name}.{_compact_utc_timestamp(now)}.bak"
    )
    shutil.copy2(db_path, backup_path)
    return backup_path


def _compact_utc_timestamp(now: str | None = None) -> str:
    value = now or utc_now()
    compact = value.replace("-", "").replace(":", "")
    if compact.endswith("+0000"):
        compact = f"{compact[:-5]}Z"
    if not compact.endswith("Z"):
        compact = f"{compact.rstrip('Z')}Z"
    return compact


def latest_decision_run(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        """
        select run_id
        from decision_runs
        where status in ('ok', 'ok_with_errors')
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
    scoring_provider_factory: Callable[[], Any] | None = None,
    brief_provider_factory: Callable[[], Any] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = validate_layer2_config(config)
    routing_cfg = _component_config(cfg, "routing")
    scoring_cfg = _component_config(cfg, "scoring_agent")
    brief_cfg = _component_config(cfg, "brief_writer")
    tool_cfg = _component_config(cfg, "tool_runtime")
    edge_cfg = _component_config(cfg, "edge_scout")
    legacy_cfg = _component_config(cfg, "legacy_deepdive")
    active_now = now or utc_now()
    active_feed_run_id = feed_run_id or default_feed_run_id(active_now)
    conn = sqlite3.connect(db_path)
    init_decision_db(conn)
    try:
        active_decision_run_id = decision_run_id or latest_decision_run(conn)
        stale_before = routing_cfg.get("finalize_stale_running_before")
        if stale_before:
            finalize_stale_running_runs(
                conn,
                before_started_at=str(stale_before),
                completed_at=active_now,
            )
            conn.commit()
        edge_enabled = bool(edge_cfg.get("enabled", False))
        scout_provider = (
            (
                provider
                or _kimi_provider_factory(
                    edge_cfg,
                    component="edge_scout",
                    default_model=DEFAULT_KIMI_SCORING_MODEL,
                )()
            )
            if edge_enabled
            else None
        )
        active_scoring_provider_factory = scoring_provider_factory or _kimi_provider_factory(
            scoring_cfg,
            component="scoring_agent",
            default_model=DEFAULT_KIMI_SCORING_MODEL,
            default_max_output_tokens=3000,
        )
        scoring_provider = provider or (
            active_scoring_provider_factory()
        )
        legacy_enabled = bool(legacy_cfg.get("enabled", False))
        active_deepdive_provider = (
            (
                deepdive_provider
                or provider
                or _kimi_provider_factory(
                    legacy_cfg,
                    component="legacy_deepdive",
                    default_model=DEFAULT_KIMI_DEEPDIVE_MODEL,
                )()
            )
            if legacy_enabled
            else None
        )
        if brief_provider_factory is not None:
            active_brief_provider_factory = brief_provider_factory
        elif provider is not None:
            # Explicit all-component injection is a test seam. Production always
            # constructs Brief Writer from brief_writer configuration below.
            active_brief_provider_factory = lambda: provider
        else:
            active_brief_provider_factory = _kimi_provider_factory(
                brief_cfg,
                component="brief_writer",
                default_model=DEFAULT_KIMI_SCORING_MODEL,
                default_timeout=90,
                default_max_output_tokens=1000,
            )
        brief_profile_provider = None
        if brief_provider_factory is not None and bool(brief_cfg.get("enabled", True)):
            brief_profile_provider = brief_provider_factory()
            brief_profile_model = getattr(brief_profile_provider, "model", "")
            brief_profile_provider_name = getattr(
                brief_profile_provider, "provider_name", ""
            )
        elif provider is not None:
            brief_profile_model = getattr(provider, "model", "")
            brief_profile_provider_name = getattr(provider, "provider_name", "")
        else:
            brief_profile_model = str(
                brief_cfg.get("model") or DEFAULT_KIMI_SCORING_MODEL
            )
            brief_profile_provider_name = str(brief_cfg.get("provider") or "kimi")
        tool_family_limiters = _tool_family_limiters_from_config(tool_cfg)
        model_profile = {
            "scout": getattr(
                scout_provider,
                "model",
                str(edge_cfg.get("model") or DEFAULT_KIMI_SCORING_MODEL),
            ),
            "scoring": getattr(scoring_provider, "model", ""),
            "brief": brief_profile_model,
            "deepdive": getattr(
                active_deepdive_provider,
                "model",
                str(legacy_cfg.get("model") or DEFAULT_KIMI_DEEPDIVE_MODEL),
            ),
            "scout_provider": getattr(
                scout_provider,
                "provider_name",
                str(edge_cfg.get("provider") or "kimi"),
            ),
            "scoring_provider": getattr(scoring_provider, "provider_name", ""),
            "brief_provider": brief_profile_provider_name,
            "deepdive_provider": getattr(
                active_deepdive_provider,
                "provider_name",
                str(legacy_cfg.get("provider") or "kimi"),
            ),
            "scoring_max_output_tokens": getattr(
                scoring_provider,
                "max_output_tokens",
                scoring_cfg.get("max_output_tokens"),
            ),
            "brief_max_output_tokens": getattr(
                brief_profile_provider,
                "max_output_tokens",
                brief_cfg.get("max_output_tokens"),
            ),
            "scoring_prompt_version": str(
                scoring_cfg.get("prompt_version")
                or DEFAULT_INVESTIGATOR_PROMPT_VERSION
            ),
            "scoring_output_schema_version": str(
                scoring_cfg.get("output_schema_version") or ""
            ),
            "scoring_context_policy_version": str(
                scoring_cfg.get("context_policy_version") or ""
            ),
            "brief_prompt_version": str(
                brief_cfg.get("prompt_version") or DEFAULT_BRIEF_PROMPT_VERSION
            ),
            "brief_output_schema_version": str(
                brief_cfg.get("output_schema_version") or ""
            ),
            "tool_registry_version": str(tool_cfg.get("registry_version") or ""),
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
                routing_cfg.get("max_edge_watch_scout", 50)
            ),
            max_scored_candidates=_configured_scoring_limit(routing_cfg),
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
        if edge_enabled:
            for group in schedule.scout_edge_watch:
                try:
                    active_scout_provider = TelemetryLLMProvider(
                        scout_provider,
                        conn=conn,
                        feed_run_id=active_feed_run_id,
                        group_id=group.group_id,
                        stage="scout",
                        timeout_seconds=_optional_int(edge_cfg.get("timeout_seconds")),
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
        candidate_errors = []
        scoring_candidates = [*schedule.score_now, *scouted]
        max_total_scoring = routing_cfg.get("max_total_scoring_candidates")
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
        conn.commit()
        scoring_concurrency = _active_scoring_concurrency(
            provider_injected=provider is not None,
            cfg=scoring_cfg,
        )
        if scoring_concurrency > 1 and scoring_candidates:
            worker_factory = active_scoring_provider_factory
            with ThreadPoolExecutor(max_workers=scoring_concurrency) as executor:
                futures = {
                    executor.submit(
                        _score_group_worker,
                        db_path,
                        active_decision_run_id,
                        active_feed_run_id,
                        group,
                        cfg,
                        worker_factory,
                        tool_family_limiters,
                    ): group
                    for group in scoring_candidates
                }
                for future in as_completed(futures):
                    group = futures[future]
                    result = future.result()
                    if result.get("result"):
                        scored.append(result["result"])
                    else:
                        candidate_errors.append(
                            {
                                "group": group,
                                "status": ROUTE_CANDIDATE_ERROR,
                                "error": result.get("error", ""),
                            }
                        )
        else:
            investigator_tools = _investigator_tools_for(
                None,
                decision_run_id=active_decision_run_id,
                scoring_provider=scoring_provider,
                scoring_cfg=scoring_cfg,
                tool_cfg=tool_cfg,
                connection_factory=lambda: sqlite3.connect(db_path, timeout=30),
                family_limiters=tool_family_limiters,
            )
            investigator_limits = _investigator_limits_from_config(scoring_cfg)
            for group in scoring_candidates:
                try:
                    active_scoring_provider = CachedTelemetryLLMProvider(
                        scoring_provider,
                        conn=conn,
                        feed_run_id=active_feed_run_id,
                        group_id=group.group_id,
                        stage="scoring",
                        timeout_seconds=_optional_int(
                            scoring_cfg.get("timeout_seconds")
                        ),
                    )
                    result = score_with_investigator(
                        conn,
                        feed_run_id=active_feed_run_id,
                        groups=[group],
                        provider=active_scoring_provider,
                        tools=investigator_tools.available_tools(),
                        tool_specs=investigator_tools.available_specs(),
                        limits=investigator_limits,
                        context_builder=ScoringContextBuilder(
                            context_policy_version=str(
                                scoring_cfg.get("context_policy_version")
                                or "layer2-scoring-context-v1"
                            )
                        ),
                        context_budget=_context_budget_from_config(scoring_cfg),
                        direct_final_enabled=bool(
                            scoring_cfg.get("enable_direct_final", False)
                        ),
                        prompt_version=str(
                            scoring_cfg.get("prompt_version")
                            or DEFAULT_INVESTIGATOR_PROMPT_VERSION
                        ),
                        output_schema_version=str(
                            scoring_cfg.get("output_schema_version")
                            or "layer2-scoring-output-v2"
                        ),
                        tool_registry_version=str(
                            tool_cfg.get("registry_version") or "layer2-tools-v1"
                        ),
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
                    candidate_errors.append(
                        {
                            "group": group,
                            "status": ROUTE_CANDIDATE_ERROR,
                            "error": str(exc)[:800],
                        }
                    )
                    record_stage_event(
                        conn,
                        feed_run_id=active_feed_run_id,
                        group_id=group.group_id,
                        stage="scoring",
                        status="scoring_error",
                        error=exc,
                    )
        if bool(brief_cfg.get("enabled", True)):
            brief_scored = [
                row for row in scored if not _is_known_paradigm_row(row, routing_cfg)
            ]
            selected_for_brief = select_deepdive_brief_candidates(
                brief_scored,
                min_score=float(routing_cfg.get("brief_min_score", 70)),
                target_count=int(routing_cfg.get("brief_target_count", 8)),
                max_count=int(routing_cfg.get("brief_max_count", 10)),
            )
        else:
            selected_for_brief = []
        selected_group_ids = {row["group"].group_id for row in selected_for_brief}
        route_counts = {
            ROUTE_SCORE_PLUS_DEEPDIVE: 0,
            ROUTE_SCORE_ONLY: 0,
            ROUTE_SUPPRESS_OR_LOW: 0,
            ROUTE_CANDIDATE_ERROR: len(candidate_errors),
        }
        for rank, row in enumerate(selected_for_brief, start=1):
            route_counts[ROUTE_SCORE_PLUS_DEEPDIVE] += 1
            _record_route_decision(
                conn,
                feed_run_id=active_feed_run_id,
                group_id=row["group"].group_id,
                route=ROUTE_SCORE_PLUS_DEEPDIVE,
                row=row,
                metadata={
                    "brief_min_score": routing_cfg.get("brief_min_score", 70),
                    "brief_rank": rank,
                    **_route_reason_metadata(row, routing_cfg),
                },
            )
            _insert_feed_item(
                conn,
                feed_run_id=active_feed_run_id,
                group_id=row["group"].group_id,
                section="today_focus",
                rank=rank,
                deepdive_status="selected",
            )
        score_only_rank = 1
        diagnostics_rank = 1
        suppressed_rows = []
        for row in sorted(scored, key=lambda item: -float(item["l2_score"])):
            route = classify_scored_route(
                row,
                selected_group_ids=selected_group_ids,
                min_score=float(routing_cfg.get("brief_min_score", 70)),
                score_only_min_score=float(
                    routing_cfg.get("score_only_min_score", 50)
                ),
            )
            if route == ROUTE_SCORE_PLUS_DEEPDIVE:
                continue
            route_counts[route] = route_counts.get(route, 0) + 1
            _record_route_decision(
                conn,
                feed_run_id=active_feed_run_id,
                group_id=row["group"].group_id,
                route=route,
                row=row,
                metadata={
                    "brief_min_score": routing_cfg.get("brief_min_score", 70),
                    "score_only_min_score": routing_cfg.get(
                        "score_only_min_score", 50
                    ),
                    **_route_reason_metadata(row, routing_cfg),
                },
            )
            if route == ROUTE_SCORE_ONLY:
                _insert_feed_item(
                    conn,
                    feed_run_id=active_feed_run_id,
                    group_id=row["group"].group_id,
                    section="scored",
                    rank=score_only_rank,
                    deepdive_status=route,
                )
                score_only_rank += 1
            elif route == ROUTE_SUPPRESS_OR_LOW:
                suppressed_rows.append(row)
        for row in suppressed_rows:
            _insert_feed_item(
                conn,
                feed_run_id=active_feed_run_id,
                group_id=row["group"].group_id,
                section="scored",
                rank=score_only_rank,
                deepdive_status=ROUTE_SUPPRESS_OR_LOW,
            )
            score_only_rank += 1
        for error_row in candidate_errors:
            _record_route_decision(
                conn,
                feed_run_id=active_feed_run_id,
                group_id=error_row["group"].group_id,
                route=ROUTE_CANDIDATE_ERROR,
                row=error_row,
                metadata={"error": error_row.get("error", "")},
            )
            _insert_feed_item(
                conn,
                feed_run_id=active_feed_run_id,
                group_id=error_row["group"].group_id,
                section="diagnostics",
                rank=diagnostics_rank,
                deepdive_status=ROUTE_CANDIDATE_ERROR,
            )
            diagnostics_rank += 1
        conn.commit()
        briefs = []
        if bool(brief_cfg.get("enabled", True)) and selected_for_brief:
            for row in selected_for_brief:
                group = row["group"]
                record_stage_event(
                    conn,
                    feed_run_id=active_feed_run_id,
                    group_id=group.group_id,
                    stage="brief",
                    status="brief_selected",
                )
            conn.commit()
            brief_concurrency = _active_brief_concurrency(
                provider_injected=provider is not None,
                factory_injected=brief_provider_factory is not None,
                cfg=brief_cfg,
            )
            brief_worker_slots: list[dict[str, Any] | None] = [None] * len(
                selected_for_brief
            )
            if brief_concurrency > 1:
                with ThreadPoolExecutor(max_workers=brief_concurrency) as executor:
                    futures = {
                        executor.submit(
                            _brief_group_worker,
                            db_path,
                            active_feed_run_id,
                            row,
                            brief_cfg,
                            active_brief_provider_factory,
                        ): index
                        for index, row in enumerate(selected_for_brief)
                    }
                    for future in as_completed(futures):
                        index = futures[future]
                        result = future.result()
                        brief_worker_slots[index] = result
            else:
                for index, row in enumerate(selected_for_brief):
                    result = _brief_group_worker(
                        db_path,
                        active_feed_run_id,
                        row,
                        brief_cfg,
                        active_brief_provider_factory,
                    )
                    brief_worker_slots[index] = result
            for row, worker_result in zip(selected_for_brief, brief_worker_slots):
                result = worker_result.get("result") if worker_result else None
                if result is None:
                    group = row["group"]
                    _update_feed_item_status(
                        conn,
                        feed_run_id=active_feed_run_id,
                        group_id=group.group_id,
                        section="today_focus",
                        deepdive_status="brief_error",
                    )
                    record_stage_event(
                        conn,
                        feed_run_id=active_feed_run_id,
                        group_id=group.group_id,
                        stage="brief",
                        status="brief_error",
                        error=(worker_result or {}).get(
                            "error", "brief worker returned no result"
                        ),
                    )
                    continue
                persist_deepdive_brief(
                    conn,
                    feed_run_id=active_feed_run_id,
                    result=result,
                )
                record_stage_event(
                    conn,
                    feed_run_id=active_feed_run_id,
                    group_id=result["group"].group_id,
                    stage="brief",
                    status="brief_ok",
                )
                briefs.append({"group": result["group"], "brief": result["brief"]})
            conn.commit()

        reports = []
        if legacy_enabled:
            web_search_timeout = _optional_int(
                tool_cfg.get("web_search_timeout_seconds")
            )
            if web_search_timeout is not None and hasattr(
                active_deepdive_provider, "timeout"
            ):
                setattr(active_deepdive_provider, "timeout", web_search_timeout)
            web_search_client = (
                KimiWebSearchClient(provider=active_deepdive_provider)
                if bool(tool_cfg.get("enable_kimi_web_search", False))
                else None
            )
            active_tools = default_deepdive_tools(
                conn,
                decision_run_id=active_decision_run_id,
                enable_kimi_web_search=bool(
                    tool_cfg.get("enable_kimi_web_search", False)
                ),
                web_search_client=web_search_client,
            )
            active_limits = DeepdiveLimits(
                max_tool_calls=int(
                    legacy_cfg.get(
                        "max_tool_calls_per_candidate",
                        (
                            int(
                                legacy_cfg.get(
                                    "max_web_search_calls_per_candidate", 3
                                )
                            )
                            + int(
                                legacy_cfg.get("max_repo_files_per_candidate", 8)
                            )
                            + int(legacy_cfg.get("max_pages_per_candidate", 6))
                        ),
                    )
                ),
                max_web_search_calls=int(
                    legacy_cfg.get("max_web_search_calls_per_candidate", 3)
                ),
                max_repo_file_calls=int(
                    legacy_cfg.get("max_repo_files_per_candidate", 8)
                ),
                max_page_fetch_calls=int(
                    legacy_cfg.get("max_pages_per_candidate", 6)
                ),
                max_hn_thread_calls=int(
                    legacy_cfg.get("max_hn_thread_fetches_per_candidate", 3)
                ),
                max_x_context_calls=int(
                    legacy_cfg.get("max_x_context_fetches_per_candidate", 5)
                ),
            )
            selected_for_deepdive = select_deepdives(
                scored,
                max_deepdives=int(routing_cfg.get("max_deepdives_per_run", 0)),
                min_l2_score=float(routing_cfg.get("deepdive_min_l2_score", 70)),
            )
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
                        timeout_seconds=_optional_int(
                            legacy_cfg.get("timeout_seconds")
                        ),
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
            "briefs": len(briefs),
            "deepdives": len(reports),
            "stage_counts": telemetry["stage_counts"],
            "error_counts": telemetry["error_counts"],
            "success_total": telemetry["success_total"],
            "error_total": telemetry["error_total"],
            "route_counts": route_counts,
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
            "briefs": len(briefs),
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


def _configured_scoring_limit(cfg: dict[str, Any]) -> int | None:
    value = cfg.get("max_scored_candidates", cfg.get("scoring_limit", 150))
    if value is None:
        return None
    return int(value)


def _insert_feed_item(
    conn: sqlite3.Connection,
    *,
    feed_run_id: str,
    group_id: str,
    section: str,
    rank: int,
    deepdive_status: str,
) -> None:
    conn.execute(
        """
        insert or replace into l2_feed_items(
          feed_run_id, group_id, section, rank, deepdive_status
        )
        values (?, ?, ?, ?, ?)
        """,
        (feed_run_id, group_id, section, rank, deepdive_status),
    )


def _record_route_decision(
    conn: sqlite3.Connection,
    *,
    feed_run_id: str,
    group_id: str,
    route: str,
    row: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    route_metadata: dict[str, Any] = {"route": route}
    if row:
        route_metadata.update(
            {
                "l2_score": row.get("l2_score"),
                "object_type": row.get("object_type"),
                "is_product_or_repo": row.get("is_product_or_repo"),
                "should_print": row.get("should_print"),
            }
        )
    if metadata:
        route_metadata.update(metadata)
    record_stage_event(
        conn,
        feed_run_id=feed_run_id,
        group_id=group_id,
        stage="route",
        status="route_decision",
        metadata=route_metadata,
    )


def _route_reason_metadata(row: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    if _is_known_paradigm_row(row, cfg):
        return {
            "known_paradigm": True,
            "route_reason": "known_paradigm_score_only",
        }
    major_company = major_company_label_for_row(row)
    if not major_company:
        return {}
    return {
        "major_company": major_company,
        "route_reason": "major_company_score_only",
    }


def _is_known_paradigm_row(row: dict[str, Any], cfg: dict[str, Any]) -> bool:
    known_keys = _known_paradigm_keys(cfg)
    if not known_keys:
        return False
    return any(value in known_keys for value in _row_identity_values(row))


def _known_paradigm_keys(cfg: dict[str, Any]) -> set[str]:
    configured = cfg.get("known_paradigm_keys")
    if configured is None or not isinstance(configured, list):
        return set(DEFAULT_KNOWN_PARADIGM_KEYS)
    return {
        _normalize_known_paradigm_value(value)
        for value in configured
        if str(value or "").strip()
    }


def _row_identity_values(row: dict[str, Any]) -> set[str]:
    group = row.get("group")
    raw_values = {
        getattr(group, "group_id", ""),
        getattr(group, "canonical_name", ""),
        getattr(group, "canonical_key", ""),
        getattr(group, "canonical_link", ""),
        row.get("group_id", ""),
        row.get("canonical_name", ""),
        row.get("canonical_key", ""),
        row.get("canonical_link", ""),
    }
    return {
        _normalize_known_paradigm_value(value)
        for value in raw_values
        if str(value or "").strip()
    }


def _normalize_known_paradigm_value(value: Any) -> str:
    text = str(value or "").strip().lower().rstrip("/")
    if text.startswith("https://github.com/") or text.startswith("http://github.com/"):
        parts = text.split("github.com/", 1)[1].split("/")
        if len(parts) >= 2 and parts[0] and parts[1]:
            return f"github:{parts[0]}/{parts[1]}"
    return text


def _update_feed_item_status(
    conn: sqlite3.Connection,
    *,
    feed_run_id: str,
    group_id: str,
    section: str,
    deepdive_status: str,
) -> None:
    conn.execute(
        """
        update l2_feed_items
        set deepdive_status = ?
        where feed_run_id = ? and group_id = ? and section = ?
        """,
        (deepdive_status, feed_run_id, group_id, section),
    )


def _active_scoring_concurrency(
    *, provider_injected: bool, cfg: dict[str, Any]
) -> int:
    if provider_injected:
        return 1
    return max(1, int(cfg.get("concurrency", 5)))


def _active_brief_concurrency(
    *,
    provider_injected: bool,
    factory_injected: bool,
    cfg: dict[str, Any],
) -> int:
    if provider_injected and not factory_injected:
        return 1
    return max(1, int(cfg.get("concurrency", 4)))


def _tool_family_limiters_from_config(cfg: dict[str, Any]) -> dict[str, Any]:
    families = cfg.get("families", {})
    github = families.get("github", {}) if isinstance(families, dict) else {}
    homepage = families.get("homepage", {}) if isinstance(families, dict) else {}
    web_search = families.get("web_search", {}) if isinstance(families, dict) else {}
    return {
        "github": CallRateLimiter(
            max_in_flight=max(1, int(github.get("max_in_flight", 5))),
            starts_per_second=max(
                0.0, float(github.get("starts_per_second", 2.0))
            ),
        ),
        "homepage": CallRateLimiter(
            max_in_flight=max(1, int(homepage.get("max_in_flight", 4))),
            starts_per_second=max(
                0.0, float(homepage.get("starts_per_second", 2.0))
            ),
        ),
        "web_search": CallRateLimiter(
            max_in_flight=max(1, int(web_search.get("max_in_flight", 2))),
            starts_per_second=max(
                0.0, float(web_search.get("starts_per_second", 1.0))
            ),
        ),
    }


def _investigator_limits_from_config(cfg: dict[str, Any]) -> InvestigatorLimits:
    tool_budget = cfg.get("tool_budget", {})
    if not isinstance(tool_budget, dict):
        tool_budget = {}
    return InvestigatorLimits(
        max_investigation_turns=int(cfg.get("max_investigation_turns", 3)),
        max_scoring_attempts=int(cfg.get("max_scoring_attempts", 3)),
        max_tool_calls_per_candidate=int(
            tool_budget.get("max_calls_per_candidate", 8)
        ),
        max_web_search_calls_per_candidate=int(
            tool_budget.get("max_web_search_calls_per_candidate", 1)
        ),
        max_github_file_calls_per_candidate=int(
            tool_budget.get(
                "max_github_file_calls_per_candidate",
                3,
            )
        ),
        max_homepage_fetches_per_candidate=int(
            tool_budget.get(
                "max_homepage_calls_per_candidate",
                1,
            )
        ),
        max_parallel_tool_calls_per_turn=int(
            tool_budget.get("max_parallel_calls_per_turn", 4)
        ),
    )


def _context_budget_from_config(cfg: dict[str, Any]) -> ContextBudget:
    raw = cfg.get("context_budget", {})
    context_cfg = raw if isinstance(raw, dict) else {}
    return ContextBudget(
        max_context_tokens=int(context_cfg.get("max_context_tokens", 32_000)),
        output_reserve=int(
            context_cfg.get(
                "output_reserve", cfg.get("max_output_tokens", 3_000)
            )
        ),
        safety_margin=int(context_cfg.get("safety_margin", 500)),
        identity_allocation=int(context_cfg.get("identity_allocation", 800)),
        evidence_summary_allocation=int(
            context_cfg.get("evidence_summary_allocation", 800)
        ),
        top_evidence_allocation=int(
            context_cfg.get("top_evidence_allocation", 2_400)
        ),
        previous_turn_allocation=int(
            context_cfg.get("previous_turn_allocation", 800)
        ),
        tool_observation_allocation=int(
            context_cfg.get("tool_observation_allocation", 2_400)
        ),
        recent_raw_tool_result_count=int(
            context_cfg.get("recent_raw_tool_result_count", 1)
        ),
    )


def _investigator_tools_for(
    conn: sqlite3.Connection | None,
    *,
    decision_run_id: str,
    scoring_provider: Any,
    scoring_cfg: dict[str, Any],
    tool_cfg: dict[str, Any],
    connection_factory: Callable[[], sqlite3.Connection] | None = None,
    family_limiters: dict[str, Any] | None = None,
) -> ScoringInvestigatorTools:
    scoring_web_search_timeout = _optional_int(
        tool_cfg.get("web_search_timeout_seconds")
    )
    if scoring_web_search_timeout is not None and hasattr(scoring_provider, "timeout"):
        setattr(scoring_provider, "timeout", scoring_web_search_timeout)
    scoring_web_search_client = (
        KimiWebSearchClient(provider=scoring_provider)
        if bool(tool_cfg.get("enable_kimi_web_search", False))
        else None
    )
    return ScoringInvestigatorTools(
        conn,
        connection_factory=connection_factory,
        decision_run_id=decision_run_id,
        readme_client=GitHubReadmeClient(),
        github_file_client=GitHubFileClient(),
        page_client=PageFetchClient(),
        web_search_client=scoring_web_search_client,
        limits=InvestigatorToolLimits(
            max_evidence_rows=int(tool_cfg.get("max_evidence_rows_per_fetch", 80)),
            max_github_file_chars=int(tool_cfg.get("max_github_file_chars", 6000)),
            max_homepage_chars=int(tool_cfg.get("max_homepage_chars", 6000)),
            max_web_results=int(tool_cfg.get("max_web_results", 5)),
        ),
        family_limiters=family_limiters,
    )


def _score_group_worker(
    db_path: Path,
    decision_run_id: str,
    feed_run_id: str,
    group: Any,
    cfg: dict[str, Any],
    scoring_provider_factory: Callable[[], Any],
    family_limiters: dict[str, Any],
) -> dict[str, Any]:
    conn = sqlite3.connect(db_path, timeout=30)
    init_decision_db(conn)
    scoring_cfg = _component_config(cfg, "scoring_agent")
    tool_cfg = _component_config(cfg, "tool_runtime")
    try:
        scoring_provider = scoring_provider_factory()
        active_scoring_provider = CachedTelemetryLLMProvider(
            scoring_provider,
            conn=conn,
            feed_run_id=feed_run_id,
            group_id=group.group_id,
            stage="scoring",
            timeout_seconds=_optional_int(scoring_cfg.get("timeout_seconds")),
        )
        investigator_tools = _investigator_tools_for(
            None,
            decision_run_id=decision_run_id,
            scoring_provider=scoring_provider,
            scoring_cfg=scoring_cfg,
            tool_cfg=tool_cfg,
            connection_factory=lambda: sqlite3.connect(db_path, timeout=30),
            family_limiters=family_limiters,
        )
        result = score_with_investigator(
            conn,
            feed_run_id=feed_run_id,
            groups=[group],
            provider=active_scoring_provider,
            tools=investigator_tools.available_tools(),
            tool_specs=investigator_tools.available_specs(),
            limits=_investigator_limits_from_config(scoring_cfg),
            context_builder=ScoringContextBuilder(
                context_policy_version=str(
                    scoring_cfg.get("context_policy_version")
                    or "layer2-scoring-context-v1"
                )
            ),
            context_budget=_context_budget_from_config(scoring_cfg),
            direct_final_enabled=bool(
                scoring_cfg.get("enable_direct_final", False)
            ),
            prompt_version=str(
                scoring_cfg.get("prompt_version")
                or DEFAULT_INVESTIGATOR_PROMPT_VERSION
            ),
            output_schema_version=str(
                scoring_cfg.get("output_schema_version")
                or "layer2-scoring-output-v2"
            ),
            tool_registry_version=str(
                tool_cfg.get("registry_version") or "layer2-tools-v1"
            ),
        )[0]
        record_stage_event(
            conn,
            feed_run_id=feed_run_id,
            group_id=group.group_id,
            stage="scoring",
            status="scoring_ok",
        )
        conn.commit()
        return {"result": result}
    except Exception as exc:
        record_stage_event(
            conn,
            feed_run_id=feed_run_id,
            group_id=group.group_id,
            stage="scoring",
            status="scoring_error",
            error=exc,
        )
        conn.commit()
        return {"result": None, "error": str(exc)[:800]}
    finally:
        conn.close()


def _brief_group_worker(
    db_path: Path,
    feed_run_id: str,
    row: dict[str, Any],
    cfg: dict[str, Any],
    brief_provider_factory: Callable[[], Any],
) -> dict[str, Any]:
    group = row["group"]
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        brief_provider = brief_provider_factory()
        active_brief_provider = CachedTelemetryLLMProvider(
            brief_provider,
            conn=conn,
            feed_run_id=feed_run_id,
            group_id=group.group_id,
            stage="brief",
            timeout_seconds=_optional_int(cfg.get("timeout_seconds")),
        )
        result = build_deepdive_brief(
            row=row,
            provider=active_brief_provider,
            prompt_version=str(
                cfg.get("prompt_version") or DEFAULT_BRIEF_PROMPT_VERSION
            ),
        )
        return {"result": result}
    except Exception as exc:
        return {"result": None, "error": str(exc)[:800]}
    finally:
        if conn is not None:
            conn.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Layer 2 Kimi Feed")
    parser.add_argument("--decision-run-id", default=None)
    parser.add_argument("--feed-run-id", default=None)
    parser.add_argument("--now", default=None)
    parser.add_argument("--edge-scout-limit", type=int, default=50)
    parser.add_argument("--scoring-limit", type=int, default=150)
    parser.add_argument("--deepdive-limit", type=int, default=0)
    parser.add_argument("--no-deepdive", action="store_true")
    parser.add_argument("--enable-legacy-deepdive", action="store_true")
    parser.add_argument("--deepdive-min-l2-score", type=float, default=70)
    parser.add_argument("--no-briefs", action="store_true")
    parser.add_argument("--brief-min-score", type=float, default=70)
    parser.add_argument("--score-only-min-score", type=float, default=50)
    parser.add_argument("--brief-target-count", type=int, default=8)
    parser.add_argument("--brief-max-count", type=int, default=10)
    parser.add_argument("--known-paradigm-key", action="append", default=None)
    parser.add_argument("--enable-edge-scout", action="store_true")
    parser.add_argument("--scout-provider", default="kimi")
    parser.add_argument("--scout-model", default=DEFAULT_KIMI_SCORING_MODEL)
    parser.add_argument("--scoring-provider", default="kimi")
    parser.add_argument("--scoring-model", default=DEFAULT_KIMI_SCORING_MODEL)
    parser.add_argument("--scoring-prompt-id", default="layer2_scoring_investigator")
    parser.add_argument(
        "--scoring-prompt-version", default=DEFAULT_INVESTIGATOR_PROMPT_VERSION
    )
    parser.add_argument(
        "--scoring-output-schema-version", default="layer2-scoring-output-v2"
    )
    parser.add_argument(
        "--scoring-context-policy-version", default="layer2-scoring-context-v1"
    )
    parser.add_argument("--brief-provider", default="kimi")
    parser.add_argument("--brief-model", default=DEFAULT_KIMI_SCORING_MODEL)
    parser.add_argument("--brief-prompt-id", default="layer2_brief_writer")
    parser.add_argument(
        "--brief-prompt-version", default=DEFAULT_BRIEF_PROMPT_VERSION
    )
    parser.add_argument(
        "--brief-output-schema-version", default="layer2-brief-output-v1"
    )
    parser.add_argument("--deepdive-provider", default="kimi")
    parser.add_argument("--deepdive-model", default=DEFAULT_KIMI_DEEPDIVE_MODEL)
    parser.add_argument("--enable-kimi-web-search", action="store_true")
    parser.add_argument("--max-total-scoring-candidates", type=int, default=None)
    parser.add_argument("--scoring-concurrency", type=int, default=5)
    parser.add_argument("--brief-concurrency", type=int, default=4)
    parser.add_argument("--max-parallel-tool-calls-per-turn", type=int, default=4)
    parser.add_argument("--github-tool-concurrency", type=int, default=5)
    parser.add_argument("--homepage-tool-concurrency", type=int, default=4)
    parser.add_argument("--web-search-tool-concurrency", type=int, default=2)
    parser.add_argument("--github-tool-rate-limit-per-second", type=float, default=2.0)
    parser.add_argument("--homepage-tool-rate-limit-per-second", type=float, default=2.0)
    parser.add_argument("--web-search-tool-rate-limit-per-second", type=float, default=1.0)
    parser.add_argument("--scout-timeout-seconds", type=int, default=90)
    parser.add_argument("--scoring-timeout-seconds", type=int, default=90)
    parser.add_argument("--brief-timeout-seconds", type=int, default=90)
    parser.add_argument("--deepdive-timeout-seconds", type=int, default=90)
    parser.add_argument("--scoring-max-output-tokens", type=int, default=3000)
    parser.add_argument("--brief-max-output-tokens", type=int, default=1000)
    parser.add_argument("--web-search-timeout-seconds", type=int, default=None)
    parser.add_argument("--finalize-stale-running-before", default=None)
    parser.add_argument("--max-investigation-turns", type=int, default=3)
    parser.add_argument("--max-scoring-attempts", type=int, default=3)
    parser.add_argument("--enable-direct-final", action="store_true")
    parser.add_argument("--max-context-tokens", type=int, default=32000)
    parser.add_argument("--context-safety-margin", type=int, default=500)
    parser.add_argument("--identity-token-allocation", type=int, default=800)
    parser.add_argument("--evidence-summary-token-allocation", type=int, default=800)
    parser.add_argument("--top-evidence-token-allocation", type=int, default=2400)
    parser.add_argument("--previous-turn-token-allocation", type=int, default=800)
    parser.add_argument("--tool-observation-token-allocation", type=int, default=2400)
    parser.add_argument("--recent-raw-tool-result-count", type=int, default=1)
    parser.add_argument("--max-tool-calls-per-candidate", type=int, default=8)
    parser.add_argument("--max-web-search-calls-per-candidate", type=int, default=1)
    parser.add_argument("--max-repo-files-per-candidate", type=int, default=3)
    parser.add_argument("--max-pages-per-candidate", type=int, default=1)
    parser.add_argument("--max-hn-thread-fetches-per-candidate", type=int, default=3)
    parser.add_argument("--max-x-context-fetches-per-candidate", type=int, default=5)
    parser.add_argument("--legacy-max-tool-calls-per-candidate", type=int, default=None)
    parser.add_argument(
        "--legacy-max-web-search-calls-per-candidate", type=int, default=None
    )
    parser.add_argument("--legacy-max-repo-files-per-candidate", type=int, default=None)
    parser.add_argument("--legacy-max-pages-per-candidate", type=int, default=None)
    parser.add_argument("--tool-registry-version", default="layer2-tools-v1")
    parser.add_argument("--max-evidence-rows-per-fetch", type=int, default=80)
    parser.add_argument("--max-github-file-chars", type=int, default=6000)
    parser.add_argument("--max-homepage-chars", type=int, default=6000)
    parser.add_argument("--max-web-results", type=int, default=5)
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_arg_parser().parse_args(argv)


def config_from_args(args: argparse.Namespace) -> dict[str, Any]:
    deepdive_limit = 0 if args.no_deepdive else args.deepdive_limit
    return {
        "routing": {
            "max_edge_watch_scout": args.edge_scout_limit,
            "max_scored_candidates": args.scoring_limit,
            "max_total_scoring_candidates": args.max_total_scoring_candidates,
            "max_deepdives_per_run": deepdive_limit,
            "deepdive_min_l2_score": args.deepdive_min_l2_score,
            "brief_min_score": args.brief_min_score,
            "score_only_min_score": args.score_only_min_score,
            "brief_target_count": args.brief_target_count,
            "brief_max_count": args.brief_max_count,
            "known_paradigm_keys": args.known_paradigm_key,
            "finalize_stale_running_before": args.finalize_stale_running_before,
        },
        "scoring_agent": {
            "provider": args.scoring_provider,
            "model": args.scoring_model,
            "prompt_id": args.scoring_prompt_id,
            "prompt_version": args.scoring_prompt_version,
            "output_schema_version": args.scoring_output_schema_version,
            "context_policy_version": args.scoring_context_policy_version,
            "timeout_seconds": args.scoring_timeout_seconds,
            "max_output_tokens": args.scoring_max_output_tokens,
            "concurrency": args.scoring_concurrency,
            "max_investigation_turns": args.max_investigation_turns,
            "max_scoring_attempts": args.max_scoring_attempts,
            "enable_direct_final": args.enable_direct_final,
            "context_budget": {
                "max_context_tokens": args.max_context_tokens,
                "output_reserve": args.scoring_max_output_tokens,
                "safety_margin": args.context_safety_margin,
                "identity_allocation": args.identity_token_allocation,
                "evidence_summary_allocation": args.evidence_summary_token_allocation,
                "top_evidence_allocation": args.top_evidence_token_allocation,
                "previous_turn_allocation": args.previous_turn_token_allocation,
                "tool_observation_allocation": args.tool_observation_token_allocation,
                "recent_raw_tool_result_count": args.recent_raw_tool_result_count,
            },
            "tool_budget": {
                "max_calls_per_candidate": args.max_tool_calls_per_candidate,
                "max_parallel_calls_per_turn": args.max_parallel_tool_calls_per_turn,
                "max_web_search_calls_per_candidate": args.max_web_search_calls_per_candidate,
                "max_github_file_calls_per_candidate": args.max_repo_files_per_candidate,
                "max_homepage_calls_per_candidate": args.max_pages_per_candidate,
            },
        },
        "brief_writer": {
            "enabled": not args.no_briefs,
            "provider": args.brief_provider,
            "model": args.brief_model,
            "prompt_id": args.brief_prompt_id,
            "prompt_version": args.brief_prompt_version,
            "output_schema_version": args.brief_output_schema_version,
            "timeout_seconds": args.brief_timeout_seconds,
            "max_output_tokens": args.brief_max_output_tokens,
            "concurrency": args.brief_concurrency,
        },
        "tool_runtime": {
            "registry_version": args.tool_registry_version,
            "enable_kimi_web_search": args.enable_kimi_web_search,
            "web_search_timeout_seconds": args.web_search_timeout_seconds,
            "max_evidence_rows_per_fetch": args.max_evidence_rows_per_fetch,
            "max_github_file_chars": args.max_github_file_chars,
            "max_homepage_chars": args.max_homepage_chars,
            "max_web_results": args.max_web_results,
            "families": {
                "github": {
                    "max_in_flight": args.github_tool_concurrency,
                    "starts_per_second": args.github_tool_rate_limit_per_second,
                },
                "homepage": {
                    "max_in_flight": args.homepage_tool_concurrency,
                    "starts_per_second": args.homepage_tool_rate_limit_per_second,
                },
                "web_search": {
                    "max_in_flight": args.web_search_tool_concurrency,
                    "starts_per_second": args.web_search_tool_rate_limit_per_second,
                },
            },
        },
        "edge_scout": {
            "enabled": args.enable_edge_scout,
            "provider": args.scout_provider,
            "model": args.scout_model,
            "timeout_seconds": args.scout_timeout_seconds,
        },
        "legacy_deepdive": {
            "enabled": args.enable_legacy_deepdive,
            "provider": args.deepdive_provider,
            "model": args.deepdive_model,
            "timeout_seconds": args.deepdive_timeout_seconds,
            "max_tool_calls_per_candidate": (
                args.legacy_max_tool_calls_per_candidate
                if args.legacy_max_tool_calls_per_candidate is not None
                else args.max_tool_calls_per_candidate
            ),
            "max_web_search_calls_per_candidate": (
                args.legacy_max_web_search_calls_per_candidate
                if args.legacy_max_web_search_calls_per_candidate is not None
                else args.max_web_search_calls_per_candidate
            ),
            "max_repo_files_per_candidate": (
                args.legacy_max_repo_files_per_candidate
                if args.legacy_max_repo_files_per_candidate is not None
                else args.max_repo_files_per_candidate
            ),
            "max_pages_per_candidate": (
                args.legacy_max_pages_per_candidate
                if args.legacy_max_pages_per_candidate is not None
                else args.max_pages_per_candidate
            ),
            "max_hn_thread_fetches_per_candidate": args.max_hn_thread_fetches_per_candidate,
            "max_x_context_fetches_per_candidate": args.max_x_context_fetches_per_candidate,
        },
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
