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


def test_class_list_puts_active_first_and_folds_ended():
    from datetime import date
    tab = schedule_gui.SwimTab.__new__(schedule_gui.SwimTab)
    tab._classes = [{"id": "STU-01"}, {"id": "STU-02"}, {"id": "STU-03"}, {"id": "STU-04"}, {"id": "STU-05"}]
    tab._all_lessons = [
        {"class_id": "STU-01", "date": date(2026, 9, 1)},   # 已結束，較早
        {"class_id": "STU-02", "date": date(2026, 10, 20)},  # 進行中，下一堂較晚
        {"class_id": "STU-03", "date": date(2026, 9, 20)},   # 已結束，較近
        {"class_id": "STU-04", "date": date(2026, 9, 30)},
        {"class_id": "STU-04", "date": date(2026, 10, 3)},   # 進行中，下一堂較早
    ]
    tab._makeups = [{"class_id": "STU-05", "status": "pending"}]  # 沒有課但欠補，仍算進行中
    active, ended = tab._split_classes(date(2026, 10, 1))
    assert [c["id"] for c, *_ in active] == ["STU-04", "STU-02", "STU-05"]
    assert active[0][1:] == (1, 0, date(2026, 10, 3))
    assert active[2][1:] == (0, 1, None)
    assert [(c["id"], last) for c, last in ended] == [("STU-03", date(2026, 9, 20)), ("STU-01", date(2026, 9, 1))]


def _lessons(sid, *days):
    from datetime import date
    return [{"schedule_id": sid, "class_id": "STU-01", "date": date.fromisoformat(d)} for d in days]


def test_postpone_goes_to_next_weekly_slot_after_last_lesson():
    from datetime import date
    ls = _lessons("SCH-1", "2026-09-12", "2026-09-19", "2026-09-26", "2026-10-24")  # 週六
    assert schedule_gui.postpone_target(ls, ls[0]) == date(2026, 10, 31)
    assert schedule_gui.postpone_target(ls, ls[-1]) == date(2026, 10, 31)  # 最後一堂也往後推一週


def test_postpone_follows_two_day_pattern_and_ignores_one_off_moves():
    from datetime import date
    # 一三班，最後一堂 10/28（三）；10/30（五）是挪過的單堂，不算固定星期
    ls = _lessons("SCH-2", "2026-10-19", "2026-10-21", "2026-10-26", "2026-10-28", "2026-10-30")
    assert schedule_gui.postpone_target(ls, ls[0]) == date(2026, 11, 2)


def test_postpone_counts_whole_class_including_standalone_lessons():
    from datetime import date
    # 真實情境（STU-11）：主排課 9/26 結束，之後都是補課獨立課次；別班的課不能算進來
    ls = _lessons("SCH-014", "2026-09-05", "2026-09-26") + _lessons(None, "2026-10-03", "2026-10-24")
    other = [{"schedule_id": None, "class_id": "STU-99", "date": date(2026, 12, 5)}]
    assert schedule_gui.postpone_target(ls + other, ls[2]) == date(2026, 10, 31)
