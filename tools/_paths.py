"""定位游戏目录与 ain 文件。

游戏身份（目录、进程名、存档目录）一律不写进代码：
优先环境变量 `GAME_DIR`，其次仓库根目录的 `config.json`（已 gitignore）。
"""

from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.json")


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    return {}


def game_dir() -> str:
    env = os.environ.get("GAME_DIR")
    if env and os.path.isdir(env):
        return env
    d = load_config().get("game_dir")
    if d and os.path.isdir(d):
        return d
    raise SystemExit(
        "找不到游戏目录：请设置 GAME_DIR 环境变量，"
        "或在 config.json 里填写 game_dir")


def ain_path() -> str:
    """游戏目录下的那个 .ain（正常只有一个）。"""
    d = game_dir()
    found = [f for f in os.listdir(d) if f.lower().endswith(".ain")]
    if not found:
        raise SystemExit(f"{d} 下没找到 .ain 文件")
    if len(found) > 1:
        raise SystemExit(f"{d} 下有多个 .ain：{found}，请在 GAME_DIR 里指定更精确的目录")
    return os.path.join(d, found[0])


def exe_name() -> str:
    name = load_config().get("exe_name")
    if not name:
        raise SystemExit("config.json 里没有填 exe_name（游戏进程名）")
    return name
