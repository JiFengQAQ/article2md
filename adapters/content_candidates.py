"""Generic content-candidate scoring for fallback extraction."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Optional

from markdown import clean_markdown

_TEXT_XPATH = (
    ".//text()[not(ancestor::script) and not(ancestor::style) and "
    "not(ancestor::noscript) and not(ancestor::template)]"
)
_PUNCT_RE = re.compile(r"[，。！？；：、,.!?;:]")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")

_POSITIVE_ATTR_HINTS = (
    "article",
    "content",
    "post",
    "detail",
    "entry",
    "story",
    "正文",
    "rich_media",
    "content-body",
    "contentbody",
    "news_txt",
    "article-body",
    "article_content",
)
_NEGATIVE_ATTR_HINTS = (
    "nav",
    "menu",
    "header",
    "footer",
    "comment",
    "reply",
    "recommend",
    "related",
    "share",
    "toolbar",
    "subscribe",
    "advert",
    "ads",
    "aside",
    "sidebar",
    "breadcrumb",
    "pagination",
)
_NEGATIVE_TEXT_HINTS = (
    "相关阅读",
    "相关推荐",
    "推荐阅读",
    "热门推荐",
    "猜你喜欢",
    "大家都在看",
    "上一篇",
    "下一篇",
    "网友评论",
    "评论区",
    "登录后评论",
)
_CONTENT_KEYWORDS = (
    "表示",
    "认为",
    "指出",
    "介绍",
    "记者",
    "报道",
    "发布",
    "消息",
    "此外",
    "同时",
    "according",
    "report",
    "analysis",
    "update",
)


def _compact_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _char_count(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def _attr_blob(node: Any) -> str:
    attrs = (
        node.get("id", ""),
        node.get("class", ""),
        node.get("role", ""),
        node.get("itemprop", ""),
        node.get("data-role", ""),
    )
    return " ".join(attrs).lower()


def _tag_name(node: Any) -> str:
    tag = getattr(node, "tag", "")
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _hint_hits(haystack: str, hints: tuple[str, ...]) -> int:
    return sum(1 for hint in hints if hint in haystack)


def _node_text(node: Any) -> str:
    text_nodes = node.xpath(_TEXT_XPATH)
    return _compact_whitespace(" ".join(text_nodes))


def _paragraph_count(node: Any, text: str) -> int:
    count = 0
    for paragraph in node.xpath(".//p"):
        plain = _node_text(paragraph)
        if _char_count(plain) >= 24:
            count += 1
    if count > 0:
        return count
    sentence_like = [part for part in re.split(r"[。！？.!?]", text) if _char_count(part) >= 20]
    return len(sentence_like)


def _descendant_negative_count(node: Any) -> int:
    count = 0
    for descendant in node.iterdescendants():
        if _hint_hits(_attr_blob(descendant), _NEGATIVE_ATTR_HINTS):
            count += 1
            if count >= 30:
                break
    return count


def _markdown_plain_text(markdown: str) -> str:
    cleaned = clean_markdown(markdown or "")
    without_images = _MARKDOWN_IMAGE_RE.sub(" ", cleaned)
    without_links = _MARKDOWN_LINK_RE.sub(r"\1", without_images)
    plain = re.sub(r"[#>*_`~\-]+", " ", without_links)
    return _compact_whitespace(plain)


def markdown_body_metrics(markdown: str) -> dict[str, float]:
    cleaned = clean_markdown(markdown or "")
    plain = _markdown_plain_text(cleaned)
    char_count = _char_count(plain)
    punct_count = len(_PUNCT_RE.findall(plain))
    link_chars = sum(len(match.group(0)) for match in _MARKDOWN_LINK_RE.finditer(cleaned))

    paragraph_count = 0
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        stripped = stripped.lstrip("#>*-0123456789. ").strip()
        if _char_count(stripped) >= 24:
            paragraph_count += 1
    if paragraph_count == 0:
        paragraph_count = len([part for part in re.split(r"[。！？.!?]", plain) if _char_count(part) >= 20])

    return {
        "char_count": float(char_count),
        "paragraph_count": float(paragraph_count),
        "punct_density": float(punct_count / max(char_count, 1)),
        "link_density": float(link_chars / max(len(cleaned), 1)),
    }


def is_markdown_body_sufficient(markdown: str, min_chars: int = 220, min_paragraphs: int = 3) -> bool:
    metrics = markdown_body_metrics(markdown)
    if metrics["char_count"] < min_chars:
        return False
    if metrics["paragraph_count"] < min_paragraphs:
        return False
    if metrics["punct_density"] < 0.006:
        return False
    if metrics["link_density"] > 0.55:
        return False
    return True


@dataclass
class _Candidate:
    node: Any
    score: float
    text_chars: int
    paragraph_count: int
    link_density: float


def _candidate_score(node: Any, text: str, text_chars: int, min_chars: int) -> _Candidate:
    attrs = _attr_blob(node)
    tag = _tag_name(node)
    semantic_bonus = 26.0 if (tag in {"article", "main"} or node.get("role", "").lower() == "main") else 0.0
    positive_hits = _hint_hits(attrs, _POSITIVE_ATTR_HINTS)
    negative_hits = _hint_hits(attrs, _NEGATIVE_ATTR_HINTS)

    paragraph_count = _paragraph_count(node, text)
    punct_density = len(_PUNCT_RE.findall(text)) / max(text_chars, 1)
    text_lower = text.lower()
    keyword_hits = sum(text_lower.count(keyword) for keyword in _CONTENT_KEYWORDS)

    link_text = _compact_whitespace(" ".join(node.xpath(".//a//text()")))
    link_density = _char_count(link_text) / max(text_chars, 1)

    descendant_negative = _descendant_negative_count(node)
    negative_text_hits = sum(text.count(keyword) for keyword in _NEGATIVE_TEXT_HINTS)
    li_count = len(node.xpath(".//li"))
    list_penalty = max(0, li_count - paragraph_count * 3)

    score = 0.0
    score += min(text_chars, 14000) / 42.0
    score += min(paragraph_count, 80) * 9.0
    score += min(keyword_hits, 12) * 4.0
    score += min(punct_density, 0.2) * 280.0
    score += min(positive_hits, 6) * 11.0
    score += semantic_bonus

    score -= min(link_density, 1.0) * 220.0
    score -= min(negative_hits, 4) * 16.0
    score -= min(descendant_negative, 30) * 9.0
    score -= min(negative_text_hits, 12) * 10.0
    score -= min(list_penalty, 100) * 1.3

    if text_chars < min_chars:
        score -= (min_chars - text_chars) * 0.6
    if paragraph_count < 2:
        score -= 35.0

    return _Candidate(
        node=node,
        score=score,
        text_chars=text_chars,
        paragraph_count=paragraph_count,
        link_density=link_density,
    )


def _should_prune_noise(node: Any) -> bool:
    tag = _tag_name(node)
    attrs = _attr_blob(node)
    negative_hits = _hint_hits(attrs, _NEGATIVE_ATTR_HINTS)
    if tag in {"nav", "aside", "footer", "header"}:
        return True
    if negative_hits == 0:
        return False

    text = _node_text(node)
    text_chars = _char_count(text)
    link_text = _compact_whitespace(" ".join(node.xpath(".//a//text()")))
    link_density = _char_count(link_text) / max(text_chars, 1)
    if link_density >= 0.2:
        return True
    return text_chars <= 500


def _serialize_candidate(node: Any, lxml_html: Any) -> str:
    cleaned = deepcopy(node)
    for descendant in list(cleaned.iterdescendants()):
        if not _should_prune_noise(descendant):
            continue
        parent = descendant.getparent()
        if parent is not None:
            parent.remove(descendant)
    return lxml_html.tostring(cleaned, encoding="unicode", method="html")


def extract_best_candidate_html(html: str, min_chars: int = 200) -> Optional[str]:
    try:
        from lxml import html as lxml_html
    except ImportError:
        return None

    try:
        root = lxml_html.fromstring(html or "")
    except Exception:
        return None

    candidates: list[_Candidate] = []
    for node in root.iter():
        tag = _tag_name(node)
        if tag not in {"article", "main", "section", "div"}:
            continue

        attrs = _attr_blob(node)
        is_semantic = tag in {"article", "main"} or node.get("role", "").lower() == "main"
        has_positive_hint = _hint_hits(attrs, _POSITIVE_ATTR_HINTS) > 0
        if not is_semantic and not has_positive_hint:
            continue

        text = _node_text(node)
        text_chars = _char_count(text)
        if text_chars < max(80, min_chars // 2):
            continue
        candidates.append(_candidate_score(node, text, text_chars, min_chars=min_chars))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item.score, reverse=True)
    char_floor = max(120, int(min_chars * 0.85))
    for candidate in candidates:
        if (
            candidate.text_chars >= char_floor
            and candidate.paragraph_count >= 2
            and candidate.link_density <= 0.65
        ):
            return _serialize_candidate(candidate.node, lxml_html)

    best = candidates[0]
    if best.score < 25 or best.text_chars < max(120, int(min_chars * 0.8)):
        return None
    return _serialize_candidate(best.node, lxml_html)
