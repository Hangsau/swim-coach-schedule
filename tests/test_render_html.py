import sys
from pathlib import Path


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import query  # noqa: E402
from render_html import render_month  # noqa: E402


def test_render_uses_passed_data_instead_of_query_default(monkeypatch, v4_data):
    lesson = v4_data["lessons"][0]
    lesson["date"] = "2031-02-03"
    v4_data["classes"][0]["name"] = "Passed-in class"

    def fail_default_load():
        raise AssertionError("render must not load query.DATA when data was supplied")

    monkeypatch.setattr(query, "load", fail_default_load)

    html = render_month(v4_data, 2031, 2)

    assert "Passed-in class" in html
    assert "總堂數：<strong>1</strong> 堂" in html
