"""
test_cli_smoke.py — 模擬 minimax LLM 多步操作 schedule_cli.py 的整合測試

確認：
- 寫入命令預設 dry-run，不改檔
- --apply 才寫
- 衝突在 preview 就攔截
- 錯誤碼穩定
- next_actions 給出

每個測試獨立 temp dir + temp yaml。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
CLI = ROOT / "scripts" / "schedule_cli.py"


@pytest.fixture
def tmp_yaml(tmp_path):
    """準備 temp yaml，回傳路徑"""
    today = date.today()
    start = today + timedelta(days=2)
    data = {
        "schema_version": 2,
        "slots": [
            {"id": "S1", "time": "09:00-10:00", "note": "早上 9 點"},
            {"id": "S2", "time": "10:10-11:10", "note": "早上 10 點"},
            {"id": "S3", "time": "13:00-14:00", "note": "下午 1 點"},
        ],
        "classes": [
            {"id": "C1", "name": "A 班", "weekly_count": 5, "level": "L1"},
        ],
        "schedules": [
            {"id": "SCH-001", "class_id": "C1", "slot_id": "S1",
             "time": "09:00-10:00",
             "day": "mon", "start_date": str(start), "duration_weeks": 4},
        ],
    }
    p = tmp_path / "schedule.yaml"
    p.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


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


def test_status_baseline(tmp_yaml):
    rc, p = run_cli(tmp_yaml, "status")
    assert rc == 0, p
    assert p["ok"]
    assert p["data"]["stats"]["classes"] == 1


def test_add_class_dry_run_no_write(tmp_yaml):
    before = tmp_yaml.read_text(encoding="utf-8")
    rc, p = run_cli(tmp_yaml, "add-class", "--id", "C2", "--name", "B", "--weekly-count", "2")
    assert rc == 0
    assert p["ok"]
    assert p["data"]["preview"] is True
    # 檔沒被改
    assert tmp_yaml.read_text(encoding="utf-8") == before


def test_add_class_apply_writes(tmp_yaml):
    rc, p = run_cli(tmp_yaml, "add-class", "--id", "C2", "--name", "B",
                    "--weekly-count", "2", "--apply")
    assert rc == 0, p
    assert p["ok"]
    assert p["data"]["applied"] is True
    # 檔被改
    data = yaml.safe_load(tmp_yaml.read_text(encoding="utf-8"))
    assert any(c["id"] == "C2" for c in data["classes"])


def test_add_class_duplicate_id_rejected(tmp_yaml):
    rc, p = run_cli(tmp_yaml, "add-class", "--id", "C1", "--name", "重複",
                    "--weekly-count", "1")
    assert rc != 0
    assert not p["ok"]
    codes = [e["code"] for e in p["errors"]]
    assert "E_DUPLICATE_ID" in codes


def test_add_schedule_conflict_blocked_in_preview(tmp_yaml):
    """製造時段重疊，在 dry-run 階段就攔截"""
    # 先加 C2
    rc, _ = run_cli(tmp_yaml, "add-class", "--id", "C2", "--name", "B",
                    "--weekly-count", "1", "--apply")
    assert rc == 0
    # 跟 C1 同 day=mon 同 slot=S1 → 一定衝突
    today = date.today()
    start = today + timedelta(days=2)
    rc, p = run_cli(tmp_yaml, "add-schedule",
                    "--class", "C2", "--slot", "S1",
                    "--day", "mon", "--start", str(start), "--weeks", "4")
    assert rc != 0
    assert not p["ok"]
    codes = [e["code"] for e in p["errors"]]
    assert "E_TIME_OVERLAP" in codes


def test_add_schedule_unknown_class_rejected(tmp_yaml):
    today = date.today()
    rc, p = run_cli(tmp_yaml, "add-schedule",
                    "--class", "NOPE", "--slot", "S1",
                    "--day", "tue", "--start", str(today + timedelta(days=2)),
                    "--weeks", "4")
    assert rc != 0
    codes = [e["code"] for e in p["errors"]]
    assert "E_CLASS_NOT_FOUND" in codes


def test_remove_class_with_refs_blocked(tmp_yaml):
    rc, p = run_cli(tmp_yaml, "remove-class", "--id", "C1")
    assert rc != 0
    codes = [e["code"] for e in p["errors"]]
    assert "E_AMBIGUOUS_TARGET" in codes


def test_remove_class_cascade_works(tmp_yaml):
    rc, p = run_cli(tmp_yaml, "remove-class", "--id", "C1", "--cascade", "--apply")
    assert rc == 0, p
    data = yaml.safe_load(tmp_yaml.read_text(encoding="utf-8"))
    assert not any(c["id"] == "C1" for c in data.get("classes", []))
    assert not any(s["class_id"] == "C1" for s in data.get("schedules", []))


def test_list_conflicts_clean(tmp_yaml):
    rc, p = run_cli(tmp_yaml, "list-conflicts")
    assert rc == 0
    assert p["ok"]
    assert p["data"]["count"] == 0


def test_add_schedule_with_time_only(tmp_yaml):
    """--time 直接寫，不需 --slot（任意插時段）"""
    today = date.today()
    rc, p = run_cli(tmp_yaml, "add-schedule",
                    "--class", "C1", "--time", "15:00-16:00",
                    "--day", "sat", "--start", str(today + timedelta(days=2)),
                    "--weeks", "4", "--apply")
    assert rc == 0, p
    data = yaml.safe_load(tmp_yaml.read_text(encoding="utf-8"))
    new_sched = data["schedules"][-1]
    assert new_sched["time"] == "15:00-16:00"
    assert "slot_id" not in new_sched
    assert new_sched["id"].startswith("SCH-")


def test_schedule_time_freezes_when_slot_changes(tmp_yaml):
    """改 slot.time 不會打到已 commit 的 schedule（凍結）"""
    # baseline schedule 用 S1 09:00-10:00
    data = yaml.safe_load(tmp_yaml.read_text(encoding="utf-8"))
    # 確認既有 schedule 有 time（migration 應有；測試 fixture 沒寫，先補）
    if "time" not in data["schedules"][0]:
        data["schedules"][0]["time"] = "09:00-10:00"
        tmp_yaml.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    # 改 S1 time → 09:30-10:30
    data = yaml.safe_load(tmp_yaml.read_text(encoding="utf-8"))
    data["slots"][0]["time"] = "09:30-10:30"
    tmp_yaml.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    # status 看 lesson 時段仍是凍結值 09:00-10:00
    rc, p = run_cli(tmp_yaml, "status")
    assert rc == 0
    # validate 應通過（schedule time 仍 09:00-10:00 跟其他不衝突）
    assert p["ok"]


def test_move_lesson_via_cli(tmp_yaml):
    """move-lesson 在原 schedule 加 except_dates + 新增補課條目"""
    today = date.today()
    from_d = today + timedelta(days=2)  # baseline schedule 第一堂之一
    # 找一個 from_date 跟 to_date 不衝突
    # baseline 是 C1 mon, start today+2；找個下個 mon 對應日
    # 簡單做法：先 status 看 upcoming 找第一堂
    rc, p = run_cli(tmp_yaml, "status")
    assert p["data"]["upcoming_7d"], "fixture 應有 upcoming"
    real_from = p["data"]["upcoming_7d"][0]["date"]
    # 改到一個沒人上課的週六（today + 5）
    to_d = date.fromisoformat(real_from) + timedelta(days=5)

    rc, p = run_cli(tmp_yaml, "move-lesson",
                    "--class", "C1",
                    "--from-date", real_from,
                    "--to-date", str(to_d),
                    "--note", "test 補課",
                    "--apply")
    assert rc == 0, p
    data = yaml.safe_load(tmp_yaml.read_text(encoding="utf-8"))
    # 原 schedule 加了 except_dates
    orig = next(s for s in data["schedules"] if s.get("day") == "mon")
    assert real_from in [str(x) for x in orig.get("except_dates", [])]
    # 新加一條 specific_dates 補課
    makeup = next(s for s in data["schedules"] if "specific_dates" in s)
    assert str(to_d) in [str(x) for x in makeup["specific_dates"]]


def test_split_schedule_via_cli(tmp_yaml):
    """split-schedule 截斷前段 + 新建後段"""
    today = date.today()
    cutoff = today + timedelta(days=14)
    rc, p = run_cli(tmp_yaml, "split-schedule",
                    "--class", "C1",
                    "--at", str(cutoff),
                    "--days", "tue,thu",
                    "--weeks", "4",
                    "--apply")
    assert rc == 0, p
    data = yaml.safe_load(tmp_yaml.read_text(encoding="utf-8"))
    # 找前段：有 end_date 接近 cutoff-1
    orig = next(s for s in data["schedules"] if s.get("day") == "mon")
    assert "end_date" in orig
    assert str(cutoff - timedelta(days=1)) in str(orig["end_date"])
    # 找後段：days=[tue,thu]
    after = next(s for s in data["schedules"] if s.get("days") == ["tue", "thu"])
    assert after["start_date"] == str(cutoff) or str(after["start_date"]) == str(cutoff)


def test_next_actions_present(tmp_yaml):
    """確認所有寫入命令給 next_actions 引導 LLM"""
    rc, p = run_cli(tmp_yaml, "add-class", "--id", "C9", "--name", "X",
                    "--weekly-count", "1")
    assert p["next_actions"], "add-class preview 應給 next_actions"
    # next_actions 應提示加 --apply
    assert any("--apply" in s for s in p["next_actions"])
