"""存档页签：离线改 `.asd`，完全不碰进程内存。

和内存改法的分工：
* 内存改：即时生效，但受地址漂移 / 变量号 / 多副本影响；
* 存档改：要先存档再读档才生效，但**没有任何地址问题**，
  而且能改运行时找不到的字段（比如砂夜子的愛盾）。

写入前强制整档备份到 `SaveData/backup_改档/`，写完回读校验。
"""

from __future__ import annotations

import glob
import os
import shutil
import time
import tkinter as tk
from tkinter import messagebox, ttk

from cint import ENERGY_MAX, MONEY_MAX, date_to_days, days_to_date
from rsave import SaveError, SaveFile

MAX_TIMEZONE = 7

# 女主可编辑字段：(键, 显示名, 下限, 上限)
HERO_FIELDS = (
    ("level", "等级", 0, 10),
    ("seed", "愛盾", 0, 1),
    ("semen", "受精", 0, 999),
    ("relation", "关系", 0, 7),
)


class SavePanel(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=12)
        self.app = app
        self.doc: SaveFile | None = None
        self.paths: list[str] = []
        self.basic_vars: dict[str, tk.StringVar] = {}
        self.hero_vars: list[dict[str, tk.StringVar]] = []
        self._build()
        self.refresh_slots()

    # ---------- 界面 ----------

    def _build(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="存档目录").pack(side="left")
        self.dir_var = tk.StringVar(value=(self.app.session.cfg.get("save_dir") or ""))
        ttk.Entry(top, textvariable=self.dir_var, width=56,
                  state="readonly").pack(side="left", padx=6)
        ttk.Button(top, text="刷新", command=self.refresh_slots).pack(side="left")
        ttk.Label(top, text="选一个存档 → 改字段 → 保存；回游戏重新读档生效",
                  style="Hint.TLabel").pack(side="left", padx=8)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, pady=8)

        left = ttk.Frame(body)
        left.pack(side="left", fill="y")
        cols = ("slot", "time", "date", "money")
        self.tree = ttk.Treeview(left, columns=cols, show="headings",
                                 selectmode="browse", height=16)
        for c, t, w in (("slot", "槽位", 60), ("time", "修改时间", 120),
                        ("date", "日期", 90), ("money", "金钱", 90)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="y")
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.on_pick())

        right = ttk.LabelFrame(body, text=" 字段 ", padding=10)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))
        self.form = right
        self._build_placeholder(right)

        bar = ttk.Frame(self)
        bar.pack(fill="x")
        self.hint = ttk.Label(bar, text="还没有选择存档", style="Hint.TLabel")
        self.hint.pack(side="left")
        ttk.Button(bar, text="保存到存档", command=self.on_save).pack(side="right")

    def _build_placeholder(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="左边选一个存档槽位", style="Hint.TLabel").pack(anchor="w")

    # ---------- 存档列表 ----------

    def refresh_slots(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.paths = []
        folder = self.dir_var.get().strip()
        if not folder or not os.path.isdir(folder):
            self.hint.configure(text="存档目录不存在，检查 config.json 的 save_dir")
            return
        files = sorted(glob.glob(os.path.join(folder, "Tsumamigui3_*.asd")))
        files = [p for p in files if os.path.basename(p)[12:-4].isdigit()]
        files.sort(key=lambda p: os.path.getmtime(p))
        for p in files:
            slot = os.path.basename(p)[12:-4]
            stamp = time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(p)))
            self.tree.insert("", "end", values=(slot, stamp, "—", "—"))
            self.paths.append(p)
        self.hint.configure(text=f"共 {len(files)} 个存档")

    def _slot_row(self, path: str) -> str:
        for iid in self.tree.get_children():
            if int(self.tree.item(iid, "values")[0]) == int(os.path.basename(path)[12:-4]):
                return iid
        return ""

    def _patch_row(self, path: str, date_text: str, money: int) -> None:
        iid = self._slot_row(path)
        if not iid:
            return
        vals = list(self.tree.item(iid, "values"))
        vals[2] = date_text
        vals[3] = str(money)
        self.tree.item(iid, values=vals)

    # ---------- 选中 ----------

    def on_pick(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        if not (0 <= idx < len(self.paths)):
            return
        path = self.paths[idx]

        def job():
            return SaveFile.load(path)

        self.app.run_bg(job, lambda doc: self._show(doc), busy="正在解析存档…")

    def _show(self, doc: SaveFile) -> None:
        self.doc = doc
        for w in self.form.winfo_children():
            w.destroy()
        self.basic_vars = {}
        self.hero_vars = []

        basic_refs = doc.basic_refs()
        self._basic_refs = basic_refs

        box = ttk.LabelFrame(self.form, text=" 基本参数 ", padding=8)
        box.pack(fill="x")
        self._add_basic(box, "金钱", "money", str(doc.cint_value(basic_refs["money"])))
        self._add_basic(box, "精力", "energy", str(doc.cint_value(basic_refs["energy"])))
        days = doc.cint_value(basic_refs["date"])
        self._add_basic(box, "日期(天数)", "date", str(days),
                        hint=days_to_date(days))
        self._add_basic(box, "时段(0~7)", "timezone",
                        str(doc.cint_value(basic_refs["timezone"])))

        heroes = doc.heroines()
        grid = ttk.LabelFrame(self.form, text=" 女主角 ", padding=8)
        grid.pack(fill="x", pady=(8, 0))
        ttk.Label(grid, text="").grid(row=0, column=0)
        for c, (_k, title, _lo, _hi) in enumerate(HERO_FIELDS, start=1):
            ttk.Label(grid, text=title).grid(row=0, column=c, padx=6, sticky="w")
        for r, hero in enumerate(heroes, start=1):
            ttk.Label(grid, text=hero["key"]).grid(row=r, column=0, sticky="w", pady=3)
            row = {}
            for c, (key, _title, _lo, _hi) in enumerate(HERO_FIELDS, start=1):
                var = tk.StringVar(value=str(doc.cint_value(hero[key])))
                ttk.Entry(grid, textvariable=var, width=8).grid(
                    row=r, column=c, padx=6, pady=3)
                row[key] = var
            self.hero_vars.append(row)

        name = os.path.basename(doc.path)
        self.hint.configure(text=f"{name}（ver {doc.version}）已载入")
        self._patch_row(doc.path, days_to_date(days),
                        doc.cint_value(basic_refs["money"]))

    def _add_basic(self, parent: ttk.Frame, title: str, key: str,
                   value: str, hint: str = "") -> None:
        row = len(self.basic_vars)
        ttk.Label(parent, text=title).grid(row=row, column=0, sticky="w", pady=2)
        var = tk.StringVar(value=value)
        ttk.Entry(parent, textvariable=var, width=12).grid(
            row=row, column=1, sticky="w", padx=8)
        if hint:
            ttk.Label(parent, text=hint, style="Hint.TLabel").grid(
                row=row, column=2, sticky="w")
        self.basic_vars[key] = var

    # ---------- 保存 ----------

    def on_save(self) -> None:
        if self.doc is None:
            messagebox.showinfo("还没选存档", "先在左边选一个存档槽位")
            return
        doc = self.doc
        try:
            money = self._read_int("money", 0, MONEY_MAX)
            energy = self._read_int("energy", 0, ENERGY_MAX)
            date = self._read_int("date", 1, 3660)
            tz = self._read_int("timezone", 0, MAX_TIMEZONE)
        except ValueError as exc:
            messagebox.showwarning("输入不对", str(exc))
            return

        changes: dict[int, int] = {
            self._basic_refs["money"]: money,
            self._basic_refs["energy"]: energy,
            self._basic_refs["date"]: date,
            self._basic_refs["timezone"]: tz,
        }
        heroes = doc.heroines()
        try:
            for hero, row in zip(heroes, self.hero_vars):
                for key, title, lo, hi in HERO_FIELDS:
                    text = row[key].get().strip()
                    if not text:
                        continue
                    try:
                        value = int(text, 0)
                    except ValueError:
                        raise ValueError(f"{hero['key']} 的{title}要填整数") from None
                    if not (lo <= value <= hi):
                        raise ValueError(f"{hero['key']} 的{title}要在 {lo}~{hi}")
                    changes[hero[key]] = value
        except ValueError as exc:
            messagebox.showwarning("输入不对", str(exc))
            return

        backup = self._backup(doc.path)
        try:
            for ref, value in changes.items():
                doc.cint_set(ref, value)
            doc.save()
            check = SaveFile.load(doc.path)
        except (OSError, SaveError) as exc:
            messagebox.showerror("保存失败", f"{exc}\n\n原档备份在：\n{backup}")
            return

        # 回读校验：每个改过的 C_INT 都要能解出新值
        bad = [ref for ref, value in changes.items()
               if check.cint_value(ref) != value]
        if bad:
            messagebox.showerror("校验失败",
                                 f"{len(bad)} 个字段回读不一致，原档备份在：\n{backup}")
            return

        self.app.log(f"存档 {os.path.basename(doc.path)} 已写入 "
                     f"{len(changes)} 个字段（备份 {os.path.basename(backup)}）")
        self._show(check)
        messagebox.showinfo("已保存",
                            "写好了。回游戏里重新读一次这个档即可生效。\n\n"
                            f"原档备份：\n{backup}")

    def _read_int(self, key: str, lo: int, hi: int) -> int:
        text = self.basic_vars[key].get().strip()
        try:
            value = int(text, 0)
        except ValueError:
            raise ValueError(f"{key} 要填整数") from None
        if not (lo <= value <= hi):
            raise ValueError(f"{key} 要在 {lo}~{hi} 之间")
        return value

    def _backup(self, path: str) -> str:
        folder = os.path.join(os.path.dirname(path), "backup_改档")
        os.makedirs(folder, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dst = os.path.join(folder, f"{stamp}_{os.path.basename(path)}")
        shutil.copy2(path, dst)
        return dst

    # ---------- 定时 ----------

    def tick(self) -> None:
        """存档页签没有需要轮询的东西（不在内存里）。"""
