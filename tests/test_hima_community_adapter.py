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
    assert "https://img.example.com/fc.jpg" not in article.images
    assert "https://img.example.com/fcp.jpg" not in article.images
    assert len(article.images) == len(re.findall(r"!\[[^\]]*\]\([^\)]+\)", article.markdown))

    assert mock_get.call_count == 1
    assert mock_get.call_args.kwargs["params"]["contentId"] == "1642222"
