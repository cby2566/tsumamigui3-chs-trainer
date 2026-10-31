"""把「附加进程 → 定位金币 → 写回」串成一套高层会话，UI 只跟它打交道。"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime

from cint import (BoolCandidate, Candidate, ENERGY_MAX, MONEY_MAX, read_at,
                  read_bool, scan as scan_cint, scan_bool, write_bool,
                  write_money)
from scan import Result, Scanner
from winmem import Process, WinMemError, find_process

DEFAULT_CONFIG = {
    # 游戏身份全部外置到 config.json（已 gitignore），代码里不留任何本地路径
    "title": "数值修改器",
    "game_dir": "",
    "exe_name": "",
    "save_dir": "",
    "backup_saves": True,
}

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "config.json")


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (OSError, ValueError):
            pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


class Session:
    # 受 C_INT 保护的数值：kind -> 上限（来自 ain 里的 Clamp 常量）
    # 愛盾是布尔 C_INT，上限 1；只有海夕里、砂夜子有盾。
    TARGETS = {
        "money": MONEY_MAX,
        "energy": ENERGY_MAX,
        "shield_kaede": 1,
        "shield_sayoko": 1,
    }

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or load_config()
        self.proc: Process | None = None
        self.scanner: Scanner | None = None
        self.candidates: list[Candidate] = []
        self.bool_candidates: list[BoolCandidate] = []
        self.addresses: dict[str, int | None] = {k: None for k in self.TARGETS}
        self.last_result: Result | None = None
        self._backed_up = False

    # ---------- 附加 ----------

    @property
    def attached(self) -> bool:
        return self.proc is not None and self.proc.alive()

    def attach(self) -> str:
        exe = (self.cfg.get("exe_name") or "").strip()
        if not exe:
            raise WinMemError("config.json 里还没填 exe_name（游戏进程名，例如 Game.exe）")
        pids = find_process(exe)
        if not pids:
            raise WinMemError(f"没找到 {exe}，请先把游戏开起来")
        if len(pids) > 1:
            raise WinMemError(f"发现多个 {exe} 进程：{pids}，请只保留一个")
        self.proc = Process(pids[0])
        self.scanner = Scanner(self.proc)
        self.candidates = []
        self.bool_candidates = []
        self.addresses = {k: None for k in self.TARGETS}
        self.last_result = None
        self._backed_up = False

        size = self.proc.total_writable()
        return f"已附加 {exe} (pid={pids[0]})，可写私有内存 {size / 1048576:.1f} MB"

    def detach(self) -> None:
        if self.proc:
            self.proc.close()
        self.proc = None
        self.scanner = None
        self.candidates = []
        self.bool_candidates = []
        self.addresses = {k: None for k in self.TARGETS}

    def require(self) -> tuple[Process, Scanner]:
        if not self.attached:
            raise WinMemError("还没有附加游戏进程")
        return self.proc, self.scanner

    # ---------- 存档备份 ----------

    def backup_saves(self) -> str | None:
        """第一次定位前把存档目录整体复制一份，只做一次。"""
        if self._backed_up or not self.cfg.get("backup_saves"):
            return None
        src = self.cfg.get("save_dir")
        self._backed_up = True
        if not src or not os.path.isdir(src):
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = os.path.join(os.path.dirname(src.rstrip("\\/")), f"SaveData_bak_{stamp}")
        try:
            shutil.copytree(src, dst)
            return dst
        except OSError:
            return None

    # ---------- 受保护的数值（金币 / 精力） ----------

    def find(self, kind: str, value: int) -> list[Candidate]:
        proc, sc = self.require()
        self.backup_saves()
        # 精力这类小数值容易撞车，用金币的地址做「同族」参考来排序
        near = None
        for other in ("money", "energy"):
            if other != kind and self.addresses.get(other) is not None:
                near = self.addresses[other]
                break
        self.candidates = scan_cint(proc, value, sc, near=near)
        return self.candidates

    # ---------- 愛盾（布尔 C_INT） ----------

    def find_bool_candidates(self) -> list[BoolCandidate]:
        """扫描所有值为 0/1 的 C_INT，按「离金币近、邻域 C_INT 多」排序。"""
        proc, sc = self.require()
        self.backup_saves()
        self.bool_candidates = scan_bool(proc, sc, near=self.addresses.get("money"))
        return self.bool_candidates

    def bind_bool(self, kind: str, address: int) -> int:
        """把某个 C_INT 绑定成指定女主的愛盾；只接受解码为 0/1 的地址。"""
        proc, _ = self.require()
        cur = read_at(proc, address)
        if cur is None or cur.decoded not in (0, 1):
            raise WinMemError("这个地址不是合法的布尔 C_INT，换个候选再试")
        self.addresses[kind] = address
        return int(cur.decoded)

    def read_bool_at(self, address: int) -> int | None:
        proc, _ = self.require()
        return read_bool(proc, address)

    def test_bool(self, address: int, value: int) -> int:
        """绑定前试写：只改这一个 C_INT，写后回读校验。"""
        proc, _ = self.require()
        if value not in (0, 1):
            raise WinMemError("愛盾只能是 0 或 1")
        if not write_bool(proc, address, value):
            raise WinMemError("试写失败，地址可能已失效")
        cur = read_at(proc, address)
        if cur is None or cur.decoded != value:
            raise WinMemError("试写后解码不一致，地址可能不是 C_INT")
        return int(cur.decoded)

    def set_bool(self, kind: str, on: bool) -> str:
        if self.addresses.get(kind) is None:
            raise WinMemError("还没绑定愛盾地址")
        return self.set_value(kind, 1 if on else 0)

    def address(self, kind: str) -> int | None:
        return self.addresses.get(kind)

    def select(self, kind: str, address: int) -> None:
        self.addresses[kind] = address

    def unbind(self, kind: str) -> None:
        self.addresses[kind] = None

    def current(self, kind: str) -> Candidate | None:
        addr = self.addresses.get(kind)
        if addr is None:
            return None
        proc, _ = self.require()
        return read_at(proc, addr)

    def set_value(self, kind: str, value: int) -> str:
        proc, _ = self.require()
        addr = self.addresses.get(kind)
        if addr is None:
            raise WinMemError(f"还没定位{kind}，先填当前数值点「定位」")
        top = self.TARGETS[kind]
        if not (0 <= value <= top):
            raise WinMemError(f"数值必须在 0 ~ {top} 之间（游戏自身会钳到这个范围）")
        old = self.current(kind)
        if not write_money(proc, addr, value):
            raise WinMemError("写入失败或回读校验不通过，地址可能已失效，请重新定位")
        new = self.current(kind)
        if new is None or new.decoded != value:
            raise WinMemError("写入后解码结果不一致")
        return f"{old.decoded if old else '?'} → {value}  (地址 0x{addr:08X})"

    # ---------- 通用搜索 ----------

    def first_scan(self, value, kind: str) -> Result:
        _, sc = self.require()
        self.last_result = sc.first_scan(value, kind)
        return self.last_result

    def refine(self, mode: str, value=None) -> Result:
        _, sc = self.require()
        if self.last_result is None:
            raise WinMemError("还没有首次扫描结果")
        self.last_result = sc.refine(self.last_result, mode, value)
        return self.last_result

    def write_value(self, address: int, value, kind: str) -> str:
        _, sc = self.require()
        if not sc.write_value(address, value, kind):
            raise WinMemError(f"写 0x{address:08X} 失败")
        return f"已写入 0x{address:08X} = {value}"


def uptime_text() -> str:
    return time.strftime("%H:%M:%S")
