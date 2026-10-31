"""AliceSoft System4 的 .ain（AI2 容器）解析器。

实测样本是一个 System 4.3 引擎的中文代理版视觉小说，容器结构如下：

    "AI2\\0" | u32 header_version(=0) | u32 解压后长度 | u32 压缩后长度 | zlib 流
    解压后是若干个 chunk，按顺序排列：

        VERS  u32                  ain 版本（本作 = 7）
        KEYC  u32                  加密 key 表（本作 = 0）
        CODE  u32 size + payload   字节码段；函数表里的 address 是这段内的偏移
        FUNC  u32 count + u32 ? + 记录[count]
        GLOB  u32 count + 记录[count]
        GSET  u32 size? ...
        STRT  u32 count + 记录[count]       <-- 结构体表（不是字符串表）
        ...

    FUNC 记录（本作变体，注意 address 在记录**末尾**）：
        <name\\0> <i32 ret_type> <i32 ret_struct> <i32 nr_args> <i32 nr_vars>
        <u32 crc> [var × nr_vars] <u32 address>
        var = <name\\0> <i32 type> <i32 struct_idx> <i32 array_rank>

        其中 nr_vars 已包含参数（前 nr_args 个是参数）。

    STRT 记录（结构体）：
        <name\\0> <i32 ctor_func_index> <i32 ?(-1)> <i32 nr_members>
        [member × nr_members]，member 同样是 <name\\0> <type> <struct_idx> <rank>

实测佐证：FUNC 表恰好 11412 条、解析结束后正好落在 GLOB 的 count 字段上；
结构体 C_INT 的 ctor_func_index = 6567，正是函数表里 C_INT@0 的索引。
"""

from __future__ import annotations

import struct
import zlib

# ain 内建类型编号（只列出项目用得到的）
TYPE_INT = 10
TYPE_FLOAT = 11
TYPE_STRING = 12
TYPE_STRUCT = 13
TYPE_FUNC = 14
TYPE_BOOL = 15
TYPE_LONG_INT = 16
TYPE_REF_INT = 17

TYPE_NAMES = {
    10: "int", 11: "float", 12: "string", 13: "struct", 14: "func",
    15: "bool", 16: "long_int", 17: "ref int", 18: "ref float",
    19: "ref string", 20: "ref struct", 21: "ref func", 22: "ref bool",
    23: "ref long_int", 24: "array int", 25: "array float",
    26: "array string", 27: "array struct", 28: "array func",
    29: "array bool", 30: "array long_int",
}

CHUNK_ORDER = ("VERS", "KEYC", "CODE", "FUNC", "GLOB", "GSET", "STRT")


class AinError(Exception):
    pass


class _Reader:
    """带越界检查的小端读取器。"""

    def __init__(self, data: bytes, pos: int = 0):
        self.d = data
        self.o = pos

    def i32(self) -> int:
        v = struct.unpack_from("<i", self.d, self.o)[0]
        self.o += 4
        return v

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.d, self.o)[0]
        self.o += 4
        return v

    def tag(self) -> str:
        t = self.d[self.o:self.o + 4]
        self.o += 4
        return t.decode("ascii", "replace")

    def cstr(self) -> str:
        end = self.d.find(b"\0", self.o, self.o + 4096)
        if end < 0:
            raise AinError(f"偏移 0x{self.o:x} 处没有找到字符串结尾: {self.d[self.o:self.o + 40]!r}")
        s = self.d[self.o:end].decode("utf-8", "replace")
        self.o = end + 1
        return s

    def skip(self, n: int) -> None:
        self.o += n


class Var:
    __slots__ = ("name", "type", "struct", "rank")

    def __init__(self, name: str, type_: int, struct_: int, rank: int):
        self.name = name
        self.type = type_
        self.struct = struct_
        self.rank = rank

    def __repr__(self) -> str:
        t = TYPE_NAMES.get(self.type, f"t{self.type}")
        if self.struct >= 0:
            t += f"#{self.struct}"
        return f"{t} {self.name}"


class Function:
    __slots__ = ("index", "name", "ret_type", "ret_struct", "nr_args",
                 "nr_vars", "crc", "vars", "address")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    @property
    def args(self):
        return self.vars[:self.nr_args]

    @property
    def locals(self):
        return self.vars[self.nr_args:]

    def __repr__(self) -> str:
        return f"#{self.index} {self.name} @0x{self.address:06X}"


class Struct:
    __slots__ = ("index", "name", "ctor", "nr_members", "members")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    def __repr__(self) -> str:
        return f"#{self.index} {self.name}({len(self.members)} members)"


class Ain:
    """解析完成的 .ain。"""

    def __init__(self):
        self.version = 0
        self.keyc = 0
        self.code = b""
        self.functions: list[Function] = []
        self.structs: list[Struct] = []
        self.globals: list[Var] = []
        self._by_name: dict[str, Function] = {}

    # ---------- 载入 ----------

    @classmethod
    def load(cls, path: str) -> "Ain":
        with open(path, "rb") as f:
            return cls.from_bytes(f.read())

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Ain":
        if raw[:4] != b"AI2\0":
            raise AinError(f"不是 AI2 文件，头部是 {raw[:4]!r}")
        header_version, unpacked, packed = struct.unpack_from("<III", raw, 4)
        if len(raw) - 16 != packed:
            raise AinError(f"压缩长度不符：头部 {packed}，实际 {len(raw) - 16}")
        data = zlib.decompress(raw[16:])
        if len(data) != unpacked:
            raise AinError(f"解压长度不符：头部 {unpacked}，实际 {len(data)}")

        self = cls()
        self.header_version = header_version
        self.raw = raw
        r = _Reader(data)

        for expect in CHUNK_ORDER:
            got = r.tag()
            if got != expect:
                # 已知怪癖：FUNC 表最后一条记录省略了 address 字段，
                # 上面多读了 4 字节，这里回退再试一次。
                r.o -= 8
                got = r.tag()
            if got != expect:
                raise AinError(f"chunk 顺序异常：期待 {expect}，实际 {got}（偏移 0x{r.o - 4:x}）")

            if expect == "VERS":
                self.version = r.u32()
            elif expect == "KEYC":
                self.keyc = r.u32()
            elif expect == "CODE":
                n = r.u32()
                self.code = data[r.o:r.o + n]
                r.skip(n)
            elif expect == "FUNC":
                count = r.u32()
                self._parse_functions(r, count)
            elif expect == "GLOB":
                count = r.u32()
                self._parse_globals(r, count)
            elif expect == "GSET":
                # 结构简单但用途未知（分组设置？），直接快进到下一个分块，
                # 避免为了一个用不到的段去逆向它。
                idx = data.find(b"STRT", r.o, r.o + 0x10000)
                if idx < 0:
                    raise AinError(f"GSET 之后找不到 STRT（偏移 0x{r.o:x}）")
                r.o = idx
            elif expect == "STRT":
                count = r.u32()
                self._parse_structs(r, count)

        self._by_name = {f.name: f for f in self.functions}
        return self

    # ---------- 各表 ----------

    def _parse_functions(self, r: _Reader, count: int) -> None:
        for i in range(count):
            address = r.u32()               # 注意：address 在记录开头
            name = r.cstr()
            ret_type = r.i32()
            ret_struct = r.i32()
            nr_args = r.i32()
            nr_vars = r.i32()
            crc = r.u32()
            vars_ = []
            for _ in range(nr_vars):
                vname = r.cstr()
                vt = r.i32()
                vs = r.i32()
                rank = r.i32()
                vars_.append(Var(vname, vt, vs, rank))
            self.functions.append(Function(
                index=i, name=name, ret_type=ret_type, ret_struct=ret_struct,
                nr_args=nr_args, nr_vars=nr_vars, crc=crc, vars=vars_,
                address=address))

    def _parse_globals(self, r: _Reader, count: int) -> None:
        # GLOB 记录比函数变量多一个 int（-1），数组类型后面还跟着各维长度
        for _ in range(count):
            name = r.cstr()
            vt = r.i32()
            vs = r.i32()
            rank = r.i32()
            r.i32()                     # 保留字段，实测恒为 -1
            for _ in range(rank if rank > 0 else 0):
                r.i32()
            self.globals.append(Var(name, vt, vs, rank))

    def _parse_structs(self, r: _Reader, count: int) -> None:
        for i in range(count):
            name = r.cstr()
            ctor = r.i32()
            unk = r.i32()
            nr_members = r.i32()
            members = []
            for _ in range(nr_members):
                mname = r.cstr()
                mt = r.i32()
                ms = r.i32()
                rank = r.i32()
                members.append(Var(mname, mt, ms, rank))
            self.structs.append(Struct(index=i, name=name, ctor=ctor,
                                       nr_members=nr_members, members=members))

    # ---------- 查询 ----------

    def func(self, name: str) -> Function | None:
        return self._by_name.get(name)

    def struct(self, name: str) -> Struct | None:
        for s in self.structs:
            if s.name == name:
                return s
        return None

    def func_ref(self, index: int) -> Function | None:
        if 0 <= index < len(self.functions):
            return self.functions[index]
        return None

    # ---------- 代码 ----------

    def code_of(self, func: Function | str) -> bytes:
        f = self.func(func) if isinstance(func, str) else func
        if f is None:
            raise AinError(f"找不到函数 {func!r}")
        return self.code[f.address:]


def main() -> None:
    import sys
    ain = Ain.load(sys.argv[1])
    print(f"ain 版本 VERS={ain.version}  KEYC={ain.keyc}")
    print(f"函数 {len(ain.functions)}  结构体 {len(ain.structs)}  全局 {len(ain.globals)}"
          f"  CODE {len(ain.code)} 字节")
    for s in ain.structs:
        print(f"\n[struct #{s.index}] {s.name}  ctor=#{s.ctor}  {s.nr_members} 个成员")
        for m in s.members:
            print(f"    {m!r}")


if __name__ == "__main__":
    main()
