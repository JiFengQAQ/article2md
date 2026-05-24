"""Generic content-candidate scoring for fallback extraction."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Optional
from markdown import clean_markdown

_TEXT_XPATH = ".//text()[not(ancestor::script) and not(ancestor::style) and not(ancestor::noscript) and not(ancestor::template)]"
_PUNCT_RE = re.compile(r"[，。！？；：、,.!?;:]")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")

_CONTAINER_TAGS = {"article", "main", "section", "div", "td"}
_HARD_PRUNE_TAGS = {"nav", "aside", "footer", "header", "form", "button", "input", "select", "textarea", "iframe"}
_ALWAYS_PRUNE_TAGS = {"script", "style", "noscript", "template", "svg", "canvas", "iframe"}
_POSITIVE_ATTR_HINTS = ("article", "content", "post", "detail", "entry", "story", "正文", "rich_media", "main", "text", "body", "news")
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
    "copyright",
)
_NEGATIVE_TEXT_HINTS = ("相关阅读", "相关推荐", "推荐阅读", "热门推荐", "猜你喜欢", "大家都在看", "上一篇", "下一篇", "网友评论", "评论区", "登录后评论", "文明上网理性发言", "返回首页", "回到首页", "广告")
_CONTENT_KEYWORDS = ("表示", "认为", "指出", "介绍", "记者", "报道", "发布", "消息", "此外", "同时", "according", "report")


@dataclass
class TextStats:
    chars: int
    paragraphs: int
    sentences: int
    punct_density: float
    link_density: float
    negative_text_hits: int


@dataclass
class _Candidate:
    node: Any
    score: float
    text_chars: int
    paragraph_count: int
    link_density: float


def _compact_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _char_count(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def _tag_name(node: Any) -> str:
    tag = getattr(node, "tag", "")
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _hint_hits(haystack: str, hints: tuple[str, ...]) -> int:
    return sum(1 for hint in hints if hint in haystack)


def _attr_blob(node: Any) -> str:
    def _flatten(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            out: list[str] = []
            for item in value:
                out.extend(_flatten(item))
            return out
        text = str(value).strip()
        return [text] if text else []

    attrs = (node.get("id"), node.get("class"), node.get("role"), node.get("itemprop"), node.get("data-role"), node.get("aria-label"), node.get("aria-labelledby"))
    tokens: list[str] = []
    for value in attrs:
        tokens.extend(_flatten(value))
    return " ".join(tokens).lower()


def _node_text(node: Any) -> str:
    return _compact_whitespace(" ".join(str(text) for text in node.xpath(_TEXT_XPATH) if text))


def _sentence_count(text: str) -> int:
    return len([part for part in re.split(r"[。！？.!?]", text) if _char_count(part) >= 10])


def _paragraph_count(node: Any, text: str) -> int:
    paragraphs = sum(1 for p in node.xpath(".//p") if _char_count(_node_text(p)) >= 24)
    if paragraphs:
        return paragraphs
    return len([part for part in re.split(r"[。！？.!?]", text) if _char_count(part) >= 20])


def _stats_from_text(text: str, paragraph_count: int, link_text: str = "") -> TextStats:
    chars = _char_count(text)
    return TextStats(
        chars=chars,
        paragraphs=paragraph_count,
        sentences=_sentence_count(text),
        punct_density=len(_PUNCT_RE.findall(text)) / max(chars, 1),
        link_density=_char_count(link_text) / max(chars, 1),
        negative_text_hits=sum(text.count(keyword) for keyword in _NEGATIVE_TEXT_HINTS),
    )


def _markdown_plain_text(markdown: str) -> str:
    cleaned = clean_markdown(markdown or "")
    text = _MARKDOWN_IMAGE_RE.sub(" ", cleaned)
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)
    return _compact_whitespace(re.sub(r"[#>*_`~\-]+", " ", text))


def markdown_body_metrics(markdown: str) -> dict[str, float]:
    cleaned = clean_markdown(markdown or "")
    plain = _markdown_plain_text(cleaned)
    paragraphs = sum(1 for line in cleaned.splitlines() if _char_count(line.strip().lstrip("#>*-0123456789. ")) >= 24)
    if paragraphs == 0:
        paragraphs = len([part for part in re.split(r"[。！？.!?]", plain) if _char_count(part) >= 20])
    link_chars = sum(len(match.group(0)) for match in _MARKDOWN_LINK_RE.finditer(cleaned))
    stats = _stats_from_text(plain, paragraphs, link_text="x" * link_chars)
    return {"char_count": float(stats.chars), "paragraph_count": float(stats.paragraphs), "punct_density": float(stats.punct_density), "link_density": float(stats.link_density)}


def _markdown_quality_score(markdown: str) -> float:
    metrics = markdown_body_metrics(markdown)
    return metrics["char_count"] + metrics["paragraph_count"] * 120.0 + metrics["punct_density"] * 3200.0 - metrics["link_density"] * 1800.0


def choose_best_markdown(markdowns: list[str], min_chars: int = 220, min_paragraphs: int = 3) -> str:
    best_markdown, best_score = "", float("-inf")
    for candidate in markdowns:
        cleaned = clean_markdown(candidate or "")
        if not cleaned:
            continue
        metrics = markdown_body_metrics(cleaned)
        score = _markdown_quality_score(cleaned)
        if metrics["char_count"] < min_chars:
            score -= (min_chars - metrics["char_count"]) * 0.8
        if metrics["paragraph_count"] < min_paragraphs:
            score -= (min_paragraphs - metrics["paragraph_count"]) * 90.0
        if score > best_score:
            best_score, best_markdown = score, cleaned
    return best_markdown


def is_markdown_body_sufficient(markdown: str, min_chars: int = 220, min_paragraphs: int = 3) -> bool:
    metrics = markdown_body_metrics(markdown)
    return metrics["char_count"] >= min_chars and metrics["paragraph_count"] >= min_paragraphs and metrics["punct_density"] >= 0.006 and metrics["link_density"] <= 0.55


def _descendant_negative_count(node: Any) -> int:
    count = 0
    for descendant in node.iterdescendants():
        if _tag_name(descendant) in _HARD_PRUNE_TAGS or _hint_hits(_attr_blob(descendant), _NEGATIVE_ATTR_HINTS):
            count += 1
            if count >= 40:
                break
    return count


def _candidate_score(node: Any, min_chars: int) -> Optional[_Candidate]:
    text = _node_text(node)
    paragraphs = _paragraph_count(node, text)
    stats = _stats_from_text(text, paragraphs, _compact_whitespace(" ".join(node.xpath(".//a//text()"))))
    if stats.chars < max(60, min_chars // 3):
        return None
    attrs, tag, role = _attr_blob(node), _tag_name(node), (node.get("role") or "").lower()
    positive_hits = _hint_hits(attrs, _POSITIVE_ATTR_HINTS)
    negative_hits = _hint_hits(attrs, _NEGATIVE_ATTR_HINTS)
    semantic_bonus = 30.0 if (tag in {"article", "main"} or role == "main") else 0.0
    keyword_hits = sum(text.lower().count(keyword) for keyword in _CONTENT_KEYWORDS)
    descendant_negative = _descendant_negative_count(node)
    li_count = len(node.xpath(".//li"))
    list_penalty = max(0, li_count - stats.paragraphs * 4)
    heading_bonus = 14.0 if any(_char_count(_node_text(h)) >= 8 for h in node.xpath(".//h1|.//h2")) else 0.0
    score = 0.0
    score += min(stats.chars, 22000) / 35.0 + min(stats.paragraphs, 100) * 10.0 + min(stats.sentences, 140) * 3.0
    score += min(keyword_hits, 16) * 4.0 + min(stats.punct_density, 0.22) * 280.0 + min(positive_hits, 8) * 14.0 + semantic_bonus + heading_bonus
    score -= min(stats.link_density, 1.0) * 260.0 + min(negative_hits, 8) * 18.0 + min(descendant_negative, 40) * 8.0
    score -= min(stats.negative_text_hits, 16) * 11.0 + min(list_penalty, 120) * 1.2
    if stats.chars < min_chars:
        score -= (min_chars - stats.chars) * 0.7
    if stats.paragraphs < 2:
        score -= 40.0
    if stats.sentences < 2:
        score -= 20.0
    return _Candidate(node=node, score=score, text_chars=stats.chars, paragraph_count=stats.paragraphs, link_density=stats.link_density)


def _is_hard_negative_node(node: Any) -> bool:
    if _tag_name(node) in _HARD_PRUNE_TAGS:
        return True
    attrs = _attr_blob(node)
    negative_hits = _hint_hits(attrs, _NEGATIVE_ATTR_HINTS)
    if negative_hits == 0:
        return False
    text = _node_text(node)
    chars = _char_count(text)
    link_density = _char_count(_compact_whitespace(" ".join(node.xpath(".//a//text()")))) / max(chars, 1)
    negative_text_hits = sum(text.count(keyword) for keyword in _NEGATIVE_TEXT_HINTS)
    return (
        (negative_hits >= 2 and chars <= 2600)
        or (negative_text_hits > 0 and chars <= 2600)
        or (link_density >= 0.45 and chars <= 2200)
        or (negative_hits >= 1 and link_density >= 0.18 and chars <= 1300)
    )


def _should_prune_noise(node: Any) -> bool:
    if _is_hard_negative_node(node):
        return True
    if _tag_name(node) not in {"div", "section", "ul", "ol", "li"}:
        return False
    has_media = bool(node.xpath(".//img|.//picture|.//figure|.//video|.//source"))
    text = _node_text(node)
    chars = _char_count(text)
    if chars == 0 and not has_media:
        return True
    link_density = _char_count(_compact_whitespace(" ".join(node.xpath(".//a//text()")))) / max(chars, 1)
    punct_density = len(_PUNCT_RE.findall(text)) / max(chars, 1)
    paragraphs = _paragraph_count(node, text)
    li_count = len(node.xpath(".//li"))
    return (link_density >= 0.55 and punct_density < 0.01) or (li_count >= 8 and paragraphs <= 1 and link_density >= 0.15)


def _prune_candidate_tree(node: Any) -> Any:
    cleaned = deepcopy(node)
    for descendant in list(cleaned.iterdescendants()):
        if _tag_name(descendant) in _ALWAYS_PRUNE_TAGS or _should_prune_noise(descendant):
            parent = descendant.getparent()
            if parent is not None:
                parent.remove(descendant)
    return cleaned


def _serialize_candidate(node: Any, lxml_html: Any) -> str:
    return lxml_html.tostring(_prune_candidate_tree(node), encoding="unicode", method="html")


def _sibling_is_content_like(node: Any, min_chars: int) -> bool:
    if _is_hard_negative_node(node):
        return False
    text = _node_text(node)
    chars = _char_count(text)
    if chars < max(40, min_chars // 4):
        return False
    paragraphs = _paragraph_count(node, text)
    link_density = _char_count(_compact_whitespace(" ".join(node.xpath(".//a//text()")))) / max(chars, 1)
    punct_density = len(_PUNCT_RE.findall(text)) / max(chars, 1)
    if link_density > 0.6 and paragraphs < 2:
        return False
    if punct_density < 0.004 and paragraphs <= 1 and chars < 300:
        return False
    return True


def _collect_sibling_group(node: Any, min_chars: int, max_hops: int = 6) -> list[Any]:
    parent = node.getparent()
    if parent is None:
        return [node]
    siblings = list(parent)
    try:
        center = siblings.index(node)
    except ValueError:
        return [node]

    def _walk(direction: int) -> list[Any]:
        picked: list[Any] = []
        misses = 0
        for offset in range(1, max_hops + 1):
            idx = center + direction * offset
            if idx < 0 or idx >= len(siblings):
                break
            sibling = siblings[idx]
            if _is_hard_negative_node(sibling):
                break
            if _sibling_is_content_like(sibling, min_chars=min_chars):
                picked.append(sibling)
                misses = 0
            else:
                misses += 1
                if misses >= 2:
                    break
        return picked

    return list(reversed(_walk(-1))) + [node] + _walk(1)


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
    stats = _stats_from_text(text, _paragraph_count(root, text), _compact_whitespace(" ".join(root.xpath(".//a//text()"))))
    return stats.chars, stats.paragraphs, stats.punct_density, stats.link_density, stats.negative_text_hits


def _html_quality_score(html: str, min_chars: int) -> float:
    chars, paragraphs, punct_density, link_density, negative_hits = _html_metrics(html)
    score = min(chars, 22000) / 35.0 + min(paragraphs, 100) * 10.0 + min(punct_density, 0.22) * 280.0
    score -= min(link_density, 1.0) * 240.0 + min(negative_hits, 16) * 10.0
    if chars < min_chars:
        score -= (min_chars - chars) * 0.8
    return score


def _gather_seed_nodes(root: Any, min_chars: int) -> list[Any]:
    seeds: list[Any] = []
    seen: set[int] = set()

    def _add(node: Any) -> None:
        if node is None:
            return
        key = id(node)
        if key in seen:
            return
        seen.add(key)
        seeds.append(node)

    for node in root.iter():
        tag = _tag_name(node)
        if tag not in _CONTAINER_TAGS:
            continue
        text = _node_text(node)
        paragraphs = _paragraph_count(node, text)
        chars = _char_count(text)
        attrs = _attr_blob(node)
        semantic = tag in {"article", "main"} or (node.get("role") or "").lower() == "main"
        hinted = _hint_hits(attrs, _POSITIVE_ATTR_HINTS) > 0
        structural = chars >= max(min_chars, 320) and paragraphs >= 3
        if semantic or hinted or structural:
            _add(node)

    for paragraph in root.xpath(".//p"):
        if _char_count(_node_text(paragraph)) < 40:
            continue
        parent = paragraph.getparent()
        if _tag_name(parent) not in {"body", "html"}:
            _add(parent)
        if parent is not None and _tag_name(parent.getparent()) not in {"body", "html"}:
            _add(parent.getparent())
    return seeds


def extract_best_candidate_html(html: str, min_chars: int = 200) -> Optional[str]:
    try:
        from lxml import html as lxml_html

        root = lxml_html.fromstring(html or "")
    except Exception:
        return None
    scored = [candidate for candidate in (_candidate_score(node, min_chars=min_chars) for node in _gather_seed_nodes(root, min_chars=min_chars)) if candidate]
    if not scored:
        return None
    scored.sort(key=lambda item: item.score, reverse=True)
    best_html: Optional[str] = None
    best_score = float("-inf")
    seen_variants: set[str] = set()

    for candidate in scored[:10]:
        variants = [_serialize_candidate(candidate.node, lxml_html)]
        siblings = _collect_sibling_group(candidate.node, min_chars=min_chars)
        if len(siblings) > 1:
            variants.append(_serialize_node_group(siblings, lxml_html))
        parent = candidate.node.getparent()
        if parent is not None and _tag_name(parent) in _CONTAINER_TAGS and _tag_name(parent) not in {"body", "html"}:
            variants.append(_serialize_candidate(parent, lxml_html))
        for variant in variants:
            normalized = _compact_whitespace(re.sub(r"<[^>]+>", " ", variant))
            if not normalized or normalized in seen_variants:
                continue
            seen_variants.add(normalized)
            score = _html_quality_score(variant, min_chars=min_chars)
            if score > best_score:
                best_score, best_html = score, variant

    if not best_html:
        return None
    chars, paragraphs, _punct, link_density, _neg = _html_metrics(best_html)
    if chars < max(120, int(min_chars * 0.75)):
        return None
    if paragraphs < 2 and chars < max(min_chars, 260):
        return None
    if link_density > 0.72:
        return None
    return best_html
