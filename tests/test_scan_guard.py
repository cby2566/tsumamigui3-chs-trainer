"""扫描器不能在「读不出来的区域」上原地空转。

读档会释放/重建堆，扫描器手里的区域快照随之过期；此时
ReadProcessMemory 返回 0 字节。若继续按 4 字节推进，几十 MB 的失效区域
就是上千万次系统调用 —— 表现就是「点了重新自动定位之后整个工具假死」。
三个扫描器（scan / cint / vmslot）都必须及时放弃该区域。

跑法（在 game_tools 目录下）：
    python tests/test_scan_guard.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))

from cint import scan as scan_cint                        # noqa: E402
from scan import Scanner                                 # noqa: E402
from vmslot import scan_slots                            # noqa: E402

DEAD_SIZE = 256 * 1024 * 1024      # 256 MB 失效区域，旧逻辑要读 6700 万次
MAX_READS = 2                      # 只允许「试一次就放弃」


class Region:
    def __init__(self, base: int, size: int):
        self.base = base
        self.size = size

    @property
    def end(self) -> int:
        return self.base + self.size


class DeadProc:
    """所有读都失败：模拟区域快照过期。"""

    def __init__(self, size: int = DEAD_SIZE):
        self.size = size
        self.reads = 0

    def regions(self):
        return [Region(0x10000000, self.size)]

    def read(self, address: int, size: int) -> bytes:
        self.reads += 1
        return b""

    def total_writable(self) -> int:
        return self.size


def test_vmslot_aborts_dead_region() -> None:
    proc = DeadProc()
    scan_slots(proc, {7779})
    assert proc.reads <= MAX_READS, f"vmslot 在失效区域上读了 {proc.reads} 次"


def test_cint_aborts_dead_region() -> None:
    proc = DeadProc()
    scan_cint(proc, 123, Scanner(proc))
    assert proc.reads <= MAX_READS, f"cint 在失效区域上读了 {proc.reads} 次"


def test_scanner_aborts_dead_region() -> None:
    proc = DeadProc()
    list(Scanner(proc)._iter_chunks(Region(0x10000000, proc.size), 4))
    assert proc.reads <= MAX_READS, f"Scanner 在失效区域上读了 {proc.reads} 次"


def main() -> None:
    for fn in (test_vmslot_aborts_dead_region,
               test_cint_aborts_dead_region,
               test_scanner_aborts_dead_region):
        fn()
        print(f"  ok  {fn.__name__}")
    print("全部通过")


if __name__ == "__main__":
    main()
