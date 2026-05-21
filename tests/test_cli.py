import json
from unittest.mock import patch

from cli import main


SAMPLE_RESULT = {
    "title": "标题",
    "subtitle": "副标题",
    "author": "作者",
    "source_url": "https://example.com/a",
    "markdown": "正文内容",
    "images": ["https://example.com/1.jpg"],
}


def test_cli_json_smoke(capsys):
    with patch("cli.article_to_dict", return_value=SAMPLE_RESULT):
        code = main(["https://example.com/a", "--json"])
    output = capsys.readouterr().out

    assert code == 0
    payload = json.loads(output)
    assert payload["title"] == "标题"
    assert payload["markdown"] == "正文内容"


def test_cli_markdown_smoke(capsys):
    with patch("cli.article_to_dict", return_value=SAMPLE_RESULT):
        code = main(["https://example.com/a"])
    output = capsys.readouterr().out

    assert code == 0
    assert "# 标题" in output
    assert "*副标题*" in output
    assert "作者: 作者" in output
    assert "正文内容" in output
