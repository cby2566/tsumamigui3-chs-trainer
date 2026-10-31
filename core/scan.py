"""内存扫描器（numpy 向量化）。

只扫「已提交 + 私有 + 可写」的区域，能过滤掉 60% 以上的噪声
（镜像区、文件映射区、栈保护页等）。

坐标一律使用目标进程内的绝对虚拟地址。
"""

from __future__ import annotations

import numpy as np

from winmem import Process, Region

CHUNK = 8 * 1024 * 1024          # 每次读 8MB
OVERLAP_MAX = 16                 # 为保证跨块匹配，块之间重叠这么多字节

DTYPES = {
    "i8": np.int8, "u8": np.uint8,
    "i16": np.int16, "u16": np.uint16,
    "i32": np.int32, "u32": np.uint32,
    "i64": np.int64, "f32": np.float32, "f64": np.float64,
}

# 显示用：把 dtype 字符映射成 tkinter 里显示的类型名
KIND_LABEL = {
    "i8": "1 字节整数", "u8": "1 字节无符号", "i16": "2 字节整数",
    "u16": "2 字节无符号", "i32": "4 字节整数", "u32": "4 字节无符号",
    "i64": "8 字节整数", "f32": "单精度浮点", "f64": "双精度浮点",
}


class Result:
    """一次扫描的结果：地址数组 + 对应值数组（平行数组）。"""

    def __init__(self, kind: str, addrs: np.ndarray, vals: np.ndarray):
        self.kind = kind
        self.addrs = addrs
        self.vals = vals

    def __len__(self) -> int:
        return len(self.addrs)

    def __repr__(self) -> str:
        return f"<Result {self.kind} {len(self)} 个候选>"


class Scanner:
    def __init__(self, proc: Process):
        self.proc = proc
        self._regions: list[Region] | None = None

    @property
    def regions(self) -> list[Region]:
        if self._regions is None:
            self._regions = self.proc.regions()
        return self._regions

    def refresh(self) -> None:
        self._regions = None

    # ---------- 底层 ----------

    def _iter_chunks(self, region: Region, item: int):
        """按块读取区域，返回 (块起始地址, 缓冲区)。

        块之间留 OVERLAP_MAX-1 字节重叠，保证跨越块边界的值也能被找到；
        推进量按**实际读到的字节数**算，部分读失败时不会跳过数据。

        整块读不出来（got == 0）就直接放弃这个区域：它多半已经被游戏释放了。
        早先这里按 4 字节继续推进，撞上几十 MB 的失效区域会变成上千万次
        ReadProcessMemory，界面就永远停在「正在扫描…」上。
        """
        off = 0
        while off < region.size:
            n = min(CHUNK, region.size - off)
            buf = self.proc.read(region.base + off, n)
            got = len(buf)
            if got == 0:
                return                      # 区域已不可读，别在原地空转
            if got >= item:
                yield region.base + off, buf
                advance = got - (OVERLAP_MAX - 1)
            else:
                advance = max(got, 4)
            off += max(advance, 4)

    def read_values(self, addrs: np.ndarray, kind: str) -> np.ndarray:
        """按地址批量读值（按区域分组，每区只读一次）。"""
        dt = np.dtype(DTYPES[kind]).newbyteorder("<")
        item = dt.itemsize
        out = np.zeros(len(addrs), dtype=dt)
        if len(addrs) == 0:
            return out
        order = np.argsort(addrs)
        sa = addrs[order]
        res = np.zeros(len(addrs), dtype=dt)
        for r in self.regions:
            lo = np.searchsorted(sa, r.base, "left")
            hi = np.searchsorted(sa, r.end, "left")
            if lo >= hi:
                continue
            buf = self.proc.read(r.base, r.size)
            if len(buf) < item:
                continue
            arr = np.frombuffer(buf, dtype=dt)
            offs = (sa[lo:hi] - r.base) // item
            ok = (offs >= 0) & (offs < len(arr))
            res[lo:hi][ok] = arr[offs[ok]]
        out[order] = res
        return out

    # ---------- 首次扫描 ----------

    def first_scan(self, value, kind: str = "i32") -> Result:
        self.refresh()                 # 游戏跑久了会新开区域，每次首扫重新枚举
        dt = np.dtype(DTYPES[kind]).newbyteorder("<")
        item = dt.itemsize
        target = dt.type(value)
        addrs: list[np.ndarray] = []
        vals: list[np.ndarray] = []
        for r in self.regions:
            for base, buf in self._iter_chunks(r, item):
                usable = (len(buf) // item) * item
                if usable < item:
                    continue
                arr = np.frombuffer(buf[:usable], dtype=dt)
                if dt.kind == "f":
                    mask = np.isclose(arr, target, rtol=0, atol=1e-4)
                else:
                    mask = arr == target
                if not mask.any():
                    continue
                idx = np.flatnonzero(mask)
                addrs.append(base + idx.astype(np.int64) * item)
                vals.append(arr[idx])
        if not addrs:
            return Result(kind, np.empty(0, np.int64), np.empty(0, dt))
        addr = np.concatenate(addrs)
        val = np.concatenate(vals)
        # 块之间有重叠，同地址可能被找到两次，去重并顺便排好序
        _, keep = np.unique(addr, return_index=True)
        return Result(kind, addr[keep], val[keep])

    def first_scan_unknown(self, kind: str = "i32") -> Result:
        """未知初值：把所有可写区的值都收进来当快照（候选量很大，慎用）。"""
        dt = np.dtype(DTYPES[kind]).newbyteorder("<")
        item = dt.itemsize
        addrs: list[np.ndarray] = []
        vals: list[np.ndarray] = []
        for r in self.regions:
            for base, buf in self._iter_chunks(r, item):
                usable = (len(buf) // item) * item
                if usable < item:
                    continue
                arr = np.frombuffer(buf[:usable], dtype=dt)
                addrs.append(base + np.arange(len(arr), dtype=np.int64) * item)
                vals.append(arr.copy())
        if not addrs:
            return Result(kind, np.empty(0, np.int64), np.empty(0, dt))
        return Result(kind, np.concatenate(addrs), np.concatenate(vals))

    # ---------- 再筛 ----------

    def refine(self, prev: Result, mode: str, value=None) -> Result:
        """mode: eq / ne / gt / lt / increased / decreased / unchanged / changed"""
        if len(prev) == 0:
            return prev
        cur = self.read_values(prev.addrs, prev.kind)
        old = prev.vals
        if mode == "eq":
            mask = cur == cur.dtype.type(value)
        elif mode == "ne":
            mask = cur != cur.dtype.type(value)
        elif mode == "gt":
            mask = cur > cur.dtype.type(value)
        elif mode == "lt":
            mask = cur < cur.dtype.type(value)
        elif mode == "increased":
            mask = cur > old
        elif mode == "decreased":
            mask = cur < old
        elif mode == "unchanged":
            mask = cur == old
        elif mode == "changed":
            mask = cur != old
        else:
            raise ValueError(f"未知筛选模式 {mode}")
        return Result(prev.kind, prev.addrs[mask], cur[mask])

    # ---------- 写入 ----------

    def write_value(self, address: int, value, kind: str) -> bool:
        dt = np.dtype(DTYPES[kind]).newbyteorder("<")
        return self.proc.write(address, dt.type(value).tobytes())
