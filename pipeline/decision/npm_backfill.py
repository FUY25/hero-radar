from __future__ import annotations

import argparse
import json
import re
import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from pipeline.decision.schema import utc_now
from pipeline.decision.bounded_parallel import bounded_parallel_map
from pipeline.decision.rate_limit import RateLimitedClient


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT / "data" / "hero_radar.sqlite"
PACKAGE_REASON_PREFIX = "package_downloads:"
GITHUB_RE = re.compile(r"github\.com[:/]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")


class NpmRegistryClient:
    def __init__(self, *, timeout: int = 20) -> None:
        self.timeout = timeout

    def request_json(self, url: str) -> Any:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "hero-radar-decision-npm-backfill",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def package_metadata(self, package: str) -> dict[str, Any]:
        encoded = urllib.parse.quote(package, safe="")
        return self.request_json(f"https://registry.npmjs.org/{encoded}")

    def downloads(self, package: str, period: str) -> dict[str, Any]:
        encoded = urllib.parse.quote(package, safe="")
        encoded_period = urllib.parse.quote(period, safe="")
        return self.request_json(
            f"https://api.npmjs.org/downloads/point/{encoded_period}/{encoded}"
        )


def package_from_reason(reason: str) -> str:
    if not reason.startswith(PACKAGE_REASON_PREFIX):
        raise ValueError(f"unsupported npm backfill reason: {reason}")
    package = reason[len(PACKAGE_REASON_PREFIX) :].strip()
    if not package:
        raise ValueError("npm backfill reason is missing a package")
    return package


def npm_package_url(package: str) -> str:
    return f"https://www.npmjs.com/package/{urllib.parse.quote(package, safe='@/')}"


def downloads_ref(package: str, period: str) -> str:
    return (
        "https://api.npmjs.org/downloads/point/"
        f"{urllib.parse.quote(period, safe='')}/{urllib.parse.quote(package, safe='')}"
    )


def metric_text(value: object) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def download_count(payload: dict[str, Any]) -> int:
    downloads = payload.get("downloads")
    if downloads is None:
        raise ValueError("npm downloads response missing downloads")
    return int(downloads)


def repository_url_from_metadata(metadata: dict[str, Any]) -> str | None:
    repository = metadata.get("repository")
    if isinstance(repository, dict):
        value = repository.get("url")
        if value:
            return str(value)
    if isinstance(repository, str) and repository:
        return repository
    links = metadata.get("links")
    if isinstance(links, dict):
        value = links.get("repository")
        if value:
            return str(value)
    return None


def github_key_from_repository(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if text.startswith("git+"):
        text = text[4:]
    if text.startswith("github:"):
        short = text.split("github:", 1)[1]
        parts = short.split("/", 1)
        if len(parts) == 2:
            owner, repo = parts
            return normalize_github_key(owner, repo)
    if text.startswith("git@github.com:"):
        text = f"https://github.com/{text.split(':', 1)[1]}"
    match = GITHUB_RE.search(text)
    if not match:
        return None
    return normalize_github_key(match.group(1), match.group(2))


def normalize_github_key(owner: str, repo: str) -> str | None:
    clean_owner = owner.strip().strip("/")
    clean_repo = repo.strip().strip("/").split("#", 1)[0].split("?", 1)[0]
    clean_repo = clean_repo.removesuffix(".git")
    if not clean_owner or not clean_repo:
        return None
    return f"github:{clean_owner.lower()}/{clean_repo.lower()}"


def entity_label(conn: sqlite3.Connection, entity_id: str) -> str:
    row = conn.execute(
        "select canonical_entity from entities where entity_id = ?",
        (entity_id,),
    ).fetchone()
    if row and row[0]:
        return str(row[0])
    return entity_id


def insert_npm_evidence(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    canonical_entity: str,
    package: str,
    metric_name: str,
    metric_value: object,
    run_id: str,
    now: str,
    raw_url_or_ref: str,
    historical_safety: str,
    note: str,
) -> None:
    conn.execute(
        """
        insert into evidence_rows(entity_id, canonical_entity, alias, source, event_at, relative_to_reference, metric_name, metric_value, family, rule_id, rule_version, signal_label, historical_safety, note, raw_url_or_ref, run_id)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_id,
            canonical_entity,
            package,
            "npm_registry",
            now,
            None,
            metric_name,
            metric_text(metric_value),
            "package_family",
            f"npm_registry_{metric_name}",
            "rules-v1",
            "backfill",
            historical_safety,
            note,
            raw_url_or_ref,
            run_id,
        ),
    )


def insert_repository_alias(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    package: str,
    github_key: str,
    now: str,
) -> None:
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
            entity_id,
            "npm_registry",
            package,
            github_key,
            "deterministic",
            "npm_registry",
            1,
            now,
            entity_id,
            github_key,
            "npm_registry",
        ),
    )


def optional_weekly_downloads(client: Any, package: str) -> int | None:
    try:
        return download_count(client.downloads(package, "last-week"))
    except Exception:
        return None


def run_npm_backfill(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    client: Any,
    now: str,
    limit: int,
    concurrency: int = 1,
    rate_limit_per_second: float = 0,
) -> dict[str, Any]:
    if concurrency <= 0:
        raise ValueError("concurrency must be positive")
    if limit <= 0:
        return {"completed": 0, "failed": 0, "signals": {}}

    jobs = conn.execute(
        """
        select id, entity_id, reason
        from backfill_jobs
        where run_id = ? and source = 'npm_registry' and status = 'pending'
        order by id
        limit ?
        """,
        (run_id, int(limit)),
    ).fetchall()
    work: list[dict[str, Any]] = []
    limited_client = RateLimitedClient(client, starts_per_second=rate_limit_per_second)
    for job_id, entity_id, reason in jobs:
        try:
            package = package_from_reason(str(reason))
        except Exception as exc:
            work.append({"job_id": job_id, "entity_id": str(entity_id), "error": exc})
            continue
        work.append(
            {
                "job_id": job_id,
                "entity_id": str(entity_id),
                "package": package,
                "canonical_entity": entity_label(conn, str(entity_id)),
                "error": None,
            }
        )

    def collect(job: dict[str, Any]) -> dict[str, Any]:
        if job.get("error") is not None:
            return job
        try:
            package = job["package"]
            package_url = npm_package_url(package)
            metadata = limited_client.package_metadata(package)
            daily_downloads = download_count(limited_client.downloads(package, "last-day"))
            weekly_downloads = optional_weekly_downloads(limited_client, package)
            repository_url = repository_url_from_metadata(metadata if isinstance(metadata, dict) else {})
            github_key = github_key_from_repository(repository_url)
            return {
                **job,
                "package_url": package_url,
                "daily_downloads": daily_downloads,
                "weekly_downloads": weekly_downloads,
                "repository_url": repository_url,
                "github_key": github_key,
            }
        except Exception as exc:
            return {**job, "error": exc}

    results = bounded_parallel_map(work, collect, concurrency=concurrency)
    completed = 0
    failed = 0
    signals: dict[str, dict[str, float | str]] = {}
    for result in results:
        if result.get("error") is not None:
            failed += 1
            conn.execute(
                "update backfill_jobs set status = 'failed', completed_at = ?, result_ref = ? where id = ?",
                (now, str(result["error"])[:500], result["job_id"]),
            )
            conn.commit()
            continue
        try:
            package = result["package"]
            canonical_entity = result["canonical_entity"]
            package_url = result["package_url"]
            daily_downloads = result["daily_downloads"]
            weekly_downloads = result["weekly_downloads"]
            repository_url = result["repository_url"]
            github_key = result["github_key"]

            insert_npm_evidence(
                conn,
                entity_id=result["entity_id"],
                canonical_entity=canonical_entity,
                package=package,
                metric_name="daily_downloads",
                metric_value=daily_downloads,
                run_id=run_id,
                now=now,
                raw_url_or_ref=downloads_ref(package, "last-day"),
                historical_safety="as_of_safe",
                note="npm daily downloads backfill",
            )
            entity_signals: dict[str, float | str] = {"daily_downloads": float(daily_downloads)}
            if weekly_downloads is not None:
                insert_npm_evidence(
                    conn,
                    entity_id=result["entity_id"],
                    canonical_entity=canonical_entity,
                    package=package,
                    metric_name="downloads_7d",
                    metric_value=weekly_downloads,
                    run_id=run_id,
                    now=now,
                    raw_url_or_ref=downloads_ref(package, "last-week"),
                    historical_safety="as_of_safe",
                    note="npm 7d downloads backfill",
                )
                entity_signals["downloads_7d"] = float(weekly_downloads)
            if github_key:
                insert_npm_evidence(
                    conn,
                    entity_id=result["entity_id"],
                    canonical_entity=canonical_entity,
                    package=package,
                    metric_name="npm_repository_link",
                    metric_value=github_key,
                    run_id=run_id,
                    now=now,
                    raw_url_or_ref=repository_url or package_url,
                    historical_safety="partial_as_of",
                    note="npm repository link backfill",
                )
                insert_repository_alias(
                    conn,
                    entity_id=result["entity_id"],
                    package=package,
                    github_key=github_key,
                    now=now,
                )
                entity_signals["repository_link"] = github_key

            conn.execute(
                "update backfill_jobs set status = 'completed', completed_at = ?, result_ref = ? where id = ?",
                (now, package_url, result["job_id"]),
            )
            signals[result["entity_id"]] = entity_signals
            completed += 1
            conn.commit()
        except Exception as exc:
            failed += 1
            conn.execute(
                "update backfill_jobs set status = 'failed', completed_at = ?, result_ref = ? where id = ?",
                (now, str(exc)[:500], result["job_id"]),
            )
            conn.commit()

    return {"completed": completed, "failed": failed, "signals": signals}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run pending npm registry backfill jobs")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--now", default=utc_now())
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        summary = run_npm_backfill(
            conn,
            run_id=args.run_id,
            client=NpmRegistryClient(),
            now=args.now,
            limit=args.limit,
        )
    finally:
        conn.close()
    print(f"completed: {summary['completed']}")
    print(f"failed: {summary['failed']}")


if __name__ == "__main__":
    main()
