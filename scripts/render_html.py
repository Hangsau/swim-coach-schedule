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
        slot = slots_by_id.get(s["slot_id"], {})
        cls = classes_by_id.get(s["class_id"], {})

        if "specific_dates" in s:
            for ds in s["specific_dates"]:
                current = _to_date(ds)
                expanded.append({
                    "date": current,
                    "day": DAY_NAMES[current.weekday()],
                    "slot_id": s["slot_id"],
                    "slot_time": slot.get("time", "?"),
                    "slot_note": slot.get("note", ""),
                    "class_id": s["class_id"],
                    "class_name": cls.get("name", "?"),
                    "level": cls.get("level", ""),
                    "note": s.get("note", ""),
                })
        else:
            start = _to_date(s["start_date"])
            if "end_date" in s:
                end = _to_date(s["end_date"]) + timedelta(days=1)
            else:
                end = start + timedelta(weeks=s["duration_weeks"])
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
            f'<a href="index.html">← 全部月份</a>',
            f'<span class="current">{year} 年 {month} 月</span>',
            f'<a href="{year}-{month+1:02d}.html">下一月 →</a>',
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
main h2 {
  margin-top: 30px;
  margin-bottom: 12px;
  color: var(--accent);
  border-bottom: 1px solid var(--border);
  padding-bottom: 6px;
}
table.calendar th, table.calendar td { padding: 8px; vertical-align: top; }
table.calendar th { background: var(--accent); color: white; }
.month-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 12px;
  margin-top: 16px;
}
.month-link a {
  display: block;
  padding: 16px;
  background: white;
  border: 1px solid var(--border);
  border-radius: 6px;
  text-align: center;
  color: var(--accent);
  text-decoration: none;
  font-weight: bold;
}
.month-link a:hover { background: var(--accent); color: white; }
@media (max-width: 768px) {
  body { padding: 10px; }
  table.calendar th, table.calendar td { padding: 4px; height: 80px; }
  table.calendar td .day-num { font-size: 12px; }
  table.calendar td .lesson { font-size: 10px; padding: 2px 4px; }
  h1 { font-size: 20px; }
  .month-nav .current { font-size: 16px; }
}
"""


def render_index(data, available_months):
    """渲染首頁：列出所有月份 + 從今天起的課程列表"""
    slots_by_id = {s["id"]: s for s in data.get("slots", [])}
    classes_by_id = {c["id"]: c for c in data.get("classes", [])}
    all_lessons = expand_schedule(data.get("schedules", []), slots_by_id, classes_by_id)

    today = date.today()
    future_lessons = [l for l in all_lessons if l["date"] >= today]
    future_lessons.sort(key=lambda l: (l["date"], l["slot_time"]))

    links = []
    for y, m in available_months:
        links.append(f'<a href="{y}-{m:02d}.html">{y} 年 {m} 月</a>')

    html = ['<!DOCTYPE html>',
            '<html lang="zh-TW">',
            '<head>',
            '<meta charset="UTF-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
            '<title>游泳教練課表</title>',
            '<style>',
            CSS,
            '</style>',
            '</head>',
            '<body>',
            '<header>',
            '<h1>🏊 課表</h1>',
            '<div class="month-nav">',
    ]
    html.append('<span class="current">全部月份</span>')
    html.append('</div></header>')
    html.append('<main>')

    if future_lessons:
        html.append('<h2>從今天起的課程</h2>')
        html.append('<table class="calendar"><thead><tr><th>日期</th><th>時間</th><th>學員</th><th>備註</th></tr></thead><tbody>')
        current_date = None
        for l in future_lessons:
            html.append('<tr>')
            html.append(f'<td>{l["date"]} ({DAY_NAMES_ZH[l["day"]]})</td>')
            html.append(f'<td>{l["slot_time"]}</td>')
            html.append(f'<td>{l["class_name"]}</td>')
            html.append(f'<td>{l["note"]}</td>')
            html.append('</tr>')
        html.append('</tbody></table>')
    else:
        html.append('<p style="text-align:center;color:#888;padding:40px;">目前沒有安排課程</p>')

    html.append('<h2>月曆 view</h2>')
    html.append('<div class="month-list">')
    for link in links:
        html.append(f'<div class="month-link">{link}</div>')
    html.append('</div>')

    html.append('</main>')
    html.append(f'<footer><p>更新時間：{datetime.now().strftime("%Y-%m-%d %H:%M")}</p></footer>')
    html.append('</body></html>')
    return "\n".join(html)


def collect_months_with_data(data):
    """找出 schedule 涵蓋的所有月份"""
    slots_by_id = {s["id"]: s for s in data.get("slots", [])}
    classes_by_id = {c["id"]: c for c in data.get("classes", [])}
    all_lessons = expand_schedule(data.get("schedules", []), slots_by_id, classes_by_id)

    months = set()
    for l in all_lessons:
        months.add((l["date"].year, l["date"].month))
    # 加上當月 + 上下月
    today = date.today()
    for offset in [-1, 0, 1]:
        m = today.month + offset
        y = today.year
        if m < 1:
            m = 12 + m
            y -= 1
        elif m > 12:
            m = m - 12
            y += 1
        months.add((y, m))
    return sorted(months)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(ROOT / "docs" / "index.html"))
    p.add_argument("--month", help="YYYY-MM 格式（單月）")
    args = p.parse_args()

    data = load()
    months = collect_months_with_data(data)

    if args.month:
        year, month = map(int, args.month.split("-"))
        html = render_month(data, year, month)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        print(f"✓ 渲染完成：{out}（{len(html)} chars）")
        print(f"  月份：{year}-{month:02d}")
    else:
        # 渲染 index（首頁 + 所有月份連結）
        docs_dir = Path(args.out).parent

        # 每個月單獨 HTML
        for y, m in months:
            month_html = render_month(data, y, m)
            month_path = docs_dir / f"{y}-{m:02d}.html"
            month_path.write_text(month_html, encoding="utf-8")
            print(f"  ✓ {y}-{m:02d}.html ({len(month_html)} chars)")

        # index.html
        index_html = render_index(data, months)
        out = Path(args.out)
        out.write_text(index_html, encoding="utf-8")
        print(f"\n✓ index.html ({len(index_html)} chars)")
        print(f"  共 {len(months)} 個月")


if __name__ == "__main__":
    main()