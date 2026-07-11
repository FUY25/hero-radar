from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from pipeline.decision.llm_cache import (
    cache_key_for,
    get_cached_response,
    store_cached_response,
)
from pipeline.decision.request_contract import (
    LLMRequestContract,
    sanitize_contract_value,
    thaw_json,
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


@dataclass(frozen=True)
class ModelCallTelemetryContext:
    """Caller-owned metadata for one logical model invocation."""

    component: str = ""
    turn_index: int | None = None
    attempt: int | None = None
    estimated_tokens: Mapping[str, Any] = field(default_factory=dict)
    context_manifest: Mapping[str, Any] = field(default_factory=dict)
    output_schema_version: str = ""
    tool_registry_version: str = ""
    context_policy_version: str = ""


def _coerce_call_context(
    value: ModelCallTelemetryContext | Mapping[str, Any] | None,
) -> ModelCallTelemetryContext:
    if value is None:
        return ModelCallTelemetryContext()
    if isinstance(value, ModelCallTelemetryContext):
        return value
    return ModelCallTelemetryContext(
        component=str(value.get("component") or ""),
        turn_index=(
            None if value.get("turn_index") is None else int(value["turn_index"])
        ),
        attempt=None if value.get("attempt") is None else int(value["attempt"]),
        estimated_tokens=(
            value.get("estimated_tokens")
            if isinstance(value.get("estimated_tokens"), Mapping)
            else {}
        ),
        context_manifest=(
            value.get("context_manifest")
            if isinstance(value.get("context_manifest"), Mapping)
            else {}
        ),
        output_schema_version=str(value.get("output_schema_version") or ""),
        tool_registry_version=str(value.get("tool_registry_version") or ""),
        context_policy_version=str(value.get("context_policy_version") or ""),
    )


def _mapping_value(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    attributes = getattr(value, "__dict__", None)
    return attributes if isinstance(attributes, Mapping) else {}


def _token_usage(provider: Any, response: Any) -> dict[str, int | None]:
    usage: Any = getattr(provider, "last_usage", None)
    if usage is None and isinstance(response, Mapping):
        usage = response.get("_usage") or response.get("usage")
    values = _mapping_value(usage)
    details = _mapping_value(
        values.get("prompt_tokens_details")
        or values.get("input_tokens_details")
    )

    def optional_int(*names: str) -> int | None:
        for name in names:
            value = values.get(name)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None
        return None

    cached = values.get("cached_input_tokens")
    if cached is None:
        cached = details.get("cached_tokens")
    try:
        cached_tokens = None if cached is None else int(cached)
    except (TypeError, ValueError):
        cached_tokens = None
    return {
        "prompt_tokens": optional_int("prompt_tokens", "input_tokens"),
        "completion_tokens": optional_int("completion_tokens", "output_tokens"),
        "cached_input_tokens": cached_tokens,
        "total_tokens": optional_int("total_tokens"),
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


def record_model_call(
    conn: sqlite3.Connection,
    *,
    feed_run_id: str,
    group_id: str | None,
    stage: str,
    task: str,
    prompt_version: str,
    provider: Any,
    input_payload: Mapping[str, Any],
    status: str,
    latency_ms: int,
    response: Any = None,
    error: BaseException | str | None = None,
    request_contract: LLMRequestContract | None = None,
    call_context: ModelCallTelemetryContext | Mapping[str, Any] | None = None,
    cache_key: str | None = None,
    collect_usage: bool = True,
) -> None:
    """Persist the final outcome of one logical invocation.

    Cache misses are represented by the provider outcome (``ok`` or ``error``),
    while a cache hit gets its own ``cache_hit`` row. The lower-level stage-event
    stream continues to carry the separate miss/start/finish lifecycle events.
    """

    context = _coerce_call_context(call_context)
    provider_name = str(getattr(provider, "provider_name", ""))
    model = str(getattr(provider, "model", ""))
    effective_cache_key = cache_key or cache_key_for(
        provider=provider_name,
        model=model,
        prompt_version=prompt_version,
        task=task,
        input_payload=input_payload,
        request_contract=request_contract,
    )
    request_fingerprint = (
        request_contract.fingerprint()
        if request_contract is not None
        else effective_cache_key
    )
    usage = (
        _token_usage(provider, response)
        if collect_usage
        else {
            "prompt_tokens": None,
            "completion_tokens": None,
            "cached_input_tokens": None,
            "total_tokens": None,
        }
    )
    err = sanitized_error(error)
    conn.execute(
        """
        insert into l2_model_calls(
          feed_run_id, group_id, component, task, turn_index, attempt,
          provider, model, request_fingerprint, cache_key, prompt_version,
          output_schema_version, tool_registry_version, context_policy_version,
          status, latency_ms, prompt_tokens, completion_tokens,
          cached_input_tokens, total_tokens, temperature, max_output_tokens,
          estimated_tokens_json, context_manifest_json, error_type, error, created_at
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            feed_run_id,
            group_id,
            context.component or stage,
            task,
            context.turn_index,
            context.attempt,
            provider_name,
            model,
            request_fingerprint,
            effective_cache_key,
            (
                request_contract.prompt_version
                if request_contract is not None and request_contract.prompt_version
                else prompt_version
            ),
            (
                (
                    request_contract.output_schema_version
                    if request_contract is not None
                    else context.output_schema_version
                )
                or context.output_schema_version
            ),
            (
                (
                    request_contract.tool_registry_version
                    if request_contract is not None
                    else context.tool_registry_version
                )
                or context.tool_registry_version
            ),
            (
                (
                    request_contract.context_policy_version
                    if request_contract is not None
                    else context.context_policy_version
                )
                or context.context_policy_version
            ),
            status,
            max(0, int(latency_ms)),
            usage["prompt_tokens"],
            usage["completion_tokens"],
            usage["cached_input_tokens"],
            usage["total_tokens"],
            (
                request_contract.actual_temperature
                if request_contract is not None
                else getattr(provider, "actual_temperature", None)
            ),
            (
                request_contract.max_output_tokens
                if request_contract is not None
                else getattr(provider, "max_output_tokens", None)
            ),
            to_json(sanitize_contract_value(context.estimated_tokens)),
            to_json(sanitize_contract_value(context.context_manifest)),
            err["error_type"] or None,
            err["error"] or None,
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
        request_contract: LLMRequestContract | None = None,
        call_context: ModelCallTelemetryContext | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if request_contract is not None:
            request_contract.validate_call(
                provider=self.provider_name,
                model=self.model,
                task=task,
                system_prompt=system_prompt,
                input_payload=input_payload,
            )
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
                input_payload=thaw_json(input_payload),
                system_prompt=system_prompt,
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            record_stage_event(
                self._conn,
                feed_run_id=self._feed_run_id,
                group_id=self._group_id,
                stage=self._stage,
                status="llm_call_error",
                error=exc,
                metadata={
                    **metadata,
                    "duration_ms": duration_ms,
                },
            )
            record_model_call(
                self._conn,
                feed_run_id=self._feed_run_id,
                group_id=self._group_id,
                stage=self._stage,
                task=task,
                prompt_version=prompt_version,
                provider=self._provider,
                input_payload=input_payload,
                status="error",
                latency_ms=duration_ms,
                error=exc,
                request_contract=request_contract,
                call_context=call_context,
                collect_usage=bool(
                    getattr(self._provider, "collect_usage_on_error", False)
                ),
            )
            self._conn.commit()
            raise
        finally:
            if timeout_overridden:
                setattr(self._provider, "timeout", old_timeout)
        duration_ms = int((time.monotonic() - started) * 1000)
        record_stage_event(
            self._conn,
            feed_run_id=self._feed_run_id,
            group_id=self._group_id,
            stage=self._stage,
            status="llm_call_ok",
            metadata={
                **metadata,
                "duration_ms": duration_ms,
                "response_keys": sorted(response.keys()) if isinstance(response, dict) else [],
            },
        )
        record_model_call(
            self._conn,
            feed_run_id=self._feed_run_id,
            group_id=self._group_id,
            stage=self._stage,
            task=task,
            prompt_version=prompt_version,
            provider=self._provider,
            input_payload=input_payload,
            status="ok",
            latency_ms=duration_ms,
            response=response,
            request_contract=request_contract,
            call_context=call_context,
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
        request_contract: LLMRequestContract | None = None,
        call_context: ModelCallTelemetryContext | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if request_contract is not None:
            request_contract.validate_call(
                provider=self.provider_name,
                model=self.model,
                task=task,
                system_prompt=system_prompt,
                input_payload=input_payload,
            )
        cache_key = cache_key_for(
            provider=self.provider_name,
            model=self.model,
            prompt_version=prompt_version,
            task=task,
            input_payload=input_payload,
            request_contract=request_contract,
        )
        metadata = {
            "task": task,
            "prompt_version": prompt_version,
            "provider": self.provider_name,
            "model": self.model,
            "cache_key": cache_key,
        }
        if request_contract is not None:
            metadata.update(
                {
                    "request_fingerprint": request_contract.fingerprint(),
                    "system_prompt_hash": request_contract.system_prompt_hash,
                    "tool_schema_hash": request_contract.tool_schema_hash,
                    "output_schema_hash": request_contract.output_schema_hash,
                    "context_policy_version": request_contract.context_policy_version,
                    "actual_temperature": request_contract.actual_temperature,
                    "max_output_tokens": request_contract.max_output_tokens,
                    "response_format": request_contract.stored_request()[
                        "response_format"
                    ],
                }
            )
        cached = get_cached_response(self._conn, cache_key)
        if cached and cached.get("status") == "ok":
            started = time.monotonic()
            record_stage_event(
                self._conn,
                feed_run_id=self._feed_run_id,
                group_id=self._group_id,
                stage=self._stage,
                status="llm_cache_hit",
                metadata=metadata,
            )
            record_model_call(
                self._conn,
                feed_run_id=self._feed_run_id,
                group_id=self._group_id,
                stage=self._stage,
                task=task,
                prompt_version=prompt_version,
                provider=self._provider,
                input_payload=input_payload,
                status="cache_hit",
                latency_ms=int((time.monotonic() - started) * 1000),
                request_contract=request_contract,
                call_context=call_context,
                cache_key=cache_key,
                collect_usage=False,
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
                input_payload=thaw_json(input_payload),
                system_prompt=system_prompt,
                request_contract=request_contract,
                call_context=call_context,
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
                request_contract=request_contract,
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
            request_contract=request_contract,
        )
        return response
