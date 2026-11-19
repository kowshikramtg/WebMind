"""The standalone webmind-client package against the real ASGI app (no network)."""

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk" / "python" / "src"))

from tests.conftest import JWT_SECRET  # noqa: E402
from webmind_client import AsyncWebMindClient, WebMindAPIError, sign_user_token  # noqa: E402

from webmind.api.app import create_app  # noqa: E402


async def test_async_client_roundtrip(runtime):
    app = create_app(runtime=runtime)
    transport = httpx.ASGITransport(app=app)
    tok = sign_user_token(JWT_SECRET, user_id="m1", roles=["manager"])
    async with AsyncWebMindClient("http://wm", token=lambda: tok, transport=transport) as wm:
        assert (await wm.health())["status"] == "ok"
        res = await wm.chat("Where can I find the eligibility requirements?")
        assert res["navigation"][0]["url"].endswith("admissions.html#eligibility")
        assert await wm.search("tuition fees")
        table = await wm.analytics(
            "aggregate", {"dataset": "projects", "metrics": [{"fn": "count"}], "group_by": ["status"]}
        )
        assert sum(r["count_records"] for r in table["rows"]) == 120
        events = [e async for e in wm.stream("What are the tuition fees?")]
        assert events[0]["type"] == "plan" and events[-1]["type"] == "final"
    async with AsyncWebMindClient("http://wm", token="bad", transport=transport) as wm:
        with pytest.raises(WebMindAPIError) as exc:
            await wm.chat("hi")
        assert exc.value.status_code == 401


def test_sign_user_token_requires_user():
    with pytest.raises(ValueError):
        sign_user_token(JWT_SECRET, user_id="")
