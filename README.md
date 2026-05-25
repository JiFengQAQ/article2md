# article2md
## 安装

```bash
pip install -r requirements.txt
```

同时建议（可选）：

```bash
pip install playwright
playwright install chromium
```

## 使用

CLI：

```bash
python extractor.py URL [--json]
```

Python API：

```python
from extractor import article_to_dict

url = "https://www.example.com/article.html"

article = article_to_dict(url)

if article is None:
    print("extract failed")
else:
    print(article["title"])
    print(article["source_url"])
    print(article["markdown"])
    print(article["images"])
```

如果只要Markdown：

```python
from extractor import article_to_markdown

md = article_to_markdown("https://www.example.com/article.html")

if md:
    print(md)
else:
    print("extract failed")
```

## 项目结构

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
  hima_community_adapter.py
  requests_adapter.py
  playwright_adapter.py
tests/
```
