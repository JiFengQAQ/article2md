"""HIMA / AITO community adapter."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import requests

from images import _dedupe, finalize_markdown_and_images
from markdown import html_to_markdown
from models import Article, IMAGE_DIMENSION_FAIL_OPEN
from adapters.base import PlatformAdapter

logger = logging.getLogger(__name__)


class HimaCommunityAdapter(PlatformAdapter):
    """Extract articles from HIMA/AITO community share pages."""

    API = "https://omp.uopes.cn/xcar/omp/xbs/cc/queryPostShareDetail"

    def __init__(self, image_fail_open: bool = IMAGE_DIMENSION_FAIL_OPEN):
        self.image_fail_open = image_fail_open

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

        markdown = finalize_markdown_and_images(
            markdown=markdown,
            images=images,
            base_url=url,
            image_fail_open=self.image_fail_open,
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
            block.get("mainBodyText") or block.get("imageUrl") or block.get("videoUrl") for block in body_blocks
        ):
            return self._parse_text_image_blocks(body_blocks)

        text_content = content_detail.get("textContent") or ""
        return text_content.strip(), []

    def _parse_richtext_blocks(self, body_blocks: list[dict[str, Any]]) -> tuple[str, list[str]]:
        html_parts: list[str] = []
        images: list[str] = []

        for block in body_blocks:
            rich_text = block.get("richText")
            if not rich_text:
                continue
            html_parts.append(rich_text)
            images.extend(self._extract_richtext_images(rich_text))

        markdown = html_to_markdown("\n".join(html_parts)) if html_parts else ""
        return markdown, _dedupe(images)

    def _parse_text_image_blocks(self, body_blocks: list[dict[str, Any]]) -> tuple[str, list[str]]:
        markdown_parts: list[str] = []
        images: list[str] = []

        for block in body_blocks:
            markdown_parts.extend(self._render_block_text(block.get("mainBodyText") or ""))

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
            parts.append(f"> 📹 视频: [{video_url}]({video_url})\n")
        if video_cover:
            parts.append(f"![视频封面]({video_cover})\n")
        return parts

    def _parse_media_lists(self, content_detail: dict[str, Any]) -> list[str]:
        images: list[str] = []

        for image_url in content_detail.get("imageContent") or []:
            if image_url:
                images.append(image_url)

        img_content_plus = (content_detail.get("imgContentPlus") or "").strip()
        if img_content_plus:
            images.append(img_content_plus)

        images.extend(self._parse_file_content_urls(content_detail.get("fileContent") or ""))
        images.extend(self._parse_file_content_urls(content_detail.get("fileContentPlus") or ""))

        return _dedupe(images)

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
            return f"{markdown}\n\n> 📹 视频: {video_url}".strip()
        return f"> 📹 视频: {video_url}"

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
