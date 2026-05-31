from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Protocol


DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEEPSEEK_PRO_MODEL = "deepseek-v4-pro"


class LLMProvider(Protocol):
    provider_name: str
    model: str

    def complete_json(
        self,
        *,
        task: str,
        prompt_version: str,
        input_payload: dict[str, Any],
        system_prompt: str = "",
    ) -> dict[str, Any]:
        ...


class FakeLLMProvider:
    provider_name = "fake"
    model = "fake-json"

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete_json(
        self,
        *,
        task: str,
        prompt_version: str,
        input_payload: dict[str, Any],
        system_prompt: str = "",
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "task": task,
                "prompt_version": prompt_version,
                "input_payload": input_payload,
                "system_prompt": system_prompt,
            }
        )
        if not self._responses:
            raise RuntimeError("FakeLLMProvider has no responses left")
        return self._responses.pop(0)


class DeepSeekProvider:
    provider_name = "deepseek"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: int = 60,
        max_retries: int = 2,
    ) -> None:
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.model = model or os.environ.get("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL)
        self.base_url = (
            base_url or os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL)
        ).rstrip("/")
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))

    def __repr__(self) -> str:
        return (
            f"DeepSeekProvider(model={self.model!r}, base_url={self.base_url!r}, "
            f"api_key_configured={bool(self.api_key)})"
        )

    def build_payload(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        temperature: float = 0,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        system_content = system_prompt.strip() or "Return strict JSON only."
        if "json" not in system_content.lower():
            system_content = f"{system_content}\nReturn strict JSON only."
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_content},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True),
                },
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
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
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")
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
                break
            except (TimeoutError, urllib.error.URLError) as exc:
                last_error = exc
        else:
            raise last_error or RuntimeError("DeepSeek request failed")
        content = body["choices"][0]["message"]["content"]
        if not content:
            raise RuntimeError("DeepSeek returned empty JSON content")
        return json.loads(content)
