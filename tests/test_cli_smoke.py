from datetime import timedelta

from conftest import codes, monday, read_yaml, run_cli


def assert_clean(cp, payload):
    assert "Traceback" not in cp.stderr
    assert set(payload) == {"ok", "data", "errors", "warnings", "next_actions"}


def test_status_baseline(v4_yaml):
    cp, p = run_cli(v4_yaml, "status")
    assert cp.returncode == 0 and p["ok"] and p["data"]["stats"]["lessons"] == 10


def test_list_classes(v4_yaml):
    _, p = run_cli(v4_yaml, "list-classes", "--with-schedules")
    assert p["data"]["count"] == 2 and p["data"]["classes"][0]["schedules"]


def test_list_slots_used(v4_yaml):
    _, p = run_cli(v4_yaml, "list-slots", "--used-only")
    assert {s["id"] for s in p["data"]["slots"]} == {"S1", "S2"}


def test_add_class_dry_run_no_write(v4_yaml):
    before = v4_yaml.read_bytes()
    cp, p = run_cli(v4_yaml, "add-class", "--name", "new", "--weekly-count", "1")
    assert cp.returncode == 0 and p["data"]["preview"] is True
    assert v4_yaml.read_bytes() == before


def test_add_class_apply(v4_yaml):
    _, p = run_cli(v4_yaml, "add-class", "--id", "C3", "--name", "new",
                   "--weekly-count", "1", "--apply")
    assert p["ok"] and any(c["id"] == "C3" for c in read_yaml(v4_yaml)["classes"])


def test_duplicate_class_clean_error(v4_yaml):
    cp, p = run_cli(v4_yaml, "add-class", "--id", "C1", "--name", "dup",
                    "--weekly-count", "1")
    assert cp.returncode != 0 and "E_DUPLICATE_ID" in codes(p)
    assert_clean(cp, p)


def test_add_schedule_expands_explicit_lessons(v4_yaml):
    start = str(monday(4))
    _, p = run_cli(v4_yaml, "add-schedule", "--class", "C1", "--slot", "S3",
                   "--start", start, "--day", "mon", "--weeks", "3", "--apply")
    data = read_yaml(v4_yaml)
    assert p["ok"] and p["data"]["lessons_added"] == 3
    assert len(data["lessons"]) == 13
    added = data["schedules"][-1]
    assert set(added).isdisjoint({"start_date", "duration_weeks", "specific_dates"})


def test_add_schedule_unknown_class_clean_error(v4_yaml):
    cp, p = run_cli(v4_yaml, "add-schedule", "--class", "NOPE", "--time", "16:00-17:00",
                    "--specific-dates", str(monday(4)))
    assert cp.returncode != 0 and "E_CLASS_NOT_FOUND" in codes(p)
    assert_clean(cp, p)


def test_add_schedule_malformed_date_clean_error(v4_yaml):
    cp, p = run_cli(v4_yaml, "add-schedule", "--class", "C1", "--slot", "S1",
                    "--specific-dates", "2026-02-30")
    assert cp.returncode != 0 and "E_SCHEMA_INVALID" in codes(p)
    assert_clean(cp, p)


def test_add_lesson_standalone(v4_yaml):
    d = str(monday(5))
    _, p = run_cli(v4_yaml, "add-lesson", "--class", "C1", "--date", d,
                   "--time", "17:00-18:00", "--apply")
    lesson = p["data"]["added_lesson"]
    assert p["ok"] and "schedule_id" not in lesson and "slot_id" not in lesson


def test_add_lesson_unknown_class(v4_yaml):
    cp, p = run_cli(v4_yaml, "add-lesson", "--class", "NOPE", "--date", str(monday(5)),
                    "--time", "17:00-18:00")
    assert "E_CLASS_NOT_FOUND" in codes(p)
    assert_clean(cp, p)


def test_cancel_missing_lesson_error(v4_yaml):
    cp, p = run_cli(v4_yaml, "cancel-lesson", "--class", "C1", "--date", "2099-01-01")
    assert cp.returncode != 0 and "E_LESSON_NOT_FOUND" in codes(p)
    assert_clean(cp, p)


def test_move_preserves_lesson_id_and_relocates(v4_yaml):
    before = read_yaml(v4_yaml)
    lesson = before["lessons"][2]
    old_date = lesson["date"]
    new_date = str(monday(6))
    _, p = run_cli(v4_yaml, "move-lesson", "--class", "C1", "--from-date", old_date,
                   "--to-date", new_date, "--to-time", "17:00-18:00", "--apply")
    after = read_yaml(v4_yaml)
    moved = next(l for l in after["lessons"] if l["id"] == lesson["id"])
    assert p["ok"] and moved["date"] == new_date and moved["time"] == "17:00-18:00"
    assert not any(l["class_id"] == "C1" and l["date"] == old_date for l in after["lessons"])
    assert len(after["lessons"]) == len(before["lessons"])


def test_move_malformed_date_clean_error(v4_yaml):
    cp, p = run_cli(v4_yaml, "move-lesson", "--class", "C1", "--from-date", "bad",
                    "--to-date", "2026-01-01")
    assert "E_SCHEMA_INVALID" in codes(p)
    assert_clean(cp, p)


def test_remove_class_requires_cascade(v4_yaml):
    _, p = run_cli(v4_yaml, "remove-class", "--id", "C1")
    assert "E_AMBIGUOUS_TARGET" in codes(p)


def test_remove_class_cascade_removes_all_refs(v4_yaml):
    _, p = run_cli(v4_yaml, "remove-class", "--id", "C1", "--cascade", "--apply")
    data = read_yaml(v4_yaml)
    assert p["ok"] and all(x.get("class_id") != "C1" for key in ("schedules", "lessons", "makeups") for x in data[key])


def test_list_conflicts_handles_time_only_lesson(v4_yaml):
    d = read_yaml(v4_yaml)["lessons"][0]["date"]
    _, add = run_cli(v4_yaml, "add-lesson", "--class", "C2", "--date", d,
                     "--time", "09:30-10:30", "--apply")
    assert not add["ok"] and "E_TIME_OVERLAP" in codes(add)


def test_next_actions_present_on_preview(v4_yaml):
    _, p = run_cli(v4_yaml, "add-class", "--name", "next", "--weekly-count", "1")
    assert p["next_actions"]
