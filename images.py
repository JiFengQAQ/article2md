"""Image extraction, normalization, and content-image filtering."""

from __future__ import annotations

import logging
import re
import struct
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse
import requests
from models import (
    IMAGE_ASPECT_RATIO_MAX,
    IMAGE_DIMENSION_BYTE_CAP,
    IMAGE_DIMENSION_FAIL_OPEN,
    IMAGE_DIMENSION_MIN_SIDE,
    IMAGE_DIMENSION_TIMEOUT,
    IMAGE_DIMENSION_WORKERS,
    USER_AGENT,
)

logger = logging.getLogger(__name__)
_INVALID_IMAGE_SCHEMES = ("data:", "blob:", "javascript:")
_PLACEHOLDER_IMAGE_HINTS = ("placeholder", "holder", "spacer", "blank", "lazyload", "loading", "default", "pixel", "transparent", "1x1", "t.png", "t.gif")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(\s*<?([^)>\s]+)>?(\s+['\"][^)]*['\"])?\s*\)")


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _normalize_image_url(src: str, base_url: str) -> str:
    src = (src or "").strip().strip('"\'')
    if not src or src.lower().startswith(_INVALID_IMAGE_SCHEMES):
        return ""
    return urljoin(base_url, src)


def _looks_like_placeholder_image_url(url: str) -> bool:
    parsed = urlparse(url or "")
    path = (parsed.path or "").lower()
    if not path:
        return True
    blob = f"{path}?{(parsed.query or '').lower()}#{(parsed.fragment or '').lower()}"
    return any(hint in blob for hint in _PLACEHOLDER_IMAGE_HINTS)


def _choose_largest_srcset_candidate(srcset: str) -> str:
    best_url, best_score = "", float("-inf")
    for item in (srcset or "").split(","):
        part = item.strip()
        if not part:
            continue
        pieces = part.split()
        url = pieces[0].strip()
        descriptor = pieces[1].strip().lower() if len(pieces) > 1 else ""
        score = 0.0
        if descriptor.endswith("w"):
            try:
                score = float(descriptor[:-1])
            except ValueError:
                pass
        elif descriptor.endswith("x"):
            try:
                score = float(descriptor[:-1]) * 1000.0
            except ValueError:
                pass
        if score > best_score:
            best_score, best_url = score, url
    return best_url


def _valid_candidate_urls(img: object, base_url: str) -> list[str]:
    raw_values: list[str] = []
    for attr in ("srcset", "data-srcset"):
        srcset = img.get(attr)
        if srcset:
            picked = _choose_largest_srcset_candidate(srcset)
            if picked:
                raw_values.append(picked)
    for attr in ("src", "data-src", "data-original", "data-webp", "data-lazy-src", "data-actualsrc", "poster"):
        value = img.get(attr)
        if value:
            raw_values.append(value)
    normalized: list[str] = []
    for value in raw_values:
        url = _normalize_image_url(value, base_url)
        if url and not _looks_like_placeholder_image_url(url):
            normalized.append(url)
    return _dedupe(normalized)


def normalize_html_images(html: str, base_url: str) -> str:
    if not html:
        return ""
    try:
        from lxml import html as lxml_html

        root = lxml_html.fromstring(html)
    except Exception:
        return html
    for img in list(root.xpath(".//img")):
        candidates = _valid_candidate_urls(img, base_url)
        if not candidates:
            parent = img.getparent()
            if parent is not None:
                parent.remove(img)
            continue
        img.set("src", candidates[0])
    return lxml_html.tostring(root, encoding="unicode", method="html")


def _extract_images_from_html(html: str, base_url: str) -> list[str]:
    if not html:
        return []
    normalized_html = normalize_html_images(html, base_url)
    try:
        from lxml import html as lxml_html

        root = lxml_html.fromstring(normalized_html)
    except Exception:
        return []
    images: list[str] = []
    for img in root.xpath(".//img"):
        normalized = _normalize_image_url((img.get("src") or "").strip(), base_url)
        if normalized and not _is_svg_url(normalized):
            images.append(normalized)
    return _dedupe(images)


def _normalize_markdown_image_url(url: str, base_url: str = "") -> str:
    return _normalize_image_url(url, base_url) if base_url else (url or "").strip().strip("<>").strip()


def _markdown_image_urls(markdown: str) -> list[str]:
    return _dedupe([
        url
        for url in (_normalize_markdown_image_url(match.group(2)) for match in _MARKDOWN_IMAGE_RE.finditer(markdown or ""))
        if url
    ])


def _rewrite_markdown_images(markdown: str, base_url: str = "", filtered_urls: set[str] | None = None) -> tuple[str, list[str]]:
    filtered_urls = filtered_urls or set()
    exported: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        alt_text, raw_url, title = match.group(1), _normalize_markdown_image_url(match.group(2)), match.group(3) or ""
        normalized = _normalize_markdown_image_url(raw_url, base_url=base_url)
        if not normalized or _is_svg_url(normalized) or raw_url in filtered_urls or normalized in filtered_urls:
            return ""
        exported.append(normalized)
        return f"![{alt_text}]({normalized}{title})"

    return _MARKDOWN_IMAGE_RE.sub(_replace, markdown or ""), _dedupe(exported)


def append_unreferenced_images(markdown: str, images: list[str], base_url: str = "") -> str:
    _ = (images, base_url)
    return markdown or ""


def _is_svg_url(url: str) -> bool:
    parsed = urlparse(url or "")
    path, query = (parsed.path or "").lower(), (parsed.query or "").lower()
    return path.endswith(".svg") or ".svg/" in path or "image/svg" in query or "image/svg" in path or "/svg" in path


def _parse_image_dimensions(data: bytes) -> Optional[tuple[int, int]]:
    if len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return struct.unpack(">II", data[16:24])
    if len(data) >= 10 and data[:6] in (b"GIF87a", b"GIF89a"):
        return struct.unpack("<HH", data[6:10])
    if len(data) >= 30 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X":
            return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
        if chunk == b"VP8 ":
            start = data.find(b"\x9d\x01\x2a", 20)
            if start != -1 and start + 7 <= len(data):
                width = int.from_bytes(data[start + 3 : start + 5], "little") & 0x3FFF
                height = int.from_bytes(data[start + 5 : start + 7], "little") & 0x3FFF
                return width, height
        if chunk == b"VP8L" and len(data) >= 25:
            bits = int.from_bytes(data[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if len(data) >= 4 and data.startswith(b"\xff\xd8"):
        index = 2
        while index + 9 <= len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            while index < len(data) and data[index] == 0xFF:
                index += 1
            if index >= len(data):
                break
            marker = data[index]
            index += 1
            if marker in (0x01,) or 0xD0 <= marker <= 0xD9:
                continue
            if index + 2 > len(data):
                break
            size = int.from_bytes(data[index : index + 2], "big")
            if size < 2:
                break
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                if index + 7 <= len(data):
                    height = int.from_bytes(data[index + 3 : index + 5], "big")
                    width = int.from_bytes(data[index + 5 : index + 7], "big")
                    return width, height
                break
            index += size
    return None


_IMAGE_DIMENSION_CACHE: dict[str, Optional[tuple[int, int]]] = {}
_IMAGE_DIMENSION_CACHE_LOCK = threading.Lock()


def _fetch_image_dimensions(url: str) -> Optional[tuple[int, int]]:
    with _IMAGE_DIMENSION_CACHE_LOCK:
        if url in _IMAGE_DIMENSION_CACHE:
            return _IMAGE_DIMENSION_CACHE[url]
    dimensions: Optional[tuple[int, int]] = None
    try:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Range": f"bytes=0-{IMAGE_DIMENSION_BYTE_CAP - 1}",
        }
        with requests.get(url, headers=headers, timeout=IMAGE_DIMENSION_TIMEOUT, stream=True, allow_redirects=True) as response:
            response.raise_for_status()
            if "image/svg" in (response.headers.get("Content-Type") or "").lower():
                dimensions = (0, 0)
            else:
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_content(chunk_size=16384):
                    if not chunk:
                        continue
                    remaining = IMAGE_DIMENSION_BYTE_CAP - total
                    chunks.append(chunk[:remaining])
                    total += min(len(chunk), remaining)
                    dimensions = _parse_image_dimensions(b"".join(chunks))
                    if dimensions or total >= IMAGE_DIMENSION_BYTE_CAP:
                        break
    except Exception as exc:
        logger.debug("Image dimension probe failed for %s: %s", url, exc)
    with _IMAGE_DIMENSION_CACHE_LOCK:
        _IMAGE_DIMENSION_CACHE[url] = dimensions
    return dimensions


def _strip_filtered_markdown_images(markdown: str, filtered_urls: set[str], base_url: str = "") -> str:
    return _rewrite_markdown_images(markdown, base_url=base_url, filtered_urls=filtered_urls)[0]


def _is_content_image_dimensions(
    dimensions: tuple[int, int],
    min_side: int = IMAGE_DIMENSION_MIN_SIDE,
    max_landscape_aspect: float = IMAGE_ASPECT_RATIO_MAX,
) -> bool:
    width, height = dimensions
    if width <= 0 or height <= 0 or width == height:
        return False
    if width < min_side and height < min_side:
        return False
    return width <= height or (width / height <= max_landscape_aspect)


def _strip_svg_and_non_content(
    markdown: str,
    images: list[str],
    min_side: int = 0,
    max_landscape_aspect: float = 0,
    base_url: str = "",
    fail_open: bool = IMAGE_DIMENSION_FAIL_OPEN,
    dimension_fetcher: Optional[Callable[[str], Optional[tuple[int, int]]]] = None,
) -> str:
    fetcher = dimension_fetcher or _fetch_image_dimensions
    markdown_raw_urls = _markdown_image_urls(markdown)
    markdown_urls = [_normalize_markdown_image_url(url, base_url) for url in markdown_raw_urls]
    image_urls = [_normalize_image_url(url, base_url) for url in images]
    candidates = _dedupe([url for url in image_urls + markdown_urls if url])
    filtered: set[str] = {url for url in candidates if _is_svg_url(url)}
    filtered.update(url for url in markdown_raw_urls if _is_svg_url(url))
    filtered.update(url for url in markdown_raw_urls if not _normalize_markdown_image_url(url, base_url=base_url))
    probe_urls = [
        url
        for url in candidates
        if min_side > 0 and max_landscape_aspect > 0 and url not in filtered and urlparse(url).scheme in ("http", "https")
    ]
    if probe_urls:
        workers = min(IMAGE_DIMENSION_WORKERS, len(probe_urls))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(fetcher, url): url for url in probe_urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    dimensions = future.result()
                except Exception as exc:
                    logger.debug("Image dimension worker failed for %s: %s", url, exc)
                    if not fail_open:
                        filtered.add(url)
                    continue
                if dimensions is None:
                    if not fail_open:
                        filtered.add(url)
                    continue
                if not _is_content_image_dimensions(dimensions, min_side=min_side, max_landscape_aspect=max_landscape_aspect):
                    filtered.add(url)
    images[:] = _dedupe([
        normalized
        for normalized in (_normalize_image_url(url, base_url) for url in images)
        if normalized and normalized not in filtered
    ])
    return _strip_filtered_markdown_images(markdown, filtered, base_url=base_url)


def finalize_markdown_and_images(
    markdown: str,
    images: list[str],
    base_url: str,
    image_fail_open: bool,
    min_side: int = IMAGE_DIMENSION_MIN_SIDE,
    max_landscape_aspect: float = IMAGE_ASPECT_RATIO_MAX,
) -> str:
    from markdown import clean_markdown

    images[:] = _dedupe([url for url in (_normalize_image_url(item, base_url) for item in images) if url])
    markdown = _rewrite_markdown_images(markdown, base_url=base_url)[0]
    markdown = _strip_svg_and_non_content(
        markdown,
        images,
        min_side=min_side,
        max_landscape_aspect=max_landscape_aspect,
        base_url=base_url,
        fail_open=image_fail_open,
    )
    markdown = clean_markdown(markdown)
    images[:] = _rewrite_markdown_images(markdown, base_url=base_url)[1]
    return markdown


normalize_image_url = _normalize_image_url
extract_images_from_html = _extract_images_from_html
dedupe = _dedupe
