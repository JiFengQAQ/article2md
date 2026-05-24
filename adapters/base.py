"""适配器基类."""

from __future__ import annotations

from typing import Any, Optional

from models import Article, DEFAULT_REQUEST_HEADERS, DEFAULT_TIMEOUT


class PlatformAdapter:
    """平台特定或通用抽取适配器的基类."""

    def can_handle(self, url: str) -> bool:
        raise NotImplementedError

    def extract(self, url: str) -> Optional[Article]:
        raise NotImplementedError

    def _request_kwargs(self) -> dict[str, Any]:
        return {
            "headers": dict(DEFAULT_REQUEST_HEADERS),
            "timeout": DEFAULT_TIMEOUT,
        }
