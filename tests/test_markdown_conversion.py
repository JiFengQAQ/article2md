import ast
import re
from pathlib import Path

from markdown import clean_markdown, html_to_markdown


def test_html_to_markdown_preserves_headings_paragraphs_links_and_images():
    html = """
    <article>
      <h1>Breaking News</h1>
      <p>First paragraph with a <a href="https://example.com/source">source</a>.</p>
      <p>Second paragraph with context.</p>
      <figure>
        <img src="https://cdn.example.com/chart.png" alt="Chart">
      </figure>
    </article>
    """

    markdown = html_to_markdown(html)

    assert re.search(r"(?m)^#\s+Breaking News\s*$", markdown)
    assert "First paragraph with a" in markdown
    assert "Second paragraph with context." in markdown
    assert re.search(r"\[source\]\((?:<)?https://example\.com/source(?:>)?\)", markdown)
    assert "![Chart](https://cdn.example.com/chart.png)" in markdown


def test_html_to_markdown_reasonably_preserves_lists_and_preformatted_code():
    html = """
    <section>
      <ul>
        <li>first item</li>
        <li>second item</li>
      </ul>
      <pre><code>def add(a, b):
    return a + b
</code></pre>
    </section>
    """

    markdown = html_to_markdown(html)

    assert re.search(r"(?m)^\s*[*+-]\s+first item\s*$", markdown)
    assert re.search(r"(?m)^\s*[*+-]\s+second item\s*$", markdown)
    assert "def add(a, b):" in markdown
    assert "return a + b" in markdown
    assert ("```" in markdown) or re.search(r"(?m)^\s{4}def add\(a, b\):", markdown)


def test_best_title_from_html_reads_h1_and_title_tags():
    from markdown import best_title_from_html

    assert best_title_from_html("<html><body><h1>正文标题</h1></body></html>") == "正文标题"
    assert best_title_from_html("<html><head><title>页面标题</title></head></html>") == "页面标题"


def test_markdown_module_has_no_html2text_import():
    markdown_file = Path(__file__).resolve().parents[1] / "markdown.py"
    tree = ast.parse(markdown_file.read_text(encoding="utf-8"), filename=str(markdown_file))

    forbidden_imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            forbidden_imports.extend(alias.name for alias in node.names if alias.name == "html2text")
        if isinstance(node, ast.ImportFrom) and node.module == "html2text":
            forbidden_imports.append("html2text")

    assert not forbidden_imports, "markdown.py must not import html2text"


def test_requirements_no_html2text_dependency():
    requirements_file = Path(__file__).resolve().parents[1] / "requirements.txt"
    lines = [line.strip().lower() for line in requirements_file.read_text(encoding="utf-8").splitlines()]
    non_comment_lines = [line for line in lines if line and not line.startswith("#")]

    assert all(not line.startswith("html2text") for line in non_comment_lines)


def test_clean_markdown_truncates_at_post_article_comment_boundary():
    raw = """
    # 鸿蒙智行智界R7启动全国规模交付

    首批车主已在多个城市完成交付。
    ![交付现场](https://img.example.com/delivery.jpg)
    新车将在下周开放更多试驾场次。
    ###
    评论（174）
    文明上网理性发言，请遵守评论服务协议
    发表评论
    查看更多 374 条评论
    热门推荐
    相关推荐
    相关阅读
    回首页看更多汽车资讯
    用户A：这个车真不错
    """

    cleaned = clean_markdown(raw)

    assert "首批车主已在多个城市完成交付。" in cleaned
    assert "![交付现场](https://img.example.com/delivery.jpg)" in cleaned
    assert "新车将在下周开放更多试驾场次。" in cleaned
    assert "###" not in cleaned
    assert "评论（174）" not in cleaned
    assert "发表评论" not in cleaned
    assert "查看更多 374 条评论" not in cleaned
    assert "热门推荐" not in cleaned
    assert "相关推荐" not in cleaned
    assert "相关阅读" not in cleaned
    assert "回首页看更多汽车资讯" not in cleaned
    assert "用户A：这个车真不错" not in cleaned


def test_clean_markdown_does_not_truncate_when_boundary_markers_appear_before_article():
    raw = """
    评论
    热门推荐
    发表评论

    # 正文标题
    这是正文第一段，介绍背景信息。
    这是正文第二段，包含更多细节。
    ![配图](https://img.example.com/story.jpg)
    """

    cleaned = clean_markdown(raw)

    assert "这是正文第一段，介绍背景信息。" in cleaned
    assert "这是正文第二段，包含更多细节。" in cleaned
    assert "![配图](https://img.example.com/story.jpg)" in cleaned
    assert "评论" not in cleaned
    assert "热门推荐" not in cleaned
    assert "发表评论" not in cleaned


def test_clean_markdown_normalizes_markdown_links_before_boundary_match():
    raw = """
    # 正文标题
    这是正文第一段，包含关键背景。
    这是正文第二段，包含更多信息。
    [相关推荐](https://example.com/related)
    这行是推荐区文案，不应该保留。
    """

    cleaned = clean_markdown(raw)

    assert "这是正文第一段，包含关键背景。" in cleaned
    assert "这是正文第二段，包含更多信息。" in cleaned
    assert "相关推荐" not in cleaned
    assert "推荐区文案" not in cleaned


def test_clean_markdown_boundary_after_title_but_before_body_does_not_truncate_article():
    raw = """
    # 正文标题
    [回首页看更多](https://example.com/)
    热门推荐

    正文第一段：这是实际内容起点，应该被保留。
    正文第二段：这段内容也应该被保留。
    """

    cleaned = clean_markdown(raw)

    assert "# 正文标题" in cleaned
    assert "正文第一段：这是实际内容起点，应该被保留。" in cleaned
    assert "正文第二段：这段内容也应该被保留。" in cleaned
    assert "回首页看更多" not in cleaned
    assert "热门推荐" not in cleaned


def test_clean_markdown_comment_link_before_body_does_not_truncate_article():
    raw = """
    # 上市 13 天，鸿蒙智行首款旅行车享界 S9T 大定突破 15000 台

    2025/9/30 9:15:12
    来源：[IT之家](https://www.ithome.com/0/886/797.htm)
    作者：**远洋**
    责编：**远洋**

    [评论：](#post_comm)

    感谢IT之家网友提供线索。

    IT之家 9 月 30 日消息，9 月 16 日，鸿蒙智行首款旅行车享界 S9T 正式上市，售价 30.98 万元起。上市 13 天大定已突破 15000 台。

    享界 S9T 首批搭载 ADS4，全系标配全维感知系统、全新一代华为途灵平台、华为悦彰音响卓越系列、鸿蒙 ALPS 健康座舱等华为黑科技。

    相关阅读：
    这行推荐阅读不该保留。
    """

    cleaned = clean_markdown(raw)

    assert "IT之家 9 月 30 日消息" in cleaned
    assert "享界 S9T 首批搭载 ADS4" in cleaned
    assert "[评论：]" not in cleaned
    assert "推荐阅读不该保留" not in cleaned


def test_is_quality_article_rejects_generic_antibot_javascript_payload():
    from markdown import is_quality_article
    from models import Article

    article = Article(
        title="访问验证",
        source_url="https://example.com/article/1",
        markdown="var glb; window.byted_acrawler.init({aid:99999999}); var __ac_signature = window.byted_acrawler.sign('','nonce'); window.location.reload();",
    )

    assert not is_quality_article(article, min_chars=20)


def test_clean_markdown_removes_residual_css_blocks():
    raw = """
    .data_color_scheme_dark{--weui-BG-0:#111;--weui-FG-0:#eee;}
    :root{--main-color:#333;--font-size:14px;}
    @media (prefers-color-scheme: dark){.content{color:#ddd;}}
    .article-content .meta, .article-content .tags { display:none; margin:0; }
    #article p { line-height: 1.8; color: #222; }

    # 正文标题
    正文第一段：这是保留内容。
    正文第二段：这也是保留内容。
    """

    cleaned = clean_markdown(raw)

    assert ".data_color_scheme_dark" not in cleaned
    assert ":root{" not in cleaned
    assert "@media" not in cleaned
    assert ".article-content .meta" not in cleaned
    assert "#article p {" not in cleaned
    assert "正文第一段：这是保留内容。" in cleaned
    assert "正文第二段：这也是保留内容。" in cleaned
