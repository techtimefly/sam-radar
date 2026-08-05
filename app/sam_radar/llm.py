from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import Settings

LOCAL_PROVIDERS = {"none", "ollama"}
CLOUD_PROVIDERS = {"openai-compatible"}
SUPPORTED_PROVIDERS = LOCAL_PROVIDERS | CLOUD_PROVIDERS


@dataclass(slots=True)
class LLMResponse:
    ok: bool
    text: str = ""
    error: str = ""
    provider: str = "none"
    model: str = ""
    mode: str = "disabled"


def normalized_provider(settings: Settings) -> str:
    provider = (settings.llm_provider or "none").strip().lower()
    return provider if provider in SUPPORTED_PROVIDERS else "none"


def ai_mode(settings: Settings) -> str:
    provider = normalized_provider(settings)
    if provider == "none" or not settings.enable_ai_assist:
        return "disabled"
    return "local" if provider in LOCAL_PROVIDERS else "cloud"


def warnings(settings: Settings) -> list[str]:
    provider = normalized_provider(settings)
    items: list[str] = []
    if provider not in {"none", ""} and not settings.enable_ai_assist:
        items.append("AI assist is disabled; provider settings are ignored.")
    if ai_mode(settings) == "cloud":
        items.append("Cloud AI providers may send opportunity text and proposal notes to an external service.")
    if provider in {"ollama", "openai-compatible"} and not settings.llm_model:
        items.append("LLM_MODEL is required for this provider.")
    if provider == "openai-compatible" and not settings.llm_api_key:
        items.append("LLM_API_KEY or OPENAI_API_KEY is required for openai-compatible providers.")
    return items


def settings_summary(settings: Settings) -> dict[str, Any]:
    provider = normalized_provider(settings)
    mode = ai_mode(settings)
    return {
        "provider": provider,
        "mode": mode,
        "enabled": bool(settings.enable_ai_assist and provider != "none"),
        "model": settings.llm_model,
        "baseUrl": settings.llm_base_url,
        "apiKeyConfigured": bool(settings.llm_api_key),
        "timeout": settings.llm_timeout,
        "supportedProviders": sorted(SUPPORTED_PROVIDERS),
        "warnings": warnings(settings),
    }


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider = normalized_provider(settings)
        self.mode = ai_mode(settings)

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 500) -> LLMResponse:
        if self.mode == "disabled":
            return LLMResponse(ok=False, error="AI assist is disabled.", provider=self.provider, model=self.settings.llm_model, mode=self.mode)
        if not self.settings.llm_model:
            return LLMResponse(ok=False, error="LLM_MODEL is required.", provider=self.provider, model="", mode=self.mode)
        if self.provider == "ollama":
            return self._ollama_complete(prompt, system=system, max_tokens=max_tokens)
        if self.provider == "openai-compatible":
            return self._openai_compatible_complete(prompt, system=system, max_tokens=max_tokens)
        return LLMResponse(ok=False, error=f"Unsupported LLM_PROVIDER: {self.provider}", provider=self.provider, model=self.settings.llm_model, mode=self.mode)

    def test_connection(self) -> dict[str, Any]:
        summary = settings_summary(self.settings)
        if self.mode == "disabled":
            return {"ok": True, "message": "AI assist disabled; deterministic/no-AI mode is active.", **summary}
        response = self.complete("Reply with exactly: ok", system="You are a connection test.", max_tokens=8)
        return {"ok": response.ok, "message": response.text.strip() or response.error, **summary}

    def _request_json(self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=max(1, self.settings.llm_timeout)) as response:
                return json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"LLM provider returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not reach LLM provider: {exc.reason}") from exc

    def _ollama_complete(self, prompt: str, *, system: str = "", max_tokens: int = 500) -> LLMResponse:
        base = (self.settings.llm_base_url or "http://localhost:11434").rstrip("/")
        full_prompt = f"{system.strip()}\n\n{prompt.strip()}".strip()
        try:
            data = self._request_json(
                f"{base}/api/generate",
                {"model": self.settings.llm_model, "prompt": full_prompt, "stream": False, "options": {"num_predict": max_tokens}},
            )
            return LLMResponse(ok=True, text=str(data.get("response") or ""), provider=self.provider, model=self.settings.llm_model, mode=self.mode)
        except RuntimeError as exc:
            return LLMResponse(ok=False, error=str(exc), provider=self.provider, model=self.settings.llm_model, mode=self.mode)

    def _openai_compatible_complete(self, prompt: str, *, system: str = "", max_tokens: int = 500) -> LLMResponse:
        if not self.settings.llm_api_key:
            return LLMResponse(ok=False, error="LLM_API_KEY or OPENAI_API_KEY is required.", provider=self.provider, model=self.settings.llm_model, mode=self.mode)
        base = (self.settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            data = self._request_json(
                f"{base}/chat/completions",
                {"model": self.settings.llm_model, "messages": messages, "max_tokens": max_tokens},
                {"Authorization": f"Bearer {self.settings.llm_api_key}"},
            )
            choices = data.get("choices") or []
            text = choices[0].get("message", {}).get("content", "") if choices else ""
            return LLMResponse(ok=True, text=str(text), provider=self.provider, model=self.settings.llm_model, mode=self.mode)
        except RuntimeError as exc:
            return LLMResponse(ok=False, error=str(exc), provider=self.provider, model=self.settings.llm_model, mode=self.mode)
