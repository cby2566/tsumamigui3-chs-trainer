"""通用数值搜索面板：首扫 / 再筛 / 写回（简化版 Cheat Engine）。"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from scan import KIND_LABEL, Result
from winmem import WinMemError

DISPLAY_LIMIT = 400          # 结果表最多渲染这么多行，避免 Treeview 卡死

FILTERS = [
    ("等于", "eq"), ("不等于", "ne"),
    ("大于", "gt"), ("小于", "lt"),
    ("变大了", "increased"), ("变小了", "decreased"),
    ("没变", "unchanged"), ("变了", "changed"),
]


class ScanPanel(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=10)
        self.app = app
        self.session = app.session
        self._build()

    # ---------- 界面 ----------

    def _build(self) -> None:
        top = ttk.LabelFrame(self, text=" 扫描条件 ", padding=8)
        top.pack(fill="x")

        ttk.Label(top, text="类型").grid(row=0, column=0, sticky="w")
        self.kind = tk.StringVar(value=KIND_LABEL["i32"])
        ttk.Combobox(top, textvariable=self.kind, width=14, state="readonly",
                     values=list(KIND_LABEL.values())).grid(
            row=1, column=0, padx=(0, 10), sticky="w")

        ttk.Label(top, text="数值").grid(row=0, column=1, sticky="w")
        self.value = tk.StringVar()
        ttk.Entry(top, textvariable=self.value, width=18).grid(row=1, column=1, padx=(0, 10))

        self.btn_first = ttk.Button(top, text="首次扫描", command=self.on_first)
        self.btn_first.grid(row=1, column=2, padx=4)

        ttk.Label(top, text="再筛条件").grid(row=0, column=3, sticky="w")
        self.filter = tk.StringVar(value="变大了")
        ttk.Combobox(top, textvariable=self.filter, width=10, state="readonly",
                     values=[f[0] for f in FILTERS]).grid(row=1, column=3, padx=(10, 10))

        self.btn_refine = ttk.Button(top, text="再次扫描", command=self.on_refine)
        self.btn_refine.grid(row=1, column=4, padx=4)
        self.btn_refine.state(["disabled"])

        ttk.Button(top, text="清空", command=self.on_clear).grid(row=1, column=5, padx=4)

        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True, pady=(10, 0))

        cols = ("addr", "value", "kind")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        for c, t, w in (("addr", "地址", 130), ("value", "当前值", 180), ("kind", "类型", 110)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        bottom = ttk.LabelFrame(self, text=" 写回 ", padding=8)
        bottom.pack(fill="x", pady=(10, 0))
        self.new_value = tk.StringVar()
        ttk.Entry(bottom, textvariable=self.new_value, width=18).pack(side="left")
        ttk.Button(bottom, text="写入选中行", command=self.on_write).pack(side="left", padx=8)
        self.hint = ttk.Label(bottom, text="先「首次扫描」再「再次扫描」，一般两三轮就能收敛",
                              foreground="#666")
        self.hint.pack(side="left", padx=10)

    def _kind_key(self) -> str:
        """Combobox 显示的是中文，内部用的是 kind 键。"""
        cur = self.kind.get()
        for k, v in KIND_LABEL.items():
            if v == cur:
                return k
        return cur if cur in KIND_LABEL else "i32"

    def _parse(self, text: str, kind: str):
        if kind.startswith("f"):
            return float(text)
        return int(text, 0)

    # ---------- 动作 ----------

    def on_first(self) -> None:
        kind = self._kind_key()
        try:
            v = self._parse(self.value.get().strip(), kind)
        except ValueError:
            messagebox.showwarning("数值不对", "请填一个合法的数字")
            return

        def job():
            return self.session.first_scan(v, kind)

        self.app.run_bg(job, self._show_result, busy="首次扫描中…",
                        on_done=lambda: self.btn_refine.state(["!disabled"]))

    def on_refine(self) -> None:
        label = self.filter.get()
        mode = next((m for t, m in FILTERS if t == label), "changed")
        kind = self._kind_key()
        val = None
        if mode in ("eq", "ne", "gt", "lt"):
            try:
                val = self._parse(self.value.get().strip(), kind)
            except ValueError:
                messagebox.showwarning("数值不对", "这个筛选条件需要填数值")
                return

        def job():
            return self.session.refine(mode, val)

        self.app.run_bg(job, self._show_result, busy="再次扫描中…")

    def _show_result(self, res: Result) -> None:
        self.tree.delete(*self.tree.get_children())
        n = len(res)
        for i in range(min(n, DISPLAY_LIMIT)):
            self.tree.insert("", "end", values=(
                f"0x{int(res.addrs[i]):08X}", res.vals[i],
                KIND_LABEL.get(res.kind, res.kind)))
        self.hint.configure(
            text=f"共 {n} 个候选" + (f"，只显示前 {DISPLAY_LIMIT} 个" if n > DISPLAY_LIMIT else ""))

    def on_write(self) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("没选目标", "先在列表里选一行")
            return
        addr_text = self.tree.item(sel[0], "values")[0]
        addr = int(addr_text, 16)
        kind = self._kind_key()
        try:
            v = self._parse(self.new_value.get().strip(), kind)
        except ValueError:
            messagebox.showwarning("数值不对", "写回值必须是数字")
            return
        try:
            msg = self.session.write_value(addr, v, kind)
        except WinMemError as e:
            messagebox.showerror("写入失败", str(e))
            return
        self.app.log(msg)

    def on_clear(self) -> None:
        self.session.last_result = None
        self.tree.delete(*self.tree.get_children())
        self.hint.configure(text="已清空，可以重新首次扫描")
        self.btn_refine.state(["disabled"])
