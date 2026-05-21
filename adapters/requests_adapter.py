"""Requests + trafilatura adapter."""

from __future__ import annotations

import logging
import re
from typing import Optional

import requests

from adapters.content_candidates import extract_best_candidate_html, is_markdown_body_sufficient
from images import _extract_images_from_html, finalize_markdown_and_images
from markdown import _is_captcha, best_title_from_html, html_to_markdown, is_quality_article
from models import Article, DEFAULT_TIMEOUT, IMAGE_DIMENSION_FAIL_OPEN
from adapters.base import PlatformAdapter

logger = logging.getLogger(__name__)


def _decoded_response_text(response: requests.Response) -> str:
    """Decode HTML with a Chinese-friendly fallback when charset is wrong/missing."""
    encoding = (response.encoding or "").lower()
    apparent = response.apparent_encoding or ""
    if apparent and (not encoding or encoding in {"iso-8859-1", "ascii"}):
        response.encoding = apparent
    return response.text


class RequestsAdapter(PlatformAdapter):
    """Fast generic fallback for server-rendered pages."""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT, image_fail_open: bool = IMAGE_DIMENSION_FAIL_OPEN):
        self.timeout = timeout
        self.image_fail_open = image_fail_open

    def can_handle(self, url: str) -> bool:
        return True

    def extract(self, url: str) -> Optional[Article]:
        trafilatura_module = None
        try:
            import trafilatura

            trafilatura_module = trafilatura
        except ImportError:
            logger.warning("trafilatura not installed, using static HTML candidate fallback")

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

        markdown = ""
        if trafilatura_module:
            try:
                markdown = (
                    trafilatura_module.extract(
                        html,
                        url=final_url,
                        output_format="markdown",
                        include_images=True,
                        include_links=True,
                        favor_recall=True,
                    )
                    or ""
                )
            except TypeError:
                markdown = trafilatura_module.extract(html, url=final_url) or ""
            except Exception as exc:
                logger.info("trafilatura extraction failed: %s", exc)

        if not is_markdown_body_sufficient(markdown, min_chars=220, min_paragraphs=3):
            candidate_html = extract_best_candidate_html(html, min_chars=220)
            if candidate_html:
                markdown = html_to_markdown(candidate_html)

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
