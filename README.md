# 游泳教練課表管理

> 個人游泳教練的課表 + 查詢工具
> 一個 YAML 檔 + 一個 CLI script，沒別的依賴

## 🌐 線上版

主網址：<https://hangsau.github.io/swim-coach-schedule/>

常用頁面：
- 月曆 view：<https://hangsau.github.io/swim-coach-schedule/2026-07.html>
- 每日 grid：<https://hangsau.github.io/swim-coach-schedule/grid-2026-07.html>
- 學員總表：<https://hangsau.github.io/swim-coach-schedule/summary.html>

> GitHub Pages 自動 build + deploy。每次 push main 後 30 秒內更新。

---

## 用法

### 1. 編輯 `data/schedule.yaml`

填三段：
- **`slots:`** — 一天有哪些時段（時間 + 備註）
- **`classes:`** — 你有哪些學員 / 班級（名稱 + 每週幾堂 + 程度）
- **`schedules:`** — 每個班的排課（哪一天、哪個時段、什麼日期開始、持續幾週）

### 2. 查詢

```bash
# 今日課表
python3 scripts/query.py today

# 本週
python3 scripts/query.py week

# 本月
python3 scripts/query.py month

# 指定某一天
python3 scripts/query.py day 2026-07-15

# 某個學員的所有課
python3 scripts/query.py class C04

# 某個時段的所有課
python3 scripts/query.py slot morning-1
```

### 3. 範例

`data/schedule.yaml` 已填 4 個範例班級（學齡前、青少年、成人、暑期密集）讓你看格式。

---

## 怎麼改成你真實的課表

把 `data/schedule.yaml` 改成你自己的內容：

```yaml
slots:
  - id: <時段ID>
    time: "HH:MM-HH:MM"
    note: <備註>

classes:
  - id: <班級ID>
    name: <學員名>
    weekly_count: <每週幾堂>
    level: <程度>

schedules:
  - class_id: <對應classes的ID>
    day: <mon/tue/wed/thu/fri/sat/sun>
    slot_id: <對應slots的ID>
    start_date: <YYYY-MM-DD>
    duration_weeks: <週數>
    note: <備註>
```

---

## 安裝

只需 Python 3.10+ 和 PyYAML：

```bash
pip install pyyaml
```

沒其他依賴。

---

## 加新功能

之後可以加（你需要再說）：
- `.ics` 行事曆匯出（給 Google Calendar）
- 衝堂偵測（同老師同時段重複）
- 月報表（這個月幾堂、哪個學員最多）
- 通知（學員第 5 堂、還剩 X 堂通知）
