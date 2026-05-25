"""抽取器CLI入口"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Optional

from extractor import article_to_dict


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract an article URL to Markdown")
    parser.add_argument("url")
    parser.add_argument("--json", action="store_true", help="output structured JSON")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    args = build_parser().parse_args(argv)
    result = article_to_dict(args.url)
    if not result:
        print("ERROR: Extraction failed")
        return 1

    if args.json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print("")
        return 0

    print(f"# {result['title']}")
    if result["subtitle"]:
        print(f"*{result['subtitle']}*")
    if result["author"]:
        print(f"作者: {result['author']}")
    print()
    print(result["markdown"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
