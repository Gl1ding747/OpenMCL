import os
import sys
import re
import subprocess

from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QLabel, QCheckBox, QSpinBox, QLineEdit,
                             QFrame, QScrollArea, QStackedWidget,
                             QListWidget, QListWidgetItem, QFileDialog,
                             QMessageBox, QComboBox, QProgressBar)
from PyQt5.QtGui import QFont

from core.paths import resource_path
from core.macro_engine import (KeyDetector, click_left, click_right,
                                vk_to_name, is_minecraft_foreground)
from ui.vpn_widget import VpnSidebarWidget, LINE_STYLE, MODE_BTN_STYLE, ACCENT_STYLE
from i18n import tr
import ctypes

CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".openmcl", "config.json")
PLUGIN_DIR = os.path.join(resource_path("ui"), "plugins")


def _plugin_cmd_args(mode, path):
    """构造插件进程启动参数。打包运行时改为 exe+--run-plugin 让 exe 内部重入解释器。"""
    if getattr(sys, "frozen", False):
        return [mode, "--run-plugin", path]
    return [mode, path]


def _plugin_popen(cmd, cwd=None, env=None):
    """运行插件子进程，Windows 下隐藏弹出的 cmd 黑框。"""
    if sys.platform.startswith("win"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE
        return subprocess.Popen(cmd, cwd=cwd, env=env, startupinfo=startupinfo,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    return subprocess.Popen(cmd, cwd=cwd, env=env)


def _spawn_plugin(cmd, cwd=None, env=None):
    """以独立进程启动插件，Windows 下隐藏 cmd 黑框。"""
    kwargs = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(cmd, cwd=cwd, env=env, **kwargs)

class _EmbedPluginHost(QWidget):
    """插件嵌入宿主：关闭/复用前清空子控件，连带销毁插件创建的 QTimer。"""

    def _clear_children(self):
        lay = self.layout()
        if lay is None:
            return
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            elif item.layout() is not None:
                item.layout().deleteLater()

    def closeEvent(self, event):
        self._clear_children()
        self.deleteLater()
        event.accept()


SECTION_TITLE = """
    color: #505058; font-size: 10px; font-weight: bold; letter-spacing: 2px;
    padding: 6px 0 2px;
"""

PAGE_BTN_STYLE = """
QPushButton { background: transparent; color: #c8c8d0; border: 1px solid #3a3a48;
              border-radius: 0px; padding: 12px 14px; font-size: 14px; text-align: left; }
QPushButton:hover { background: #1c1c26; color: #f0f0f0; border-color: #5a5a70; }
QPushButton:pressed { background: #14141c; }
"""

BACK_BTN_STYLE = """
QPushButton { background: transparent; color: #808088; border: none;
              font-size: 13px; padding: 4px 0; text-align: left; }
QPushButton:hover { color: #d0d0d0; }
"""


class ServerDownloadWorker(QThread):
    """后台下载 Minecraft 服务端 jar。"""
    progress = pyqtSignal(str, int, int)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, downloader, vm, version_id, dest_dir, aria2_manager=None, parent=None):
        super().__init__(parent)
        self._downloader = downloader
        self._vm = vm
        self._version_id = version_id
        self._dest_dir = dest_dir
        self._aria2 = aria2_manager

    def run(self):
        try:
            data = self._vm.load_version_json(self._version_id)
            if data is None:
                data = self._vm.fetch_version_json(self._version_id)
            server_info = data.get("downloads", {}).get("server", {})
            if not server_info:
                self.failed.emit("No server download info for version")
                return
            url = server_info.get("url") or server_info.get("_original_url")
            sha1 = server_info.get("sha1")
            dest = os.path.join(self._dest_dir, f"server-{self._version_id}.jar")
            # 优先用 aria2 多线程下载；不可用时回退到 requests
            if self._aria2 is not None and not self._aria2.is_running():
                try:
                    self._aria2.start()
                except Exception:
                    pass
            self._downloader.download_file(
                url, dest, sha1=sha1, use_aria2=True,
                progress_callback=lambda n, d, t: self.progress.emit(n, d, t))
            self.finished_ok.emit(dest)
        except Exception as e:
            self.failed.emit(str(e))


class VersionListWorker(QThread):
    """后台获取可下载的服务端版本列表。"""
    loaded = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, vm, parent=None):
        super().__init__(parent)
        self._vm = vm

    def run(self):
        try:
            versions = self._vm.get_version_list()
            releases = []
            for v in versions:
                vid = v.get("id", "")
                vtype = v.get("type", "release")
                if vtype in ("release", "snapshot"):
                    releases.append((vid, vtype))
            releases.sort(key=lambda x: x[0], reverse=True)
            self.loaded.emit(releases)
        except Exception as e:
            self.failed.emit(str(e))


class FunctionWidget(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")

        self._macro_enabled_l = False
        self._macro_interval_l = 100
        self._macro_vk_l = 0x75
        self._macro_down_l = False

        self._macro_enabled_r = False
        self._macro_interval_r = 100
        self._macro_vk_r = 0x76
        self._macro_down_r = False

        self._key_detector = None
        self._recording_target = None

        self._load_config()

        self._hotkey_timer = QTimer(self)
        self._hotkey_timer.setInterval(50)
        self._hotkey_timer.timeout.connect(self._poll_hotkeys)
        self._hotkey_timer.start()

        self._click_l_timer = QTimer(self)
        self._click_l_timer.timeout.connect(self._do_click_l)
        self._click_l_timer.setInterval(self._macro_interval_l)
        self._click_l_timer.start()

        self._click_r_timer = QTimer(self)
        self._click_r_timer.timeout.connect(self._do_click_r)
        self._click_r_timer.setInterval(self._macro_interval_r)
        self._click_r_timer.start()

        self._build_ui()
        self._apply_macro_l_ui()
        self._apply_macro_r_ui()

    # ------------------------------------------------------------------
    #  config
    # ------------------------------------------------------------------
    def _load_config(self):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                import json
                self._config = json.load(f)
        except Exception:
            self._config = {}
        self._macro_enabled_l = self._config.get("macro_l_enabled", False)
        self._macro_interval_l = self._config.get("macro_l_interval", 100)
        self._macro_vk_l = self._config.get("macro_l_hotkey", 0x75)
        self._macro_enabled_r = self._config.get("macro_r_enabled", False)
        self._macro_interval_r = self._config.get("macro_r_interval", 100)
        self._macro_vk_r = self._config.get("macro_r_hotkey", 0x76)

    def _save_config(self):
        try:
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                import json
                json.dump(self._config, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ------------------------------------------------------------------
    #  UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._stack = QStackedWidget(self)
        layout.addWidget(self._stack, 1)

        # --- page 0 : entry list ---
        home = QWidget()
        hv = QVBoxLayout(home)
        hv.setContentsMargins(0, 4, 0, 0)
        hv.setSpacing(10)

        title = QLabel(tr("Tools"))
        title.setStyleSheet("color: #e0e0e0; font-size: 16px; font-weight: 400; padding: 2px 0 8px;")
        hv.addWidget(title)

        entry_macro = QPushButton("\u25cf  " + tr("Macro"))
        entry_macro.setStyleSheet(PAGE_BTN_STYLE)
        entry_macro.setCursor(Qt.PointingHandCursor)
        entry_macro.clicked.connect(lambda: self._show_page(1))
        hv.addWidget(entry_macro)

        entry_vpn = QPushButton("\u25cf  " + tr("Multiplayer Helper"))
        entry_vpn.setStyleSheet(PAGE_BTN_STYLE)
        entry_vpn.setCursor(Qt.PointingHandCursor)
        entry_vpn.clicked.connect(lambda: self._show_page(2))
        hv.addWidget(entry_vpn)

        entry_server = QPushButton("\u25cf  " + tr("Server"))
        entry_server.setStyleSheet(PAGE_BTN_STYLE)
        entry_server.setCursor(Qt.PointingHandCursor)
        entry_server.clicked.connect(lambda: self._show_page(4))
        hv.addWidget(entry_server)

        entry_plugin = QPushButton("\u25cf  " + tr("Plugins"))
        entry_plugin.setStyleSheet(PAGE_BTN_STYLE)
        entry_plugin.setCursor(Qt.PointingHandCursor)
        entry_plugin.clicked.connect(lambda: self._show_page(3))
        hv.addWidget(entry_plugin)

        # 宿主注入区：插件通过 home_entry.json 声明，向主页面追加一级按钮
        self._home_plugin_box = QWidget()
        self._home_plugin_lay = QVBoxLayout(self._home_plugin_box)
        self._home_plugin_lay.setContentsMargins(0, 0, 0, 0)
        self._home_plugin_lay.setSpacing(10)
        hv.addWidget(self._home_plugin_box)

        hv.addStretch()
        self._stack.addWidget(home)

        # 构建完毕后启动 IPC 轮询，动态接收插件注入的按钮
        self._start_ipc_polling()

        # --- page 1 : macro ---
        self._build_macro_page()

        # --- page 2 : multiplayer helper (client) ---
        self._build_vpn_page()

        # --- page 3 : plugins ---
        self._build_plugin_page()

        # --- page 4 : server ---
        self._build_server_page()

        self._stack.setCurrentIndex(0)

    def _page_header(self, text):
        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)

        back = QPushButton("\u2190 " + tr("Back"))
        back.setStyleSheet(BACK_BTN_STYLE)
        back.setCursor(Qt.PointingHandCursor)
        back.setFixedHeight(24)
        bl.addWidget(back)

        lbl = QLabel(text)
        lbl.setStyleSheet("color: #e0e0e0; font-size: 14px; padding: 2px 0;")
        bl.addWidget(lbl)
        bl.addStretch()
        return bar, back

    def _build_macro_page(self):
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(12)

        bar, back = self._page_header("Macro")
        back.clicked.connect(lambda: self._show_page(0))
        pl.addWidget(bar)

        pl.addWidget(self._section_label("Clicker"))

        pl.addLayout(self._macro_row(
            "l", "\u25cf", "Auto Click (L):",
            self._macro_enabled_l, self._macro_vk_l, self._macro_interval_l,
            self._on_enable_l, self._on_interval_l, self._on_record_l
        ))

        pl.addLayout(self._macro_row(
            "r", "\u25cf", "Auto Click (R):",
            self._macro_enabled_r, self._macro_vk_r, self._macro_interval_r,
            self._on_enable_r, self._on_interval_r, self._on_record_r
        ))

        pl.addStretch()
        self._stack.addWidget(page)

    def _build_vpn_page(self):
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(12)

        bar, back = self._page_header(tr("Multiplayer Helper"))
        back.clicked.connect(lambda: self._show_page(0))
        pl.addWidget(bar)

        self._vpn_page_container = QWidget()
        pv = QVBoxLayout(self._vpn_page_container)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(8)

        self._vpn_widget = VpnSidebarWidget(initial_mode="p2p",
                                            show_mode_buttons=True,
                                            show_vpn=True, show_mc=False)
        pv.addWidget(self._vpn_widget)
        pv.addStretch()
        pl.addWidget(self._vpn_page_container, 1)
        self._stack.addWidget(page)

    def _build_server_page(self):
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(12)

        bar, back = self._page_header(tr("Server"))
        back.clicked.connect(lambda: self._show_page(0))
        pl.addWidget(bar)

        # 子导航：管理 / 下载
        nav = QHBoxLayout()
        nav.setSpacing(2)
        nav.addStretch()
        self._server_manage_btn = QPushButton(tr("Manage"))
        self._server_manage_btn.setCheckable(True)
        self._server_manage_btn.setStyleSheet(MODE_BTN_STYLE)
        self._server_manage_btn.setCursor(Qt.PointingHandCursor)
        self._server_manage_btn.setChecked(True)
        self._server_manage_btn.clicked.connect(lambda: self._server_stack_local.setCurrentIndex(0))
        nav.addWidget(self._server_manage_btn)

        self._server_download_btn = QPushButton(tr("Download"))
        self._server_download_btn.setCheckable(True)
        self._server_download_btn.setStyleSheet(MODE_BTN_STYLE)
        self._server_download_btn.setCursor(Qt.PointingHandCursor)
        self._server_download_btn.clicked.connect(self._show_server_download)
        nav.addWidget(self._server_download_btn)
        nav.addStretch()
        pl.addLayout(nav)

        self._server_stack_local = QStackedWidget()

        # 页0：MC 服务器管理（启动/停止）
        manage_page = QWidget()
        mv = QVBoxLayout(manage_page)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(8)
        self._server_widget = VpnSidebarWidget(initial_mode="server",
                                               show_mode_buttons=False,
                                               show_vpn=False, show_mc=True)
        mv.addWidget(self._server_widget)
        mv.addStretch()
        self._server_stack_local.addWidget(manage_page)

        # 页1：服务端下载页
        self._server_stack_local.addWidget(self._build_server_download_page())

        pl.addWidget(self._server_stack_local, 1)
        self._stack.addWidget(page)

    def _build_server_download_page(self):
        page = QWidget()
        vl = QVBoxLayout(page)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(10)

        lbl = QLabel(tr("Download Minecraft Server"))
        lbl.setStyleSheet("color: #c0c0c8; font-size: 13px;")
        vl.addWidget(lbl)

        row = QHBoxLayout()
        row.setSpacing(4)
        self._server_dl_combo = QComboBox()
        self._server_dl_combo.setStyleSheet(MODE_BTN_STYLE)
        self._server_dl_combo.setMinimumWidth(0)
        row.addWidget(self._server_dl_combo, 1)
        refresh_btn = QPushButton(tr("Refresh"))
        refresh_btn.setStyleSheet(MODE_BTN_STYLE)
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.clicked.connect(self._load_server_versions)
        row.addWidget(refresh_btn)
        vl.addLayout(row)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(4)
        self._server_dl_dir = QLineEdit()
        self._server_dl_dir.setStyleSheet(LINE_STYLE)
        self._server_dl_dir.setPlaceholderText(tr("Save directory"))
        dir_row.addWidget(self._server_dl_dir, 1)
        browse_btn = QPushButton("...")
        browse_btn.setFixedSize(28, 28)
        browse_btn.setStyleSheet(MODE_BTN_STYLE)
        browse_btn.setCursor(Qt.PointingHandCursor)
        browse_btn.clicked.connect(self._choose_server_dl_dir)
        dir_row.addWidget(browse_btn)
        vl.addLayout(dir_row)

        self._server_dl_progress = QProgressBar()
        self._server_dl_progress.setRange(0, 100)
        self._server_dl_progress.setValue(0)
        self._server_dl_progress.setStyleSheet("""
            QProgressBar { background: #1a1a22; border: 1px solid #2a2a30; height: 8px;
                           border-radius: 4px; text-align: center; }
            QProgressBar::chunk { background: #4a8a5a; }""")
        vl.addWidget(self._server_dl_progress)

        self._server_dl_status = QLabel("")
        self._server_dl_status.setStyleSheet("color: #808088; font-size: 11px;")
        self._server_dl_status.setWordWrap(True)
        vl.addWidget(self._server_dl_status)

        self._server_dl_btn = QPushButton(tr("Download"))
        self._server_dl_btn.setStyleSheet(ACCENT_STYLE)
        self._server_dl_btn.setCursor(Qt.PointingHandCursor)
        self._server_dl_btn.clicked.connect(self._start_server_download)
        vl.addWidget(self._server_dl_btn)

        vl.addStretch()
        return page

    def _show_server_download(self):
        self._server_stack_local.setCurrentIndex(1)
        self._server_manage_btn.setChecked(False)
        self._server_download_btn.setChecked(True)
        self._load_server_versions()

    def _choose_server_dl_dir(self):
        d = QFileDialog.getExistingDirectory(self, tr("Save directory"))
        if d:
            self._server_dl_dir.setText(d)

    def _load_server_versions(self):
        vm, dl = self._get_vm_downloader()
        if vm is None:
            self._server_dl_status.setText("未找到版本：无法获取版本管理器。")
            return
        self._server_dl_status.setText("正在加载版本列表...")
        self._server_dl_combo.clear()
        self._version_worker = VersionListWorker(vm, self)
        self._version_worker.loaded.connect(self._on_versions_loaded)
        self._version_worker.failed.connect(self._on_server_versions_error)
        self._version_worker.start()

    def _on_server_versions_error(self, e):
        self._server_dl_status.setText(
            "未找到版本：加载失败（{}）。请检查网络后点击“刷新”重试。".format(e))

    def _on_versions_loaded(self, versions):
        self._server_dl_combo.clear()
        for vid, vtype in versions:
            self._server_dl_combo.addItem(f"{vid}  ({vtype})", vid)
        if not versions:
            self._server_dl_status.setText("未找到版本：版本列表为空。")
            return
        self._server_dl_status.setText("加载到 {} 个版本。".format(len(versions)))

    def _start_server_download(self):
        version_id = self._server_dl_combo.currentData()
        dest_dir = self._server_dl_dir.text().strip()
        if not version_id:
            self._server_dl_status.setText("未找到版本：请先选择一个版本。")
            return
        if not dest_dir:
            dest_dir = os.path.join(os.path.expanduser("~"), ".openmcl", "servers")
        os.makedirs(dest_dir, exist_ok=True)
        self._server_dl_btn.setEnabled(False)
        self._server_dl_progress.setValue(0)
        self._server_dl_status.setText(tr("Downloading {} via aria2 ...").format(version_id))
        vm, dl = self._get_vm_downloader()
        amgr = getattr(self, "_aria2", None) or getattr(self.parent(), "aria2_manager", None)

        self._server_dl_worker = ServerDownloadWorker(
            dl, vm, version_id, dest_dir, aria2_manager=amgr, parent=self)
        self._server_dl_worker.progress.connect(self._on_server_dl_progress)
        self._server_dl_worker.finished_ok.connect(self._on_server_dl_done)
        self._server_dl_worker.failed.connect(self._on_server_dl_error)
        self._server_dl_worker.start()

    def _on_server_dl_progress(self, name, done, total):
        if total > 0:
            self._server_dl_progress.setValue(int(done * 100 / total))
        self._server_dl_status.setText(f"{name}: {done // 1048576}MB / {total // 1048576}MB")

    def _on_server_dl_done(self, path):
        self._server_dl_btn.setEnabled(True)
        self._server_dl_progress.setValue(100)
        self._server_dl_status.setText(tr("Downloaded: {}").format(path))

    def _on_server_dl_error(self, err):
        self._server_dl_btn.setEnabled(True)
        self._server_dl_status.setText(tr("Download failed: {}").format(err))

    def _get_vm_downloader(self):
        win = self.parent()
        vm = getattr(self, "_vm", None)
        dl = getattr(self, "_downloader", None)
        amgr = getattr(self, "_aria2", None)
        if vm is None:
            vm = getattr(win, "vm", None)
        if dl is None:
            dl = getattr(win, "downloader", None)
        if amgr is None:
            amgr = getattr(win, "aria2_manager", None)
        if vm is None:
            from core.version_manager import VersionManager
            vm = VersionManager()
            self._vm = vm
        if dl is None:
            from core.downloader import Downloader
            if amgr is None:
                amgr = self._ensure_aria2()
            dl = Downloader(vm, aria2_manager=amgr)
            self._downloader = dl
        if amgr is not None:
            self._aria2 = amgr
            self._ensure_aria2(amgr)
        return vm, dl

    def _ensure_aria2(self, amgr=None):
        """确保 aria2 已运行用于多线程下载；返回管理器实例。"""
        if amgr is None:
            amgr = getattr(self, "_aria2", None)
        if amgr is None:
            from core.aria2_manager import Aria2Manager
            amgr = Aria2Manager()
            self._aria2 = amgr
        if not amgr.is_running():
            if getattr(self, "_aria2_starting", False):
                return amgr
            self._aria2_starting = True
            import threading as _th

            def _late_start():
                try:
                    if not amgr.is_running():
                        amgr.start()
                except Exception as e:
                    print("aria2 start failed:", e)
                finally:
                    self._aria2_starting = False

            _th.Thread(target=_late_start, daemon=True).start()
        return amgr

    def _build_plugin_page(self):
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(10)

        bar, back = self._page_header(tr("Plugins"))
        back.clicked.connect(lambda: self._show_page(0))
        pl.addWidget(bar)

        hint = QLabel(tr("Put plugins in: {}").format(PLUGIN_DIR))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #606068; font-size: 11px; padding: 2px 0;")
        pl.addWidget(hint)

        self._plugin_list = QListWidget()
        self._plugin_list.setStyleSheet("""QListWidget { background: #1a1a22; color: #c0c0c8;
            border: 1px solid #2a2a30; font-size: 12px; }
            QListWidget::item { padding: 8px 8px; border-bottom: 1px solid #222230; }
            QListWidget::item:selected { background: #2a2a40; color: #e0e0e0; }""")
        self._plugin_list.itemClicked.connect(self._on_plugin_clicked)
        pl.addWidget(self._plugin_list, 1)

        footer = QHBoxLayout()
        open_btn = QPushButton(tr("Open Plugin Folder"))
        open_btn.setStyleSheet("""
            QPushButton { background: #22222c; color: #a0a0a8; border: 1px solid #33333f;
                          padding: 6px 10px; font-size: 12px; }
            QPushButton:hover { color: #e0e0e0; }""")
        open_btn.clicked.connect(self._open_plugin_folder)
        footer.addWidget(open_btn)

        run_btn = QPushButton(tr("Run Plugin"))
        run_btn.setStyleSheet("""
            QPushButton { background: #2a3a2a; color: #90d090; border: 1px solid #3a4a3a;
                          padding: 6px 10px; font-size: 12px; }
            QPushButton:hover { color: #c0f0c0; }""")
        run_btn.clicked.connect(self._run_selected_plugin)
        footer.addWidget(run_btn)
        pl.addLayout(footer)

        self._refresh_plugins()
        self._stack.addWidget(page)

    def _show_page(self, idx):
        self._stack.setCurrentIndex(idx)
        if idx == 3:
            self._refresh_plugins()
        if idx == 0:
            self._poll_ipc_injects()

    # ------------------------------------------------------------------
    #  主页面按钮运行时注入（runtime home entry injection）
    #  插件运行后调用 plugin_api.inject_home_button(...)，向 IPC 目录写请求，
    #  宿主用 QTimer 轮询到请求后，动态把按钮追加到主页面的注入区。
    #  点击按钮即重新以独立进程启动对应插件入口。
    # ------------------------------------------------------------------
    @staticmethod
    def _ipc_dir():
        d = os.path.join(os.path.expanduser("~"), ".openmcl", "plugins_ipc")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return d

    def _start_ipc_polling(self):
        self._ipc_timer = QTimer(self)
        self._ipc_timer.setInterval(800)
        self._ipc_timer.timeout.connect(self._poll_ipc_injects)
        self._ipc_timer.start()

    def _poll_ipc_injects(self):
        """轮询 IPC 目录，把来自插件的注入请求应用到主页面。"""
        try:
            import json as _json
        except Exception:
            return
        d = self._ipc_dir()
        try:
            names = [n for n in os.listdir(d)
                     if n.startswith("inject_") and n.lower().endswith(".json")]
        except Exception:
            return
        for name in names:
            path = os.path.join(d, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = _json.load(f)
                if payload.get("op") == "add_home_button":
                    self._apply_home_inject(
                        payload.get("title", "Plugin"),
                        payload.get("entry"),
                        payload.get("message", ""))
            except Exception:
                pass
            finally:
                try:
                    os.remove(path)
                except Exception:
                    pass

    def _apply_home_inject(self, title, entry, message=""):
        """在主页面的注入区追加一个按钮；按 (title, entry) 去重，避免重复注入。"""
        lay = self._home_plugin_lay
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if w is not None and w.property("_home_key") == (title, entry):
                return
        btn = QPushButton("\u25cf  " + title)
        btn.setStyleSheet(PAGE_BTN_STYLE)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setProperty("_home_key", (title, entry))
        if message:
            btn.setToolTip(message)
        btn.clicked.connect(
            lambda checked=False, e=entry: self._start_plugin_entry(e))
        lay.addWidget(btn)

    # ------------------------------------------------------------------
    #  plugin support
    # ------------------------------------------------------------------
    @staticmethod
    def _list_plugin_files():
        """扫描插件目录。

        支持两种布局：
          1. 文件夹插件：plugins/<插件名>_<版本>/ 内含 plugin.py（或 main.py / __main__.py）
             该文件夹为入口所在目录，可放置附属资源，作为独立插件单位；
          2. 单文件插件（兼容旧格式）：plugins/<插件名>_<版本>.py

        返回 [(显示名, 版本, 入口相对路径)]。
        """
        if not os.path.isdir(PLUGIN_DIR):
            return []
        # plugin_api.py 是 API 模块，不作为可运行插件展示
        excluded = {"plugin_api.py"}
        result = []
        try:
            for name in sorted(os.listdir(PLUGIN_DIR)):
                # EXEC_ 前缀插件由内置的自动询问机制管理，不混入普通插件列表
                if name.startswith("EXEC_"):
                    continue
                if name in excluded:
                    continue
                full = os.path.join(PLUGIN_DIR, name)
                if os.path.isfile(full) and name.lower().endswith(".py"):
                    # 单文件插件：直接以文件名为入口
                    pname, ver = FunctionWidget._parse_plugin_filename(name)
                    result.append((pname, ver, name))
                elif os.path.isdir(full) and not name.startswith("."):
                    # 文件夹插件：找第一个入口文件
                    for entry in ("plugin.py", "main.py", "__main__.py"):
                        ep = os.path.join(full, entry)
                        if os.path.isfile(ep):
                            pname, ver = FunctionWidget._parse_plugin_filename(name)
                            rel = os.path.join(name, entry)
                            result.append((pname, ver, rel))
                            break
        except Exception:
            pass
        return result

    @staticmethod
    def _parse_plugin_filename(name):
        """把 '插件名_版本号'（文件夹或文件名）解析成 (插件名, 版本号)。"""
        stem = name
        if stem.endswith(".py"):
            stem = stem[:-3]
        # 兼容 plain.metadata.json 之类的多点外形，仅去掉末段扩展名
        m = re.match(r"^(.*?)[_-]v?([0-9][0-9A-Za-z._-]*)$", stem)
        if m:
            return m.group(1), m.group(2)
        return stem, ""

    def _refresh_plugins(self):
        self._plugin_list.clear()
        for pname, ver, entry in self._list_plugin_files():
            if ver:
                item = QListWidgetItem(f"{pname}  v{ver}")
            else:
                item = QListWidgetItem(pname)
            item.setData(Qt.UserRole, entry)
            self._plugin_list.addItem(item)

    def _on_plugin_clicked(self, item):
        pass

    def _selected_plugin_path(self):
        item = self._plugin_list.currentItem()
        if item is None:
            return None
        return os.path.join(PLUGIN_DIR, item.data(Qt.UserRole))

    def _open_plugin_folder(self):
        try:
            os.makedirs(PLUGIN_DIR, exist_ok=True)
        except Exception:
            pass
        path = PLUGIN_DIR
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore
        else:
            subprocess.Popen(["xdg-open", path])

    def _run_plugin_window(self, path, entry_dir):
        """在主进程内运行插件：插件窗口作为独立的外部顶层窗口显示。

        不嵌入二级菜单(避免覆盖菜单/功能页消失)，也不走子进程 exec_(避免
        嵌套事件循环卡死)。主进程事件循环已在运行，插件 Window.resize. 只 show
        即可稳定显示，关闭时销毁控件连带停掉其 QTimer。
        """
        import runpy
        import importlib
        # 插件脚本顶层 `import plugin_api`，用同名顶层模块加载，确保 patch 到
        # 插件真正用到的那个对象，否则会出现两个模块对象导致 patch 失效、
        # 插件走原始 exec_ 嵌套阻塞主界面。
        if PLUGIN_DIR not in sys.path:
            sys.path.insert(0, PLUGIN_DIR)
        plugin_api = importlib.import_module("plugin_api")

        _Win = plugin_api.Window
        def _ext_run(self_):
            w = self_._window
            w.show()
            w.raise_()
            w.activateWindow()
            w.setWindowModality(Qt.NonModal)

            def _on_close(ev):
                w.deleteLater()
                ev.accept()
            w.closeEvent = _on_close
            return 0

        def _ext_quit(self_):
            self_._window.close()
        _Win.run = _ext_run
        _Win.quit = _ext_quit

        old_dir = os.getcwd()
        try:
            os.chdir(entry_dir)
            if entry_dir not in sys.path:
                sys.path.insert(0, entry_dir)
            runpy.run_path(path, run_name="__main__")
        except Exception as et:
            # 插件运行失败：记录日志便于排查，不影响主界面
            import traceback
            import io as _io
            try:
                log = os.path.join(os.path.expanduser("~"), ".openmcl", "crash.log")
                os.makedirs(os.path.dirname(log), exist_ok=True)
                with _io.open(log, "a", encoding="utf-8") as f:
                    f.write("\n========== CRASH (plugin) ==========\n")
                    traceback.print_exception(type(et), et, et.__traceback__, file=f)
            except Exception:
                traceback.print_exc()
        finally:
            os.chdir(old_dir)

    def _run_selected_plugin(self):
        item = self._plugin_list.currentItem()
        if item is None:
            QMessageBox.information(self, tr("Plugins"),
                tr("No plugin selected."))
            return
        entry = item.data(Qt.UserRole)
        path = os.path.join(PLUGIN_DIR, entry)
        if not os.path.isfile(path):
            return
        try:
            mode = sys.executable
        except Exception:
            mode = "python"
        # 工作目录设为插件所在目录，并把入口目录与插件根目录都加入 sys.path，
        # 这样插件既能 import 同目录插件(plugin_api 等)也能用自身文件。
        plugin_root = os.path.dirname(entry)
        entry_dir = os.path.join(PLUGIN_DIR, plugin_root) if plugin_root else PLUGIN_DIR
        env = dict(os.environ)
        sep = os.pathsep
        pyr = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = sep.join(x for x in (PLUGIN_DIR, entry_dir, pyr) if x)
        self._run_plugin_window(path, entry_dir)

    # ------------------------------------------------------------------
    #  内置 EXEC_ 自动运行（每次打开 Launcher 后询问）
    # ------------------------------------------------------------------
    @staticmethod
    def _scan_exec_plugins():
        """扫描 plugins 目录下所有 EXEC_ 开头插件。

        返回 [(插件名, 相对入口路径, 是否文件夹)]。
        """
        results = []
        if not os.path.isdir(PLUGIN_DIR):
            return results
        for name in sorted(os.listdir(PLUGIN_DIR)):
            if not name.startswith("EXEC_"):
                continue
            full = os.path.join(PLUGIN_DIR, name)
            if os.path.isdir(full):
                for entry in ("plugin.py", "main.py", "__main__.py"):
                    if os.path.isfile(os.path.join(full, entry)):
                        results.append((name, os.path.join(name, entry), True))
                        break
            elif name.lower().endswith(".py"):
                results.append((name[:-3], name, False))
        return results

    def _start_plugin_entry(self, entry):
        """以独立进程启动一个插件入口。"""
        path = os.path.join(PLUGIN_DIR, entry)
        if not os.path.isfile(path):
            return
        try:
            mode = sys.executable
        except Exception:
            mode = "python"
        plugin_root = os.path.dirname(entry)
        entry_dir = os.path.join(PLUGIN_DIR, plugin_root) if plugin_root else PLUGIN_DIR
        env = dict(os.environ)
        sep = os.pathsep
        pyr = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = sep.join(x for x in (PLUGIN_DIR, entry_dir, pyr) if x)
        self._run_plugin_window(path, entry_dir)

    def _prompt_exec_plugins(self):
        """弹出对话框询问是否运行每个 EXEC_ 插件，勾选后一键启动。"""
        try:
            found = self._scan_exec_plugins()
        except Exception:
            return
        if not found:
            return

        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                     QLabel, QCheckBox, QPushButton)
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Run EXEC_ Plugins"))
        dlg.resize(420, 60 + len(found) * 34)
        v = QVBoxLayout(dlg)
        lbl = QLabel(tr("Found {} EXEC_ plugins. Select which to run:")
                     .format(len(found)))
        v.addWidget(lbl)
        checks = []
        for name, entry, is_folder in found:
            cb = QCheckBox(f"{name}  ({'folder' if is_folder else 'file'})")
            cb.setChecked(True)
            v.addWidget(cb)
            checks.append((cb, entry))
        h = QHBoxLayout()
        top = QPushButton(tr("Select All"))
        none = QPushButton(tr("Select None"))
        h.addWidget(top)
        h.addWidget(none)
        h.addStretch()
        v.addLayout(h)
        run_btn = QPushButton(tr("Run Selected"))
        v.addWidget(run_btn)

        def select_all():
            for cb, _ in checks:
                cb.setChecked(True)

        def select_none():
            for cb, _ in checks:
                cb.setChecked(False)

        def do_run():
            started = 0
            for cb, entry in checks:
                if cb.isChecked():
                    self._start_plugin_entry(entry)
                    started += 1
            dlg.accept()
            if started:
                QMessageBox.information(self, tr("Run EXEC_ Plugins"),
                    tr("Started {} plugin(s).").format(started))

        top.clicked.connect(select_all)
        none.clicked.connect(select_none)
        run_btn.clicked.connect(do_run)
        dlg.exec_()

    def showEvent(self, event):
        """首次显示后，异步询问是否运行 EXEC_ 插件（每次启动）。"""
        super().showEvent(event)
        if not getattr(self, "_exec_prompt_done", False):
            self._exec_prompt_done = True
            QTimer.singleShot(600, self._prompt_exec_plugins)

    def _macro_row(self, tag, dot_char, label_text,
                   enabled, vk, interval,
                   on_enable, on_interval, on_record):
        row = QVBoxLayout()
        row.setSpacing(6)

        # --- line 1: dot + checkbox + status ---
        line1 = QHBoxLayout()
        line1.setSpacing(10)

        dot = QLabel(dot_char)
        dot.setFixedWidth(20)
        setattr(self, f"_macro_dot_{tag}", dot)
        line1.addWidget(dot)

        line1.addWidget(QLabel(tr(label_text)))

        cb = QCheckBox()
        cb.setChecked(enabled)
        cb.stateChanged.connect(on_enable)
        setattr(self, f"_macro_enable_cb_{tag}", cb)
        line1.addWidget(cb)

        status = QLabel()
        status.setStyleSheet("color: #606068; font-size: 10px;")
        setattr(self, f"_macro_status_{tag}", status)
        line1.addWidget(status)

        line1.addStretch()
        row.addLayout(line1)

        # --- line 2: interval + hotkey ---
        line2 = QHBoxLayout()
        line2.setSpacing(10)

        line2.addWidget(QLabel(tr("Interval:")))
        spin = QSpinBox()
        spin.setRange(10, 2000)
        spin.setValue(interval)
        spin.setSingleStep(10)
        spin.valueChanged.connect(on_interval)
        setattr(self, f"_macro_interval_spin_{tag}", spin)
        line2.addWidget(spin, 1)

        line2.addWidget(QLabel(tr("Hotkey:")))
        display = QLineEdit(vk_to_name(vk))
        display.setReadOnly(True)
        display.setStyleSheet(
            "border: 1px solid #2a2a35; border-radius: 4px; background: #14141a;"
            "color: #d0d0d8; padding: 2px 6px; font-size: 10px;")
        setattr(self, f"_macro_hotkey_display_{tag}", display)
        line2.addWidget(display, 1)

        rec = QPushButton(tr("Record"))
        rec.setFixedHeight(28)
        rec.clicked.connect(on_record)
        setattr(self, f"_macro_record_btn_{tag}", rec)
        line2.addWidget(rec)

        line2.addStretch()
        row.addLayout(line2)

        return row

    def _section_label(self, text):
        lbl = QLabel(tr(text))
        lbl.setStyleSheet(SECTION_TITLE)
        return lbl

    def _apply_macro_l_ui(self):
        self._apply_channel_ui("l", self._macro_enabled_l, self._macro_vk_l)

    def _apply_macro_r_ui(self):
        self._apply_channel_ui("r", self._macro_enabled_r, self._macro_vk_r)

    def _apply_channel_ui(self, tag, enabled, vk):
        name = vk_to_name(vk)
        dot = getattr(self, f"_macro_dot_{tag}")
        status = getattr(self, f"_macro_status_{tag}")
        if enabled:
            dot.setStyleSheet("color: #606068; font-size: 14px;")
            status.setText(tr("Hold {}").format(name))
            status.setStyleSheet("color: #606068; font-size: 10px;")
        else:
            dot.setStyleSheet("color: #505050; font-size: 14px;")
            status.setText(tr("Disabled"))
            status.setStyleSheet("color: #505050; font-size: 10px;")

    # ------------------------------------------------------------------
    #  hotkey polling  (shared 50 ms timer)
    # ------------------------------------------------------------------
    def _poll_hotkeys(self):
        dl = (ctypes.windll.user32.GetAsyncKeyState(self._macro_vk_l) & 0x8000) != 0
        dr = (ctypes.windll.user32.GetAsyncKeyState(self._macro_vk_r) & 0x8000) != 0
        self._macro_down_l = dl
        self._macro_down_r = dr

        mc = is_minecraft_foreground()

        self._update_channel_status("l", self._macro_enabled_l, self._macro_vk_l,
                                    self._macro_down_l, mc)
        self._update_channel_status("r", self._macro_enabled_r, self._macro_vk_r,
                                    self._macro_down_r, mc)

    def _update_channel_status(self, tag, enabled, vk, down, mc):
        dot = getattr(self, f"_macro_dot_{tag}")
        status = getattr(self, f"_macro_status_{tag}")
        name = vk_to_name(vk)
        if enabled and down and mc:
            dot.setStyleSheet("color: #50c878; font-size: 14px;")
            status.setText(tr("Clicking..."))
            status.setStyleSheet("color: #50c878; font-size: 10px;")
        elif enabled:
            dot.setStyleSheet("color: #606068; font-size: 14px;")
            status.setText(tr("Hold {}").format(name))
            status.setStyleSheet("color: #606068; font-size: 10px;")
        else:
            dot.setStyleSheet("color: #505050; font-size: 14px;")
            status.setText(tr("Disabled"))
            status.setStyleSheet("color: #505050; font-size: 10px;")

    # ------------------------------------------------------------------
    #  click timers
    # ------------------------------------------------------------------
    def _do_click_l(self):
        if self._macro_enabled_l and self._macro_down_l and is_minecraft_foreground():
            click_left()

    def _do_click_r(self):
        if self._macro_enabled_r and self._macro_down_r and is_minecraft_foreground():
            click_right()

    # ------------------------------------------------------------------
    #  handlers  left
    # ------------------------------------------------------------------
    def _on_enable_l(self, state):
        self._macro_enabled_l = bool(state)
        self._config["macro_l_enabled"] = self._macro_enabled_l
        self._save_config()
        self._apply_macro_l_ui()

    def _on_interval_l(self, value):
        self._macro_interval_l = value
        self._config["macro_l_interval"] = value
        self._save_config()
        self._click_l_timer.setInterval(value)

    def _on_record_l(self):
        self._recording_target = "l"
        self._macro_record_btn_l.setEnabled(False)
        self._macro_record_btn_l.setText(tr("..."))
        self._macro_hotkey_display_l.setText(tr("Press any key..."))
        self._start_recording()

    # ------------------------------------------------------------------
    #  handlers  right
    # ------------------------------------------------------------------
    def _on_enable_r(self, state):
        self._macro_enabled_r = bool(state)
        self._config["macro_r_enabled"] = self._macro_enabled_r
        self._save_config()
        self._apply_macro_r_ui()

    def _on_interval_r(self, value):
        self._macro_interval_r = value
        self._config["macro_r_interval"] = value
        self._save_config()
        self._click_r_timer.setInterval(value)

    def _on_record_r(self):
        self._recording_target = "r"
        self._macro_record_btn_r.setEnabled(False)
        self._macro_record_btn_r.setText(tr("..."))
        self._macro_hotkey_display_r.setText(tr("Press any key..."))
        self._start_recording()

    # ------------------------------------------------------------------
    #  key detector  (shared)
    # ------------------------------------------------------------------
    def _start_recording(self):
        self._key_detector = KeyDetector(self)
        self._key_detector.key_detected.connect(self._on_key_detected)
        self._key_detector.start()

    def _on_key_detected(self, vk, name):
        tag = self._recording_target
        self._recording_target = None

        if tag is None:
            self._key_detector = None
            return

        rec_btn = getattr(self, f"_macro_record_btn_{tag}")
        disp = getattr(self, f"_macro_hotkey_display_{tag}")
        rec_btn.setEnabled(True)
        rec_btn.setText(tr("Record"))
        disp.setText(name)

        setattr(self, f"_macro_vk_{tag}", vk)
        setattr(self, f"_macro_down_{tag}", False)
        self._config[f"macro_{tag}_hotkey"] = vk
        self._save_config()

        getattr(self, f"_apply_macro_{tag}_ui")()
        self._key_detector = None

    # ------------------------------------------------------------------
    #  shutdown
    # ------------------------------------------------------------------
    def shutdown(self):
        self._click_l_timer.stop()
        self._click_r_timer.stop()
        self._hotkey_timer.stop()
        if self._key_detector and self._key_detector.isRunning():
            self._key_detector.stop()
            self._key_detector.wait(500)
        if hasattr(self, '_vpn_widget'):
            self._vpn_widget._unload()
        if hasattr(self, '_server_widget'):
            self._server_widget._unload()
        # function_widget 内可能单独起过一个 aria2 实例，关闭时必须一并停掉，
        # 否则会残留 uma aria2c.exe（main_window 只停它自己那个 manager）
        amgr = getattr(self, "_aria2", None)
        if amgr is not None:
            try:
                amgr.stop()
            except Exception:
                pass