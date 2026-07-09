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
- GUI 動作 7 入口：加/改/刪班級、加/刪排課、單堂調整（挪課/取消/臨時加課三合一 dialog）、一鍵上線
- 一鍵上線 flow：git fetch → pull --ff-only（接住 CI bot commit；分岔就紅字停下）→ render_html → git add data docs → commit → push
- split-schedule / .ics 匯出 / 月報表：**不在 GUI**，走 CLI 或 LLM
- 已過 /code-audit（12 項自動修正：死碼、常數化、thread 例外防 _loading 卡死等）+ compile + hub smoke test
- e2e 驗收（2026-07-10）：測試班 GUI 上線→CI 綠→線上頁出現→整組移除→線上頁清除，全程通過；途中修掉兩個整合測試對 time-only schedule 的硬索引 bug（ff7ac7c），並把誤入版控的 __pycache__ 清出
- README 已加「圖形介面（GUI）」章節 + 補命令表缺漏兩列

## 已知事項 / 待辦

- level 欄位多為「待確認」——資料債，非程式問題
