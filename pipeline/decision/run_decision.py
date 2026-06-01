from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from pipeline.decision.entity_resolution import Entity, ResolutionResult, normalize_name_key, resolve_entities
from pipeline.decision.rules import (
    BackfillJob,
    EdgeWatchCandidate,
    EvidenceRow,
    PotentialCandidate,
    evaluate_entities,
    load_rules,
)
from pipeline.decision.schema import (
    begin_decision_run,
    finish_decision_run,
    init_decision_db,
    reset_decision_stage,
    to_json,
    utc_now,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT / "data" / "hero_radar.sqlite"
DEFAULT_EXPORT_PATH = ROOT / "data" / "exports" / "candidates_latest.json"
RUN_SCOPED_TABLES = [
    "potential_candidates",
    "edge_watch_candidates",
    "backfill_jobs",
    "entity_mentions",
    "evidence_rows",
]
CLASSIFIER_EVIDENCE_SOURCES = {"hn_llm_classifier", "x_tweets", "npm_registry"}


def read_latest_items(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select
            i.id,
            i.run_id,
            i.snapshot_id,
            i.source,
            i.external_id,
            i.name,
            i.url,
            i.fetched_at,
            i.heat,
            i.velocity,
            i.acceleration,
            i.source_rank,
            i.description,
            i.metadata_json,
            i.raw_json
        from items i
        join (
            select max(id) as snapshot_id
            from snapshots
            where status = 'ok'
            group by source
        ) latest on latest.snapshot_id = i.snapshot_id
        order by i.source, i.source_rank is null, i.source_rank, i.id
        """
    ).fetchall()
    columns = [
        "id",
        "run_id",
        "snapshot_id",
        "source",
        "external_id",
        "name",
        "url",
        "fetched_at",
        "heat",
        "velocity",
        "acceleration",
        "source_rank",
        "description",
        "metadata_json",
        "raw_json",
    ]
    output: list[dict[str, Any]] = []
    for db_row in rows:
        row = dict(zip(columns, db_row, strict=True))
        row["metadata"] = safe_json(row.pop("metadata_json"), default={})
        row["raw"] = safe_json(row.pop("raw_json"), default={})
        output.append(row)
    return output


def safe_json(value: Any, *, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def config_hash(rules: dict[str, Any]) -> str:
    raw = json.dumps(rules, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def latest_source_run_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "select run_id from snapshots where status = 'ok' order by id desc limit 1"
    ).fetchone()
    return row[0] if row else None


def reconcile_entity_ids(
    conn: sqlite3.Connection,
    resolution: ResolutionResult,
) -> dict[str, str]:
    _ = conn
    return {entity.entity_id: entity.entity_id for entity in resolution.entities}


def remap_resolution_ids(
    resolution: ResolutionResult,
    reconciled_ids: dict[str, str],
) -> ResolutionResult:
    entities: list[Entity] = []
    for entity in resolution.entities:
        new_id = reconciled_ids.get(entity.entity_id, entity.entity_id)
        entities.append(dataclasses.replace(entity, entity_id=new_id))
    item_to_entity = {
        item_id: reconciled_ids.get(entity_id, entity_id)
        for item_id, entity_id in resolution.item_to_entity.items()
    }
    return ResolutionResult(entities=entities, item_to_entity=item_to_entity)


def referenced_entity_ids(result) -> set[str]:
    entity_ids = {row.entity_id for row in result.evidence_rows}
    entity_ids.update(candidate.entity_id for candidate in result.potential_candidates)
    entity_ids.update(candidate.entity_id for candidate in result.edge_watch_candidates)
    entity_ids.update(job.entity_id for job in result.backfill_jobs)
    return entity_ids


def candidate_impact_item_ids(
    result,
    resolution: ResolutionResult,
) -> tuple[set[int], set[int]]:
    source_item_ids_by_entity = {
        entity.entity_id: {ref.item_id for ref in entity.source_refs}
        for entity in resolution.entities
    }
    potential_item_ids: set[int] = set()
    for candidate in result.potential_candidates:
        potential_item_ids.update(source_item_ids_by_entity.get(candidate.entity_id, set()))
    edge_item_ids: set[int] = set()
    for candidate in result.edge_watch_candidates:
        edge_item_ids.update(source_item_ids_by_entity.get(candidate.entity_id, set()))
    return potential_item_ids, edge_item_ids


def write_entities(
    conn: sqlite3.Connection,
    resolution: ResolutionResult,
    referenced_ids: set[str],
    first_seen: str,
) -> None:
    for entity in resolution.entities:
        if entity.entity_id not in referenced_ids:
            continue
        source_item_ids = [ref.item_id for ref in entity.source_refs]
        conn.execute(
            """
            insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json)
            values (?, ?, ?, ?, ?, ?, ?)
            on conflict(entity_id) do update set
                canonical_entity = excluded.canonical_entity,
                canonical_key = excluded.canonical_key,
                key_type = excluded.key_type,
                aliases_json = excluded.aliases_json,
                source_item_ids_json = excluded.source_item_ids_json
            """,
            (
                entity.entity_id,
                entity.canonical_entity,
                entity.canonical_key,
                entity.key_type,
                first_seen,
                to_json(list(entity.aliases)),
                to_json(source_item_ids),
            ),
        )
        for alias in {entity.canonical_key, *entity.aliases}:
            if not alias:
                continue
            conn.execute(
                """
                insert into alias_links(entity_id, source, external_id, alias, confidence, origin, approved, created_at)
                select ?, ?, ?, ?, ?, ?, ?, ?
                where not exists (
                    select 1 from alias_links
                    where entity_id = ? and alias = ? and origin = ?
                )
                """,
                (
                    entity.entity_id,
                    "decision",
                    entity.canonical_key,
                    alias,
                    "deterministic",
                    "stage_a",
                    1,
                    first_seen,
                    entity.entity_id,
                    alias,
                    "stage_a",
                ),
            )
    conn.commit()


def write_evidence(conn: sqlite3.Connection, evidence_rows: list[EvidenceRow]) -> None:
    conn.executemany(
        """
        insert into evidence_rows(entity_id, canonical_entity, alias, source, event_at, relative_to_reference, metric_name, metric_value, family, rule_id, rule_version, signal_label, historical_safety, note, raw_url_or_ref, run_id)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row.entity_id,
                row.canonical_entity,
                row.alias,
                row.source,
                row.event_at,
                row.relative_to_reference,
                row.metric_name,
                row.metric_value,
                row.family,
                row.rule_id,
                row.rule_version,
                row.signal_label,
                row.historical_safety,
                row.note,
                row.raw_url_or_ref,
                row.run_id,
            )
            for row in evidence_rows
        ],
    )
    conn.commit()


def write_candidates(
    conn: sqlite3.Connection,
    *,
    potential_candidates: list[PotentialCandidate],
    edge_watch_candidates: list[EdgeWatchCandidate],
    backfill_jobs: list[BackfillJob],
) -> None:
    conn.executemany(
        """
        insert into potential_candidates(entity_id, run_id, level, fired_families_json, first_trigger_at)
        values (?, ?, ?, ?, ?)
        on conflict(run_id, entity_id) do update set
            level = excluded.level,
            fired_families_json = excluded.fired_families_json,
            first_trigger_at = excluded.first_trigger_at
        """,
        [
            (
                candidate.entity_id,
                candidate.run_id,
                candidate.level,
                to_json(list(candidate.fired_families)),
                candidate.first_trigger_at,
            )
            for candidate in potential_candidates
        ],
    )
    conn.executemany(
        """
        insert into edge_watch_candidates(entity_id, run_id, reason_json, source_refs_json, status)
        values (?, ?, ?, ?, ?)
        on conflict(run_id, entity_id) do update set
            reason_json = excluded.reason_json,
            source_refs_json = excluded.source_refs_json,
            status = excluded.status
        """,
        [
            (
                candidate.entity_id,
                candidate.run_id,
                to_json(list(candidate.reasons)),
                to_json(list(candidate.source_refs)),
                candidate.status,
            )
            for candidate in edge_watch_candidates
        ],
    )
    conn.executemany(
        """
        insert into backfill_jobs(entity_id, run_id, source, reason, status, requested_at)
        values (?, ?, ?, ?, ?, ?)
        on conflict(run_id, entity_id, source, reason) do update set
            status = case
                when backfill_jobs.status in ('completed', 'failed') then backfill_jobs.status
                else excluded.status
            end,
            requested_at = excluded.requested_at,
            completed_at = backfill_jobs.completed_at,
            result_ref = backfill_jobs.result_ref
        """,
        [
            (
                job.entity_id,
                job.run_id,
                job.source,
                job.reason,
                job.status,
                job.requested_at,
            )
            for job in backfill_jobs
        ],
    )
    conn.commit()


def persist_pending_backfill_jobs(conn: sqlite3.Connection, jobs: list[BackfillJob]) -> None:
    write_candidates(
        conn,
        potential_candidates=[],
        edge_watch_candidates=[],
        backfill_jobs=jobs,
    )


def read_classifier_evidence(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select entity_id, canonical_entity, alias, source, event_at, relative_to_reference,
               metric_name, metric_value, family, rule_id, rule_version, signal_label,
               historical_safety, note, raw_url_or_ref, run_id
        from evidence_rows
        where run_id = ?
        order by id
        """,
        (run_id,),
    ).fetchall()
    columns = [
        "entity_id",
        "canonical_entity",
        "alias",
        "source",
        "event_at",
        "relative_to_reference",
        "metric_name",
        "metric_value",
        "family",
        "rule_id",
        "rule_version",
        "signal_label",
        "historical_safety",
        "note",
        "raw_url_or_ref",
        "run_id",
    ]
    return [
        dict(zip(columns, row, strict=True))
        for row in rows
        if row[3] in CLASSIFIER_EVIDENCE_SOURCES
    ]


def _key_label(key: str) -> str:
    if ":" not in key:
        return key
    return key.split(":", 1)[1].replace("-", " ").strip()


def _classifier_entity_from_evidence(
    conn: sqlite3.Connection,
    entity_id: str,
    rows: list[dict[str, Any]],
) -> Entity | None:
    row = conn.execute(
        """
        select canonical_entity, canonical_key, key_type, aliases_json, source_item_ids_json
        from entities
        where entity_id = ?
        """,
        (entity_id,),
    ).fetchone()
    if row:
        return Entity(
            entity_id=entity_id,
            canonical_entity=str(row[0] or row[1] or entity_id),
            canonical_key=str(row[1] or entity_id),
            key_type=str(row[2] or "name"),
            aliases=tuple(safe_json(row[3], default=[])),
            source_refs=(),
        )

    first = rows[0] if rows else {}
    raw_key = str(first.get("canonical_entity") or first.get("alias") or "")
    canonical_key = raw_key if ":" in raw_key else normalize_name_key(raw_key)
    if not canonical_key:
        return None
    return Entity(
        entity_id=entity_id,
        canonical_entity=_key_label(canonical_key),
        canonical_key=canonical_key,
        key_type=canonical_key.split(":", 1)[0],
        aliases=(canonical_key,),
        source_refs=(),
    )


def add_classifier_entities_to_resolution(
    conn: sqlite3.Connection,
    resolution: ResolutionResult,
    classifier_evidence: list[dict[str, Any]],
) -> ResolutionResult:
    existing = {entity.entity_id for entity in resolution.entities}
    by_entity: dict[str, list[dict[str, Any]]] = {}
    for row in classifier_evidence:
        entity_id = str(row.get("entity_id") or "")
        if entity_id and entity_id not in existing:
            by_entity.setdefault(entity_id, []).append(row)
    if not by_entity:
        return resolution

    entities = list(resolution.entities)
    for entity_id in sorted(by_entity):
        entity = _classifier_entity_from_evidence(conn, entity_id, by_entity[entity_id])
        if entity is not None:
            entities.append(entity)
    entities.sort(key=lambda entity: entity.entity_id)
    return ResolutionResult(entities=entities, item_to_entity=dict(resolution.item_to_entity))


def export_candidates(conn: sqlite3.Connection, run_id: str, path: Path) -> None:
    candidates = [
        {
            "entity_id": row[0],
            "canonical_entity": row[1],
            "canonical_key": row[2],
            "level": row[3],
            "fired_families": safe_json(row[4], default=[]),
            "first_trigger_at": row[5],
        }
        for row in conn.execute(
            """
            select pc.entity_id, e.canonical_entity, e.canonical_key, pc.level, pc.fired_families_json, pc.first_trigger_at
            from potential_candidates pc
            join entities e on e.entity_id = pc.entity_id
            where pc.run_id = ?
            order by
                case pc.level when 'high_potential' then 0 when 'potential' then 1 else 2 end,
                e.canonical_entity
            """,
            (run_id,),
        ).fetchall()
    ]
    edge_watch = [
        {
            "entity_id": row[0],
            "canonical_entity": row[1],
            "canonical_key": row[2],
            "reasons": safe_json(row[3], default=[]),
            "source_refs": safe_json(row[4], default=[]),
            "status": row[5],
        }
        for row in conn.execute(
            """
            select ew.entity_id, e.canonical_entity, e.canonical_key, ew.reason_json, ew.source_refs_json, ew.status
            from edge_watch_candidates ew
            join entities e on e.entity_id = ew.entity_id
            where ew.run_id = ?
            order by e.canonical_entity
            """,
            (run_id,),
        ).fetchall()
    ]
    backfill_jobs = [
        {
            "entity_id": row[0],
            "source": row[1],
            "reason": row[2],
            "status": row[3],
            "requested_at": row[4],
            "completed_at": row[5],
            "result_ref": row[6],
        }
        for row in conn.execute(
            """
            select entity_id, source, reason, status, requested_at, completed_at, result_ref
            from backfill_jobs
            where run_id = ?
            order by id
            """,
            (run_id,),
        ).fetchall()
    ]
    payload = {
        "run_id": run_id,
        "generated_at": utc_now(),
        "candidates": candidates,
        "edge_watch": edge_watch,
        "backfill_jobs": backfill_jobs,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def run_decision(
    *,
    db_path: Path,
    run_id: str,
    export_json_path: Path,
    now: str,
    github_client: Any | None = None,
    hn_llm_provider: Any | None = None,
    hn_classifier_limit: int = 0,
    x_llm_provider: Any | None = None,
    x_classifier_limit: int = 0,
    llm_concurrency: int = 1,
    x_stage1_batch_size: int = 100,
    x_credible_handles: set[str] | None = None,
    npm_client: Any | None = None,
    npm_backfill_limit: int = 0,
    resolver_search_client: Any | None = None,
    resolver_search_limit: int = 0,
    resolver_research_provider: Any | None = None,
    resolver_research_limit: int = 0,
    resolver_research_rounds: int = 0,
    readme_client: Any | None = None,
    enrich_readme_limit: int = 0,
) -> dict[str, int | str]:
    rules = load_rules()
    conn = sqlite3.connect(db_path)
    try:
        init_decision_db(conn)
        begin_decision_run(
            conn,
            run_id=run_id,
            source_snapshot_run_id=latest_source_run_id(conn),
            config_hash=config_hash(rules),
            rule_version=str(rules.get("version", "rules-v1")),
        )
        reset_decision_stage(conn, run_id=run_id, tables=RUN_SCOPED_TABLES)
        from pipeline.decision.classifier_preflight import classifier_preflight_summary

        preflight_summary = classifier_preflight_summary(
            conn,
            now=now,
            x_limit=x_classifier_limit,
            hn_limit=hn_classifier_limit,
        )
        rows = read_latest_items(conn)
        resolution = resolve_entities(rows, first_seen=now)
        reconciled_ids = reconcile_entity_ids(conn, resolution)
        resolution = remap_resolution_ids(resolution, reconciled_ids)

        pass1 = evaluate_entities(
            rows,
            resolution,
            run_id=run_id,
            rule_version=str(rules.get("version", "rules-v1")),
            now=now,
            rules=rules,
        )
        extra_github_signals: dict[str, dict[str, float]] = {}
        if (github_client is not None or npm_client is not None) and pass1.backfill_jobs:
            # Persist the shortlist before the bounded external runner reads pending jobs.
            referenced = referenced_entity_ids(pass1)
            write_entities(conn, resolution, referenced, now)
            persist_pending_backfill_jobs(conn, pass1.backfill_jobs)

        if github_client is not None and pass1.backfill_jobs:
            from pipeline.decision.backfill import run_backfill_jobs

            extra_github_signals = run_backfill_jobs(
                conn,
                run_id=run_id,
                github_client=github_client,
                now=now,
            ).get("signals", {})

        hn_summary: dict[str, Any] = {"classified": 0}
        if hn_llm_provider is not None and hn_classifier_limit > 0:
            from pipeline.decision.hn_classifier import run_hn_classifier

            potential_item_ids, edge_item_ids = candidate_impact_item_ids(pass1, resolution)
            hn_summary = run_hn_classifier(
                conn,
                run_id=run_id,
                provider=hn_llm_provider,
                limit=hn_classifier_limit,
                now=now,
                llm_concurrency=llm_concurrency,
                potential_item_ids=potential_item_ids,
                edge_item_ids=edge_item_ids,
            )

        x_stage1_summary: dict[str, Any] = {"mentions": 0}
        x_stage2_summary: dict[str, Any] = {"tiered": 0}
        if x_llm_provider is not None and x_classifier_limit > 0:
            from pipeline.decision.x_classifier import run_x_stage1, run_x_stage2

            x_stage1_summary = run_x_stage1(
                conn,
                run_id=run_id,
                provider=x_llm_provider,
                credible_handles=x_credible_handles or set(),
                now=now,
                limit=x_classifier_limit,
                batch_size=x_stage1_batch_size,
            )
            x_stage2_summary = run_x_stage2(
                conn,
                run_id=run_id,
                provider=x_llm_provider,
                now=now,
                limit=x_classifier_limit,
            )

        npm_summary: dict[str, Any] = {"completed": 0, "failed": 0}
        if npm_client is not None and npm_backfill_limit > 0:
            from pipeline.decision.npm_backfill import run_npm_backfill

            npm_summary = run_npm_backfill(
                conn,
                run_id=run_id,
                client=npm_client,
                now=now,
                limit=npm_backfill_limit,
            )

        resolver_summary: dict[str, Any] = {
            "enriched": 0,
            "aliases": 0,
            "proposals": 0,
            "researched": 0,
        }
        if hn_summary.get("classified") or x_stage2_summary.get("tiered"):
            from pipeline.decision.resolver import enrich_classifier_candidates

            resolver_summary = enrich_classifier_candidates(
                conn,
                run_id=run_id,
                search_client=resolver_search_client,
                max_searches_per_candidate=resolver_search_limit,
                research_provider=(
                    resolver_research_provider if resolver_research_limit > 0 else None
                ),
                max_research_rounds=resolver_research_rounds,
                now=now,
            )

        classifier_evidence = read_classifier_evidence(conn, run_id)
        resolution = add_classifier_entities_to_resolution(conn, resolution, classifier_evidence)
        final_result = (
            evaluate_entities(
                rows,
                resolution,
                run_id=run_id,
                rule_version=str(rules.get("version", "rules-v1")),
                now=now,
                rules=rules,
                extra_github_signals=extra_github_signals,
                classifier_evidence=classifier_evidence,
            )
            if extra_github_signals or classifier_evidence
            else pass1
        )

        referenced = referenced_entity_ids(final_result)
        write_entities(conn, resolution, referenced, now)
        write_evidence(conn, final_result.evidence_rows)
        write_candidates(
            conn,
            potential_candidates=final_result.potential_candidates,
            edge_watch_candidates=final_result.edge_watch_candidates,
            backfill_jobs=final_result.backfill_jobs,
        )
        readme_summary: dict[str, Any] = {"fetched": 0, "cached": 0, "skipped": 0}
        if readme_client is not None and enrich_readme_limit > 0:
            from pipeline.decision.readme_enrichment import enrich_candidate_readmes

            readme_summary = enrich_candidate_readmes(
                conn,
                run_id=run_id,
                client=readme_client,
                limit=enrich_readme_limit,
            )
        export_candidates(conn, run_id, export_json_path)
        finish_decision_run(conn, run_id=run_id, status="ok", note="done")

        summary: dict[str, int | str] = {
            "entities": len(referenced),
            "potential_candidates": len(final_result.potential_candidates),
            "edge_watch_candidates": len(final_result.edge_watch_candidates),
            "backfill_jobs": len(final_result.backfill_jobs),
            "hn_classified": int(hn_summary.get("classified") or 0),
            "x_stage1_mentions": int(x_stage1_summary.get("mentions") or 0),
            "x_stage2_tiered": int(x_stage2_summary.get("tiered") or 0),
            "npm_backfill_completed": int(npm_summary.get("completed") or 0),
            "resolver_enriched": int(resolver_summary.get("enriched") or 0),
            "resolver_aliases": int(resolver_summary.get("aliases") or 0),
            "resolver_proposals": int(resolver_summary.get("proposals") or 0),
            "resolver_researched": int(resolver_summary.get("researched") or 0),
            "readme_fetched": int(readme_summary.get("fetched") or 0),
            "readme_cached": int(readme_summary.get("cached") or 0),
            "export": str(export_json_path),
        }
        summary.update(preflight_summary)
        return summary
    except Exception as exc:
        finish_decision_run(conn, run_id=run_id, status="failed", note=str(exc))
        raise
    finally:
        conn.close()


def default_run_id(now: dt.datetime | None = None) -> str:
    active_now = now or dt.datetime.now(dt.timezone.utc)
    return f"decision_{active_now.strftime('%Y%m%d')}"


def build_github_client() -> Any:
    from pipeline.decision.backfill import GitHubClient

    return GitHubClient(token=os.environ.get("GITHUB_TOKEN"))


def parse_credible_handles(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part.strip().lower() for part in value.split(",") if part.strip()}


def build_deepseek_provider_from_args(args: argparse.Namespace) -> Any:
    from pipeline.decision.llm_provider import DeepSeekProvider
    from pipeline.decision.smoke_llm import load_env_file, load_json_secrets

    load_env_file()
    load_json_secrets()
    return DeepSeekProvider(model=args.llm_model)


def build_github_readme_client_from_args(args: argparse.Namespace) -> Any:
    from pipeline.decision.readme_enrichment import GitHubReadmeClient

    return GitHubReadmeClient()


def build_npm_client_from_args(args: argparse.Namespace) -> Any:
    from pipeline.decision.npm_backfill import NpmRegistryClient

    _ = args
    return NpmRegistryClient()


def build_resolver_search_client_from_args(args: argparse.Namespace) -> Any:
    from pipeline.decision.web_search import DuckDuckGoSearchClient

    return DuckDuckGoSearchClient()


def run_from_args(
    args: argparse.Namespace,
    *,
    decision_runner: Any = run_decision,
    llm_provider_builder: Any = build_deepseek_provider_from_args,
    github_client_builder: Any = build_github_client,
    github_readme_client_builder: Any = build_github_readme_client_from_args,
    npm_client_builder: Any = build_npm_client_from_args,
    resolver_search_client_builder: Any = build_resolver_search_client_from_args,
) -> dict[str, int | str]:
    hn_limit = int(args.classify_hn_limit or 0)
    x_limit = int(args.classify_x_limit or 0)
    resolver_search_limit = int(getattr(args, "resolver_search_limit", 0) or 0)
    resolver_research_limit = int(getattr(args, "resolver_research_limit", 0) or 0)
    resolver_research_rounds = int(getattr(args, "resolver_research_rounds", 3) or 0)
    enrich_readme_limit = int(getattr(args, "enrich_readme_limit", 0) or 0)
    npm_backfill_limit = int(getattr(args, "npm_backfill_limit", 0) or 0)
    llm_provider = (
        llm_provider_builder(args)
        if hn_limit > 0 or x_limit > 0 or resolver_research_limit > 0
        else None
    )
    resolver_search_client = (
        resolver_search_client_builder(args)
        if resolver_search_limit > 0 or resolver_research_limit > 0
        else None
    )
    return decision_runner(
        db_path=args.db,
        run_id=args.run_id,
        export_json_path=args.export_json,
        now=args.now,
        github_client=github_client_builder() if args.backfill else None,
        hn_llm_provider=llm_provider if hn_limit > 0 else None,
        hn_classifier_limit=hn_limit,
        x_llm_provider=llm_provider if x_limit > 0 else None,
        x_classifier_limit=x_limit,
        llm_concurrency=int(getattr(args, "llm_concurrency", 1) or 1),
        x_stage1_batch_size=args.x_stage1_batch_size,
        x_credible_handles=parse_credible_handles(args.x_credible_handles),
        npm_client=npm_client_builder(args) if npm_backfill_limit > 0 else None,
        npm_backfill_limit=npm_backfill_limit,
        resolver_search_client=resolver_search_client,
        resolver_search_limit=resolver_search_limit,
        resolver_research_provider=llm_provider if resolver_research_limit > 0 else None,
        resolver_research_limit=resolver_research_limit,
        resolver_research_rounds=resolver_research_rounds,
        readme_client=(
            github_readme_client_builder(args) if enrich_readme_limit > 0 else None
        ),
        enrich_readme_limit=enrich_readme_limit,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic pre-Layer2 decision pipeline")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--run-id", default=default_run_id())
    parser.add_argument("--export-json", type=Path, default=DEFAULT_EXPORT_PATH)
    parser.add_argument("--now", default=utc_now())
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--classify-hn-limit", type=int, default=0)
    parser.add_argument("--classify-x-limit", type=int, default=0)
    parser.add_argument("--llm-model", default=None)
    parser.add_argument("--llm-concurrency", type=int, default=1)
    parser.add_argument("--x-stage1-batch-size", type=int, default=100)
    parser.add_argument("--x-credible-handles", default="")
    parser.add_argument("--resolver-search-limit", type=int, default=0)
    parser.add_argument("--resolver-research-limit", type=int, default=0)
    parser.add_argument("--resolver-research-rounds", type=int, default=3)
    parser.add_argument("--npm-backfill-limit", type=int, default=0)
    parser.add_argument("--enrich-readme-limit", type=int, default=0)
    args = parser.parse_args()

    summary = run_from_args(args)
    print("Decision run complete")
    print(f"entities: {summary['entities']}")
    print(f"potential_candidates: {summary['potential_candidates']}")
    print(f"edge_watch_candidates: {summary['edge_watch_candidates']}")
    print(f"backfill_jobs: {summary['backfill_jobs']}")
    if "hn_classified" in summary:
        print(f"hn_classified: {summary['hn_classified']}")
    if "x_stage1_mentions" in summary:
        print(f"x_stage1_mentions: {summary['x_stage1_mentions']}")
    if "x_stage2_tiered" in summary:
        print(f"x_stage2_tiered: {summary['x_stage2_tiered']}")
    if "x_classifier_candidates_7d" in summary:
        print(f"x_time_basis: {summary['x_time_basis']}")
        print(f"x_classifier_candidates_7d: {summary['x_classifier_candidates_7d']}")
        print(f"x_classifier_will_process: {summary['x_classifier_will_process']}")
    if "hn_classifier_units" in summary:
        print(f"hn_classifier_units: {summary['hn_classifier_units']}")
        print(f"hn_classifier_will_process: {summary['hn_classifier_will_process']}")
    if "resolver_researched" in summary:
        print(f"resolver_researched: {summary['resolver_researched']}")
    if "readme_fetched" in summary:
        print(f"readme_fetched: {summary['readme_fetched']}")
    print(f"export: {summary['export']}")


if __name__ == "__main__":
    main()
