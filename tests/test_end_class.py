from datetime import timedelta

from conftest import codes, monday, read_yaml, run_cli, write_yaml


def test_end_class_keeps_past_and_removes_future(v4_yaml):
    cutoff = str(monday())
    before = read_yaml(v4_yaml)
    past_ids = {l["id"] for l in before["lessons"] if l["class_id"] == "C1" and l["date"] < cutoff}
    _, p = run_cli(v4_yaml, "end-class", "--class", "C1", "--from", cutoff, "--apply")
    after = read_yaml(v4_yaml)
    ids = {l["id"] for l in after["lessons"]}
    assert p["ok"] and past_ids <= ids
    assert all(l["date"] < cutoff for l in after["lessons"] if l["class_id"] == "C1")


def test_end_class_removes_empty_schedule(v4_yaml):
    cutoff = str(monday(-10))
    _, p = run_cli(v4_yaml, "end-class", "--class", "C1", "--from", cutoff, "--apply")
    data = read_yaml(v4_yaml)
    assert p["data"]["class_removed"] is True
    assert all(s["class_id"] != "C1" for s in data["schedules"])
    assert all(c["id"] != "C1" for c in data["classes"])


def test_end_class_preserves_other_class(v4_yaml):
    run_cli(v4_yaml, "end-class", "--class", "C1", "--from", str(monday()), "--apply")
    data = read_yaml(v4_yaml)
    assert any(c["id"] == "C2" for c in data["classes"])
    assert any(l["class_id"] == "C2" for l in data["lessons"])


def test_end_class_dry_run_no_write(v4_yaml):
    before = v4_yaml.read_bytes()
    _, p = run_cli(v4_yaml, "end-class", "--class", "C1", "--from", str(monday()))
    assert p["ok"] and p["data"]["preview"] is True and v4_yaml.read_bytes() == before


def test_end_class_unknown_class_clean_error(v4_yaml):
    cp, p = run_cli(v4_yaml, "end-class", "--class", "NOPE", "--from", str(monday()))
    assert "E_CLASS_NOT_FOUND" in codes(p) and "Traceback" not in cp.stderr


def test_end_class_malformed_date_clean_error(v4_yaml):
    cp, p = run_cli(v4_yaml, "end-class", "--class", "C1", "--from", "2026-02-30")
    assert "E_SCHEMA_INVALID" in codes(p) and "Traceback" not in cp.stderr


def test_end_class_without_lessons_or_schedule_errors(tmp_path, v4_data):
    v4_data["classes"].append({"id": "C3", "name": "empty", "weekly_count": 1})
    path = write_yaml(tmp_path, v4_data)
    _, p = run_cli(path, "end-class", "--class", "C3", "--from", str(monday()))
    assert "E_SCHEMA_INVALID" in codes(p)


def test_end_class_removes_makeups_if_class_removed(tmp_path, v4_data):
    v4_data["makeups"] = [{"id": "MU-001", "class_id": "C1", "origin_date": str(monday()),
                            "status": "pending", "makeup_date": None, "makeup_lesson_id": None}]
    path = write_yaml(tmp_path, v4_data)
    _, p = run_cli(path, "end-class", "--class", "C1", "--from", str(monday(-10)), "--apply")
    assert p["data"]["makeups_removed"] == 1 and read_yaml(path)["makeups"] == []
