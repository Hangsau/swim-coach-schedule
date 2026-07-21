from copy import deepcopy
from datetime import date, timedelta

import pytest

from conftest import codes, monday, read_yaml, run_cli, write_yaml


def lesson_date(data, class_id="C1", index=2):
    return [l for l in data["lessons"] if l["class_id"] == class_id][index]["date"]


def test_cancel_makeup_roundtrip(v4_yaml):
    original = read_yaml(v4_yaml)
    target = lesson_date(original)
    _, before = run_cli(v4_yaml, "list-makeups")
    _, cancelled = run_cli(v4_yaml, "cancel-lesson", "--class", "C1", "--date", target,
                           "--makeup", "--reason", "leave", "--apply")
    _, pending = run_cli(v4_yaml, "list-makeups")
    mu_id = cancelled["data"]["makeup"]["id"]
    makeup_day = str(monday(6) + timedelta(days=3))
    _, fulfilled = run_cli(v4_yaml, "fulfill-makeup", "--makeup-id", mu_id,
                           "--date", makeup_day, "--time", "17:00-18:00", "--apply")
    _, after = run_cli(v4_yaml, "list-makeups")
    written = read_yaml(v4_yaml)
    makeup = next(m for m in written["makeups"] if m["id"] == mu_id)
    assert before["data"]["pending_total"] == 0
    assert pending["data"]["pending_total"] == 1
    assert len(read_yaml(v4_yaml)["makeups"]) == 1
    assert after["data"]["pending_total"] == 0
    assert fulfilled["ok"] and makeup["status"] == "fulfilled"
    assert any(l["id"] == makeup["makeup_lesson_id"] for l in written["lessons"])
    assert not any(l["class_id"] == "C1" and l["date"] == target for l in written["lessons"])


def test_cancel_without_makeup_only_deletes_lesson(v4_yaml):
    target = lesson_date(read_yaml(v4_yaml))
    _, p = run_cli(v4_yaml, "cancel-lesson", "--class", "C1", "--date", target, "--apply")
    assert p["ok"] and read_yaml(v4_yaml)["makeups"] == []


def test_fulfill_missing_makeup_clean_error(v4_yaml):
    cp, p = run_cli(v4_yaml, "fulfill-makeup", "--makeup-id", "MU-999",
                    "--date", str(monday(6)), "--time", "17:00-18:00")
    assert cp.returncode != 0 and "E_MAKEUP_NOT_FOUND" in codes(p) and "Traceback" not in cp.stderr


def test_cancel_missing_makeup_clean_error(v4_yaml):
    cp, p = run_cli(v4_yaml, "cancel-makeup", "--makeup-id", "MU-999")
    assert "E_MAKEUP_NOT_FOUND" in codes(p) and "Traceback" not in cp.stderr


def test_fulfill_overlap_is_envelope_not_crash(v4_yaml):
    target = lesson_date(read_yaml(v4_yaml))
    run_cli(v4_yaml, "cancel-lesson", "--class", "C1", "--date", target, "--makeup", "--apply")
    occupied = read_yaml(v4_yaml)["lessons"][0]["date"]
    cp, p = run_cli(v4_yaml, "fulfill-makeup", "--makeup-id", "MU-001",
                    "--date", occupied, "--time", "09:30-10:30", "--apply")
    assert not p["ok"] and "E_TIME_OVERLAP" in codes(p) and "Traceback" not in cp.stderr


def _fulfilled_file(tmp_path, v4_data, linked=True):
    d = str(monday(7))
    lesson = {"id": "L-9000", "class_id": "C1", "date": d, "time": "17:00-18:00"}
    if linked:
        lesson.update(schedule_id="SCH-001", slot_id="S1")
    v4_data["lessons"].append(lesson)
    v4_data["makeups"] = [{"id": "MU-001", "class_id": "C1", "origin_date": "2026-01-01",
                            "origin_schedule_id": "SCH-001", "reason": "leave",
                            "status": "fulfilled", "makeup_date": d,
                            "makeup_lesson_id": "L-9000"}]
    return write_yaml(tmp_path, v4_data), d


def _assert_reverted(path):
    m = read_yaml(path)["makeups"][0]
    assert m["status"] == "pending" and m["makeup_date"] is None and m["makeup_lesson_id"] is None


def test_cancel_fulfilled_lesson_reverts_pending(tmp_path, v4_data):
    path, d = _fulfilled_file(tmp_path, v4_data, linked=False)
    _, p = run_cli(path, "cancel-lesson", "--class", "C1", "--date", d, "--apply")
    assert p["data"]["makeups_reverted"] == ["MU-001"]
    assert p["data"]["makeups_reused"] == ["MU-001"]
    assert "fulfill-makeup --makeup-id MU-001" in p["next_actions"][0]
    written = read_yaml(path)
    assert [m["id"] for m in written["makeups"] if m["status"] == "pending"] == ["MU-001"]
    _assert_reverted(path)


def test_cancel_fulfilled_lesson_with_makeup_reuses_same_pending(tmp_path, v4_data):
    path, d = _fulfilled_file(tmp_path, v4_data, linked=False)
    _, p = run_cli(path, "cancel-lesson", "--class", "C1", "--date", d,
                   "--makeup", "--reason", "cancelled makeup", "--apply")
    written = read_yaml(path)
    pending = [m for m in written["makeups"] if m["status"] == "pending"]
    assert p["ok"] and p["data"]["makeups_reverted"] == ["MU-001"]
    assert p["data"]["makeups_reused"] == ["MU-001"]
    assert "makeup" not in p["data"]
    assert len(pending) == 1 and pending[0]["id"] == "MU-001"
    assert "fulfill-makeup --makeup-id MU-001" in p["next_actions"][0]


def test_remove_schedule_reverts_fulfilled_makeup(tmp_path, v4_data):
    path, _ = _fulfilled_file(tmp_path, v4_data)
    _, p = run_cli(path, "remove-schedule", "--schedule-id", "SCH-001", "--apply")
    assert p["ok"]
    _assert_reverted(path)


def test_update_schedule_regeneration_reverts_fulfilled_makeup(tmp_path, v4_data):
    path, _ = _fulfilled_file(tmp_path, v4_data)
    _, p = run_cli(path, "update-schedule", "--schedule-id", "SCH-001", "--start", str(monday(8)),
                   "--day", "mon", "--lessons", "2", "--apply")
    assert p["ok"] and p["data"]["makeups_reverted"] == ["MU-001"]
    _assert_reverted(path)


def test_split_schedule_reverts_fulfilled_makeup(tmp_path, v4_data):
    path, d = _fulfilled_file(tmp_path, v4_data)
    _, p = run_cli(path, "split-schedule", "--schedule-id", "SCH-001", "--at", d,
                   "--day", "mon", "--to-time", "17:00-18:00", "--lessons", "2", "--apply")
    assert p["ok"]
    _assert_reverted(path)


def test_end_class_reverts_fulfilled_makeup_when_class_remains(tmp_path, v4_data):
    path, d = _fulfilled_file(tmp_path, v4_data)
    _, p = run_cli(path, "end-class", "--class", "C1", "--from", d, "--apply")
    assert p["ok"] and not p["data"]["class_removed"]
    _assert_reverted(path)


def test_remove_class_cascade_removes_related_makeup(tmp_path, v4_data):
    path, _ = _fulfilled_file(tmp_path, v4_data)
    _, p = run_cli(path, "remove-class", "--id", "C1", "--cascade", "--apply")
    assert p["ok"] and read_yaml(path)["makeups"] == []


def test_move_fulfilled_lesson_keeps_fulfilled_and_syncs_date(tmp_path, v4_data):
    path, d = _fulfilled_file(tmp_path, v4_data, linked=False)
    new_day = str(monday(8) + timedelta(days=4))
    _, p = run_cli(path, "move-lesson", "--class", "C1", "--from-date", d,
                   "--to-date", new_day, "--apply")
    m = read_yaml(path)["makeups"][0]
    assert p["ok"] and m["status"] == "fulfilled" and m["makeup_date"] == new_day
    assert m["makeup_lesson_id"] == "L-9000"


def test_list_makeups_filters_status(tmp_path, v4_data):
    path, _ = _fulfilled_file(tmp_path, v4_data)
    _, pending = run_cli(path, "list-makeups")
    _, all_items = run_cli(path, "list-makeups", "--status", "all")
    assert pending["data"]["count"] == 0 and all_items["data"]["count"] == 1
