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
    IMAGE_DIMENSION_MIN_LONG_SIDE,
    IMAGE_DIMENSION_TIMEOUT,
    IMAGE_DIMENSION_WORKERS,
    USER_AGENT,
)

logger = logging.getLogger(__name__)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _normalize_image_url(src: str, base_url: str) -> str:
    src = (src or "").strip().strip('"\'')
    if not src or src.startswith(("data:", "blob:", "javascript:")):
        return ""
    return urljoin(base_url, src)


def _extract_images_from_html(html: str, base_url: str) -> list[str]:
    images: list[str] = []
    for match in re.finditer(r"<img\b[^>]*>", html or "", flags=re.IGNORECASE):
        tag = match.group(0)
        src = ""
        for attr in ("src", "data-src", "data-original", "data-lazy-src", "srcset", "data-srcset"):
            found = re.search(rf'{attr}\s*=\s*["\']([^"\']+)["\']', tag, flags=re.IGNORECASE)
            if found:
                src = found.group(1).split(",")[0].strip().split(" ")[0]
                break
        url = _normalize_image_url(src, base_url)
        if url:
            images.append(url)
    return _dedupe(images)


def _markdown_image_urls(markdown: str) -> list[str]:
    urls: list[str] = []
    pattern = r'!\[[^\]]*\]\(\s*([^)\s]+)(?:\s+["\'][^)]*["\'])?\s*\)'
    for match in re.finditer(pattern, markdown or ""):
        url = match.group(1).strip().strip("<>").strip()
        if url:
            urls.append(url)
    return _dedupe(urls)


def _normalize_markdown_image_url(url: str, base_url: str = "") -> str:
    if base_url:
        return _normalize_image_url(url, base_url)
    return (url or "").strip().strip("<>").strip()


def append_unreferenced_images(markdown: str, images: list[str], base_url: str = "") -> str:
    """Append images not referenced in markdown so they remain visible in output."""
    markdown = markdown or ""
    existing = _markdown_image_urls(markdown)
    existing_set = set(existing)
    normalized_existing = {_normalize_markdown_image_url(url, base_url) for url in existing}

    for image_url in _dedupe(images):
        if image_url in existing_set or image_url in normalized_existing:
            continue
        markdown += f"\n\n![]( {image_url} )"
        existing_set.add(image_url)
    return markdown


def _is_svg_url(url: str) -> bool:
    parsed = urlparse(url or "")
    path = (parsed.path or "").lower()
    query = (parsed.query or "").lower()
    return (
        path.endswith(".svg")
        or ".svg/" in path
        or "image/svg" in query
        or "image/svg" in path
        or "/svg" in path
    )


def _parse_image_dimensions(data: bytes) -> Optional[tuple[int, int]]:
    if len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return struct.unpack(">II", data[16:24])

    if len(data) >= 10 and data[:6] in (b"GIF87a", b"GIF89a"):
        return struct.unpack("<HH", data[6:10])

    if len(data) >= 30 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X" and len(data) >= 30:
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
            return width, height
        if chunk == b"VP8 " and len(data) >= 30:
            start = data.find(b"\x9d\x01\x2a", 20)
            if start != -1 and start + 7 <= len(data):
                width = int.from_bytes(data[start + 3 : start + 5], "little") & 0x3FFF
                height = int.from_bytes(data[start + 5 : start + 7], "little") & 0x3FFF
                return width, height
        if chunk == b"VP8L" and len(data) >= 25:
            bits = int.from_bytes(data[21:25], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
            return width, height

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
            if marker in (
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            ):
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
        with requests.get(
            url,
            headers=headers,
            timeout=IMAGE_DIMENSION_TIMEOUT,
            stream=True,
            allow_redirects=True,
        ) as response:
            response.raise_for_status()
            content_type = (response.headers.get("Content-Type") or "").lower()
            if "image/svg" in content_type:
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
    except Exception as exc:  # pragma: no cover - network behavior
        logger.debug("Image dimension probe failed for %s: %s", url, exc)
        dimensions = None

    with _IMAGE_DIMENSION_CACHE_LOCK:
        _IMAGE_DIMENSION_CACHE[url] = dimensions
    return dimensions


def _strip_filtered_markdown_images(markdown: str, filtered_urls: set[str], base_url: str = "") -> str:
    from markdown import clean_markdown

    image_ref = re.compile(r'!\[[^\]]*\]\(\s*([^)\s]+)(?:\s+["\'][^)]*["\'])?\s*\)')
    lines: list[str] = []
    for line in (markdown or "").replace("\r\n", "\n").split("\n"):
        matches = list(image_ref.finditer(line))
        if not matches:
            lines.append(line.rstrip())
            continue

        stripped_line = line
        remove_entire_line = False
        for match in reversed(matches):
            url = match.group(1).strip().strip("<>").strip()
            normalized_url = _normalize_markdown_image_url(url, base_url)
            if url not in filtered_urls and normalized_url not in filtered_urls:
                continue
            without_ref = stripped_line[: match.start()] + stripped_line[match.end() :]
            if without_ref.strip() == "":
                remove_entire_line = True
            stripped_line = without_ref

        if not remove_entire_line:
            lines.append(stripped_line.rstrip())

    return clean_markdown("\n".join(lines))


def _is_content_image_dimensions(
    dimensions: tuple[int, int],
    min_long_side: int = IMAGE_DIMENSION_MIN_LONG_SIDE,
    max_aspect_ratio: float = IMAGE_ASPECT_RATIO_MAX,
) -> bool:
    """Keep likely article images: large enough and not square/ultra-wide."""
    width, height = dimensions
    if width <= 0 or height <= 0:
        return False
    ratio = width / height
    return (
        (width >= min_long_side or height >= min_long_side)
        and ((0 < ratio < 1) or (1 < ratio <= max_aspect_ratio))
    )


def _strip_svg_and_non_content(
    markdown: str,
    images: list[str],
    min_long_side: int = 0,
    max_aspect_ratio: float = 0,
    base_url: str = "",
    fail_open: bool = IMAGE_DIMENSION_FAIL_OPEN,
    dimension_fetcher: Optional[Callable[[str], Optional[tuple[int, int]]]] = None,
) -> str:
    """Remove SVG and known-non-content images from arrays and markdown refs."""
    fetcher = dimension_fetcher or _fetch_image_dimensions

    markdown_urls = _markdown_image_urls(markdown)
    normalized_markdown_urls = [_normalize_markdown_image_url(url, base_url) for url in markdown_urls]
    candidates = _dedupe(images + normalized_markdown_urls)

    filtered_urls = {url for url in candidates if _is_svg_url(url)}
    filtered_urls.update(url for url in markdown_urls if _is_svg_url(url))

    probe_urls = [
        url
        for url in candidates
        if min_long_side > 0
        and max_aspect_ratio > 0
        and url not in filtered_urls
        and urlparse(url).scheme in ("http", "https")
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
                        filtered_urls.add(url)
                    continue
                if dimensions is None:
                    if not fail_open:
                        filtered_urls.add(url)
                    continue
                if not _is_content_image_dimensions(
                    dimensions,
                    min_long_side=min_long_side,
                    max_aspect_ratio=max_aspect_ratio,
                ):
                    filtered_urls.add(url)

    images[:] = [url for url in images if url not in filtered_urls]
    return _strip_filtered_markdown_images(markdown, filtered_urls, base_url=base_url)


def finalize_markdown_and_images(
    markdown: str,
    images: list[str],
    base_url: str,
    image_fail_open: bool,
    min_long_side: int = IMAGE_DIMENSION_MIN_LONG_SIDE,
    max_aspect_ratio: float = IMAGE_ASPECT_RATIO_MAX,
) -> str:
    """Shared adapter post-processing pipeline."""
    from markdown import clean_markdown

    images[:] = _dedupe(images)
    markdown = append_unreferenced_images(markdown, images, base_url=base_url)
    markdown = _strip_svg_and_non_content(
        markdown,
        images,
        min_long_side=min_long_side,
        max_aspect_ratio=max_aspect_ratio,
        base_url=base_url,
        fail_open=image_fail_open,
    )
    return clean_markdown(markdown)


# Backward-friendly aliases.
normalize_image_url = _normalize_image_url
extract_images_from_html = _extract_images_from_html
dedupe = _dedupe
