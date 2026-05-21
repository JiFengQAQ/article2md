"""Adapter base classes."""

from __future__ import annotations

from typing import Any, Optional

from models import Article, DEFAULT_REQUEST_HEADERS, DEFAULT_TIMEOUT


class PlatformAdapter:
    """Base class for platform-specific or generic extraction adapters."""

    def can_handle(self, url: str) -> bool:
        raise NotImplementedError

    def extract(self, url: str) -> Optional[Article]:
        raise NotImplementedError

    def _request_kwargs(self) -> dict[str, Any]:
        return {
            "headers": dict(DEFAULT_REQUEST_HEADERS),
            "timeout": DEFAULT_TIMEOUT,
        }
