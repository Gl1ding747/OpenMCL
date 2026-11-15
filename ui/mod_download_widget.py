import os
import json
import shutil
import zipfile
from PyQt5 import sip
import uuid
import time
import sys
import traceback
import tempfile
from pathlib import Path

import requests
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
                             QPushButton, QLabel, QListWidget, QListWidgetItem,
                             QProgressBar, QMessageBox, QGroupBox,
                             QComboBox, QSplitter, QFrame, QCheckBox)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QUrl, QSize, QTimer
from PyQt5.QtGui import QFont, QDesktopServices, QPixmap, QIcon
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage


# ── Constants ────────────────────────────────────────

MODRINTH_API = "https://api.modrinth.com/v2"
MODRINTH_CDN = "https://cdn.modrinth.com"
DOWNLOAD_CACHE = Path.home() / ".openmcl" / "cache" / "downloads"

LOADER_MAP = {
    "forge": "forge",
    "fabric": "fabric",
    "neoforge": "neoforge",
    "quilt": "quilt",
    "": None,
}


# ── Threads ──────────────────────────────────────────

class ModSearchThread(QThread):
    finished = pyqtSignal(list, str)

    def __init__(self, query, mc_version, loader, index="relevance",
                 project_type=None):
        super().__init__()
        self.query = query
        self.mc_version = mc_version
        self.loader = loader
        self.index = index
        self.project_type = project_type

    def run(self):
        try:
            facets = []
            if self.project_type:
                facets.append([f"project_type:{self.project_type}"])
            if self.mc_version:
                facets.append([f"versions:{self.mc_version}"])
            if self.loader:
                facets.append([f"categories:{self.loader}"])

            params = {
                "query": self.query or "",
                "limit": 30,
                "offset": 0,
                "index": self.index,
            }
            if facets:
                params["facets"] = json.dumps(facets)

            resp = requests.get(
                f"{MODRINTH_API}/search",
                params=params,
                timeout=30,
                headers={"User-Agent": "OpenMCL/1.0"},
            )
            resp.raise_for_status()
            data = resp.json()
            self.finished.emit(data.get("hits", []), "")
        except Exception as e:
            self.finished.emit([], str(e))


class ModDetailThread(QThread):
    finished = pyqtSignal(dict, list, str)

    def __init__(self, project_id):
        super().__init__()
        self.project_id = project_id

    def run(self):
        try:
            resp = requests.get(
                f"{MODRINTH_API}/project/{self.project_id}",
                timeout=30,
                headers={"User-Agent": "OpenMCL/1.0"},
            )
            resp.raise_for_status()
            project = resp.json()

            versions_resp = requests.get(
                f"{MODRINTH_API}/project/{self.project_id}/version",
                timeout=30,
                headers={"User-Agent": "OpenMCL/1.0"},
            )
            versions_resp.raise_for_status()
            versions = versions_resp.json()
            project["_versions"] = versions
            self.finished.emit(project, versions, "")
        except Exception as e:
            self.finished.emit({}, [], str(e))


class DepResolveThread(QThread):
    finished = pyqtSignal(list, str)

    def __init__(self, version_data, mc_version, loader):
        super().__init__()
        self.version_data = version_data
        self.mc_version = mc_version
        self.loader = loader
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            if self._cancelled:
                self.finished.emit([], "Cancelled")
                return
            resolved = self._resolve_recursive(
                self.version_data, self.mc_version, self.loader, set())
            if self._cancelled:
                self.finished.emit([], "Cancelled")
                return
            self.finished.emit(resolved, "")
        except Exception as e:
            self.finished.emit([], str(e))

    def _resolve_recursive(self, version_data, mc_version, loader, visited):
        project_id = version_data.get("project_id", "")
        version_id = version_data.get("id", "")
        if version_id in visited:
            return []
        visited.add(version_id)

        deps = version_data.get("dependencies", [])
        results = []

        for dep in deps:
            dep_type = dep.get("dependency_type", "")
            if dep_type not in ("required",):
                continue

            dep_project_id = dep.get("project_id")
            if not dep_project_id:
                continue

            try:
                vresp = requests.get(
                    f"{MODRINTH_API}/project/{dep_project_id}/version",
                    timeout=30,
                    headers={"User-Agent": "OpenMCL/1.0"},
                )
                vresp.raise_for_status()
                dep_versions = vresp.json()
            except Exception:
                continue

            best = self._pick_best_version(dep_versions, mc_version, loader)
            if best:
                sub = self._resolve_recursive(
                    best, mc_version, loader, visited)
                results.extend(sub)
                results.append(best)

        return results

    @staticmethod
    def _pick_best_version(versions, mc_version, loader):
        candidates = []
        for v in versions:
            game_versions = v.get("game_versions", [])
            v_loaders = v.get("loaders", [])
            if mc_version and mc_version not in game_versions:
                continue
            if loader and loader not in v_loaders:
                continue
            candidates.append(v)
        if not candidates:
            for v in versions:
                game_versions = v.get("game_versions", [])
                if mc_version and mc_version not in game_versions:
                    continue
                candidates.append(v)
        if not candidates and versions:
            candidates = versions
        return candidates[0] if candidates else None


class ModDownloadThread(QThread):
    progress = pyqtSignal(int, int, int)
    finished = pyqtSignal(bool, str)

    def __init__(self, url, filename, target_dir, aria2_mgr, sha1=None):
        super().__init__()
        self.url = url
        self.filename = filename
        self.target_dir = target_dir
        self.aria2 = aria2_mgr
        self.sha1 = sha1
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            if self._cancelled:
                self.finished.emit(False, "Cancelled")
                return

            print("[M] run() entered", flush=True)
            if not self.target_dir:
                self.finished.emit(False, "target directory is empty")
                return

            print(f"[M] target_dir={self.target_dir}", flush=True)
            target_dir = str(self.target_dir)
            target = os.path.join(target_dir, str(self.filename))
            print(f"[M] target={target}", flush=True)

            print("[M] makedirs target...", flush=True)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            print("[M] makedirs target OK", flush=True)

            home = os.path.expanduser("~")
            print(f"[M] home={home}", flush=True)
            tmp_dir = os.path.join(home, ".openmcl", "cache", "downloads")
            print(f"[M] tmp_dir={tmp_dir}", flush=True)

            print(f"[M] tmp_dir exists before makedirs? {os.path.isdir(tmp_dir)}", flush=True)
            if os.path.isdir(home):
                print(f"[M] home writable: {os.access(home, os.W_OK)}", flush=True)
            os.makedirs(tmp_dir, exist_ok=True)
            print(f"[M] tmp_dir exists after makedirs? {os.path.isdir(tmp_dir)}", flush=True)

            if not os.path.isdir(tmp_dir):
                tmp_dir = os.path.join(tempfile.gettempdir(), "openmcl_cache")
                print(f"[M] FALLBACK to temp: {tmp_dir}", flush=True)
                os.makedirs(tmp_dir, exist_ok=True)

            tmp_file = os.path.join(tmp_dir, f"dl_{uuid.uuid4().hex}_{str(self.filename)}")
            print(f"[M] tmp_file={tmp_file}", flush=True)

            for attempt in range(3):
                if self._cancelled:
                    self.finished.emit(False, "Cancelled")
                    return

                wait = (attempt + 1) * 3
                print(f"[M] attempt {attempt+1}/3", flush=True)
                downloaded_ok = False
                try:
                    if self.aria2 and self.aria2.is_running():
                        print(f"[M] aria2 download...", flush=True)
                        try:
                            self.aria2.download_single(
                                self.url, tmp_file, sha1=self.sha1, timeout=600)
                            time.sleep(wait)
                            if os.path.isfile(tmp_file) and os.path.getsize(tmp_file) > 0:
                                print(f"[M] aria2 OK: {os.path.getsize(tmp_file)} bytes", flush=True)
                                downloaded_ok = True
                            else:
                                print(f"[M] aria2 finished but file missing", flush=True)
                        except Exception as ex:
                            print(f"[M] aria2 failed: {ex}, falling back to requests", flush=True)
                    if not downloaded_ok and not self._cancelled:
                        print(f"[M] GET {self.url[:80]}", flush=True)
                        resp = requests.get(
                            self.url, stream=True, timeout=(15, 120),
                            headers={"User-Agent": "OpenMCL/1.0"})
                        print(f"[M] resp status={resp.status_code}", flush=True)
                        resp.raise_for_status()
                        total = int(resp.headers.get("content-length", 0))
                        downloaded = 0
                        print(f"[M] opening {tmp_file} for write...", flush=True)
                        with open(tmp_file, "wb") as f:
                            for chunk in resp.iter_content(chunk_size=65536):
                                if chunk:
                                    f.write(chunk)
                                    downloaded += len(chunk)
                                    self.progress.emit(downloaded, total, 0)
                        print(f"[M] wrote {downloaded} bytes", flush=True)
                except (requests.ConnectionError, requests.Timeout,
                        ConnectionResetError, ConnectionAbortedError,
                        OSError) as e:
                    print(f"[M] network error (will retry): {e}", flush=True)
                    if attempt < 2:
                        time.sleep(wait)
                        continue
                    raise

                if self._cancelled:
                    self.finished.emit(False, "Cancelled")
                    return

                time.sleep(wait)
                if os.path.isfile(tmp_file) and os.path.getsize(tmp_file) > 0:
                    print(f"[M] file OK: {os.path.getsize(tmp_file)} bytes", flush=True)
                    break
                print(f"[M] file MISSING attempt {attempt+1}", flush=True)

            if self._cancelled:
                self.finished.emit(False, "Cancelled")
                return

            if not (os.path.isfile(tmp_file) and os.path.getsize(tmp_file) > 0):
                raise RuntimeError(f"Download failed after 3 attempts: {tmp_file}")

            print(f"[M] move {tmp_file} -> {target}", flush=True)
            shutil.move(tmp_file, target)
            print(f"[M] done", flush=True)
            self.finished.emit(True, target)
        except Exception as e:
            traceback.print_exc()
            print(f"[M] EXCEPTION: {e}", flush=True)
            self.finished.emit(False, str(e))


class BatchDownloadThread(QThread):
    progress_signal = pyqtSignal(int, int, str)
    finished_signal = pyqtSignal(int, int, list)

    def __init__(self, tasks, aria2_mgr):
        super().__init__()
        self.tasks = tasks
        self.aria2 = aria2_mgr
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        total = len(self.tasks)
        ok = 0
        errors = []
        for i, task in enumerate(self.tasks):
            if self._cancelled:
                self.finished_signal.emit(ok, total, errors)
                return

            url = task["url"]
            filename = task["filename"]
            target_dir = task["dir"]
            label = task.get("label", filename)
            sha1 = task.get("sha1")
            self.progress_signal.emit(ok, total, f"({ok}/{total}) {label} ...")
            try:
                if not target_dir:
                    errors.append(f"{label}: target directory is empty")
                    self.progress_signal.emit(ok, total, f"({ok}/{total}) FAIL: {label}")
                    continue
                td = str(target_dir)
                target = os.path.join(td, str(filename))

                home = os.path.expanduser("~")
                tmp_dir = os.path.join(home, ".openmcl", "cache", "downloads")
                os.makedirs(tmp_dir, exist_ok=True)
                if not os.path.isdir(tmp_dir):
                    tmp_dir = os.path.join(tempfile.gettempdir(), "openmcl_cache")
                    os.makedirs(tmp_dir, exist_ok=True)
                tmp_file = os.path.join(tmp_dir, f"dl_{uuid.uuid4().hex}_{str(filename)}")

                print(f"[B] {label} tmp={tmp_file} target={target}", flush=True)

                for attempt in range(3):
                    if self._cancelled:
                        self.finished_signal.emit(ok, total, errors)
                        return

                    wait = (attempt + 1) * 3
                    print(f"[B] {label} attempt {attempt+1}/3", flush=True)
                    downloaded_ok = False
                    try:
                        if self.aria2 and self.aria2.is_running():
                            try:
                                self.aria2.download_single(
                                    url, tmp_file, sha1=sha1, timeout=600)
                                time.sleep(wait)
                                if os.path.isfile(tmp_file) and os.path.getsize(tmp_file) > 0:
                                    downloaded_ok = True
                            except Exception as ex:
                                print(f"[B] {label} aria2 failed: {ex}", flush=True)
                        if not downloaded_ok and not self._cancelled:
                            resp = requests.get(
                                url, stream=True, timeout=(15, 120),
                                headers={"User-Agent": "OpenMCL/1.0"})
                            resp.raise_for_status()
                            with open(tmp_file, "wb") as f:
                                for chunk in resp.iter_content(chunk_size=65536):
                                    if chunk:
                                        f.write(chunk)
                            print(f"[B] {label} download done", flush=True)
                    except (requests.ConnectionError, requests.Timeout,
                            ConnectionResetError, ConnectionAbortedError,
                            OSError) as e:
                        print(f"[B] {label} network error (will retry): {e}", flush=True)
                        if attempt < 2:
                            time.sleep(wait)
                            continue
                        raise

                    if self._cancelled:
                        self.finished_signal.emit(ok, total, errors)
                        return

                    time.sleep(wait)
                    if os.path.isfile(tmp_file) and os.path.getsize(tmp_file) > 0:
                        print(f"[B] {label} file OK: {os.path.getsize(tmp_file)} bytes", flush=True)
                        break
                    print(f"[B] {label} file MISSING attempt {attempt+1}", flush=True)

                if self._cancelled:
                    self.finished_signal.emit(ok, total, errors)
                    return

                if not (os.path.isfile(tmp_file) and os.path.getsize(tmp_file) > 0):
                    errors.append(f"{label}: download failed after 3 attempts")
                    self.progress_signal.emit(ok, total, f"({ok}/{total}) FAIL: {label}")
                    continue

                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.move(tmp_file, target)
                ok += 1
                self.progress_signal.emit(ok, total, f"({ok}/{total}) OK: {label}")
            except Exception as e:
                traceback.print_exc()
                print(f"[B] {label} EXCEPTION: {e}", flush=True)
                errors.append(f"{label}: {e}")
                self.progress_signal.emit(ok, total, f"({ok}/{total}) FAIL: {label}")
        self.finished_signal.emit(ok, total, errors)


class ModpackInstallThread(QThread):
    progress_signal = pyqtSignal(int, int, str)
    finished_signal = pyqtSignal(bool, str, str)

    def __init__(self, mrpack_url, version_id, loader_type, loader_version,
                 instance_name, vm, aria2=None):
        super().__init__()
        self.mrpack_url = mrpack_url
        self.version_id = version_id
        self.loader_type = loader_type
        self.loader_version = loader_version
        self.instance_name = instance_name
        self.vm = vm
        self.aria2 = aria2
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _download_file(self, url, dest, label):
        home = os.path.expanduser("~")
        tmp_dir = os.path.join(home, ".openmcl", "cache", "downloads")
        os.makedirs(tmp_dir, exist_ok=True)
        if not os.path.isdir(tmp_dir):
            tmp_dir = os.path.join(tempfile.gettempdir(), "openmcl_cache")
            os.makedirs(tmp_dir, exist_ok=True)

        # 优先走 aria2（与 Mod 下载统一），失败回退 requests
        if self.aria2 is not None:
            try:
                if self.aria2.is_running():
                    dir_name = os.path.dirname(dest)
                    if dir_name:
                        os.makedirs(dir_name, exist_ok=True)
                    _lbl = label

                    def _cb(name, done, total, _label=_lbl):
                        if not self._cancelled:
                            self.progress_signal.emit(done, total, _label)

                    self.aria2.download_single(
                        url, dest, progress_callback=_cb, timeout=600)
                    if os.path.isfile(dest) and os.path.getsize(dest) > 0:
                        return True
            except Exception as e:
                print(f"[MP] aria2 failed, fallback to requests: {e}", flush=True)
                try:
                    if os.path.exists(dest):
                        os.remove(dest)
                except OSError:
                    pass

        for attempt in range(3):
            if self._cancelled:
                return False

            wait = (attempt + 1) * 3
            try:
                resp = requests.get(
                    url, stream=True, timeout=(15, 120),
                    headers={"User-Agent": "OpenMCL/1.0"})
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                downloaded = 0
                self.progress_signal.emit(0, total, label)
                with open(dest, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            self.progress_signal.emit(
                                downloaded, total, label)
                if os.path.isfile(dest) and os.path.getsize(dest) > 0:
                    return True
            except (requests.ConnectionError, requests.Timeout,
                    ConnectionResetError, ConnectionAbortedError,
                    OSError) as e:
                print(f"[MP] network error: {e}", flush=True)
                if attempt < 2 and not self._cancelled:
                    time.sleep(wait)
                    continue
                raise
            if not self._cancelled:
                time.sleep(wait)
        return False

    def run(self):
        try:
            home = os.path.expanduser("~")
            target_dir = os.path.join(
                home, ".openmcl", "versions", self.instance_name)
            mods_dir = os.path.join(target_dir, "mods")

            if self._cancelled:
                self.finished_signal.emit(False, "", "Cancelled")
                return

            tmp_dir = os.path.join(home, ".openmcl", "cache", "downloads")
            os.makedirs(tmp_dir, exist_ok=True)
            if not os.path.isdir(tmp_dir):
                tmp_dir = os.path.join(
                    tempfile.gettempdir(), "openmcl_cache")
                os.makedirs(tmp_dir, exist_ok=True)

            mrpack_path = os.path.join(tmp_dir, f"mp_{uuid.uuid4().hex}.mrpack")
            self.progress_signal.emit(0, 1, "Downloading modpack...")
            if not self._download_file(self.mrpack_url, mrpack_path,
                                "Downloading modpack..."):
                self.finished_signal.emit(False, "", "Cancelled")
                return

            if self._cancelled:
                self.finished_signal.emit(False, "", "Cancelled")
                return

            self.progress_signal.emit(1, 1, "Extracting modpack...")
            extract_dir = os.path.join(tmp_dir, f"mp_extract_{uuid.uuid4().hex}")
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(mrpack_path, "r") as zf:
                zf.extractall(extract_dir)

            index_path = os.path.join(extract_dir, "modrinth.index.json")
            if not os.path.isfile(index_path):
                self.finished_signal.emit(
                    False, "", "modrinth.index.json not found")
                return

            with open(index_path, "r", encoding="utf-8") as f:
                index = json.load(f)

            files = index.get("files", [])
            if not files:
                self.finished_signal.emit(
                    False, "", "Modpack contains no mod files")
                return

            os.makedirs(mods_dir, exist_ok=True)

            total = len(files)
            ok = 0
            errors = []
            for i, fdata in enumerate(files):
                if self._cancelled:
                    self.finished_signal.emit(False, "", "Cancelled")
                    return

                path = fdata.get("path", f"mod_{i}.jar")
                downloads = fdata.get("downloads", [])
                if not downloads:
                    errors.append(f"{path}: no download URL")
                    continue
                url = downloads[0]
                fname = os.path.basename(path) or f"mod_{i}.jar"
                dest = os.path.join(mods_dir, fname)
                label = f"({i+1}/{total}) {fname}"

                try:
                    if not self._download_file(url, dest, label):
                        self.finished_signal.emit(False, "", "Cancelled")
                        return
                    ok += 1
                    self.progress_signal.emit(
                        ok, total, f"({ok}/{total}) OK: {fname}")
                except Exception as e:
                    errors.append(f"{fname}: {e}")
                    self.progress_signal.emit(
                        ok, total, f"({ok}/{total}) FAIL: {fname}")

            if self._cancelled:
                self.finished_signal.emit(False, "", "Cancelled")
                return

            overrides = os.path.join(extract_dir, "overrides")
            if os.path.isdir(overrides):
                self.progress_signal.emit(
                    ok, total, "Copying overrides...")
                for root, dirs, fnames in os.walk(overrides):
                    rel = os.path.relpath(root, overrides)
                    if rel == ".":
                        rel = ""
                    dest_root = os.path.join(target_dir, rel)
                    os.makedirs(dest_root, exist_ok=True)
                    for fn in fnames:
                        src = os.path.join(root, fn)
                        dst = os.path.join(dest_root, fn)
                        shutil.copy2(src, dst)

            try:
                os.remove(mrpack_path)
                shutil.rmtree(extract_dir, ignore_errors=True)
            except Exception:
                pass

            if self._cancelled:
                self.finished_signal.emit(False, "", "Cancelled")
                return

            self.progress_signal.emit(ok, total + 1, "Installing Minecraft version...")
            try:
                self.vm.fetch_version_manifest()
                version_info = self.vm.fetch_version_json(self.version_id)
                self.vm.save_version_json(self.instance_name, version_info)

                jar_info = version_info.get("downloads", {}).get("client", {})
                jar_url = jar_info.get("url", "")
                if jar_url:
                    jar_path = self.vm.get_version_jar_path(self.instance_name)
                    os.makedirs(os.path.dirname(jar_path), exist_ok=True)
                    self._download_file(jar_url, jar_path,
                                        f"Downloading Minecraft {self.version_id}.jar")
            except Exception as e:
                self.finished_signal.emit(False, target_dir,
                                          f"Mods OK but Minecraft install failed: {e}")
                return

            all_libs = list(version_info.get("libraries", []))
            inherited = version_info.get("inheritsFrom")
            visited = set()
            while inherited and inherited not in visited:
                visited.add(inherited)
                parent_data = self.vm.load_version_json(inherited)
                if parent_data:
                    all_libs.extend(parent_data.get("libraries", []))
                    inherited = parent_data.get("inheritsFrom")
                else:
                    break

            libs_to_dl = []
            for lib in all_libs:
                if self.vm._should_download_lib(lib):
                    path = self.vm._get_lib_path(lib)
                    if path and not os.path.isfile(path):
                        libs_to_dl.append(lib)

            if libs_to_dl:
                self.progress_signal.emit(0, len(libs_to_dl),
                                          f"Downloading {len(libs_to_dl)} libraries...")
                done = 0
                for lib in libs_to_dl:
                    if self._cancelled:
                        self.finished_signal.emit(False, "", "Cancelled")
                        return
                    try:
                        self.vm._download_lib(lib)
                    except Exception as e:
                        self.progress_signal.emit(
                            done, len(libs_to_dl),
                            f"Lib failed: {e}")
                    done += 1
                    self.progress_signal.emit(
                        done, len(libs_to_dl),
                        f"Library ({done}/{len(libs_to_dl)})")

            if errors:
                self.finished_signal.emit(
                    False, target_dir,
                    f"Installed {ok}/{total} mods.\nErrors:\n" +
                    "\n".join(errors[:5]))
            else:
                self.finished_signal.emit(
                    True, target_dir,
                    f"Modpack installed: {ok} mods to\n{target_dir}")
        except Exception as e:
            traceback.print_exc()
            self.finished_signal.emit(False, "", str(e))


# ── Web Page ─────────────────────────────────────────

def _build_project_html(project, versions, selected_idx,
                         mc_version, loader, show_deps,
                         dep_versions):
    keywords = []
    slug = project.get("slug", "")
    title = project.get("title", slug)
    author = project.get("author", project.get("team", "Unknown"))
    desc = project.get("description", "")
    icon_url = project.get("icon_url", "")
    if not icon_url and slug:
        icon_url = f"{MODRINTH_CDN}/data/{slug}/icon.png"
    downloads = project.get("downloads", 0)
    followers = project.get("followers", 0)
    categories = ", ".join(project.get("categories", []))
    project_type = project.get("project_type", "mod")
    client_side = project.get("client_side", "unknown")
    server_side = project.get("server_side", "unknown")
    issues_url = project.get("issues_url", "")
    source_url = project.get("source_url", "")
    wiki_url = project.get("wiki_url", "")
    license_info = project.get("license", {})
    license_name = license_info.get("name", "") if isinstance(license_info, dict) else ""

    if project_type == "modpack":
        action_buttons = '<button class="btn btn-install" onclick="console.log(\'openmcl:install-modpack\')">Install Modpack</button>'
    else:
        action_buttons = (
            '<button class="btn btn-install" onclick="console.log(\'openmcl:install\')">Install</button>'
            '<button class="btn btn-install-deps" onclick="console.log(\'openmcl:install-deps\')">Install + Dependencies</button>'
        )

    versions_opts = ""
    for i, v in enumerate(versions):
        gv = ", ".join(v.get("game_versions", []))
        ld = ", ".join(v.get("loaders", []))
        vn = v.get("version_number", v.get("name", "?"))
        sel = "selected" if i == selected_idx else ""
        versions_opts += (
            f'<option value="{i}" {sel}>'
            f'{vn}  [{gv}]  ({ld})'
            f'</option>'
        )

    deps_html = ""
    if show_deps and dep_versions:
        deps_html += '<div class="deps-section">'
        deps_html += '<h3>Dependencies</h3><ul>'
        for dv in dep_versions:
            dname = dv.get("name", dv.get("version_number", "?"))
            dpid = dv.get("project_id", "")
            dtype = "required"
            for dd in versions[selected_idx].get("dependencies", []):
                if dd.get("project_id") == dpid:
                    dtype = dd.get("dependency_type", "required")
                    break
            deps_html += (
                f'<li class="dep-item dep-{dtype}">'
                f'{dname}'
                f'<span class="dep-type">{dtype}</span>'
                f'</li>'
            )
        deps_html += '</ul></div>'

    links_html = ""
    if source_url or issues_url or wiki_url:
        links_html += '<div class="links-section"><h3>Links</h3>'
        if source_url:
            links_html += f'<a href="{source_url}" target="_blank">Source</a> '
        if issues_url:
            links_html += f'<a href="{issues_url}" target="_blank">Issues</a> '
        if wiki_url:
            links_html += f'<a href="{wiki_url}" target="_blank">Wiki</a> '
        links_html += '</div>'

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: "Segoe UI", Arial, sans-serif; background:#121214; color:#d0d0d0; padding:16px; }}
.header {{ display:flex; gap:16px; align-items:flex-start; margin-bottom:16px; }}
.icon {{ width:96px; height:96px; object-fit:cover; background:#1c1c20; flex-shrink:0; }}
.header-info {{ flex:1; }}
.header-info h1 {{ font-size:18px; margin-bottom:4px; color:#e0e0e0; }}
.header-info .author {{ font-size:13px; color:#909098; margin-bottom:8px; }}
.header-info .meta {{ font-size:12px; color:#606068; }}
.header-info .meta span {{ margin-right:12px; }}
.description {{ font-size:13px; line-height:1.6; margin-bottom:12px; color:#b0b0b8; }}
.links-section {{ margin-bottom:12px; font-size:13px; }}
.links-section a {{ color:#808090; margin-right:12px; text-decoration:none; }}
.version-row {{ margin-bottom:12px; }}
.version-row select {{ width:100%; padding:6px; background:#1c1c20; color:#d0d0d0; border:1px solid #2a2a30; font-size:13px; }}
.deps-section {{ margin-bottom:12px; }}
.deps-section h3 {{ font-size:14px; margin-bottom:6px; color:#a0a0a8; }}
.deps-section ul {{ list-style:none; }}
.dep-item {{ font-size:12px; padding:4px 8px; margin:2px 0; background:#1c1c20; display:flex; justify-content:space-between; }}
.dep-type {{ font-size:11px; color:#80a080; }}
.dep-optional .dep-type {{ color:#a09860; }}
.dep-incompatible {{ opacity:0.6; }}
.btn {{ display:inline-block; padding:8px 16px; border:1px solid #3a3a44; font-size:14px; cursor:pointer; margin-right:8px; color:#d0d0d0; font-weight:600; background:#1e1e22; }}
.btn:hover {{ background:#2a2a34; }}
.btn-install {{ background:#1e2e1e; border-color:#2a3a2a; }}
.btn-install:hover {{ background:#2a3e2a; }}
.btn-install-deps {{ background:#1e1e2e; border-color:#2a2a3a; }}
.btn-install-deps:hover {{ background:#2a2a3e; }}
.btn-open {{ background:#1e1e22; border-color:#3a3a44; }}
.btn-open:hover {{ background:#2a2a34; }}
.actions {{ margin-top:12px; }}
.error {{ color:#e06060; font-size:12px; }}
</style></head>
<body>
<div class="header">
<img class="icon" src="{icon_url}" onerror="this.style.display='none'"
     alt="mod icon">
<div class="header-info">
<h1>{title}</h1>
<div class="author">By: {author}</div>
<div class="meta">
<span>Downloads: {downloads:,}</span>
<span>Followers: {followers:,}</span>
<span>Type: {project_type}</span>
</div>
<div class="meta">
<span>Client: {client_side}</span>
<span>Server: {server_side}</span>
<span>License: {license_name}</span>
</div>
</div>
</div>
<div class="description">{desc[:3000]}</div>
{links_html}
<div class="version-row">
<label style="font-size:13px;color:#909098;">Version:</label>
<select id="versionSelect" onchange="console.log('openmcl:select-version:'+this.value)">
{versions_opts}
</select>
</div>
{deps_html}
<div class="actions">
{action_buttons}
<button class="btn btn-open" onclick="console.log('openmcl:open')">Open on Modrinth</button>
</div>
</body></html>"""


class ModPage(QWebEnginePage):
    action_requested = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackgroundColor(Qt.transparent)

    def javaScriptConsoleMessage(self, level, msg, line, source):
        if msg.startswith("openmcl:"):
            payload = msg[len("openmcl:"):]
            if payload.startswith("select-version:"):
                self.action_requested.emit(
                    "select-version", payload[len("select-version:"):])
            else:
                self.action_requested.emit(payload, "")

    def acceptNavigationRequest(self, url, nav_type, is_main):
        url_str = url.toString()
        if url_str.startswith("http://") or url_str.startswith("https://"):
            QDesktopServices.openUrl(url)
            return False
        return True

    def createWindow(self, wintype):
        return None


class ModPageView(QWebEngineView):
    action_requested = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(400)
        self._project = None
        self._versions = []
        self._selected_idx = 0
        self._mc_version = ""
        self._loader = ""
        self._dep_versions = []
        self._show_deps = False
        page = ModPage(self)
        page.action_requested.connect(self.action_requested)
        self.setPage(page)
        self._show_placeholder()

    def _show_placeholder(self):
        html = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body{background:#121214;color:#808088;font-family:"Segoe UI",Arial,sans-serif;
display:flex;align-items:center;justify-content:center;height:100vh;font-size:16px;}
</style></head><body>Select a mod to view details</body></html>"""
        self.setHtml(html)

    def show_project(self, project, versions, mc_version="", loader=""):
        self._project = project
        self._versions = versions
        self._selected_idx = 0
        self._mc_version = mc_version or ""
        self._loader = loader or ""
        self._dep_versions = []
        self._show_deps = False
        self._render()

    def show_dependencies(self, dep_versions):
        self._dep_versions = dep_versions
        self._show_deps = True
        self._render()

    def _render(self):
        if not self._project or not self._versions:
            self._show_placeholder()
            return
        html = _build_project_html(
            self._project, self._versions, self._selected_idx,
            self._mc_version, self._loader,
            self._show_deps, self._dep_versions)
        self.setHtml(html)


# ── Main Widget ──────────────────────────────────────

class ModDownloadWidget(QWidget):
    console_log = pyqtSignal(str)
    install_finished = pyqtSignal()

    def __init__(self, vm, aria2_manager=None, parent=None):
        super().__init__(parent)
        self.vm = vm
        self.aria2 = aria2_manager
        self.mods_dir = None
        self._results = []
        self._current_project = None
        self._current_versions = []
        self._current_selected_idx = 0
        self._dep_versions = []
        self._dl_queue = []
        self._dl_errors = []
        self._pending_main_task = None
        self._is_initial_loaded = False
        self._active_threads = []
        self._dl_threads = []
        if DOWNLOAD_CACHE.exists():
            shutil.rmtree(str(DOWNLOAD_CACHE), ignore_errors=True)
        os.makedirs(os.path.join(os.path.expanduser("~"), ".openmcl", "cache", "downloads"), exist_ok=True)
        self._init_ui()

    def _track_thread(self, thread):
        self._active_threads[:] = [t for t in self._active_threads if t.isRunning()]
        self._active_threads.append(thread)

    def _create_dl_row(self, label):
        pw = QWidget()
        pw_layout = QHBoxLayout(pw)
        pw_layout.setContentsMargins(4, 2, 4, 2)
        pw_name = QLabel(label)
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
        pw_cancel.setToolTip("Cancel download")
        pw_cancel.setStyleSheet(
            "QPushButton { color: #e74c3c; font-weight: bold; border: none; }"
            "QPushButton:hover { color: #c0392b; }")
        pw_layout.addWidget(pw_cancel)
        self.dl_container.addWidget(pw)
        return pw, pw_bar, pw_status, pw_cancel

    def _remove_dl_row(self, thread):
        if hasattr(thread, '_pw'):
            thread._pw.setVisible(False)
            self.dl_container.removeWidget(thread._pw)
            thread._pw.deleteLater()
        if thread in self._dl_threads:
            self._dl_threads.remove(thread)

    # ── UI construction ──────────────────────────────

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        search_group = QGroupBox("Search Mods")
        search_layout = QVBoxLayout(search_group)

        top_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search mods on Modrinth...")
        self.search_input.returnPressed.connect(self._on_search)
        top_row.addWidget(self.search_input, 1)

        self.loader_filter = QComboBox()
        self.loader_filter.addItem("All Loaders", "")
        self.loader_filter.currentIndexChanged.connect(self._on_filter_changed)
        for key, label in [("forge", "Forge"), ("fabric", "Fabric"),
                           ("neoforge", "NeoForge"), ("quilt", "Quilt")]:
            self.loader_filter.addItem(label, key)
        top_row.addWidget(self.loader_filter)

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

        self.status_label = QLabel("Loading popular mods...")
        self.status_label.setWordWrap(True)
        left_layout.addWidget(self.status_label)

        splitter.addWidget(left_panel)

        right_panel = QFrame()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 0, 0, 0)

        self.mod_view = ModPageView()
        self.mod_view.action_requested.connect(self._on_web_action)
        right_layout.addWidget(self.mod_view, 1)

        self.target_row = QHBoxLayout()
        self.auto_loader_cb = QCheckBox("Auto-detect modloader")
        self.auto_loader_cb.setChecked(True)
        self.auto_loader_cb.setToolTip(
            "Auto-detect Fabric/Forge from the target instance")
        self.target_row.addWidget(self.auto_loader_cb)
        self.target_label = QLabel("Install to:")
        self.target_row.addWidget(self.target_label)
        self.instance_combo = QComboBox()
        self.instance_combo.setMinimumHeight(26)
        self._refresh_instances()
        self.target_row.addWidget(self.instance_combo, 1)
        right_layout.addLayout(self.target_row)

        splitter.addWidget(right_panel)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([280, 560])
        root.addWidget(splitter, 1)

        mod_mgr_group = QGroupBox("Installed Mods")
        mod_mgr_layout = QVBoxLayout(mod_mgr_group)

        toolbar = QHBoxLayout()
        self.refresh_mods_btn = QPushButton("Refresh")
        self.refresh_mods_btn.clicked.connect(self._refresh_mod_list)
        toolbar.addWidget(self.refresh_mods_btn)
        self.disable_mod_btn = QPushButton("Toggle Enable/Disable")
        self.disable_mod_btn.clicked.connect(self._toggle_mod)
        toolbar.addWidget(self.disable_mod_btn)
        self.delete_mod_btn = QPushButton("Delete")
        self.delete_mod_btn.clicked.connect(self._delete_mod)
        toolbar.addWidget(self.delete_mod_btn)
        self.open_mods_folder_btn = QPushButton("Open Folder")
        self.open_mods_folder_btn.clicked.connect(self._open_mods_folder)
        toolbar.addWidget(self.open_mods_folder_btn)
        toolbar.addStretch()
        mod_mgr_layout.addLayout(toolbar)

        self.mod_list = QListWidget()
        self.mod_list.setAlternatingRowColors(True)
        mod_mgr_layout.addWidget(self.mod_list)

        root.addWidget(mod_mgr_group)

        self.dl_container = QVBoxLayout()
        self.dl_container.setContentsMargins(0, 0, 0, 0)
        root.addLayout(self.dl_container)

        self.instance_combo.currentIndexChanged.connect(self._refresh_mod_list)

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
            loader = self.loader_filter.currentData()
            mc_version = self.version_filter.currentData()
            self.status_label.setText("Loading popular mods...")

            self._auto_load = True
            if hasattr(self, '_search_thread') and self._search_thread is not None:
                self._search_thread.finished.disconnect(self._on_search_done)
            self._search_thread = ModSearchThread(
                "", mc_version, loader, index="downloads", project_type=None)
            self._search_thread.finished.connect(self._on_search_done)
            self._track_thread(self._search_thread)
            self._search_thread.start()
        except Exception as e:
            self.status_label.setText(f"Load failed: {e}")
            self.console_log.emit(f"Mod load error: {e}")

    # ── Version / instance combo management ─────────

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

    def _refresh_instances(self):
        self.instance_combo.clear()
        installed = self.vm.get_installed_versions()
        for vid in installed:
            ver_json_path = os.path.join(
                os.path.expanduser("~"), ".openmcl", "versions",
                vid, f"{vid}.json")
            ml = ""
            if os.path.isfile(ver_json_path):
                try:
                    with open(ver_json_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    oml = data.get("_openmcl", {})
                    ml_data = oml.get("modloader", {})
                    if isinstance(ml_data, dict):
                        ml = ml_data.get("type", "")
                    elif isinstance(ml_data, str):
                        ml = ml_data
                except Exception:
                    pass
            label = vid
            if ml:
                label += f" ({ml})"
            self.instance_combo.addItem(label, vid)

    # ── Mod management ──────────────────────────────

    def _mod_dir(self):
        if self.mods_dir:
            return Path(self.mods_dir)
        target_id = self.instance_combo.currentData()
        if not target_id:
            return None
        return Path.home() / ".openmcl" / "versions" / target_id / "mods"

    def set_mods_dir(self, mods_dir):
        self.mods_dir = mods_dir
        self.auto_loader_cb.setVisible(False)
        self.target_label.setVisible(False)
        self.instance_combo.setVisible(False)

    def _get_mods_dir(self):
        if self.mods_dir:
            return str(self.mods_dir)
        target_id = self.instance_combo.currentData()
        if not target_id:
            return None
        target_dir = os.path.join(
            os.path.expanduser("~"), ".openmcl", "versions",
            target_id, "mods")
        return target_dir

    def _refresh_mod_list(self):
        self.mod_list.clear()
        mod_dir = self._mod_dir()
        if not mod_dir or not mod_dir.is_dir():
            return
        for entry in sorted(os.listdir(str(mod_dir)), key=str.lower):
            path = mod_dir / entry
            if not path.is_file():
                continue
            if entry.endswith(".disabled"):
                base = entry[:-9]
                label = f"[DISABLED] {base}"
            else:
                label = entry
            size = path.stat().st_size
            if size < 1024:
                s = f"{size} B"
            elif size < 1024 * 1024:
                s = f"{size / 1024:.1f} KB"
            else:
                s = f"{size / (1024 * 1024):.1f} MB"
            item = QListWidgetItem(f"{label}  ({s})")
            item.setData(Qt.UserRole, entry)
            item.setToolTip(str(path))
            if entry.endswith(".disabled"):
                item.setForeground(Qt.gray)
            self.mod_list.addItem(item)
        self.status_label.setText(
            f"{self.mod_list.count()} mod(s) in selected instance")

    def _toggle_mod(self):
        item = self.mod_list.currentItem()
        if not item:
            return
        filename = item.data(Qt.UserRole)
        mod_dir = self._mod_dir()
        if not mod_dir:
            return
        if filename.endswith(".disabled"):
            new_name = filename[:-9]
        else:
            new_name = filename + ".disabled"
        old_path = mod_dir / filename
        new_path = mod_dir / new_name
        if new_path.exists():
            QMessageBox.warning(self, "Conflict",
                                f"{new_name} already exists.")
            return
        os.rename(str(old_path), str(new_path))
        self._refresh_mod_list()

    def _delete_mod(self):
        item = self.mod_list.currentItem()
        if not item:
            return
        filename = item.data(Qt.UserRole)
        mod_dir = self._mod_dir()
        if not mod_dir:
            return
        path = mod_dir / filename
        if not path.exists():
            return
        reply = QMessageBox.question(
            self, "Delete Mod",
            f"Are you sure you want to delete:\n{filename}?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        os.remove(str(path))
        self._refresh_mod_list()

    def _open_mods_folder(self):
        mod_dir = self._mod_dir()
        if not mod_dir:
            return
        mod_dir_str = str(mod_dir)
        os.makedirs(mod_dir_str, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(mod_dir_str))

    # ── Instance modloader detection ─────────────────

    def _detect_modloader(self):
        target_id = self.instance_combo.currentData()
        if not target_id:
            return ""
        ver_json_path = os.path.join(
            os.path.expanduser("~"), ".openmcl", "versions",
            target_id, f"{target_id}.json")
        if not os.path.isfile(ver_json_path):
            return ""
        try:
            with open(ver_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            oml = data.get("_openmcl", {})
            ml_data = oml.get("modloader", {})
            if isinstance(ml_data, dict):
                return ml_data.get("type", "")
            elif isinstance(ml_data, str):
                return ml_data
            return ""
        except Exception:
            return ""

    def _on_filter_changed(self):
        if not self._is_initial_loaded:
            return
        self._load_popular()

    # ── Search ──────────────────────────────────────

    def _on_search(self):
        query = self.search_input.text().strip()
        loader = self.loader_filter.currentData()
        mc_version = self.version_filter.currentData()
        self.status_label.setText("Searching...")
        self.result_list.clear()
        self._results = []

        self._auto_load = False
        if hasattr(self, '_search_thread') and self._search_thread is not None:
            self._search_thread.finished.disconnect(self._on_search_done)
        self._search_thread = ModSearchThread(
            query, mc_version, loader, project_type=None)
        self._search_thread.finished.connect(self._on_search_done)
        self._track_thread(self._search_thread)
        self._search_thread.start()

    def _on_search_done(self, hits, error):
        try:
            self._handle_search_done(hits, error)
        except Exception as e:
            self.status_label.setText(f"Display error: {e}")
            self.console_log.emit(f"Mod display error: {e}")

    def _handle_search_done(self, hits, error):
        if error:
            self.status_label.setText(f"Search error: {error}")
            self.console_log.emit(f"Mod search failed: {error}")
            return
        self._results = hits
        self.result_list.clear()
        if not hits:
            self.status_label.setText("No mods found.")
            return
        prefix = "Popular" if getattr(self, '_auto_load', False) else "Found"
        self.status_label.setText(f"{prefix} {len(hits)} mods.")
        self.result_list.blockSignals(True)
        for hit in hits:
            title = hit.get("title", hit.get("slug", "?"))
            desc = hit.get("description", "")
            pid = hit.get("project_id", hit.get("slug", ""))
            icon_url = hit.get("icon_url", "")
            item = QListWidgetItem(f"{title}\n{desc[:80]}")
            item.setData(Qt.UserRole, pid)
            item.setToolTip(desc)
            if icon_url:
                self._load_item_icon(item, icon_url)
            self.result_list.addItem(item)
        self.result_list.blockSignals(False)
        self.mod_view._show_placeholder()

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

    # ── Result selection ────────────────────────────

    def _on_result_selected(self, row):
        try:
            print(f"[D] _on_result_selected row={row}", flush=True)
            if row < 0 or row >= len(self._results):
                print(f"[D] row out of range, results={len(self._results)}", flush=True)
                return
            hit = self._results[row]
            pid = hit.get("project_id", hit.get("slug", ""))
            print(f"[D] pid={pid} title={hit.get('title','?')}", flush=True)
            if not pid:
                print("[D] empty pid, returning", flush=True)
                return

            if hasattr(self, '_detail_thread') and self._detail_thread is not None:
                self._detail_thread.finished.disconnect(self._on_detail_done)
            self._detail_thread = ModDetailThread(pid)
            self._detail_thread.finished.connect(self._on_detail_done)
            self._track_thread(self._detail_thread)
            self._detail_thread.start()
        except Exception as e:
            traceback.print_exc()
            self.console_log.emit(f"Mod select error: {e}")

    def _on_detail_done(self, project, versions, error):
        try:
            print(f"[D] _on_detail_done error={error!r} project_keys={list(project.keys())[:5] if project else 'EMPTY'} n_versions={len(versions)}", flush=True)
            self._handle_detail_done(project, versions, error)
        except Exception as e:
            traceback.print_exc()
            self.console_log.emit(f"Mod detail error: {e}")

    def _handle_detail_done(self, project, versions, error):
        print(f"[D] _handle_detail_done error={error!r}", flush=True)
        if error:
            self.console_log.emit(f"Failed to load mod details: {error}")
            return
        self._current_project = project
        self._current_versions = versions
        self._current_selected_idx = 0
        self._dep_versions = []

        mc_version = _extract_base(self.version_filter.currentData() or "")
        loader = self.loader_filter.currentData()
        if self.auto_loader_cb.isChecked():
            detected = self._detect_modloader()
            if detected:
                loader = LOADER_MAP.get(detected, detected)
        print(f"[D] calling show_project type={project.get('project_type','?')}", flush=True)
        self.mod_view.show_project(project, versions, mc_version, loader)
        print(f"[D] show_project done", flush=True)

    def _on_web_action(self, action, arg):
        if action == "install":
            self._install(with_deps=False)
        elif action == "install-deps":
            self._install(with_deps=True)
        elif action == "open":
            self._on_open_modrinth()
        elif action == "select-version":
            try:
                idx = int(arg)
                if 0 <= idx < len(self._current_versions):
                    self._current_selected_idx = idx
                    if self._dep_versions:
                        self.mod_view.show_dependencies(self._dep_versions)
            except (ValueError, IndexError):
                pass

    def _on_open_modrinth(self):
        if self._current_project:
            slug = self._current_project.get("slug", "")
            if slug:
                pt = self._current_project.get("project_type", "mod")
                QDesktopServices.openUrl(
                    QUrl(f"https://modrinth.com/{pt}/{slug}"))

    # ── Install ────────────────────────────────────

    def _install(self, with_deps=False):
        if not self._current_versions:
            QMessageBox.warning(self, "No Version", "Select a mod version first.")
            return

        target_dir = self._get_mods_dir()
        if not target_dir:
            QMessageBox.warning(
                self, "No Instance",
                "Install a Minecraft version first.")
            return
        os.makedirs(target_dir, exist_ok=True)

        version_data = self._current_versions[self._current_selected_idx]
        files = version_data.get("files", [])
        if not files:
            QMessageBox.warning(self, "No File", "No file available.")
            return

        main_file = files[0]
        main_hashes = main_file.get("hashes", {})

        self._pending_main_task = {
            "url": main_file.get("url", ""),
            "filename": main_file.get("filename", "mod.jar"),
            "dir": target_dir,
            "label": version_data.get("version_number", "mod"),
            "sha1": main_hashes.get("sha1"),
        }

        if with_deps:
            mc_version = _extract_base(self.version_filter.currentData() or "")
            loader = self.loader_filter.currentData()
            if self.auto_loader_cb.isChecked():
                detected = self._detect_modloader()
                if detected:
                    loader = LOADER_MAP.get(detected, detected)
            self._resolve_and_install(
                version_data, mc_version, loader, target_dir)
        else:
            self._start_single_download()

    def _start_single_download(self):
        t = self._pending_main_task
        if not t:
            return

        pw, pw_bar, pw_status, pw_cancel = self._create_dl_row(t['label'])
        thread = ModDownloadThread(
            t["url"], t["filename"], t["dir"],
            aria2_mgr=self.aria2, sha1=t.get("sha1"))
        thread._pw = pw
        thread._pw_bar = pw_bar
        thread._pw_status = pw_status
        thread._pw_cancel = pw_cancel
        pw_cancel.clicked.connect(thread.cancel)
        thread.progress.connect(self._on_single_progress)
        thread.finished.connect(self._on_single_done)
        self._dl_threads.append(thread)
        self._track_thread(thread)
        thread.start()

    def _on_single_progress(self, completed, total, _):
        thread = self.sender()
        if thread is None:
            return
        if total > 0:
            thread._pw_bar.setMaximum(total)
            thread._pw_bar.setValue(completed)
        else:
            thread._pw_bar.setMaximum(0)

    def _on_single_done(self, success, result):
        thread = self.sender()
        if thread is None:
            return
        if result == "Cancelled":
            self.console_log.emit("Download cancelled by user")
        elif success:
            self.console_log.emit(f"Downloaded mod to: {result}")
            QMessageBox.information(self, "Done", f"Mod saved to:\n{result}")
        else:
            self.console_log.emit(f"Download failed: {result}")
            QMessageBox.critical(self, "Download Failed", str(result))
        self._remove_dl_row(thread)

    def _resolve_and_install(self, version_data, mc_version, loader,
                              target_dir):

        pw, pw_bar, pw_status, pw_cancel = self._create_dl_row("Resolving deps...")
        self._dep_thread = DepResolveThread(
            version_data, mc_version, loader)
        self._dep_thread._pw = pw
        self._dep_thread._pw_bar = pw_bar
        self._dep_thread._pw_status = pw_status
        self._dep_thread._pw_cancel = pw_cancel
        pw_cancel.clicked.connect(self._dep_thread.cancel)
        self._dep_thread.finished.connect(
            lambda deps, err: self._on_deps_resolved(
                deps, err, target_dir))
        self._dl_threads.append(self._dep_thread)
        self._track_thread(self._dep_thread)
        self._dep_thread.start()

    def _on_deps_resolved(self, dep_versions, error, target_dir):
        thread = self.sender()
        if thread:
            self._remove_dl_row(thread)
        if error == "Cancelled":
            self.console_log.emit("Dependency resolution cancelled by user")
            return
        if error:
            self.console_log.emit(f"Dependency resolution failed: {error}")
            QMessageBox.critical(self, "Error", str(error))
            return

        self._dep_versions = dep_versions
        tasks = []

        for dv in dep_versions:
            dfiles = dv.get("files", [])
            if dfiles:
                f = dfiles[0]
                fh = f.get("hashes", {})
                tasks.append({
                    "url": f.get("url", ""),
                    "filename": f.get("filename", f"{dv.get('name','dep')}.jar"),
                    "dir": target_dir,
                    "label": f"{dv.get('name', f.get('filename','dep'))} (dep)",
                    "sha1": fh.get("sha1"),
                })

        if self._pending_main_task:
            tasks.append(self._pending_main_task)
            self._pending_main_task = None

        if dep_versions:
            self.mod_view.show_dependencies(dep_versions)

        self._start_batch_download(tasks)

    def _start_batch_download(self, tasks):
        if not tasks:
            return
        for task in tasks:
            os.makedirs(str(task["dir"]), exist_ok=True)

        pw, pw_bar, pw_status, pw_cancel = self._create_dl_row(
            f"Batch ({len(tasks)} mods)")
        self._dl_queue = tasks
        self._dl_errors = []
        thread = BatchDownloadThread(tasks, self.aria2)
        thread._pw = pw
        thread._pw_bar = pw_bar
        thread._pw_status = pw_status
        thread._pw_cancel = pw_cancel
        pw_cancel.clicked.connect(thread.cancel)
        thread.progress_signal.connect(self._on_batch_progress)
        thread.finished_signal.connect(self._on_batch_done)
        self._dl_threads.append(thread)
        self._track_thread(thread)
        thread.start()

    def _on_batch_progress(self, ok, total, text):
        thread = self.sender()
        if thread is None:
            return
        if total > 0:
            thread._pw_bar.setMaximum(total)
        thread._pw_bar.setValue(ok)
        thread._pw_status.setText(text)

    def _on_batch_done(self, ok, total, errors):
        thread = self.sender()
        if thread is None:
            return
        if thread._cancelled:
            self.console_log.emit(
                f"Batch download cancelled ({ok}/{total} completed)")
        elif errors:
            err_msg = "\n".join(errors[:5])
            self.console_log.emit(
                f"Installed {ok}/{total} mods. Errors:\n{err_msg}")
            QMessageBox.warning(
                self, "Partial Install",
                f"{ok}/{total} mods installed.\n\nErrors:\n{err_msg}")
        else:
            self.console_log.emit(f"Installed {ok} mod(s) successfully.")
            QMessageBox.information(
                self, "Done",
                f"Installed {ok} mod(s) successfully.")
        self.install_finished.emit()
        self._remove_dl_row(thread)


# ── Icon loader thread ──────────────────────────────

class IconLoadThread(QThread):
    finished_with_icon = pyqtSignal()

    def __init__(self, url):
        super().__init__()
        self.url = url
        self.data = None

    def run(self):
        try:
            resp = requests.get(
                self.url, timeout=15,
                headers={"User-Agent": "OpenMCL/1.0"})
            if resp.status_code == 200:
                self.data = resp.content
        except Exception:
            pass
        self.finished_with_icon.emit()


# ── Helpers ─────────────────────────────────────────

def _extract_base(version_str):
    if not version_str:
        return ""
    parts = version_str.split("-", 1)
    return parts[0]