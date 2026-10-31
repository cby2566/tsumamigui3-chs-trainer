"""通用的「受 C_INT 保护的数值」面板。

金币和精力走的是同一套机制（都是 C_GAME_PARAMETER 里的 C_INT，key 都是 0），
只有上限不同，所以共用一个面板：金币 0~999999，精力 0~20。
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import messagebox, ttk

from winmem import WinMemError

RECOVER_TIMEOUT = 30.0       # 后台重定位超过这个秒数就算卡住，强制放行重试


class ValuePanel(ttk.Frame):
    def __init__(self, master, app, kind: str, label: str, max_value: int,
                 hint: str = "", show_fill: bool = True, describe=None):
        super().__init__(master, padding=12)
        self.app = app
        self.session = app.session
        self.kind = kind
        self.label = label
        self.max_value = max_value
        self.show_fill = show_fill
        self.describe = describe          # 可选：把数值转成人能读的说明（比如天数 -> 月日）
        self.lock_target: int | None = None
        self._last_good: int | None = None
        self._recovering = False
        self._recover_at = 0.0
        self._fail_streak = 0            # 连续解码失败次数（去抖用）
        self._by_addr: dict[int, str] = {}
        self._suppress = False           # 程序自动换行时，别重复刷「选中」日志
        self._warned_stale = False       # 「界面可能没刷新」只提一次
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
        if self.show_fill:
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

        requested = value
        # 改过内存之后，游戏界面未必立刻重绘：界面还停在旧数字，内存里已经是新值。
        # 这时候照界面填的数字一定搜不到，人就会以为「地址失效了」。
        # 所以先按用户填的搜，搜不到再用已绑定地址当前解出的真实值兜底。
        actual = self._bound_value()
        fallback = actual if actual is not None and actual != requested else None

        def job():
            cands = self.session.find(self.kind, requested)
            if cands or fallback is None:
                return cands, requested, requested
            return self.session.find(self.kind, fallback), requested, fallback

        self.app.run_bg(job, lambda r: self._show_candidates(*r),
                        busy=f"正在扫描{self.label}…")

    def _show_text(self, value: int) -> str:
        """数值 + 可选的可读说明，比如 216（8月4日）。"""
        if self.describe is not None:
            try:
                return f"{value}（{self.describe(value)}）"
            except Exception:                             # noqa: BLE001
                pass
        return str(value)

    def _render(self, cands) -> None:
        """重建候选列表，并记住地址 → 行号的映射（用来按地址回选）。"""
        self.tree.delete(*self.tree.get_children())
        self._by_addr = {}
        for c in cands:
            iid = self.tree.insert("", "end", values=(
                f"0x{c.address:08X}", c.value, f"{c.cord}/{c.backup_pattern}",
                "校验码自洽" + ("，前面还有 C_INT" if c.has_neighbour_cint else "")))
            self._by_addr[c.address] = iid

    def _select_addr(self, addr: int) -> None:
        iid = self._by_addr.get(addr)
        if iid:
            self._suppress = True
            try:
                self.tree.selection_set(iid)
                self.tree.focus(iid)
            finally:
                self._suppress = False
        self.session.select(self.kind, addr)

    def _show_candidates(self, cands, requested: int, used: int) -> None:
        if not cands:
            actual = self._bound_value()
            if actual is not None:
                self.app.log(f"没找到{self.label}候选（按 {used} 找）；"
                             f"工具读到的实际值是 {actual}")
            else:
                self.app.log(f"没找到{self.label}候选：确认填的数字和游戏里显示的一致")
            # 一次扫空不代表原来的地址没了，别把已绑定的地址从列表里抹掉
            return
        if used != requested:
            self.app.log(f"{self.label}：按界面值 {requested} 搜不到，但内存里实际已经是 "
                         f"{used}（游戏界面可能还没刷新），已按 {used} 定位")
            self._warned_stale = True
        self._render(cands)
        # 同一个逻辑值可能存在多份副本，换到副本上写容易被真身覆盖回去：
        # 原地址还在候选里就继续用它。
        keep = self.session.address(self.kind)
        pick = next((c for c in cands if c.address == keep), cands[0])
        self._select_addr(pick.address)
        self.app.log(f"{self.label}找到 {len(cands)} 个候选，选中 {pick}")

    def _selected_addr(self) -> int | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return int(self.tree.item(sel[0], "values")[0], 16)

    def _on_pick(self) -> None:
        addr = self._selected_addr()
        if addr is not None:
            self.session.select(self.kind, addr)
            if self._suppress:
                return
            cur = self.session.current(self.kind)
            if cur is not None:
                self.app.log(f"{self.label}选中 0x{addr:08X}，当前值 {cur.decoded_loose}")

    def _bound_value(self) -> int | None:
        """已绑定地址当前解码出的值；没绑定、或解不出时返回 None。"""
        if self.session.address(self.kind) is None:
            return None
        cur = self.session.current(self.kind)
        return cur.decoded_loose if cur is not None else None

    def adopt_auto(self) -> None:
        """附加时如果已经自动定位到地址，直接铺到界面上，不用人再点「定位」。

        数据不自洽（解不出来）时照样把地址铺出来 —— 地址本身是对的，
        点一次「设为」会写回自洽的 16 字节，等于顺手把它修好。
        """
        addr = self.session.address(self.kind)
        if addr is None:
            return
        cur = self.session.current(self.kind)
        if cur is None:
            return
        self._render([cur])
        self._select_addr(addr)
        self._fail_streak = 0
        if cur.decoded_loose is None:
            self.current_entry.set("")
            self.live.configure(text="当前：数据不自洽（点「设为」写一次即可修复）")
        else:
            self.current_entry.set(str(cur.decoded_loose))
            self.live.configure(text=f"当前：{self._show_text(cur.decoded_loose)}")

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
        if cur is None or cur.decoded_loose is None:
            messagebox.showinfo("还没定位", f"先定位{self.label}再说")
            return
        self._apply(max(0, min(cur.decoded_loose + delta, self.max_value)))

    def _apply(self, value: int) -> None:
        # 列表可能被一次空扫描清掉，但 session 里绑定的地址仍然是有效的，
        # 这时不该反过来要求用户重新选一行。
        addr = self._selected_addr() or self.session.address(self.kind)
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
        if not self._warned_stale:
            self._warned_stale = True
            self.app.log(f"提示：游戏界面不一定会立刻重绘。界面还显示旧数字时，"
                         f"直接在本页继续改就行，别照着旧数字重新定位")
        self.lock_target = value if self.lock_var.get() else None

    def _on_lock_toggle(self) -> None:
        if not self.lock_var.get():
            self.lock_target = None
            self.app.log(f"{self.label}锁定已关闭")
            return
        addr = self._selected_addr() or self.session.address(self.kind)
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
            self._fail_streak = 0
            return
        cur = self.session.current(self.kind)
        shown = cur.decoded_loose if cur is not None else None
        if shown is None:
            # 游戏重写一个 C_INT 是分几次落盘的，读到中间态会「解不出来」。
            # 单次失败多半是撞上了那个瞬间，先别急着判死刑。
            self._fail_streak += 1
            if self._fail_streak < 3:
                return
            # 地址是按变量号拿到的吗？是的话它一定没错 —— 解不出来只能说明
            # C_INT 的数据坏了（主校验码和值对不上），点「设为」写一次就能修好。
            # 这种情况不能去「重新定位」，否则会陷进反复扫描。
            if self.kind in self.session.from_var_id:
                self.live.configure(text="当前：数据不自洽（点「设为」写一次即可修复）")
                return
            self.live.configure(text="当前：地址已失效")
            self._try_recover()
            return
        self._fail_streak = 0
        self._last_good = shown
        self.live.configure(text=f"当前：{self._show_text(shown)}")
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
        if self._recovering:
            # 后台扫描万一卡住（撞上刚被游戏释放的内存之类），
            # 别让面板永久锁在「正在重新定位」上 —— 超时就强制放行重试。
            if now - self._recover_at < RECOVER_TIMEOUT:
                return
            self._recovering = False
            self.app.log(f"{self.label}上次重新定位超时，重试")
        if self._last_good is None or now - self._recover_at < 5:
            return
        self._recovering = True
        self._recover_at = now
        value = self._last_good
        old_addr = self.session.address(self.kind)

        def job():
            try:
                return self.session.find(self.kind, value)
            except Exception as exc:                      # noqa: BLE001
                # 异常必须当结果带回去：run_bg 的错误分支不会调用 done()，
                # _recovering 就会一直是 True，面板永远停在「正在重新定位」。
                return exc

        def done(result) -> None:
            self._recovering = False
            if isinstance(result, Exception):
                self.app.log(f"{self.label}重新定位失败：{result}")
                return
            cands = result
            if not cands:
                self.app.log(f"{self.label}地址失效，且按上次的值 {value} 已找不到；"
                             f"请在游戏里看一眼当前{self.label}再重新定位")
                return
            # 同一个逻辑值可能同时存在几份副本（实测精力的两份连校验码都一模一样）。
            # 原地址还在候选里就继续绑它，换到副本上写很可能被真身覆盖回去。
            keep = next((c for c in cands if c.address == old_addr), None)
            if keep is None and len(cands) > 1:
                self.app.log(f"{self.label}地址失效，按 {value} 扫到 {len(cands)} 个候选，"
                             f"请到本页签点「定位」手动挑一个")
                return
            pick = keep or cands[0]
            self._render(cands)
            self._select_addr(pick.address)
            self.app.log(f"{self.label}地址短暂不可读，已恢复绑定 "
                         f"0x{pick.address:08X}（值 {pick.value}）")

        self.app.run_bg(job, done, busy=f"正在重新定位{self.label}…")
