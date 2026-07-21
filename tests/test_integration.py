from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys

from conftest import read_yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from query import expand_schedule  # noqa: E402


def expanded(path):
    data = read_yaml(path)
    slots = {s["id"]: s for s in data.get("slots", []) if s.get("id")}
    classes = {c["id"]: c for c in data.get("classes", []) if c.get("id")}
    return expand_schedule(data.get("schedules", []), slots, classes, data=data)


def test_expand_returns_every_explicit_lesson(v4_yaml):
    assert len(expanded(v4_yaml)) == len(read_yaml(v4_yaml)["lessons"])


def test_expand_sorted_by_date_and_time(v4_yaml):
    lessons = expanded(v4_yaml)
    keys = [(l["date"], l["slot_time"], l["class_id"]) for l in lessons]
    assert keys == sorted(keys)


def test_expand_preserves_schedule_links(v4_yaml):
    raw_links = [(l.get("schedule_id"), l["class_id"], l["date"]) for l in read_yaml(v4_yaml)["lessons"]]
    expanded_links = [(l.get("schedule_id"), l["class_id"], str(l["date"])) for l in expanded(v4_yaml)]
    assert sorted(expanded_links) == sorted(raw_links)


def test_expand_enriches_class_name(v4_yaml):
    by_id = {l["class_id"]: l["class_name"] for l in expanded(v4_yaml)}
    assert by_id == {"C1": "A class", "C2": "B class"}


def test_expand_enriches_slot_note(v4_yaml):
    assert any(l["slot_note"] == "morning" for l in expanded(v4_yaml))


def test_expand_supports_standalone_time_only(v4_yaml):
    data = read_yaml(v4_yaml)
    data["lessons"].append({"id": "L-9000", "class_id": "C1", "date": "2027-01-01",
                            "time": "17:00-18:00", "note": "extra"})
    v4_yaml.write_text(__import__("yaml").safe_dump(data, sort_keys=False), encoding="utf-8")
    item = next(l for l in expanded(v4_yaml) if l["note"] == "extra")
    assert item["slot_id"] is None and item["slot_time"] == "17:00-18:00"


def test_schedule_metadata_does_not_generate_lessons(v4_yaml):
    data = read_yaml(v4_yaml)
    data["schedules"].append({"id": "SCH-999", "class_id": "C1", "time": "18:00-19:00"})
    v4_yaml.write_text(__import__("yaml").safe_dump(data, sort_keys=False), encoding="utf-8")
    assert len(expanded(v4_yaml)) == 10


def test_lessons_without_schedule_are_not_lost(v4_yaml):
    data = read_yaml(v4_yaml)
    data["lessons"][0].pop("schedule_id")
    v4_yaml.write_text(__import__("yaml").safe_dump(data, sort_keys=False), encoding="utf-8")
    assert len(expanded(v4_yaml)) == 10


def test_empty_lessons_expand_empty(tmp_path, v4_data):
    from conftest import write_yaml
    v4_data["lessons"] = []
    assert expanded(write_yaml(tmp_path, v4_data)) == []


def test_large_explicit_list_expands_exactly(tmp_path, v4_data):
    from conftest import write_yaml
    v4_data["lessons"] = [{"id": f"L-{i:04d}", "class_id": "C1",
                            "date": f"2027-{1 + (i // 28) % 12:02d}-{1 + i % 28:02d}",
                            "time": "17:00-18:00"} for i in range(1, 501)]
    path = write_yaml(tmp_path, v4_data)
    assert len(expanded(path)) == 500


def test_expanded_dates_are_date_objects(v4_yaml):
    assert all(hasattr(l["date"], "weekday") for l in expanded(v4_yaml))


def test_schedule_time_does_not_override_lesson_time(v4_yaml):
    data = read_yaml(v4_yaml)
    data["schedules"][0]["time"] = "20:00-21:00"
    v4_yaml.write_text(__import__("yaml").safe_dump(data, sort_keys=False), encoding="utf-8")
    assert all(l["slot_time"] == "09:00-10:00" for l in expanded(v4_yaml) if l["class_id"] == "C1")
