"""VM 变量槽表扫描 —— 按变量号直接定位，不用按值搜。

AliceSoft 的 VM 给每个活变量准备一个 0x70 字节的描述槽，结构非常规整：

    +0x00  → 指向值对象（对 C_INT 变量来说就是 C_INT 的地址）
    +0x04     0x10        值大小 = 16
    +0x08     0x10
    +0x0C     4
    +0x18     变量号      编译期定死，跨进程重启不变

槽在堆上按 0x70 排布。用「+0x04 / +0x08 / +0x0C」这三连特征扫一遍，
就能把所有活变量捞出来，实测 0.5 ~ 0.6 秒。

**为什么用变量号而不是按值搜**：

  * 按值搜要人先在游戏界面读出数字，而界面值会因为「外部改内存不重绘」
    和内存分叉（详见 RE_NOTES 第 3 节），经常搜不到。
  * 同一个值在堆上可能有多份副本（实测精力有两份），按值搜会挑错。
  * 变量号是编译产物，只和脚本有关，跟堆布局、游戏版本都无关。

实测（同一台机器，跨重启）：
    金币 999899 → 变量号 7779 (0x1E63)，槽地址 0x06EB99B0 → 0x06460B40
    精力 20     → 变量号 7751 (0x1E47)，槽地址 0x06EB9EF0 → 0x06460AD0
地址全变，编号不变。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

from cint import decode, decode_loose
from scan import CHUNK

SLOT_STRIDE = 0x70          # 槽的排布间距
VAR_ID_OFFSET = 0x18        # 槽内变量号的偏移
SLOT_READ = 0x20            # 判定一个槽需要读到的字节数

# 实测得到的变量号；不同版本可能不同，所以允许从 config.json 覆盖
DEFAULT_VAR_IDS = {
    "money": 7779,          # 0x1E63  C_GAME_PARAMETER.m_nMoney
    "energy": 7751,         # 0x1E47  C_GAME_PARAMETER.m_nEnergy
    "date": 7515,           # 0x1D5B  C_GAME_PARAMETER.m_nDate（存的是经过天数）
    "fertilize_kaede_1": 8264,    # 0x2048  海夕里 ①（主人公）
    "fertilize_kaede_2": 8294,    # 0x2066  海夕里 ②
    "fertilize_sayoko_1": 8823,   # 0x2277  砂夜子 ①，实测 60
    "fertilize_sayoko_2": 8856,   # 0x2298  砂夜子 ②，实测 0
    "fertilize_sayoko_3": 8889,   # 0x22B9  砂夜子 ③
    "fertilize_aoi_1": 9352,      # 0x2488  神野 藍 ①
    "fertilize_sumire_1": 9884,   # 0x269C  神野 菫 ①
}


@dataclass(frozen=True)
class Slot:
    """一个变量槽。"""

    slot: int               # 槽自身的地址
    value_addr: int         # 值对象的地址（可直接读写）
    value: int | None       # 解码后的当前值；数据不自洽时为 None
    var_id: int             # 变量号

    def __str__(self) -> str:
        shown = "不可读" if self.value is None else self.value
        return f"变量号 {self.var_id} 值={shown} @0x{self.value_addr:08X}"


def scan_slots(proc, want: set[int] | None = None) -> dict[int, Slot]:
    """扫出变量槽，返回 {变量号: Slot}。

    want 给定时只收集这些变量号 —— 判定顺序是先比变量号再解码，
    绝大多数候选在第一次读之后就被丢掉，比全量枚举快得多。

    注意：指定 want 时**不再用解码结果过滤**。变量号本身就是唯一标识，
    哪怕 C_INT 的数据已经不自洽（比如被写坏、主校验码对不上），
    槽和地址仍然是可信的，照样要返回 —— 否则一次数据损坏就会让
    这个数值彻底定位不到。不指定 want 的全量枚举才需要解码来排除假槽。
    """
    out: dict[int, Slot] = {}
    for r in proc.regions():
        off = 0
        while off < r.size:
            buf = proc.read(r.base + off, min(CHUNK, r.size - off))
            got = len(buf)
            usable = (got // 4) * 4
            if usable >= 16:
                a = np.frombuffer(buf[:usable], dtype="<u4")
                v0, v1, v2, v3 = a[:-3], a[1:-2], a[2:-1], a[3:]
                # 三连特征；v0 必须是个像样的用户态地址
                mask = ((v1 == 0x10) & (v2 == 0x10) & (v3 == 4)
                        & ((v0 & 3) == 0) & (v0 > 0x10000) & (v0 < 0x7FFF_FFFF))
                for i in np.flatnonzero(mask):
                    s = r.base + off + int(i) * 4
                    b = proc.read(s, SLOT_READ)
                    if len(b) < SLOT_READ:
                        continue
                    vid = struct.unpack_from("<I", b, VAR_ID_OFFSET)[0]
                    if want is not None and vid not in want:
                        continue
                    if vid in out:
                        continue
                    ptr = struct.unpack_from("<I", b, 0)[0]
                    cb = proc.read(ptr, 16)
                    fields = struct.unpack("<4I", cb) if len(cb) == 16 else None
                    if fields is None:
                        continue
                    if want is None:
                        # 全量枚举要靠严格解码排掉假槽（否则堆上全是 0x10/0x10/4）
                        d = decode(fields)
                        if d is None:
                            continue
                    else:
                        # 指定了变量号，槽一定是我们想要的，用宽松解码读值，
                        # 免得主校验码被写成 0xFF00|值 的数值被当成「不可读」
                        d = decode_loose(fields)
                    out[vid] = Slot(s, ptr, d, vid)
            off += max(got - 12, 4)
    return out
