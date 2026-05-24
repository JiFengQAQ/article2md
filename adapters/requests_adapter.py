"""Requests-based generic adapter for server-rendered article pages."""

from __future__ import annotations

import logging
import re
from typing import Optional

import requests

from adapters.base import PlatformAdapter
from adapters.content_candidates import (
    choose_best_markdown,
    extract_best_candidate_html,
    is_markdown_body_sufficient,
)
from images import _extract_images_from_html, finalize_markdown_and_images, normalize_html_images
from markdown import _is_captcha, best_title_from_html, html_to_markdown, is_quality_article
from models import Article, DEFAULT_TIMEOUT, IMAGE_DIMENSION_FAIL_OPEN

logger = logging.getLogger(__name__)


def _decoded_response_text(response: requests.Response) -> str:
    """Decode HTML with a Chinese-friendly fallback when charset is wrong/missing."""
    encoding = (response.encoding or "").lower()
    apparent = response.apparent_encoding or ""
    if apparent and (not encoding or encoding in {"iso-8859-1", "ascii"}):
        response.encoding = apparent
    return response.text


def build_article_from_html(
    *,
    html: str,
    final_url: str,
    source_url: str,
    image_fail_open: bool,
    min_chars: int = 220,
) -> Optional[Article]:
    normalized_html = normalize_html_images(html, final_url)
    title = best_title_from_html(normalized_html)

    markdown_candidates: list[str] = []
    candidate_html = extract_best_candidate_html(normalized_html, min_chars=min_chars)
    fallback_candidate_html = extract_best_candidate_html(normalized_html, min_chars=140) if not candidate_html else ""

    if candidate_html:
        markdown_candidates.append(html_to_markdown(candidate_html))
    if fallback_candidate_html:
        markdown_candidates.append(html_to_markdown(fallback_candidate_html))

    markdown = choose_best_markdown(markdown_candidates, min_chars=min_chars, min_paragraphs=3)
    if not markdown and candidate_html:
        markdown = html_to_markdown(candidate_html)
    if not markdown and fallback_candidate_html:
        markdown = html_to_markdown(fallback_candidate_html)
    if not markdown:
        markdown = html_to_markdown(normalized_html)

    if not is_markdown_body_sufficient(markdown, min_chars=min_chars, min_paragraphs=3):
        markdown = choose_best_markdown(markdown_candidates or [markdown], min_chars=140, min_paragraphs=2) or markdown

    images = _extract_images_from_html(normalized_html, final_url)
    markdown = finalize_markdown_and_images(
        markdown=markdown,
        images=images,
        base_url=final_url,
        image_fail_open=image_fail_open,
    )

    return Article(
        title=title or best_title_from_html(normalized_html, fallback=""),
        source_url=source_url,
        markdown=markdown,
        images=images,
    )


class RequestsAdapter(PlatformAdapter):
    """Fast generic fallback for server-rendered pages."""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT, image_fail_open: bool = IMAGE_DIMENSION_FAIL_OPEN):
        self.timeout = timeout
        self.image_fail_open = image_fail_open

    def can_handle(self, url: str) -> bool:
        return True

    def extract(self, url: str) -> Optional[Article]:
        try:
            kwargs = self._request_kwargs()
            kwargs["timeout"] = self.timeout
            response = requests.get(url, allow_redirects=True, **kwargs)
            response.raise_for_status()
            html = _decoded_response_text(response)
        except Exception as exc:
            logger.info("Requests fallback failed: %s", exc)
            return None

        final_url = response.url or url
        visible_hint = re.sub(r"<[^>]+>", " ", html[:20000])
        if _is_captcha(title=best_title_from_html(html), text=visible_hint, url=final_url):
            logger.warning("CAPTCHA / anti-bot page detected by requests: %s", final_url)
            return None

        article = build_article_from_html(
            html=html,
            final_url=final_url,
            source_url=url,
            image_fail_open=self.image_fail_open,
        )
        if article and is_quality_article(article, min_chars=100):
            return article
        return None
