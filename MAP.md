# MAP — swim-coach-schedule（游泳教練課表）

> 結構地圖，給冷啟動讀者（人/LLM）。格式見 `C:\claudehome\CODEBASE_MAP_METHODOLOGY.md`。
> 行為規範見 `CLAUDE.md`；進度/待辦見 `HANDOFF.md`。
>
> `last_verified: 2026-07-22`（schema v4 明確課次模型）

---

## 1. 一句話定位 + 技術棧

**游泳教練課表**：`data/schedule.yaml`（單一真實來源）→ CLI 增刪改（dry-run→apply 兩段式、JSON envelope）→ `render_html.py` 產靜態頁 → GitHub Pages。
Python 3.10+ / PyYAML / Tkinter（GUI）。無其他依賴。

---

## 2. 「要做 X → 去讀 Y」決策索引

| 你要做的事 | 動這裡 |
|-----------|--------|
| 增刪改班級 / 排課（程式路徑） | `scripts/schedule_cli.py`（1643 行，19 子命令；全部寫入 `lessons`；停課補課走 cancel-lesson --makeup → fulfill-makeup） |
| 用滑鼠增刪改 + 一鍵 push | `scripts/schedule_gui.py`（Tkinter 月曆主視圖；嵌桌面看板 hub） |
| 唯讀查課表 | `scripts/query.py`（today/week/month/day/class/slot） |
| 改驗證規則（課次 / 衝突 / 日期 / 待補課） | `scripts/validate.py`（472 行） |
| 改行事曆頁面長相 | `scripts/render_html.py`（940 行 → `docs/`） |
| 改 CI / Pages 部署 | `.github/workflows/build.yml`、`pages.yml` |
| 查 CLI 錯誤碼含義 | `README.md` 錯誤碼處理表 |

---

## 3. 檔案地圖

| 檔 | 行 | 職責 | 依賴 |
|----|----|------|------|
| `data/schedule.yaml` | ~1300 | schema v4 唯一資料來源：slots / classes / schedules metadata / lessons / makeups | — |
| `scripts/migrate_v4.py` | 377 | v3 pattern 凍結展開器；等價自檢、makeup 連結轉換、備份、原子遷移；v4 重跑冪等 | pyyaml |
| `scripts/schedule_cli.py` | 1643 | 19 個命令的全部寫入路徑；dry-run/envelope/atomic_write；直接增刪改 lessons；makeups pending/fulfilled 銷帳與回復 | validate.py, query.py |
| `scripts/validate.py` | 472 | schema v4、lesson 欄位／引用、時段重疊、makeups 驗證；`--strict` 供 CI | pyyaml |
| `scripts/query.py` | 180 | 直接讀頂層 lessons，轉成 render/GUI 相容形狀；不再展開 pattern | pyyaml |
| `scripts/render_html.py` | 940 | 產 `docs/`：月曆 / grid / summary / index | query.py |
| `scripts/schedule_gui.py` | 1478 | Tkinter 月曆 thin client；顯示實際 lessons、standalone lessons 與欠補帳；全部寫入走 CLI subprocess | schedule_cli.py, query.py |
| `tests/` | 9 檔 / 106 tests | CLI、integration、validate、migration、render、end/update/makeup 系統測試 | pytest |

**產物**：`docs/`（render_html 輸出，勿手改）。

---

## 4. 踩雷點 / 非顯而易見處

1. **不可手改 `data/schedule.yaml`**：跳過 validate + diff 防呆；CI strict 會紅。一律走 CLI。
2. **lesson 的 `time` 是課次真相**：改 `slots[].time` 不會打到既有 lessons；schedule.time 只是分組顯示／未來重排的 metadata。
3. **CI auto-commit**：push 後若 docs drift，bot 會補一個 commit → 本地隨即落後 remote；下次 push 前必 `fetch` + `pull --ff-only`（GUI 一鍵上線已內建）。drift 常見成因：render 的「更新時間 / today 高亮」依執行當地日期，本機（UTC+8）與 CI（UTC）跨日時必 drift，屬正常現象。
4. **cp950**：所有 script 輸出含中文，subprocess / console 必 `PYTHONIOENCODING=utf-8`，GUI 內部 subprocess helper 已強制。
5. **README 命令表 vs CLI**：2026-07-22 已同步 19 命令；日後加子命令記得同步，權威來源是 `build_parser()`。
6. **undo 備份目錄跟著 yaml 走**：`_backup_dir()` = `<yaml 檔所在>/.backup/`（保留 10 份），所以測試用 tmp 檔不會污染 `data/.backup/`；`data/.backup/` 已入 `.gitignore`。
7. **makeup 連結指向 lesson**：fulfilled 必須有 `makeup_lesson_id`；補課堂被刪時必須經 `_revert_makeups_for_removed_lessons` 回 pending。time-only lesson 的 `slot_id=None` 合法，重疊排序必須維持 None-safe。
8. **render 必須傳同一份 data**：`render_html.expand_schedule(..., data)` 要把呼叫端 data 傳給 query；不能偷偷重讀預設檔，回歸測試在 `tests/test_render_html.py`。
9. **v3 相容字面只留 migration**：舊 pattern／負面日期只允許存在 `migrate_v4.py` 的凍結轉換器；runtime、tests、README 不得重新引入。
