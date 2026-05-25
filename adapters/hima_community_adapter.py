"""HIMA/AITO社区适配器"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import requests

from images import _dedupe, finalize_markdown_and_images
from markdown import html_to_markdown
from models import Article
from adapters.base import PlatformAdapter

logger = logging.getLogger(__name__)


class HimaCommunityAdapter(PlatformAdapter):
    """从HIMA/AITO社区分享页抽取文章"""

    API = "https://omp.uopes.cn/xcar/omp/xbs/cc/queryPostShareDetail"

    def __init__(self):
        pass

    def can_handle(self, url: str) -> bool:
        return "omp.uopes.cn" in url

    def extract(self, url: str) -> Optional[Article]:
        content_id = self._parse_content_id(url)
        if not content_id:
            logger.warning("Cannot parse contentId from URL: %s", url)
            return None

        data = self._fetch_share_detail(content_id)
        if not data:
            return None

        content_detail = data.get("contentDetail")
        if not content_detail:
            return None

        user = data.get("userInfoVo", {}) or {}
        title = content_detail.get("title") or ""
        subtitle = content_detail.get("subtitle") or ""
        topic_names = content_detail.get("topicNames") or ""

        markdown, images = self._parse_body_blocks(content_detail)
        images.extend(self._parse_media_lists(content_detail))
        images = _dedupe(images)

        markdown_image_refs = self._markdown_image_refs(markdown)
        for img_url in images:
            normalized = self._normalize_markdown_image_ref(img_url)
            if normalized in markdown_image_refs:
                continue
            markdown += f"\n![]( {img_url} )\n"
            markdown_image_refs.add(normalized)

        markdown = finalize_markdown_and_images(
            markdown=markdown,
            images=images,
            base_url=url,
        )
        markdown = self._append_video_summary(markdown, content_detail)

        title = self._fallback_title(title, topic_names, markdown, content_detail)

        return Article(
            title=title,
            subtitle=subtitle,
            author=user.get("creatorName") or "",
            source_url=url,
            markdown=markdown.strip(),
            images=images,
        )

    def _fetch_share_detail(self, content_id: str) -> Optional[dict[str, Any]]:
        try:
            response = requests.get(
                self.API,
                params={"contentId": content_id},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            logger.error("API request failed for %s: %s", content_id, exc)
            return None

        if payload.get("code") != 0:
            logger.error("API error: %s", payload.get("msg"))
            return None

        return payload

    @staticmethod
    def _parse_content_id(url: str) -> Optional[str]:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if "contentId" in query and query["contentId"]:
            return query["contentId"][0]

        match = re.search(r"contentId[=/](\d+)", url)
        if match:
            return match.group(1)
        return None

    def _parse_body_blocks(self, content_detail: dict[str, Any]) -> tuple[str, list[str]]:
        body_blocks = content_detail.get("articleMainBodyList") or []

        if body_blocks and any(block.get("richText") for block in body_blocks):
            return self._parse_richtext_blocks(body_blocks)

        if body_blocks and any(
            block.get("mainBodyText")
            or block.get("imageUrl")
            or block.get("fileBodyContent")
            or block.get("videoUrl")
            for block in body_blocks
        ):
            return self._parse_text_image_blocks(body_blocks)

        text_content = content_detail.get("textContent") or ""
        return text_content.strip(), []

    def _parse_richtext_blocks(self, body_blocks: list[dict[str, Any]]) -> tuple[str, list[str]]:
        html_parts: list[str] = []
        images: list[str] = []

        for block in body_blocks:
            rich_text = block.get("richText")
            richtext_images: list[str] = []
            if rich_text:
                html_parts.append(rich_text)
                richtext_images = self._extract_richtext_images(rich_text)
                images.extend(richtext_images)

            file_body_urls = self._parse_file_body_content_urls(block)
            if file_body_urls:
                images.extend(file_body_urls)
            else:
                image_url = (block.get("imageUrl") or "").strip()
                richtext_url_set = {url.strip() for url in richtext_images if url}
                if image_url and image_url not in richtext_url_set:
                    html_parts.append(
                        f'<img src="{image_url}" alt="正文配图">'
                    )
                    images.append(image_url)

        markdown = html_to_markdown("\n".join(html_parts)) if html_parts else ""
        return markdown, _dedupe(images)

    def _parse_text_image_blocks(self, body_blocks: list[dict[str, Any]]) -> tuple[str, list[str]]:
        markdown_parts: list[str] = []
        images: list[str] = []

        for block in body_blocks:
            markdown_parts.extend(self._render_block_text(block.get("mainBodyText") or ""))

            file_body_urls = self._parse_file_body_content_urls(block)
            if file_body_urls:
                images.extend(file_body_urls)
            else:
                image_url = (block.get("imageUrl") or "").strip()
                if image_url:
                    markdown_parts.append(f"![]( {image_url} )\n")
                    images.append(image_url)

            markdown_parts.extend(self._render_block_video(block))
            cover = (block.get("videoCoverUrl") or "").strip()
            if cover:
                images.append(cover)

        return "\n".join(markdown_parts).strip(), _dedupe(images)

    @staticmethod
    def _extract_richtext_images(rich_text: str) -> list[str]:
        urls: list[str] = []
        for match in re.finditer(r'<img[^>]+src="([^"]+)"', rich_text):
            urls.append(match.group(1))
        return urls

    @staticmethod
    def _render_block_text(text: str) -> list[str]:
        parts: list[str] = []
        for paragraph in text.strip().split("\n"):
            paragraph = paragraph.strip()
            if paragraph:
                parts.append(paragraph + "\n")
        return parts

    @staticmethod
    def _render_block_video(block: dict[str, Any]) -> list[str]:
        video_url = (block.get("videoUrl") or "").strip()
        video_cover = (block.get("videoCoverUrl") or "").strip()
        parts: list[str] = []
        if video_url:
            parts.append(f"> 视频: [{video_url}]({video_url})\n")
        if video_cover:
            parts.append(f"![视频封面]({video_cover})\n")
        return parts

    def _parse_media_lists(self, content_detail: dict[str, Any]) -> list[str]:
        file_content_urls = self._parse_file_content_urls(content_detail.get("fileContent") or "")
        image_content_urls = [
            image_url.strip()
            for image_url in (content_detail.get("imageContent") or [])
            if isinstance(image_url, str) and image_url.strip()
        ]
        primary_urls = file_content_urls if file_content_urls else image_content_urls

        file_content_plus_urls = self._parse_file_content_urls(content_detail.get("fileContentPlus") or "")
        img_content_plus = (content_detail.get("imgContentPlus") or "").strip()
        img_content_plus_urls = [img_content_plus] if img_content_plus else []
        plus_urls = file_content_plus_urls if file_content_plus_urls else img_content_plus_urls

        if self._should_suppress_plus_media(primary_urls, plus_urls, file_content_urls, file_content_plus_urls):
            plus_urls = []

        return _dedupe(primary_urls + plus_urls)

    @staticmethod
    def _parse_file_body_content_urls(block: dict[str, Any]) -> list[str]:
        file_body_content = block.get("fileBodyContent")
        if not file_body_content:
            return []

        if isinstance(file_body_content, str):
            return HimaCommunityAdapter._parse_file_content_urls(file_body_content)

        if isinstance(file_body_content, list):
            urls: list[str] = []
            for item in file_body_content:
                if not isinstance(item, dict):
                    continue
                image_url = (item.get("imagePath") or "") + (item.get("imageName") or "")
                if image_url:
                    urls.append(image_url)
            return _dedupe(urls)

        return []

    @staticmethod
    def _should_suppress_plus_media(
        primary_urls: list[str],
        plus_urls: list[str],
        file_content_urls: list[str],
        file_content_plus_urls: list[str],
    ) -> bool:
        if not primary_urls or not plus_urls:
            return False

        primary_url_set = set(primary_urls)
        if any(url in primary_url_set for url in plus_urls):
            return True

        if file_content_urls and file_content_plus_urls and len(file_content_urls) == 1 and len(file_content_plus_urls) == 1:
            return True

        return False

    @staticmethod
    def _normalize_markdown_image_ref(url: str) -> str:
        return (url or "").strip().strip("<>").strip()

    @classmethod
    def _markdown_image_refs(cls, markdown: str) -> set[str]:
        refs: set[str] = set()
        for match in re.finditer(r'!\[[^\]]*\]\(\s*([^)\s]+)(?:\s+["\'][^)]*["\'])?\s*\)', markdown or ""):
            normalized = cls._normalize_markdown_image_ref(match.group(1))
            if normalized:
                refs.add(normalized)
        return refs

    @staticmethod
    def _parse_file_content_urls(raw_content: str) -> list[str]:
        if not raw_content:
            return []
        try:
            items = json.loads(raw_content)
        except json.JSONDecodeError:
            return []
        if not isinstance(items, list):
            return []

        urls: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            image_url = (item.get("imagePath") or "") + (item.get("imageName") or "")
            if image_url:
                urls.append(image_url)
        return urls

    @staticmethod
    def _append_video_summary(markdown: str, content_detail: dict[str, Any]) -> str:
        video = content_detail.get("videoVo") or {}
        video_url = (video.get("videoUrl") or "").strip()
        if not video_url:
            return markdown
        if markdown:
            return f"{markdown}\n\n> 视频: {video_url}".strip()
        return f"> 视频: {video_url}"

    @staticmethod
    def _fallback_title(
        title: str,
        topic_names: str,
        markdown: str,
        content_detail: dict[str, Any],
    ) -> str:
        if title:
            return title
        if topic_names:
            return topic_names

        text_content = (content_detail.get("textContent") or "").strip()
        if text_content:
            title = text_content.split("\n", 1)[0].strip()

        if not title:
            first_line = markdown.split("\n", 1)[0].strip().lstrip("# ")
            first_line = re.sub(r"!\[.*?\]\(.*?\)", "", first_line).strip()
            title = first_line

        if len(title) > 50:
            title = title[:50] + "..."

        return title or "(无标题)"
