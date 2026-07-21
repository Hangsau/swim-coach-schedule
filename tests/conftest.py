import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
CLI = ROOT / "scripts" / "schedule_cli.py"


def monday(offset_weeks=0):
    today = date.today()
    return today - timedelta(days=today.weekday()) + timedelta(weeks=offset_weeks)


def base_data():
    days = [monday(-2), monday(-1), monday(1), monday(2), monday(3)]
    return {
        "schema_version": 4,
        "slots": [
            {"id": "S1", "time": "09:00-10:00", "note": "morning"},
            {"id": "S2", "time": "10:10-11:10", "note": "late morning"},
            {"id": "S3", "time": "14:00-15:00", "note": "afternoon"},
        ],
        "classes": [
            {"id": "C1", "name": "A class", "weekly_count": 1, "level": "L1"},
            {"id": "C2", "name": "B class", "weekly_count": 1, "level": "L2"},
        ],
        "schedules": [
            {"id": "SCH-001", "class_id": "C1", "slot_id": "S1", "time": "09:00-10:00", "label": "weekly"},
            {"id": "SCH-002", "class_id": "C2", "slot_id": "S2", "time": "10:10-11:10", "label": "weekly"},
        ],
        "lessons": [
            *[{"id": f"L-{i:04d}", "schedule_id": "SCH-001", "class_id": "C1",
               "date": str(d), "time": "09:00-10:00", "slot_id": "S1"}
              for i, d in enumerate(days, 1)],
            *[{"id": f"L-{i:04d}", "schedule_id": "SCH-002", "class_id": "C2",
               "date": str(d + timedelta(days=1)), "time": "10:10-11:10", "slot_id": "S2"}
              for i, d in enumerate(days, 101)],
        ],
        "makeups": [],
    }


def write_yaml(tmp_path, data=None, name="schedule.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data if data is not None else base_data(),
                                   allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def read_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def run_cli(path, *args):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cp = subprocess.run([sys.executable, str(CLI), "--file", str(path), "--json", *args],
                        capture_output=True, text=True, encoding="utf-8", env=env)
    try:
        payload = json.loads(cp.stdout)
    except json.JSONDecodeError:
        payload = {"_raw": cp.stdout, "_stderr": cp.stderr}
    return cp, payload


def codes(payload):
    return [e.get("code") for e in payload.get("errors", [])]


@pytest.fixture
def v4_data():
    return deepcopy(base_data())


@pytest.fixture
def v4_yaml(tmp_path, v4_data):
    return write_yaml(tmp_path, v4_data)
