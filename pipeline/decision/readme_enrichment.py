from __future__ import annotations

import base64
import json
import os
import sqlite3
import urllib.parse
import urllib.request
from typing import Any

from pipeline.decision.cache import api_cache_key, get_api_cache, put_api_cache, stable_hash
from pipeline.decision.bounded_parallel import bounded_parallel_map
from pipeline.decision.candidate_context import approved_github_alias_key
from pipeline.decision.rate_limit import RateLimitedClient


README_SOURCE = "github_readme"
README_WINDOW = "candidate_context"
MAX_README_CHARS = 8000
MAX_README_PREVIEW_CHARS = 1000


def github_repo_key_from_link(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.startswith("github:"):
        repo = raw.split(":", 1)[1]
    else:
        parsed = urllib.parse.urlparse(raw)
        if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
            return None
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(parts) < 2:
            return None
        repo = f"{parts[0]}/{parts[1]}"
    if "/" not in repo:
        return None
    owner, name = repo.split("/", 1)
    owner = owner.strip().lower()
    name = name.strip().lower().removesuffix(".git")
    if not owner or not name or "/" in name:
        return None
    return f"{owner}/{name}"


class GitHubReadmeClient:
    def __init__(self, token: str | None = None, timeout: int = 30) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN", "")
        self.timeout = timeout

    def get_readme_text(self, repo_key: str) -> str:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repo_key}/readme",
            headers={
                "Accept": "application/vnd.github+json",
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload.get("content") or ""
        encoding = payload.get("encoding") or ""
        if encoding == "base64":
            return base64.b64decode(content).decode("utf-8", errors="replace")
        return str(content)


def _readme_input_hash(repo_key: str) -> str:
    return stable_hash({"repo_key": repo_key, "max_chars": MAX_README_CHARS})


def _readme_cache_key(repo_key: str) -> str:
    return api_cache_key(
        source=README_SOURCE,
        external_id=repo_key,
        window=README_WINDOW,
        input_hash=_readme_input_hash(repo_key),
    )


def read_cached_readme_excerpt(
    conn: sqlite3.Connection,
    *,
    repo_key: str,
) -> dict[str, Any] | None:
    normalized = github_repo_key_from_link(f"github:{repo_key}")
    if not normalized:
        return None
    return get_api_cache(conn, _readme_cache_key(normalized))


def fetch_and_cache_readme_excerpt(
    conn: sqlite3.Connection,
    *,
    client: Any,
    repo_key: str,
) -> dict[str, Any]:
    normalized = github_repo_key_from_link(f"github:{repo_key}")
    if not normalized:
        raise ValueError(f"invalid GitHub repo key: {repo_key!r}")
    cached = read_cached_readme_excerpt(conn, repo_key=normalized)
    if cached:
        return cached

    text = client.get_readme_text(normalized)
    excerpt = str(text or "")[:MAX_README_CHARS]
    response = {
        "repo_key": normalized,
        "excerpt": excerpt,
        "preview": excerpt[:MAX_README_PREVIEW_CHARS],
        "chars": len(excerpt),
    }
    input_hash = _readme_input_hash(normalized)
    put_api_cache(
        conn,
        cache_key=_readme_cache_key(normalized),
        source=README_SOURCE,
        external_id=normalized,
        window=README_WINDOW,
        input_hash=input_hash,
        response=response,
        status="ok",
    )
    return response


def _candidate_repo_keys(conn: sqlite3.Connection, run_id: str) -> list[str]:
    rows = conn.execute(
        """
        select e.entity_id, e.canonical_key
        from potential_candidates pc
        join entities e on e.entity_id = pc.entity_id
        where pc.run_id = ?
        union
        select e.entity_id, e.canonical_key
        from edge_watch_candidates ew
        join entities e on e.entity_id = ew.entity_id
        where ew.run_id = ?
        order by 1, 2
        """,
        (run_id, run_id),
    ).fetchall()
    repo_keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        canonical_key = str(row[1] or "")
        alias_key = approved_github_alias_key(conn, str(row[0]), canonical_key)
        repo_key = github_repo_key_from_link(canonical_key) or github_repo_key_from_link(alias_key)
        if not repo_key or repo_key in seen:
            continue
        seen.add(repo_key)
        repo_keys.append(repo_key)
    return repo_keys


def enrich_candidate_readmes(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    client: Any,
    limit: int,
    concurrency: int = 1,
    rate_limit_per_second: float = 0,
) -> dict[str, int]:
    if concurrency <= 0:
        raise ValueError("concurrency must be positive")
    max_items = max(0, int(limit or 0))
    summary = {"fetched": 0, "cached": 0, "skipped": 0}
    if max_items <= 0:
        return summary

    missing: list[str] = []
    limited_client = RateLimitedClient(client, starts_per_second=rate_limit_per_second)
    for repo_key in _candidate_repo_keys(conn, run_id):
        if summary["cached"] + len(missing) >= max_items:
            summary["skipped"] += 1
            continue
        if read_cached_readme_excerpt(conn, repo_key=repo_key):
            summary["cached"] += 1
            continue
        missing.append(repo_key)

    def collect(repo_key: str) -> dict[str, Any]:
        try:
            text = limited_client.get_readme_text(repo_key)
            excerpt = str(text or "")[:MAX_README_CHARS]
            return {
                "repo_key": repo_key,
                "response": {
                    "repo_key": repo_key,
                    "excerpt": excerpt,
                    "preview": excerpt[:MAX_README_PREVIEW_CHARS],
                    "chars": len(excerpt),
                },
                "error": None,
            }
        except Exception as exc:
            return {"repo_key": repo_key, "response": None, "error": exc}

    for result in bounded_parallel_map(missing, collect, concurrency=concurrency):
        if result["error"] is not None:
            summary["skipped"] += 1
            continue
        repo_key = result["repo_key"]
        put_api_cache(
            conn,
            cache_key=_readme_cache_key(repo_key),
            source=README_SOURCE,
            external_id=repo_key,
            window=README_WINDOW,
            input_hash=_readme_input_hash(repo_key),
            response=result["response"],
            status="ok",
        )
        summary["fetched"] += 1
    return summary
