from __future__ import annotations

import time
import random
import urllib.error
from typing import Any

from pipeline.decision.kimi_provider import KimiEmptyContentError, KimiProvider
from pipeline.decision.rate_limit import StartRateLimiter


class RateLimitedKimiEvalProvider:
    """Fresh, uncached Kimi provider with one shared outbound start gate."""

    provider_name = "kimi"
    eval_cache_mode = "disabled"
    collect_usage_on_error = True

    def __init__(
        self,
        *,
        limiter: StartRateLimiter,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: int = 90,
        max_retries: int = 2,
        retry_backoff_seconds: float = 1.0,
        max_output_tokens: int = 3000,
        thinking_type: str = "disabled",
        input_cost_per_million: float | None = None,
        cached_input_cost_per_million: float | None = None,
        output_cost_per_million: float | None = None,
        cost_currency: str = "USD",
        pricing_revision: str = "",
    ) -> None:
        if (input_cost_per_million is None) != (output_cost_per_million is None):
            raise ValueError(
                "input and output cost rates must both be supplied or both omitted"
            )
        self._provider = KimiProvider(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
            max_output_tokens=max_output_tokens,
            thinking_type=thinking_type,
        )
        self._limiter = limiter
        self._max_retries = max(0, int(max_retries))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self.input_cost_per_million = input_cost_per_million
        self.cached_input_cost_per_million = cached_input_cost_per_million
        self.output_cost_per_million = output_cost_per_million
        self.cost_currency = str(cost_currency or "USD").upper()
        self.pricing_revision = str(pricing_revision or "")
        self.last_usage: dict[str, Any] | None = None
        self.last_cost: dict[str, Any] | float | None = None
        self.last_attempts: list[dict[str, Any]] = []

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def base_url(self) -> str:
        return self._provider.base_url

    @property
    def api_key(self) -> str:
        return self._provider.api_key

    @property
    def timeout(self) -> int:
        return self._provider.timeout

    @property
    def max_retries(self) -> int:
        return self._max_retries

    @property
    def max_output_tokens(self) -> int | None:
        return self._provider.max_output_tokens

    @property
    def actual_temperature(self) -> float:
        return self._provider.actual_temperature

    @property
    def response_format(self) -> dict[str, str]:
        return self._provider.response_format

    @property
    def request_options(self) -> dict[str, Any]:
        return self._provider.request_options

    @property
    def thinking_type(self) -> str | None:
        return self._provider.thinking_type

    def handshake(self) -> dict[str, Any]:
        return self._provider.handshake()

    def complete_json(self, **call: Any) -> dict[str, Any]:
        last_error: BaseException | None = None
        self.last_usage = None
        self.last_cost = None
        self.last_attempts = []
        usage_attempts: list[dict[str, Any]] = []
        cost_attempts: list[dict[str, Any] | float] = []
        for attempt in range(self._max_retries + 1):
            self._limiter.wait()
            started = time.monotonic()
            try:
                response = self._provider.complete_json(**call)
                usage = self._provider.last_usage
                cost = self._provider.last_cost
                if cost is None and usage is not None:
                    cost = self._estimated_cost(usage)
                if isinstance(usage, dict):
                    usage_attempts.append(dict(usage))
                if isinstance(cost, (dict, int, float)) and not isinstance(cost, bool):
                    cost_attempts.append(cost)
                self.last_attempts.append(
                    {
                        "attempt": attempt + 1,
                        "status": "ok",
                        "latency_ms": int((time.monotonic() - started) * 1000),
                        "usage": usage,
                        "cost": cost,
                        "error_type": None,
                        "response_diagnostics": getattr(
                            self._provider, "last_response_diagnostics", None
                        ),
                    }
                )
                self.last_usage = _sum_usage(usage_attempts)
                self.last_cost = _sum_costs(
                    cost_attempts, currency=self.cost_currency
                )
                return response
            except Exception as exc:
                last_error = exc
                usage = self._provider.last_usage
                cost = self._provider.last_cost
                if cost is None and usage is not None:
                    cost = self._estimated_cost(usage)
                if isinstance(usage, dict):
                    usage_attempts.append(dict(usage))
                if isinstance(cost, (dict, int, float)) and not isinstance(cost, bool):
                    cost_attempts.append(cost)
                self.last_attempts.append(
                    {
                        "attempt": attempt + 1,
                        "status": "error",
                        "latency_ms": int((time.monotonic() - started) * 1000),
                        "usage": usage,
                        "cost": cost,
                        "error_type": type(exc).__name__,
                        "response_diagnostics": getattr(
                            self._provider, "last_response_diagnostics", None
                        ),
                    }
                )
                self.last_usage = _sum_usage(usage_attempts)
                self.last_cost = _sum_costs(
                    cost_attempts, currency=self.cost_currency
                )
                if attempt >= self._max_retries or not _retryable_error(exc):
                    break
                delay = _retry_delay(
                    exc,
                    base_seconds=self.retry_backoff_seconds,
                    attempt=attempt,
                )
                if delay > 0:
                    time.sleep(delay)
        raise last_error or RuntimeError("Kimi eval request failed")

    def _estimated_cost(self, usage: dict[str, Any]) -> dict[str, Any] | None:
        if self.input_cost_per_million is None or self.output_cost_per_million is None:
            return None
        try:
            prompt_tokens = int(
                usage.get("prompt_tokens") or usage.get("input_tokens") or 0
            )
            completion_tokens = int(
                usage.get("completion_tokens") or usage.get("output_tokens") or 0
            )
        except (TypeError, ValueError):
            return None
        details = usage.get("prompt_tokens_details") or usage.get(
            "input_tokens_details"
        )
        cached_value = usage.get("cached_input_tokens")
        if cached_value is None and isinstance(details, dict):
            cached_value = details.get("cached_tokens")
        try:
            cached_tokens = max(0, min(prompt_tokens, int(cached_value or 0)))
        except (TypeError, ValueError):
            cached_tokens = 0
        cache_rate = (
            float(self.cached_input_cost_per_million)
            if self.cached_input_cost_per_million is not None
            else float(self.input_cost_per_million)
        )
        uncached_tokens = prompt_tokens - cached_tokens
        amount = (
            uncached_tokens * float(self.input_cost_per_million)
            + cached_tokens * cache_rate
            + completion_tokens * float(self.output_cost_per_million)
        ) / 1_000_000
        return {
            "amount": round(amount, 8),
            "currency": self.cost_currency,
            "source": "configured_rate_estimate",
            "pricing_revision": self.pricing_revision,
            "input_cost_per_million": self.input_cost_per_million,
            "cached_input_cost_per_million": cache_rate,
            "output_cost_per_million": self.output_cost_per_million,
        }


def _sum_usage(attempts: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not attempts:
        return None
    prompt = sum(int(row.get("prompt_tokens") or row.get("input_tokens") or 0) for row in attempts)
    completion = sum(
        int(row.get("completion_tokens") or row.get("output_tokens") or 0)
        for row in attempts
    )
    total = sum(
        int(row.get("total_tokens") or 0) for row in attempts
    ) or prompt + completion
    cached = 0
    for row in attempts:
        details = row.get("prompt_tokens_details") or row.get("input_tokens_details")
        value = row.get("cached_input_tokens")
        if value is None and isinstance(details, dict):
            value = details.get("cached_tokens")
        cached += int(value or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_input_tokens": cached,
        "total_tokens": total,
    }


def _sum_costs(
    attempts: list[dict[str, Any] | float], *, currency: str
) -> dict[str, Any] | None:
    amounts: list[float] = []
    sources: set[str] = set()
    currencies: set[str] = set()
    for value in attempts:
        if isinstance(value, dict):
            amount = value.get("amount")
            sources.add(str(value.get("source") or "provider_reported"))
            if value.get("currency"):
                currencies.add(str(value["currency"]).upper())
        else:
            amount = value
            sources.add("provider_reported")
            currencies.add(currency)
        try:
            amounts.append(float(amount))
        except (TypeError, ValueError):
            continue
    if not amounts:
        return None
    return {
        "amount": round(sum(amounts), 8),
        "currency": (
            next(iter(currencies))
            if len(currencies) == 1
            else "mixed"
            if currencies
            else currency
        ),
        "source": next(iter(sources)) if len(sources) == 1 else "mixed",
    }


def _retryable_error(exc: BaseException) -> bool:
    if isinstance(exc, KimiEmptyContentError):
        return False
    if isinstance(exc, urllib.error.HTTPError):
        return int(exc.code) in {408, 409, 425, 429, 500, 502, 503, 504}
    return isinstance(
        exc,
        (TimeoutError, urllib.error.URLError, RuntimeError, ValueError, KeyError, IndexError),
    )


def _retry_delay(
    exc: BaseException, *, base_seconds: float, attempt: int
) -> float:
    exponential = max(0.0, float(base_seconds)) * (2**attempt)
    retry_after = 0.0
    if isinstance(exc, urllib.error.HTTPError):
        try:
            retry_after = float(exc.headers.get("Retry-After") or 0)
        except (AttributeError, TypeError, ValueError):
            retry_after = 0.0
    base = min(30.0, max(exponential, retry_after))
    return min(30.0, base + random.uniform(0, base * 0.25)) if base > 0 else 0.0
