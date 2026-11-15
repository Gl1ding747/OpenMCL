import ctypes
import threading
from ctypes import wintypes

NTSTATUS = ctypes.c_long
STATUS_SUCCESS = 0x00000000

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.VirtualAlloc.restype = wintypes.LPVOID
_k32.VirtualFree.restype = wintypes.BOOL
_k32.CloseHandle.restype = wintypes.BOOL

_nt = ctypes.WinDLL("ntdll", use_last_error=True)

_stub_cache = {}
_stub_lock = threading.Lock()

MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_FREE = 0x10000
MEM_IMAGE = 0x1000000
MEM_MAPPED = 0x40000
MEM_PRIVATE = 0x20000

PAGE_NOACCESS = 0x01
PAGE_READONLY = 0x02
PAGE_READWRITE = 0x04
PAGE_EXECUTE = 0x10
PAGE_EXECUTE_READ = 0x20
PAGE_EXECUTE_READWRITE = 0x40

PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400


def _get_syscall_number(func_name):
    addr = ctypes.cast(getattr(_nt, func_name), ctypes.c_void_p).value
    raw = (ctypes.c_ubyte * 64)()
    ctypes.memmove(raw, addr, 64)
    b = bytes(raw)
    # Windows amd64 syscall stubs load the SSN as an imm32 (`B8 <imm32>`),
    # usually right after `mov r10, rcx` (4C 8B D1). Builds differ slightly,
    # so scan the prologue for the pattern instead of pinning a fixed offset.
    for i in range(0, len(b) - 4):
        if b[i] == 0xB8:
            ssn = (b[i + 1]
                   | (b[i + 2] << 8)
                   | (b[i + 3] << 16)
                   | (b[i + 4] << 24))
            # Native ntdll syscall numbers are small; reject garbage matches.
            if 0 < ssn < 0x1000:
                return ssn
    raise RuntimeError(
        "Cannot extract syscall number for {} (unexpected stub)".format(func_name)
    )


def _build_stub_bytes(ssn):
    return bytes([
        0x4C, 0x8B, 0xD1,
        0xB8,
        ssn & 0xFF,
        (ssn >> 8) & 0xFF,
        (ssn >> 16) & 0xFF,
        (ssn >> 24) & 0xFF,
        0x0F, 0x05,
        0xC3,
    ])


def _get_or_make_stub(func_name):
    with _stub_lock:
        cached = _stub_cache.get(func_name)
        if cached is not None:
            return cached
        ssn = _get_syscall_number(func_name)
        code = _build_stub_bytes(ssn)
        addr = _k32.VirtualAlloc(
            0, len(code), MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE
        )
        if not addr:
            raise ctypes.WinError()
        ctypes.memmove(addr, code, len(code))
        _stub_cache[func_name] = (addr, ssn)
        return addr, ssn


NtRVM_FUNC = ctypes.CFUNCTYPE(
    NTSTATUS,
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.LPVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
)

NtWVM_FUNC = ctypes.CFUNCTYPE(
    NTSTATUS,
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.LPVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
)

_k32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.LPVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
_k32.ReadProcessMemory.restype = wintypes.BOOL

# 当直接 syscall 读取在当前系统行不通（stub 形态不符等）时，自动降级为标准
# kernel32.ReadProcessMemory，保证功能可用（只是不再“无痕”）。
_read_stealth = True
_read_std_failures = 0
_READ_STD_FAIL_LIMIT = 8


def _read_std(handle, address, size):
    buf = (ctypes.c_ubyte * size)()
    bytes_read = ctypes.c_size_t(0)
    ok = _k32.ReadProcessMemory(
        wintypes.HANDLE(handle),
        wintypes.LPVOID(address),
        buf,
        size,
        ctypes.byref(bytes_read),
    )
    if not ok:
        raise ctypes.WinError()
    return bytes(buf[: bytes_read.value])


def stealth_read_process_memory(pid, address, size):
    if size == 0:
        return b""

    h_proc = _k32.OpenProcess(PROCESS_VM_READ, False, pid)
    if not h_proc:
        raise ctypes.WinError()
    try:
        return _syscall_read_vm(h_proc, address, size)
    finally:
        _k32.CloseHandle(h_proc)


def _syscall_read_vm(handle, address, size):
    global _read_stealth, _read_std_failures
    if _read_stealth:
        try:
            stub_addr, _ssn = _get_or_make_stub("NtReadVirtualMemory")
        except Exception:
            _read_stealth = False
        else:
            try:
                fn = NtRVM_FUNC(stub_addr)
                buf = (ctypes.c_ubyte * size)()
                bytes_read = ctypes.c_size_t(0)
                status = fn(
                    wintypes.HANDLE(handle),
                    wintypes.LPVOID(address),
                    buf,
                    size,
                    ctypes.byref(bytes_read),
                )
                if status == STATUS_SUCCESS:
                    return bytes(buf[: bytes_read.value])
            except Exception:
                pass
            _read_std_failures += 1
            if _read_std_failures >= _READ_STD_FAIL_LIMIT:
                _read_stealth = False
    return _read_std(handle, address, size)


def stealth_write_process_memory(pid, address, data):
    if not data:
        return 0

    h_proc = _k32.OpenProcess(
        PROCESS_VM_WRITE | PROCESS_VM_OPERATION, False, pid
    )
    if not h_proc:
        raise ctypes.WinError()
    try:
        stub_addr, _ssn = _get_or_make_stub("NtWriteVirtualMemory")
        fn = NtWVM_FUNC(stub_addr)

        buf = (ctypes.c_ubyte * len(data))(*data)
        bytes_written = ctypes.c_size_t(0)

        status = fn(
            wintypes.HANDLE(h_proc),
            wintypes.LPVOID(address),
            buf,
            len(data),
            ctypes.byref(bytes_written),
        )
        if status != STATUS_SUCCESS:
            raise OSError("NtWriteVirtualMemory 0x{:08X}".format(status))
        return bytes_written.value
    finally:
        _k32.CloseHandle(h_proc)


def cleanup_stubs():
    with _stub_lock:
        for stub_addr, _ssn in _stub_cache.values():
            _k32.VirtualFree(stub_addr, 0, 0x8000)
        _stub_cache.clear()


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("_pad", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


_k32.VirtualQueryEx.restype = ctypes.c_size_t
_k32.VirtualQueryEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(MEMORY_BASIC_INFORMATION),
    ctypes.c_size_t,
]


def _classify_region(state, mbi_type, protect, region_size):
    if state != MEM_COMMIT:
        return None
    if protect in (PAGE_NOACCESS, 0):
        return None
    if protect & (PAGE_EXECUTE | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE):
        return "CODE"
    if mbi_type == MEM_IMAGE:
        return "IMAGE"
    if mbi_type == MEM_MAPPED:
        return "MAPPED"
    # JVM 堆按块提交，可能切成很多中小块；128MB 门槛会漏掉碎片化堆。
    if region_size >= 16 * 1024 * 1024:
        return "HEAP"
    if 512 * 1024 <= region_size <= 8 * 1024 * 1024:
        return "STACK"
    return "PRIVATE"


def _size_str(size):
    if size >= 1024 * 1024 * 1024:
        return "{:.2f} GB".format(size / (1024 * 1024 * 1024))
    if size >= 1024 * 1024:
        return "{:.1f} MB".format(size / (1024 * 1024))
    if size >= 1024:
        return "{:.0f} KB".format(size / 1024)
    return "{} B".format(size)


def _prot_str(protect):
    m = {
        PAGE_READONLY: "R", PAGE_READWRITE: "RW",
        PAGE_EXECUTE: "X", PAGE_EXECUTE_READ: "RX",
        PAGE_EXECUTE_READWRITE: "RWX",
    }
    return m.get(protect, "{:02X}".format(protect))


def query_memory_regions(pid):
    h_proc = _k32.OpenProcess(
        PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid
    )
    if not h_proc:
        raise ctypes.WinError()
    try:
        regions = []
        addr = 0
        max_addr = 0x7FFFFFFFFFFF if ctypes.sizeof(ctypes.c_void_p) == 8 else 0x7FFFFFFF
        mbi = MEMORY_BASIC_INFORMATION()
        while addr < max_addr:
            ret = _k32.VirtualQueryEx(
                h_proc, ctypes.c_void_p(addr),
                ctypes.byref(mbi), ctypes.sizeof(mbi)
            )
            if ret == 0:
                break
            # c_void_p 对值为 0(NULL 页) 的指针会解析为 None，必须先归零成 int。
            base = mbi.BaseAddress or 0
            size = mbi.RegionSize or 0
            region_type = _classify_region(mbi.State, mbi.Type, mbi.Protect, size)
            if region_type is not None:
                regions.append({
                    "base": base,
                    "size": size,
                    "type": region_type,
                    "protect": _prot_str(mbi.Protect),
                    "size_str": _size_str(size),
                })
            if size <= 0:
                break  # 0 尺寸区域视为枚举结束，避免死循环。
            addr = base + size
        return regions
    finally:
        _k32.CloseHandle(h_proc)