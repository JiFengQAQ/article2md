"""复用通用 DOM 抽取管线的 Playwright 渲染适配器"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from adapters.base import PlatformAdapter
from markdown import _is_captcha, best_title_from_html, is_quality_article
from adapters.requests_adapter import build_article_from_html
from models import Article, DEFAULT_RETRIES, DEFAULT_TIMEOUT, IMAGE_DIMENSION_FAIL_OPEN, USER_AGENT

logger = logging.getLogger(__name__)


class PlaywrightAdapter(PlatformAdapter):
    """动态页面的通用浏览器渲染兜底适配器"""

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
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("Playwright not installed, cannot fallback")
            return None

        deadline = time.monotonic() + max(1, self.timeout)
        attempts = max(1, self.retries + 1)
        last_error: Optional[Exception] = None

        for attempt in range(attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                article = self._extract_once(url, sync_playwright, remaining)
                if article and is_quality_article(article, min_chars=100):
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

    def _extract_once(self, url: str, sync_playwright: Any, budget: float) -> Optional[Article]:
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
            browser.close()

        if _is_captcha(title=title, text=text, url=final_url):
            logger.warning("CAPTCHA / anti-bot page detected by Playwright: %s", final_url)
            return None

        article = build_article_from_html(
            html=html,
            final_url=final_url,
            source_url=url,
            image_fail_open=self.image_fail_open,
        )
        if not article:
            return None
        article.title = (title or article.title).strip()
        return article

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
