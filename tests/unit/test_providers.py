import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from webmind.core.config import EmbeddingsConfig, LLMConfig
from webmind.core.errors import ProviderError
from webmind.providers.base import ChatMessage, extract_json
from webmind.providers.embeddings import HashingEmbedder, OllamaEmbedder, OpenAICompatibleEmbedder
from webmind.providers.llm import AnthropicProvider, OllamaProvider, OpenAICompatibleProvider, create_llm

MSGS = [ChatMessage(role="system", content="sys"), ChatMessage(role="user", content="hi")]


class Answer(BaseModel):
    value: int


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json('Sure! {"a": 3} hope that helps') == {"a": 3}
    with pytest.raises(ValueError):
        extract_json("no json")


def test_none_provider_means_extractive():
    assert create_llm(LLMConfig(provider="none")) is None


async def test_ollama_generate_and_stream():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.update(body)
        if body["stream"]:
            lines = [json.dumps({"message": {"content": p}, "done": False}) for p in ("Hel", "lo")]
            lines.append(json.dumps({"done": True}))
            return httpx.Response(200, text="\n".join(lines))
        return httpx.Response(
            200, json={"message": {"content": '{"value": 7}'}, "prompt_eval_count": 3, "eval_count": 2}
        )

    p = OllamaProvider(
        LLMConfig(provider="ollama", model="m"), client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    res = await p.generate_structured(MSGS, Answer)
    assert res.value == 7
    assert seen["format"]["properties"]["value"]["type"] == "integer"
    assert "".join([x async for x in p.stream(MSGS)]) == "Hello"


async def test_openai_compatible_generate_stream_and_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer k"
        body = json.loads(request.content)
        if body.get("stream"):
            sse = "".join(f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}\n\n" for c in ("a", "b"))
            return httpx.Response(200, text=sse + "data: [DONE]\n\n")
        if body["model"] == "broken":
            return httpx.Response(500, json={})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer k"})
    p = OpenAICompatibleProvider(LLMConfig(provider="openai", model="m", api_key="k"), client=client)
    assert (await p.generate(MSGS)).text == "ok"
    assert "".join([x async for x in p.stream(MSGS)]) == "ab"
    bad = OpenAICompatibleProvider(LLMConfig(provider="openai", model="broken", api_key="k"), client=client)
    with pytest.raises(ProviderError):
        await bad.generate(MSGS)


class _FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _anthropic(response, **cfg):
    fake = SimpleNamespace(beta=SimpleNamespace(messages=_FakeMessages(response)))
    conf = LLMConfig(provider="anthropic", model="claude-opus-5-5", api_key="test-key", **cfg)
    return AnthropicProvider(conf, client=fake), fake.beta.messages


async def test_anthropic_request_shape():
    resp = SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text='{"value": 4}')],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=5, output_tokens=6),
    )
    p, calls = _anthropic(resp, effort="low")
    out = await p.generate_structured(MSGS, Answer)
    assert out.value == 4
    req = calls.calls[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["system"] == "sys"
    assert req["messages"] == [{"role": "user", "content": "hi"}]
    assert "temperature" not in req  # current models reject sampling parameters
    assert req["output_config"]["effort"] == "low"
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert req["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert req["fallbacks"] == "default" and req["betas"] == ["server-side-fallback-2026-07-01"]


async def test_anthropic_refusal_raises_and_fallback_can_be_disabled():
    resp = SimpleNamespace(
        content=[], stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"), usage=None
    )
    p, calls = _anthropic(resp, refusal_fallback="none")
    with pytest.raises(ProviderError, match="declined"):
        await p.generate(MSGS)
    assert "fallbacks" not in calls.calls[0]


async def test_hashing_embedder_properties():
    e = HashingEmbedder(256)
    a, b, c = await e.embed_documents(
        ["Eligibility requirements for admission", "admission eligibility requirement", "library opening hours"]
    )
    assert len(a) == 256 and abs(sum(x * x for x in a) - 1) < 1e-6

    def cos(x, y):
        return sum(i * j for i, j in zip(x, y, strict=True))

    assert cos(a, b) > cos(a, c)
    assert (await e.embed_query("Eligibility requirements for admission")) == a  # deterministic
    assert e.model_id == "webmind-hashing-v1:256"


async def test_remote_embedders():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path.endswith("/api/embed"):
            return httpx.Response(200, json={"embeddings": [[1.0, 0.0, 0.0] for _ in body["input"]]})
        return httpx.Response(
            200, json={"data": [{"index": i, "embedding": [0.0, 1.0]} for i, _ in enumerate(body["input"])]}
        )

    t = httpx.MockTransport(handler)
    o = OllamaEmbedder(EmbeddingsConfig(provider="ollama", model="nomic"), client=httpx.AsyncClient(transport=t))
    assert await o.embed_documents(["a", "b"]) == [[1.0, 0.0, 0.0]] * 2
    assert o.dimensions == 3 and o.model_id == "ollama:nomic"
    oa = OpenAICompatibleEmbedder(
        EmbeddingsConfig(provider="openai", model="te", batch_size=1), client=httpx.AsyncClient(transport=t)
    )
    assert await oa.embed_documents(["a", "b", "c"]) == [[0.0, 1.0]] * 3
