from __future__ import annotations

from unittest.mock import patch

from adapters.content_candidates import extract_best_candidate_html
from adapters.requests_adapter import build_article_from_html
from images import finalize_markdown_and_images
from markdown import is_quality_article
from models import Article


def test_quality_gate_rejects_low_chinese_antibot_payload_without_vendor_tokens():
    article = Article(
        title="访问验证",
        source_url="https://example.com/article/1",
        markdown=(
            "var token = Math.random().toString(36); "
            "document.cookie = 'verify=' + token; "
            "window.location.href = '/challenge?from=article'; "
            "Please enable JavaScript and reload the page."
        ),
    )

    assert not is_quality_article(article, min_chars=20)


def test_quality_gate_allows_chinese_article_with_english_product_terms():
    article = Article(
        title="华为乾崑ADS 4技术解析",
        source_url="https://example.com/article/2",
        markdown=(
            "华为乾崑ADS 4在感知链路、规划策略与执行稳定性方面做了系统升级，"
            "通过WEWA架构提升城市道路和高速场景的一体化体验。"
            "团队表示OTA后会继续开放更多调试指标。"
        ),
    )

    assert is_quality_article(article, min_chars=60)


def test_refactored_image_pipeline_filters_and_syncs_in_one_pass():
    images = [
        "https://example.com/img/keep.jpg",
        "https://example.com/img/orphan.jpg",
        "https://example.com/img/icon.svg",
        "https://example.com/img/tiny.jpg",
    ]
    markdown = "前文\n\n![](/img/keep.jpg)\n\n![](https://example.com/img/icon.svg)\n\n![](/img/tiny.jpg)\n\n后文"
    dims = {
        "https://example.com/img/keep.jpg": (1200, 800),
        "https://example.com/img/orphan.jpg": (1200, 800),
        "https://example.com/img/tiny.jpg": (120, 90),
    }

    with patch("images._fetch_image_dimensions", side_effect=lambda url: dims.get(url)):
        final = finalize_markdown_and_images(
            markdown=markdown,
            images=images,
            base_url="https://example.com/news/1",
            image_fail_open=False,
        )

    assert final.count("![](") == 1
    assert "https://example.com/img/keep.jpg" in final
    assert images == ["https://example.com/img/keep.jpg"]
    assert "orphan" not in final
    assert "icon.svg" not in final
    assert "tiny" not in final


def test_candidate_extraction_still_merges_adjacent_body_blocks_after_stats_refactor():
    html = """
    <html><body>
      <div class="shell">
        <div class="crumb">首页 / 新闻</div>
        <div class="wrap">
          <div><p>第一段正文：发布会介绍了系统升级路径，覆盖城市道路、高速和泊车等复杂场景，并强调长期稳定性。</p></div>
          <div><p>第二段正文：研发团队表示会继续通过数据闭环优化安全策略，提升用户在高频通勤场景中的体验。</p></div>
          <div><p>第三段正文：合作伙伴将获得更多调试指标和质量分析工具，帮助缩短接入验证周期。</p></div>
          <div class="related">相关推荐：<a href="/x">链接</a></div>
        </div>
      </div>
    </body></html>
    """

    candidate_html = extract_best_candidate_html(html, min_chars=120)
    assert candidate_html is not None
    assert "第一段正文" in candidate_html
    assert "第三段正文" in candidate_html

    article = build_article_from_html(
        html=html,
        final_url="https://example.com/news/3",
        source_url="https://example.com/news/3",
        image_fail_open=True,
        min_chars=120,
    )
    assert article is not None
    assert "相关推荐" not in article.markdown
