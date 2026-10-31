"""结构诊断：不依赖具体金币数值，把内存里所有「长得像 C_INT」的
16 字节结构揪出来，并按是否成串聚集排序。

用法：
    python tools/live_check.py [进程名]
进程名缺省取 config.json 里的 exe_name。

正常情况会看到几到几十个 C_INT，其中"前面还挨着一个 C_INT"的那几个
基本就是 C_GAME_PARAMETER 里的成员（m_nEnergy / m_nMoney ...）。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np                                       # noqa: E402

from _paths import exe_name                              # noqa: E402
from cint import MONEY_MAX, PATTERN_HALF_MAX             # noqa: E402
from scan import CHUNK                                   # noqa: E402
from winmem import Process, WinMemError, find_process    # noqa: E402


def collect(proc: Process) -> list[tuple[int, int, int, int, int, int]]:
    """返回 [(地址, 值, code, backup, cord, backupPattern)]。"""
    rows = []
    for r in proc.regions():
        off = 0
        while off < r.size:
            buf = proc.read(r.base + off, min(CHUNK, r.size - off))
            got = len(buf)
            usable = (got // 4) * 4
            if usable >= 16:
                a = np.frombuffer(buf[:usable], dtype="<u4")
                v0, v1, v2, v3 = a[:-3], a[1:-2], a[2:-1], a[3:]
                mask = ((v0 <= MONEY_MAX) & (v1 != 0) & (v2 != 0)
                        & ((v3 >> 16) < PATTERN_HALF_MAX)
                        & ((v3 & 0xFFFF) < PATTERN_HALF_MAX))
                for i in np.flatnonzero(mask):
                    rows.append((r.base + off + int(i) * 4, int(v0[i]), int(v1[i]),
                                 int(v2[i]), int(v3[i]) >> 16, int(v3[i]) & 0xFFFF))
            off += max(got - 12, 4)
    return rows


def main() -> int:
    if len(sys.argv) > 1:
        name = sys.argv[1]
    else:
        try:
            name = exe_name()
        except SystemExit as e:
            print(e)
            return 1
    pids = find_process(name)
    if not pids:
        print(f"没找到 {name}，先把游戏开起来")
        return 1
    try:
        with Process(pids[0]) as proc:
            print(f"附加 {name} pid={pids[0]}，可写私有内存 "
                  f"{proc.total_writable() / 1048576:.1f} MB")
            rows = collect(proc)
    except WinMemError as e:
        print("失败：", e)
        return 1

    addrs = {r[0]: r for r in rows}
    rows.sort(key=lambda r: (r[0] - 16) not in addrs)      # 前面接一个的排前面
    print(f"\n找到 {len(rows)} 个 C_INT 形状的结构：")
    for a, v, c, b, cord, bp in rows[:40]:
        nb = "  ← 前面还有 C_INT" if (a - 16) in addrs else ""
        print(f"  0x{a:08X}  值={v:<8d} pattern={cord}/{bp}{nb}")
    if len(rows) > 40:
        print(f"  …… 省略 {len(rows) - 40} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
