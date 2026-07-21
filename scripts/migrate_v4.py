#!/usr/bin/env python3
"""
migrate_v4.py — schedule.yaml v3 → v4 一次性遷移（冪等）

v3：schedules 用 pattern（day/days/specific_dates）+ except_dates，讀取時展開。
v4：頂層 lessons 明確課次清單（一堂一筆）；schedules 降級為分組 metadata。

Usage:
  python scripts/migrate_v4.py [--file path/to/schedule.yaml]

流程：
  1. schema_version == 4 → print「已是 v4」exit 0
  2. 用內嵌的 v3 展開器凍結副本展開全部 schedules → lessons
  3. makeups: makeup_schedule_id → makeup_lesson_id
  4. 等價自檢（v3 展開全集 vs lessons 全集，按 (class_id, date, time) 逐堂 diff）
  5. 備份 <原檔同目錄>/schedule.pre-v4.yaml → 寫檔 → 輸出統計
"""
import argparse
import os
import sys
import tempfile
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import yaml

# Windows cp950 console 對非 ASCII print 會 crash
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).parent.parent
DEFAULT_YAML = ROOT / "data" / "schedule.yaml"

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_ZH_CHAR = {
    "mon": "一", "tue": "二", "wed": "三", "thu": "四",
    "fri": "五", "sat": "六", "sun": "日",
}


def _to_date(d):
    """YAML 會自動把 date 字串轉成 datetime.date，這裡做兼容。"""
    if hasattr(d, "year") and hasattr(d, "month") and hasattr(d, "day") and hasattr(d, "hour"):
        return d.date()
    if hasattr(d, "year") and hasattr(d, "month") and hasattr(d, "day"):
        return d
    return datetime.strptime(str(d), "%Y-%m-%d").date()


# =====================================================================
# v3 展開器凍結副本（複製自 v3 scripts/query.py，不 import——遷移後
# query.py 已是 v4 新版，不再認得 pattern / except_dates）
# =====================================================================

def _v3_resolve_time(s, slot):
    """schedule.time 優先（凍結值）；無則查 slot.time（當下定義）"""
    if s.get("time"):
        return s["time"]
    return slot.get("time", "?")


def _v3_except_set(s):
    """schedule.except_dates 排除清單（move-lesson 用）"""
    raw = s.get("except_dates") or []
    out = set()
    for d in raw:
        try:
            out.add(_to_date(d))
        except Exception:
            pass
    return out


def v3_expand_schedule(schedules, slots_by_id, classes_by_id):
    """展開 schedule → 具體日期清單（v3 語意凍結副本）

    模式：
      - specific_dates: 直接展開成給定清單（一次性的課）
      - day + start_date + duration_weeks/end_date: 每週固定那天
      - days + start_date + total_lessons/end_date/duration_weeks: 多日固定

    schedule 可帶：
      - time（凍結時段，例如 "15:00-16:00"，優先於 slot.time）
      - except_dates（排除清單，move-lesson 用）
    """
    expanded = []
    for s in schedules:
        slot = slots_by_id.get(s.get("slot_id"), {}) if s.get("slot_id") else {}
        cls = classes_by_id.get(s["class_id"], {})
        slot_time = _v3_resolve_time(s, slot)
        slot_note = slot.get("note", "")
        except_dates = _v3_except_set(s)

        def _emit(current, day_label):
            if current in except_dates:
                return False
            expanded.append({
                "date": current,
                "day": day_label,
                "slot_id": s.get("slot_id"),
                "slot_time": slot_time,
                "slot_note": slot_note,
                "class_id": s["class_id"],
                "class_name": cls.get("name", "?"),
                "level": cls.get("level", ""),
                "note": s.get("note", ""),
                "schedule_id": s.get("id"),
            })
            return True

        if "specific_dates" in s:
            for ds in s["specific_dates"]:
                current = _to_date(ds)
                _emit(current, DAY_NAMES[current.weekday()])
        elif "day" in s:
            start = _to_date(s["start_date"])
            max_lessons = s.get("total_lessons", 99999)
            if "end_date" in s:
                end = _to_date(s["end_date"]) + timedelta(days=1)
            elif "duration_weeks" in s:
                end = start + timedelta(weeks=s["duration_weeks"])
            elif "total_lessons" in s:
                # 只給堂數：展開範圍由 max_lessons 收斂，+52 週緩衝 except_dates
                end = start + timedelta(weeks=max_lessons + 52)
            else:
                end = start + timedelta(weeks=12)
            target_day = DAY_NAMES.index(s["day"])
            days_ahead = (target_day - start.weekday()) % 7
            current = start + timedelta(days=days_ahead)
            count = 0
            while current < end and count < max_lessons:
                if _emit(current, s["day"]):
                    count += 1
                current += timedelta(days=7)
        elif "days" in s:
            start = _to_date(s["start_date"])
            target_day_set = set(DAY_NAMES.index(d) for d in s["days"])
            max_lessons = s.get("total_lessons", 99999)
            if "end_date" in s:
                end = _to_date(s["end_date"]) + timedelta(days=1)
            elif "duration_weeks" in s:
                end = start + timedelta(weeks=s["duration_weeks"])
            elif "total_lessons" in s:
                # 只給堂數：展開範圍由 max_lessons 收斂，+52 週緩衝 except_dates
                end = start + timedelta(weeks=max_lessons + 52)
            else:
                end = start + timedelta(weeks=12)
            current = start
            count = 0
            while current < end and count < max_lessons:
                if current.weekday() in target_day_set:
                    if _emit(current, DAY_NAMES[current.weekday()]):
                        count += 1
                current += timedelta(days=1)
    return expanded

# ================== v3 展開器凍結副本結束 ==================


PATTERN_FIELDS = ("day", "days", "start_date", "end_date", "duration_weeks",
                  "total_lessons", "specific_dates", "except_dates")


def make_label(s):
    """由 v3 pattern 生成顯示用 label"""
    if "specific_dates" in s:
        return "指定日期"
    if "day" in s:
        return "週" + DAY_ZH_CHAR.get(s["day"], "?")
    if "days" in s:
        chars = [DAY_ZH_CHAR.get(d, "?")
                 for d in sorted(s["days"], key=DAY_NAMES.index)]
        return "週" + "".join(chars)
    return None


def build_v4_schedule(s):
    """schedule 降級為 metadata：保留 id/class_id/slot_id/time/note + label，丟棄 pattern 欄位"""
    out = {"id": s.get("id"), "class_id": s.get("class_id")}
    if s.get("slot_id"):
        out["slot_id"] = s["slot_id"]
    if s.get("time"):
        out["time"] = s["time"]
    label = make_label(s)
    if label:
        out["label"] = label
    if s.get("note"):
        out["note"] = s["note"]
    return out


def build_lessons(expanded):
    """v3 展開結果 → lessons 清單（L-NNNN 按日期序編）"""
    ordered = sorted(
        expanded,
        key=lambda l: (str(l["date"]), l["slot_time"] or "", l["schedule_id"] or ""))
    lessons = []
    for i, l in enumerate(ordered, start=1):
        lesson = {
            "id": f"L-{i:04d}",
            "schedule_id": l["schedule_id"],
            "class_id": l["class_id"],
            "date": str(l["date"]),
            "time": l["slot_time"],
        }
        if l.get("slot_id"):
            lesson["slot_id"] = l["slot_id"]
        if l.get("note"):
            lesson["note"] = l["note"]
        lessons.append(lesson)
    return lessons


def migrate_makeups(makeups, lessons):
    """makeup_schedule_id → makeup_lesson_id；找不到對應 lesson 回傳錯誤清單"""
    errors = []
    by_schedule = {}
    for l in lessons:
        by_schedule.setdefault(l.get("schedule_id"), []).append(l)
    new_makeups = []
    for m in makeups:
        nm = {}
        for k, v in m.items():
            if k == "makeup_schedule_id":
                continue
            nm[k] = v
        sched_id = m.get("makeup_schedule_id")
        if sched_id:
            candidates = by_schedule.get(sched_id, [])
            if len(candidates) == 1:
                nm["makeup_lesson_id"] = candidates[0]["id"]
            else:
                md = m.get("makeup_date")
                matched = [c for c in candidates
                           if md is not None and c["date"] == str(_to_date(md))]
                if len(matched) == 1:
                    nm["makeup_lesson_id"] = matched[0]["id"]
                else:
                    errors.append(
                        f"makeup {m.get('id')}: makeup_schedule_id={sched_id} "
                        f"找不到唯一對應 lesson（候選 {len(candidates)} 筆）")
        else:
            nm["makeup_lesson_id"] = None
        new_makeups.append(nm)
    return new_makeups, errors


def equivalence_check(expanded, lessons):
    """v3 展開全集 vs 新 lessons 全集，按 (class_id, date, time) 逐堂 diff"""
    v3_counter = Counter(
        (l["class_id"], str(l["date"]), l["slot_time"]) for l in expanded)
    v4_counter = Counter(
        (l["class_id"], l["date"], l["time"]) for l in lessons)
    diffs = []
    for key in sorted(set(v3_counter) | set(v4_counter)):
        a, b = v3_counter.get(key, 0), v4_counter.get(key, 0)
        if a != b:
            diffs.append(f"  {key}: v3={a} v4={b}")
    return diffs


def atomic_write_text(path, text):
    """Write *text* beside *path*, then atomically replace the target.

    Keeping the temporary file in the target directory ensures ``os.replace``
    stays on the same filesystem.  Any failed write, flush, fsync, or replace
    leaves the existing target untouched and removes the temporary file.
    """
    path = Path(path)
    fd, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temp_path = Path(temp_name)
    stream = None
    try:
        stream = os.fdopen(fd, "w", encoding="utf-8", newline="")
        with stream as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    except BaseException:
        if stream is None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def migrate(path):
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        print("FAIL: yaml 頂層必須是 mapping")
        return 1

    if data.get("schema_version") == 4:
        print("已是 v4")
        return 0

    if data.get("schema_version") != 3:
        print(f"FAIL: 不支援 schema_version={data.get('schema_version')!r}，只接受 v3")
        return 1

    slots = data.get("slots", []) or []
    classes = data.get("classes", []) or []
    schedules = data.get("schedules", []) or []
    makeups = data.get("makeups", []) or []

    slots_by_id = {s.get("id"): s for s in slots if isinstance(s, dict) and s.get("id")}
    classes_by_id = {c.get("id"): c for c in classes if isinstance(c, dict) and c.get("id")}

    # 1. v3 展開
    expanded = v3_expand_schedule(schedules, slots_by_id, classes_by_id)

    # 2. 生成 lessons + 降級 schedules
    lessons = build_lessons(expanded)
    new_schedules = [build_v4_schedule(s) for s in schedules]

    # 3. makeups 欄位改名
    new_makeups, mk_errors = migrate_makeups(makeups, lessons)
    if mk_errors:
        print("FAIL: makeups 遷移失敗，不寫檔：")
        for e in mk_errors:
            print("  " + e)
        return 1

    # 4. 等價自檢
    diffs = equivalence_check(expanded, lessons)
    if diffs:
        print("FAIL: 等價自檢不通過（v3 展開 vs lessons 有差異），不寫檔：")
        for d in diffs:
            print(d)
        return 1
    print(f"OK 等價自檢通過：{len(expanded)} 堂逐堂一致")

    # 5. 備份 + 寫檔
    backup = path.with_name("schedule.pre-v4.yaml")
    backup.write_text(raw, encoding="utf-8")
    print(f"OK 備份已寫入 {backup.name}")

    out = {
        "schema_version": 4,
        "slots": slots,
        "classes": classes,
        "schedules": new_schedules,
        "lessons": lessons,
    }
    if makeups:
        out["makeups"] = new_makeups
    atomic_write_text(
        path,
        yaml.safe_dump(out, allow_unicode=True, sort_keys=False,
                       default_flow_style=False))

    print(f"OK 遷移完成：班數={len(classes)} 排程數={len(new_schedules)} 堂數={len(lessons)}")
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--file", default=str(DEFAULT_YAML), help="目標 yaml 檔（預設 data/schedule.yaml）")
    args = p.parse_args()
    sys.exit(migrate(args.file))


if __name__ == "__main__":
    main()
