# swim-coach-schedule — 工作守則

> 行為規範（很少改）。狀態快照見 `HANDOFF.md`，結構導航見 `MAP.md`，LLM 排課操作協議見 `README.md`。

## 一句話

游泳教練課表：一個 YAML（`data/schedule.yaml`）+ 一個 CLI（`scripts/schedule_cli.py`）+ GitHub Pages 靜態行事曆。

## 鐵則

1. **所有寫入走 `schedule_cli.py`，不直接編輯 `data/schedule.yaml`**（CI strict validate 會擋壞資料；手改繞過驗證與 diff 防呆）
2. **兩段式寫入**：先 dry-run（不加 `--apply`）看 diff / errors，確認後才 `--apply`
3. **改完 data 後本地跑 `python scripts/render_html.py`** 再 commit（CI 有 auto-rebuild 兜底，但依賴它會多一個 bot commit 且讓本地落後）
4. **改動做完直接 commit + push，不問**（用戶靠線上行事曆看課表，不推等於看不到）
5. push 前若 remote 可能領先（CI auto-commit / 其他機器），先 `git fetch` + `git pull --ff-only`；ff 失敗停下來看，不 merge 不 rebase
6. Windows console 是 cp950：跑任何 script 都 `PYTHONIOENCODING=utf-8`
7. 對 `docs/` 內容不手改（`render_html.py` 產物，會被覆蓋）

## 操作介面（三條路，同一個 CLI 底層）

| 介面 | 場景 |
|------|------|
| `scripts/schedule_gui.py`（Tkinter，嵌在桌面看板 hub 分頁） | Hang 自己日常增刪改 + 一鍵上線 |
| `scripts/schedule_cli.py`（JSON envelope） | LLM（TG→m3 / Claude）排課，協議見 README |
| `scripts/query.py` | 唯讀查詢（today / week / month / class / slot） |

GUI 是 thin client：所有寫入 subprocess 呼叫 CLI，自己不碰 YAML。

## 環境

Python 3.10+，僅依賴 PyYAML。`.gitattributes` 強制 LF。
Repo：https://github.com/Hangsau/swim-coach-schedule（公開）——**程式碼內不得出現本機絕對路徑**。
