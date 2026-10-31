"""愛盾面板：布尔 C_INT —— 0=盾破、1=盾在。

2026-11-01 复核结论：
* 愛盾是**布尔**，不是三态。曾一度按 0/1/2 三态处理，已撤回。
  证据一：C_HEROINE_PARAMETER_BASE@SetSeed 内部是
          Clamp(nValue, 0, nLimit)，nLimit 由 ■ヒロイン愛盾上限取得 给出；
  证据二：该上限函数对海夕里/砂夜子（键 0x3F1/0x3F2）返回 1，藍/菫返回 0，
          参数 nLevel 根本没被使用；
  证据三：28 个存档、112 条女主记录里 m_nSeed 只出现过 0 和 1。
* 海夕里 = 变量号 8729 (0x2219)，已接入 vmslot 自动定位。
* 砂夜子的变量号还没找到，可直接改存档（离线改档）绕过。
* 外部写内存不会触发游戏重绘：写完后要关掉游戏状态栏再重开才能看到新值。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from winmem import WinMemError

SHIELD_LABELS = ("盾破", "盾在")   # 下标 = 内存值
SHIELD_MAX = 1


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
            text=("愛盾只有两种状态：盾破 / 盾在。海夕里走变量号自动定位；"
                  "砂夜子的变量号还没找到（可直接改存档绕过）。"
                  "改完内存游戏画面不会自动刷新——关掉状态栏再重开才能看到。"),
            style="Hint.TLabel", wraplength=820, justify="left",
        ).pack(anchor="w", pady=(0, 10))

        kaede = ShieldRow(self, self.app, "shield_kaede", "海夕里", known=True)
        kaede.pack(fill="x", pady=(0, 8))
        sayoko = ShieldRow(self, self.app, "shield_sayoko", "砂夜子", known=False)
        sayoko.pack(fill="x", pady=(0, 8))
        self.rows = {"shield_kaede": kaede, "shield_sayoko": sayoko}

    def tick(self) -> None:
        for row in self.rows.values():
            try:
                row.tick()
            except Exception:                     # noqa: BLE001
                pass


class ShieldRow(ttk.LabelFrame):
    def __init__(self, master, app, kind: str, label: str, known: bool):
        super().__init__(master, text=f" {label} ", padding=10)
        self.app = app
        self.session = app.session
        self.kind = kind
        self.label = label
        self.known = known

        self.combo = ttk.Combobox(self, values=list(SHIELD_LABELS),
                                  state="readonly", width=14)
        self.combo.pack(side="left")
        self.combo.bind("<<ComboboxSelected>>", self.on_select)

        self.live = ttk.Label(self, text="当前：未附加", style="Hint.TLabel")
        self.live.pack(side="left", padx=12)

        if not known:
            ttk.Label(
                self,
                text="（变量号未知：定位到后加进 vmslot.DEFAULT_VAR_IDS 即可启用）",
                style="Hint.TLabel",
            ).pack(side="left", padx=8)

        self.combo.state(["disabled"])

    # ---------- 写入 ----------

    def on_select(self, _event=None) -> None:
        idx = self.combo.current()
        if idx < 0:
            return
        try:
            msg = self.session.set_value(self.kind, idx)
        except WinMemError as exc:
            messagebox.showerror("写入失败", str(exc))
            self.tick()
            return
        self.app.log(f"{self.label}愛盾 → {SHIELD_LABELS[idx]}：{msg}")

    # ---------- 定时刷新 ----------

    def tick(self) -> None:
        if not self.session.attached:
            self.combo.state(["disabled"])
            self.live.configure(text="当前：未附加")
            return
        if not self.known:
            self.combo.state(["disabled"])
            self.live.configure(text="当前：变量号未知，待定位")
            return
        addr = self.session.address(self.kind)
        if addr is None:
            self.combo.state(["disabled"])
            self.live.configure(text="当前：未定位（附加游戏 / 重新自动定位后可用）")
            return
        cur = self.session.current(self.kind)
        value = cur.decoded_loose if cur else None
        if value is None or not (0 <= value <= SHIELD_MAX):
            self.combo.state(["disabled"])
            self.live.configure(
                text=f"当前：读不出来（{value}）——地址可能已失效，重新自动定位")
            return
        self.combo.state(["!disabled"])
        # 用户正下拉选择时不要覆盖显示
        if self.app.focus_get() is not self.combo:
            self.combo.set(SHIELD_LABELS[value])
        self.live.configure(text=f"当前：{SHIELD_LABELS[value]}  (0x{addr:08X})")
