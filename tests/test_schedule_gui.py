import sys
from pathlib import Path


SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import schedule_gui  # noqa: E402


def quick_tab():
    tab = schedule_gui.SwimTab.__new__(schedule_gui.SwimTab)
    tab._slots = [
        {"id": "S3", "time": "09:00-10:00"},
        {"id": "S4", "time": "10:10-11:10"},
        {"id": "S3-copy", "time": "09:00-10:00"},
    ]
    return tab


class FakeButton:
    def __init__(self):
        self.options = {}

    def config(self, **kwargs):
        self.options.update(kwargs)


def test_quick_lesson_form_needs_no_separate_class_or_schedule_step():
    fields = quick_tab()._fields_quick_add_lesson("2026-09-29")

    assert [field["flag"] for field in fields] == [
        "--name", "--date", "--time", "--note",
    ]
    assert fields[1]["value"] == "2026-09-29"
    assert fields[2]["kind"] == "combo"
    assert fields[2]["required"] is True


def test_quick_lesson_time_is_editable_list_without_duplicates():
    fields = quick_tab()._fields_quick_add_lesson()
    time_field = next(field for field in fields if field["flag"] == "--time")

    assert time_field["values"] == ["09:00-10:00", "10:10-11:10"]
    assert "08:00-09:00" in time_field["hint"]


def test_primary_action_moves_from_quick_add_to_publish_after_change():
    tab = quick_tab()
    tab.quick_btn = FakeButton()
    tab.push_btn = FakeButton()

    tab._set_action_emphasis(publish_ready=False)
    assert tab.quick_btn.options["bg"] == schedule_gui.DONE
    assert tab.push_btn.options["bg"] == schedule_gui.TRACK

    tab._set_action_emphasis(publish_ready=True)
    assert tab.quick_btn.options["bg"] == schedule_gui.TRACK
    assert tab.push_btn.options["bg"] == schedule_gui.DONE
