import extractor


def test_top_level_public_exports_exist():
    assert extractor.Article is not None
    assert extractor.ArticleExtractor is not None
    assert callable(extractor.article_to_markdown)
    assert callable(extractor.article_to_dict)
