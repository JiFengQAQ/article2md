"""Markdown conversion, cleanup, and article quality checks."""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

from markdownify import markdownify

from models import Article, BOILERPLATE_PATTERNS, CAPTCHA_PATTERNS

_EMPTY_HEADING_PATTERN = re.compile(r"^#{1,6}\s*$")
_POST_ARTICLE_BOUNDARY_PATTERNS = (
    re.compile(r"^(?:评论|评论区|网友评论|全部评论|最新评论)(?:[（(\[【]?\s*\d+\s*[）)\]】]?)?$"),
    re.compile(r"^(?:写评论|发表评论|发布评论|参与评论|登录后评论|评论加载中).*$"),
    re.compile(r"^(?:查看更多|查看全部)\s*\d+\s*条?评论.*$"),
    re.compile(r"^(?:热门推荐|相关推荐|相关阅读|推荐阅读|猜你喜欢|大家都在看|相关内容|热门文章)$"),
    re.compile(r"^(?:返回首页|回到首页|回首页看更多|返回频道|返回列表).*$"),
    re.compile(r"^(?:文明上网理性发言|理性发言.*|请遵守.*评论.*协议.*)$"),
)


def _normalize_markdown(markdown: str) -> str:
    markdown = (markdown or "").replace("\r\n", "\n")
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    return markdown.strip()


def html_to_markdown(html: str) -> str:
    """Convert rich HTML to Markdown."""
    if not html:
        return ""
    markdown = markdownify(
        html,
        heading_style="ATX",
        bullets="*",
        strip=("script", "style"),
    )
    return _normalize_markdown(markdown)


def _line_text_for_matching(line: str) -> str:
    text = line.strip()
    text = re.sub(r"^(?:>\s*)+", "", text)
    text = re.sub(r"^#{1,6}\s*", "", text)
    text = re.sub(r"^(?:[*+-]|\d+\.)\s+", "", text)

    # Normalize markdown image/link labels before boundary matching.
    for _ in range(3):
        new_text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
        new_text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", new_text)
        if new_text == text:
            break
        text = new_text

    text = re.sub(r"[`*_~]+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalized_boundary_variants(line: str) -> tuple[str, str]:
    normalized = _line_text_for_matching(line)
    compact = re.sub(r"[\s\-|｜|:：·•]+", "", normalized)
    return normalized, compact


def _is_post_article_boundary(line: str) -> bool:
    normalized, compact = _normalized_boundary_variants(line)
    if not normalized:
        return False
    for pattern in _POST_ARTICLE_BOUNDARY_PATTERNS:
        if pattern.match(normalized) or pattern.match(compact):
            return True
    return False


def _is_body_content_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if _EMPTY_HEADING_PATTERN.match(stripped):
        return False
    if re.match(r"^!\[[^\]]*\]\([^)]+\)$", stripped):
        return True

    text = _line_text_for_matching(stripped)
    if not text:
        return False

    char_count = len(re.sub(r"\s+", "", text))
    if char_count >= 24:
        return True
    if char_count >= 14 and re.search(r"[，。！？；：,.!?;:]", text):
        return True
    return False


def clean_markdown(markdown: str) -> str:
    """Remove extraction boilerplate while preserving paragraph layout."""
    if not markdown:
        return ""

    lines: list[str] = []
    previous_blank = False
    body_started = False

    for line in markdown.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if _EMPTY_HEADING_PATTERN.match(stripped):
            continue
        if any(re.search(pattern, stripped, re.IGNORECASE) for pattern in BOILERPLATE_PATTERNS):
            continue

        if _is_post_article_boundary(line):
            if body_started:
                break
            continue

        if not stripped:
            if not previous_blank:
                lines.append("")
            previous_blank = True
            continue

        lines.append(line.rstrip())
        if _is_body_content_line(line):
            body_started = True
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
