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
- 月報表（這個月幾堂、哪個學員最多）
- 通知（學員第 5 堂、還剩 X 堂通知）

---

## 給 LLM（minimax / claude-m3）的操作協議

> Hang 透過 TG 對你（LLM）下達排課指令（中文自然語言），你必須**透過 `scripts/schedule_cli.py` 操作**，不可直接編輯 `data/schedule.yaml`。

### 寫入兩段式

所有寫入命令預設 **dry-run**（只算 diff、不寫檔）：

```bash
# 1. preview（看會發生什麼）
python scripts/schedule_cli.py --json add-schedule --class STU-04 --slot S5 --days mon,wed --start 2026-09-01 --weeks 8

# 2. 確認 ok=true、diff 合理後，加 --apply 真寫
python scripts/schedule_cli.py --json add-schedule --class STU-04 --slot S5 --days mon,wed --start 2026-09-01 --weeks 8 --apply
```

**`--apply` 沒加 = 不會改檔**。這是防呆核心。

### 標準工作流

1. **動手前**：`status --json` 看現況（總班數 / 堂數 / 衝突 / 孤兒 / 下週課表）
2. **新增/修改前**：先跑 dry-run（不加 `--apply`）確認 diff 跟你預期一致
3. **失敗時**：讀 `errors[].code`，按下表處理；不要憑空猜原因
4. **成功寫入後**：執行 `next_actions[]` 指示（通常是 render docs + commit）

### 命令清單

| 命令 | 用途 | 寫入？ |
|------|------|------|
| `status` | 健康摘要 + 本週課 + 衝突 + 孤兒 | 否 |
| `list-classes [--with-schedules]` | 列所有班 | 否 |
| `list-slots [--used-only]` | 列所有時段 | 否 |
| `list-conflicts` | 只列衝突 | 否 |
| `add-class --id --name --weekly-count [--level] [--note]` | 新增班 | 是 |
| `update-class --id [--name] [--weekly-count] [--level] [--note]` | 改班欄位 | 是 |
| `remove-class --id [--cascade]` | 刪班（cascade 連帶刪 schedule） | 是 |
| `add-schedule --class --slot --start --(day\|days\|specific-dates) [--weeks\|--end\|--lessons]` | 新增 schedule | 是 |
| `remove-schedule --class [--slot-id] [--day] [--all]` | 刪 schedule | 是 |

### 錯誤碼處理表

| code | 意思 | 你該做 |
|------|------|--------|
| `E_CLASS_NOT_FOUND` | class id 不存在 | 拼字確認；不存在就先 `add-class` |
| `E_SLOT_NOT_FOUND` | slot id 不存在 | `list-slots` 查可用 id（目前固定 S3..S10） |
| `E_DUPLICATE_ID` | 該 id 已存在 | 改用 `update-class` 或換一個 id |
| `E_DUPLICATE_SCHEDULE` | 完全相同的 schedule 重複 | 不需重加；改用 `update-schedule`（未來實作）或先 remove |
| `E_TIME_OVERLAP` | 兩堂課時段重疊（教練只有一人） | 看 `context.lesson_a/b`；改時段或挪其中一堂 |
| `E_WEEKLY_COUNT_EXCEEDED` | 排的堂數 > class.weekly_count | 先 `update-class --weekly-count` 拉高，或減 schedule |
| `E_INVALID_DATE_RANGE` | end_date <= start_date / time 反序 / 跨午夜 | 修日期或時段 |
| `E_PAST_DATE` / `W_PAST_DATE` | specific_date 早於 today-7d | 補登資料可保留（warning）；strict mode 拒絕 |
| `E_DATE_TOO_FAR` | 日期超過合理 horizon | 拆短週期，或檢查日期 |
| `E_NO_TERMINATION` | day/days 沒帶 duration_weeks/end_date/total_lessons | 加一個終止條件 |
| `E_AMBIGUOUS_TARGET` | remove 命中多條或孤兒問題 | 看 `context.matches` 縮條件，或加 `--all`/`--cascade` |
| `E_SCHEMA_INVALID` | 欄位格式錯 | 看 `context.path` 修對應欄位 |
| `E_VALIDATE_FAILED` | 寫入前 validate 整批失敗 | 看 errors[] 細項 |

### JSON envelope 規格

所有命令（含 errors）輸出固定結構：

```json
{
  "ok": true,
  "data": { ... },
  "errors": [{"code": "...", "msg": "...", "context": {...}}],
  "warnings": [{"code": "W_...", "msg": "...", "context": {...}}],
  "next_actions": ["下一步建議文字"]
}
```

退出碼：0 = ok（可含 warnings）/ 非 0 = errors。

### 不可做

- 直接編輯 `data/schedule.yaml`（CI 會擋）
- 跳過 dry-run 直接 `--apply` 而沒讀 diff
- 對 `vendor/`、`scripts/`、`docs/` 內檔做任何修改
- 任何 `git reset --hard` / `git clean -fd` / `git push --force`

### 失敗回退

```bash
# 寫壞了想還原（commit 前）
git restore data/schedule.yaml

# 已 commit 想 revert
git revert HEAD
```

### 範例：Hang 在 TG 說「STU-11 阿明 每週二四 早上 10:10 從 7/15 排 12 堂」

```bash
# 1. 看現況
python scripts/schedule_cli.py --json status

# 2. 加 class（dry-run）
python scripts/schedule_cli.py --json add-class --id STU-11 --name "阿明" --weekly-count 2

# 3. 確認 diff ok，--apply 寫入
python scripts/schedule_cli.py --json add-class --id STU-11 --name "阿明" --weekly-count 2 --apply

# 4. 加 schedule（dry-run；用 S4 = 10:10-11:10）
python scripts/schedule_cli.py --json add-schedule --class STU-11 --slot S4 --days tue,thu --start 2026-07-15 --lessons 12

# 5. 看 errors[]；若 E_TIME_OVERLAP 表示 S4 tue/thu 已被占，要挑別的時段或減
# 若 ok=true，加 --apply 寫入
python scripts/schedule_cli.py --json add-schedule --class STU-11 --slot S4 --days tue,thu --start 2026-07-15 --lessons 12 --apply

# 6. 更新 docs（按 next_actions 提示）
python scripts/render_html.py

# 7. commit
git add data/schedule.yaml docs/
git commit -m "feat: 加 STU-11 阿明 每週二四 12 堂"
```
