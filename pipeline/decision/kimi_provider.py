from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_KIMI_BASE_URL = "https://api.moonshot.ai/v1"
DEFAULT_KIMI_SCOUT_MODEL = "kimi-k2.5"
DEFAULT_KIMI_SCORING_MODEL = "kimi-k2.5"
DEFAULT_KIMI_DEEPDIVE_MODEL = "kimi-k2.6"
ROOT = Path(__file__).resolve().parents[2]
LOCAL_SECRETS_PATH = ROOT / "pipeline" / "secrets.local.json"


def load_local_kimi_config(path: Path | None = None) -> dict[str, str]:
    active_path = path or LOCAL_SECRETS_PATH
    try:
        payload = json.loads(active_path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, TypeError, ValueError):
        return {}
    kimi = payload.get("kimi") if isinstance(payload, dict) else {}
    if not isinstance(kimi, dict):
        return {}
    return {
        "api_key": str(kimi.get("api_key") or ""),
        "base_url": str(kimi.get("base_url") or ""),
        "model": str(kimi.get("model") or ""),
    }


def kimi_temperature(model: str, requested: float) -> float:
    if str(model or "").lower().startswith("kimi-k2"):
        return 1
    return requested


class KimiProvider:
    provider_name = "kimi"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: int = 90,
        max_retries: int = 2,
        max_output_tokens: int | None = None,
    ) -> None:
        local_config = load_local_kimi_config()
        self.api_key = (
            api_key
            or os.environ.get("KIMI_API_KEY", "")
            or os.environ.get("MOONSHOT_API_KEY", "")
            or local_config.get("api_key", "")
        )
        self.model = (
            model
            or os.environ.get("KIMI_MODEL", "")
            or local_config.get("model", "")
            or DEFAULT_KIMI_SCORING_MODEL
        )
        self.base_url = (
            base_url
            or os.environ.get("KIMI_BASE_URL", "")
            or os.environ.get("MOONSHOT_BASE_URL", "")
            or local_config.get("base_url", "")
            or DEFAULT_KIMI_BASE_URL
        ).rstrip("/")
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.max_output_tokens = (
            None if max_output_tokens is None else max(1, int(max_output_tokens))
        )

    def __repr__(self) -> str:
        return (
            f"KimiProvider(model={self.model!r}, base_url={self.base_url!r}, "
            f"api_key_configured={bool(self.api_key)})"
        )

    @property
    def actual_temperature(self) -> float:
        return kimi_temperature(self.model, 0)

    @property
    def response_format(self) -> dict[str, str]:
        return {"type": "json_object"}

    def handshake(self) -> dict[str, Any]:
        host = urlparse(self.base_url).netloc
        if not self.api_key:
            return {
                "ok": False,
                "base_url_host": host,
                "key_configured": False,
                "models_count": 0,
                "reason": "Kimi key not configured",
            }
        request = urllib.request.Request(
            f"{self.base_url}/models",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return {
                "ok": False,
                "base_url_host": host,
                "key_configured": True,
                "models_count": 0,
                "status": exc.code,
                "reason": "HTTPError",
            }
        except (TimeoutError, urllib.error.URLError, ValueError) as exc:
            return {
                "ok": False,
                "base_url_host": host,
                "key_configured": True,
                "models_count": 0,
                "reason": type(exc).__name__,
            }
        data = body.get("data") if isinstance(body, dict) else []
        return {
            "ok": True,
            "base_url_host": host,
            "key_configured": True,
            "models_count": len(data) if isinstance(data, list) else 0,
        }

    def build_payload(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        temperature: float = 0,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = True,
    ) -> dict[str, Any]:
        system_content = system_prompt.strip() or "Return strict JSON only."
        if json_mode and "json" not in system_content.lower():
            system_content = f"{system_content}\nReturn strict JSON only."
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_content},
                {
                    "role": "user",
                    "content": json.dumps(
                        user_payload, ensure_ascii=False, sort_keys=True
                    ),
                },
            ],
            "temperature": kimi_temperature(self.model, temperature),
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        active_max_tokens = (
            self.max_output_tokens if max_tokens is None else max(1, int(max_tokens))
        )
        if active_max_tokens is not None:
            payload["max_tokens"] = active_max_tokens
        if tools:
            payload["tools"] = tools
        return payload

    def complete_json(
        self,
        *,
        task: str,
        prompt_version: str,
        input_payload: dict[str, Any],
        system_prompt: str = "",
    ) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("KIMI_API_KEY or MOONSHOT_API_KEY is not configured")
        payload = self.build_payload(
            system_prompt=system_prompt,
            user_payload=input_payload,
            temperature=0,
        )
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        last_error: BaseException | None = None
        for _attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                content = body["choices"][0]["message"]["content"]
                if not content:
                    raise RuntimeError("Kimi returned empty JSON content")
                return json.loads(content)
            except (
                TimeoutError,
                urllib.error.URLError,
                RuntimeError,
                ValueError,
                KeyError,
                IndexError,
            ) as exc:
                last_error = exc
        raise last_error or RuntimeError("Kimi request failed")


class KimiWebSearchClient:
    """Small wrapper around Kimi's built-in `$web_search` tool."""

    WEB_SEARCH_TOOL = {"type": "builtin_function", "function": {"name": "$web_search"}}

    def __init__(self, *, provider: KimiProvider) -> None:
        self.provider = provider

    def search(self, *, query: str, max_results: int = 5) -> dict[str, Any]:
        if not self.provider.api_key:
            raise RuntimeError("KIMI_API_KEY or MOONSHOT_API_KEY is not configured")
        payload = self.provider.build_payload(
            system_prompt=(
                "Use web search to gather concise external context. "
                "Return a compact plain-text summary with URLs when available."
            ),
            user_payload={"query": query, "max_results": max(1, min(8, int(max_results)))},
            temperature=0,
            max_tokens=1200,
            tools=[self.WEB_SEARCH_TOOL],
            json_mode=False,
        )
        request = urllib.request.Request(
            f"{self.provider.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.provider.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.provider.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = str(body["choices"][0]["message"].get("content") or "")
        return {"query": query, "content": content[:6000], "model": self.provider.model}
