"""把 C_INT 相关函数反汇编出来，用于还原数值的存储/校验算法。

用法（游戏目录在 config.json 的 game_dir，或用 GAME_DIR 环境变量覆盖）：
    python tools/analyze_cint.py

输出直接打到终端；想存文件就重定向。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ain import Ain           # noqa: E402
from disasm import dump       # noqa: E402
from _paths import ain_path   # noqa: E402

TARGETS = [
    "C_INT@0",
    "C_INT@Get",
    "C_INT@Set",
    "C_INT@Add",
    "C_INT@Sub",
    "C_INT@GetKey",
    "C_INT@SetEncryptionPattern",
    "C_INT@GetCodePattern",
    "C_INT@GetBackUpCodePattern",
    "C_GAME_PARAMETER@GetMoney",
    "C_GAME_PARAMETER@SetMoney",
    "C_GAME_PARAMETER@AddMoney",
    "C_GAME_PARAMETER@SubMoney",
]


def main() -> None:
    ain_file = ain_path()
    ain = Ain.load(ain_file)

    print(f"# {os.path.basename(ain_file)}  VERS={ain.version}  "
          f"fun={len(ain.functions)} struct={len(ain.structs)}")
    cint = ain.struct("C_INT")
    print(f"# C_INT = struct #{cint.index}, 成员 "
          f"{[(m.name, m.type) for m in cint.members]}")
    gp = ain.struct("C_GAME_PARAMETER")
    if gp:
        print(f"# C_GAME_PARAMETER = struct #{gp.index}, 成员:")
        for i, m in enumerate(gp.members):
            print(f"#     [{i}] {m!r}")
    print()

    for name in TARGETS:
        print("=" * 100)
        print(dump(ain, name))
        print()


if __name__ == "__main__":
    main()
