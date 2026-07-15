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

## 圖形介面（GUI）

不想打指令的話，用 Tkinter 編輯器（Windows）：

```bat
set PYTHONIOENCODING=utf-8
pythonw scripts\schedule_gui.py
```

- **月曆是主視圖**（與線上頁同一套展開邏輯）：
  - **點一堂課** → 選單：取消這天這堂／挪到別天換時段／這班臨時加一堂／修改班級／刪除排課／刪除班級
  - **點空白日** → 選單：幫既有班在這天加一堂／建立全新班級（兩步精靈，中途放棄自動撤銷、不留孤兒班）
  - 一天超過 3 堂收成「+N 堂…」，點開列全滿再選
  - 衝突日日期數字標紅；撞課錯誤訊息會寫成人話（撞到哪班、哪個時段）
- **頭列「班級 ▾」** → 班級列表（每班顯示每週堂數與未來堂數；還欠補課時整列紅字標「⚠ 欠補 N 堂」），點一班 → **班級詳情面板**：
  - 頂端紅字「⚠ 還欠 N 堂補課」，每筆待補課一列，附「補課…」（開 fulfill-makeup 表單）與「撤銷」
  - 每條排課列出**未來每一堂**的日期 chip（不再只給最近 3 堂），點一個日期 → 單堂選單：取消這堂（不補）／取消並登記待補（之後補）／挪到別天／只改這堂時間；已取消的未來日期以灰字標「已取消」
  - 點排課摘要列 → 改排課／換時段／刪除；底部班級級操作：修改資料／加一堂／新增排課／結束此班／刪除班級
- 表單內所有日期欄位旁都有 **📅 小月曆**，點日期直接填入，不用手打
- 每個動作都是「表單 → dry-run 看 diff → 確認才寫入」，底層全部走 `schedule_cli.py`，不直接碰 YAML
- **一鍵上線** = `git pull --ff-only` → 重建頁面 → commit → push，一兩分鐘後線上行事曆更新
- `split-schedule` 等進階操作不在 GUI，走下方 CLI 協議
- GUI 也可作為分頁嵌入其他 Tkinter host：`from schedule_gui import build_tab; build_tab(parent_frame)`

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
| `add-class --name --weekly-count [--id] [--level] [--note]` | 新增班（id 省略自動編 STU-NN） | 是 |
| `update-class --id [--name] [--weekly-count] [--level] [--note]` | 改班欄位 | 是 |
| `remove-class --id [--cascade]` | 刪班（cascade 連帶刪 schedule） | 是 |
| `end-class --class --from` | 結束班級：from（含）之後堂次移除、之前保留；全無保留堂次時班級一併移除 | 是 |
| `undo` | 復原上一次寫入（`data/.backup/` 最新備份；連按兩次 = 還原回去） | 是 |
| `add-schedule --class (--slot\|--time) --start --(day\|days\|specific-dates) [--weeks\|--end\|--lessons] [--note]` | 新增 schedule | 是 |
| `update-schedule (--schedule-id\|--class) [--start] [--end\|--weeks\|--lessons] [--day\|--days] [--slot\|--time] [--note]` | 就地改一條 schedule 的任意欄位（起始日填錯的救援路徑）；dry-run 回報前後堂數與 `past_lessons_lost` | 是 |
| `remove-schedule (--schedule-id \| --class [--slot-id] [--day] [--all])` | 刪 schedule（`--schedule-id` 直刪指定條） | 是 |
| `move-lesson --class --from-date --to-date [--to-slot\|--to-time] [--note]` | 挪一堂課（補課） | 是 |
| `cancel-lesson --class --date [--reason] [--makeup]` | 取消一堂；加 `--makeup` 登記為待補課（欠補），補課日決定後用 `fulfill-makeup` 銷帳 | 是 |
| `fulfill-makeup --makeup-id --date (--slot\|--time) [--note]` | 銷帳一筆待補課：新增補課那堂（specific_dates）並把該筆標記 fulfilled | 是 |
| `list-makeups [--class] [--status pending\|fulfilled\|all]` | 列待補課（預設只列 pending） | 否 |
| `cancel-makeup --makeup-id` | 撤銷一筆待補課登記（不再欠補；不影響已取消的原課） | 是 |
| `add-lesson --class --date (--slot\|--time) [--note]` | 臨時加一堂（單日） | 是 |
| `split-schedule (--class\|--schedule-id) --at --(day\|days) [--to-slot\|--to-time] (--weeks\|--end\|--lessons) [--note]` | 把某 schedule 在某日切兩半，過去不動、未來改 | 是 |

### 待補課帳本（makeups）

某天不能上課但之後要補時，用「取消 → 登記欠補 → 補課銷帳」三步，讓行事曆替你記住還欠幾堂：

1. `cancel-lesson --class C --date D --makeup --apply` — 取消那堂並登記 `MU-NNN`（status=pending），原課進 `except_dates`
2. 補課日決定後 `fulfill-makeup --makeup-id MU-NNN --date 新日期 --slot S --apply` — 新增補課那堂、把 `MU-NNN` 標記 fulfilled 並連到新 schedule
3. 隨時 `list-makeups` 看還欠哪幾堂；登記錯了用 `cancel-makeup --makeup-id MU-NNN` 撤銷

`makeups` 是 optional 頂層 list，每筆 `{id, class_id, origin_date, origin_schedule_id, reason, status, makeup_date, makeup_schedule_id}`。GUI 班級詳情面板會用紅字顯示「還欠 N 堂補課」並提供一鍵補課。

### 錯誤碼處理表

| code | 意思 | 你該做 |
|------|------|--------|
| `E_CLASS_NOT_FOUND` | class id 不存在 | 拼字確認；不存在就先 `add-class` |
| `E_SLOT_NOT_FOUND` | slot id 不存在 | `list-slots` 查可用 id；或改用 `--time HH:MM-HH:MM` 直接寫 |
| `E_DUPLICATE_ID` | 該 id 已存在 | 改用 `update-class` 或換一個 id |
| `E_DUPLICATE_SCHEDULE` | 完全相同的 schedule 重複 | 不需重加；改用 `update-schedule` 或先 remove |
| `E_SCHEDULE_NOT_FOUND` | schedule id 不存在（或該班沒有 schedule） | 看 `context.available`；`list-classes --with-schedules` 查 id |
| `E_TIME_OVERLAP` | 兩堂課時段重疊（教練只有一人） | 看 `context.lesson_a/b`；改時段或挪其中一堂 |
| `E_WEEKLY_COUNT_EXCEEDED` | 排的堂數 > class.weekly_count | 先 `update-class --weekly-count` 拉高，或減 schedule |
| `E_INVALID_DATE_RANGE` | end_date <= start_date / time 反序 / 跨午夜 | 修日期或時段 |
| `E_DATE_TOO_FAR` | 日期超過合理 horizon | 拆短週期，或檢查日期 |
| `E_NO_TERMINATION` | day/days 沒帶 duration_weeks/end_date/total_lessons | 加一個終止條件 |
| `E_AMBIGUOUS_TARGET` | 命中多條 schedule（remove / update / split） | 看 `context.matches` 或 `context.candidates`，加 `--schedule-id` 指定，或 `--all`/`--cascade` |
| `E_SCHEMA_INVALID` | 欄位格式錯 | 看 `context.path` 修對應欄位 |
| `E_MAKEUP_NOT_FOUND` | 待補課 id 不存在（fulfill / cancel-makeup） | 看 `context.available`；`list-makeups` 查 id |
| `E_MAKEUP_ALREADY_FULFILLED` | 該待補課已補過 | 已銷帳，不需再補；`list-makeups --status all` 查狀態 |
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

### slot 別名 vs 直接 --time

**slot 別名（S3-S10 等）= 當下季常用時段的快速命名**，方便你打字省略；不是永久 schema。
- 排當下常用時段：`--slot S3`（系統會自動把對應的時段時間凍結進 schedule）
- 排不在常用清單的時段：`--time "15:00-16:00"`（直接寫，不用發明 slot id）
- 每條 schedule 內部都有 `time` 欄位凍結時段值；之後 Hang 改 `slots[].time` 不會打到過去的 schedule

### 補課 / 挪課（單堂）

「乖乖 7/8 那堂颱風停課，改到 7/11 週六補」：

```bash
python scripts/schedule_cli.py --json move-lesson \
  --class STU-04 --from-date 2026-07-08 --to-date 2026-07-11 \
  --note "颱風停課改週六補" --apply
```

行為：原 schedule 自動加 `except_dates: [2026-07-08]`（跳過該日且不算進 total_lessons）+ 新增一條 specific_dates 補課條目到 7/11。要改時段一起改用 `--to-time "下午時段"` 或 `--to-slot S5`。

### 換時段不動過去（中段切換）

「英特兒 STU-05 開學後 9/1 起，從每週一四 改成每週二四 16 週」：

```bash
python scripts/schedule_cli.py --json split-schedule \
  --class STU-05 --at 2026-09-01 --days tue,thu --weeks 16 \
  --note "開學換時段" --apply
```

行為：原 schedule 被截到 8/31（前段 = 暑假紀錄不動）；新建後段從 9/1 開始 days=tue/thu 跑 16 週。**過去歷史完整保留**。

可選參數：
- 後段換時段：加 `--to-slot S5` 或 `--to-time "13:00-14:00"`
- 後段終止：`--weeks N` / `--end YYYY-MM-DD` / `--lessons N`（**必須選一個**，不然 fail）
- 該 class 有多條 day/days schedule 時必須用 `--schedule-id SCH-XXX` 明指

### 過渡週的 weekly_count 衝突

split-schedule 過渡週可能會多一堂課（前段結尾 + 後段開頭重疊到同一週），引發 `E_WEEKLY_COUNT_EXCEEDED`。處理方式：
1. 先 `update-class --weekly-count` 暫時拉高，過渡週後再改回
2. 或挑下一個週一作為 `--at`，避免跨週

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
