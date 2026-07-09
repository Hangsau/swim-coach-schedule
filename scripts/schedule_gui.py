#!/usr/bin/env python
"""游泳課表編輯器（Tkinter）。

thin client：所有寫入 subprocess 呼叫 schedule_cli.py，維持 dry-run → --apply 兩段式，
自己不碰 data/schedule.yaml。查詢也走 CLI（--json envelope）。

用法：
  獨立視窗： PYTHONIOENCODING=utf-8 pythonw scripts/schedule_gui.py
  嵌入 host： from schedule_gui import build_tab; build_tab(parent_frame)

動作 7 入口：加/改/刪班級、加/刪排課、單堂調整（挪課/取消/臨時加課）、一鍵上線。
split-schedule 等進階操作不在 GUI，走 CLI 或 LLM（見 README）。
"""

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "scripts" / "schedule_cli.py"
RENDER = ROOT / "scripts" / "render_html.py"
PAGES_URL = "https://hangsau.github.io/swim-coach-schedule/"

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
F_MONO  = ("Consolas", 9)

# pythonw（無主控台）下起 console 子進程不彈黑框
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

SEP = "｜"           # combo 顯示值「id｜名稱」的分隔符，顯示與解析共用
ERR_TAIL = 400       # 畫面上錯誤訊息截尾長度
ERR_TAIL_LONG = 800  # envelope 錯誤訊息截尾長度


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


def sched_summary(s):
    parts = []
    if s.get("day"):
        parts.append(str(s["day"]))
    if s.get("days"):
        parts.append(",".join(map(str, s["days"])))
    if s.get("specific_dates"):
        parts.append("指定日 " + ",".join(map(str, s["specific_dates"])))
    if s.get("time"):
        parts.append(str(s["time"]))
    elif s.get("slot_id"):
        parts.append(str(s["slot_id"]))
    if s.get("start_date"):
        parts.append(f"起 {s['start_date']}")
    if s.get("duration_weeks"):
        parts.append(f"{s['duration_weeks']} 週")
    if s.get("total_lessons"):
        parts.append(f"{s['total_lessons']} 堂")
    if s.get("end_date"):
        parts.append(f"迄 {s['end_date']}")
    if s.get("except_dates"):
        parts.append(f"跳過 {len(s['except_dates'])} 日")
    return "　·　".join(parts)


class FormDialog(tk.Toplevel):
    """通用表單。fields: [{flag,label,kind(entry|combo|check),values?,hint?,required?}]

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
                v = tk.BooleanVar(value=False)
                tk.Checkbutton(self, variable=v, bg=BG, activebackground=BG,
                               selectcolor=PANEL).grid(row=r, column=1, sticky="w")
            elif f["kind"] == "combo":
                v = tk.StringVar()
                ttk.Combobox(self, textvariable=v, values=f.get("values", []),
                             width=34, font=F_ROW).grid(row=r, column=1, sticky="we", pady=3)
            else:
                v = tk.StringVar()
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
                     color=DONE, fg="#12151a").pack(side="right", padx=4)
        self.wait_visibility()
        self.grab_set()


class SwimTab(tk.Frame):
    def __init__(self, container):
        super().__init__(container, bg=BG)
        self._loading = False
        self._classes = []   # [{id,name,...,schedules:[...]}]
        self._slots = []     # [{id,time,note,used}]
        self._setup_style()
        self._build_static()
        self.after(200, self.refresh)

    # ---------- UI 骨架 ----------

    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Swim.Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=FG, font=F_ROW, rowheight=26, borderwidth=0)
        style.configure("Swim.Treeview.Heading", background=TRACK, foreground=FG,
                        font=F_SMALL, relief="flat")
        style.map("Swim.Treeview", background=[("selected", TRACK)])
        style.configure("TCombobox", fieldbackground=PANEL, background=TRACK,
                        foreground=FG)

    def _build_static(self):
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", pady=(14, 0), padx=18)
        tk.Label(head, text="游泳課表", bg=BG, fg=HEAD, font=F_TITLE,
                 anchor="w").pack(side="left")
        make_btn(head, "重新整理", self.refresh).pack(side="right")

        self.sum_lbl = tk.Label(self, text="載入中…", bg=BG, fg=FG, font=F_ROW,
                                anchor="w", wraplength=680, justify="left")
        self.sum_lbl.pack(fill="x", padx=18, pady=(4, 0))
        self.git_lbl = tk.Label(self, text="", bg=BG, fg=MUTED, font=F_SMALL, anchor="w")
        self.git_lbl.pack(fill="x", padx=18)

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=18, pady=(8, 4))
        for text, cmd in (("＋班級", self.act_add_class),
                          ("改班級", self.act_update_class),
                          ("刪班級", self.act_remove_class),
                          ("＋排課", self.act_add_schedule),
                          ("刪排課", self.act_remove_schedule),
                          ("單堂調整", self.act_lesson_menu)):
            make_btn(bar, text, cmd).pack(side="left", padx=(0, 6))

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=18)
        self.tree = ttk.Treeview(body, style="Swim.Treeview",
                                 columns=("detail",), show="tree headings")
        self.tree.heading("#0", text="班級 / 排課")
        self.tree.heading("detail", text="內容")
        self.tree.column("#0", width=220, stretch=False)
        self.tree.column("detail", width=430)
        sb = tk.Scrollbar(body, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", padx=18, pady=10)
        tk.Label(foot, text="commit 訊息（可空）", bg=BG, fg=MUTED,
                 font=F_SMALL).pack(side="left")
        self.commit_msg = tk.Entry(foot, width=30, bg=PANEL, fg=FG,
                                   insertbackground=FG, relief="flat", font=F_ROW)
        self.commit_msg.pack(side="left", padx=6)
        self.push_btn = make_btn(foot, "一鍵上線（render + push）", self.push_flow,
                                 color=DONE, fg="#12151a")
        self.push_btn.pack(side="left", padx=6)
        self.push_lbl = tk.Label(self, text="", bg=BG, fg=MUTED, font=F_SMALL,
                                 anchor="w", wraplength=680, justify="left")
        self.push_lbl.pack(fill="x", padx=18, pady=(0, 8))

    # ---------- 資料載入 ----------

    def refresh(self):
        if self._loading:
            return
        self._loading = True

        def work():
            try:
                status = run_cli(["status"])
                classes = run_cli(["list-classes", "--with-schedules"])
                slots = run_cli(["list-slots"])
                g_dirty = run_cmd(["git", "status", "--porcelain"])
                ahead = git_ahead()
                self.after(0, lambda: self._render(status, classes, slots, g_dirty, ahead))
            except Exception as ex:  # 例外若靜默吞掉，_loading 會卡 True、之後永遠不刷新
                self.after(0, lambda: self.sum_lbl.config(
                    text=f"✘ 載入失敗:{ex}", fg=BAD))
            finally:
                self._loading = False

        threading.Thread(target=work, daemon=True).start()

    def _render(self, status, classes, slots, g_dirty, ahead):
        d = status.get("data") or {}
        week = d.get("upcoming_7d") or []
        conflicts = [e for e in status.get("errors") or []
                     if e.get("code") == "E_TIME_OVERLAP"]
        orphan = d.get("orphan_classes") or []
        parts = [f"未來 7 天 {len(week)} 堂"]
        parts.append(f"衝突 {len(conflicts)}" if conflicts else "無衝突")
        if orphan:
            parts.append(f"孤兒班 {len(orphan)}（{','.join(orphan)}）")
        next_lesson = week[0] if week else None
        if next_lesson:
            parts.append(f"下一堂：{next_lesson['date']} {next_lesson['slot_time']} "
                         f"{next_lesson['class_name']}")
        self.sum_lbl.config(text="　·　".join(parts),
                            fg=BAD if conflicts else FG)

        dirty = len([ln for ln in (g_dirty.stdout or "").splitlines() if ln.strip()])
        bits = []
        if dirty:
            bits.append(f"未 commit 變更 {dirty} 檔")
        if ahead:
            bits.append(f"領先 remote {ahead} commit")
        self.git_lbl.config(text="git：" + ("　·　".join(bits) if bits else "已同步"),
                            fg=PROG if bits else MUTED)

        self._classes = (classes.get("data") or {}).get("classes") or []
        self._slots = (slots.get("data") or {}).get("slots") or []
        self.tree.delete(*self.tree.get_children())
        for c in self._classes:
            label = f"{c.get('id')}　{c.get('name') or ''}"
            detail = f"每週 {c.get('weekly_count')} 堂" + \
                     (f"　·　{c.get('level')}" if c.get("level") else "")
            node = self.tree.insert("", "end", text=label, values=(detail,), open=True)
            for s in c.get("schedules") or []:
                self.tree.insert(node, "end", text=s.get("id") or "（排課）",
                                 values=(sched_summary(s),))

    # ---------- 共用選項 ----------

    def _class_values(self):
        return [f"{c['id']}{SEP}{c.get('name') or ''}" for c in self._classes]

    def _slot_values(self):
        return [f"{s['id']}{SEP}{s.get('time') or ''}" for s in self._slots]

    # ---------- 動作（全部 dry-run → 確認 → apply）----------

    def _form_then_run(self, title, cmd, fields):
        dlg = FormDialog(self, title, fields)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        args = [cmd]
        for flag, val in dlg.result.items():
            args += [flag] if val is True else [flag, val]
        resp = run_cli(args)
        ConfirmDialog(self, title, resp,
                      on_confirm=lambda: self._apply(title, args))

    def _apply(self, title, args):
        resp = run_cli(args + ["--apply"])
        ConfirmDialog(self, title + "（已寫入）" if resp.get("ok") else title, resp)
        self.refresh()

    def act_add_class(self):
        self._form_then_run("新增班級", "add-class", [
            {"flag": "--id", "label": "班級 ID", "kind": "entry", "required": True,
             "hint": "例 STU-12"},
            {"flag": "--name", "label": "學員 / 班名", "kind": "entry", "required": True},
            {"flag": "--weekly-count", "label": "每週堂數", "kind": "entry",
             "required": True, "hint": "整數"},
            {"flag": "--level", "label": "程度", "kind": "entry"},
            {"flag": "--note", "label": "備註", "kind": "entry"},
        ])

    def act_update_class(self):
        self._form_then_run("修改班級（空欄不改）", "update-class", [
            {"flag": "--id", "label": "班級", "kind": "combo", "required": True,
             "values": self._class_values()},
            {"flag": "--name", "label": "學員 / 班名", "kind": "entry"},
            {"flag": "--weekly-count", "label": "每週堂數", "kind": "entry"},
            {"flag": "--level", "label": "程度", "kind": "entry"},
            {"flag": "--note", "label": "備註", "kind": "entry"},
        ])

    def act_remove_class(self):
        self._form_then_run("刪除班級", "remove-class", [
            {"flag": "--id", "label": "班級", "kind": "combo", "required": True,
             "values": self._class_values()},
            {"flag": "--cascade", "label": "連帶刪其排課", "kind": "check"},
        ])

    def act_add_schedule(self):
        self._form_then_run("新增排課", "add-schedule", [
            {"flag": "--class", "label": "班級", "kind": "combo", "required": True,
             "values": self._class_values()},
            {"flag": "--slot", "label": "常用時段", "kind": "combo",
             "values": self._slot_values(), "hint": "或改填下欄自訂時段"},
            {"flag": "--time", "label": "自訂時段", "kind": "entry",
             "hint": "HH:MM-HH:MM（與常用時段擇一）"},
            {"flag": "--days", "label": "每週幾", "kind": "entry",
             "hint": "mon,tue,wed,thu,fri,sat,sun（逗號分隔可多天）"},
            {"flag": "--specific-dates", "label": "或指定日期", "kind": "entry",
             "hint": "YYYY-MM-DD 逗號分隔（與每週幾擇一）"},
            {"flag": "--start", "label": "開始日", "kind": "entry",
             "hint": "YYYY-MM-DD（每週制必填）"},
            {"flag": "--weeks", "label": "持續週數", "kind": "entry",
             "hint": "週數 / 結束日 / 總堂數 三擇一"},
            {"flag": "--end", "label": "結束日", "kind": "entry"},
            {"flag": "--lessons", "label": "總堂數", "kind": "entry"},
            {"flag": "--note", "label": "備註", "kind": "entry"},
        ])

    def act_remove_schedule(self):
        self._form_then_run("刪除排課", "remove-schedule", [
            {"flag": "--class", "label": "班級", "kind": "combo", "required": True,
             "values": self._class_values()},
            {"flag": "--slot-id", "label": "限定時段", "kind": "combo",
             "values": self._slot_values(), "hint": "命中多條時用來縮小範圍"},
            {"flag": "--day", "label": "限定星期", "kind": "entry", "hint": "mon/tue/…"},
            {"flag": "--all", "label": "允許一次刪多條", "kind": "check"},
        ])

    def act_lesson_menu(self):
        menu = tk.Menu(self, tearoff=0, bg=PANEL, fg=FG,
                       activebackground=TRACK, activeforeground=FG, font=F_ROW)
        menu.add_command(label="挪課 / 補課（換日或換時段）", command=self.act_move_lesson)
        menu.add_command(label="取消一堂（不補）", command=self.act_cancel_lesson)
        menu.add_command(label="臨時加一堂（單日）", command=self.act_add_lesson)
        menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def act_move_lesson(self):
        self._form_then_run("挪課 / 補課", "move-lesson", [
            {"flag": "--class", "label": "班級", "kind": "combo", "required": True,
             "values": self._class_values()},
            {"flag": "--from-date", "label": "原上課日", "kind": "entry",
             "required": True, "hint": "YYYY-MM-DD"},
            {"flag": "--to-date", "label": "改到哪天", "kind": "entry",
             "required": True, "hint": "YYYY-MM-DD"},
            {"flag": "--to-slot", "label": "改時段（常用）", "kind": "combo",
             "values": self._slot_values(), "hint": "不換就留空"},
            {"flag": "--to-time", "label": "改時段（自訂）", "kind": "entry",
             "hint": "HH:MM-HH:MM"},
            {"flag": "--note", "label": "備註", "kind": "entry",
             "hint": "例：颱風停課改週六補"},
        ])

    def act_cancel_lesson(self):
        self._form_then_run("取消一堂（不補）", "cancel-lesson", [
            {"flag": "--class", "label": "班級", "kind": "combo", "required": True,
             "values": self._class_values()},
            {"flag": "--date", "label": "取消日期", "kind": "entry",
             "required": True, "hint": "YYYY-MM-DD"},
            {"flag": "--reason", "label": "原因", "kind": "entry",
             "hint": "教練生病 / 學員請假 …"},
        ])

    def act_add_lesson(self):
        self._form_then_run("臨時加一堂（單日）", "add-lesson", [
            {"flag": "--class", "label": "班級", "kind": "combo", "required": True,
             "values": self._class_values()},
            {"flag": "--date", "label": "日期", "kind": "entry",
             "required": True, "hint": "YYYY-MM-DD"},
            {"flag": "--slot", "label": "時段（常用）", "kind": "combo",
             "values": self._slot_values()},
            {"flag": "--time", "label": "時段（自訂）", "kind": "entry",
             "hint": "HH:MM-HH:MM"},
            {"flag": "--note", "label": "備註", "kind": "entry"},
        ])

    # ---------- 一鍵上線 ----------

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
    root.geometry("760x820")
    root.minsize(620, 640)
    build_tab(root)
    root.mainloop()


if __name__ == "__main__":
    main()
