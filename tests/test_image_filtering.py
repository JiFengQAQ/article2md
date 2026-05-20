import unittest
from unittest.mock import patch

import extractor


class ImageFilteringTests(unittest.TestCase):
    def test_svg_url_variants_are_detected(self):
        self.assertTrue(extractor._is_svg_url("https://example.com/icon.svg"))
        self.assertTrue(extractor._is_svg_url("https://example.com/icon.svg?x=1"))
        self.assertTrue(extractor._is_svg_url("https://example.com/img?format=image/svg+xml"))

    def test_relative_markdown_ref_is_removed_when_absolute_image_is_filtered(self):
        images = ["https://example.com/img/small.png"]
        with patch.object(extractor, "_fetch_image_dimensions", return_value=(599, 450)):
            markdown = extractor._strip_svg_and_small(
                "before\n\n![](/img/small.png)\n\nafter",
                images,
                600,
                450,
                base_url="https://example.com/article/1",
            )
        self.assertNotIn("small.png", markdown)
        self.assertEqual(images, [])
        self.assertIn("before", markdown)
        self.assertIn("after", markdown)

    def test_threshold_and_fail_open(self):
        images = [
            "https://example.com/keep.png",
            "https://example.com/unknown.png",
        ]
        dims = {
            "https://example.com/keep.png": (600, 450),
            "https://example.com/unknown.png": None,
        }
        with patch.object(extractor, "_fetch_image_dimensions", side_effect=lambda url: dims[url]):
            markdown = extractor._strip_svg_and_small(
                "![](https://example.com/keep.png)\n\n![](https://example.com/unknown.png)",
                images,
                600,
                450,
            )
        self.assertIn("keep.png", markdown)
        self.assertIn("unknown.png", markdown)
        self.assertEqual(images, [
            "https://example.com/keep.png",
            "https://example.com/unknown.png",
        ])


if __name__ == "__main__":
    unittest.main()
