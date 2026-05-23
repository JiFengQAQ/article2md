import json
import re
from unittest.mock import Mock, patch

from adapters.hima_community_adapter import HimaCommunityAdapter


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

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload

    def fake_dimensions(_url: str):
        return (900, 450)

    with patch("adapters.hima_community_adapter.requests.get", return_value=response) as mock_get:
        with patch("images._fetch_image_dimensions", side_effect=fake_dimensions):
            adapter = HimaCommunityAdapter(image_fail_open=False)
            article = adapter.extract(
                "https://omp.uopes.cn/static/webapp/share/article_details.html?contentId=1642222"
            )

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
    assert "https://img.example.com/fcp.jpg" in article.images
    assert len(article.images) == len(re.findall(r"!\[[^\]]*\]\([^\)]+\)", article.markdown))

    assert mock_get.call_count == 1
    assert mock_get.call_args.kwargs["params"]["contentId"] == "1642222"


def test_hima_dynamic_post_extracts_images_from_imagecontent_and_filecontent():
    """Dynamics (stype=3) have no articleMainBodyList but have imageContent/fileContent."""
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

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload

    def fake_dimensions(_url: str):
        return (900, 450)

    with patch("adapters.hima_community_adapter.requests.get", return_value=response):
        with patch("images._fetch_image_dimensions", side_effect=fake_dimensions):
            adapter = HimaCommunityAdapter(image_fail_open=False)
            article = adapter.extract(
                "https://omp.uopes.cn/static/webapp/share/dynamic_details.html?contentId=1646354"
            )

    assert article is not None
    assert "全网寻找" in article.markdown
    # All 4 images should appear in both markdown and images list
    assert "https://cdn.example.com/img1.jpg" in article.images
    assert "https://cdn.example.com/img2.png" in article.images
    assert "https://cdn.example.com/fc1.jpg" in article.images
    assert "https://cdn.example.com/fc2.jpg" in article.images
    assert len(article.images) == 4
    assert len(re.findall(r"!\[[^\]]*\]\([^\)]+\)", article.markdown)) == 4


def test_hima_article_block_with_imageurl_but_no_richtext_still_extracts_images():
    """Block with imageUrl but empty richText should not be skipped."""
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

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload

    def fake_dimensions(_url: str):
        return (900, 450)

    with patch("adapters.hima_community_adapter.requests.get", return_value=response):
        with patch("images._fetch_image_dimensions", side_effect=fake_dimensions):
            adapter = HimaCommunityAdapter(image_fail_open=False)
            article = adapter.extract(
                "https://omp.uopes.cn/static/webapp/share/article_details.html?contentId=1642743"
            )

    assert article is not None
    # poster from richText block[0]
    assert "https://cdn.example.com/poster.jpg" in article.images
    # body_img from block[1] imageUrl (was previously skipped)
    assert "https://cdn.example.com/body_img.jpg" in article.images
    # top from imageContent
    assert "https://cdn.example.com/top.jpg" in article.images
    # fc_item from fileContent
    assert "https://cdn.example.com/fc_item.jpg" in article.images
    # fbc from block[1] fileBodyContent
    assert "https://cdn.example.com/fbc.jpg" in article.images
    assert len(article.images) == 5
    assert len(re.findall(r"!\[[^\]]*\]\([^\)]+\)", article.markdown)) == 5


