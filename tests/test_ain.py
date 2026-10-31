"""M1 的三条自检：容器边界、函数表地址、反汇编精确对齐。

跑法（在 game_tools 目录下）：
    python tests/test_ain.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

from ain import Ain                                    # noqa: E402
from disasm import disassemble                          # noqa: E402
from _paths import ain_path                             # noqa: E402

# 实测得到的锚点：函数名 -> (索引, 起始地址, 到下一个函数的长度)
ANCHORS = {
    "C_INT@Get":      (6568, 0x18FB04, 462),
    "C_INT@Set":      (6569, 0x18FCD2, 198),
    "C_INT@Add":      (6570, 0x18FD98, 56),
    "C_INT@Sub":      (6571, 0x18FDD0, 56),
    "C_INT@GetKey":   (6572, 0x18FE08, 62),
}


def test_container(ain: Ain) -> None:
    assert ain.version == 7, ain.version
    assert len(ain.functions) == 11412, len(ain.functions)
    assert len(ain.structs) == 366, len(ain.structs)
    assert len(ain.globals) == 218, len(ain.globals)
    assert ain.structs[191].name == "C_INT"
    assert [m.name for m in ain.structs[191].members] == [
        "m_nValue", "m_nCode", "m_nBackUpCode", "m_nPattern"]


def test_anchors(ain: Ain) -> None:
    for name, (idx, addr, size) in ANCHORS.items():
        f = ain.func(name)
        assert f is not None, name
        assert (f.index, f.address) == (idx, addr), (name, f.index, f.address)
        nxt = min(g.address for g in ain.functions if g.address > f.address)
        assert nxt - f.address == size, (name, nxt - f.address, size)


def test_disasm(ain: Ain) -> None:
    """每个函数都要恰好被反汇编到下一函数的起点，差一个字节说明指令宽度表错了。"""
    for name, (_, addr, size) in ANCHORS.items():
        ins = disassemble(ain, name)
        last = ins[-1]
        consumed = last.offset + 2 + 4 * len(last.args)
        assert consumed == size, (name, consumed, size)


def main() -> None:
    ain = Ain.load(ain_path())
    for fn in (test_container, test_anchors, test_disasm):
        fn(ain)
        print(f"  ok  {fn.__name__}")
    print("全部通过")


if __name__ == "__main__":
    main()
