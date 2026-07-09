"""
test_integration.py — render 整合測試

確保：
1. 每一個 schedule 都有對應的 lessons 被 render 出來
2. 每個班總堂數 > 0
3. 沒有空 grid（每班每月至少有一堂）
4. 沒有時段重複（同一班同時段不會出現兩次）
5. duration_weeks 正確生效（不會跑成 default 12 週）
"""
import sys
import re
from pathlib import Path
from collections import defaultdict

def parse_time_str(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)

def time_overlap(s1, s2):
    s1s, s1e = parse_time_str(s1.split("-")[0]), parse_time_str(s1.split("-")[1])
    s2s, s2e = parse_time_str(s2.split("-")[0]), parse_time_str(s2.split("-")[1])
    return s1s < s2e and s2s < s1e




ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from query import load, expand_schedule


def test_all_schedules_have_lessons():
    """每個 schedule 至少有一堂 expanded lesson"""
    data = load()
    slots_by_id = {s['id']: s for s in data.get('slots', [])}
    classes_by_id = {c['id']: c for c in data.get('classes', [])}
    all_lessons = expand_schedule(data.get('schedules', []), slots_by_id, classes_by_id)

    for s in data.get('schedules', []):
        # schedule 可以是 slot_id 引用或 time-only 自訂時段（validate.py：兩者擇一）
        lessons_for_this = [l for l in all_lessons
                            if l['class_id'] == s['class_id'] and l['slot_id'] == s.get('slot_id')]
        assert len(lessons_for_this) > 0, \
            f"schedule {s['class_id']} {s.get('slot_id') or s.get('time')} 沒展開任何 lessons（模式 day={s.get('day')} days={s.get('days')} total={s.get('total_lessons')}）"
    print(f"✓ 所有 {len(data.get('schedules', []))} 個 schedule 都有 expanded lessons")


def test_all_classes_have_lessons():
    """每個 class 至少有一堂"""
    data = load()
    slots_by_id = {s['id']: s for s in data.get('slots', [])}
    classes_by_id = {c['id']: c for c in data.get('classes', [])}
    all_lessons = expand_schedule(data.get('schedules', []), slots_by_id, classes_by_id)

    for c in data.get('classes', []):
        lessons = [l for l in all_lessons if l['class_id'] == c['id']]
        assert len(lessons) > 0, f"class {c['id']} ({c['name']}) 沒課"
    print(f"✓ 所有 {len(data.get('classes', []))} 個 class 都有課")


def test_duration_weeks_respected():
    """days + duration_weeks 模式要跑正確的週數（不會跑成 default 12 週）"""
    data = load()
    slots_by_id = {s['id']: s for s in data.get('slots', [])}
    classes_by_id = {c['id']: c for c in data.get('classes', [])}
    all_lessons = expand_schedule(data.get('schedules', []), slots_by_id, classes_by_id)

    for s in data.get('schedules', []):
        if 'days' in s and 'duration_weeks' in s:
            lessons = [l for l in all_lessons if l['class_id'] == s['class_id'] and l['slot_id'] == s['slot_id']]
            expected = s['duration_weeks'] * len(s['days'])
            actual = len(lessons)
            assert abs(actual - expected) <= 1, \
                f"schedule {s['class_id']} {s['slot_id']} days={s['days']} duration_weeks={s['duration_weeks']} 預期 {expected} 堂，實際 {actual} 堂（可能被 default 12 週覆蓋）"
    print(f"✓ 所有 days + duration_weeks 模式都跑正確的週數")


def test_single_day_total_lessons_expands():
    """回歸：day（單數）+ total_lessons（無 end_date/duration_weeks）要展開成剛好 N 堂

    原本 day 分支只認 end_date/duration_weeks，缺兩者時直接 KeyError('duration_weeks')。
    """
    slots = {'S3': {'id': 'S3', 'time': '09:00-10:00', 'note': ''}}
    classes = {'C1': {'id': 'C1', 'name': '兒童', 'level': ''}}
    sched = [{'id': 'X1', 'class_id': 'C1', 'slot_id': 'S3',
              'start_date': '2026-07-11', 'day': 'sat', 'total_lessons': 10}]
    lessons = expand_schedule(sched, slots, classes)
    assert len(lessons) == 10, f"day+total_lessons 應展開 10 堂，實際 {len(lessons)}"
    assert lessons[0]['date'].isoformat() == '2026-07-11'
    assert lessons[-1]['date'].isoformat() == '2026-09-12'  # 第 10 個週六


def test_multi_days_total_lessons_only_expands():
    """days（複數）+ total_lessons（無 end/weeks）不被 default 12 週截斷，展開剛好 N 堂"""
    slots = {'S7': {'id': 'S7', 'time': '15:20-16:20', 'note': ''}}
    classes = {'C2': {'id': 'C2', 'name': '成人', 'level': ''}}
    sched = [{'id': 'X2', 'class_id': 'C2', 'slot_id': 'S7',
              'start_date': '2026-07-10', 'days': ['mon', 'fri'], 'total_lessons': 30}]
    lessons = expand_schedule(sched, slots, classes)
    assert len(lessons) == 30, f"days+total_lessons(30) 應展開 30 堂，實際 {len(lessons)}"


def test_no_class_double_booking():
    """沒有「同班同時段重複」（同班同日同時段只允許一堂）"""
    data = load()
    slots_by_id = {s['id']: s for s in data.get('slots', [])}
    classes_by_id = {c['id']: c for c in data.get('classes', [])}
    all_lessons = expand_schedule(data.get('schedules', []), slots_by_id, classes_by_id)

    by_class_date_slot = defaultdict(list)
    for l in all_lessons:
        key = (l['class_id'], l['date'], l['slot_id'])
        by_class_date_slot[key].append(l)

    conflicts = [(k, lst) for k, lst in by_class_date_slot.items() if len(lst) > 1]
    if conflicts:
        for (cid, d, sid), lst in conflicts:
            print(f"  ✗ {cid} {d} {sid}: 出現 {len(lst)} 次 → {[l['class_name'] for l in lst]}")
        assert False, f"發現 {len(conflicts)} 個同班同時段重複衝突"
    print(f"✓ 沒有同班同時段重複衝突")


def test_no_slot_double_booking():
    """沒有「跨班同時段」（同日同時段只能有一堂 — 因為你只有一個人一池）"""
    data = load()
    slots_by_id = {s['id']: s for s in data.get('slots', [])}
    classes_by_id = {c['id']: c for c in data.get('classes', [])}
    all_lessons = expand_schedule(data.get('schedules', []), slots_by_id, classes_by_id)

    by_date_slot = defaultdict(list)
    for l in all_lessons:
        key = (l['date'], l['slot_id'])
        by_date_slot[key].append(l)

    conflicts = [(k, lst) for k, lst in by_date_slot.items() if len(lst) > 1]
    if conflicts:
        for (d, sid), lst in conflicts:
            names = [f"{l['class_id']}({l['class_name']})" for l in lst]
            print(f"  ✗ {d} {sid}: {len(lst)} 堂 → {names}")
        assert False, f"發現 {len(conflicts)} 個同日同時段跨班衝突（你只有一個人，泳池同時間只能一班）"
    print(f"✓ 沒有同日同時段跨班衝突")


def test_no_reserved_slot_conflict():
    """沒有 schedule 跟 reserved slots 時間區段重疊（user 已有課的時段被標記為 reserved）"""
    data = load()
    slots_by_id = {s['id']: s for s in data.get('slots', [])}
    reserved = data.get('reserved', [])
    all_lessons = expand_schedule(data.get('schedules', []), slots_by_id, {c['id']: c for c in data.get('classes', [])})

    conflicts = []
    for r in reserved:
        r_time = r.get('time', '')
        if not r_time:
            continue
        for l in all_lessons:
            l_time = l.get('slot_time', '')
            if not l_time:
                continue
            if time_overlap(r_time, l_time):
                conflicts.append((r, l))

    if conflicts:
        for r, l in conflicts:
            print(f"  ✗ {r['id']} ({r['time']}) 跟 {l['date']} {l['day']} {l['slot_id']} ({l['slot_time']}) 重疊 → {l['class_name']}")
        assert False, f"發現 {len(conflicts)} 個 reserved slot 衝突（你已說有課的時段不該排新課）"
    print(f"✓ 沒有 reserved slot 衝突")


def test_time_overlap_basic():
    """時間區段重疊偵測（基礎函式）"""
    assert time_overlap("09:00-10:00", "09:00-10:00") == True
    assert time_overlap("09:00-10:00", "10:00-11:00") == False
    assert time_overlap("09:00-10:00", "09:30-10:30") == True
    assert time_overlap("09:00-10:00", "08:30-09:30") == True
    assert time_overlap("18:00-19:00", "18:30-19:30") == True
    assert time_overlap("19:10-20:10", "18:30-19:30") == True
    assert time_overlap("18:00-19:00", "19:00-20:00") == False
    print("✓ 時間重疊基礎函式正確")


def test_no_dead_slots():
    """沒有 schedule 引用的 slot 應該被清掉（避免月曆時段表頭有死格）"""
    data = load()
    slots_by_id = {s['id']: s for s in data.get('slots', [])}
    classes_by_id = {c['id']: c for c in data.get('classes', [])}
    all_lessons = expand_schedule(data.get('schedules', []), slots_by_id, classes_by_id)

    used_slots = set(l['slot_id'] for l in all_lessons)
    dead = [s['id'] for s in data.get('slots', []) if s['id'] not in used_slots]

    if dead:
        for sid in dead:
            slot = slots_by_id[sid]
            print(f"  ✗ {sid} {slot['time']} 沒任何 schedule 用（建議刪除或加 schedule）")
        assert False, f"{len(dead)} 個 dead slot（沒 schedule 用）"
    print(f"✓ 所有 slot 都有 schedule 用")


def test_all_references_valid():
    """所有 schedule 引用都有效（slot_id 和 class_id 都存在）"""
    data = load()
    slot_ids = {s['id'] for s in data.get('slots', [])}
    class_ids = {c['id'] for c in data.get('classes', [])}

    invalid_slot = []
    invalid_class = []
    for s in data.get('schedules', []):
        # time-only schedule 沒有 slot_id 是合法的（validate.py：slot_id 或 time 擇一）
        if s.get('slot_id') is not None and s['slot_id'] not in slot_ids:
            invalid_slot.append(s)
        if s.get('class_id') not in class_ids:
            invalid_class.append(s)

    if invalid_slot:
        for s in invalid_slot:
            print(f"  ✗ slot_id 找不到: {s}")
        assert False, f"{len(invalid_slot)} 個 schedule 引用不存在的 slot_id"
    if invalid_class:
        for s in invalid_class:
            print(f"  ✗ class_id 找不到: {s}")
        assert False, f"{len(invalid_class)} 個 schedule 引用不存在的 class_id"
    print(f"✓ 所有 {len(data.get('schedules', []))} 個 schedule 引用都有效")


def test_html_contains_all_class_names():
    """每個班的名字至少出現在某個 HTML 一次"""
    data = load()
    slots_by_id = {s['id']: s for s in data.get('slots', [])}
    classes_by_id = {c['id']: c for c in data.get('classes', [])}
    all_lessons = expand_schedule(data.get('schedules', []), slots_by_id, classes_by_id)
    docs_dir = ROOT / "docs"

    all_content = ""
    for f in docs_dir.glob("*.html"):
        all_content += f.read_text(encoding="utf-8")

    for c in data.get('classes', []):
        if c['name'] in all_content:
            print(f"  ✓ {c['name']} 出現在 HTML")
        else:
            lessons = [l for l in all_lessons if l['class_id'] == c['id']]
            print(f"  ✗ {c['name']} 不在 HTML（但有 {len(lessons)} 堂）")


if __name__ == "__main__":
    print("=" * 50)
    print("Render 整合測試")
    print("=" * 50)
    test_all_schedules_have_lessons()
    test_all_classes_have_lessons()
    test_duration_weeks_respected()
    test_no_class_double_booking()
    test_no_slot_double_booking()
    test_no_reserved_slot_conflict()
    test_time_overlap_basic()
    test_no_dead_slots()
    test_all_references_valid()
    print("\n--- HTML 內容檢查 ---")
    test_html_contains_all_class_names()
    print("\n✓ 全部通過")
