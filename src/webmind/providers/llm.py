"""Concrete LLM providers.

- ``ollama``     — local models via the Ollama HTTP API.
- ``openai``     — any OpenAI-compatible Chat Completions endpoint (OpenAI, vLLM,
                   LM Studio, llama.cpp server, Together, Groq, ... via ``base_url``).
- ``anthropic``  — Claude via the official ``anthropic`` SDK (optional extra).

Each provider reports what it supports through ``ModelCapabilities``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from webmind.core.config import LLMConfig
from webmind.core.errors import ProviderError, ProviderNotInstalledError
from webmind.providers.base import ChatMessage, LLMProvider, LLMResult, ModelCapabilities, ModelInfo


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline $defs and close objects (additionalProperties: false) for strict JSON-schema modes."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node and node["$ref"].startswith("#/$defs/"):
                return walk(defs[node["$ref"].split("/")[-1]])
            out = {k: walk(v) for k, v in node.items() if k not in ("$defs", "title", "default")}
            if out.get("type") == "object" and "properties" in out:
                out["additionalProperties"] = False
                out["required"] = list(out["properties"].keys())
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


# ------------------------------------------------------------------- Ollama


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, cfg: LLMConfig, client: httpx.AsyncClient | None = None) -> None:
        self.cfg = cfg
        self.base_url = (cfg.base_url or "http://localhost:11434").rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=cfg.timeout_seconds)

    @property
    def info(self) -> ModelInfo:
        return ModelInfo(
            provider=self.name,
            model=self.cfg.model or "",
            capabilities=ModelCapabilities(streaming=True, structured_output=True, tool_calling=False),
        )

    def _payload(self, messages, temperature, max_tokens, stream: bool) -> dict[str, Any]:
        return {
            "model": self.cfg.model,
            "messages": [m.model_dump() for m in messages],
            "stream": stream,
            "options": {
                "temperature": self.cfg.temperature if temperature is None else temperature,
                "num_predict": max_tokens or self.cfg.max_tokens,
            },
        }

    async def generate(self, messages, *, temperature=None, max_tokens=None, json_schema=None):
        payload = self._payload(messages, temperature, max_tokens, stream=False)
        if json_schema is not None:
            payload["format"] = json_schema
        try:
            r = await self._client.post(f"{self.base_url}/api/chat", json=payload)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"ollama request failed: {exc}") from exc
        data = r.json()
        return LLMResult(
            text=data.get("message", {}).get("content", ""),
            usage={
                "input_tokens": int(data.get("prompt_eval_count") or 0),
                "output_tokens": int(data.get("eval_count") or 0),
            },
            finish_reason=data.get("done_reason"),
        )

    async def stream(self, messages, *, temperature=None, max_tokens=None) -> AsyncIterator[str]:
        payload = self._payload(messages, temperature, max_tokens, stream=True)
        try:
            async with self._client.stream("POST", f"{self.base_url}/api/chat", json=payload) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    piece = data.get("message", {}).get("content")
                    if piece:
                        yield piece
                    if data.get("done"):
                        break
        except httpx.HTTPError as exc:
            raise ProviderError(f"ollama stream failed: {exc}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()


# ------------------------------------------------------- OpenAI-compatible


class OpenAICompatibleProvider(LLMProvider):
    name = "openai"

    def __init__(self, cfg: LLMConfig, client: httpx.AsyncClient | None = None) -> None:
        self.cfg = cfg
        self.base_url = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")
        headers = {}
        if cfg.api_key is not None:
            headers["Authorization"] = f"Bearer {cfg.api_key.get_secret_value()}"
        self._client = client or httpx.AsyncClient(timeout=cfg.timeout_seconds, headers=headers)

    @property
    def info(self) -> ModelInfo:
        return ModelInfo(
            provider=self.name,
            model=self.cfg.model or "",
            capabilities=ModelCapabilities(streaming=True, structured_output=True, tool_calling=True),
        )

    def _payload(self, messages, temperature, max_tokens) -> dict[str, Any]:
        return {
            "model": self.cfg.model,
            "messages": [m.model_dump() for m in messages],
            "temperature": self.cfg.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.cfg.max_tokens,
        }

    async def generate(self, messages, *, temperature=None, max_tokens=None, json_schema=None):
        payload = self._payload(messages, temperature, max_tokens)
        if json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": _strict_schema(json_schema), "strict": True},
            }
        try:
            r = await self._client.post(f"{self.base_url}/chat/completions", json=payload)
            if r.status_code == 400 and json_schema is not None:
                # Some compatible servers lack json_schema support; fall back to JSON mode.
                payload["response_format"] = {"type": "json_object"}
                r = await self._client.post(f"{self.base_url}/chat/completions", json=payload)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"openai-compatible request failed: {exc}") from exc
        data = r.json()
        choice = data["choices"][0]
        usage = data.get("usage") or {}
        return LLMResult(
            text=choice["message"].get("content") or "",
            usage={
                "input_tokens": int(usage.get("prompt_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or 0),
            },
            finish_reason=choice.get("finish_reason"),
        )

    async def stream(self, messages, *, temperature=None, max_tokens=None) -> AsyncIterator[str]:
        payload = {**self._payload(messages, temperature, max_tokens), "stream": True}
        try:
            async with self._client.stream("POST", f"{self.base_url}/chat/completions", json=payload) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    body = line[5:].strip()
                    if body == "[DONE]":
                        break
                    data = json.loads(body)
                    if not data.get("choices"):
                        continue
                    piece = data["choices"][0].get("delta", {}).get("content")
                    if piece:
                        yield piece
        except httpx.HTTPError as exc:
            raise ProviderError(f"openai-compatible stream failed: {exc}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------- Anthropic


class AnthropicProvider(LLMProvider):
    """Claude via the official SDK (``pip install 'webmind[anthropic]'``).

    Notes on the current API surface:
    - sampling parameters (temperature) are not sent; current models reject them;
    - thinking is adaptive by default on current models, so ``max_tokens`` should leave
      room for it (WebMind's default is 4096);
    - structured output uses ``output_config.format`` (json_schema);
    - a ``refusal`` stop reason is surfaced as a ProviderError, and by default the
      server-side refusal fallback (``fallbacks: "default"``) is enabled.
    """

    name = "anthropic"
    _FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(self, cfg: LLMConfig, client: Any | None = None) -> None:
        self.cfg = cfg
        if client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise ProviderNotInstalledError("anthropic", "anthropic") from exc
            kwargs: dict[str, Any] = {
                "api_key": cfg.api_key.get_secret_value() if cfg.api_key else None,
                "timeout": cfg.timeout_seconds,
            }
            if cfg.base_url:
                kwargs["base_url"] = cfg.base_url
            client = anthropic.AsyncAnthropic(**kwargs)
        self._client = client

    @property
    def info(self) -> ModelInfo:
        return ModelInfo(
            provider=self.name,
            model=self.cfg.model or "",
            capabilities=ModelCapabilities(streaming=True, structured_output=True, tool_calling=True),
        )

    def _request(self, messages: Sequence[ChatMessage], max_tokens: int | None) -> dict[str, Any]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        convo = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
        req: dict[str, Any] = {
            "model": self.cfg.model,
            "max_tokens": max_tokens or self.cfg.max_tokens,
            "messages": convo,
        }
        if system:
            req["system"] = system
        output_config: dict[str, Any] = {}
        if self.cfg.effort:
            output_config["effort"] = self.cfg.effort
        if output_config:
            req["output_config"] = output_config
        if self.cfg.refusal_fallback == "default":
            req["betas"] = [self._FALLBACK_BETA]
            req["fallbacks"] = "default"
        return req

    @staticmethod
    def _text(message: Any) -> str:
        return "".join(b.text for b in message.content if getattr(b, "type", None) == "text")

    def _check(self, message: Any) -> None:
        if getattr(message, "stop_reason", None) == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise ProviderError(f"anthropic: request declined by the model (category={category})")

    async def generate(self, messages, *, temperature=None, max_tokens=None, json_schema=None):
        req = self._request(messages, max_tokens)
        if json_schema is not None:
            req.setdefault("output_config", {})["format"] = {
                "type": "json_schema",
                "schema": _strict_schema(json_schema),
            }
        try:
            message = await self._client.beta.messages.create(**req)
        except ProviderError:
            raise
        except Exception as exc:  # SDK error hierarchy; re-raised as a provider error
            raise ProviderError(f"anthropic request failed: {exc}") from exc
        self._check(message)
        usage = getattr(message, "usage", None)
        return LLMResult(
            text=self._text(message),
            usage={
                "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            },
            finish_reason=getattr(message, "stop_reason", None),
        )

    async def stream(self, messages, *, temperature=None, max_tokens=None) -> AsyncIterator[str]:
        req = self._request(messages, max_tokens)
        try:
            async with self._client.beta.messages.stream(**req) as stream:
                async for text in stream.text_stream:
                    yield text
                final = await stream.get_final_message()
        except Exception as exc:
            raise ProviderError(f"anthropic stream failed: {exc}") from exc
        self._check(final)

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            await close()


def create_llm(cfg: LLMConfig) -> LLMProvider | None:
    """Build the configured LLM provider. ``provider: none`` returns None (extractive mode)."""
    if cfg.provider == "none":
        return None
    if cfg.provider == "ollama":
        return OllamaProvider(cfg)
    if cfg.provider == "openai":
        return OpenAICompatibleProvider(cfg)
    if cfg.provider == "anthropic":
        return AnthropicProvider(cfg)
    raise ProviderError(f"unknown llm provider: {cfg.provider}")  # pragma: no cover
