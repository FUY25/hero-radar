from __future__ import annotations

from typing import Any


AXIS_NAMES = [
    "workflow_shift",
    "technical_substance",
    "product_market_fit",
    "momentum",
    "confidence",
    "risk_penalty",
    "derivative_news_penalty",
]

SUPPORT_AXES = [
    "workflow_shift",
    "technical_substance",
    "product_market_fit",
    "momentum",
    "confidence",
    "risk_penalty",
    "derivative_news_penalty",
]


def validate_scoring_turn_v2(response: Any) -> None:
    """Enforce the host-side v2 response contract before normalization."""

    if not isinstance(response, dict):
        raise ValueError("scoring v2 response must be an object")
    action = response.get("action")
    if action not in {"use_tools", "final"}:
        raise ValueError("scoring v2 action must be use_tools or final")
    _validate_sufficiency(response.get("information_sufficiency"))

    if action == "use_tools":
        _require_exact_keys(
            response,
            {
                "action",
                "information_sufficiency",
                "information_need",
                "tool_requests",
            },
            "scoring v2 use_tools response",
        )
        _validate_information_need(response.get("information_need"))
        _validate_tool_requests(response.get("tool_requests"))
        return

    _require_exact_keys(
        response,
        {"action", "information_sufficiency", "score"},
        "scoring v2 final response",
    )
    _validate_score(response.get("score"))


def _validate_sufficiency(value: Any) -> None:
    fields = {
        "identity",
        "workflow_shift",
        "technical_substance",
        "product_market_fit",
        "momentum",
    }
    if not isinstance(value, dict):
        raise ValueError("scoring v2 requires complete information_sufficiency")
    _require_exact_keys(value, fields, "scoring v2 information_sufficiency")
    if any(level not in {"weak", "medium", "strong"} for level in value.values()):
        raise ValueError("scoring v2 information_sufficiency has invalid level")


def _validate_information_need(value: Any) -> None:
    fields = {"question", "target_axes", "expected_decision_impact"}
    if not isinstance(value, dict):
        raise ValueError("scoring v2 use_tools requires structured information_need")
    _require_exact_keys(value, fields, "scoring v2 information_need")
    _require_bounded_string(value.get("question"), "information_need.question", 1_000)
    _require_bounded_string(
        value.get("expected_decision_impact"),
        "information_need.expected_decision_impact",
        1_000,
    )
    axes = _validate_string_array(
        value.get("target_axes"),
        "information_need.target_axes",
        min_items=1,
        max_items=len(SUPPORT_AXES),
        max_chars=max(len(axis) for axis in SUPPORT_AXES),
        unique=True,
    )
    if any(axis not in SUPPORT_AXES for axis in axes):
        raise ValueError("scoring v2 information_need has unknown target axis")


def _validate_tool_requests(value: Any) -> None:
    if not isinstance(value, list) or not value:
        raise ValueError("scoring v2 use_tools requires at least one tool request")
    if len(value) > 8:
        raise ValueError("scoring v2 tool_requests exceeds maxItems=8")
    for request in value:
        if not isinstance(request, dict):
            raise ValueError("scoring v2 tool request must be an object")
        _require_exact_keys(request, {"name", "arguments"}, "scoring v2 tool request")
        _require_bounded_string(request.get("name"), "tool_request.name", 80)
        if not isinstance(request.get("arguments"), dict):
            raise ValueError("scoring v2 tool_request.arguments must be an object")


def _validate_score(value: Any) -> None:
    fields = {
        "object_type",
        "is_product_or_repo",
        "axes",
        "supporting_evidence",
        "negative_evidence",
        "known_gaps",
        "primary_reason",
        "rationale_short",
        "topic_tags",
        "caveats",
        "should_print",
    }
    if not isinstance(value, dict):
        raise ValueError("scoring v2 final response requires score")
    _require_exact_keys(value, fields, "scoring v2 final score")
    if value.get("object_type") not in {
        "product",
        "repo",
        "package",
        "research_tool",
        "model_release",
        "article",
        "news",
        "unknown",
    }:
        raise ValueError("scoring v2 score has invalid object_type")
    if not isinstance(value.get("is_product_or_repo"), bool) or not isinstance(
        value.get("should_print"), bool
    ):
        raise ValueError("scoring v2 score boolean fields are required")
    _validate_axes(value.get("axes"))
    _validate_claim_array(value.get("supporting_evidence"), "supporting_evidence")
    _validate_claim_array(value.get("negative_evidence"), "negative_evidence")
    _validate_string_array(
        value.get("known_gaps"), "known_gaps", max_items=8, max_chars=240
    )
    _require_bounded_string(value.get("primary_reason"), "primary_reason", 80)
    _require_bounded_string(value.get("rationale_short"), "rationale_short", 1_000)
    _validate_string_array(
        value.get("topic_tags"), "topic_tags", max_items=8, max_chars=40
    )
    _validate_string_array(
        value.get("caveats"), "caveats", max_items=8, max_chars=240
    )


def _validate_axes(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("scoring v2 score.axes must be an object")
    _require_exact_keys(value, set(AXIS_NAMES), "scoring v2 score.axes")
    for name, number in value.items():
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise ValueError(f"scoring v2 axis {name} must be numeric")
        maximum = 25 if name.endswith("penalty") else 100
        if not 0 <= float(number) <= maximum:
            raise ValueError(f"scoring v2 axis {name} is out of range")


def _validate_claim_array(value: Any, field: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"scoring v2 score.{field} must be an array")
    if len(value) > 8:
        raise ValueError(f"scoring v2 score.{field} exceeds maxItems=8")
    for claim in value:
        if not isinstance(claim, dict):
            raise ValueError(f"scoring v2 score.{field} item must be an object")
        _require_exact_keys(
            claim,
            {"claim", "evidence_refs", "supports_axes", "claim_type"},
            f"scoring v2 score.{field} item",
        )
        _require_bounded_string(claim.get("claim"), f"{field}.claim", 1_000)
        _validate_string_array(
            claim.get("evidence_refs"),
            f"{field}.evidence_refs",
            min_items=1,
            max_items=8,
            max_chars=160,
            unique=True,
        )
        axes = _validate_string_array(
            claim.get("supports_axes"),
            f"{field}.supports_axes",
            min_items=1,
            max_items=len(SUPPORT_AXES),
            max_chars=max(len(axis) for axis in SUPPORT_AXES),
            unique=True,
        )
        if any(axis not in SUPPORT_AXES for axis in axes):
            raise ValueError(f"scoring v2 score.{field} has unknown support axis")
        if claim.get("claim_type") not in {"observed", "inferred"}:
            raise ValueError("scoring v2 claim_type must be observed or inferred")


def _require_exact_keys(value: dict[str, Any], expected: set[str], field: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{field} has unknown or missing fields")


def _require_bounded_string(value: Any, field: str, max_chars: int) -> str:
    if not isinstance(value, str) or not value or len(value) > max_chars:
        raise ValueError(
            f"scoring v2 {field} must be a non-empty string "
            f"of at most {max_chars} characters"
        )
    return value


def _validate_string_array(
    value: Any,
    field: str,
    *,
    min_items: int = 0,
    max_items: int,
    max_chars: int,
    unique: bool = False,
) -> list[str]:
    if not isinstance(value, list) or not min_items <= len(value) <= max_items:
        raise ValueError(f"scoring v2 {field} has invalid item count")
    if any(
        not isinstance(item, str) or not item or len(item) > max_chars
        for item in value
    ):
        raise ValueError(f"scoring v2 {field} contains an invalid string")
    if unique and len(set(value)) != len(value):
        raise ValueError(f"scoring v2 {field} requires unique items")
    return value


def brief_writer_output_schema_v1() -> dict[str, Any]:
    bounded_item = {"type": "string", "minLength": 1, "maxLength": 220}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "layer2-brief-output-v1",
        "type": "object",
        "additionalProperties": False,
        "required": ["category", "headline", "core_highlights", "use_cases"],
        "properties": {
            "category": {
                "type": "object",
                "additionalProperties": False,
                "required": ["primary", "tags"],
                "properties": {
                    "primary": {"type": "string", "minLength": 1, "maxLength": 40},
                    "tags": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 40,
                        },
                        "maxItems": 8,
                    },
                },
            },
            "headline": {"type": "string", "minLength": 1, "maxLength": 160},
            "core_highlights": {
                "type": "array",
                "items": bounded_item,
                "minItems": 1,
                "maxItems": 3,
            },
            "use_cases": {
                "type": "array",
                "items": bounded_item,
                "maxItems": 4,
            },
            "caveat": {"type": "string", "maxLength": 240},
        },
    }


def scoring_turn_output_schema_v2() -> dict[str, Any]:
    bounded_text = {"type": "string", "minLength": 1, "maxLength": 1_000}
    short_text = {"type": "string", "minLength": 1, "maxLength": 240}
    string_list = {
        "type": "array",
        "items": short_text,
        "maxItems": 8,
    }
    axis_properties = {
        name: {
            "type": "number",
            "minimum": 0,
            "maximum": 25 if name.endswith("penalty") else 100,
        }
        for name in AXIS_NAMES
    }
    claim_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["claim", "evidence_refs", "supports_axes", "claim_type"],
        "properties": {
            "claim": bounded_text,
            "evidence_refs": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 160},
                "minItems": 1,
                "maxItems": 8,
                "uniqueItems": True,
            },
            "supports_axes": {
                "type": "array",
                "items": {"type": "string", "enum": SUPPORT_AXES},
                "minItems": 1,
                "maxItems": len(SUPPORT_AXES),
                "uniqueItems": True,
            },
            "claim_type": {"type": "string", "enum": ["observed", "inferred"]},
        },
    }
    sufficiency_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "identity",
            "workflow_shift",
            "technical_substance",
            "product_market_fit",
            "momentum",
        ],
        "properties": {
            name: {"type": "string", "enum": ["weak", "medium", "strong"]}
            for name in [
                "identity",
                "workflow_shift",
                "technical_substance",
                "product_market_fit",
                "momentum",
            ]
        },
    }
    information_need_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["question", "target_axes", "expected_decision_impact"],
        "properties": {
            "question": bounded_text,
            "target_axes": {
                "type": "array",
                "items": {"type": "string", "enum": SUPPORT_AXES},
                "minItems": 1,
                "uniqueItems": True,
            },
            "expected_decision_impact": bounded_text,
        },
    }
    tool_request_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "arguments"],
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 80},
            "arguments": {"type": "object"},
        },
    }
    score_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "object_type",
            "is_product_or_repo",
            "axes",
            "supporting_evidence",
            "negative_evidence",
            "known_gaps",
            "primary_reason",
            "rationale_short",
            "topic_tags",
            "caveats",
            "should_print",
        ],
        "properties": {
            "object_type": {
                "type": "string",
                "enum": [
                    "product",
                    "repo",
                    "package",
                    "research_tool",
                    "model_release",
                    "article",
                    "news",
                    "unknown",
                ],
            },
            "is_product_or_repo": {"type": "boolean"},
            "axes": {
                "type": "object",
                "additionalProperties": False,
                "required": AXIS_NAMES,
                "properties": axis_properties,
            },
            "supporting_evidence": {
                "type": "array",
                "items": {"$ref": "#/$defs/claim"},
                "maxItems": 8,
            },
            "negative_evidence": {
                "type": "array",
                "items": {"$ref": "#/$defs/claim"},
                "maxItems": 8,
            },
            "known_gaps": string_list,
            "primary_reason": {"type": "string", "minLength": 1, "maxLength": 80},
            "rationale_short": bounded_text,
            "topic_tags": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 40},
                "maxItems": 8,
            },
            "caveats": string_list,
            "should_print": {"type": "boolean"},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "layer2-scoring-output-v2",
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "information_sufficiency"],
        "properties": {
            "action": {"type": "string", "enum": ["use_tools", "final"]},
            "information_sufficiency": {"$ref": "#/$defs/information_sufficiency"},
            "information_need": {"$ref": "#/$defs/information_need"},
            "tool_requests": {
                "type": "array",
                "items": {"$ref": "#/$defs/tool_request"},
                "minItems": 1,
                "maxItems": 8,
            },
            "score": {"$ref": "#/$defs/score"},
        },
        "oneOf": [
            {
                "properties": {"action": {"const": "use_tools"}},
                "required": ["information_need", "tool_requests"],
                "not": {"required": ["score"]},
            },
            {
                "properties": {"action": {"const": "final"}},
                "required": ["score"],
                "not": {
                    "anyOf": [
                        {"required": ["tool_requests"]},
                        {"required": ["information_need"]},
                    ]
                },
            },
        ],
        "$defs": {
            "claim": claim_schema,
            "information_sufficiency": sufficiency_schema,
            "information_need": information_need_schema,
            "tool_request": tool_request_schema,
            "score": score_schema,
        },
    }
