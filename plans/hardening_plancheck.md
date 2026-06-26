# swim-coach-schedule 防呆與 LLM-friendly 重構 — plan-check

> 作者：Claude (Opus 4.7) — 2026-06-27
> 版本：v2（codex 審查後修訂；用戶 inline 答覆吸收）
> 用戶決策（v1 § 7 inline）：(1) STU-10 直接刪；(2) CI 自決，不要分支；(3) note metadata 採最小遷移（用戶不懂 → 不擅自刪人類可讀文字）

## v2 修訂摘要（吸收 codex review）

致命：**CLI 寫入語義反轉** — 預設 dry-run，真寫入需 `--apply`（v1 寫成 dry-run=False 矛盾，會打穿防呆核心）

W4 補：(a) `update-class` / `update-schedule` 子命令（防 LLM 用 remove+add 流失資料）；(b) `schedules[].id` stable identifier（如 `SCH-001`）；(c) `except_dates` 欄位（move-lesson 才能實作）；(d) JSON envelope 統一 `{"ok", "data", "errors", "warnings", "next_actions"}`；(e) 錯誤碼補：`E_DUPLICATE_SCHEDULE` / `E_INVALID_DATE_RANGE` / `E_WEEKLY_COUNT_EXCEEDED` / `E_VALIDATE_FAILED`；(f) atomic write 改成「temp 上先 validate → 通過才 replace」（v1 順序顛倒）

W1 補洞：time regex 收緊 `^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$`；禁跨午夜；`start_date` 加上限 `today + 730 days`；`days` 必須 unique；`weekly_count >= 1`；schedule 不能同時有 `specific_dates` 跟 `day/days`；duplicate schedule entry 偵測進 validate（不只 W2 測試）；孤兒 class 在 strict mode 改 fail（不只 warn）

順序拆：原 W1→W2→...→W5 改為 **W0 → W1a → W5 → W1b → W2 → W3 → W4 → W8 → W7 → W6**
- W0（新增）：抽 `expand_schedule` 出來成 `scripts/expand.py`
- W1a：validate v1 寬鬆模式（容許現有 yaml）
- W5：data migration（刪 STU-10、加 `schema_version: 2`、最小 note 清理）
- W1b：strict mode 開啟
- 其餘照順序

W5 收斂：用戶說 note 「不知道這是什麼」→ **最小遷移**。只刪 schedule 中跟 `day/slot_id` 明顯重複表達的 note（如「每週三 9:00-10:00」），保留所有人類軌跡文字（「user 修正」「7/20 開始」），不引入 `history` 結構（codex 建議的版本用戶看不懂）。schema header 用 `schema_version: 2`（不用 `schema_url`）

W6 CI：(a) 用 `if: github.actor != 'github-actions[bot]'` 守衛（取代脆弱的 commit message contains）；(b) `test_docs_up_to_date` 只在 PR 階段跑（main push 會 auto-commit 修好，不該 fail）；(c) workflow 加 `permissions: contents: write` + `concurrency`；(d) 用 `uv run --with pyyaml --with pytest`

Sonnet self-contained 補強：implement 階段每個 W 的 spec 加：YAML shape 範例、expand_schedule 語義（duration_weeks 含 start week、total_lessons 跨 days 截到 N 堂停、end_date inclusive、specific_dates 排序去重）、CLI YAML 寫入規則（保留 key 順序 / 不破壞 anchors / 不動現有 comments / atomic write 流程）

驗收測試數修正：W1 8 + W2 6 + W4/W8 CLI/workflow ≈ 8 = 22+ 條，不寫死數字

---

## v1 原文（保留作為對照）

> 版本：v1（規劃完成，等用戶確認）
> 觸發：用戶要 minimax 經 TG 接受自然語言指令後操作此系統，現有設計連動性差、無 schema、衝突偵測缺漏；2026-06-26~27 連續 commit 在加減 STU-09/10 印證了「這邊改那邊沒改」的痛點

---

## 1. 目標狀態（plan 完成後系統長這樣）

### 1.1 使用者輪廓重新定位

**主要使用者 = minimax（LLM）**，不是人類。Hang 在 TG 對 minimax 講人話：「STU-11 阿明 每週二四 早上 10 點 從 7/15 排 12 堂」；minimax 解析後**透過 CLI 操作**，不直接編 YAML。

→ 設計目標：CLI 要讓 LLM **不可能**犯常見的蠢：
- 重複加同條 schedule
- 改 schedule 忘了改對應 class 的 note 文字
- 漏 add-class 直接 add-schedule（孤兒 schedule）
- 加了 schedule 用過期 / 不存在的 slot_id
- 加 specific_dates 跟既有班同時段
- 改了 yaml 忘了 rebuild docs
- 改了一處時段，別處跟進忘了改

### 1.2 系統能力承諾

1. **CLI 是唯一寫入入口**。LLM 不直接 edit YAML，所有寫操作走 `scripts/schedule_cli.py`
2. **每個 CLI 命令都跑 full validate**，validate 失敗就拒絕寫入，回 structured JSON 報錯
3. **衝突偵測升級成「時段區間重疊」**，不再只看 slot_id 字面相等
4. **CI 強制把關**：push 時自動跑 validate + tests + rebuild docs，全綠才能 merge
5. **YAML 保留為 source of truth**（人類 git diff 友好），但結構欄位收緊，`note` 不再混進 metadata
6. **dry-run 模式預設提供 diff**，LLM 可先 preview 再 commit
7. **`status` 命令**讓 LLM 在每次操作前/後抓系統快照（健康度 + 衝突 + 下週課表）

---

## 2. 影響範圍

### 既有檔案（修）

| 檔 | 修什麼 |
|---|---|
| `scripts/render_html.py` | 修第 610 行 hardcoded Linux path；修第 742、753、759、765、771 行的 `print("✓ ...")` Windows cp950 crash |
| `scripts/query.py` | 不動邏輯，但 `expand_schedule` 抽出成可重用模組（供 validate.py 用） |
| `tests/test_integration.py` | 補 6 條測試（見 W2） |
| `data/schedule.yaml` | (a) 修 STU-10 孤兒；(b) 把 `note` 內結構性 metadata（「7/20 開始」「user 修正：原 13 改 16」）取消，改為 `history` 或刪除；(c) 加 top-level schema header 標版本 |
| `README.md` | 加 minimax 使用指引段（CLI prompt 模板） |

### 新建檔案

| 檔 | 用途 |
|---|---|
| `scripts/validate.py` | schema + 一致性檢查單一入口；可 CLI 調用，可被 CI 跑，可被 cli.py 內部呼叫 |
| `scripts/schedule_cli.py` | LLM-friendly CLI（add-class / add-schedule / move-lesson / remove-schedule / list-conflicts / status / preview 等子命令） |
| `tests/test_validate.py` | validate 自己的 unit test（合法 / 各種非法情境） |
| `tests/test_cli.py` | CLI integration test（subprocess + temp yaml） |
| `.github/workflows/build.yml` | push main 時：uv sync → pytest → render_html → 比對 docs 是否更新；diff 不為空就自動 commit docs 回來 |
| `plans/hardening_plancheck.md` | 本檔 |

---

## 3. 執行路徑（W1 → W8）

**每個 W 完成立刻 `git add + commit`，commit message 標 `W{n}` + 派工標註**（按 claudehome CLAUDE.md 收尾規則）

### W1 — `scripts/validate.py`（schema + 一致性）`[claude: sonnet]`

**輸出**：CLI `python scripts/validate.py [--strict]`，可獨立跑、可被 import。

**檢查項（全部 fail-loud，回 structured result）**：

1. **schema validation**（YAML 載入時）：
   - `slots[].id` 字串非空 + 整檔唯一
   - `slots[].time` 符合 `^\d{2}:\d{2}-\d{2}:\d{2}$` 且 start < end
   - `slots[].note` 字串（可空）
   - `classes[].id` 字串非空 + 整檔唯一
   - `classes[].name` 非空
   - `classes[].weekly_count` int >= 0
   - `classes[].level` 字串（可空）
   - `schedules[].class_id` 必須在 `classes` 內
   - `schedules[].slot_id` 必須在 `slots` 內
   - `schedules` 必須有 `day` XOR `days` XOR `specific_dates` 之一（互斥）
   - `day` ∈ {mon,tue,wed,thu,fri,sat,sun}；`days` 為 subset 且非空
   - `start_date` ISO 格式且不早於 2020-01-01
   - `specific_dates[]` 全部 ISO 格式
   - 至少有 `duration_weeks`/`end_date`/`total_lessons`/`specific_dates` 之一作為終止條件

2. **跨欄位一致性**：
   - **無孤兒 class**：每個 `classes[].id` 至少被一條 schedule 引用 → 否則 warn（不 fail，但 minimax 看 status 會看到）
   - **無孤兒 slot**：每個 `slots[].id` 至少被一條 schedule 引用 → 否則 warn
   - **weekly_count 一致性**：每個 class 實際展開後最大週堂數 ≤ class.weekly_count（容許等於，不允許超）
   - **start_date < end_date**：若 end_date 存在，必須 > start_date

3. **時段衝突**（核心）：
   - 用既有 `time_overlap()` 算法
   - 同日，**任兩堂 lesson** 若 `slot.time` 有重疊（不只看 slot_id），標衝突
   - **例外**：用 `--allow-overlap-classes` 標籤的 class 對之間允許共用（未來多教練用，目前用不到）
   - 衝突清單按 `(date, time)` 排序輸出

4. **specific_dates 合理性**：
   - 每個 date 在 `[today - 7, today + 365]` 區間內（過去最多容許 7 天遲補登）
   - 如果 `--strict`，過去日期一律 fail
   - 用 warn 級別提示週末日期（sat/sun）— 因為教練可能週末確實有課，不 hard fail

5. **回傳格式**：
   ```json
   {
     "ok": false,
     "errors": [{"code": "SLOT_TIME_OVERLAP", "msg": "...", "context": {...}}, ...],
     "warnings": [{"code": "ORPHAN_CLASS", "msg": "STU-10 ...", "context": {...}}],
     "stats": {"classes": 10, "slots": 8, "schedules": 11, "lessons_expanded": 234}
   }
   ```
   - exit code 0 = ok（即使有 warnings）；exit code 1 = errors

**測試**：`tests/test_validate.py` 含 8 個情境：合法、孤兒 class、孤兒 slot、時段重疊、specific_date 過去、specific_date 太遠未來、duplicate id、schedule 缺終止條件

---

### W2 — `tests/test_integration.py` 補測試 `[claude: sonnet]`

加 6 條（保留現有 10 條）：

1. `test_no_time_overlap_across_classes`：用 `validate.time_overlap` 偵測**任兩堂 lesson 的時段是否重疊**（不再只看 slot_id 字面）— 攻擊 1 的剋星
2. `test_specific_dates_in_horizon`：所有 `specific_dates` 落在 `[2020-01-01, today+365]` — 攻擊 2 的剋星
3. `test_weekly_count_consistency`：每個 class 實際週擴展數 ≤ `weekly_count` — 攻擊 3 的剋星
4. `test_schedule_has_termination`：每條 schedule 至少有 duration_weeks/end_date/total_lessons/specific_dates 一個
5. `test_docs_up_to_date`：跑 render_html → 比對 docs/*.html — drift 的剋星（CI 也跑這條）
6. `test_no_duplicate_schedule_entry`：完全相同的 schedule 條目（class_id + slot_id + day + start_date 全等）不准重複

---

### W3 — 修 `render_html.py` bug `[claude: sonnet]`

1. **line 610**：`docs_dir = P("/home/hangsau/projects/swim-coach-schedule/docs")` → `docs_dir = ROOT / "docs"`
2. **line 742, 753, 759, 765, 771**：含 `✓` 字元的 print → 改成 `[ok]` 純 ASCII，或在檔頭加：
   ```python
   import sys
   if sys.stdout.encoding != 'utf-8':
       sys.stdout.reconfigure(encoding='utf-8')
   ```
   採後者，保留視覺上的勾。
3. 順便補：`render_grid` 的 `import calendar as cal_mod` 已存在於頂層 import → 移除重複 import
4. **drift 補丁**：跑完 render 後，自動跑 `git diff --name-only docs/` 列出哪些檔變了，print 出來方便 LLM 知道要 commit 哪些

---

### W4 — `scripts/schedule_cli.py` LLM-friendly CLI `[claude: sonnet]`

**設計原則**：
- 所有命令支援 `--json` 旗標，預設就是 JSON 輸出（LLM 友好）
- 寫入命令預設 `--dry-run=False`，但**強制顯示 diff 後再寫**（給 LLM 一次機會 abort）
- 失敗時 exit code != 0，stderr 給 structured error
- 每次成功寫入後自動跑 validate；validate fail 就 rollback（temp file → atomic rename）
- 不自動 git commit，留給 minimax 操作（但提示「下一步：git add data/schedule.yaml && git commit -m ...」）

**子命令清單**：

| 命令 | 參數 | 行為 |
|------|------|------|
| `status` | (none) | 印健康摘要：總班數 / 總堂數 / 本週課 / 衝突清單 / warning 清單 / 孤兒清單 |
| `list-classes` | `[--with-schedules]` | 列出所有 class，可選帶其 schedule 摘要 |
| `list-slots` | `[--used-only]` | 列出 slot |
| `list-conflicts` | (none) | 只列 validate 偵測到的衝突，方便 minimax 主動巡邏 |
| `add-class` | `--id --name --weekly-count [--level] [--note]` | 新增 class；ID 重複就拒絕；提示「下一步：用 add-schedule 排課」 |
| `add-schedule` | `--class --slot --start --(day\|days\|specific-dates) --(weeks\|end\|lessons)` | 新增 schedule；class/slot 不存在就拒絕並列出可用清單；衝突就拒絕並列出衝突 lesson |
| `remove-class` | `--id [--cascade]` | 刪 class；有 schedule 引用且未加 `--cascade` 就拒絕（防誤刪） |
| `remove-schedule` | `--class [--slot] [--day]` | 刪 schedule；無匹配就拒絕；多個匹配且未加 `--all` 就列出讓 LLM 二次選 |
| `move-lesson` | `--class --from-date --to-(slot\|date)` | 把單一展開後的 lesson 搬走 — 內部會新增 specific_dates 排除原日 + 加新 lesson |
| `preview` | 跟 add-* 同參數 + `--what add-schedule\|...` | dry-run，印 before/after diff，不寫檔 |

**錯誤碼**（穩定 enum，給 LLM 程式化處理）：
- `E_CLASS_NOT_FOUND` / `E_SLOT_NOT_FOUND` / `E_DUPLICATE_ID`
- `E_TIME_OVERLAP`（含 conflict_with: [{class_id, date, slot_id, slot_time}]）
- `E_PAST_DATE` / `E_DATE_TOO_FAR`
- `E_SCHEMA_INVALID`（含 field 路徑）
- `E_NO_TERMINATION`
- `E_AMBIGUOUS_TARGET`（remove 多匹配時）

**README 加段落示範**：給 minimax 看的 prompt template
```
你（minimax）操作此系統的方式：
1. 先跑 `python scripts/schedule_cli.py status --json` 看現況
2. 寫入前先跑 preview 確認 diff
3. 失敗時讀 error code，按表處理
4. 完成後跑 `python scripts/render_html.py` 更新 docs
5. 提示用戶 commit
```

---

### W5 — 清資料 + schema 落地 `[claude: sonnet]`

1. **修 STU-10 孤兒**：
   - 目前 `classes` 有 STU-10「個別班」但無 schedule → 預設刪 class（commit log 顯示用戶之前嘗試過刪又加回）
   - 但因為用戶之前明確說「之前 ceb0ad8 誤刪」，這次保守做法：**把 STU-10 從 classes 移到一個新區塊 `pending_classes`**，標 `awaiting_schedule: true`，CLI status 會顯示提示，但不再讓 validate fail
   - 新區塊在 schema 也納入驗證

2. **清 note 內 metadata drift**：
   - schedule 的 `note: "每週三 9:00-10:00"` 跟 `day: wed` + `slot: S3` 重複表達 → note 改成純粹 free comment（讓教練留訊息用，不再放結構資訊）
   - schedule 的 `note: "user 修正：原 13 改 16"` → 移到新欄位 `history: ["2026-06-XX: total_lessons 13→16"]`，可累積
   - class 的 `note: "7/20 開始"` → 此資訊由 schedules.start_date 表達，刪除
   - 不破壞性：保留 note 欄位，只清掉**重複表達結構欄位**的內容

3. **schema header**：YAML 最上方加：
   ```yaml
   version: 2
   schema_url: ./scripts/validate.py  # validate 從這版號決定 strict 程度
   ```

4. **migration 註記**：寫 `data/MIGRATION_v1_to_v2.md`，記錄欄位變更（讓未來 LLM 看歷史能懂）

---

### W6 — `.github/workflows/build.yml` CI `[claude: sonnet]`

```yaml
name: build
on: { push: { branches: [main] }, pull_request: { branches: [main] } }
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv venv && uv pip install pyyaml pytest
      - run: uv run python scripts/validate.py --strict
      - run: uv run pytest tests/ -v
      - run: uv run python scripts/render_html.py
      - name: Check docs drift
        run: |
          if ! git diff --quiet docs/; then
            echo "docs/ drifted, auto-committing"
            git config user.name "github-actions"
            git config user.email "actions@github.com"
            git add docs/
            git commit -m "ci: auto-rebuild docs from yaml"
            git push
          fi
        # 只在 push to main 時推回，PR 階段 fail
        if: github.event_name == 'push' && github.ref == 'refs/heads/main'
      - name: PR drift fail
        if: github.event_name == 'pull_request'
        run: git diff --exit-code docs/ || (echo "docs/ not up to date — run render_html.py and commit" && exit 1)
```

**注意**：自動 push 需要 `permissions: contents: write`，要在 workflow 最上層加。

---

### W7 — README 加 minimax 使用指引 `[claude: sonnet]`

新增段落「給 LLM（minimax / claude-m3）的操作協議」：
- CLI 入口
- JSON 輸出格式
- 錯誤碼處理表
- 常見任務範例（add new class / move lesson / find conflicts）
- 失敗回退（git restore data/schedule.yaml）

---

### W8 — minimax-flavor integration smoke `[claude: sonnet]`

寫一段 `tests/test_llm_workflow.py`，模擬 minimax 的多步操作：
1. status → 看到 stats
2. preview add-schedule → 看到 diff
3. add-schedule → 成功
4. add-schedule（衝突）→ 收到 E_TIME_OVERLAP，含 conflict_with 詳情
5. remove-schedule → 成功
6. final validate → 全綠

確認 CLI 對 LLM 「不可能犯蠢」的承諾兌現。

---

## 4. 預期風險 + 預案

| 風險 | 機率 | 影響 | 預案 |
|------|------|------|------|
| 改 schema 後現有 yaml validate fail | 高 | 立刻紅 | W1 validate 跟 W5 清資料同 commit，確保切換點原子；不分批 push |
| CI auto-push docs 引發循環 commit | 中 | 無限 build | workflow 加 `if: !contains(github.event.head_commit.message, 'auto-rebuild')` 守衛 |
| Windows / Linux 路徑差異再現 | 中 | render bug 復發 | 全面用 `Path(__file__).parent.parent`，禁止 hardcode；W3 + grep 整檔確認無 `/home/`、`C:\` 字串 |
| minimax 不照協議寫直接 edit yaml | 中 | drift 復發 | CI 把關（validate fail 擋 push）；README 對 minimax 明寫「禁止直接 edit yaml」 |
| `time_overlap` 偵測誤把同時段共用標衝突 | 低 | minimax 改不動課表 | 衝突偵測限定 lesson 之間，不算 slot 自身定義；同 slot 兩堂只發在 specific_dates 撞期時 |
| CLI 命令膨脹 minimax 記不住 | 中 | LLM 用錯命令 | README 給「最常用 3 命令」速查；`status` 命令在每次寫入後自動列出推薦 next-step |
| 自動 commit docs 把學員資料推上公開 repo | 低 | 隱私洩漏 | docs/ 內容已是公開課表（GitHub Pages 既已公開），但驗證一次內容無 PII；姓名都是學員代號或暱稱（STU-XX / 長頸鹿 / 乖乖），確認 OK |

---

## 5. 整體檢視

- **跟 swim-coach（PWA）的關係**：兩個 repo 不相干，本 plan 不涉及 swim-coach
- **跟 my-site / Vortex 的關係**：無
- **跟 Hestia / Talos 的關係**：無
- **5H 配額考量**：plan-check 寫完約用 5–8% 配額；implement 預估會用 30–50% 配額。建o議 shotclock 兩小時後啟動 Snnet（不是 Opus），Sonnet 配額池跟 Opus 分開、且 Sonnet 對這類「按 plan 執行」任務夠用
- **派工**：W1–W8 全 Sonnet 自寫（無需 minimax-m2.7 / opencode-go），因為都是中度複雜的編輯任務，非 verbatim spec
- **不破壞性**：
  - 舊 CLI（query.py）保留可用
  - 舊 yaml 結構在 W5 才動，且 W5 跟 W1 在同 commit
  - 線上版（GitHub Pages）只在 W6 部署後第一次自動 rebuild 時改變
- **可回退性**：每個 W 獨立 commit，可單獨 revert
- **驗收**：
  - `python scripts/validate.py --strict` exit 0
  - `pytest tests/ -v` 全綠（16 條測試）
  - `python scripts/render_html.py` 在 Windows 不 crash + grid view 連結正常
  - `python scripts/schedule_cli.py status --json` 印出有效 JSON
  - 手測：故意製造時段重疊 → CLI 拒絕並回 `E_TIME_OVERLAP`
  - CI workflow 跑通

---

## 6. 派工總表（implement 階段照表執行）

| W | 內容 | 派工 | 預估時間 |
|---|------|------|----------|
| W1 | validate.py | claude: sonnet | 中 |
| W2 | tests 補 6 條 | claude: sonnet | 小 |
| W3 | render_html bug 修 | claude: sonnet | 小 |
| W4 | schedule_cli.py | claude: sonnet | 大 |
| W5 | 資料清整 + schema header | claude: sonnet | 小 |
| W6 | CI workflow | claude: sonnet | 小 |
| W7 | README minimax 指引 | claude: sonnet | 小 |
| W8 | LLM-flavor smoke test | claude: sonnet | 小 |

全程 Sonnet。預估 implement 階段一個 session 內可收。

---

## 7. 等待確認

執行前需要你 OK 三件事：

1. **STU-10 處理方式**：移到 `pending_classes` 區塊（不刪）vs 直接刪？我預設前者（保守），告訴我要不要改後者 (我不知道這什麼 刪掉吧)
2. **CI 自動 commit docs 回 main 分支**：你 OK 嗎？這代表 CI 會用 GITHUB_TOKEN push commit。替代方案是 CI 失敗，要求人類本地 rebuild 後再 push (我看不懂?反正我只要一個 不要分支 你自己有權限 你自己搞)
3. **`note` 欄位 metadata 清理**：W5 會刪掉一些 note 文字（移到 history 或刪除），這會動到 git diff，你 OK 嗎？ (不知道這是什麼)

如果你睡前看到這份 plan，預設答案是 **全 yes**，shotclock 兩小時後自動跑 implement。睡醒不滿意可 revert。
