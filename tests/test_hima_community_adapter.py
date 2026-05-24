import json
import re
from unittest.mock import Mock, patch

from adapters.hima_community_adapter import HimaCommunityAdapter


def _fake_dimensions(_url: str):
    return (900, 450)


def _extract_with_payload(payload: dict, content_id: str = "1642222"):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload

    with patch("adapters.hima_community_adapter.requests.get", return_value=response) as mock_get:
        with patch("images._fetch_image_dimensions", side_effect=_fake_dimensions):
            adapter = HimaCommunityAdapter(image_fail_open=False)
            article = adapter.extract(
                f"https://omp.uopes.cn/static/webapp/share/article_details.html?contentId={content_id}"
            )
    return article, mock_get


def test_hima_community_adapter_parses_sample_payload():
    payload = {
        "code": 0,
        "contentDetail": {
            "title": "",
            "subtitle": "副标题",
            "topicNames": "社区话题",
            "textContent": "兜底文本",
            "articleMainBodyList": [
                {
                    "mainBodyText": "第一段\n第二段",
                    "imageUrl": "https://img.example.com/body.jpg",
                    "videoUrl": "https://video.example.com/in-block.mp4",
                    "videoCoverUrl": "https://img.example.com/cover.jpg",
                }
            ],
            "imageContent": ["https://img.example.com/content.jpg"],
            "imgContentPlus": "https://img.example.com/banner.jpg",
            "fileContent": json.dumps([
                {"imagePath": "https://img.example.com/", "imageName": "fc.jpg"}
            ]),
            "fileContentPlus": json.dumps([
                {"imagePath": "https://img.example.com/", "imageName": "fcp.jpg"}
            ]),
            "videoVo": {"videoUrl": "https://video.example.com/final.mp4"},
        },
        "userInfoVo": {"creatorName": "作者A"},
    }

    article, mock_get = _extract_with_payload(payload, content_id="1642222")

    assert article is not None
    assert article.title == "社区话题"
    assert article.subtitle == "副标题"
    assert article.author == "作者A"
    assert "第一段" in article.markdown
    assert "第二段" in article.markdown
    assert "https://video.example.com/in-block.mp4" in article.markdown
    assert "https://video.example.com/final.mp4" in article.markdown
    assert "https://img.example.com/body.jpg" in article.images
    assert "https://img.example.com/cover.jpg" in article.images
    assert "https://img.example.com/fc.jpg" in article.images
    assert "https://img.example.com/content.jpg" not in article.images
    assert "https://img.example.com/banner.jpg" not in article.images
    assert "https://img.example.com/fcp.jpg" not in article.images
    assert len(article.images) == 3
    assert len(article.images) == len(re.findall(r"!\[[^\]]*\]\([^\)]+\)", article.markdown))

    assert mock_get.call_count == 1
    assert mock_get.call_args.kwargs["params"]["contentId"] == "1642222"


def test_hima_dynamic_post_extracts_images_from_imagecontent_and_filecontent():
    """动态内容优先使用 fileContent 而不是 imageContent, 避免重复版本图片."""
    payload = {
        "code": 0,
        "contentDetail": {
            "title": None,
            "topicNames": "#问界M7见证幸福每一刻#",
            "textContent": "全网寻找有人比我提车还快的吗。评论区告诉我。",
            "articleMainBodyList": [],
            "imageContent": [
                "https://cdn.example.com/img1.jpg",
                "https://cdn.example.com/img2.png",
            ],
            "fileContent": json.dumps([
                {"imagePath": "https://cdn.example.com/", "imageName": "fc1.jpg"},
                {"imagePath": "https://cdn.example.com/", "imageName": "fc2.jpg"},
            ]),
        },
        "userInfoVo": {"creatorName": "Andy欣泽"},
    }

    article, _ = _extract_with_payload(payload, content_id="1646354")

    assert article is not None
    assert "全网寻找" in article.markdown
    assert "https://cdn.example.com/img1.jpg" not in article.images
    assert "https://cdn.example.com/img2.png" not in article.images
    assert "https://cdn.example.com/fc1.jpg" in article.images
    assert "https://cdn.example.com/fc2.jpg" in article.images
    assert len(article.images) == 2
    assert len(re.findall(r"!\[[^\]]*\]\([^\)]+\)", article.markdown)) == 2


def test_hima_richtext_imageurl_same_url_is_not_duplicated():
    payload = {
        "code": 0,
        "contentDetail": {
            "title": "T",
            "subtitle": "",
            "textContent": "",
            "articleMainBodyList": [
                {
                    "richText": '<p><img src="https://cdn.example.com/same.jpg"></p>',
                    "imageUrl": "https://cdn.example.com/same.jpg",
                    "fileBodyContent": "",
                }
            ],
            "imageContent": [],
            "fileContent": "",
        },
        "userInfoVo": {"creatorName": "A"},
    }

    article, _ = _extract_with_payload(payload, content_id="1650001")

    assert article is not None
    assert article.images == ["https://cdn.example.com/same.jpg"]
    assert len(re.findall(r"!\[[^\]]*\]\([^\)]+\)", article.markdown)) == 1


def test_hima_article_block_with_imageurl_but_no_richtext_still_extracts_images():
    """正文块优先使用 fileBodyContent 而不是 imageUrl."""
    payload = {
        "code": 0,
        "contentDetail": {
            "title": "幸福旗舰问界M7累计交付突破45万台",
            "subtitle": "",
            "textContent": "",
            "articleMainBodyList": [
                {
                    "richText": '<p><a href="https://link.example.com"><img src="https://cdn.example.com/poster.jpg"></a></p>',
                    "imageUrl": "",
                    "mainBodyText": "",
                },
                {
                    "richText": "",
                    "imageUrl": "https://cdn.example.com/body_img.jpg",
                    "mainBodyText": "",
                    "fileBodyContent": json.dumps([
                        {"imagePath": "https://cdn.example.com/", "imageName": "fbc.jpg"}
                    ]),
                },
            ],
            "imageContent": ["https://cdn.example.com/top.jpg"],
            "fileContent": json.dumps([
                {"imagePath": "https://cdn.example.com/", "imageName": "fc_item.jpg"}
            ]),
        },
        "userInfoVo": {"creatorName": "官方资讯"},
    }

    article, _ = _extract_with_payload(payload, content_id="1642743")

    assert article is not None
    assert "https://cdn.example.com/poster.jpg" in article.images
    assert "https://cdn.example.com/body_img.jpg" not in article.images
    assert "https://cdn.example.com/top.jpg" not in article.images
    assert "https://cdn.example.com/fc_item.jpg" in article.images
    assert "https://cdn.example.com/fbc.jpg" in article.images
    assert len(article.images) == 3
    assert len(re.findall(r"!\[[^\]]*\]\([^\)]+\)", article.markdown)) == 3


def test_hima_media_list_duplicate_cover_prefers_single_filecontent_item():
    payload = {
        "code": 0,
        "contentDetail": {
            "title": "T",
            "subtitle": "",
            "textContent": "纯文本",
            "articleMainBodyList": [],
            "imageContent": ["https://cdn.example.com/preview.jpg"],
            "imgContentPlus": "https://cdn.example.com/plus-preview.jpg",
            "fileContent": json.dumps([
                {"imagePath": "https://cdn.example.com/", "imageName": "primary.jpg"}
            ]),
            "fileContentPlus": json.dumps([
                {"imagePath": "https://cdn.example.com/", "imageName": "plus.jpg"}
            ]),
        },
        "userInfoVo": {"creatorName": "A"},
    }

    article, _ = _extract_with_payload(payload, content_id="1576724")

    assert article is not None
    assert article.images == ["https://cdn.example.com/primary.jpg"]
    assert len(re.findall(r"!\[[^\]]*\]\([^\)]+\)", article.markdown)) == 1


def test_hima_missing_image_injection_checks_markdown_image_refs_not_substrings():
    payload = {
        "code": 0,
        "contentDetail": {
            "title": "T",
            "subtitle": "",
            "textContent": "",
            "articleMainBodyList": [
                {
                    "richText": '<p><a href="https://cdn.example.com/fc.jpg">点击查看</a></p>',
                    "imageUrl": "",
                    "fileBodyContent": "",
                }
            ],
            "imageContent": [],
            "fileContent": json.dumps([
                {"imagePath": "https://cdn.example.com/", "imageName": "fc.jpg"}
            ]),
        },
        "userInfoVo": {"creatorName": "A"},
    }

    article, _ = _extract_with_payload(payload, content_id="1650002")

    assert article is not None
    assert re.search(r"!\[[^\]]*\]\(\s*https://cdn\.example\.com/fc\.jpg\s*\)", article.markdown)
    assert article.images == ["https://cdn.example.com/fc.jpg"]
