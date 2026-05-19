# article2md

文章链接 → Markdown 提取器。支持鸿蒙智行/AITO 社区所有内容类型。

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
