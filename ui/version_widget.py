import os
import json
import shutil
import zipfile
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
                             QPushButton, QLabel, QProgressBar, QMessageBox,
                             QGroupBox, QLineEdit, QListWidgetItem, QFileDialog,
                             QDialog, QComboBox, QMenu)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont

from core.version_manager import VersionManager, network_available
from core.downloader import Downloader
from core.modloader_manager import ModLoaderManager


class FetchManifestWorker(QThread):
    finished = pyqtSignal(bool, str)

    def __init__(self, vm):
        super().__init__()
        self.vm = vm

    def run(self):
        try:
            self.vm.fetch_version_manifest()
            self.finished.emit(True, "")
        except Exception as e:
            self.finished.emit(False, str(e))


class InstallWorker(QThread):
    progress = pyqtSignal(str, int, int)
    stage = pyqtSignal(str)
    log = pyqtSignal(str)
    finished = pyqtSignal(bool, str, str)
    asset_progress = pyqtSignal(int, int)
    lib_progress = pyqtSignal(int, int)

    def __init__(self, vm, downloader, version_id):
        super().__init__()
        self.vm = vm
        self.downloader = downloader
        self.version_id = version_id

    def _mb(self, b):
        if b > 1048576:
            return f"{b / 1048576:.1f} MB"
        if b > 1024:
            return f"{b / 1024:.0f} KB"
        return f"{b} B"

    def _jar_progress(self, name, downloaded, total):
        self.progress.emit(name, downloaded, total)
        pct = downloaded * 100 // total if total > 0 else 0
        self.log.emit(f"  JAR: {self._mb(downloaded)} / {self._mb(total)} ({pct}%)")

    def _asset_tracker(self, total):
        last_pct = [-1]
        def track(completed, t):
            self.asset_progress.emit(completed, t)
            pct = completed * 100 // max(t, 1)
            if pct != last_pct[0] and pct % 10 == 0:
                last_pct[0] = pct
                self.log.emit(f"  Assets: {completed}/{t} ({pct}%)")
            elif completed == t:
                self.log.emit(f"  Assets: {completed}/{t} (100%)")
        return track

    def _lib_tracker(self, total):
        last_pct = [-1]
        def track(completed, t):
            self.lib_progress.emit(completed, t)
            pct = completed * 100 // max(t, 1)
            if pct != last_pct[0] and pct % 10 == 0:
                last_pct[0] = pct
                self.log.emit(f"  Libraries: {completed}/{t} ({pct}%)")
            elif completed == t:
                self.log.emit(f"  Libraries: {completed}/{t} (100%)")
        return track

    def run(self):
        try:
            self.log.emit(f"Installing Minecraft {self.version_id}...")
            self.stage.emit("Downloading version JSON...")
            self.log.emit("  Fetching version metadata from BMCLAPI...")
            self.downloader.download_version_json(self.version_id)

            self.stage.emit("Downloading Minecraft client jar...")
            self.log.emit("  Downloading client JAR...")
            self.downloader.download_client_jar(
                self.version_id,
                progress_callback=self._jar_progress
            )

            self.stage.emit("Downloading asset index...")
            self.log.emit("  Downloading asset index...")
            self.downloader.download_asset_index(self.version_id)

            self.stage.emit("Downloading assets...")
            data = self.downloader.vm.load_version_json(self.version_id)
            asset_index_id = data.get("assetIndex", {}).get("id")
            asset_index_path = self.downloader.vm.get_asset_index_path(asset_index_id)
            if os.path.exists(asset_index_path):
                with open(asset_index_path, "r", encoding="utf-8") as f:
                    asset_index = json.load(f)
                total_assets = len(asset_index.get("objects", {}))
                self.log.emit(f"  Downloading {total_assets} assets (16 threads)...")
            self.downloader.download_assets(
                self.version_id,
                overall_progress=self._asset_tracker(0)
            )

            self.stage.emit("Downloading libraries...")
            data = self.downloader.vm.load_version_json(self.version_id)
            total_libs = len(data.get("libraries", []))
            self.log.emit(f"  Downloading {total_libs} libraries (8 threads)...")
            libs_ok, lib_failed = self.downloader.download_libraries(
                self.version_id,
                overall_progress=self._lib_tracker(0)
            )

            self.stage.emit("Extracting native libraries...")
            self.log.emit("  Extracting native libraries...")
            self.downloader.extract_natives(self.version_id)

            if lib_failed:
                self.log.emit(f"WARNING: {len(lib_failed)} library download(s) failed!")
                for name in lib_failed[:5]:
                    self.log.emit(f"  - {name}")
                if len(lib_failed) > 5:
                    self.log.emit(f"  ... and {len(lib_failed) - 5} more")
                self.finished.emit(
                    True,
                    f"Version {self.version_id} installed with {len(lib_failed)} library failure(s). "
                    f"Try reinstalling if launch fails.",
                    self.version_id
                )
            else:
                self.log.emit(f"Installation of {self.version_id} complete!")
                self.finished.emit(True, f"Version {self.version_id} installed successfully!", self.version_id)
        except Exception as e:
            self.log.emit(f"ERROR: {e}")
            self.finished.emit(False, str(e), self.version_id)


class ImportWorker(QThread):
    stage = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, vm, source_dir):
        super().__init__()
        self.vm = vm
        self.source_dir = source_dir

    def run(self):
        try:
            self.stage.emit("Scanning source directory...")
            src_versions = os.path.join(self.source_dir, "versions")
            src_assets = os.path.join(self.source_dir, "assets")
            src_libraries = os.path.join(self.source_dir, "libraries")

            count = 0
            if os.path.isdir(src_versions):
                for name in os.listdir(src_versions):
                    json_path = os.path.join(src_versions, name, f"{name}.json")
                    if not os.path.isfile(json_path):
                        continue
                    dst_dir = os.path.join(self.vm.versions_dir, name)
                    if os.path.exists(dst_dir):
                        continue
                    self.stage.emit(f"Importing version: {name}...")
                    shutil.copytree(os.path.join(src_versions, name), dst_dir)
                    count += 1

            if os.path.isdir(src_assets):
                for sub in ["indexes", "objects"]:
                    src_sub = os.path.join(src_assets, sub)
                    dst_sub = os.path.join(self.vm.assets_dir, sub)
                    if os.path.isdir(src_sub):
                        os.makedirs(dst_sub, exist_ok=True)
                        for fname in os.listdir(src_sub):
                            src_f = os.path.join(src_sub, fname)
                            dst_f = os.path.join(dst_sub, fname)
                            if not os.path.exists(dst_f):
                                if os.path.isdir(src_f):
                                    shutil.copytree(src_f, dst_f)
                                else:
                                    shutil.copy2(src_f, dst_f)

            if os.path.isdir(src_libraries):
                os.makedirs(self.vm.libraries_dir, exist_ok=True)
                self.stage.emit("Importing libraries (this may take a while)...")
                for root, dirs, files in os.walk(src_libraries):
                    rel = os.path.relpath(root, src_libraries)
                    dst_root = os.path.join(self.vm.libraries_dir, rel)
                    os.makedirs(dst_root, exist_ok=True)
                    for f in files:
                        src_f = os.path.join(root, f)
                        dst_f = os.path.join(dst_root, f)
                        if not os.path.exists(dst_f):
                            try:
                                shutil.copy2(src_f, dst_f)
                            except OSError:
                                pass

            self.finished.emit(True, f"Imported {count} versions from external installation.")
        except Exception as e:
            self.finished.emit(False, str(e))


class VersionWidget(QWidget):
    install_requested = pyqtSignal(str)
    uninstall_requested = pyqtSignal(str)
    console_log = pyqtSignal(str)

    def __init__(self, vm=None, downloader=None):
        super().__init__()
        self.vm = vm if vm else VersionManager()
        self.downloader = downloader if downloader else Downloader(self.vm)
        self.modloader = ModLoaderManager(self.vm, self.downloader)
        self.install_worker = None
        self.import_worker = None
        self._current_install_id = None
        self._java_path = "java"
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        actions = QHBoxLayout()
        refresh_btn = QPushButton("Refresh List")
        refresh_btn.clicked.connect(self.on_refresh)
        actions.addWidget(refresh_btn)

        import_btn = QPushButton("Import .minecraft")
        import_btn.clicked.connect(self.on_import)
        actions.addWidget(import_btn)

        import_modpack_btn = QPushButton("Import Modpack")
        import_modpack_btn.clicked.connect(self.on_import_modpack)
        actions.addWidget(import_modpack_btn)
        layout.addLayout(actions)

        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Filter:"))
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Search versions...")
        self.filter_input.textChanged.connect(self.on_filter)
        filter_layout.addWidget(self.filter_input)

        self.show_releases = QPushButton("Releases")
        self.show_releases.setCheckable(True)
        self.show_releases.setChecked(True)
        self.show_releases.clicked.connect(self.on_filter)
        self.show_snapshots = QPushButton("Snapshots")
        self.show_snapshots.setCheckable(True)
        self.show_snapshots.setChecked(True)
        self.show_snapshots.clicked.connect(self.on_filter)
        self.show_old = QPushButton("Old")
        self.show_old.setCheckable(True)
        self.show_old.setChecked(False)
        self.show_old.clicked.connect(self.on_filter)

        filter_layout.addWidget(self.show_releases)
        filter_layout.addWidget(self.show_snapshots)
        filter_layout.addWidget(self.show_old)
        layout.addLayout(filter_layout)

        self.version_list = QListWidget()
        self.version_list.itemDoubleClicked.connect(self.on_double_click)
        self.version_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.version_list.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self.version_list, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        progress_group = QGroupBox("Progress")
        progress_layout = QVBoxLayout(progress_group)

        self.stage_label = QLabel("Ready")
        progress_layout.addWidget(self.stage_label)

        self.file_progress = QProgressBar()
        progress_layout.addWidget(self.file_progress)

        progress_layout.addWidget(QLabel("Assets:"))
        self.asset_progress = QProgressBar()
        progress_layout.addWidget(self.asset_progress)

        progress_layout.addWidget(QLabel("Libraries:"))
        self.lib_progress = QProgressBar()
        progress_layout.addWidget(self.lib_progress)

        layout.addWidget(progress_group)

    def on_refresh(self):
        self.status_label.setText("Fetching version manifest...")
        self._manifest_worker = FetchManifestWorker(self.vm)
        self._manifest_worker.finished.connect(self._on_manifest_ready)
        self._manifest_worker.start()

    def _on_manifest_ready(self, success, error):
        if success:
            self.refresh_version_list()
            self.status_label.setText("Version list updated.")
        else:
            self.status_label.setText(f"Error fetching manifest: {error}")

    def refresh_version_list(self):
        self.version_list.clear()
        try:
            versions = self.vm.get_version_list()
        except Exception:
            self.status_label.setText("Could not fetch versions. Please refresh.")
            return

        if not versions:
            if self.vm._version_manifest is None:
                self.status_label.setText("No data - click Refresh or check network.")
            else:
                self.status_label.setText("No versions found.")
            return

        installed = set(self.vm.get_installed_versions())
        filter_text = self.filter_input.text().lower()

        for v in versions:
            vid = v["id"]
            vtype = v.get("type", "unknown")

            if not self.show_releases.isChecked() and vtype == "release":
                continue
            if not self.show_snapshots.isChecked() and vtype == "snapshot":
                continue
            if vtype == "demo":
                continue
            if not self.show_old.isChecked() and vtype not in ("release", "snapshot"):
                continue

            if filter_text and filter_text not in vid.lower():
                continue

            prefix = "[Installed] " if vid in installed else ""
            item = QListWidgetItem(f"{prefix}{vid} ({vtype})")
            item.setData(Qt.UserRole, vid)
            if vid in installed:
                item.setForeground(Qt.darkGreen)
            self.version_list.addItem(item)

    def on_filter(self):
        self.refresh_version_list()

    def _on_context_menu(self, pos):
        item = self.version_list.itemAt(pos)
        if item is None:
            return
        vid = item.data(Qt.UserRole)
        if vid is None:
            return

        menu = QMenu(self)
        installed = vid in set(self.vm.get_installed_versions())

        if not installed:
            inst_action = menu.addAction(f"Install {vid}")
            inst_action.triggered.connect(lambda: self.install_version(vid))
        else:
            uninst_action = menu.addAction(f"Uninstall {vid}")
            uninst_action.triggered.connect(lambda: self._uninstall_version(vid))

        ml_action = menu.addAction(f"Install Mod Loader for {vid}")
        ml_action.triggered.connect(lambda: self._on_install_modloader(vid))

        menu.exec_(self.version_list.viewport().mapToGlobal(pos))

    def _uninstall_version(self, version_id):
        reply = QMessageBox.question(
            self, "Confirm Uninstall",
            f"Are you sure you want to uninstall Minecraft {version_id}?\n\n"
            f"This will delete the version folder and all related files.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self.console_log.emit(f"Uninstalling {version_id}...")
        self.status_label.setText(f"Uninstalling {version_id}...")

        version_dir = os.path.join(self.vm.versions_dir, version_id)
        game_dir = self.vm.get_version_game_dir(version_id)

        deleted = False
        for d in [version_dir, game_dir]:
            d = os.path.normpath(d)
            mc_dir = os.path.normpath(self.vm.minecraft_dir)
            if d == mc_dir:
                continue
            if os.path.isdir(d):
                try:
                    shutil.rmtree(d, ignore_errors=True)
                    deleted = True
                except Exception as e:
                    self.console_log.emit(f"  Warning: {e}")

        if deleted:
            self.console_log.emit(f"Uninstalled {version_id}.")
            self.uninstall_requested.emit(version_id)
        else:
            self.console_log.emit(f"  No files found for {version_id}.")
        self.status_label.setText(f"Uninstalled {version_id}.")
        self.refresh_version_list()

    def _on_install_modloader(self, mcversion):
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Install Mod Loader for {mcversion}")
        dlg.setMinimumWidth(500)
        layout = QVBoxLayout(dlg)

        layout.addWidget(QLabel(f"Minecraft: {mcversion}"))

        loader_layout = QHBoxLayout()
        loader_layout.addWidget(QLabel("Loader:"))
        loader_combo = QComboBox()
        loader_combo.addItem("Forge", "forge")
        loader_combo.addItem("NeoForge", "neoforge")
        loader_combo.addItem("Fabric", "fabric")
        loader_combo.addItem("Quilt", "quilt")
        loader_combo.addItem("LiteLoader", "liteloader")
        loader_combo.addItem("OptiFine", "optifine")
        loader_layout.addWidget(loader_combo, 1)
        layout.addLayout(loader_layout)

        layout.addWidget(QLabel("Available versions:"))
        version_list = QListWidget()
        version_list.setMinimumHeight(180)
        layout.addWidget(version_list)

        status_label = QLabel("")
        layout.addWidget(status_label)

        progress = QProgressBar()
        progress.setVisible(False)
        layout.addWidget(progress)

        btn_row = QHBoxLayout()
        install_btn = QPushButton("Install")
        install_btn.setEnabled(False)
        btn_row.addWidget(install_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        def fetch_versions(loader_type):
            version_list.clear()
            install_btn.setEnabled(False)
            status_label.setText("Fetching...")
            try:
                if loader_type == "forge":
                    builds = self.modloader.get_forge_builds(mcversion)
                    for b in builds:
                        item = QListWidgetItem(f"Forge {b['version']} (build {b['build']})")
                        item.setData(Qt.UserRole, ("forge", b))
                        version_list.addItem(item)
                elif loader_type == "neoforge":
                    builds = self.modloader.get_neoforge_versions(mcversion)
                    for b in builds:
                        item = QListWidgetItem(f"NeoForge {b['version']}")
                        item.setData(Qt.UserRole, ("neoforge", b))
                        version_list.addItem(item)
                elif loader_type == "fabric":
                    loaders = self.modloader.get_fabric_loader_versions()
                    for l in loaders:
                        ver = l.get("version", "")
                        stable = "stable" if l.get("stable", False) else "dev"
                        item = QListWidgetItem(f"Fabric Loader {ver} ({stable})")
                        item.setData(Qt.UserRole, ("fabric", l))
                        version_list.addItem(item)
                elif loader_type == "quilt":
                    loaders = self.modloader.get_quilt_loader_versions()
                    for l in loaders:
                        ver = l.get("version", "")
                        item = QListWidgetItem(f"Quilt Loader {ver}")
                        item.setData(Qt.UserRole, ("quilt", l))
                        version_list.addItem(item)
                elif loader_type == "liteloader":
                    all_builds = self.modloader.get_liteloader_versions()
                    builds = all_builds.get(mcversion, [])
                    for b in builds:
                        item = QListWidgetItem(f"LiteLoader {b['version']} (build {b['build']})")
                        item.setData(Qt.UserRole, ("liteloader", b))
                        version_list.addItem(item)
                elif loader_type == "optifine":
                    builds = self.modloader.get_optifine_versions(mcversion)
                    for b in builds:
                        item = QListWidgetItem(f"OptiFine {b['version']}")
                        item.setData(Qt.UserRole, ("optifine", b))
                        version_list.addItem(item)
                status_label.setText(f"Found {version_list.count()} versions.")
            except Exception as e:
                status_label.setText(f"Error: {e}")

        loader_combo.currentIndexChanged.connect(
            lambda: fetch_versions(loader_combo.currentData()))

        version_list.currentRowChanged.connect(
            lambda _: install_btn.setEnabled(version_list.currentRow() >= 0))

        def do_install():
            item = version_list.currentItem()
            if item is None:
                return
            loader_type, data = item.data(Qt.UserRole)

            dlg.setEnabled(False)
            progress.setVisible(True)
            progress.setValue(0)
            self.console_log.emit(f"Installing {loader_type} for {mcversion}...")

            self._ml_thread = ModLoaderInstallThread(
                self.modloader, self._java_path, mcversion, loader_type, data)
            self._ml_thread.log.connect(self.console_log.emit)
            self._ml_thread.progress.connect(
                lambda n, d, t: progress.setValue(d * 100 // max(t, 1) if t > 0 else 0))
            self._ml_thread.finished_signal.connect(
                lambda ok, msg: self._on_ml_done(dlg, ok, msg, mcversion))
            self._ml_thread.start()

        install_btn.clicked.connect(do_install)
        fetch_versions("forge")
        dlg.exec_()

    def _on_ml_done(self, dlg, ok, msg, mcversion):
        dlg.setEnabled(True)
        if ok:
            dlg.accept()
            self.console_log.emit(f"Mod loader installed: {msg}")
            QMessageBox.information(self, "Success", f"Installed: {msg}")
            self.refresh_version_list()
            self.install_requested.emit(mcversion)
        else:
            self.console_log.emit(f"Install failed: {msg}")
            QMessageBox.critical(dlg, "Error", str(msg))

    def set_java_path(self, path):
        self._java_path = path or "java"

    def on_double_click(self, item):
        version_id = item.data(Qt.UserRole)
        if not version_id:
            return

        installed = self.vm.get_installed_versions()
        if version_id in installed:
            QMessageBox.information(self, "Info", f"Version {version_id} is already installed.")
            return

        reply = QMessageBox.question(
            self, "Install Version",
            f"Do you want to install Minecraft {version_id}?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.install_version(version_id)

    def install_version(self, version_id):
        self._current_install_id = version_id
        self.file_progress.setValue(0)
        self.asset_progress.setValue(0)
        self.lib_progress.setValue(0)

        self.install_worker = InstallWorker(self.vm, self.downloader, version_id)
        self.install_worker.log.connect(self.console_log.emit)
        self.install_worker.progress.connect(self.on_file_progress)
        self.install_worker.stage.connect(self.on_stage)
        self.install_worker.asset_progress.connect(self.on_asset_progress)
        self.install_worker.lib_progress.connect(self.on_lib_progress)
        self.install_worker.finished.connect(self.on_install_finished)
        self.install_worker.start()

    def on_import(self):
        path = QFileDialog.getExistingDirectory(self, "Select .minecraft folder")
        if not path:
            return

        versions_dir = os.path.join(path, "versions")
        if not os.path.isdir(versions_dir):
            QMessageBox.warning(self, "Warning", "No versions directory found. Is this a valid .minecraft folder?")
            return

        reply = QMessageBox.question(
            self, "Import Installation",
            f"Import Minecraft data from:\n{path}\n\nThis will copy versions, assets, and libraries.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self.stage_label.setText("Importing...")
        self.import_worker = ImportWorker(self.vm, path)
        self.import_worker.stage.connect(self.on_stage)
        self.import_worker.finished.connect(self.on_import_finished)
        self.import_worker.start()

    def on_import_modpack(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Modpack File",
            "",
            "Modpack Files (*.zip *.mrpack);;All Files (*.*)"
        )
        if not path:
            return

        try:
            self.stage_label.setText("Analyzing modpack...")
            with zipfile.ZipFile(path, "r") as zf:
                names = zf.namelist()

                manifest = None
                for candidate in ["manifest.json", "modpack.json", "minecraftinstance.json"]:
                    if candidate in names:
                        manifest = json.loads(zf.read(candidate).decode("utf-8"))
                        break

                if manifest is None:
                    QMessageBox.warning(self, "Warning", "Could not find manifest in modpack archive.")
                    return

                mc_version = None
                if "minecraft" in manifest:
                    mc_version = manifest["minecraft"].get("version")
                elif "version" in manifest:
                    mc_version = manifest.get("version")
                elif "targetVersion" in manifest:
                    mc_version = manifest["targetVersion"]

                if mc_version:
                    self.stage_label.setText(f"Modpack requires Minecraft {mc_version}")
                    self.status_label.setText(f"Modpack Minecraft version: {mc_version}")
                    reply = QMessageBox.question(
                        self, "Modpack Detected",
                        f"Modpack requires Minecraft {mc_version}.\nInstall this version now?",
                        QMessageBox.Yes | QMessageBox.No
                    )
                    if reply == QMessageBox.Yes:
                        self.install_version(mc_version)
                else:
                    self.status_label.setText(f"Modpack loaded: {path}")
                    QMessageBox.information(self, "Modpack", "Modpack loaded but Minecraft version not detected in manifest.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to read modpack: {e}")

    def on_import_finished(self, success, message):
        if success:
            QMessageBox.information(self, "Success", message)
            self.refresh_version_list()
            self.install_requested.emit("")
        else:
            QMessageBox.critical(self, "Error", message)
        self.stage_label.setText("Ready")

    def on_stage(self, msg):
        self.stage_label.setText(msg)

    def on_file_progress(self, name, downloaded, total):
        if total > 0:
            self.file_progress.setMaximum(total)
            self.file_progress.setValue(downloaded)

    def on_asset_progress(self, completed, total):
        if total > 0:
            self.asset_progress.setMaximum(total)
            self.asset_progress.setValue(completed)

    def on_lib_progress(self, completed, total):
        if total > 0:
            self.lib_progress.setMaximum(total)
            self.lib_progress.setValue(completed)

    def on_install_finished(self, success, message, version_id):
        if success:
            QMessageBox.information(self, "Success", message)
            self.refresh_version_list()
            self.install_requested.emit(version_id)
        else:
            QMessageBox.critical(self, "Error", message)
        self._current_install_id = None


class ModLoaderInstallThread(QThread):
    progress = pyqtSignal(str, int, int)
    log = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, modloader, java_path, mcversion, loader_type, data):
        super().__init__()
        self.modloader = modloader
        self.java_path = java_path
        self.mcversion = mcversion
        self.loader_type = loader_type
        self.data = data

    def run(self):
        try:
            self.log.emit(f"Starting {self.loader_type} installation...")
            if self.loader_type == "forge":
                forge_id = self.modloader.install_forge(
                    self.java_path, self.mcversion, self.data,
                    log_callback=lambda msg: self.log.emit(msg),
                    progress_callback=lambda n, d, t: self.progress.emit(n, d, t)
                )
                self.finished_signal.emit(True, forge_id)
            elif self.loader_type == "neoforge":
                neoforge_id = self.modloader.install_neoforge(
                    self.java_path, self.mcversion, self.data,
                    log_callback=lambda msg: self.log.emit(msg),
                    progress_callback=lambda n, d, t: self.progress.emit(n, d, t)
                )
                self.finished_signal.emit(True, neoforge_id)
            elif self.loader_type == "fabric":
                fabric_id = self.modloader.install_fabric(
                    self.java_path, self.mcversion, self.data.get("version"),
                    log_callback=lambda msg: self.log.emit(msg),
                    progress_callback=lambda n, d, t: self.progress.emit(n, d, t)
                )
                self.finished_signal.emit(True, fabric_id)
            elif self.loader_type == "quilt":
                quilt_id = self.modloader.install_quilt(
                    self.java_path, self.mcversion, self.data.get("version"),
                    log_callback=lambda msg: self.log.emit(msg),
                    progress_callback=lambda n, d, t: self.progress.emit(n, d, t)
                )
                self.finished_signal.emit(True, quilt_id)
            elif self.loader_type == "liteloader":
                liteloader_id = self.modloader.install_liteloader(
                    self.java_path, self.mcversion, self.data,
                    log_callback=lambda msg: self.log.emit(msg),
                    progress_callback=lambda n, d, t: self.progress.emit(n, d, t)
                )
                self.finished_signal.emit(True, liteloader_id)
            elif self.loader_type == "optifine":
                optifine_id = self.modloader.install_optifine(
                    self.java_path, self.mcversion, self.data,
                    log_callback=lambda msg: self.log.emit(msg),
                    progress_callback=lambda n, d, t: self.progress.emit(n, d, t)
                )
                self.finished_signal.emit(True, optifine_id)
            else:
                self.finished_signal.emit(False, f"Unknown loader: {self.loader_type}")
        except Exception as e:
            self.finished_signal.emit(False, str(e))