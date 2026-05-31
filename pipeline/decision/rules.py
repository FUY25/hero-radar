from __future__ import annotations

import dataclasses
import datetime as dt
import json
from pathlib import Path
from typing import Any

from pipeline.decision.entity_resolution import Entity, ResolutionResult


LEVEL_ORDER = {"none": 0, "watch": 1, "potential": 2, "high_potential": 3}
GITHUB_BOARD_SOURCES = {
    "github_trending",
    "github_movers_trending_repos",
    "github_movers_repofomo",
}
HF_SOURCES = {"huggingface_models", "huggingface_datasets", "huggingface_spaces"}
HN_NOISE_PROJECTNESS = {"news_article", "topic_discussion", "research_paper", "unknown"}
X_TIER_LEVELS = {"watch": "watch", "potential": "potential", "high": "high_potential", "high_potential": "high_potential"}


@dataclasses.dataclass(frozen=True)
class EvidenceRow:
    entity_id: str
    canonical_entity: str
    alias: str | None
    source: str
    event_at: str
    relative_to_reference: str | None
    metric_name: str
    metric_value: str
    family: str
    rule_id: str
    rule_version: str
    signal_label: str
    historical_safety: str
    note: str
    raw_url_or_ref: str | None
    run_id: str


@dataclasses.dataclass(frozen=True)
class PotentialCandidate:
    entity_id: str
    run_id: str
    level: str
    fired_families: tuple[str, ...]
    first_trigger_at: str


@dataclasses.dataclass(frozen=True)
class EdgeWatchCandidate:
    entity_id: str
    run_id: str
    reasons: tuple[str, ...]
    source_refs: tuple[str, ...]
    status: str


@dataclasses.dataclass(frozen=True)
class BackfillJob:
    entity_id: str
    run_id: str
    source: str
    reason: str
    status: str
    requested_at: str
    priority: float = 0.0


@dataclasses.dataclass(frozen=True)
class RuleEvaluationResult:
    evidence_rows: list[EvidenceRow]
    potential_candidates: list[PotentialCandidate]
    edge_watch_candidates: list[EdgeWatchCandidate]
    backfill_jobs: list[BackfillJob]


@dataclasses.dataclass
class EntityState:
    entity: Entity
    level: str = "none"
    fired_families: set[str] = dataclasses.field(default_factory=set)
    trigger_times: list[str] = dataclasses.field(default_factory=list)
    weak_signals: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    reasons: list[str] = dataclasses.field(default_factory=list)


def load_rules(path: Path | None = None) -> dict[str, Any]:
    rules_path = path or Path(__file__).resolve().parents[1] / "rules.json"
    return json.loads(rules_path.read_text())


def parse_time(value: Any) -> dt.datetime | None:
    if not value:
        return None
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = dt.datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def iso_time(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def number(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def value_text(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return str(value)


def level_for_thresholds(value: float, thresholds: dict[str, Any]) -> str:
    level = "none"
    for candidate in ("watch", "potential", "high_potential"):
        threshold = thresholds.get(candidate)
        if threshold is not None and value >= number(threshold):
            level = candidate
    return level


def is_at_least(level: str, target: str) -> bool:
    return LEVEL_ORDER[level] >= LEVEL_ORDER[target]


def promote(state: EntityState, level: str, family: str, event_at: str, reason: str) -> None:
    if LEVEL_ORDER[level] > LEVEL_ORDER[state.level]:
        state.level = level
    state.fired_families.add(family)
    state.trigger_times.append(event_at)
    state.reasons.append(reason)


def add_weak_signal(state: EntityState, family: str, event_at: str) -> None:
    state.weak_signals.append((family, event_at))


def evidence(
    *,
    state: EntityState,
    row: dict[str, Any] | None,
    source: str,
    event_at: str,
    metric_name: str,
    metric_value: float | str,
    family: str,
    rule_id: str,
    rule_version: str,
    level: str,
    historical_safety: str,
    note: str,
    run_id: str,
) -> EvidenceRow:
    raw_ref = f"item:{row['id']}" if row and row.get("id") is not None else None
    return EvidenceRow(
        entity_id=state.entity.entity_id,
        canonical_entity=state.entity.canonical_entity,
        alias=str(row.get("name")) if row and row.get("name") is not None else None,
        source=source,
        event_at=event_at,
        relative_to_reference=None,
        metric_name=metric_name,
        metric_value=value_text(metric_value) if isinstance(metric_value, (int, float)) else str(metric_value),
        family=family,
        rule_id=rule_id,
        rule_version=rule_version,
        signal_label="early_trigger" if is_at_least(level, "potential") else "watch",
        historical_safety=historical_safety,
        note=note,
        raw_url_or_ref=raw_ref,
        run_id=run_id,
    )


def row_time(row: dict[str, Any], metadata_key: str | None = None, fallback: str = "fetched_at") -> str:
    metadata = row.get("metadata") or {}
    if metadata_key and isinstance(metadata, dict):
        parsed = parse_time(metadata.get(metadata_key))
        if parsed:
            return iso_time(parsed)
    parsed = parse_time(row.get(fallback))
    if parsed:
        return iso_time(parsed)
    return str(row.get(fallback) or "")


def rows_by_entity(rows: list[dict[str, Any]], resolution: ResolutionResult) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        entity_id = resolution.item_to_entity.get(int(row["id"]))
        if entity_id:
            grouped.setdefault(entity_id, []).append(row)
    return grouped


def evidence_field(row: Any, field: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(field, default)
    return getattr(row, field, default)


def npm_evidence_by_entity(classifier_evidence: list[Any] | None) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = {}
    for row in classifier_evidence or []:
        if evidence_field(row, "source") != "npm_registry":
            continue
        if evidence_field(row, "family") != "package_family":
            continue
        entity_id = evidence_field(row, "entity_id")
        if entity_id:
            grouped.setdefault(str(entity_id), []).append(row)
    return grouped


def x_evidence_by_entity(classifier_evidence: list[Any] | None) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = {}
    for row in classifier_evidence or []:
        if evidence_field(row, "source") != "x_tweets":
            continue
        if evidence_field(row, "family") != "x_social":
            continue
        entity_id = evidence_field(row, "entity_id")
        if entity_id:
            grouped.setdefault(str(entity_id), []).append(row)
    return grouped


def hn_noise_item_ids(classifier_evidence: list[Any] | None) -> set[int]:
    item_ids: set[int] = set()
    for row in classifier_evidence or []:
        if evidence_field(row, "source") != "hn_llm_classifier":
            continue
        if evidence_field(row, "metric_name") != "hn_projectness":
            continue
        projectness = str(evidence_field(row, "metric_value") or "")
        if projectness not in HN_NOISE_PROJECTNESS and evidence_field(row, "signal_label") != "noise":
            continue
        raw_ref = str(evidence_field(row, "raw_url_or_ref") or "")
        if not raw_ref.startswith("item:"):
            continue
        try:
            item_ids.add(int(raw_ref.split(":", 1)[1]))
        except ValueError:
            continue
    return item_ids


def hn_row_is_noise(row: dict[str, Any], noise_item_ids: set[int]) -> bool:
    row_id = row.get("id")
    try:
        return int(row_id) in noise_item_ids
    except (TypeError, ValueError):
        return False


def entity_map(resolution: ResolutionResult) -> dict[str, Entity]:
    return {entity.entity_id: entity for entity in resolution.entities}


def evaluate_entities(
    rows: list[dict[str, Any]],
    resolution: ResolutionResult,
    run_id: str,
    rule_version: str,
    now: str,
    rules: dict[str, Any] | None = None,
    extra_github_signals: dict[str, dict[str, float]] | None = None,
    classifier_evidence: list[Any] | None = None,
) -> RuleEvaluationResult:
    active_rules = rules or load_rules()
    now_dt = parse_time(now) or dt.datetime.now(dt.timezone.utc)
    grouped = rows_by_entity(rows, resolution)
    entities = entity_map(resolution)
    npm_classifier_evidence = npm_evidence_by_entity(classifier_evidence)
    x_classifier_evidence = x_evidence_by_entity(classifier_evidence)
    hn_noise_items = hn_noise_item_ids(classifier_evidence)
    states = {
        entity_id: EntityState(entity=entity)
        for entity_id, entity in entities.items()
        if entity_id in grouped
        or (extra_github_signals and entity_id in extra_github_signals)
        or entity_id in npm_classifier_evidence
        or entity_id in x_classifier_evidence
    }
    evidence_rows: list[EvidenceRow] = []

    for entity_id in sorted(states):
        state = states[entity_id]
        entity_rows = grouped.get(entity_id, [])
        evidence_rows.extend(
            evaluate_github_trending(state, entity_rows, active_rules, rule_version, run_id)
        )
        evidence_rows.extend(
            evaluate_trending_repos(state, entity_rows, active_rules, rule_version, run_id)
        )
        evidence_rows.extend(
            evaluate_repofomo(state, entity_rows, active_rules, rule_version, run_id)
        )
        evidence_rows.extend(
            evaluate_hn_firebase(state, entity_rows, active_rules, rule_version, run_id, hn_noise_items)
        )
        evidence_rows.extend(
            evaluate_hn_algolia(state, entity_rows, active_rules, rule_version, run_id, now_dt, hn_noise_items)
        )
        evidence_rows.extend(
            evaluate_product_hunt(state, entity_rows, active_rules, rule_version, run_id)
        )
        evidence_rows.extend(
            evaluate_huggingface(state, entity_rows, active_rules, rule_version, run_id, now_dt)
        )
        evidence_rows.extend(
            evaluate_npm_registry(
                state,
                npm_classifier_evidence.get(entity_id, []),
                active_rules,
                rule_version,
                run_id,
                now,
            )
        )
        evidence_rows.extend(
            evaluate_x_social_evidence(
                state,
                x_classifier_evidence.get(entity_id, []),
                active_rules,
                rule_version,
                run_id,
                now,
            )
        )
        evidence_rows.extend(
            evaluate_extra_github_signals(
                state,
                extra_github_signals or {},
                active_rules,
                rule_version,
                run_id,
                now,
            )
        )

    evidence_rows.extend(
        evaluate_verified_cross_source(states, active_rules, rule_version, run_id, now_dt, now)
    )

    potential_candidates = build_potential_candidates(states, run_id)
    edge_watch_candidates = build_edge_watch_candidates(states, grouped, run_id)
    backfill_jobs = build_backfill_jobs(
        states,
        grouped,
        potential_candidates,
        rows,
        resolution,
        active_rules,
        run_id,
        now_dt,
        now,
    )

    return RuleEvaluationResult(
        evidence_rows=evidence_rows,
        potential_candidates=potential_candidates,
        edge_watch_candidates=edge_watch_candidates,
        backfill_jobs=backfill_jobs,
    )


def evaluate_github_trending(
    state: EntityState,
    rows: list[dict[str, Any]],
    rules: dict[str, Any],
    rule_version: str,
    run_id: str,
) -> list[EvidenceRow]:
    daily_rows = [
        row
        for row in rows
        if row.get("source") == "github_trending"
        and (row.get("metadata") or {}).get("period") == "daily"
    ]
    if not daily_rows:
        return []
    best = max(daily_rows, key=lambda row: number((row.get("metadata") or {}).get("period_stars")))
    metric_value = number((best.get("metadata") or {}).get("period_stars"))
    level = level_for_thresholds(metric_value, rules["github_trending"]["daily_stars"])
    if level == "none":
        return []
    event_at = row_time(best)
    promote(state, level, "github", event_at, "github_trending_daily")
    if level == "watch":
        add_weak_signal(state, "github", event_at)
    return [
        evidence(
            state=state,
            row=best,
            source="github_trending",
            event_at=event_at,
            metric_name="stars_today",
            metric_value=metric_value,
            family="github",
            rule_id=f"github_trending_daily_{level}",
            rule_version=rule_version,
            level=level,
            historical_safety="snapshot_only",
            note="daily trending threshold passed",
            run_id=run_id,
        )
    ]


def evaluate_trending_repos(
    state: EntityState,
    rows: list[dict[str, Any]],
    rules: dict[str, Any],
    rule_version: str,
    run_id: str,
) -> list[EvidenceRow]:
    output: list[EvidenceRow] = []
    for row in rows:
        metadata = row.get("metadata") or {}
        if row.get("source") != "github_movers_trending_repos" or metadata.get("period") != "daily":
            continue
        for metric_name in ("stars_velocity", "forks_velocity"):
            metric_value = number(metadata.get(metric_name))
            level = level_for_thresholds(metric_value, rules["trending_repos"][metric_name])
            if level == "none":
                continue
            event_at = row_time(row)
            promote(state, level, "github", event_at, f"trending_repos_{metric_name}")
            if level == "watch":
                add_weak_signal(state, "github", event_at)
            output.append(
                evidence(
                    state=state,
                    row=row,
                    source="github_movers_trending_repos",
                    event_at=event_at,
                    metric_name=metric_name,
                    metric_value=metric_value,
                    family="github",
                    rule_id=f"trending_repos_{metric_name}_{level}",
                    rule_version=rule_version,
                    level=level,
                    historical_safety="snapshot_only",
                    note="daily movers threshold passed",
                    run_id=run_id,
                )
            )
    return output


def evaluate_repofomo(
    state: EntityState,
    rows: list[dict[str, Any]],
    rules: dict[str, Any],
    rule_version: str,
    run_id: str,
) -> list[EvidenceRow]:
    output: list[EvidenceRow] = []
    thresholds = rules["repofomo"]
    for row in rows:
        metadata = row.get("metadata") or {}
        if row.get("source") != "github_movers_repofomo":
            continue
        stars_7d = number(metadata.get("stars_7d"))
        stars_30d = number(metadata.get("stars_30d"))
        stars_60d = number(metadata.get("stars_60d"))
        new_forks = number(metadata.get("new_forks") or metadata.get("forks_7d"))
        accelerating = stars_7d / 7 > stars_30d / 30 > stars_60d / 60
        level = "none"
        if stars_7d >= number(thresholds["stars_7d_watch"]):
            level = "watch"
        if stars_7d >= number(thresholds["stars_7d_potential_if_accelerating"]) and accelerating:
            level = "potential"
        if stars_7d >= number(thresholds["stars_7d_high"]) or new_forks >= number(thresholds["new_forks_high"]):
            level = "high_potential"
        if level == "none":
            continue
        event_at = row_time(row)
        promote(state, level, "github", event_at, "repofomo_stars_7d")
        if level == "watch":
            add_weak_signal(state, "github", event_at)
        output.append(
            evidence(
                state=state,
                row=row,
                source="github_movers_repofomo",
                event_at=event_at,
                metric_name="stars_7d",
                metric_value=stars_7d,
                family="github",
                rule_id=f"repofomo_stars_7d_{level}",
                rule_version=rule_version,
                level=level,
                historical_safety="snapshot_only",
                note="repofomo threshold passed",
                run_id=run_id,
            )
        )
    return output


def evaluate_hn_firebase(
    state: EntityState,
    rows: list[dict[str, Any]],
    rules: dict[str, Any],
    rule_version: str,
    run_id: str,
    hn_noise_items: set[int] | None = None,
) -> list[EvidenceRow]:
    output: list[EvidenceRow] = []
    thresholds = rules["hn"]
    strong = state.entity.key_type in {"github", "domain"}
    for row in rows:
        metadata = row.get("metadata") or {}
        if row.get("source") != "hn_firebase":
            continue
        if hn_row_is_noise(row, hn_noise_items or set()):
            continue
        score = number(metadata.get("score"))
        level = "none"
        if score >= number(thresholds["front_page_score_watch"]):
            level = "watch"
        if strong and score >= number(thresholds["front_page_score_potential"]):
            level = "potential"
        if level == "none":
            continue
        event_at = row_time(row)
        promote(state, level, "hn", event_at, "hn_firebase_score")
        if level == "watch":
            add_weak_signal(state, "hn", event_at)
        output.append(
            evidence(
                state=state,
                row=row,
                source="hn_firebase",
                event_at=event_at,
                metric_name="hn_score",
                metric_value=score,
                family="hn",
                rule_id=f"hn_firebase_score_{level}",
                rule_version=rule_version,
                level=level,
                historical_safety="snapshot_only",
                note="hn front page threshold passed",
                run_id=run_id,
            )
        )
    return output


def evaluate_hn_algolia(
    state: EntityState,
    rows: list[dict[str, Any]],
    rules: dict[str, Any],
    rule_version: str,
    run_id: str,
    now_dt: dt.datetime,
    hn_noise_items: set[int] | None = None,
) -> list[EvidenceRow]:
    if state.entity.key_type not in {"github", "domain"}:
        return []
    stories: dict[str, dict[str, Any]] = {}
    for row in rows:
        metadata = row.get("metadata") or {}
        if row.get("source") != "hn_algolia":
            continue
        if hn_row_is_noise(row, hn_noise_items or set()):
            continue
        created = parse_time(metadata.get("created_at") or row.get("fetched_at"))
        if not created or created < now_dt - dt.timedelta(days=7) or created > now_dt:
            continue
        story_id = str(metadata.get("story_id") or metadata.get("objectID") or row.get("external_id"))
        stories.setdefault(story_id, row)
    count = len(stories)
    if count < number(rules["hn"]["strict_story_count_7d_watch"]):
        return []
    level = "potential" if count >= number(rules["hn"]["strict_story_count_7d_potential"]) else "watch"
    first_row = sorted(
        stories.values(),
        key=lambda row: row_time(row, "created_at"),
    )[0]
    event_at = row_time(first_row, "created_at")
    promote(state, level, "hn", event_at, "hn_algolia_strict_story_count")
    if level == "watch":
        add_weak_signal(state, "hn", event_at)
    return [
        evidence(
            state=state,
            row=first_row,
            source="hn_algolia",
            event_at=event_at,
            metric_name="strict_story_count_7d",
            metric_value=count,
            family="hn",
            rule_id=f"hn_algolia_strict_story_count_7d_{level}",
            rule_version=rule_version,
            level=level,
            historical_safety="as_of_safe",
            note="strict linked HN story count in 7d",
            run_id=run_id,
        )
    ]


def evaluate_product_hunt(
    state: EntityState,
    rows: list[dict[str, Any]],
    rules: dict[str, Any],
    rule_version: str,
    run_id: str,
) -> list[EvidenceRow]:
    output: list[EvidenceRow] = []
    thresholds = rules["product_hunt"]
    for row in rows:
        metadata = row.get("metadata") or {}
        if row.get("source") != "product_hunt":
            continue
        rank_values = [
            ("daily_rank", number(metadata.get("daily_rank"), default=999999)),
            ("weekly_rank", number(metadata.get("weekly_rank"), default=999999)),
        ]
        metric_name, rank = min(rank_values, key=lambda pair: pair[1])
        level = "none"
        if rank <= number(thresholds["rank_watch"]):
            level = "watch"
        if rank <= number(thresholds["rank_potential"]):
            level = "potential"
        if level == "none":
            continue
        event_at = row_time(row)
        promote(state, level, "product_hunt", event_at, "product_hunt_rank")
        if level == "watch":
            add_weak_signal(state, "product_hunt", event_at)
        output.append(
            evidence(
                state=state,
                row=row,
                source="product_hunt",
                event_at=event_at,
                metric_name=metric_name,
                metric_value=rank,
                family="product_hunt",
                rule_id=f"product_hunt_rank_{level}",
                rule_version=rule_version,
                level=level,
                historical_safety="snapshot_only",
                note="product hunt rank threshold passed",
                run_id=run_id,
            )
        )
    return output


def evaluate_huggingface(
    state: EntityState,
    rows: list[dict[str, Any]],
    rules: dict[str, Any],
    rule_version: str,
    run_id: str,
    now_dt: dt.datetime,
) -> list[EvidenceRow]:
    resources: dict[str, dict[str, Any]] = {}
    for row in rows:
        metadata = row.get("metadata") or {}
        if row.get("source") not in HF_SOURCES:
            continue
        created = parse_time(metadata.get("createdAt") or metadata.get("created_at"))
        if not created or created < now_dt - dt.timedelta(hours=48) or created > now_dt:
            continue
        resource_id = str(row.get("external_id") or row.get("name") or row.get("id"))
        resources.setdefault(resource_id, row)
    count = len(resources)
    if count < number(rules["huggingface"]["single_resource_watch"]):
        return []
    level = (
        "potential"
        if count >= number(rules["huggingface"]["exact_resources_48h_potential"])
        else "watch"
    )
    first_row = sorted(
        resources.values(),
        key=lambda row: row_time(row, "created_at"),
    )[0]
    event_at = row_time(first_row, "created_at")
    promote(state, level, "huggingface", event_at, "huggingface_resources_48h")
    if level == "watch":
        add_weak_signal(state, "huggingface", event_at)
    return [
        evidence(
            state=state,
            row=first_row,
            source="huggingface",
            event_at=event_at,
            metric_name="hf_resources_48h",
            metric_value=count,
            family="huggingface",
            rule_id=f"huggingface_resources_48h_{level}",
            rule_version=rule_version,
            level=level,
            historical_safety="as_of_safe",
            note="linked huggingface resource count in 48h",
            run_id=run_id,
        )
    ]


def evaluate_npm_registry(
    state: EntityState,
    npm_evidence: list[Any],
    rules: dict[str, Any],
    rule_version: str,
    run_id: str,
    now: str,
) -> list[EvidenceRow]:
    if not npm_evidence:
        return []
    thresholds = rules.get("npm_registry", {}).get("daily_downloads", {})
    output: list[EvidenceRow] = []
    packages: dict[str, dict[str, Any]] = {}

    for row in npm_evidence:
        metric_name = evidence_field(row, "metric_name")
        if metric_name not in {"daily_downloads", "downloads_7d"}:
            continue
        package_key = str(
            evidence_field(row, "alias")
            or evidence_field(row, "raw_url_or_ref")
            or state.entity.canonical_key
        )
        package = packages.setdefault(package_key, {})
        existing = package.get(metric_name)
        if existing is None or number(evidence_field(row, "metric_value")) > number(
            evidence_field(existing, "metric_value")
        ):
            package[metric_name] = row

    for package_key in sorted(packages):
        package = packages[package_key]
        daily_row = package.get("daily_downloads")
        if daily_row is None:
            continue
        daily_downloads = number(evidence_field(daily_row, "metric_value"))
        weekly_row = package.get("downloads_7d")
        weekly_downloads = (
            number(evidence_field(weekly_row, "metric_value"))
            if weekly_row is not None
            else None
        )
        rising = (
            weekly_downloads is not None
            and weekly_downloads > 0
            and daily_downloads > weekly_downloads / 7
        )

        level = "none"
        if daily_downloads >= number(thresholds.get("watch")):
            level = "watch"
        if daily_downloads >= number(thresholds.get("potential")) and rising:
            level = "potential"
        if daily_downloads >= number(thresholds.get("high_potential")):
            level = "high_potential"
        if level == "none":
            continue

        event_at = str(evidence_field(daily_row, "event_at") or now)
        promote(state, level, "package_family", event_at, "npm_registry_daily_downloads")
        if level == "watch":
            add_weak_signal(state, "package_family", event_at)
        if level == "high_potential":
            note = "npm daily downloads high threshold passed"
        elif level == "potential":
            note = "npm daily downloads potential threshold passed and rising versus 7d average"
        else:
            note = "npm daily downloads watch threshold passed; rising not proven"
        output.append(
            EvidenceRow(
                entity_id=state.entity.entity_id,
                canonical_entity=state.entity.canonical_entity,
                alias=evidence_field(daily_row, "alias"),
                source="npm_registry",
                event_at=event_at,
                relative_to_reference=None,
                metric_name="daily_downloads",
                metric_value=value_text(daily_downloads),
                family="package_family",
                rule_id=f"npm_registry_daily_downloads_{level}",
                rule_version=rule_version,
                signal_label="early_trigger" if is_at_least(level, "potential") else "watch",
                historical_safety=str(
                    evidence_field(daily_row, "historical_safety") or "as_of_safe"
                ),
                note=note,
                raw_url_or_ref=evidence_field(daily_row, "raw_url_or_ref"),
                run_id=run_id,
            )
        )
    return output


def x_has_citation(row: Any) -> bool:
    raw_ref = str(evidence_field(row, "raw_url_or_ref") or "")
    return "tweet:" in raw_ref


def evaluate_x_social_evidence(
    state: EntityState,
    x_evidence: list[Any],
    rules: dict[str, Any],
    rule_version: str,
    run_id: str,
    now: str,
) -> list[EvidenceRow]:
    if not x_evidence:
        return []
    if (rules.get("x_social") or {}).get("enabled", True) is False:
        return []

    tier_rows = [
        row
        for row in x_evidence
        if evidence_field(row, "metric_name") == "x_tier"
        and str(evidence_field(row, "metric_value") or "") in X_TIER_LEVELS
        and x_has_citation(row)
    ]
    if not tier_rows:
        return []

    best = max(
        tier_rows,
        key=lambda row: LEVEL_ORDER[X_TIER_LEVELS[str(evidence_field(row, "metric_value"))]],
    )
    x_tier = str(evidence_field(best, "metric_value"))
    level = X_TIER_LEVELS[x_tier]
    event_at = str(evidence_field(best, "event_at") or now)
    promote(state, level, "x_social", event_at, "x_social_tier")
    if level == "watch":
        add_weak_signal(state, "x_social", event_at)

    return [
        EvidenceRow(
            entity_id=state.entity.entity_id,
            canonical_entity=state.entity.canonical_entity,
            alias=evidence_field(best, "alias"),
            source="x_tweets",
            event_at=event_at,
            relative_to_reference=None,
            metric_name="x_tier",
            metric_value=x_tier,
            family="x_social",
            rule_id=f"x_social_tier_{level}",
            rule_version=rule_version,
            signal_label="early_trigger" if is_at_least(level, "potential") else "watch",
            historical_safety=str(
                evidence_field(best, "historical_safety") or "llm_source_classifier"
            ),
            note=str(evidence_field(best, "note") or "accepted x_social source tier"),
            raw_url_or_ref=evidence_field(best, "raw_url_or_ref"),
            run_id=run_id,
        )
    ]


def evaluate_extra_github_signals(
    state: EntityState,
    extra_github_signals: dict[str, dict[str, float]],
    rules: dict[str, Any],
    rule_version: str,
    run_id: str,
    now: str,
) -> list[EvidenceRow]:
    signals = extra_github_signals.get(state.entity.entity_id)
    if not signals:
        return []
    output: list[EvidenceRow] = []
    thresholds = rules["github_trending"]["daily_stars"]
    for metric_name, metric_value in sorted(signals.items()):
        if metric_name not in {"stars_24h", "stars_7d"}:
            continue
        level = level_for_thresholds(number(metric_value), thresholds)
        if level == "none":
            continue
        promote(state, level, "github", now, f"github_backfill_{metric_name}")
        if level == "watch":
            add_weak_signal(state, "github", now)
        output.append(
            evidence(
                state=state,
                row=None,
                source="github_backfill",
                event_at=now,
                metric_name=metric_name,
                metric_value=number(metric_value),
                family="github",
                rule_id=f"github_backfill_{metric_name}_{level}",
                rule_version=rule_version,
                level=level,
                historical_safety="as_of_safe",
                note="precise backfill threshold passed",
                run_id=run_id,
            )
        )
    return output


def evaluate_verified_cross_source(
    states: dict[str, EntityState],
    rules: dict[str, Any],
    rule_version: str,
    run_id: str,
    now_dt: dt.datetime,
    now: str,
) -> list[EvidenceRow]:
    output: list[EvidenceRow] = []
    required = int(rules["verified_cross_source"]["weak_signals_48h_potential"])
    for state in states.values():
        if state.entity.key_type not in {"github", "domain"}:
            continue
        recent_families = {
            family
            for family, event_at in state.weak_signals
            if (parsed := parse_time(event_at))
            and now_dt - dt.timedelta(hours=48) <= parsed <= now_dt
        }
        if len(recent_families) < required:
            continue
        promote(
            state,
            "potential",
            "verified_cross_source",
            now,
            "verified_cross_source_two_weak_48h",
        )
        output.append(
            evidence(
                state=state,
                row=None,
                source="decision_rules",
                event_at=now,
                metric_name="weak_source_families_48h",
                metric_value=len(recent_families),
                family="verified_cross_source",
                rule_id="verified_cross_source_two_weak_48h",
                rule_version=rule_version,
                level="potential",
                historical_safety="snapshot_only",
                note="two weak source families within 48h",
                run_id=run_id,
            )
        )
    return output


def build_potential_candidates(states: dict[str, EntityState], run_id: str) -> list[PotentialCandidate]:
    candidates: list[PotentialCandidate] = []
    for state in states.values():
        if not is_at_least(state.level, "potential"):
            continue
        candidates.append(
            PotentialCandidate(
                entity_id=state.entity.entity_id,
                run_id=run_id,
                level=state.level,
                fired_families=tuple(sorted(state.fired_families)),
                first_trigger_at=min(state.trigger_times) if state.trigger_times else "",
            )
        )
    return sorted(candidates, key=lambda candidate: (candidate.level, candidate.entity_id))


def build_edge_watch_candidates(
    states: dict[str, EntityState],
    grouped: dict[str, list[dict[str, Any]]],
    run_id: str,
) -> list[EdgeWatchCandidate]:
    candidates: list[EdgeWatchCandidate] = []
    for state in states.values():
        if state.level != "watch":
            continue
        source_refs = tuple(
            f"item:{row['id']}"
            for row in grouped.get(state.entity.entity_id, [])
            if row.get("id") is not None
        )
        candidates.append(
            EdgeWatchCandidate(
                entity_id=state.entity.entity_id,
                run_id=run_id,
                reasons=tuple(sorted(set(state.reasons))),
                source_refs=source_refs,
                status="active",
            )
        )
    return sorted(candidates, key=lambda candidate: candidate.entity_id)


def build_backfill_jobs(
    states: dict[str, EntityState],
    grouped: dict[str, list[dict[str, Any]]],
    potential_candidates: list[PotentialCandidate],
    rows: list[dict[str, Any]],
    resolution: ResolutionResult,
    rules: dict[str, Any],
    run_id: str,
    now_dt: dt.datetime,
    now: str,
) -> list[BackfillJob]:
    jobs: dict[tuple[str, str], BackfillJob] = {}
    board_entities = {
        entity_id
        for entity_id, entity_rows in grouped.items()
        if any(row.get("source") in GITHUB_BOARD_SOURCES for row in entity_rows)
    }

    for candidate in potential_candidates:
        state = states[candidate.entity_id]
        if state.entity.key_type == "github":
            jobs[(candidate.entity_id, "potential_candidate")] = BackfillJob(
                entity_id=candidate.entity_id,
                run_id=run_id,
                source="github_stargazers",
                reason="potential_candidate",
                status="pending",
                requested_at=now,
                priority=1_000_000 + LEVEL_ORDER[candidate.level],
            )

    entity_by_id = entity_map(resolution)
    for row in rows:
        if row.get("source") != "github_search":
            continue
        entity_id = resolution.item_to_entity.get(int(row["id"]))
        if not entity_id or entity_id in board_entities:
            continue
        entity = entity_by_id.get(entity_id)
        if not entity or entity.key_type != "github":
            continue
        metadata = row.get("metadata") or {}
        created = parse_time(metadata.get("created_at"))
        pushed = parse_time(metadata.get("pushed_at"))
        if not created or not pushed:
            continue
        age_days = max((now_dt - created).total_seconds() / 86400, 1)
        stars = number(metadata.get("stars") or metadata.get("stargazers_count"))
        stars_per_day = stars / age_days
        backfill_rules = rules["github_search_backfill"]
        if stars_per_day < number(backfill_rules["min_stars_per_day"]):
            continue
        if now_dt - pushed > dt.timedelta(days=number(backfill_rules["pushed_within_days"])):
            continue
        if now_dt - created > dt.timedelta(days=number(backfill_rules["created_within_days"])):
            continue
        jobs[(entity_id, "github_search_velocity_prefilter")] = BackfillJob(
            entity_id=entity_id,
            run_id=run_id,
            source="github_stargazers",
            reason="github_search_velocity_prefilter",
            status="pending",
            requested_at=now,
            priority=stars_per_day,
        )

    return sorted(jobs.values(), key=lambda job: (-job.priority, job.entity_id))[
        : int(rules.get("backfill_max_jobs", 40))
    ]
