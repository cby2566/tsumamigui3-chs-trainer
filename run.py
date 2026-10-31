"""System4 C_INT 数值修改器 —— 入口。

必须用带 tkinter + numpy 的解释器运行。若当前解释器缺依赖，
本脚本会自动切到一个已知可用的解释器上。游戏目录/进程名在 config.json 里配。
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
for sub in ("core", "ui", "tools"):
    path = os.path.join(ROOT, sub)
    if path not in sys.path:
        sys.path.insert(0, path)

# 已知可用的解释器（按优先级）
PY_CANDIDATES = [
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python313\python.exe"),
    r"C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe",
    r"C:\Python313\python.exe",
]


def _missing() -> list[str]:
    missing = []
    for mod in ("tkinter", "numpy"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    return missing


def _relaunch() -> bool:
    me = os.path.abspath(sys.executable)
    for cand in PY_CANDIDATES:
        if not os.path.exists(cand):
            continue
        if os.path.abspath(cand).lower() == me.lower():
            continue
        try:
            subprocess.Popen([cand, os.path.abspath(__file__), *sys.argv[1:]])
            return True
        except OSError:
            continue
    return False


def main() -> int:
    missing = _missing()
    if missing:
        if _relaunch():
            return 0
        print("缺少依赖：" + "、".join(missing))
        print("请用带 tkinter 和 numpy 的 Python 运行，例如：")
        print(r'  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" run.py')
        return 1

    from app import App
    from session import Session

    app = App(Session())
    app.log("先把游戏开起来，然后点右上角「附加游戏」")
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
