import sys
from datetime import date
from pathlib import Path

import pytest


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import query  # noqa: E402
from render_html import (  # noqa: E402
    _assert_grid_complete,
    render_grid,
    render_month,
    render_summary,
)


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


def add_custom_time_lesson(data):
    data["classes"].append({
        "id": "C-CUSTOM",
        "name": "僑泰國二7(代課)",
        "weekly_count": 1,
        "level": "",
    })
    data["lessons"].append({
        "id": "L-CUSTOM",
        "class_id": "C-CUSTOM",
        "date": "2031-02-03",
        "time": "08:00-09:00",
    })


def test_custom_time_lesson_appears_in_every_online_view(v4_data):
    add_custom_time_lesson(v4_data)

    grid_html = render_grid(v4_data, 2031, 2)
    month_html = render_month(v4_data, 2031, 2)
    summary_html = render_summary(v4_data)

    assert '自訂<br><span class="th-time">08:00-09:00</span>' in grid_html
    assert "僑泰國二7(代課)" in grid_html
    assert grid_html.index("08:00-09:00") < grid_html.index("09:00-10:00")
    assert "08:00-09:00（自訂時段）" in month_html
    assert "時段：08:00-09:00" in summary_html
    assert "08:00-09:00（自訂時段）" in summary_html


def test_grid_integrity_guard_rejects_a_dropped_lesson():
    lesson_date = date(2031, 2, 3)
    lessons = [{"id": "L1"}, {"id": "L2"}]
    grid = {
        lesson_date: {
            "08:00-09:00": ["A"],
            "09:00-10:00": ["B"],
        },
    }

    with pytest.raises(RuntimeError, match="應顯示 2 堂.*實際只納入 1 堂"):
        _assert_grid_complete(lessons, grid, ["09:00-10:00"])
