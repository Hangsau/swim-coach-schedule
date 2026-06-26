#!/usr/bin/env python3
"""
render_html.py — 把 schedule.yaml 渲染成行事曆風格的 HTML 靜態網頁

Usage:
  python3 scripts/render_html.py [--out docs/index.html] [--month 2026-07]
"""
import argparse
import calendar
import sys
import yaml
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data" / "schedule.yaml"

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_NAMES_ZH = {
    "mon": "週一", "tue": "週二", "wed": "週三", "thu": "週四",
    "fri": "週五", "sat": "週六", "sun": "週日"
}
DAY_NAMES_ZH_FULL = {
    "mon": "星期一", "tue": "星期二", "wed": "星期三", "thu": "星期四",
    "fri": "星期五", "sat": "星期六", "sun": "星期日"
}


def _to_date(d):
    if hasattr(d, "hour"):
        return d.date()
    if hasattr(d, "year") and hasattr(d, "month") and hasattr(d, "day"):
        return d
    return datetime.strptime(str(d), "%Y-%m-%d").date()


def load():
    return yaml.safe_load(DATA.read_text(encoding="utf-8"))


def expand_schedule(schedules, slots_by_id, classes_by_id):
    expanded = []
    for s in schedules:
        start = _to_date(s["start_date"])
        end = start + timedelta(weeks=s["duration_weeks"])
        slot = slots_by_id.get(s["slot_id"], {})
        cls = classes_by_id.get(s["class_id"], {})
        target_day = DAY_NAMES.index(s["day"])
        days_ahead = (target_day - start.weekday()) % 7
        first_date = start + timedelta(days=days_ahead)
        current = first_date
        while current < end:
            expanded.append({
                "date": current,
                "day": s["day"],
                "slot_id": s["slot_id"],
                "slot_time": slot.get("time", "?"),
                "slot_note": slot.get("note", ""),
                "class_id": s["class_id"],
                "class_name": cls.get("name", "?"),
                "level": cls.get("level", ""),
                "note": s.get("note", ""),
            })
            current += timedelta(days=7)
    return expanded


def render_month(data, year, month):
    """渲染月曆 view。"""
    slots_by_id = {s["id"]: s for s in data.get("slots", [])}
    classes_by_id = {c["id"]: c for c in data.get("classes", [])}
    all_lessons = expand_schedule(data.get("schedules", []), slots_by_id, classes_by_id)

    # 該月所有 lesson
    month_lessons = [l for l in all_lessons if l["date"].year == year and l["date"].month == month]

    # 按日期分組
    by_date = defaultdict(list)
    for l in month_lessons:
        by_date[l["date"]].append(l)
    for d in by_date:
        by_date[d].sort(key=lambda l: l["slot_time"])

    # 行事曆結構
    cal = calendar.Calendar(firstweekday=0)  # 週一開始
    weeks = cal.monthdayscalendar(year, month)

    html = ['<!DOCTYPE html>',
            '<html lang="zh-TW">',
            '<head>',
            '<meta charset="UTF-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
            f'<title>課表 — {year} 年 {month} 月</title>',
            '<style>',
            CSS,
            '</style>',
            '</head>',
            '<body>',
            '<header>',
            f'<h1>🏊 課表</h1>',
            f'<div class="month-nav">',
            f'<a href="?month={year}-{month-1:02d}">← 上一月</a>',
            f'<span class="current">{year} 年 {month} 月</span>',
            f'<a href="?month={year}-{month+1:02d}">下一月 →</a>',
            '</div>',
            '</header>',
            '<main>',
            '<table class="calendar">',
            '<thead><tr>',
    ]
    for d in DAY_NAMES_ZH.values():
        html.append(f'<th>{d}</th>')
    html.append('</tr></thead><tbody>')

    today = date.today()
    for week in weeks:
        html.append('<tr>')
        for i, day in enumerate(week):
            if day == 0:
                html.append('<td class="empty"></td>')
            else:
                d = date(year, month, day)
                is_today = d == today
                cls = "today" if is_today else ""
                html.append(f'<td class="{cls}">')
                html.append(f'<div class="day-num">{day}</div>')
                if d in by_date:
                    for l in by_date[d]:
                        html.append('<div class="lesson">')
                        html.append(f'<div class="time">{l["slot_time"]}</div>')
                        html.append(f'<div class="class">{l["class_name"]}</div>')
                        html.append('</div>')
                html.append('</td>')
        html.append('</tr>')

    html.append('</tbody></table>')
    html.append('</main>')
    html.append('<footer>')
    html.append(f'<p>更新時間：{datetime.now().strftime("%Y-%m-%d %H:%M")}</p>')
    html.append('</footer>')
    html.append('</body></html>')

    return "\n".join(html)


CSS = """
:root {
  --bg: #faf8f8;
  --fg: #2b2b2b;
  --accent: #284b63;
  --today: #fff23688;
  --lesson-bg: #e8f0f5;
  --border: #e5e5e5;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans TC", "微軟正黑體", sans-serif;
  background: var(--bg);
  color: var(--fg);
  line-height: 1.5;
  padding: 20px;
  max-width: 1100px;
  margin: 0 auto;
}
header {
  margin-bottom: 30px;
  border-bottom: 2px solid var(--accent);
  padding-bottom: 20px;
}
h1 { color: var(--accent); font-size: 28px; margin-bottom: 16px; }
.month-nav {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 18px;
}
.month-nav a {
  color: var(--accent);
  text-decoration: none;
  padding: 8px 16px;
  border: 1px solid var(--accent);
  border-radius: 6px;
  font-size: 14px;
}
.month-nav a:hover { background: var(--accent); color: white; }
.month-nav .current { font-weight: bold; font-size: 24px; }
table.calendar {
  width: 100%;
  border-collapse: collapse;
  background: white;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}
table.calendar th, table.calendar td {
  border: 1px solid var(--border);
  padding: 8px;
  vertical-align: top;
  height: 110px;
  width: 14.28%;
}
table.calendar th {
  background: var(--accent);
  color: white;
  text-align: center;
  height: auto;
  padding: 12px;
  font-weight: normal;
}
table.calendar td.empty { background: #f5f5f5; }
table.calendar td.today { background: var(--today); }
table.calendar td .day-num {
  font-weight: bold;
  font-size: 14px;
  margin-bottom: 4px;
}
table.calendar td .lesson {
  background: var(--lesson-bg);
  border-left: 3px solid var(--accent);
  padding: 4px 6px;
  margin-bottom: 4px;
  font-size: 12px;
  border-radius: 0 4px 4px 0;
}
table.calendar td .lesson .time {
  font-weight: bold;
  color: var(--accent);
  font-size: 11px;
}
table.calendar td .lesson .class {
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
footer {
  margin-top: 30px;
  text-align: center;
  color: #888;
  font-size: 13px;
}
@media (max-width: 768px) {
  body { padding: 10px; }
  table.calendar th, table.calendar td { padding: 4px; height: 80px; }
  table.calendar td .day-num { font-size: 12px; }
  table.calendar td .lesson { font-size: 10px; padding: 2px 4px; }
  h1 { font-size: 20px; }
  .month-nav .current { font-size: 16px; }
}
"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(ROOT / "docs" / "index.html"))
    p.add_argument("--month", help="YYYY-MM 格式")
    args = p.parse_args()

    data = load()

    if args.month:
        year, month = map(int, args.month.split("-"))
    else:
        today = date.today()
        year, month = today.year, today.month

    html = render_month(data, year, month)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"✓ 渲染完成：{out}（{len(html)} chars）")
    print(f"  月份：{year}-{month:02d}")
    print(f"  查看：file://{out.absolute()}")


if __name__ == "__main__":
    main()