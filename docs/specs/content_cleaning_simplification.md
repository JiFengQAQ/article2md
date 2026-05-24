# Content Cleaning Simplification Spec

## 1. Scope

In scope:
- `markdown.py`
- `images.py`
- `adapters/content_candidates.py`
- Any newly added Python modules that implement content cleaning, candidate extraction, or image post-processing logic.

Out of scope:
- CLI user interface and argument flow.
- Platform adapter orchestration (`requests` / `playwright` routing), except call-site compatibility updates.
- HIMA-specific API fetching logic, except interface-level compatibility adjustments.

## 2. Preserved Behaviors

- `html_to_markdown` still converts HTML to Markdown and strips `script` / `style` content.
- `clean_markdown` still:
  - removes empty headings;
  - compresses redundant blank lines;
  - removes obvious CSS residue;
  - truncates when post-article boundaries (comment/recommend/home-return markers) appear after article body starts;
  - skips pre-body boundaries instead of truncating the whole article.
- `is_quality_article` still rejects obvious CAPTCHA, anti-bot JavaScript payloads, access-wall pages, and short content.
- `best_title_from_html` still checks `og:title`, `twitter:title`, `h1`, and `title`.
- `normalize_html_images` still chooses real image URLs from `srcset` / `data-srcset` / lazy attributes and replaces placeholder `src`.
- `finalize_markdown_and_images` still keeps `Article.images` exactly synced with unique image URLs exported in final Markdown.
- `extract_best_candidate_html` still uses generalized DOM candidate scoring and handles:
  - article containers;
  - weak-tag long paragraphs;
  - adjacent content block merging;
  - recommendation/comment chrome pruning;
  - media-only content block retention.

## 3. Acceptable Regressions

- Reduce micro-tuned weights, keywords, and duplicated scoring branches.
- Allow minor recall loss on edge-case websites.
- Merge similar rules into broader heuristics.

## 4. Non-Acceptable Regressions

- Weakening or deleting existing tests at scale just to pass.
- Markdown output containing obvious CSS blocks or `script`/`style`/`noscript`/`template`/`svg`/`canvas`/`iframe` content.
- Losing all images or desynchronizing `Article.images` from Markdown image refs.
- Treating access-wall/anti-bot payloads as valid long-form articles.
- Breaking public import compatibility.
