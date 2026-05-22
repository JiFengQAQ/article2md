# article2md

文章链接 -> Markdown 提取器。面向“新闻详情页 / 博客文章 / 论坛长帖 / 常见 SPA 正文页”的泛用抽取，不做域名特判。

## 架构（仅三类方案）

1. 鸿蒙智行社区内部 API
- 命中 `omp.uopes.cn` 时，直接走社区接口提取结构化正文与媒体。

2. 外部网站 DOM 主体 + markdownify（通用主路径）
- 基于静态 HTML 做正文候选容器评分与噪声剪枝。
- 评分综合：文本长度、段落/句子密度、中文标点密度、链接密度惩罚、导航/评论/推荐/分享/广告等负向惩罚。
- 支持正文 sibling 合并，提升“正文分散在多个兄弟节点”的完整度。
- 将候选 HTML 转为 Markdown，并统一做后处理清洗。

3. 困难网站 Playwright 兜底
- 对重前端渲染或静态抓取正文不足的页面，使用浏览器渲染后再走通用正文抽取链路。

## 安装

```bash
pip install -r requirements.txt
```

可选（动态页兜底）：

```bash
pip install playwright
playwright install chromium
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
  - `content_candidates.py`: 通用候选正文容器评分、sibling 合并、噪声剪枝、Markdown 质量度量。

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

三条提取路径统一走同一后处理流水线：

1. 保留 Markdown 中按原文结构出现的图片，不把孤儿图片追加到文末。
2. 从 `images` 与 Markdown 图片引用联合过滤 SVG/非正文图。
3. 清理抽取噪音（空标题、评论区、相关推荐、返回首页、文明发言等后文边界）。
4. 将 `Article.images` 同步为导出 Markdown 中实际保留的图片引用，保证数量计数准确。

图片过滤规则：
`长边 >= 480 且 max(宽/高, 高/宽) <= 5`。

- 未知尺寸默认 `fail-closed`（删除）。
- `image_fail_open=True` 或 `--image-fail-open` 时改为保留未知尺寸图片。

## 能力与局限

- 对常见正文页（新闻详情、博客文章、论坛长帖、常见 SPA 渲染正文）有较强泛化抽取能力。
- 对强登录墙、强反爬、正文极度碎片化（跨 iframe/Shadow DOM）或高度交互聚合页，可能只能得到短文本或失败。
- 本项目不做站点特判，不保证在每个站点都达到人工清洗质量。

## 扩展新平台

在 `adapters/` 新建适配器并继承 `PlatformAdapter`，实现：

- `can_handle(url) -> bool`
- `extract(url) -> Article | None`

然后在 `extractor.py` 的 `ArticleExtractor.adapters` 中注册。
