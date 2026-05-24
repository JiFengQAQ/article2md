"""适配器实现"""

from adapters.base import PlatformAdapter
from adapters.hima_community_adapter import HimaCommunityAdapter
from adapters.playwright_adapter import PlaywrightAdapter
from adapters.requests_adapter import RequestsAdapter

__all__ = [
    "PlatformAdapter",
    "HimaCommunityAdapter",
    "RequestsAdapter",
    "PlaywrightAdapter",
]
