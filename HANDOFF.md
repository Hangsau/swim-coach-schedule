# HANDOFF — swim-coach-schedule

> 狀態快照（每次實質推進後更新）。行為規範見 `CLAUDE.md`，結構見 `MAP.md`。
> `updated: 2026-07-11`

## 現況

- schema v3；slots S3–S11、班級 STU-01 起約 12 個、schedules 由 CLI 維護；新增 optional 頂層 `makeups`（待補課帳本）
- CLI 19 個子命令（新增 end-class / undo / update-schedule / cancel-lesson --makeup / fulfill-makeup / list-makeups / cancel-makeup）；README 命令表已同步
- CI（build.yml）：push main → strict validate + pytest + rebuild docs（drift 時 bot auto-commit）→ pages.yml 部署
- 線上版：https://hangsau.github.io/swim-coach-schedule/

## 本次（2026-07-10）：桌面編輯 GUI

- 新增 `scripts/schedule_gui.py`：Tkinter 課表編輯器，可獨立跑、也可 `build_tab(parent)` 嵌入
- 常駐方式：嵌在 **桌面看板 hub**（`C:\claudehome\tools\deskboard\hub.py`，本機、不在任何 repo）分頁②；分頁① 是 religions-history 刊版
- 啟動入口：`C:\claudehome\tools\deskboard\桌面看板.bat`（**取代** religions-history 的 `狀態看板.bat` 作為日常入口；舊 bat 保留可單開刊版，未刪）
- 一鍵上線 flow：git fetch → pull --ff-only（接住 CI bot commit；分岔就紅字停下）→ render_html → git add data docs → commit → push
- split-schedule / .ics 匯出 / 月報表：**不在 GUI**，走 CLI 或 LLM
- e2e 驗收（2026-07-10）：測試班 GUI 上線→CI 綠→線上頁出現→整組移除→線上頁清除，全程通過；途中修掉兩個整合測試對 time-only schedule 的硬索引 bug（ff7ac7c），並把誤入版控的 __pycache__ 清出

### GUI v2（同日改版）：月曆為主視圖

- v1 的「7 個按鈕入口」被用戶判定難用（看不懂 UI 怎麼運作）→ 全檔重寫（850 行）：月曆為主視圖，直接點選操作
  - 點課 chip → 選單（取消／挪課／臨時加堂／改班級／刪排課／刪班級，全部預填該課的班級與日期）
  - 點空白日 → 幫既有班加一堂（預填日期）／新班級兩步精靈（中途放棄自動 rollback remove-class，防孤兒班）
  - 一天 >3 堂收「+N 堂…」popup；衝突日日期標紅；E_TIME_OVERLAP 錯誤人話化（撞到哪班哪時段）
  - level 欄位在 GUI 完全隱藏（介面不再出現「待確認」）
  - 唯讀展開用 `query.expand_schedule`（與線上頁同一套）；寫入仍全走 CLI subprocess
- 大宗程式碼發包 `claude-m3 -p`（MiniMax 月費，零 Anthropic 配額）產出，Claude 只做 spec／驗證／微修／審查；發包教訓：大檔需 `CLAUDE_CODE_MAX_OUTPUT_TOKENS=32000`，偶發靜默空回直接重試
- 已過 /code-audit（Step4 微修 3 處 + 審查自動修正 5 項）+ compile + headless smoke（35 格／175 堂／13 班）
- README GUI 章節已改寫為月曆操作說明
- v2 驗收六項全過（cancel roundtrip／撞課人話／精靈 rollback 無孤兒／無「待確認」／hub 迴歸／真實上線 e2e）；途中修掉 time-only schedule 硬索引第三處 `test_integration.py::test_duration_weeks_respected`（c4dcf55，同 ff7ac7c 類型）

### GUI v3（同日加版）：班級列表 + 日期小月曆

- 用戶追加兩需求：① 各班列表可點進去針對該班操作 ② 日期欄用小月曆點選；追加範圍：改班級要能改全部欄位（名稱／堂數／程度／備註）
- 實作（發包 claude-m3 in-place 編輯，850→1028 行）：
  - 頭列新增「班級 ▾」→ 班級列表 Toplevel（每班：ID／名稱／每週堂數／未來堂數），點一班開操作選單（修改資料全欄位預填現值／加一堂／新增排課／刪除排課／刪除班級）
  - 新 `MiniCal` class（stdlib calendar，無新依賴）：FormDialog `kind="date"` 欄位旁 📅 按鈕開小月曆，◀▶ 換月、點日回填 YYYY-MM-DD；grab 巢狀處理（關閉還 grab 給表單）
  - 8 個日期欄位全換 date kind（加堂／取消／挪課×2／排課 start+end／精靈 start+end）；`--specific-dates` 逗號多值保持 entry
  - `_fields_update_class` 改 `class_rec` 簽名全欄位預填（含 level——月曆仍隱藏 level，僅編輯表單可改）
- 發包實錄：第 1 發靜默空回（exit 0 無輸出無改動，v2 同型故障），第 2 發成功；驗證：py_compile + 全 diff 對照 spec + smoke（13 班 175 堂／MiniCal 換月選日／班級列表 13 列／prefill）+ update-class dry-run roundtrip

## 本次（2026-07-11）：W1–W5 易用性五連發

用戶三需求：① 新增班級要自動生成代號 ② 結束班級但保留已上堂次 ③ 整體易用性盤點。

- **W1 自動編號**：`add-class` 的 `--id` 改 optional，`next_class_id()` 掃現有 STU-NN 給下一號；GUI 新班精靈拿掉 ID 欄，改讀回傳 `added_class.id`（c626761）
- **W2 end-class 子命令**：`end-class --class X --from DATE` 保留 from 之前堂次、移除之後；day 模式設 `end_date`（= from−1）+ 移除 duration_weeks/total_lessons；specific_dates 過濾；kept==0 整條排課移除、班級無排課時連 class 記錄一併移除（過 strict E_ORPHAN_CLASS）；`tests/test_end_class.py` 5 情境（ffbf97c）
- **W3+W4 GUI 入口**：課 chip 選單與班級選單都有「結束此班（保留已上堂次）…」（chip 版預填該堂日期）與「這班從某天起換時段…」（split-schedule 表單）；ConfirmDialog 顯示「保留 N 堂／移除 M 堂」摘要（1c43c5e）
- **W5 undo**：`atomic_write` 寫檔前先備份到 `<yaml所在>/.backup/`（保留 10 份）；`undo` 子命令還原最新備份，undo 兩次＝redo；GUI 更多選單加「復原上一步」；`.gitignore` 加 `data/.backup/`（1c43c5e）
- 已過 /code-audit（自動修 4 項：`_backup_dir`/`BACKUP_GLOB` 抽共用、walrus 免二次 parse、命名兩處；保留 1 項不改）+ 51 tests pass + headless smoke
- **事故記錄**：發包 claude-m3 期間 m3 對真實 `data/schedule.yaml` 跑了 `--apply` 測試（SCH-014 加 except_date、新增 SCH-019）→ 已 `git checkout` 還原並 strict validate 確認；m3 中途配額耗盡，W3+W4 改派 Sonnet sub-agent 完成

## 本次（2026-07-11）：W6–W8 排課詳情與就地修改

用戶兩需求：① 每班可看排課狀態（日期、已上/未來堂數）並直接從班級進入修改 ② 起始日填錯要能就地改（先前只能刪掉重排）。

- **W6 `update-schedule` 子命令**：`--schedule-id` 直接鎖定（或 `--class` 單條自動鎖、多條回 `E_AMBIGUOUS_TARGET` + candidates 清單）；可改 start/end/weeks/lessons/day/days/slot/time/note 任意欄位，三擇一欄位（weeks/end/lessons）設一個自動清掉另兩個；dry-run 回報 `lessons_before/after`、`first_lesson/last_lesson`、**`past_lessons_lost`**（改日期會弄丟幾堂已上過的課，防呆核心）。`remove-schedule` 同步支援 `--schedule-id` 指定刪除單條
- **W7 GUI 班級詳情面板**：班級列表點一班 → 詳情視窗（每條排課一列：星期／時段／起始日／總堂數／已上 N・未來 M／近 3 堂日期），點排課列開選單（修改此排課＝update-schedule 全欄位預填／從某天起換時段／刪除此排課），底部班級級操作（修改資料／加一堂／新增排課／結束此班／刪除班級）；開表單前先關詳情視窗（Toplevel 不受 refresh 重繪、防 grab 衝突）
- **W8 ConfirmDialog 改動摘要**：apply 確認框顯示「改動後共 N 堂（原 M 堂），第一堂／最後一堂日期」；`past_lessons_lost > 0` 時紅字警告「有 N 堂已上過的課會因此消失」
- W6/W7 平行發包兩個 Sonnet sub-agent（1 agent = 1 檔避免衝突；spec 內含介面契約讓 GUI 先於 CLI 寫）；W8 + 視窗生命週期修正手動完成
- `tests/test_update_schedule.py` 9 情境；全套 60 tests pass
- 已過 /code-audit：自動修 4 項——**修掉一個 crash bug**（`--days mon,banana` 在 preview 展開前未驗星期值 → 裸 ValueError 無 envelope；現回 `E_SCHEMA_INVALID` + allowed 清單）、`NEAR_LESSONS_SHOWN` 常數、`_close_then` 統一、`cands`→`candidates`；保留 3 項不改（changed_fields 逐欄特例／run_cli 測試 helper 重複需 conftest.py 超範圍／E_SCHEDULE_NOT_FOUND emit 為 house style）

## 本次（2026-07-11）：W9 待補課帳本（makeups）

用戶真實痛點：星期六精緻班有兩天確定不能上、還沒排補課；怕自己忘記欠了幾堂。「補課全靠我自己記 我要這個行事曆幹嘛」——行事曆必須替他記住欠補，不是叫他手動追蹤。

- **資料模型**：新增 optional 頂層 `makeups` list，每筆 `{id: MU-NNN, class_id, origin_date, origin_schedule_id, reason, status: pending|fulfilled, makeup_date, makeup_schedule_id}`；additive，不動既有 schema，strict CI 不受影響
- **CLI（+4 命令，共 19）**：
  - `cancel-lesson --makeup`：取消那堂（原課進 except_dates）+ 登記一筆 pending 欠補，回 next_action 提示 fulfill 指令
  - `fulfill-makeup --makeup-id --date (--slot|--time) [--note]`：新增補課那堂（time-only specific_dates）+ 該筆標記 fulfilled 連到新 SCH；防呆 E_MAKEUP_NOT_FOUND / E_MAKEUP_ALREADY_FULFILLED / E_SLOT_NOT_FOUND
  - `list-makeups [--class] [--status]`（唯讀，預設 pending）、`cancel-makeup --makeup-id`（撤銷登記）
- **validate**：新增 `validate_makeups`（list 結構、class_id 存在、status 合法、origin_date 可解析、fulfilled 需 makeup_date、MU id 唯一）；stats 加 `makeups_pending`
- **修掉一個潛藏 crash bug**（回歸測試已固化）：`validate_cross` 的 E_TIME_OVERLAP 排序 key 在 `slot_id=None`（time-only 補課）與有 slot 的課混排時 `None < str` 裸 crash → 改 None-safe key，撞課現在乾淨回 `E_TIME_OVERLAP`
- **GUI 班級詳情面板改版**（回應「你只給最近的、沒辦法直接改點出來的日期」）：
  - 班級列表欠補時整列紅字「⚠ 欠補 N 堂」
  - 詳情頂端紅字「⚠ 還欠 N 堂補課」+ 每筆待補課列（補課… / 撤銷）
  - 每條排課列出**未來每一堂**日期 chip（每列 8 個換行），點日期 → 單堂選單：取消不補／取消並登記待補／挪到別天／只改這堂時間；已取消未來日期灰字標「已取消」
  - 移除舊「近 3 堂」常數，改鋪全部
- `tests/test_makeup.py` 8 情境（含 None-slot overlap 回歸）；全套 68 tests pass；headless GUI smoke（詳情面板 + 待補課 + 班級列表）通過
- 已過 /code-audit

## 已知事項 / 待辦

- level 欄位多為「待確認」——資料債，非程式問題（GUI 已隱藏此欄，僅 YAML / CLI 可見）
