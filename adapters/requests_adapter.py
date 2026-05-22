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
from images import _extract_images_from_html, finalize_markdown_and_images
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


def _readability_html(html: str) -> str:
    """Optional readability extraction for static pages."""
    try:
        from readability import Document
    except ImportError:
        return ""

    try:
        summary = Document(html or "").summary() or ""
        return summary
    except Exception as exc:
        logger.info("readability extraction failed: %s", exc)
        return ""


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
        title = best_title_from_html(html)
        visible_hint = re.sub(r"<[^>]+>", " ", html[:20000])
        if _is_captcha(title=title, text=visible_hint, url=final_url):
            logger.warning("CAPTCHA / anti-bot page detected by requests: %s", final_url)
            return None

        markdown_candidates: list[str] = []

        candidate_html = extract_best_candidate_html(html, min_chars=220)
        if candidate_html:
            markdown_candidates.append(html_to_markdown(candidate_html))

        readability_html = _readability_html(html)
        if readability_html:
            markdown_candidates.append(html_to_markdown(readability_html))

        markdown = choose_best_markdown(markdown_candidates, min_chars=220, min_paragraphs=3)

        if not markdown and candidate_html:
            markdown = html_to_markdown(candidate_html)
        if not markdown and readability_html:
            markdown = html_to_markdown(readability_html)

        if not is_markdown_body_sufficient(markdown, min_chars=220, min_paragraphs=3):
            # Keep best effort output for short notices, then let final quality gate decide.
            markdown = choose_best_markdown(markdown_candidates, min_chars=140, min_paragraphs=2)

        images = _extract_images_from_html(html, final_url)
        markdown = finalize_markdown_and_images(
            markdown=markdown,
            images=images,
            base_url=final_url,
            image_fail_open=self.image_fail_open,
        )

        article = Article(
            title=title or best_title_from_html(html, fallback=""),
            source_url=url,
            markdown=markdown,
            images=images,
        )

        if is_quality_article(article, min_chars=200):
            return article
        return None
