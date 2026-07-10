"""
test_update_schedule.py — update-schedule + remove-schedule --schedule-id 整合測試

測試情境：
1. 改 start_date --apply 後 yaml 寫入正確、envelope 有 lessons_before/after
2. 原有 end_date 的 schedule 設 --weeks → end_date 被移除、duration_weeks 寫入
3. 一班兩條 schedule 只給 --class → E_AMBIGUOUS_TARGET，context 列候選 id
4. --schedule-id 不存在 → E_SCHEDULE_NOT_FOUND
5. 沒給任何編輯欄 → E_SCHEMA_INVALID
6. start_date 在過去、把 start 改到未來 → past_lessons_lost > 0
7. dry-run 檔案 bytes 不變且 data.preview 為 true
8. remove-schedule --schedule-id 刪指定條
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


# ── 共用 fixture ───────────────────────────────────────────────────────────────

def _make_yaml(tmp_path, schedules, classes=None, slots=None):
    """建立 YAML 測試檔"""
    if slots is None:
        slots = [{"id": "S1", "time": "09:00-10:00", "note": "早上9點"}]
    if classes is None:
        classes = [{"id": "C1", "name": "A 班", "weekly_count": 2, "level": "L1"}]
    data = {
        "schema_version": 2,
        "slots": slots,
        "classes": classes,
        "schedules": schedules,
    }
    p = tmp_path / "schedule.yaml"
    p.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


# ── 情境 1：改 start_date --apply ─────────────────────────────────────────────

@pytest.fixture
def yaml_single_schedule(tmp_path):
    today = date.today()
    old_start = str(today + timedelta(days=7))
    new_start = str(today + timedelta(days=14))
    schedules = [
        {
            "id": "SCH-001",
            "class_id": "C1",
            "slot_id": "S1",
            "time": "09:00-10:00",
            "day": "mon",
            "start_date": old_start,
            "duration_weeks": 4,
        }
    ]
    return _make_yaml(tmp_path, schedules), old_start, new_start


def test_update_start_date_apply(yaml_single_schedule):
    yaml_path, old_start, new_start = yaml_single_schedule

    rc, p = run_cli(yaml_path, "update-schedule", "--schedule-id", "SCH-001",
                    "--start", new_start, "--apply")
    assert rc == 0, p
    assert p["ok"], p

    d = p["data"]
    assert "lessons_before" in d, "envelope 應有 lessons_before"
    assert "lessons_after" in d, "envelope 應有 lessons_after"
    assert d["lessons_before"] > 0
    assert d["lessons_after"] > 0
    assert "start_date" in d["changed_fields"]

    # yaml 寫入驗證
    written = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    sched = next(s for s in written["schedules"] if s["id"] == "SCH-001")
    assert str(sched["start_date"]) == new_start, f"start_date 應改為 {new_start}"


# ── 情境 2：end_date schedule 設 --weeks → end_date 被移除 ───────────────────

@pytest.fixture
def yaml_with_end_date(tmp_path):
    today = date.today()
    schedules = [
        {
            "id": "SCH-001",
            "class_id": "C1",
            "slot_id": "S1",
            "time": "09:00-10:00",
            "day": "mon",
            "start_date": str(today + timedelta(days=7)),
            "end_date": str(today + timedelta(days=60)),
        }
    ]
    return _make_yaml(tmp_path, schedules)


def test_switch_end_date_to_weeks(yaml_with_end_date):
    yaml_path = yaml_with_end_date

    rc, p = run_cli(yaml_path, "update-schedule", "--schedule-id", "SCH-001",
                    "--weeks", "6", "--apply")
    assert rc == 0, p
    assert p["ok"], p

    written = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    sched = next(s for s in written["schedules"] if s["id"] == "SCH-001")
    assert "end_date" not in sched, "end_date 應被移除"
    assert sched.get("duration_weeks") == 6, "duration_weeks 應寫入 6"


# ── 情境 3：一班兩條 schedule → E_AMBIGUOUS_TARGET ───────────────────────────

@pytest.fixture
def yaml_two_schedules(tmp_path):
    today = date.today()
    schedules = [
        {
            "id": "SCH-001",
            "class_id": "C1",
            "slot_id": "S1",
            "time": "09:00-10:00",
            "day": "mon",
            "start_date": str(today + timedelta(days=7)),
            "duration_weeks": 4,
        },
        {
            "id": "SCH-002",
            "class_id": "C1",
            "slot_id": "S1",
            "time": "09:00-10:00",
            "day": "wed",
            "start_date": str(today + timedelta(days=7)),
            "duration_weeks": 4,
        },
    ]
    return _make_yaml(tmp_path, schedules)


def test_ambiguous_target(yaml_two_schedules):
    yaml_path = yaml_two_schedules

    rc, p = run_cli(yaml_path, "update-schedule", "--class", "C1",
                    "--start", "2026-09-01")
    assert rc != 0, "有歧義應回傳非 0 exit code"
    assert not p["ok"], p

    codes = [e["code"] for e in p.get("errors", [])]
    assert "E_AMBIGUOUS_TARGET" in codes, f"errors = {p.get('errors')}"

    # context 應列出兩個候選 id
    ctx = p["errors"][0].get("context", {})
    candidates = ctx.get("candidates", [])
    candidate_ids = [c["id"] for c in candidates]
    assert "SCH-001" in candidate_ids, "候選清單應含 SCH-001"
    assert "SCH-002" in candidate_ids, "候選清單應含 SCH-002"


# ── 情境 4：--schedule-id 不存在 → E_SCHEDULE_NOT_FOUND ──────────────────────

def test_schedule_not_found(yaml_single_schedule):
    yaml_path, _, _ = yaml_single_schedule

    rc, p = run_cli(yaml_path, "update-schedule", "--schedule-id", "SCH-999",
                    "--start", "2026-09-01")
    assert rc != 0, "不存在應回傳非 0"
    assert not p["ok"], p

    codes = [e["code"] for e in p.get("errors", [])]
    assert "E_SCHEDULE_NOT_FOUND" in codes, f"errors = {p.get('errors')}"


# ── 情境 5：沒給任何編輯欄 → E_SCHEMA_INVALID ────────────────────────────────

def test_no_edit_fields(yaml_single_schedule):
    yaml_path, _, _ = yaml_single_schedule

    rc, p = run_cli(yaml_path, "update-schedule", "--schedule-id", "SCH-001")
    assert rc != 0, "沒有編輯欄應回傳非 0"
    assert not p["ok"], p

    codes = [e["code"] for e in p.get("errors", [])]
    assert "E_SCHEMA_INVALID" in codes, f"errors = {p.get('errors')}"


# ── 情境 6：start_date 在過去、把 start 改到未來 → past_lessons_lost > 0 ─────

@pytest.fixture
def yaml_past_start(tmp_path):
    today = date.today()
    # 找最近的週一
    days_since_mon = today.weekday()
    last_mon = today - timedelta(days=days_since_mon)
    past_start = str(last_mon - timedelta(weeks=4))  # 4 週前的週一
    future_start = str(today + timedelta(days=14))   # 2 週後

    schedules = [
        {
            "id": "SCH-001",
            "class_id": "C1",
            "slot_id": "S1",
            "time": "09:00-10:00",
            "day": "mon",
            "start_date": past_start,
            "duration_weeks": 12,
        }
    ]
    return _make_yaml(tmp_path, schedules), future_start


def test_past_lessons_lost(yaml_past_start):
    yaml_path, future_start = yaml_past_start

    # dry-run（不 --apply），但還是要計算 past_lessons_lost
    rc, p = run_cli(yaml_path, "update-schedule", "--schedule-id", "SCH-001",
                    "--start", future_start)
    assert rc == 0, p
    assert p["ok"], p

    d = p["data"]
    assert d.get("past_lessons_lost", 0) > 0, \
        f"改起始日往後移應有 past_lessons_lost > 0，實際 = {d.get('past_lessons_lost')}"


# ── 情境 7：dry-run 檔案 bytes 不變且 data.preview 為 true ──────────────────

def test_dryrun_no_write(yaml_single_schedule):
    yaml_path, _, new_start = yaml_single_schedule
    before_bytes = yaml_path.read_bytes()

    rc, p = run_cli(yaml_path, "update-schedule", "--schedule-id", "SCH-001",
                    "--start", new_start)
    assert rc == 0, p
    assert p["ok"], p
    assert p["data"].get("preview") is True, "dry-run 應回傳 preview=true"
    assert yaml_path.read_bytes() == before_bytes, "dry-run 不應改變檔案"


# ── 情境 8：remove-schedule --schedule-id 刪指定條 ───────────────────────────

@pytest.fixture
def yaml_for_remove(tmp_path):
    today = date.today()
    schedules = [
        {
            "id": "SCH-001",
            "class_id": "C1",
            "slot_id": "S1",
            "time": "09:00-10:00",
            "day": "mon",
            "start_date": str(today + timedelta(days=7)),
            "duration_weeks": 4,
        },
        {
            "id": "SCH-002",
            "class_id": "C1",
            "slot_id": "S1",
            "time": "09:00-10:00",
            "day": "wed",
            "start_date": str(today + timedelta(days=7)),
            "duration_weeks": 4,
        },
    ]
    return _make_yaml(tmp_path, schedules)


def test_remove_by_schedule_id(yaml_for_remove):
    yaml_path = yaml_for_remove

    rc, p = run_cli(yaml_path, "remove-schedule", "--schedule-id", "SCH-001", "--apply")
    assert rc == 0, p
    assert p["ok"], p
    assert p["data"].get("removed_count") == 1

    written = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    ids = [s["id"] for s in written.get("schedules", [])]
    assert "SCH-001" not in ids, "SCH-001 應被刪除"
    assert "SCH-002" in ids, "SCH-002 應保留"


def test_remove_by_schedule_id_not_found(yaml_for_remove):
    yaml_path = yaml_for_remove

    rc, p = run_cli(yaml_path, "remove-schedule", "--schedule-id", "SCH-999")
    assert rc != 0, "不存在應回傳非 0"
    assert not p["ok"], p
    codes = [e["code"] for e in p.get("errors", [])]
    assert "E_SCHEDULE_NOT_FOUND" in codes, f"errors = {p.get('errors')}"
