# MAP — swim-coach-schedule（游泳教練課表）

> 結構地圖，給冷啟動讀者（人/LLM）。格式見 `C:\claudehome\CODEBASE_MAP_METHODOLOGY.md`。
> 行為規範見 `CLAUDE.md`；進度/待辦見 `HANDOFF.md`。
>
> `last_verified: 2026-07-11`

---

## 1. 一句話定位 + 技術棧

**游泳教練課表**：`data/schedule.yaml`（單一真實來源）→ CLI 增刪改（dry-run→apply 兩段式、JSON envelope）→ `render_html.py` 產靜態頁 → GitHub Pages。
Python 3.10+ / PyYAML / Tkinter（GUI）。無其他依賴。

---

## 2. 「要做 X → 去讀 Y」決策索引

| 你要做的事 | 動這裡 |
|-----------|--------|
| 增刪改班級 / 排課（程式路徑） | `scripts/schedule_cli.py`（927 行，15 子命令） |
| 用滑鼠增刪改 + 一鍵 push | `scripts/schedule_gui.py`（Tkinter 月曆主視圖；嵌桌面看板 hub） |
| 唯讀查課表 | `scripts/query.py`（today/week/month/day/class/slot） |
| 改驗證規則（衝突 / 堂數 / 日期） | `scripts/validate.py`（510 行） |
| 改行事曆頁面長相 | `scripts/render_html.py`（940 行 → `docs/`） |
| 改 CI / Pages 部署 | `.github/workflows/build.yml`、`pages.yml` |
| 查 CLI 錯誤碼含義 | `README.md` 錯誤碼處理表 |

---

## 3. 檔案地圖

| 檔 | 行 | 職責 | 依賴 |
|----|----|------|------|
| `data/schedule.yaml` | ~250 | 唯一資料來源：slots / classes / schedules（schema v3） | — |
| `scripts/schedule_cli.py` | 927 | 全部寫入路徑；envelope + preview_diff + atomic_write（寫前備份至 `.backup/` 供 undo）；寫前 inline validate | validate.py |
| `scripts/validate.py` | 510 | schema + 衝突 + 堂數 + 日期驗證；`--strict` 供 CI | pyyaml |
| `scripts/query.py` | 248 | 唯讀展開 schedules → 具體日期堂次 | pyyaml |
| `scripts/render_html.py` | 940 | 產 `docs/`：月曆 / grid / summary / index | query.py |
| `scripts/schedule_gui.py` | ~1090 | Tkinter 月曆主視圖編輯器（點課/點日直接操作、班級列表面板、日期欄 MiniCal 小月曆、新班精靈防孤兒 rollback、結束班級/換時段/undo 入口）；寫入 thin client 全走 CLI subprocess，唯讀展開 import query.py；`build_tab(parent)` 可嵌入 | schedule_cli.py（subprocess）、query.py |
| `tests/` | 4 檔 | CLI smoke / integration / validate / end-class 測試（CI 跑） | pytest |

**產物**：`docs/`（render_html 輸出，勿手改）。

---

## 4. 踩雷點 / 非顯而易見處

1. **不可手改 `data/schedule.yaml`**：跳過 validate + diff 防呆；CI strict 會紅。一律走 CLI。
2. **schedule 內 `time` 欄位是凍結值**：改 `slots[].time` 不影響既有 schedule（設計如此，防止改 slot 打到歷史）。
3. **CI auto-commit**：push 後若 docs drift，bot 會補一個 commit → 本地隨即落後 remote；下次 push 前必 `fetch` + `pull --ff-only`（GUI 一鍵上線已內建）。drift 常見成因：render 的「更新時間 / today 高亮」依執行當地日期，本機（UTC+8）與 CI（UTC）跨日時必 drift，屬正常現象。
4. **cp950**：所有 script 輸出含中文，subprocess / console 必 `PYTHONIOENCODING=utf-8`，GUI 內部 subprocess helper 已強制。
5. **README 命令表 vs CLI**：2026-07-11 已同步 15 命令（含 end-class / undo）；日後加子命令記得同步，權威來源是 `build_parser()`（schedule_cli.py:795）。
6. **undo 備份目錄跟著 yaml 走**：`_backup_dir()` = `<yaml 檔所在>/.backup/`（保留 10 份），所以測試用 tmp 檔不會污染 `data/.backup/`；`data/.backup/` 已入 `.gitignore`。
