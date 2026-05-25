from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from adapters.content_candidates import extract_best_candidate_html
from images import _markdown_image_urls, finalize_markdown_and_images
from markdown import clean_markdown, html_to_markdown


def _count_cleaning_lines(repo_root: Path) -> int:
    explicit = {
        repo_root / "markdown.py",
        repo_root / "images.py",
        repo_root / "adapters" / "content_candidates.py",
    }

    keyword_files = set()
    for py_file in repo_root.rglob("*.py"):
        rel = py_file.relative_to(repo_root)
        rel_str = str(rel).lower()
        if rel_str.startswith("tests/"):
            continue
        if py_file in explicit:
            continue
        if any(token in rel_str for token in ("clean", "content", "candidate", "image")):
            keyword_files.add(py_file)

    targets = sorted(explicit | keyword_files)
    total = 0
    for file_path in targets:
        total += len(file_path.read_text(encoding="utf-8").splitlines())
    return total


def test_content_cleaning_code_budget():
    repo_root = Path(__file__).resolve().parents[1]
    assert _count_cleaning_lines(repo_root) <= 1092


def test_clean_markdown_keeps_article_but_drops_css_and_tail_noise():
    raw = """
    评论

    :root { --brand: #f00; }
    .card { color: red; }

    # 标题
    正文第一段：这是保留内容，包含足够文字信息和标点。
    正文第二段：继续补充细节，确保正文已经开始。

    相关推荐
    这行推荐区内容必须被截断删除。
    评论区
    """

    cleaned = clean_markdown(raw)

    assert "正文第一段" in cleaned
    assert "正文第二段" in cleaned
    assert "评论\n\n" not in cleaned
    assert ":root" not in cleaned
    assert ".card {" not in cleaned
    assert "相关推荐" not in cleaned
    assert "推荐区内容" not in cleaned
    assert "评论区" not in cleaned


def test_candidate_extraction_keeps_media_only_blocks_and_drops_chrome():
    html = """
    <html><body>
      <article class="content-body">
        <p>正文第一段：发布会披露大量关键细节，覆盖复杂场景验证与后续路线，并解释了感知融合、规划策略、控制执行和工程验证的协同机制，整体方案强调长期稳定性、安全冗余和跨城市部署一致性。</p>
        <figure><img src="https://example.com/images/figure.jpg" alt="图1"></figure>
        <div class="image-only"><img src="https://example.com/images/standalone.jpg" alt="图2"></div>
        <p>正文第二段：团队说明将继续开放能力并完善质量闭环机制，后续版本会提供更多调试指标和回归工具，帮助合作伙伴缩短接入周期并提升量产交付效率，同时保持异常诊断和问题追踪链路可审计。</p>
      </article>
      <nav>首页 频道 视频 财经</nav>
      <section class="related">相关推荐 <a href="/r1">链接1</a></section>
      <section class="comments">评论区 登录后评论</section>
      <style>.x{color:red}</style>
      <script>console.log('x')</script>
    </body></html>
    """

    candidate_html = extract_best_candidate_html(html, min_chars=120)
    assert candidate_html is not None

    markdown = html_to_markdown(candidate_html)
    assert "正文第一段" in markdown
    assert "正文第二段" in markdown
    assert "figure.jpg" in markdown
    assert "standalone.jpg" in markdown
    assert "相关推荐" not in markdown
    assert "评论区" not in markdown
    assert "首页 频道" not in markdown


def test_finalize_markdown_and_images_syncs_to_exported_images():
    images = [
        "https://example.com/img/content.jpg",
        "https://example.com/img/orphan.jpg",
        "https://example.com/img/vector.svg",
        "https://example.com/img/tiny.jpg",
    ]

    markdown = (
        "前文\n\n"
        "![](/img/content.jpg)\n\n"
        "![](https://example.com/img/vector.svg)\n\n"
        "![](https://example.com/img/tiny.jpg)\n\n"
        "后文"
    )

    dims = {
        "https://example.com/img/content.jpg": (1280, 800),
        "https://example.com/img/orphan.jpg": (1280, 800),
        "https://example.com/img/tiny.jpg": (120, 90),
    }

    with patch("images._fetch_image_dimensions", side_effect=lambda url: dims.get(url)):
        final_markdown = finalize_markdown_and_images(
            markdown=markdown,
            images=images,
            base_url="https://example.com/news/1",
            min_side=480,
            max_landscape_aspect=5,
        )

    exported = _markdown_image_urls(final_markdown)
    assert exported == ["https://example.com/img/content.jpg"]
    assert images == ["https://example.com/img/content.jpg"]
    assert "vector.svg" not in final_markdown
    assert "tiny.jpg" not in final_markdown
    assert "orphan.jpg" not in final_markdown
