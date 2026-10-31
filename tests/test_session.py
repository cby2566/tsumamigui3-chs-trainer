"""自动定位的重建语义自检 —— 不需要游戏在跑。

跑法（在 game_tools 目录下）：
    python tests/test_session.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))

import session as session_mod                            # noqa: E402
from session import Session                              # noqa: E402
from vmslot import Slot                                  # noqa: E402


class FakeProc:
    """只满足 Session.attached 的最小接口。"""

    def alive(self) -> bool:
        return True

    def close(self) -> None:
        pass


def _patch_slots(mapping: dict) -> object:
    """把 scan_slots 换成给定结果，返回原函数以便还原。"""
    old = session_mod.scan_slots
    session_mod.scan_slots = lambda proc, want=None: {
        vid: s for vid, s in mapping.items() if want is None or vid in want}
    return old


def test_autolocate_binds_all_and_completes() -> None:
    s = Session({"backup_saves": False})
    s.proc = FakeProc()
    slots = {vid: Slot(0x1000 + i * 0x70, 0x3000 + i * 0x10, 1, vid)
             for i, vid in enumerate(s.var_ids.values())}

    old = _patch_slots(slots)
    try:
        result = s.autolocate()
    finally:
        session_mod.scan_slots = old

    assert result.complete, result.missing
    assert len(result.bound) == len(s.var_ids)
    assert s.addresses["money"] == slots[7779].value_addr
    assert "money" in s.from_var_id


def test_autolocate_drops_stale_address() -> None:
    """读档进行中扫描会落空：旧地址必须清掉，不能留着野指针。"""
    s = Session({"backup_saves": False})
    s.proc = FakeProc()
    s.addresses["money"] = 0xDEAD0000
    s.from_var_id.add("money")

    old = _patch_slots({})
    try:
        result = s.autolocate()
    finally:
        session_mod.scan_slots = old

    assert s.addresses["money"] is None
    assert "money" not in s.from_var_id
    assert "money" in result.missing
    assert not result.complete


def main() -> None:
    for fn in (test_autolocate_binds_all_and_completes,
               test_autolocate_drops_stale_address):
        fn()
        print(f"  ok  {fn.__name__}")
    print("全部通过")


if __name__ == "__main__":
    main()
