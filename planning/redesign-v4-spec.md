# v4 規格書：明確課次清單模型（廢除 except_dates）

> 2026-07-22。經 /plan-check + 用戶簡化確認。派工 agent 以本檔為唯一契約。
> 核心：**課表存的就是一堂一堂的明確日期。刪掉就是刪掉。退課＝刪 + 欠補 +1。補課＝加一堂 + 銷帳。加課隨時可加。**
> 無狀態機、無還原、無雙向連結、無 except_dates、無每週 pattern 展開。

## 0. 事故背景（為什麼要改）

- v3 用「pattern（day/days/specific_dates）＋ except_dates 負面清單」在讀取時展開。
- 2026-07-21 事故：GUI「挪課」實作＝對來源 schedule 加 except_date ＋ 新增目的 schedule。對單日 specific_dates 排程挪課會產生「specific ∩ except 自我抵消的空殼」（SCH-022），展開零堂 → CI 紅。
- v4 之後這類矛盾**結構上不可能**：挪課就是改那筆 lesson 的日期/時間。

## 1. Schema v4（`schema_version: 4`）

`slots`、`classes` 兩段**完全不變**。

### schedules（降級為分組 metadata，不參與展開）

```yaml
schedules:
- id: SCH-019          # 沿用現有 ID
  class_id: STU-14
  slot_id: S10         # optional（time-only 排程無）
  time: 20:10-21:10    # 凍結顯示值（沿用 v3 語意）
  label: 週二四        # optional，遷移時由 pattern 生成（day/days→中文；specific_dates→「指定日期」）
  note: ...            # optional
```

**移除欄位**（遷移時丟棄）：`day` `days` `start_date` `end_date` `duration_weeks` `total_lessons` `specific_dates` `except_dates`。

### lessons（新頂層 key，唯一課次真相）

```yaml
lessons:
- id: L-0001           # L-NNNN 四位流水號，全域唯一，遷移按日期序編
  schedule_id: SCH-019 # optional；standalone（臨時加堂/補課）可無
  class_id: STU-14     # 必填（denormalize，免 join）
  date: '2026-07-14'   # 必填 YYYY-MM-DD
  time: 20:10-21:10    # 必填 HH:MM-HH:MM
  slot_id: S10         # optional
  note: ...            # optional
```

### makeups（概念沿用，一個欄位改名）

`makeup_schedule_id` → **`makeup_lesson_id`**（指向補課那筆 lesson 的 L-id）。
`origin_schedule_id` 保留為歷史參考，validate **不**檢查其存在性。
其餘欄位（id/class_id/origin_date/reason/status/makeup_date）與 pending/fulfilled 語意不變。

## 2. 展開層契約（最重要的簡化）

`query.expand_schedule(schedules, slots_by_id, classes_by_id)` **簽名與回傳形狀完全不變**（回傳 lesson dict list，含 class_id/slot_id/time/date 等現有鍵）。實作改為：直接讀頂層 `lessons` 排序回傳（函式需可取得 lessons——允許把 `load()` 回傳的 data 傳入或加參數，但**回傳形狀不可變**）。
→ `render_html.py` 與 GUI 唯讀路徑預期近零修改。`query._except_set`（query.py:48）刪除。

## 3. validate.py v4 規則

**刪除**：except_dates 全部規則、pattern 展開驗證、`E_WEEKLY_COUNT_EXCEEDED`（用戶模型：加課/補課隨時可加，weekly_count 降為純顯示 metadata）。
**保留**：slots/classes 結構、ID 唯一性、E_TIME_OVERLAP（改為 lessons 兩兩比對，None-safe 排序沿用）、E_ORPHAN_CLASS（改義：class 無任何 lesson 且無 schedule 才算孤兒）、makeups 驗證（fulfilled 需 makeup_date；新增：fulfilled 需 makeup_lesson_id 且該 L-id 存在）。
**新增**：lessons 結構驗證（必填欄位、日期/時間格式、L-id 唯一、class_id 存在、schedule_id 若有必須存在）；`schema_version != 4` → 明確錯誤「請跑 scripts/migrate_v4.py」。

## 4. migrate_v4.py（一次性，冪等）

1. `schema_version == 4` → print「已是 v4」exit 0（不動作）。
2. **內嵌 v3 展開器凍結副本**（從現有 query.py 複製 `_except_set`+`expand_schedule`，不 import——遷移後 query.py 已是新版）。
3. 每條 v3 schedule 展開 → lessons（帶 schedule_id）；schedule 保留 id/class_id/slot_id/time/note + 生成 label；丟棄 pattern 欄位。
4. makeups：每筆 fulfilled 的 makeup_schedule_id X → 找 schedule X 展開出的那筆 lesson（單日 specific_dates 必唯一）→ 寫 makeup_lesson_id；欄位改名。
5. **等價自檢**：v3 展開全集 vs 新 lessons 全集，按 `(class_id, date, time)` 逐堂 diff，**非空即 abort 不寫檔**並列出差異。
6. 寫檔前備份 `data/schedule.pre-v4.yaml`；輸出統計（班數/排程數/堂數）。

## 5. CLI 19 命令對映（介面凍結：命令名、旗標、envelope 頂層鍵不變；內部語意如下）

| 命令 | v4 語意 |
|------|---------|
| status / list-classes / list-slots / list-conflicts | 讀 lessons 統計，輸出格式不變 |
| add-class / update-class / remove-class | 不變；remove-class cascade＝刪其 schedules+lessons+相關 makeups 檢查沿用 |
| add-schedule | pattern 旗標照收 → **當場展開成 N 筆 lessons** + 1 筆 schedule metadata；dry-run 預覽列全部日期 |
| add-lesson | 直接加一筆 standalone lesson（不再建 specific_dates schedule） |
| cancel-lesson | **刪除該 (class, date) 的 lesson**；`--makeup` 同時登記欠補；找不到 → 新錯誤碼 `E_LESSON_NOT_FOUND` |
| move-lesson | **原地改該筆 lesson 的 date/slot/time**（不刪不增，無空殼） |
| fulfill-makeup | 加一筆 standalone lesson + 該筆 makeup 標 fulfilled + 寫 makeup_lesson_id |
| list-makeups / cancel-makeup | 不變 |
| update-schedule | 保留 `date < 執行日` 的 lessons；`>=` 的刪除，依新 pattern 參數重展開加入；`past_lessons_lost` 防呆沿用 |
| split-schedule | 同上機制：from 起的未來 lessons 刪除，依新時段重生 |
| end-class | 刪 from 起的 lessons；schedule 無剩餘 lessons → 連 schedule 刪；class 全空 → 連 class 刪（沿用 W2 語意） |
| remove-schedule | 刪 schedule + 其全部 lessons |
| undo | 不變（.backup/ 機制照舊） |

錯誤碼：新增 `E_LESSON_NOT_FOUND`；移除 `E_WEEKLY_COUNT_EXCEEDED`；其餘全部沿用。
兩段式 dry-run→apply、atomic_write、備份 10 份：全部沿用不動。

## 6. GUI 對映（thin client 不變，subprocess 呼叫 CLI）

- 課 chip「取消不補／取消並登記待補」→ cancel-lesson（±--makeup）；「挪到別天」→ move-lesson（原地改）
- 移除 GUI 內對 except_dates 的一切認知（「已取消」灰字判斷來源改為「該日無 lesson」＝不顯示）
- 欠補紅字帳、班級詳情、MiniCal、一鍵上線：全部不變

## 7. 分散點清零（驗收 `grep -rn except_dates scripts/ tests/ README.md` = 0）

| 檔 | 已知位置 |
|----|---------|
| query.py | `_except_set`(48)、expand_schedule 內引用 |
| schedule_cli.py | cancel-lesson/move-lesson 寫入路徑、preview 展開 |
| validate.py | except 相關規則 |
| schedule_gui.py | 取消/挪課選單路徑 |
| render_html.py | 若經 query 則無直接引用（確認後即可） |
| tests/ | 多檔 fixture 與斷言 |
| README.md | LLM 協議、命令表、錯誤碼表 |

## 8. 測試要求（≥ 現有 69，新增必含）

- 遷移等價：v3 fixture → migrate → 舊展開 == lessons 零差異；冪等重跑
- 欠補 roundtrip：cancel-lesson --makeup → list-makeups +1 → fulfill → −1
- move-lesson 無空殼：挪課後 grep 無自我抵消結構、原日消失新日出現
- 邊界：畸形日期/未知欄位/不存在 class/E_LESSON_NOT_FOUND → 乾淨 envelope
- E_TIME_OVERLAP lessons 版（含 time-only None slot 回歸）
