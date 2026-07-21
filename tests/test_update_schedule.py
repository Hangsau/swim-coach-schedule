from datetime import date, timedelta

from conftest import codes, monday, read_yaml, run_cli


def test_update_time_changes_future_in_place(v4_yaml):
    before = read_yaml(v4_yaml)
    past = {l["id"]: l for l in before["lessons"] if l["schedule_id"] == "SCH-001" and l["date"] < str(date.today())}
    future_ids = {l["id"] for l in before["lessons"] if l["schedule_id"] == "SCH-001" and l["date"] >= str(date.today())}
    _, p = run_cli(v4_yaml, "update-schedule", "--schedule-id", "SCH-001",
                   "--time", "16:00-17:00", "--apply")
    after = read_yaml(v4_yaml)
    by_id = {l["id"]: l for l in after["lessons"]}
    assert p["ok"] and all(by_id[i]["time"] == "16:00-17:00" for i in future_ids)
    assert all(by_id[i]["time"] == l["time"] for i, l in past.items())


def test_update_pattern_preserves_past_and_rebuilds_future(v4_yaml):
    before = read_yaml(v4_yaml)
    past_ids = {l["id"] for l in before["lessons"] if l["schedule_id"] == "SCH-001" and l["date"] < str(date.today())}
    _, p = run_cli(v4_yaml, "update-schedule", "--schedule-id", "SCH-001",
                   "--start", str(monday(4)), "--day", "mon", "--lessons", "3", "--apply")
    data = read_yaml(v4_yaml)
    assert p["ok"] and p["data"]["past_lessons_lost"] == 0
    assert past_ids <= {l["id"] for l in data["lessons"]}
    assert p["data"]["lessons_after"] == len(past_ids) + 3


def test_update_dry_run_bytes_unchanged(v4_yaml):
    before = v4_yaml.read_bytes()
    _, p = run_cli(v4_yaml, "update-schedule", "--schedule-id", "SCH-001", "--time", "16:00-17:00")
    assert p["ok"] and p["data"]["preview"] is True and v4_yaml.read_bytes() == before


def test_update_unknown_schedule_clean_error(v4_yaml):
    cp, p = run_cli(v4_yaml, "update-schedule", "--schedule-id", "SCH-999", "--time", "16:00-17:00")
    assert "E_SCHEDULE_NOT_FOUND" in codes(p) and "Traceback" not in cp.stderr


def test_update_unknown_class_clean_error(v4_yaml):
    cp, p = run_cli(v4_yaml, "update-schedule", "--class", "NOPE", "--time", "16:00-17:00")
    assert "E_SCHEDULE_NOT_FOUND" in codes(p) and "Traceback" not in cp.stderr


def test_update_no_edit_fields(v4_yaml):
    _, p = run_cli(v4_yaml, "update-schedule", "--schedule-id", "SCH-001")
    assert "E_SCHEMA_INVALID" in codes(p)


def test_update_malformed_start_clean_error(v4_yaml):
    cp, p = run_cli(v4_yaml, "update-schedule", "--schedule-id", "SCH-001", "--start", "bad")
    assert "E_SCHEMA_INVALID" in codes(p) and "Traceback" not in cp.stderr


def test_update_class_ambiguous_target(v4_yaml):
    data = read_yaml(v4_yaml)
    data["schedules"].append({"id": "SCH-003", "class_id": "C1", "time": "18:00-19:00"})
    data["lessons"].append({"id": "L-9000", "schedule_id": "SCH-003", "class_id": "C1",
                            "date": str(monday(4)), "time": "18:00-19:00"})
    v4_yaml.write_text(__import__("yaml").safe_dump(data, sort_keys=False), encoding="utf-8")
    _, p = run_cli(v4_yaml, "update-schedule", "--class", "C1", "--time", "16:00-17:00")
    assert "E_AMBIGUOUS_TARGET" in codes(p)


def test_remove_schedule_deletes_its_lessons(v4_yaml):
    before = read_yaml(v4_yaml)
    expected = sum(l.get("schedule_id") == "SCH-001" for l in before["lessons"])
    _, p = run_cli(v4_yaml, "remove-schedule", "--schedule-id", "SCH-001", "--apply")
    after = read_yaml(v4_yaml)
    assert p["ok"] and p["data"]["removed_lessons"] == expected
    assert all(l.get("schedule_id") != "SCH-001" for l in after["lessons"])


def test_remove_schedule_not_found(v4_yaml):
    _, p = run_cli(v4_yaml, "remove-schedule", "--schedule-id", "SCH-999")
    assert "E_SCHEDULE_NOT_FOUND" in codes(p)


def test_split_schedule_creates_new_metadata_and_lessons(v4_yaml):
    at = str(monday(1))
    _, p = run_cli(v4_yaml, "split-schedule", "--schedule-id", "SCH-001", "--at", at,
                   "--day", "wed", "--to-slot", "S3", "--lessons", "3", "--apply")
    data = read_yaml(v4_yaml)
    after_id = p["data"]["after"]["schedule"]["id"]
    assert p["ok"] and sum(l.get("schedule_id") == after_id for l in data["lessons"]) == 3


def test_split_requires_termination(v4_yaml):
    _, p = run_cli(v4_yaml, "split-schedule", "--schedule-id", "SCH-001",
                   "--at", str(monday(1)), "--day", "mon")
    assert "E_NO_TERMINATION" in codes(p)
