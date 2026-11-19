"""WebMind — open-source, self-hosted website intelligence infrastructure."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from webmind.version import __version__

if TYPE_CHECKING:
    from webmind.sdk import WebMind

__all__ = ["WebMind", "__version__"]


def __getattr__(name: str) -> Any:  # lazy import keeps `import webmind` lightweight
    if name == "WebMind":
        from webmind.sdk import WebMind

        return WebMind
    raise AttributeError(name)
