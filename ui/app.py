"""主窗口：进程状态 + 金币 / 精力 / 愛盾 / 通用数值搜索。"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from cint import DATE_MAX, ENERGY_MAX, FERTILIZE_MAX, MONEY_MAX, days_to_date
from session import Session
from shield_panel import ShieldPanel
from value_panel import ValuePanel

TICK_MS = 200


class App(tk.Tk):
    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        title = (session.cfg.get("title") or "数值修改器").strip()
        self.title(f"{title}  ·  System4.3 / C_INT")
        self.geometry("860x640")
        self.minsize(780, 560)
        self._setup_style()
        self._build()
        self._queue: queue.Queue = queue.Queue()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(TICK_MS, self._tick)
        self.after(50, self._drain_queue)

    # ---------- 外观 ----------

    def _setup_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        base = ("Microsoft YaHei UI", 9)
        self.option_add("*Font", base)
        style.configure("Treeview", font=base, rowheight=22)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Hint.TLabel", foreground="#666666")

    def _build(self) -> None:
        head = ttk.Frame(self, padding=(12, 10, 12, 0))
        head.pack(fill="x")
        ttk.Label(head, text=(self.session.cfg.get("title") or "数值修改器"),
                  style="Title.TLabel").pack(side="left")
        self.status = ttk.Label(head, text="未附加游戏", style="Hint.TLabel")
        self.status.pack(side="left", padx=14)
        self.btn_attach = ttk.Button(head, text="附加游戏", command=self.on_attach)
        self.btn_attach.pack(side="right")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=12, pady=10)
        self.money_panel = ValuePanel(nb, self, "money", "金币", MONEY_MAX,
                                      "，由 SetMoney 写死")
        self.energy_panel = ValuePanel(nb, self, "energy", "精力", ENERGY_MAX,
                                       "，由 SetEnergy 写死")
        self.date_panel = ValuePanel(nb, self, "date", "日期", DATE_MAX,
                                     "，填从 1 月 1 日算起的天数，改动前先存档",
                                     show_fill=False, describe=days_to_date)
        self.fert_panel = ValuePanel(nb, self, "fertilize", "受精值·海夕里",
                                     FERTILIZE_MAX, "，主人公")
        self.fert2_panel = ValuePanel(nb, self, "fertilize_sayoko_1", "受精值·砂夜子①",
                                      FERTILIZE_MAX, "，砂夜子")
        self.fert3_panel = ValuePanel(nb, self, "fertilize_sayoko_2", "受精值·砂夜子②",
                                      FERTILIZE_MAX, "，砂夜子")
        self.fert4_panel = ValuePanel(nb, self, "fertilize_sayoko_3", "受精值·砂夜子③",
                                      FERTILIZE_MAX, "，砂夜子")
        self.shield_panel = ShieldPanel(nb, self)
        # 「数值搜索」页签默认不挂：它扫的是裸内存值，而本作的值全是 C_INT，
        # 直接改 m_nValue 会被 Get() 的自愈逻辑改回去，改了等于没改。
        # scan_panel.py 仍然保留，需要时把下面这行加回来即可：
        #   nb.add(ScanPanel(nb, self), text="  数值搜索  ")
        nb.add(self.money_panel, text="  金币  ")
        nb.add(self.energy_panel, text="  精力  ")
        nb.add(self.date_panel, text="  日期  ")
        nb.add(self.fert_panel, text="  受精·海夕里  ")
        nb.add(self.fert2_panel, text="  受精·砂夜子①  ")
        nb.add(self.fert3_panel, text="  受精·砂夜子②  ")
        nb.add(self.fert4_panel, text="  受精·砂夜子③  ")
        nb.add(self.shield_panel, text="  愛盾  ")

        log_frame = ttk.LabelFrame(self, text=" 日志 ", padding=(8, 4))
        log_frame.pack(fill="both", padx=12, pady=(0, 12))
        self.logbox = ScrolledText(log_frame, height=6, wrap="word",
                                   font=("Consolas", 9), state="disabled",
                                   relief="flat", background="#fafafa")
        self.logbox.pack(fill="both", expand=True)

    # ---------- 日志 / 异步 ----------

    def log(self, msg: str) -> None:
        self.logbox.configure(state="normal")
        self.logbox.insert("end", f"· {msg}\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def run_bg(self, job, on_ok, busy: str = "处理中…", on_done=None) -> None:
        """后台线程只负责算，结果塞队列；主线程轮询取出来再碰控件。

        直接在工作线程里调 Tk 的 after() 是非线程安全的，会撞
        "main thread is not in main loop"。
        """
        self.status.configure(text=busy)

        def worker():
            try:
                result = job()
            except Exception as exc:                      # noqa: BLE001
                self._queue.put(("err", exc, None, None))
            else:
                self._queue.put(("ok", result, on_ok, on_done))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_queue(self) -> None:
        while True:
            try:
                kind, payload, on_ok, on_done = self._queue.get_nowait()
            except queue.Empty:
                break
            if kind == "err":
                self.status.configure(text="出错")
                self.log(f"失败：{payload}")
                messagebox.showerror("出错了", str(payload))
            else:
                self.status.configure(text="就绪")
                on_ok(payload)
                if on_done:
                    on_done()
        self.after(50, self._drain_queue)

    # ---------- 进程 ----------

    def on_attach(self) -> None:
        if self.session.attached:
            self.session.detach()
            self.status.configure(text="未附加游戏")
            self.btn_attach.configure(text="附加游戏")
            self.log("已断开")
            return

        def job():
            return self.session.attach()

        def done(msg):
            self.btn_attach.configure(text="断开")
            for line in str(msg).splitlines():
                self.log(line)
            # 自动定位命中时，直接把地址铺到两个页签上，省掉手填数字那一步
            for panel in (self.money_panel, self.energy_panel, self.date_panel,
                          self.fert_panel, self.fert2_panel, self.fert3_panel,
                          self.fert4_panel):
                panel.adopt_auto()

        self.run_bg(job, done, busy="正在附加…")

    # ---------- 定时 ----------

    def _tick(self) -> None:
        for panel in (self.money_panel, self.energy_panel, self.date_panel,
                      self.fert_panel, self.fert2_panel, self.fert3_panel,
                      self.fert4_panel, self.shield_panel):
            try:
                panel.tick()
            except Exception:                            # noqa: BLE001
                pass
        self.after(TICK_MS, self._tick)

    def _on_close(self) -> None:
        self.session.detach()
        self.destroy()
