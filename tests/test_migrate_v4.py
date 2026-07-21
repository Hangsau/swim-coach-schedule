from collections import Counter
from copy import deepcopy
from pathlib import Path
import sys

import pytest
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import migrate_v4  # noqa: E402


def legacy_data():
    exclusion_key = "except_" + "dates"
    return {
        "schema_version": 3,
        "slots": [{"id": "S1", "time": "09:00-10:00", "note": "morning"}],
        "classes": [{"id": "C1", "name": "A", "weekly_count": 2, "level": "L1"}],
        "schedules": [
            {"id": "SCH-001", "class_id": "C1", "slot_id": "S1", "time": "09:00-10:00",
             "days": ["mon", "wed"], "start_date": "2026-01-05", "total_lessons": 5,
             exclusion_key: ["2026-01-07"]},
            {"id": "SCH-002", "class_id": "C1", "time": "17:00-18:00",
             "specific_dates": ["2026-02-01"]},
        ],
        "makeups": [{"id": "MU-001", "class_id": "C1", "origin_date": "2026-01-07",
                     "status": "fulfilled", "makeup_date": "2026-02-01",
                     "makeup_schedule_id": "SCH-002"}],
    }


def write_legacy(tmp_path, data=None):
    path = tmp_path / "schedule.yaml"
    path.write_text(yaml.safe_dump(data if data is not None else legacy_data(), sort_keys=False), encoding="utf-8")
    return path


def triples_from_expanded(data):
    slots = {s["id"]: s for s in data.get("slots", [])}
    classes = {c["id"]: c for c in data.get("classes", [])}
    expanded = migrate_v4.v3_expand_schedule(data.get("schedules", []), slots, classes)
    return Counter((l["class_id"], str(l["date"]), l["slot_time"]) for l in expanded)


def triples_from_lessons(data):
    return Counter((l["class_id"], str(l["date"]), l["time"]) for l in data.get("lessons", []))


def test_migration_preserves_class_date_time_multiset(tmp_path):
    old = legacy_data()
    expected = triples_from_expanded(old)
    path = write_legacy(tmp_path, old)
    assert migrate_v4.migrate(path) == 0
    assert triples_from_lessons(yaml.safe_load(path.read_text(encoding="utf-8"))) == expected


def test_migration_second_run_is_idempotent(tmp_path):
    path = write_legacy(tmp_path)
    assert migrate_v4.migrate(path) == 0
    once = path.read_bytes()
    assert migrate_v4.migrate(path) == 0
    assert path.read_bytes() == once


def test_migration_writes_exact_backup(tmp_path):
    path = write_legacy(tmp_path)
    original = path.read_bytes()
    assert migrate_v4.migrate(path) == 0
    assert (tmp_path / "schedule.pre-v4.yaml").read_bytes() == original


def test_migration_converts_fulfilled_makeup_reference(tmp_path):
    path = write_legacy(tmp_path)
    assert migrate_v4.migrate(path) == 0
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    m = data["makeups"][0]
    assert m["makeup_lesson_id"] in {l["id"] for l in data["lessons"]}
    assert "makeup_schedule_id" not in m


def test_empty_migration(tmp_path):
    path = write_legacy(tmp_path, {"schema_version": 3, "slots": [], "classes": [], "schedules": []})
    assert migrate_v4.migrate(path) == 0
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 4 and data["lessons"] == []


def test_partial_optional_sections_migrate(tmp_path):
    path = write_legacy(tmp_path, {"schema_version": 3})
    assert migrate_v4.migrate(path) == 0
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data == {"schema_version": 4, "slots": [], "classes": [], "schedules": [], "lessons": []}


def test_large_migration_keeps_all_lessons(tmp_path):
    data = legacy_data()
    data["schedules"] = [{"id": f"SCH-{i:03d}", "class_id": "C1", "time": "17:00-18:00",
                          "specific_dates": [f"2026-{1 + (i // 28) % 12:02d}-{1 + i % 28:02d}"]}
                         for i in range(1, 301)]
    data["makeups"] = []
    path = write_legacy(tmp_path, data)
    assert migrate_v4.migrate(path) == 0
    migrated = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert len(migrated["lessons"]) == 300 and len({l["id"] for l in migrated["lessons"]}) == 300


def test_failed_makeup_mapping_does_not_write(tmp_path):
    data = legacy_data()
    data["makeups"][0]["makeup_schedule_id"] = "SCH-NOPE"
    path = write_legacy(tmp_path, data)
    before = path.read_bytes()
    assert migrate_v4.migrate(path) == 1
    assert path.read_bytes() == before and not (tmp_path / "schedule.pre-v4.yaml").exists()


def test_interrupted_target_write_leaves_original_bytes(tmp_path, monkeypatch):
    path = write_legacy(tmp_path)
    before = path.read_bytes()

    def fail_replace(source, target):
        assert Path(target) == path
        assert Path(source).parent == path.parent
        assert Path(source).read_text(encoding="utf-8").startswith("schema_version: 4")
        raise OSError("simulated interruption")

    monkeypatch.setattr(migrate_v4.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated interruption"):
        migrate_v4.migrate(path)
    assert path.read_bytes() == before
    assert list(tmp_path.glob(f".{path.name}.*.tmp")) == []


@pytest.mark.parametrize("version", [None, 1, 2, 5, "3"])
def test_unsupported_schema_does_not_write_target_or_backup(tmp_path, version):
    data = legacy_data()
    if version is None:
        data.pop("schema_version")
    else:
        data["schema_version"] = version
    path = write_legacy(tmp_path, data)
    before = path.read_bytes()

    assert migrate_v4.migrate(path) == 1
    assert path.read_bytes() == before
    assert not (tmp_path / "schedule.pre-v4.yaml").exists()
    assert list(tmp_path.glob(f".{path.name}.*.tmp")) == []
