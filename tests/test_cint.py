"""布尔 C_INT（愛盾）的编解码自检。

跑法（在 game_tools 目录下）：
    python tests/test_cint.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))

from cint import BoolCandidate, decode, encode          # noqa: E402


def test_bool_roundtrip() -> None:
    for value in (0, 1):
        fields = encode(value)
        assert decode(fields) == value, (value, fields)
        assert (fields[3] >> 16) == 7 and (fields[3] & 0xFFFF) == 7


def test_bool_candidate_near_values() -> None:
    cand = BoolCandidate(0x1000, 1, [(0x0FF0, 0), (0x1010, 2)])
    assert cand.near_values == [0, 2]


def main() -> None:
    for fn in (test_bool_roundtrip, test_bool_candidate_near_values):
        fn()
        print(f"  ok  {fn.__name__}")
    print("全部通过")


if __name__ == "__main__":
    main()
