"""System4 字节码反汇编（线性扫描，够用即可）。

指令编码：u16 opcode + n × i32 参数，宽度 = 2 + 4n。
本作有个怪癖：每个函数体最后一条指令是 `FUNC <下一个函数的索引>`，
它属于"分隔符"而不是逻辑代码，反汇编时会标成 <epilogue>。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from opcodes import OPCODES, name as op_name, width as op_width


@dataclass
class Insn:
    offset: int
    op: int
    name: str
    args: list[int] = field(default_factory=list)
    note: str = ""

    def __str__(self) -> str:
        a = ", ".join(f"0x{v:X}" if abs(v) > 9 else str(v) for v in self.args)
        base = f"+{self.offset:4d}  {self.name:<22s} {a}"
        return f"{base:<46s} ; {self.note}" if self.note else base


def disassemble(ain, func, annotate: bool = True) -> list[Insn]:
    """反汇编单个函数；func 可以是 Function 或函数名。"""
    if isinstance(func, str):
        func = ain.func(func)
        if func is None:
            raise KeyError(func)

    code = ain.code
    start = func.address
    # 函数结束位置 = 下一个函数的起始地址（函数表按地址递增排列）
    end = len(code)
    for f in ain.functions:
        if f.address is not None and f.address > start:
            end = min(end, f.address)

    out: list[Insn] = []
    o = start
    while o < end:
        op = struct.unpack_from("<H", code, o)[0]
        n = OPCODES.get(op, (None, 0))[1]
        ins = Insn(offset=o - start, op=op, name=op_name(op),
                   args=[struct.unpack_from("<i", code, o + 2 + 4 * i)[0] for i in range(n)])
        if annotate:
            ins.note = _annotate(ain, func, out, ins)
        out.append(ins)
        o += 2 + 4 * n
        if ins.name == "RETURN" and o + 6 == end:
            # 结尾的 FUNC <next> 分隔符
            op2 = struct.unpack_from("<H", code, o)[0]
            if op2 == 0x61:
                nxt = struct.unpack_from("<i", code, o + 2)[0]
                out.append(Insn(offset=o - start, op=op2, name=op_name(op2),
                                args=[nxt], note=f"<epilogue> 分隔符，指向 #{nxt} "
                                                 f"{_fname(ain, nxt)}"))
                o += 6
                break
    return out


def _fname(ain, idx: int) -> str:
    f = ain.func_ref(idx)
    return f.name if f else "?"


def _stack_has(prev: list[Insn], n: int) -> list[Insn]:
    return prev[-n:] if len(prev) >= n else prev


def _annotate(ain, func, prev: list[Insn], ins: Insn) -> str:
    if ins.name in ("CALLMETHOD", "CALLFUNC", "CALLHLL", "FUNC") and ins.args:
        return f"-> #{ins.args[0]} {_fname(ain, ins.args[0])}"

    if ins.name in ("PUSH", "PUSHLOCALPAGE", "PUSHSTRUCTPAGE") and ins.args:
        # 形如 PUSHSTRUCTPAGE / PUSH n 的成员访问
        if ins.name == "PUSH" and prev and prev[-1].name == "PUSHSTRUCTPAGE":
            return f"成员 #{ins.args[0]}"

    if ins.name == "PUSH" and prev and prev[-1].name == "PUSHLOCALPAGE":
        idx = ins.args[0]
        if 0 <= idx < len(func.vars):
            v = func.vars[idx]
            k = "参数" if idx < func.nr_args else "局部"
            return f"{k} {idx}: {v.name} ({v.type})"

    if ins.name in ("ASSIGN", "PLUSA", "MINUSA", "XORA") and prev:
        # 尽量标出赋值目标
        target = _guess_assign_target(func, prev)
        if target:
            return f"-> {target}"

    return ""


def _guess_assign_target(func, prev: list[Insn]) -> str:
    """从紧邻的指令猜赋值目标（page/index 模式）。"""
    if len(prev) >= 2 and prev[-1].name == "PUSH" and prev[-2].name == "PUSHLOCALPAGE":
        i = prev[-1].args[0]
        if 0 <= i < len(func.vars):
            return func.vars[i].name
    if len(prev) >= 2 and prev[-1].name == "PUSH" and prev[-2].name == "PUSHSTRUCTPAGE":
        return f"this[{prev[-1].args[0]}]"
    return ""


def dump(ain, func_name: str) -> str:
    fn = ain.func(func_name)
    if fn is None:
        return f"// 找不到函数 {func_name}"
    lines = [f"// {fn.name}  #{fn.index}  @0x{fn.address:06X}  "
             f"args={[v.name for v in fn.vars[:fn.nr_args]]}  "
             f"locals={[v.name for v in fn.vars[fn.nr_args:]]}"]
    lines += [str(i) for i in disassemble(ain, fn)]
    return "\n".join(lines)
