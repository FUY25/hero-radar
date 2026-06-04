from __future__ import annotations

import re
import sqlite3
import time
from typing import Any

from pipeline.decision.llm_cache import (
    cache_key_for,
    get_cached_response,
    store_cached_response,
)
from pipeline.decision.schema import to_json, utc_now


SECRET_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9._-]+", re.I),
    re.compile(r"sk-[A-Za-z0-9._-]+", re.I),
    re.compile(r"(api[_-]?key\s*[:=]\s*)[A-Za-z0-9._-]+", re.I),
]

SUCCESS_STATUSES = {
    "scheduled",
    "skipped_unchanged",
    "pending_budget",
    "scout_disabled",
    "scout_ok",
    "scout_filtered",
    "scoring_ok",
    "brief_selected",
    "brief_ok",
    "deepdive_selected",
    "deepdive_ok",
}

OBSERVATION_STATUSES = {
    "llm_cache_hit",
    "llm_cache_miss",
    "llm_call_started",
    "llm_call_ok",
    "llm_call_error",
}


def sanitize_text(value: Any, *, max_chars: int = 800) -> str:
    text = str(value or "")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(
            lambda match: (
                f"{match.group(1)}[redacted]" if match.lastindex else "[redacted]"
            ),
            text,
        )
    return text[:max_chars]


def sanitized_error(error: BaseException | str | None) -> dict[str, str]:
    if error is None:
        return {"error_type": "", "error": ""}
    if isinstance(error, BaseException):
        return {
            "error_type": type(error).__name__,
            "error": sanitize_text(error),
        }
    return {"error_type": "Error", "error": sanitize_text(error)}


def record_stage_event(
    conn: sqlite3.Connection,
    *,
    feed_run_id: str,
    group_id: str | None,
    stage: str,
    status: str,
    error: BaseException | str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    err = sanitized_error(error)
    conn.execute(
        """
        insert into l2_stage_events(
          feed_run_id, group_id, stage, status, error_type, error, metadata_json, created_at
        )
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            feed_run_id,
            group_id,
            stage,
            status,
            err["error_type"],
            err["error"],
            to_json(metadata or {}),
            utc_now(),
        ),
    )


def stage_summary(conn: sqlite3.Connection, feed_run_id: str) -> dict[str, Any]:
    rows = conn.execute(
        "select stage, status from l2_stage_events where feed_run_id = ?",
        (feed_run_id,),
    ).fetchall()
    stage_counts: dict[str, int] = {}
    error_counts: dict[str, int] = {}
    success_total = 0
    error_total = 0
    for stage, status in rows:
        stage_counts[status] = stage_counts.get(status, 0) + 1
        if status in OBSERVATION_STATUSES:
            continue
        if str(status).endswith("_error"):
            error_total += 1
            error_counts[stage] = error_counts.get(stage, 0) + 1
        elif status in SUCCESS_STATUSES:
            success_total += 1
    return {
        "stage_counts": stage_counts,
        "error_counts": error_counts,
        "success_total": success_total,
        "error_total": error_total,
    }


def final_run_status(summary: dict[str, Any]) -> str:
    error_total = int(summary.get("error_total") or 0)
    success_total = int(summary.get("success_total") or 0)
    if error_total and success_total:
        return "ok_with_errors"
    if error_total and not success_total:
        return "error"
    return "ok"


class TelemetryLLMProvider:
    def __init__(
        self,
        provider: Any,
        *,
        conn: sqlite3.Connection,
        feed_run_id: str,
        group_id: str | None,
        stage: str,
        timeout_seconds: int | None = None,
    ) -> None:
        self._provider = provider
        self._conn = conn
        self._feed_run_id = feed_run_id
        self._group_id = group_id
        self._stage = stage
        self._timeout_seconds = timeout_seconds

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    @property
    def provider_name(self) -> str:
        return str(getattr(self._provider, "provider_name", ""))

    @property
    def model(self) -> str:
        return str(getattr(self._provider, "model", ""))

    def complete_json(
        self,
        *,
        task: str,
        prompt_version: str,
        input_payload: dict[str, Any],
        system_prompt: str = "",
    ) -> dict[str, Any]:
        metadata = {
            "task": task,
            "prompt_version": prompt_version,
            "provider": self.provider_name,
            "model": self.model,
        }
        if self._timeout_seconds is not None:
            metadata["timeout_seconds"] = int(self._timeout_seconds)
        record_stage_event(
            self._conn,
            feed_run_id=self._feed_run_id,
            group_id=self._group_id,
            stage=self._stage,
            status="llm_call_started",
            metadata=metadata,
        )
        self._conn.commit()
        started = time.monotonic()
        old_timeout = getattr(self._provider, "timeout", None)
        timeout_overridden = (
            self._timeout_seconds is not None and hasattr(self._provider, "timeout")
        )
        if timeout_overridden:
            setattr(self._provider, "timeout", int(self._timeout_seconds))
        try:
            response = self._provider.complete_json(
                task=task,
                prompt_version=prompt_version,
                input_payload=input_payload,
                system_prompt=system_prompt,
            )
        except Exception as exc:
            record_stage_event(
                self._conn,
                feed_run_id=self._feed_run_id,
                group_id=self._group_id,
                stage=self._stage,
                status="llm_call_error",
                error=exc,
                metadata={
                    **metadata,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            self._conn.commit()
            raise
        finally:
            if timeout_overridden:
                setattr(self._provider, "timeout", old_timeout)
        record_stage_event(
            self._conn,
            feed_run_id=self._feed_run_id,
            group_id=self._group_id,
            stage=self._stage,
            status="llm_call_ok",
            metadata={
                **metadata,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "response_keys": sorted(response.keys()) if isinstance(response, dict) else [],
            },
        )
        self._conn.commit()
        return response


class CachedTelemetryLLMProvider(TelemetryLLMProvider):
    def complete_json(
        self,
        *,
        task: str,
        prompt_version: str,
        input_payload: dict[str, Any],
        system_prompt: str = "",
    ) -> dict[str, Any]:
        cache_key = cache_key_for(
            provider=self.provider_name,
            model=self.model,
            prompt_version=prompt_version,
            task=task,
            input_payload=input_payload,
        )
        metadata = {
            "task": task,
            "prompt_version": prompt_version,
            "provider": self.provider_name,
            "model": self.model,
            "cache_key": cache_key,
        }
        cached = get_cached_response(self._conn, cache_key)
        if cached and cached.get("status") == "ok":
            record_stage_event(
                self._conn,
                feed_run_id=self._feed_run_id,
                group_id=self._group_id,
                stage=self._stage,
                status="llm_cache_hit",
                metadata=metadata,
            )
            self._conn.commit()
            return cached["response_json"]
        record_stage_event(
            self._conn,
            feed_run_id=self._feed_run_id,
            group_id=self._group_id,
            stage=self._stage,
            status="llm_cache_miss",
            metadata=metadata,
        )
        self._conn.commit()
        try:
            response = super().complete_json(
                task=task,
                prompt_version=prompt_version,
                input_payload=input_payload,
                system_prompt=system_prompt,
            )
        except Exception as exc:
            store_cached_response(
                self._conn,
                provider=self.provider_name,
                model=self.model,
                prompt_version=prompt_version,
                task=task,
                input_payload=input_payload,
                request_payload={
                    "input_payload": input_payload,
                    "system_prompt": system_prompt,
                },
                response_payload={},
                status="error",
                error=sanitize_text(exc),
            )
            raise
        store_cached_response(
            self._conn,
            provider=self.provider_name,
            model=self.model,
            prompt_version=prompt_version,
            task=task,
            input_payload=input_payload,
            request_payload={
                "input_payload": input_payload,
                "system_prompt": system_prompt,
            },
            response_payload=response,
            status="ok",
        )
        return response
