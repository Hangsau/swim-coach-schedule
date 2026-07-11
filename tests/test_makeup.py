"""
test_makeup.py — 待補課帳本（cancel-lesson --makeup / fulfill-makeup / cancel-makeup）整合測試

測試情境：
1. cancel-lesson --makeup --apply → 建立 MU-001 pending，原課進 except_dates
2. fulfill-makeup --apply → 新增補課那堂、makeup 標記 fulfilled 並連到新 schedule
3. fulfill-makeup 補課日撞到別班 → E_TIME_OVERLAP（不是裸 crash；None slot_id 排序回歸測試）
4. 重複 fulfill 同一筆 → E_MAKEUP_ALREADY_FULFILLED
5. fulfill 不存在的 makeup-id → E_MAKEUP_NOT_FOUND
6. cancel-makeup --apply 撤銷登記；再撤同一筆 → E_MAKEUP_NOT_FOUND
7. list-makeups 預設只列 pending，--status all 列全部
8. validate 拒絕非法 status（結構驗證單元）
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

sys.path.insert(0, str(ROOT / "scripts"))


def run_cli(yaml_path, *args):
    cmd = [sys.executable, str(CLI), "--file", str(yaml_path), "--json", *args]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, encoding="utf-8")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {"_raw": result.stdout, "_stderr": result.stderr}
    return result.returncode, payload


def _write(tmp_path, schedules, classes=None, slots=None, makeups=None):
    if slots is None:
        slots = [{"id": "S1", "time": "09:00-10:00", "note": "早上9點"}]
    if classes is None:
        classes = [{"id": "C1", "name": "精緻班", "weekly_count": 1, "level": "L1"}]
    data = {
        "schema_version": 2,
        "slots": slots,
        "classes": classes,
        "schedules": schedules,
    }
    if makeups is not None:
        data["makeups"] = makeups
    p = tmp_path / "schedule.yaml"
    p.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


def _future_dates(n, step=7, start=8):
    today = date.today()
    return [str(today + timedelta(days=start + i * step)) for i in range(n)]


@pytest.fixture
def yaml_cancellable(tmp_path):
    """C1 有兩堂未來指定日期課，第一堂可拿來取消。"""
    d1, d2 = _future_dates(2)
    schedules = [{
        "id": "SCH-001",
        "class_id": "C1",
        "slot_id": "S1",
        "time": "09:00-10:00",
        "specific_dates": [d1, d2],
    }]
    return _write(tmp_path, schedules), d1, d2


# ── 情境 1：cancel-lesson --makeup 建立待補課 ─────────────────────────────────

def test_cancel_with_makeup_creates_entry(yaml_cancellable):
    yaml_path, d1, _ = yaml_cancellable

    rc, p = run_cli(yaml_path, "cancel-lesson", "--class", "C1", "--date", d1,
                    "--reason", "教練生病", "--makeup", "--apply")
    assert rc == 0, p
    assert p["ok"], p
    mk = p["data"]["makeup"]
    assert mk["id"] == "MU-001"
    assert mk["status"] == "pending"
    assert mk["origin_date"] == d1

    written = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert len(written["makeups"]) == 1
    sched = written["schedules"][0]
    assert d1 in [str(x) for x in sched.get("except_dates", [])]


# ── 情境 2：fulfill-makeup 銷帳並新增補課那堂 ────────────────────────────────

def test_fulfill_settles_and_adds_lesson(yaml_cancellable):
    yaml_path, d1, _ = yaml_cancellable
    run_cli(yaml_path, "cancel-lesson", "--class", "C1", "--date", d1,
            "--makeup", "--apply")

    makeup_date = str(date.today() + timedelta(days=30))
    rc, p = run_cli(yaml_path, "fulfill-makeup", "--makeup-id", "MU-001",
                    "--date", makeup_date, "--slot", "S1", "--apply")
    assert rc == 0, p
    assert p["ok"], p
    assert p["data"]["fulfilled_makeup"] == "MU-001"
    assert p["data"]["makeup_date"] == makeup_date

    written = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    mk = written["makeups"][0]
    assert mk["status"] == "fulfilled"
    assert mk["makeup_date"] == makeup_date
    new_sched_id = mk["makeup_schedule_id"]
    added = next(s for s in written["schedules"] if s["id"] == new_sched_id)
    assert makeup_date in [str(x) for x in added["specific_dates"]]


# ── 情境 3：補課日撞到別班 → E_TIME_OVERLAP（None slot_id 排序回歸） ──────────

def test_fulfill_overlap_returns_error_not_crash(tmp_path):
    d1, = _future_dates(1)
    makeup_date = str(date.today() + timedelta(days=30))
    classes = [
        {"id": "C1", "name": "精緻班", "weekly_count": 1},
        {"id": "C2", "name": "占位班", "weekly_count": 1},
    ]
    schedules = [
        {"id": "SCH-001", "class_id": "C1", "slot_id": "S1",
         "time": "09:00-10:00", "specific_dates": [d1]},
        # C2 已占用補課目標日的 09:00-10:00
        {"id": "SCH-002", "class_id": "C2", "slot_id": "S1",
         "time": "09:00-10:00", "specific_dates": [makeup_date]},
    ]
    yaml_path = _write(tmp_path, schedules, classes=classes)
    run_cli(yaml_path, "cancel-lesson", "--class", "C1", "--date", d1,
            "--makeup", "--apply")

    # 補課用 --time（time-only，slot_id=None）撞 C2 → 需回乾淨 E_TIME_OVERLAP
    rc, p = run_cli(yaml_path, "fulfill-makeup", "--makeup-id", "MU-001",
                    "--date", makeup_date, "--time", "09:00-10:00", "--apply")
    assert rc != 0, p
    assert not p["ok"], p
    codes = [e["code"] for e in p.get("errors", [])]
    assert "E_TIME_OVERLAP" in codes, f"應回 E_TIME_OVERLAP 而非 crash，errors={p.get('errors')}"


# ── 情境 4：重複 fulfill → E_MAKEUP_ALREADY_FULFILLED ────────────────────────

def test_double_fulfill(yaml_cancellable):
    yaml_path, d1, _ = yaml_cancellable
    run_cli(yaml_path, "cancel-lesson", "--class", "C1", "--date", d1,
            "--makeup", "--apply")
    makeup_date = str(date.today() + timedelta(days=30))
    run_cli(yaml_path, "fulfill-makeup", "--makeup-id", "MU-001",
            "--date", makeup_date, "--slot", "S1", "--apply")

    rc, p = run_cli(yaml_path, "fulfill-makeup", "--makeup-id", "MU-001",
                    "--date", str(date.today() + timedelta(days=45)),
                    "--slot", "S1", "--apply")
    assert rc != 0, p
    codes = [e["code"] for e in p.get("errors", [])]
    assert "E_MAKEUP_ALREADY_FULFILLED" in codes, f"errors={p.get('errors')}"


# ── 情境 5：fulfill 不存在的 id → E_MAKEUP_NOT_FOUND ─────────────────────────

def test_fulfill_not_found(yaml_cancellable):
    yaml_path, _, _ = yaml_cancellable
    rc, p = run_cli(yaml_path, "fulfill-makeup", "--makeup-id", "MU-999",
                    "--date", str(date.today() + timedelta(days=30)),
                    "--slot", "S1", "--apply")
    assert rc != 0, p
    codes = [e["code"] for e in p.get("errors", [])]
    assert "E_MAKEUP_NOT_FOUND" in codes, f"errors={p.get('errors')}"


# ── 情境 6：cancel-makeup 撤銷登記 ──────────────────────────────────────────

def test_cancel_makeup(yaml_cancellable):
    yaml_path, d1, _ = yaml_cancellable
    run_cli(yaml_path, "cancel-lesson", "--class", "C1", "--date", d1,
            "--makeup", "--apply")

    rc, p = run_cli(yaml_path, "cancel-makeup", "--makeup-id", "MU-001", "--apply")
    assert rc == 0, p
    assert p["data"]["cancelled_makeup"] == "MU-001"
    written = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert written.get("makeups", []) == []

    # 再撤同一筆 → 不存在
    rc2, p2 = run_cli(yaml_path, "cancel-makeup", "--makeup-id", "MU-001", "--apply")
    assert rc2 != 0, p2
    codes = [e["code"] for e in p2.get("errors", [])]
    assert "E_MAKEUP_NOT_FOUND" in codes, f"errors={p2.get('errors')}"


# ── 情境 7：list-makeups 過濾 ───────────────────────────────────────────────

def test_list_makeups_filters(yaml_cancellable):
    yaml_path, d1, d2 = yaml_cancellable
    run_cli(yaml_path, "cancel-lesson", "--class", "C1", "--date", d1,
            "--makeup", "--apply")
    # 銷帳 MU-001
    run_cli(yaml_path, "fulfill-makeup", "--makeup-id", "MU-001",
            "--date", str(date.today() + timedelta(days=30)), "--slot", "S1", "--apply")
    # 再登記一筆 pending（取消 d2）
    run_cli(yaml_path, "cancel-lesson", "--class", "C1", "--date", d2,
            "--makeup", "--apply")

    rc, p = run_cli(yaml_path, "list-makeups")  # 預設 pending
    assert rc == 0, p
    assert p["data"]["count"] == 1
    assert p["data"]["makeups"][0]["status"] == "pending"

    rc2, p2 = run_cli(yaml_path, "list-makeups", "--status", "all")
    assert p2["data"]["count"] == 2


# ── 情境 8b：cancel-lesson 壞日期回乾淨錯誤（不裸 crash）──────────────────────

def test_cancel_lesson_bad_date(yaml_cancellable):
    yaml_path, _, _ = yaml_cancellable
    rc, p = run_cli(yaml_path, "cancel-lesson", "--class", "C1",
                    "--date", "banana", "--makeup", "--apply")
    assert rc != 0, p
    assert not p["ok"], p
    codes = [e["code"] for e in p.get("errors", [])]
    assert "E_SCHEMA_INVALID" in codes, f"壞日期應回 E_SCHEMA_INVALID，errors={p.get('errors')}"


# ── 情境 8：validate 拒絕非法 status（結構驗證單元）─────────────────────────

def test_validate_rejects_bad_status():
    import validate as v
    data = {
        "schema_version": 2,
        "slots": [{"id": "S1", "time": "09:00-10:00"}],
        "classes": [{"id": "C1", "name": "A", "weekly_count": 1}],
        "schedules": [],
        "makeups": [{"id": "MU-001", "class_id": "C1",
                     "origin_date": "2026-07-01", "status": "bogus"}],
    }
    errors = v.validate_makeups(data)
    codes = [e["code"] for e in errors]
    assert "E_SCHEMA_INVALID" in codes, f"應拒絕非法 status，errors={errors}"
