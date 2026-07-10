from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from pipeline.decision.request_contract import freeze_json, thaw_json


ToolExecutor = Callable[[dict[str, Any]], dict[str, Any]]
ToolAvailability = Callable[["ToolCandidateContext"], bool]
ResultProjector = Callable[
    [dict[str, Any], str, dict[str, Any]], dict[str, Any]
]


@dataclass(frozen=True)
class ToolCandidateContext:
    entity_ids: tuple[str, ...] = ()
    repo_key: str | None = None
    canonical_url: str | None = None
    has_retrievable_evidence: bool = False
    needs_technical_evidence: bool = False
    needs_product_description: bool = False
    unresolved_identity: bool = False
    missing_first_party_material: bool = False
    needs_momentum_verification: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ToolCandidateContext:
        raw_entity_ids = value.get("entity_ids") or ()
        return cls(
            entity_ids=tuple(str(item) for item in raw_entity_ids),
            repo_key=str(value.get("repo_key") or "") or None,
            canonical_url=str(value.get("canonical_url") or "") or None,
            has_retrievable_evidence=bool(value.get("has_retrievable_evidence")),
            needs_technical_evidence=bool(value.get("needs_technical_evidence")),
            needs_product_description=bool(value.get("needs_product_description")),
            unresolved_identity=bool(value.get("unresolved_identity")),
            missing_first_party_material=bool(value.get("missing_first_party_material")),
            needs_momentum_verification=bool(value.get("needs_momentum_verification")),
        )


@dataclass(frozen=True)
class ToolSpec:
    name: str
    version: str
    description: str
    input_schema: Mapping[str, Any]
    family: str
    cost: str
    executor: ToolExecutor
    availability: ToolAvailability
    timeout_seconds: int
    max_result_tokens: int
    cache_policy: str
    concurrency_key: str
    max_in_flight: int
    starts_per_second: float
    result_projector: ResultProjector

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_schema", freeze_json(self.input_schema))
        if self.input_schema.get("type") != "object":
            raise ValueError(f"tool {self.name} input schema must be an object")
        if self.input_schema.get("additionalProperties") is not False:
            raise ValueError(
                f"tool {self.name} input schema must reject additional properties"
            )
        if not self.name or not self.version or not self.description:
            raise ValueError("tool name, version, and description are required")
        if not callable(self.executor) or not callable(self.availability):
            raise ValueError(f"tool {self.name} requires executor and availability")
        if not callable(self.result_projector):
            raise ValueError(f"tool {self.name} requires a result projector")

    def is_available(self, candidate: ToolCandidateContext) -> bool:
        return bool(self.availability(candidate))

    def model_projection(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": thaw_json(self.input_schema),
            "cost_hint": self.cost,
        }

    def fingerprint_projection(self) -> dict[str, Any]:
        return {"version": self.version, **self.model_projection()}

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            validate_tool_arguments(self.input_schema, arguments)
        except ValueError as exc:
            return {"status": "rejected", "error": str(exc)}
        return self.executor(dict(arguments))

    def project_result(
        self,
        result: dict[str, Any],
        *,
        observation_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        return self.result_projector(result, observation_id, arguments)


def registry_by_name(specs: tuple[ToolSpec, ...]) -> Mapping[str, ToolSpec]:
    registry: dict[str, ToolSpec] = {}
    for spec in specs:
        if spec.name in registry:
            raise ValueError(f"duplicate ToolSpec name: {spec.name}")
        registry[spec.name] = spec
    return MappingProxyType(registry)


def validate_tool_arguments(schema: Mapping[str, Any], arguments: Any) -> None:
    _validate_schema_value(schema, arguments, path="arguments")


def _validate_schema_value(
    schema: Mapping[str, Any], value: Any, *, path: str
) -> None:
    if "oneOf" in schema:
        matches = sum(
            1
            for child in schema["oneOf"]
            if _schema_matches(child, value, path=path)
        )
        if matches != 1:
            raise ValueError(f"{path} must match exactly one allowed shape")
    if "anyOf" in schema:
        if not any(
            _schema_matches(child, value, path=path) for child in schema["anyOf"]
        ):
            raise ValueError(f"{path} must match an allowed shape")

    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be an object")
        properties = schema.get("properties") or {}
        required = tuple(schema.get("required") or ())
        for key in required:
            if key not in value:
                raise ValueError(f"{path}.{key} is required")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                raise ValueError(
                    f"{path} contains unknown properties: {', '.join(unknown)}"
                )
        for key, item in value.items():
            child = properties.get(key)
            if child is not None:
                _validate_schema_value(child, item, path=f"{path}.{key}")
        return
    if expected_type == "string":
        if not isinstance(value, str):
            raise ValueError(f"{path} must be a string")
        if len(value) < int(schema.get("minLength") or 0):
            raise ValueError(f"{path} is too short")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            raise ValueError(f"{path} is too long")
        pattern = schema.get("pattern")
        if pattern and re.search(str(pattern), value) is None:
            raise ValueError(f"{path} does not match the allowed pattern")
    elif expected_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{path} must be an integer")
        if "minimum" in schema and value < int(schema["minimum"]):
            raise ValueError(f"{path} is below the minimum")
        if "maximum" in schema and value > int(schema["maximum"]):
            raise ValueError(f"{path} is above the maximum")
    elif expected_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{path} must be a number")
    elif expected_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{path} must be a boolean")
    elif expected_type == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path} must be an array")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                _validate_schema_value(item_schema, item, path=f"{path}[{index}]")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} is not an allowed value")


def _schema_matches(schema: Mapping[str, Any], value: Any, *, path: str) -> bool:
    try:
        _validate_schema_value(schema, value, path=path)
    except ValueError:
        return False
    return True
