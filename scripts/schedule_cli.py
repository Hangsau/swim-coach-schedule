#!/usr/bin/env python3
"""
schedule_cli.py — LLM-friendly CLI for swim-coach-schedule

設計目標：minimax LLM 經 TG 收到自然語言指令後，透過此 CLI 操作系統，
不直接編輯 yaml。每個寫入命令：
  - 預設 dry-run（preview diff），實寫入需 --apply
  - 寫入前自動 validate；fail 拒絕並回 structured JSON
  - atomic write：先寫 temp + validate → 通過才 replace
  - JSON envelope: {"ok", "data", "errors", "warnings", "next_actions"}

子命令：
  status / list-classes / list-slots / list-conflicts
  add-class / update-class / remove-class
  add-schedule / remove-schedule / move-lesson
  preview-add-schedule

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
from query import expand_schedule  # noqa: E402


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


# -------------- commands: read --------------

def cmd_status(args):
    result = validate_all(args.file, strict=args.strict)
    data = load_yaml(args.file)
    slots_by_id = {s["id"]: s for s in data.get("slots", []) if s.get("id")}
    classes_by_id = {c["id"]: c for c in data.get("classes", []) if c.get("id")}
    lessons = expand_schedule(data.get("schedules", []) or [], slots_by_id, classes_by_id)
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
    next_actions = []
    if not result["ok"]:
        next_actions.append("先跑 list-conflicts 看細節，依 error code 處理")
    if orphan_classes:
        next_actions.append(f"孤兒 class（無 schedule）: {orphan_classes}；用 add-schedule 或 remove-class")
    env = envelope(
        result["ok"],
        data={
            "stats": result["stats"],
            "upcoming_7d": upcoming,
            "orphan_classes": orphan_classes,
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
    used_set = {s.get("slot_id") for s in schedules}
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
    refs = [s for s in schedules if s.get("class_id") == args.id]
    if refs and not args.cascade:
        emit(envelope(False,
                      errors=[_err("E_AMBIGUOUS_TARGET",
                                   f"class {args.id} 仍有 {len(refs)} 條 schedule 引用",
                                   schedule_count=len(refs))],
                      next_actions=["加 --cascade 連帶刪除 schedule，或先 remove-schedule"]),
             args.json)
    new_data = copy.deepcopy(data)
    new_data["classes"] = [c for c in new_data["classes"] if c.get("id") != args.id]
    if args.cascade:
        new_data["schedules"] = [s for s in new_data.get("schedules", []) if s.get("class_id") != args.id]
    _commit_or_preview(args, new_data,
                       {"removed_class_id": args.id, "cascaded_schedules": len(refs) if args.cascade else 0})


def cmd_add_schedule(args):
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
    # 自動賦予 schedule id
    existing_ids = [s.get("id", "") for s in data.get("schedules", []) if s.get("id", "").startswith("SCH-")]
    max_n = max([int(x.split("-")[1]) for x in existing_ids if x.split("-")[1].isdigit()] + [0])
    new_id = f"SCH-{max_n + 1:03d}"
    new_sched = {"id": new_id, "class_id": args.class_id}
    if args.slot_id:
        new_sched["slot_id"] = args.slot_id
    # time 凍結值：優先 --time，否則從 slot 抄
    if args.time:
        new_sched["time"] = args.time
    elif args.slot_id:
        slot_def = next(s for s in slots if s.get("id") == args.slot_id)
        new_sched["time"] = slot_def.get("time")
    if args.start:
        new_sched["start_date"] = args.start
    if args.day:
        new_sched["day"] = args.day
    elif args.days:
        new_sched["days"] = _parse_days(args.days)
    elif args.specific_dates:
        new_sched["specific_dates"] = _parse_specific_dates(args.specific_dates)
    if args.weeks is not None:
        new_sched["duration_weeks"] = args.weeks
    if args.end:
        new_sched["end_date"] = args.end
    if args.lessons is not None:
        new_sched["total_lessons"] = args.lessons
    if args.note:
        new_sched["note"] = args.note
    new_data = copy.deepcopy(data)
    new_data.setdefault("schedules", []).append(new_sched)
    _commit_or_preview(args, new_data, {"added_schedule": new_sched})


def cmd_move_lesson(args):
    """挪一堂課：在原 schedule 加 except_dates 排除原日 + 新增 specific_dates 補課條目"""
    data = load_yaml(args.file)
    schedules = data.get("schedules", []) or []
    slots_by_id = {s["id"]: s for s in data.get("slots", []) if s.get("id")}
    classes_by_id = {c["id"]: c for c in data.get("classes", []) if c.get("id")}
    # 找命中該 (class, from_date) 的 schedule（透過 expand 確認原日真的有課）
    lessons = expand_schedule(schedules, slots_by_id, classes_by_id)
    from_date = datetime.strptime(args.from_date, "%Y-%m-%d").date()
    matched = [l for l in lessons if l["class_id"] == args.class_id and l["date"] == from_date]
    if not matched:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   f"class {args.class_id} 在 {args.from_date} 沒有原本的課")]),
             args.json)
    if len(matched) > 1:
        emit(envelope(False,
                      errors=[_err("E_AMBIGUOUS_TARGET",
                                   f"{args.class_id} {args.from_date} 同日有 {len(matched)} 堂",
                                   matches=matched)]),
             args.json)
    src = matched[0]
    src_sched_id = src.get("schedule_id")
    if not src_sched_id:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   "原 schedule 沒有 id（需要 migration）")]),
             args.json)
    new_data = copy.deepcopy(data)
    # 在原 schedule 加 except_dates
    for s in new_data["schedules"]:
        if s.get("id") == src_sched_id:
            ed = [str(x) for x in (s.get("except_dates") or [])]
            ed.append(args.from_date)
            s["except_dates"] = sorted(ed)
            break
    # 新增 specific_dates 補課條目
    existing_ids = [s.get("id", "") for s in new_data["schedules"] if s.get("id", "").startswith("SCH-")]
    max_n = max([int(x.split("-")[1]) for x in existing_ids if x.split("-")[1].isdigit()] + [0])
    makeup = {
        "id": f"SCH-{max_n + 1:03d}",
        "class_id": args.class_id,
        "specific_dates": [args.to_date],
    }
    if args.to_time:
        makeup["time"] = args.to_time
    elif args.to_slot:
        slot = slots_by_id.get(args.to_slot)
        if not slot:
            emit(envelope(False,
                          errors=[_err("E_SLOT_NOT_FOUND", f"slot {args.to_slot} 不存在")]),
                 args.json)
        makeup["slot_id"] = args.to_slot
        makeup["time"] = slot.get("time")
    else:
        # 同時段移到別日
        makeup["time"] = src.get("slot_time")
        if src.get("slot_id"):
            makeup["slot_id"] = src.get("slot_id")
    if args.note:
        makeup["note"] = args.note
    new_data["schedules"].append(makeup)
    _commit_or_preview(args, new_data,
                       {"moved_from": {"date": args.from_date, "schedule_id": src_sched_id},
                        "moved_to": makeup})


def cmd_split_schedule(args):
    """切某條 schedule：在某日截斷 + 新建後段。過去不動。"""
    data = load_yaml(args.file)
    schedules = data.get("schedules", []) or []
    slots_by_id = {s["id"]: s for s in data.get("slots", []) if s.get("id")}
    at_date = datetime.strptime(args.at, "%Y-%m-%d").date()

    # 找 schedule
    target = None
    if args.schedule_id:
        target = next((s for s in schedules if s.get("id") == args.schedule_id), None)
    else:
        cands = [s for s in schedules if s.get("class_id") == args.class_id
                 and ("day" in s or "days" in s)]  # specific_dates 不適用 split
        if not cands:
            emit(envelope(False,
                          errors=[_err("E_SCHEMA_INVALID",
                                       f"class {args.class_id} 沒有可 split 的 schedule（只支援 day/days mode）")]),
                 args.json)
        if len(cands) > 1:
            emit(envelope(False,
                          errors=[_err("E_AMBIGUOUS_TARGET",
                                       f"class {args.class_id} 有 {len(cands)} 條 day/days schedule",
                                       matches=[{"id": c.get("id"),
                                                 "day": c.get("day"),
                                                 "days": c.get("days")} for c in cands])],
                          next_actions=["改用 --schedule-id 指定（例：SCH-005）"]),
                 args.json)
        target = cands[0]
    if target is None:
        emit(envelope(False, errors=[_err("E_SCHEMA_INVALID", "找不到目標 schedule")]), args.json)
    if "specific_dates" in target:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   "specific_dates 模式不可 split（直接 remove + add）")]),
             args.json)

    new_data = copy.deepcopy(data)
    # 在新版找對應條目
    new_target = next(s for s in new_data["schedules"] if s.get("id") == target.get("id"))

    cutoff = at_date - timedelta(days=1)  # 前段最後一天
    new_target["end_date"] = str(cutoff)
    # 移除其他終止條件（避免衝突）
    new_target.pop("duration_weeks", None)
    new_target.pop("total_lessons", None)

    # 建後段
    existing_ids = [s.get("id", "") for s in new_data["schedules"] if s.get("id", "").startswith("SCH-")]
    max_n = max([int(x.split("-")[1]) for x in existing_ids if x.split("-")[1].isdigit()] + [0])
    after = {"id": f"SCH-{max_n + 1:03d}",
             "class_id": target["class_id"],
             "start_date": args.at}

    # day/days 來自 args；沒指定就沿用原 schedule
    if args.day:
        after["day"] = args.day
    elif args.days:
        after["days"] = [d.strip().lower() for d in args.days.split(",") if d.strip()]
    elif "day" in target:
        after["day"] = target["day"]
    elif "days" in target:
        after["days"] = list(target["days"])

    # slot/time 來自 args；沒指定就沿用
    if args.to_time:
        after["time"] = args.to_time
    elif args.to_slot:
        slot = slots_by_id.get(args.to_slot)
        if not slot:
            emit(envelope(False, errors=[_err("E_SLOT_NOT_FOUND",
                                              f"slot {args.to_slot} 不存在")]), args.json)
        after["slot_id"] = args.to_slot
        after["time"] = slot.get("time")
    else:
        if target.get("slot_id"):
            after["slot_id"] = target["slot_id"]
        if target.get("time"):
            after["time"] = target["time"]

    # 終止條件（前段被截斷了，後段必須明確指定一個；不沿用前段 end_date）
    if args.weeks is not None:
        after["duration_weeks"] = args.weeks
    elif args.end:
        after["end_date"] = args.end
    elif args.lessons is not None:
        after["total_lessons"] = args.lessons
    else:
        emit(envelope(False,
                      errors=[_err("E_NO_TERMINATION",
                                   "後段需要終止條件：--weeks N / --end YYYY-MM-DD / --lessons N")],
                      next_actions=["再跑一次加上 --weeks 12 或 --end 2026-12-31 等"]),
             args.json)

    if args.note:
        after["note"] = args.note

    new_data["schedules"].append(after)
    _commit_or_preview(args, new_data,
                       {"split_at": args.at,
                        "before": {"id": target.get("id"), "end_date": str(cutoff)},
                        "after": after})


def cmd_cancel_lesson(args):
    """取消某堂課不補：在原 schedule 加 except_dates"""
    data = load_yaml(args.file)
    schedules = data.get("schedules", []) or []
    slots_by_id = {s["id"]: s for s in data.get("slots", []) if s.get("id")}
    classes_by_id = {c["id"]: c for c in data.get("classes", []) if c.get("id")}
    lessons = expand_schedule(schedules, slots_by_id, classes_by_id)
    target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    matched = [l for l in lessons if l["class_id"] == args.class_id and l["date"] == target_date]
    if not matched:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   f"class {args.class_id} 在 {args.date} 沒有課可取消")]),
             args.json)
    if len(matched) > 1:
        emit(envelope(False,
                      errors=[_err("E_AMBIGUOUS_TARGET",
                                   f"{args.class_id} {args.date} 同日有 {len(matched)} 堂",
                                   matches=matched)]),
             args.json)
    src = matched[0]
    src_sched_id = src.get("schedule_id")
    if not src_sched_id:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   "原 schedule 沒有 id（需要 migration）")]),
             args.json)
    new_data = copy.deepcopy(data)
    for s in new_data["schedules"]:
        if s.get("id") == src_sched_id:
            ed = [str(x) for x in (s.get("except_dates") or [])]
            if args.date in ed:
                emit(envelope(False,
                              errors=[_err("E_DUPLICATE_SCHEDULE",
                                           f"{args.date} 已在 except_dates 內")]),
                     args.json)
            ed.append(args.date)
            s["except_dates"] = sorted(ed)
            break
    _commit_or_preview(args, new_data,
                       {"cancelled_date": args.date,
                        "schedule_id": src_sched_id,
                        "reason": args.reason or ""})


def cmd_add_lesson(args):
    """臨時加一堂（單日 specific_dates 簡化版）"""
    data = load_yaml(args.file)
    classes = data.get("classes", []) or []
    slots = data.get("slots", []) or []
    if not any(c.get("id") == args.class_id for c in classes):
        emit(envelope(False,
                      errors=[_err("E_CLASS_NOT_FOUND", f"class {args.class_id} 不存在",
                                   available=[c.get("id") for c in classes])]),
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
    new_data = copy.deepcopy(data)
    existing_ids = [s.get("id", "") for s in new_data.get("schedules", []) if s.get("id", "").startswith("SCH-")]
    max_n = max([int(x.split("-")[1]) for x in existing_ids if x.split("-")[1].isdigit()] + [0])
    new_sched = {"id": f"SCH-{max_n + 1:03d}",
                 "class_id": args.class_id,
                 "specific_dates": [args.date]}
    if args.time:
        new_sched["time"] = args.time
    elif args.slot_id:
        slot_def = next(s for s in slots if s.get("id") == args.slot_id)
        new_sched["slot_id"] = args.slot_id
        new_sched["time"] = slot_def.get("time")
    if args.note:
        new_sched["note"] = args.note
    new_data.setdefault("schedules", []).append(new_sched)
    _commit_or_preview(args, new_data, {"added_lesson": new_sched})


def cmd_remove_schedule(args):
    data = load_yaml(args.file)
    schedules = data.get("schedules", []) or []
    matched_idx = []
    for i, s in enumerate(schedules):
        if s.get("class_id") != args.class_id:
            continue
        if args.slot_id and s.get("slot_id") != args.slot_id:
            continue
        if args.day and s.get("day") != args.day:
            continue
        matched_idx.append(i)
    if not matched_idx:
        emit(envelope(False,
                      errors=[_err("E_SCHEMA_INVALID",
                                   f"找不到 class={args.class_id} slot={args.slot_id} day={args.day} 的 schedule")]),
             args.json)
    if len(matched_idx) > 1 and not args.all:
        emit(envelope(False,
                      errors=[_err("E_AMBIGUOUS_TARGET",
                                   f"找到 {len(matched_idx)} 條匹配",
                                   matches=[schedules[i] for i in matched_idx])],
                      next_actions=["加 --all 全刪，或縮小條件（--slot-id / --day）"]),
             args.json)
    new_data = copy.deepcopy(data)
    keep = []
    for i, s in enumerate(schedules):
        if i not in matched_idx:
            keep.append(s)
    new_data["schedules"] = keep
    _commit_or_preview(args, new_data, {"removed_count": len(matched_idx)})


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
    rc.add_argument("--cascade", action="store_true", help="連帶刪 schedule")
    rc.add_argument("--apply", action="store_true")

    asd = sub.add_parser("add-schedule", help="新增 schedule")
    asd.add_argument("--class", dest="class_id", required=True)
    asd.add_argument("--slot", dest="slot_id", help="當下常用時段別名（list-slots 看清單）；可省略改用 --time")
    asd.add_argument("--time", help="HH:MM-HH:MM 直接指定時段（任意插時，不需 slot 別名）")
    asd.add_argument("--start", help="start_date YYYY-MM-DD")
    asd.add_argument("--day", help="單 day（mon|tue|...）")
    asd.add_argument("--days", help="多 day（逗號分隔）")
    asd.add_argument("--specific-dates", help="指定日期（逗號分隔）")
    asd.add_argument("--weeks", type=int, help="duration_weeks")
    asd.add_argument("--end", help="end_date YYYY-MM-DD")
    asd.add_argument("--lessons", type=int, help="total_lessons")
    asd.add_argument("--note")
    asd.add_argument("--apply", action="store_true")

    ss = sub.add_parser("split-schedule", help="切某條 schedule（過去不動 + 改未來）")
    ss.add_argument("--schedule-id", help="目標 schedule id（例 SCH-005）")
    ss.add_argument("--class", dest="class_id", help="或指定 class（自動找該 class 唯一的 day/days schedule）")
    ss.add_argument("--at", required=True, help="從哪天開始改 YYYY-MM-DD（這天起算後段）")
    ss.add_argument("--day", help="後段 single day（不寫則沿用前段）")
    ss.add_argument("--days", help="後段 multi days（逗號分隔）")
    ss.add_argument("--to-slot", help="後段 slot 別名（不寫則沿用前段）")
    ss.add_argument("--to-time", help="後段時段 HH:MM-HH:MM（不寫則沿用前段）")
    ss.add_argument("--weeks", type=int, help="後段 duration_weeks")
    ss.add_argument("--end", help="後段 end_date YYYY-MM-DD")
    ss.add_argument("--lessons", type=int, help="後段 total_lessons")
    ss.add_argument("--note", help="後段備註（例「開學換時段」）")
    ss.add_argument("--apply", action="store_true")

    cl = sub.add_parser("cancel-lesson", help="取消某堂課（不補）")
    cl.add_argument("--class", dest="class_id", required=True)
    cl.add_argument("--date", required=True, help="要取消的日期 YYYY-MM-DD")
    cl.add_argument("--reason", help="備註原因（教練生病、學員請假等）")
    cl.add_argument("--apply", action="store_true")

    al = sub.add_parser("add-lesson", help="臨時加一堂（單日）")
    al.add_argument("--class", dest="class_id", required=True)
    al.add_argument("--date", required=True, help="新加課的日期 YYYY-MM-DD")
    al.add_argument("--slot", dest="slot_id", help="常用時段別名")
    al.add_argument("--time", help="HH:MM-HH:MM 直接寫")
    al.add_argument("--note")
    al.add_argument("--apply", action="store_true")

    ml = sub.add_parser("move-lesson", help="挪一堂課（補課/改時段）")
    ml.add_argument("--class", dest="class_id", required=True)
    ml.add_argument("--from-date", required=True, help="原本上課日 YYYY-MM-DD")
    ml.add_argument("--to-date", required=True, help="改到哪天 YYYY-MM-DD")
    ml.add_argument("--to-slot", help="改用哪個 slot（不變就不寫）")
    ml.add_argument("--to-time", help="改成什麼時段（不變就不寫）")
    ml.add_argument("--note", help="備註（例如「7/20 颱風停課改補」）")
    ml.add_argument("--apply", action="store_true")

    rs = sub.add_parser("remove-schedule", help="刪 schedule（按 class + 可選 slot/day）")
    rs.add_argument("--class", dest="class_id", required=True)
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
        "add-schedule": cmd_add_schedule,
        "remove-schedule": cmd_remove_schedule,
        "move-lesson": cmd_move_lesson,
        "split-schedule": cmd_split_schedule,
        "cancel-lesson": cmd_cancel_lesson,
        "add-lesson": cmd_add_lesson,
    }
    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
