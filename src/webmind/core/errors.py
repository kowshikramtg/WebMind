"""WebMind exception hierarchy."""

from __future__ import annotations


class WebMindError(Exception):
    """Base class for all WebMind errors."""


class ConfigurationError(WebMindError):
    """Configuration is missing or invalid."""


class ProviderError(WebMindError):
    """An LLM / embedding / reranking provider failed."""


class ProviderNotInstalledError(ProviderError):
    """An optional dependency required by a configured provider is not installed."""

    def __init__(self, provider: str, extra: str) -> None:
        super().__init__(
            f"provider '{provider}' requires optional dependencies; "
            f"install with: pip install 'webmind[{extra}]'"
        )


class AuthenticationError(WebMindError):
    """The caller's identity could not be verified."""


class AuthorizationError(WebMindError):
    """The verified principal is not permitted to perform an operation."""

    def __init__(self, message: str, *, action: str | None = None, resource: str | None = None) -> None:
        super().__init__(message)
        self.action = action
        self.resource = resource


class ReadOnlyViolationError(AuthorizationError):
    """An operation attempted to modify a source while read-only mode is enforced."""


class QueryValidationError(WebMindError):
    """A structured query failed validation against the dataset allowlist."""


class ConnectorError(WebMindError):
    """A source connector failed to discover or fetch content."""


class NotFoundError(WebMindError):
    """The requested object does not exist or is not visible to the principal."""
