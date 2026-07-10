# HANDOFF — swim-coach-schedule

> 狀態快照（每次實質推進後更新）。行為規範見 `CLAUDE.md`，結構見 `MAP.md`。
> `updated: 2026-07-10`

## 現況

- schema v3；slots S3–S11、班級 STU-01 起約 10 個、schedules 由 CLI 維護
- CLI 13 個子命令；README 命令表已含全部 13 個（含 cancel-lesson / add-lesson）
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

## 已知事項 / 待辦

- level 欄位多為「待確認」——資料債，非程式問題（GUI 已隱藏此欄，僅 YAML / CLI 可見）
