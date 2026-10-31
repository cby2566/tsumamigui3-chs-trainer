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

import struct
from dataclasses import dataclass

import numpy as np

from scan import CHUNK, Scanner
from winmem import Process

MASK32 = 0xFFFF_FFFF
KEY_A = 0xB47C275A
KEY_B = 0xAAAAAAAA          # 反汇编里是 -0x55555556

MONEY_MAX = 999_999         # C_GAME_PARAMETER@SetMoney 里的 Clamp(0, 0xF423F)
ENERGY_MAX = 20             # C_GAME_PARAMETER@SetEnergy 里的 Clamp(0, 0x14)
MONEY_MEMBER_OFFSET = 112   # m_nMoney 在 C_GAME_PARAMETER 内的偏移
ENERGY_MEMBER_OFFSET = 96   # m_nEnergy，用于邻接确认

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
