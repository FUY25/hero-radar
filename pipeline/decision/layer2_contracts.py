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
                "not": {"anyOf": [{"required": ["tool_requests"]}]},
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
