"""
文章 → Markdown 提取器
通用架构：已知平台 API 直调（快）→ 未知平台 Playwright 兜底（慢）
零 Hermes 依赖，纯 Python + pip
"""
import re
import json
import logging
import time
import argparse
import struct
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional, Any
from urllib.parse import urlparse, parse_qs, urljoin

import requests
from html2text import HTML2Text

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10
DEFAULT_RETRIES = 2
IMAGE_DIMENSION_MIN_W = 600
IMAGE_DIMENSION_MIN_H = 450
IMAGE_DIMENSION_BYTE_CAP = 512 * 1024
IMAGE_DIMENSION_WORKERS = 8
IMAGE_DIMENSION_TIMEOUT = (3.05, 3)

CAPTCHA_PATTERNS = (
    "百度安全验证",
    "安全验证",
    "请完成下方验证",
    "验证码",
    "captcha",
    "anti-bot",
    "人机验证",
)

BOILERPLATE_PATTERNS = (
    r"^\s*同意并继续\s*$",
    r"^\s*请登录.*$",
    r"^\s*登录后.*$",
    r"^\s*打开.*?APP.*$",
    r"^\s*下载.*?APP.*$",
    r"^\s*cookie\s+.*$",
    r"^\s*Cookies?\s+.*$",
    r"^\s*继续浏览.*$",
)


@dataclass
class Article:
    title: str = ""
    subtitle: str = ""
    author: str = ""
    source_url: str = ""
    markdown: str = ""
    images: list[str] = field(default_factory=list)


# ── HTML → Markdown 转换器 ──────────────────────────────────────────

def _make_converter() -> HTML2Text:
    h = HTML2Text()
    h.body_width = 0          # 不自动换行
    h.ignore_links = False     # 保留链接
    h.ignore_images = False    # 保留图片 ![](url)
    h.images_to_alt = False    # 图片用 src 而非 alt
    h.skip_internal_links = False
    h.protect_links = True
    return h


def html_to_markdown(html: str) -> str:
    """Rich HTML → Markdown"""
    return _make_converter().handle(html).strip()


def _clean_markdown(markdown: str) -> str:
    """Remove common extraction boilerplate while preserving paragraph layout."""
    if not markdown:
        return ""
    lines = []
    blank = False
    for line in markdown.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if any(re.search(p, stripped, re.IGNORECASE) for p in BOILERPLATE_PATTERNS):
            continue
        if not stripped:
            if not blank:
                lines.append("")
            blank = True
            continue
        lines.append(line.rstrip())
        blank = False
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


def _is_quality_article(article: Optional[Article], min_chars: int = 100) -> bool:
    if not article:
        return False
    markdown = _clean_markdown(article.markdown)
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


def _normalize_image_url(src: str, base_url: str) -> str:
    src = (src or "").strip().strip("\"'")
    if not src or src.startswith(("data:", "blob:", "javascript:")):
        return ""
    return urljoin(base_url, src)


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _decoded_response_text(resp: requests.Response) -> str:
    """Decode HTML with a Chinese-friendly fallback when servers lie/omit charset."""
    encoding = (resp.encoding or "").lower()
    apparent = resp.apparent_encoding or ""
    if apparent and (not encoding or encoding in {"iso-8859-1", "ascii"}):
        resp.encoding = apparent
    return resp.text


def _extract_images_from_html(html: str, base_url: str) -> list[str]:
    images = []
    for match in re.finditer(r"<img\b[^>]*>", html or "", flags=re.IGNORECASE):
        tag = match.group(0)
        src = ""
        for attr in ("src", "data-src", "data-original", "data-lazy-src", "srcset", "data-srcset"):
            m = re.search(rf'{attr}\s*=\s*["\']([^"\']+)["\']', tag, flags=re.IGNORECASE)
            if m:
                src = m.group(1).split(",")[0].strip().split(" ")[0]
                break
        url = _normalize_image_url(src, base_url)
        if url:
            images.append(url)
    return _dedupe(images)


_IMAGE_DIMENSION_CACHE: dict[str, Optional[tuple[int, int]]] = {}
_IMAGE_DIMENSION_CACHE_LOCK = threading.Lock()


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
                width = int.from_bytes(data[start + 3:start + 5], "little") & 0x3fff
                height = int.from_bytes(data[start + 5:start + 7], "little") & 0x3fff
                return width, height
        if chunk == b"VP8L" and len(data) >= 25:
            bits = int.from_bytes(data[21:25], "little")
            width = (bits & 0x3fff) + 1
            height = ((bits >> 14) & 0x3fff) + 1
            return width, height
    if len(data) >= 4 and data.startswith(b"\xff\xd8"):
        i = 2
        while i + 9 <= len(data):
            if data[i] != 0xff:
                i += 1
                continue
            while i < len(data) and data[i] == 0xff:
                i += 1
            if i >= len(data):
                break
            marker = data[i]
            i += 1
            if marker in (0x01,) or 0xd0 <= marker <= 0xd9:
                continue
            if i + 2 > len(data):
                break
            size = int.from_bytes(data[i:i + 2], "big")
            if size < 2:
                break
            if marker in (
                0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7,
                0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf,
            ):
                if i + 7 <= len(data):
                    height = int.from_bytes(data[i + 3:i + 5], "big")
                    width = int.from_bytes(data[i + 5:i + 7], "big")
                    return width, height
                break
            i += size
    return None


def _fetch_image_dimensions(url: str) -> Optional[tuple[int, int]]:
    with _IMAGE_DIMENSION_CACHE_LOCK:
        if url in _IMAGE_DIMENSION_CACHE:
            return _IMAGE_DIMENSION_CACHE[url]

    dims = None
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Range": f"bytes=0-{IMAGE_DIMENSION_BYTE_CAP - 1}",
        }
        with requests.get(
            url,
            headers=headers,
            timeout=IMAGE_DIMENSION_TIMEOUT,
            stream=True,
            allow_redirects=True,
        ) as resp:
            resp.raise_for_status()
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if "image/svg" in content_type:
                dims = (0, 0)
            else:
                chunks = []
                total = 0
                for chunk in resp.iter_content(chunk_size=16384):
                    if not chunk:
                        continue
                    remaining = IMAGE_DIMENSION_BYTE_CAP - total
                    chunks.append(chunk[:remaining])
                    total += min(len(chunk), remaining)
                    dims = _parse_image_dimensions(b"".join(chunks))
                    if dims or total >= IMAGE_DIMENSION_BYTE_CAP:
                        break
    except Exception as e:
        logger.debug("Image dimension probe failed for %s: %s", url, e)
        dims = None

    with _IMAGE_DIMENSION_CACHE_LOCK:
        _IMAGE_DIMENSION_CACHE[url] = dims
    return dims


def _markdown_image_urls(markdown: str) -> list[str]:
    urls = []
    for match in re.finditer(r'!\[[^\]]*\]\(\s*([^)\s]+)(?:\s+["\'][^)]*["\'])?\s*\)', markdown or ""):
        url = match.group(1).strip().strip("<>").strip()
        if url:
            urls.append(url)
    return _dedupe(urls)


def _normalize_markdown_image_url(url: str, base_url: str = "") -> str:
    if base_url:
        return _normalize_image_url(url, base_url)
    return (url or "").strip().strip("<>").strip()


def _strip_filtered_markdown_images(markdown: str, filtered_urls: set[str], base_url: str = "") -> str:
    image_ref = re.compile(r'!\[[^\]]*\]\(\s*([^)\s]+)(?:\s+["\'][^)]*["\'])?\s*\)')
    lines = []
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
            without_ref = stripped_line[:match.start()] + stripped_line[match.end():]
            if without_ref.strip() == "":
                remove_entire_line = True
            stripped_line = without_ref
        if not remove_entire_line:
            lines.append(stripped_line.rstrip())
    return _clean_markdown("\n".join(lines))


def _strip_svg_and_small(markdown: str, images: list[str], min_w: int = 0, min_h: int = 0, base_url: str = "") -> str:
    """Remove SVG and known-too-small images from image arrays and markdown refs."""
    markdown_urls = _markdown_image_urls(markdown)
    normalized_markdown_urls = [
        _normalize_markdown_image_url(url, base_url)
        for url in markdown_urls
    ]
    candidates = _dedupe(images + normalized_markdown_urls)
    filtered_urls = {url for url in candidates if _is_svg_url(url)}
    filtered_urls.update(url for url in markdown_urls if _is_svg_url(url))

    probe_urls = [
        url for url in candidates
        if min_w > 0 and min_h > 0
        and url not in filtered_urls
        and urlparse(url).scheme in ("http", "https")
    ]
    if probe_urls:
        workers = min(IMAGE_DIMENSION_WORKERS, len(probe_urls))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_fetch_image_dimensions, url): url for url in probe_urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    dims = future.result()
                except Exception as e:
                    logger.debug("Image dimension worker failed for %s: %s", url, e)
                    continue
                if dims and (dims[0] < min_w or dims[1] < min_h):
                    filtered_urls.add(url)

    images[:] = [url for url in images if url not in filtered_urls]
    return _strip_filtered_markdown_images(markdown, filtered_urls, base_url=base_url)


def _best_title_from_html(html: str, fallback: str = "") -> str:
    patterns = (
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
        r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\']([^"\']+)["\']',
        r"<h1\b[^>]*>(.*?)</h1>",
        r"<title\b[^>]*>(.*?)</title>",
    )
    for pattern in patterns:
        m = re.search(pattern, html or "", flags=re.IGNORECASE | re.DOTALL)
        if m:
            title = re.sub(r"<[^>]+>", "", m.group(1))
            title = re.sub(r"\s+", " ", title).strip()
            if title:
                return title
    return (fallback or "").strip()


# ── 平台适配器 ──────────────────────────────────────────────────────

class PlatformAdapter:
    """基类"""

    def can_handle(self, url: str) -> bool:
        raise NotImplementedError

    def extract(self, url: str) -> Optional[Article]:
        raise NotImplementedError

    def _request_kwargs(self) -> dict[str, Any]:
        return {
            "headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            "timeout": DEFAULT_TIMEOUT,
        }


class HuaweiAutoAdapter(PlatformAdapter):
    """鸿蒙智行 / AITO 社区文章"""

    API = "https://omp.uopes.cn/xcar/omp/xbs/cc/queryPostShareDetail"

    def can_handle(self, url: str) -> bool:
        return "omp.uopes.cn" in url

    def extract(self, url: str) -> Optional[Article]:
        content_id = self._parse_content_id(url)
        if not content_id:
            logger.warning("Cannot parse contentId from URL: %s", url)
            return None

        try:
            resp = requests.get(
                self.API,
                params={"contentId": content_id},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error("API request failed for %s: %s", content_id, e)
            return None

        if data.get("code") != 0:
            logger.error("API error: %s", data.get("msg"))
            return None

        cd = data.get("contentDetail")
        if not cd:
            return None

        user = data.get("userInfoVo", {}) or {}

        # 标题：API title → topicNames → textContent 首行
        title = cd.get("title") or ""
        subtitle = cd.get("subtitle") or ""
        topic_names = cd.get("topicNames") or ""

        body_blocks = cd.get("articleMainBodyList") or []
        images: list[str] = []

        # ── 路径1: richText（type=4 官方文章，HTML 含内嵌图片）──
        if body_blocks and any(b.get("richText") for b in body_blocks):
            html_parts = []
            for block in body_blocks:
                rt = block.get("richText")
                if rt:
                    html_parts.append(rt)
                    for m in re.finditer(r'<img[^>]+src="([^"]+)"', rt):
                        images.append(m.group(1))
            markdown = html_to_markdown("\n".join(html_parts))

        # ── 路径2: mainBodyText + imageUrl/videoUrl（type=8 PGC / 转发帖）──
        elif body_blocks and any(
            b.get("mainBodyText") or b.get("imageUrl") or b.get("videoUrl")
            for b in body_blocks
        ):
            md_parts = []
            for block in body_blocks:
                text = block.get("mainBodyText") or ""
                img_url = block.get("imageUrl") or ""
                video_url = block.get("videoUrl") or ""
                video_cover = block.get("videoCoverUrl") or ""
                if text.strip():
                    paragraphs = text.strip().split("\n")
                    for p in paragraphs:
                        p = p.strip()
                        if p:
                            md_parts.append(p + "\n")
                if img_url:
                    md_parts.append(f"![]( {img_url} )\n")
                    if img_url not in images:
                        images.append(img_url)
                if video_url:
                    md_parts.append(
                        f"> 📹 视频: [{video_url}]({video_url})\n"
                    )
                    if video_cover:
                        md_parts.append(f"![视频封面]({video_cover})\n")
                        if video_cover not in images:
                            images.append(video_cover)
            markdown = "\n".join(md_parts)

        # ── 路径3: textContent only（type=0 用户帖）──
        else:
            tc = cd.get("textContent") or ""
            markdown = tc.strip() if tc else ""

        # ── 补齐图片 ──
        for img in cd.get("imageContent") or []:
            if img and img not in images:
                images.append(img)
        # imgContentPlus（顶部 banner 大图，单 URL 字符串）
        icp = cd.get("imgContentPlus") or ""
        if icp and icp not in images:
            images.append(icp)
        # fileContent（正文配图，JSON数组）
        fc_raw = cd.get("fileContent") or ""
        if fc_raw:
            try:
                for fc_img in json.loads(fc_raw):
                    img_url = (fc_img.get("imagePath") or "") + (
                        fc_img.get("imageName") or ""
                    )
                    if img_url and img_url not in images:
                        images.append(img_url)
            except json.JSONDecodeError:
                pass
        # fileContentPlus（顶部 banner 图，JSON数组）
        fcp_raw = cd.get("fileContentPlus") or ""
        if fcp_raw:
            try:
                for fcp_img in json.loads(fcp_raw):
                    img_url = (fcp_img.get("imagePath") or "") + (
                        fcp_img.get("imageName") or ""
                    )
                    if img_url and img_url not in images:
                        images.append(img_url)
            except json.JSONDecodeError:
                pass
        # 追加 markdown 中尚未引用的图片
        existing_imgs = set(
            re.findall(
                r'!\[.*?\]\(\s*(\S+?)\s*\)', markdown
            )
        )
        for img_url in images:
            if img_url not in existing_imgs:
                markdown += f"\n\n![]( {img_url} )"
        markdown = _strip_svg_and_small(
            markdown,
            images,
            IMAGE_DIMENSION_MIN_W,
            IMAGE_DIMENSION_MIN_H,
            base_url=url,
        )

        # ── 视频链接 ──
        video = cd.get("videoVo") or {}
        if video.get("videoUrl"):
            markdown += (
                f"\n\n> 📹 视频: {video['videoUrl']}"
            )

        # ── 标题兜底：textContent/markdown 首行 ──
        if not title:
            title = topic_names or ""
        if not title:
            # 从正文取第一句（最多50字）
            first_line = markdown.split("\n")[0].strip().lstrip("# ")
            # 去掉图片标记
            first_line = re.sub(r'!\[.*?\]\(.*?\)', '', first_line).strip()
            if len(first_line) > 50:
                first_line = first_line[:50] + "…"
            title = first_line or "(无标题)"

        return Article(
            title=title,
            subtitle=subtitle,
            author=user.get("creatorName") or "",
            source_url=url,
            markdown=markdown.strip(),
            images=images,
        )

    @staticmethod
    def _parse_content_id(url: str) -> Optional[str]:
        """从 URL 提取 contentId"""
        # 尝试 query string
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if "contentId" in qs:
            return qs["contentId"][0]
        # 尝试路径
        m = re.search(r"contentId[=/](\d+)", url)
        if m:
            return m.group(1)
        return None


# ── Requests 快速通用兜底 ───────────────────────────────────────────

class RequestsAdapter(PlatformAdapter):
    """服务端渲染页面 → requests + trafilatura 快速提取"""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT):
        self.timeout = timeout

    def can_handle(self, url: str) -> bool:
        return True

    def extract(self, url: str) -> Optional[Article]:
        try:
            import trafilatura
        except ImportError:
            logger.warning("trafilatura not installed, skipping requests fallback")
            return None

        try:
            kwargs = self._request_kwargs()
            kwargs["timeout"] = self.timeout
            resp = requests.get(url, allow_redirects=True, **kwargs)
            resp.raise_for_status()
            html = _decoded_response_text(resp)
        except Exception as e:
            logger.info("Requests fallback failed: %s", e)
            return None

        final_url = resp.url or url
        title = _best_title_from_html(html)
        visible_hint = re.sub(r"<[^>]+>", " ", html[:20000])
        if _is_captcha(title=title, text=visible_hint, url=final_url):
            logger.warning("CAPTCHA / anti-bot page detected by requests: %s", final_url)
            return None

        try:
            markdown = trafilatura.extract(
                html,
                url=final_url,
                output_format="markdown",
                include_images=True,
                include_links=True,
                favor_recall=True,
            ) or ""
        except TypeError:
            markdown = trafilatura.extract(html, url=final_url) or ""
        except Exception as e:
            logger.info("trafilatura extraction failed: %s", e)
            return None

        article = Article(
            title=title,
            source_url=url,
            markdown=_clean_markdown(markdown),
            images=_extract_images_from_html(html, final_url),
        )

        # 追加 markdown 中尚未引用的图片
        existing = set(re.findall(r'!\[.*?\]\(\s*(\S+?)\s*\)', article.markdown))
        for img_url in article.images:
            if img_url not in existing:
                article.markdown += f"\n\n![]( {img_url} )"
        article.markdown = _strip_svg_and_small(
            article.markdown,
            article.images,
            IMAGE_DIMENSION_MIN_W,
            IMAGE_DIMENSION_MIN_H,
            base_url=final_url,
        )
        if not article.title:
            article.title = _best_title_from_html(html, fallback="")
        if _is_quality_article(article, min_chars=200):
            return article
        return None


# ── Playwright 通用兜底 ──────────────────────────────────────────────

class PlaywrightAdapter(PlatformAdapter):
    """未知平台 → 无头浏览器渲染 + Mozilla Readability 提取"""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES):
        self.timeout = timeout
        self.retries = retries

    def can_handle(self, url: str) -> bool:
        return True  # 兜底

    def extract(self, url: str) -> Optional[Article]:
        try:
            from readability import Document
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("Playwright / readability-lxml not installed, cannot fallback")
            return None

        deadline = time.monotonic() + max(1, self.timeout)
        attempts = max(1, self.retries + 1)
        last_error = None

        for attempt in range(attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                article = self._extract_once(url, Document, sync_playwright, remaining)
                if _is_quality_article(article):
                    return article
                logger.info("Playwright extraction failed quality validation: %s", url)
            except Exception as e:
                last_error = e
                logger.info("Playwright attempt %s failed: %s", attempt + 1, e)

            if attempt < attempts - 1:
                sleep_for = min(0.5 * (2 ** attempt), max(0, deadline - time.monotonic()))
                if sleep_for > 0:
                    time.sleep(sleep_for)

        if last_error:
            logger.error("Playwright browser failed: %s", last_error)
        return None

    def _extract_once(self, url: str, Document: Any, sync_playwright: Any, budget: float) -> Optional[Article]:
        timeout_ms = max(1000, int(min(budget, self.timeout) * 1000))
        browser = None
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
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
            browser = None

        if _is_captcha(title=title, text=text, url=final_url):
            logger.warning("CAPTCHA / anti-bot page detected by Playwright: %s", final_url)
            return None

        doc = Document(html)
        article_html = doc.summary()
        markdown = html_to_markdown(article_html)
        title = title or doc.title()
        images = _dedupe(
            rendered_images
            + _extract_images_from_html(article_html, final_url)
            + _extract_images_from_html(html, final_url)
        )
        # 追加 markdown 中尚未引用的图片
        existing = set(re.findall(r'!\[.*?\]\(\s*(\S+?)\s*\)', markdown))
        for img_url in images:
            if img_url not in existing:
                markdown += f"\n\n![]( {img_url} )"
        markdown = _strip_svg_and_small(
            markdown,
            images,
            IMAGE_DIMENSION_MIN_W,
            IMAGE_DIMENSION_MIN_H,
            base_url=final_url,
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
        return (title or _best_title_from_html(html)).strip()

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


# ── 调度器 ──────────────────────────────────────────────────────────

class ArticleExtractor:
    """自动选择适配器"""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES):
        self.timeout = timeout
        self.retries = retries
        # 平台适配器按优先级排列（越具体越靠前）
        self.adapters: list[PlatformAdapter] = [
            HuaweiAutoAdapter(),
            RequestsAdapter(timeout=timeout),
            # 通用兜底放最后
            PlaywrightAdapter(timeout=timeout, retries=retries),
        ]

    def extract(self, url: str) -> Optional[Article]:
        for adapter in self.adapters:
            if adapter.can_handle(url):
                logger.info("Using %s for %s", type(adapter).__name__, url)
                try:
                    article = adapter.extract(url)
                except Exception as e:
                    logger.error("%s crashed: %s", type(adapter).__name__, e)
                    article = None
                if article:
                    return article
                # 平台专属适配器失败 → 不兜底（兜底也不会更好）
                if isinstance(adapter, HuaweiAutoAdapter):
                    logger.warning(
                        "%s returned None for known platform, skipping fallback",
                        type(adapter).__name__,
                    )
                    return None
        return None


# ── 便捷函数 ────────────────────────────────────────────────────────

_extractor = ArticleExtractor()


def article_to_markdown(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> Optional[str]:
    """一行调用：URL → Markdown 字符串"""
    extractor = _extractor if (timeout, retries) == (DEFAULT_TIMEOUT, DEFAULT_RETRIES) else ArticleExtractor(timeout, retries)
    article = extractor.extract(url)
    return article.markdown if article else None


def article_to_dict(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> Optional[dict]:
    """URL → 结构化字典"""
    extractor = _extractor if (timeout, retries) == (DEFAULT_TIMEOUT, DEFAULT_RETRIES) else ArticleExtractor(timeout, retries)
    article = extractor.extract(url)
    if not article:
        return None
    return {
        "title": article.title,
        "subtitle": article.subtitle,
        "author": article.author,
        "source_url": article.source_url,
        "markdown": article.markdown,
        "images": article.images,
    }


# ── CLI ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python extractor.py <url>")
        print("       python extractor.py <url> --json")
        sys.exit(1)

    url = sys.argv[1]
    as_json = "--json" in sys.argv

    result = article_to_dict(url)
    if not result:
        print("ERROR: Extraction failed")
        sys.exit(1)

    if as_json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    else:
        print(f"# {result['title']}")
        if result["subtitle"]:
            print(f"*{result['subtitle']}*")
        if result["author"]:
            print(f"作者: {result['author']}")
        print()
        print(result["markdown"])
