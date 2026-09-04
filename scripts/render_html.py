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

# Windows console（cp950）print ✓ 會 crash，強制 UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

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


def expand_schedule(schedules, slots_by_id, classes_by_id, data=None):
    """直接用 query.py 的 expand_schedule（單一來源，避免 drift）"""
    import sys
    if "scripts" not in sys.path:
        sys.path.insert(0, str(Path(__file__).parent))
    from query import expand_schedule as _expand
    return _expand(schedules, slots_by_id, classes_by_id, data=data)


def _time_sort_key(time_value):
    """時段以開始時間排序；同時適用常用 slot 與無 slot_id 的臨時課。"""
    return str(time_value or "99:99-99:99").split("-")[0].strip()


def _slots_grouped_by_time(slots_by_id):
    """回傳 time -> [(slot_id, slot)]，供實際課次時間與常用 slot 對齊。"""
    grouped = defaultdict(list)
    for sid, slot in slots_by_id.items():
        time_value = slot.get("time")
        if time_value:
            grouped[time_value].append((sid, slot))
    return grouped


def _time_description(time_value, slots_by_time):
    """顯示實際時間；無常用 slot 對應時明標「自訂時段」。"""
    matches = slots_by_time.get(time_value) or []
    if not matches:
        return f"{time_value}（自訂時段）"
    ids = "/".join(sid for sid, _ in matches)
    notes = " / ".join(dict.fromkeys(
        slot.get("note", "") for _, slot in matches if slot.get("note")))
    note_text = f" {notes}" if notes else ""
    return f"{time_value}{note_text}（{ids}）"


def _assert_grid_complete(month_lessons, grid, time_values):
    """上線安全門：grid 漏掉任何課次就讓 render/CI 失敗，不部署殘缺頁。"""
    rendered_count = sum(
        len(by_time.get(time_value, []))
        for by_time in grid.values()
        for time_value in time_values
    )
    expected_count = len(month_lessons)
    if rendered_count != expected_count:
        raise RuntimeError(
            f"grid 課次完整性失敗：應顯示 {expected_count} 堂，"
            f"實際只納入 {rendered_count} 堂")


def render_month(data, year, month):
    """渲染月曆 view。"""
    slots_by_id = {s["id"]: s for s in data.get("slots", [])}
    classes_by_id = {c["id"]: c for c in data.get("classes", [])}
    all_lessons = expand_schedule(data.get("schedules", []), slots_by_id, classes_by_id, data=data)

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
            '<meta name="viewport" content="width=device-width, initial-scale=1.0, minimum-scale=0.3, maximum-scale=3.0">',
            f'<title>課表 — {year} 年 {month} 月</title>',
            '<style>',
            CSS,
            '</style>',
            '</head>',
            '<body>',
            '<header>',
            f'<h1>🏊 課表</h1>',
            f'<div class="month-nav">',
    ]
    # 上一月（跨年處理）
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    # 下一月（跨年處理）
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1
    # 算 available_months（這個 render 範圍內所有 schedule 涵蓋的月）
    _available = set()
    for _l in all_lessons:
        _available.add((_l['date'].year, _l['date'].month))
    _available.add((year, month))  # 至少自己
    if (prev_year, prev_month) in _available:
        html.append(f'<a href="{prev_year}-{prev_month:02d}.html">← 上一月</a>')
    html.append('<a href="index.html">全部</a>')
    html.append(f'<span class="current">{year} 年 {month} 月</span>')
    if (next_year, next_month) in _available:
        html.append(f'<a href="{next_year}-{next_month:02d}.html">下一月 →</a>')
    html.append(f'<a href="grid-{year}-{month:02d}.html">↳ grid</a>')
    html.append('<a href="summary.html">學員總表</a>')
    html.append('</div>')
    html.append('</header>')
    html.append('<main>')
    html.append('<table class="calendar">')
    html.append('<thead><tr>')
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
                # 該日堂數標籤
                if d in by_date and by_date[d]:
                    html.append(f'<span class="day-count">{len(by_date[d])} 堂</span>')
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

    # 月統計
    from collections import Counter
    time_count = Counter()
    class_count = Counter()
    for day_lessons in by_date.values():
        for l in day_lessons:
            time_count[l["slot_time"]] += 1
            class_count[l["class_name"]] += 1
    total = sum(time_count.values())

    html.append('<aside class="month-stats">')
    html.append(f'<h2>📊 {year} 年 {month} 月統計</h2>')
    html.append(f'<div class="stat-total">總堂數：<strong>{total}</strong> 堂</div>')
    html.append('<table class="calendar"><thead><tr><th>時段</th><th>堂數</th></tr></thead><tbody>')
    slots_by_time = _slots_grouped_by_time(slots_by_id)
    for time_value in sorted(time_count, key=_time_sort_key):
        html.append(
            f'<tr><td>{_time_description(time_value, slots_by_time)}</td>'
            f'<td>{time_count[time_value]}</td></tr>')
    html.append('</tbody></table>')
    html.append('<table class="calendar"><thead><tr><th>學員</th><th>堂數</th></tr></thead><tbody>')
    for cname in sorted(class_count.keys()):
        html.append(f'<tr><td>{cname}</td><td>{class_count[cname]}</td></tr>')
    html.append('</tbody></table>')
    html.append('</aside>')

    html.append('<footer>')
    html.append(f'<p>更新時間：{datetime.now().strftime("%Y-%m-%d")}</p>')
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
.day-count {
  display: inline-block;
  font-size: 11px;
  background: #e0e8f0;
  color: var(--accent);
  padding: 1px 6px;
  border-radius: 8px;
  margin-bottom: 4px;
  font-weight: bold;
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
aside.month-stats {
  background: white;
  padding: 16px 20px;
  margin-top: 30px;
  border: 1px solid var(--border);
  border-radius: 6px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}
aside.month-stats h2 { margin-top: 0; }
.stat-total {
  font-size: 18px;
  padding: 12px;
  background: var(--accent);
  color: white;
  border-radius: 6px;
  margin-bottom: 16px;
  text-align: center;
}
.stat-total strong { font-size: 24px; }
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
.summary-block {
  background: white;
  padding: 16px;
  margin-bottom: 20px;
  border: 1px solid var(--border);
  border-radius: 6px;
  box-shadow: 0 1px 2px rgba(0,0,0,0.05);
}
.summary-block h2 {
  color: var(--accent);
  font-size: 18px;
  margin-bottom: 8px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.summary-block .count {
  font-size: 14px;
  background: var(--accent);
  color: white;
  padding: 4px 10px;
  border-radius: 12px;
}
.summary-block .meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 14px;
  color: #555;
  margin-bottom: 12px;
}
.summary-block .meta span { background: #f5f5f5; padding: 4px 10px; border-radius: 4px; }
.summary-block.total {
  background: #faf8e0;
  border-color: var(--accent);
}
.summary-block table { width: 100%; font-size: 13px; }
.summary-block table th,
.summary-block table td {
  padding: 6px 10px;
  border-bottom: 1px solid #eee;
  text-align: left;
}
.summary-block table th { background: #f5f5f5; font-weight: bold; }
/* ---- 首頁：快速導航 + 接下來 7 天 + 收合完整清單 ---- */
.quick-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 20px;
}
.quick-nav a {
  flex: 1 1 130px;
  text-align: center;
  padding: 16px 12px;
  border: 1px solid var(--accent);
  border-radius: 6px;
  color: var(--accent);
  text-decoration: none;
  font-weight: bold;
  font-size: 16px;
  background: white;
}
.quick-nav a.primary {
  flex: 2 1 200px;
  background: var(--accent);
  color: white;
}
.quick-nav a:hover { background: var(--accent); color: white; }
.upcoming { margin-bottom: 8px; }
.day-group {
  background: white;
  border: 1px solid var(--border);
  border-radius: 6px;
  margin-bottom: 10px;
  overflow: hidden;
  box-shadow: 0 1px 2px rgba(0,0,0,0.05);
}
.day-head {
  background: #f0f4f8;
  padding: 8px 14px;
  font-weight: bold;
  color: var(--accent);
  border-bottom: 1px solid var(--border);
}
.day-head .wd { color: #888; font-weight: normal; font-size: 13px; margin-left: 8px; }
.up-lesson {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid #f2f2f2;
}
.up-lesson:last-child { border-bottom: none; }
.up-time { font-weight: bold; color: var(--accent); font-size: 15px; min-width: 100px; }
.up-name { font-size: 16px; }
.up-note { color: #888; font-size: 13px; }
details.full-list {
  margin-top: 24px;
  background: white;
  border: 1px solid var(--border);
  border-radius: 6px;
}
details.full-list summary {
  cursor: pointer;
  padding: 14px 16px;
  font-weight: bold;
  color: var(--accent);
  font-size: 15px;
}
details.full-list[open] summary { border-bottom: 1px solid var(--border); }
details.full-list table.calendar { border: none; box-shadow: none; }
@media (max-width: 768px) {
  body { padding: 10px; }
  table.calendar th, table.calendar td { padding: 4px; height: 80px; }
  table.calendar td .day-num { font-size: 12px; }
  table.calendar td .lesson { font-size: 10px; padding: 2px 4px; }
  h1 { font-size: 20px; }
  .month-nav .current { font-size: 16px; }
  .up-time { min-width: 80px; font-size: 14px; }
  .up-name { font-size: 15px; }
}
"""


def render_index(data, available_months):
    """渲染首頁：頂部快速導航 → 接下來 7 天 → 完整未來課程（收合）"""
    slots_by_id = {s["id"]: s for s in data.get("slots", [])}
    classes_by_id = {c["id"]: c for c in data.get("classes", [])}
    all_lessons = expand_schedule(data.get("schedules", []), slots_by_id, classes_by_id, data=data)

    today = date.today()
    future_lessons = [l for l in all_lessons if l["date"] >= today]
    future_lessons.sort(key=lambda l: (l["date"], l["slot_time"]))

    cur_y, cur_m = today.year, today.month
    week_end = today + timedelta(days=6)
    soon_lessons = [l for l in future_lessons if l["date"] <= week_end]

    html = ['<!DOCTYPE html>',
            '<html lang="zh-TW">',
            '<head>',
            '<meta charset="UTF-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1.0, minimum-scale=0.3, maximum-scale=3.0">',
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
    html.append('<span class="current">首頁</span>')
    html.append('</div></header>')
    html.append('<main>')

    # 頂部快速導航（一進來就能進月曆）
    html.append('<nav class="quick-nav">')
    html.append(f'<a class="primary" href="{cur_y}-{cur_m:02d}.html">📅 本月月曆</a>')
    html.append(f'<a href="grid-{cur_y}-{cur_m:02d}.html">▦ 本月每日 grid</a>')
    html.append('<a href="summary.html">📋 學員總表</a>')
    html.append('</nav>')

    # 各月連結
    html.append('<div class="month-list">')
    for y, m in available_months:
        html.append(f'<div class="month-link"><a href="{y}-{m:02d}.html">{y} 年 {m} 月</a></div>')
    html.append('</div>')

    # 接下來 7 天
    html.append('<h2>接下來 7 天</h2>')
    if soon_lessons:
        html.append('<div class="upcoming">')
        cur = None
        for l in soon_lessons:
            if l["date"] != cur:
                if cur is not None:
                    html.append('</div>')  # 收前一天
                cur = l["date"]
                html.append('<div class="day-group">')
                html.append(f'<div class="day-head">{l["date"].strftime("%m/%d")}'
                            f'<span class="wd">{DAY_NAMES_ZH[l["day"]]}</span></div>')
            html.append('<div class="up-lesson">')
            html.append(f'<span class="up-time">{l["slot_time"]}</span>')
            html.append(f'<span class="up-name">{l["class_name"]}</span>')
            if l["note"]:
                html.append(f'<span class="up-note">{l["note"]}</span>')
            html.append('</div>')
        html.append('</div>')  # 收最後一天
        html.append('</div>')  # 收 upcoming
    else:
        html.append('<p style="color:#888;padding:20px;">最近 7 天沒有課</p>')

    # 完整未來課程（預設收合）
    if future_lessons:
        html.append('<details class="full-list">')
        html.append(f'<summary>完整未來課程（共 {len(future_lessons)} 堂，點開看）</summary>')
        html.append('<table class="calendar"><thead><tr><th>日期</th><th>時間</th><th>學員</th><th>備註</th></tr></thead><tbody>')
        for l in future_lessons:
            html.append('<tr>')
            html.append(f'<td>{l["date"]} ({DAY_NAMES_ZH[l["day"]]})</td>')
            html.append(f'<td>{l["slot_time"]}</td>')
            html.append(f'<td>{l["class_name"]}</td>')
            html.append(f'<td>{l["note"]}</td>')
            html.append('</tr>')
        html.append('</tbody></table>')
        html.append('</details>')

    html.append('</main>')
    html.append(f'<footer><p>更新時間：{datetime.now().strftime("%Y-%m-%d")}</p></footer>')
    html.append('</body></html>')
    return "\n".join(html)


def collect_months_with_data(data):
    """找出 schedule 涵蓋的所有月份"""
    slots_by_id = {s["id"]: s for s in data.get("slots", [])}
    classes_by_id = {c["id"]: c for c in data.get("classes", [])}
    all_lessons = expand_schedule(data.get("schedules", []), slots_by_id, classes_by_id, data=data)

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


def render_summary(data):
    """渲染學員總表頁面"""
    from collections import Counter

    slots_by_id = {s["id"]: s for s in data.get("slots", [])}
    classes_by_id = {c["id"]: c for c in data.get("classes", [])}
    all_lessons = expand_schedule(data.get("schedules", []), slots_by_id, classes_by_id, data=data)

    html = ['<!DOCTYPE html>',
            '<html lang="zh-TW">',
            '<head>',
            '<meta charset="UTF-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1.0, minimum-scale=0.3, maximum-scale=3.0">',
            '<title>學員總表 — 游泳教練課表</title>',
            '<style>',
            CSS,
            '</style>',
            '</head>',
            '<body>',
            '<header>',
            '<h1>📋 學員總表</h1>',
            '<div class="month-nav">',
            '<a href="index.html">← 回首頁</a>',
            '<span class="current">學員總覽</span>',
            '<a href="2026-07.html">→ 月曆（7 月）</a>',
            '<a href="grid-2026-07.html">→ grid（7 月）</a>',
            '</div>',
            '</header>',
            '<main>',
    ]

    total = 0
    for cls_id in classes_by_id:
        cls = classes_by_id[cls_id]
        lessons = sorted([l for l in all_lessons if l["class_id"] == cls_id], key=lambda x: x["date"])
        if not lessons:
            continue
        lesson_times = sorted(
            set(l["slot_time"] for l in lessons), key=_time_sort_key)
        days_count = Counter(l["day"] for l in lessons)
        days_str = "、".join(f"{DAY_NAMES_ZH[d]}{c}" for d, c in sorted(days_count.items()))

        html.append('<div class="summary-block">')
        html.append(f'<h2>{cls["name"]} <span class="count">{len(lessons)} 堂</span></h2>')
        html.append('<div class="meta">')
        html.append(f'<span>時段：{"、".join(lesson_times)}</span>')
        html.append(f'<span>每週：{days_str}</span>')
        html.append(f'<span>開始：{lessons[0]["date"]}（{DAY_NAMES_ZH[lessons[0]["day"]]}）</span>')
        html.append(f'<span>結束：{lessons[-1]["date"]}（{DAY_NAMES_ZH[lessons[-1]["day"]]}）</span>')
        html.append('</div>')
        html.append('<table class="calendar"><thead><tr><th>#</th><th>日期</th><th>星期</th></tr></thead><tbody>')
        for i, l in enumerate(lessons, 1):
            html.append(f'<tr><td>{i}</td><td>{l["date"]}</td><td>{DAY_NAMES_ZH[l["day"]]}</td></tr>')
        html.append('</tbody></table>')
        html.append('</div>')

        total += len(lessons)

    html.append('<div class="summary-block total">')
    html.append(f'<h2>總計 {total} 堂</h2>')

    time_count = Counter(l["slot_time"] for l in all_lessons)
    html.append('<table class="calendar"><thead><tr><th>時段</th><th>堂數</th></tr></thead><tbody>')
    slots_by_time = _slots_grouped_by_time(slots_by_id)
    for time_value in sorted(time_count, key=_time_sort_key):
        html.append(
            f'<tr><td>{_time_description(time_value, slots_by_time)}</td>'
            f'<td>{time_count[time_value]}</td></tr>')
    html.append('</tbody></table>')
    html.append('</div>')

    html.append('</main>')
    html.append(f'<footer><p>更新時間：{datetime.now().strftime("%Y-%m-%d")}</p></footer>')
    html.append('</body></html>')
    return "\n".join(html)


def render_grid(data, year, month):
    """渲染 daily grid view：每天一列 × 每個實際時間一欄。

    常用 slot 與無 slot_id 的臨時／自訂時段都必須顯示；
    有課的格子顯示學員名，沒課留空。
    """
    slots_by_id = {s["id"]: s for s in data.get("slots", [])}
    classes_by_id = {c["id"]: c for c in data.get("classes", [])}
    all_lessons = expand_schedule(data.get("schedules", []), slots_by_id, classes_by_id, data=data)

    # 篩選該月
    month_lessons = [l for l in all_lessons if l["date"].year == year and l["date"].month == month]
    # 欄位以實際時間為 key：常用 slots 全保留，本月臨時時段動態加入。
    slots_by_time = _slots_grouped_by_time(slots_by_id)
    time_values = sorted(
        set(slots_by_time) | {l["slot_time"] for l in month_lessons},
        key=_time_sort_key)
    time_meta = []
    for time_value in time_values:
        matches = slots_by_time.get(time_value) or []
        heading = "/".join(sid for sid, _ in matches) if matches else "自訂"
        time_meta.append((time_value, heading))

    # 建立 lookup: date -> 實際 time -> list of class names
    grid = {}
    for l in month_lessons:
        grid.setdefault(l["date"], {}).setdefault(l["slot_time"], []).append(l["class_name"])

    # 不再允許「資料有課、grid 沒顯示」靜默上線。
    _assert_grid_complete(month_lessons, grid, time_values)

    # 用 calendar 找月最後一天
    import calendar as cal_mod
    _, last_day_num = cal_mod.monthrange(year, month)

    # 所有該月的日期（用來生成完整網格）
    all_dates = []
    for d_num in range(1, last_day_num + 1):
        all_dates.append(date(year, month, d_num))

    html = ['<!DOCTYPE html>',
            '<html lang="zh-TW">',
            '<head>',
            '<meta charset="UTF-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1.0, minimum-scale=0.3, maximum-scale=3.0">',
            f'<title>每日時段 grid — {year} 年 {month} 月</title>',
            '<style>',
            CSS,
            GRID_CSS,
            '</style>',
            '</head>',
            '<body>',
            '<header>',
            '<h1>📅 每日時段 grid</h1>',
            '<div class="month-nav">',
            '<a href="index.html">← 回首頁</a>',
            f'<span class="current">{year} 年 {month} 月</span>',
    ]
    # 上一月 / 下一月（邊界檢查）
    if month == 1:
        py, pm = year - 1, 12
    else:
        py, pm = year, month - 1
    if month == 12:
        ny, nm = year + 1, 1
    else:
        ny, nm = year, month + 1
    docs_dir = ROOT / "docs"
    if (docs_dir / f"grid-{py}-{pm:02d}.html").exists():
        html.append(f'<a href="grid-{py}-{pm:02d}.html">← 上一月</a>')
    html.append(f'<a href="{year}-{month:02d}.html">→ 月曆</a>')
    if (docs_dir / f"grid-{ny}-{nm:02d}.html").exists():
        html.append(f'<a href="grid-{ny}-{nm:02d}.html">下一月 →</a>')
    html.append('<a href="summary.html">學員總表</a>')
    html.append('</div></header>')
    html.append('<main>')

    html.append('<p class="hint">每列 = 一天。每欄 = 一個時段。空格 = 沒課。</p>')
    html.append('<div class="grid-wrapper">')
    html.append('<table class="grid">')
    # 表頭
    html.append('<thead><tr>')
    html.append('<th class="date-col">日期</th>')
    for time_value, heading in time_meta:
        description = _time_description(time_value, slots_by_time)
        html.append(
            f'<th title="{description}">{heading}<br>'
            f'<span class="th-time">{time_value}</span></th>')
    html.append('</tr></thead>')
    html.append('<tbody>')

    # 每行
    for d in all_dates:
        weekday = d.weekday()
        wd_zh = DAY_NAMES_ZH[DAY_NAMES[weekday]]
        html.append('<tr>')
        html.append(f'<td class="date-col">{d.strftime("%m/%d")}<br><span class="wd">{wd_zh}</span></td>')
        for time_value, _ in time_meta:
            if d in grid and time_value in grid[d]:
                # 列出所有學員（用 / 分隔）
                names = " / ".join(grid[d][time_value])
                html.append(f'<td class="filled" title="{names}">{names}</td>')
            else:
                html.append('<td class="empty"></td>')
        html.append('</tr>')

    html.append('</tbody>')

    # tfoot 合計行：每個時段欄底加總堂數
    from collections import Counter
    time_count = Counter()
    for l in month_lessons:
        time_count[l["slot_time"]] += 1
    total = sum(time_count.values())

    html.append('<tfoot>')
    html.append('<tr class="totals">')
    html.append(f'<td class="date-col">合計<br><span class="wd">{total} 堂</span></td>')
    for time_value, _ in time_meta:
        cnt = time_count.get(time_value, 0)
        html.append(f'<td class="total-cell">{cnt if cnt else ""}</td>')
    html.append('</tr>')
    html.append('</tfoot>')

    html.append('</table></div>')
    html.append('</main>')
    html.append(f'<footer><p>更新時間：{datetime.now().strftime("%Y-%m-%d")}</p></footer>')
    html.append('</body></html>')
    return "\n".join(html)


GRID_CSS = """
.grid-wrapper {
  overflow-x: auto;
  background: white;
  border: 1px solid var(--border);
  border-radius: 6px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}
table.grid {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
table.grid th, table.grid td {
  border: 1px solid var(--border);
  padding: 6px 4px;
  text-align: center;
  white-space: nowrap;
}
table.grid thead th {
  background: var(--accent);
  color: white;
  padding: 8px 4px;
  position: sticky;
  top: 0;
  z-index: 2;
  font-size: 11px;
}
table.grid thead th .th-time {
  font-size: 10px;
  opacity: 0.8;
}
table.grid .date-col {
  background: #f0f4f8;
  font-weight: bold;
  position: sticky;
  left: 0;
  z-index: 1;
}
table.grid .wd {
  font-size: 10px;
  color: #888;
  font-weight: normal;
}
table.grid td.empty {
  background: #fafafa;
}
table.grid td.filled {
  background: #e8f0f5;
  color: var(--accent);
  font-weight: 500;
}
table.grid tbody tr:hover {
  background: #f5f5f5;
}
table.grid tbody tr:hover td.empty {
  background: #e8e8e8;
}
table.grid tfoot tr.totals {
  background: var(--accent);
  color: white;
  font-weight: bold;
}
table.grid tfoot td {
  border-top: 2px solid var(--accent);
  padding: 8px 4px;
}
table.grid tfoot td.date-col {
  background: var(--accent);
  color: white;
}
table.grid tfoot td.total-cell {
  font-size: 14px;
}
table.grid tfoot td.date-col .wd {
  color: rgba(255,255,255,0.85);
  font-size: 11px;
}
.hint {
  font-size: 13px;
  color: #888;
  margin-bottom: 8px;
  padding: 8px 12px;
  background: #faf8e0;
  border-left: 4px solid var(--accent);
  border-radius: 4px;
}
@media (max-width: 768px) {
  table.grid { font-size: 9px; }
  table.grid th, table.grid td { padding: 3px 1px; }
  table.grid thead th .th-time { font-size: 8px; }
  /* 提示可 pinch-zoom（用瀏覽器 viewport 縮放） */
  .grid-wrapper::before {
    content: "↔ 可橫向滑動，或 pinch-zoom 看全貌";
    display: block;
    font-size: 11px;
    color: #888;
    padding: 6px;
    background: #f5f5f5;
    text-align: center;
  }
}
"""


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

            # grid view
            grid_html = render_grid(data, y, m)
            grid_path = docs_dir / f"grid-{y}-{m:02d}.html"
            grid_path.write_text(grid_html, encoding="utf-8")
            print(f"  ✓ grid-{y}-{m:02d}.html ({len(grid_html)} chars)")

        # index.html
        index_html = render_index(data, months)
        out = Path(args.out)
        out.write_text(index_html, encoding="utf-8")
        print(f"\n✓ index.html ({len(index_html)} chars)")

        # summary.html
        summary_html = render_summary(data)
        summary_path = docs_dir / "summary.html"
        summary_path.write_text(summary_html, encoding="utf-8")
        print(f"✓ summary.html ({len(summary_html)} chars)")

        print(f"  共 {len(months)} 個月")


if __name__ == "__main__":
    main()
