import os
import json
import platform
import webbrowser
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QTabWidget, QPushButton, QLabel, QComboBox,
                             QSpinBox, QGroupBox, QLineEdit, QCheckBox,
                             QMessageBox, QInputDialog, QFileDialog,
                             QSplitter, QFrame, QProgressDialog,
                             QDialog, QDialogButtonBox, QFormLayout,
                             QListWidget, QScrollArea, QStackedWidget,
                             QGraphicsOpacityEffect, QSlider, QTextEdit,
                             QTreeWidget, QTreeWidgetItem, QHeaderView)
from PyQt5.QtCore import Qt, QTimer, QProcess, QThread, pyqtSignal, QMutex, QWaitCondition, QEasingCurve, QPropertyAnimation, QPoint, QParallelAnimationGroup, QRect
from PyQt5.QtGui import QFont, QColor

from core.version_manager import VersionManager, MIRROR_NAMES
from core.downloader import Downloader
from core.aria2_manager import Aria2Manager
from core.auth import AccountManager
from core.launcher import (MinecraftLauncher, find_java, find_java_for_mc,
                           get_mc_required_java, get_java_major_version,
                           get_mc_required_java_for_version)
from core.version_manager import network_available
from ui.new_instance_widget import FetchManifestWorker
from ui.new_instance_widget import NewInstanceWidget
from ui.instances_widget import InstancesWidget
from ui.console_widget import ConsoleWidget
from ui.modpack_download_widget import ModpackDownloadWidget
from ui.side_panel import SidePanel
from ui.function_widget import FunctionWidget
from i18n import tr, current_lang, available_langs, init_lang


CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".openmcl", "config.json")

SIDEBAR_QSS = """
QFrame#sidebar {
    background: #16161a;
    border-right: 1px solid #202028;
}
"""

SIDEBAR_LIST_QSS = """
QListWidget {
    background: transparent;
    border: none;
    outline: none;
}
QListWidget::item {
    background: transparent;
    color: #808088;
    border-left: 2px solid transparent;
    padding: 10px 14px;
    font-size: 12px;
}
QListWidget::item:selected {
    background: #222228;
    border-left: 2px solid #808088;
    color: #e0e0e0;
}
QListWidget::item:hover {
    background: #1c1c22;
    color: #c0c0c0;
}
"""

ACCENT_BTN_QSS = """
QPushButton {
    background: #282830;
    color: #e8e8e8;
    border: 1px solid #383840;
    padding: 12px;
    font-size: 13px;
    font-weight: bold;
    letter-spacing: 3px;
}
QPushButton:hover {
    background: #32323c;
    border-color: #4a4a54;
}
QPushButton:pressed {
    background: #1e1e24;
}
"""

STOP_BTN_QSS = """
QPushButton {
    background: #2a1e1e;
    color: #e06060;
    border: 1px solid #382828;
    padding: 12px;
    font-size: 13px;
    font-weight: bold;
    letter-spacing: 3px;
}
QPushButton:hover {
    background: #342828;
    border-color: #483030;
}
"""

SMALL_SIDEBAR_BTN_QSS = """
QPushButton {
    background: transparent;
    color: #808088;
    border: 1px solid #222228;
    padding: 5px 8px;
    font-size: 10px;
    letter-spacing: 0.5px;
}
QPushButton:hover {
    background: #222228;
    color: #c0c0c0;
    border-color: #303038;
}
"""

SEPARATOR_QSS = "border: none; background: #202028; max-height: 1px;"

RUNNING_INSTANCE_QSS = """
#runningInstance {
    background: #1a1a20;
    border: 1px solid #24242c;
    border-radius: 4px;
    padding: 6px 8px;
}
QLabel#runningDot {
    color: #50c878;
    font-size: 10px;
}
QLabel#runningLabel {
    color: #a0a0b0;
    font-size: 10px;
}
QLabel#runningPid {
    color: #606070;
    font-size: 9px;
}
"""

MEM_BTN_QSS = """
QPushButton {
    background: #1c1c24;
    color: #7080a0;
    border: 1px solid #282834;
    padding: 3px 8px;
    font-size: 9px;
    letter-spacing: 0.5px;
}
QPushButton:hover {
    background: #242430;
    color: #90a8d0;
    border-color: #384050;
}
"""


class AnimTabWidget(QTabWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._prev_idx = 0
        self._tab_group = None
        self._indicator = None
        bar = self.tabBar()
        bar.setDrawBase(False)
        bar.setUsesScrollButtons(True)
        bar.setElideMode(Qt.ElideRight)
        bar.currentChanged.connect(self._on_tab_switch)

    def _ensure_indicator(self):
        if self._indicator is None:
            self._indicator = QWidget(self)
            self._indicator.setFixedHeight(2)
            self._indicator.setStyleSheet("background: #707078;")
            self._indicator.setAttribute(Qt.WA_TransparentForMouseEvents)
            self._indicator.show()
        bar = self.tabBar()
        idx = bar.currentIndex()
        if idx < 0 or bar.count() == 0:
            self._indicator.hide()
            return
        r = bar.tabRect(idx)
        if r.width() > 0:
            self._indicator.setGeometry(r.x(), bar.height() - 2, r.width(), 2)
            self._indicator.show()

    def _move_indicator(self, idx):
        if self._indicator is None:
            return
        bar = self.tabBar()
        if idx < 0 or idx >= bar.count():
            return
        target = bar.tabRect(idx)
        if target.width() <= 0:
            return
        self._indicator.show()
        anim = QPropertyAnimation(self._indicator, b"geometry")
        anim.setDuration(340)
        anim.setStartValue(self._indicator.geometry())
        anim.setEndValue(QRect(target.x(), bar.height() - 2, target.width(), 2))
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        anim.start()

    def addTab(self, widget, label):
        super().addTab(widget, label)
        self._ensure_indicator()

    def _on_tab_switch(self, new_idx):
        prev_w = self.widget(self._prev_idx)
        new_w = self.widget(new_idx)
        self._prev_idx = new_idx

        self._move_indicator(new_idx)

        if not prev_w or not new_w or prev_w is new_w:
            return

        op_out = prev_w.graphicsEffect()
        if not isinstance(op_out, QGraphicsOpacityEffect):
            op_out = QGraphicsOpacityEffect(prev_w)
            prev_w.setGraphicsEffect(op_out)
        op_out.setOpacity(1.0)

        op_in = new_w.graphicsEffect()
        if not isinstance(op_in, QGraphicsOpacityEffect):
            op_in = QGraphicsOpacityEffect(new_w)
            new_w.setGraphicsEffect(op_in)
        op_in.setOpacity(0.0)

        anim_out = QPropertyAnimation(op_out, b"opacity")
        anim_out.setDuration(100)
        anim_out.setStartValue(1.0)
        anim_out.setEndValue(0.0)
        anim_out.setEasingCurve(QEasingCurve.OutQuad)

        anim_in = QPropertyAnimation(op_in, b"opacity")
        anim_in.setDuration(240)
        anim_in.setStartValue(0.0)
        anim_in.setEndValue(1.0)
        anim_in.setEasingCurve(QEasingCurve.OutCubic)

        self._tab_group = QParallelAnimationGroup()
        self._tab_group.addAnimation(anim_out)
        self._tab_group.addAnimation(anim_in)

        def _cleanup():
            prev_w.setGraphicsEffect(None)
            new_w.setGraphicsEffect(None)

        self._tab_group.finished.connect(_cleanup)
        self._tab_group.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._ensure_indicator()


class SkinStationDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Skin Station Login"))
        self.setMinimumWidth(420)
        layout = QFormLayout(self)
        layout.setSpacing(10)
        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("Email or username")
        layout.addRow("Account:", self.email_input)
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setPlaceholderText("Password")
        layout.addRow("Password:", self.password_input)
        self.api_input = QLineEdit()
        self.api_input.setText(tr("https://littleskin.cn/api/yggdrasil"))
        self.api_input.setPlaceholderText("https://your.site/api/yggdrasil")
        layout.addRow("API Root:", self.api_input)
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self._validate_and_accept)
        btn_box.rejected.connect(self.reject)
        layout.addRow(btn_box)

    def _validate_and_accept(self):
        email = self.email_input.text().strip()
        password = self.password_input.text()
        api_root = self.api_input.text().strip()
        if not email:
            QMessageBox.warning(self, tr("Missing"), tr("Please enter your account."))
            return
        if not password:
            QMessageBox.warning(self, tr("Missing"), tr("Please enter your password."))
            return
        if not api_root:
            QMessageBox.warning(self, tr("Missing"), tr("Please enter the API root URL."))
            return
        self.email = email
        self.password = password
        self.api_root = api_root
        self.accept()


class MicrosoftLoginThread(QThread):
    finished = pyqtSignal(object, str)
    failed = pyqtSignal(str)
    login_message = pyqtSignal(str)
    request_code = pyqtSignal()

    def __init__(self, account_manager, parent=None):
        super().__init__(parent)
        self.am = account_manager
        self._code = None
        self._cancelled = False
        self._mutex = QMutex()
        self._cond = QWaitCondition()

    def run(self):
        def callback(type_, msg):
            if type_ == "message":
                self.login_message.emit(msg)
                return None
            elif type_ == "input":
                self._mutex.lock()
                self._code = None
                self._cancelled = False
                self.request_code.emit()
                self._cond.wait(self._mutex)
                cancelled = self._cancelled
                code = self._code
                self._mutex.unlock()
                if cancelled:
                    raise Exception("Login cancelled")
                return code
            return None
        try:
            account = self.am.login_microsoft(callback=callback)
            self.finished.emit(account, "success")
        except Exception as e:
            self.failed.emit(str(e))

    def provide_code(self, code):
        self._mutex.lock()
        self._code = code
        self._cancelled = code is None
        self._cond.wakeAll()
        self._mutex.unlock()


class MemoryViewerDialog(QDialog):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._main = main_window
        self.setWindowTitle(tr("Memory Viewer"))
        self.setMinimumSize(640, 480)
        self.setStyleSheet("""
            QDialog { background: #141418; }
            QLabel { color: #a0a0b0; font-size: 11px; }
            QLineEdit { background: #1c1c20; color: #d0d0d0; border: 1px solid #2a2a30;
                        padding: 5px 8px; font-family: Consolas, monospace; }
            QComboBox { background: #1c1c20; color: #d0d0d0; border: 1px solid #2a2a30;
                        padding: 5px 8px; }
            QTextEdit { background: #0c0c10; color: #b0b0c0; border: 1px solid #2a2a30;
                        font-family: Consolas, monospace; font-size: 12px; }
        """)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.addWidget(QLabel(tr("Instance:")))
        self._instance_combo = QComboBox()
        self._instance_combo.setMinimumWidth(180)
        head.addWidget(self._instance_combo, 1)

        head.addWidget(QLabel(tr("Address:")))
        self._addr_input = QLineEdit("0x")
        self._addr_input.setPlaceholderText("0x140000000")
        self._addr_input.setMinimumWidth(140)
        head.addWidget(self._addr_input, 2)

        head.addWidget(QLabel(tr("Size:")))
        self._size_spin = QSpinBox()
        self._size_spin.setRange(1, 65536)
        self._size_spin.setValue(256)
        self._size_spin.setStyleSheet(
            "QSpinBox { background: #1c1c20; color: #d0d0d0; border: 1px solid #2a2a30; padding: 4px; }")
        head.addWidget(self._size_spin)

        layout.addLayout(head)

        btns = QHBoxLayout()
        self._read_btn = QPushButton(tr("Read"))
        self._read_btn.setStyleSheet(ACCENT_BTN_QSS)
        self._read_btn.clicked.connect(self._do_read)
        btns.addWidget(self._read_btn)

        self._write_btn = QPushButton(tr("Write"))
        self._write_btn.setStyleSheet("""
            QPushButton { background: #2a1e1e; color: #e0b060; border: 1px solid #382828;
                          padding: 8px 16px; font-weight: bold; }
            QPushButton:hover { background: #342828; border-color: #483030; }
        """)
        self._write_btn.clicked.connect(self._do_write)
        btns.addWidget(self._write_btn)

        self._auto_refresh_cb = QCheckBox(tr("Auto 500ms"))
        self._auto_refresh_cb.setStyleSheet("color: #808090; font-size: 10px;")
        btns.addWidget(self._auto_refresh_cb)

        self._scan_btn = QPushButton(tr("Scan Regions"))
        self._scan_btn.setStyleSheet("""
            QPushButton { background: #1a1a24; color: #7080a0; border: 1px solid #282834;
                          padding: 6px 12px; font-size: 10px; }
            QPushButton:hover { background: #242430; color: #90a8d0; border-color: #384050; }
        """)
        self._scan_btn.clicked.connect(self._do_scan_regions)
        btns.addWidget(self._scan_btn)
        btns.addStretch()
        layout.addLayout(btns)

        self._region_tree = QTreeWidget()
        self._region_tree.setHeaderLabels(["Base Address", "Size", "Type", "Prot"])
        self._region_tree.setColumnCount(4)
        self._region_tree.setRootIsDecorated(False)
        self._region_tree.setAlternatingRowColors(True)
        self._region_tree.setMaximumHeight(180)
        self._region_tree.setStyleSheet("""
            QTreeWidget { background: #0c0c10; color: #b0b0c0; border: 1px solid #2a2a30;
                          font-family: Consolas, monospace; font-size: 11px;
                          alternate-background-color: #101016; }
            QTreeWidget::item:selected { background: #203040; color: #e0e0e0; }
            QHeaderView::section { background: #141418; color: #707080; border: 1px solid #202028;
                                   padding: 3px 6px; font-size: 10px; }
        """)
        self._region_tree.header().setStretchLastSection(False)
        self._region_tree.header().resizeSection(0, 150)
        self._region_tree.header().resizeSection(1, 80)
        self._region_tree.header().resizeSection(2, 70)
        self._region_tree.header().resizeSection(3, 50)
        self._region_tree.itemDoubleClicked.connect(self._on_region_selected)
        layout.addWidget(self._region_tree)

        lbl = QLabel(tr("Hex Dump  (address  hex_bytes  ASCII)"))
        lbl.setStyleSheet("color: #505058; font-size: 9px; letter-spacing: 1px;")
        layout.addWidget(lbl)

        self._hex_view = QTextEdit()
        self._hex_view.setReadOnly(True)
        layout.addWidget(self._hex_view, 1)

        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self._do_read)
        self._auto_refresh_cb.stateChanged.connect(
            lambda s: self._auto_timer.start(500) if s else self._auto_timer.stop())

    def _get_selected_instance_id(self):
        return self._instance_combo.currentData()

    def _do_read(self):
        iid = self._get_selected_instance_id()
        if not iid:
            self._hex_view.setPlainText(tr("No instance selected."))
            return
        try:
            addr_text = self._addr_input.text().strip()
            if addr_text.lower().startswith("0x"):
                address = int(addr_text, 16)
            else:
                address = int(addr_text, 16) if all(c in "0123456789abcdefABCDEF" for c in addr_text) else int(addr_text)
        except ValueError:
            self._hex_view.setPlainText(tr("Invalid address: {}").format(addr_text))
            return
        size = self._size_spin.value()
        try:
            data = self._main.read_instance_memory(iid, address, size)
            self._hex_view.setPlainText(self._format_hex(address, data))
        except Exception as e:
            self._hex_view.setPlainText(tr("Read error: {}").format(str(e)))

    def _format_hex(self, base_addr, data):
        lines = []
        for offset in range(0, len(data), 16):
            chunk = data[offset:offset + 16]
            hex_part = " ".join("{:02X}".format(b) for b in chunk)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append("{:016X}  {:<48s}  {}".format(
                base_addr + offset, hex_part, ascii_part))
        return "\n".join(lines)

    def _do_write(self):
        iid = self._get_selected_instance_id()
        if not iid:
            return
        try:
            addr_text = self._addr_input.text().strip()
            if addr_text.lower().startswith("0x"):
                address = int(addr_text, 16)
            else:
                address = int(addr_text, 16)
        except ValueError:
            return
        text, ok = QInputDialog.getText(
            self, tr("Write Bytes"), tr("Hex bytes (e.g. 90 90):"))
        if not ok or not text:
            return
        try:
            data = bytes.fromhex(text.replace(" ", ""))
            written = self._main.write_instance_memory(iid, address, data)
            self._hex_view.setPlainText(
                tr("Wrote {} bytes at {:016X}").format(written, address))
            self._do_read()
        except Exception as e:
            self._hex_view.setPlainText(tr("Write error: {}").format(str(e)))

    def _do_scan_regions(self):
        iid = self._get_selected_instance_id()
        if not iid:
            self._region_tree.clear()
            self._region_tree.setHeaderLabels(["Base Address", "Size", "Type", "Prot"])
            return
        try:
            info = self._main.get_running_instances().get(iid, {})
            pid = info.get("pid")
            if not pid:
                self._hex_view.setPlainText(tr("PID not found."))
                return
            from core.stealth_memory import query_memory_regions
            regions = query_memory_regions(pid)
        except Exception as e:
            self._hex_view.setPlainText(tr("Scan error: {}").format(str(e)))
            return

        self._region_tree.clear()
        type_colors = {
            "HEAP": "#f0a060", "CODE": "#60d080", "STACK": "#6090d0",
            "IMAGE": "#888890", "MAPPED": "#a080c0", "PRIVATE": "#606068",
        }
        for r in regions:
            item = QTreeWidgetItem([
                "0x{:016X}".format(r["base"]),
                r["size_str"],
                r["type"],
                r["protect"],
            ])
            color = type_colors.get(r["type"], "#a0a0b0")
            for col in range(4):
                item.setForeground(col, QColor(color))
            self._region_tree.addTopLevelItem(item)

    def _on_region_selected(self, item):
        base = item.text(0)
        self._addr_input.setText(base)
        self._size_spin.setValue(256)
        self._do_read()

    def refresh_instances(self):
        current = self._instance_combo.currentData()
        self._instance_combo.clear()
        for iid, info in self._main.get_running_instances().items():
            self._instance_combo.addItem(
                "{}  (PID {})".format(info["version_id"], info["pid"]), iid)
        if current and self._instance_combo.findData(current) >= 0:
            self._instance_combo.setCurrentIndex(self._instance_combo.findData(current))


class MainWindow(QMainWindow):
    game_output = pyqtSignal(str)
    game_stopped = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("OpenMCL - Minecraft Launcher"))
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.resize(1050, 680)
        self._drag_pos = None

        self._load_config()
        self.vm = VersionManager(preferred_mirror=self._config.get("mirror", "fastmc"))
        self.aria2_manager = Aria2Manager()
        self.downloader = Downloader(self.vm, aria2_manager=self.aria2_manager)
        self.am = AccountManager()
        self.launcher = MinecraftLauncher(self.vm, self.downloader, self.am)
        self.game_process = None
        self._game_processes = {}
        self._game_instance_counter = 0
        self._game_running = False
        self._shutting_down = False
        self._current_launched_version = None
        self._selected_version = ""
        self._threads = []
        self._btn_anims = []
        self._memory_viewer = None

        # 统一内存读取接入口：游戏启动绑定 PID，退出自动 detach。
        from core.memory_api import get_memory_access
        self.memory_access = get_memory_access()

        self._init_ui()
        self._apply_ui_config()
        self.game_output.connect(self._on_game_output)
        self._start_aria2_bg()
        QTimer.singleShot(50, self._startup_load)

    def _start_aria2_bg(self):
        class Aria2StartThread(QThread):
            status = pyqtSignal(str)
            def __init__(self_inner, amgr, split):
                super().__init__()
                self_inner.amgr = amgr
                self_inner.split = split
            def run(self_inner):
                try:
                    self_inner.amgr.start(split=self_inner.split)
                    self_inner.status.emit("aria2 started")
                except Exception as e:
                    self_inner.status.emit("aria2: {}".format(e))
        threads = self._config.get("aria2_threads", 8)
        t = Aria2StartThread(self.aria2_manager, threads)
        t.status.connect(lambda msg: self.console_widget.append_info(msg))
        self._threads.append(t)
        t.start()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = self._create_sidebar()
        sidebar.setFixedWidth(232)
        root.addWidget(sidebar)

        self.tab_widget = AnimTabWidget()

        self.console_widget = ConsoleWidget()
        self.tab_widget.addTab(self.console_widget, tr("Console"))

        self.new_instance_widget = NewInstanceWidget(self.vm, self.downloader)
        self.new_instance_widget.install_requested.connect(self._on_version_installed)
        self.new_instance_widget.modloader_install_requested.connect(self._on_modloader_install_requested)
        self.new_instance_widget.console_log.connect(self.console_widget.append_info)
        self.new_instance_widget.side_panel_task.connect(self._on_side_panel_task)
        self.new_instance_widget.side_panel_progress.connect(self._on_side_panel_update)
        self.new_instance_widget.side_panel_done.connect(self._on_side_panel_task_done)
        self.new_instance_widget.set_aria2_threads(self._config.get("aria2_threads", 8))
        self.tab_widget.addTab(self.new_instance_widget, tr("New Instance"))

        self.instances_widget = InstancesWidget(self.vm, self.downloader, self.launcher)
        self.instances_widget.launch_requested.connect(self._on_instances_launch)
        self.instances_widget.console_log.connect(self.console_widget.append_info)
        self.instances_widget.refresh_needed.connect(self._refresh_version_combo)
        self.instances_widget.message_requested.connect(self._on_instances_message)
        self.instances_widget.detail_requested.connect(self._on_instance_detail)
        self.instances_widget.detail_install_modloader.connect(self._on_install_modloader_detail)
        self.instances_widget.side_panel_task.connect(self._on_side_panel_task)
        self.instances_widget.side_panel_progress.connect(self._on_side_panel_update)
        self.instances_widget.side_panel_done.connect(self._on_side_panel_task_done)
        self.game_stopped.connect(self.instances_widget.on_game_stopped)
        self.tab_widget.addTab(self.instances_widget, tr("Instances"))

        self.modpack_widget = ModpackDownloadWidget(self.vm, aria2_manager=self.aria2_manager)
        self.modpack_widget.console_log.connect(self.console_widget.append_info)
        self.modpack_widget.instance_created.connect(self._on_modpack_instance_created)
        self.tab_widget.addTab(self.modpack_widget, tr("Modpacks"))

        self._settings_tab = self._create_settings_tab()
        self.tab_widget.addTab(self._settings_tab, tr("Settings"))

        self.tab_widget.setCurrentIndex(2)

        root.addWidget(self.tab_widget, 1)

        self.function_widget = FunctionWidget(self)

        self._init_win_ctrls(central)

        self._side_panel = SidePanel(self)
        self._side_panel.result_ready.connect(self._on_side_panel_result)
        self._sp_callback = None
        self._side_panel_task_ids = {}

    def _on_side_panel_result(self, result):
        if self._sp_callback:
            cb = self._sp_callback
            self._sp_callback = None
            cb(result)

    def _show_message(self, msg_type, title, text, callback=None):
        self._sp_callback = callback
        self._side_panel.show_message(msg_type, title, text)

    def _show_input(self, title, label, default="", callback=None):
        self._sp_callback = callback
        self._side_panel.show_input(title, label, default)

    def _show_progress(self, title, text="", callback=None):
        self._sp_callback = callback
        return self._side_panel.show_progress(title, text)

    def _show_mod_browser(self, title, mc_version, loader, mods_dir):
        aria2 = self.downloader._aria2 if hasattr(self, 'downloader') and self.downloader else None
        self._side_panel.show_mod_browser(title, mc_version, loader, mods_dir, aria2)

    def _hide_panel(self, task_id=None):
        if task_id:
            self._side_panel.finish_progress(task_id)
        else:
            self._side_panel.hide_panel()

    def _on_instances_message(self, msg_type, title, text, callback):
        self._show_message(msg_type, title, text, callback=callback)

    def _on_reinstall_requested(self, version_id):
        self.tab_widget.setCurrentIndex(1)
        self.new_instance_widget.reinstall_version(version_id)

    def _on_instance_detail(self, version_id):
        from .instances_widget import InstanceDetailDialog
        dlg = InstanceDetailDialog(
            version_id, self.vm, self.downloader, self.instances_widget.modloader,
            self.instances_widget._default_java_path, self.instances_widget._java_list,
            self.instances_widget._launcher, self)
        self._show_detail_tab(dlg)

    def _on_install_modloader_detail(self, version_id, ml_type, ml_info, loader_version):
        from .instances_widget import InstanceDetailDialog
        dlg = InstanceDetailDialog(
            version_id, self.vm, self.downloader, self.instances_widget.modloader,
            self.instances_widget._default_java_path, self.instances_widget._java_list,
            self.instances_widget._launcher, self, initial_tab="modloader")
        dlg.install_modloader_for(ml_type, ml_info, loader_version)
        self._show_detail_tab(dlg)

    def _show_detail_tab(self, dlg):
        dlg.console_log.connect(self.console_widget.append_info)
        dlg.loader_installed.connect(self.instances_widget.refresh)
        dlg.uninstalled.connect(lambda vid: (self.instances_widget.refresh(), self.instances_widget.refresh_needed.emit()))
        dlg.launch_requested.connect(self._on_instances_launch)
        dlg.java_changed.connect(self.instances_widget._on_instance_java_changed)
        dlg.side_panel_task.connect(self._on_side_panel_task)
        dlg.side_panel_progress.connect(self._on_side_panel_update)
        dlg.side_panel_done.connect(self._on_side_panel_task_done)
        dlg.message_requested.connect(self._on_instances_message)
        dlg.reinstall_requested.connect(self._on_reinstall_requested)
        dlg.closed.connect(lambda: self._close_detail_tab(dlg))

        MainWindow._tab_history.append(self.tab_widget.currentIndex())
        self.tab_widget.addTab(dlg, dlg.version_id)
        self.tab_widget.setCurrentIndex(self.tab_widget.count() - 1)

    def _close_detail_tab(self, dlg):
        idx = self.tab_widget.indexOf(dlg)
        if idx >= 0:
            self.tab_widget.removeTab(idx)
            dlg.deleteLater()
        self.tab_widget.setCurrentIndex(0)

    def _animate_button_press(self, btn):
        if btn.graphicsEffect() is not None:
            return
        op = QGraphicsOpacityEffect(btn)
        op.setOpacity(1.0)
        btn.setGraphicsEffect(op)
        anim = QPropertyAnimation(op, b"opacity")
        anim.setDuration(100)
        anim.setKeyValueAt(0, 1.0)
        anim.setKeyValueAt(0.2, 0.6)
        anim.setKeyValueAt(1.0, 1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()
        self._btn_anims.append(anim)
        QTimer.singleShot(200, lambda: self._btn_anims.remove(anim) if anim in self._btn_anims else None)

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, '_win_ctrl_frame'):
            self._win_ctrl_frame.setGeometry(max(0, self.width() - 116), 4, 108, 32)
            self._win_ctrl_frame.show()
            self._win_ctrl_frame.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_win_ctrl_frame'):
            self._win_ctrl_frame.setGeometry(max(0, self.width() - 116), 4, 108, 32)

    def _start_drag(self, ev):
        self._drag_pos = ev.globalPos()

    def eventFilter(self, obj, ev):
        from PyQt5.QtCore import QEvent
        if ev.type() == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
            self._drag_pos = ev.globalPos()
            return True
        elif ev.type() == QEvent.MouseMove and self._drag_pos is not None:
            delta = ev.globalPos() - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = ev.globalPos()
            return True
        elif ev.type() == QEvent.MouseButtonRelease:
            self._drag_pos = None
            return True
        return super().eventFilter(obj, ev)

    def _init_win_ctrls(self, parent):
        self._win_ctrl_frame = QFrame(parent)
        self._win_ctrl_frame.setObjectName("winCtrlFrame")
        self._win_ctrl_frame.setFixedSize(108, 32)
        self._win_ctrl_frame.setStyleSheet("""
            #winCtrlFrame {
                background: rgba(30, 30, 38, 0.85);
                border: none;
                border-radius: 0;
            }
        """)
        layout = QHBoxLayout(self._win_ctrl_frame)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        btn_style = """
            QPushButton {
                background: transparent; color: #8888a0; border: none;
                font-size: 13px; padding: 0; min-width: 34px; min-height: 26px;
            }
            QPushButton:hover { background: #2a2a3a; color: #d0d0d8; }
        """
        min_btn = QPushButton("─")
        min_btn.setStyleSheet(btn_style)
        min_btn.clicked.connect(self.showMinimized)
        layout.addWidget(min_btn)

        max_btn = QPushButton("□")
        max_btn.setStyleSheet(btn_style)
        max_btn.clicked.connect(self._toggle_maximize)
        layout.addWidget(max_btn)

        close_btn = QPushButton("✕")
        close_btn.setStyleSheet(btn_style.replace("#2a2a3a", "#c42b1c").replace("#8888a0", "#b08888"))
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

    def _toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _create_sidebar(self):
        panel = QFrame()
        panel.setObjectName("sidebar")
        panel.setStyleSheet(SIDEBAR_QSS)
        panel.installEventFilter(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 10, 14, 14)
        layout.setSpacing(0)

        brand = QHBoxLayout()
        brand.setSpacing(8)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet("background: #808088;")
        brand.addWidget(dot)
        name_lbl = QLabel(tr("OpenMinecraft"))
        name_lbl.setStyleSheet("color: #d0d0d0; font-size: 10px; font-weight: 500; letter-spacing: 3px;")
        brand.addWidget(name_lbl)
        brand.addStretch()
        sub_lbl = QLabel(tr("LAUNCHER"))
        sub_lbl.setStyleSheet("color: #505058; font-size: 9px; letter-spacing: 1.5px;")
        brand.addWidget(sub_lbl)
        layout.addLayout(brand)

        layout.addSpacing(12)

        self._nav_back_btn = QPushButton("\u2190")
        self._nav_back_btn.setFixedSize(36, 36)
        self._nav_back_btn.setStyleSheet("""
            QPushButton { background: transparent; color: #808088; border: none;
                          font-size: 18px; }
            QPushButton:hover { color: #d0d0d0; background: #1e1e26; }
        """)
        self._nav_back_btn.setCursor(Qt.PointingHandCursor)
        self._nav_back_btn.clicked.connect(self._nav_go_back)
        layout.addWidget(self._nav_back_btn)

        self.account_label = QLabel(tr("Not logged in"))
        self.account_label.setAlignment(Qt.AlignCenter)
        self.account_label.setStyleSheet("color: #606068; font-size: 10px; padding: 4px;")
        layout.addWidget(self.account_label)

        layout.addSpacing(6)

        login_row = QHBoxLayout()
        login_row.setSpacing(3)
        for name, slot in [("MS", self.on_login_microsoft), ("Off", self.on_login_offline), ("Skin", self.on_login_skin_station)]:
            btn = QPushButton(name)
            btn.setStyleSheet(SMALL_SIDEBAR_BTN_QSS)
            btn.clicked.connect(slot)
            btn.pressed.connect(lambda b=btn: self._animate_button_press(b))
            login_row.addWidget(btn)
        layout.addLayout(login_row)

        layout.addSpacing(10)

        title_row = QHBoxLayout()
        title_lbl = QLabel(tr("VERSIONS"))
        title_lbl.setStyleSheet("color: #505058; font-size: 9px; font-weight: bold; letter-spacing: 2px; padding: 4px 0;")
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        layout.addLayout(title_row)

        layout.addSpacing(4)

        self._instance_list = QListWidget()
        self._instance_list.setStyleSheet(SIDEBAR_LIST_QSS)
        self._instance_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._instance_list.currentTextChanged.connect(self._on_sidebar_instance_changed)
        self._instance_list.itemClicked.connect(self._on_sidebar_item_clicked)
        layout.addWidget(self._instance_list, 1)

        layout.addSpacing(12)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(SEPARATOR_QSS)
        layout.addWidget(sep)

        layout.addSpacing(10)

        self.launch_btn = QPushButton(tr("LAUNCH"))
        self.launch_btn.setStyleSheet(ACCENT_BTN_QSS)
        self.launch_btn.setMinimumHeight(46)
        self.launch_btn.setCursor(Qt.PointingHandCursor)
        self.launch_btn.clicked.connect(self.on_launch)
        self.launch_btn.pressed.connect(lambda: self._animate_button_press(self.launch_btn))

        self.stop_btn = QPushButton(tr("STOP"))
        self.stop_btn.setStyleSheet(STOP_BTN_QSS)
        self.stop_btn.setMinimumHeight(46)
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.clicked.connect(self.on_stop)
        self.stop_btn.pressed.connect(lambda: self._animate_button_press(self.stop_btn))
        self.stop_btn.setVisible(False)

        layout.addWidget(self.launch_btn)
        layout.addWidget(self.stop_btn)

        self._running_section = QWidget()
        self._running_section.setVisible(False)
        running_layout = QVBoxLayout(self._running_section)
        running_layout.setContentsMargins(0, 8, 0, 2)
        running_layout.setSpacing(4)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet(SEPARATOR_QSS)
        running_layout.addWidget(sep2)

        running_title = QLabel(tr("RUNNING"))
        running_title.setStyleSheet(
            "color: #505058; font-size: 9px; font-weight: bold; letter-spacing: 2px; padding: 4px 0;")
        running_layout.addWidget(running_title)

        self._running_instances_list = QVBoxLayout()
        self._running_instances_list.setSpacing(3)
        running_layout.addLayout(self._running_instances_list)
        running_layout.addStretch()
        layout.addWidget(self._running_section)

        layout.addSpacing(6)

        self.func_btn = QPushButton(tr("Function"))
        self.func_btn.setStyleSheet(SMALL_SIDEBAR_BTN_QSS)
        self.func_btn.setMinimumHeight(32)
        self.func_btn.setCursor(Qt.PointingHandCursor)
        self.func_btn.clicked.connect(self._on_func_btn)
        layout.addWidget(self.func_btn)

        self._game_timer = QTimer(self)
        self._game_timer.timeout.connect(self._poll_game_exit)
        self._game_timer.setInterval(500)

        return panel

    _tab_history = []

    def _nav_go_back(self):
        if MainWindow._tab_history:
            idx = MainWindow._tab_history.pop()
            if idx < self.tab_widget.count():
                self.tab_widget.setCurrentIndex(idx)

    def _on_func_btn(self):
        self._side_panel.show_function_widget(self.function_widget)

    def _on_sidebar_instance_changed(self, version_id):
        if version_id:
            self._selected_version = version_id
            prev = self.tab_widget.currentIndex()
            self.tab_widget.setCurrentIndex(2)
            if prev != 2:
                MainWindow._tab_history.append(prev)
            QTimer.singleShot(0, self._auto_detect_java)

    def _on_sidebar_item_clicked(self, item):
        if item:
            self._selected_version = item.text()
            prev = self.tab_widget.currentIndex()
            self.tab_widget.setCurrentIndex(2)
            if prev != 2:
                MainWindow._tab_history.append(prev)
            QTimer.singleShot(0, self._auto_detect_java)

    def _refresh_instance_sidebar(self):
        self._instance_list.blockSignals(True)
        prev = self._instance_list.currentItem()
        prev_text = prev.text() if prev else self._selected_version
        self._instance_list.clear()
        installed = self.vm.get_installed_versions()
        for v in sorted(installed, reverse=True):
            self._instance_list.addItem(v)
        if prev_text:
            items = self._instance_list.findItems(prev_text, Qt.MatchExactly)
            if items:
                self._instance_list.setCurrentItem(items[0])
                self._selected_version = prev_text
        elif self._instance_list.count() > 0:
            self._instance_list.setCurrentRow(0)
            self._selected_version = self._instance_list.item(0).text()
        else:
            self._selected_version = ""
        self._instance_list.blockSignals(False)

    def _create_settings_tab(self):
        w = QScrollArea()
        w.setObjectName("contentScroll")
        w.setWidgetResizable(True)
        w.setFrameShape(QFrame.NoFrame)
        w.setStyleSheet("QScrollArea { background: #121214; border: none; }")
        inner = QWidget()
        inner.setStyleSheet("background: #121214;")
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(24, 16, 24, 20)
        layout.setSpacing(10)

        layout.addWidget(self._section_label("ACCOUNT"))
        self._settings_account_label = QLabel(self.account_label.text())
        self._settings_account_label.setStyleSheet("color: #606068; font-size: 10px; padding: 4px;")
        layout.addWidget(self._settings_account_label)

        layout.addWidget(self._section_label("OPACITY"))
        opac_row = QHBoxLayout()
        opac_row.setSpacing(10)
        opac_row.addWidget(QLabel(tr("Window:")))
        self._opacity_slider = QSlider(Qt.Horizontal)
        self._opacity_slider.setRange(60, 100)
        self._opacity_slider.setValue(int(self._config.get("window_opacity", 92)))
        self._opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self._opacity_label = QLabel(tr("{}%").format(self._opacity_slider.value()))
        self._opacity_label.setStyleSheet("color: #a0a0a8; min-width: 36px;")
        self._opacity_slider.valueChanged.connect(
            lambda v: self._opacity_label.setText(tr("{}%").format(v)))
        opac_row.addWidget(self._opacity_slider, 1)
        opac_row.addWidget(self._opacity_label)
        layout.addLayout(opac_row)

        layout.addWidget(self._section_label("LANGUAGE"))
        lang_row = QHBoxLayout()
        lang_row.setSpacing(10)
        lang_row.addWidget(QLabel(tr("Language:")))
        self.lang_combo = QComboBox()
        self.lang_combo.setMinimumHeight(28)
        langs = available_langs()
        for code, name in langs.items():
            self.lang_combo.addItem(name, code)
        cur = current_lang()
        idx = self.lang_combo.findData(cur)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        self.lang_combo.currentIndexChanged.connect(self._on_language_changed)
        lang_row.addWidget(self.lang_combo, 1)
        layout.addLayout(lang_row)

        layout.addWidget(self._section_label("JAVA"))
        java_row = QHBoxLayout()
        java_row.setSpacing(10)
        java_row.addWidget(QLabel(tr("Default Java:")))
        self.java_combo = QComboBox()
        self.java_combo.setMinimumHeight(28)
        self.java_combo.currentIndexChanged.connect(self._on_java_changed)
        java_row.addWidget(self.java_combo, 1)
        layout.addLayout(java_row)

        java_btn_row = QHBoxLayout()
        java_btn_row.setSpacing(8)
        self.java_auto_btn = QPushButton(tr("Detect"))
        self.java_auto_btn.clicked.connect(self._auto_detect_java)
        self.java_auto_btn.pressed.connect(lambda: self._animate_button_press(self.java_auto_btn))
        java_btn_row.addWidget(self.java_auto_btn)
        self.java_browse_btn = QPushButton(tr("Browse"))
        self.java_browse_btn.clicked.connect(self.on_browse_java)
        self.java_browse_btn.pressed.connect(lambda: self._animate_button_press(self.java_browse_btn))
        java_btn_row.addWidget(self.java_browse_btn)
        java_btn_row.addStretch()
        layout.addLayout(java_btn_row)

        layout.addWidget(self._section_label("DOWNLOAD"))
        mirror_row = QHBoxLayout()
        mirror_row.setSpacing(10)
        mirror_row.addWidget(QLabel(tr("Mirror:")))
        self.mirror_combo = QComboBox()
        self.mirror_combo.setMinimumHeight(28)
        for key, name in MIRROR_NAMES.items():
            self.mirror_combo.addItem(name, key)
        self.mirror_combo.currentIndexChanged.connect(self._on_mirror_changed)
        mirror_row.addWidget(self.mirror_combo, 1)
        layout.addLayout(mirror_row)

        aria2_row = QHBoxLayout()
        aria2_row.setSpacing(10)
        aria2_row.addWidget(QLabel(tr("Aria2 threads:")))
        self.aria2_threads_spin = QSpinBox()
        self.aria2_threads_spin.setRange(1, 128)
        self.aria2_threads_spin.setValue(self._config.get("aria2_threads", 8))
        self.aria2_threads_spin.valueChanged.connect(self._on_aria2_threads_changed)
        aria2_row.addWidget(self.aria2_threads_spin, 1)
        layout.addLayout(aria2_row)

        layout.addWidget(self._section_label("MEMORY"))
        mem_row = QHBoxLayout()
        mem_row.setSpacing(10)
        mem_row.addWidget(QLabel(tr("Max RAM:")))
        self.memory_spin = QSpinBox()
        self.memory_spin.setRange(512, 65536)
        self.memory_spin.setValue(2048)
        self.memory_spin.setSingleStep(512)
        self.memory_spin.setSuffix(" MB")
        mem_row.addWidget(self.memory_spin, 1)
        layout.addLayout(mem_row)

        mem_row2 = QHBoxLayout()
        mem_row2.setSpacing(10)
        mem_row2.addWidget(QLabel(tr("Min RAM:")))
        self.min_memory_spin = QSpinBox()
        self.min_memory_spin.setRange(0, 65536)
        self.min_memory_spin.setValue(0)
        self.min_memory_spin.setSingleStep(256)
        self.min_memory_spin.setSpecialValueText("Default")
        self.min_memory_spin.setSuffix(" MB")
        mem_row2.addWidget(self.min_memory_spin, 1)
        layout.addLayout(mem_row2)

        layout.addWidget(self._section_label("WINDOW"))
        res_row = QHBoxLayout()
        res_row.setSpacing(10)
        res_row.addWidget(QLabel(tr("Mode:")))
        self.window_mode = QComboBox()
        self.window_mode.setMinimumHeight(28)
        self.window_mode.addItem("Windowed", "windowed")
        self.window_mode.addItem("Fullscreen", "fullscreen")
        self.window_mode.currentIndexChanged.connect(self._on_window_mode_changed)
        res_row.addWidget(self.window_mode, 1)
        self.res_combo = QComboBox()
        self.res_combo.setMinimumHeight(28)
        self.res_combo.setEditable(True)
        for r in ["854x480", "1280x720", "1366x768", "1920x1080", "2560x1440", "3840x2160"]:
            self.res_combo.addItem(r)
        self.res_combo.setCurrentText("854x480")
        self.res_combo.currentTextChanged.connect(self._on_resolution_changed)
        res_row.addWidget(self.res_combo)
        layout.addLayout(res_row)

        layout.addWidget(self._section_label("JVM ARGS"))
        self.jvm_args_input = QLineEdit()
        self.jvm_args_input.setPlaceholderText("Extra JVM args (global fallback)...")
        layout.addWidget(self.jvm_args_input)

        layout.addWidget(self._section_label("DIRECTORY"))
        dir_row = QHBoxLayout()
        dir_row.setSpacing(6)
        self.dir_input = QLineEdit()
        self.dir_input.setText(self.vm.minecraft_dir)
        dir_row.addWidget(self.dir_input, 1)
        browse_btn = QPushButton(tr("..."))
        browse_btn.setFixedWidth(32)
        browse_btn.clicked.connect(self.on_browse_dir)
        browse_btn.pressed.connect(lambda: self._animate_button_press(browse_btn))
        dir_row.addWidget(browse_btn)
        apply_btn = QPushButton(tr("OK"))
        apply_btn.setFixedWidth(40)
        apply_btn.clicked.connect(self.on_apply_dir)
        apply_btn.pressed.connect(lambda: self._animate_button_press(apply_btn))
        dir_row.addWidget(apply_btn)
        layout.addLayout(dir_row)

        layout.addStretch()
        w.setWidget(inner)
        return w

    def _section_label(self, text):
        lbl = QLabel(tr(text))
        lbl.setStyleSheet(
            "color: #505058; font-size: 10px; font-weight: bold; letter-spacing: 2px; padding: 6px 0 2px;")
        return lbl

    def _on_opacity_changed(self, value):
        self._config["window_opacity"] = value
        self.setWindowOpacity(value / 100.0)
        self._save_config()

    def _on_language_changed(self, idx):
        code = self.lang_combo.itemData(idx)
        if code and code != current_lang():
            init_lang(code)
            self._config["language"] = code
            self._save_config()
            self._retranslate_ui()

    def _retranslate_ui(self):
        self.setWindowTitle(tr("OpenMCL - Minecraft Launcher"))
        self.tab_widget.setTabText(0, tr("Console"))
        self.tab_widget.setTabText(1, tr("New Instance"))
        self.tab_widget.setTabText(2, tr("Instances"))
        self.tab_widget.setTabText(3, tr("Modpacks"))
        self.tab_widget.setTabText(4, tr("Settings"))

    def _apply_ui_config(self):
        opacity = self._config.get("window_opacity", 92)
        self.setWindowOpacity(opacity / 100.0)
        accounts = self.am.load_accounts()
        if accounts:
            self.am.current_account = accounts[-1]
            acc = accounts[-1]
            tag = ""
            if acc.user_type == "offline":
                tag = " (offline)"
            elif acc.user_type == "yggdrasil":
                tag = " (Yggdrasil)"
            txt = "Logged in as: {}{}".format(acc.username, tag)
            self.account_label.setText(txt)
            if hasattr(self, '_settings_account_label'):
                self._settings_account_label.setText(txt)

        mirror = self._config.get("mirror", "fastmc")
        idx = self.mirror_combo.findData(mirror)
        if idx >= 0:
            self.mirror_combo.setCurrentIndex(idx)

        wm = self._config.get("window_mode", "windowed")
        idx = self.window_mode.findData(wm)
        if idx >= 0:
            self.window_mode.setCurrentIndex(idx)

        res = self._config.get("resolution", "854x480")
        self.res_combo.setCurrentText(res)

    def _on_side_panel_task(self, task_id, title, text):
        self._side_panel_task_ids[task_id] = self._side_panel.show_progress(title, text)

    def _on_side_panel_update(self, task_id, text):
        real_id = self._side_panel_task_ids.get(task_id)
        if real_id:
            self._side_panel.update_progress(real_id, text)

    def _on_side_panel_task_done(self, task_id):
        real_id = self._side_panel_task_ids.pop(task_id, None)
        if real_id:
            self._side_panel.finish_progress(real_id)

    def _startup_load(self):
        self._apply_ui_config()
        self._startup_task = self._show_progress(tr("Loading"), tr("Detecting Java installations..."))

        class JavaDetectThread(QThread):
            result = pyqtSignal(list)
            def run(self_inner):
                try:
                    result = find_java()
                    self_inner.result.emit(result)
                except Exception:
                    self_inner.result.emit([])

        t = JavaDetectThread()
        t.result.connect(self._on_startup_java_ready)
        self._threads.append(t)
        t.start()

    def _on_startup_java_ready(self, java_list):
        self._hide_panel(getattr(self, "_startup_task", None))
        self._refresh_version_combo()
        self._populate_java_combo(java_list)
        self.instances_widget.set_java_list(java_list)
        self.console_widget.append_info(tr("Found {} Java installation(s).").format(len(java_list)))
        self._auto_refresh_attempts = 0
        self._auto_refresh_versions()

    def _auto_refresh_versions(self):
        if not network_available():
            self.console_widget.append_info(tr("No network - version list will load when online."))
            self.new_instance_widget.status_label.setText(tr("Offline mode. Click 'Refresh List' when connected."))
            return
        self.console_widget.append_info(tr("Fetching version manifest..."))
        self._manifest_fetch_worker = FetchManifestWorker(self.vm)
        self._manifest_fetch_worker.finished.connect(self._on_auto_manifest_ready)
        self._manifest_fetch_worker.start()

    def _on_auto_manifest_ready(self, success, error):
        if success:
            self.new_instance_widget.refresh_version_list()
            self.instances_widget.refresh()
            self.console_widget.append_info(tr("Version list loaded."))
        else:
            self._auto_refresh_attempts += 1
            if self._auto_refresh_attempts < 5:
                self.console_widget.append_info(
                    "Manifest fetch failed (attempt {}/5), retrying...".format(self._auto_refresh_attempts))
                QTimer.singleShot(2000, self._auto_refresh_versions)
            else:
                self.console_widget.append_info(tr("Could not fetch version manifest after 5 attempts."))
                self.new_instance_widget.status_label.setText(tr("Failed to load. Click 'Refresh List' to retry."))

    def _load_config(self):
        self._config = {}
        try:
            if os.path.isfile(CONFIG_FILE):
                with open(CONFIG_FILE, "r") as f:
                    self._config = json.load(f)
        except Exception:
            self._config = {}

    def _save_config(self):
        try:
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            with open(CONFIG_FILE, "w") as f:
                json.dump(self._config, f, indent=2)
        except Exception:
            pass

    def _on_mirror_changed(self):
        key = self.mirror_combo.currentData()
        if key and key != self._config.get("mirror"):
            self._config["mirror"] = key
            self._save_config()
            self.vm.preferred_mirror = key
            self.console_widget.append_info(tr("Mirror switched to: {}").format(MIRROR_NAMES.get(key, key)))

    def _on_aria2_threads_changed(self, value):
        if value != self._config.get("aria2_threads"):
            self._config["aria2_threads"] = value
            self._save_config()
            self.new_instance_widget.set_aria2_threads(value)
            self.console_widget.append_info(tr("Download threads set to {} (applied on next install)").format(value))

    def _on_window_mode_changed(self):
        key = self.window_mode.currentData()
        if key:
            self._config["window_mode"] = key
            self._save_config()

    def _on_resolution_changed(self):
        res = self.res_combo.currentText().strip()
        if res:
            self._config["resolution"] = res
            self._save_config()

    def _refresh_version_combo(self):
        self._refresh_instance_sidebar()

    def _on_java_changed(self):
        java_path = self.java_combo.currentData() or "java"
        self.new_instance_widget.set_java_path(java_path)
        self.instances_widget.set_java_path(java_path)

    def _on_version_installed(self, version_id):
        self._refresh_version_combo()
        self.instances_widget.refresh()

    def _on_modpack_instance_created(self, instance_name, loader_type, mc_version):
        self._refresh_version_combo()
        self.instances_widget.refresh()
        if loader_type and loader_type != "vanilla":
            self.console_widget.append_info(
                "Modpack instance '{}' created. Installing {} loader...".format(instance_name, loader_type))
            self.instances_widget.install_modloader_for(instance_name, loader_type, {}, "")

    def _on_modloader_install_requested(self, version_id, ml_type, ml_info, loader_ver):
        self.tab_widget.setCurrentIndex(2)
        self.console_widget.append_info(tr("Installing {} for {}...").format(ml_type, version_id))
        self.instances_widget.install_modloader_for(version_id, ml_type, ml_info, loader_ver)

    def on_login_microsoft(self):
        self.console_widget.append_info(tr("Starting Microsoft login..."))
        self._login_thread = MicrosoftLoginThread(self.am, self)
        self._login_thread.login_message.connect(lambda msg: self._show_message("information", tr("Microsoft Login"), msg))
        self._login_thread.request_code.connect(self._show_ms_code_dialog)
        self._login_thread.finished.connect(self._on_ms_login_success)
        self._login_thread.failed.connect(self._on_ms_login_failed)
        self._login_thread.start()

    def _show_ms_code_dialog(self):
        self._show_input(tr("Microsoft Login"), tr("Enter the code from your browser:"), callback=lambda code: (
            self._login_thread.provide_code(code.strip()) if code and code.strip() else self._login_thread.provide_code(None)
        ))

    def _on_ms_login_success(self, account, msg):
        txt = "Logged in as: {}".format(account.username)
        self.account_label.setText(txt)
        if hasattr(self, '_settings_account_label'):
            self._settings_account_label.setText(txt)
        self.console_widget.append_success(tr("Logged in as {}").format(account.username))
        self._show_message("information", tr("Success"), tr("Logged in as {}").format(account.username))

    def _on_ms_login_failed(self, err):
        self.console_widget.append_error(tr("Login failed: {}").format(err))
        self._show_message("critical", tr("Login Failed"), err)

    def on_login_offline(self):
        self._show_input(tr("Offline Login"), tr("Username:"), callback=lambda name: self._on_offline_login_done(name))

    def _on_offline_login_done(self, name):
        if name and name.strip():
            account = self.am.add_offline_account(name.strip())
            txt = "Logged in as: {} (offline)".format(account.username)
            self.account_label.setText(txt)
            if hasattr(self, '_settings_account_label'):
                self._settings_account_label.setText(txt)
            self.console_widget.append_success(tr("Offline login as {}").format(account.username))
            self._show_message("information", tr("Success"), tr("Offline login as {}").format(account.username))

    def on_login_skin_station(self):
        fields = [
            (tr("Account:"), "", False),
            (tr("Password:"), "", True),
            ("API Root:", "https://littleskin.cn/api/yggdrasil", False),
        ]
        self._side_panel.show_form(tr("Skin Station Login"), fields)

        def on_result(result):
            if result is None:
                return
            email = result.get(0, "").strip()
            password = result.get(1, "")
            api_root = result.get(2, "").strip()
            if not email or not password or not api_root:
                self._show_message("warning", tr("Missing"), tr("All fields are required."))
                return
            self.console_widget.append_info(tr("Authenticating with {}...").format(api_root))
            try:
                account = self.am.login_skin_station(email, password, api_root)
                txt = "Logged in as: {} (Yggdrasil)".format(account.username)
                self.account_label.setText(txt)
                if hasattr(self, '_settings_account_label'):
                    self._settings_account_label.setText(txt)
                self.console_widget.append_success(tr("Logged in as {} via {}").format(account.username, api_root))
                self._show_message("information", tr("Success"), tr("Logged in as {}").format(account.username))
            except Exception as e:
                self.console_widget.append_error(tr("Auth failed: {}").format(e))
                self._show_message("critical", tr("Login Failed"), str(e))

        self._side_panel.result_ready.connect(on_result, Qt.UniqueConnection)

    def on_browse_java(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Java Executable", "",
            "Java Executable (java.exe javaw.exe java);;All Files (*.*)")
        if path:
            jv = get_java_major_version(path)
            label = "Java {} (manual)".format(jv) if jv > 0 else os.path.basename(path)
            idx = self.java_combo.findData(path)
            if idx < 0:
                self.java_combo.insertItem(0, label, path)
                self.java_combo.insertSeparator(1)
                idx = 0
            self.java_combo.setCurrentIndex(idx)
            self.console_widget.append_info(tr("Added Java: {}").format(path))

    _cached_java_list = None

    def _auto_detect_java(self):
        if MainWindow._cached_java_list is not None:
            self.instances_widget.set_java_list(MainWindow._cached_java_list)
            self._populate_java_combo(MainWindow._cached_java_list)
            return
        task_id = self._show_progress(tr("Java Detection"), tr("Detecting Java installations..."))

        class DetectThread(QThread):
            result = pyqtSignal(list)
            def run(self_inner):
                try:
                    java_list = find_java()
                    self_inner.result.emit(java_list)
                except Exception:
                    self_inner.result.emit([])

        def on_ready(java_list):
            self._hide_panel(task_id)
            MainWindow._cached_java_list = java_list
            self._populate_java_combo(java_list)
            self.instances_widget.set_java_list(java_list)

        t = DetectThread()
        t.result.connect(on_ready)
        self._threads.append(t)
        t.start()

    def _populate_java_combo(self, java_list, mc_version=None):
        required = get_mc_required_java(mc_version) if mc_version else 0
        manual_items = []
        for i in range(self.java_combo.count()):
            manual_items.append((self.java_combo.itemText(i), self.java_combo.itemData(i)))
        self.java_combo.clear()
        if not java_list and not manual_items:
            self.java_combo.addItem("java (not found)", "java")
            self.console_widget.append_warning(tr("No Java installations found."))
            return
        for j in java_list:
            path = j["path"]
            ver = j.get("version", "?")
            arch = j.get("arch", "?")
            jv = j.get("jvm_major", 0) or get_java_major_version(path)
            is_ok = True
            if required > 0 and jv > 0:
                is_ok = jv >= required
            tag = "OK" if is_ok else "OLD"
            label = "[{}] Java {} ({}-bit)".format(tag, ver, arch)
            self.java_combo.addItem(label, path)
            self.java_combo.setItemData(self.java_combo.count() - 1, path, Qt.ToolTipRole)
        for text, data in manual_items:
            if data is None:
                continue
            idx = self.java_combo.findData(data)
            if idx < 0:
                self.java_combo.addItem(text, data)
        cnt = len(java_list)
        self.console_widget.append_info(tr("Found {} Java installation(s).").format(cnt))

    def on_browse_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select Minecraft Directory")
        if path:
            self.dir_input.setText(path)

    def on_apply_dir(self):
        new_dir = self.dir_input.text()
        if new_dir:
            self.vm = VersionManager(new_dir, preferred_mirror=self.vm.preferred_mirror)
            self.downloader = Downloader(self.vm, aria2_manager=self.aria2_manager)
            self.am = AccountManager(new_dir)
            self.launcher = MinecraftLauncher(self.vm, self.downloader, self.am)
            self.new_instance_widget.vm = self.vm
            self.new_instance_widget.downloader = self.downloader
            self.instances_widget.vm = self.vm
            self.instances_widget.downloader = self.downloader
            self._startup_load()
            self.console_widget.append_success(tr("Minecraft directory changed to {}").format(new_dir))

    def _on_instances_launch(self, version_id):
        items = self._instance_list.findItems(version_id, Qt.MatchExactly)
        if items:
            self._instance_list.setCurrentItem(items[0])
        self.on_launch(version_id)

    def on_launch(self, version_id=None):
        if version_id is None:
            version_id = self._selected_version
        if not version_id:
            item = self._instance_list.currentItem()
            if item:
                version_id = item.text()
        if not version_id:
            self._show_message("warning", tr("Warning"), tr("Please select a version to launch."))
            return
        if self.am.current_account is None:
            self._show_message("warning", tr("Warning"), tr("Please login first."))
            return
        installed = self.vm.get_installed_versions()
        if version_id not in installed:
            self._show_message("warning", tr("Warning"), tr("Version {} is not installed.").format(version_id))
            self.tab_widget.setCurrentIndex(0)
            return

        data = self.vm.load_version_json(version_id)
        oml = data.get("_openmcl", {}) if data else {}
        java_path = oml.get("java_path", "") or self.java_combo.currentData() or "java"

        jvm_major = get_java_major_version(java_path)
        required = get_mc_required_java_for_version(self.vm, version_id)
        if required > 0 and jvm_major > 0 and jvm_major < required:
            java_list = find_java_for_mc(version_id)
            for j in java_list:
                j_major = (get_java_major_version(j["path"]) if j.get("version") != "unknown" else 0)
                if j_major >= required:
                    self.console_widget.append_warning(
                        "Java {} required, switched from JVM {}".format(required, jvm_major))
                    java_path = j["path"]
                    break

        max_memory = self.memory_spin.value()
        min_memory = self.min_memory_spin.value()
        fullscreen = self.window_mode.currentData() == "fullscreen"
        res_text = self.res_combo.currentText().strip()
        width = height = None
        try:
            parts = res_text.replace("x", "X").replace("\u00d7", "X").split("X")
            w = int(parts[0].strip())
            h = int(parts[1].strip())
            if w > 0 and h > 0:
                width, height = w, h
        except Exception:
            pass

        global_jvm_text = self.jvm_args_input.text().strip()
        jvm_args = ([a.strip() for a in global_jvm_text.split() if a.strip()] if global_jvm_text else None)
        inst_jvm_text = oml.get("jvm_args", "")
        if inst_jvm_text:
            inst_jvm = [a.strip() for a in inst_jvm_text.split() if a.strip()]
            if jvm_args is None:
                jvm_args = inst_jvm
            else:
                jvm_args = jvm_args + inst_jvm
        if jvm_args:
            self.console_widget.append_info(tr("JVM args: {}").format(' '.join(jvm_args)))

        self.console_widget.append_info(tr("Preparing launch {}...").format(version_id))
        self.console_widget.append_info(tr("Java: {}").format(java_path))
        self.console_widget.append_info(tr("Max Memory: {}MB").format(max_memory))
        self._game_instance_counter += 1
        instance_id = "{}:{}".format(version_id, self._game_instance_counter)
        self._current_launched_version = version_id
        self.console_widget.append_info(tr("Launching instance {}...").format(instance_id))

        class LaunchThread(QThread):
            finished_signal = pyqtSignal(object, str, object)
            def __init__(self_inner, launcher, vid, java_p, max_mem, min_mem, jvm_a, w, h, fs, cb):
                super().__init__()
                self_inner._launcher = launcher
                self_inner._vid = vid
                self_inner._java_p = java_p
                self_inner._max_mem = max_mem
                self_inner._min_mem = min_mem
                self_inner._jvm_a = jvm_a
                self_inner._w = w
                self_inner._h = h
                self_inner._fs = fs
                self_inner._cb = cb

            def run(self_inner):
                try:
                    process = self_inner._launcher.launch(
                        version_id=self_inner._vid,
                        java_path=self_inner._java_p,
                        max_memory=self_inner._max_mem,
                        min_memory=self_inner._min_mem,
                        jvm_args=self_inner._jvm_a,
                        width=self_inner._w,
                        height=self_inner._h,
                        fullscreen=self_inner._fs,
                        callback=self_inner._cb,
                    )
                    iso = getattr(self_inner._launcher, '_process_isolation', None)
                    self_inner.finished_signal.emit(process, "", iso)
                except Exception as e:
                    self_inner.finished_signal.emit(None, str(e), None)

        def on_launch_done(process, error_msg, iso):
            if error_msg:
                self.console_widget.append_error(tr("Launch failed: {}").format(error_msg))
                self._show_message("critical", tr("Launch Error"), error_msg)
                self._set_game_running(False)
            else:
                self.game_process = process
                self._game_processes[instance_id] = {
                    "process": process,
                    "version_id": version_id,
                    "iso": iso,
                    "pid": process.pid,
                }
                try:
                    self.memory_access.attach(process.pid)
                except Exception as e:
                    self.console_widget.append_warning(
                        "Memory attach failed: {}".format(e))
                self._set_game_running(True)
                self._update_running_instances_sidebar()

        t = LaunchThread(self.launcher, version_id, java_path, max_memory,
                         min_memory if min_memory > 0 else None,
                         jvm_args, width, height, fullscreen,
                         lambda line: self.game_output.emit(line))
        t.finished_signal.connect(on_launch_done)
        t.finished.connect(lambda: self._cleanup_thread(t))
        self._threads.append(t)
        t.start()

    def _cleanup_thread(self, t):
        if t in self._threads:
            self._threads.remove(t)

    def _on_game_output(self, line):
        if "[ERROR]" in line.upper() or "ERROR" in line.upper():
            self.console_widget.append_error(line)
        elif "WARN" in line.upper():
            self.console_widget.append_warning(line)
        else:
            self.console_widget.append(line)

    def _set_game_running(self, running):
        if running:
            self._game_running = True
        else:
            self._game_running = bool(self._game_processes)
        self.launch_btn.setVisible(not self._game_running)
        self.stop_btn.setVisible(self._game_running)
        if self._game_running:
            self._game_timer.start()
        else:
            self._game_timer.stop()
            self.launch_btn.setEnabled(True)
        self._update_running_instances_sidebar()

    def on_stop(self):
        if not self._game_processes:
            self._set_game_running(False)
            return
        self.console_widget.append_warning(tr("Stopping all Minecraft instances..."))
        self.stop_btn.setEnabled(False)
        for iid, entry in list(self._game_processes.items()):
            proc = entry["process"]
            try:
                proc.terminate()
            except Exception:
                pass
        for iid, entry in list(self._game_processes.items()):
            proc = entry["process"]
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            iso = entry.get("iso")
            if iso:
                try:
                    iso.close()
                except Exception:
                    pass
        self._game_processes.clear()
        self.console_widget.append_info(tr("All Minecraft instances stopped."))
        self._set_game_running(False)

    def get_running_instances(self):
        return {
            iid: {"version_id": e["version_id"], "pid": e["pid"]}
            for iid, e in self._game_processes.items()
            if e["process"].poll() is None
        }

    def read_instance_memory(self, instance_id, address, size):
        entry = self._game_processes.get(instance_id)
        if not entry or entry["process"].poll() is not None:
            raise RuntimeError("Instance {} not running".format(instance_id))
        api = self.memory_access.get(entry["pid"])
        if api is not None and api.is_open:
            return api.read_bytes(address, size)
        from core.stealth_memory import stealth_read_process_memory
        return stealth_read_process_memory(entry["pid"], address, size)

    def write_instance_memory(self, instance_id, address, data):
        entry = self._game_processes.get(instance_id)
        if not entry or entry["process"].poll() is not None:
            raise RuntimeError("Instance {} not running".format(instance_id))
        api = self.memory_access.get(entry["pid"])
        if api is not None and api.is_open:
            return api.write_bytes(address, data)
        from core.stealth_memory import stealth_write_process_memory
        return stealth_write_process_memory(entry["pid"], address, data)

    def _update_running_instances_sidebar(self):
        while self._running_instances_list.count():
            item = self._running_instances_list.takeAt(0)
            w = item.widget()
            if w is None:
                inner = item.layout()
                if inner is not None:
                    while inner.count():
                        sw = inner.takeAt(0).widget()
                        if sw:
                            sw.setParent(None)
                            sw.deleteLater()
                    inner.setParent(None)
                continue
            w.setParent(None)
            w.deleteLater()

        running = self.get_running_instances()
        if not running:
            self._running_section.setVisible(False)
            return
        self._running_section.setVisible(True)

        for iid, info in running.items():
            row_widget = QWidget()
            row_widget.setObjectName("runningInstance")
            row_widget.setStyleSheet(RUNNING_INSTANCE_QSS)
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)

            dot = QLabel("\u25cf")
            dot.setObjectName("runningDot")
            row.addWidget(dot)

            label = QLabel(info["version_id"])
            label.setObjectName("runningLabel")
            row.addWidget(label)

            pid_label = QLabel("PID:{}".format(info["pid"]))
            pid_label.setObjectName("runningPid")
            row.addWidget(pid_label)

            row.addStretch()

            mem_btn = QPushButton("MEM")
            mem_btn.setStyleSheet(MEM_BTN_QSS)
            mem_btn.setFixedHeight(22)
            mem_btn.clicked.connect(
                lambda checked, i=iid: self._open_memory_viewer(i))
            row.addWidget(mem_btn)

            self._running_instances_list.addWidget(row_widget)

    def _open_memory_viewer(self, instance_id):
        if self._memory_viewer is None:
            self._memory_viewer = MemoryViewerDialog(self)
        self._memory_viewer.refresh_instances()
        idx = self._memory_viewer._instance_combo.findData(instance_id)
        if idx >= 0:
            self._memory_viewer._instance_combo.setCurrentIndex(idx)
        self._memory_viewer.show()
        self._memory_viewer.raise_()
        self._memory_viewer.activateWindow()

    def _poll_game_exit(self):
        if not self._game_processes:
            self._set_game_running(False)
            return
        exited = []
        exited_pids = []
        for iid, entry in list(self._game_processes.items()):
            proc = entry["process"]
            rc = proc.poll()
            if rc is not None:
                self.console_widget.append_info(
                    tr("Instance {} ({}) exited with code {}.").format(iid, entry["version_id"], rc))
                exited.append(iid)
                iso = entry.get("iso")
                if iso:
                    try:
                        iso.close()
                    except Exception:
                        pass
                self.game_stopped.emit(entry["version_id"])
                exited_pids.append(entry["pid"])
        for iid in exited:
            del self._game_processes[iid]
        for pid in exited_pids:
            try:
                self.memory_access.detach(pid)
            except Exception:
                pass
        if not self._game_processes:
            self._set_game_running(False)
        else:
            self._update_running_instances_sidebar()

    def closeEvent(self, event):
        if self._shutting_down:
            event.accept()
            return
        if self._game_processes:
            event.ignore()
            self._show_message("question", tr("Confirm"),
                tr("Minecraft is still running. Are you sure you want to exit?"),
                callback=lambda ok: self._on_exit_confirm(ok))
            return

        self._do_close()

    def _on_exit_confirm(self, ok):
        if ok:
            for iid, entry in list(self._game_processes.items()):
                proc = entry["process"]
                try:
                    proc.terminate()
                except Exception:
                    pass
                iso = entry.get("iso")
                if iso:
                    try:
                        iso.close()
                    except Exception:
                        pass
            self._game_processes.clear()
            self._set_game_running(False)
            self._do_close()

    def _do_close(self):
        self._shutting_down = True
        self.aria2_manager.stop()

        try:
            self.memory_access.detach_all()
        except Exception:
            pass

        if hasattr(self, 'function_widget'):
            self.function_widget.shutdown()

        for widget in [self.modpack_widget]:
            for attr in ['_active_threads', '_mp_threads', '_dl_threads']:
                threads = getattr(widget, attr, None)
                if threads is None:
                    continue
                for t in list(threads):
                    if t.isRunning():
                        t.quit()
                        t.wait(500)

        for t in self._threads:
            if t.isRunning():
                t.quit()
                t.wait(500)
        QApplication.processEvents()
        self.close()