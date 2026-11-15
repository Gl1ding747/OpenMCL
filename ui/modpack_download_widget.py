import os
import json
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
                             QPushButton, QLabel, QListWidget, QListWidgetItem,
                             QProgressBar, QMessageBox, QGroupBox,
                             QComboBox, QSplitter, QFrame, QInputDialog)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSize, QTimer
from PyQt5.QtGui import QFont, QPixmap, QIcon
from PyQt5 import sip

from ui.mod_download_widget import (ModSearchThread, ModPageView,
                                    ModpackInstallThread, DOWNLOAD_CACHE,
                                    IconLoadThread, _extract_base)
import shutil


class ModpackDownloadWidget(QWidget):
    console_log = pyqtSignal(str)
    instance_created = pyqtSignal(str, str, str)

    def __init__(self, vm, aria2_manager=None, parent=None):
        super().__init__(parent)
        self.vm = vm
        self.aria2 = aria2_manager
        self._results = []
        self._current_project = None
        self._current_versions = []
        self._current_selected_idx = 0
        self._is_initial_loaded = False
        self._active_threads = []
        self._mp_threads = []
        if DOWNLOAD_CACHE.exists():
            shutil.rmtree(str(DOWNLOAD_CACHE), ignore_errors=True)
        os.makedirs(os.path.join(os.path.expanduser("~"), ".openmcl", "cache", "downloads"), exist_ok=True)
        self._init_ui()

    def _track_thread(self, thread):
        self._active_threads[:] = [t for t in self._active_threads if t.isRunning()]
        self._active_threads.append(thread)

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        search_group = QGroupBox("Search Modpacks")
        search_layout = QVBoxLayout(search_group)

        top_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search modpacks on Modrinth...")
        self.search_input.returnPressed.connect(self._on_search)
        top_row.addWidget(self.search_input, 1)

        self.version_filter = QComboBox()
        self.version_filter.setEditable(True)
        self.version_filter.setMinimumWidth(100)
        self.version_filter.currentIndexChanged.connect(self._on_filter_changed)
        self._refresh_versions()
        top_row.addWidget(self.version_filter)

        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self._on_search)
        top_row.addWidget(self.search_btn)

        search_layout.addLayout(top_row)
        root.addWidget(search_group)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(5)
        splitter.setChildrenCollapsible(False)

        left_panel = QFrame()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.result_list = QListWidget()
        self.result_list.setIconSize(QSize(48, 48))
        self.result_list.currentRowChanged.connect(self._on_result_selected)
        left_layout.addWidget(self.result_list)

        self.status_label = QLabel("Loading popular modpacks...")
        self.status_label.setWordWrap(True)
        left_layout.addWidget(self.status_label)

        splitter.addWidget(left_panel)

        right_panel = QFrame()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 0, 0, 0)

        self.mod_view = ModPageView()
        self.mod_view.action_requested.connect(self._on_web_action)
        right_layout.addWidget(self.mod_view, 1)

        self.version_label = QLabel("Versions:")
        self.version_label.setStyleSheet("margin-top: 4px; font-weight: bold;")
        right_layout.addWidget(self.version_label)

        self.version_list = QListWidget()
        self.version_list.setMaximumHeight(120)
        self.version_list.currentRowChanged.connect(self._on_version_selected)
        right_layout.addWidget(self.version_list)

        self.version_desc_label = QLabel("")
        self.version_desc_label.setWordWrap(True)
        self.version_desc_label.setMaximumHeight(50)
        right_layout.addWidget(self.version_desc_label)

        self.install_btn = QPushButton("Install Modpack")
        self.install_btn.clicked.connect(self._install_modpack)
        self.install_btn.setMinimumHeight(34)
        self.install_btn.setStyleSheet(
            "QPushButton { background: #282830; color: #e8e8e8; border: 1px solid #383840; padding: 8px 18px; font-weight: bold; letter-spacing: 2px; }"
            "QPushButton:hover { background: #32323c; border-color: #4a4a54; }"
            "QPushButton:pressed { background: #1e1e24; }"
            "QPushButton:disabled { background: #1a1a1e; color: #505050; border-color: #222228; }"
        )
        right_layout.addWidget(self.install_btn)

        splitter.addWidget(right_panel)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([280, 560])
        root.addWidget(splitter, 1)

        self.dl_container = QVBoxLayout()
        self.dl_container.setContentsMargins(0, 0, 0, 0)
        root.addLayout(self.dl_container)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(50, self._ensure_initial_load)

    def _ensure_initial_load(self):
        if self._is_initial_loaded:
            return
        self._is_initial_loaded = True
        self._load_popular()

    def _load_popular(self):
        try:
            mc_version = self.version_filter.currentData()
            self.status_label.setText("Loading popular modpacks...")

            self._auto_load = True
            if hasattr(self, '_search_thread') and self._search_thread is not None:
                self._search_thread.finished.disconnect(self._on_search_done)
            self._search_thread = ModSearchThread(
                "", mc_version, "", index="downloads", project_type="modpack")
            self._search_thread.finished.connect(self._on_search_done)
            self._track_thread(self._search_thread)
            self._search_thread.start()
        except Exception as e:
            self.status_label.setText(f"Load failed: {e}")
            self.console_log.emit(f"Modpack load error: {e}")

    def _refresh_versions(self):
        current = self.version_filter.currentText()
        self.version_filter.clear()
        self.version_filter.addItem("Any Version", "")
        installed = self.vm.get_installed_versions()
        seen = set()
        for vid in installed:
            base = _extract_base(vid)
            if base not in seen:
                seen.add(base)
                self.version_filter.addItem(base, base)
        if current:
            idx = self.version_filter.findText(current)
            if idx >= 0:
                self.version_filter.setCurrentIndex(idx)

    def _on_filter_changed(self):
        if not self._is_initial_loaded:
            return
        self._load_popular()

    def _on_search(self):
        query = self.search_input.text().strip()
        mc_version = self.version_filter.currentData()
        self.status_label.setText("Searching...")
        self.result_list.clear()
        self._results = []

        self._auto_load = False
        if hasattr(self, '_search_thread') and self._search_thread is not None:
            self._search_thread.finished.disconnect(self._on_search_done)
        self._search_thread = ModSearchThread(
            query, mc_version, "", project_type="modpack")
        self._search_thread.finished.connect(self._on_search_done)
        self._track_thread(self._search_thread)
        self._search_thread.start()

    def _on_search_done(self, hits, error):
        try:
            self._handle_search_done(hits, error)
        except Exception as e:
            self.status_label.setText(f"Display error: {e}")
            self.console_log.emit(f"Modpack display error: {e}")

    def _handle_search_done(self, hits, error):
        if error:
            self.status_label.setText(f"Search error: {error}")
            self.console_log.emit(f"Modpack search error: {error}")
            return

        self._results = hits
        self.result_list.clear()

        if not hits:
            self.status_label.setText("No modpacks found.")
            return

        self.status_label.setText(f"Found {len(hits)} modpacks.")
        for i, hit in enumerate(hits):
            title = hit.get("title", "Unknown")
            icon_url = hit.get("icon_url", "")
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, i)
            item.setToolTip(hit.get("description", ""))
            if icon_url:
                self._load_item_icon(item, icon_url)
            self.result_list.addItem(item)

    def _load_item_icon(self, item, url):
        thread = IconLoadThread(url)
        thread.finished_with_icon.connect(
            lambda i=item, t=thread: self._apply_icon(i, t))
        self._track_thread(thread)
        thread.start()
        item.setData(Qt.UserRole + 1, thread)

    def _apply_icon(self, item, thread):
        if sip.isdeleted(item):
            return
        if thread.data:
            pix = QPixmap()
            pix.loadFromData(thread.data)
            if not pix.isNull():
                item.setIcon(QIcon(pix.scaled(
                    48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)))

    def _on_result_selected(self, idx):
        if idx < 0 or idx >= len(self._results):
            return
        project = self._results[idx]
        self._current_project = project
        self._current_versions = []
        self._current_selected_idx = 0
        self.version_list.clear()
        self.version_desc_label.setText("")
        self._show_project_detail(project)
        slug = project.get("slug", "")
        if not slug:
            return
        self._fetch_project_versions(slug)

    def _show_project_detail(self, project):
        mc_version = self.version_filter.currentData() or ""
        self.mod_view.show_project(project, [], mc_version, "")

    def _fetch_project_versions(self, slug):
        class VersionFetchThread(QThread):
            finished = pyqtSignal(list, str)

            def __init__(self, slug):
                super().__init__()
                self.slug = slug

            def run(self):
                import requests
                try:
                    url = f"https://api.modrinth.com/v2/project/{self.slug}/version"
                    resp = requests.get(url, timeout=15,
                                        headers={"User-Agent": "OpenMCL/1.0"})
                    resp.raise_for_status()
                    data = resp.json()
                    self.finished.emit(data, "")
                except Exception as e:
                    self.finished.emit([], str(e))

        if hasattr(self, '_vf_thread') and self._vf_thread is not None:
            self._vf_thread.finished.disconnect(self._on_versions_fetched)
        self._vf_thread = VersionFetchThread(slug)
        self._vf_thread.finished.connect(self._on_versions_fetched)
        self._track_thread(self._vf_thread)
        self._vf_thread.start()

    def _on_versions_fetched(self, versions, error):
        if error:
            self.console_log.emit(f"Version fetch: {error}")
            return
        self._current_versions = versions
        self.version_list.clear()
        for v in versions:
            name = v.get("name", "Unknown")
            game_versions = v.get("game_versions", [])
            loaders = v.get("loaders", [])
            label = f"{name}"
            if game_versions:
                label += f"  [MC {'/'.join(game_versions[:2])}]"
            if loaders:
                label += f"  ({'/'.join(loaders[:2])})"
            self.version_list.addItem(label)
        if versions:
            self.version_list.setCurrentRow(0)

    def _on_version_selected(self, idx):
        if idx < 0 or idx >= len(self._current_versions):
            return
        self._current_selected_idx = idx
        v = self._current_versions[idx]
        changelog = v.get("changelog", "") or ""
        game_versions = v.get("game_versions", [])
        loaders = v.get("loaders", [])
        info = f"MC: {', '.join(game_versions[:3])}  |  Loader: {', '.join(loaders[:3])}"
        self.version_desc_label.setText(info)

    def _on_web_action(self, action_type, data):
        if action_type == "install-modpack":
            self._install_modpack()
        elif action_type == "show-version-list":
            pass

    def _install_modpack(self):
        if not self._current_versions:
            QMessageBox.warning(
                self, "No Version", "Select a modpack version first.")
            return

        version_data = self._current_versions[self._current_selected_idx]
        files = version_data.get("files", [])
        if not files:
            QMessageBox.warning(self, "No File", "No file available.")
            return

        main_file = files[0]
        mrpack_url = main_file.get("url", "")
        if not mrpack_url:
            QMessageBox.warning(
                self, "No URL", "Modpack download URL not found.")
            return

        game_versions = version_data.get("game_versions", [])
        loaders = version_data.get("loaders", [])
        mc_ver = game_versions[0] if game_versions else "unknown"
        loader_ver = loaders[0] if loaders else "vanilla"
        pack_name = self._current_project.get("title", "modpack")

        instance_name, ok = QInputDialog.getText(
            self, "Install Modpack",
            f"Pack: {pack_name}\nMC: {mc_ver}  Loader: {loader_ver}\n\n"
            f"Instance name:",
            text=pack_name.replace(" ", "_").lower())
        if not ok or not instance_name.strip():
            return

        instance_name = instance_name.strip()
        target_dir = os.path.join(
            os.path.expanduser("~"), ".openmcl", "versions", instance_name)
        if os.path.exists(target_dir):
            QMessageBox.warning(
                self, "Exists",
                f"Instance '{instance_name}' already exists.")
            return

        pw = QWidget()
        pw_layout = QHBoxLayout(pw)
        pw_layout.setContentsMargins(4, 2, 4, 2)
        pw_name = QLabel(instance_name)
        pw_name.setMinimumWidth(120)
        pw_name.setStyleSheet("font-weight: bold;")
        pw_layout.addWidget(pw_name)
        pw_bar = QProgressBar()
        pw_bar.setMaximum(0)
        pw_layout.addWidget(pw_bar, 1)
        pw_status = QLabel("Starting...")
        pw_status.setMinimumWidth(240)
        pw_layout.addWidget(pw_status)
        pw_cancel = QPushButton("✕")
        pw_cancel.setFixedWidth(28)
        pw_cancel.setFixedHeight(22)
        pw_cancel.setToolTip("Cancel installation")
        pw_cancel.setStyleSheet(
            "QPushButton { border-radius: 0px; background-color: #3a2020; color: #e57373; border: 1px solid rgba(255,255,255,0.10); font-weight: bold; }"
            "QPushButton:hover { background-color: #4a2828; border-color: rgba(255,255,255,0.20); }")
        pw_layout.addWidget(pw_cancel)
        self.dl_container.addWidget(pw)

        thread = ModpackInstallThread(
            mrpack_url, mc_ver, loader_ver, "",
            instance_name, self.vm, self.aria2)
        thread._loader_type = loader_ver
        thread._mc_ver = mc_ver
        thread._instance_name = instance_name
        thread._pw = pw
        thread._pw_bar = pw_bar
        thread._pw_status = pw_status
        thread._pw_cancel = pw_cancel
        pw_cancel.clicked.connect(thread.cancel)
        thread.progress_signal.connect(self._on_mp_progress)
        thread.finished_signal.connect(self._on_mp_done)
        self._mp_threads.append(thread)
        self._track_thread(thread)
        thread.start()

    def _on_mp_progress(self, ok, total, text):
        thread = self.sender()
        if thread is None:
            return
        if total > 0:
            thread._pw_bar.setMaximum(total)
            thread._pw_bar.setValue(ok)
        else:
            thread._pw_bar.setMaximum(0)
        thread._pw_status.setText(text)

    def _on_mp_done(self, success, target_dir, message):
        thread = self.sender()
        if thread is None:
            return
        loader_type = getattr(thread, '_loader_type', 'vanilla')
        mc_ver = getattr(thread, '_mc_ver', '')
        instance_name = getattr(thread, '_instance_name', '')
        if message == "Cancelled":
            self.console_log.emit(
                f"Modpack installation cancelled: {instance_name}")
        elif success:
            self.console_log.emit(f"Modpack installed: {message}")
            QMessageBox.information(self, "Done", message)
            self.instance_created.emit(instance_name, loader_type, mc_ver)
        else:
            self.console_log.emit(f"Modpack install failed: {message}")
            QMessageBox.critical(self, "Failed", message)

        if hasattr(thread, '_pw'):
            thread._pw.setVisible(False)
            self.dl_container.removeWidget(thread._pw)
            thread._pw.deleteLater()
        if thread in self._mp_threads:
            self._mp_threads.remove(thread)