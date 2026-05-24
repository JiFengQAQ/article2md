# Codex Prompt: article2md 内容清洗瘦身重构

## 角色与目标

你在 `/tmp/article2md-cleaning-refactor-20260524_132705` 这个 git worktree 工作，当前分支是 `refactor/content-cleaning-simplify-20260524_132705`，基线来自 `main` 的 `f6e584a`。

目标：对 article2md 的“内容清洗/候选正文抽取/图片后处理”做一次 **Spec Driven Development + Test Driven Development** 的瘦身重构。

核心要求很硬：

1. 不要在 `main` 分支上操作。
2. 至少砍掉内容清洗相关代码 **25%**，理想区间 25%-50%。
3. 换来更清晰的结构、更好的可读性、更少特判。
4. 允许一定真实网页抽取质量下滑，但现有公开 API、CLI、核心离线测试和基本图文能力不能坏。
5. 不要把旧代码原样搬到新文件里骗行数；如果新增清洗相关模块，要把它计入代码量预算。
6. 不要重新引入 `trafilatura`。
7. 除非必要，不要加站点特判；优先泛化规则。
8. 最终不要提交 commit；保持工作区改动即可，由外层 Hermes 验证和决定是否提交。

## 现状审计

当前内容清洗相关主要集中在：

- `markdown.py`
  - Markdown 转换、正文清洗、CSS 残留过滤、评论/推荐边界截断、验证码/访问墙识别、标题抽取、质量判断。
  - 基线：313 行，16 个函数。
- `images.py`
  - 图片 URL 归一化、lazy/srcset 选择、占位图过滤、Markdown 图片同步、SVG/尺寸过滤、图片尺寸解析和网络探测。
  - 基线：493 行，20 个函数。
- `adapters/content_candidates.py`
  - DOM 候选正文评分、噪声剪枝、兄弟节点合并、Markdown/HTML 质量评分。
  - 基线：651 行，28 个函数。

基线总量：

- 上述三文件总行数：1457
- 非空行：1214

最低验收线：

- 清洗相关总行数必须 `<= 1092`（1457 * 0.75，至少减少 25%）。
- 更好目标：`<= 950`。
- 极限目标：`<= 875`，但不要为了极限破坏结构。

## Spec Driven Development：先写规格，再改代码

创建或更新 `docs/specs/content_cleaning_simplification.md`，内容必须包含：

1. 范围
   - 包含：`markdown.py`、`images.py`、`adapters/content_candidates.py` 以及你新增的任何内容清洗相关模块。
   - 不包含：CLI、平台 adapter 编排、HIMA 专用 API 获取逻辑，除非只是调用接口适配。

2. 保留行为
   - `html_to_markdown` 仍能把 HTML 转 Markdown，并剔除 script/style。
   - `clean_markdown` 仍能：
     - 去空标题；
     - 压缩多余空行；
     - 过滤明显 CSS 残留；
     - 在正文开始后遇到评论/推荐/返回首页等后文边界时截断；
     - 边界在正文前出现时跳过而不是截断全文。
   - `is_quality_article` 仍能拒绝明显 CAPTCHA、反爬 JS、访问墙、短内容。
   - `best_title_from_html` 仍能读 `og:title`、`twitter:title`、`h1`、`title`。
   - `normalize_html_images` 仍能从 `srcset` / `data-srcset` / lazy 属性选择真实图片，并替换 placeholder `src`。
   - `finalize_markdown_and_images` 仍保持 `Article.images` 与最终 Markdown 中唯一图片 URL 同步。
   - `extract_best_candidate_html` 仍走泛化 DOM 候选评分，能处理：正文容器、弱标签长段落、相邻正文块合并、推荐/评论区剪枝、纯图片块保留。

3. 可接受下滑
   - 可以减少大量微调权重、关键词、重复评分逻辑。
   - 可以牺牲少数边缘网站的最优召回。
   - 可以把多个相近规则合并为更粗的规则。

4. 不可接受下滑
   - 现有测试大面积改弱或删除。
   - Markdown 输出出现明显 CSS 块、script/style/noscript/template/svg/canvas/iframe 内容。
   - 图片全部丢失、`Article.images` 与 Markdown 图片不同步。
   - 访问墙/反爬 JS 被当作成功长文章。
   - 公共导入兼容性破坏。

## TDD：先加会失败的结构测试，再动生产代码

新增 `tests/test_content_cleaning_simplification.py`，先写至少这些测试，然后运行它们，确认结构预算测试在基线下失败：

1. `test_content_cleaning_code_budget`
   - 统计清洗相关源码文件总行数。
   - 包括：
     - `markdown.py`
     - `images.py`
     - `adapters/content_candidates.py`
     - 你新增的任何名字包含 `clean` / `content` / `candidate` / `image` 且不在 `tests/` 下的 Python 文件，如果它们属于清洗逻辑。
   - 断言总行数 `<= 1092`。
   - 这个测试在初始基线必须失败，这是本轮 RED。

2. `test_clean_markdown_keeps_article_but_drops_css_and_tail_noise`
   - 构造 Markdown：前置“评论”边界、CSS 块、两段真实正文、后置“相关推荐/评论区”。
   - 断言：正文保留；CSS 和后文边界删除；前置边界不会截断全文。

3. `test_candidate_extraction_keeps_media_only_blocks_and_drops_chrome`
   - 构造 HTML：正文段落 + `<figure><img>` + image-only div + nav/related/comment/style/script。
   - 通过 `extract_best_candidate_html` + `html_to_markdown` 验证正文和图片存在，chrome 不存在。

4. `test_finalize_markdown_and_images_syncs_to_exported_images`
   - 构造相对图片、孤儿图片、SVG、小尺寸图。
   - mock `_fetch_image_dimensions`。
   - 断言最终 Markdown 只含内容图，`images` 精确等于 Markdown 中唯一图片 URL。

运行命令：

```bash
python3 -m pytest tests/test_content_cleaning_simplification.py -q
```

预期：第一步至少结构预算测试失败，然后再开始改生产代码。

## 重构方向

你可以自由设计，但建议按下面思路瘦身：

1. 合并 Markdown 清理逻辑
   - 把 `_line_text_for_matching`、boundary 变体、body line 判断、CSS 判断收敛为少数清晰 helper。
   - 边界模式用少量 regex / token 组表达，不要堆太多函数。
   - CSS 过滤可以粗一点，只要能挡住明显 `:root{}`、`@media`、selector block、property lines。

2. 合并质量评分逻辑
   - `content_candidates.py` 里 Markdown metrics、HTML metrics、candidate score 目前重复算字符数、段落数、链接密度、标点密度。
   - 抽成一个小的 `TextStats` / `score_text` 风格 helper，或更简单的 tuple 函数。
   - 权重减少，不追求精密。

3. 简化 DOM 候选流程
   - 保留：seed 收集、候选评分、剪枝、兄弟合并。
   - 删除过细微调：过多关键词、过多 negative hit 分支、过多相近阈值。
   - `_should_prune_noise` / `_is_hard_negative_node` 可合并。
   - `_html_quality_score` 和 `_candidate_score` 可共用基础评分。

4. 简化图片处理
   - 保留 lazy/srcset/placeholder、SVG 过滤、尺寸规则、Markdown 同步。
   - 图片尺寸解析可考虑用更紧凑的格式处理，但别引入重依赖。
   - `_MARKDOWN_IMAGE_RE` 不要重复定义多次。
   - `_strip_filtered_markdown_images`、`_sync_images_to_markdown`、URL 归一化可合并/减少重复。

5. 保持公共兼容别名
   - `markdown.py` 的 `_clean_markdown`、`_is_quality_article`、`_best_title_from_html`。
   - `images.py` 的 `normalize_image_url`、`extract_images_from_html`、`dedupe`。
   - 现有测试可能 import 私有 helper，能保留则保留；若删 helper，优先更新测试到行为层，而不是弱化行为。

## 必跑验证

每个阶段执行：

```bash
python3 -m pytest tests/test_content_cleaning_simplification.py -q
python3 -m pytest -q
python3 -m compileall -q markdown.py images.py adapters tests
python3 cli.py --help >/tmp/article2md_help.txt
python3 - <<'PY'
from extractor import article_to_markdown, article_to_dict, ArticleExtractor, Article
from markdown import clean_markdown, html_to_markdown, best_title_from_html, is_quality_article
from images import finalize_markdown_and_images, normalize_html_images
from adapters.content_candidates import extract_best_candidate_html
print('imports_ok', callable(article_to_markdown), callable(article_to_dict), ArticleExtractor.__name__, Article.__name__)
print('cleaners_ok', callable(clean_markdown), callable(html_to_markdown), callable(best_title_from_html), callable(is_quality_article))
print('images_ok', callable(finalize_markdown_and_images), callable(normalize_html_images))
print('candidates_ok', callable(extract_best_candidate_html))
PY
```

最后输出：

1. 修改摘要。
2. 行数对比：基线 1457 / 当前多少 / 减少百分比。
3. 哪些行为可能下滑。
4. 测试命令和结果。
5. 是否建议外层 Hermes 采纳。

## 禁止事项

- 禁止直接操作 `main`。
- 禁止删除测试来换绿。
- 禁止通过把代码搬进不计数文件来骗预算。
- 禁止 `curl | python`、`curl | sh` 这类直接执行网络内容的管道。
- 禁止引入大型新依赖。
- 禁止把所有站点质量问题变成域名特判。
