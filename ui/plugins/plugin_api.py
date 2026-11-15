"""OpenMCL 插件 API。

插件以独立进程运行，Python 会把本文件所在目录加入 sys.path，
因此插件里直接 ``import plugin_api`` 即可使用。

本模块都是纯函数，基于磁盘路径 / psutil 读取，不依赖 OpenMCL 宿主，
插件进程可安全调用。
"""

import os
import json
import sys


# ---------------------------------------------------------------- 路径
def base_dir():
    """OpenMCL 数据根目录（~/.openmcl）。"""
    return os.path.join(os.path.expanduser("~"), ".openmcl")


def config_file():
    """全局配置文件路径。"""
    return os.path.join(base_dir(), "config.json")


def versions_dir():
    """所有已安装版本的目录。"""
    return os.path.join(base_dir(), "versions")


def assets_dir():
    """全局资源(assets)目录。"""
    return os.path.join(base_dir(), "assets")


def libraries_dir():
    """依赖库(library)目录。"""
    return os.path.join(base_dir(), "libraries")


def servers_dir():
    """MC 服务端保存目录。"""
    return os.path.join(base_dir(), "servers")


def plugins_dir():
    """插件目录。"""
    return os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- 注入
def _ipc_dir():
    """插件与宿主的 IPC 目录。"""
    d = os.path.join(base_dir(), "plugins_ipc")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _self_entry():
    """推断当前插件相对插件根目录的入口路径。"""
    cwd = os.getcwd()
    plugins = plugins_dir()
    for name in ("plugin.py", "main.py", "__main__.py"):
        p = os.path.join(cwd, name)
        if os.path.isfile(p):
            return os.path.relpath(p, plugins)
    return os.path.relpath(__file__, plugins)


def inject_home_button(title, entry=None, message=""):
    """运行时向宿主请求：把按钮注入到主页面（在运行后才真正注入）。

    - title   : 按钮显示的文字
    - entry   : 点击按钮要启动的插件入口（相对插件根目录）。
                默认自动推断当前插件入口，即点击后重新运行本插件。
    - message : 可选提示文字（暂用于 tooltip）。

    宿主会轮询 IPC 目录并动态把按钮追加到主页面的注入区，
    点击按钮即以独立进程启动对应入口。
    """
    try:
        import uuid
        import json as _json
        if entry is None:
            entry = _self_entry()
        payload = {
            "op": "add_home_button",
            "title": str(title),
            "entry": entry,
            "message": str(message),
        }
        path = os.path.join(_ipc_dir(), "inject_" + uuid.uuid4().hex + ".json")
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(payload, f, ensure_ascii=False)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 版本
def get_versions():
    """返回已安装的版本 id 列表。"""
    d = versions_dir()
    if not os.path.isdir(d):
        return []
    result = []
    for name in os.listdir(d):
        if os.path.isfile(os.path.join(d, name, name + ".json")):
            result.append(name)
    return sorted(result)


def version_dir(version_id):
    """某版本所在目录。"""
    return os.path.join(versions_dir(), str(version_id))


def version_jar(version_id):
    """某版本的服务端/客户端 jar 路径。"""
    return os.path.join(version_dir(version_id), str(version_id) + ".jar")


def version_json(version_id):
    """某版本的版本定义 json 路径。"""
    return os.path.join(version_dir(version_id), str(version_id) + ".json")


# ---------------------------------------------------------------- 配置
def config():
    """读取全局配置（dict），失败返回空 dict。"""
    try:
        with open(config_file(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_config(key, default=None):
    """读取全局配置中的单个键。"""
    return config().get(key, default)


# ---------------------------------------------------------------- 内存
def total_memory():
    """系统总内存(字节)。"""
    try:
        import psutil
        return psutil.virtual_memory().total
    except Exception:
        return 0


def available_memory():
    """系统可用内存(字节)。"""
    try:
        import psutil
        return psutil.virtual_memory().available
    except Exception:
        return 0


def used_memory():
    """系统已用内存(字节)。"""
    try:
        import psutil
        return psutil.virtual_memory().used
    except Exception:
        return 0


def process_memory(pid=None):
    """指定进程(默认当前进程)占用内存(字节)。"""
    try:
        import psutil
        p = psutil.Process(pid if pid is not None else os.getpid())
        return p.memory_info().rss
    except Exception:
        return 0


# ---------------------------------------------------------------- Java
def find_java():
    """在常见目录中扫描可用的 Java 路径列表。"""
    candidates = []
    if os.name == "nt":
        exe = "java.exe"
        roots = []
        for key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
            v = os.environ.get(key)
            if v:
                roots.append(v)
        roots += ["C:\\Program Files\\Eclipse Adoptium",
                  "C:\\Program Files\\Eclipse Foundation",
                  "C:\\Program Files\\Microsoft",
                  "C:\\Program Files\\Java"]
        for root in roots:
            if not os.path.isdir(root):
                continue
            try:
                entries = os.listdir(root)
            except Exception:
                continue
            for item in entries:
                for sub in ("bin", "jre\\bin"):
                    p = os.path.join(root, item, sub, exe)
                    if os.path.isfile(p):
                        candidates.append(p)
    else:
        exe = "java"
        for root in ["/usr/lib/jvm", "/usr/local/lib/jvm", "/Library/Java/JavaVirtualMachines"]:
            if not os.path.isdir(root):
                continue
            try:
                entries = os.listdir(root)
            except Exception:
                continue
            for item in entries:
                for sub in ("bin", "jre/bin"):
                    p = os.path.join(root, item, sub, exe)
                    if os.path.isfile(p):
                        candidates.append(p)
    jh = os.environ.get("JAVA_HOME")
    if jh:
        p = os.path.join(jh, "bin", exe)
        if os.path.isfile(p):
            candidates.insert(0, p)
    if not candidates and sys.platform.startswith("win"):
        from shutil import which
        w = which("java")
        if w:
            candidates.append(w)
    return candidates


def java_path():
    """可用的 Java 路径，找不到返回 "java"。"""
    lst = find_java()
    return lst[0] if lst else "java"


# ---------------------------------------------------------------- 运行实例
def _find_mc_windows():
    """(Windows) 枚举当前可见的 Minecraft 窗口，返回 [(pid, title, hwnd)]。"""
    out = []
    if not sys.platform.startswith("win"):
        return out
    try:
        import ctypes
        import ctypes.wintypes
    except Exception:
        return out
    try:
        user32 = ctypes.windll.user32
        EnumWindows = user32.EnumWindows
        GetWindowThreadProcessId = user32.GetWindowThreadProcessId
        GetWindowTextLengthW = user32.GetWindowTextLengthW
        GetWindowTextW = user32.GetWindowTextW
        IsWindowVisible = user32.IsWindowVisible
        enumproc = ctypes.WINFUNCTYPE(ctypes.c_bool,
                                      ctypes.c_void_p, ctypes.c_void_p)

        def match(hwnd, lparam):
            if not IsWindowVisible(hwnd):
                return True
            try:
                ln = GetWindowTextLengthW(hwnd)
                if ln <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(ln + 1)
                GetWindowTextW(hwnd, buf, ln + 1)
                title = buf.value
                low = title.lower()
                if "minecraft" not in low:
                    return True
                wpid = ctypes.c_ulong()
                GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
                out.append((wpid.value, title, int(hwnd)))
            except Exception:
                pass
            return True

        EnumWindows(enumproc(match), 0)
    except Exception:
        pass
    return out


def running_instances():
    """检测当前正在运行的 Minecraft 实例。

    通过 Java 进程 + 窗口标题识别。

    返回一个列表，每项为 dict：
      - pid   : 进程 id
      - name  : 进程名（如 javaw.exe），未知时为空字符串
      - exe   : 进程可执行文件路径，未知时为空字符串
      - title : 关联的窗口标题，无窗口时为空字符串
      - hwnd  : 窗口句柄（仅 Windows 找到窗口时存在），否则为 None

    未检测到任何实例时返回空列表。
    """
    result = []
    seen = set()
    try:
        import psutil
    except Exception:
        psutil = None

    # 1) 按 Java 进程（javaw/java）识别
    if psutil is not None:
        targets = ("javaw", "java")
        for proc in psutil.process_iter(["pid", "name", "exe"]):
            try:
                pname = (proc.info.get("name") or "")
                stem = pname.rsplit(".", 1)[0].lower() if pname else ""
                if stem not in targets:
                    continue
                pid = proc.info["pid"]
                if pid == os.getpid():  # 排除 plugin_api 自身所在进程
                    continue
                if pid in seen:
                    continue
                seen.add(pid)
                result.append({
                    "pid": pid,
                    "name": pname,
                    "exe": proc.info.get("exe") or "",
                    "title": "",
                    "hwnd": None,
                })
            except Exception:
                continue

    # 2) 按窗口标题补全实例信息（Windows）
    if sys.platform.startswith("win"):
        for pid, title, hwnd in _find_mc_windows():
            if pid in seen:
                for it in result:
                    if it["pid"] == pid:
                        it["title"] = title
                        it["hwnd"] = hwnd
                        break
            else:
                seen.add(pid)
                result.append({
                    "pid": pid,
                    "name": "",
                    "exe": "",
                    "title": title,
                    "hwnd": hwnd,
                })
    return result


# ================================================================
#  简易 GUI 支持（基于 PyQt5）
#  插件运行在独立进程，可安全创建自己的窗口。
#  用法：
#      import plugin_api as api
#      w = api.make_window("My Plugin", 400, 300)
#      w.label("Hello")
#      w.button("Click", lambda: print("clicked"))
#      w.spacing()
#      w.run()
# ================================================================

def _pyqt():
    """惰性导入并返回 PyQt5 模块。"""
    from PyQt5 import QtWidgets, QtCore, QtGui
    return QtWidgets, QtCore, QtGui


class Window:
    """便捷窗口：把常用控件变成简单的方法调用。

    布局按顺序纵向排列（VBox），每个方法创建控件并追加。
    """

    def __init__(self, title="OpenMCL Plugin", width=420, height=320):
        QtWidgets, QtCore, QtGui = _pyqt()
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        self.qt = QtWidgets
        self.qtc = QtCore
        self.qtg = QtGui
        self._window = QtWidgets.QWidget()
        self._window.setWindowTitle(title)
        self._window.resize(width, height)
        self._layout = QtWidgets.QVBoxLayout(self._window)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._layout.setSpacing(8)
        self._running = False

    # ---- 容器 ----
    def _add(self, widget, **kw):
        self._layout.addWidget(widget, **kw)
        return widget

    def horizontal(self, widgets=None):
        """创建一行横向布局，返回该容器，可继续往里面 add。"""
        row = self.qt.QWidget()
        lay = self.qt.QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        if widgets:
            for witem in widgets:
                lay.addWidget(witem)
        # 挂在 window 上，返回能 add 的对象
        class _Row:
            def add(self, widget):
                lay.addWidget(widget)
                return widget
        self._layout.addWidget(row)
        return _Row()

    # ---- 控件 ----
    def label(self, text):
        return self._add(self.qt.QLabel(str(text)))

    def input(self, text="", placeholder=""):
        ed = self.qt.QLineEdit(str(text))
        if placeholder:
            ed.setPlaceholderText(placeholder)
        return self._add(ed)

    def textarea(self, text="", readonly=False):
        if text is None:
            text = ""
        tx = self.qt.QPlainTextEdit(str(text))
        tx.setReadOnly(readonly)
        return self._add(tx)

    def button(self, text, on_click=None):
        btn = self.qt.QPushButton(str(text))
        if on_click is not None:
            btn.clicked.connect(on_click)
        return self._add(btn)

    def checkbox(self, text, checked=False, on_toggle=None):
        cb = self.qt.QCheckBox(str(text))
        cb.setChecked(checked)
        if on_toggle is not None:
            cb.toggled.connect(on_toggle)
        return self._add(cb)

    def combo(self, items, current=0, on_change=None):
        cm = self.qt.QComboBox()
        cm.addItems([str(x) for x in items])
        if items:
            cm.setCurrentIndex(min(current, len(items) - 1))
        if on_change is not None:
            cm.currentIndexChanged.connect(on_change)
        return self._add(cm)

    def slider(self, minimum=0, maximum=100, value=50, on_change=None):
        sd = self.qt.QSlider(self.qtc.Qt.Horizontal)
        sd.setRange(minimum, maximum)
        sd.setValue(value)
        if on_change is not None:
            sd.valueChanged.connect(on_change)
        return self._add(sd)

    def spacing(self, height=6):
        self._layout.addSpacing(height)

    def stretch(self):
        self._layout.addStretch()

    # ---- 运行 ----
    def show(self):
        if not self._window.isVisible():
            self._window.show()

    def run(self):
        """显示窗口并进入事件循环（阻塞，直到窗口关闭）。"""
        self.show()
        self._running = True
        self._window.closeEvent = self._on_close_event
        return self.app.exec_()

    def _on_close_event(self, event):
        self._running = False
        event.accept()

    def quit(self):
        self.app.quit()


def make_window(title="OpenMCL Plugin", width=420, height=320):
    """创建并返回一个简易窗口。"""
    return Window(title, width, height)


def run_gui(win):
    """运行一个已构建好的窗口（等价于 win.run()）。"""
    return win.run()


# ---------------------------------------------------------------- 叠加窗
# 一个绑定到 MC 实例窗口上的 HUD 风格透明小窗：
#   - 无边框、透明背景
#   - 始终置顶（WindowStaysOnTopHint）
#   - 鼠标点击穿透（不拦截实例的任何点击/输入，不影响操控）
# 支持用 make_overlay() 构建，bind_to_mc() 绑定实例窗口并跟随其位置。

def _win_click_through(hwnd):
    """通过 Win32 扩展样式让窗口点击穿透（WS_EX_TRANSPARENT | WS_EX_LAYERED）。"""
    try:
        import ctypes
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_LAYERED = 0x00080000
        ws = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ws | WS_EX_TRANSPARENT | WS_EX_LAYERED)
        return True
    except Exception:
        return False


class Overlay:
    """与 MC 实例窗口绑定的置顶透明叠加窗。"""

    def __init__(self, title="", width=240, height=140, clickable=False):
        QtWidgets, QtCore, QtGui = _pyqt()
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        self.qt = QtWidgets
        self.qtc = QtCore
        self.qtg = QtGui

        flags = (QtCore.Qt.FramelessWindowHint
                 | QtCore.Qt.WindowStaysOnTopHint
                 | QtCore.Qt.Tool)
        self._clickable = clickable
        if not clickable:
            # 点击穿透模式：不接收鼠标、不抢焦点
            flags |= QtCore.Qt.WindowDoesNotAcceptFocus
            self._win = QtWidgets.QWidget(None, flags)
            self._win.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        else:
            # 可点击模式：接收鼠标事件，可放按钮/输入框
            self._win = QtWidgets.QWidget(None, flags)
        self._win.setWindowTitle(title)
        self._win.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self._win.resize(width, height)
        self._layout = QtWidgets.QVBoxLayout(self._win)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(4)
        self._margin = 12
        self._bound = False
        self._fg_count = 0
        self._fg_state = False

    # ---- 控件（与 Window 类似的追加方式）----
    def _add(self, widget):
        self._layout.addWidget(widget)
        return widget

    def label(self, text, color="#ffffff", size=13):
        lbl = self.qt.QLabel(str(text))
        lbl.setStyleSheet(
            "QLabel { color: %s; font-size: %dpx; font-weight: bold;"
            " background: rgba(0,0,0,0); }" % (color, size))
        return self._add(lbl)

    def textarea(self, text="", color="#e0e0e0", readonly=True):
        tx = self.qt.QPlainTextEdit(str(text))
        tx.setReadOnly(readonly)
        tx.setStyleSheet(
            "QPlainTextEdit { background: rgba(0,0,0,0); color: %s;"
            " border: none; font-size: 11px; }" % color)
        return self._add(tx)

    def spacing(self, h=4):
        self._layout.addSpacing(h)

    def stretch(self):
        self._layout.addStretch()

    def button(self, text, on_click=None):
        """向叠加窗追加一个按钮（需在 clickable=True 模式下才有意义）。"""
        btn = self.qt.QPushButton(str(text))
        btn.setStyleSheet(
            "QPushButton { background: rgba(20,20,32,200); color: #ffffff;"
            " border: 1px solid #2a2a38; padding: 6px 12px; font-size: 12px; }"
            "QPushButton:hover { background: rgba(42,42,64,220); }")
        if on_click is not None:
            btn.clicked.connect(on_click)
        return self._add(btn)

    # ---- 位置/绑定 ----
    def move(self, x, y):
        self._win.move(int(x), int(y))

    def _win_hwnd(self):
        return int(self._win.winId())

    def set_click_through(self, enabled=True):
        """开启后鼠标点击直接穿透到下层 MC 实例，不拦截操控。"""
        if enabled:
            _win_click_through(self._win_hwnd())

    def bind_to_mc(self, pid=None, follow=True):
        """绑定到 MC 实例窗口：
        - 在 Windows 上按标题 / 进程 pid 找到实例窗口
        - 把叠加窗移动到实例窗口内、置顶显示
        - follow=True 时每 300ms 跟随实例窗口位置/大小
        其它平台退化为仅置顶显示。"""
        if sys.platform.startswith("win"):
            self._bind_win32(pid, follow)
        else:
            print("Overlay.bind_to_mc: 非 Windows 平台仅置顶显示")
        self.show()
        return self

    def _bind_win32(self, pid, follow):
        import ctypes
        import ctypes.wintypes
        try:
            FindWindowW = ctypes.windll.user32.FindWindowW
            EnumWindows = ctypes.windll.user32.EnumWindows
            GetWindowTextLengthW = ctypes.windll.user32.GetWindowTextLengthW
            GetWindowTextW = ctypes.windll.user32.GetWindowTextW
            GetWindowThreadProcessId = ctypes.windll.user32.GetWindowThreadProcessId
            GetWindowRect = ctypes.windll.user32.GetWindowRect
            IsWindowVisible = ctypes.windll.user32.IsWindowVisible
        except Exception:
            return

        target = [None]

        EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def match(hwnd, lparam):
            if not IsWindowVisible(hwnd):
                return True
            try:
                # 进程匹配
                if pid is not None:
                    wpid = ctypes.c_ulong()
                    GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
                    if wpid.value != pid:
                        return True
                # 标题匹配
                ln = GetWindowTextLengthW(hwnd)
                if ln <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(ln + 1)
                GetWindowTextW(hwnd, buf, ln + 1)
                title = buf.value
                low = title.lower()
                if any(k in low for k in ("minecraft", "mcl launcher", "openmcl")):
                    target[0] = hwnd
                    return False
                return True
            except Exception:
                return True

        EnumWindows(EnumProc(match), 0)
        hwnd = target[0]
        if hwnd is None:
            print("Overlay.bind_to_mc: 未找到 MC 窗口，仅置顶显示")
            return
        self._bound_hwnd = hwnd

        def place():
            try:
                rect = ctypes.wintypes.RECT()
                GetWindowRect(hwnd, ctypes.byref(rect))
                w = rect.right - rect.left
                h = rect.bottom - rect.top
                self._win.setFixedSize(int(w), int(h))
                self._win.move(int(rect.left), int(rect.top))
                self._win.show()
            except Exception:
                pass

        try:
            import ctypes.wintypes
        except Exception:
            pass
        place()

        # 点击穿透：仅对穿透模式生效，不干扰 MC 操控；可点击模式交给用户点击
        if not self._clickable:
            self.set_click_through(True)

        if follow:
            timer = self.qtc.QTimer(self._win)
            timer.timeout.connect(place)
            timer.start(300)
            self._follow_timer = timer

    def show_only_when_foreground(self, enabled=True, interval=250):
        """实例窗口不在最上方时自动隐藏叠加窗。

        - 判断基于 Windows 前台窗口：仅当绑定到的实例窗口处于前台时才显示；
        - 忽略叠加窗自己：叠加窗置顶，可能短暂成为前台，此时不把它算作
          “实例被切走”，而是保持显示；
        - 可配合 bind_to_mc() 使用（需先绑定到实例窗口拿到 hwnd）。
        """
        if not enabled or not sys.platform.startswith("win"):
            if getattr(self, "_fg_timer", None) is not None:
                try:
                    self._fg_timer.stop()
                except Exception:
                    pass
            return self
        if getattr(self, "_fg_timer", None) is None:
            self._fg_timer = self.qtc.QTimer(self._win)
            self._fg_timer.timeout.connect(self._check_foreground)
        else:
            try:
                self._fg_timer.stop()
            except Exception:
                pass
        self._fg_timer.start(int(interval))
        # 用当前实际可见状态校准，并立即判定一次，避免初始误显
        self._fg_state = bool(self._win.isVisible())
        self._fg_count = 0
        self._check_foreground()
        return self

    def _check_foreground(self):
        """内部：检查实例是否在前台，决定叠加窗显隐。

        带滞回（hysteresis / 防抖）：只有连续多次判定为非前台才真正隐藏，
        偶发的一次失焦不会立刻隐藏，从而避免窗口来回 show/hide 造成的闪烁。
        """
        hwnd = getattr(self, "_bound_hwnd", None)
        if hwnd is None:
            return
        try:
            import ctypes
            fg = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        except Exception:
            return
        if fg == int(self._win.winId()):
            # 忽略叠加窗自己：视为实例仍在下方，重置计数并保持现状
            self._fg_count = 0
            return
        if fg == hwnd:
            # 实例在前台：立即显示并重置防抖计数
            self._fg_count = 0
            if not self._fg_state:
                self._fg_state = True
                self._win.show()
        else:
            # 实例不在前台：累计连续帧数，达到阈值才隐藏
            self._fg_count += 1
            if self._fg_count >= 2 and self._fg_state:
                self._fg_state = False
                self._win.hide()

    def show(self):
        self._win.show()

    def hide(self):
        self._win.hide()

    def run(self):
        self.show()
        self._win.closeEvent = lambda e: e.accept()
        return self.app.exec_()

    def quit(self):
        self.app.quit()


def make_overlay(title="OpenMCL Overlay", width=240, height=140, clickable=False):
    """创建与 MC 实例绑定的置顶透明叠加窗。

    - clickable=False（默认）：点击穿透，不拦截实例操控，仅展示信息；
    - clickable=True       ：可接收鼠标点击，可放按钮/输入框交互控件。
    """
    return Overlay(title, width, height, clickable=clickable)


# ================================================================
#  键盘 / 鼠标控制（仅 MC 窗口内有效，Windows）
#  通过 Win32 消息直接投递给目标 Minecraft 窗口，因此：
#    - 键鼠事件只被该游戏窗口接收，不影响桌面、其它程序
#    - foreground_only=True（默认）时，仅当该窗口处于前台才真正注入，
#      否则忽略并返回 False —— 这就是“仅 MC 内有效”的保证
#  目标窗口句柄可来自 running_instances()[i]["hwnd"]，
#  或在 make_input() 里直接传 pid / hwnd。
# 用法：
#      import plugin_api as api
#      insts = api.running_instances()
#      if insts:
#          inp = api.make_input(pid=insts[0]["pid"], foreground_only=True)
#          inp.tap("w")            # 前进
#          inp.key_down("ctrl"); inp.tap("a"); inp.key_up("ctrl")
#          inp.text("hi")
#          inp.move(320, 240); inp.click()
# 非 Windows / 找不到窗口时，所有操作安全返回 False，不会报错。
# ================================================================

_MOUSE_MSGS = {
    "left":   (0x0201, 0x0202),   # WM_LBUTTONDOWN / UP
    "right":  (0x0204, 0x0205),   # WM_RBUTTONDOWN / UP
    "middle": (0x0207, 0x0208),   # WM_MBUTTONDOWN / UP
}
_WM_MOUSEMOVE = 0x0200
_WM_MOUSEWHEEL = 0x020A
_WM_KEYDOWN = 0x0100
_WM_KEYUP = 0x0101
_WM_CHAR = 0x0102

_NAMED_KEYS = {
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "tab": 0x09, "backspace": 0x08,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "insert": 0x2D, "delete": 0x2E, "del": 0x2E,
    "minus": 0xBD, "equal": 0xBB, "comma": 0xBC, "period": 0xBE,
    "slash": 0xBF, "backslash": 0xDC, "quote": 0xDE, "semicolon": 0xBA,
    "lbracket": 0xDB, "rbracket": 0xDD, "grave": 0xC0, "tilde": 0xC0,
}


def _key_to_vk(key):
    """把按键名(如 'w'、'ctrl'、'1')转成虚拟键码；未知返回 None。"""
    if key is None:
        return None
    s = str(key).strip().lower()
    if not s:
        return None
    if s in _NAMED_KEYS:
        return _NAMED_KEYS[s]
    if len(s) == 1:
        c = s[0]
        if "a" <= c <= "z":
            return ord(c) - ord("a") + 0x41
        if "0" <= c <= "9":
            return ord(c)
        return None
    # f1..f24
    if s.startswith("f") and s[1:].isdigit():
        n = int(s[1:])
        if 1 <= n <= 24:
            return 0x70 + (n - 1)
    return None


def _decode_pt(point):
    """把 (x, y) 打包成 WM 消息用的 LPARAM（客户端坐标）。"""
    x = max(0, int(point[0])) & 0xFFFF
    y = max(0, int(point[1])) & 0xFFFF
    return (y << 16) | x


def _win_hwnd_for_pid(pid):
    """按进程 id 找到匹配的 Minecraft 可见窗口句柄，找不到返回 0。"""
    if not sys.platform.startswith("win"):
        return 0
    found = [0]
    try:
        import ctypes
        user32 = ctypes.windll.user32
        GetWindowThreadProcessId = user32.GetWindowThreadProcessId
        GetWindowTextLengthW = user32.GetWindowTextLengthW
        GetWindowTextW = user32.GetWindowTextW
        IsWindowVisible = user32.IsWindowVisible
        EnumWindows = user32.EnumWindows
        EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def match(hwnd, lparam):
            if not IsWindowVisible(hwnd):
                return True
            try:
                wpid = ctypes.c_ulong()
                GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
                if wpid.value != pid:
                    return True
                low = ""
                try:
                    ln = GetWindowTextLengthW(hwnd)
                    if ln > 0:
                        buf = ctypes.create_unicode_buffer(ln + 1)
                        GetWindowTextW(hwnd, buf, ln + 1)
                        low = buf.value.lower()
                except Exception:
                    pass
                if "minecraft" in low or low == "":
                    found[0] = int(hwnd)
                    return False
                return True
            except Exception:
                return True

        EnumWindows(EnumProc(match), 0)
    except Exception:
        pass
    return found[0]


class Input:
    """向指定 Minecraft 窗口注入键盘 / 鼠标事件（仅该窗口内有效）。

    - hwnd / pid : 二选一，目标 MC 窗口；都为空则无法注入(操作返回 False)
    - foreground_only=True（默认）：仅在目标窗口处于前台时才注入，
      避免切到其它程序时误发输入 —— 即“仅 MC 内有效”的约束。
    所有方法都是安全的：失败返回 False，成功返回 True，不会抛异常。
    """

    def __init__(self, hwnd=None, pid=None, foreground_only=True):
        self._user32 = None
        self.hwnd = int(hwnd or 0)
        self.foreground_only = bool(foreground_only)
        if not self.hwnd and pid:
            self.hwnd = _win_hwnd_for_pid(pid)
        try:
            import ctypes
            self._user32 = ctypes.windll.user32
        except Exception:
            self._user32 = None

    @property
    def bound(self):
        """是否已绑定到有效的目标窗口。"""
        return bool(self.hwnd and self._user32)

    def foreground(self):
        """目标窗口当前是否处于前台。"""
        if not self.bound:
            return False
        try:
            fg = int(self._user32.GetForegroundWindow() or 0)
            return fg == self.hwnd
        except Exception:
            return False

    def _allow(self):
        """窗口可用 + foreground_only 通过才允许注入。"""
        if not self.bound:
            return False
        if self.foreground_only and not self.foreground():
            return False
        return True

    def _post(self, msg, wparam, lparam=0):
        try:
            return bool(self._user32.PostMessageW(self.hwnd, msg, wparam, lparam))
        except Exception:
            return False

    # ---- 键盘 ----
    def key_down(self, key):
        """按下某键（虚拟键），返回是否注入成功。"""
        vk = _key_to_vk(key)
        if vk is None or not self._allow():
            return False
        return self._post(_WM_KEYDOWN, vk, 0)

    def key_up(self, key):
        """抬起某键。"""
        vk = _key_to_vk(key)
        if vk is None or not self._allow():
            return False
        return self._post(_WM_KEYUP, vk, 0)

    def tap(self, key):
        """按下再抬起（一次敲击）。"""
        vk = _key_to_vk(key)
        if vk is None or not self._allow():
            return False
        ok1 = self._post(_WM_KEYDOWN, vk, 0)
        ok2 = self._post(_WM_KEYUP, vk, 0)
        return bool(ok1 and ok2)

    def key(self, key, down=None):
        """通用：down=None 表示敲击；否则按 down 决定按下/抬起。"""
        if down is None:
            return self.tap(key)
        return self.key_down(key) if down else self.key_up(key)

    def text(self, text):
        """一次性发送一串文本（逐字符 WM_CHAR）。"""
        if not text or not self._allow():
            return False
        ok = True
        for ch in str(text):
            if not self._post(_WM_CHAR, ord(ch), 0):
                ok = False
        return ok

    # ---- 鼠标 ----
    def move(self, x, y):
        """把鼠标移到窗口客户端坐标 (x, y)（相对该窗口左上角）。"""
        if not self._allow():
            return False
        return self._post(_WM_MOUSEMOVE, 0, _decode_pt((x, y)))

    def mouse_down(self, button="left"):
        """在某按钮上按下鼠标。"""
        pair = _MOUSE_MSGS.get(str(button).lower())
        if not pair or not self._allow():
            return False
        return self._post(pair[0], 0, 0)

    def mouse_up(self, button="left"):
        """抬起鼠标按钮。"""
        pair = _MOUSE_MSGS.get(str(button).lower())
        if not pair or not self._allow():
            return False
        return self._post(pair[1], 0, 0)

    def click(self, x=None, y=None, button="left", hold_ms=0):
        """在给定坐标点击鼠标（不传坐标则在当前鼠标位置点击）。

        - button : 'left' / 'right' / 'middle'
        - hold_ms: 按住多久再抬起(毫秒)，0 表示立即抬起
        """
        pair = _MOUSE_MSGS.get(str(button).lower())
        if not pair or not self._allow():
            return False
        if x is not None and y is not None:
            self._post(_WM_MOUSEMOVE, 0, _decode_pt((x, y)))
            # 移动后给窗口一点处理时间
            try:
                import time as _time
                _time.sleep(0.01)
            except Exception:
                pass
        ok1 = self._post(pair[0], 0, 0)
        if hold_ms > 0:
            try:
                import time as _time
                _time.sleep(hold_ms / 1000.0)
            except Exception:
                pass
        ok2 = self._post(pair[1], 0, 0)
        return bool(ok1 and ok2)

    def scroll(self, delta):
        """滚轮。正数向上、负数向下，常见一格为 ±120。"""
        if not self._allow():
            return False
        # WM_MOUSEWHEEL: wparam 高 16 位为带符号 delta，lparam 用屏幕坐标
        wparam = (int(delta) & 0xFFFF) << 16
        sx, sy = 0, 0
        try:
            import ctypes as _c
            RECT = _c.wintypes.RECT
            GetClientRect = self._user32.GetClientRect
            GetClientRect.argtypes = [_c.c_void_p, _c.POINTER(RECT)]
            GetClientRect.restype = _c.c_int
            rect = RECT()
            if GetClientRect(self.hwnd, _c.byref(rect)):
                cx, cy = rect.right // 2, rect.bottom // 2
                ClientToScreen = self._user32.ClientToScreen
                ClientToScreen.argtypes = [_c.c_void_p, _c.POINTER(_c.wintypes.POINT)]
                ClientToScreen.restype = _c.c_int
                pt = _c.wintypes.POINT(cx, cy)
                if ClientToScreen(self.hwnd, _c.byref(pt)):
                    sx, sy = int(pt.x), int(pt.y)
        except Exception:
            sx, sy = 0, 0
        lparam = _decode_pt((sx, sy))
        return self._post(_WM_MOUSEWHEEL, wparam, lparam)


def make_input(hwnd=None, pid=None, foreground_only=True):
    """创建键鼠注入控制器（仅目标 MC 窗口内有效）。

    例：inp = api.make_input(pid=inst["pid"], foreground_only=True)
    """
    return Input(hwnd=hwnd, pid=pid, foreground_only=foreground_only)