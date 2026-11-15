import os
import json
import re
import shutil
from datetime import datetime
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
                             QPushButton, QLabel, QComboBox, QListWidget,
                             QListWidgetItem, QProgressBar,
                             QGroupBox, QGridLayout, QApplication, QCheckBox,
                             QLineEdit, QScrollArea, QDialog, QSplitter, QFrame,
                             QSizePolicy, QSpacerItem, QFileDialog)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize
from PyQt5.QtGui import QFont

from core.modloader_manager import ModLoaderManager
from core.launcher import get_java_major_version, find_java, get_mc_required_java_for_version
from core.instance_importer import import_external
from i18n import tr


TILE_STYLE = """
QFrame#instanceTile {
    background-color: #1a1a1e;
    border: 1px solid #24242c;
}
QFrame#instanceTile:hover {
    border-color: #34343e;
}
"""

FLAT_BTN_STYLE = """
QPushButton {
    background: #1e1e22;
    color: #c0c0c0;
    border: 1px solid #2a2a30;
    padding: 6px 14px;
}
QPushButton:hover {
    background: #282830;
    border-color: #3a3a42;
    color: #e0e0e0;
}
QPushButton:pressed {
    background: #16161a;
}
"""

ACCENT_BTN_STYLE = """
QPushButton {
    background: #282830;
    color: #e8e8e8;
    border: 1px solid #383840;
    padding: 8px 18px;
    font-size: 12px;
    font-weight: bold;
    letter-spacing: 2px;
}
QPushButton:hover {
    background: #32323c;
    border-color: #4a4a54;
}
QPushButton:pressed {
    background: #1e1e24;
}
"""

BLUE_BTN_STYLE = """
QPushButton {
    background: #1e1e22;
    color: #c0c0c0;
    border: 1px solid #2a2a30;
    padding: 6px 14px;
}
QPushButton:hover {
    background: #282830;
    color: #e8e8e8;
}
"""

DANGER_BTN_STYLE = """
QPushButton {
    background: #1e1e22;
    color: #e06060;
    border: 1px solid #2a2a30;
    padding: 5px 12px;
}
QPushButton:hover {
    background: #2a2020;
    border-color: #4a3030;
}
"""

INPUT_STYLE = """
QLineEdit, QComboBox, QSpinBox {
    background: #1c1c20;
    color: #e0e0e0;
    border: 1px solid #2a2a30;
    padding: 5px 8px;
}
QComboBox::drop-down { border: none; width: 20px; }
QComboBox::down-arrow { image: none; border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 5px solid #808088; margin-right: 6px; }
QComboBox QAbstractItemView {
    background: #1c1c20;
    color: #e0e0e0;
    border: 1px solid #2a2a30;
    selection-background-color: #2a2a34;
    selection-color: #fff;
    outline: none;
}
"""

LIST_STYLE = """
QListWidget {
    background: #1a1a1e;
    color: #c0c0c0;
    border: 1px solid #202028;
}
QListWidget::item { padding: 3px 8px; }
QListWidget::item:selected { background: #282830; color: #fff; }
"""

GROUP_STYLE = """
QGroupBox {
    color: #909098;
    border: 1px solid #202028;
    margin-top: 12px;
    padding-top: 14px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}
"""

TILE_ACCENT = {
    "release": "#4CAF50",
    "snapshot": "#FF9800",
    "old_alpha": "#9C27B0",
    "old_beta": "#E91E63",
    "forge": "#E91E63",
    "neoforge": "#FF9800",
    "fabric": "#64B5F6",
    "quilt": "#9C27B0",
}


class ModLoaderFetchThread(QThread):
    finished = pyqtSignal(str, list, str)

    def __init__(self, modloader, loader_type, mcversion):
        super().__init__()
        self.modloader = modloader
        self.loader_type = loader_type
        self.mcversion = mcversion

    def run(self):
        try:
            results = []
            if self.loader_type == "forge":
                builds = self.modloader.get_forge_builds(self.mcversion)
                for b in builds:
                    results.append({
                        "label": "Forge {} (build {})".format(b["version"], b["build"]),
                        "data": ("forge", b),
                    })
            elif self.loader_type == "neoforge":
                builds = self.modloader.get_neoforge_versions(self.mcversion)
                for b in builds:
                    results.append({
                        "label": "NeoForge {}".format(b["version"]),
                        "data": ("neoforge", b),
                    })
            elif self.loader_type == "fabric":
                loaders = self.modloader.get_fabric_loader_versions()
                for l in loaders:
                    ver = l.get("version", "")
                    stable = "stable" if l.get("stable", False) else "dev"
                    results.append({
                        "label": "Fabric Loader {} ({})".format(ver, stable),
                        "data": ("fabric", l),
                    })
            elif self.loader_type == "quilt":
                loaders = self.modloader.get_quilt_loader_versions()
                for l in loaders:
                    ver = l.get("version", "")
                    results.append({
                        "label": "Quilt Loader {}".format(ver),
                        "data": ("quilt", l),
                    })
            elif self.loader_type == "optifine":
                from core.launcher import extract_base_mc_version
                base_mc = extract_base_mc_version(self.mcversion)
                builds = self.modloader.get_optifine_versions(base_mc)
                for b in builds:
                    results.append({
                        "label": "OptiFine {}".format(b["version"]),
                        "data": ("optifine", b),
                    })
            self.finished.emit(self.loader_type, results, "")
        except Exception as e:
            self.finished.emit(self.loader_type, [], str(e))


class ModLoaderInstallThread(QThread):
    progress = pyqtSignal(str, int, int)
    log = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)
    side_panel_task = pyqtSignal(str, str, str)
    side_panel_progress = pyqtSignal(str, str)
    side_panel_done = pyqtSignal(str)
    side_panel_task = pyqtSignal(str, str, str)
    side_panel_progress = pyqtSignal(str, str)
    side_panel_done = pyqtSignal(str)

    def __init__(self, modloader, java_path, mcversion, loader_type, data):
        super().__init__()
        self.modloader = modloader
        self.java_path = java_path
        self.mcversion = mcversion
        self.loader_type = loader_type
        self.data = data

    def run(self):
        try:
            task_id = "ml_" + self.loader_type + "_" + self.mcversion
            self.side_panel_task.emit(task_id, "Installing " + self.loader_type,
                                       "For Minecraft " + self.mcversion)
            self.side_panel_progress.emit(task_id, "Installing...")
            def _sp(task_id=task_id): self.side_panel_done.emit(task_id)
            self.log.emit("Starting {} installation...".format(self.loader_type))
            if self.loader_type == "forge":
                result = self.modloader.install_forge(
                    self.java_path, self.mcversion, self.data,
                    log_callback=lambda msg: self.log.emit(msg),
                    progress_callback=lambda n, d, t: self.progress.emit(n, d, t))
            elif self.loader_type == "neoforge":
                result = self.modloader.install_neoforge(
                    self.java_path, self.mcversion, self.data,
                    log_callback=lambda msg: self.log.emit(msg),
                    progress_callback=lambda n, d, t: self.progress.emit(n, d, t))
            elif self.loader_type == "fabric":
                result = self.modloader.install_fabric(
                    self.java_path, self.mcversion, self.data.get("version"),
                    log_callback=lambda msg: self.log.emit(msg),
                    progress_callback=lambda n, d, t: self.progress.emit(n, d, t))
            elif self.loader_type == "quilt":
                result = self.modloader.install_quilt(
                    self.java_path, self.mcversion, self.data.get("version"),
                    log_callback=lambda msg: self.log.emit(msg),
                    progress_callback=lambda n, d, t: self.progress.emit(n, d, t))
            elif self.loader_type == "optifine":
                result = self.modloader.install_optifine(
                    self.java_path, self.mcversion, self.data,
                    log_callback=lambda msg: self.log.emit(msg),
                    progress_callback=lambda n, d, t: self.progress.emit(n, d, t))
            else:
                self.finished_signal.emit(False, "Unknown loader: {}".format(self.loader_type))
                return
            self.side_panel_done.emit(task_id)
            self.finished_signal.emit(True, result)
        except Exception as e:
            self.side_panel_done.emit(task_id)
            self.finished_signal.emit(False, str(e))


class SaveSyncThread(QThread):
    progress = pyqtSignal(int, int)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, launcher, version_id, direction):
        super().__init__()
        self._launcher = launcher
        self._version_id = version_id
        self._direction = direction

    def run(self):
        try:
            ok = self._launcher.sync_shared_saves(
                self._version_id, self._direction,
                progress_callback=lambda c, t: self.progress.emit(c, t))
            if ok:
                self.finished_signal.emit(True, "Save sync completed.")
            else:
                self.finished_signal.emit(False, "Save sync skipped (not enabled).")
        except Exception as e:
            self.finished_signal.emit(False, "Save sync error: {}".format(e))


class ImportWorker(QThread):
    """后台导入外部实例/整合包。"""
    finished_import = pyqtSignal(dict)

    def __init__(self, source, minecraft_dir, target_id=None, parent=None):
        super().__init__(parent)
        self.source = source
        self.minecraft_dir = minecraft_dir
        self.target_id = target_id

    def run(self):
        try:
            result = import_external(self.source, self.minecraft_dir,
                                     target_id=self.target_id)
        except Exception as e:  # noqa: BLE001
            result = {"ok": False, "error": "导入出错: {}".format(e)}
        self.finished_import.emit(result)


class InstancesWidget(QWidget):
    launch_requested = pyqtSignal(str)
    console_log = pyqtSignal(str)
    refresh_needed = pyqtSignal()
    message_requested = pyqtSignal(str, str, str, object)
    detail_requested = pyqtSignal(str)
    detail_install_modloader = pyqtSignal(str, str, object, str)
    side_panel_task = pyqtSignal(str, str, str)
    side_panel_progress = pyqtSignal(str, str)
    side_panel_done = pyqtSignal(str)
    side_panel_task = pyqtSignal(str, str, str)
    side_panel_progress = pyqtSignal(str, str)
    side_panel_done = pyqtSignal(str)

    def __init__(self, vm, downloader, launcher=None, java_path="java"):
        super().__init__()
        self.vm = vm
        self.downloader = downloader
        self.modloader = ModLoaderManager(vm, downloader)
        self._launcher = launcher
        self._default_java_path = java_path or "java"
        self._java_list = []
        self._init_ui()
        self.refresh()

    def set_java_path(self, path):
        self._default_java_path = path or "java"

    def set_java_list(self, java_list):
        self._java_list = java_list

    def _on_import_clicked(self):
        """让用户选择目录或 zip 后后台导入。"""
        if getattr(self, "_import_worker", None) is not None and self._import_worker.isRunning():
            return
        folder = QFileDialog.getExistingDirectory(
            self, tr("Select a folder to import (a .minecraft or modpack folder)"))
        source = None
        if folder:
            source = folder
        else:
            zf, _ = QFileDialog.getOpenFileName(
                self, tr("Select a modpack archive (.zip)"),
                "", "Archive (*.zip);;All Files (*)")
            if zf:
                source = zf
        if not source:
            return
        self.message_requested.emit("info", tr("Importing..."),
                                    tr("Importing: {}").format(os.path.basename(source)), None)
        self._import_worker = ImportWorker(source, self.vm.minecraft_dir, parent=self)
        self._import_worker.finished_import.connect(self._on_import_done)
        self._import_worker.start()

    def _on_import_done(self, result):
        if result.get("ok"):
            self.refresh()
            self.refresh_needed.emit()
            msg = result.get("message") or tr("Import done")
            self.message_requested.emit("ok", tr("Import finished"), msg, None)
        else:
            self.message_requested.emit("error", tr("Import failed"),
                                        result.get("error") or tr("Unknown error"), None)
        self._import_worker = None

    def refresh(self):
        self._clear_tiles()
        installed = self.vm.get_installed_versions()
        if not installed:
            self._tiles_area.setVisible(False)
            self._tile_empty.setVisible(True)
            return
        self._tiles_area.setVisible(True)
        self._tile_empty.setVisible(False)

        sorted_ids = sorted(installed, reverse=True)
        cols = max(1, self.width() // 210)
        for idx, vid in enumerate(sorted_ids):
            tile = InstanceTile(vid, self.vm, self)
            tile.launch_requested.connect(self.launch_requested.emit)
            tile.console_log.connect(self.console_log.emit)
            tile.detail_requested.connect(self._show_detail)
            self._tile_grid.addWidget(tile, idx // cols, idx % cols)

    def _clear_tiles(self):
        while self._tile_grid.count():
            item = self._tile_grid.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_tiles()

    def _relayout_tiles(self):
        widgets = []
        while self._tile_grid.count():
            item = self._tile_grid.takeAt(0)
            w = item.widget()
            if w:
                widgets.append(w)
        cols = max(1, self.width() // 210)
        for i, w in enumerate(widgets):
            self._tile_grid.addWidget(w, i // cols, i % cols)

    def _show_detail(self, version_id):
        try:
            self.detail_requested.emit(version_id)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.message_requested.emit("critical", tr("Error"), str(e), None)

    def _on_instance_java_changed(self, version_id, java_path):
        pass

    def on_game_stopped(self, version_id):
        pass

    def install_modloader_for(self, version_id, ml_type, ml_info, loader_version=""):
        self.detail_install_modloader.emit(version_id, ml_type, ml_info, loader_version)
        self.refresh()

    def _get_instance_java(self, version_id):
        data = self.vm.load_version_json(version_id)
        oml = data.get("_openmcl", {}) if data else {}
        return oml.get("java_path", self._default_java_path)

    def _get_instance_jvm_args(self, version_id):
        data = self.vm.load_version_json(version_id)
        oml = data.get("_openmcl", {}) if data else {}
        text = oml.get("jvm_args", "")
        if not text:
            return None
        return [a.strip() for a in text.split() if a.strip()]

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        top_row = QHBoxLayout()
        self.title_label = QLabel(tr("Instances"))
        self.title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #e8e8e8;")
        top_row.addWidget(self.title_label)
        top_row.addStretch()
        import_btn = QPushButton(tr("Import"))
        import_btn.setStyleSheet(FLAT_BTN_STYLE)
        import_btn.clicked.connect(self._on_import_clicked)
        top_row.addWidget(import_btn)
        refresh_btn = QPushButton(tr("Refresh"))
        refresh_btn.setStyleSheet(FLAT_BTN_STYLE)
        refresh_btn.clicked.connect(self.refresh)
        top_row.addWidget(refresh_btn)
        layout.addLayout(top_row)

        self._tiles_area = QScrollArea()
        self._tiles_area.setWidgetResizable(True)
        self._tiles_area.setFrameShape(QFrame.NoFrame)
        self._tiles_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        tile_container = QWidget()
        tile_container.setStyleSheet("background: transparent;")
        self._tile_grid = QGridLayout(tile_container)
        self._tile_grid.setContentsMargins(4, 4, 4, 4)
        self._tile_grid.setSpacing(14)
        self._tile_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._tiles_area.setWidget(tile_container)
        layout.addWidget(self._tiles_area, 1)

        self._tile_empty = QLabel(tr("No installed versions.\nUse 'New Instance' to download Minecraft."))
        self._tile_empty.setAlignment(Qt.AlignCenter)
        self._tile_empty.setStyleSheet("color: #5f5f5f; font-size: 14px; padding: 40px;")
        self._tile_empty.setVisible(False)
        layout.addWidget(self._tile_empty)


class InstanceTile(QFrame):
    launch_requested = pyqtSignal(str)
    console_log = pyqtSignal(str)
    detail_requested = pyqtSignal(str)

    def __init__(self, version_id, vm, parent=None):
        super().__init__(parent)
        self.version_id = version_id
        self.vm = vm
        self.setObjectName("instanceTile")
        self.setFixedSize(200, 200)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(TILE_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        data = self.vm.load_version_json(self.version_id)
        oml = data.get("_openmcl", {}) if data else {}
        ml = oml.get("modloader", {})
        ml_type = ml.get("type", "") if ml else ""

        vtype = data.get("type", "release") if data else "release"
        accent = TILE_ACCENT.get(ml_type) or TILE_ACCENT.get(vtype, "#4CAF50")

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        self._type_badge = QLabel(ml_type.upper()[:8] if ml_type else vtype.upper()[:8])
        self._type_badge.setStyleSheet(
            "background-color: {}; color: white; font-size: 9px; "
            "font-weight: bold; padding: 2px 7px;".format(accent))
        self._type_badge.setFixedHeight(18)
        top_row.addWidget(self._type_badge)
        top_row.addStretch()

        settings_btn = QPushButton(":")
        settings_btn.setFixedSize(22, 22)
        settings_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #e0e0e0; border: none; font-size: 16px; font-weight: bold; }"
            "QPushButton:hover { color: #ffffff; }")
        settings_btn.setToolTip("Settings")
        settings_btn.clicked.connect(lambda: self.detail_requested.emit(self.version_id))
        top_row.addWidget(settings_btn)
        layout.addLayout(top_row)

        title_layout = QVBoxLayout()
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(0)

        short_name = self.version_id
        if len(short_name) > 20:
            short_name = short_name[:18] + ".."
        self._name_label = QLabel(short_name)
        self._name_label.setWordWrap(True)
        self._name_label.setStyleSheet(
            "color: #e8e8e8; font-size: 14px; font-weight: bold; background: transparent;")
        self._name_label.setToolTip(self.version_id)
        title_layout.addWidget(self._name_label)

        mc_ver = data.get("id", self.version_id) if data else self.version_id
        self._mc_label = QLabel(tr("MC {}").format(mc_ver))
        self._mc_label.setStyleSheet("color: #8a8a8a; font-size: 10px; background: transparent;")
        title_layout.addWidget(self._mc_label)
        layout.addLayout(title_layout)

        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 4, 0, 0)
        info_layout.setSpacing(2)

        self._loader_label = QLabel(self._get_loader_text(ml))
        self._loader_label.setStyleSheet("color: #8a8a8a; font-size: 10px; background: transparent;")
        info_layout.addWidget(self._loader_label)

        java_path = oml.get("java_path", "")
        if java_path and os.path.isfile(java_path):
            jv = get_java_major_version(java_path)
            java_label = "Java {}".format(jv) if jv > 0 else os.path.basename(java_path)
            self._java_label = QLabel(java_label)
        else:
            self._java_label = QLabel(tr("Java 8+"))
        self._java_label.setStyleSheet("color: #8a8a8a; font-size: 10px; background: transparent;")
        info_layout.addWidget(self._java_label)
        layout.addLayout(info_layout)

        layout.addStretch()

        launch_btn = QPushButton(tr("Launch"))
        launch_btn.setMinimumHeight(36)
        launch_btn.setStyleSheet(ACCENT_BTN_STYLE)
        launch_btn.clicked.connect(lambda: self.launch_requested.emit(self.version_id))
        layout.addWidget(launch_btn)

    def _get_loader_text(self, ml):
        if not ml:
            return "Vanilla"
        t = ml.get("type", "")
        v = ml.get("version", "")
        names = {"forge": "Forge", "neoforge": "NeoForge", "fabric": "Fabric", "quilt": "Quilt"}
        name = names.get(t, t)
        return "{} {}".format(name, v) if v else name

    def mouseDoubleClickEvent(self, event):
        self.launch_requested.emit(self.version_id)


class InstanceDetailDialog(QWidget):
    console_log = pyqtSignal(str)
    side_panel_task = pyqtSignal(str, str, str)
    side_panel_progress = pyqtSignal(str, str)
    side_panel_done = pyqtSignal(str)
    loader_installed = pyqtSignal()
    uninstalled = pyqtSignal(str)
    launch_requested = pyqtSignal(str)
    reinstall_requested = pyqtSignal(str)
    java_changed = pyqtSignal(str, str)
    message_requested = pyqtSignal(str, str, str, object)
    closed = pyqtSignal()

    LOADERS = [
        ("Forge", "forge"),
        ("NeoForge", "neoforge"),
        ("Fabric", "fabric"),
        ("Quilt", "quilt"),
    ]

    def __init__(self, version_id, vm, downloader, modloader,
                 java_path, java_list, launcher=None, parent=None,
                 initial_tab="mods"):
        super().__init__(parent)
        self.version_id = version_id
        self.vm = vm
        self.downloader = downloader
        self.modloader = modloader
        self._java_path = java_path or "java"
        self._java_list = java_list or []
        self._launcher = launcher
        self._sync_thread = None

        self.setWindowTitle(tr("{} - Settings").format(version_id))
        self.resize(700, 580)
        self.setMinimumSize(550, 450)

        self._init_ui(initial_tab)
        self._load()

    def _init_ui(self, initial_tab):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)

        back_btn = QPushButton("←")
        back_btn.setFixedSize(28, 28)
        back_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #808088; border: none; font-size: 16px; }"
            "QPushButton:hover { color: #e0e0e0; }")
        back_btn.setToolTip(tr("Back"))
        back_btn.clicked.connect(self.close)
        title_row.addWidget(back_btn)

        title_scroll = QScrollArea()
        title_scroll.setWidgetResizable(True)
        title_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        title_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        title_scroll.setFixedHeight(32)
        title_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:horizontal { height: 4px; background: transparent; }"
            "QScrollBar::handle:horizontal { background: #3a3a50; border-radius: 2px; }"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }")
        self._title_label = QLabel(self.version_id)
        self._title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #e8e8e8; padding: 0 4px;")
        title_scroll.setWidget(self._title_label)
        title_row.addWidget(title_scroll, 1)

        title_row.addStretch()

        launch_btn = QPushButton(tr("Launch"))
        launch_btn.setStyleSheet(ACCENT_BTN_STYLE)
        launch_btn.clicked.connect(lambda: (self.launch_requested.emit(self.version_id), self.close()))
        title_row.addWidget(launch_btn)

        reinstall_btn = QPushButton(tr("Reinstall"))
        reinstall_btn.setStyleSheet(BLUE_BTN_STYLE)
        reinstall_btn.clicked.connect(lambda: (self.reinstall_requested.emit(self.version_id), self.close()))
        title_row.addWidget(reinstall_btn)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #808088; border: none; font-size: 15px; }"
            "QPushButton:hover { background: #c42b1c; color: #f0f0f0; }")
        close_btn.setToolTip(tr("Close"))
        close_btn.clicked.connect(self.close)
        title_row.addWidget(close_btn)

        layout.addLayout(title_row)

        self._tab_widget = QTabWidget()
        self._tab_widget.setStyleSheet(
            "QTabWidget::pane { border-radius: 0px; border: none; "
            "border-top: 1px solid rgba(255,255,255,0.07); background: rgba(26,26,28,0.92); }"
            "QTabBar::tab { border-radius: 0px; background: transparent; color: #8a8a8a; padding: 8px 16px; "
            "border: none; margin-right: 0; border-bottom: 2px solid transparent; letter-spacing: 0.4px; }"
            "QTabBar::tab:selected { background: rgba(255,255,255,0.04); color: #fff; border-bottom: 2px solid #e6e6e6; }"
            "QTabBar::tab:hover { background: rgba(255,255,255,0.04); color: #dedede; }")

        self._java_tab = self._create_java_tab()
        self._mods_tab = self._create_mods_tab()
        self._loader_tab = self._create_loader_tab()
        self._jvm_tab = self._create_jvm_tab()
        self._saves_tab = self._create_saves_tab()

        self._tab_widget.addTab(self._java_tab, tr("Java"))
        self._tab_widget.addTab(self._mods_tab, tr("Mods"))
        self._tab_widget.addTab(self._loader_tab, tr("Mod Loader"))
        self._tab_widget.addTab(self._jvm_tab, tr("JVM Args"))
        self._tab_widget.addTab(self._saves_tab, tr("Saves"))
        layout.addWidget(self._tab_widget, 1)

        for combo in self.findChildren(QComboBox):
            v = combo.view()
            if v:
                v.setStyleSheet(
                    "QAbstractItemView { background: #2a2a2e; color: #e8e8e8; "
                    "border: 1px solid rgba(255,255,255,0.10); "
                    "selection-background-color: rgba(255,255,255,0.10); outline: none; }")

        tab_names = {"java": 0, "mods": 1, "modloader": 2, "jvm": 3, "saves": 4}
        self._tab_widget.setCurrentIndex(tab_names.get(initial_tab, 1))

    def _create_java_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        hint = QLabel(tr("Select a Java runtime for this instance.\nHigher versions of Minecraft require newer Java."))
        hint.setStyleSheet("color: #8a8a8a; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._java_combo = QComboBox()
        self._java_combo.setMinimumHeight(28)
        self._java_combo.setStyleSheet(INPUT_STYLE)
        self._java_combo.currentIndexChanged.connect(self._on_java_selected)
        layout.addWidget(self._java_combo)

        self._java_recommend = QLabel(tr(""))
        self._java_recommend.setWordWrap(True)
        self._java_recommend.setStyleSheet("font-size: 11px;")
        layout.addWidget(self._java_recommend)

        btn_row = QHBoxLayout()
        detect_btn = QPushButton(tr("Auto Detect"))
        detect_btn.setStyleSheet(FLAT_BTN_STYLE)
        detect_btn.clicked.connect(self._detect_java)
        btn_row.addWidget(detect_btn)

        browse_btn = QPushButton(tr("Browse..."))
        browse_btn.setStyleSheet(FLAT_BTN_STYLE)
        browse_btn.clicked.connect(self._browse_java)
        btn_row.addWidget(browse_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)
        layout.addStretch()
        return w

    def _create_mods_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        self._mod_count = QLabel(tr("0 mod(s)"))
        self._mod_count.setStyleSheet("color: #8a8a8a;")
        toolbar.addWidget(self._mod_count)
        toolbar.addStretch()

        refresh_btn = QPushButton(tr("Refresh"))
        refresh_btn.setStyleSheet(FLAT_BTN_STYLE)
        refresh_btn.clicked.connect(self._refresh_mod_list)
        toolbar.addWidget(refresh_btn)

        dl_btn = QPushButton(tr("Download Mods"))
        dl_btn.setStyleSheet(BLUE_BTN_STYLE)
        dl_btn.clicked.connect(self._open_mod_download)
        toolbar.addWidget(dl_btn)
        layout.addLayout(toolbar)

        self._mods_list = QListWidget()
        self._mods_list.setStyleSheet(LIST_STYLE)
        self._mods_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._mods_list.customContextMenuRequested.connect(self._mod_context_menu)
        layout.addWidget(self._mods_list, 1)

        bottom_row = QHBoxLayout()
        mods_folder_btn = QPushButton(tr("Open Mods Folder"))
        mods_folder_btn.setStyleSheet(FLAT_BTN_STYLE)
        mods_folder_btn.clicked.connect(self._open_mods_folder)
        bottom_row.addWidget(mods_folder_btn)
        bottom_row.addStretch()

        del_btn = QPushButton(tr("Delete Instance"))
        del_btn.setStyleSheet(DANGER_BTN_STYLE)
        del_btn.clicked.connect(self._delete_version)
        bottom_row.addWidget(del_btn)
        layout.addLayout(bottom_row)
        return w

    def _create_loader_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        info_group = QGroupBox("Installed")
        info_group.setStyleSheet(GROUP_STYLE)
        info_layout = QGridLayout(info_group)
        info_layout.addWidget(QLabel(tr("ModLoader:")), 0, 0)
        self._ml_label = QLabel(tr("None"))
        self._ml_label.setStyleSheet("color: #e8e8e8; font-weight: bold;")
        info_layout.addWidget(self._ml_label, 0, 1)
        self._ml_uninstall = QPushButton(tr("Uninstall"))
        self._ml_uninstall.setStyleSheet(DANGER_BTN_STYLE)
        self._ml_uninstall.clicked.connect(self._uninstall_modloader)
        self._ml_uninstall.setVisible(False)
        info_layout.addWidget(self._ml_uninstall, 0, 2)

        info_layout.addWidget(QLabel(tr("OptiFine:")), 1, 0)
        self._of_label = QLabel(tr("None"))
        self._of_label.setStyleSheet("color: #e8e8e8; font-weight: bold;")
        info_layout.addWidget(self._of_label, 1, 1)
        self._of_uninstall = QPushButton(tr("Uninstall"))
        self._of_uninstall.setStyleSheet(DANGER_BTN_STYLE)
        self._of_uninstall.clicked.connect(self._uninstall_optifine)
        self._of_uninstall.setVisible(False)
        info_layout.addWidget(self._of_uninstall, 1, 2)
        layout.addWidget(info_group)

        install_group = QGroupBox("Install Mod Loader")
        install_group.setStyleSheet(GROUP_STYLE)
        il = QVBoxLayout(install_group)

        loader_row = QHBoxLayout()
        self._loader_combo = QComboBox()
        self._loader_combo.setStyleSheet(INPUT_STYLE)
        for name, key in self.LOADERS:
            self._loader_combo.addItem(name, key)
        self._loader_combo.currentIndexChanged.connect(self._on_loader_type_changed)
        loader_row.addWidget(self._loader_combo)

        install_btn = QPushButton(tr("Install"))
        install_btn.setStyleSheet(BLUE_BTN_STYLE)
        install_btn.clicked.connect(self._install_modloader)
        loader_row.addWidget(install_btn)
        il.addLayout(loader_row)

        self._loader_ver_list = QListWidget()
        self._loader_ver_list.setStyleSheet(LIST_STYLE)
        self._loader_ver_list.setMaximumHeight(140)
        il.addWidget(self._loader_ver_list)

        self._loader_status = QLabel(tr(""))
        self._loader_status.setStyleSheet("color: #8a8a8a; font-size: 11px;")
        il.addWidget(self._loader_status)

        self._loader_progress = QProgressBar()
        self._loader_progress.setVisible(False)
        il.addWidget(self._loader_progress)
        layout.addWidget(install_group)

        of_group = QGroupBox("OptiFine")
        of_group.setStyleSheet(GROUP_STYLE)
        of_layout = QVBoxLayout(of_group)
        of_sel = QHBoxLayout()
        self._of_list = QListWidget()
        self._of_list.setStyleSheet(LIST_STYLE)
        self._of_list.setMaximumHeight(100)
        of_sel.addWidget(self._of_list, 1)
        self._of_install_btn = QPushButton(tr("Install"))
        self._of_install_btn.setStyleSheet(BLUE_BTN_STYLE)
        self._of_install_btn.clicked.connect(self._install_optifine)
        of_sel.addWidget(self._of_install_btn)
        of_layout.addLayout(of_sel)
        self._of_status = QLabel(tr("Fetching..."))
        self._of_status.setStyleSheet("color: #8a8a8a; font-size: 11px;")
        of_layout.addWidget(self._of_status)
        self._of_progress = QProgressBar()
        self._of_progress.setVisible(False)
        of_layout.addWidget(self._of_progress)
        layout.addWidget(of_group)
        return w

    def _create_jvm_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        hint = QLabel(
            "Per-instance JVM arguments.\n"
            "Example: --add-opens java.base/java.lang=ALL-UNNAMED")
        hint.setStyleSheet("color: #8a8a8a; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._jvm_input = QLineEdit()
        self._jvm_input.setPlaceholderText("Extra JVM args for this instance...")
        self._jvm_input.setStyleSheet(INPUT_STYLE)
        self._jvm_input.textChanged.connect(self._on_jvm_changed)
        layout.addWidget(self._jvm_input)

        layout.addStretch()
        return w

    def _create_saves_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        hint = QLabel(
            "Sync saves across instances by creating symlinks to a shared saves folder.")
        hint.setStyleSheet("color: #8a8a8a; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._shared_cb = QCheckBox("Share saves across all instances")
        self._shared_cb.setStyleSheet("QCheckBox { color: #b4b4b4; }")
        self._shared_cb.stateChanged.connect(self._on_shared_changed)
        layout.addWidget(self._shared_cb)

        self._save_status = QLabel(tr(""))
        self._save_status.setStyleSheet("color: #8a8a8a;")
        layout.addWidget(self._save_status)

        self._sync_btn = QPushButton(tr("Sync Now"))
        self._sync_btn.setStyleSheet(FLAT_BTN_STYLE)
        self._sync_btn.clicked.connect(self._sync_saves)
        self._sync_btn.setVisible(False)
        layout.addWidget(self._sync_btn)

        self._save_progress = QProgressBar()
        self._save_progress.setVisible(False)
        self._save_progress.setMaximumHeight(14)
        layout.addWidget(self._save_progress)

        layout.addStretch()
        return w

    def _load(self):
        self._populate_java()
        self._refresh_modloader_status()
        self._on_loader_type_changed()
        self._fetch_optifine()
        self._load_jvm_args()
        self._load_saves()
        QTimer.singleShot(10, self._refresh_mod_list)

    def _populate_java(self):
        self._java_combo.blockSignals(True)
        self._java_combo.clear()

        data = self.vm.load_version_json(self.version_id)
        oml = data.get("_openmcl", {}) if data else {}
        saved_java = oml.get("java_path", "")

        required = get_mc_required_java_for_version(self.vm, self.version_id)

        if saved_java and os.path.isfile(saved_java):
            jv = get_java_major_version(saved_java)
            tag = "OK" if (jv == 0 or required == 0 or jv >= required) else "OLD"
            label = "[{}] {} (Java {})".format(tag, os.path.basename(saved_java), jv)
            self._java_combo.addItem(label, saved_java)
            self._java_combo.setCurrentIndex(0)
            self._java_path = saved_java

        for j in self._java_list:
            path = j.get("path", "")
            if path == saved_java:
                continue
            if not path or not os.path.isfile(path):
                continue
            ver = j.get("version", "?")
            arch = j.get("arch", "?")
            jv = j.get("jvm_major", 0) or get_java_major_version(path)
            ok = (jv == 0 or required == 0 or jv >= required)
            tag = "OK" if ok else "OLD"
            label = "[{}] Java {} ({}-bit)".format(tag, ver, arch)
            self._java_combo.addItem(label, path)

        if not saved_java and self._java_combo.count() == 0:
            self._java_combo.addItem("java (default)", "java")
            if self._java_path and self._java_path != "java":
                self._java_combo.addItem(self._java_path, self._java_path)

        self._update_java_recommend(required)
        self._java_combo.blockSignals(False)

    def _update_java_recommend(self, required):
        if required <= 0:
            self._java_recommend.setText(tr(""))
            return
        java_list = find_java()
        compat = [j for j in java_list
                  if get_java_major_version(j.get("path", "")) >= required]
        if compat:
            names = ", ".join("Java {}".format(j.get("version", "?"))
                              for j in compat[:2])
            if len(compat) > 2:
                names += " +{} more".format(len(compat) - 2)
            self._java_recommend.setText(
                "Requires Java {}+   Found: {}".format(required, names))
            self._java_recommend.setStyleSheet("color: #4CAF50; font-size: 11px;")
        else:
            self._java_recommend.setText(
                "Requires Java {}+ - no compatible Java found!".format(required))
            self._java_recommend.setStyleSheet("color: #F44336; font-size: 11px;")

    def _on_java_selected(self):
        path = self._java_combo.currentData()
        if path:
            self._java_path = path
            self._save_instance_config("java_path", path)
            self.java_changed.emit(self.version_id, path)

    def _detect_java(self):
        class DThread(QThread):
            result = pyqtSignal(list)

            def run(self):
                try:
                    self.result.emit(find_java())
                except Exception:
                    self.result.emit([])

        def on_done(jl):
            self._java_list = jl
            self._populate_java()

        t = DThread()
        t.result.connect(on_done)
        t.start()
        self._detect_thread = t

    def _browse_java(self):
        from PyQt5.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Java Executable", "",
            "Java (java.exe javaw.exe java);;All (*.*)")
        if path:
            jv = get_java_major_version(path)
            self._java_list.append({
                "path": path,
                "version": str(jv) if jv > 0 else "?",
                "arch": "64",
                "jvm_major": jv,
            })
            self._populate_java()

    def _refresh_modloader_status(self):
        data = self.vm.load_version_json(self.version_id)
        oml = data.get("_openmcl", {}) if data else {}
        ml = oml.get("modloader")
        if ml:
            names = {"forge": "Forge", "neoforge": "NeoForge",
                     "fabric": "Fabric", "quilt": "Quilt"}
            n = names.get(ml.get("type", ""), ml.get("type", ""))
            self._ml_label.setText(tr("{} {}").format(n, ml.get("version", "")))
            self._ml_uninstall.setVisible(True)
        else:
            self._ml_label.setText(tr("None"))
            self._ml_uninstall.setVisible(False)
        of = oml.get("optifine")
        if of:
            self._of_label.setText(tr("OptiFine {}").format(of))
            self._of_uninstall.setVisible(True)
        else:
            self._of_label.setText(tr("None"))
            self._of_uninstall.setVisible(False)

    def _get_mods_dir(self):
        return os.path.join(
            self.vm.get_version_game_dir(self.version_id), "mods")

    def _refresh_mod_list(self):
        try:
            self._mods_list.clear()
            mods_dir = self._get_mods_dir()
            if not os.path.isdir(mods_dir):
                self._mod_count.setText(tr("0 mod(s)"))
                return
            entries = sorted(os.listdir(mods_dir))
            count = 0
            for name in entries:
                if not name.lower().endswith(".jar"):
                    continue
                full = os.path.join(mods_dir, name)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                if size < 1024:
                    sz = "{} B".format(size)
                elif size < 1048576:
                    sz = "{:.1f} KB".format(size / 1024)
                else:
                    sz = "{:.1f} MB".format(size / 1048576)
                label = "{}  [{}]".format(name, sz)
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, name)
                item.setToolTip(full)
                self._mods_list.addItem(item)
                count += 1
            self._mod_count.setText(tr("{} mod(s)").format(count))
        except Exception as e:
            self._mod_count.setText(tr("Error: {}").format(e))

    def _mod_context_menu(self, pos):
        item = self._mods_list.itemAt(pos)
        if not item:
            return
        from PyQt5.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #2a2a2e; color: #b4b4b4; }"
            "QMenu::item:selected { background: #444; }")
        rm = menu.addAction("Remove")
        op = menu.addAction("Open Mods Folder")
        act = menu.exec_(self._mods_list.mapToGlobal(pos))
        if act == rm:
            self._remove_mod(item)
        elif act == op:
            self._open_mods_folder()

    def _remove_mod(self, item):
        name = item.data(Qt.UserRole)
        full = os.path.join(self._get_mods_dir(), name)
        self.message_requested.emit("question", tr("Remove"),
            tr("Delete '{}'?").format(name),
            lambda ok: self._do_remove_mod(ok, full, name))

    def _do_remove_mod(self, ok, full, name):
        if not ok:
            return
        try:
            os.remove(full)
            self.console_log.emit("Removed mod: {}".format(name))
        except OSError as e:
            self.console_log.emit("Failed: {}".format(e))
        self._refresh_mod_list()

    def _open_mod_download(self):
        data = self.vm.load_version_json(self.version_id)
        mc_version = self.version_id
        loader = ""
        if data:
            parent = data.get("inheritsFrom", "")
            mc_version = parent if parent else self.version_id
            oml = data.get("_openmcl", {})
            loader = oml.get("modloader", {}).get("type", "")
            if not loader:
                for key in ("forge", "neoforge", "fabric", "quilt"):
                    if key in self.version_id.lower():
                        loader = key
                        break
        w = self.window()
        if hasattr(w, '_show_mod_browser'):
            w._show_mod_browser(tr("Download Mods"), mc_version, loader, self._get_mods_dir())
        else:
            from PyQt5.QtWidgets import QDialog as QDlg
            from ui.mod_download_widget import ModDownloadWidget
            dlg = QDlg(self)
            dlg.setWindowTitle(tr("Download Mods"))
            dlg.resize(750, 550)
            layout = QVBoxLayout(dlg)
            mw = ModDownloadWidget(self.vm)
            mw.set_mods_dir(self._get_mods_dir())
            mw.console_log.connect(self.console_log.emit)
            mw.install_finished.connect(
                lambda: (self._refresh_mod_list(),
                         self.console_log.emit("Done.")))
            layout.addWidget(mw)
            self._mod_dlg = dlg
            dlg.show()

    def _open_mods_folder(self):
        d = self._get_mods_dir()
        os.makedirs(d, exist_ok=True)
        if hasattr(os, "startfile"):
            os.startfile(d)

    def _delete_version(self):
        self.message_requested.emit("question", tr("Delete"),
                tr("Delete instance '{}' permanently?\nThis cannot be undone.").format(
                    self.version_id),
                lambda ok: self._do_delete_version(ok))

    def _do_delete_version(self, ok):
        if not ok:
            return
        for sub in ["versions", "natives"]:
            d = os.path.join(self.vm.minecraft_dir, sub, self.version_id)
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
        gd = self.vm.get_version_game_dir(self.version_id)
        if os.path.isdir(gd) and gd != self.vm.minecraft_dir:
            shutil.rmtree(gd, ignore_errors=True)
        self.uninstalled.emit(self.version_id)
        self.close()

    def _on_loader_type_changed(self):
        self._loader_ver_list.clear()
        self._loader_status.setText(tr("Fetching..."))
        t = ModLoaderFetchThread(
            self.modloader, self._loader_combo.currentData(),
            self.version_id)
        t.finished.connect(self._on_loader_fetched)
        t.start()
        self._loader_fetch_thread = t

    def _on_loader_fetched(self, lt, results, error):
        if lt != self._loader_combo.currentData():
            return
        self._loader_ver_list.clear()
        if error:
            self._loader_status.setText(tr("Error: {}").format(error))
            return
        for r in results:
            item = QListWidgetItem(r["label"])
            item.setData(Qt.UserRole, r["data"])
            self._loader_ver_list.addItem(item)
        self._loader_status.setText(
            "{} versions available.".format(len(results)))

    def install_modloader_for(self, ml_type, ml_info, loader_version=""):
        for name, key in self.LOADERS:
            if key == ml_type:
                idx = self._loader_combo.findData(key)
                if idx >= 0:
                    self._loader_combo.setCurrentIndex(idx)
                break
        self._install_modloader()

    def _install_modloader(self):
        item = self._loader_ver_list.currentItem()
        if not item:
            self.message_requested.emit("info", tr("Info"), tr("Select a version first."), None)
            return
        lt, data = item.data(Qt.UserRole)
        self.message_requested.emit("question", tr("Confirm"),
                tr("Install {} for {}?").format(lt, self.version_id),
                lambda ok: self._do_install_modloader(ok, lt, data))

    def _do_install_modloader(self, ok, lt, data):
        if not ok:
            return
        self._loader_progress.setVisible(True)
        self._loader_progress.setValue(0)
        self._tab_widget.setEnabled(False)
        self.console_log.emit(
            "Installing {} for {}...".format(lt, self.version_id))
        t = ModLoaderInstallThread(
            self.modloader, self._java_path, self.version_id, lt, data)
        t.log.connect(self.console_log.emit)
        t.progress.connect(
            lambda n, d, total: self._loader_progress.setValue(
                d * 100 // max(total, 1) if total > 0 else 0))
        t.side_panel_task.connect(self.side_panel_task.emit)
        t.side_panel_progress.connect(self.side_panel_progress.emit)
        t.side_panel_done.connect(self.side_panel_done.emit)
        t.finished_signal.connect(self._on_loader_installed)
        t.start()
        self._loader_install_thread = t

    def _on_loader_installed(self, ok, msg):
        self._tab_widget.setEnabled(True)
        self._loader_progress.setVisible(False)
        if ok:
            self.console_log.emit("Installed: {}".format(msg))
            self._refresh_modloader_status()
            self.loader_installed.emit()
        else:
            self.console_log.emit("Failed: {}".format(msg))

    def _fetch_optifine(self):
        from core.launcher import extract_base_mc_version
        base = extract_base_mc_version(self.version_id)
        self._of_list.clear()
        self._of_status.setText(tr("Fetching..."))
        t = ModLoaderFetchThread(self.modloader, "optifine", base)
        t.finished.connect(self._on_of_fetched)
        t.start()
        self._of_fetch_thread = t

    def _on_of_fetched(self, lt, results, error):
        if lt != "optifine":
            return
        self._of_list.clear()
        if error:
            self._of_status.setText(tr("Error: {}").format(error))
            return
        for r in results:
            item = QListWidgetItem(r["label"])
            item.setData(Qt.UserRole, r["data"])
            self._of_list.addItem(item)
        self._of_status.setText(
            "{} versions available.".format(len(results)))

    def _install_optifine(self):
        item = self._of_list.currentItem()
        if not item:
            self.message_requested.emit("info", tr("Info"),
                                    tr("Select OptiFine version first."), None)
            return
        lt, data = item.data(Qt.UserRole)
        self.message_requested.emit("question", tr("Confirm"),
                tr("Install OptiFine {}?").format(data["version"]),
                lambda ok: self._do_install_optifine(ok, lt, data))

    def _do_install_optifine(self, ok, lt, data):
        if not ok:
            return

        mc_dir = os.path.abspath(self.vm.minecraft_dir)
        profiles_path = os.path.join(mc_dir, "launcher_profiles.json")
        try:
            profiles = {}
            if os.path.exists(profiles_path):
                with open(profiles_path, "r", encoding="utf-8") as f:
                    profiles = json.load(f)
            profiles.setdefault("profiles", {})[self.version_id] = {
                "name": self.version_id,
                "type": "custom",
                "lastVersionId": self.version_id,
            }
            with open(profiles_path, "w", encoding="utf-8") as f:
                json.dump(profiles, f, indent=2)
        except Exception:
            pass
        QApplication.clipboard().setText(mc_dir)
        self.message_requested.emit("info",
            tr("OptiFine"),
            tr("Paste this path in the installer (Ctrl+V):\n{}").format(mc_dir), None)

        self._of_progress.setVisible(True)
        self._of_install_btn.setEnabled(False)
        t = ModLoaderInstallThread(
            self.modloader, self._java_path, self.version_id, lt, data)
        t.log.connect(self.console_log.emit)
        t.progress.connect(
            lambda n, d, total: self._of_progress.setValue(
                d * 100 // max(total, 1) if total > 0 else 0))
        t.finished_signal.connect(self._on_of_installed)
        t.start()
        self._of_install_thread = t

    def _on_of_installed(self, ok, msg):
        self._of_install_btn.setEnabled(True)
        self._of_progress.setVisible(False)
        if ok:
            self.console_log.emit("OptiFine: {}".format(msg))
            self._refresh_modloader_status()
            self.loader_installed.emit()
        else:
            self.console_log.emit("OptiFine failed: {}".format(msg))

    def _uninstall_modloader(self):
        self.message_requested.emit("question", tr("Uninstall"),
                tr("Remove modloader from {}?").format(self.version_id),
                lambda ok: self._do_uninstall_modloader(ok))

    def _do_uninstall_modloader(self, ok):
        if not ok:
            return
        json_path = os.path.join(
            self.vm.minecraft_dir, "versions", self.version_id,
            "{}.json".format(self.version_id))
        try:
            data = {}
            if os.path.exists(json_path):
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            oml = data.get("_openmcl", {})
            oml.pop("modloader", None)
            data["_openmcl"] = oml
            data["mainClass"] = "net.minecraft.client.main.Main"
            if "minecraftArguments" in data:
                data["minecraftArguments"] = re.sub(
                    r'\s*--tweakClass\s+\S+', '',
                    data["minecraftArguments"])
            vanilla = self.vm.fetch_version_json(self.version_id)
            if vanilla:
                if "arguments" in data:
                    data["arguments"] = vanilla.get(
                        "arguments", data["arguments"])
                data["libraries"] = vanilla.get(
                    "libraries", data.get("libraries", []))
            fabric_cache = os.path.join(
                self.vm.minecraft_dir, "versions", self.version_id,
                ".fabric")
            if os.path.exists(fabric_cache):
                shutil.rmtree(fabric_cache, ignore_errors=True)
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self._refresh_modloader_status()
            self.console_log.emit("ModLoader uninstalled.")
            self.loader_installed.emit()
        except Exception as e:
            self.console_log.emit("Failed: {}".format(e))

    def _uninstall_optifine(self):
        self.message_requested.emit("question", tr("Uninstall"),
                tr("Remove OptiFine from {}?").format(self.version_id),
                lambda ok: self._do_uninstall_optifine(ok))

    def _do_uninstall_optifine(self, ok):
        if not ok:
            return
        json_path = os.path.join(
            self.vm.minecraft_dir, "versions", self.version_id,
            "{}.json".format(self.version_id))
        try:
            data = {}
            if os.path.exists(json_path):
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            oml = data.get("_openmcl", {})
            oml.pop("optifine", None)
            data["_openmcl"] = oml
            data["libraries"] = [
                l for l in data.get("libraries", [])
                if "optifine" not in str(l.get("name", "")).lower()]
            if "minecraftArguments" in data:
                data["minecraftArguments"] = re.sub(
                    r'\s*--tweakClass\s+optifine\.\S+', '',
                    data["minecraftArguments"])
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self._refresh_modloader_status()
            self.console_log.emit("OptiFine uninstalled.")
            self.loader_installed.emit()
        except Exception as e:
            self.console_log.emit("Failed: {}".format(e))

    def _load_jvm_args(self):
        data = self.vm.load_version_json(self.version_id)
        oml = data.get("_openmcl", {}) if data else {}
        args = oml.get("jvm_args", "")
        self._jvm_input.blockSignals(True)
        self._jvm_input.setText(args if args else "")
        self._jvm_input.blockSignals(False)

    def _on_jvm_changed(self):
        self._save_instance_config(
            "jvm_args", self._jvm_input.text().strip())

    def _load_saves(self):
        data = self.vm.load_version_json(self.version_id)
        oml = data.get("_openmcl", {}) if data else {}
        self._shared_cb.blockSignals(True)
        self._shared_cb.setChecked(oml.get("shared_saves", False))
        self._shared_cb.blockSignals(False)
        self._update_save_ui(oml.get("shared_saves", False))

    def _on_shared_changed(self, state):
        self._save_instance_config("shared_saves", bool(state))
        self._update_save_ui(bool(state))

    def _update_save_ui(self, shared):
        if shared:
            self._save_status.setText(tr("Saves will be synced on next launch."))
            self._sync_btn.setVisible(True)
        else:
            self._save_status.setText(tr(""))
            self._sync_btn.setVisible(False)

    def _sync_saves(self):
        if self._launcher is None:
            return
        self._sync_btn.setEnabled(False)
        self._save_progress.setVisible(True)
        self._save_progress.setValue(0)
        t = SaveSyncThread(self._launcher, self.version_id, "to_shared")
        t.progress.connect(
            lambda c, t: self._save_progress.setValue(
                c * 100 // max(t, 1) if t > 0 else 0))
        t.finished_signal.connect(self._on_sync_done)
        t.start()
        self._save_sync_thread = t

    def _on_sync_done(self, ok, msg):
        self._save_progress.setVisible(False)
        self._sync_btn.setEnabled(True)
        self.console_log.emit(msg)

    def get_instance_java(self):
        return self._java_path

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)

    def get_instance_jvm_args(self):
        text = self._jvm_input.text().strip()
        if not text:
            return None
        return [a.strip() for a in text.split() if a.strip()]

    def _save_instance_config(self, key, value):
        json_path = os.path.join(
            self.vm.minecraft_dir, "versions", self.version_id,
            "{}.json".format(self.version_id))
        try:
            data = {}
            if os.path.exists(json_path):
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            oml = data.setdefault("_openmcl", {})
            if value or isinstance(value, bool):
                oml[key] = value
            else:
                oml.pop(key, None)
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            self.console_log.emit("Failed to save {}: {}".format(key, e))