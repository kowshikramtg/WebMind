"""Every configuration file shipped in the repository must validate."""

from pathlib import Path

import pytest

from webmind.core.config import load_config

ROOT = Path(__file__).resolve().parents[2]
CONFIGS = [
    ROOT / "examples" / "basic-website" / "webmind.yaml",
    ROOT / "examples" / "local-llm" / "webmind.yaml",
    ROOT / "deployments" / "docker" / "webmind.yaml",
]


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: str(p.relative_to(ROOT)))
def test_shipped_config_is_valid(path, monkeypatch):
    monkeypatch.setenv("WEBMIND_JWT_SECRET", "x" * 48)
    monkeypatch.setenv("WEBMIND_DATABASE_URL", "postgresql+psycopg://u:p@db:5432/webmind")
    cfg = load_config(path)
    assert cfg.security.read_only_default is True


def test_cli_init_creates_valid_config(tmp_path, monkeypatch):
    from webmind.cli.main import main

    assert main(["init", str(tmp_path), "--yes", "--source", "./site", "--auth", "host_application"]) == 0
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "WEBMIND_JWT_SECRET=" in env and ".env" in (tmp_path / ".gitignore").read_text(encoding="utf-8")
    monkeypatch.delenv("WEBMIND_JWT_SECRET", raising=False)
    cfg = load_config(tmp_path / "webmind.yaml")  # picks secrets up from the generated .env
    assert cfg.security.authentication.mode == "host_application"
    assert main(["init", str(tmp_path), "--yes"]) == 1  # refuses to overwrite


def test_cli_index_and_ask(tmp_path, capsys):
    from webmind.cli.main import main

    site = tmp_path / "site"
    site.mkdir()
    (site / "faq.md").write_text("# FAQ\n## Opening hours\nWe are open from 9 to 5 on weekdays.\n", encoding="utf-8")
    assert main(["init", str(tmp_path), "--yes", "--source", "./site"]) == 0
    cfg = str(tmp_path / "webmind.yaml")
    assert main(["index", "--config", cfg]) == 0
    assert "new=1" in capsys.readouterr().out
    assert main(["ask", "When are you open?", "--config", cfg]) == 0
    assert "9 to 5" in capsys.readouterr().out
    assert main(["connectors", "list", "--config", cfg]) == 0
