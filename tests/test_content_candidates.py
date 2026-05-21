import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

from adapters.content_candidates import extract_best_candidate_html
from adapters.playwright_adapter import PlaywrightAdapter
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


def test_candidate_extraction_prefers_main_article_container():
    candidate_html = extract_best_candidate_html(SAMPLE_HTML, min_chars=220)
    assert candidate_html is not None

    markdown = html_to_markdown(candidate_html)
    assert "进入大规模验证阶段" in markdown
    assert "工具链" in markdown
    assert "热门推荐" not in markdown
    assert "评论区" not in markdown


def test_requests_adapter_uses_candidate_when_trafilatura_is_too_short():
    response = Mock()
    response.raise_for_status.return_value = None
    response.url = "https://example.com/news/1"
    response.encoding = "utf-8"
    response.apparent_encoding = "utf-8"
    response.text = SAMPLE_HTML

    fake_trafilatura = SimpleNamespace(extract=lambda *_args, **_kwargs: "简讯")
    with patch.dict(sys.modules, {"trafilatura": fake_trafilatura}):
        with patch("adapters.requests_adapter.requests.get", return_value=response):
            article = RequestsAdapter(timeout=3, image_fail_open=False).extract("https://example.com/news/1")

    assert article is not None
    assert "进入大规模验证阶段" in article.markdown
    assert "工具链" in article.markdown
    assert "热门推荐" not in article.markdown
    assert "评论区" not in article.markdown


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


class _FakeReadabilityDocument:
    def __init__(self, html: str):
        self._html = html

    def summary(self) -> str:
        _ = self._html
        return "<div>简讯</div>"

    def title(self) -> str:
        return "readability-title"


def test_playwright_adapter_uses_candidate_when_readability_is_too_short():
    adapter = PlaywrightAdapter(timeout=3, retries=0, image_fail_open=False)
    article = adapter._extract_once(
        "https://example.com/news/1",
        Document=_FakeReadabilityDocument,
        sync_playwright=_fake_sync_playwright,
        budget=3,
    )

    assert article is not None
    assert "进入大规模验证阶段" in article.markdown
    assert "工具链" in article.markdown
    assert "热门推荐" not in article.markdown
    assert "评论区" not in article.markdown
