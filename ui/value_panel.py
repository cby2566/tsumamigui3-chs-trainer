"""通用的「受 C_INT 保护的数值」面板。

金币和精力走的是同一套机制（都是 C_GAME_PARAMETER 里的 C_INT，key 都是 0），
只有上限不同，所以共用一个面板：金币 0~999999，精力 0~20。
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import messagebox, ttk

from winmem import WinMemError


class ValuePanel(ttk.Frame):
    def __init__(self, master, app, kind: str, label: str, max_value: int, hint: str = ""):
        super().__init__(master, padding=12)
        self.app = app
        self.session = app.session
        self.kind = kind
        self.label = label
        self.max_value = max_value
        self.lock_target: int | None = None
        self._last_good: int | None = None
        self._recovering = False
        self._recover_at = 0.0
        self._build(hint)

    # ---------- 界面 ----------

    def _build(self, hint: str) -> None:
        box = ttk.LabelFrame(self, text=f" 1. 定位{self.label} ", padding=10)
        box.pack(fill="x")
        ttk.Label(box, text=f"游戏里现在显示的{self.label}").pack(side="left")
        self.current_entry = tk.StringVar()
        ttk.Entry(box, textvariable=self.current_entry, width=12).pack(side="left", padx=8)
        ttk.Button(box, text="定位", command=self.on_locate).pack(side="left")
        ttk.Button(box, text="重新扫描", command=self.on_locate).pack(side="left", padx=6)
        ttk.Label(box, text=f"（范围 0 ~ {self.max_value}）" + hint,
                  style="Hint.TLabel").pack(side="left", padx=6)

        mid = ttk.LabelFrame(self, text=" 2. 候选地址 ", padding=8)
        mid.pack(fill="both", expand=True, pady=10)
        cols = ("addr", "value", "pattern", "note")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings",
                                 selectmode="browse", height=5)
        for c, t, w in (("addr", "地址", 140), ("value", "值", 110),
                        ("pattern", "pattern", 110), ("note", "备注", 260)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_pick())

        act = ttk.LabelFrame(self, text=f" 3. 修改{self.label} ", padding=10)
        act.pack(fill="x")
        ttk.Label(act, text="目标值").pack(side="left")
        self.target = tk.StringVar(value=str(self.max_value))
        ttk.Entry(act, textvariable=self.target, width=12).pack(side="left", padx=8)
        ttk.Button(act, text="设为", width=7, command=self.on_set).pack(side="left", padx=2)
        ttk.Button(act, text="加满", width=7,
                   command=lambda: self._apply(self.max_value)).pack(side="left", padx=2)
        ttk.Button(act, text="+1", width=5,
                   command=lambda: self._apply_delta(1)).pack(side="left", padx=2)
        ttk.Button(act, text="-1", width=5,
                   command=lambda: self._apply_delta(-1)).pack(side="left", padx=2)
        self.lock_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(act, text="锁定（自动回写）", variable=self.lock_var,
                        command=self._on_lock_toggle).pack(side="left", padx=14)
        self.live = ttk.Label(act, text="当前：—", style="Hint.TLabel")
        self.live.pack(side="left", padx=6)

    # ---------- 定位 ----------

    def on_locate(self) -> None:
        text = self.current_entry.get().strip()
        if not text:
            messagebox.showinfo("还差一步", f"先把游戏里当前显示的{self.label}数字填进来")
            return
        try:
            value = int(text, 0)
        except ValueError:
            messagebox.showwarning("数值不对", f"{self.label}要填整数")
            return

        def job():
            return self.session.find(self.kind, value)

        self.app.run_bg(job, self._show_candidates, busy=f"正在扫描{self.label}…")

    def _show_candidates(self, cands) -> None:
        self.tree.delete(*self.tree.get_children())
        for c in cands:
            self.tree.insert("", "end", values=(
                f"0x{c.address:08X}", c.value, f"{c.cord}/{c.backup_pattern}",
                "校验码自洽" + ("，前面还有 C_INT" if c.has_neighbour_cint else "")))
        if not cands:
            self.app.log(f"没找到{self.label}候选：确认填的数字和游戏里显示的一致")
            return
        first = self.tree.get_children()[0]
        self.tree.selection_set(first)
        self.session.select(self.kind, cands[0].address)
        self.app.log(f"{self.label}找到 {len(cands)} 个候选，已选中第一个：{cands[0]}")

    def _selected_addr(self) -> int | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return int(self.tree.item(sel[0], "values")[0], 16)

    def _on_pick(self) -> None:
        addr = self._selected_addr()
        if addr is not None:
            self.session.select(self.kind, addr)
            cur = self.session.current(self.kind)
            if cur is not None:
                self.app.log(f"{self.label}选中 0x{addr:08X}，当前值 {cur.decoded}")

    # ---------- 修改 ----------

    def on_set(self) -> None:
        text = self.target.get().strip()
        try:
            value = int(text, 0)
        except ValueError:
            messagebox.showwarning("数值不对", "目标值要填整数")
            return
        self._apply(value)

    def _apply_delta(self, delta: int) -> None:
        cur = self.session.current(self.kind)
        if cur is None or cur.decoded is None:
            messagebox.showinfo("还没定位", f"先定位{self.label}再说")
            return
        self._apply(max(0, min(cur.decoded + delta, self.max_value)))

    def _apply(self, value: int) -> None:
        addr = self._selected_addr()
        if addr is None:
            messagebox.showinfo("没选目标", "先在候选列表里选一个地址")
            return
        self.session.select(self.kind, addr)
        try:
            msg = self.session.set_value(self.kind, value)
        except WinMemError as e:
            messagebox.showerror("写入失败", str(e))
            return
        self.app.log(f"{self.label}已改：" + msg)
        self.lock_target = value if self.lock_var.get() else None

    def _on_lock_toggle(self) -> None:
        if not self.lock_var.get():
            self.lock_target = None
            self.app.log(f"{self.label}锁定已关闭")
            return
        addr = self._selected_addr()
        if addr is None:
            messagebox.showinfo("没选目标", "锁定前先选一个候选地址")
            self.lock_var.set(False)
            return
        self.session.select(self.kind, addr)
        try:
            self.lock_target = int(self.target.get().strip(), 0)
        except ValueError:
            self.lock_target = self.max_value
        self.app.log(f"{self.label}已开启锁定：保持 {self.lock_target}")

    # ---------- 定时刷新 ----------

    def tick(self) -> None:
        if not self.session.attached or self.session.address(self.kind) is None:
            return
        cur = self.session.current(self.kind)
        if cur is None:
            self.live.configure(text="当前：地址已失效")
            self._try_recover()
            return
        shown = cur.decoded
        if shown is None:
            # 堆对象会被回收/搬家，表现为原地址不再是合法 C_INT
            self.live.configure(text="当前：地址已失效")
            self._try_recover()
            return
        self._last_good = shown
        self.live.configure(text=f"当前：{shown}")
        if self.lock_target is not None and shown != self.lock_target:
            try:
                self.session.set_value(self.kind, self.lock_target)
            except WinMemError:
                self.lock_var.set(False)
                self.lock_target = None
                self.app.log(f"{self.label}锁定已自动关闭：写入失败")

    def _try_recover(self) -> None:
        """地址失效时，用最后一次已知的值重新扫一遍（限流 5 秒一次）。"""
        now = time.time()
        if self._recovering or self._last_good is None or now - self._recover_at < 5:
            return
        self._recovering = True
        self._recover_at = now
        value = self._last_good

        def job():
            return self.session.find(self.kind, value)

        def done(cands) -> None:
            self._recovering = False
            if len(cands) == 1:
                self.session.select(self.kind, cands[0].address)
                self.app.log(f"{self.label}地址已失效，自动重新定位到 "
                             f"0x{cands[0].address:08X}（值 {value}）")
            elif cands:
                self.app.log(f"{self.label}地址失效，按 {value} 扫到 {len(cands)} 个候选，"
                             f"请到本页签点「定位」手动挑一个")
            else:
                self.app.log(f"{self.label}地址失效，且按上次的值 {value} 已找不到；"
                             f"请在游戏里看一眼当前{self.label}再重新定位")

        self.app.run_bg(job, done, busy=f"正在重新定位{self.label}…")
