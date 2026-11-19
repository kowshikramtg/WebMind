"""Shared fixtures: an isolated copy of the Lumen Valley demo per test."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest
import yaml

from webmind.core.config import config_from_dict
from webmind.runtime import WebMindRuntime
from webmind.security.identity import issue_token

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "basic-website"
JWT_SECRET = "test-secret-" + "x" * 40


def _load_setup_module():
    spec = importlib.util.spec_from_file_location("setup_demo", DEMO / "setup_demo.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def demo_dir(tmp_path: Path) -> Path:
    """A fresh copy of the demo site, generated PDFs and records database."""
    target = tmp_path / "demo"
    shutil.copytree(DEMO / "site", target / "site")
    shutil.copy(DEMO / "webmind.yaml", target / "webmind.yaml")
    setup = _load_setup_module()
    setup.build_database(target / "generated" / "records.db")
    setup.build_pdfs(target / "generated" / "docs")
    return target


def demo_config_dict(demo_dir: Path, **overrides) -> dict:
    data = yaml.safe_load((demo_dir / "webmind.yaml").read_text(encoding="utf-8"))
    data["security"]["authentication"]["jwt"]["secret"] = JWT_SECRET
    data["llm"] = {"provider": "none"}
    data["server"]["admin_api_key"] = "admin-key-for-tests-0123456789"
    data["server"]["rate_limit_per_minute"] = 0
    for key, value in overrides.items():
        data[key] = value
    return data


@pytest.fixture
def demo_config(demo_dir: Path):
    return config_from_dict(demo_config_dict(demo_dir), base_dir=demo_dir)


@pytest.fixture
async def runtime(demo_config):
    rt = WebMindRuntime(demo_config)
    await rt.initialize()
    await rt.sync()
    yield rt
    await rt.close()


def principal(rt: WebMindRuntime, user: str, roles: list[str], **attrs):
    return rt.policy.build_principal(
        tenant_id=rt.cfg.app.default_tenant, user_id=user, roles=roles, attributes=attrs, authenticated=True
    )


def token(user: str, roles: list[str], **attrs) -> str:
    return issue_token(JWT_SECRET, user_id=user, roles=roles, attributes=attrs)
