"""C_INT —— 本作（System 4.3）的金币/精力容器。

完整的格式与算法笔记见 `docs/RE_NOTES.md`（ain 结构、分派表、踩过的坑都在那里）。
下面是本模块用到的部分。

结构体定义（ain 里 struct #191，共 4 个 int = 16 字节）：

    struct C_INT {
        int m_nValue;        // 明文值
        int m_nCode;         // 校验码  = Enc(nValue, xorKey, 高位 pattern) ^ nKey
        int m_nBackUpCode;   // 备份码  = Enc(nValue, xorKey, 低位 pattern)
        int m_nPattern;      // (cord << 16) | backup，两半各是 0..7 的随机数
    };

由反汇编还原的算法（函数 #6568~#6575、#6595~#6621）：

    xorKey(nValue) = 0xB47C275A ^ ((nValue << 8) | nValue) ^ 0xAAAAAAAA
    cord   = (m_nPattern >> 16) & 0xFFFF
    backup =  m_nPattern        & 0xFFFF
    m_nCode       = Enc(m_nValue, xorKey, cord  ) ^ nKey
    m_nBackUpCode = Enc(m_nValue, xorKey, backup)

    Enc(v, k, pattern) 按 (pattern % 8) + 1 分派到 Encrytion01..08，
    但**分派表是打乱的**：

        nIndex = pattern % 8 + 1
        nIndex: 8    7    1    5    4    2    6    3
        实际:   01   02   03   04   05   06   07   08

    只有 nIndex == 8（即 pattern % 8 == 7）才落到 **Encrytion01 == v ^ k**。
    踩过的坑：一开始按 pattern = 0 写入，结果 0 → nIndex 1 → Encrytion03，
    游戏解出来是乱值，金币直接变 0（GetMoney 把它钳到 0）。

`C_INT@Get(nKey)` 的流程是：
    1. 用 m_nCode 解出 nValue，用 m_nBackUpCode 解出 nBackUp；
    2. 两者不相等 -> 判定被篡改：先把 m_nValue 塞回去"自愈"，仍不等则
       把全局错误标志置位、返回值变 0；
    3. 相等则返回 m_nValue。
所以**只改 m_nValue 是没用的**：Get 返回的是 m_nCode 解出来的值，而且
还会校验两个码是否自洽。

破解办法（本模块采用）：让 pattern 的两半都取 7，于是
`nIndex = 7 % 8 + 1 = 8`，两个码都落到 Encrytion01（纯 XOR），只要令

    m_nCode = m_nBackUpCode = m_nValue ^ xorKey(m_nValue)

就同时满足「两码自洽」与「解得原值」，Get 会原样返回。金币的 nKey 是 0
（见 C_GAME_PARAMETER@GetMoney / @SetMoney 反汇编），所以上式不再需要 key 参数。

金币的归属：C_GAME_PARAMETER（struct #239）的第 8 个成员 m_nMoney，
结构体内偏移 112；同结构体第 7 个成员是 m_nEnergy（偏移 96），
可以拿来做「相邻 C_INT」的二次确认。
"""

from __future__ import annotations

import bisect
import struct
from dataclasses import dataclass

import numpy as np

from scan import CHUNK, Scanner
from winmem import PAGE_EXECUTE_READWRITE, PAGE_READWRITE, Process

MASK32 = 0xFFFF_FFFF
KEY_A = 0xB47C275A
KEY_B = 0xAAAAAAAA          # 反汇编里是 -0x55555556

MONEY_MAX = 999_999         # C_GAME_PARAMETER@SetMoney 里的 Clamp(0, 0xF423F)
ENERGY_MAX = 20             # C_GAME_PARAMETER@SetEnergy 里的 Clamp(0, 0x14)
DATE_MAX = 9999             # m_nDate 存的是「经过天数」，SSG 给的显示上限也是 9999
# 受精值上限按女主不同，取自游戏里显示的最大值（SSG 里统一写 999，偏大）
FERTILIZE_MAX = 999         # 兜底上限
FERTILIZE_MAX_KAEDE = 150   # 海夕里
FERTILIZE_MAX_SAYOKO = 250  # 砂夜子
FERTILIZE_MAX_AOI = 100     # 神野 藍
FERTILIZE_MAX_SUMIRE = 999  # 神野 菫
MONEY_MEMBER_OFFSET = 112   # m_nMoney 在 C_GAME_PARAMETER 内的偏移
ENERGY_MEMBER_OFFSET = 96   # m_nEnergy，用于邻接确认

# 注意：金币和精力是**各自独立分配的 C_INT 堆对象**，不是结构体内相邻的成员，
# 所以「精力地址 + 固定偏移 = 金币地址」这种推算**不成立**。
# 实测三次启动的差值分别是 +0x30、+0x30、-0x150 —— 前两次一致纯属堆分配巧合。
# 要稳定定位请走变量号（见 vmslot.py），不要用偏移猜。

PATTERN_HALF_MAX = 8        # SetEncryptionPattern 里 rand 的上界
PATTERN_XOR = 7             # 令 pattern % 8 == 7，分派到 nIndex 8 = Encrytion01(纯 XOR)


def xor_key(value: int) -> int:
    """由明文值推出结构体内部使用的 XOR key（32 位）。"""
    v = value & MASK32
    mixed = ((v << 8) | v) & MASK32
    return (KEY_A ^ mixed ^ KEY_B) & MASK32


# ---------- C_VALUE_ENCRYPTION 的 8 个变体 ----------
# 由 ain 字节码逐条还原。Encrytion01..08 与 Decryption01..08 互为逆运算。

_A1 = 0xFF00FFFF
_A2 = 0x00FF00FF


def _compound(hi_top: int, hi_bottom: int, low_top: int, low_bottom: int) -> int:
    return (((hi_top & 0xFF) << 24) | ((hi_bottom & 0xFF) << 16)
            | ((low_top & 0xFF) << 8) | (low_bottom & 0xFF)) & MASK32


def _hi_top(v: int) -> int:
    return (v >> 24) & 0xFF


def _hi_bottom(v: int) -> int:
    return (v >> 16) & 0xFF


def _low_top(v: int) -> int:
    return (v >> 8) & 0xFF


def _low_bottom(v: int) -> int:
    return v & 0xFF


def _enc05(v: int) -> int:
    return _compound(_hi_bottom(v), _low_bottom(v), _hi_top(v), _low_top(v))


def _dec05(v: int) -> int:
    return _compound(_low_top(v), _hi_top(v), _low_bottom(v), _hi_bottom(v))


def _enc06(v: int) -> int:
    return _compound(_low_bottom(v), _low_top(v), _hi_bottom(v), _hi_top(v))


def encrypt(value: int, key: int, pattern: int) -> int:
    """对应 C_VALUE_ENCRYPTION@Encrytion，含被打乱的分派表。"""
    v = value & MASK32
    k = key & MASK32
    idx = (pattern % 8) + 1
    if idx == 8:                      # Encrytion01
        return (v ^ k) & MASK32
    if idx == 7:                      # Encrytion02
        return ((v ^ MASK32) ^ k) & MASK32
    if idx == 1:                      # Encrytion03
        v = v ^ _A1
        v = _enc05(v)
        return (v ^ k) & MASK32
    if idx == 5:                      # Encrytion04
        v = v ^ k
        v = v ^ _A1
        v = _enc05(v)
        return (v ^ k) & MASK32
    if idx == 4:                      # Encrytion05
        return _enc05(v)
    if idx == 2:                      # Encrytion06
        return _enc06(v)
    if idx == 6:                      # Encrytion07
        v = v ^ _A2
        v = _enc06(v)
        return (v ^ k) & MASK32
    # idx == 3 -> Encrytion08
    v = v ^ k
    v = v ^ _A2
    v = _enc06(v)
    return (v ^ k) & MASK32


def decrypt(code: int, key: int, pattern: int) -> int:
    """对应 C_VALUE_ENCRYPTION@Decryption，是 encrypt 的逆运算。"""
    v = code & MASK32
    k = key & MASK32
    idx = (pattern % 8) + 1
    if idx == 8:                      # Decryption01
        return (v ^ k) & MASK32
    if idx == 7:                      # Decryption02
        return ((v ^ k) ^ MASK32) & MASK32
    if idx == 1:                      # Decryption03
        v = v ^ k
        v = _dec05(v)
        return (v ^ _A1) & MASK32
    if idx == 5:                      # Decryption04
        v = v ^ k
        v = _dec05(v)
        v = v ^ _A1
        return (v ^ k) & MASK32
    if idx == 4:                      # Decryption05
        return _dec05(v)
    if idx == 2:                      # Decryption06
        return _enc06(v)
    if idx == 6:                      # Decryption07
        v = v ^ k
        v = _enc06(v)
        return (v ^ _A2) & MASK32
    # idx == 3 -> Decryption08
    v = v ^ k
    v = _enc06(v)
    v = v ^ _A2
    return (v ^ k) & MASK32


def encode(value: int, nkey: int = 0) -> tuple[int, int, int, int]:
    """把明文值编成一组自洽的 (m_nValue, m_nCode, m_nBackUpCode, m_nPattern)。"""
    v = value & MASK32
    k = xor_key(v)
    code = (encrypt(v, k, PATTERN_XOR) ^ (nkey & MASK32)) & MASK32
    backup = encrypt(v, k, PATTERN_XOR)
    pattern = (PATTERN_XOR << 16) | PATTERN_XOR
    return v, code, backup, pattern


def decode(fields: tuple[int, int, int, int], nkey: int = 0) -> int | None:
    """按游戏自己的算法解出明文值；不像合法 C_INT 则返回 None。

    注意：全零的 16 字节在数学上是「自洽」的（decrypt(0,k) 两边都等于 k），
    所以必须额外要求两个校验码非零，否则一整片空闲内存都会被当成值。
    """
    v, code, backup, pattern = fields
    if code == 0 and backup == 0:
        return None
    cord = (pattern >> 16) & 0xFFFF
    bpat = pattern & 0xFFFF
    k = xor_key(v & MASK32)
    nv = decrypt((code & MASK32) ^ (nkey & MASK32), k, cord)
    nb = decrypt(backup & MASK32, k, bpat)
    if nv != nb:
        return None
    return nv


def decode_loose(fields: tuple[int, int, int, int], nkey: int = 0) -> int | None:
    """宽松解码：主码对不上时，只要「明文和备份码一致」就认明文。

    实测有些 C_INT 的 m_nCode 编码的是 `0xFF00 | 值` 而不是值本身
    （受精值 20 → 主码解出 0xFF14，精力 10 → 主码解出 0xFF0A），
    而 m_nValue 和 m_nBackUpCode 都是正确的。严格 decode 要求两个码
    解出同一个数，遇到这种就会判成「数据损坏」。

    这类数值游戏自己能正常读（界面显示没问题），所以定位和显示时
    用这个宽松版本；只有写回校验仍然走严格 decode。
    """
    v, code, backup, pattern = fields
    d = decode(fields, nkey)
    if d is not None:
        # 还有更彻底的一种：连两个校验码都按 `0xFF00 | 值` 编码，
        # 于是严格解码「成功」但解出 0xFF28(65320)，而明文是 40。
        # 高字节为 0xFF 且低字节与明文一致时，认明文。
        if d != v and v <= 0xFF and (d >> 8) == 0xFF and (d & 0xFF) == v:
            return v
        return d
    if code == 0 and backup == 0:
        return None
    k = xor_key(v & MASK32)
    # 第四种形态：只有一个码是好的，另一个带着 0xFF 高字节（甚至整个坏掉）。
    # 实测日期对象：主码解出 232（正确），备份码解出 65512(0xFF68)（坏的）。
    # 只要有一个码能和明文自洽，就认明文。
    if decrypt((code ^ nkey) & MASK32, k, (pattern >> 16) & 0xFFFF) == (v & MASK32):
        return v
    if decrypt(backup & MASK32, k, pattern & 0xFFFF) == (v & MASK32):
        return v
    return None


def days_to_date(days: int) -> str:
    """把 m_nDate 的值转成「X月Y日」。

    实测这个字段存的是「从当年 1 月 1 日算起的天数」，不是从剧情开始算：
    8月4日 存的是 216（31+28+31+30+31+30+31+4），按平年算。
    """
    if days < 1:
        return "—"
    months = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    m, left = 1, days
    for length in months:
        if left <= length:
            return f"{m}月{left}日"
        left -= length
        m += 1
    return f"第 {days} 天"


def date_to_days(month: int, day: int) -> int:
    """「X月Y日」转回天数，供需要反向换算时用。"""
    months = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    if not (1 <= month <= 12 and 1 <= day <= months[month - 1]):
        raise ValueError(f"{month}月{day}日 不合法")
    return sum(months[:month - 1]) + day


@dataclass
class Candidate:
    address: int
    value: int
    code: int
    backup: int
    pattern: int
    has_neighbour_cint: bool = False

    @property
    def cord(self) -> int:
        return (self.pattern >> 16) & 0xFFFF

    @property
    def backup_pattern(self) -> int:
        return self.pattern & 0xFFFF

    @property
    def fields(self) -> tuple[int, int, int, int]:
        """(m_nValue, m_nCode, m_nBackUpCode, m_nPattern)，喂给 encode/decode 用。"""
        return self.value, self.code, self.backup, self.pattern

    @property
    def decoded(self) -> int | None:
        return decode(self.fields)

    @property
    def decoded_loose(self) -> int | None:
        """宽松解码，见模块里的 decode_loose()。用于定位和显示，不用于写回校验。"""
        return decode_loose(self.fields)

    def __str__(self) -> str:
        return (f"0x{self.address:08X}  值={self.value}  pattern={self.cord}/"
                f"{self.backup_pattern}" + ("  [相邻有 C_INT]" if self.has_neighbour_cint else ""))


def _plausible_cint(a: np.ndarray, i: int) -> bool:
    """判断 a[i:i+4] 看起来像不像一个 C_INT（弱判据，只用于邻接确认）。"""
    if i < 0 or i + 4 > len(a):
        return False
    _, code, backup, pattern = a[i:i + 4]
    return (code != 0 and backup != 0
            and ((pattern >> 16) & 0xFFFF) < PATTERN_HALF_MAX
            and (pattern & 0xFFFF) < PATTERN_HALF_MAX)


def scan(proc: Process, money: int, scanner: Scanner | None = None,
         strict: bool = True, near: int | None = None) -> list[Candidate]:
    """在可写私有内存里找「值等于 money 且结构像 C_INT」的 16 字节窗口。

    strict=True 时还会用游戏自己的算法把两个校验码解一遍，
    要求解出来的值正好等于 money —— 这一步能干掉绝大多数巧合命中，
    对付「精力」这种只有 0~20 的小数值尤其必要。

    near 给一个已知同族地址（比如金币），候选会按离它由近到远排序：
    金币和精力都是同一套游戏参数对象里的成员，通常挨得很近。
    """
    sc = scanner or Scanner(proc)
    sc.refresh()
    target = money & MASK32
    found: dict[int, Candidate] = {}

    for r in sc.regions:
        off = 0
        while off < r.size:
            n = min(CHUNK, r.size - off)
            buf = proc.read(r.base + off, n)
            got = len(buf)
            if got == 0:
                break                   # 区域已被释放，跳过；不能按 4 字节原地推
            usable = (got // 4) * 4
            if usable >= 16:
                a = np.frombuffer(buf[:usable], dtype="<u4")
                v0, v1, v2, v3 = a[:-3], a[1:-2], a[2:-1], a[3:]
                mask = ((v0 == target) & (v1 != 0) & (v2 != 0)
                        & ((v3 >> 16) < PATTERN_HALF_MAX)
                        & ((v3 & 0xFFFF) < PATTERN_HALF_MAX))
                for idx in np.flatnonzero(mask):
                    addr = r.base + off + int(idx) * 4
                    if addr in found:
                        continue
                    cand = Candidate(
                        address=addr,
                        value=int(v0[idx]), code=int(v1[idx]),
                        backup=int(v2[idx]), pattern=int(v3[idx]),
                        has_neighbour_cint=_plausible_cint(a, int(idx) - 4))
                    if strict and cand.decoded != money:
                        continue
                    found[addr] = cand
            # 留 12 字节重叠，保证 4 字节对齐的 16 字节结构跨块时也不漏
            off += max(got - 12, 4)
    # 先给「相邻有 C_INT」的候选优先（大概率就是 C_GAME_PARAMETER 里的那个）；
    # 有 near 时再按离 near 的距离排（精力/金币同族，通常挨得很近）
    def rank(c: Candidate):
        dist = abs(c.address - near) if near is not None else 0
        return (dist, not c.has_neighbour_cint, c.address)

    return sorted(found.values(), key=rank)


def scan_all(proc: Process, scanner: Scanner | None = None) -> list[Candidate]:
    """找出所有「自洽」的 C_INT（解出来的值 == 明文值）。

    注意：C_INT 子页在堆里不是连续排布的，不能按固定偏移或 16 字节步进串联；
    这里只做全量枚举，给 scan_bool() 做邻域统计用。
    """
    sc = scanner or Scanner(proc)
    sc.refresh()
    found: dict[int, Candidate] = {}

    for r in sc.regions:
        # 只碰普通可写数据页；排除可执行页，避免把代码里的常量当成对象写坏。
        if not (r.protect & PAGE_READWRITE) or (r.protect & PAGE_EXECUTE_READWRITE):
            continue
        off = 0
        while off < r.size:
            buf = proc.read(r.base + off, min(CHUNK, r.size - off))
            if not buf:
                break
            usable = (len(buf) // 4) * 4
            if usable >= 16:
                b = np.frombuffer(buf[:usable], dtype="<u4")
                # C_INT 可能落在任意 4 字节边界，四个相位都要覆盖
                for phase in range(4):
                    m = (len(b) - phase) // 4
                    if m < 1:
                        continue
                    a = b[phase:phase + 4 * m].reshape(-1, 4)
                    code, backup, pattern = a[:, 1], a[:, 2], a[:, 3]
                    mask = ((code != 0) & (backup != 0)
                            & ((pattern >> 16) < PATTERN_HALF_MAX)
                            & ((pattern & 0xFFFF) < PATTERN_HALF_MAX))
                    for idx in np.flatnonzero(mask):
                        addr = r.base + off + (phase + 4 * int(idx)) * 4
                        if addr in found:
                            continue
                        cand = Candidate(
                            address=addr,
                            value=int(a[idx, 0]), code=int(a[idx, 1]),
                            backup=int(a[idx, 2]), pattern=int(a[idx, 3]))
                        if cand.decoded != cand.value:
                            continue
                        found[addr] = cand
            # 16 字节重叠，避免 C_INT 跨块时漏掉；dict 会去重
            off += max(usable - 16, 4)
    return sorted(found.values(), key=lambda c: c.address)


@dataclass
class BoolCandidate:
    """一个值为 0/1 的 C_INT，附带邻域里的其他 C_INT（用于识别女主记录）。"""

    address: int
    value: int
    neighbours: list[tuple[int, int]]

    @property
    def near_values(self) -> list[int]:
        return [v for _, v in self.neighbours]

    def __str__(self) -> str:
        near = " ".join(str(v) for v in self.near_values[:8]) or "-"
        return f"0x{self.address:08X}  值={self.value}  邻近={near}"


def scan_bool(proc: Process, scanner: Scanner | None = None,
              near: int | None = None, radius: int = 0x800) -> list[BoolCandidate]:
    """找出所有值为 0/1 的 C_INT。

    布尔值只有两种取值，单靠值扫描会命中很多无关 C_INT；所以附带
    radius 范围内的邻域值，界面可以把「附近像不像一条女主记录」展示给用户。
    near 通常是金币地址：全局参数对象在同一个堆区，按距离排序能让候选更集中。
    """
    all_cints = scan_all(proc, scanner)
    addrs = [c.address for c in all_cints]
    out: list[BoolCandidate] = []
    for c in all_cints:
        if c.decoded not in (0, 1):
            continue
        lo = bisect.bisect_left(addrs, c.address - radius)
        hi = bisect.bisect_right(addrs, c.address + radius)
        neighbours = [(n.address, n.decoded) for n in all_cints[lo:hi]
                      if n.address != c.address]
        out.append(BoolCandidate(c.address, int(c.decoded), neighbours))

    def rank(b: BoolCandidate):
        dist = abs(b.address - near) if near is not None else 0
        return (dist, -len(b.neighbours), b.address)

    return sorted(out, key=rank)


def read_bool(proc: Process, address: int) -> int | None:
    """读一个布尔 C_INT；不是 0/1 就返回 None（地址可能已失效）。"""
    cand = read_at(proc, address)
    if cand is None or cand.decoded not in (0, 1):
        return None
    return int(cand.decoded)


def write_bool(proc: Process, address: int, value: bool | int) -> bool:
    """写布尔 C_INT：1=开，0=关。走的是和金币完全相同的 C_INT 编码。"""
    return write_money(proc, address, 1 if value else 0)


def read_at(proc: Process, address: int) -> Candidate | None:
    buf = proc.read(address, 16)
    if len(buf) != 16:
        return None
    v, code, backup, pattern = struct.unpack("<4i", buf)
    return Candidate(address, v, code, backup, pattern)


def write_money(proc: Process, address: int, value: int) -> bool:
    """把目标地址处的 C_INT 写成自洽的一组值，并回读校验。"""
    v, code, backup, pattern = encode(value)
    data = struct.pack("<4i", v, code, backup, pattern)
    if not proc.write(address, data):
        return False
    buf = proc.read(address, 16)
    if len(buf) != 16:
        return False
    fields = struct.unpack("<4i", buf)
    return fields == (v, code, backup, pattern) and decode(fields) == value
