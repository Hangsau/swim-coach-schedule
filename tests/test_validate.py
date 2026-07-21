from copy import deepcopy

import pytest

from conftest import write_yaml

import sys
from pathlib import Path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from validate import time_overlap, validate_all  # noqa: E402


def result(tmp_path, data, strict=False):
    return validate_all(write_yaml(tmp_path, data), strict=strict)


def has(r, code):
    return any(e["code"] == code for e in r["errors"])


def test_baseline_valid(tmp_path, v4_data):
    r = result(tmp_path, v4_data)
    assert r["ok"] and r["stats"]["lessons"] == 10


def test_schema_version_requires_v4(tmp_path, v4_data):
    v4_data["schema_version"] = 3
    r = result(tmp_path, v4_data)
    assert has(r, "E_SCHEMA_VERSION") and "migrate_v4.py" in r["errors"][0]["msg"]


@pytest.mark.parametrize("field", ["id", "class_id", "date", "time"])
def test_lesson_required_fields(tmp_path, v4_data, field):
    v4_data["lessons"][0].pop(field)
    assert has(result(tmp_path, v4_data), "E_SCHEMA_INVALID")


@pytest.mark.parametrize("bad", ["2026-2-03", "not-a-date", "2026-02-30"])
def test_lesson_malformed_dates(tmp_path, v4_data, bad):
    v4_data["lessons"][0]["date"] = bad
    assert has(result(tmp_path, v4_data), "E_SCHEMA_INVALID")


@pytest.mark.parametrize("bad", ["9:00-10:00", "25:00-26:00", "10:00-09:00"])
def test_lesson_invalid_times(tmp_path, v4_data, bad):
    v4_data["lessons"][0]["time"] = bad
    assert not result(tmp_path, v4_data)["ok"]


def test_duplicate_lesson_id(tmp_path, v4_data):
    v4_data["lessons"][1]["id"] = v4_data["lessons"][0]["id"]
    assert has(result(tmp_path, v4_data), "E_DUPLICATE_ID")


def test_missing_class_reference(tmp_path, v4_data):
    v4_data["lessons"][0]["class_id"] = "NOPE"
    assert has(result(tmp_path, v4_data), "E_CLASS_NOT_FOUND")


def test_missing_schedule_reference(tmp_path, v4_data):
    v4_data["lessons"][0]["schedule_id"] = "SCH-NOPE"
    assert has(result(tmp_path, v4_data), "E_SCHEDULE_NOT_FOUND")


def test_standalone_lesson_needs_no_schedule(tmp_path, v4_data):
    v4_data["lessons"][0].pop("schedule_id")
    assert result(tmp_path, v4_data)["ok"]


def test_overlap_different_slots(tmp_path, v4_data):
    other = deepcopy(v4_data["lessons"][0])
    other.update(id="L-9000", class_id="C2", schedule_id="SCH-002",
                 slot_id="S2", time="09:30-10:30")
    v4_data["lessons"].append(other)
    assert has(result(tmp_path, v4_data), "E_TIME_OVERLAP")


def test_overlap_with_none_slot_is_cleanly_reported(tmp_path, v4_data):
    d = v4_data["lessons"][0]["date"]
    v4_data["lessons"].append({"id": "L-9000", "class_id": "C2", "date": d,
                               "time": "09:30-10:30"})
    r = result(tmp_path, v4_data)
    assert has(r, "E_TIME_OVERLAP")
    assert all("Traceback" not in e["msg"] for e in r["errors"])


def test_touching_times_do_not_overlap():
    assert not time_overlap("09:00-10:00", "10:00-11:00")


def test_overlapping_times_overlap():
    assert time_overlap("09:00-10:00", "09:59-11:00")


def test_weekly_count_is_metadata_only(tmp_path, v4_data):
    v4_data["classes"][0]["weekly_count"] = 1
    # Three explicit lessons in one week are allowed by v4.
    base = v4_data["lessons"][2]
    v4_data["lessons"].extend([
        {**base, "id": "L-8001", "date": str(base["date"]), "time": "16:00-17:00", "slot_id": None},
        {**base, "id": "L-8002", "date": str(base["date"]), "time": "18:00-19:00", "slot_id": None},
    ])
    assert result(tmp_path, v4_data)["ok"]


def test_orphan_class_warns_then_strict_fails(tmp_path, v4_data):
    v4_data["classes"].append({"id": "C3", "name": "orphan", "weekly_count": 1})
    loose = result(tmp_path, v4_data)
    strict = result(tmp_path, v4_data, strict=True)
    assert loose["ok"] and any(w["code"] == "W_ORPHAN_CLASS" for w in loose["warnings"])
    assert has(strict, "E_ORPHAN_CLASS")


def test_schedule_metadata_prevents_orphan(tmp_path, v4_data):
    v4_data["classes"].append({"id": "C3", "name": "planned", "weekly_count": 1})
    v4_data["schedules"].append({"id": "SCH-003", "class_id": "C3", "time": "18:00-19:00"})
    assert result(tmp_path, v4_data, strict=True)["ok"]


def test_fulfilled_makeup_requires_lesson_reference(tmp_path, v4_data):
    v4_data["makeups"] = [{"id": "MU-001", "class_id": "C1", "origin_date": "2026-01-01",
                            "status": "fulfilled", "makeup_date": "2026-01-02"}]
    assert has(result(tmp_path, v4_data), "E_SCHEMA_INVALID")


def test_fulfilled_makeup_rejects_dangling_lesson(tmp_path, v4_data):
    v4_data["makeups"] = [{"id": "MU-001", "class_id": "C1", "origin_date": "2026-01-01",
                            "status": "fulfilled", "makeup_date": "2026-01-02",
                            "makeup_lesson_id": "L-NOPE"}]
    assert has(result(tmp_path, v4_data), "E_LESSON_NOT_FOUND")


def test_pending_makeup_allows_null_fulfillment_fields(tmp_path, v4_data):
    v4_data["makeups"] = [{"id": "MU-001", "class_id": "C1", "origin_date": "2026-01-01",
                            "status": "pending", "makeup_date": None, "makeup_lesson_id": None}]
    assert result(tmp_path, v4_data)["ok"]


def test_unknown_lesson_field_is_rejected_with_path(tmp_path, v4_data):
    v4_data["lessons"][0]["future_extension"] = {"safe": True}
    r = result(tmp_path, v4_data)
    matching = [e for e in r["errors"] if e["code"] == "E_SCHEMA_INVALID"
                and e["context"].get("path") == "lessons[0].future_extension"]
    assert not r["ok"]
    assert matching
