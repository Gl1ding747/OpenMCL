import ctypes
from ctypes import wintypes

kernel32 = ctypes.windll.kernel32

# ------------------------------------------------------------------
#  Job Object
# ------------------------------------------------------------------
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x00001000


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


JobObjectExtendedLimitInformation = 9


def create_job_object():
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def set_job_limits(job_handle, breakaway_ok=True):
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = 0x2000
    if breakaway_ok:
        info.BasicLimitInformation.LimitFlags |= (
            JOB_OBJECT_LIMIT_BREAKAWAY_OK
            | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
        )
    size = ctypes.sizeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION)
    ok = kernel32.SetInformationJobObject(
        job_handle,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        size,
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


def assign_process_to_job(job_handle, process_handle):
    ok = kernel32.AssignProcessToJobObject(job_handle, process_handle)
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


# ------------------------------------------------------------------
#  Memory read / write
# ------------------------------------------------------------------
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400

SIZE_T = ctypes.c_size_t
LPCVOID = ctypes.c_void_p
LPVOID = ctypes.c_void_p


def read_process_memory(pid, address, size):
    h_proc = kernel32.OpenProcess(
        PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h_proc:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        buf = ctypes.create_string_buffer(size)
        bytes_read = SIZE_T()
        ok = kernel32.ReadProcessMemory(
            h_proc,
            LPCVOID(address),
            LPVOID(buf),
            size,
            ctypes.byref(bytes_read),
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        return buf.raw[:bytes_read.value]
    finally:
        kernel32.CloseHandle(h_proc)


def write_process_memory(pid, address, data):
    h_proc = kernel32.OpenProcess(
        PROCESS_VM_WRITE | PROCESS_VM_OPERATION, False, pid)
    if not h_proc:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        buf = ctypes.create_string_buffer(data)
        bytes_written = SIZE_T()
        ok = kernel32.WriteProcessMemory(
            h_proc,
            LPVOID(address),
            LPVOID(buf),
            len(data),
            ctypes.byref(bytes_written),
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        return bytes_written.value
    finally:
        kernel32.CloseHandle(h_proc)


# ------------------------------------------------------------------
#  Public API
# ------------------------------------------------------------------
class ProcessIsolation:
    def __init__(self, breakaway_ok=True):
        self._job = create_job_object()
        set_job_limits(self._job, breakaway_ok=breakaway_ok)
        self._active = True

    def isolate_process(self, pid):
        if not self._active:
            return
        h_proc = kernel32.OpenProcess(0x01F0FFF, False, pid)
        if not h_proc:
            return
        try:
            assign_process_to_job(self._job, h_proc)
        finally:
            kernel32.CloseHandle(h_proc)

    def close(self):
        if self._job:
            kernel32.CloseHandle(self._job)
            self._job = None
        self._active = False