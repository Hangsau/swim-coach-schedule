"""schedule_gui.py v2 — 月曆為主視圖的游泳課表編輯器

thin client：所有寫入走 schedule_cli.py subprocess（dry-run → --apply 兩段式），
唯讀展開課表用 query.py 的函數（與線上頁同一套邏輯）。

可獨立跑（main），也可被桌面看板 hub 以 build_tab(parent) 嵌入。

互動模式：
  - 月曆是主視圖；點一堂課 → 選單（取消／挪課／加一堂／改班級／刪排課／刪班級）
  - 點空白日 → 選單（幫既有班加一堂／新班級精靈）
  - 新班級精靈中途放棄自動 rollback，避免孤兒班
  - E_TIME_OVERLAP 錯誤訊息人話化
  - 介面上不顯示 level 欄位
"""

import calendar
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from datetime import date, datetime
from pathlib import Path
from tkinter import ttk

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "scripts" / "schedule_cli.py"
RENDER = ROOT / "scripts" / "render_html.py"
PAGES_URL = "https://hangsau.github.io/swim-coach-schedule/"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from query import DAY_NAMES, expand_schedule, load as load_data  # noqa: E402

# ---- 配色：沿用桌面看板 NOC 風（與 religions-history 刊版一致；蓄意複製、不跨 repo import）----
BG    = "#15181c"
PANEL = "#1e2228"
FG    = "#d7dae0"
MUTED = "#7c828c"
DONE  = "#5b8a52"
PROG  = "#d9a441"
BAD   = "#c0504d"
TRACK = "#2b3038"
HEAD  = "#c8956c"

FONT    = "Microsoft JhengHei"
F_TITLE = (FONT, 17, "bold")
F_SEC   = (FONT, 11, "bold")
F_ROW   = (FONT, 10)
F_SMALL = (FONT, 9)
F_CHIP  = (FONT, 8)
F_MONO  = ("Consolas", 9)

# pythonw（無主控台）下起 console 子進程不彈黑框
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

SEP = "｜"           # combo 顯示值「id｜名稱」的分隔符，顯示與解析共用
ERR_TAIL = 400       # 畫面上錯誤訊息截尾長度
ERR_TAIL_LONG = 800  # envelope 錯誤訊息截尾長度

MAX_CHIPS = 3        # 每日格最多直接顯示的課數，超過收進「+N」
CHIP_FG = "#e8eaee"      # 班級 chip 上的亮字
DARK_TEXT = "#12151a"    # 強調色（綠）按鈕上的深色字
WEEKDAY_ZH = ["一", "二", "三", "四", "五", "六", "日"]
# 班級色票（NOC 同調的暗色系；以 class_id 字元和取模指派，跨 session 穩定）
CHIP_COLORS = ["#3d5a80", "#5b8a52", "#8a6d3b", "#7a4b6b",
               "#4b7a78", "#8a524d", "#586b8a", "#6b8a52"]


def chip_color(class_id):
    return CHIP_COLORS[sum(map(ord, str(class_id))) % len(CHIP_COLORS)]


def make_btn(parent, text, cmd, color=TRACK, fg=FG):
    return tk.Button(parent, text=text, command=cmd, bg=color, fg=fg, font=F_ROW,
                     relief="flat", activebackground=TRACK, activeforeground=FG,
                     padx=12, pady=3)


def run_cmd(argv, timeout=300):
    """統一 subprocess：list 傳參（無 shell）、強制 utf-8（Windows console 是 cp950）。"""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [str(a) for a in argv], cwd=str(ROOT), capture_output=True,
        encoding="utf-8", errors="replace", env=env,
        creationflags=CREATE_NO_WINDOW, timeout=timeout)


def run_cli(args):
    """呼叫 schedule_cli.py --json，永遠回 envelope dict（subprocess 失敗也包成 envelope）。"""
    p = run_cmd([sys.executable, CLI, "--json", *args])
    try:
        return json.loads(p.stdout)
    except (json.JSONDecodeError, ValueError):
        msg = (p.stderr or p.stdout or "（無輸出）").strip()[-ERR_TAIL_LONG:]
        return {"ok": False, "data": {}, "warnings": [], "next_actions": [],
                "errors": [{"code": "E_GUI_SUBPROCESS", "msg": msg, "context": {}}]}


def git_ahead():
    """本地領先 remote 幾個 commit；無 upstream / 解析失敗回 0。"""
    out = (run_cmd(["git", "rev-list", "--count", "@{u}..HEAD"]).stdout or "").strip()
    return int(out) if out.isdigit() else 0


def humanize(resp, class_names):
    """把 CLI 錯誤訊息改寫成教練看得懂的話（就地改 msg，回傳同一 dict）。"""
    for e in resp.get("errors") or []:
        if e.get("code") == "E_TIME_OVERLAP":
            ctx = e.get("context") or {}
            a = ctx.get("lesson_a") or {}
            b = ctx.get("lesson_b") or {}

            def label(x):
                cid = x.get("class_id", "?")
                return f"{class_names.get(cid, cid)}（{x.get('time', '?')}）"

            e["msg"] = (f"{ctx.get('date', '')} 撞課：{label(a)} 與 {label(b)} "
                        f"時段重疊。請換時段或換日期再試。")
    return resp


class MiniCal(tk.Toplevel):
    """迷你月曆：點日回傳 'YYYY-MM-DD' 字串，grab 還給 owner。"""

    def __init__(self, owner, initial, on_pick, x=None, y=None):
        super().__init__(owner)
        try:
            d = date.fromisoformat(initial) if initial else date.today()
        except (ValueError, TypeError):
            d = date.today()
        self._owner = owner
        self._on_pick = on_pick
        self._year, self._month = d.year, d.month
        self.title("選日期")
        self.configure(bg=BG, padx=8, pady=6)
        self.transient(owner)
        self.resizable(False, False)
        if x is not None and y is not None:
            self.geometry(f"+{x}+{y}")
        # 頭列：◀  /  月份標題  /  ▶
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x")
        make_btn(head, "◀", lambda: self._shift(-1)).pack(side="left")
        self._title_lbl = tk.Label(head, text="", bg=BG, fg=HEAD, font=F_SEC)
        self._title_lbl.pack(side="left", expand=True)
        make_btn(head, "▶", lambda: self._shift(1)).pack(side="right")
        # 星期表頭
        wk = tk.Frame(self, bg=BG)
        wk.pack(fill="x", pady=(4, 2))
        for zh in WEEKDAY_ZH:
            tk.Label(wk, text=zh, bg=BG, fg=MUTED, font=F_SMALL,
                     width=3).pack(side="left")
        # 日格容器（換月時只清這個）
        self._grid = tk.Frame(self, bg=BG)
        self._grid.pack(fill="x")
        self._draw()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.wait_visibility()
        self.grab_set()

    def _shift(self, delta):
        m = self._month + delta
        self._year += (m - 1) // 12
        self._month = (m - 1) % 12 + 1
        for w in self._grid.winfo_children():
            w.destroy()
        self._draw()

    def _draw(self):
        self._title_lbl.config(text=f"{self._year} 年 {self._month} 月")
        today = date.today()
        for week in calendar.Calendar(firstweekday=0).monthdatescalendar(
                self._year, self._month):
            row = tk.Frame(self._grid, bg=BG)
            row.pack(fill="x")
            for d in week:
                in_month = (d.month == self._month)
                bg = PANEL if in_month else BG
                fg = FG if in_month else MUTED
                if d == today:
                    fg = HEAD
                    font = (FONT, 10, "bold")
                else:
                    font = F_ROW
                tk.Button(row, text=str(d.day), relief="flat", bg=bg, fg=fg,
                          font=font, width=3,
                          command=lambda dd=d: self._pick(dd)).pack(side="left")

    def _pick(self, d):
        self._on_pick(str(d))
        self._close()

    def _close(self):
        self.destroy()
        try:
            self._owner.grab_set()
        except tk.TclError:
            pass


class FormDialog(tk.Toplevel):
    """通用表單。fields: [{flag,label,kind(entry|combo|check|date),values?,hint?,required?,value?}]

    value = 預填值（entry/combo/date 為字串，check 為 bool）。
    submit 後 self.result = {flag: str_value 或 True(check)}；空欄位不進 result。
    """

    def __init__(self, parent, title, fields):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG, padx=16, pady=12)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.result = None
        self._fields = fields
        self._vars = {}

        tk.Label(self, text=title, bg=BG, fg=HEAD, font=F_SEC).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        r = 1
        for f in fields:
            lab = f["label"] + ("＊" if f.get("required") else "")
            tk.Label(self, text=lab, bg=BG, fg=FG, font=F_ROW, anchor="w").grid(
                row=r, column=0, sticky="w", padx=(0, 10), pady=3)
            if f["kind"] == "check":
                v = tk.BooleanVar(value=bool(f.get("value")))
                tk.Checkbutton(self, variable=v, bg=BG, activebackground=BG,
                               selectcolor=PANEL).grid(row=r, column=1, sticky="w")
            elif f["kind"] == "combo":
                v = tk.StringVar(value=f.get("value", ""))
                ttk.Combobox(self, textvariable=v, values=f.get("values", []),
                             width=34, font=F_ROW).grid(row=r, column=1, sticky="we", pady=3)
            elif f["kind"] == "date":
                v = tk.StringVar(value=f.get("value", ""))
                rowf = tk.Frame(self, bg=BG)
                rowf.grid(row=r, column=1, sticky="we", pady=3)
                tk.Entry(rowf, textvariable=v, width=26, bg=PANEL, fg=FG,
                         insertbackground=FG, relief="flat",
                         font=F_ROW).pack(side="left")
                btn = tk.Button(rowf, text="📅", bg=TRACK, fg=FG, relief="flat",
                                font=F_ROW, padx=6)
                btn.pack(side="left", padx=(4, 0))
                btn.config(command=lambda b=btn, var=v: MiniCal(
                    self, var.get(), var.set,
                    x=b.winfo_rootx(),
                    y=b.winfo_rooty() + b.winfo_height()))
            else:
                v = tk.StringVar(value=f.get("value", ""))
                tk.Entry(self, textvariable=v, width=36, bg=PANEL, fg=FG,
                         insertbackground=FG, relief="flat", font=F_ROW).grid(
                    row=r, column=1, sticky="we", pady=3)
            self._vars[f["flag"]] = v
            r += 1
            if f.get("hint"):
                tk.Label(self, text=f["hint"], bg=BG, fg=MUTED, font=F_SMALL,
                         anchor="w").grid(row=r, column=1, sticky="w")
                r += 1

        self.err = tk.Label(self, text="", bg=BG, fg=BAD, font=F_SMALL, anchor="w")
        self.err.grid(row=r, column=0, columnspan=2, sticky="w", pady=(6, 0))
        btns = tk.Frame(self, bg=BG)
        btns.grid(row=r + 1, column=0, columnspan=2, sticky="e", pady=(10, 0))
        make_btn(btns, "取消", self.destroy, color=PANEL).pack(side="left", padx=4)
        make_btn(btns, "下一步（先看 diff）", self._submit).pack(side="left", padx=4)
        self.wait_visibility()
        self.grab_set()
        self.focus_set()

    def _submit(self):
        out = {}
        for f in self._fields:
            v = self._vars[f["flag"]]
            if f["kind"] == "check":
                if v.get():
                    out[f["flag"]] = True
                continue
            s = v.get().strip()
            if s:
                # combo 值形如「STU-04｜乖乖」→ 只取 id；一般輸入原樣保留
                out[f["flag"]] = s.split(SEP)[0].strip() if f["kind"] == "combo" else s
            elif f.get("required"):
                self.err.config(text=f"「{f['label']}」必填")
                return
        self.result = out
        self.destroy()


class ConfirmDialog(tk.Toplevel):
    """顯示 dry-run / apply 結果：errors、warnings、diff；ok 時提供「確認寫入」。"""

    def __init__(self, parent, title, resp, on_confirm=None):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG, padx=14, pady=10)
        self.transient(parent.winfo_toplevel())
        self.geometry("640x480")

        ok = bool(resp.get("ok"))
        head = "✔ dry-run 通過，確認 diff 後寫入" if ok and on_confirm else \
               ("✔ 完成" if ok else "✘ 有錯誤，未寫入")
        tk.Label(self, text=f"{title}　{head}", bg=BG, fg=(DONE if ok else BAD),
                 font=F_SEC, anchor="w").pack(fill="x")

        for e in resp.get("errors") or []:
            tk.Label(self, text=f"錯誤 {e.get('code')}：{e.get('msg')}", bg=BG, fg=BAD,
                     font=F_SMALL, anchor="w", wraplength=600, justify="left").pack(fill="x")
        for w in resp.get("warnings") or []:
            tk.Label(self, text=f"警告 {w.get('code')}：{w.get('msg')}", bg=BG, fg=PROG,
                     font=F_SMALL, anchor="w", wraplength=600, justify="left").pack(fill="x")

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, pady=(6, 0))
        txt = tk.Text(body, bg=PANEL, fg=FG, font=F_MONO, relief="flat",
                      wrap="none", insertbackground=FG)
        sb = tk.Scrollbar(body, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        diff = (resp.get("data") or {}).get("diff") or "（無 diff——資料未變）"
        txt.insert("1.0", diff)
        txt.tag_configure("add", foreground=DONE)
        txt.tag_configure("del", foreground=BAD)
        for i, line in enumerate(diff.splitlines(), start=1):
            if line.startswith("+") and not line.startswith("+++"):
                txt.tag_add("add", f"{i}.0", f"{i}.end")
            elif line.startswith("-") and not line.startswith("---"):
                txt.tag_add("del", f"{i}.0", f"{i}.end")
        txt.configure(state="disabled")

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", pady=(8, 0))
        make_btn(btns, "關閉", self.destroy, color=PANEL).pack(side="right", padx=4)
        if ok and on_confirm:
            make_btn(btns, "確認寫入（--apply）",
                     lambda: (self.destroy(), on_confirm()),
                     color=DONE, fg=DARK_TEXT).pack(side="right", padx=4)
        self.wait_visibility()
        self.grab_set()


class SwimTab(tk.Frame):
    """月曆主視圖的游泳課表分頁。"""

    def __init__(self, container):
        super().__init__(container, bg=BG)
        t = date.today()
        self._loading = False
        self._classes = []
        self._slots = []
        self._all_lessons = []
        self._conflict_dates = set()
        self._year, self._month = t.year, t.month

        self._setup_style()
        self._build_static()
        self.after(200, self.refresh)

    # ---- 排版 ----

    def _setup_style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=PANEL, background=PANEL,
                        foreground=FG, arrowcolor=FG, borderwidth=0)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL)],
                  foreground=[("readonly", FG)])

    def _build_static(self):
        # 頭列
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=18, pady=(14, 0))
        tk.Label(head, text="游泳課表", bg=BG, fg=HEAD, font=F_TITLE).pack(side="left")

        nav = tk.Frame(head, bg=BG)
        nav.pack(side="right")
        make_btn(nav, "重新整理", self.refresh).pack(side="right", padx=4)
        more_btn = make_btn(nav, "更多 ▾", self._more_menu)
        more_btn.pack(side="right", padx=4)
        self._more_btn = more_btn
        make_btn(nav, "班級 ▾", self._class_panel).pack(side="right", padx=4)
        make_btn(nav, "今天", self._goto_today).pack(side="right", padx=4)
        self.month_lbl = tk.Label(nav, text="", bg=BG, fg=HEAD, font=F_SEC)
        self.month_lbl.pack(side="right", padx=6)
        make_btn(nav, "▶", lambda: self._shift_month(1)).pack(side="right", padx=2)
        make_btn(nav, "◀", lambda: self._shift_month(-1)).pack(side="right", padx=2)

        # 摘要 + git 列
        meta = tk.Frame(self, bg=BG)
        meta.pack(fill="x", padx=18, pady=(6, 0))
        self.sum_lbl = tk.Label(meta, text="", bg=BG, fg=MUTED, font=F_SMALL, anchor="w")
        self.sum_lbl.pack(side="left", fill="x", expand=True)
        self.git_lbl = tk.Label(meta, text="", bg=BG, fg=MUTED, font=F_SMALL, anchor="e")
        self.git_lbl.pack(side="right")

        # 星期表頭
        wk = tk.Frame(self, bg=BG)
        wk.pack(fill="x", padx=18, pady=(8, 0))
        for i, zh in enumerate(WEEKDAY_ZH):
            tk.Label(wk, text=f"週{zh}", bg=BG, fg=MUTED, font=F_SMALL,
                     anchor="w").grid(row=0, column=i, sticky="we", padx=2)

        for i in range(7):
            wk.grid_columnconfigure(i, weight=1, uniform="wd")

        # 月曆本體
        self.cal = tk.Frame(self, bg=BG)
        self.cal.pack(fill="both", expand=True, padx=18, pady=(4, 6))
        for c in range(7):
            self.cal.grid_columnconfigure(c, weight=1, uniform="cal")
        for r in range(6):
            self.cal.grid_rowconfigure(r, weight=1, uniform="calr")

        # 底部上線列
        push_row = tk.Frame(self, bg=BG)
        push_row.pack(fill="x", padx=18, pady=(0, 14))
        self.commit_msg = tk.Entry(push_row, bg=PANEL, fg=FG, insertbackground=FG,
                                   relief="flat", font=F_ROW)
        self.commit_msg.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.push_btn = make_btn(push_row, "一鍵上線（render + push）",
                                 self.push_flow, color=DONE, fg=DARK_TEXT)
        self.push_btn.pack(side="left", padx=4)
        self.push_lbl = tk.Label(push_row, text="", bg=BG, fg=MUTED, font=F_SMALL,
                                 anchor="w", wraplength=520, justify="left")
        self.push_lbl.pack(side="left", padx=8)

    # ---- 載入 / 渲染 ----

    def refresh(self):
        if self._loading:
            return
        self._loading = True

        def work():
            try:
                status = run_cli(["status"])
                classes = run_cli(["list-classes", "--with-schedules"])
                slots = run_cli(["list-slots"])
                data = load_data()
                slots_by_id = {s["id"]: s for s in data.get("slots") or []}
                classes_by_id = {c["id"]: c for c in data.get("classes") or []}
                lessons = expand_schedule(
                    data.get("schedules") or [], slots_by_id, classes_by_id)
                g_dirty = run_cmd(["git", "status", "--porcelain"])
                ahead = git_ahead()
                self.after(0, lambda: self._render(
                    status, classes, slots, lessons, g_dirty, ahead))
            except Exception as ex:
                self.after(0, lambda: self.sum_lbl.config(
                    text=f"✘ 載入失敗：{ex}", fg=BAD))
            finally:
                self._loading = False

        threading.Thread(target=work, daemon=True).start()

    def _render(self, status, classes, slots, lessons, g_dirty, ahead):
        # 摘要
        up = (status.get("data") or {}).get("upcoming_7d") or []
        count = len(up)
        if count:
            next_lesson = min(up, key=lambda x: (x.get("date", ""), x.get("slot_time", "")))
            next_txt = f"　下一堂：{next_lesson.get('date', '')} {next_lesson.get('slot_time', '')} {next_lesson.get('class_name', '')}"
        else:
            next_txt = ""
        c_dates = {e.get("context", {}).get("date")
                   for e in status.get("errors") or []
                   if e.get("code") == "E_TIME_OVERLAP"}
        c_dates.discard(None)
        self._conflict_dates = c_dates
        conflict_txt = (f"　⚠ 衝突 {len(c_dates)} 天" if c_dates else "　無衝突")
        orphan = (status.get("data") or {}).get("orphan_classes") or []
        orphan_txt = (f"　孤兒班 {len(orphan)}" if orphan else "")
        self.sum_lbl.config(
            text=f"未來 7 天 {count} 堂{conflict_txt}{orphan_txt}{next_txt}",
            fg=(BAD if c_dates else (DONE if count else MUTED)))

        # git
        dirty = bool((g_dirty.stdout or "").strip())
        if dirty and ahead:
            git_txt = f"⚠ 本地有 {ahead} 個未推送 commit，且有未 commit 變更"
        elif ahead:
            git_txt = f"↑ 本地領先 {ahead} 個 commit"
        elif dirty:
            git_txt = "● 有未 commit 變更"
        else:
            git_txt = "✓ 乾淨"
        self.git_lbl.config(text=git_txt,
                            fg=(PROG if (dirty or ahead) else MUTED))

        self._classes = (classes.get("data") or {}).get("classes") or []
        self._slots = (slots.get("data") or {}).get("slots") or []
        self._all_lessons = lessons
        self._render_calendar()

    def _render_calendar(self):
        for w in self.cal.winfo_children():
            w.destroy()
        self.month_lbl.config(text=f"{self._year} 年 {self._month} 月")

        weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(
            self._year, self._month)[:6]
        by_date = {}
        for lesson in self._all_lessons:
            by_date.setdefault(lesson["date"], []).append(lesson)
        for lessons_of_day in by_date.values():
            lessons_of_day.sort(key=lambda x: x.get("slot_time", ""))

        today = date.today()
        for r, week in enumerate(weeks):
            for c, day in enumerate(week):
                in_month = (day.month == self._month)
                is_today = (day == today)
                is_conflict = (str(day) in self._conflict_dates)
                bg_cell = PANEL if in_month else BG
                bd_color = HEAD if is_today else TRACK
                cell = tk.Frame(self.cal, bg=bg_cell, bd=0,
                                highlightthickness=1,
                                highlightbackground=bd_color)
                cell.grid(row=r, column=c, sticky="nsew", padx=1, pady=1)

                day_fg = FG if in_month else MUTED
                if is_conflict and in_month:
                    day_fg = BAD
                day_font = (FONT, 10, "bold") if is_today else F_SMALL
                day_lbl = tk.Label(cell, text=str(day.day), bg=bg_cell, fg=day_fg,
                                   font=day_font, anchor="w")
                day_lbl.pack(fill="x", padx=4, pady=(3, 1))

                lessons = by_date.get(day) or []
                if in_month:
                    cell.bind("<Button-1>",
                              lambda ev, dd=day: self._day_menu(ev, dd))
                    day_lbl.bind("<Button-1>",
                                 lambda ev, dd=day: self._day_menu(ev, dd))

                if not in_month:
                    continue

                visible = lessons[:MAX_CHIPS]
                for lesson in visible:
                    text = f"{lesson['slot_time'].split('-')[0]} {lesson['class_name']}"
                    chip = tk.Label(cell, text=text, bg=chip_color(lesson["class_id"]),
                                    fg=CHIP_FG, font=F_CHIP, anchor="w", padx=4)
                    chip.pack(fill="x", padx=2, pady=1)
                    chip.bind("<Button-1>",
                              lambda ev, ll=lesson: self._lesson_menu(ev, ll))
                if len(lessons) > MAX_CHIPS:
                    extra = len(lessons) - MAX_CHIPS
                    more = tk.Label(cell, text=f"+{extra} 堂…", bg=TRACK,
                                    fg=MUTED, font=F_CHIP, anchor="w", padx=4)
                    more.pack(fill="x", padx=2, pady=1)
                    more.bind("<Button-1>",
                              lambda ev, dd=day, ls=lessons: self._day_popup(dd, ls))

    # ---- 月導航 ----

    def _shift_month(self, delta):
        m = self._month + delta
        self._year += (m - 1) // 12
        self._month = (m - 1) % 12 + 1
        self._render_calendar()

    def _goto_today(self):
        t = date.today()
        self._year, self._month = t.year, t.month
        self._render_calendar()

    # ---- 共用 helpers ----

    def _class_values(self):
        return [f"{c['id']}{SEP}{c.get('name') or ''}" for c in self._classes]

    def _slot_values(self):
        return [f"{s['id']}{SEP}{s.get('time') or ''}" for s in self._slots]

    def _class_names(self):
        return {c["id"]: (c.get("name") or c["id"]) for c in self._classes}

    @staticmethod
    def _args(cmd, result):
        args = [cmd]
        for flag, val in result.items():
            args += [flag] if val is True else [flag, str(val)]
        return args

    # ---- 表單欄位 builders（多個選單共用同一組欄位）----

    def _slot_time_fields(self):
        return [
            {"flag": "--slot", "label": "常用時段", "kind": "combo",
             "values": self._slot_values(), "hint": "或改填下欄自訂時段"},
            {"flag": "--time", "label": "自訂時段", "kind": "entry",
             "hint": "HH:MM-HH:MM（與常用時段擇一）"},
        ]

    def _fields_add_lesson(self, class_value="", date_value=""):
        return [
            {"flag": "--class", "label": "班級", "kind": "combo",
             "values": self._class_values(), "value": class_value,
             "required": True},
            {"flag": "--date", "label": "日期", "kind": "date",
             "value": date_value, "required": True, "hint": "YYYY-MM-DD"},
            *self._slot_time_fields(),
            {"flag": "--note", "label": "備註", "kind": "entry"},
        ]

    def _fields_add_schedule(self, class_value=""):
        return [
            {"flag": "--class", "label": "班級", "kind": "combo",
             "values": self._class_values(), "value": class_value,
             "required": True},
            *self._slot_time_fields(),
            {"flag": "--day", "label": "單一星期", "kind": "combo",
             "values": DAY_NAMES,
             "hint": "與 --days / --specific-dates 三擇一"},
            {"flag": "--days", "label": "多個星期", "kind": "entry",
             "hint": "mon,tue,wed,...（逗號分隔）"},
            {"flag": "--specific-dates", "label": "指定日期",
             "kind": "entry",
             "hint": "YYYY-MM-DD,YYYY-MM-DD"},
            {"flag": "--start", "label": "開始日", "kind": "date",
             "required": True, "hint": "YYYY-MM-DD"},
            {"flag": "--weeks", "label": "持續週數", "kind": "entry",
             "hint": "週數 / 結束日 / 總堂數 三擇一"},
            {"flag": "--end", "label": "結束日", "kind": "date",
             "hint": "YYYY-MM-DD"},
            {"flag": "--lessons", "label": "總堂數", "kind": "entry"},
            {"flag": "--note", "label": "備註", "kind": "entry"},
        ]

    def _fields_update_class(self, class_rec=None):
        if class_rec is not None:
            id_val = f"{class_rec['id']}{SEP}{class_rec.get('name') or ''}"
            name_val = class_rec.get("name") or ""
            wc_val = str(class_rec.get("weekly_count") or "")
            level_val = class_rec.get("level") or ""
            note_val = class_rec.get("note") or ""
        else:
            id_val = name_val = wc_val = level_val = note_val = ""
        return [
            {"flag": "--id", "label": "班級 ID", "kind": "combo",
             "values": self._class_values(), "value": id_val,
             "required": True},
            {"flag": "--name", "label": "名稱", "kind": "entry",
             "value": name_val},
            {"flag": "--weekly-count", "label": "每週堂數", "kind": "entry",
             "value": wc_val, "hint": "整數"},
            {"flag": "--level", "label": "程度", "kind": "entry",
             "value": level_val},
            {"flag": "--note", "label": "備註", "kind": "entry",
             "value": note_val},
        ]

    def _fields_remove_class(self, id_value=""):
        return [
            {"flag": "--id", "label": "班級 ID", "kind": "combo",
             "values": self._class_values(), "value": id_value,
             "required": True},
            {"flag": "--cascade", "label": "連同排課一起刪", "kind": "check",
             "value": True},
        ]

    def _fields_remove_schedule(self, class_value="", day_value=""):
        return [
            {"flag": "--class", "label": "班級", "kind": "combo",
             "values": self._class_values(), "value": class_value,
             "required": True},
            {"flag": "--day", "label": "星期", "kind": "combo",
             "values": DAY_NAMES, "value": day_value},
            {"flag": "--slot-id", "label": "時段", "kind": "combo",
             "values": self._slot_values()},
            {"flag": "--all", "label": "刪除該班所有排課", "kind": "check"},
        ]

    @staticmethod
    def _fields_add_class():
        return [
            {"flag": "--id", "label": "班級 ID", "kind": "entry",
             "required": True, "hint": "例 STU-12"},
            {"flag": "--name", "label": "學員 / 班名", "kind": "entry",
             "required": True},
            {"flag": "--weekly-count", "label": "每週堂數", "kind": "entry",
             "required": True, "hint": "整數"},
            {"flag": "--note", "label": "備註", "kind": "entry"},
        ]

    # ---- 表單 + 確認 ----

    def _form_then_run(self, title, cmd, fields):
        dlg = FormDialog(self, title, fields)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        args = self._args(cmd, dlg.result)
        resp = humanize(run_cli(args), self._class_names())
        ConfirmDialog(self, title, resp,
                      on_confirm=lambda: self._apply(title, args))

    def _apply(self, title, args):
        resp = humanize(run_cli(args + ["--apply"]), self._class_names())
        ConfirmDialog(self, title + "（已寫入）" if resp.get("ok") else title, resp)
        self.refresh()

    # ---- 選單 ----

    def _lesson_menu(self, ev, lesson):
        cls_val = f"{lesson['class_id']}{SEP}{lesson['class_name']}"
        menu = tk.Menu(self, tearoff=0, bg=PANEL, fg=FG, activebackground=TRACK,
                       activeforeground=FG, font=F_ROW)
        menu.add_command(
            label=f"{lesson['date']}　{lesson['slot_time']}　{lesson['class_name']}",
            state="disabled")
        menu.add_separator()
        menu.add_command(label="取消這天這堂（不補）",
                         command=lambda: self._form_then_run(
                             "取消一堂", "cancel-lesson", [
                                 {"flag": "--class", "label": "班級", "kind": "combo",
                                  "values": self._class_values(), "value": cls_val,
                                  "required": True},
                                 {"flag": "--date", "label": "日期", "kind": "date",
                                  "value": str(lesson["date"]), "required": True},
                                 {"flag": "--reason", "label": "原因", "kind": "entry"},
                             ]))
        menu.add_command(label="挪到別天 / 換時段",
                         command=lambda: self._form_then_run(
                             "挪課", "move-lesson", [
                                 {"flag": "--class", "label": "班級", "kind": "combo",
                                  "values": self._class_values(), "value": cls_val,
                                  "required": True},
                                 {"flag": "--from-date", "label": "原日期", "kind": "date",
                                  "value": str(lesson["date"]), "required": True},
                                 {"flag": "--to-date", "label": "新日期", "kind": "date",
                                  "required": True, "hint": "YYYY-MM-DD"},
                                 {"flag": "--to-slot", "label": "新時段（常用）",
                                  "kind": "combo", "values": self._slot_values(),
                                  "hint": "或改填下欄"},
                                 {"flag": "--to-time", "label": "新時段（自訂）",
                                  "kind": "entry", "hint": "HH:MM-HH:MM"},
                                 {"flag": "--note", "label": "備註", "kind": "entry"},
                             ]))
        menu.add_command(label="這班臨時再加一堂",
                         command=lambda: self._form_then_run(
                             "加一堂", "add-lesson",
                             self._fields_add_lesson(class_value=cls_val)))
        menu.add_separator()
        menu.add_command(label="修改班級資料…",
                         command=lambda: self._form_then_run(
                             "修改班級", "update-class",
                             self._fields_update_class(
                                 class_rec=next(
                                     (c for c in self._classes
                                      if c["id"] == lesson["class_id"]),
                                     None))))
        menu.add_command(label="刪除這條排課…",
                         command=lambda: self._form_then_run(
                             "刪除排課", "remove-schedule",
                             self._fields_remove_schedule(
                                 class_value=cls_val, day_value=lesson["day"])))
        menu.add_command(label="刪除整個班級…",
                         command=lambda: self._form_then_run(
                             "刪除班級", "remove-class",
                             self._fields_remove_class(id_value=cls_val)))
        try:
            menu.tk_popup(ev.x_root, ev.y_root)
        finally:
            menu.grab_release()

    def _day_menu(self, ev, day):
        menu = tk.Menu(self, tearoff=0, bg=PANEL, fg=FG, activebackground=TRACK,
                       activeforeground=FG, font=F_ROW)
        menu.add_command(label=str(day), state="disabled")
        menu.add_separator()
        menu.add_command(label="幫既有班在這天加一堂",
                         command=lambda: self._form_then_run(
                             "加一堂", "add-lesson",
                             self._fields_add_lesson(date_value=str(day))))
        menu.add_command(label="建立全新班級（兩步精靈）",
                         command=lambda: self._wizard_new_class(day))
        try:
            menu.tk_popup(ev.x_root, ev.y_root)
        finally:
            menu.grab_release()

    def _day_popup(self, day, lessons):
        popup = tk.Toplevel(self)
        popup.title(str(day))
        popup.configure(bg=BG, padx=10, pady=8)
        popup.transient(self.winfo_toplevel())
        tk.Label(popup, text=f"{day}　共 {len(lessons)} 堂", bg=BG, fg=HEAD,
                 font=F_SEC, anchor="w").pack(fill="x", pady=(0, 6))
        for lesson in lessons:
            text = f"{lesson['slot_time']}  {lesson['class_name']}"
            chip = tk.Label(popup, text=text, bg=chip_color(lesson["class_id"]),
                            fg=CHIP_FG, font=F_ROW, anchor="w", padx=6, pady=2)
            chip.pack(fill="x", pady=1)
            chip.bind("<Button-1>", lambda ev, ll=lesson, p=popup: (
                p.destroy(), self._lesson_menu(ev, ll)))
        make_btn(popup, "關閉", popup.destroy, color=PANEL).pack(side="right", pady=(6, 0))
        popup.wait_visibility()
        popup.grab_set()

    def _class_panel(self):
        """班級總覽：列出所有班級與未來堂數，點一列開操作選單。"""
        panel = tk.Toplevel(self)
        panel.title("班級列表")
        panel.configure(bg=BG, padx=12, pady=10)
        panel.transient(self.winfo_toplevel())
        tk.Label(panel, text=f"班級（{len(self._classes)}）　點一班開操作選單",
                 bg=BG, fg=HEAD, font=F_SEC, anchor="w").pack(
            fill="x", pady=(0, 6))
        today_d = date.today()
        for c in sorted(self._classes, key=lambda c: c["id"]):
            cid = c["id"]
            name = c.get("name") or ""
            wc = c.get("weekly_count")
            wc_str = str(wc) if wc else "?"
            m = sum(1 for l in self._all_lessons
                    if l["class_id"] == cid and l["date"] >= today_d)
            text = f"{cid}　{name}　每週 {wc_str} 堂　未來 {m} 堂"
            row = tk.Label(panel, text=text, anchor="w", bg=PANEL, fg=FG,
                           font=F_ROW, padx=8, pady=3)
            row.pack(fill="x", pady=1)
            row.bind("<Button-1>",
                     lambda ev, cc=c, p=panel: (
                         p.destroy(), self._class_menu(ev, cc)))
        make_btn(panel, "關閉", panel.destroy, color=PANEL).pack(
            side="right", pady=(8, 0))
        panel.wait_visibility()
        panel.grab_set()

    def _class_menu(self, ev, c):
        """班級總覽點擊後的操作選單（避開 day_popup，獨立路徑）。"""
        cls_val = f"{c['id']}{SEP}{c.get('name') or ''}"
        menu = tk.Menu(self, tearoff=0, bg=PANEL, fg=FG, activebackground=TRACK,
                       activeforeground=FG, font=F_ROW)
        menu.add_command(
            label=f"{c['id']}　{c.get('name') or ''}", state="disabled")
        menu.add_separator()
        menu.add_command(label="修改班級資料（名稱／堂數／程度／備註）",
                         command=lambda: self._form_then_run(
                             "修改班級", "update-class",
                             self._fields_update_class(class_rec=c)))
        menu.add_command(label="這班臨時加一堂",
                         command=lambda: self._form_then_run(
                             "加一堂", "add-lesson",
                             self._fields_add_lesson(class_value=cls_val)))
        menu.add_command(label="新增每週固定排課",
                         command=lambda: self._form_then_run(
                             "新增排課", "add-schedule",
                             self._fields_add_schedule(class_value=cls_val)))
        menu.add_separator()
        menu.add_command(label="刪除排課…",
                         command=lambda: self._form_then_run(
                             "刪除排課", "remove-schedule",
                             self._fields_remove_schedule(class_value=cls_val)))
        menu.add_command(label="刪除整個班級…",
                         command=lambda: self._form_then_run(
                             "刪除班級", "remove-class",
                             self._fields_remove_class(id_value=cls_val)))
        try:
            menu.tk_popup(ev.x_root, ev.y_root)
        finally:
            menu.grab_release()

    # ---- 頭列「更多 ▾」------

    def _more_menu(self):
        menu = tk.Menu(self, tearoff=0, bg=PANEL, fg=FG, activebackground=TRACK,
                       activeforeground=FG, font=F_ROW)
        menu.add_command(label="新增班級（無精靈，純表單）",
                         command=lambda: self._form_then_run(
                             "新增班級", "add-class", self._fields_add_class()))
        menu.add_command(label="修改班級",
                         command=lambda: self._form_then_run(
                             "修改班級", "update-class",
                             self._fields_update_class()))
        menu.add_command(label="刪除班級",
                         command=lambda: self._form_then_run(
                             "刪除班級", "remove-class",
                             self._fields_remove_class()))
        menu.add_separator()
        menu.add_command(label="新增每週固定排課",
                         command=lambda: self._form_then_run(
                             "新增排課", "add-schedule",
                             self._fields_add_schedule()))
        menu.add_command(label="刪除排課",
                         command=lambda: self._form_then_run(
                             "刪除排課", "remove-schedule",
                             self._fields_remove_schedule()))
        try:
            menu.tk_popup(self._more_btn.winfo_rootx(),
                          self._more_btn.winfo_rooty() + self._more_btn.winfo_height())
        finally:
            menu.grab_release()

    # ---- 新班級精靈（防孤兒班）----

    def _wizard_new_class(self, day):
        f1 = FormDialog(self, "新班級（第 1 步／共 2 步：基本資料）",
                        self._fields_add_class())
        self.wait_window(f1)
        if f1.result is None:
            return
        args1 = self._args("add-class", f1.result)
        resp = humanize(run_cli(args1), self._class_names())
        if not resp.get("ok"):
            ConfirmDialog(self, "新班級（第 1 步）", resp)
            return
        resp = humanize(run_cli(args1 + ["--apply"]), self._class_names())
        if not resp.get("ok"):
            ConfirmDialog(self, "新班級（第 1 步）", resp)
            return
        cid = f1.result["--id"]

        # 第 2 步：排課。沒完成就 rollback，避免孤兒班（CI strict 會擋）
        state = {"applied": False}
        f2 = FormDialog(self, f"新班級（第 2 步／共 2 步：{cid} 的排課）", [
            *self._slot_time_fields(),
            {"flag": "--days", "label": "每週幾", "kind": "entry",
             "value": DAY_NAMES[day.weekday()],
             "hint": "mon,tue,wed,thu,fri,sat,sun（逗號分隔可多天）"},
            {"flag": "--start", "label": "開始日", "kind": "date",
             "value": str(day), "hint": "YYYY-MM-DD"},
            {"flag": "--weeks", "label": "持續週數", "kind": "entry",
             "hint": "週數 / 結束日 / 總堂數 三擇一"},
            {"flag": "--end", "label": "結束日", "kind": "date"},
            {"flag": "--lessons", "label": "總堂數", "kind": "entry"},
            {"flag": "--note", "label": "備註", "kind": "entry"},
        ])
        self.wait_window(f2)
        if f2.result is not None:
            args2 = self._args("add-schedule", {"--class": cid, **f2.result})
            resp2 = humanize(run_cli(args2), self._class_names())

            def do_apply():
                r = humanize(run_cli(args2 + ["--apply"]), self._class_names())
                state["applied"] = bool(r.get("ok"))
                ConfirmDialog(self, "新班級排課", r)

            # ConfirmDialog 只在 resp ok 時顯示確認鈕，這裡直接傳即可
            dlg = ConfirmDialog(self, "新班級排課（dry-run）", resp2,
                                on_confirm=do_apply)
            self.wait_window(dlg)

        if not state["applied"]:
            rollback_resp = run_cli(["remove-class", "--id", cid, "--apply"])
            if rollback_resp.get("ok"):
                self._push_say(f"已自動撤銷剛建立的班級 {cid}（排課未完成，避免孤兒班）", PROG)
            else:
                self._push_say(f"⚠ 撤銷失敗：班級 {cid} 目前沒有排課（孤兒班），"
                               f"請用「更多 ▾ → 刪除班級」手動刪除", BAD)
        self.refresh()

    # ---- 上線流程 ----

    def push_flow(self):
        self.push_btn.config(state="disabled")
        self._push_say("上線中…", PROG)

        def say(text, color=MUTED):
            self.after(0, lambda: self._push_say(text, color))

        def work():
            try:
                steps = [
                    ("同步 remote（fetch）", ["git", "fetch"]),
                    # ff-only：remote 領先（如 CI auto-rebuild commit）就快轉；會分岔則停下，不自動 merge/rebase
                    ("同步 remote（pull --ff-only）", ["git", "pull", "--ff-only"]),
                    ("重建行事曆頁面", [sys.executable, RENDER]),
                    ("git add", ["git", "add", "data/", "docs/"]),
                ]
                for desc, argv in steps:
                    say(f"{desc}…")
                    p = run_cmd(argv)
                    if p.returncode != 0:
                        say(f"✘ {desc} 失敗：{(p.stderr or p.stdout).strip()[-ERR_TAIL:]}\n"
                            "（remote 若有新變更，先重新整理確認狀況再重試）", BAD)
                        return
                staged = run_cmd(["git", "diff", "--cached", "--quiet"]).returncode != 0
                if not staged and not git_ahead():
                    say("沒有需要上線的變更。", MUTED)
                    return
                if staged:
                    msg = self.commit_msg.get().strip() or \
                        f"課表更新（GUI）{datetime.now():%Y-%m-%d %H:%M}"
                    say("commit…")
                    p = run_cmd(["git", "commit", "-m", msg])
                    if p.returncode != 0:
                        say(f"✘ commit 失敗：{(p.stderr or p.stdout).strip()[-ERR_TAIL:]}", BAD)
                        return
                say("push…")
                p = run_cmd(["git", "push"])
                if p.returncode != 0:
                    say(f"✘ push 被拒：{(p.stderr or p.stdout).strip()[-ERR_TAIL:]}\n"
                        "（多半是 remote 剛有新 commit；重按一次會先 ff-only 同步再推）", BAD)
                    return
                say(f"✔ 已 push。CI 驗證 + Pages 部署約 1–2 分鐘後上線：{PAGES_URL}", DONE)
            except Exception as ex:  # 背景 thread 例外不能靜默吞掉
                say(f"✘ 例外：{ex}", BAD)
            finally:
                self.after(0, lambda: self.push_btn.config(state="normal"))
                self.after(0, self.refresh)

        threading.Thread(target=work, daemon=True).start()

    def _push_say(self, text, color):
        self.push_lbl.config(text=text, fg=color)


def build_tab(container):
    """給 host（桌面看板 hub）用：在 container 裡建整個分頁並回傳。"""
    tab = SwimTab(container)
    tab.pack(fill="both", expand=True)
    return tab


def main():
    root = tk.Tk()
    root.title("游泳課表編輯器")
    root.configure(bg=BG)
    root.geometry("1060x780")
    root.minsize(880, 660)
    build_tab(root)
    root.mainloop()


if __name__ == "__main__":
    main()
