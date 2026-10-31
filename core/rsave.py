"""AliceSoft System4 存档（.asd / rsave）的解析与原地改写。

用法只限「离线改档」：游戏关掉 → 改 .asd → 进游戏读档生效。
它不碰进程内存，所以没有地址漂移、变量号、多副本这些问题。

容器格式（实测本作 ver=9）::

    "GD\\x01\\x01" | u32 解压长度 | zlib 流
    解压后：
    "RSM" | u32 version | key(cstr) | comments(str_array)
          | 返回记录 + 调用栈 + 调用帧
          | u32 n_heap + heap 对象[ n_heap ]
          | func_names(str_array)        # ver>=6

heap 对象 tag::

    0  全局帧     slots[i] = 全局槽 i 指向的对象（ref）
    1  局部帧
    2  字符串
    3  数组       slots[i] = 元素 ref
    4  结构体     slots[i] = 成员 ref（C_INT 的 4 个 int 直接存在槽里）
    5  委托
   -1  空

**每个槽在解压后的 buffer 里都有确定的字节偏移**（`slot_offsets`），
改写时直接 `struct.pack_into` 回去即可，不需要重排堆。

关键结构（本作实测）：

    C_GAME_PARAMETER            C_HEROINE_PARAMETER_BASE
      [3] gamemode                [0] key(字符串)   [1] level
      [4] date                    [2] seed(愛盾)    [3] seed_turn
      [5] timezone                [4] add_seed      [5] semen
      [7] energy                  [6] enemy_semen   [7] support_semen
      [8] money                   [8] relation
      [9] place

C_INT 的 4 个槽依次是 value / code / backup / pattern，写入时按游戏算法
重算（见 `cint.encode`），pattern 固定 7/7 走纯 XOR。

本模块只依赖 `cint`，不依赖任何第三方修改器代码。
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

from cint import encode

M32 = 0xFFFFFFFF
ASD_MAGIC = b"GD\x01\x01"
RSM_MAGIC = b"RSM"

TAG_GLOBALS = 0
TAG_LOCALS = 1
TAG_STRING = 2
TAG_ARRAY = 3
TAG_STRUCT = 4
TAG_DELEGATE = 5
TAG_NULL = -1

SUPPORTED_VERSIONS = (4, 6, 7, 9)


class SaveError(Exception):
    """存档结构不符合预期。"""


class _Reader:
    def __init__(self, buf: bytes):
        self.b = buf
        self.i = 0

    def i32(self) -> int:
        if self.i + 4 > len(self.b):
            raise SaveError("存档在 %d 处提前结束" % self.i)
        v = struct.unpack_from("<i", self.b, self.i)[0]
        self.i += 4
        return v

    def cstr(self) -> str:
        end = self.b.find(b"\x00", self.i)
        if end < 0:
            raise SaveError("字符串没有终止符 @%d" % self.i)
        s = self.b[self.i:end].decode("utf-8", "replace")
        self.i = end + 1
        return s

    def int_array(self) -> list[int]:
        n = self.i32()
        if n < 0 or n > 1_000_000:
            raise SaveError("数组长度异常 %d @%d" % (n, self.i))
        return [self.i32() for _ in range(n)]

    def str_array(self) -> list[str]:
        n = self.i32()
        if n < 0 or n > 1_000_000:
            raise SaveError("字符串数组长度异常 %d" % n)
        return [self.cstr() for _ in range(n)]


@dataclass
class HeapObject:
    """heap 里的一个对象。"""

    index: int
    tag: int
    struct_type: str | None = None
    func_name: str | None = None
    text: str | None = None
    types: list[int] = field(default_factory=list)
    slots: list[int] = field(default_factory=list)
    slot_offsets: list[int] = field(default_factory=list)

    @property
    def is_cint(self) -> bool:
        return self.tag == TAG_STRUCT and len(self.slots) >= 4

    def __repr__(self) -> str:
        kind = {TAG_GLOBALS: "globals", TAG_LOCALS: "locals", TAG_STRING: "string",
                TAG_ARRAY: "array", TAG_STRUCT: "struct", TAG_DELEGATE: "delegate",
                TAG_NULL: "null"}.get(self.tag, f"tag{self.tag}")
        extra = self.struct_type or self.text or ""
        return f"#{self.index} {kind} {extra}".strip()


def _read_ret_record(r: _Reader) -> None:
    if r.i32() == -1:
        return
    r.cstr()          # caller_func
    r.i32()           # local_addr
    r.i32()           # crc


def _read_heap_object(r: _Reader, index: int, ver: int) -> HeapObject:
    tag = r.i32()
    if tag == TAG_NULL:
        return HeapObject(index, TAG_NULL)

    obj = HeapObject(index, tag)
    r.i32()                                  # ref（对象自己的编号）
    if ver >= 9:
        r.i32()                              # seq

    if tag in (TAG_GLOBALS, TAG_LOCALS):
        if tag == TAG_GLOBALS:
            if r.i32() != -1:
                raise SaveError("全局帧的占位字段不是 -1")
        else:
            obj.func_name = r.cstr()
        obj.types = r.int_array()
        if tag == TAG_LOCALS and ver >= 9:
            r.i32()                          # struct_ptr
        size = r.i32()
        n = size // 4
        for _ in range(n):
            obj.slot_offsets.append(r.i)
            obj.slots.append(r.i32())
        return obj

    if tag == TAG_STRING:
        r.i32()                              # uk
        ln = r.i32()
        if ln < 0 or r.i + ln > len(r.b):
            raise SaveError("字符串长度异常 %d" % ln)
        obj.text = r.b[r.i:r.i + ln].decode("utf-8", "replace").rstrip("\x00")
        r.i += ln
        return obj

    if tag == TAG_ARRAY:
        r.i32()                              # rank_minus_1
        r.i32()                              # data_type
        if ver != 4:
            r.cstr()                         # struct_type
        else:
            r.i32()
        r.i32()                              # root_rank
        r.i32()                              # is_not_empty
        size = r.i32()
        n = size // 4
        for _ in range(n):
            obj.slot_offsets.append(r.i)
            obj.slots.append(r.i32())
        return obj

    if tag == TAG_STRUCT:
        if ver != 4:
            r.cstr()                         # ctor
            r.cstr()                         # dtor
            r.i32()                          # uk
            obj.struct_type = r.cstr()
        else:
            r.i32(); r.i32(); r.i32()
            obj.struct_type = str(r.i32())
        obj.types = r.int_array()
        size = r.i32()
        n = size // 4
        for _ in range(n):
            obj.slot_offsets.append(r.i)
            obj.slots.append(r.i32())
        return obj

    if tag == TAG_DELEGATE:
        size = r.i32()
        r.i += size
        return obj

    raise SaveError("未知的 heap 对象类型 %d @#%d" % (tag, index))


def parse_rsave(raw: bytes) -> tuple[int, list[str], list[HeapObject]]:
    """解析解压后的 rsave 数据，返回 (version, comments, heap)。"""
    if raw[:3] != RSM_MAGIC:
        raise SaveError("不是 rsave 数据（开头是 %r）" % raw[:3])
    r = _Reader(raw)
    r.i = 4
    ver = r.i32()
    if ver not in SUPPORTED_VERSIONS:
        raise SaveError("不支持的 rsave 版本 %d" % ver)
    r.cstr()                                  # key
    comments = r.str_array() if ver >= 7 else []

    if r.i >= len(raw):
        return ver, comments, []

    _read_ret_record(r)
    r.i32()                                   # uk1
    r.int_array()                             # stack
    lp = r.int_array()
    ft = r.int_array()
    r.int_array()                             # stack_pages
    if len(lp) != len(ft):
        raise SaveError("调用帧错乱：local_pages=%d func_types=%d" % (len(lp), len(ft)))
    n = r.i32()
    for _ in range(n):
        _read_ret_record(r)
    r.i32(); r.i32(); r.i32()                 # uk2..4
    if ver >= 9:
        r.i32()                               # next_seq

    n = r.i32()
    if n < 0 or n > 5_000_000:
        raise SaveError("heap 对象数异常 %d" % n)
    heap = [_read_heap_object(r, i, ver) for i in range(n)]
    # 末尾还有 func_names（ver>=6），改档不需要，忽略。
    return ver, comments, heap


@dataclass
class SaveFile:
    """一个已解压、可原地改写的存档。"""

    path: str
    raw: bytearray
    version: int
    comments: list[str]
    heap: list[HeapObject]

    # ---------- 载入 / 保存 ----------

    @classmethod
    def load(cls, path: str) -> "SaveFile":
        with open(path, "rb") as f:
            blob = f.read()
        return cls.load_bytes(blob, path)

    @classmethod
    def load_bytes(cls, blob: bytes, path: str = "<memory>") -> "SaveFile":
        if blob[:4] != ASD_MAGIC:
            raise SaveError("不是 System4 存档（开头是 %r）" % blob[:4])
        if len(blob) < 8:
            raise SaveError("存档文件过短")
        declared = struct.unpack_from("<I", blob, 4)[0]
        try:
            raw = zlib.decompress(blob[8:])
        except zlib.error as exc:
            raise SaveError("解压失败：%s" % exc) from exc
        if len(raw) != declared:
            raise SaveError("解压长度不符：头写 %d，实得 %d" % (declared, len(raw)))
        ver, comments, heap = parse_rsave(raw)
        return cls(path, bytearray(raw), ver, comments, heap)

    def to_bytes(self) -> bytes:
        comp = zlib.compress(bytes(self.raw), 1)
        return ASD_MAGIC + struct.pack("<I", len(self.raw)) + comp

    def save(self, path: str | None = None) -> None:
        target = path or self.path
        with open(target, "wb") as f:
            f.write(self.to_bytes())

    # ---------- 对象访问 ----------

    def obj(self, ref: int) -> HeapObject:
        if not (0 <= ref < len(self.heap)):
            raise SaveError("ref %d 越界（heap 只有 %d 项）" % (ref, len(self.heap)))
        return self.heap[ref]

    def globals_frame(self) -> HeapObject:
        for o in self.heap:
            if o.tag == TAG_GLOBALS:
                return o
        raise SaveError("存档里没有全局帧")

    def global_ref(self, gidx: int) -> int:
        """全局槽 gidx 指向的对象 ref；-1 表示空。"""
        slots = self.globals_frame().slots
        if not (0 <= gidx < len(slots)):
            raise SaveError("全局槽 %d 越界（共 %d 个）" % (gidx, len(slots)))
        return slots[gidx]

    # ---------- C_INT 读写 ----------

    def cint_value(self, ref: int) -> int:
        o = self.obj(ref)
        if not o.is_cint:
            raise SaveError("#%d 不是 C_INT（tag=%d, slots=%d）"
                            % (ref, o.tag, len(o.slots)))
        return o.slots[0] & M32

    def cint_set(self, ref: int, value: int) -> None:
        """按游戏算法重算 4 个字段并原地写回 buffer。"""
        o = self.obj(ref)
        if not o.is_cint:
            raise SaveError("#%d 不是 C_INT" % ref)
        fields = encode(int(value) & M32)
        for k in range(4):
            v = fields[k]
            packed = v if v < 2 ** 31 else v - 2 ** 32
            struct.pack_into("<i", self.raw, o.slot_offsets[k], packed)
            o.slots[k] = packed

    # ---------- 语义封装 ----------

    #: 本作实测的全局槽位号（不同版本可能不同，调用方可覆盖）
    GAME_PARAM_SLOT = 185
    HEROINE_PARAM_SLOT = 186

    def game_param(self) -> HeapObject:
        return self.obj(self.global_ref(self.GAME_PARAM_SLOT))

    def heroine_param(self) -> HeapObject:
        return self.obj(self.global_ref(self.HEROINE_PARAM_SLOT))

    def basic_refs(self) -> dict[str, int]:
        gp = self.game_param()
        return {"gamemode": gp.slots[3], "date": gp.slots[4],
                "timezone": gp.slots[5], "energy": gp.slots[7],
                "money": gp.slots[8], "place": gp.slots[9]}

    def heroines(self) -> list[dict]:
        """返回 4 条女主记录（key + 各字段的 C_INT ref）。"""
        hp = self.heroine_param()
        arr = self.obj(hp.slots[0])
        out = []
        for ref in arr.slots:
            if ref == -1:
                continue
            h = self.obj(ref)
            key_obj = self.obj(h.slots[0])
            out.append({
                "key": key_obj.text or str(h.slots[0]),
                "level": h.slots[1], "seed": h.slots[2],
                "seed_turn": h.slots[3], "add_seed": h.slots[4],
                "semen": h.slots[5], "enemy_semen": h.slots[6],
                "support_semen": h.slots[7], "relation": h.slots[8],
            })
        return out
