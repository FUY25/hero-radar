from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from pipeline.decision.layer2_models import LEVEL_RANK
from pipeline.decision.schema import to_json, utc_now


DEFAULT_DEEPDIVE_PROMPT_VERSION = "layer2-deepdive-v1"


PLAN_SYSTEM_PROMPT = """
You are planning a bounded Hero Radar project deepdive.
Return strict JSON with tool_requests: an array of {name, arguments}.
Use only tools that are listed in available_tools. Respect max_tool_calls and
all per-tool-family budgets.
"""


SYNTHESIS_SYSTEM_PROMPT = """
You are writing a bounded Hero Radar project deepdive.
Use only candidate context, score context, and tool_trace.
Return strict JSON with summary, why_now, what_changed, evidence,
adoption_path, risks, open_questions, recommended_action.
"""


ToolFn = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class DeepdiveLimits:
    max_tool_calls: int = 20
    max_web_search_calls: int = 3
    max_repo_tree_calls: int = 2
    max_repo_file_calls: int = 8
    max_page_fetch_calls: int = 6
    max_hn_thread_calls: int = 3
    max_x_context_calls: int = 5
    max_tool_result_chars: int = 6000


def select_deepdives(
    scored: list[dict[str, Any]],
    *,
    max_deepdives: int,
    min_l2_score: float,
) -> list[dict[str, Any]]:
    eligible = [
        row for row in scored if float(row.get("l2_score", 0)) >= min_l2_score
    ]
    return sorted(
        eligible,
        key=lambda row: (
            -float(row.get("l2_score", 0)),
            -LEVEL_RANK.get(row["group"].level, 0),
            row["group"].canonical_name.lower(),
        ),
    )[: max(0, int(max_deepdives))]


def run_deepdives(
    conn: sqlite3.Connection,
    *,
    feed_run_id: str,
    scored: list[dict[str, Any]],
    provider: Any,
    max_deepdives: int,
    min_l2_score: float,
    tools: dict[str, ToolFn] | None = None,
    limits: DeepdiveLimits | None = None,
    prompt_version: str = DEFAULT_DEEPDIVE_PROMPT_VERSION,
) -> list[dict[str, Any]]:
    active_tools = tools or {}
    active_limits = limits or DeepdiveLimits()
    selected = select_deepdives(
        scored, max_deepdives=max_deepdives, min_l2_score=min_l2_score
    )
    reports: list[dict[str, Any]] = []
    for index, row in enumerate(selected, start=1):
        group = row["group"]
        plan_payload = {
            "group_id": group.group_id,
            "candidate": group.context,
            "score": _score_payload(row),
            "available_tools": sorted(active_tools),
            "limits": {
                "max_tool_calls": active_limits.max_tool_calls,
                "max_web_search_calls": active_limits.max_web_search_calls,
                "max_repo_tree_calls": active_limits.max_repo_tree_calls,
                "max_repo_file_calls": active_limits.max_repo_file_calls,
                "max_page_fetch_calls": active_limits.max_page_fetch_calls,
                "max_hn_thread_calls": active_limits.max_hn_thread_calls,
                "max_x_context_calls": active_limits.max_x_context_calls,
            },
        }
        plan = provider.complete_json(
            task="layer2_deepdive_plan",
            prompt_version=prompt_version,
            input_payload=plan_payload,
            system_prompt=PLAN_SYSTEM_PROMPT,
        )
        tool_trace = _run_tool_plan(plan, active_tools, active_limits)
        synthesis_payload = {
            "group_id": group.group_id,
            "candidate": group.context,
            "score": _score_payload(row),
            "tool_trace": tool_trace,
        }
        response = provider.complete_json(
            task="layer2_deepdive_synthesis",
            prompt_version=prompt_version,
            input_payload=synthesis_payload,
            system_prompt=SYNTHESIS_SYSTEM_PROMPT,
        )
        summary = _validate_report(response)
        cache_key = _cache_key(
            provider.provider_name, provider.model, prompt_version, synthesis_payload
        )
        conn.execute(
            """
            insert or replace into deepdive_reports(
              feed_run_id, group_id, status, summary_json, tool_trace_json,
              provider, model, prompt_version, cache_key, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feed_run_id,
                group.group_id,
                "ok",
                to_json(summary),
                to_json(tool_trace),
                provider.provider_name,
                provider.model,
                prompt_version,
                cache_key,
                utc_now(),
            ),
        )
        conn.execute(
            """
            insert or replace into l2_feed_items(feed_run_id, group_id, section, rank, deepdive_status)
            values (?, ?, ?, ?, ?)
            """,
            (feed_run_id, group.group_id, "today_focus", index, "ok"),
        )
        reports.append({"group": group, "summary": summary, "tool_trace": tool_trace})
    conn.commit()
    return reports


def _run_tool_plan(
    plan: dict[str, Any],
    tools: dict[str, ToolFn],
    limits: DeepdiveLimits,
) -> list[dict[str, Any]]:
    requests = plan.get("tool_requests")
    if not isinstance(requests, list):
        raise ValueError("deepdive plan must include tool_requests array")
    trace: list[dict[str, Any]] = []
    family_counts: dict[str, int] = {}
    total_count = 0
    for request in requests:
        if not isinstance(request, dict):
            continue
        name = str(request.get("name") or "")
        arguments = (
            request.get("arguments") if isinstance(request.get("arguments"), dict) else {}
        )
        family = _tool_family(name)
        if total_count >= max(0, limits.max_tool_calls) or not _within_family_budget(
            family_counts, family, limits
        ):
            trace.append(
                {
                    "tool": name,
                    "arguments": arguments,
                    "status": "budget_exceeded",
                    "result": {},
                }
            )
            continue
        if name not in tools:
            trace.append(
                {
                    "tool": name,
                    "arguments": arguments,
                    "status": "unavailable",
                    "result": {},
                }
            )
            continue
        result = tools[name](arguments)
        total_count += 1
        family_counts[family] = family_counts.get(family, 0) + 1
        trace.append(
            {
                "tool": name,
                "arguments": arguments,
                "status": "ok",
                "result": _trim_result(result, limits.max_tool_result_chars),
            }
        )
    return trace


def _tool_family(name: str) -> str:
    if name == "kimi_web_search":
        return "web_search"
    if name == "fetch_github_tree":
        return "repo_tree"
    if name == "fetch_github_file":
        return "repo_file"
    if name == "fetch_homepage_or_docs":
        return "page_fetch"
    if name == "fetch_hn_thread":
        return "hn_thread"
    if name == "fetch_x_tweet_context":
        return "x_context"
    return "generic"


def _within_family_budget(
    counts: dict[str, int], family: str, limits: DeepdiveLimits
) -> bool:
    caps = {
        "web_search": limits.max_web_search_calls,
        "repo_tree": limits.max_repo_tree_calls,
        "repo_file": limits.max_repo_file_calls,
        "page_fetch": limits.max_page_fetch_calls,
        "hn_thread": limits.max_hn_thread_calls,
        "x_context": limits.max_x_context_calls,
    }
    cap = caps.get(family)
    return cap is None or counts.get(family, 0) < max(0, cap)


def _score_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "l2_score": row.get("l2_score"),
        "primary_reason": row.get("primary_reason"),
        "topic_tags": row.get("topic_tags", []),
        "rationale_short": row.get("rationale_short", ""),
        "caveats": row.get("caveats", []),
    }


def _trim_result(result: dict[str, Any], max_chars: int) -> dict[str, Any]:
    text = to_json(result)
    if len(text) <= max_chars:
        return result
    return {"truncated": True, "text": text[:max_chars]}


def _validate_report(response: dict[str, Any]) -> dict[str, Any]:
    required = [
        "summary",
        "why_now",
        "what_changed",
        "evidence",
        "adoption_path",
        "risks",
        "open_questions",
        "recommended_action",
    ]
    missing = [key for key in required if key not in response]
    if missing:
        raise ValueError(f"deepdive response missing fields: {missing}")
    return {
        "summary": str(response["summary"])[:2000],
        "why_now": str(response["why_now"])[:1200],
        "what_changed": str(response["what_changed"])[:1200],
        "evidence": [str(item)[:400] for item in response["evidence"]][:10],
        "adoption_path": str(response["adoption_path"])[:1200],
        "risks": [str(item)[:400] for item in response["risks"]][:10],
        "open_questions": [str(item)[:400] for item in response["open_questions"]][
            :10
        ],
        "recommended_action": str(response["recommended_action"])[:80],
    }


def _cache_key(
    provider: str, model: str, prompt_version: str, payload: dict[str, Any]
) -> str:
    digest = hashlib.sha256(to_json(payload).encode("utf-8")).hexdigest()
    return f"{provider}:{model}:{prompt_version}:{digest}"


def default_deepdive_tools(
    conn: sqlite3.Connection,
    *,
    decision_run_id: str,
    enable_kimi_web_search: bool = False,
    web_search_client: Any | None = None,
) -> dict[str, ToolFn]:
    def read_evidence_rows(arguments: dict[str, Any]) -> dict[str, Any]:
        entity_id = str(arguments.get("entity_id") or "")
        rows = conn.execute(
            """
            select source, event_at, metric_name, metric_value, family, signal_label, note, raw_url_or_ref
            from evidence_rows
            where run_id = ? and entity_id = ?
            order by event_at desc, id desc
            limit 80
            """,
            (decision_run_id, entity_id),
        ).fetchall()
        return {
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
            ]
        }

    def read_source_items(arguments: dict[str, Any]) -> dict[str, Any]:
        item_ids = [
            int(str(ref).split(":", 1)[1])
            for ref in arguments.get("source_refs") or []
            if str(ref).startswith("item:")
            and str(ref).split(":", 1)[1].isdigit()
        ][:20]
        if not item_ids:
            return {"items": []}
        placeholders = ",".join("?" for _ in item_ids)
        rows = conn.execute(
            f"""
            select id, source, name, url, description, metadata_json, raw_json
            from items
            where id in ({placeholders})
            order by id
            """,
            item_ids,
        ).fetchall()
        return {
            "items": [
                {
                    "id": row[0],
                    "source": row[1],
                    "name": row[2],
                    "url": row[3],
                    "description": row[4],
                    "metadata": _json_loads(row[5], {}),
                    "raw": _json_loads(row[6], {}),
                }
                for row in rows
            ]
        }

    def fetch_cached_readme(arguments: dict[str, Any]) -> dict[str, Any]:
        repo = _normalize_repo(str(arguments.get("repo") or ""))
        row = conn.execute(
            """
            select response_json
            from api_cache
            where source = 'github_readme' and external_id = ? and status = 'ok'
            order by fetched_at desc
            limit 1
            """,
            (repo,),
        ).fetchone()
        return _json_loads(row[0], {}) if row else {"missing": True, "repo": repo}

    def fetch_github_tree(arguments: dict[str, Any]) -> dict[str, Any]:
        repo = _normalize_repo(str(arguments.get("repo") or ""))
        if not repo:
            return {"missing": True, "reason": "repo required"}
        payload = _fetch_json(
            f"https://api.github.com/repos/{repo}/git/trees/HEAD?recursive=1"
        )
        tree = payload.get("tree") if isinstance(payload, dict) else []
        return {
            "repo": repo,
            "paths": [
                {
                    "path": item.get("path"),
                    "type": item.get("type"),
                    "size": item.get("size"),
                }
                for item in tree[:250]
                if isinstance(item, dict)
            ],
        }

    def fetch_github_file(arguments: dict[str, Any]) -> dict[str, Any]:
        repo = _normalize_repo(str(arguments.get("repo") or ""))
        path = str(arguments.get("path") or "").lstrip("/")
        if not repo or not path:
            return {"missing": True, "reason": "repo and path required"}
        url = f"https://raw.githubusercontent.com/{repo}/HEAD/{path}"
        return {"repo": repo, "path": path, "content": _fetch_text(url, max_bytes=40000)}

    def fetch_package_manifest(arguments: dict[str, Any]) -> dict[str, Any]:
        package = str(arguments.get("package") or arguments.get("npm") or "").strip()
        repo = _normalize_repo(str(arguments.get("repo") or ""))
        if package:
            payload = _fetch_json(f"https://registry.npmjs.org/{package}")
            return {
                "package": package,
                "description": payload.get("description"),
                "dist_tags": payload.get("dist-tags"),
                "repository": payload.get("repository"),
            }
        if repo:
            return fetch_github_file({"repo": repo, "path": "package.json"})
        return {"missing": True, "reason": "package or repo required"}

    def fetch_homepage_or_docs(arguments: dict[str, Any]) -> dict[str, Any]:
        url = str(arguments.get("url") or "").strip()
        if not (url.startswith("https://") or url.startswith("http://")):
            return {"missing": True, "reason": "http(s) url required"}
        return {"url": url, "content": _fetch_text(url, max_bytes=60000)}

    def fetch_hn_thread(arguments: dict[str, Any]) -> dict[str, Any]:
        item_id = str(arguments.get("item_id") or "").strip().removeprefix("item:")
        if not item_id.isdigit():
            return {"missing": True, "reason": "numeric HN item_id required"}
        item = _fetch_json(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json")
        kids = item.get("kids") or []
        comments = [
            _fetch_json(f"https://hacker-news.firebaseio.com/v0/item/{kid}.json")
            for kid in kids[:10]
        ]
        return {"item": item, "comments": comments}

    def fetch_x_tweet_context(arguments: dict[str, Any]) -> dict[str, Any]:
        tweet_id = str(arguments.get("tweet_id") or "").strip().removeprefix("tweet:")
        row = conn.execute(
            """
            select id, name, url, description, metadata_json, raw_json
            from items
            where source = 'x_tweets'
              and (external_id like ? or url like ?)
            order by id desc
            limit 1
            """,
            (f"%{tweet_id}", f"%/{tweet_id}"),
        ).fetchone()
        if not row:
            return {"missing": True, "tweet_id": tweet_id}
        return {
            "item_id": row[0],
            "name": row[1],
            "url": row[2],
            "description": row[3],
            "metadata": _json_loads(row[4], {}),
            "raw": _json_loads(row[5], {}),
        }

    def kimi_web_search(arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        max_results = int(arguments.get("max_results") or 5)
        if not enable_kimi_web_search:
            return {"disabled": True, "query": query}
        if web_search_client is None:
            raise RuntimeError(
                "Kimi web search is enabled but web_search_client is not configured"
            )
        return web_search_client.search(
            query=query, max_results=max(1, min(8, max_results))
        )

    return {
        "read_evidence_rows": read_evidence_rows,
        "read_source_items": read_source_items,
        "fetch_cached_readme": fetch_cached_readme,
        "fetch_homepage_or_docs": fetch_homepage_or_docs,
        "fetch_github_tree": fetch_github_tree,
        "fetch_github_file": fetch_github_file,
        "fetch_package_manifest": fetch_package_manifest,
        "fetch_hn_thread": fetch_hn_thread,
        "fetch_x_tweet_context": fetch_x_tweet_context,
        "kimi_web_search": kimi_web_search,
    }


def _normalize_repo(value: str) -> str:
    repo = (
        value.strip()
        .removeprefix("github:")
        .removeprefix("https://github.com/")
        .strip("/")
    )
    parts = repo.split("/")
    if len(parts) < 2:
        return ""
    return f"{parts[0].lower()}/{parts[1].lower()}"


def _github_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "HeroRadarLayer2"}
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=_github_headers())
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read(2_000_000).decode("utf-8"))


def _fetch_text(url: str, *, max_bytes: int) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "HeroRadarLayer2"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.read(max_bytes).decode("utf-8", errors="replace")


def _json_loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default
