"""Public extractor API and adapter orchestration."""

from __future__ import annotations

import logging
from typing import Optional

from adapters import HimaCommunityAdapter, PlatformAdapter, PlaywrightAdapter, RequestsAdapter
from models import Article, DEFAULT_RETRIES, DEFAULT_TIMEOUT, IMAGE_DIMENSION_FAIL_OPEN

logger = logging.getLogger(__name__)


class ArticleExtractor:
    """Auto-select adapter by platform and fallback chain."""

    def __init__(
        self,
        timeout: int = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        image_fail_open: bool = IMAGE_DIMENSION_FAIL_OPEN,
    ):
        self.timeout = timeout
        self.retries = retries
        self.image_fail_open = image_fail_open
        self.adapters: list[PlatformAdapter] = [
            HimaCommunityAdapter(image_fail_open=image_fail_open),
            RequestsAdapter(timeout=timeout, image_fail_open=image_fail_open),
            PlaywrightAdapter(timeout=timeout, retries=retries, image_fail_open=image_fail_open),
        ]

    def extract(self, url: str) -> Optional[Article]:
        for adapter in self.adapters:
            if not adapter.can_handle(url):
                continue

            logger.info("Using %s for %s", type(adapter).__name__, url)
            try:
                article = adapter.extract(url)
            except Exception as exc:
                logger.error("%s crashed: %s", type(adapter).__name__, exc)
                article = None

            if article:
                return article

            if isinstance(adapter, HimaCommunityAdapter):
                logger.warning("%s returned None for known platform, skipping fallback", type(adapter).__name__)
                return None

        return None


_extractor = ArticleExtractor()


def _choose_extractor(
    timeout: int,
    retries: int,
    image_fail_open: bool,
) -> ArticleExtractor:
    if (timeout, retries, image_fail_open) == (DEFAULT_TIMEOUT, DEFAULT_RETRIES, IMAGE_DIMENSION_FAIL_OPEN):
        return _extractor
    return ArticleExtractor(timeout=timeout, retries=retries, image_fail_open=image_fail_open)


def article_to_markdown(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    image_fail_open: bool = IMAGE_DIMENSION_FAIL_OPEN,
) -> Optional[str]:
    """One-liner API: URL -> markdown."""
    extractor = _choose_extractor(timeout=timeout, retries=retries, image_fail_open=image_fail_open)
    article = extractor.extract(url)
    return article.markdown if article else None


def article_to_dict(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    image_fail_open: bool = IMAGE_DIMENSION_FAIL_OPEN,
) -> Optional[dict]:
    """URL -> structured article dict."""
    extractor = _choose_extractor(timeout=timeout, retries=retries, image_fail_open=image_fail_open)
    article = extractor.extract(url)
    if not article:
        return None

    return {
        "title": article.title,
        "subtitle": article.subtitle,
        "author": article.author,
        "source_url": article.source_url,
        "markdown": article.markdown,
        "images": article.images,
    }


__all__ = [
    "Article",
    "ArticleExtractor",
    "article_to_markdown",
    "article_to_dict",
]


if __name__ == "__main__":
    from cli import main as _cli_main

    raise SystemExit(_cli_main())
