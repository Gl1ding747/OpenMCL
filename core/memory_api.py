"""OpenMCL 内存读取统一接入口。

把底层 `stealth_memory`（原生 syscall + ReadProcessMemory 回退）封装成一份
干净、可复用的 API，供启动器本体或外部脚本接入目标 Java 进程读取/写入内存。

设计要点
--------
- 无自定义启发式逻辑：只提供“打开进程 -> 枚举区域 -> 读写内存 -> 关闭”这类
  原子能力，坐标定位等业务逻辑由调用方基于这些原语自行实现。
- 用进程句柄池省去反复 OpenProcess 的开销，attach 一次、多次读写。
- 单例入口 `get_memory_access()` 提供“进程 ID -> 读取句柄”的集中管理，
  与主窗口的生命周期绑定：游戏启动时 attach、退出时 detach。

用法示例
--------
    api = get_memory_access()
    api.attach(pid)
    regions = api.regions()
    data = api.read_bytes(addr, 16)
    vals  = api.read_doubles(addr, 8)
    api.write_bytes(addr, payload)
    api.detach()
"""

import ctypes
import threading
from . import stealth_memory as _sm


class ProcessNotOpenError(RuntimeError):
    """目标进程未通过 attach 打开就尝试读写。"""


class MemoryApi:
    """基于一个目标进程的内存读写句柄，提供类型化读写。"""

    def __init__(self):
        self._pid = None
        self._handle = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    @property
    def pid(self):
        return self._pid

    @property
    def is_open(self):
        return self._handle is not None

    def attach(self, pid, access_flags=None):
        """打开指定 PID 的进程，建立可复用句柄。

        支持静默 syscall 读取；若该 API 关闭了句柄池，也可退化为每次
        OpenProcess（）调用以便兼容只读权限场景。返回 self 便于链式调用。
        """
        self.detach()
        pid = int(pid)
        flags = access_flags or (
            _sm.PROCESS_VM_READ
            | _sm.PROCESS_VM_WRITE
            | _sm.PROCESS_VM_OPERATION
            | _sm.PROCESS_QUERY_INFORMATION
        )
        handle = _sm._k32.OpenProcess(flags, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._pid = pid
        self._handle = handle
        return self

    def detach(self):
        if self._handle:
            _sm._k32.CloseHandle(self._handle)
            self._handle = None
        self._pid = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.detach()
        return False

    # ------------------------------------------------------------------
    # 区域枚举
    # ------------------------------------------------------------------
    def regions(self, alive_only=True):
        """返回该进程已提交、可读的内存区域列表。

        每项含 base, size, protect, mem_type, kind。kind 为
        CODE / IMAGE / MAPPED / HEAP / STACK / PRIVATE。
        """
        from ctypes import wintypes
        if not self._handle:
            raise ProcessNotOpenError("process not attached")
        regions = []
        addr = 0
        mbi = _sm.MEMORY_BASIC_INFORMATION()
        while True:
            ret = _sm._k32.VirtualQueryEx(
                wintypes.HANDLE(self._handle),
                ctypes.c_void_p(addr),
                ctypes.byref(mbi),
                ctypes.sizeof(mbi),
            )
            if not ret:
                break
            base = mbi.BaseAddress or 0
            size = mbi.RegionSize or 0
            if size == 0:
                break
            kind = _sm._classify_region(mbi.State, mbi.Type, mbi.Protect, size)
            if kind and (not alive_only or mbi.State == _sm.MEM_COMMIT):
                regions.append({
                    "base": base,
                    "size": size,
                    "protect": mbi.Protect,
                    "mem_type": mbi.Type,
                    "kind": kind,
                })
            next_addr = base + size
            if next_addr <= addr:
                break
            addr = next_addr
        return regions

    def readable_regions(self):
        """已提交且可读的内存区域（供扫坐标/特征时过滤候选区）。"""
        return [r for r in self.regions() if r["protect"] & 0x40  # RWX
                or r["protect"] & 0x04  # RW
                or r["protect"] & 0x02]  # R

    # ------------------------------------------------------------------
    # 原始读写
    # ------------------------------------------------------------------
    def _require_handle(self):
        if not self._handle:
            raise ProcessNotOpenError("process not attached")

    def read_bytes(self, address, size):
        """读取 size 字节，返回 bytes。权限不足或地址越界抛异常。"""
        self._require_handle()
        if size == 0:
            return b""
        buf = (ctypes.c_ubyte * size)()
        read = ctypes.c_size_t(0)
        ok = _sm._k32.ReadProcessMemory(
            ctypes.wintypes.HANDLE(self._handle),
            ctypes.wintypes.LPVOID(address),
            buf, size, ctypes.byref(read),
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        return bytes(buf[: read.value])

    def write_bytes(self, address, data):
        """写入 data(bytes)，返回成功写出的字节数。"""
        self._require_handle()
        if not data:
            return 0
        buf = (ctypes.c_ubyte * len(data))(*data)
        written = ctypes.c_size_t(0)
        ok = _sm._k32.WriteProcessMemory(
            ctypes.wintypes.HANDLE(self._handle),
            ctypes.wintypes.LPVOID(address),
            buf, len(data), ctypes.byref(written),
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        return written.value

    # ------------------------------------------------------------------
    # 类型化读取
    # ------------------------------------------------------------------
    def read_byte(self, address):
        return self.read_bytes(address, 1)[0]

    def read_int(self, address):
        return ctypes.c_int32.from_buffer_copy(self.read_bytes(address, 4)).value

    def read_uint(self, address):
        return ctypes.c_uint32.from_buffer_copy(self.read_bytes(address, 4)).value

    def read_long(self, address):
        return ctypes.c_int64.from_buffer_copy(self.read_bytes(address, 8)).value

    def read_pointer(self, address):
        return ctypes.c_void_p.from_buffer_copy(self.read_bytes(address, 8)).value

    def read_float(self, address):
        return ctypes.c_float.from_buffer_copy(self.read_bytes(address, 4)).value

    def read_double(self, address):
        return ctypes.c_double.from_buffer_copy(self.read_bytes(address, 8)).value

    def read_doubles(self, address, count):
        msg = "read_doubles expects count bytes "
        if count < 0:
            raise ValueError("negative count")
        raw = self.read_bytes(address, count * 8)
        if len(raw) != count * 8:
            raise IOError(
                "short read: wanted {} bytes, got {}".format(count * 8, len(raw)))
        return list(ctypes.c_double.from_buffer_copy(raw))

    def read_cstring(self, address, max_len=1024):
        """读取以 NUL 结尾的 UTF-8/ANSI 字符串（含与安全上限）。"""
        raw = self.read_bytes(address, max_len)
        if raw is None:
            return ""
        idx = raw.find(b"\x00")
        if idx >= 0:
            raw = raw[:idx]
        try:
            return raw.decode("utf-8", "replace")
        except Exception:
            return raw.decode("latin-1", "replace")


# ----------------------------------------------------------------------
# 全局接入口：集中管理“实例 -> 内存句柄”
# ----------------------------------------------------------------------
class MemoryAccess:
    """按 (pid) 缓存 MemoryApi 的统一入口。

    主窗口在游戏启动/停止时调用 attach / detach；外部脚本可通过
    instance 方法拿到与某实例绑定的 api，不必关心 PID 细节。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._by_pid = {}

    def _api_for_pid(self, pid):
        with self._lock:
            api = self._by_pid.get(pid)
            if api is None:
                api = MemoryApi()
                self._by_pid[pid] = api
            return api

    def attach(self, pid):
        api = self._api_for_pid(pid)
        if not api.is_open:
            api.attach(pid)
        return api

    def get(self, pid):
        """返回已打开的 api，未打开则返回 None（不自动拉起）。"""
        api = self._by_pid.get(pid)
        if api is not None and api.is_open:
            return api
        return None

    def detach(self, pid):
        with self._lock:
            api = self._by_pid.pop(pid, None)
        if api is not None:
            api.detach()

    def detach_all(self):
        with self._lock:
            apis = list(self._by_pid.values())
            self._by_pid.clear()
        for api in apis:
            api.detach()


_default_access = None
_default_lock = threading.Lock()


def get_memory_access():
    """返回进程级单例 MemoryAccess，作为全项目的统一内存接入口。"""
    global _default_access
    if _default_access is None:
        with _default_lock:
            if _default_access is None:
                _default_access = MemoryAccess()
    return _default_access