"""Windows 进程内存访问（纯 ctypes）。

宿主是 64 位 Python，目标游戏是 32 位进程（WOW64），所以：
  * MEMORY_BASIC_INFORMATION 必须按 **64 位** 布局解析（VirtualQueryEx 的
    结构体大小由调用方决定，不是由目标进程决定）；
  * 枚举模块必须带 TH32CS_SNAPMODULE32，否则 32 位模块列表会是空的。

只做只读扫描 + 精确写入，不注入、不挂起线程。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import struct
from dataclasses import dataclass

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---- 常量 ----
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
SYNCHRONIZE = 0x00100000
STILL_ACTIVE = 259

MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_READWRITE = 0x04
PAGE_WRITECOPY = 0x08
PAGE_EXECUTE_READWRITE = 0x40
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01

TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
MAX_PATH = 260
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class MEMORY_BASIC_INFORMATION64(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wt.DWORD),
        ("PartitionId", wt.WORD),
        ("_pad0", wt.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
        ("_pad1", wt.DWORD),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("th32ModuleID", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("GlblcntUsage", wt.DWORD),
        ("ProccntUsage", wt.DWORD),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)),
        ("modBaseSize", wt.DWORD),
        ("hModule", wt.HMODULE),
        ("szModule", wt.WCHAR * 256),
        ("szExePath", wt.WCHAR * MAX_PATH),
    ]


kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.ReadProcessMemory.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t)]
kernel32.WriteProcessMemory.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t)]
kernel32.VirtualQueryEx.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MEMORY_BASIC_INFORMATION64),
    ctypes.c_size_t]
kernel32.VirtualQueryEx.restype = ctypes.c_size_t
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
kernel32.GetExitCodeProcess.restype = wt.BOOL
kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
kernel32.Module32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.Module32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(MODULEENTRY32W)]


class WinMemError(Exception):
    pass


@dataclass(frozen=True)
class Region:
    base: int
    size: int
    protect: int

    @property
    def end(self) -> int:
        return self.base + self.size


def _check(ok, what: str) -> None:
    if not ok:
        err = ctypes.get_last_error()
        raise WinMemError(f"{what} 失败，GetLastError={err} (0x{err:X})")


class Process:
    """一个已附加的进程。支持 with 语句。"""

    def __init__(self, pid: int):
        self.pid = pid
        access = (PROCESS_QUERY_INFORMATION | PROCESS_VM_OPERATION
                  | PROCESS_VM_READ | PROCESS_VM_WRITE | SYNCHRONIZE)
        self.handle = kernel32.OpenProcess(access, False, pid)
        if not self.handle:
            raise WinMemError(
                f"OpenProcess(pid={pid}) 失败，错误码 {ctypes.get_last_error()}；"
                "如果游戏以管理员权限运行，本工具也需要管理员权限")

    # ---------- 生命周期 ----------

    def close(self) -> None:
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def alive(self) -> bool:
        """目标进程是否还在跑。用 GetExitCodeProcess 而不是 WaitForSingleObject，
        后者在没拿到 SYNCHRONIZE 权限时会直接返回 WAIT_FAILED(-1)。"""
        if not self.handle:
            return False
        code = wt.DWORD(0)
        if not kernel32.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE

    # ---------- 读写 ----------

    def read(self, address: int, size: int) -> bytes:
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t(0)
        ok = kernel32.ReadProcessMemory(self.handle, ctypes.c_void_p(address),
                                        buf, size, ctypes.byref(got))
        if not ok:
            return b""
        return buf.raw[:got.value]

    def write(self, address: int, data: bytes) -> bool:
        buf = ctypes.create_string_buffer(data, len(data))
        put = ctypes.c_size_t(0)
        ok = kernel32.WriteProcessMemory(self.handle, ctypes.c_void_p(address),
                                         buf, len(data), ctypes.byref(put))
        return bool(ok) and put.value == len(data)

    def read_i32(self, address: int) -> int | None:
        b = self.read(address, 4)
        return struct.unpack("<i", b)[0] if len(b) == 4 else None

    def write_i32(self, address: int, value: int) -> bool:
        return self.write(address, struct.pack("<i", value))

    # ---------- 区域 ----------

    def regions(self, writable_only: bool = True) -> list[Region]:
        out: list[Region] = []
        mbi = MEMORY_BASIC_INFORMATION64()
        addr = 0
        limit = 0x7FFF_FFFF  # 32 位用户态上限
        size = ctypes.sizeof(mbi)
        while addr < limit:
            n = kernel32.VirtualQueryEx(self.handle, ctypes.c_void_p(addr),
                                        ctypes.byref(mbi), size)
            if not n:
                break
            base = mbi.BaseAddress or 0
            rsize = mbi.RegionSize or 0
            if rsize == 0:
                break
            if (mbi.State == MEM_COMMIT and mbi.Type == MEM_PRIVATE
                    and not (mbi.Protect & PAGE_GUARD)
                    and mbi.Protect & (PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READWRITE)):
                out.append(Region(base, rsize, mbi.Protect))
            addr = base + rsize
        return out

    def modules(self) -> dict[str, int]:
        """模块名(小写) -> 基址。32 位目标必须用 SNAPMODULE32。"""
        snap = kernel32.CreateToolhelp32Snapshot(
            TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, self.pid)
        if snap == INVALID_HANDLE_VALUE or not snap:
            raise WinMemError(f"模块快照失败，错误码 {ctypes.get_last_error()}")
        try:
            me = MODULEENTRY32W()
            me.dwSize = ctypes.sizeof(me)
            out: dict[str, int] = {}
            ok = kernel32.Module32FirstW(snap, ctypes.byref(me))
            while ok:
                base = ctypes.cast(me.modBaseAddr, ctypes.c_void_p).value or 0
                out[me.szModule.lower()] = base
                ok = kernel32.Module32NextW(snap, ctypes.byref(me))
            return out
        finally:
            kernel32.CloseHandle(snap)

    def total_writable(self) -> int:
        return sum(r.size for r in self.regions())


def find_process(name: str) -> list[int]:
    """按进程名（不含 .exe 也行）查找 PID。"""
    import ctypes.wintypes as w
    TH32CS_SNAPPROCESS = 0x2

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", w.DWORD), ("cntUsage", w.DWORD),
            ("th32ProcessID", w.DWORD), ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", w.DWORD), ("cntThreads", w.DWORD),
            ("th32ParentProcessID", w.DWORD), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", w.DWORD), ("szExeFile", w.WCHAR * MAX_PATH),
        ]

    kernel32.Process32FirstW.argtypes = [w.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE or not snap:
        raise WinMemError("进程快照失败")
    want = name.lower()
    if not want.endswith(".exe"):
        want += ".exe"
    pids = []
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(pe)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            if pe.szExeFile.lower() == want:
                pids.append(pe.th32ProcessID)
            ok = kernel32.Process32NextW(snap, ctypes.byref(pe))
    finally:
        kernel32.CloseHandle(snap)
    return pids
