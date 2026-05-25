"""基于Requests的服务端渲染文章页通用适配器"""

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
from models import Article, DEFAULT_TIMEOUT

logger = logging.getLogger(__name__)


def _decoded_response_text(response: requests.Response) -> str:
    """在charset错误或缺失时用中文友好的兜底编码解码HTML"""
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
    )

    return Article(
        title=title or best_title_from_html(normalized_html, fallback=""),
        source_url=source_url,
        markdown=markdown,
        images=images,
    )


class RequestsAdapter(PlatformAdapter):
    """服务端渲染页面的快速通用兜底适配器"""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT):
        self.timeout = timeout

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
        )
        if article and is_quality_article(article, min_chars=100):
            return article
        return None
