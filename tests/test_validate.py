"""
test_validate.py — validate.py 單元測試

涵蓋 codex review 指出的所有攻擊面：
- 時段重疊
- specific_dates 過去 / 太遠未來 / 五年後
- weekly_count 超出
- duplicate schedule
- 終止條件缺失
- day vs days vs specific_dates 互斥
- 時段格式錯
- end_date <= start_date
- 孤兒 class（strict mode）

每個測試獨立用 temp yaml，不污染真實 data。
"""
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from validate import validate_all, time_overlap  # noqa: E402


def _write(data):
    """寫入 temp yaml，回傳路徑"""
    f = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".yaml", delete=False)
    f.write(yaml.dump(data, allow_unicode=True, sort_keys=False))
    f.close()
    return f.name


def _base():
    """合法的最小 schedule.yaml"""
    today = date.today()
    start = today + timedelta(days=2)
    return {
        "schema_version": 2,
        "slots": [
            {"id": "S1", "time": "09:00-10:00", "note": "早上 9 點"},
            {"id": "S2", "time": "10:10-11:10", "note": "早上 10 點"},
        ],
        "classes": [
            {"id": "C1", "name": "A 班", "weekly_count": 1, "level": "L1"},
            {"id": "C2", "name": "B 班", "weekly_count": 1, "level": "L1"},
        ],
        "schedules": [
            {"class_id": "C1", "slot_id": "S1", "day": "mon",
             "start_date": str(start), "duration_weeks": 4},
            {"class_id": "C2", "slot_id": "S2", "day": "tue",
             "start_date": str(start), "duration_weeks": 4},
        ],
    }


# -------------- 合法 baseline --------------

def test_baseline_valid():
    p = _write(_base())
    r = validate_all(p)
    assert r["ok"], f"baseline 應 ok，但: {r['errors']}"
    assert r["stats"]["lessons_expanded"] > 0


# -------------- 時段重疊（攻擊 1 剋星） --------------

def test_time_overlap_different_slots():
    """兩個 slot 時段重疊，同日不同班 → 應 fail"""
    data = _base()
    data["slots"][1]["time"] = "09:30-10:30"  # 跟 S1 09:00-10:00 重疊
    # 讓兩班同日同 slot 各自一堂
    data["schedules"][1]["day"] = "mon"
    p = _write(data)
    r = validate_all(p)
    assert not r["ok"]
    codes = [e["code"] for e in r["errors"]]
    assert "E_TIME_OVERLAP" in codes


def test_time_overlap_helper():
    assert time_overlap("09:00-10:00", "09:30-10:30") is True
    assert time_overlap("09:00-10:00", "10:00-11:00") is False
    assert time_overlap("09:00-10:00", "08:00-08:30") is False
    assert time_overlap("19:00-20:30", "20:10-21:10") is True  # 跨 30 分鐘


# -------------- specific_dates 範圍（攻擊 2 剋星） --------------

def test_specific_dates_too_far_future():
    """specific_date 超過 today+365d → fail"""
    data = _base()
    far = date.today() + timedelta(days=400)
    data["schedules"].append({
        "class_id": "C1", "slot_id": "S1",
        "specific_dates": [str(far)],
    })
    p = _write(data)
    r = validate_all(p)
    assert not r["ok"]
    assert any(e["code"] == "E_DATE_TOO_FAR" for e in r["errors"])


def test_specific_dates_past_warn_only():
    """specific_date 過去 → 非 strict 是 warning，strict 是 error"""
    data = _base()
    past = date.today() - timedelta(days=30)
    data["schedules"].append({
        "class_id": "C1", "slot_id": "S1",
        "specific_dates": [str(past)],
    })
    p = _write(data)
    r = validate_all(p, strict=False)
    assert r["ok"]  # warning 不 fail
    assert any(w["code"] == "W_PAST_DATE" for w in r["warnings"])

    r_strict = validate_all(p, strict=True)
    assert not r_strict["ok"]
    assert any(e["code"] == "E_PAST_DATE" for e in r_strict["errors"])


# -------------- weekly_count（攻擊 3 剋星） --------------

def test_weekly_count_exceeded():
    data = _base()
    data["classes"][0]["weekly_count"] = 1
    data["schedules"][0]["day"] = None
    data["schedules"][0].pop("day")
    data["schedules"][0]["days"] = ["mon", "tue", "wed"]  # 每週 3 堂
    p = _write(data)
    r = validate_all(p)
    assert not r["ok"]
    assert any(e["code"] == "E_WEEKLY_COUNT_EXCEEDED" for e in r["errors"])


def test_weekly_count_must_be_positive():
    data = _base()
    data["classes"][0]["weekly_count"] = 0
    p = _write(data)
    r = validate_all(p)
    assert not r["ok"]
    codes = [e["code"] for e in r["errors"]]
    assert "E_SCHEMA_INVALID" in codes


# -------------- 終止條件 --------------

def test_no_termination_fail():
    data = _base()
    data["schedules"][0].pop("duration_weeks")
    p = _write(data)
    r = validate_all(p)
    assert not r["ok"]
    assert any(e["code"] == "E_NO_TERMINATION" for e in r["errors"])


# -------------- duplicate schedule --------------

def test_duplicate_schedule_entry():
    data = _base()
    data["schedules"].append(data["schedules"][0].copy())  # 完全複製
    p = _write(data)
    r = validate_all(p)
    assert not r["ok"]
    assert any(e["code"] == "E_DUPLICATE_SCHEDULE" for e in r["errors"])


# -------------- duplicate id --------------

def test_duplicate_class_id():
    data = _base()
    data["classes"].append({"id": "C1", "name": "重複", "weekly_count": 1, "level": "X"})
    p = _write(data)
    r = validate_all(p)
    assert not r["ok"]
    assert any(e["code"] == "E_DUPLICATE_ID" for e in r["errors"])


def test_duplicate_slot_id():
    data = _base()
    data["slots"].append({"id": "S1", "time": "15:00-16:00", "note": "夜"})
    p = _write(data)
    r = validate_all(p)
    assert not r["ok"]
    assert any(e["code"] == "E_DUPLICATE_ID" for e in r["errors"])


# -------------- 時段格式 --------------

def test_slot_time_format_strict():
    data = _base()
    data["slots"][0]["time"] = "9:00-10:00"  # 應該要 09 不是 9
    p = _write(data)
    r = validate_all(p)
    assert not r["ok"]
    assert any(e["code"] == "E_SCHEMA_INVALID" for e in r["errors"])


def test_slot_time_invalid_hour():
    data = _base()
    data["slots"][0]["time"] = "25:00-26:00"
    p = _write(data)
    r = validate_all(p)
    assert not r["ok"]


def test_slot_time_start_after_end():
    data = _base()
    data["slots"][0]["time"] = "10:00-09:00"
    p = _write(data)
    r = validate_all(p)
    assert not r["ok"]
    assert any(e["code"] == "E_INVALID_DATE_RANGE" for e in r["errors"])


# -------------- day vs days vs specific 互斥 --------------

def test_day_and_days_exclusive():
    data = _base()
    data["schedules"][0]["days"] = ["wed"]
    p = _write(data)
    r = validate_all(p)
    assert not r["ok"]


def test_specific_dates_and_day_exclusive():
    data = _base()
    data["schedules"][0]["specific_dates"] = [str(date.today() + timedelta(days=10))]
    p = _write(data)
    r = validate_all(p)
    assert not r["ok"]


# -------------- days 必須 unique --------------

def test_days_duplicate_rejected():
    data = _base()
    data["schedules"][0].pop("day")
    data["schedules"][0]["days"] = ["mon", "mon", "wed"]
    p = _write(data)
    r = validate_all(p)
    assert not r["ok"]


# -------------- end_date <= start_date --------------

def test_end_before_start():
    data = _base()
    data["schedules"][0].pop("duration_weeks")
    today = date.today()
    data["schedules"][0]["start_date"] = str(today + timedelta(days=30))
    data["schedules"][0]["end_date"] = str(today + timedelta(days=10))
    p = _write(data)
    r = validate_all(p)
    assert not r["ok"]
    assert any(e["code"] == "E_INVALID_DATE_RANGE" for e in r["errors"])


# -------------- 孤兒 class（strict） --------------

def test_orphan_class_warn_or_fail():
    data = _base()
    data["classes"].append({"id": "C3", "name": "孤兒", "weekly_count": 1, "level": "X"})
    p = _write(data)
    r = validate_all(p, strict=False)
    assert r["ok"]  # 非 strict 只 warn
    assert any(w["code"] == "W_ORPHAN_CLASS" for w in r["warnings"])
    r_strict = validate_all(p, strict=True)
    assert not r_strict["ok"]
    assert any(e["code"] == "E_ORPHAN_CLASS" for e in r_strict["errors"])


# -------------- 真實 yaml validate 全綠 --------------

def test_real_yaml_strict_passes():
    """真實的 data/schedule.yaml 必須通過 strict validate"""
    real = ROOT / "data" / "schedule.yaml"
    r = validate_all(str(real), strict=True)
    assert r["ok"], f"真實 yaml strict 不應 fail：{r['errors']}"
