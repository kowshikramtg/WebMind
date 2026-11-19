"""Provider abstractions.

WebMind never talks to a model vendor directly from the intelligence layer; it uses
these interfaces. Providers declare their capabilities so callers never assume a
feature (streaming, structured output, tool calling) that a model does not support.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field, ValidationError

from webmind.core.errors import ProviderError

T = TypeVar("T", bound=BaseModel)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ModelCapabilities(BaseModel):
    streaming: bool = False
    structured_output: bool = False
    tool_calling: bool = False
    max_context_tokens: int | None = None


class ModelInfo(BaseModel):
    provider: str
    model: str
    capabilities: ModelCapabilities


class LLMResult(BaseModel):
    text: str
    usage: dict[str, int] = Field(default_factory=dict)
    finish_reason: str | None = None


class LLMProvider(ABC):
    """Text-generation provider interface."""

    name: str = "abstract"

    @property
    @abstractmethod
    def info(self) -> ModelInfo: ...

    @abstractmethod
    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResult:
        """Generate a completion. When ``json_schema`` is given and the provider supports
        structured output, the provider constrains output to that schema."""

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Stream a completion. Providers without native streaming yield a single chunk."""
        result = await self.generate(messages, temperature=temperature, max_tokens=max_tokens)
        yield result.text

    async def generate_structured(self, messages: Sequence[ChatMessage], schema: type[T]) -> T:
        """Generate an object validated against ``schema``.

        Uses native structured output when supported, otherwise instructs the model to
        emit JSON. The result is always validated with Pydantic; one repair attempt is
        made on validation failure.
        """
        json_schema = schema.model_json_schema()
        msgs = list(messages)
        if not self.info.capabilities.structured_output:
            msgs.append(
                ChatMessage(
                    role="user",
                    content="Respond with a single JSON object that matches this JSON schema, "
                    f"and nothing else:\n{json.dumps(json_schema)}",
                )
            )
        last_error: Exception | None = None
        for _ in range(2):
            result = await self.generate(
                msgs,
                temperature=0.0,
                json_schema=json_schema if self.info.capabilities.structured_output else None,
            )
            try:
                return schema.model_validate(extract_json(result.text))
            except (ValueError, ValidationError) as exc:
                last_error = exc
                msgs = [
                    *msgs,
                    ChatMessage(role="assistant", content=result.text),
                    ChatMessage(
                        role="user",
                        content=f"That output was invalid ({str(exc)[:300]}). Return only corrected JSON.",
                    ),
                ]
        raise ProviderError(f"{self.name}: structured output failed validation: {last_error}")

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None


def extract_json(text: str) -> Any:
    """Parse the first JSON object in ``text`` (tolerates code fences / surrounding prose)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found in model output")
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(text[start:])
    return obj


class EmbeddingProvider(ABC):
    """Embedding provider interface.

    ``model_id`` must uniquely identify the model *and* any preprocessing that affects
    vectors; it is recorded in the index so a model change is detected and triggers a
    rebuild instead of silently mixing incompatible vectors.
    """

    name: str = "abstract"

    @property
    @abstractmethod
    def model_id(self) -> str: ...

    @property
    @abstractmethod
    def dimensions(self) -> int: ...

    @abstractmethod
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]

    async def aclose(self) -> None:  # pragma: no cover
        return None


class Reranker(ABC):
    """Cross-encoder style reranker: scores (query, passage) pairs."""

    name: str = "abstract"

    @abstractmethod
    async def score(self, query: str, passages: Sequence[str]) -> list[float]: ...
