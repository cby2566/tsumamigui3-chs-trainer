"""主窗口：进程状态 + 金币 / 精力 / 日期 / 受精值 / 愛盾。"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from cint import (DATE_MAX, ENERGY_MAX, FERTILIZE_MAX_KAEDE,
                  FERTILIZE_MAX_SAYOKO, FERTILIZE_MAX_AOI, FERTILIZE_MAX_SUMIRE,
                  MONEY_MAX, days_to_date)
from session import AutoResult, Session
from save_panel import SavePanel
from shield_panel import ShieldPanel
from value_panel import ValuePanel

TICK_MS = 200
RELOCATE_RETRY_MS = 1500     # 重新自动定位未命中时的重试间隔
RELOCATE_RETRY_MAX = 10      # 最多重试 10 次（约 15 秒），够读完一次档
RELOCATE_TIMEOUT = 20.0      # 单次扫描超过这个时间就判定卡住，放行界面


class GroupPanel(ttk.Frame):
    """把同一族的数值面板收进二级 Notebook，避免顶层页签被撑爆。

    面板实例本身不变，adopt_auto() / tick() 照旧按实例调用。
    """

    def __init__(self, master):
        super().__init__(master)
        self.book = ttk.Notebook(self)
        self.book.pack(fill="both", expand=True)

    def add(self, panel: ttk.Frame, title: str) -> None:
        self.book.add(panel, text=f"  {title}  ")


class App(tk.Tk):
    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        title = (session.cfg.get("title") or "数值修改器").strip()
        self.title(f"{title}  ·  System4.3 / C_INT")
        self.geometry("980x660")
        self.minsize(900, 580)
        self._setup_style()
        self._build()
        self._queue: queue.Queue = queue.Queue()
        self._relocating = False         # 重试链正在跑时，别让按钮叠出第二条链
        self._reloc_started = 0.0        # 本次扫描的起始时间（看门狗用）
        self._reloc_token = 0            # 看门狗放弃后，晚到的结果不再处理
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
        # 读档会把值对象销毁重建，自动定位需要能重新跑一遍，不能只在附加时试一次
        self.btn_relocate = ttk.Button(head, text="重新自动定位",
                                       command=self.on_relocate)
        self.btn_relocate.pack(side="right", padx=(0, 6))

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=12, pady=10)
        self.money_panel = ValuePanel(nb, self, "money", "金币", MONEY_MAX,
                                      "，由 SetMoney 写死")
        self.energy_panel = ValuePanel(nb, self, "energy", "精力", ENERGY_MAX,
                                       "，由 SetEnergy 写死")
        self.date_panel = ValuePanel(nb, self, "date", "日期", DATE_MAX,
                                     "，填从 1 月 1 日算起的天数，改动前先存档",
                                     show_fill=False, describe=days_to_date)
        # 受精值是「每个女主多个点位」，平铺会把顶层页签挤爆，收进二级 Notebook。
        # 面板实例照旧，adopt_auto()/tick() 不用改。
        self.fert_group = GroupPanel(nb)
        self.fert_panel = ValuePanel(self.fert_group.book, self, "fertilize_kaede_1",
                                     "受精值·海夕里①", FERTILIZE_MAX_KAEDE,
                                     "，主人公，上限 150")
        self.fert1b_panel = ValuePanel(self.fert_group.book, self, "fertilize_kaede_2",
                                       "受精值·海夕里②", FERTILIZE_MAX_KAEDE,
                                       "，上限 150")
        self.fert2_panel = ValuePanel(self.fert_group.book, self, "fertilize_sayoko_1",
                                      "受精值·砂夜子①", FERTILIZE_MAX_SAYOKO,
                                      "，上限 250")
        self.fert3_panel = ValuePanel(self.fert_group.book, self, "fertilize_sayoko_2",
                                      "受精值·砂夜子②", FERTILIZE_MAX_SAYOKO,
                                      "，上限 250")
        self.fert4_panel = ValuePanel(self.fert_group.book, self, "fertilize_sayoko_3",
                                      "受精值·砂夜子③", FERTILIZE_MAX_SAYOKO,
                                      "，上限 250")
        self.fert5_panel = ValuePanel(self.fert_group.book, self, "fertilize_aoi_1",
                                      "受精值·藍①", FERTILIZE_MAX_AOI,
                                      "，上限 100")
        self.fert6_panel = ValuePanel(self.fert_group.book, self, "fertilize_sumire_1",
                                      "受精值·菫①", FERTILIZE_MAX_SUMIRE,
                                      "，上限 999")
        for panel, title in ((self.fert_panel, "海夕里①"),
                             (self.fert1b_panel, "海夕里②"),
                             (self.fert2_panel, "砂夜子①"),
                             (self.fert3_panel, "砂夜子②"),
                             (self.fert4_panel, "砂夜子③"),
                             (self.fert5_panel, "藍①"),
                             (self.fert6_panel, "菫①")):
            self.fert_group.add(panel, title)
        self.shield_panel = ShieldPanel(nb, self)
        self.save_panel = SavePanel(nb, self)
        # 「数值搜索」页签默认不挂：它扫的是裸内存值，而本作的值全是 C_INT，
        # 直接改 m_nValue 会被 Get() 的自愈逻辑改回去，改了等于没改。
        # scan_panel.py 仍然保留，需要时把下面这行加回来即可：
        #   nb.add(ScanPanel(nb, self), text="  数值搜索  ")
        nb.add(self.money_panel, text="  金币  ")
        nb.add(self.energy_panel, text="  精力  ")
        nb.add(self.date_panel, text="  日期  ")
        nb.add(self.fert_group, text="  受精值  ")
        nb.add(self.shield_panel, text="  愛盾  ")
        nb.add(self.save_panel, text="  存档  ")

        log_frame = ttk.LabelFrame(self, text=" 日志 ", padding=(8, 4))
        log_frame.pack(fill="both", padx=12, pady=(0, 12))
        self.logbox = ScrolledText(log_frame, height=6, wrap="word",
                                   font=("Consolas", 9), state="disabled",
                                   relief="flat", background="#fafafa")
        self.logbox.pack(fill="both", expand=True)

    def _value_panels(self) -> tuple[ValuePanel, ...]:
        """所有 C_INT 数值面板。

        愛盾不在其中：它不是 ValuePanel，海夕里的盾已接变量号（vmslot 里
        shield_kaede），自动定位命中后地址进 session.addresses，
        ShieldPanel.tick() 自己会刷新显示。
        """
        return (self.money_panel, self.energy_panel, self.date_panel,
                self.fert_panel, self.fert1b_panel, self.fert2_panel,
                self.fert3_panel, self.fert4_panel,
                self.fert5_panel, self.fert6_panel)

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
                continue
            self.status.configure(text="就绪")
            try:
                on_ok(payload)
                if on_done:
                    on_done()
            except Exception as exc:                      # noqa: BLE001
                # 回调里的异常绝不能冒出去：一旦跳出这个函数，末尾的 after()
                # 就再也不会被调度，整个后台结果队列从此僵死
                # （界面表现为「点什么都没反应」）。
                self.log(f"界面刷新失败：{exc}")
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
            # 自动定位命中时，直接把地址铺到各页签上，省掉手填数字那一步
            for panel in self._value_panels():
                try:
                    panel.adopt_auto()
                except Exception as exc:                  # noqa: BLE001
                    # 一个面板刷不出来不该拖累其它九个
                    self.log(f"{panel.label} 刷新失败：{exc}")

        self.run_bg(job, done, busy="正在附加…")

    def on_relocate(self, attempt: int = 0) -> None:
        """重新按变量号定位所有数值。

        读档会把值对象销毁重建，期间槽还指着已释放的内存，一次扫描经常全落空。
        所以未命中时按固定间隔重试有限次，等读档落定后自动接上。
        """
        if not self.session.attached:
            if attempt == 0:
                messagebox.showinfo("还没附加", "先点右上角「附加游戏」")
            self._relocating = False
            return
        if attempt == 0:
            if self._relocating:
                self.log("正在自动定位，先别重复点")
                return
            self._relocating = True
            self._reloc_token += 1
            self.after(1000, self._watch_relocate)

        self._reloc_started = time.time()
        token = self._reloc_token

        def job():
            try:
                return self.session.autolocate()
            except Exception as exc:                      # noqa: BLE001
                # 异常也要带回主线程：run_bg 的错误分支不会调 done()，
                # 否则 _relocating 会一直是 True，按钮再也点不动。
                return AutoResult({}, [], f"自动定位失败：{exc}")

        def done(result) -> None:
            if token != self._reloc_token:
                # 已经被看门狗判定放弃，这份迟到结果不要再改界面、也别再起重试链
                return
            for line in result.message.splitlines():
                if line:
                    self.log(line)
            if not self.session.attached:
                self._relocating = False
                return
            for panel in self._value_panels():
                try:
                    panel.adopt_auto()
                except Exception as exc:                  # noqa: BLE001
                    # 一个面板刷不出来不该拖累其它九个
                    self.log(f"{panel.label} 刷新失败：{exc}")
            if result.missing and attempt < RELOCATE_RETRY_MAX:
                self.status.configure(
                    text=f"等待读档完成…（重试 {attempt + 1}/{RELOCATE_RETRY_MAX}）")
                self.after(RELOCATE_RETRY_MS,
                           lambda: self.on_relocate(attempt + 1))
                return
            self._relocating = False
            if result.missing:
                self.log("重新自动定位仍未命中：确认游戏已经读完档，再点一次按钮")

        self.run_bg(job, done, busy="正在自动定位…")

    def _watch_relocate(self) -> None:
        """扫描卡住时把界面放行。

        Python 线程没法从外面杀掉，但至少不能让按钮和状态栏永久锁死；
        放弃时把 token 加一，那份迟到的结果就不会再来改界面。
        """
        if not self._relocating:
            return
        if time.time() - self._reloc_started < RELOCATE_TIMEOUT:
            self.after(1000, self._watch_relocate)
            return
        self._relocating = False
        self._reloc_token += 1
        self.status.configure(text="就绪")
        self.log(f"自动定位超过 {RELOCATE_TIMEOUT:.0f} 秒没有结果，已停止等待"
                 f"（后台线程可能卡在失效内存上）。可以再点一次，或手动定位")

    # ---------- 定时 ----------

    def _tick(self) -> None:
        for panel in (*self._value_panels(), self.shield_panel):
            try:
                panel.tick()
            except Exception:                            # noqa: BLE001
                pass
        self.after(TICK_MS, self._tick)

    def _on_close(self) -> None:
        self.session.detach()
        self.destroy()
