from __future__ import annotations

import base64
import ipaddress
import json
import os
import sqlite3
import urllib.parse
import urllib.request
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Callable, Iterator

from pipeline.decision.cache import (
    api_cache_key,
    get_api_cache,
    put_api_cache,
    stable_hash,
)
from pipeline.decision.readme_enrichment import (
    MAX_README_CHARS,
    fetch_and_cache_readme_excerpt,
    github_repo_key_from_link,
)
from pipeline.decision.layer2_tool_registry import (
    ToolCandidateContext,
    ToolSpec,
    registry_by_name,
)


ToolFn = Callable[[dict[str, Any]], dict[str, Any]]

GITHUB_FILE_SOURCE = "scoring_github_file"
HOMEPAGE_SOURCE = "scoring_homepage_excerpt"
WEB_SEARCH_SOURCE = "scoring_web_search"
TOOL_WINDOW = "scoring_investigator"
TOOL_REGISTRY_VERSION = "layer2-tools-v1"

_REPO_SEGMENT_PATTERN = r"[^/\\\s]+"
_REPO_KEY_PATTERN = rf"^{_REPO_SEGMENT_PATTERN}/{_REPO_SEGMENT_PATTERN}$"
_GITHUB_MANIFEST_PATTERN = (
    r"^(?!(?:[Dd][Oo][Cc][Ss]|[Ee][Xx][Aa][Mm][Pp][Ll][Ee][Ss])/)"
    r"(?:(?!\.{1,2}(?:/|$))[^/\\]+/){0,2}"
    r"(?:[Pp][Aa][Cc][Kk][Aa][Gg][Ee]\.[Jj][Ss][Oo][Nn]|"
    r"[Pp][Yy][Pp][Rr][Oo][Jj][Ee][Cc][Tt]\.[Tt][Oo][Mm][Ll]|"
    r"[Cc][Aa][Rr][Gg][Oo]\.[Tt][Oo][Mm][Ll]|"
    r"[Gg][Oo]\.[Mm][Oo][Dd]|"
    r"[Dd][Ee][Nn][Oo]\.[Jj][Ss][Oo][Nn]|"
    r"[Rr][Ee][Qq][Uu][Ii][Rr][Ee][Mm][Ee][Nn][Tt][Ss]\.[Tt][Xx][Tt]|"
    r"[Rr][Ee][Aa][Dd][Mm][Ee][^/\\]*)$"
)
_GITHUB_DOC_PATTERN = (
    r"^(?:[Dd][Oo][Cc][Ss]|[Ee][Xx][Aa][Mm][Pp][Ll][Ee][Ss])/"
    r"(?:(?!\.{1,2}(?:/|$))[^/\\]+/)?"
    r"(?:[Ii][Nn][Dd][Ee][Xx]|[Rr][Ee][Aa][Dd][Mm][Ee])\.[Mm][Dd]$"
)


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
        conn: sqlite3.Connection | None = None,
        *,
        connection_factory: Callable[[], sqlite3.Connection] | None = None,
        decision_run_id: str,
        readme_client: Any | None = None,
        github_file_client: Any | None = None,
        page_client: Any | None = None,
        web_search_client: Any | None = None,
        limits: InvestigatorToolLimits | None = None,
        family_limiters: dict[str, Any] | None = None,
    ) -> None:
        if conn is None and connection_factory is None:
            raise ValueError("conn or connection_factory is required")
        self.conn = conn
        self.connection_factory = connection_factory
        self.decision_run_id = decision_run_id
        self.readme_client = readme_client
        self.github_file_client = github_file_client
        self.page_client = page_client
        self.web_search_client = web_search_client
        self.limits = limits or InvestigatorToolLimits()
        self.family_limiters = family_limiters or {}
        self._tool_specs = self._build_tool_specs()

    @property
    def registry_version(self) -> str:
        return TOOL_REGISTRY_VERSION

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        if self.connection_factory is None:
            if self.conn is None:
                raise RuntimeError("investigator tool connection is not configured")
            yield self.conn
            return
        conn = self.connection_factory()
        try:
            yield conn
        finally:
            conn.close()

    def _external_limit(self, family: str) -> Any:
        return self.family_limiters.get(family) or nullcontext()

    def available_specs(
        self,
        candidate: ToolCandidateContext | dict[str, Any] | None = None,
    ) -> dict[str, ToolSpec]:
        context = _candidate_context(candidate)
        if context is None:
            return dict(self._tool_specs)
        return {
            name: spec
            for name, spec in self._tool_specs.items()
            if spec.is_available(context)
        }

    def model_tool_specs(
        self,
        candidate: ToolCandidateContext | dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return [
            spec.model_projection()
            for spec in self.available_specs(candidate).values()
        ]

    def tool_fingerprint_specs(
        self,
        candidate: ToolCandidateContext | dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return [
            spec.fingerprint_projection()
            for spec in self.available_specs(candidate).values()
        ]

    def active_tool_versions(
        self,
        candidate: ToolCandidateContext | dict[str, Any] | None = None,
    ) -> tuple[str, ...]:
        return tuple(
            f"{spec.name}@{spec.version}"
            for spec in self.available_specs(candidate).values()
        )

    def available_tools(
        self,
        candidate: ToolCandidateContext | dict[str, Any] | None = None,
    ) -> dict[str, ToolFn]:
        return {
            name: spec.execute
            for name, spec in self.available_specs(candidate).items()
        }

    def _build_tool_specs(self) -> dict[str, ToolSpec]:
        repo_properties = _repo_argument_properties()
        repo_shape = _repo_argument_shape()
        specs = [
            ToolSpec(
                name="read_evidence_rows",
                version="1",
                description=(
                    "Read bounded persisted evidence rows for one candidate entity. "
                    "Use only when the initial packet says retrievable evidence was omitted."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "entity_id": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 200,
                        }
                    },
                    "required": ["entity_id"],
                    "additionalProperties": False,
                },
                family="evidence",
                cost="local_low",
                executor=self.read_evidence_rows,
                availability=lambda candidate: candidate.has_retrievable_evidence,
                timeout_seconds=5,
                max_result_tokens=1800,
                cache_policy="decision_run_snapshot",
                concurrency_key="sqlite_read",
                max_in_flight=4,
                starts_per_second=20.0,
                result_projector=_project_evidence_rows,
            ),
            ToolSpec(
                name="fetch_github_readme",
                version="1",
                description=(
                    "Fetch the canonical README for a verified GitHub repository. "
                    "Repository identity must already be resolved."
                ),
                input_schema={
                    "type": "object",
                    "properties": repo_properties,
                    "anyOf": repo_shape,
                    "additionalProperties": False,
                },
                family="github",
                cost="remote_cached_medium",
                executor=self.fetch_github_readme,
                availability=lambda candidate: bool(candidate.repo_key)
                and candidate.needs_technical_evidence,
                timeout_seconds=int(getattr(self.readme_client, "timeout", 30)),
                max_result_tokens=2000,
                cache_policy="api_cache_by_repo",
                concurrency_key="github",
                max_in_flight=5,
                starts_per_second=2.0,
                result_projector=_project_github_readme,
            ),
            ToolSpec(
                name="fetch_github_file",
                version="1",
                description=(
                    "Fetch one allowlisted GitHub README, manifest, docs index, or examples "
                    "index file from an already resolved repository."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        **repo_properties,
                        "path": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 300,
                            "oneOf": [
                                {
                                    "type": "string",
                                    "pattern": _GITHUB_MANIFEST_PATTERN,
                                },
                                {"type": "string", "pattern": _GITHUB_DOC_PATTERN},
                            ],
                        },
                    },
                    "required": ["path"],
                    "anyOf": repo_shape,
                    "additionalProperties": False,
                },
                family="github",
                cost="remote_cached_medium",
                executor=self.fetch_github_file,
                availability=lambda candidate: bool(candidate.repo_key)
                and candidate.needs_technical_evidence,
                timeout_seconds=int(
                    getattr(self.github_file_client, "timeout", 30)
                ),
                max_result_tokens=1600,
                cache_policy="api_cache_by_repo_path",
                concurrency_key="github",
                max_in_flight=5,
                starts_per_second=2.0,
                result_projector=_project_github_file,
            ),
            ToolSpec(
                name="fetch_homepage_or_docs",
                version="1",
                description=(
                    "Fetch bounded text from a public canonical homepage or documentation URL. "
                    "Private and local network targets are rejected by the host."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "minLength": 8,
                            "maxLength": 1000,
                            "pattern": r"^[Hh][Tt][Tt][Pp][Ss]?://",
                        }
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
                family="homepage",
                cost="remote_cached_medium",
                executor=self.fetch_homepage_or_docs,
                availability=lambda candidate: _safe_candidate_url(candidate)
                and candidate.needs_product_description,
                timeout_seconds=int(getattr(self.page_client, "timeout", 20)),
                max_result_tokens=1600,
                cache_policy="api_cache_by_normalized_url",
                concurrency_key="homepage",
                max_in_flight=4,
                starts_per_second=2.0,
                result_projector=_project_homepage,
            ),
        ]
        if self.web_search_client is not None:
            specs.append(
                ToolSpec(
                    name="web_search",
                    version="1",
                    description=(
                        "Search public web evidence only as a last resort for unresolved identity, "
                        "missing first-party material, or independent momentum verification."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 500,
                            },
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": max(1, self.limits.max_web_results),
                            },
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                    family="web_search",
                    cost="remote_high",
                    executor=self.web_search,
                    availability=lambda candidate: candidate.unresolved_identity
                    or candidate.missing_first_party_material
                    or candidate.needs_momentum_verification,
                    timeout_seconds=int(
                        getattr(self.web_search_client, "timeout", 45)
                    ),
                    max_result_tokens=1500,
                    cache_policy="api_cache_by_query",
                    concurrency_key="web_search",
                    max_in_flight=2,
                    starts_per_second=1.0,
                    result_projector=_project_web_search,
                )
            )
        return dict(registry_by_name(tuple(specs)))

    def read_evidence_rows(self, arguments: dict[str, Any]) -> dict[str, Any]:
        entity_id = str(arguments.get("entity_id") or "")
        if not entity_id:
            return {"status": "rejected", "error": "entity_id is required", "rows": []}
        with self._connection() as conn:
            rows = conn.execute(
                """
                select source, event_at, metric_name, metric_value, family,
                       signal_label, note, raw_url_or_ref
                from evidence_rows
                where run_id = ? and entity_id = ?
                order by event_at desc, id desc
                limit ?
                """,
                (
                    self.decision_run_id,
                    entity_id,
                    max(1, self.limits.max_evidence_rows),
                ),
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
        repo_key = _repo_key_from_arguments(arguments)
        if not repo_key:
            return {"status": "rejected", "error": "valid repo_key is required"}
        if self.readme_client is None:
            return {"status": "unavailable", "error": "readme client is not configured"}
        with self._external_limit("github"):
            with self._connection() as conn:
                response = fetch_and_cache_readme_excerpt(
                    conn, client=self.readme_client, repo_key=repo_key
                )
        return {
            "status": "ok",
            **response,
            "truncated": int(response.get("chars") or 0) >= MAX_README_CHARS,
        }

    def fetch_github_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        repo_key = _repo_key_from_arguments(arguments)
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
        with self._external_limit("github"):
            with self._connection() as conn:
                cached = get_api_cache(conn, cache_key)
                if cached:
                    return cached
                text = str(self.github_file_client.get_file_text(repo_key, path) or "")
                response = {
                    "status": "ok",
                    "repo_key": repo_key,
                    "path": path,
                    "excerpt": text[: self.limits.max_github_file_chars],
                    "chars": min(len(text), self.limits.max_github_file_chars),
                    "truncated": len(text) > self.limits.max_github_file_chars,
                }
                put_api_cache(
                    conn,
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
        with self._external_limit("homepage"):
            with self._connection() as conn:
                cached = get_api_cache(conn, cache_key)
                if cached:
                    return cached
                text = str(self.page_client.fetch_text(normalized_url) or "")
                response = {
                    "status": "ok",
                    "url": normalized_url,
                    "excerpt": text[: self.limits.max_homepage_chars],
                    "chars": min(len(text), self.limits.max_homepage_chars),
                    "truncated": len(text) > self.limits.max_homepage_chars,
                }
                put_api_cache(
                    conn,
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
        with self._external_limit("web_search"):
            with self._connection() as conn:
                cached = get_api_cache(conn, cache_key)
                if cached:
                    return cached
                try:
                    raw_results = self.web_search_client.search(query, limit=limit)
                except TypeError:
                    raw_results = self.web_search_client.search(
                        query=query, max_results=limit
                    )
                normalized_results = _normalize_search_results(raw_results, limit)
                response = {
                    "status": "ok",
                    "query": query,
                    "results": normalized_results,
                    "truncated": _raw_search_result_count(raw_results) > len(
                        normalized_results
                    ),
                }
                put_api_cache(
                    conn,
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


def _repo_key_from_arguments(arguments: dict[str, Any]) -> str | None:
    candidates: list[str] = []
    owner = str(arguments.get("owner") or "").strip()
    repo = str(arguments.get("repo") or "").strip()
    if owner and repo and "/" not in repo:
        candidates.append(f"{owner}/{repo}")
    for key in ["repo_key", "repo_full_name", "full_name", "repo"]:
        value = str(arguments.get(key) or "").strip()
        if value:
            candidates.append(value)
    for key in ["url", "github_url", "html_url"]:
        value = str(arguments.get(key) or "").strip()
        if value:
            candidates.append(value)
    for candidate in candidates:
        repo_key = github_repo_key_from_link(candidate)
        if not repo_key:
            repo_key = github_repo_key_from_link(f"github:{candidate}")
        if repo_key:
            return repo_key
    return None


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


def _raw_search_result_count(raw_results: Any) -> int:
    if isinstance(raw_results, dict) and isinstance(raw_results.get("results"), list):
        return len(raw_results["results"])
    if isinstance(raw_results, list):
        return len(raw_results)
    return 1


def _candidate_context(
    candidate: ToolCandidateContext | dict[str, Any] | None,
) -> ToolCandidateContext | None:
    if candidate is None:
        return None
    if isinstance(candidate, ToolCandidateContext):
        return candidate
    return ToolCandidateContext.from_mapping(candidate)


def _repo_argument_properties() -> dict[str, Any]:
    segment = {
        "type": "string",
        "minLength": 1,
        "maxLength": 200,
        "pattern": rf"^{_REPO_SEGMENT_PATTERN}$",
    }
    full_name = {
        "type": "string",
        "minLength": 3,
        "maxLength": 400,
        "pattern": _REPO_KEY_PATTERN,
    }
    github_url = {
        "type": "string",
        "minLength": 12,
        "maxLength": 1000,
        "pattern": (
            r"^[Hh][Tt][Tt][Pp][Ss]?://"
            r"[Gg][Ii][Tt][Hh][Uu][Bb]\.[Cc][Oo][Mm]/[^/\s]+/[^/\s#?]+"
        ),
    }
    return {
        "owner": dict(segment),
        "repo": {
            "type": "string",
            "minLength": 1,
            "maxLength": 400,
        },
        "repo_key": dict(full_name),
        "repo_full_name": dict(full_name),
        "full_name": dict(full_name),
        "url": dict(github_url),
        "github_url": dict(github_url),
        "html_url": dict(github_url),
    }


def _repo_argument_shape() -> list[dict[str, Any]]:
    return [
        {"type": "object", "required": ["owner", "repo"]},
        {
            "type": "object",
            "properties": {"repo": {"type": "string", "pattern": _REPO_KEY_PATTERN}},
            "required": ["repo"],
        },
        *(
            {"type": "object", "required": [key]}
            for key in [
                "repo_key",
                "repo_full_name",
                "full_name",
                "url",
                "github_url",
                "html_url",
            ]
        ),
    ]


def _safe_candidate_url(candidate: ToolCandidateContext) -> bool:
    if not candidate.canonical_url:
        return False
    ok, _reason = validate_public_http_url(candidate.canonical_url)
    return ok


def _bounded_excerpt(text: Any, *, max_chars: int) -> tuple[str, bool]:
    raw = str(text or "")
    return raw[:max_chars], len(raw) > max_chars


def _observation(
    *,
    observation_id: str,
    tool: str,
    status: str,
    provenance: dict[str, Any],
    facts: dict[str, Any],
    excerpt: str,
    truncated: bool,
    relevant_axes: list[str],
) -> dict[str, Any]:
    return {
        "observation_id": str(observation_id),
        "tool": tool,
        "status": status,
        "trust": "external_untrusted",
        "provenance": provenance,
        "facts": facts,
        "excerpt": excerpt,
        "truncated": bool(truncated),
        "relevant_axes": relevant_axes,
    }


def _project_evidence_rows(
    result: dict[str, Any], observation_id: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    rows = result.get("rows") if isinstance(result.get("rows"), list) else []
    excerpt, truncated = _bounded_excerpt(
        json.dumps(rows, ensure_ascii=False, sort_keys=True), max_chars=7200
    )
    return _observation(
        observation_id=observation_id,
        tool="read_evidence_rows",
        status=str(result.get("status") or "error"),
        provenance={
            "entity_id": str(arguments.get("entity_id") or ""),
            "sources": sorted(
                {
                    str(row.get("raw_url_or_ref") or row.get("source") or "")
                    for row in rows
                    if isinstance(row, dict)
                }
                - {""}
            ),
        },
        facts={"row_count": len(rows)},
        excerpt=excerpt,
        truncated=truncated,
        relevant_axes=["momentum", "product_market_fit", "confidence"],
    )


def _project_github_readme(
    result: dict[str, Any], observation_id: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    excerpt, truncated = _bounded_excerpt(result.get("excerpt"), max_chars=8000)
    return _observation(
        observation_id=observation_id,
        tool="fetch_github_readme",
        status=str(result.get("status") or "error"),
        provenance={
            "repo_key": str(
                result.get("repo_key")
                or _repo_key_from_arguments(arguments)
                or ""
            )
        },
        facts={"chars": int(result.get("chars") or len(excerpt))},
        excerpt=excerpt,
        truncated=truncated or bool(result.get("truncated")),
        relevant_axes=[
            "workflow_shift",
            "technical_substance",
            "product_market_fit",
            "confidence",
        ],
    )


def _project_github_file(
    result: dict[str, Any], observation_id: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    excerpt, truncated = _bounded_excerpt(result.get("excerpt"), max_chars=6400)
    return _observation(
        observation_id=observation_id,
        tool="fetch_github_file",
        status=str(result.get("status") or "error"),
        provenance={
            "repo_key": str(result.get("repo_key") or _repo_key_from_arguments(arguments) or ""),
            "path": str(result.get("path") or arguments.get("path") or ""),
        },
        facts={"chars": int(result.get("chars") or len(excerpt))},
        excerpt=excerpt,
        truncated=truncated or bool(result.get("truncated")),
        relevant_axes=["workflow_shift", "technical_substance", "confidence"],
    )


def _project_homepage(
    result: dict[str, Any], observation_id: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    excerpt, truncated = _bounded_excerpt(result.get("excerpt"), max_chars=6400)
    return _observation(
        observation_id=observation_id,
        tool="fetch_homepage_or_docs",
        status=str(result.get("status") or "error"),
        provenance={"url": str(result.get("url") or arguments.get("url") or "")},
        facts={"chars": int(result.get("chars") or len(excerpt))},
        excerpt=excerpt,
        truncated=truncated or bool(result.get("truncated")),
        relevant_axes=["workflow_shift", "product_market_fit", "confidence"],
    )


def _project_web_search(
    result: dict[str, Any], observation_id: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    results = result.get("results") if isinstance(result.get("results"), list) else []
    excerpt, truncated = _bounded_excerpt(
        json.dumps(results, ensure_ascii=False, sort_keys=True), max_chars=6000
    )
    return _observation(
        observation_id=observation_id,
        tool="web_search",
        status=str(result.get("status") or "error"),
        provenance={
            "query": str(result.get("query") or arguments.get("query") or ""),
            "urls": [
                str(item.get("url") or "")
                for item in results
                if isinstance(item, dict) and item.get("url")
            ],
        },
        facts={"result_count": len(results)},
        excerpt=excerpt,
        truncated=truncated or bool(result.get("truncated")),
        relevant_axes=["momentum", "product_market_fit", "confidence"],
    )
