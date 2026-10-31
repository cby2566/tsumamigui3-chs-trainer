"""rsave（.asd）解析与原地改写的自检。

跑法（在 game_tools 目录下）：
    python tests/test_rsave.py

不依赖游戏在跑；用真实存档做只读校验，再在内存里做一次写入往返。
存档目录取 config.json 的 save_dir。
"""

from __future__ import annotations

import glob
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "core"))

from cint import MONEY_MAX, ENERGY_MAX          # noqa: E402
from rsave import SaveFile                      # noqa: E402


def _first_save() -> str | None:
    try:
        with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
            save_dir = json.load(f).get("save_dir") or ""
    except (OSError, ValueError):
        return None
    if not save_dir or not os.path.isdir(save_dir):
        return None
    files = sorted(glob.glob(os.path.join(save_dir, "Tsumamigui3_*.asd")))
    files = [p for p in files if os.path.basename(p)[12:-4].isdigit()]
    return files[-1] if files else None


def test_parse_real_save() -> None:
    path = _first_save()
    if path is None:
        print("    （没有可用存档，跳过）")
        return
    doc = SaveFile.load(path)
    assert doc.version in (4, 6, 7, 9), doc.version

    basic = doc.basic_refs()
    assert 0 <= doc.cint_value(basic["money"]) <= MONEY_MAX
    assert 0 <= doc.cint_value(basic["energy"]) <= ENERGY_MAX

    heroes = doc.heroines()
    assert len(heroes) == 4, len(heroes)
    keys = [h["key"] for h in heroes]
    assert len(set(keys)) == 4, keys
    for h in heroes:
        assert doc.cint_value(h["seed"]) in (0, 1), (h["key"], doc.cint_value(h["seed"]))
        assert 0 <= doc.cint_value(h["level"]) <= 10
    print(f"    {os.path.basename(path)} ver={doc.version} 女主={keys}")


def test_write_roundtrip() -> None:
    path = _first_save()
    if path is None:
        print("    （没有可用存档，跳过）")
        return
    doc = SaveFile.load(path)
    ref = doc.heroines()[0]["semen"]
    old = doc.cint_value(ref)
    target = 7 if old != 7 else 8

    doc.cint_set(ref, target)
    again = SaveFile.load_bytes(doc.to_bytes(), doc.path)
    assert again.cint_value(ref) == target, (again.cint_value(ref), target)
    assert again.heroines()[0]["key"] == doc.heroines()[0]["key"]
    print(f"    semen {old} → {target} 写入并回读一致")


def main() -> None:
    for fn in (test_parse_real_save, test_write_roundtrip):
        fn()
        print(f"  ok  {fn.__name__}")
    print("全部通过")


if __name__ == "__main__":
    main()
