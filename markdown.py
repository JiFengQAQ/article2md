"""Markdown conversion, cleanup, and article quality checks."""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

from html2text import HTML2Text

from models import Article, BOILERPLATE_PATTERNS, CAPTCHA_PATTERNS


def _make_converter() -> HTML2Text:
    converter = HTML2Text()
    converter.body_width = 0
    converter.ignore_links = False
    converter.ignore_images = False
    converter.images_to_alt = False
    converter.skip_internal_links = False
    converter.protect_links = True
    return converter


def html_to_markdown(html: str) -> str:
    """Convert rich HTML to Markdown."""
    return _make_converter().handle(html).strip()


def clean_markdown(markdown: str) -> str:
    """Remove extraction boilerplate while preserving paragraph layout."""
    if not markdown:
        return ""

    lines: list[str] = []
    previous_blank = False
    for line in markdown.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if any(re.search(pattern, stripped, re.IGNORECASE) for pattern in BOILERPLATE_PATTERNS):
            continue
        if not stripped:
            if not previous_blank:
                lines.append("")
            previous_blank = True
            continue
        lines.append(line.rstrip())
        previous_blank = False
    return "\n".join(lines).strip()


def _is_captcha(title: str = "", text: str = "", url: str = "") -> bool:
    haystack = "\n".join([title or "", text or "", url or ""]).lower()
    parsed = urlparse(url or "")
    if parsed.netloc.endswith("passport.baidu.com"):
        return True
    if "captcha" in parsed.path.lower() or "captcha" in parsed.query.lower():
        return True
    return any(pattern.lower() in haystack for pattern in CAPTCHA_PATTERNS)


def _content_text(markdown: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", markdown or "")
    text = re.sub(r"\[[^\]]+\]\([^)]+\)", "", text)
    text = re.sub(r"[#>*_`\-\s]+", "", text)
    return text


def is_quality_article(article: Optional[Article], min_chars: int = 100) -> bool:
    if not article:
        return False
    markdown = clean_markdown(article.markdown)
    title = (article.title or "").strip()
    if _is_captcha(title=title, text=markdown, url=article.source_url):
        return False
    if len(_content_text(markdown)) < min_chars:
        return False
    captcha_hits = sum(1 for pattern in CAPTCHA_PATTERNS if pattern.lower() in markdown.lower())
    if captcha_hits >= 2:
        return False
    article.markdown = markdown
    return True


def best_title_from_html(html: str, fallback: str = "") -> str:
    patterns = (
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
        r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\']([^"\']+)["\']',
        r"<h1\\b[^>]*>(.*?)</h1>",
        r"<title\\b[^>]*>(.*?)</title>",
    )
    for pattern in patterns:
        match = re.search(pattern, html or "", flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        title = re.sub(r"<[^>]+>", "", match.group(1))
        title = re.sub(r"\s+", " ", title).strip()
        if title:
            return title
    return (fallback or "").strip()


# Backward-friendly aliases.
_clean_markdown = clean_markdown
_is_quality_article = is_quality_article
_best_title_from_html = best_title_from_html
