#!/usr/bin/env python3
"""
validate.py — schedule.yaml schema + 一致性檢查單一入口

Usage:
  python scripts/validate.py [--strict] [--json] [--file path/to/yaml]

Exit codes:
  0 = ok（容許 warnings）
  1 = errors

可被 import：
  from validate import validate_all, time_overlap, ValidateResult
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
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
DEFAULT_YAML = ROOT / "data" / "schedule.yaml"

sys.path.insert(0, str(Path(__file__).parent))
from query import expand_schedule  # 共用 expand 邏輯，避免 drift

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HORIZON_PAST_DAYS = 7      # specific_dates 容許 today - 7 天內（補登）
HORIZON_FUTURE_DAYS = 365  # specific_dates 容許 today + 365 天內
START_DATE_FUTURE_DAYS = 730  # schedules.start_date 最遠未來 2 年


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


def validate_schema(data, strict=False):
    """純結構 + 欄位驗證，不算 expansion"""
    errors = []
    warnings = []

    # version
    sv = data.get("schema_version")
    if sv is None and strict:
        warnings.append(_err("MISSING_SCHEMA_VERSION", "yaml 缺 schema_version 欄位（建議加 schema_version: 2）"))

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
        t = s.get("time", "")
        if not isinstance(t, str) or not TIME_RE.match(t):
            errors.append(_err("E_SCHEMA_INVALID",
                               f"slots[{i}].time 格式須為 HH:MM-HH:MM 且 0–23:0–59",
                               path=f"slots[{i}].time", got=t))
        else:
            ts = parse_time_str(t.split("-")[0])
            te = parse_time_str(t.split("-")[1])
            if ts >= te:
                errors.append(_err("E_INVALID_DATE_RANGE",
                                   f"slots[{i}].time start>=end（跨午夜不支援）",
                                   path=f"slots[{i}].time", got=t))
    # 唯一性
    dup_slots = [k for k, c in Counter(slot_ids).items() if c > 1]
    for k in dup_slots:
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
    dup_classes = [k for k, c in Counter(class_ids).items() if c > 1]
    for k in dup_classes:
        errors.append(_err("E_DUPLICATE_ID", f"class id 重複: {k}", path="classes[].id", value=k))

    slot_id_set = set(slot_ids)
    class_id_set = set(class_ids)

    # --- schedules ---
    today = date.today()
    start_future_max = today + timedelta(days=START_DATE_FUTURE_DAYS)
    horizon_past = today - timedelta(days=HORIZON_PAST_DAYS)
    horizon_future = today + timedelta(days=HORIZON_FUTURE_DAYS)

    schedule_keys = []  # for duplicate detection
    for i, s in enumerate(schedules):
        if not isinstance(s, dict):
            errors.append(_err("E_SCHEMA_INVALID", f"schedules[{i}] 不是 dict", path=f"schedules[{i}]"))
            continue

        cid = s.get("class_id")
        sid = s.get("slot_id")
        s_time = s.get("time")
        if cid not in class_id_set:
            errors.append(_err("E_CLASS_NOT_FOUND",
                               f"schedules[{i}].class_id={cid} 不存在於 classes",
                               path=f"schedules[{i}].class_id", value=cid))
        # slot_id 或 time 至少要有一個
        if not sid and not s_time:
            errors.append(_err("E_SCHEMA_INVALID",
                               f"schedules[{i}] slot_id 或 time 至少需一個",
                               path=f"schedules[{i}]"))
        if sid and sid not in slot_id_set:
            errors.append(_err("E_SLOT_NOT_FOUND",
                               f"schedules[{i}].slot_id={sid} 不存在於 slots",
                               path=f"schedules[{i}].slot_id", value=sid))
        # time 格式驗（凍結時段）
        if s_time:
            if not isinstance(s_time, str) or not TIME_RE.match(s_time):
                errors.append(_err("E_SCHEMA_INVALID",
                                   f"schedules[{i}].time 格式須為 HH:MM-HH:MM",
                                   path=f"schedules[{i}].time", got=s_time))
            else:
                ts = parse_time_str(s_time.split("-")[0])
                te = parse_time_str(s_time.split("-")[1])
                if ts >= te:
                    errors.append(_err("E_INVALID_DATE_RANGE",
                                       f"schedules[{i}].time start>=end",
                                       path=f"schedules[{i}].time", got=s_time))
        # except_dates 格式驗
        ed_list = s.get("except_dates") or []
        if ed_list:
            if not isinstance(ed_list, list):
                errors.append(_err("E_SCHEMA_INVALID",
                                   f"schedules[{i}].except_dates 必須是 list",
                                   path=f"schedules[{i}].except_dates"))
            else:
                for j, d in enumerate(ed_list):
                    try:
                        _to_date(d)
                    except Exception:
                        errors.append(_err("E_SCHEMA_INVALID",
                                           f"schedules[{i}].except_dates[{j}] 解析失敗",
                                           path=f"schedules[{i}].except_dates[{j}]", got=str(d)))

        # day vs days vs specific_dates 互斥
        has_day = "day" in s
        has_days = "days" in s
        has_specific = "specific_dates" in s
        modes = sum([has_day, has_days, has_specific])
        if modes == 0:
            errors.append(_err("E_SCHEMA_INVALID",
                               f"schedules[{i}] 必須有 day / days / specific_dates 其一",
                               path=f"schedules[{i}]"))
        elif modes > 1:
            errors.append(_err("E_SCHEMA_INVALID",
                               f"schedules[{i}] day / days / specific_dates 三者互斥",
                               path=f"schedules[{i}]"))

        if has_day:
            if s["day"] not in DAYS:
                errors.append(_err("E_SCHEMA_INVALID",
                                   f"schedules[{i}].day 必須是 {DAYS}",
                                   path=f"schedules[{i}].day", got=s["day"]))
        if has_days:
            ds = s["days"]
            if not isinstance(ds, list) or not ds:
                errors.append(_err("E_SCHEMA_INVALID",
                                   f"schedules[{i}].days 必須非空 list",
                                   path=f"schedules[{i}].days"))
            else:
                bad = [d for d in ds if d not in DAYS]
                if bad:
                    errors.append(_err("E_SCHEMA_INVALID",
                                       f"schedules[{i}].days 含非法值 {bad}",
                                       path=f"schedules[{i}].days", got=ds))
                if len(set(ds)) != len(ds):
                    errors.append(_err("E_SCHEMA_INVALID",
                                       f"schedules[{i}].days 內有重複",
                                       path=f"schedules[{i}].days", got=ds))

        # specific_dates
        if has_specific:
            sd = s["specific_dates"]
            if not isinstance(sd, list) or not sd:
                errors.append(_err("E_SCHEMA_INVALID",
                                   f"schedules[{i}].specific_dates 必須非空 list",
                                   path=f"schedules[{i}].specific_dates"))
            else:
                for j, d in enumerate(sd):
                    try:
                        dt = _to_date(d)
                    except Exception:
                        errors.append(_err("E_SCHEMA_INVALID",
                                           f"schedules[{i}].specific_dates[{j}] 解析失敗",
                                           path=f"schedules[{i}].specific_dates[{j}]", got=str(d)))
                        continue
                    if dt < horizon_past:
                        code = "E_PAST_DATE" if strict else "W_PAST_DATE"
                        msg = f"schedules[{i}].specific_dates[{j}]={dt} 早於 today-{HORIZON_PAST_DAYS}d"
                        (errors if strict else warnings).append(
                            _err(code, msg, path=f"schedules[{i}].specific_dates[{j}]",
                                 value=str(dt), today=str(today))
                        )
                    if dt > horizon_future:
                        errors.append(_err("E_DATE_TOO_FAR",
                                           f"schedules[{i}].specific_dates[{j}]={dt} 超過 today+{HORIZON_FUTURE_DAYS}d",
                                           path=f"schedules[{i}].specific_dates[{j}]",
                                           value=str(dt), today=str(today)))

        # start_date
        sd_str = s.get("start_date")
        if sd_str is not None:
            try:
                sd_dt = _to_date(sd_str)
            except Exception:
                errors.append(_err("E_SCHEMA_INVALID",
                                   f"schedules[{i}].start_date 解析失敗",
                                   path=f"schedules[{i}].start_date", got=str(sd_str)))
                sd_dt = None
            if sd_dt and sd_dt < date(2020, 1, 1):
                errors.append(_err("E_INVALID_DATE_RANGE",
                                   f"schedules[{i}].start_date={sd_dt} 早於 2020-01-01",
                                   path=f"schedules[{i}].start_date", value=str(sd_dt)))
            if sd_dt and sd_dt > start_future_max:
                errors.append(_err("E_DATE_TOO_FAR",
                                   f"schedules[{i}].start_date={sd_dt} 超過 today+{START_DATE_FUTURE_DAYS}d",
                                   path=f"schedules[{i}].start_date", value=str(sd_dt)))

        # end_date
        ed_str = s.get("end_date")
        if ed_str is not None and sd_str is not None:
            try:
                ed_dt = _to_date(ed_str)
                if sd_dt and ed_dt <= sd_dt:
                    errors.append(_err("E_INVALID_DATE_RANGE",
                                       f"schedules[{i}].end_date={ed_dt} <= start_date={sd_dt}",
                                       path=f"schedules[{i}].end_date",
                                       start_date=str(sd_dt), end_date=str(ed_dt)))
            except Exception:
                errors.append(_err("E_SCHEMA_INVALID",
                                   f"schedules[{i}].end_date 解析失敗",
                                   path=f"schedules[{i}].end_date", got=str(ed_str)))

        # 終止條件至少一個
        if has_day or has_days:
            has_termination = any(k in s for k in ("duration_weeks", "end_date", "total_lessons"))
            if not has_termination:
                errors.append(_err("E_NO_TERMINATION",
                                   f"schedules[{i}] day/days 模式至少需 duration_weeks/end_date/total_lessons 之一",
                                   path=f"schedules[{i}]"))

        # duplicate schedule entry
        if has_day:
            key = (cid, sid, "day", s["day"], str(sd_str))
        elif has_days:
            key = (cid, sid, "days", tuple(sorted(s.get("days") or [])), str(sd_str))
        elif has_specific:
            key = (cid, sid, "specific", tuple(sorted(str(_to_date(d)) for d in (s.get("specific_dates") or []))))
        else:
            key = None
        if key is not None:
            schedule_keys.append((i, key))

    dup_keys = defaultdict(list)
    for i, k in schedule_keys:
        dup_keys[k].append(i)
    for k, idxs in dup_keys.items():
        if len(idxs) > 1:
            errors.append(_err("E_DUPLICATE_SCHEDULE",
                               f"schedules 重複: {k}（indices={idxs}）",
                               indices=idxs, key=str(k)))

    return errors, warnings


def validate_cross(data, strict=False):
    """跨欄位一致性 + lesson 衝突偵測"""
    errors = []
    warnings = []

    slots = data.get("slots", []) or []
    classes = data.get("classes", []) or []
    schedules = data.get("schedules", []) or []

    slots_by_id = {s.get("id"): s for s in slots if s.get("id")}
    classes_by_id = {c.get("id"): c for c in classes if c.get("id")}

    # 過濾掉 schema 已 invalid 的 schedules 避免 expand 爆掉
    safe_schedules = []
    for s in schedules:
        if not isinstance(s, dict):
            continue
        if s.get("class_id") not in classes_by_id:
            continue
        # slot_id 可選；若有則須在 slots 內；若無則必須有 time
        sid = s.get("slot_id")
        if sid is not None and sid not in slots_by_id:
            continue
        if sid is None and not s.get("time"):
            continue
        if "days" in s:
            ds = s.get("days") or []
            if not all(d in DAYS for d in ds):
                continue
        if "day" in s and s["day"] not in DAYS:
            continue
        safe_schedules.append(s)

    try:
        lessons = expand_schedule(safe_schedules, slots_by_id, classes_by_id)
    except Exception as e:
        errors.append(_err("E_EXPAND_FAILED", f"expand_schedule 拋例外: {e}"))
        return errors, warnings, []

    # 孤兒 class
    used_class = {l["class_id"] for l in lessons}
    for c in classes:
        cid = c.get("id")
        if cid and cid not in used_class:
            code = "E_ORPHAN_CLASS" if strict else "W_ORPHAN_CLASS"
            msg = f"class {cid} ({c.get('name')}) 沒對應 schedule"
            (errors if strict else warnings).append(_err(code, msg, class_id=cid))

    # 孤兒 slot（一律 warn）
    used_slot = {l["slot_id"] for l in lessons}
    for s in slots:
        sid = s.get("id")
        if sid and sid not in used_slot:
            warnings.append(_err("W_ORPHAN_SLOT",
                                 f"slot {sid} ({s.get('time')}) 沒對應 schedule",
                                 slot_id=sid))

    # weekly_count 一致性：每 class 每週實際展開堂數 <= weekly_count
    # 用 ISO week 算
    by_class_week = defaultdict(int)
    for l in lessons:
        iso_year, iso_week, _ = l["date"].isocalendar()
        by_class_week[(l["class_id"], iso_year, iso_week)] += 1
    max_week = defaultdict(int)
    for (cid, _, _), n in by_class_week.items():
        if n > max_week[cid]:
            max_week[cid] = n
    for cid, mx in max_week.items():
        wc = classes_by_id.get(cid, {}).get("weekly_count")
        if isinstance(wc, int) and mx > wc:
            errors.append(_err("E_WEEKLY_COUNT_EXCEEDED",
                               f"class {cid} 實際最大週堂數 {mx} > weekly_count {wc}",
                               class_id=cid, observed=mx, declared=wc))

    # 時段重疊（lesson vs lesson）— 核心
    by_date = defaultdict(list)
    for l in lessons:
        by_date[l["date"]].append(l)
    overlap_pairs = set()
    for d, day_lessons in by_date.items():
        for i, a in enumerate(day_lessons):
            for b in day_lessons[i+1:]:
                # 跳過完全相同的 (slot_id) 同班相同 entry — 已由 duplicate 檢查抓
                a_t = a.get("slot_time", "")
                b_t = b.get("slot_time", "")
                if not a_t or not b_t:
                    continue
                if a_t == b_t and a["slot_id"] == b["slot_id"]:
                    # 同 slot 同班/不同班會被 slot_double_booking 抓；先標衝突
                    pass
                if time_overlap(a_t, b_t):
                    pair_key = tuple(sorted([
                        (a["class_id"], a["slot_id"], a_t),
                        (b["class_id"], b["slot_id"], b_t),
                    ]))
                    pair_full_key = (d, pair_key)
                    if pair_full_key in overlap_pairs:
                        continue
                    overlap_pairs.add(pair_full_key)
                    errors.append(_err("E_TIME_OVERLAP",
                                       f"{d} 時段重疊：{a['class_id']}({a_t}) <-> {b['class_id']}({b_t})",
                                       date=str(d),
                                       lesson_a={"class_id": a["class_id"], "slot_id": a["slot_id"], "time": a_t},
                                       lesson_b={"class_id": b["class_id"], "slot_id": b["slot_id"], "time": b_t}))

    return errors, warnings, lessons


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

    errors, warnings = validate_schema(data, strict=strict)

    # 跨欄位（即便 schema 有錯也跑，拿到的衝突資訊只用安全 schedules）
    cross_errors, cross_warnings, lessons = validate_cross(data, strict=strict)
    errors.extend(cross_errors)
    warnings.extend(cross_warnings)

    stats = {
        "classes": len(data.get("classes", []) or []),
        "slots": len(data.get("slots", []) or []),
        "schedules": len(data.get("schedules", []) or []),
        "lessons_expanded": len(lessons),
        "schema_version": data.get("schema_version"),
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
                   help="strict 模式：孤兒 class、specific_dates 過去日皆視為 error")
    p.add_argument("--json", action="store_true", help="輸出 JSON")
    p.add_argument("--file", default=str(DEFAULT_YAML))
    args = p.parse_args()

    result = validate_all(args.file, strict=args.strict)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        st = result["stats"]
        print(f"[stats] classes={st.get('classes')} slots={st.get('slots')} "
              f"schedules={st.get('schedules')} lessons={st.get('lessons_expanded')} "
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
