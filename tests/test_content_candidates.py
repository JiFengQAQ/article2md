from pathlib import Path
from unittest.mock import Mock, patch

from adapters.content_candidates import (
    _attr_blob,
    _serialize_candidate,
    _serialize_node_group,
    extract_best_candidate_html,
)
from adapters.playwright_adapter import PlaywrightAdapter
from adapters.requests_adapter import build_article_from_html
from adapters.requests_adapter import RequestsAdapter
from markdown import html_to_markdown


SAMPLE_HTML = """
<html>
  <head>
    <title>示例新闻标题</title>
  </head>
  <body>
    <div id="top-nav" class="nav menu">
      <a href="/a">首页</a>
      <a href="/b">科技</a>
      <a href="/c">汽车</a>
      <a href="/d">财经</a>
    </div>
    <main>
      <article class="news-detail content-body article-content">
        <h1>示例新闻标题</h1>
        <p>在发布会上，负责人表示新一代系统已经进入大规模验证阶段，团队将继续优化稳定性与安全性。</p>
        <p>该方案围绕硬件、算法与工程流程进行协同设计，同时强调在复杂交通环境下的持续学习能力。</p>
        <p>项目成员介绍，研发过程覆盖多城市路测与封闭场地测试，并通过数据闭环推动版本快速迭代。</p>
        <p>此外，企业还公布了面向合作伙伴的工具链，帮助产业链更高效地完成接入、验证与量产准备。</p>
        <p>根据现场披露的信息，后续版本将进一步补充场景覆盖范围，并向开发者开放更多调试接口。</p>
      </article>
      <section class="recommend related">
        热门推荐：<a href="/r1">相关阅读一</a> <a href="/r2">相关阅读二</a>
      </section>
    </main>
    <div class="comments reply">
      评论区：登录后评论，查看更多网友评论
    </div>
  </body>
</html>
""".strip()


SIBLING_HTML = """
<html>
  <head><title>分段正文</title></head>
  <body>
    <div class="page-shell">
      <div class="breadcrumb"><a href="/">首页</a> / 资讯</div>
      <div class="wrap">
        <div>
          <p>第一段正文：记者在发布现场表示，本次升级覆盖底盘、智驾与座舱协同，重点优化复杂工况稳定性。</p>
          <p>第二段正文：研发团队介绍，方案已经在多城路测环境完成长期验证，并对高频场景进行了专项回归。</p>
        </div>
        <div>
          <p>第三段正文：新版本将向合作伙伴开放调试接口与质量分析工具，帮助产业链缩短接入周期。</p>
          <p>第四段正文：后续还会逐步扩展可解释能力与异常诊断机制，提升交付效率和维护体验。</p>
        </div>
        <div class="related-list">相关阅读：<a href="/x">链接1</a><a href="/y">链接2</a></div>
      </div>
    </div>
  </body>
</html>
""".strip()


def test_candidate_extraction_prefers_main_article_container():
    candidate_html = extract_best_candidate_html(SAMPLE_HTML, min_chars=220)
    assert candidate_html is not None

    markdown = html_to_markdown(candidate_html)
    assert "进入大规模验证阶段" in markdown
    assert "工具链" in markdown
    assert "热门推荐" not in markdown
    assert "评论区" not in markdown


def test_candidate_extraction_merges_siblings_and_prunes_related_blocks():
    candidate_html = extract_best_candidate_html(SIBLING_HTML, min_chars=220)
    assert candidate_html is not None

    markdown = html_to_markdown(candidate_html)
    assert "第一段正文" in markdown
    assert "第四段正文" in markdown
    assert "相关阅读" not in markdown


def test_candidate_extraction_handles_missing_optional_attributes():
    html = """
    <html><body>
      <article>
        <h1>无属性正文容器</h1>
        <p>第一段正文：发布会上介绍，系统会覆盖更多复杂交通场景，并提升长期运行稳定性。</p>
        <p>第二段正文：研发团队表示，新版本将继续通过数据闭环优化安全策略与交互体验。</p>
        <p>第三段正文：后续还会面向合作伙伴开放调试工具，帮助项目更快完成量产验证，并持续公开阶段性测试结果。</p>
      </article>
    </body></html>
    """

    candidate_html = extract_best_candidate_html(html, min_chars=100)

    assert candidate_html is not None
    markdown = html_to_markdown(candidate_html)
    assert "系统会覆盖更多复杂交通场景" in markdown


def test_attr_blob_handles_none_list_and_tuple_without_repr_noise():
    class _FakeNode:
        def __init__(self, attrs):
            self._attrs = attrs

        def get(self, key):
            return self._attrs.get(key)

    node = _FakeNode(
        {
            "id": None,
            "class": ["Article", "Main"],
            "role": ("content", "primary"),
            "itemprop": "articleBody",
            "data-role": None,
            "aria-label": ("正文",),
            "aria-labelledby": ["Title", None, "Lead"],
        }
    )
    blob = _attr_blob(node)
    assert "['article', 'main']" not in blob
    assert "('content', 'primary')" not in blob
    assert "article main" in blob
    assert "content primary" in blob
    assert "articlebody" in blob
    assert "正文" in blob
    assert "title lead" in blob


def test_serialize_candidate_prunes_always_removed_tags():
    from lxml import html as lxml_html

    html = """
    <html><body>
      <article class="content">
        <style>.x{color:red}</style>
        <script>console.log("x")</script>
        <noscript>noscript text</noscript>
        <template><div>tmpl</div></template>
        <svg><text>vector</text></svg>
        <canvas>canvas text</canvas>
        <iframe src="/embed"></iframe>
        <p>正文段落保留</p>
      </article>
    </body></html>
    """
    root = lxml_html.fromstring(html)
    article = root.xpath("//article")[0]
    serialized = _serialize_candidate(article, lxml_html)
    assert "<style" not in serialized
    assert "<script" not in serialized
    assert "<noscript" not in serialized
    assert "<template" not in serialized
    assert "<svg" not in serialized
    assert "<canvas" not in serialized
    assert "<iframe" not in serialized
    assert "正文段落保留" in serialized


def test_serialize_node_group_prunes_always_removed_tags():
    from lxml import html as lxml_html

    html = """
    <html><body>
      <div class="group">
        <section><p>第一段正文</p><style>.a{}</style></section>
        <section><p>第二段正文</p><script>bad()</script><iframe src="/x"></iframe></section>
      </div>
    </body></html>
    """
    root = lxml_html.fromstring(html)
    nodes = root.xpath("//div[@class='group']/section")
    serialized = _serialize_node_group(nodes, lxml_html)
    assert "<style" not in serialized
    assert "<script" not in serialized
    assert "<iframe" not in serialized
    assert "第一段正文" in serialized
    assert "第二段正文" in serialized


def test_requests_adapter_uses_dom_candidate_main_path():
    response = Mock()
    response.raise_for_status.return_value = None
    response.url = "https://example.com/news/1"
    response.encoding = "utf-8"
    response.apparent_encoding = "utf-8"
    response.text = SAMPLE_HTML

    with patch("adapters.requests_adapter.requests.get", return_value=response):
        article = RequestsAdapter(timeout=3, image_fail_open=False).extract("https://example.com/news/1")

    assert article is not None
    assert "进入大规模验证阶段" in article.markdown
    assert "工具链" in article.markdown
    assert "热门推荐" not in article.markdown
    assert "评论区" not in article.markdown


def test_requests_adapter_source_does_not_use_trafilatura_main_path():
    source_file = Path(__file__).resolve().parents[1] / "adapters" / "requests_adapter.py"
    source = source_file.read_text(encoding="utf-8").lower()
    assert "trafilatura" not in source


def test_requests_adapter_normalizes_lazy_images_and_exports_absolute_markdown_urls():
    html = """
    <html><body>
      <article class="news-detail content-body">
        <h1>测试标题</h1>
        <p>第一段正文：发布会披露了大量技术细节，并在多地场景完成验证，具备稳定的量产交付能力，现场还展示了多个复杂工况样例，覆盖城市道路、快速路和泊车等高频场景，强调长期可靠性与安全冗余。</p>
        <p>第二段正文：团队强调会持续优化算法、硬件协同与工程流程，提升复杂路况下的安全冗余与体验，同时会以阶段性版本持续开放调试信息，帮助开发者定位问题、复现缺陷并验证修复策略。</p>
        <p>第三段正文：后续版本还将开放更多调试接口，为合作伙伴提供更高效的接入与验证工具，并通过统一的质量度量体系跟踪性能变化，公开关键测试维度与回归流程，保证升级过程可追溯。</p>
        <img src="" data-src="/assets/cover.jpg" alt="配图">
      </article>
    </body></html>
    """
    response = Mock()
    response.raise_for_status.return_value = None
    response.url = "https://example.com/news/100"
    response.encoding = "utf-8"
    response.apparent_encoding = "utf-8"
    response.text = html

    with patch("adapters.requests_adapter.requests.get", return_value=response):
        with patch("images._fetch_image_dimensions", return_value=(1280, 720)):
            article = RequestsAdapter(timeout=3, image_fail_open=False).extract("https://example.com/news/100")

    assert article is not None
    assert "(https://example.com/assets/cover.jpg)" in article.markdown
    assert "![](/assets/cover.jpg)" not in article.markdown
    assert article.images == ["https://example.com/assets/cover.jpg"]


def test_build_article_from_html_preserves_standalone_image_blocks_in_candidate_body():
    html = """
    <html><body>
      <article class="news-detail content-body article-content">
        <h1>图文正文保留测试</h1>
        <p>第一段正文：发布会披露了系统升级路线，包含感知融合、规划控制和工程验证流程，覆盖城市与高速多场景，强调长期稳定性与安全冗余。</p>
        <p><img src="/images/p-inline.jpg" alt="段落内配图"></p>
        <p>第二段正文：团队介绍了版本回归策略与问题闭环机制，并展示了多个复杂路况样例，说明新架构在真实环境中的适应能力持续提升。</p>
        <figure><img src="/images/figure.jpg" alt="图注配图"></figure>
        <p>第三段正文：后续会开放更多调试指标与性能观测能力，帮助合作伙伴更高效定位问题，缩短迭代周期并提高交付一致性。</p>
        <div class="image-only"><img src="/images/div-only.jpg" alt="独立图片区块"></div>
        <p>第四段正文：产品团队还强调将继续完善异常处理机制与冗余策略，在复杂交通环境中维持稳定体验。</p>
      </article>
    </body></html>
    """

    with patch("images._fetch_image_dimensions", return_value=(1280, 720)):
        article = build_article_from_html(
            html=html,
            final_url="https://example.com/news/200",
            source_url="https://example.com/news/200",
            image_fail_open=False,
            min_chars=120,
        )

    assert article is not None
    assert "https://example.com/images/p-inline.jpg" in article.markdown
    assert "https://example.com/images/figure.jpg" in article.markdown
    assert "https://example.com/images/div-only.jpg" in article.markdown
    assert article.images == [
        "https://example.com/images/p-inline.jpg",
        "https://example.com/images/figure.jpg",
        "https://example.com/images/div-only.jpg",
    ]


class _FakeLocator:
    def __init__(self, text: str):
        self._text = text

    def count(self) -> int:
        return 1

    def inner_text(self, timeout: int = 0) -> str:
        _ = timeout
        return self._text


class _FakePage:
    def __init__(self, html: str, final_url: str):
        self._html = html
        self.url = final_url
        self._body_text = (
            "示例新闻标题 "
            "在发布会上，负责人表示新一代系统已经进入大规模验证阶段。 "
            "此外，企业还公布了面向合作伙伴的工具链。"
        )

    def goto(self, url: str, wait_until: str, timeout: int) -> None:
        _ = (url, wait_until, timeout)

    def wait_for_timeout(self, ms: int) -> None:
        _ = ms

    def wait_for_selector(self, selector: str, timeout: int) -> None:
        _ = (selector, timeout)

    def evaluate(self, script: str, *args):
        _ = args
        if "querySelectorAll('article img" in script:
            return []
        return ""

    def content(self) -> str:
        return self._html

    def locator(self, selector: str) -> _FakeLocator:
        _ = selector
        return _FakeLocator(self._body_text)


class _FakeBrowser:
    def __init__(self, html: str, final_url: str):
        self._page = _FakePage(html, final_url)

    def new_page(self, **kwargs) -> _FakePage:
        _ = kwargs
        return self._page

    def close(self) -> None:
        return None


class _FakeChromium:
    def __init__(self, html: str, final_url: str):
        self._html = html
        self._final_url = final_url

    def launch(self, headless: bool = True) -> _FakeBrowser:
        _ = headless
        return _FakeBrowser(self._html, self._final_url)


class _FakePlaywrightContext:
    def __init__(self, html: str, final_url: str):
        self.chromium = _FakeChromium(html, final_url)

    def __enter__(self) -> "_FakePlaywrightContext":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)
        return None


def _fake_sync_playwright():
    return _FakePlaywrightContext(SAMPLE_HTML, "https://example.com/news/1")


def test_playwright_adapter_reuses_dom_candidate_pipeline_with_rendered_html():
    adapter = PlaywrightAdapter(timeout=3, retries=0, image_fail_open=False)
    with patch("adapters.playwright_adapter.build_article_from_html", wraps=build_article_from_html) as patched_pipeline:
        article = adapter._extract_once(
            "https://example.com/news/1",
            sync_playwright=_fake_sync_playwright,
            budget=3,
        )

    assert article is not None
    assert "进入大规模验证阶段" in article.markdown
    assert "工具链" in article.markdown
    assert "热门推荐" not in article.markdown
    assert "评论区" not in article.markdown
    patched_pipeline.assert_called()
    call_html = patched_pipeline.call_args.kwargs["html"]
    assert "news-detail content-body article-content" in call_html
