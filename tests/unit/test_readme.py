"""The README's examples must stay true: configs validate, imports exist, commands parse, links resolve."""

import ast
import importlib
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from webmind.cli.main import build_parser
from webmind.core.config import config_from_dict

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text(encoding="utf-8")
sys.path.insert(0, str(ROOT / "sdk" / "python" / "src"))


def blocks(lang: str) -> list[str]:
    return re.findall(rf"```{lang}\n(.*?)```", README, re.S)


def test_yaml_examples_validate_against_the_real_schema(monkeypatch):
    monkeypatch.setenv("ANALYTICS_DB_URL", "sqlite:///./analytics.db")
    monkeypatch.setenv("WEBMIND_JWT_SECRET", "x" * 40)
    merged: dict = {}
    found = blocks("yaml")
    assert len(found) >= 3
    for block in found:
        merged.update(yaml.safe_load(block))
    cfg = config_from_dict(merged)  # raises ConfigurationError on any unknown key or invalid value
    assert {s.type for s in cfg.sources} == {"website", "filesystem"}
    assert cfg.llm.provider == "ollama" and cfg.vector_store.provider == "database"
    assert cfg.security.authentication.mode == "host_application"
    assert cfg.dataset("projects").column("budget").access is not None  # the field-level policy shown


def test_python_examples_parse_and_only_use_real_apis():
    from webmind_client import AsyncWebMindClient, WebMindClient

    from webmind import WebMind

    sources = blocks("python")
    assert len(sources) >= 3
    for src in sources:
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in ("webmind", "webmind_client"):
                mod = importlib.import_module(node.module)
                for alias in node.names:
                    assert hasattr(mod, alias.name), f"{node.module}.{alias.name} does not exist"
    for cls, names in (
        (WebMindClient, ["chat", "close"]),
        (AsyncWebMindClient, ["stream"]),
        (WebMind, ["initialize", "ask", "close"]),
    ):
        for n in names:
            assert hasattr(cls, n), f"{cls.__name__}.{n}"
    assert "user_id" in WebMind.ask.__code__.co_varnames and "attributes" in WebMind.ask.__kwdefaults__


def test_cli_commands_in_the_readme_parse_with_the_real_cli():
    parser = build_parser()
    count = 0
    for block in blocks("bash"):
        for line in block.splitlines():
            line = line.split("  #")[0].strip()
            if line.startswith("webmind "):
                parser.parse_args(shlex.split(line)[1:])
                count += 1
            match = re.match(r"python (\S+\.py)", line)
            if match:
                assert (ROOT / match.group(1)).exists(), match.group(1)
    assert count >= 5


def test_widget_attributes_in_the_readme_are_implemented():
    widget = (ROOT / "src" / "webmind" / "api" / "static" / "webmind-widget.js").read_text(encoding="utf-8")
    used = set(re.findall(r"\b(data-[a-z-]+)=", README)) | set(re.findall(r"`(data-[a-z-]+)", README))
    assert {"data-endpoint", "data-token-url", "data-login-url", "data-title", "data-suggestions"} <= used
    for attr in used:
        assert f'"{attr}"' in widget, f"{attr} is documented but not implemented"


@pytest.mark.skipif(
    shutil.which("node") is None or not (ROOT / "sdk/react/dist/index.js").exists(), reason="needs built sdk/react"
)
def test_react_imports_in_the_readme_exist():
    out = (
        subprocess.run(  # noqa: S603
            [shutil.which("node"), "-e", "import('./dist/index.js').then(m=>console.log(Object.keys(m).join(',')))"],
            cwd=ROOT / "sdk" / "react",
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        .stdout.strip()
        .split(",")
    )
    wanted = {n.strip() for m in re.findall(r'import \{([^}]*)\} from "@webmind/react"', README) for n in m.split(",")}
    assert {"WebMindProvider", "WebMindFloating"} <= wanted
    assert wanted <= set(out), wanted - set(out)


def test_links_and_images_resolve():
    targets = set(re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", README)) | set(re.findall(r'src="([^"]+)"', README))
    local = {t for t in targets if not t.startswith(("http://", "https://", "mailto:"))}
    assert len(local) >= 15
    missing = sorted(t for t in local if not (ROOT / t).exists())
    assert not missing, f"README links to missing files: {missing}"


def test_readme_does_not_claim_unpublished_packages_or_unverified_badges():
    assert "pip install webmind" not in README and "npm install @webmind/react\n" not in README
    assert "actions/workflows" not in README  # no CI badge before CI has actually run
    assert "not yet published to PyPI or npm" in README
