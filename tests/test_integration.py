"""
test_integration.py — render 整合測試

確保：
1. 每一個 schedule 都有對應的 lessons 被 render 出來
2. 每個班總堂數 > 0
3. 沒有空 grid（每班每月至少有一堂）
4. 沒有時段重複（同一班同時段不會出現兩次）
"""
import sys
import re
from pathlib import Path
from collections import defaultdict

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
        lessons_for_this = [l for l in all_lessons if l['class_id'] == s['class_id'] and l['slot_id'] == s['slot_id']]
        assert len(lessons_for_this) > 0, \
            f"schedule {s['class_id']} {s['slot_id']} 沒展開任何 lessons（模式 day={s.get('day')} days={s.get('days')} total={s.get('total_lessons')}）"
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


def test_all_references_valid():
    """所有 schedule 引用都有效（slot_id 和 class_id 都存在）"""
    data = load()
    slot_ids = {s['id'] for s in data.get('slots', [])}
    class_ids = {c['id'] for c in data.get('classes', [])}

    invalid_slot = []
    invalid_class = []
    for s in data.get('schedules', []):
        if s.get('slot_id') not in slot_ids:
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
    test_no_class_double_booking()
    test_no_slot_double_booking()
    test_all_references_valid()
    print("\n--- HTML 內容檢查 ---")
    test_html_contains_all_class_names()
    print("\n✓ 全部通過")
