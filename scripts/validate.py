#!/usr/bin/env python3
"""
validate.py — schedule.yaml schema + 一致性檢查單一入口（v4）

v4：課次真相在頂層 lessons（一堂一筆）；schedules 為分組 metadata，
不參與展開。不再有 pattern 展開驗證、負面排除清單、weekly_count 上限驗證。

Usage:
  python scripts/validate.py [--strict] [--json] [--file path/to/yaml]

Exit codes:
  0 = ok（容許 warnings）
  1 = errors

可被 import：
  from validate import validate_all, time_overlap, DAYS
"""
import argparse
import json
import re
import sys

# Windows cp950 console 對非 ASCII print 會 crash
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
DEFAULT_YAML = ROOT / "data" / "schedule.yaml"

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LESSON_FIELDS = {"id", "schedule_id", "class_id", "date", "time", "slot_id", "note"}


def _err(code, msg, **ctx):
    return {"code": code, "msg": msg, "context": ctx}


def _to_date(d):
    if hasattr(d, "year") and hasattr(d, "month") and hasattr(d, "day") and hasattr(d, "hour"):
        return d.date()
    if hasattr(d, "year") and hasattr(d, "month") and hasattr(d, "day"):
        return d
    return datetime.strptime(str(d), "%Y-%m-%d").date()


def parse_time_str(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def time_overlap(s1, s2):
    """HH:MM-HH:MM 跟 HH:MM-HH:MM 區間是否重疊（端點接觸不算）"""
    a_start, a_end = parse_time_str(s1.split("-")[0]), parse_time_str(s1.split("-")[1])
    b_start, b_end = parse_time_str(s2.split("-")[0]), parse_time_str(s2.split("-")[1])
    return a_start < b_end and b_start < a_end


def _check_time_field(errors, t, path):
    """time 欄位格式 + start<end 檢查，回傳是否格式合法"""
    if not isinstance(t, str) or not TIME_RE.match(t):
        errors.append(_err("E_SCHEMA_INVALID",
                           f"{path} 格式須為 HH:MM-HH:MM 且 0–23:0–59",
                           path=path, got=t))
        return False
    ts = parse_time_str(t.split("-")[0])
    te = parse_time_str(t.split("-")[1])
    if ts >= te:
        errors.append(_err("E_INVALID_DATE_RANGE",
                           f"{path} start>=end（跨午夜不支援）",
                           path=path, got=t))
        return False
    return True


def validate_schema(data, strict=False):
    """slots / classes / schedules(metadata) 結構驗證"""
    errors = []
    warnings = []

    slots = data.get("slots", []) or []
    classes = data.get("classes", []) or []
    schedules = data.get("schedules", []) or []

    # --- slots ---
    slot_ids = []
    for i, s in enumerate(slots):
        if not isinstance(s, dict):
            errors.append(_err("E_SCHEMA_INVALID", f"slots[{i}] 不是 dict", path=f"slots[{i}]"))
            continue
        sid = s.get("id")
        if not sid or not isinstance(sid, str):
            errors.append(_err("E_SCHEMA_INVALID", f"slots[{i}].id 必填非空字串", path=f"slots[{i}].id"))
        else:
            slot_ids.append(sid)
        _check_time_field(errors, s.get("time", ""), f"slots[{i}].time")
    for k in [k for k, c in Counter(slot_ids).items() if c > 1]:
        errors.append(_err("E_DUPLICATE_ID", f"slot id 重複: {k}", path="slots[].id", value=k))

    # --- classes ---
    class_ids = []
    for i, c in enumerate(classes):
        if not isinstance(c, dict):
            errors.append(_err("E_SCHEMA_INVALID", f"classes[{i}] 不是 dict", path=f"classes[{i}]"))
            continue
        cid = c.get("id")
        if not cid or not isinstance(cid, str):
            errors.append(_err("E_SCHEMA_INVALID", f"classes[{i}].id 必填非空字串", path=f"classes[{i}].id"))
        else:
            class_ids.append(cid)
        if not c.get("name"):
            errors.append(_err("E_SCHEMA_INVALID", f"classes[{i}].name 必填", path=f"classes[{i}].name"))
        wc = c.get("weekly_count")
        if wc is None or not isinstance(wc, int):
            errors.append(_err("E_SCHEMA_INVALID",
                               f"classes[{i}].weekly_count 必填 int",
                               path=f"classes[{i}].weekly_count", got=wc))
        elif wc < 1:
            errors.append(_err("E_SCHEMA_INVALID",
                               f"classes[{i}].weekly_count 必須 >= 1",
                               path=f"classes[{i}].weekly_count", got=wc))
    for k in [k for k, c in Counter(class_ids).items() if c > 1]:
        errors.append(_err("E_DUPLICATE_ID", f"class id 重複: {k}", path="classes[].id", value=k))

    slot_id_set = set(slot_ids)
    class_id_set = set(class_ids)

    # --- schedules（v4：分組 metadata，不參與展開）---
    schedule_ids = []
    for i, s in enumerate(schedules):
        if not isinstance(s, dict):
            errors.append(_err("E_SCHEMA_INVALID", f"schedules[{i}] 不是 dict", path=f"schedules[{i}]"))
            continue
        sch_id = s.get("id")
        if not sch_id or not isinstance(sch_id, str):
            errors.append(_err("E_SCHEMA_INVALID",
                               f"schedules[{i}].id 必填非空字串", path=f"schedules[{i}].id"))
        else:
            schedule_ids.append(sch_id)
        cid = s.get("class_id")
        if cid not in class_id_set:
            errors.append(_err("E_CLASS_NOT_FOUND",
                               f"schedules[{i}].class_id={cid} 不存在於 classes",
                               path=f"schedules[{i}].class_id", value=cid))
        sid = s.get("slot_id")
        if sid and sid not in slot_id_set:
            errors.append(_err("E_SLOT_NOT_FOUND",
                               f"schedules[{i}].slot_id={sid} 不存在於 slots",
                               path=f"schedules[{i}].slot_id", value=sid))
        if s.get("time"):
            _check_time_field(errors, s["time"], f"schedules[{i}].time")
    for k in [k for k, c in Counter(schedule_ids).items() if c > 1]:
        errors.append(_err("E_DUPLICATE_ID", f"schedule id 重複: {k}", path="schedules[].id", value=k))

    return errors, warnings


def validate_lessons(data):
    """lessons 結構驗證（v4 核心）。回傳 (errors, safe_lessons)。

    safe_lessons = 結構合法、可用於跨欄位檢查的 lessons（date 已正規化為 date 物件）
    """
    errors = []
    lessons = data.get("lessons", []) or []
    if not isinstance(lessons, list):
        errors.append(_err("E_SCHEMA_INVALID", "lessons 必須是 list", path="lessons"))
        return errors, []

    class_id_set = {c.get("id") for c in (data.get("classes") or [])
                    if isinstance(c, dict) and c.get("id")}
    schedule_id_set = {s.get("id") for s in (data.get("schedules") or [])
                       if isinstance(s, dict) and s.get("id")}

    lesson_ids = []
    safe = []
    for i, l in enumerate(lessons):
        if not isinstance(l, dict):
            errors.append(_err("E_SCHEMA_INVALID", f"lessons[{i}] 不是 dict", path=f"lessons[{i}]"))
            continue
        for field in sorted(set(l) - LESSON_FIELDS):
            path = f"lessons[{i}].{field}"
            errors.append(_err("E_SCHEMA_INVALID", f"{path} 是未知欄位", path=path))
        ok = True
        lid = l.get("id")
        if not lid or not isinstance(lid, str):
            errors.append(_err("E_SCHEMA_INVALID",
                               f"lessons[{i}].id 必填非空字串", path=f"lessons[{i}].id"))
            ok = False
        else:
            lesson_ids.append(lid)
        cid = l.get("class_id")
        if not cid:
            errors.append(_err("E_SCHEMA_INVALID",
                               f"lessons[{i}].class_id 必填", path=f"lessons[{i}].class_id"))
            ok = False
        elif cid not in class_id_set:
            errors.append(_err("E_CLASS_NOT_FOUND",
                               f"lessons[{i}].class_id={cid} 不存在於 classes",
                               path=f"lessons[{i}].class_id", value=cid))
            ok = False
        d = l.get("date")
        d_norm = None
        if d is None:
            errors.append(_err("E_SCHEMA_INVALID",
                               f"lessons[{i}].date 必填", path=f"lessons[{i}].date"))
            ok = False
        elif not DATE_RE.match(str(d)):
            errors.append(_err("E_SCHEMA_INVALID",
                               f"lessons[{i}].date 格式須為 YYYY-MM-DD",
                               path=f"lessons[{i}].date", got=str(d)))
            ok = False
        else:
            try:
                d_norm = _to_date(d)
            except Exception:
                errors.append(_err("E_SCHEMA_INVALID",
                                   f"lessons[{i}].date 解析失敗",
                                   path=f"lessons[{i}].date", got=str(d)))
                ok = False
        t = l.get("time")
        if t is None:
            errors.append(_err("E_SCHEMA_INVALID",
                               f"lessons[{i}].time 必填", path=f"lessons[{i}].time"))
            ok = False
        elif not _check_time_field(errors, t, f"lessons[{i}].time"):
            ok = False
        sch_id = l.get("schedule_id")
        if sch_id is not None and sch_id not in schedule_id_set:
            errors.append(_err("E_SCHEDULE_NOT_FOUND",
                               f"lessons[{i}].schedule_id={sch_id} 不存在於 schedules",
                               path=f"lessons[{i}].schedule_id", value=sch_id))
        if ok and d_norm is not None:
            nl = dict(l)
            nl["date"] = d_norm
            safe.append(nl)
    for k in [k for k, c in Counter(lesson_ids).items() if c > 1]:
        errors.append(_err("E_DUPLICATE_ID", f"lesson id 重複: {k}", path="lessons[].id", value=k))

    return errors, safe


def validate_makeups(data, lesson_id_set):
    """待補課帳本（makeups）結構驗證。makeups 為 optional 頂層 list。"""
    errors = []
    makeups = data.get("makeups")
    if makeups is None:
        return errors
    if not isinstance(makeups, list):
        errors.append(_err("E_SCHEMA_INVALID", "makeups 必須是 list", path="makeups"))
        return errors
    class_id_set = {c.get("id") for c in (data.get("classes") or [])
                    if isinstance(c, dict) and c.get("id")}
    mu_ids = []
    for i, m in enumerate(makeups):
        if not isinstance(m, dict):
            errors.append(_err("E_SCHEMA_INVALID", f"makeups[{i}] 不是 dict", path=f"makeups[{i}]"))
            continue
        mid = m.get("id")
        if not mid or not isinstance(mid, str):
            errors.append(_err("E_SCHEMA_INVALID", f"makeups[{i}].id 必填非空字串", path=f"makeups[{i}].id"))
        else:
            mu_ids.append(mid)
        if m.get("class_id") not in class_id_set:
            errors.append(_err("E_CLASS_NOT_FOUND",
                               f"makeups[{i}].class_id={m.get('class_id')} 不存在於 classes",
                               path=f"makeups[{i}].class_id", value=m.get("class_id")))
        status = m.get("status", "pending")
        if status not in ("pending", "fulfilled"):
            errors.append(_err("E_SCHEMA_INVALID",
                               f"makeups[{i}].status 必須是 pending / fulfilled",
                               path=f"makeups[{i}].status", got=status))
        od = m.get("origin_date")
        if od is None:
            errors.append(_err("E_SCHEMA_INVALID", f"makeups[{i}].origin_date 必填", path=f"makeups[{i}].origin_date"))
        else:
            try:
                _to_date(od)
            except Exception:
                errors.append(_err("E_SCHEMA_INVALID", f"makeups[{i}].origin_date 解析失敗",
                                   path=f"makeups[{i}].origin_date", got=str(od)))
        if status == "fulfilled":
            md = m.get("makeup_date")
            if md is None:
                errors.append(_err("E_SCHEMA_INVALID",
                                   f"makeups[{i}] status=fulfilled 需有 makeup_date",
                                   path=f"makeups[{i}].makeup_date"))
            else:
                try:
                    _to_date(md)
                except Exception:
                    errors.append(_err("E_SCHEMA_INVALID", f"makeups[{i}].makeup_date 解析失敗",
                                       path=f"makeups[{i}].makeup_date", got=str(md)))
            mlid = m.get("makeup_lesson_id")
            if not mlid:
                errors.append(_err("E_SCHEMA_INVALID",
                                   f"makeups[{i}] status=fulfilled 需有 makeup_lesson_id",
                                   path=f"makeups[{i}].makeup_lesson_id"))
            elif mlid not in lesson_id_set:
                errors.append(_err("E_LESSON_NOT_FOUND",
                                   f"makeups[{i}].makeup_lesson_id={mlid} 不存在於 lessons",
                                   path=f"makeups[{i}].makeup_lesson_id", value=mlid))
    for k in [k for k, c in Counter(mu_ids).items() if c > 1]:
        errors.append(_err("E_DUPLICATE_ID", f"makeup id 重複: {k}", path="makeups[].id", value=k))
    return errors


def validate_cross(data, safe_lessons, strict=False):
    """跨欄位一致性：孤兒 class/slot + lesson 時段重疊"""
    errors = []
    warnings = []

    slots = data.get("slots", []) or []
    classes = data.get("classes", []) or []
    schedules = data.get("schedules", []) or []

    # 孤兒 class（v4 改義：無任何 lesson 且無 schedule 才算孤兒）
    used_class = {l.get("class_id") for l in safe_lessons}
    scheduled_class = {s.get("class_id") for s in schedules if isinstance(s, dict)}
    for c in classes:
        if not isinstance(c, dict):
            continue
        cid = c.get("id")
        if cid and cid not in used_class and cid not in scheduled_class:
            code = "E_ORPHAN_CLASS" if strict else "W_ORPHAN_CLASS"
            msg = f"class {cid} ({c.get('name')}) 沒有任何 lesson 也沒有 schedule"
            (errors if strict else warnings).append(_err(code, msg, class_id=cid))

    # 孤兒 slot（一律 warn）
    used_slot = {l.get("slot_id") for l in safe_lessons}
    for s in slots:
        if not isinstance(s, dict):
            continue
        sid = s.get("id")
        if sid and sid not in used_slot:
            warnings.append(_err("W_ORPHAN_SLOT",
                                 f"slot {sid} ({s.get('time')}) 沒對應 lesson",
                                 slot_id=sid))

    # 時段重疊（lesson vs lesson）— 核心
    by_date = defaultdict(list)
    for l in safe_lessons:
        by_date[l["date"]].append(l)
    overlap_pairs = set()
    for d, day_lessons in by_date.items():
        for i, a in enumerate(day_lessons):
            for b in day_lessons[i+1:]:
                a_t = a.get("time", "")
                b_t = b.get("time", "")
                if not a_t or not b_t:
                    continue
                if time_overlap(a_t, b_t):
                    # slot_id 可能為 None（time-only lesson，如補課），排序 key 需 None-safe
                    pair_key = tuple(sorted(
                        [(a.get("class_id"), a.get("slot_id"), a_t),
                         (b.get("class_id"), b.get("slot_id"), b_t)],
                        key=lambda x: tuple("" if v is None else str(v) for v in x)))
                    pair_full_key = (d, pair_key)
                    if pair_full_key in overlap_pairs:
                        continue
                    overlap_pairs.add(pair_full_key)
                    errors.append(_err("E_TIME_OVERLAP",
                                       f"{d} 時段重疊：{a.get('class_id')}({a_t}) <-> {b.get('class_id')}({b_t})",
                                       date=str(d),
                                       lesson_a={"class_id": a.get("class_id"), "slot_id": a.get("slot_id"), "time": a_t},
                                       lesson_b={"class_id": b.get("class_id"), "slot_id": b.get("slot_id"), "time": b_t}))

    return errors, warnings


def validate_all(yaml_path=DEFAULT_YAML, strict=False):
    """主入口：回傳 dict envelope"""
    try:
        raw = Path(yaml_path).read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
    except Exception as e:
        return {
            "ok": False,
            "errors": [_err("E_YAML_PARSE", f"yaml 解析失敗: {e}")],
            "warnings": [],
            "stats": {},
        }
    if not isinstance(data, dict):
        return {
            "ok": False,
            "errors": [_err("E_SCHEMA_INVALID", "yaml 頂層必須是 mapping")],
            "warnings": [],
            "stats": {},
        }

    sv = data.get("schema_version")
    if sv != 4:
        return {
            "ok": False,
            "errors": [_err("E_SCHEMA_VERSION",
                            f"schema_version={sv} 不是 4，請跑 scripts/migrate_v4.py",
                            got=sv)],
            "warnings": [],
            "stats": {"schema_version": sv},
        }

    errors, warnings = validate_schema(data, strict=strict)

    lesson_errors, safe_lessons = validate_lessons(data)
    errors.extend(lesson_errors)

    lesson_id_set = {l.get("id") for l in (data.get("lessons") or [])
                     if isinstance(l, dict) and l.get("id")}
    errors.extend(validate_makeups(data, lesson_id_set))

    cross_errors, cross_warnings = validate_cross(data, safe_lessons, strict=strict)
    errors.extend(cross_errors)
    warnings.extend(cross_warnings)

    makeups_all = data.get("makeups", []) or []
    stats = {
        "classes": len(data.get("classes", []) or []),
        "slots": len(data.get("slots", []) or []),
        "schedules": len(data.get("schedules", []) or []),
        "lessons": len(data.get("lessons", []) or []),
        "makeups_pending": sum(1 for m in makeups_all
                               if isinstance(m, dict) and m.get("status", "pending") == "pending"),
        "schema_version": sv,
    }
    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "stats": stats,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--strict", action="store_true",
                   help="strict 模式：孤兒 class 視為 error")
    p.add_argument("--json", action="store_true", help="輸出 JSON")
    p.add_argument("--file", default=str(DEFAULT_YAML))
    args = p.parse_args()

    result = validate_all(args.file, strict=args.strict)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        st = result["stats"]
        print(f"[stats] classes={st.get('classes')} slots={st.get('slots')} "
              f"schedules={st.get('schedules')} lessons={st.get('lessons')} "
              f"schema_version={st.get('schema_version')}")
        if result["errors"]:
            print(f"\n[errors] {len(result['errors'])} 條")
            for e in result["errors"]:
                print(f"  [{e['code']}] {e['msg']}")
        if result["warnings"]:
            print(f"\n[warnings] {len(result['warnings'])} 條")
            for w in result["warnings"]:
                print(f"  [{w['code']}] {w['msg']}")
        print(f"\n[result] {'OK' if result['ok'] else 'FAIL'}")

    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
