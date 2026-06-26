#!/usr/bin/env python3
"""
query.py — 游泳教練課表查詢工具

Usage:
  python3 query.py today
  python3 query.py week
  python3 query.py month
  python3 query.py day 2026-07-15
  python3 query.py class C01
  python3 query.py slot morning-2
"""
import sys
import yaml
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data" / "schedule.yaml"

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_NAMES_ZH = {
    "mon": "週一", "tue": "週二", "wed": "週三", "thu": "週四",
    "fri": "週五", "sat": "週六", "sun": "週日"
}


def load():
    return yaml.safe_load(DATA.read_text(encoding="utf-8"))


def _to_date(d):
    """YAML 會自動把 date 字串轉成 datetime.date，這裡做兼容。"""
    if hasattr(d, "year") and hasattr(d, "month") and hasattr(d, "day") and hasattr(d, "hour"):
        return d.date()
    if hasattr(d, "year") and hasattr(d, "month") and hasattr(d, "day"):
        return d
    return datetime.strptime(str(d), "%Y-%m-%d").date()


def expand_schedule(schedules, slots_by_id, classes_by_id):
    """展開 schedule → 具體日期清單

    兩種模式：
      - specific_dates: 直接展開成給定清單（一次性的課）
      - day + start_date + duration_weeks: 自動展開每週那個 day 的課
    """
    expanded = []
    for s in schedules:
        slot = slots_by_id.get(s["slot_id"], {})
        cls = classes_by_id.get(s["class_id"], {})

        if "specific_dates" in s:
            # 一次性指定日期
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
            # 重複模式（每週）
            start = _to_date(s["start_date"])
            # end_date 優先，否則用 duration_weeks 算
            if "end_date" in s:
                end = _to_date(s["end_date"]) + timedelta(days=1)  # 含當天
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


def print_lessons(lessons, title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")
    if not lessons:
        print("\n  （沒有課）\n")
        return
    # 按日期 + 時段排序
    lessons.sort(key=lambda l: (l["date"], l["slot_time"]))
    current_date = None
    for l in lessons:
        if l["date"] != current_date:
            current_date = l["date"]
            print(f"\n📅 {current_date} ({DAY_NAMES_ZH[l['day']]})")
        print(f"   {l['slot_time']:12s}  {l['class_name']:30s}  {l['level']}")
        if l["note"]:
            print(f"     └─ {l['note']}")
    print()


def cmd_today():
    data = load()
    slots_by_id = {s["id"]: s for s in data.get("slots", [])}
    classes_by_id = {c["id"]: c for c in data.get("classes", [])}
    all_lessons = expand_schedule(data.get("schedules", []), slots_by_id, classes_by_id)
    today_lessons = [l for l in all_lessons if l["date"] == date.today()]
    print_lessons(today_lessons, f"今日 ({date.today()})")


def cmd_week():
    data = load()
    slots_by_id = {s["id"]: s for s in data.get("slots", [])}
    classes_by_id = {c["id"]: c for c in data.get("classes", [])}
    all_lessons = expand_schedule(data.get("schedules", []), slots_by_id, classes_by_id)
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    week_lessons = [l for l in all_lessons if monday <= l["date"] <= sunday]
    print_lessons(week_lessons, f"本週 ({monday} ~ {sunday})")


def cmd_month():
    data = load()
    slots_by_id = {s["id"]: s for s in data.get("slots", [])}
    classes_by_id = {c["id"]: c for c in data.get("classes", [])}
    all_lessons = expand_schedule(data.get("schedules", []), slots_by_id, classes_by_id)
    today = date.today()
    month_lessons = [l for l in all_lessons if l["date"].year == today.year and l["date"].month == today.month]
    print_lessons(month_lessons, f"本月 ({today.year}-{today.month:02d})")


def cmd_day(target_date_str):
    data = load()
    slots_by_id = {s["id"]: s for s in data.get("slots", [])}
    classes_by_id = {c["id"]: c for c in data.get("classes", [])}
    all_lessons = expand_schedule(data.get("schedules", []), slots_by_id, classes_by_id)
    target = _to_date(target_date_str)
    day_lessons = [l for l in all_lessons if l["date"] == target]
    print_lessons(day_lessons, f"{target_date_str} ({DAY_NAMES_ZH[DAY_NAMES[target.weekday()]]})")


def cmd_class(class_id):
    data = load()
    slots_by_id = {s["id"]: s for s in data.get("slots", [])}
    classes_by_id = {c["id"]: c for c in data.get("classes", [])}
    all_lessons = expand_schedule(data.get("schedules", []), slots_by_id, classes_by_id)
    cls_lessons = [l for l in all_lessons if l["class_id"] == class_id]
    cls = classes_by_id.get(class_id, {})
    title = f"{cls.get('name', class_id)}（{cls.get('level', '')}）"
    print_lessons(cls_lessons, title)


def cmd_slot(slot_id):
    data = load()
    slots_by_id = {s["id"]: s for s in data.get("slots", [])}
    classes_by_id = {c["id"]: c for c in data.get("classes", [])}
    all_lessons = expand_schedule(data.get("schedules", []), slots_by_id, classes_by_id)
    slot_lessons = [l for l in all_lessons if l["slot_id"] == slot_id]
    slot = slots_by_id.get(slot_id, {})
    title = f"{slot.get('time', '?')}（{slot.get('note', '')}）"
    print_lessons(slot_lessons, title)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd == "today":
        cmd_today()
    elif cmd == "week":
        cmd_week()
    elif cmd == "month":
        cmd_month()
    elif cmd == "day" and len(sys.argv) >= 3:
        cmd_day(sys.argv[2])
    elif cmd == "class" and len(sys.argv) >= 3:
        cmd_class(sys.argv[2])
    elif cmd == "slot" and len(sys.argv) >= 3:
        cmd_slot(sys.argv[2])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()