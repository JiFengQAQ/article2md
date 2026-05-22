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

_CONTAINER_TAGS = {"article", "main", "section", "div", "td"}
_HARD_PRUNE_TAGS = {
    "nav",
    "aside",
    "footer",
    "header",
    "form",
    "button",
    "input",
    "select",
    "textarea",
    "iframe",
}

_POSITIVE_ATTR_HINTS = (
    "article",
    "content",
    "post",
    "detail",
    "entry",
    "story",
    "正文",
    "rich_media",
    "main",
    "text",
    "body",
    "news",
    "thread",
    "topic",
    "doc",
    "article-body",
    "article_content",
    "content-body",
    "contentbody",
    "news_txt",
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
    "ad-",
    "ad_",
    "aside",
    "sidebar",
    "breadcrumb",
    "pagination",
    "pager",
    "taglist",
    "hot",
    "rank",
    "topic-list",
    "copyright",
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
    "文明上网理性发言",
    "返回首页",
    "回到首页",
    "广告",
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
        node.get("id"),
        node.get("class"),
        node.get("role"),
        node.get("itemprop"),
        node.get("data-role"),
        node.get("aria-label"),
        node.get("aria-labelledby"),
    )
    return " ".join(str(attr) for attr in attrs if attr).lower()


def _tag_name(node: Any) -> str:
    tag = getattr(node, "tag", "")
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _hint_hits(haystack: str, hints: tuple[str, ...]) -> int:
    return sum(1 for hint in hints if hint in haystack)


def _node_text(node: Any) -> str:
    text_nodes = node.xpath(_TEXT_XPATH)
    return _compact_whitespace(" ".join(str(text) for text in text_nodes if text))


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


def _sentence_count(text: str) -> int:
    return len([part for part in re.split(r"[。！？.!?]", text) if _char_count(part) >= 10])


def _descendant_negative_count(node: Any) -> int:
    count = 0
    for descendant in node.iterdescendants():
        tag = _tag_name(descendant)
        attrs = _attr_blob(descendant)
        if tag in _HARD_PRUNE_TAGS or _hint_hits(attrs, _NEGATIVE_ATTR_HINTS):
            count += 1
            if count >= 40:
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


def _markdown_quality_score(markdown: str) -> float:
    metrics = markdown_body_metrics(markdown)
    score = 0.0
    score += metrics["char_count"]
    score += metrics["paragraph_count"] * 120.0
    score += metrics["punct_density"] * 3600.0
    score -= metrics["link_density"] * 1800.0
    return score


def choose_best_markdown(markdowns: list[str], min_chars: int = 220, min_paragraphs: int = 3) -> str:
    """Select the best markdown body from multiple generic candidates."""
    best_markdown = ""
    best_score = float("-inf")

    for candidate in markdowns:
        cleaned = clean_markdown(candidate or "")
        if not cleaned:
            continue
        score = _markdown_quality_score(cleaned)
        metrics = markdown_body_metrics(cleaned)

        # Soft penalty instead of hard drop, to avoid losing short-but-valid article pages.
        if metrics["char_count"] < min_chars:
            score -= (min_chars - metrics["char_count"]) * 0.8
        if metrics["paragraph_count"] < min_paragraphs:
            score -= (min_paragraphs - metrics["paragraph_count"]) * 90.0

        if score > best_score:
            best_score = score
            best_markdown = cleaned

    return best_markdown


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


def _candidate_score(node: Any, min_chars: int) -> Optional[_Candidate]:
    text = _node_text(node)
    text_chars = _char_count(text)
    if text_chars < max(60, min_chars // 3):
        return None

    attrs = _attr_blob(node)
    tag = _tag_name(node)
    role = (node.get("role") or "").lower()

    semantic_bonus = 30.0 if (tag in {"article", "main"} or role == "main") else 0.0
    positive_hits = _hint_hits(attrs, _POSITIVE_ATTR_HINTS)
    negative_hits = _hint_hits(attrs, _NEGATIVE_ATTR_HINTS)

    paragraph_count = _paragraph_count(node, text)
    sentence_count = _sentence_count(text)
    punct_density = len(_PUNCT_RE.findall(text)) / max(text_chars, 1)
    text_lower = text.lower()
    keyword_hits = sum(text_lower.count(keyword) for keyword in _CONTENT_KEYWORDS)

    link_text = _compact_whitespace(" ".join(node.xpath(".//a//text()")))
    link_density = _char_count(link_text) / max(text_chars, 1)

    descendant_negative = _descendant_negative_count(node)
    negative_text_hits = sum(text.count(keyword) for keyword in _NEGATIVE_TEXT_HINTS)
    li_count = len(node.xpath(".//li"))
    list_penalty = max(0, li_count - paragraph_count * 4)

    heading_bonus = 0.0
    for heading in node.xpath(".//h1|.//h2"):
        if _char_count(_node_text(heading)) >= 8:
            heading_bonus = 14.0
            break

    score = 0.0
    score += min(text_chars, 22000) / 35.0
    score += min(paragraph_count, 100) * 10.0
    score += min(sentence_count, 140) * 3.0
    score += min(keyword_hits, 16) * 4.0
    score += min(punct_density, 0.22) * 300.0
    score += min(positive_hits, 8) * 14.0
    score += semantic_bonus + heading_bonus

    score -= min(link_density, 1.0) * 260.0
    score -= min(negative_hits, 8) * 18.0
    score -= min(descendant_negative, 40) * 8.0
    score -= min(negative_text_hits, 16) * 11.0
    score -= min(list_penalty, 120) * 1.2

    if text_chars < min_chars:
        score -= (min_chars - text_chars) * 0.7
    if paragraph_count < 2:
        score -= 40.0
    if sentence_count < 2:
        score -= 20.0

    return _Candidate(
        node=node,
        score=score,
        text_chars=text_chars,
        paragraph_count=paragraph_count,
        link_density=link_density,
    )


def _is_hard_negative_node(node: Any) -> bool:
    tag = _tag_name(node)
    if tag in _HARD_PRUNE_TAGS:
        return True

    attrs = _attr_blob(node)
    negative_hits = _hint_hits(attrs, _NEGATIVE_ATTR_HINTS)
    if negative_hits == 0:
        return False

    text = _node_text(node)
    text_chars = _char_count(text)
    link_text = _compact_whitespace(" ".join(node.xpath(".//a//text()")))
    link_density = _char_count(link_text) / max(text_chars, 1)
    negative_text_hits = sum(text.count(keyword) for keyword in _NEGATIVE_TEXT_HINTS)

    if negative_hits >= 2 and text_chars <= 2400:
        return True
    if negative_text_hits > 0 and text_chars <= 2500:
        return True
    if link_density >= 0.45 and text_chars <= 2200:
        return True
    if negative_hits >= 1 and link_density >= 0.18 and text_chars <= 1200:
        return True
    return False


def _should_prune_noise(node: Any) -> bool:
    if _is_hard_negative_node(node):
        return True

    tag = _tag_name(node)
    if tag not in {"div", "section", "ul", "ol", "li"}:
        return False

    text = _node_text(node)
    text_chars = _char_count(text)
    if text_chars == 0:
        return True

    link_text = _compact_whitespace(" ".join(node.xpath(".//a//text()")))
    link_density = _char_count(link_text) / max(text_chars, 1)
    punct_density = len(_PUNCT_RE.findall(text)) / max(text_chars, 1)

    # List-heavy link blocks are usually recommendations, tag clouds, or sidebars.
    li_count = len(node.xpath(".//li"))
    paragraph_count = _paragraph_count(node, text)
    if link_density >= 0.55 and punct_density < 0.01:
        return True
    if li_count >= 8 and paragraph_count <= 1 and link_density >= 0.15:
        return True

    return False


def _prune_candidate_tree(node: Any) -> Any:
    cleaned = deepcopy(node)
    for descendant in list(cleaned.iterdescendants()):
        if not _should_prune_noise(descendant):
            continue
        parent = descendant.getparent()
        if parent is not None:
            parent.remove(descendant)
    return cleaned


def _serialize_candidate(node: Any, lxml_html: Any) -> str:
    cleaned = _prune_candidate_tree(node)
    return lxml_html.tostring(cleaned, encoding="unicode", method="html")


def _sibling_is_content_like(node: Any, min_chars: int) -> bool:
    if _is_hard_negative_node(node):
        return False

    text = _node_text(node)
    text_chars = _char_count(text)
    if text_chars < max(40, min_chars // 4):
        return False

    link_text = _compact_whitespace(" ".join(node.xpath(".//a//text()")))
    link_density = _char_count(link_text) / max(text_chars, 1)
    paragraph_count = _paragraph_count(node, text)
    punct_density = len(_PUNCT_RE.findall(text)) / max(text_chars, 1)

    if link_density > 0.6 and paragraph_count < 2:
        return False
    if punct_density < 0.004 and paragraph_count <= 1 and text_chars < 300:
        return False
    return True


def _collect_sibling_group(node: Any, min_chars: int, max_hops: int = 6) -> list[Any]:
    parent = node.getparent()
    if parent is None:
        return [node]

    siblings = list(parent)
    try:
        index = siblings.index(node)
    except ValueError:
        return [node]

    picked_left: list[Any] = []
    misses = 0
    for offset in range(1, max_hops + 1):
        pos = index - offset
        if pos < 0:
            break
        sibling = siblings[pos]
        if _is_hard_negative_node(sibling):
            break
        if _sibling_is_content_like(sibling, min_chars=min_chars):
            picked_left.append(sibling)
            misses = 0
        else:
            misses += 1
            if misses >= 2:
                break

    picked_right: list[Any] = []
    misses = 0
    for offset in range(1, max_hops + 1):
        pos = index + offset
        if pos >= len(siblings):
            break
        sibling = siblings[pos]
        if _is_hard_negative_node(sibling):
            break
        if _sibling_is_content_like(sibling, min_chars=min_chars):
            picked_right.append(sibling)
            misses = 0
        else:
            misses += 1
            if misses >= 2:
                break

    return list(reversed(picked_left)) + [node] + picked_right


def _serialize_node_group(nodes: list[Any], lxml_html: Any) -> str:
    wrapper = lxml_html.Element("div")
    for node in nodes:
        wrapper.append(_prune_candidate_tree(node))
    return lxml_html.tostring(wrapper, encoding="unicode", method="html")


def _html_metrics(html: str) -> tuple[int, int, float, float, int]:
    try:
        from lxml import html as lxml_html

        root = lxml_html.fromstring(html or "")
    except Exception:
        return 0, 0, 0.0, 1.0, 0

    text = _node_text(root)
    text_chars = _char_count(text)
    paragraph_count = _paragraph_count(root, text)
    punct_density = len(_PUNCT_RE.findall(text)) / max(text_chars, 1)
    link_text = _compact_whitespace(" ".join(root.xpath(".//a//text()")))
    link_density = _char_count(link_text) / max(text_chars, 1)
    negative_text_hits = sum(text.count(keyword) for keyword in _NEGATIVE_TEXT_HINTS)
    return text_chars, paragraph_count, punct_density, link_density, negative_text_hits


def _html_quality_score(html: str, min_chars: int) -> float:
    text_chars, paragraph_count, punct_density, link_density, negative_text_hits = _html_metrics(html)

    score = 0.0
    score += min(text_chars, 22000) / 35.0
    score += min(paragraph_count, 100) * 10.0
    score += min(punct_density, 0.22) * 300.0
    score -= min(link_density, 1.0) * 240.0
    score -= min(negative_text_hits, 16) * 10.0

    if text_chars < min_chars:
        score -= (min_chars - text_chars) * 0.8
    return score


def _gather_seed_nodes(root: Any, min_chars: int) -> list[Any]:
    seeds: list[Any] = []
    seen_ids: set[int] = set()

    def add(node: Any) -> None:
        if node is None:
            return
        node_id = id(node)
        if node_id in seen_ids:
            return
        seen_ids.add(node_id)
        seeds.append(node)

    for node in root.iter():
        tag = _tag_name(node)
        if tag not in _CONTAINER_TAGS:
            continue

        attrs = _attr_blob(node)
        text = _node_text(node)
        text_chars = _char_count(text)
        paragraph_count = _paragraph_count(node, text)

        semantic = tag in {"article", "main"} or (node.get("role") or "").lower() == "main"
        hinted = _hint_hits(attrs, _POSITIVE_ATTR_HINTS) > 0
        structural = text_chars >= max(min_chars, 320) and paragraph_count >= 3
        if semantic or hinted or structural:
            add(node)

    # For weakly-labeled pages, lift candidates from long paragraph parents.
    for paragraph in root.xpath(".//p"):
        text = _node_text(paragraph)
        if _char_count(text) < 40:
            continue
        parent = paragraph.getparent()
        if _tag_name(parent) not in {"body", "html"}:
            add(parent)
        if parent is not None:
            grandparent = parent.getparent()
            if _tag_name(grandparent) not in {"body", "html"}:
                add(grandparent)

    return seeds


def extract_best_candidate_html(html: str, min_chars: int = 200) -> Optional[str]:
    try:
        from lxml import html as lxml_html
    except ImportError:
        return None

    try:
        root = lxml_html.fromstring(html or "")
    except Exception:
        return None

    seeds = _gather_seed_nodes(root, min_chars=min_chars)
    scored_candidates: list[_Candidate] = []
    for node in seeds:
        candidate = _candidate_score(node, min_chars=min_chars)
        if candidate:
            scored_candidates.append(candidate)

    if not scored_candidates:
        return None

    scored_candidates.sort(key=lambda item: item.score, reverse=True)

    best_html: Optional[str] = None
    best_score = float("-inf")
    seen_variants: set[str] = set()

    for candidate in scored_candidates[:10]:
        variants: list[str] = []

        base_html = _serialize_candidate(candidate.node, lxml_html)
        variants.append(base_html)

        sibling_group = _collect_sibling_group(candidate.node, min_chars=min_chars)
        if len(sibling_group) > 1:
            variants.append(_serialize_node_group(sibling_group, lxml_html))

        parent = candidate.node.getparent()
        if parent is not None and _tag_name(parent) in _CONTAINER_TAGS and _tag_name(parent) not in {"body", "html"}:
            variants.append(_serialize_candidate(parent, lxml_html))

        for variant in variants:
            normalized = _compact_whitespace(re.sub(r"<[^>]+>", " ", variant))
            if not normalized or normalized in seen_variants:
                continue
            seen_variants.add(normalized)

            variant_score = _html_quality_score(variant, min_chars=min_chars)
            if variant_score > best_score:
                best_score = variant_score
                best_html = variant

    if not best_html:
        return None

    text_chars, paragraph_count, _punct_density, link_density, _negative_hits = _html_metrics(best_html)
    char_floor = max(120, int(min_chars * 0.75))
    if text_chars < char_floor:
        return None
    if paragraph_count < 2 and text_chars < max(min_chars, 260):
        return None
    if link_density > 0.72:
        return None

    return best_html
