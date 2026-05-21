# article2md

文章链接 -> Markdown 提取器。支持鸿蒙智行/AITO 社区内容，并通过 `requests + trafilatura + markdownify` 与 Playwright 兜底提取常见网页正文。

通用网页提取新增“双兜底”策略（无域名特判）：

- `readability` / `trafilatura` 结果质量不足时，自动从 HTML/渲染 DOM 中做候选正文容器打分提取。
- 候选评分综合文本长度、段落数、正文标点/关键词、链接密度惩罚、导航/评论/推荐区域惩罚。
- 适配新闻详情、博客文章、论坛长帖等常见页面结构。

## 安装

```bash
pip install -r requirements.txt
```

## 使用

CLI：

```bash
python extractor.py URL [--json] [--image-fail-open]
```

Python API：

```python
from extractor import article_to_markdown, article_to_dict, ArticleExtractor, Article
```

## 项目结构（单层根目录）

```text
README.md
requirements.txt
extractor.py
cli.py
models.py
markdown.py
images.py
adapters/
  __init__.py
  base.py
  content_candidates.py
  huawei.py
  requests_adapter.py
  playwright_adapter.py
tests/
```

模块职责：

- `extractor.py`: 主 API（`article_to_markdown` / `article_to_dict`）与调度器 `ArticleExtractor`，也可直接作为 CLI 入口运行。
- `cli.py`: CLI 实现。
- `models.py`: `Article` 数据模型与共享常量。
- `markdown.py`: HTML -> Markdown、正文清洗、标题提取、质量校验。
- `images.py`: 图片 URL 提取/规范化、Markdown 图片解析、尺寸解析、正文图过滤。
- `adapters/*`: 平台适配器实现。
  - `content_candidates.py`: 通用候选正文容器评分与质量判定，供 requests/playwright 共享。

## 示例

```bash
# Markdown 输出
python extractor.py "https://omp.uopes.cn/static/webapp/share/article_details.html?contentId=1642222"

# JSON 输出
python extractor.py "https://omp.uopes.cn/static/webapp/share/article_details.html?contentId=1642222" --json

# 图片尺寸探测失败时保留图片（默认 fail-closed）
python extractor.py "https://example.com/article" --image-fail-open
```

## 图文后处理规则

三条提取路径（Huawei/Requests/Playwright）统一使用同一后处理流水线：

1. 保留 Markdown 中已经按原文结构出现的图片，不再把孤儿图片追加到文末。
2. 同时从 `images` 和 Markdown 图片引用中过滤 SVG/非正文图。
3. 清理常见抽取噪音文本。
4. 将 `Article.images` 同步为导出的 Markdown 中实际保留的图片引用，保证数量计数准确。

图片过滤规则：
`(宽 >= 700 或 高 >= 700) 且 宽/高 ∈ (0,1) ∪ (1,3]`。

- 未知尺寸默认 `fail-closed`（删除）。
- `image_fail_open=True` 或 `--image-fail-open` 时改为保留未知尺寸图片。

## 能力与局限

- 对常见正文页（`article/main/role=main` 或 `content/post/detail` 类容器）有较强泛化提取能力。
- 对强登录墙、强反爬、纯视频页、重度聚合页仍可能只能拿到短文本或失败。
- 若页面主体本身是短讯，输出长度会随源文长度而短，不会做站点特判补写。

## 扩展新平台

在 `adapters/` 新建适配器并继承 `PlatformAdapter`，实现：

- `can_handle(url) -> bool`
- `extract(url) -> Article | None`

然后在 `extractor.py` 的 `ArticleExtractor.adapters` 中注册。

## 可选 Playwright 兜底

```bash
pip install playwright readability-lxml
playwright install chromium
```
