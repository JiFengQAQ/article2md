import unittest
from unittest.mock import patch

import extractor


class ImageFilteringTests(unittest.TestCase):
    def test_svg_url_variants_are_detected(self):
        self.assertTrue(extractor._is_svg_url("https://example.com/icon.svg"))
        self.assertTrue(extractor._is_svg_url("https://example.com/icon.svg?x=1"))
        self.assertTrue(extractor._is_svg_url("https://example.com/img?format=image/svg+xml"))

    def test_relative_markdown_ref_is_removed_when_absolute_image_is_filtered(self):
        images = ["https://example.com/img/thumb.png"]
        with patch.object(extractor, "_fetch_image_dimensions", return_value=(699, 500)):
            markdown = extractor._strip_svg_and_non_content(
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
            "https://example.com/drop-square.png": (700, 700),     # ratio=1 is excluded
            "https://example.com/drop-panorama.png": (1600, 400),  # ratio>3 is excluded
            "https://example.com/unknown.png": None,               # default fail-closed
        }
        with patch.object(extractor, "_fetch_image_dimensions", side_effect=lambda url: dims[url]):
            markdown = extractor._strip_svg_and_non_content(
                "\n\n".join(f"![]({url})" for url in images),
                images,
                700,
                3,
            )
        self.assertIn("keep-landscape.png", markdown)
        self.assertIn("keep-portrait.png", markdown)
        self.assertNotIn("drop-small.png", markdown)
        self.assertNotIn("drop-square.png", markdown)
        self.assertNotIn("drop-panorama.png", markdown)
        self.assertNotIn("unknown.png", markdown)
        self.assertEqual(images, [
            "https://example.com/keep-landscape.png",
            "https://example.com/keep-portrait.png",
        ])

    def test_fail_open_keeps_unknown_dimension_images(self):
        images = ["https://example.com/unknown.png"]
        with patch.object(extractor, "_fetch_image_dimensions", return_value=None):
            markdown = extractor._strip_svg_and_non_content(
                "![](https://example.com/unknown.png)",
                images,
                700,
                3,
                fail_open=True,
            )
        self.assertIn("unknown.png", markdown)
        self.assertEqual(images, ["https://example.com/unknown.png"])


if __name__ == "__main__":
    unittest.main()
