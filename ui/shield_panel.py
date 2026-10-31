"""愛盾开关面板：海夕里 / 砂夜子的布尔 C_INT。

游戏规则里愛盾只有 0/1 两种状态（1=盾在，0=盾破），而且只有这两位女主有盾。
C_INT 子页不是连续排布的，所以定位方式不是「按偏移算」，而是：

    扫描所有值为 0/1、校验码自洽的 C_INT
    → 列出邻域里的其他 C_INT（女主记录的特征）
    → 试写 1/0，让用户回游戏确认是哪一位
    → 确认后绑定，之后勾选框直接读写。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from winmem import WinMemError

HEROINES = [
    ("shield_kaede", "海夕里"),
    ("shield_sayoko", "砂夜子"),
]


class ShieldPanel(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=12)
        self.app = app
        self.session = app.session
        self.rows: dict[str, ShieldRow] = {}
        self._build()

    def _build(self) -> None:
        ttk.Label(
            self,
            text="愛盾是布尔值：1 = 盾在，0 = 盾破。只有海夕里、砂夜子有盾。",
            style="Hint.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            self,
            text="第一次用需要「绑定」：扫描候选 → 试写 → 回游戏看是谁的盾 → 绑定。之后勾选框即可直接开关。",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(2, 10))

        for kind, label in HEROINES:
            row = ShieldRow(self, self.app, kind, label)
            row.pack(fill="x", pady=(0, 8))
            self.rows[kind] = row

        ttk.Label(
            self,
            text="提示：地址只在当前游戏进程内有效；重启游戏后需要重新绑定。",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(4, 0))

    def tick(self) -> None:
        for row in self.rows.values():
            try:
                row.tick()
            except Exception:                     # noqa: BLE001
                pass


class ShieldRow(ttk.LabelFrame):
    def __init__(self, master, app, kind: str, label: str):
        super().__init__(master, text=f" {label} ", padding=10)
        self.app = app
        self.session = app.session
        self.kind = kind
        self.label = label

        self.check_var = tk.BooleanVar(value=False)
        self.check = ttk.Checkbutton(
            self, text="愛盾（盾在）", variable=self.check_var,
            command=self.on_toggle)
        self.check.pack(side="left")

        self.live = ttk.Label(self, text="当前：未绑定", style="Hint.TLabel")
        self.live.pack(side="left", padx=12)

        ttk.Button(self, text="绑定…", command=self.on_bind).pack(side="right")
        ttk.Button(self, text="解除", command=self.on_unbind).pack(
            side="right", padx=6)
        self.check.state(["disabled"])

    # ---------- 绑定 ----------

    def on_bind(self) -> None:
        if not self.session.attached:
            messagebox.showinfo("还没附加", "先点右上角「附加游戏」")
            return

        def job():
            return self.session.find_bool_candidates()

        def done(cands) -> None:
            if not cands:
                messagebox.showinfo(
                    "没找到",
                    "没有找到值为 0/1 的合法 C_INT。\n"
                    "请确认游戏还开着；如果刚重启过，先重新定位金币/精力再试。")
                return
            self.app.log(f"{self.label}愛盾：找到 {len(cands)} 个 0/1 候选")
            ShieldPicker(self, self.app, self.kind, self.label, cands)

        self.app.run_bg(job, done, busy="正在扫描愛盾候选…（约 20 秒）")

    def on_unbind(self) -> None:
        self.session.unbind(self.kind)
        self.check_var.set(False)
        self.check.state(["disabled"])
        self.live.configure(text="当前：未绑定")
        self.app.log(f"{self.label}愛盾已解除绑定")

    def _on_bound(self, value: int) -> None:
        self.app.log(f"{self.label}愛盾已绑定，当前值 {value}（1=盾在，0=盾破）")
        self.tick()

    # ---------- 开关 ----------

    def on_toggle(self) -> None:
        want = bool(self.check_var.get())
        try:
            msg = self.session.set_bool(self.kind, want)
        except WinMemError as exc:
            self.check_var.set(not want)
            messagebox.showerror("写入失败", str(exc))
            return
        self.app.log(f"{self.label}愛盾 → {'盾在' if want else '盾破'}：{msg}")

    # ---------- 定时刷新 ----------

    def tick(self) -> None:
        if not self.session.attached or self.session.address(self.kind) is None:
            self.check.state(["disabled"])
            self.live.configure(text="当前：未绑定")
            return
        cur = self.session.current(self.kind)
        if cur is None or cur.decoded not in (0, 1):
            self.check.state(["disabled"])
            self.live.configure(text="当前：地址失效，请重新绑定")
            return
        on = cur.decoded == 1
        self.check.state(["!disabled"])
        self.check_var.set(on)
        addr = self.session.address(self.kind)
        self.live.configure(
            text=f"当前：{'盾在' if on else '盾破'}  (0x{addr:08X})")


class ShieldPicker(tk.Toplevel):
    """候选列表 + 试写。绑定前必须先让用户确认是哪一位女主的盾。"""

    def __init__(self, master: ShieldRow, app, kind: str, label: str, cands):
        super().__init__(master)
        self.master_row = master
        self.app = app
        self.session = app.session
        self.kind = kind
        self.label = label
        # 孤立候选（邻域里没有别的 C_INT）大概率不是女主记录，先折叠掉；
        # 实在只剩孤立候选时再全部显示。
        filtered = [c for c in cands if c.neighbours]
        self.cands = filtered or list(cands)
        self.cands.sort(key=lambda c: (
            -sum(1 for v in c.near_values if 0 <= v <= 10),
            -len(c.neighbours), c.address))
        self._by_iid: dict[str, object] = {}
        self._tested: dict[int, int] = {}   # 试写过的地址 -> 原值
        self._bound = False
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self.title(f"绑定 {label} 的愛盾")
        self.geometry("820x430")
        self.minsize(720, 360)
        self.transient(master.winfo_toplevel())
        self._build()
        self.update_idletasks()
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def _build(self) -> None:
        ttk.Label(
            self,
            text=("候选是「解码为 0/1」的 C_INT。选中一行先试写，再切回游戏看状态栏："
                  "只有愛盾图标变了才是对的；如果变的是爱等级或其他数值，说明选错了，换一个候选。"
                  "试写会立即改内存；取消或关闭窗口会自动还原。"),
            style="Hint.TLabel", wraplength=770, justify="left",
        ).pack(anchor="w", padx=12, pady=(10, 4))

        cols = ("addr", "value", "near", "note")
        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True, padx=12, pady=6)
        self.tree = ttk.Treeview(mid, columns=cols, show="headings",
                                 selectmode="browse")
        for c, t, w in (("addr", "地址", 130), ("value", "当前值", 70),
                        ("near", "邻近 C_INT 值", 300), ("note", "备注", 250)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        for cand in self.cands:
            near = " ".join(str(v) for v in cand.near_values[:10]) or "-"
            small = sum(1 for v in cand.near_values if 0 <= v <= 10)
            note = f"{len(cand.neighbours)} 个邻近 C_INT"
            if small >= 2:
                note += "，像女主记录"
            iid = self.tree.insert("", "end", values=(
                f"0x{cand.address:08X}", cand.value, near, note))
            self._by_iid[iid] = cand

        first = self.tree.get_children()
        if first:
            self.tree.selection_set(first[0])
            self.tree.focus(first[0])

        self.status = ttk.Label(self, text="尚未试写", style="Hint.TLabel")
        self.status.pack(anchor="w", padx=12)

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=12, pady=10)
        ttk.Button(bar, text="试写 1（盾在）",
                   command=lambda: self._test(1)).pack(side="left")
        ttk.Button(bar, text="试写 0（盾破）",
                   command=lambda: self._test(0)).pack(side="left", padx=6)
        ttk.Button(bar, text="还原试写",
                   command=self._restore).pack(side="left", padx=(14, 0))
        ttk.Button(bar, text="绑定选中",
                   command=self._bind).pack(side="right")
        ttk.Button(bar, text="取消",
                   command=self.destroy).pack(side="right", padx=6)

    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return self._by_iid.get(sel[0])

    def _test(self, value: int) -> None:
        cand = self._selected()
        if cand is None:
            messagebox.showinfo("没选目标", "先选中一行候选")
            return
        old = self.session.read_bool_at(cand.address)
        if old is None:
            messagebox.showwarning("地址失效", "这个候选已经不是 0/1 了，请重新扫描")
            return
        if cand.address not in self._tested:
            self._tested[cand.address] = old
        try:
            got = self.session.test_bool(cand.address, value)
        except WinMemError as exc:
            messagebox.showerror("试写失败", str(exc))
            return
        self.status.configure(
            text=f"已试写 0x{cand.address:08X} = {got}；请切回游戏看状态栏确认。"
                 f"关闭窗口会自动还原。")

    def _restore(self) -> None:
        """把试写过的地址恢复原值；用户没绑定就关窗口时也会走这里。"""
        for addr, old in list(self._tested.items()):
            try:
                self.session.test_bool(addr, old)
            except Exception:                     # noqa: BLE001
                pass
        self._tested.clear()
        try:
            self.status.configure(text="已还原试写")
        except tk.TclError:
            pass

    def _bind(self) -> None:
        cand = self._selected()
        if cand is None:
            messagebox.showinfo("没选目标", "先选中一行候选")
            return
        try:
            value = self.session.bind_bool(self.kind, cand.address)
        except WinMemError as exc:
            messagebox.showerror("绑定失败", str(exc))
            return
        # 绑定目标保留当前试写结果；其他试写过的候选恢复原值。
        for addr, old in list(self._tested.items()):
            if addr == cand.address:
                continue
            try:
                self.session.test_bool(addr, old)
            except Exception:                     # noqa: BLE001
                pass
        self._tested.clear()
        self._bound = True
        self.master_row._on_bound(value)
        self.destroy()

    def destroy(self) -> None:
        if not getattr(self, "_bound", False):
            self._restore()
        super().destroy()
