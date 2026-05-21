from unittest.mock import patch

from images import finalize_markdown_and_images


def test_finalize_markdown_and_images_shared_postprocess():
    base_url = "https://example.com/article/1"
    images = [
        "https://example.com/img/keep.png",
        "https://example.com/img/add.png",
        "https://example.com/img/small.png",
        "https://example.com/img/icon.svg",
    ]
    markdown = "同意并继续\n\n正文段落\n\n![](/img/keep.png)"

    dims = {
        "https://example.com/img/keep.png": (900, 450),
        "https://example.com/img/add.png": (1000, 800),
        "https://example.com/img/small.png": (600, 500),
    }

    with patch("images._fetch_image_dimensions", side_effect=lambda url: dims[url]):
        final_markdown = finalize_markdown_and_images(
            markdown=markdown,
            images=images,
            base_url=base_url,
            image_fail_open=False,
        )

    assert "同意并继续" not in final_markdown
    assert "keep.png" in final_markdown
    assert "add.png" in final_markdown
    assert "small.png" not in final_markdown
    assert "icon.svg" not in final_markdown
    assert images == [
        "https://example.com/img/keep.png",
        "https://example.com/img/add.png",
    ]
