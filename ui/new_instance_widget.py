import os
import hashlib
import threading
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
                             QPushButton, QLabel, QProgressBar, QMessageBox,
                             QGroupBox, QLineEdit, QListWidgetItem, QComboBox,
                             QFrame, QSplitter, QCheckBox)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont

from core.version_manager import VersionManager, network_available, rewrite_url_for_mirror, mirror_to_origin
from core.downloader import Downloader
from core.modloader_manager import ModLoaderManager
from i18n import tr
from concurrent.futures import ThreadPoolExecutor, as_completed


ML_NAMES = {
    "": "None (Vanilla)",
    "forge": "Forge",
    "fabric": "Fabric",
    "neoforge": "NeoForge",
    "quilt": "Quilt",
    "optifine": "OptiFine",
}


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
    progress_range = pyqtSignal(int, int)
    progress_update = pyqtSignal(int)
    stage = pyqtSignal(str)
    log = pyqtSignal(str)
    finished = pyqtSignal(bool, str, str)
    side_panel_task = pyqtSignal(str, str, str)
    side_panel_progress = pyqtSignal(str, str)
    side_panel_done = pyqtSignal(str)

    def __init__(self, vm, downloader, version_id, shared_assets=True, aria2_threads=8):
        super().__init__()
        self.vm = vm
        self.downloader = downloader
        self.version_id = version_id
        self.shared_assets = shared_assets
        self.aria2_threads = aria2_threads

    def _mb(self, b):
        if b > 1048576:
            return f"{b / 1048576:.1f} MB"
        if b > 1024:
            return f"{b / 1024:.0f} KB"
        return f"{b} B"

    def _collect_all_tasks(self, version_info):
        tasks = []
        import json as _json

        jar_info = version_info.get("downloads", {}).get("client", {})
        jar_url = jar_info.get("url", "")
        jar_size = jar_info.get("size", 0)
        jar_path = self.vm.get_version_jar_path(self.version_id)
        jar_sha1 = jar_info.get("sha1", "")
        if jar_url and jar_size and not os.path.isfile(jar_path):
            tasks.append((jar_url, jar_path, jar_sha1))
            self.log.emit(f"Queued: {self.version_id}.jar ({self._mb(jar_size)})")

        libs = list(version_info.get("libraries", []))
        inherited = version_info.get("inheritsFrom")
        visited = set()
        while inherited and inherited not in visited:
            visited.add(inherited)
            parent_data = self.vm.load_version_json(inherited)
            if parent_data:
                libs.extend(parent_data.get("libraries", []))
                inherited = parent_data.get("inheritsFrom")
            else:
                break

        lib_count = 0
        for lib in libs:
            if not self.vm._should_download_lib(lib):
                continue
            path = self.vm._get_lib_path(lib)
            if os.path.isfile(path):
                continue
            artifact = lib.get("downloads", {}).get("artifact", {})
            url = artifact.get("url", "")
            sha1 = artifact.get("sha1", "")
            if not url:
                maven_rel = self.vm._maven_path(lib.get("name", ""))
                if maven_rel:
                    path = os.path.join(self.vm.libraries_dir, maven_rel)
                    origin = f"https://libraries.minecraft.net/{maven_rel}"
                    url = rewrite_url_for_mirror(origin, self.vm.preferred_mirror)
            if url and path:
                tasks.append((url, path, sha1))
                lib_count += 1
        if lib_count:
            self.log.emit(f"Queued: {lib_count} libraries")

        asset_index_id = version_info.get("assetIndex", {}).get("id")
        if not asset_index_id:
            inherited_asset = version_info.get("inheritsFrom")
            visited_asset = set()
            while inherited_asset and inherited_asset not in visited_asset:
                visited_asset.add(inherited_asset)
                parent_data = self.vm.load_version_json(inherited_asset)
                if parent_data:
                    asset_index_id = parent_data.get("assetIndex", {}).get("id")
                    if asset_index_id:
                        break
                    inherited_asset = parent_data.get("inheritsFrom")
                else:
                    break
        if not asset_index_id:
            asset_index_id = self.version_id

        asset_index_path = self.vm.get_asset_index_path(asset_index_id)
        if not os.path.exists(asset_index_path):
            try:
                self.downloader.download_asset_index(self.version_id)
            except Exception as e:
                self.log.emit(f"Asset index mirror failed: {e}, trying direct...")
                asset_index_url = version_info.get("assetIndex", {}).get("url", "")
                if not asset_index_url:
                    inherited_url = version_info.get("inheritsFrom")
                    visited_url = set()
                    while inherited_url and inherited_url not in visited_url:
                        visited_url.add(inherited_url)
                        pd = self.vm.load_version_json(inherited_url)
                        if pd:
                            asset_index_url = pd.get("assetIndex", {}).get("url", "")
                            if asset_index_url:
                                break
                            inherited_url = pd.get("inheritsFrom")
                        else:
                            break
                if asset_index_url:
                    try:
                        import requests as _req
                        r = _req.get(asset_index_url, timeout=30, verify=False)
                        r.raise_for_status()
                        os.makedirs(os.path.dirname(asset_index_path), exist_ok=True)
                        with open(asset_index_path, "w", encoding="utf-8") as f:
                            f.write(r.text)
                    except Exception as e2:
                        self.log.emit(f"Asset index download failed: {e2}")
                        asset_index_path = None
                else:
                    asset_index_path = None

        if asset_index_path and os.path.exists(asset_index_path):
            asset_count = 0
            with open(asset_index_path, "r", encoding="utf-8") as f:
                asset_data = _json.load(f)
            objects = asset_data.get("objects", {})
            for name, info in objects.items():
                h = info["hash"]
                size = info["size"]
                dest = self.vm.get_asset_object_path(h)
                if self.shared_assets and os.path.exists(dest) and os.path.getsize(dest) == size:
                        continue
                origin = f"https://resources.download.minecraft.net/{h[:2]}/{h}"
                url = rewrite_url_for_mirror(origin, self.vm.preferred_mirror)
                tasks.append((url, dest, h))
                asset_count += 1
            if asset_count:
                self.log.emit(f"Queued: {asset_count} assets")
        else:
            self.log.emit("Warning: no asset index available, sounds may be missing")

        return tasks

    def run(self):
        try:
            version_info = None
            try:
                version_info = self.vm.fetch_version_json(self.version_id)
            except Exception as e:
                self.log.emit(f"Network error fetching version JSON: {e}, trying local cache...")
                version_info = self.vm.load_version_json(self.version_id)
            if version_info is None:
                raise RuntimeError(f"Version JSON for {self.version_id} not found (network unavailable and no local cache)")
            self.side_panel_task.emit("install", "Installing " + self.version_id, "Collecting files...")

            version_dir = self.vm.get_version_game_dir(self.version_id)
            os.makedirs(version_dir, exist_ok=True)
            json_path = os.path.join(version_dir, f"{self.version_id}.json")
            import json as _json
            with open(json_path, "w", encoding="utf-8") as f:
                _json.dump(version_info, f, indent=2)

            cleaned = 0
            objects_dir = os.path.join(self.vm.assets_dir, "objects")
            if os.path.isdir(objects_dir):
                for root, dirs, files in os.walk(objects_dir):
                    for fn in files:
                        fp = os.path.join(root, fn)
                        try:
                            if os.path.getsize(fp) == 0:
                                os.remove(fp)
                                cleaned += 1
                        except OSError:
                            pass
            if cleaned > 0:
                self.log.emit(f"Cleaned {cleaned} empty/corrupt asset files from previous install")

            all_tasks = self._collect_all_tasks(version_info)
            total = len(all_tasks)
            if total == 0:
                self.side_panel_done.emit("install")
                self.log.emit(f"Version {self.version_id} already installed.")
                self.finished.emit(True, self.version_id, "")
                return

            asset_tasks = []
            non_asset_tasks = []
            _am = f"assets{os.sep}objects"
            for url, dest, sha1_val in all_tasks:
                if _am in os.path.normpath(dest):
                    asset_tasks.append((url, dest, sha1_val))
                else:
                    non_asset_tasks.append((url, dest, sha1_val))

            A_THREADS = self.aria2_threads
            aria2_per_instance = max(1, self.aria2_threads // 2)
            self.log.emit(f"Library files: {len(non_asset_tasks)} (2x aria2 {aria2_per_instance} threads each) | Assets: {len(asset_tasks)} ({A_THREADS} threads)")

            if self.downloader._aria2 and not self.downloader._aria2.is_running():
                self.log.emit(f"Starting dual aria2 engine ({aria2_per_instance}×2)...")
                self.downloader._aria2.start(max_concurrent=aria2_per_instance, num_instances=2)

            self.stage.emit(f"Downloading {total} files...")
            self.side_panel_progress.emit("install", f"Downloading {total} files...")
            self.progress_range.emit(0, total)

            def batch_progress(completed, all_count, clen, tlen, speed):
                speed_str = ""
                if speed > 0:
                    if speed > 1048576:
                        speed_str = f" {speed / 1048576:.1f} MB/s"
                    elif speed > 1024:
                        speed_str = f" {speed / 1024:.0f} KB/s"
                    else:
                        speed_str = f" {speed} B/s"
                total_mb = tlen / 1048576 if tlen > 0 else 0
                self.progress_update.emit(completed)
                self.side_panel_progress.emit("install",
                    f"{completed}/{all_count} done{speed_str}" +
                    (f" ({total_mb:.0f} MB total)" if total_mb > 0 else ""))

            url_map = {os.path.normpath(dest): url for url, dest, _ in all_tasks}
            sha1_map = {os.path.normpath(dest): sha1_val for _, dest, sha1_val in all_tasks}

            all_successes = []
            all_failures = []

            if non_asset_tasks:
                nt_total = len(non_asset_tasks)
                self.log.emit(f"Downloading {nt_total} library/jar files via aria2...")
                try:
                    s, f = self.downloader._aria2.batch_download(
                        non_asset_tasks, progress_callback=batch_progress)
                    self.downloader._aria2.clear_completed()
                    all_successes.extend(s)
                    all_failures.extend(f)
                    if f:
                        self.log.emit(f"Aria2: {len(f)}/{nt_total} library files failed")
                except Exception as aria_err:
                    self.log.emit(f"Aria2 error for libraries: {aria_err}, falling back...")
                    try:
                        self.downloader._aria2.clear_completed()
                    except Exception:
                        pass
                    lock = threading.Lock()
                    ok = [0]
                    done = [0]

                    def _dl_non_asset(u, d, s_val):
                        dn = os.path.normpath(d)
                        try:
                            if not os.path.isfile(dn) or os.path.getsize(dn) == 0:
                                self.downloader.download_file(u, d, sha1=s_val, progress_callback=None)
                            if os.path.isfile(dn) and os.path.getsize(dn) > 0:
                                all_successes.append(("", dn))
                                with lock:
                                    ok[0] += 1
                                return
                        except Exception:
                            pass
                        all_failures.append(("", dn))

                    with ThreadPoolExecutor(max_workers=8) as pool:
                        futs = [pool.submit(_dl_non_asset, u, d, s_val) for u, d, s_val in non_asset_tasks]
                        for _ in as_completed(futs):
                            with lock:
                                done[0] += 1
                            if done[0] % 50 == 0 or done[0] == nt_total:
                                self.progress_update.emit(ok[0])
                    self.log.emit(f"Library fallback: {ok[0]}/{nt_total} succeeded")

            if asset_tasks:
                at_total = len(asset_tasks)
                self.log.emit(f"Downloading {at_total} assets via {A_THREADS} threads...")
                self.side_panel_progress.emit("install", f"Assets 0/{at_total}")

                alock = threading.Lock()
                a_ok = [0]
                a_done = [0]
                a_base = len(all_successes) + len(all_failures)

                def _dl_asset(u, d, s_val):
                    dn = os.path.normpath(d)
                    try:
                        if not os.path.isfile(dn) or os.path.getsize(dn) == 0:
                            self.downloader.download_file(u, d, sha1=s_val, progress_callback=None,
                                                          use_aria2=False)
                        if os.path.isfile(dn) and os.path.getsize(dn) > 0:
                            all_successes.append(("", dn))
                            with alock:
                                a_ok[0] += 1
                            return
                    except Exception:
                        pass
                    all_failures.append(("", dn))

                with ThreadPoolExecutor(max_workers=A_THREADS) as pool:
                    futs = [pool.submit(_dl_asset, u, d, s_val) for u, d, s_val in asset_tasks]
                    for _ in as_completed(futs):
                        with alock:
                            a_done[0] += 1
                        if a_done[0] % 200 == 0 or a_done[0] == at_total:
                            self.progress_update.emit(a_base + a_ok[0] + len(all_failures))
                            pct = a_done[0] * 100 // at_total
                            self.side_panel_progress.emit("install",
                                f"Assets {a_done[0]}/{at_total} ({pct}%)")

                self.log.emit(f"Assets: {a_ok[0]}/{at_total} succeeded, {len(all_failures)} total failures")

            failed = len(all_failures)
            if failed > 0:
                self.log.emit(f"Retrying {failed} failed items...")
                self.side_panel_progress.emit("install", f"Retrying {failed} items...")

                retry_items = []
                for gid, dest_path in all_failures:
                    dest_norm = os.path.normpath(dest_path)
                    if os.path.isfile(dest_norm) and os.path.getsize(dest_norm) > 0:
                        continue
                    retry_url = url_map.get(dest_norm, "")
                    if retry_url:
                        origin_url = mirror_to_origin(retry_url)
                        asset_sha1 = sha1_map.get(dest_norm, "")
                        retry_items.append((retry_url, origin_url, dest_norm, asset_sha1))

                def _retry_one(url, origin_url, dest, sha1_val):
                    try:
                        self.downloader.download_file(
                            url, dest, sha1=sha1_val, progress_callback=None, fallback_url=origin_url)
                        return os.path.isfile(dest) and os.path.getsize(dest) > 0
                    except Exception:
                        return False

                if retry_items:
                    ok = 0
                    lk = threading.Lock()
                    with ThreadPoolExecutor(max_workers=32) as pool:
                        futures = {pool.submit(_retry_one, u, o, d, s): i
                                   for i, (u, o, d, s) in enumerate(retry_items)}
                        for future in as_completed(futures):
                            if future.result():
                                with lk:
                                    ok += 1
                    self.log.emit(f"Retry: {ok}/{len(retry_items)} recovered")
                else:
                    self.log.emit("All failed items already have valid files on disk.")

            self.progress_update.emit(total)
            self.side_panel_done.emit("install")

            still_missing = []
            for url, dest, sha1_val in all_tasks:
                dest_norm = os.path.normpath(dest)
                if not os.path.isfile(dest_norm) or os.path.getsize(dest_norm) == 0:
                    still_missing.append((url, dest_norm, sha1_val))
            if still_missing:
                self.log.emit(f"Final pass: {len(still_missing)} files missing, trying origin...")
                ok = 0
                for m_url, m_dest, m_sha1 in still_missing:
                    try:
                        origin_url = mirror_to_origin(m_url)
                        self.downloader.download_file(
                            origin_url, m_dest, sha1=m_sha1, progress_callback=None)
                        if os.path.isfile(m_dest) and os.path.getsize(m_dest) > 0:
                            ok += 1
                    except Exception:
                        pass
                self.log.emit(f"Final pass: {ok}/{len(still_missing)} recovered from origin")

            self.log.emit(f"Version {self.version_id} installed.")
            self.finished.emit(True, self.version_id, "")
        except Exception as e:
            self.side_panel_done.emit("install")
            self.log.emit(f"Install error: {e}")
            self.finished.emit(False, self.version_id, str(e))


class MLFetchWorker(QThread):
    finished = pyqtSignal(str, list)
    log = pyqtSignal(str)

    def __init__(self, mlm, ml_type, mcversion):
        super().__init__()
        self.mlm = mlm
        self.ml_type = ml_type
        self.mcversion = mcversion

    def run(self):
        try:
            if self.ml_type == "forge":
                result = self.mlm.get_forge_builds(self.mcversion)
            elif self.ml_type == "neoforge":
                result = self.mlm.get_neoforge_versions(self.mcversion)
            elif self.ml_type == "fabric":
                result = self.mlm.get_fabric_loader_versions()
            elif self.ml_type == "quilt":
                result = self.mlm.get_quilt_loader_versions()
            elif self.ml_type == "optifine":
                result = self.mlm.get_optifine_versions(self.mcversion)
            else:
                result = []
            self.finished.emit(self.ml_type, result)
        except Exception as e:
            self.log.emit(f"Error fetching {self.ml_type} versions: {e}")
            self.finished.emit(self.ml_type, [])


class NewInstanceWidget(QWidget):
    install_requested = pyqtSignal(str)
    modloader_install_requested = pyqtSignal(str, str, object, str)
    console_log = pyqtSignal(str)
    side_panel_task = pyqtSignal(str, str, str)
    side_panel_progress = pyqtSignal(str, str)
    side_panel_done = pyqtSignal(str)

    def __init__(self, vm, downloader, parent=None):
        super().__init__(parent)
        self.vm = vm
        self.downloader = downloader
        self.mlm = ModLoaderManager(self.vm, self.downloader)
        self._java_path = "java"
        self._ml_data = {}
        self._aria2_threads = 8
        self._init_ui()

    def set_java_path(self, path):
        self._java_path = path

    def set_aria2_threads(self, num):
        self._aria2_threads = max(1, int(num))

    def set_version(self, version_id):
        for i in range(self.version_list.count()):
            if self.version_list.item(i).text() == version_id:
                self.version_list.setCurrentRow(i)
                break

    def _init_ui(self):
        splitter = QSplitter(Qt.Horizontal)

        left_frame = QFrame()
        left_layout = QVBoxLayout(left_frame)
        left_layout.setContentsMargins(0, 0, 0, 0)

        header_row = QHBoxLayout()
        header_row.addWidget(QLabel(tr("Minecraft Versions")))
        self.refresh_btn = QPushButton(tr("Refresh List"))
        self.refresh_btn.clicked.connect(self._on_refresh)
        header_row.addWidget(self.refresh_btn)
        left_layout.addLayout(header_row)

        filter_row = QHBoxLayout()
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filter (e.g. 1.21, snapshot)...")
        self.filter_input.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.filter_input)
        left_layout.addLayout(filter_row)

        self.status_label = QLabel(tr(""))
        self.status_label.setStyleSheet("color: #606068; font-size: 11px;")
        left_layout.addWidget(self.status_label)

        self.version_list = QListWidget()
        self.version_list.currentItemChanged.connect(self._on_version_selected)
        left_layout.addWidget(self.version_list, 1)

        splitter.addWidget(left_frame)

        right_frame = QFrame()
        right_layout = QVBoxLayout(right_frame)
        right_layout.setContentsMargins(8, 0, 0, 0)

        config_group = QGroupBox("Instance Configuration")
        cg_layout = QVBoxLayout(config_group)
        cg_layout.setSpacing(6)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel(tr("Name:")))
        self.instance_name = QLineEdit()
        self.instance_name.setPlaceholderText("Auto-generated from selection")
        name_row.addWidget(self.instance_name, 1)
        cg_layout.addLayout(name_row)

        info_label = QLabel(tr(""))
        info_label.setStyleSheet("color: #606068; font-size: 11px;")
        cg_layout.addWidget(info_label)
        self.version_info_label = info_label

        ml_row = QHBoxLayout()
        ml_row.addWidget(QLabel(tr("Mod Loader:")))
        self.ml_combo = QComboBox()
        self.ml_combo.setMinimumHeight(26)
        for key, name in ML_NAMES.items():
            self.ml_combo.addItem(name, key)
        self.ml_combo.currentIndexChanged.connect(self._on_ml_changed)
        ml_row.addWidget(self.ml_combo, 1)
        cg_layout.addLayout(ml_row)

        mlver_row = QHBoxLayout()
        mlver_row.addWidget(QLabel(tr("ML Version:")))
        self.ml_ver_combo = QComboBox()
        self.ml_ver_combo.setMinimumHeight(26)
        self.ml_ver_combo.setEnabled(False)
        mlver_row.addWidget(self.ml_ver_combo, 1)
        cg_layout.addLayout(mlver_row)

        self.ml_status = QLabel(tr(""))
        self.ml_status.setStyleSheet("color: #606068; font-size: 11px;")
        cg_layout.addWidget(self.ml_status)

        self.shared_assets_cb = QCheckBox(tr("Share assets with existing versions"))
        self.shared_assets_cb.setChecked(True)
        self.shared_assets_cb.setToolTip(tr("Skip downloading assets already present from other installations. Uncheck to force re-download all assets."))
        cg_layout.addWidget(self.shared_assets_cb)

        right_layout.addWidget(config_group)

        self.create_btn = QPushButton(tr("Create Instance"))
        self.create_btn.setStyleSheet(
            "QPushButton { background: #282830; color: #e8e8e8; font-size: 12px; padding: 9px; border: 1px solid #383840; letter-spacing: 2px; font-weight: bold; }"
            "QPushButton:hover { background: #32323c; border-color: #4a4a54; }"
            "QPushButton:disabled { background: #1a1a1e; color: #505050; border-color: #222228; }"
        )
        self.create_btn.clicked.connect(self._on_create)
        self.create_btn.setEnabled(False)
        right_layout.addWidget(self.create_btn)

        progress_group = QGroupBox("Progress")
        pg_layout = QVBoxLayout(progress_group)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        pg_layout.addWidget(self.progress_bar)

        self.log_label = QLabel(tr(""))
        self.log_label.setWordWrap(True)
        self.log_label.setStyleSheet("color: #909098; font-size: 11px;")
        pg_layout.addWidget(self.log_label)
        right_layout.addWidget(progress_group)

        right_layout.addStretch()
        splitter.addWidget(right_frame)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(splitter)

    def refresh_version_list(self):
        versions = self.vm.get_version_list()
        self.version_list.clear()
        filter_text = self.filter_input.text().lower()

        for v in versions:
            if filter_text and filter_text not in v["id"].lower():
                continue
            item = QListWidgetItem()
            item.setText(v["id"])
            item.setData(Qt.UserRole, v)
            tag = v.get("type", "release")
            if tag == "demo":
                continue
            if tag == "snapshot":
                item.setForeground(Qt.yellow)
            elif tag == "old_beta" or tag == "old_alpha":
                item.setForeground(Qt.gray)
            self.version_list.addItem(item)

        release_count = sum(1 for v in versions if v.get("type") == "release")
        snapshot_count = sum(1 for v in versions if v.get("type") == "snapshot")
        total = len(versions)
        self.status_label.setText(
            f"{release_count} releases, {snapshot_count} snapshots ({total} total)")

    def _apply_filter(self):
        self.refresh_version_list()

    def _on_refresh(self):
        if not network_available():
            QMessageBox.information(self, tr("No Network"),
                                    "No internet connection detected. Please connect and try again.")
            return

        self.status_label.setText(tr("Loading version list..."))
        self.refresh_btn.setEnabled(False)
        self._manifest_worker = FetchManifestWorker(self.vm)
        self._manifest_worker.finished.connect(self._on_manifest_ready)
        self._manifest_worker.start()

    def _on_manifest_ready(self, success, error):
        self.refresh_btn.setEnabled(True)
        if success:
            self.refresh_version_list()
            self.console_log.emit("Version list refreshed.")
        else:
            self.status_label.setText(f"Error: {error[:100]}")
            QMessageBox.critical(self, tr("Error"), f"Failed to load version list:\n{error}")

    def _on_version_selected(self, current, previous):
        if current is None:
            self.create_btn.setEnabled(False)
            self.instance_name.clear()
            self.version_info_label.clear()
            return

        vdata = current.data(Qt.UserRole)
        if vdata is None:
            self.create_btn.setEnabled(False)
            return

        vid = vdata["id"]
        installed = vid in self.vm.get_installed_versions()
        tag = vdata.get("type", "release")
        release = vdata.get("releaseTime", "")[:10]

        status = "Installed" if installed else "Not installed"
        self.version_info_label.setText(
            f"{vid} | {tag} | {release} | [{status}]")
        self.version_info_label.setStyleSheet(
            "color: #6a8a6a; font-size: 11px;" if installed else
            "color: #606068; font-size: 11px;")

        self.create_btn.setEnabled(True)
        self._auto_name(vid, installed)
        self._on_ml_changed()

    def _auto_name(self, vid, installed):
        ml_key = self.ml_combo.currentData()
        ml_info = self._get_current_ml_info()
        if ml_key and ml_info:
            ml_ver = ml_info.get("version", ml_info.get("tag", ""))
            name = f"{vid}-{ml_key}-{ml_ver}" if ml_ver else f"{vid}-{ml_key}"
        else:
            name = vid
        self.instance_name.setPlaceholderText(name)
        if not self.instance_name.text() or installed:
            self.instance_name.setText(tr(""))
        else:
            pass

    def _on_ml_changed(self):
        ml_key = self.ml_combo.currentData()
        self.ml_ver_combo.clear()
        current = self.version_list.currentItem()
        if current is None or not ml_key or ml_key == "":
            self.ml_ver_combo.setEnabled(False)
            self.ml_status.setText(tr(""))
            self._auto_name("", False)
            return

        vdata = current.data(Qt.UserRole)
        if vdata is None:
            return
        mcversion = vdata["id"]

        self.ml_ver_combo.setEnabled(True)
        self.ml_ver_combo.clear()
        self.ml_ver_combo.addItem("Loading...", None)

        self._ml_fetch = MLFetchWorker(self.mlm, ml_key, mcversion)
        self._ml_fetch.finished.connect(self._on_ml_loaded)
        self._ml_fetch.log.connect(self.console_log.emit)
        self._ml_fetch.start()

    def _on_ml_loaded(self, ml_type, data):
        if ml_type != self.ml_combo.currentData():
            return

        self.ml_ver_combo.clear()
        current = self.version_list.currentItem()
        mcversion = current.data(Qt.UserRole)["id"] if current else ""

        if not data:
            self.ml_ver_combo.addItem(f"No versions for MC {mcversion}", None)
            self.ml_status.setText(f"No {ml_type} builds for {mcversion}")
            return

        if ml_type == "forge":
            for b in data[:50]:
                label = b.get("version", str(b.get("build", "?")))
                self.ml_ver_combo.addItem(label, b)
            self.ml_status.setText(f"{len(data)} Forge builds for {mcversion}")
        elif ml_type == "neoforge":
            for b in data:
                v = b.get("version", "?")
                self.ml_ver_combo.addItem(v, b)
            self.ml_status.setText(f"{len(data)} NeoForge builds for {mcversion}")
        elif ml_type == "fabric":
            for b in data:
                v = b.get("version", "?")
                self.ml_ver_combo.addItem(v, b)
            self.ml_status.setText(f"{len(data)} Fabric loader versions")
        elif ml_type == "quilt":
            for b in data:
                v = b.get("version", "?")
                self.ml_ver_combo.addItem(v, b)
            self.ml_status.setText(f"{len(data)} Quilt loader versions")
        elif ml_type == "optifine":
            for b in data:
                v = b.get("version", "?")
                self.ml_ver_combo.addItem(v, b)
            self.ml_status.setText(f"{len(data)} OptiFine builds for {mcversion}")

        self.ml_ver_combo.currentIndexChanged.connect(
            lambda: self._on_ml_version_selected(mcversion))

        self._on_ml_version_selected(mcversion)

    def _on_ml_version_selected(self, mcversion):
        del mcversion
        vid = self.version_list.currentItem().data(Qt.UserRole)["id"] if self.version_list.currentItem() else ""
        installed = vid in self.vm.get_installed_versions()
        self._auto_name(vid, installed)

    def _get_current_ml_info(self):
        idx = self.ml_ver_combo.currentIndex()
        if idx < 0:
            return None
        return self.ml_ver_combo.itemData(idx)

    def reinstall_version(self, version_id):
        self._version_id = version_id
        self._ml_key = None
        self._ml_info = None
        self._phase = "install_mc"

        self.create_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.log_label.setText(f"Reinstalling Minecraft {version_id}...")
        self.console_log.emit(f"Reinstalling Minecraft {version_id}...")

        self._worker = InstallWorker(self.vm, self.downloader, version_id,
                                          self.shared_assets_cb.isChecked(),
                                          aria2_threads=self._aria2_threads)
        self._worker.log.connect(self._on_worker_log)
        self._worker.stage.connect(self._on_worker_stage)
        self._worker.progress_range.connect(self._on_progress_range)
        self._worker.progress_update.connect(self._on_progress_update)
        self._worker.finished.connect(self._on_mc_installed)
        self._worker.side_panel_task.connect(self.side_panel_task)
        self._worker.side_panel_progress.connect(self.side_panel_progress)
        self._worker.side_panel_done.connect(self.side_panel_done)
        self._worker.start()

    def _on_create(self):
        current = self.version_list.currentItem()
        if current is None:
            return

        vdata = current.data(Qt.UserRole)
        version_id = vdata["id"]
        ml_key = self.ml_combo.currentData()
        ml_info = self._get_current_ml_info()

        if ml_key == "":
            ml_key = None

        instance_name = self.instance_name.text().strip() or self.instance_name.placeholderText()

        self.create_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.log_label.setText(tr("Checking prerequisites..."))

        self._version_id = version_id
        self._ml_key = ml_key
        self._ml_info = ml_info
        self._phase = "check"

        installed = version_id in self.vm.get_installed_versions()
        if not installed:
            self._phase = "install_mc"
            self.log_label.setText(f"Downloading Minecraft {version_id}...")
            self.console_log.emit(f"Installing Minecraft {version_id}...")

            self._worker = InstallWorker(self.vm, self.downloader, version_id,
                                              self.shared_assets_cb.isChecked(),
                                              aria2_threads=self._aria2_threads)
            self._worker.log.connect(self._on_worker_log)
            self._worker.stage.connect(self._on_worker_stage)
            self._worker.progress_range.connect(self._on_progress_range)
            self._worker.progress_update.connect(self._on_progress_update)
            self._worker.finished.connect(self._on_mc_installed)
            self._worker.side_panel_task.connect(self.side_panel_task)
            self._worker.side_panel_progress.connect(self.side_panel_progress)
            self._worker.side_panel_done.connect(self.side_panel_done)
            self._worker.start()
        else:
            self.console_log.emit(f"Minecraft {version_id} already installed.")
            self._on_mc_installed(True, version_id, "")

    def _on_worker_log(self, msg):
        self.console_log.emit(msg)

    def _on_worker_stage(self, msg):
        self.log_label.setText(msg)

    def _on_progress_range(self, min_val, max_val):
        self.progress_bar.setRange(min_val, max_val)
        self.progress_bar.setValue(min_val)

    def _on_progress_update(self, val):
        if self.progress_bar.maximum() > 0:
            self.progress_bar.setValue(val)

    def _on_mc_installed(self, success, version_id, error):
        if not success:
            self.log_label.setText(f"Error: {error[:200]}")
            self.create_btn.setEnabled(True)
            self.progress_bar.setVisible(False)
            QMessageBox.critical(self, tr("Install Error"), error[:500])
            return

        self.install_requested.emit(version_id)
        self.console_log.emit(f"Minecraft {version_id} installed successfully.")

        ml_key = self._ml_key
        if not ml_key:
            self.progress_bar.setVisible(False)
            self.log_label.setText(f"Instance '{version_id}' created successfully!")
            self.create_btn.setEnabled(True)
            self.console_log.emit(f"Done! Instance '{version_id}' ready to launch.")
            return

        ml_info = self._ml_info
        ml_name = ML_NAMES.get(ml_key, ml_key)

        self.log_label.setText(f"Switching to Instances for {ml_name} setup...")
        self.console_log.emit(f"Preparing {ml_name} install for {version_id} on Instances page...")

        loader_ver = None
        if ml_key in ("fabric", "quilt"):
            if isinstance(ml_info, dict):
                loader_ver = ml_info.get("version", "")
            elif isinstance(ml_info, str):
                loader_ver = ml_info

        self.modloader_install_requested.emit(
            version_id, ml_key,
            ml_info if ml_key in ("forge", "neoforge", "optifine") else None,
            loader_ver or "")

        self.progress_bar.setVisible(False)
        self.log_label.setText(f"Instance '{version_id}' ready for {ml_name} install.")
        self.create_btn.setEnabled(True)