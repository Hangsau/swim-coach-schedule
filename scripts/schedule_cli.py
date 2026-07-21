#!/usr/bin/env python3
"""
schedule_cli.py — LLM-friendly CLI for swim-coach-schedule（v4）

設計目標：minimax LLM 經 TG 收到自然語言指令後，透過此 CLI 操作系統，
不直接編輯 yaml。每個寫入命令：
  - 預設 dry-run（preview diff），實寫入需 --apply
  - 寫入前自動 validate；fail 拒絕並回 structured JSON
  - atomic write：先寫 temp + validate → 通過才 replace
  - JSON envelope: {"ok", "data", "errors", "warnings", "next_actions"}

v4 語意：課次真相在頂層 lessons（一堂一筆），schedules 降級為分組
metadata。add-schedule 當場展開成 N 筆 lessons；cancel-lesson 直接刪
lesson；move-lesson 原地改 lesson 的 date/slot/time。

子命令：
  status / list-classes / list-slots / list-conflicts
  add-class / update-class / remove-class / end-class
  add-schedule / update-schedule / remove-schedule / move-lesson
  split-schedule / cancel-lesson / add-lesson / undo
  fulfill-makeup / list-makeups / cancel-makeup（待補課帳本）

退出碼: 0=成功（含 warnings）/ 非 0=失敗
"""
import argparse
import copy
import difflib
import json
import re
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

# Windows cp950 console
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).parent.parent
DEFAULT_YAML = ROOT / "data" / "schedule.yaml"

sys.path.insert(0, str(Path(__file__).parent))
from validate import validate_all, time_overlap, DAYS  # noqa: E402
from query import DAY_NAMES, expand_schedule  # noqa: E402

DAY_ZH_CHAR = {
    "mon": "一", "tue": "二", "wed": "三", "thu": "四",
    "fri": "五", "sat": "六", "sun": "日",
}


# -------------- envelope helpers --------------

def envelope(ok, data=None, errors=None, warnings=None, next_actions=None):
    return {
        "ok": bool(ok),
        "data": data or {},
        "errors": errors or [],
        "warnings": warnings or [],
        "next_actions": next_actions or [],
    }


def emit(env, json_mode):
    if json_mode:
        print(json.dumps(env, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"[result] {'OK' if env['ok'] else 'FAIL'}")
        if env["data"]:
            print(f"[data] {json.dumps(env['data'], ensure_ascii=False, default=str)}")
        for e in env["errors"]:
            print(f"  [{e.get('code')}] {e.get('msg')}")
        for w in env["warnings"]:
            print(f"  [{w.get('code')}] {w.get('msg')}")
        for n in env["next_actions"]:
            print(f"  -> {n}")
    sys.exit(0 if env["ok"] else 1)


def _err(code, msg, **ctx):
    return {"code": code, "msg": msg, "context": ctx}


# -------------- yaml read/write --------------

def load_yaml(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def dump_yaml_text(data):
    return yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


BACKUP_KEEP = 10
BACKUP_GLOB = "schedule-*.yaml"


def _backup_dir(path):
    return Path(path).parent / ".backup"


def _backup_current(path):
    """寫入前備份現行檔到同目錄 .backup/，保留最新 BACKUP_KEEP 份（undo 資料來源）。"""
    src = Path(path)
    if not src.exists():
        return
    bdir = _backup_dir(path)
    bdir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    (bdir / f"schedule-{stamp}.yaml").write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8")
    for old in sorted(bdir.glob(BACKUP_GLOB))[:-BACKUP_KEEP]:
        old.unlink(missing_ok=True)


def atomic_write(path, data, strict=False):
    """temp 寫入 + validate → 通過才 replace。回 (ok, errors, warnings, diff)"""
    text = dump_yaml_text(data)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".yaml", delete=False,
        dir=str(Path(path).parent)
    )
    tmp_path = Path(tmp.name)
    try:
        tmp.write(text)
        tmp.close()
        result = validate_all(str(tmp_path), strict=strict)
        if not result["ok"]:
            tmp_path.unlink(missing_ok=True)
            return False, result["errors"], result["warnings"], None
        # diff before replace
        old_text = Path(path).read_text(encoding="utf-8") if Path(path).exists() else ""
        diff = "".join(difflib.unified_diff(
            old_text.splitlines(keepends=True),
            text.splitlines(keepends=True),
            fromfile=str(path) + ".before",
            tofile=str(path) + ".after",
            n=2,
        ))
        _backup_current(path)
        tmp_path.replace(path)
        return True, [], result["warnings"], diff
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        return False, [_err("E_WRITE_FAILED", f"atomic write 失敗: {e}")], [], None


def preview_diff(path, new_data):
    """不寫，只算 diff"""
    old_text = Path(path).read_text(encoding="utf-8") if Path(path).exists() else ""
    new_text = dump_yaml_text(new_data)
    return "".join(difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile="before",
        tofile="after",
        n=2,
    ))


# -------------- v4 lesson helpers --------------

def _parse_date_safe(d):
    try:
        return datetime.strptime(str(d), "%Y-%m-%d").date()
    except Exception:
        return None


def _lesson_date(l):
    return _parse_date_safe(l.get("date"))


def _match_lessons(lessons, class_id, target_date):
    """找 (class_id, date) 命中的 lessons"""
    return [l for l in lessons
            if l.get("class_id") == class_id and _lesson_date(l) == target_date]


def _sort_lessons(lessons):
    lessons.sort(key=lambda l: (str(l.get("date", "")), str(l.get("time", "")),
                                str(l.get("id", ""))))


def next_schedule_id(schedules):
    existing = [str(s.get("id", "")) for s in schedules
                if str(s.get("id", "")).startswith("SCH-")]
    max_n = max([int(x.split("-")[1]) for x in existing
                 if x.split("-")[1].isdigit()] + [0])
    return f"SCH-{max_n + 1:03d}"


def _alloc_lessons(numbering_source, dates, class_id, time_str,
                   schedule_id=None, slot_id=None, note=None):
    """為 dates 產生新 lessons；L-id 從 numbering_source（原始全量 lessons）
    的最大號碼往後編，避免重用已刪 lesson 的 id。"""
    nums = [int(m.group(1)) for l in numbering_source
            if (m := re.match(r"^L-(\d+)$", str(l.get("id", ""))))]
    n = max(nums, default=0)
    new = []
    for d in sorted(dates):
        n += 1
        lesson = {"id": f"L-{n:04d}"}
        if schedule_id:
            lesson["schedule_id"] = schedule_id
        lesson["class_id"] = class_id
        lesson["date"] = str(d)
        lesson["time"] = time_str
        if slot_id:
            lesson["slot_id"] = slot_id
        if note:
            lesson["note"] = note
        new.append(lesson)
    return new


def _revert_makeups_for_removed_lessons(new_data, removed_ids):
    """被刪的 lessons 若是某筆 fulfilled makeup 的補課堂（makeup_lesson_id），
    該 makeup 標回 pending（補課取消 → 欠補復活），避免懸空引用。回傳被還原的 makeup ids。"""
    reverted = []
    for m in new_data.get("makeups", []) or []:
        if m.get("status") == "fulfilled" and m.get("makeup_lesson_id") in removed_ids:
            m["status"] = "pending"
            m["makeup_date"] = None
            m["makeup_lesson_id"] = None
            reverted.append(m.get("id"))
    return reverted


def make_label(day=None, days=None, specific=False):
    """由 pattern 參數生成顯示用 label（同 migrate_v4.py 規則）"""
    if specific:
        return "指定日期"
    if day:
        return "週" + DAY_ZH_CHAR.get(day, "?")
    if days:
        chars = [DAY_ZH_CHAR.get(d, "?") for d in sorted(days, key=DAY_NAMES.index)]
        return "週" + "".join(chars)
    return None


def expand_pattern_dates(day=None, days=None, specific_dates=None,
                         start=None, end=None, weeks=None, total_lessons=None):
    """pattern 參數 → 具體日期清單（date 物件，升冪）。

    展開規則沿用 v3（migrate_v4.py 凍結副本同源）：
      - specific_dates：直接列出
      - day/days + start：週期展開；終止條件 end / weeks / total_lessons，
        皆未給時預設 12 週
    格式錯誤丟 ValueError（呼叫端轉 envelope error）。
    """
    out = []
    if specific_dates:
        for ds in specific_dates:
            d = _parse_date_safe(ds)
            if d is None:
                raise ValueError(f"日期格式錯誤：{ds}（需 YYYY-MM-DD）")
            out.append(d)
        return sorted(out)
    start_d = _parse_date_safe(start)
    if start_d is None:
        raise ValueError(f"start 日期格式錯誤：{start}（需 YYYY-MM-DD）")
    max_lessons = total_lessons if total_lessons is not None else 99999
    if end:
        end_d = _parse_date_safe(end)
        if end_d is None:
            raise ValueError(f"end 日期格式錯誤：{end}（需 YYYY-MM-DD）")
        end_d = end_d + timedelta(days=1)
    elif weeks is not None:
        end_d = start_d + timedelta(weeks=weeks)
    elif total_lessons is not None:
        end_d = start_d + timedelta(weeks=max_lessons + 52)
    else:
        end_d = start_d + timedelta(weeks=12)
    if day:
        target = DAY_NAMES.index(day)
        days_ahead = (target - start_d.weekday()) % 7
        current = start_d + timedelta(days=days_ahead)
        count = 0
        while current < end_d and count < max_lessons:
            out.append(current)
            count += 1
            current += timedelta(days=7)
    elif days:
        target_set = {DAY_NAMES.index(d) for d in days}
        current = start_d
        count = 0
        while current < end_d and count < max_lessons:
            if current.weekday() in target_set:
                out.append(current)
                count += 1
            current += timedelta(days=1)
    return out


# -------------- commands: read --------------

def cmd_status(args):
    result = validate_all(args.file, strict=args.strict)
    data = load_yaml(args.file)
    slots_by_id = {s["id"]: s for s in data.get("slots", []) if s.get("id")}
    classes_by_id = {c["id"]: c for c in data.get("classes", []) if c.get("id")}
    lessons = expand_schedule(data.get("schedules", []) or [], slots_by_id,
                              classes_by_id, data=data)
    today = date.today()
    week_end = today + timedelta(days=7)
    upcoming = sorted(
        [{"date": str(l["date"]), "slot_time": l["slot_time"],
          "class_id": l["class_id"], "class_name": l["class_name"]}
         for l in lessons if today <= l["date"] <= week_end],
        key=lambda x: (x["date"], x["slot_time"]),
    )
    used_class = {l["class_id"] for l in lessons}
    orphan_classes = [c["id"] for c in data.get("classes", []) if c.get("id") and c["id"] not in used_class]
    pending_makeups = [
        {"id": m.get("id"), "class_id": m.get("class_id"),
         "origin_date": str(m.get("origin_date")), "reason": m.get("reason") or ""}
        for m in (data.get("makeups", []) or [])
        if m.get("status", "pending") == "pending"
    ]
    next_actions = []
    if not result["ok"]:
        next_actions.append("先跑 list-conflicts 看細節，依 error code 處理")
    if orphan_classes:
        next_actions.append(f"孤兒 class（無任何課次）: {orphan_classes}；用 add-schedule / add-lesson 或 remove-class")
    if pending_makeups:
        next_actions.append(f"待補課 {len(pending_makeups)} 筆；補課日決定後用 fulfill-makeup 銷帳")
    env = envelope(
        result["ok"],
        data={
            "stats": result["stats"],
            "upcoming_7d": upcoming,
            "orphan_classes": orphan_classes,
            "pending_makeups": pending_makeups,
        },
        errors=result["errors"],
        warnings=result["warnings"],
        next_actions=next_actions,
    )
    emit(env, args.json)


def cmd_list_classes(args):
    data = load_yaml(args.file)
    classes = data.get("classes", []) or []
    schedules = data.get("schedules", []) or []
    info = []
    for c in classes:
        entry = {"id": c.get("id"), "name": c.get("name"),
                 "weekly_count": c.get("weekly_count"), "level": c.get("level")}
        if args.with_schedules:
            entry["schedules"] = [
                {k: v for k, v in s.items() if k != "note"}
                for s in schedules if s.get("class_id") == c.get("id")
            ]
        info.append(entry)
    emit(envelope(True, data={"classes": info, "count": len(info)}), args.json)


def cmd_list_slots(args):
    data = load_yaml(args.file)
    slots = data.get("slots", []) or []
    schedules = data.get("schedules", []) or []
    lessons = data.get("lessons", []) or []
    used_set = {s.get("slot_id") for s in schedules} | {l.get("slot_id") for l in lessons}
    info = [{"id": s["id"], "time": s.get("time"), "note": s.get("note"),
             "used": s["id"] in used_set} for s in slots if s.get("id")]
    if args.used_only:
        info = [x for x in info if x["used"]]
    emit(envelope(True, data={"slots": info, "count": len(info)}), args.json)


def cmd_list_conflicts(args):
    result = validate_all(args.file, strict=False)
    conflicts = [e for e in result["errors"] if e.get("code") == "E_TIME_OVERLAP"]
    env = envelope(
        len(conflicts) == 0,
        data={"conflicts": conflicts, "count": len(conflicts)},
        errors=conflicts if conflicts else [],
        warnings=result["warnings"],
        next_actions=["有衝突就用 remove-schedule 或 move-lesson 處理"] if conflicts else [],
    )
    emit(env, args.json)


# -------------- commands: write helpers --------------

def _parse_days(s):
    return [d.strip().lower() for d in s.split(",") if d.strip()]


def _parse_specific_dates(s):
    return [d.strip() for d in s.split(",") if d.strip()]


def _commit_or_preview(args, new_data, success_data, next_actions=None):
    """共用：preview / apply / commit 判斷"""
    if not args.apply:
        diff = preview_diff(args.file, new_data)
        result = validate_all_inline(new_data, strict=args.strict)
        env = envelope(
            result["ok"],
            data={"preview": True, "diff": diff, "after_stats": result["stats"], **success_data},
            errors=result["errors"],
            warnings=result["warnings"],
            next_actions=(["dry-run 通過；加 --apply 真寫入"] if result["ok"]
                          else ["修正錯誤後重跑"]) + (next_actions or []),
        )
        emit(env, args.json)
    else:
        ok, errs, warns, diff = atomic_write(args.file, new_data, strict=args.strict)
        env = envelope(
            ok,
            data={"applied": True, "diff": diff, **success_data} if ok else success_data,
            errors=errs,
            warnings=warns,
            next_actions=(next_actions or []) + ([
                "更新 docs：python scripts/render_html.py",
                "commit：git add data/ docs/ && git commit -m '<簡短說明>'",
            ] if ok else []),
        )
        emit(env, args.json)


def validate_all_inline(data_dict, strict=False):
    """validate.validate_all 是讀檔；此處用 in-memory"""
    tmp = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".yaml", delete=False)
    try:
        tmp.write(dump_yaml_text(data_dict))
        tmp.close()
        return validate_all(tmp.name, strict=strict)
    finally:
        Path(tmp.name).unlink(missing_ok=True)


# -------------- commands: write --------------

def next_class_id(classes):
    nums = [int(m.group(1)) for c in classes
            if (m := re.match(r"^STU-(\d+)$", str(c.get("id", ""))))]
    return f"STU-{max(nums, default=0) + 1:02d}"


def next_makeup_id(makeups):
    nums = [int(m.group(1)) for mk in makeups
            if (m := re.match(r"^MU-(\d+)$", str(mk.get("id", ""))))]
    return f"MU-{max(nums, default=0) + 1:03d}"


def cmd_add_class(args):
    data = load_yaml(args.file)
    classes = data.setdefault("classes", [])
    cid = args.id or next_class_id(classes)
    if any(c.get("id") == cid for c in classes):
        emit(envelope(False, errors=[_err("E_DUPLICATE_ID", f"class id {cid} 已存在")],
                      next_actions=["改用 update-class 修改現有 class"]), args.json)
    new_class = {"id": cid, "name": args.name, "weekly_count": args.weekly_count,
                 "level": args.level or "待確認"}
    if args.note:
        new_class["note"] = args.note
    new_data = copy.deepcopy(data)
    new_data["classes"].append(new_class)
    _commit_or_preview(args, new_data, {"added_class": new_class},
                       next_actions=[f"下一步：add-schedule --class {cid} ..."])


def cmd_update_class(args):
    data = load_yaml(args.file)
    target = next((c for c in (data.get("classes") or []) if c.get("id") == args.id), None)
    if not target:
        emit(envelope(False, errors=[_err("E_CLASS_NOT_FOUND", f"class {args.id} 不存在")]), args.json)
    new_data = copy.deepcopy(data)
    new_target = next(c for c in new_data["classes"] if c.get("id") == args.id)
    if args.name is not None:
        new_target["name"] = args.name
    if args.weekly_count is not None:
        new_target["weekly_count"] = args.weekly_count
    if args.level is not None:
        new_target["level"] = args.level
    if args.note is not None:
        new_target["note"] = args.note
    _commit_or_preview(args, new_data, {"updated_class_id": args.id})


def cmd_remove_class(args):
    data = load_yaml(args.file)
    cls = next((c for c in (data.get("classes") or []) if c.get("id") == args.id), None)
    if not cls:
        emit(envelope(False, errors=[_err("E_CLASS_NOT_FOUND", f"class {args.id} 不存在")]), args.json)
    schedules = data.get("schedules", []) or []
    lessons = data.get("lessons", []) or []
    makeups = data.get("makeups", []) or []
    sched_refs = [s for s in schedules if s.get("class_id") == args.id]
    lesson_refs = [l for l in lessons if l.get("class_id") == args.id]
    makeup_refs = [m for m in makeups if m.get("class_id") == args.id]
    if (sched_refs or lesson_refs) and not args.cascade:
        emit(envelope(False,
                      errors=[_err("E_AMBIGUOUS_TARGET",
                                   f"class {args.id} 仍有 {len(sched_refs)} 條 schedule、"
                                   f"{len(lesson_refs)} 筆 lesson 引用",
                                   schedule_count=len(sched_refs),
                                   lesson_count=len(lesson_refs))],
                      next_actions=["加 --cascade 連帶刪除 schedule/lesson，或先 remove-schedule"]),
             args.json)
    new_data = copy.deepcopy(data)
    new_data["classes"] = [c for c in new_data["classes"] if c.get("id") != args.id]
    if args.cascade:
        new_data["schedules"] = [s for s in new_data.get("schedules", []) or []
                                 if s.get("class_id") != args.id]
        new_data["lessons"] = [l for l in new_data.get("lessons", []) or []
                               if l.get("class_id") != args.id]
        if makeup_refs:
            new_data["makeups"] = [m for m in new_data.get("makeups", []) or []
                                   if m.get("class_id") != args.id]
    _commit_or_preview(args, new_data,
                       {"removed_class_id": args.id,
                        "cascaded_schedules": len(sched_refs) if args.cascade else 0,
                        "cascaded_lessons": len(lesson_refs) if args.cascade else 0,
                        "cascaded_makeups": len(makeup_refs) if args.cascade else 0})


def cmd_add_schedule(args):
    """新增排程：pattern 旗標當場展開成 N 筆 lessons + 1 筆 schedule metadata"""
    data = load_yaml(args.file)
    classes = data.get("classes", []) or []
    slots = data.get("slots", []) or []
    if not any(c.get("id") == args.class_id for c in classes):
        emit(envelope(False,
                      errors=[_err("E_CLASS_NOT_FOUND", f"class {args.class_id} 不存在",
                                   available=[c.get("id") for c in classes])],
                      next_actions=["先 add-class，或檢查 class id 拼字"]),
             args.json)
    # --slot 跟 --time 至少一個
    if not args.slot_id and not args.time:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   "--slot 跟 --time 至少要指定一個")],
                      next_actions=["用 list-slots 看常用時段；或直接 --time HH:MM-HH:MM"]),
             args.json)
    if args.slot_id and not any(s.get("id") == args.slot_id for s in slots):
        emit(envelope(False,
                      errors=[_err("E_SLOT_NOT_FOUND", f"slot {args.slot_id} 不存在",
                                   available=[s.get("id") for s in slots])]),
             args.json)
    modes = [bool(args.day), bool(args.days), bool(args.specific_dates)]
    if sum(modes) != 1:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   "day / days / specific-dates 必須恰指定一個")]),
             args.json)
    # 星期值驗證（v4 當場展開，壞值必須先擋）
    day = args.day
    days = _parse_days(args.days) if args.days else None
    bad_days = []
    if day and day not in DAY_NAMES:
        bad_days = [day]
    elif days:
        bad_days = [d for d in days if d not in DAY_NAMES]
    if bad_days:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   f"不合法的星期值：{','.join(bad_days)}",
                                   allowed=list(DAY_NAMES))]),
             args.json)
    if (day or days) and not args.start:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   "day / days 模式需要 --start YYYY-MM-DD")]),
             args.json)
    specific = _parse_specific_dates(args.specific_dates) if args.specific_dates else None
    # 展開日期
    try:
        dates = expand_pattern_dates(day=day, days=days, specific_dates=specific,
                                     start=args.start, end=args.end,
                                     weeks=args.weeks, total_lessons=args.lessons)
    except ValueError as e:
        emit(envelope(False, errors=[_err("E_SCHEMA_INVALID", str(e))]), args.json)
    if not dates:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   "展開結果為 0 堂，請檢查 pattern 參數")]),
             args.json)
    # schedule metadata（v4：不存 pattern 欄位）
    new_id = next_schedule_id(data.get("schedules", []) or [])
    new_sched = {"id": new_id, "class_id": args.class_id}
    if args.slot_id:
        new_sched["slot_id"] = args.slot_id
    if args.time:
        new_sched["time"] = args.time
    elif args.slot_id:
        slot_def = next(s for s in slots if s.get("id") == args.slot_id)
        new_sched["time"] = slot_def.get("time")
    label = make_label(day=day, days=days, specific=bool(specific))
    if label:
        new_sched["label"] = label
    if args.note:
        new_sched["note"] = args.note
    new_data = copy.deepcopy(data)
    new_data.setdefault("schedules", []).append(new_sched)
    new_lessons = _alloc_lessons(data.get("lessons", []) or [], dates,
                                 class_id=args.class_id,
                                 time_str=new_sched.get("time"),
                                 schedule_id=new_id,
                                 slot_id=args.slot_id,
                                 note=args.note)
    new_data.setdefault("lessons", []).extend(new_lessons)
    _sort_lessons(new_data["lessons"])
    extra = []
    if (day or days) and args.weeks is None and args.end is None and args.lessons is None:
        extra.append("未指定 --weeks/--end/--lessons，預設展開 12 週")
    _commit_or_preview(args, new_data,
                       {"added_schedule": new_sched,
                        "lessons_added": len(new_lessons),
                        "lesson_dates": [str(d) for d in dates]},
                       next_actions=extra or None)


def cmd_move_lesson(args):
    """挪一堂課：原地改該筆 lesson 的 date/slot/time（不刪不增，無空殼）"""
    data = load_yaml(args.file)
    lessons = data.get("lessons", []) or []
    slots_by_id = {s["id"]: s for s in data.get("slots", []) if s.get("id")}
    from_date = _parse_date_safe(args.from_date)
    if from_date is None:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   f"日期格式錯誤：{args.from_date}（需 YYYY-MM-DD）")]),
             args.json)
    to_date = _parse_date_safe(args.to_date)
    if to_date is None:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   f"日期格式錯誤：{args.to_date}（需 YYYY-MM-DD）")]),
             args.json)
    matched = _match_lessons(lessons, args.class_id, from_date)
    if not matched:
        emit(envelope(False,
                      errors=[_err("E_LESSON_NOT_FOUND",
                                   f"class {args.class_id} 在 {args.from_date} 沒有課可挪")]),
             args.json)
    if len(matched) > 1:
        emit(envelope(False,
                      errors=[_err("E_AMBIGUOUS_TARGET",
                                   f"{args.class_id} {args.from_date} 同日有 {len(matched)} 堂",
                                   matches=matched)]),
             args.json)
    if args.to_slot and args.to_slot not in slots_by_id:
        emit(envelope(False,
                      errors=[_err("E_SLOT_NOT_FOUND", f"slot {args.to_slot} 不存在")]),
             args.json)
    src = matched[0]
    new_data = copy.deepcopy(data)
    target = next(l for l in new_data["lessons"] if l.get("id") == src.get("id"))
    target["date"] = args.to_date
    if args.to_time:
        target["time"] = args.to_time
        target.pop("slot_id", None)
    elif args.to_slot:
        target["slot_id"] = args.to_slot
        target["time"] = slots_by_id[args.to_slot].get("time")
    # 不給 to-slot / to-time：同時段挪到別天（time/slot 不動）
    if args.note:
        target["note"] = args.note
    # 若這堂是某筆 fulfilled makeup 的補課堂，makeup_date 跟著挪
    makeups_synced = []
    for m in new_data.get("makeups", []) or []:
        if m.get("status") == "fulfilled" and m.get("makeup_lesson_id") == src.get("id"):
            m["makeup_date"] = args.to_date
            makeups_synced.append(m.get("id"))
    _sort_lessons(new_data["lessons"])
    success = {"moved_lesson_id": src.get("id"),
               "moved_from": {"date": args.from_date,
                              "schedule_id": src.get("schedule_id")},
               "moved_to": target}
    if makeups_synced:
        success["makeups_synced"] = makeups_synced
    _commit_or_preview(args, new_data, success)


def cmd_split_schedule(args):
    """切某條 schedule：from 起的未來 lessons 刪除，依新時段重生為後段新 schedule"""
    data = load_yaml(args.file)
    schedules = data.get("schedules", []) or []
    slots_by_id = {s["id"]: s for s in data.get("slots", []) if s.get("id")}
    at_date = _parse_date_safe(args.at)
    if at_date is None:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   f"--at {args.at} 不是合法 YYYY-MM-DD")]),
             args.json)

    # 找 schedule
    target = None
    if args.schedule_id:
        target = next((s for s in schedules if s.get("id") == args.schedule_id), None)
        if target is None:
            emit(envelope(False,
                          errors=[_err("E_SCHEDULE_NOT_FOUND",
                                       f"schedule {args.schedule_id} 不存在",
                                       available=[s.get("id") for s in schedules if s.get("id")])]),
                 args.json)
    else:
        cands = [s for s in schedules if s.get("class_id") == args.class_id]
        if not cands:
            emit(envelope(False,
                          errors=[_err("E_SCHEMA_INVALID",
                                       f"class {args.class_id} 沒有可 split 的 schedule")]),
                 args.json)
        if len(cands) > 1:
            emit(envelope(False,
                          errors=[_err("E_AMBIGUOUS_TARGET",
                                       f"class {args.class_id} 有 {len(cands)} 條 schedule",
                                       matches=[{"id": c.get("id"),
                                                 "label": c.get("label"),
                                                 "time": c.get("time")} for c in cands])],
                          next_actions=["改用 --schedule-id 指定（例：SCH-005）"]),
                 args.json)
        target = cands[0]

    sid = target.get("id")
    all_lessons = data.get("lessons", []) or []
    sched_lessons = [l for l in all_lessons if l.get("schedule_id") == sid]
    kept = [l for l in sched_lessons
            if (_lesson_date(l) is not None and _lesson_date(l) < at_date)]
    removed = [l for l in sched_lessons
               if (_lesson_date(l) is not None and _lesson_date(l) >= at_date)]

    # 後段 day/days：args 優先；沒指定就從被移除的未來 lessons 推導星期
    if args.day or args.days:
        day = args.day
        days = _parse_days(args.days) if args.days else None
        bad = [day] if (day and day not in DAY_NAMES) else \
              [d for d in (days or []) if d not in DAY_NAMES]
        if bad:
            emit(envelope(False,
                          errors=[_err("E_SCHEMA_INVALID",
                                       f"不合法的星期值：{','.join(bad)}",
                                       allowed=list(DAY_NAMES))]),
                 args.json)
    else:
        wd = sorted({_lesson_date(l).weekday() for l in removed if _lesson_date(l)})
        if not wd:
            emit(envelope(False,
                          errors=[_err("E_SCHEMA_INVALID",
                                       f"{args.at} 起無未來 lessons 可推導星期，"
                                       "請指定 --day 或 --days")]),
                 args.json)
        derived = [DAY_NAMES[i] for i in wd]
        day, days = (derived[0], None) if len(derived) == 1 else (None, derived)

    # 後段 slot/time：args 優先；沒指定就沿用前段
    after_slot = None
    after_time = None
    if args.to_time:
        after_time = args.to_time
    elif args.to_slot:
        slot = slots_by_id.get(args.to_slot)
        if not slot:
            emit(envelope(False, errors=[_err("E_SLOT_NOT_FOUND",
                                              f"slot {args.to_slot} 不存在")]), args.json)
        after_slot = args.to_slot
        after_time = slot.get("time")
    else:
        after_slot = target.get("slot_id")
        after_time = target.get("time")
    if not after_time:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   "無法決定後段時段，請指定 --to-slot 或 --to-time")]),
             args.json)

    # 終止條件（後段必須明確指定一個）
    if args.weeks is None and args.end is None and args.lessons is None:
        emit(envelope(False,
                      errors=[_err("E_NO_TERMINATION",
                                   "後段需要終止條件：--weeks N / --end YYYY-MM-DD / --lessons N")],
                      next_actions=["再跑一次加上 --weeks 12 或 --end 2026-12-31 等"]),
             args.json)

    try:
        dates = expand_pattern_dates(day=day, days=days, start=args.at,
                                     end=args.end, weeks=args.weeks,
                                     total_lessons=args.lessons)
    except ValueError as e:
        emit(envelope(False, errors=[_err("E_SCHEMA_INVALID", str(e))]), args.json)
    if not dates:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   "後段展開結果為 0 堂，請檢查參數")]),
             args.json)

    new_data = copy.deepcopy(data)
    # 建後段 schedule metadata
    after_id = next_schedule_id(new_data.get("schedules", []) or [])
    after = {"id": after_id, "class_id": target["class_id"]}
    if after_slot:
        after["slot_id"] = after_slot
    after["time"] = after_time
    label = make_label(day=day, days=days)
    if label:
        after["label"] = label
    if args.note:
        after["note"] = args.note
    new_data["schedules"].append(after)
    # 刪除 from 起的未來 lessons + 重生後段 lessons
    removed_ids = {l.get("id") for l in removed}
    new_data["lessons"] = [l for l in new_data.get("lessons", []) or []
                           if l.get("id") not in removed_ids]
    makeups_reverted = _revert_makeups_for_removed_lessons(new_data, removed_ids)
    new_lessons = _alloc_lessons(all_lessons, dates,
                                 class_id=target["class_id"],
                                 time_str=after_time,
                                 schedule_id=after_id,
                                 slot_id=after_slot,
                                 note=args.note)
    new_data["lessons"].extend(new_lessons)
    _sort_lessons(new_data["lessons"])
    # 前段沒剩任何 lessons → 連 schedule metadata 刪
    before_removed = len(kept) == 0
    if before_removed:
        new_data["schedules"] = [s for s in new_data["schedules"] if s.get("id") != sid]
    success = {"split_at": args.at,
               "before": {"id": sid, "kept_lessons": len(kept),
                          "removed_lessons": len(removed),
                          "schedule_removed": before_removed},
               "after": {"schedule": after,
                         "lessons_added": len(new_lessons),
                         "lesson_dates": [str(d) for d in dates]}}
    if makeups_reverted:
        success["makeups_reverted"] = makeups_reverted
    _commit_or_preview(args, new_data, success)


def cmd_end_class(args):
    """結束班級：刪 from（含）起的 lessons；schedule 無剩餘 lessons → 連 schedule 刪；
    class 全空 → 連 class 刪（已上堂次全保留，月報表照算）"""
    data = load_yaml(args.file)
    classes = data.get("classes") or []
    if not any(c.get("id") == args.class_id for c in classes):
        emit(envelope(False,
                      errors=[_err("E_CLASS_NOT_FOUND", f"class {args.class_id} 不存在")]),
             args.json)
    from_date = _parse_date_safe(args.from_date)
    if from_date is None:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   f"--from {args.from_date} 不是合法 YYYY-MM-DD")]),
             args.json)
    schedules = data.get("schedules", []) or []
    all_lessons = data.get("lessons", []) or []
    class_schedules = [s for s in schedules if s.get("class_id") == args.class_id]
    class_lessons = [l for l in all_lessons if l.get("class_id") == args.class_id]
    if not class_schedules and not class_lessons:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   f"class {args.class_id} 沒有排課，改用 remove-class")]),
             args.json)

    kept = [l for l in class_lessons
            if (_lesson_date(l) is not None and _lesson_date(l) < from_date)]
    removed = [l for l in class_lessons
               if (_lesson_date(l) is None or _lesson_date(l) >= from_date)]
    removed_lesson_ids = {l.get("id") for l in removed}

    new_data = copy.deepcopy(data)
    new_data["lessons"] = [l for l in new_data.get("lessons", []) or []
                           if l.get("id") not in removed_lesson_ids]
    makeups_reverted = _revert_makeups_for_removed_lessons(new_data, removed_lesson_ids)

    remaining_by_sched = {}
    for l in new_data["lessons"]:
        if l.get("schedule_id"):
            remaining_by_sched.setdefault(l["schedule_id"], 0)
            remaining_by_sched[l["schedule_id"]] += 1
    truncated = []
    removed_ids = []
    for s in class_schedules:
        sid = s.get("id")
        if not sid:
            continue
        affected = any(l.get("schedule_id") == sid for l in removed)
        if remaining_by_sched.get(sid, 0) == 0:
            removed_ids.append(sid)
        elif affected:
            truncated.append(sid)
    new_data["schedules"] = [s for s in new_data.get("schedules", []) or []
                             if s.get("id") not in set(removed_ids)]

    class_has_lessons = any(l.get("class_id") == args.class_id for l in new_data["lessons"])
    class_has_schedules = any(s.get("class_id") == args.class_id for s in new_data["schedules"])
    class_removed = not class_has_lessons and not class_has_schedules
    makeups_removed = 0
    if class_removed:
        new_data["classes"] = [c for c in new_data["classes"] if c.get("id") != args.class_id]
        old_makeups = new_data.get("makeups", []) or []
        makeups_removed = sum(1 for m in old_makeups if m.get("class_id") == args.class_id)
        if makeups_removed:
            new_data["makeups"] = [m for m in old_makeups
                                   if m.get("class_id") != args.class_id]

    success = {
        "ended_class_id": args.class_id,
        "from": args.from_date,
        "kept_lessons": len(kept),
        "removed_lessons": len(removed),
        "schedules_truncated": truncated,
        "schedules_removed": removed_ids,
        "class_removed": class_removed,
        "makeups_removed": makeups_removed,
    }
    if makeups_reverted:
        success["makeups_reverted"] = makeups_reverted
    _commit_or_preview(args, new_data, success)


def cmd_undo(args):
    """復原上一次寫入：還原 .backup/ 最新備份（再 undo 一次 = 還原回去）"""
    backup_dir = _backup_dir(args.file)
    backups = sorted(backup_dir.glob(BACKUP_GLOB)) if backup_dir.exists() else []
    if not backups:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID", "沒有可復原的備份（.backup/ 是空的）")]),
             args.json)
    latest = backups[-1]
    new_data = load_yaml(latest)
    _commit_or_preview(args, new_data, {"restored_from": latest.name},
                       next_actions=["復原後再 undo 一次可還原回復原前的狀態"])


def cmd_cancel_lesson(args):
    """取消某堂課：直接刪該 (class, date) 的 lesson；--makeup 同時登記欠補"""
    data = load_yaml(args.file)
    lessons = data.get("lessons", []) or []
    target_date = _parse_date_safe(args.date)
    if target_date is None:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   f"日期格式錯誤：{args.date}（需 YYYY-MM-DD）")]),
             args.json)
    matched = _match_lessons(lessons, args.class_id, target_date)
    if not matched:
        emit(envelope(False,
                      errors=[_err("E_LESSON_NOT_FOUND",
                                   f"class {args.class_id} 在 {args.date} 沒有課可取消")]),
             args.json)
    if len(matched) > 1:
        emit(envelope(False,
                      errors=[_err("E_AMBIGUOUS_TARGET",
                                   f"{args.class_id} {args.date} 同日有 {len(matched)} 堂",
                                   matches=matched)]),
             args.json)
    src = matched[0]
    new_data = copy.deepcopy(data)
    new_data["lessons"] = [l for l in new_data["lessons"] if l.get("id") != src.get("id")]
    reverted = _revert_makeups_for_removed_lessons(new_data, {src.get("id")})
    success = {"cancelled_date": args.date,
               "lesson_id": src.get("id"),
               "schedule_id": src.get("schedule_id"),
               "reason": args.reason or ""}
    if reverted:
        success["makeups_reverted"] = reverted
    next_actions = None
    if reverted:
        success["makeups_reused"] = reverted
        next_actions = [f"既有待補課 {mu_id} 已恢復；補課日決定後：fulfill-makeup "
                        f"--makeup-id {mu_id} --date <YYYY-MM-DD> --slot <slot> --apply"
                        for mu_id in reverted]
    elif args.makeup:
        makeups = new_data.setdefault("makeups", [])
        mu_id = next_makeup_id(makeups)
        entry = {"id": mu_id,
                 "class_id": args.class_id,
                 "origin_date": args.date,
                 "origin_schedule_id": src.get("schedule_id"),
                 "reason": args.reason or "",
                 "status": "pending",
                 "makeup_date": None,
                 "makeup_lesson_id": None}
        makeups.append(entry)
        success["makeup"] = entry
        next_actions = [f"補課日決定後：fulfill-makeup --makeup-id {mu_id} "
                        f"--date <YYYY-MM-DD> --slot <slot> --apply"]
    _commit_or_preview(args, new_data, success, next_actions=next_actions)


def cmd_fulfill_makeup(args):
    """銷帳一筆待補課：加一筆 standalone lesson + 該筆 makeup 標 fulfilled"""
    data = load_yaml(args.file)
    makeups = data.get("makeups", []) or []
    target = next((m for m in makeups if m.get("id") == args.makeup_id), None)
    if not target:
        emit(envelope(False,
                      errors=[_err("E_MAKEUP_NOT_FOUND",
                                   f"待補課 {args.makeup_id} 不存在",
                                   available=[m.get("id") for m in makeups
                                              if m.get("status", "pending") == "pending"])]),
             args.json)
    if target.get("status") == "fulfilled":
        emit(envelope(False,
                      errors=[_err("E_MAKEUP_ALREADY_FULFILLED",
                                   f"{args.makeup_id} 已於 {target.get('makeup_date')} 補課")]),
             args.json)
    if _parse_date_safe(args.date) is None:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   f"日期格式錯誤：{args.date}（需 YYYY-MM-DD）")]),
             args.json)
    slots = data.get("slots", []) or []
    if not args.slot_id and not args.time:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   "--slot 或 --time 至少要指定一個")]),
             args.json)
    if args.slot_id and not any(s.get("id") == args.slot_id for s in slots):
        emit(envelope(False,
                      errors=[_err("E_SLOT_NOT_FOUND", f"slot {args.slot_id} 不存在")]),
             args.json)
    class_id = target.get("class_id")
    if args.time:
        time_str = args.time
        slot_id = None
    else:
        slot_def = next(s for s in slots if s.get("id") == args.slot_id)
        time_str = slot_def.get("time")
        slot_id = args.slot_id
    note = args.note or f"補課（原 {target.get('origin_date')}）"
    new_data = copy.deepcopy(data)
    new_lesson = _alloc_lessons(data.get("lessons", []) or [], [args.date],
                                class_id=class_id, time_str=time_str,
                                slot_id=slot_id, note=note)[0]
    new_data.setdefault("lessons", []).append(new_lesson)
    _sort_lessons(new_data["lessons"])
    for m in new_data["makeups"]:
        if m.get("id") == args.makeup_id:
            m["status"] = "fulfilled"
            m["makeup_date"] = args.date
            m["makeup_lesson_id"] = new_lesson["id"]
            m.pop("makeup_schedule_id", None)
            break
    _commit_or_preview(args, new_data,
                       {"fulfilled_makeup": args.makeup_id,
                        "makeup_date": args.date,
                        "added_lesson": new_lesson})


def cmd_list_makeups(args):
    """列待補課（預設只列 pending）"""
    data = load_yaml(args.file)
    makeups = data.get("makeups", []) or []
    class_names = {c["id"]: c.get("name") for c in data.get("classes", []) if c.get("id")}
    items = makeups
    if args.class_id:
        items = [m for m in items if m.get("class_id") == args.class_id]
    if args.status != "all":
        items = [m for m in items if m.get("status", "pending") == args.status]
    out = []
    for m in items:
        e = dict(m)
        e["class_name"] = class_names.get(m.get("class_id"))
        out.append(e)
    pending_total = sum(1 for m in makeups if m.get("status", "pending") == "pending")
    emit(envelope(True, data={"makeups": out, "count": len(out),
                              "pending_total": pending_total}), args.json)


def cmd_cancel_makeup(args):
    """撤銷一筆待補課登記（不影響已取消的原課，只是不再欠補）"""
    data = load_yaml(args.file)
    makeups = data.get("makeups", []) or []
    if not any(m.get("id") == args.makeup_id for m in makeups):
        emit(envelope(False,
                      errors=[_err("E_MAKEUP_NOT_FOUND", f"待補課 {args.makeup_id} 不存在")]),
             args.json)
    new_data = copy.deepcopy(data)
    new_data["makeups"] = [m for m in new_data.get("makeups", [])
                           if m.get("id") != args.makeup_id]
    _commit_or_preview(args, new_data, {"cancelled_makeup": args.makeup_id})


def cmd_add_lesson(args):
    """臨時加一堂：直接加一筆 standalone lesson"""
    data = load_yaml(args.file)
    classes = data.get("classes", []) or []
    slots = data.get("slots", []) or []
    if not any(c.get("id") == args.class_id for c in classes):
        emit(envelope(False,
                      errors=[_err("E_CLASS_NOT_FOUND", f"class {args.class_id} 不存在",
                                   available=[c.get("id") for c in classes])]),
             args.json)
    if _parse_date_safe(args.date) is None:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   f"日期格式錯誤：{args.date}（需 YYYY-MM-DD）")]),
             args.json)
    if not args.slot_id and not args.time:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   "--slot 或 --time 至少要指定一個")]),
             args.json)
    if args.slot_id and not any(s.get("id") == args.slot_id for s in slots):
        emit(envelope(False,
                      errors=[_err("E_SLOT_NOT_FOUND", f"slot {args.slot_id} 不存在")]),
             args.json)
    if args.time:
        time_str = args.time
        slot_id = None
    else:
        slot_def = next(s for s in slots if s.get("id") == args.slot_id)
        time_str = slot_def.get("time")
        slot_id = args.slot_id
    new_data = copy.deepcopy(data)
    new_lesson = _alloc_lessons(data.get("lessons", []) or [], [args.date],
                                class_id=args.class_id, time_str=time_str,
                                slot_id=slot_id, note=args.note)[0]
    new_data.setdefault("lessons", []).append(new_lesson)
    _sort_lessons(new_data["lessons"])
    _commit_or_preview(args, new_data, {"added_lesson": new_lesson})


def cmd_update_schedule(args):
    """改一條 schedule：保留 date < 今天的 lessons；今天（含）起的依新參數重生。

    - 有給 pattern / 期間參數（--day/--days/--start/--end/--weeks/--lessons）：
      未來 lessons 刪除重展開（星期沒給就從現有未來 lessons 推導）
    - 只給 --slot/--time/--note：未來 lessons 原地改時段（保留個別挪課過的日期）
    """
    data = load_yaml(args.file)
    schedules = data.get("schedules", []) or []
    slots = data.get("slots", []) or []
    slots_by_id = {s["id"]: s for s in slots if s.get("id")}

    # 1. 目標解析
    target = None
    if args.schedule_id:
        target = next((s for s in schedules if s.get("id") == args.schedule_id), None)
        if target is None:
            available = [s.get("id") for s in schedules if s.get("id")]
            emit(envelope(False,
                          errors=[_err("E_SCHEDULE_NOT_FOUND",
                                       f"schedule {args.schedule_id} 不存在",
                                       available=available)],
                          next_actions=["用 list-classes --with-schedules 查現有 schedule id"]),
                 args.json)
    elif args.class_id:
        candidates = [s for s in schedules if s.get("class_id") == args.class_id]
        if len(candidates) == 0:
            emit(envelope(False,
                          errors=[_err("E_SCHEDULE_NOT_FOUND",
                                       f"class {args.class_id} 沒有任何 schedule")],
                          next_actions=["用 add-schedule 新增"]),
                 args.json)
        elif len(candidates) == 1:
            target = candidates[0]
        else:
            candidates_info = [
                {"id": c.get("id"),
                 "label": c.get("label"),
                 "time": c.get("time")}
                for c in candidates
            ]
            emit(envelope(False,
                          errors=[_err("E_AMBIGUOUS_TARGET",
                                       f"class {args.class_id} 有 {len(candidates)} 條 schedule，請用 --schedule-id 指定",
                                       candidates=candidates_info)],
                          next_actions=[f"加 --schedule-id {c['id']} 重試" for c in candidates_info]),
                 args.json)
    else:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   "請指定 --schedule-id 或 --class")]),
             args.json)

    # 2. 欄位驗證
    edit_fields = [args.start, args.end, args.weeks, args.lessons,
                   args.day, args.days, args.slot_id, args.time, args.note]
    if all(f is None for f in edit_fields):
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID", "沒有要改的欄位，請指定至少一個編輯欄位")]),
             args.json)

    # weeks/end/lessons 三擇一
    termination_count = sum(1 for x in [args.weeks, args.end, args.lessons] if x is not None)
    if termination_count > 1:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   "--weeks / --end / --lessons 三者只能指定一個")]),
             args.json)

    # day 和 days 不能並存
    if args.day is not None and args.days is not None:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   "--day 和 --days 不能同時指定")]),
             args.json)

    # 星期值驗證
    if args.day is not None:
        bad_days = [args.day] if args.day not in DAY_NAMES else []
    elif args.days is not None:
        bad_days = [d for d in _parse_days(args.days) if d not in DAY_NAMES]
    else:
        bad_days = []
    if bad_days:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   f"不合法的星期值：{','.join(bad_days)}",
                                   allowed=list(DAY_NAMES))]),
             args.json)

    # 日期格式驗證
    if args.start and _parse_date_safe(args.start) is None:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   f"--start {args.start} 不是合法 YYYY-MM-DD")]),
             args.json)
    if args.end and _parse_date_safe(args.end) is None:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   f"--end {args.end} 不是合法 YYYY-MM-DD")]),
             args.json)

    # slot 存在驗證
    if args.slot_id and args.slot_id not in slots_by_id:
        emit(envelope(False,
                      errors=[_err("E_SLOT_NOT_FOUND",
                                   f"slot {args.slot_id} 不存在",
                                   available=list(slots_by_id.keys()))]),
             args.json)

    # 3. 套用（深拷貝後修改 metadata）
    new_data = copy.deepcopy(data)
    new_sched = next(s for s in new_data["schedules"] if s.get("id") == target.get("id"))

    changed_fields = []

    if args.slot_id is not None:
        if new_sched.get("slot_id") != args.slot_id:
            changed_fields.append("slot_id")
        new_sched["slot_id"] = args.slot_id
        # 若沒同時給 --time，把凍結 time 換成該 slot 的 time
        if args.time is None:
            slot_def = slots_by_id[args.slot_id]
            new_time = slot_def.get("time")
            if new_sched.get("time") != new_time:
                changed_fields.append("time")
            new_sched["time"] = new_time

    if args.time is not None:
        if new_sched.get("time") != args.time:
            if "time" not in changed_fields:
                changed_fields.append("time")
        new_sched["time"] = args.time

    if args.note is not None:
        if new_sched.get("note") != args.note:
            changed_fields.append("note")
        new_sched["note"] = args.note

    for flag, field in [(args.start, "start_date"), (args.end, "end_date"),
                        (args.weeks, "duration_weeks"), (args.lessons, "total_lessons"),
                        (args.day, "day"), (args.days, "days")]:
        if flag is not None:
            changed_fields.append(field)

    # lesson 生效時段：metadata time（凍結顯示值）優先，無則查 slot
    eff_time = new_sched.get("time")
    if not eff_time and new_sched.get("slot_id"):
        eff_time = slots_by_id.get(new_sched["slot_id"], {}).get("time")

    # 4. lessons 分段：過去保留、未來重生 / 原地改
    today = date.today()
    sid = target.get("id")
    all_lessons = new_data.get("lessons", []) or []
    sched_lessons = [l for l in all_lessons if l.get("schedule_id") == sid]
    past = [l for l in sched_lessons
            if (_lesson_date(l) is not None and _lesson_date(l) < today)]
    future = [l for l in sched_lessons
              if (_lesson_date(l) is None or _lesson_date(l) >= today)]
    lessons_before = len(sched_lessons)

    pattern_regen = any(x is not None for x in
                        (args.day, args.days, args.start, args.end,
                         args.weeks, args.lessons))

    if pattern_regen:
        # 星期：args 優先；沒給就從現有未來 lessons 推導
        if args.day is not None:
            day, days = args.day, None
        elif args.days is not None:
            day, days = None, _parse_days(args.days)
        else:
            wd = sorted({_lesson_date(l).weekday() for l in future if _lesson_date(l)})
            if not wd:
                emit(envelope(False,
                              errors=[_err("E_SCHEMA_INVALID",
                                           f"schedule {sid} 已無未來 lessons 可推導星期，"
                                           "請指定 --day 或 --days")]),
                     args.json)
            derived = [DAY_NAMES[i] for i in wd]
            day, days = (derived[0], None) if len(derived) == 1 else (None, derived)
        # 起始：args 優先；否則從最早未來 lesson 起；再否則今天
        if args.start:
            start = args.start
        elif future:
            start = min(str(l.get("date")) for l in future)
        else:
            start = str(today)
        # 終止：args 優先；否則保持原未來堂數
        weeks, end, total = args.weeks, args.end, args.lessons
        if weeks is None and end is None and total is None:
            if not future:
                emit(envelope(False,
                              errors=[_err("E_NO_TERMINATION",
                                           "已無未來 lessons，需指定 --weeks / --end / --lessons")]),
                     args.json)
            total = len(future)
        try:
            dates = expand_pattern_dates(day=day, days=days, start=start,
                                         end=end, weeks=weeks, total_lessons=total)
        except ValueError as e:
            emit(envelope(False, errors=[_err("E_SCHEMA_INVALID", str(e))]), args.json)
        # 只重生今天（含）以後；過去 lessons 一律保留
        dates = [d for d in dates if d >= today]
        if not eff_time:
            emit(envelope(False,
                          errors=[_err("E_SCHEMA_INVALID",
                                       "無法決定時段，請指定 --slot 或 --time")]),
                 args.json)
        future_ids = {l.get("id") for l in future}
        new_data["lessons"] = [l for l in all_lessons if l.get("id") not in future_ids]
        makeups_reverted = _revert_makeups_for_removed_lessons(new_data, future_ids)
        new_lessons = _alloc_lessons(data.get("lessons", []) or [], dates,
                                     class_id=target.get("class_id"),
                                     time_str=eff_time,
                                     schedule_id=sid,
                                     slot_id=new_sched.get("slot_id"))
        new_data["lessons"].extend(new_lessons)
        _sort_lessons(new_data["lessons"])
        # label 跟著新 pattern 更新
        new_label = make_label(day=day, days=days)
        if new_label and new_sched.get("label") != new_label:
            new_sched["label"] = new_label
            if "label" not in changed_fields:
                changed_fields.append("label")
        after_dates = [str(l.get("date")) for l in past] + [str(d) for d in dates]
        lessons_after = len(past) + len(dates)
    else:
        # 只改時段/備註：未來 lessons 原地改，日期不動
        makeups_reverted = []
        if ("time" in changed_fields or "slot_id" in changed_fields) and eff_time:
            future_ids = {l.get("id") for l in future}
            for l in new_data["lessons"]:
                if l.get("id") in future_ids:
                    l["time"] = eff_time
                    if args.slot_id is not None:
                        l["slot_id"] = args.slot_id
        after_dates = [str(l.get("date")) for l in sched_lessons]
        lessons_after = lessons_before

    # 5. 統計（v4：過去 lessons 永不刪除 → past_lessons_lost 恆 0，欄位沿用）
    after_sorted = sorted(after_dates)
    first_lesson = after_sorted[0] if after_sorted else None
    last_lesson = after_sorted[-1] if after_sorted else None
    past_lessons_lost = 0

    success_data = {
        "schedule_id": target.get("id"),
        "changed_fields": changed_fields,
        "lessons_before": lessons_before,
        "lessons_after": lessons_after,
        "first_lesson": first_lesson,
        "last_lesson": last_lesson,
        "past_lessons_lost": past_lessons_lost,
    }
    if makeups_reverted:
        success_data["makeups_reverted"] = makeups_reverted

    next_actions = []
    if past_lessons_lost > 0:
        next_actions.append(
            f"警告：改動將使 {past_lessons_lost} 堂已過去的課消失，確認後再 --apply"
        )

    _commit_or_preview(args, new_data, success_data, next_actions=next_actions)


def _schedule_weekdays(lessons, schedule_id):
    """schedule 底下 lessons 的星期集合（remove-schedule --day 過濾用）"""
    return {DAY_NAMES[_lesson_date(l).weekday()]
            for l in lessons
            if l.get("schedule_id") == schedule_id and _lesson_date(l) is not None}


def cmd_remove_schedule(args):
    """刪 schedule + 其全部 lessons"""
    data = load_yaml(args.file)
    schedules = data.get("schedules", []) or []
    all_lessons = data.get("lessons", []) or []

    def _remove(ids):
        id_set = set(ids)
        new_data = copy.deepcopy(data)
        new_data["schedules"] = [s for s in new_data["schedules"]
                                 if s.get("id") not in id_set]
        removed_lesson_ids = {l.get("id") for l in new_data.get("lessons", []) or []
                              if l.get("schedule_id") in id_set}
        new_data["lessons"] = [l for l in new_data.get("lessons", []) or []
                               if l.get("schedule_id") not in id_set]
        makeups_reverted = _revert_makeups_for_removed_lessons(new_data, removed_lesson_ids)
        return new_data, len(removed_lesson_ids), makeups_reverted

    # 若給了 --schedule-id，直接刪那條
    if getattr(args, "schedule_id", None):
        target = next((s for s in schedules if s.get("id") == args.schedule_id), None)
        if target is None:
            available = [s.get("id") for s in schedules if s.get("id")]
            emit(envelope(False,
                          errors=[_err("E_SCHEDULE_NOT_FOUND",
                                       f"schedule {args.schedule_id} 不存在",
                                       available=available)]),
                 args.json)
        new_data, removed_lessons, makeups_reverted = _remove([args.schedule_id])
        success = {"removed_count": 1, "removed_id": args.schedule_id,
                   "removed_lessons": removed_lessons}
        if makeups_reverted:
            success["makeups_reverted"] = makeups_reverted
        _commit_or_preview(args, new_data, success)
        return

    if not args.class_id:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   "請指定 --schedule-id 或 --class")]),
             args.json)

    matched_ids = []
    for s in schedules:
        if s.get("class_id") != args.class_id:
            continue
        if args.slot_id and s.get("slot_id") != args.slot_id:
            continue
        if args.day and args.day not in _schedule_weekdays(all_lessons, s.get("id")):
            continue
        matched_ids.append(s.get("id"))
    if not matched_ids:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   f"找不到 class={args.class_id} slot={args.slot_id} day={args.day} 的 schedule")]),
             args.json)
    if len(matched_ids) > 1 and not args.all:
        emit(envelope(False,
                      errors=[_err("E_AMBIGUOUS_TARGET",
                                   f"找到 {len(matched_ids)} 條匹配",
                                   matches=[s for s in schedules
                                            if s.get("id") in set(matched_ids)])],
                      next_actions=["加 --all 全刪，或縮小條件（--slot-id / --day）"]),
             args.json)
    new_data, removed_lessons, makeups_reverted = _remove(matched_ids)
    success = {"removed_count": len(matched_ids),
               "removed_lessons": removed_lessons}
    if makeups_reverted:
        success["makeups_reverted"] = makeups_reverted
    _commit_or_preview(args, new_data, success)


# -------------- argparse --------------

def build_parser():
    p = argparse.ArgumentParser(description="LLM-friendly schedule CLI")
    p.add_argument("--file", default=str(DEFAULT_YAML))
    p.add_argument("--json", action="store_true", help="JSON 輸出（預設 plain）")
    p.add_argument("--strict", action="store_true", help="strict validate 模式")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="健康摘要 + 衝突 + 本週課表")

    lc = sub.add_parser("list-classes", help="列所有 class")
    lc.add_argument("--with-schedules", action="store_true")

    ls = sub.add_parser("list-slots", help="列所有 slot")
    ls.add_argument("--used-only", action="store_true")

    sub.add_parser("list-conflicts", help="只列時段衝突")

    ac = sub.add_parser("add-class", help="新增 class")
    ac.add_argument("--id", help="省略則自動編下一個 STU-NN")
    ac.add_argument("--name", required=True)
    ac.add_argument("--weekly-count", type=int, required=True)
    ac.add_argument("--level")
    ac.add_argument("--note")
    ac.add_argument("--apply", action="store_true", help="實際寫入（預設只 preview）")

    uc = sub.add_parser("update-class", help="改 class 欄位")
    uc.add_argument("--id", required=True)
    uc.add_argument("--name")
    uc.add_argument("--weekly-count", type=int)
    uc.add_argument("--level")
    uc.add_argument("--note")
    uc.add_argument("--apply", action="store_true")

    rc = sub.add_parser("remove-class", help="刪 class")
    rc.add_argument("--id", required=True)
    rc.add_argument("--cascade", action="store_true", help="連帶刪 schedule/lesson")
    rc.add_argument("--apply", action="store_true")

    asd = sub.add_parser("add-schedule", help="新增 schedule（當場展開成 lessons）")
    asd.add_argument("--class", dest="class_id", required=True)
    asd.add_argument("--slot", dest="slot_id", help="當下常用時段別名（list-slots 看清單）；可省略改用 --time")
    asd.add_argument("--time", help="HH:MM-HH:MM 直接指定時段（任意插時，不需 slot 別名）")
    asd.add_argument("--start", help="start_date YYYY-MM-DD（day/days 模式必填）")
    asd.add_argument("--day", help="單 day（mon|tue|...）")
    asd.add_argument("--days", help="多 day（逗號分隔）")
    asd.add_argument("--specific-dates", help="指定日期（逗號分隔）")
    asd.add_argument("--weeks", type=int, help="展開週數")
    asd.add_argument("--end", help="展開終止日 YYYY-MM-DD")
    asd.add_argument("--lessons", type=int, help="展開總堂數")
    asd.add_argument("--note")
    asd.add_argument("--apply", action="store_true")

    ss = sub.add_parser("split-schedule", help="切某條 schedule（過去不動 + 改未來）")
    ss.add_argument("--schedule-id", help="目標 schedule id（例 SCH-005）")
    ss.add_argument("--class", dest="class_id", help="或指定 class（自動找該 class 唯一的 schedule）")
    ss.add_argument("--at", required=True, help="從哪天開始改 YYYY-MM-DD（這天起算後段）")
    ss.add_argument("--day", help="後段 single day（不寫則沿用前段）")
    ss.add_argument("--days", help="後段 multi days（逗號分隔）")
    ss.add_argument("--to-slot", help="後段 slot 別名（不寫則沿用前段）")
    ss.add_argument("--to-time", help="後段時段 HH:MM-HH:MM（不寫則沿用前段）")
    ss.add_argument("--weeks", type=int, help="後段展開週數")
    ss.add_argument("--end", help="後段終止日 YYYY-MM-DD")
    ss.add_argument("--lessons", type=int, help="後段總堂數")
    ss.add_argument("--note", help="後段備註（例「開學換時段」）")
    ss.add_argument("--apply", action="store_true")

    ec = sub.add_parser("end-class", help="結束班級（保留 from 之前已上堂次，移除之後）")
    ec.add_argument("--class", dest="class_id", required=True)
    ec.add_argument("--from", dest="from_date", required=True,
                    help="從這天起不再上 YYYY-MM-DD")
    ec.add_argument("--apply", action="store_true")

    ud = sub.add_parser("undo", help="復原上一次寫入（.backup/ 最新備份）")
    ud.add_argument("--apply", action="store_true")

    cl = sub.add_parser("cancel-lesson", help="取消某堂課（--makeup 則登記待補）")
    cl.add_argument("--class", dest="class_id", required=True)
    cl.add_argument("--date", required=True, help="要取消的日期 YYYY-MM-DD")
    cl.add_argument("--reason", help="備註原因（教練生病、學員請假等）")
    cl.add_argument("--makeup", action="store_true",
                    help="登記為待補課（欠補），補課日決定後用 fulfill-makeup 銷帳")
    cl.add_argument("--apply", action="store_true")

    fm = sub.add_parser("fulfill-makeup", help="銷帳一筆待補課（新增補課那堂）")
    fm.add_argument("--makeup-id", dest="makeup_id", required=True, help="待補課 id（例 MU-001）")
    fm.add_argument("--date", required=True, help="補課日期 YYYY-MM-DD")
    fm.add_argument("--slot", dest="slot_id", help="常用時段別名")
    fm.add_argument("--time", help="HH:MM-HH:MM 直接寫")
    fm.add_argument("--note")
    fm.add_argument("--apply", action="store_true")

    lm = sub.add_parser("list-makeups", help="列待補課（預設只列 pending）")
    lm.add_argument("--class", dest="class_id", help="只列某班")
    lm.add_argument("--status", choices=["pending", "fulfilled", "all"],
                    default="pending", help="預設 pending")

    cm = sub.add_parser("cancel-makeup", help="撤銷一筆待補課登記（不再欠補）")
    cm.add_argument("--makeup-id", dest="makeup_id", required=True)
    cm.add_argument("--apply", action="store_true")

    al = sub.add_parser("add-lesson", help="臨時加一堂（單日 standalone lesson）")
    al.add_argument("--class", dest="class_id", required=True)
    al.add_argument("--date", required=True, help="新加課的日期 YYYY-MM-DD")
    al.add_argument("--slot", dest="slot_id", help="常用時段別名")
    al.add_argument("--time", help="HH:MM-HH:MM 直接寫")
    al.add_argument("--note")
    al.add_argument("--apply", action="store_true")

    ml = sub.add_parser("move-lesson", help="挪一堂課（原地改該筆 lesson 的日期/時段）")
    ml.add_argument("--class", dest="class_id", required=True)
    ml.add_argument("--from-date", required=True, help="原本上課日 YYYY-MM-DD")
    ml.add_argument("--to-date", required=True, help="改到哪天 YYYY-MM-DD")
    ml.add_argument("--to-slot", help="改用哪個 slot（不變就不寫）")
    ml.add_argument("--to-time", help="改成什麼時段（不變就不寫）")
    ml.add_argument("--note", help="備註（例如「7/20 颱風停課改補」）")
    ml.add_argument("--apply", action="store_true")

    up = sub.add_parser("update-schedule", help="改一條 schedule（過去保留、未來重生）")
    up.add_argument("--schedule-id", dest="schedule_id")
    up.add_argument("--class", dest="class_id")
    up.add_argument("--start", help="重展開起始日 YYYY-MM-DD")
    up.add_argument("--end", help="重展開終止日 YYYY-MM-DD（三擇一）")
    up.add_argument("--weeks", type=int, help="重展開週數（三擇一）")
    up.add_argument("--lessons", type=int, help="重展開總堂數（三擇一）")
    up.add_argument("--day", help="改成單 day（mon|tue|...）")
    up.add_argument("--days", help="改成多 day（逗號分隔）")
    up.add_argument("--slot", dest="slot_id", help="改成指定 slot id")
    up.add_argument("--time", help="改成 HH:MM-HH:MM 時段")
    up.add_argument("--note", help="備註")
    up.add_argument("--apply", action="store_true")

    rs = sub.add_parser("remove-schedule", help="刪 schedule + 其全部 lessons（按 schedule-id 或 class + 可選 slot/day）")
    rs.add_argument("--schedule-id", dest="schedule_id", help="直接指定 schedule id 刪除")
    rs.add_argument("--class", dest="class_id")
    rs.add_argument("--slot-id")
    rs.add_argument("--day")
    rs.add_argument("--all", action="store_true", help="允許刪多條")
    rs.add_argument("--apply", action="store_true")

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    dispatch = {
        "status": cmd_status,
        "list-classes": cmd_list_classes,
        "list-slots": cmd_list_slots,
        "list-conflicts": cmd_list_conflicts,
        "add-class": cmd_add_class,
        "update-class": cmd_update_class,
        "remove-class": cmd_remove_class,
        "end-class": cmd_end_class,
        "undo": cmd_undo,
        "add-schedule": cmd_add_schedule,
        "update-schedule": cmd_update_schedule,
        "remove-schedule": cmd_remove_schedule,
        "move-lesson": cmd_move_lesson,
        "split-schedule": cmd_split_schedule,
        "cancel-lesson": cmd_cancel_lesson,
        "add-lesson": cmd_add_lesson,
        "fulfill-makeup": cmd_fulfill_makeup,
        "list-makeups": cmd_list_makeups,
        "cancel-makeup": cmd_cancel_makeup,
    }
    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
