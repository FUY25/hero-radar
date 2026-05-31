from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.decision.llm_provider import DeepSeekProvider, LLMProvider


PROMPT_VERSION = "smoke-v1"
SMOKE_TASK = "llm_smoke"


def load_env_file(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_json_secrets(path: Path = ROOT / "pipeline" / "secrets.local.json") -> None:
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(payload, dict):
        return
    deepseek = payload.get("deepseek")
    if not isinstance(deepseek, dict):
        return
    env_map = {
        "api_key": "DEEPSEEK_API_KEY",
        "model": "DEEPSEEK_MODEL",
        "base_url": "DEEPSEEK_BASE_URL",
    }
    for source_key, env_key in env_map.items():
        value = deepseek.get(source_key)
        if isinstance(value, str) and value and env_key not in os.environ:
            os.environ[env_key] = value


def smoke_payload() -> dict[str, Any]:
    return {
        "text": (
            "A developer launches a new open-source repo for local AI workflow "
            "automation: https://github.com/example/demo."
        ),
        "required_schema": {
            "ok": "boolean",
            "is_project_signal": "boolean",
            "confidence": "number",
        },
    }


def run_llm_smoke(provider: LLMProvider) -> dict[str, Any]:
    return provider.complete_json(
        task=SMOKE_TASK,
        prompt_version=PROMPT_VERSION,
        input_payload=smoke_payload(),
        system_prompt=(
            "Return strict JSON only. Judge whether the text contains a software "
            "project signal."
        ),
    )


def summarize_llm_result(result: Any, provider: LLMProvider) -> dict[str, Any]:
    keys = sorted(str(key) for key in result.keys()) if isinstance(result, dict) else []
    return {
        "ok": isinstance(result, dict),
        "provider": provider.provider_name,
        "model": provider.model,
        "keys": keys[:20],
    }


def redacted_error_message(exc: Exception, provider: DeepSeekProvider) -> str:
    message = str(exc)
    if provider.api_key:
        message = message.replace(provider.api_key, "[redacted]")
    return message[:300]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one bounded DeepSeek JSON smoke call."
    )
    parser.add_argument("--model", default=None)
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args(argv)

    load_env_file()
    load_json_secrets()
    provider = DeepSeekProvider(model=args.model, timeout=args.timeout)
    if not provider.api_key:
        print(
            json.dumps(
                {
                    "ok": False,
                    "skipped": "DEEPSEEK_API_KEY is not configured",
                    "provider": provider.provider_name,
                    "model": provider.model,
                },
                sort_keys=True,
            )
        )
        return 0

    try:
        result = run_llm_smoke(provider)
        summary = summarize_llm_result(result, provider)
    except Exception as exc:
        summary = {
            "ok": False,
            "provider": provider.provider_name,
            "model": provider.model,
            "error_type": type(exc).__name__,
            "message": redacted_error_message(exc, provider),
        }
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
