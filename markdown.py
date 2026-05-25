"""Markdown转换, 清理和文章质量检查"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse
from markdownify import markdownify
from models import Article, CAPTCHA_PATTERNS

_EMPTY_HEADING_RE = re.compile(r"^#{1,6}\s*$")
_IMAGE_LINE_RE = re.compile(r"^!\[[^\]]*\]\([^)]+\)$")
_DATE_PREFIX_RE = re.compile(r"^\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}")
_META_PREFIX_RE = re.compile(r"^(?:来源|作者|责编|编辑|发布于|发布时间|责任编辑)[:：]")
_QUOTE_PREFIX_RE = re.compile(r"^(?:>\s*)+")
_HEADING_PREFIX_RE = re.compile(r"^#{1,6}\s*")
_LIST_PREFIX_RE = re.compile(r"^(?:[*+-]|\d+\.)\s+")
_MARKDOWN_IMAGE_INLINE_RE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_MARKDOWN_LINK_INLINE_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MARKDOWN_FORMAT_RE = re.compile(r"[`*_~]+")
_WHITESPACE_RE = re.compile(r"\s+")
_BOUNDARY_COMPACT_RE = re.compile(r"[\s\-|｜:：·•]+")
_COMMENT_MARKERS = ("评论", "评论区", "网友评论", "全部评论", "最新评论")
_COMMENT_ACTION_MARKERS = ("写评论", "发表评论", "发布评论", "参与评论", "登录后评论", "评论加载中")
_RECOMMENDATION_TAIL_MARKERS = ("热门推荐", "相关推荐", "相关阅读", "推荐阅读", "热门阅读", "相关新闻", "今日热点", "频道热点", "热门排行", "猜你喜欢", "大家都在看", "相关内容", "热门文章", "加载中")
_NAV_RELATIONSHIP_MARKERS = ("返回首页", "回到首页", "回首页看更多", "返回频道", "返回列表", "上一篇", "下一篇", "文章标签", "文中提及", "作者其他作品")
_LEGAL_PREFIX_MARKERS = ("文明上网理性发言", "理性发言", "免责声明", "特别声明", "Notice", "版权保护", "版权所有")
_LEGAL_EDITORIAL_MARKERS = (*_LEGAL_PREFIX_MARKERS, "请遵守", "责任编辑")
_COMMENT_TAIL_MARKERS = ("查看更多", "查看全部")
_FEED_META_TRIGGER_FRAGMENTS = ("浏览", "小时前", "分钟前", "昨天", "前天")
_BOUNDARY_MARKER_GROUPS = (_COMMENT_MARKERS, _COMMENT_ACTION_MARKERS, _COMMENT_TAIL_MARKERS, _RECOMMENDATION_TAIL_MARKERS, _NAV_RELATIONSHIP_MARKERS, _LEGAL_EDITORIAL_MARKERS)
_ALL_NEGATIVE_TEXT_HINTS = tuple(dict.fromkeys((
    *_COMMENT_MARKERS,
    *_COMMENT_ACTION_MARKERS,
    *_COMMENT_TAIL_MARKERS,
    *_RECOMMENDATION_TAIL_MARKERS,
    *_NAV_RELATIONSHIP_MARKERS,
    *_LEGAL_EDITORIAL_MARKERS,
    "浏览 ·",
    "浏览·",
    "广告",
    *_FEED_META_TRIGGER_FRAGMENTS,
)))

def _escaped_alternation(terms: tuple[str, ...]) -> str:
    """生成经过转义的正则分支"""
    return "|".join(re.escape(term) for term in terms)


def _compile_boundary_compact_trigger_pattern(
    marker_groups: tuple[tuple[str, ...], ...],
    feed_fragments: tuple[str, ...],
) -> re.Pattern[str]:
    """编译边界快速触发正则"""
    compact_terms = [_BOUNDARY_COMPACT_RE.sub("", term) for group in marker_groups for term in group]
    escaped = _escaped_alternation(tuple(dict.fromkeys((*compact_terms, *feed_fragments))))
    return re.compile(rf"(?:{escaped})")


_FEED_LIST_METADATA_PATTERNS = (
    re.compile(r"^\d+\s*(?:小时前|分钟前)$"),
    re.compile(r"^(?:昨天|前天)\s*[·•|｜:：]\s*浏览.*$"),
    re.compile(r"^浏览\s*[·•|｜:：]\s*\d.*$"),
    re.compile(r"^浏览\s*\d.*$"),
)
_POST_BOUNDARY_PATTERNS = (
    re.compile(rf"^(?:{_escaped_alternation(_COMMENT_MARKERS)})(?:[（(\[【]?\s*\d+\s*[）)\]】]?)?$"),
    re.compile(rf"^(?:{_escaped_alternation(_COMMENT_ACTION_MARKERS)}).*$"),
    re.compile(rf"^(?:{_escaped_alternation(_COMMENT_TAIL_MARKERS)})\s*\d+\s*条?评论.*$"),
    re.compile(rf"^(?:{_escaped_alternation(_RECOMMENDATION_TAIL_MARKERS)})(?:[.。…]{{0,3}})?$"),
    re.compile(rf"^(?:{_escaped_alternation(_NAV_RELATIONSHIP_MARKERS)}).*$"),
    re.compile(rf"^(?:{_escaped_alternation(_LEGAL_PREFIX_MARKERS)}).*$"),
    re.compile(r"^请遵守.*评论.*协议.*$"),
    re.compile(rf"^(?:[（(\[【]\s*)?{re.escape('责任编辑')}(?:[:：]?.*)?(?:[）)\]】])?$"),
    *_FEED_LIST_METADATA_PATTERNS,
)
_BOUNDARY_COMPACT_TRIGGER_RE = _compile_boundary_compact_trigger_pattern(_BOUNDARY_MARKER_GROUPS, _FEED_META_TRIGGER_FRAGMENTS)
_CSS_BLOCK_START_RE = re.compile(
    r"^\s*(?:@(?:media|supports|keyframes|font-face|layer|container)\b[^{}]*|:root\b|[.#]?[a-z0-9_:-][^{}]*)\s*\{",
    re.IGNORECASE,
)
_CSS_PROP_RE = re.compile(r"^\s*(?:--[a-z0-9\-_]+|[a-z\-]+)\s*:\s*[^:]+;?\s*$", re.IGNORECASE)
_CHINESE_RE = re.compile(r"[\u3400-\u9fff]")
_VISIBLE_TEXT_RE = re.compile(r"[\u3400-\u9fffA-Za-z0-9]")


def _normalize_markdown(markdown: str) -> str:
    text = (markdown or "").replace("\r\n", "\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def html_to_markdown(html: str) -> str:
    if not html:
        return ""
    return _normalize_markdown(markdownify(html, heading_style="ATX", bullets="*", strip=("script", "style")))


def _line_text_for_matching(line: str) -> str:
    text = (line or "").strip()
    text = _QUOTE_PREFIX_RE.sub("", text)
    text = _HEADING_PREFIX_RE.sub("", text)
    text = _LIST_PREFIX_RE.sub("", text)
    if "](" in text:
        for _ in range(3):
            replaced = _MARKDOWN_IMAGE_INLINE_RE.sub(r"\1", text)
            replaced = _MARKDOWN_LINK_INLINE_RE.sub(r"\1", replaced)
            if replaced == text:
                break
            text = replaced
    return _WHITESPACE_RE.sub(" ", _MARKDOWN_FORMAT_RE.sub("", text)).strip()


def _boundary_variants(line: str) -> tuple[str, str]:
    normalized = _line_text_for_matching(line)
    return normalized, _BOUNDARY_COMPACT_RE.sub("", normalized)


def _has_boundary_compact_trigger(stripped: str) -> bool:
    compact_raw = _BOUNDARY_COMPACT_RE.sub("", stripped)
    return bool(_BOUNDARY_COMPACT_TRIGGER_RE.search(compact_raw))


def _is_post_article_boundary(line: str) -> bool:
    stripped = (line or "").strip()
    if not stripped:
        return False
    if not _has_boundary_compact_trigger(stripped):
        return False
    normalized, compact = _boundary_variants(line)
    return bool(normalized) and any(pattern.match(normalized) or pattern.match(compact) for pattern in _POST_BOUNDARY_PATTERNS)


def _is_body_content_line(line: str) -> bool:
    stripped = (line or "").strip()
    if not stripped or _EMPTY_HEADING_RE.match(stripped):
        return False
    if _IMAGE_LINE_RE.match(stripped):
        return True
    text = _line_text_for_matching(stripped)
    if not text:
        return False
    chars = len(_WHITESPACE_RE.sub("", text))
    return chars >= 24 or (chars >= 14 and bool(re.search(r"[，。！？；：,.!?;:]", text)))


def _is_substantive_article_line(line: str) -> bool:
    stripped = (line or "").strip()
    if stripped.startswith("#") or _IMAGE_LINE_RE.match(stripped) or not _is_body_content_line(stripped):
        return False
    text = _line_text_for_matching(stripped)
    compact = _WHITESPACE_RE.sub("", text)
    return not (_DATE_PREFIX_RE.match(compact) or _META_PREFIX_RE.match(text) or _is_post_article_boundary(stripped))


def _looks_like_css_block_start(line: str) -> bool:
    stripped = (line or "").strip()
    return bool(stripped and not stripped.startswith("# ") and _CSS_BLOCK_START_RE.match(stripped) and "{" in stripped)


def _looks_like_css_inline(line: str) -> bool:
    stripped = (line or "").strip()
    if not stripped or stripped.startswith("# "):
        return False
    if stripped.startswith(":root"):
        return True
    if _CSS_BLOCK_START_RE.match(stripped):
        return "}" in stripped and ":" in stripped
    return bool(_CSS_PROP_RE.match(stripped) and stripped.endswith(";"))


def clean_markdown(markdown: str) -> str:
    if not markdown:
        return ""
    lines: list[str] = []
    body_started = False
    previous_blank = False
    css_depth = 0
    for raw_line in markdown.replace("\r\n", "\n").split("\n"):
        stripped = raw_line.strip()
        if css_depth > 0:
            css_depth += raw_line.count("{") - raw_line.count("}")
            css_depth = max(css_depth, 0)
            continue
        if _looks_like_css_inline(stripped):
            continue
        if _looks_like_css_block_start(stripped):
            css_depth = max(1, raw_line.count("{") - raw_line.count("}"))
            continue
        if _EMPTY_HEADING_RE.match(stripped):
            continue
        if _is_post_article_boundary(raw_line):
            if body_started:
                break
            continue
        if not stripped:
            if not previous_blank:
                lines.append("")
            previous_blank = True
            continue
        lines.append(raw_line.rstrip())
        body_started = body_started or _is_substantive_article_line(raw_line)
        previous_blank = False
    return "\n".join(lines).strip()


def _pattern_haystack(*parts: str) -> str:
    return "\n".join(part or "" for part in parts).lower().replace("\\_", "_")


def _is_captcha(title: str = "", text: str = "", url: str = "") -> bool:
    haystack = _pattern_haystack(title, text, url)
    parsed = urlparse(url or "")
    return (
        parsed.netloc.endswith("passport.baidu.com")
        or "captcha" in parsed.path.lower()
        or "captcha" in parsed.query.lower()
        or any(pattern.lower() in haystack for pattern in CAPTCHA_PATTERNS)
    )


def _content_text(markdown: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", markdown or "")
    text = re.sub(r"\[[^\]]+\]\([^)]+\)", "", text)
    return re.sub(r"[#>*_`\-\s]+", "", text)


def _chinese_char_ratio(*parts: str) -> float:
    visible = "".join(_VISIBLE_TEXT_RE.findall("\n".join(parts)))
    return len(_CHINESE_RE.findall(visible)) / max(len(visible), 1)


def _is_access_wall_payload(title: str = "", text: str = "", url: str = "") -> bool:
    title_lower = (title or "").strip().lower()
    text_lower = (text or "").lower()
    compact = _WHITESPACE_RE.sub("", text_lower)
    parsed = urlparse(url or "")
    if "sina visitor system" in title_lower or "sina visitor system" in text_lower:
        return True
    if "/visitor/visitor" in parsed.path.lower() or "/visitor/visitor" in (url or "").lower():
        return True
    if "visitor/visitor" in text_lower and ("window.use_fp" in text_lower or "incarnate" in text_lower):
        return True
    unauthorized_message = "unauthorizedaccess" in compact or "unauthorized access" in text_lower
    unauthorized_status = bool(re.search(r'"(?:status(?:_?code)?|code)"\s*:\s*401\b', text_lower))
    if unauthorized_message and unauthorized_status and len(_content_text(text)) <= 180:
        return True
    edge_markers = (
        "请求已被拦截",
        "安全策略拦截",
        "在线攻击",
        "请求id",
        "tencent cloud edgeone",
        "edgeone web安全分析",
        "access denied",
        "request blocked",
    )
    return sum(1 for marker in edge_markers if marker in text_lower or marker in title_lower) >= 2


def is_quality_article(article: Optional[Article], min_chars: int = 100) -> bool:
    if not article:
        return False
    markdown = clean_markdown(article.markdown)
    title = (article.title or "").strip()
    if _is_captcha(title=title, text=markdown, url=article.source_url):
        return False
    if _is_access_wall_payload(title=title, text=markdown, url=article.source_url):
        return False
    if len(_content_text(markdown)) < min_chars:
        return False
    if _chinese_char_ratio(title, _content_text(markdown)) < 0.5:
        return False
    if sum(1 for pattern in CAPTCHA_PATTERNS if pattern.lower() in _pattern_haystack(markdown)) >= 2:
        return False
    article.markdown = markdown
    return True


def best_title_from_html(html: str, fallback: str = "") -> str:
    patterns = (
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
        r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:title["\']',
        r"<h1\b[^>]*>(.*?)</h1>",
        r"<title\b[^>]*>(.*?)</title>",
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


_clean_markdown = clean_markdown
_is_quality_article = is_quality_article
_best_title_from_html = best_title_from_html
