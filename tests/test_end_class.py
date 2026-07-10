"""
test_end_class.py — end-class 子命令整合測試

覆蓋五種情境：
1. day 模式 mixed (kept>0, removed>0) → --apply 後 end_date 設定、duration_weeks 移除
2. from 在所有堂次之前 → schedule + class 整條移除，strict validate 通過
3. specific_dates 模式 → >= from 的日期被濾掉
4. dry-run (不加 --apply) → 檔案 bytes 不變
5. class 不存在 → E_CLASS_NOT_FOUND，exit code 非 0
"""
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
CLI = ROOT / "scripts" / "schedule_cli.py"

# 把 scripts/ 加入 path，以便 import validate
sys.path.insert(0, str(ROOT / "scripts"))


def run_cli(yaml_path, *args):
    """跑 CLI，回傳 (returncode, parsed_json)"""
    cmd = [sys.executable, str(CLI), "--file", str(yaml_path), "--json", *args]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, encoding="utf-8")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {"_raw": result.stdout, "_stderr": result.stderr}
    return result.returncode, payload


# ── 情境 1：day 模式，過去與未來都有堂次 ──────────────────────────────────────
@pytest.fixture
def yaml_day_mixed(tmp_path):
    """
    C1 班，週一課，start_date = 4 週前的週一，duration_weeks = 8
    → 前 4 週已上（< from），後 4 週未上（>= from）
    from_date = 今天（往前對齊週一，確保堂次分佈）
    """
    today = date.today()
    # 找 4 週前的週一
    days_since_mon = today.weekday()  # 0=mon
    last_mon = today - timedelta(days=days_since_mon)
    start = last_mon - timedelta(weeks=3)   # 3 週前的週一（含 start week 共 4 週）
    # from_date = 今天的週一（今天本身可能是週一或之後）
    from_date = last_mon + timedelta(weeks=1)  # 下週一開始截止

    data = {
        "schema_version": 2,
        "slots": [{"id": "S1", "time": "09:00-10:00", "note": "早上9點"}],
        "classes": [{"id": "C1", "name": "A 班", "weekly_count": 2, "level": "L1"}],
        "schedules": [
            {
                "id": "SCH-001",
                "class_id": "C1",
                "slot_id": "S1",
                "time": "09:00-10:00",
                "day": "mon",
                "start_date": str(start),
                "duration_weeks": 8,
            }
        ],
    }
    p = tmp_path / "schedule.yaml"
    p.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p, str(from_date)


def test_day_mode_truncate_apply(yaml_day_mixed):
    yaml_path, from_date = yaml_day_mixed
    expected_end = str(date.fromisoformat(from_date) - timedelta(days=1))

    rc, p = run_cli(yaml_path, "end-class", "--class", "C1", "--from", from_date, "--apply")
    assert rc == 0, p
    assert p["ok"], p

    d = p["data"]
    assert d["ended_class_id"] == "C1"
    assert d["from"] == from_date
    assert d["kept_lessons"] > 0, "應有保留堂次"
    assert d["removed_lessons"] > 0, "應有移除堂次"
    assert "SCH-001" in d["schedules_truncated"]
    assert d["schedules_removed"] == []
    assert d["class_removed"] is False

    # 驗證 yaml 寫入正確
    written = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    sched = next(s for s in written["schedules"] if s["id"] == "SCH-001")
    assert str(sched["end_date"]) == expected_end, f"end_date 應為 {expected_end}"
    assert "duration_weeks" not in sched, "duration_weeks 應被移除"
    assert "total_lessons" not in sched, "total_lessons 應被移除"


# ── 情境 2：from 在所有堂次之前 → 整條移除 + class 一併移除 ──────────────────
@pytest.fixture
def yaml_future_only(tmp_path):
    """C1 班，下週才開始，from_date = 明天 → 所有堂次 >= from → kept=0"""
    today = date.today()
    start = today + timedelta(days=7)   # 一週後才開始
    from_date = today + timedelta(days=1)  # 明天 → 早於 start

    data = {
        "schema_version": 2,
        "slots": [{"id": "S1", "time": "09:00-10:00", "note": "早上9點"}],
        "classes": [{"id": "C1", "name": "A 班", "weekly_count": 2, "level": "L1"}],
        "schedules": [
            {
                "id": "SCH-001",
                "class_id": "C1",
                "slot_id": "S1",
                "time": "09:00-10:00",
                "day": "mon",
                "start_date": str(start),
                "duration_weeks": 4,
            }
        ],
    }
    p = tmp_path / "schedule.yaml"
    p.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p, str(from_date)


def test_all_future_removes_class(yaml_future_only):
    yaml_path, from_date = yaml_future_only

    rc, p = run_cli(yaml_path, "end-class", "--class", "C1", "--from", from_date, "--apply")
    assert rc == 0, p
    assert p["ok"], p

    d = p["data"]
    assert d["class_removed"] is True
    assert "SCH-001" in d["schedules_removed"]
    assert d["schedules_truncated"] == []

    # 寫入後 strict validate 必須通過
    from validate import validate_all
    result = validate_all(yaml_path, strict=True)
    assert result["ok"], f"strict validate 失敗：{result.get('errors')}"

    written = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert not any(c["id"] == "C1" for c in written.get("classes", [])), "C1 應被移除"
    assert not any(s["id"] == "SCH-001" for s in written.get("schedules", [])), "SCH-001 應被移除"


# ── 情境 3：specific_dates 模式 ────────────────────────────────────────────────
@pytest.fixture
def yaml_specific_dates(tmp_path):
    """C1 班，specific_dates 含過去 2 個日期與未來 2 個日期"""
    today = date.today()
    past1 = today - timedelta(days=14)
    past2 = today - timedelta(days=7)
    future1 = today + timedelta(days=7)
    future2 = today + timedelta(days=14)
    from_date = today + timedelta(days=1)  # 明天起截止 → past1, past2 保留；future1, future2 移除

    data = {
        "schema_version": 2,
        "slots": [{"id": "S1", "time": "09:00-10:00", "note": "早上9點"}],
        "classes": [{"id": "C1", "name": "A 班", "weekly_count": 2, "level": "L1"}],
        "schedules": [
            {
                "id": "SCH-001",
                "class_id": "C1",
                "slot_id": "S1",
                "time": "09:00-10:00",
                "specific_dates": [str(past1), str(past2), str(future1), str(future2)],
            }
        ],
    }
    p = tmp_path / "schedule.yaml"
    p.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p, str(from_date), str(past1), str(past2), str(future1), str(future2)


def test_specific_dates_filtered(yaml_specific_dates):
    yaml_path, from_date, past1, past2, future1, future2 = yaml_specific_dates

    rc, p = run_cli(yaml_path, "end-class", "--class", "C1", "--from", from_date, "--apply")
    assert rc == 0, p
    assert p["ok"], p

    d = p["data"]
    assert d["kept_lessons"] == 2, "應保留 2 個過去日期"
    assert d["removed_lessons"] == 2, "應移除 2 個未來日期"
    assert "SCH-001" in d["schedules_truncated"]

    written = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    sched = next(s for s in written["schedules"] if s["id"] == "SCH-001")
    remaining = [str(d) for d in sched["specific_dates"]]
    assert past1 in remaining, "過去日期 past1 應保留"
    assert past2 in remaining, "過去日期 past2 應保留"
    assert future1 not in remaining, "未來日期 future1 應被移除"
    assert future2 not in remaining, "未來日期 future2 應被移除"


# ── 情境 4：dry-run（不加 --apply）→ 檔案 bytes 不變 ─────────────────────────
@pytest.fixture
def yaml_for_dryrun(tmp_path):
    """簡單 day 模式，未來有堂次"""
    today = date.today()
    start = today + timedelta(days=2)
    from_date = today + timedelta(days=9)

    data = {
        "schema_version": 2,
        "slots": [{"id": "S1", "time": "09:00-10:00", "note": "早上9點"}],
        "classes": [{"id": "C1", "name": "A 班", "weekly_count": 2, "level": "L1"}],
        "schedules": [
            {
                "id": "SCH-001",
                "class_id": "C1",
                "slot_id": "S1",
                "time": "09:00-10:00",
                "day": "mon",
                "start_date": str(start),
                "duration_weeks": 4,
            }
        ],
    }
    p = tmp_path / "schedule.yaml"
    p.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p, str(from_date)


def test_dryrun_no_write(yaml_for_dryrun):
    yaml_path, from_date = yaml_for_dryrun
    before_bytes = yaml_path.read_bytes()

    rc, p = run_cli(yaml_path, "end-class", "--class", "C1", "--from", from_date)
    assert rc == 0, p
    assert p["ok"], p
    assert p["data"].get("preview") is True, "dry-run 應回傳 preview=true"

    assert yaml_path.read_bytes() == before_bytes, "dry-run 不應改變檔案"


# ── 情境 5：class 不存在 → E_CLASS_NOT_FOUND，exit code 非 0 ─────────────────
@pytest.fixture
def yaml_minimal(tmp_path):
    today = date.today()
    data = {
        "schema_version": 2,
        "slots": [{"id": "S1", "time": "09:00-10:00", "note": "早上9點"}],
        "classes": [{"id": "C1", "name": "A 班", "weekly_count": 2, "level": "L1"}],
        "schedules": [
            {
                "id": "SCH-001",
                "class_id": "C1",
                "slot_id": "S1",
                "time": "09:00-10:00",
                "day": "mon",
                "start_date": str(today + timedelta(days=2)),
                "duration_weeks": 4,
            }
        ],
    }
    p = tmp_path / "schedule.yaml"
    p.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


def test_class_not_found(yaml_minimal):
    today = date.today()
    from_date = str(today + timedelta(days=5))

    rc, p = run_cli(yaml_minimal, "end-class", "--class", "NOPE", "--from", from_date)
    assert rc != 0, "class 不存在應回傳非 0 exit code"
    assert not p["ok"], p
    codes = [e["code"] for e in p.get("errors", [])]
    assert "E_CLASS_NOT_FOUND" in codes, f"errors = {p.get('errors')}"
