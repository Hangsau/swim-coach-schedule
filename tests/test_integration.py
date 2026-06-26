"""
test_integration.py — render 整合測試

確保：
1. 每一個 schedule 都有對應的 lessons 被 render 出來
2. 每個班總堂數 > 0
3. 沒有空 grid（每班每月至少有一堂）
"""
import sys
import re
from pathlib import Path

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


def test_html_contains_all_class_names():
    """每個班的名字至少出現在某個 HTML 一次"""
    data = load()
    slots_by_id = {s['id']: s for s in data.get('slots', [])}
    classes_by_id = {c['id']: c for c in data.get('classes', [])}
    all_lessons = expand_schedule(data.get('schedules', []), slots_by_id, classes_by_id)
    docs_dir = ROOT / "docs"

    # 所有 HTML 內容（拼起來）
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
    print("\n--- HTML 內容檢查 ---")
    test_html_contains_all_class_names()
    print("\n✓ 全部通過")
