import pytest

from webmind.core.config import config_from_dict, load_config
from webmind.core.errors import ConfigurationError


def test_defaults_are_valid_and_safe():
    cfg = config_from_dict({})
    assert cfg.security.read_only_default is True
    assert cfg.llm.provider == "none"
    assert cfg.embeddings.provider == "hashing"
    assert cfg.telemetry.enabled is False
    assert cfg.server.host == "127.0.0.1"


def test_read_only_cannot_be_disabled():
    with pytest.raises(ConfigurationError, match="read_only_default"):
        config_from_dict({"security": {"read_only_default": False}})


def test_env_substitution_and_defaults(monkeypatch):
    monkeypatch.setenv("WM_TEST_MODEL", "llama3.1")
    cfg = config_from_dict(
        {"llm": {"provider": "ollama", "model": "${WM_TEST_MODEL}", "base_url": "${WM_UNSET:-http://x:1}"}}
    )
    assert cfg.llm.model == "llama3.1"
    assert cfg.llm.base_url == "http://x:1"


def test_unset_secret_is_treated_as_missing(monkeypatch):
    monkeypatch.delenv("WM_NOT_SET", raising=False)
    with pytest.raises(ConfigurationError, match="WM_NOT_SET"):
        config_from_dict({"llm": {"provider": "anthropic", "model": "m", "api_key": "${WM_NOT_SET}"}})


def test_short_hs256_secret_rejected():
    with pytest.raises(ConfigurationError, match="at least 32"):
        config_from_dict({"security": {"authentication": {"mode": "host_application", "jwt": {"secret": "short"}}}})


def test_unknown_keys_rejected():
    with pytest.raises(ConfigurationError):
        config_from_dict({"llm": {"provider": "none", "unknown_option": 1}})


def test_source_discriminator_and_unique_ids():
    cfg = config_from_dict(
        {
            "sources": [
                {"id": "site", "type": "website", "url": "https://example.org"},
                {"id": "docs", "type": "filesystem", "path": "./docs"},
            ]
        }
    )
    assert [s.type for s in cfg.sources] == ["website", "filesystem"]
    with pytest.raises(ConfigurationError, match="unique"):
        config_from_dict({"sources": [{"id": "a", "type": "filesystem", "path": "."}] * 2})


def test_dataset_column_references_validated():
    base = {
        "id": "p",
        "connection": "sqlite:///x.db",
        "table": "projects",
        "columns": [{"name": "id", "type": "integer"}, {"name": "created", "type": "date"}],
    }
    config_from_dict({"datasets": [{**base, "date_column": "created"}]})
    with pytest.raises(ConfigurationError, match="unknown column"):
        config_from_dict({"datasets": [{**base, "date_column": "missing"}]})
    with pytest.raises(ConfigurationError, match="date or datetime"):
        config_from_dict({"datasets": [{**base, "date_column": "id"}]})


def test_public_access_rule_cannot_have_roles():
    with pytest.raises(ConfigurationError):
        config_from_dict(
            {
                "sources": [
                    {
                        "id": "a",
                        "type": "filesystem",
                        "path": ".",
                        "access": {"visibility": "public", "roles": ["admin"]},
                    }
                ]
            }
        )


def test_cors_wildcard_rejected_in_production():
    with pytest.raises(ConfigurationError):
        config_from_dict({"app": {"environment": "production"}, "server": {"cors_origins": ["*"]}})


def test_load_config_file(tmp_path):
    (tmp_path / "webmind.yaml").write_text("app:\n  name: demo\n", encoding="utf-8")
    cfg = load_config(tmp_path / "webmind.yaml")
    assert cfg.app.name == "demo"
    assert cfg.resolve_path("data") == (tmp_path / "data").resolve()
