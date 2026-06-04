from __future__ import annotations

import base64
import ipaddress
import json
import os
import sqlite3
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Callable

from pipeline.decision.cache import (
    api_cache_key,
    get_api_cache,
    put_api_cache,
    stable_hash,
)
from pipeline.decision.readme_enrichment import (
    fetch_and_cache_readme_excerpt,
    github_repo_key_from_link,
)


ToolFn = Callable[[dict[str, Any]], dict[str, Any]]

GITHUB_FILE_SOURCE = "scoring_github_file"
HOMEPAGE_SOURCE = "scoring_homepage_excerpt"
WEB_SEARCH_SOURCE = "scoring_web_search"
TOOL_WINDOW = "scoring_investigator"


@dataclass(frozen=True)
class InvestigatorToolLimits:
    max_evidence_rows: int = 80
    max_github_file_chars: int = 6000
    max_homepage_chars: int = 6000
    max_web_results: int = 5


class GitHubFileClient:
    def __init__(self, token: str | None = None, timeout: int = 30) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN", "")
        self.timeout = timeout

    def get_file_text(self, repo_key: str, path: str) -> str:
        quoted_path = urllib.parse.quote(path.strip("/"))
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repo_key}/contents/{quoted_path}",
            headers={
                "Accept": "application/vnd.github+json",
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = str(payload.get("content") or "")
        if str(payload.get("encoding") or "") == "base64":
            return base64.b64decode(content).decode("utf-8", errors="replace")
        return content


class PageFetchClient:
    def __init__(self, timeout: int = 20, max_bytes: int = 200_000) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes

    def fetch_text(self, url: str) -> str:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "text/html,text/plain,application/xhtml+xml",
                "User-Agent": "HeroRadarScoringInvestigator/1.0",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            content_type = str(response.headers.get("Content-Type") or "").lower()
            if not any(
                allowed in content_type
                for allowed in ["text/html", "text/plain", "application/xhtml+xml"]
            ):
                raise ValueError(f"unsupported content type: {content_type}")
            raw = response.read(self.max_bytes + 1)
        return raw[: self.max_bytes].decode("utf-8", errors="replace")


class ScoringInvestigatorTools:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        decision_run_id: str,
        readme_client: Any | None = None,
        github_file_client: Any | None = None,
        page_client: Any | None = None,
        web_search_client: Any | None = None,
        limits: InvestigatorToolLimits | None = None,
    ) -> None:
        self.conn = conn
        self.decision_run_id = decision_run_id
        self.readme_client = readme_client
        self.github_file_client = github_file_client
        self.page_client = page_client
        self.web_search_client = web_search_client
        self.limits = limits or InvestigatorToolLimits()

    def available_tools(self) -> dict[str, ToolFn]:
        return {
            "read_evidence_rows": self.read_evidence_rows,
            "fetch_github_readme": self.fetch_github_readme,
            "fetch_github_file": self.fetch_github_file,
            "fetch_homepage_or_docs": self.fetch_homepage_or_docs,
            "web_search": self.web_search,
        }

    def read_evidence_rows(self, arguments: dict[str, Any]) -> dict[str, Any]:
        entity_id = str(arguments.get("entity_id") or "")
        if not entity_id:
            return {"status": "rejected", "error": "entity_id is required", "rows": []}
        rows = self.conn.execute(
            """
            select source, event_at, metric_name, metric_value, family,
                   signal_label, note, raw_url_or_ref
            from evidence_rows
            where run_id = ? and entity_id = ?
            order by event_at desc, id desc
            limit ?
            """,
            (self.decision_run_id, entity_id, max(1, self.limits.max_evidence_rows)),
        ).fetchall()
        return {
            "status": "ok",
            "rows": [
                {
                    "source": row[0],
                    "event_at": row[1],
                    "metric_name": row[2],
                    "metric_value": row[3],
                    "family": row[4],
                    "signal_label": row[5],
                    "note": row[6],
                    "raw_url_or_ref": row[7],
                }
                for row in rows
            ],
        }

    def fetch_github_readme(self, arguments: dict[str, Any]) -> dict[str, Any]:
        repo_key = github_repo_key_from_link(f"github:{arguments.get('repo_key')}")
        if not repo_key:
            return {"status": "rejected", "error": "valid repo_key is required"}
        if self.readme_client is None:
            return {"status": "unavailable", "error": "readme client is not configured"}
        response = fetch_and_cache_readme_excerpt(
            self.conn, client=self.readme_client, repo_key=repo_key
        )
        return {"status": "ok", **response}

    def fetch_github_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        repo_key = github_repo_key_from_link(f"github:{arguments.get('repo_key')}")
        path = str(arguments.get("path") or "").strip().strip("/")
        if not repo_key:
            return {"status": "rejected", "error": "valid repo_key is required"}
        if not _is_allowed_github_path(path):
            return {"status": "rejected", "error": "github path is not allowed"}
        if self.github_file_client is None:
            return {
                "status": "unavailable",
                "error": "github file client is not configured",
            }
        cache_key, input_hash = _tool_cache_key(
            source=GITHUB_FILE_SOURCE,
            external_id=f"{repo_key}:{path}",
            payload={
                "repo_key": repo_key,
                "path": path,
                "max_chars": self.limits.max_github_file_chars,
            },
        )
        cached = get_api_cache(self.conn, cache_key)
        if cached:
            return cached
        text = str(self.github_file_client.get_file_text(repo_key, path) or "")
        response = {
            "status": "ok",
            "repo_key": repo_key,
            "path": path,
            "excerpt": text[: self.limits.max_github_file_chars],
            "chars": min(len(text), self.limits.max_github_file_chars),
        }
        put_api_cache(
            self.conn,
            cache_key=cache_key,
            source=GITHUB_FILE_SOURCE,
            external_id=f"{repo_key}:{path}",
            window=TOOL_WINDOW,
            input_hash=input_hash,
            response=response,
        )
        return response

    def fetch_homepage_or_docs(self, arguments: dict[str, Any]) -> dict[str, Any]:
        url = str(arguments.get("url") or "").strip()
        ok, reason = validate_public_http_url(url)
        if not ok:
            return {"status": "rejected", "error": reason}
        if self.page_client is None:
            return {"status": "unavailable", "error": "page client is not configured"}
        normalized_url = _normalize_url(url)
        cache_key, input_hash = _tool_cache_key(
            source=HOMEPAGE_SOURCE,
            external_id=normalized_url,
            payload={
                "url": normalized_url,
                "max_chars": self.limits.max_homepage_chars,
            },
        )
        cached = get_api_cache(self.conn, cache_key)
        if cached:
            return cached
        text = str(self.page_client.fetch_text(normalized_url) or "")
        response = {
            "status": "ok",
            "url": normalized_url,
            "excerpt": text[: self.limits.max_homepage_chars],
            "chars": min(len(text), self.limits.max_homepage_chars),
        }
        put_api_cache(
            self.conn,
            cache_key=cache_key,
            source=HOMEPAGE_SOURCE,
            external_id=normalized_url,
            window=TOOL_WINDOW,
            input_hash=input_hash,
            response=response,
        )
        return response

    def web_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return {"status": "rejected", "error": "query is required"}
        if self.web_search_client is None:
            return {"status": "unavailable", "error": "web search client is not configured"}
        limit = max(
            1,
            min(
                self.limits.max_web_results,
                int(arguments.get("limit") or self.limits.max_web_results),
            ),
        )
        cache_key, input_hash = _tool_cache_key(
            source=WEB_SEARCH_SOURCE,
            external_id=query,
            payload={"query": query, "limit": limit},
        )
        cached = get_api_cache(self.conn, cache_key)
        if cached:
            return cached
        try:
            raw_results = self.web_search_client.search(query, limit=limit)
        except TypeError:
            raw_results = self.web_search_client.search(
                query=query, max_results=limit
            )
        response = {
            "status": "ok",
            "query": query,
            "results": _normalize_search_results(raw_results, limit),
        }
        put_api_cache(
            self.conn,
            cache_key=cache_key,
            source=WEB_SEARCH_SOURCE,
            external_id=query,
            window=TOOL_WINDOW,
            input_hash=input_hash,
            response=response,
        )
        return response


def validate_public_http_url(url: str) -> tuple[bool, str]:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return False, "url must use http or https"
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False, "url host is required"
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return False, "local hostnames are not allowed"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True, ""
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return False, "private or local IP addresses are not allowed"
    return True, ""


def _tool_cache_key(
    *,
    source: str,
    external_id: str,
    payload: dict[str, Any],
) -> tuple[str, str]:
    input_hash = stable_hash({"source": source, **payload})
    return (
        api_cache_key(
            source=source,
            external_id=external_id,
            window=TOOL_WINDOW,
            input_hash=input_hash,
        ),
        input_hash,
    )


def _is_allowed_github_path(path: str) -> bool:
    if not path or path.startswith("/") or "\\" in path:
        return False
    posix = PurePosixPath(path)
    if any(part in {"", ".", ".."} for part in posix.parts):
        return False
    parts = [part.lower() for part in posix.parts]
    if len(parts) > 3:
        return False
    basename = parts[-1]
    allowed_basenames = {
        "package.json",
        "pyproject.toml",
        "cargo.toml",
        "go.mod",
        "deno.json",
        "requirements.txt",
    }
    if basename in allowed_basenames or basename.startswith("readme"):
        return True
    return parts[0] in {"docs", "examples"} and basename in {
        "index.md",
        "readme.md",
    }


def _normalize_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path or "/",
            "",
            parsed.query,
            "",
        )
    )


def _normalize_search_results(raw_results: Any, limit: int) -> list[dict[str, Any]]:
    if isinstance(raw_results, dict):
        if "results" in raw_results and isinstance(raw_results["results"], list):
            raw_items = raw_results["results"]
        else:
            raw_items = [raw_results]
    elif isinstance(raw_results, list):
        raw_items = raw_results
    else:
        raw_items = [{"content": str(raw_results)}]
    normalized: list[dict[str, Any]] = []
    for item in raw_items[:limit]:
        if isinstance(item, dict):
            normalized.append(
                {
                    "title": str(item.get("title") or "")[:200],
                    "url": str(item.get("url") or "")[:500],
                    "snippet": str(
                        item.get("snippet")
                        or item.get("content")
                        or item.get("description")
                        or ""
                    )[:1000],
                }
            )
        else:
            normalized.append({"title": "", "url": "", "snippet": str(item)[:1000]})
    return normalized
