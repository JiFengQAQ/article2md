"""
文章 → Markdown 提取器
通用架构：已知平台 API 直调（快）→ 未知平台 Playwright 兜底（慢）
零 Hermes 依赖，纯 Python + pip
"""
import re
import json
import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse, parse_qs

import requests
from html2text import HTML2Text

logger = logging.getLogger(__name__)


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


# ── 平台适配器 ──────────────────────────────────────────────────────

class PlatformAdapter:
    """基类"""

    def can_handle(self, url: str) -> bool:
        raise NotImplementedError

    def extract(self, url: str) -> Optional[Article]:
        raise NotImplementedError


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
            markdown = "\n".join(md_parts)

        # ── 路径3: textContent only（type=0 用户帖）──
        else:
            tc = cd.get("textContent") or ""
            markdown = tc.strip() if tc else ""

        # ── 补齐图片 ──
        for img in cd.get("imageContent") or []:
            if img and img not in images:
                images.append(img)
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
        # 追加 markdown 中尚未引用的图片
        existing_imgs = set(
            re.findall(
                r'!\[.*?\]\(\s*(\S+?)\s*\)', markdown
            )
        )
        for img_url in images:
            if img_url not in existing_imgs:
                markdown += f"\n\n![]( {img_url} )"

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


# ── Playwright 通用兜底 ──────────────────────────────────────────────

class PlaywrightAdapter(PlatformAdapter):
    """未知平台 → 无头浏览器渲染 + Mozilla Readability 提取"""

    def can_handle(self, url: str) -> bool:
        return True  # 兜底

    def extract(self, url: str) -> Optional[Article]:
        try:
            from readability import Document
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("Playwright / readability-lxml not installed, cannot fallback")
            return None

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="networkidle", timeout=30000)
                html = page.content()
                title = page.title()
                browser.close()
        except Exception as e:
            logger.error("Playwright browser failed: %s", e)
            return None

        doc = Document(html)
        article_html = doc.summary()
        markdown = html_to_markdown(article_html)

        # 提取图片
        images = re.findall(r'<img[^>]+src="([^"]+)"', article_html)

        return Article(
            title=title or doc.title(),
            source_url=url,
            markdown=markdown,
            images=images,
        )


# ── 调度器 ──────────────────────────────────────────────────────────

class ArticleExtractor:
    """自动选择适配器"""

    def __init__(self):
        # 平台适配器按优先级排列（越具体越靠前）
        self.adapters: list[PlatformAdapter] = [
            HuaweiAutoAdapter(),
            # 通用兜底放最后
            PlaywrightAdapter(),
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
                if not isinstance(adapter, PlaywrightAdapter):
                    logger.warning(
                        "%s returned None for known platform, skipping fallback",
                        type(adapter).__name__,
                    )
                    return None
        return None


# ── 便捷函数 ────────────────────────────────────────────────────────

_extractor = ArticleExtractor()


def article_to_markdown(url: str) -> Optional[str]:
    """一行调用：URL → Markdown 字符串"""
    article = _extractor.extract(url)
    return article.markdown if article else None


def article_to_dict(url: str) -> Optional[dict]:
    """URL → 结构化字典"""
    article = _extractor.extract(url)
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
