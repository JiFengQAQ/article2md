import re
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

    assert "同意并继续" in final_markdown
    assert "keep.png" in final_markdown
    assert "add.png" not in final_markdown
    assert "small.png" not in final_markdown
    assert "icon.svg" not in final_markdown
    assert images == [
        "https://example.com/img/keep.png",
    ]


def test_finalize_markdown_and_images_counts_only_exported_markdown_images():
    base_url = "https://example.com/article/1"
    images = [
        "https://example.com/img/first.jpg",
        "https://example.com/img/orphan.jpg",
        "https://example.com/img/second.jpg",
    ]
    markdown = "前文\n\n![](/img/first.jpg)\n\n中段\n\n![](https://example.com/img/second.jpg)\n\n后文"

    dims = {
        "https://example.com/img/first.jpg": (900, 450),
        "https://example.com/img/orphan.jpg": (1000, 800),
        "https://example.com/img/second.jpg": (800, 900),
    }

    with patch("images._fetch_image_dimensions", side_effect=lambda url: dims[url]):
        final_markdown = finalize_markdown_and_images(
            markdown=markdown,
            images=images,
            base_url=base_url,
            image_fail_open=False,
        )

    markdown_image_urls = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", final_markdown)
    markdown_image_unique = {url.strip() for url in markdown_image_urls}
    assert "orphan.jpg" not in final_markdown
    assert images == [
        "https://example.com/img/first.jpg",
        "https://example.com/img/second.jpg",
    ]
    assert len(images) == len(markdown_image_unique) == 2
