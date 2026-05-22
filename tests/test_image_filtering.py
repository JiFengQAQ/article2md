import unittest
import re
from unittest.mock import patch

import images as image_utils


class ImageFilteringTests(unittest.TestCase):
    def test_normalize_html_images_fills_real_src_and_picks_largest_srcset(self):
        html = """
        <article>
          <img id="a" src="" data-src="/images/a-large.jpg">
          <img id="b" src="javascript:void(0)" data-original="https://cdn.example.com/b.jpg">
          <img id="c" src="blob:https://example.com/abc" data-lazy-src="/images/c.jpg">
          <img id="d" data-actualsrc="/images/d.jpg">
          <img id="e" srcset="/images/e-320.jpg 320w, /images/e-1280.jpg 1280w">
          <img id="f" data-srcset="/images/f-640.jpg 640w, /images/f-1600.jpg 1600w">
          <img id="g" poster="/images/g-poster.jpg">
          <img id="h" src="data:image/png;base64,AAAA" data-src="https://cdn.example.com/h.jpg">
          <img id="i" src="/images/i-small.jpg" srcset="/images/i-small.jpg 320w, /images/i-large.jpg 1024w">
        </article>
        """

        normalized = image_utils.normalize_html_images(html, "https://example.com/news/1")

        self.assertIn('id="a" src="https://example.com/images/a-large.jpg"', normalized)
        self.assertIn('id="b" src="https://cdn.example.com/b.jpg"', normalized)
        self.assertIn('id="c" src="https://example.com/images/c.jpg"', normalized)
        self.assertRegex(normalized, r'id="d"[^>]*src="https://example\.com/images/d\.jpg"')
        self.assertRegex(normalized, r'id="e"[^>]*src="https://example\.com/images/e-1280\.jpg"')
        self.assertRegex(normalized, r'id="f"[^>]*src="https://example\.com/images/f-1600\.jpg"')
        self.assertRegex(normalized, r'id="g"[^>]*src="https://example\.com/images/g-poster\.jpg"')
        self.assertIn('id="h" src="https://cdn.example.com/h.jpg"', normalized)
        self.assertRegex(normalized, r'id="i"[^>]*src="https://example\.com/images/i-large\.jpg"')

    def test_normalize_html_images_uses_lazy_original_when_src_is_placeholder(self):
        html = '''
        <article>
          <img src="//img.example.com/images/v2/t.png" data-original="https://cdn.example.com/real.jpg">
        </article>
        '''

        normalized = image_utils.normalize_html_images(html, "https://example.com/news/1")

        self.assertIn('src="https://cdn.example.com/real.jpg"', normalized)
        self.assertNotIn('/images/v2/t.png', normalized)

    def test_normalize_html_images_drops_obviously_invalid_image_sources(self):
        html = """
        <article>
          <img src="data:image/png;base64,AAAA">
          <img src="blob:https://example.com/abc">
          <img src="javascript:void(0)">
          <img src="">
        </article>
        """
        normalized = image_utils.normalize_html_images(html, "https://example.com/a")
        self.assertNotIn("data:image/png", normalized)
        self.assertNotIn("blob:https://", normalized)
        self.assertNotIn("javascript:void(0)", normalized)

    def test_extract_images_from_html_handles_common_img_attributes(self):
        html = '''
        <article>
          <img src="/images/hero.jpg">
          <img data-src="https://cdn.example.com/lazy.webp">
          <img srcset="/images/large.jpg 1200w, /images/small.jpg 600w">
          <img src="data:image/png;base64,AAAA">
        </article>
        '''

        self.assertEqual(
            image_utils._extract_images_from_html(html, "https://example.com/news/1"),
            [
                "https://example.com/images/hero.jpg",
                "https://cdn.example.com/lazy.webp",
                "https://example.com/images/large.jpg",
            ],
        )

    def test_svg_url_variants_are_detected(self):
        self.assertTrue(image_utils._is_svg_url("https://example.com/icon.svg"))
        self.assertTrue(image_utils._is_svg_url("https://example.com/icon.svg?x=1"))
        self.assertTrue(image_utils._is_svg_url("https://example.com/img?format=image/svg+xml"))

    def test_relative_markdown_ref_is_removed_when_absolute_image_is_filtered(self):
        images = ["https://example.com/img/thumb.png"]
        with patch.object(image_utils, "_fetch_image_dimensions", return_value=(699, 500)):
            markdown = image_utils._strip_svg_and_non_content(
                "before\n\n![](/img/thumb.png)\n\nafter",
                images,
                700,
                3,
                base_url="https://example.com/article/1",
            )
        self.assertNotIn("thumb.png", markdown)
        self.assertEqual(images, [])
        self.assertIn("before", markdown)
        self.assertIn("after", markdown)

    def test_content_image_shape_filter_and_default_fail_closed(self):
        images = [
            "https://example.com/keep-landscape.png",
            "https://example.com/keep-portrait.png",
            "https://example.com/drop-small.png",
            "https://example.com/drop-square.png",
            "https://example.com/drop-panorama.png",
            "https://example.com/unknown.png",
        ]
        dims = {
            "https://example.com/keep-landscape.png": (900, 450),  # ratio=2, long side OK
            "https://example.com/keep-portrait.png": (500, 700),   # ratio<1, long side OK
            "https://example.com/drop-small.png": (699, 500),      # long side too short
            "https://example.com/drop-square.png": (700, 700),     # ratio=1 is accepted
            "https://example.com/drop-panorama.png": (1600, 400),  # ratio>3 is excluded
            "https://example.com/unknown.png": None,               # default fail-closed
        }
        with patch.object(image_utils, "_fetch_image_dimensions", side_effect=lambda url: dims[url]):
            markdown = image_utils._strip_svg_and_non_content(
                "\n\n".join(f"![]({url})" for url in images),
                images,
                700,
                3,
            )
        self.assertIn("keep-landscape.png", markdown)
        self.assertIn("keep-portrait.png", markdown)
        self.assertIn("drop-square.png", markdown)
        self.assertNotIn("drop-small.png", markdown)
        self.assertNotIn("drop-panorama.png", markdown)
        self.assertNotIn("unknown.png", markdown)
        self.assertEqual(images, [
            "https://example.com/keep-landscape.png",
            "https://example.com/keep-portrait.png",
            "https://example.com/drop-square.png",
        ])

    def test_fail_open_keeps_unknown_dimension_images(self):
        images = ["https://example.com/unknown.png"]
        with patch.object(image_utils, "_fetch_image_dimensions", return_value=None):
            markdown = image_utils._strip_svg_and_non_content(
                "![](https://example.com/unknown.png)",
                images,
                700,
                3,
                fail_open=True,
            )
        self.assertIn("unknown.png", markdown)
        self.assertEqual(images, ["https://example.com/unknown.png"])

    def test_finalize_postprocess_normalizes_markdown_image_urls_to_absolute(self):
        images = ["https://example.com/assets/keep.jpg"]
        with patch.object(image_utils, "_fetch_image_dimensions", return_value=(1200, 800)):
            markdown = image_utils.finalize_markdown_and_images(
                markdown="段落\n\n![](/assets/keep.jpg)",
                images=images,
                base_url="https://example.com/news/1",
                image_fail_open=False,
            )
        self.assertIn("![](https://example.com/assets/keep.jpg)", markdown)
        self.assertNotIn("![](/assets/keep.jpg)", markdown)

    def test_finalize_removes_invalid_data_blob_javascript_image_refs(self):
        images = ["https://example.com/ok.jpg"]
        with patch.object(image_utils, "_fetch_image_dimensions", return_value=(1200, 800)):
            markdown = image_utils.finalize_markdown_and_images(
                markdown=(
                    "![](data:image/png;base64,AAAA)\n\n"
                    "![](blob:https://example.com/id)\n\n"
                    "![](javascript:void(0))\n\n"
                    "![](https://example.com/ok.jpg)"
                ),
                images=images,
                base_url="https://example.com/a",
                image_fail_open=False,
            )
        self.assertNotIn("data:image", markdown)
        self.assertNotIn("blob:https://", markdown)
        self.assertNotIn("javascript:void(0)", markdown)
        self.assertIn("https://example.com/ok.jpg", markdown)
        self.assertEqual(images, ["https://example.com/ok.jpg"])

    def test_finalize_syncs_images_with_unique_markdown_urls(self):
        images = [
            "https://example.com/img/dup.jpg",
            "https://example.com/img/orphan.jpg",
            "https://example.com/img/dup.jpg",
        ]
        with patch.object(
            image_utils,
            "_fetch_image_dimensions",
            side_effect=lambda url: (1200, 800) if "dup" in url else (1300, 900),
        ):
            markdown = image_utils.finalize_markdown_and_images(
                markdown=(
                    "前文\n\n"
                    "![](https://example.com/img/dup.jpg)\n\n"
                    "中段\n\n"
                    "![](/img/dup.jpg)\n\n"
                    "后文"
                ),
                images=images,
                base_url="https://example.com/article/1",
                image_fail_open=False,
            )
        exported_unique = set(image_utils._markdown_image_urls(markdown))
        self.assertEqual(images, ["https://example.com/img/dup.jpg"])
        self.assertEqual(exported_unique, {"https://example.com/img/dup.jpg"})


if __name__ == "__main__":
    unittest.main()
