from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence


_SECRET_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth_token",
    "authorization",
    "bearer_token",
    "client_secret",
    "cookie",
    "deepseek_api_key",
    "github_token",
    "kimi_api_key",
    "moonshot_api_key",
    "password",
    "proxy_authorization",
    "secret",
    "set_cookie",
    "x_api_key",
}
_SECRET_PATTERNS = (
    re.compile(r"Bearer\s+[^\s\"']+", re.I),
    re.compile(r"\bsk-[A-Za-z0-9._-]+", re.I),
    re.compile(
        r"((?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)\s*[:=]\s*)[^\s,;\"']+",
        re.I,
    ),
)


def canonical_json(value: Any) -> str:
    return json.dumps(
        thaw_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_json_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sanitize_contract_value(value: Any) -> Any:
    """Return a JSON-safe value with credential material removed.

    Secret-bearing keys are omitted rather than redacted so changing a credential
    cannot invalidate an otherwise identical model request. Secret-like text is
    replaced with a stable marker before either fingerprinting or persistence.
    """

    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).strip().lower().replace("-", "_")
            if normalized_key in _SECRET_KEYS:
                continue
            sanitized[str(key)] = sanitize_contract_value(item)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [sanitize_contract_value(item) for item in value]
    if isinstance(value, str):
        text = value
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub(
                lambda match: (
                    f"{match.group(1)}[redacted]" if match.lastindex else "[redacted]"
                ),
                text,
            )
        return text
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    return value


def thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw_json(item) for item in value]
    return value


@dataclass(frozen=True)
class LLMRequestContract:
    provider: str
    model: str
    task: str
    system_prompt: str
    active_tools: tuple[Any, ...]
    output_schema: Any
    context_policy_version: str
    input_payload: Any
    actual_temperature: float
    max_output_tokens: int | None
    response_format: Any
    active_tool_versions: tuple[str, ...] = ()
    prompt_version: str = ""
    output_schema_version: str = ""
    tool_registry_version: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "system_prompt",
            str(sanitize_contract_value(self.system_prompt)),
        )
        object.__setattr__(
            self,
            "active_tools",
            tuple(
                freeze_json(sanitize_contract_value(tool))
                for tool in tuple(self.active_tools)
            ),
        )
        object.__setattr__(
            self,
            "output_schema",
            freeze_json(sanitize_contract_value(self.output_schema)),
        )
        object.__setattr__(
            self,
            "input_payload",
            freeze_json(sanitize_contract_value(self.input_payload)),
        )
        object.__setattr__(
            self,
            "response_format",
            freeze_json(sanitize_contract_value(self.response_format)),
        )
        object.__setattr__(
            self,
            "active_tool_versions",
            tuple(str(value) for value in self.active_tool_versions),
        )
        object.__setattr__(self, "actual_temperature", float(self.actual_temperature))
        if self.max_output_tokens is not None:
            object.__setattr__(
                self, "max_output_tokens", max(1, int(self.max_output_tokens))
            )

    @classmethod
    def create(
        cls,
        *,
        provider: str,
        model: str,
        task: str,
        system_prompt: str,
        active_tools: Sequence[Mapping[str, Any]],
        output_schema: Mapping[str, Any],
        context_policy_version: str,
        input_payload: Mapping[str, Any],
        actual_temperature: float,
        max_output_tokens: int | None,
        response_format: Mapping[str, Any] | str | None,
        prompt_version: str = "",
        output_schema_version: str = "",
        tool_registry_version: str = "",
        active_tool_versions: Sequence[str] = (),
    ) -> LLMRequestContract:
        return cls(
            provider=str(provider),
            model=str(model),
            task=str(task),
            system_prompt=system_prompt,
            active_tools=tuple(active_tools),
            output_schema=output_schema,
            context_policy_version=str(context_policy_version),
            input_payload=input_payload,
            actual_temperature=actual_temperature,
            max_output_tokens=max_output_tokens,
            response_format=response_format or {},
            active_tool_versions=tuple(active_tool_versions),
            prompt_version=str(prompt_version),
            output_schema_version=str(output_schema_version),
            tool_registry_version=str(tool_registry_version),
        )

    @classmethod
    def for_provider(
        cls,
        provider: Any,
        *,
        task: str,
        system_prompt: str,
        active_tools: Sequence[Mapping[str, Any]],
        output_schema: Mapping[str, Any],
        context_policy_version: str,
        input_payload: Mapping[str, Any],
        actual_temperature: float | None = None,
        max_output_tokens: int | None = None,
        response_format: Mapping[str, Any] | str | None = None,
        prompt_version: str = "",
        output_schema_version: str = "",
        tool_registry_version: str = "",
        active_tool_versions: Sequence[str] = (),
    ) -> LLMRequestContract:
        provider_defaults = _provider_request_defaults(provider)
        return cls.create(
            provider=str(getattr(provider, "provider_name", "")),
            model=str(getattr(provider, "model", "")),
            task=task,
            system_prompt=system_prompt,
            active_tools=active_tools,
            output_schema=output_schema,
            context_policy_version=context_policy_version,
            input_payload=input_payload,
            actual_temperature=(
                provider_defaults["actual_temperature"]
                if actual_temperature is None
                else actual_temperature
            ),
            max_output_tokens=(
                provider_defaults["max_output_tokens"]
                if max_output_tokens is None
                else max_output_tokens
            ),
            response_format=(
                provider_defaults["response_format"]
                if response_format is None
                else response_format
            ),
            prompt_version=prompt_version,
            output_schema_version=output_schema_version,
            tool_registry_version=tool_registry_version,
            active_tool_versions=active_tool_versions,
        )

    @property
    def system_prompt_hash(self) -> str:
        return stable_json_hash(
            {"version": self.prompt_version, "content": self.system_prompt}
        )

    @property
    def tool_schema_hash(self) -> str:
        return stable_json_hash(
            {
                "registry_version": self.tool_registry_version,
                "tool_versions": self.active_tool_versions,
                "active_tools": self.active_tools,
            }
        )

    @property
    def output_schema_hash(self) -> str:
        return stable_json_hash(
            {
                "version": self.output_schema_version,
                "schema": self.output_schema,
            }
        )

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "task": self.task,
            "system_prompt_hash": self.system_prompt_hash,
            "tool_schema_hash": self.tool_schema_hash,
            "output_schema_hash": self.output_schema_hash,
            "context_policy_version": self.context_policy_version,
            "input_payload": thaw_json(self.input_payload),
            "actual_temperature": self.actual_temperature,
            "max_output_tokens": self.max_output_tokens,
            "response_format": thaw_json(self.response_format),
        }

    def fingerprint(self) -> str:
        return stable_json_hash(self.fingerprint_payload())

    def stored_request(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "task": self.task,
            "prompt_version": self.prompt_version,
            "output_schema_version": self.output_schema_version,
            "tool_registry_version": self.tool_registry_version,
            "context_policy_version": self.context_policy_version,
            "system_prompt": self.system_prompt,
            "system_prompt_hash": self.system_prompt_hash,
            "active_tools": thaw_json(self.active_tools),
            "active_tool_versions": list(self.active_tool_versions),
            "tool_schema_hash": self.tool_schema_hash,
            "output_schema": thaw_json(self.output_schema),
            "output_schema_hash": self.output_schema_hash,
            "input_payload": thaw_json(self.input_payload),
            "actual_temperature": self.actual_temperature,
            "max_output_tokens": self.max_output_tokens,
            "response_format": thaw_json(self.response_format),
            "request_fingerprint": self.fingerprint(),
        }

    def validate_call(
        self,
        *,
        provider: str,
        model: str,
        task: str,
        system_prompt: str,
        input_payload: Mapping[str, Any],
    ) -> None:
        expected = {
            "provider": self.provider,
            "model": self.model,
            "task": self.task,
            "system_prompt": self.system_prompt,
            "input_payload": thaw_json(self.input_payload),
        }
        actual = {
            "provider": str(provider),
            "model": str(model),
            "task": str(task),
            "system_prompt": str(sanitize_contract_value(system_prompt)),
            "input_payload": sanitize_contract_value(input_payload),
        }
        for key, expected_value in expected.items():
            if canonical_json(actual[key]) != canonical_json(expected_value):
                raise ValueError(f"request contract {key} does not match actual call")


def _provider_request_defaults(provider: Any) -> dict[str, Any]:
    defaults = {
        "actual_temperature": getattr(provider, "actual_temperature", 0),
        "max_output_tokens": getattr(provider, "max_output_tokens", None),
        "response_format": getattr(
            provider, "response_format", {"type": "json_object"}
        ),
    }
    build_payload = getattr(provider, "build_payload", None)
    if not callable(build_payload):
        return defaults
    try:
        payload = build_payload(
            system_prompt="",
            user_payload={},
            temperature=0,
            max_tokens=None,
        )
    except (TypeError, ValueError):
        return defaults
    if not isinstance(payload, Mapping):
        return defaults
    return {
        "actual_temperature": payload.get(
            "temperature", defaults["actual_temperature"]
        ),
        "max_output_tokens": payload.get(
            "max_tokens", defaults["max_output_tokens"]
        ),
        "response_format": payload.get(
            "response_format", defaults["response_format"]
        ),
    }
