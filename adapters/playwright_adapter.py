"""Playwright + readability fallback adapter."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from adapters.content_candidates import extract_best_candidate_html, is_markdown_body_sufficient
from images import _dedupe, _extract_images_from_html, _normalize_image_url, finalize_markdown_and_images
from markdown import _is_captcha, best_title_from_html, html_to_markdown, is_quality_article
from models import Article, DEFAULT_RETRIES, DEFAULT_TIMEOUT, IMAGE_DIMENSION_FAIL_OPEN, USER_AGENT
from adapters.base import PlatformAdapter

logger = logging.getLogger(__name__)


class PlaywrightAdapter(PlatformAdapter):
    """Generic browser-rendered fallback for dynamic pages."""

    def __init__(
        self,
        timeout: int = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        image_fail_open: bool = IMAGE_DIMENSION_FAIL_OPEN,
    ):
        self.timeout = timeout
        self.retries = retries
        self.image_fail_open = image_fail_open

    def can_handle(self, url: str) -> bool:
        return True

    def extract(self, url: str) -> Optional[Article]:
        try:
            from readability import Document
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("Playwright / readability-lxml not installed, cannot fallback")
            return None

        deadline = time.monotonic() + max(1, self.timeout)
        attempts = max(1, self.retries + 1)
        last_error: Optional[Exception] = None

        for attempt in range(attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                article = self._extract_once(url, Document, sync_playwright, remaining)
                if is_quality_article(article):
                    return article
                logger.info("Playwright extraction failed quality validation: %s", url)
            except Exception as exc:
                last_error = exc
                logger.info("Playwright attempt %s failed: %s", attempt + 1, exc)

            if attempt < attempts - 1:
                sleep_for = min(0.5 * (2**attempt), max(0, deadline - time.monotonic()))
                if sleep_for > 0:
                    time.sleep(sleep_for)

        if last_error:
            logger.error("Playwright browser failed: %s", last_error)
        return None

    def _extract_once(self, url: str, Document: Any, sync_playwright: Any, budget: float) -> Optional[Article]:
        timeout_ms = max(1000, int(min(budget, self.timeout) * 1000))

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=USER_AGENT,
                viewport={"width": 1365, "height": 900},
                locale="zh-CN",
            )
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(min(2500, max(500, timeout_ms // 4)))
            self._wait_for_article_or_scroll(page)

            html = page.content()
            title = self._page_title(page, html)
            final_url = page.url
            text = page.locator("body").inner_text(timeout=1000) if page.locator("body").count() else ""
            rendered_images = self._rendered_images(page, final_url)
            browser.close()

        if _is_captcha(title=title, text=text, url=final_url):
            logger.warning("CAPTCHA / anti-bot page detected by Playwright: %s", final_url)
            return None

        doc = Document(html)
        article_html = doc.summary() or ""
        markdown = html_to_markdown(article_html) if article_html else ""
        if not is_markdown_body_sufficient(markdown, min_chars=220, min_paragraphs=3):
            candidate_html = extract_best_candidate_html(html, min_chars=220)
            if candidate_html:
                article_html = candidate_html
                markdown = html_to_markdown(article_html)
        title = title or doc.title()

        images = _dedupe(
            rendered_images
            + _extract_images_from_html(article_html, final_url)
            + _extract_images_from_html(html, final_url)
        )
        markdown = finalize_markdown_and_images(
            markdown=markdown,
            images=images,
            base_url=final_url,
            image_fail_open=self.image_fail_open,
        )

        return Article(
            title=title,
            source_url=url,
            markdown=markdown,
            images=images,
        )

    @staticmethod
    def _wait_for_article_or_scroll(page: Any) -> None:
        try:
            page.wait_for_selector("article, main, .content, .article, h1", timeout=2000)
        except Exception:
            pass

        try:
            for fraction in (0.35, 0.7, 1):
                page.evaluate(
                    "(fraction) => window.scrollTo(0, document.body.scrollHeight * fraction)",
                    fraction,
                )
                page.wait_for_timeout(500)
            page.evaluate("() => window.scrollTo(0, 0)")
            page.wait_for_timeout(300)
        except Exception:
            pass

    @staticmethod
    def _page_title(page: Any, html: str) -> str:
        try:
            title = page.evaluate(
                """() => {
                    const og = document.querySelector('meta[property="og:title"], meta[name="twitter:title"]');
                    const h1 = document.querySelector('h1');
                    return (og && og.content) || (h1 && h1.innerText) || document.title || '';
                }"""
            )
        except Exception:
            title = ""
        return (title or best_title_from_html(html)).strip()

    @staticmethod
    def _rendered_images(page: Any, base_url: str) -> list[str]:
        try:
            images = page.evaluate(
                """() => Array.from(
                    document.querySelectorAll('article img, .content img, .article img, main img, p img')
                ).map(img => img.currentSrc || img.src || img.dataset.src || img.dataset.original || '')"""
            )
        except Exception:
            images = []
        return _dedupe([_normalize_image_url(src, base_url) for src in images])
