"""Mouse/Keyboard macro engine using Windows SendInput API."""
import os
import ctypes
from ctypes import wintypes
from PyQt5.QtCore import QThread, pyqtSignal

# --- Win32 constants ---
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010

KEYEVENTF_KEYDOWN = 0x0000
KEYEVENTF_KEYUP = 0x0002

WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
        ]

    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", _U),
    ]


VK_TO_NAME = {
    0x01: "Left Mouse", 0x02: "Right Mouse", 0x04: "Middle Mouse",
    0x05: "X1 Mouse", 0x06: "X2 Mouse",
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter",
    0x10: "Shift", 0x11: "Ctrl", 0x12: "Alt",
    0x13: "Pause", 0x14: "Caps Lock", 0x1B: "Esc",
    0x20: "Space", 0x21: "Page Up", 0x22: "Page Down",
    0x23: "End", 0x24: "Home",
    0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
    0x2C: "Print Screen", 0x2D: "Insert", 0x2E: "Delete",
    **{0x30 + i: str(i) for i in range(10)},
    **{0x41 + i: chr(0x41 + i) for i in range(26)},
    **{0x60 + i: f"Num {i}" for i in range(10)},
    0x6A: "Num *", 0x6B: "Num +", 0x6D: "Num -",
    0x6E: "Num .", 0x6F: "Num /",
    **{0x70 + i: f"F{i+1}" for i in range(12)},
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
    0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
}

IS_MOUSE_BUTTON = {0x01, 0x02, 0x04, 0x05, 0x06}


def vk_to_name(vk):
    return VK_TO_NAME.get(vk, f"VK_{vk:02X}")


def is_minecraft_foreground():
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd:
        return False

    class_buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, class_buf, 256)
    if class_buf.value == "GLFW30":
        return True

    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle:
        return False

    exe_buf = ctypes.create_unicode_buffer(260)
    ok = False
    try:
        ctypes.windll.psapi.GetModuleBaseNameW(handle, None, exe_buf, 260)
    except Exception:
        pass
    else:
        exe = os.path.basename(exe_buf.value).lower()
        ok = exe in ("javaw.exe", "java.exe")
    ctypes.windll.kernel32.CloseHandle(handle)
    return ok


def _send_input(*inputs):
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    return ctypes.windll.user32.SendInput(n, arr, ctypes.sizeof(INPUT))


def click_left():
    down = INPUT()
    down.type = INPUT_MOUSE
    down.u.mi.dwFlags = MOUSEEVENTF_LEFTDOWN
    up = INPUT()
    up.type = INPUT_MOUSE
    up.u.mi.dwFlags = MOUSEEVENTF_LEFTUP
    _send_input(down, up)


def click_right():
    down = INPUT()
    down.type = INPUT_MOUSE
    down.u.mi.dwFlags = MOUSEEVENTF_RIGHTDOWN
    up = INPUT()
    up.type = INPUT_MOUSE
    up.u.mi.dwFlags = MOUSEEVENTF_RIGHTUP
    _send_input(down, up)


def hold_left():
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.u.mi.dwFlags = MOUSEEVENTF_LEFTDOWN
    _send_input(inp)


def release_left():
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.u.mi.dwFlags = MOUSEEVENTF_LEFTUP
    _send_input(inp)


def press_key(vk):
    down = INPUT()
    down.type = INPUT_KEYBOARD
    down.u.ki.wVk = vk
    down.u.ki.dwFlags = KEYEVENTF_KEYDOWN
    up = INPUT()
    up.type = INPUT_KEYBOARD
    up.u.ki.wVk = vk
    up.u.ki.dwFlags = KEYEVENTF_KEYUP
    _send_input(down, up)


WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_LBUTTONDOWN = 0x0201
WM_RBUTTONDOWN = 0x0204
WM_MBUTTONDOWN = 0x0207
WM_XBUTTONDOWN = 0x020B

MOUSE_MSG_TO_VK = {
    WM_LBUTTONDOWN: 0x01,
    WM_RBUTTONDOWN: 0x02,
    WM_MBUTTONDOWN: 0x04,
}

HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class HotkeyListener(QThread):
    hotkey_pressed = pyqtSignal(int)

    def __init__(self, vk=0x75, parent=None):
        super().__init__(parent)
        self._vk = vk
        self._running = False
        self._thread_id = 0
        self._hook = None
        self._hook_proc = None

    def set_hotkey(self, vk):
        self._vk = vk
        if self._running:
            self._restart_hook()

    def _restart_hook(self):
        if self._hook:
            ctypes.windll.user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)

    def run(self):
        self._running = True
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

        if self._vk in IS_MOUSE_BUTTON:
            self._install_mouse_hook()
        else:
            if not ctypes.windll.user32.RegisterHotKey(None, 1, MOD_NOREPEAT, self._vk):
                return

        msg = wintypes.MSG()
        while self._running:
            result = ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result > 0:
                if msg.message == WM_HOTKEY and msg.wParam == 1:
                    self.hotkey_pressed.emit(self._vk)
                elif self._hook_proc and msg.message == self._vk:
                    self.hotkey_pressed.emit(self._vk)
            elif result == 0 or result == -1:
                break

        if self._hook:
            ctypes.windll.user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
        ctypes.windll.user32.UnregisterHotKey(None, 1)

    def _install_mouse_hook(self):
        def mouse_proc(nCode, wParam, lParam):
            if nCode >= 0:
                vk = None
                if wParam in MOUSE_MSG_TO_VK:
                    vk = MOUSE_MSG_TO_VK[wParam]
                elif wParam == WM_XBUTTONDOWN:
                    msll = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    xbtn = (msll.mouseData >> 16) & 0xFFFF
                    vk = 0x05 if xbtn == 1 else 0x06
                if vk == self._vk:
                    ctypes.windll.user32.PostThreadMessageW(self._thread_id, self._vk, 0, 0)
            return ctypes.windll.user32.CallNextHookEx(None, nCode, wParam, lParam)

        self._hook_proc = HOOKPROC(mouse_proc)
        self._hook = ctypes.windll.user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._hook_proc,
            ctypes.windll.kernel32.GetModuleHandleW(None), 0)

    def stop(self):
        self._running = False
        if self._hook:
            ctypes.windll.user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class KeyDetector(QThread):
    key_detected = pyqtSignal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False

    def run(self):
        import time
        prev = {}
        for vk in range(1, 255):
            state = ctypes.windll.user32.GetAsyncKeyState(vk)
            prev[vk] = (state & 0x8000) != 0
        time.sleep(0.05)

        self._running = True
        while self._running:
            for vk in range(1, 255):
                state = ctypes.windll.user32.GetAsyncKeyState(vk)
                pressed = (state & 0x8000) != 0
                if pressed and not prev.get(vk, False):
                    name = vk_to_name(vk)
                    if name:
                        self.key_detected.emit(vk, name)
                        return
                prev[vk] = pressed
            time.sleep(0.005)

    def stop(self):
        self._running = False