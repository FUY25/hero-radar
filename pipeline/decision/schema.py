from __future__ import annotations

import datetime as dt
import json
import sqlite3
from typing import Sequence


DECISION_SCHEMA_SQL = """
create table if not exists decision_runs (
    run_id text primary key,
    source_snapshot_run_id text,
    started_at text not null,
    completed_at text,
    status text not null,
    config_hash text not null,
    rule_version text not null,
    note text
);

create table if not exists entities (
    entity_id text primary key,
    canonical_entity text not null,
    canonical_key text not null,
    key_type text not null,
    first_seen text not null,
    aliases_json text not null,
    source_item_ids_json text not null
);

create table if not exists alias_links (
    id integer primary key autoincrement,
    entity_id text not null,
    source text not null,
    external_id text not null,
    alias text not null,
    confidence text not null,
    origin text not null,
    approved integer not null default 0,
    created_at text not null
);

create table if not exists entity_merge_proposals (
    id integer primary key autoincrement,
    run_id text not null,
    orphan text not null,
    target_entity_id text,
    confidence real not null,
    reason text not null,
    status text not null,
    created_at text not null
);

create table if not exists potential_candidates (
    id integer primary key autoincrement,
    entity_id text not null,
    run_id text not null,
    level text not null,
    fired_families_json text not null,
    first_trigger_at text not null,
    unique(run_id, entity_id)
);

create table if not exists edge_watch_candidates (
    id integer primary key autoincrement,
    entity_id text not null,
    run_id text not null,
    reason_json text not null,
    source_refs_json text not null,
    status text not null,
    unique(run_id, entity_id)
);

create table if not exists backfill_jobs (
    id integer primary key autoincrement,
    entity_id text not null,
    run_id text not null,
    source text not null,
    reason text not null,
    status text not null,
    requested_at text not null,
    completed_at text,
    result_ref text,
    unique(run_id, entity_id, source, reason)
);

create table if not exists entity_mentions (
    id integer primary key autoincrement,
    entity_id text not null,
    run_id text not null,
    window text not null,
    distinct_authors integer not null,
    credible_authors integer not null,
    mention_count integer not null,
    mention_acceleration real,
    source_refs_json text not null,
    unique(run_id, entity_id, window)
);

create table if not exists evidence_rows (
    id integer primary key autoincrement,
    entity_id text not null,
    canonical_entity text not null,
    alias text,
    source text not null,
    event_at text not null,
    relative_to_reference text,
    metric_name text not null,
    metric_value text not null,
    family text not null,
    rule_id text not null,
    rule_version text not null,
    signal_label text not null,
    historical_safety text not null,
    note text not null,
    raw_url_or_ref text,
    run_id text not null
);

create table if not exists api_cache (
    cache_key text primary key,
    source text not null,
    external_id text not null,
    window text not null,
    input_hash text not null,
    response_json text not null,
    status text not null,
    fetched_at text not null,
    expires_at text,
    error text
);

create table if not exists llm_cache (
    cache_key text primary key,
    provider text not null,
    model text not null,
    prompt_version text not null,
    task text not null,
    input_hash text not null,
    request_json text not null,
    response_json text not null,
    status text not null,
    created_at text not null,
    expires_at text,
    error text
);

create table if not exists l2_feed_runs (
    feed_run_id text primary key,
    decision_run_id text not null,
    started_at text not null,
    completed_at text,
    status text not null,
    config_hash text not null,
    model_profile_json text not null,
    note text
);

create table if not exists l2_candidate_groups (
    group_id text primary key,
    feed_run_id text not null,
    canonical_entity_id text not null,
    canonical_name text not null,
    canonical_key text not null,
    canonical_link text,
    member_entity_ids_json text not null,
    level text not null,
    source_families_json text not null,
    evidence_hash text not null,
    grouping_reason_json text not null,
    context_json text not null
);

create table if not exists l2_scout_results (
    id integer primary key autoincrement,
    feed_run_id text not null,
    group_id text not null,
    included_in_scoring integer not null,
    scout_score real not null,
    reason text not null,
    needed_context_json text not null,
    risk text not null,
    confidence real not null,
    provider text not null,
    model text not null,
    prompt_version text not null,
    cache_key text not null,
    unique(feed_run_id, group_id)
);

create table if not exists l2_scores (
    id integer primary key autoincrement,
    feed_run_id text not null,
    group_id text not null,
    l2_score real not null,
    axes_json text not null,
    primary_reason text not null,
    topic_tags_json text not null,
    rationale_short text not null,
    caveats_json text not null,
    provider text not null,
    model text not null,
    prompt_version text not null,
    cache_key text not null,
    unique(feed_run_id, group_id)
);

create table if not exists deepdive_reports (
    id integer primary key autoincrement,
    feed_run_id text not null,
    group_id text not null,
    status text not null,
    summary_json text not null,
    tool_trace_json text not null,
    provider text not null,
    model text not null,
    prompt_version text not null,
    cache_key text not null,
    created_at text not null,
    unique(feed_run_id, group_id)
);

create table if not exists l2_feed_items (
    id integer primary key autoincrement,
    feed_run_id text not null,
    group_id text not null,
    section text not null,
    rank integer not null,
    deepdive_status text not null,
    unique(feed_run_id, group_id, section)
);

create table if not exists l2_stage_events (
    id integer primary key autoincrement,
    feed_run_id text not null,
    group_id text,
    stage text not null,
    status text not null,
    error_type text,
    error text,
    metadata_json text not null,
    created_at text not null
);

create table if not exists feed_feedback (
    id integer primary key autoincrement,
    feed_run_id text not null,
    group_id text not null,
    vote text not null,
    created_at text not null,
    unique(feed_run_id, group_id)
);

create index if not exists idx_entities_key on entities(key_type, canonical_key);
create index if not exists idx_evidence_run_entity on evidence_rows(run_id, entity_id);
create index if not exists idx_candidates_run_level on potential_candidates(run_id, level);
create index if not exists idx_edge_watch_run on edge_watch_candidates(run_id);
create index if not exists idx_backfill_run_status on backfill_jobs(run_id, status);
create index if not exists idx_l2_groups_run on l2_candidate_groups(feed_run_id);
create index if not exists idx_l2_scores_run_score on l2_scores(feed_run_id, l2_score);
create index if not exists idx_l2_feed_items_run_section on l2_feed_items(feed_run_id, section, rank);
create index if not exists idx_l2_stage_events_run on l2_stage_events(feed_run_id, stage, status);
"""


def utc_now() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def to_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def init_decision_db(conn: sqlite3.Connection) -> None:
    conn.executescript(DECISION_SCHEMA_SQL)


def begin_decision_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    source_snapshot_run_id: str | None,
    config_hash: str,
    rule_version: str,
) -> None:
    conn.execute(
        """
        insert into decision_runs(run_id, source_snapshot_run_id, started_at, status, config_hash, rule_version, note)
        values (?, ?, ?, 'running', ?, ?, '')
        on conflict(run_id) do update set
            source_snapshot_run_id = excluded.source_snapshot_run_id,
            status = 'running',
            config_hash = excluded.config_hash,
            rule_version = excluded.rule_version
        """,
        (run_id, source_snapshot_run_id, utc_now(), config_hash, rule_version),
    )
    conn.commit()


def finish_decision_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    status: str,
    note: str = "",
) -> None:
    conn.execute(
        "update decision_runs set completed_at = ?, status = ?, note = ? where run_id = ?",
        (utc_now(), status, note, run_id),
    )
    conn.commit()


def reset_decision_stage(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    tables: Sequence[str],
) -> None:
    allowed = {
        "potential_candidates": "run_id",
        "edge_watch_candidates": "run_id",
        "backfill_jobs": "run_id",
        "entity_mentions": "run_id",
        "evidence_rows": "run_id",
        "l2_candidate_groups": "feed_run_id",
        "l2_scout_results": "feed_run_id",
        "l2_scores": "feed_run_id",
        "deepdive_reports": "feed_run_id",
        "l2_feed_items": "feed_run_id",
        "l2_stage_events": "feed_run_id",
        "feed_feedback": "feed_run_id",
    }
    for table in tables:
        if table not in allowed:
            raise ValueError(f"refusing to reset unknown run-scoped table: {table}")
        conn.execute(f"delete from {table} where {allowed[table]} = ?", (run_id,))
    conn.commit()
