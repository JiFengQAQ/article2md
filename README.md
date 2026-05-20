# article2md

文章链接 → Markdown 提取器。支持鸿蒙智行/AITO 社区内容，并通过 `requests + trafilatura` 与 Playwright 兜底提取常见新闻、媒体号、百科、微博长文等网页。

## 安装

```bash
pip install -r requirements.txt
```

## 使用

```bash
# CLI 输出 Markdown
python extractor.py "https://omp.uopes.cn/static/webapp/share/article_details.html?contentId=1642222"

# CLI 输出 JSON
python extractor.py "https://omp.uopes.cn/static/webapp/share/article_details.html?contentId=1642222" --json
```

### Python 调用

```python
from extractor import article_to_markdown, article_to_dict

md = article_to_markdown(url)        # → str | None
d = article_to_dict(url)             # → dict | None
# d = {'title', 'subtitle', 'author', 'source_url', 'markdown', 'images'}
```

## 图文处理能力

- 已知平台优先走 API，普通网页优先走 `requests + trafilatura`，质量不足时用 Playwright 渲染兜底。
- Markdown 会保留正文图片；当正文抽取器漏掉图片时，会从原始 HTML / 渲染 DOM 中补齐未引用图片。
- SVG 会从 `images` 数组和 Markdown 图片引用中同时剔除。
- 小图会从 `images` 数组和 Markdown 图片引用中同时剔除：宽度 `<600` 或高度 `<450` 的图片视为头像、图标、缩略图等非正文图。
- 图片尺寸检测直接解析 JPEG / PNG / GIF / WebP 头部字节，不依赖 Pillow；网络失败或未知格式采用 fail-open，不误删可能有效的正文图。

## 已知限制

- CAPTCHA、登录墙、强反爬空白页无法通用提取。
- 视频分享页不是文章页，当前不会把视频口播/字幕转成正文。
- 站点返回错误编码时会使用 apparent encoding 修正常见中文乱码，但极端混合编码页面仍可能需要上游修复。

## 支持的内容类型

| 类型 | 说明 | 正文来源 | 图片 |
|------|------|----------|------|
| type=4 articleContentType=1 | 官方文章（功能解读） | richText HTML | 内嵌 + imageContent |
| type=8 articleType=2 | PGC 文章 | mainBodyText | imageUrl / fileContent |
| type=4 articleType=2 | 转发帖 | mainBodyText | 视频封面 |
| type=0 | 用户帖 | textContent | imageContent / fileContent |

## 扩展新平台

继承 `PlatformAdapter`，实现 `can_handle()` 和 `extract()`，注册到 `ArticleExtractor.adapters` 即可。

## 可选：Playwright 兜底

```bash
pip install playwright readability-lxml
playwright install chromium
```

安装后，未知 URL 会自动走无头浏览器渲染路径。
