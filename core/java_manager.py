import os
import json
import re
import sys
import platform
import zipfile
import tarfile
import shutil
import tempfile

import requests
from .version_manager import rewrite_url_to_bmclapi
from .downloader import create_retry_session
from core.paths import resource_root

MOJANG_RUNTIME_URL = (
    "https://bmclapi2.bangbang93.com/v1/products/java-runtime/"
    "2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json"
)


MOJANG_RUNTIME_FALLBACK = (
    "https://launchermeta.mojang.com/v1/products/java-runtime/"
    "2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json"
)


PLATFORM_MAP = {
    ("Windows", "AMD64"): "windows-x64",
    ("Windows", "x86"):   "windows-x86",
    ("Darwin", "AMD64"):  "mac-os",
    ("Darwin", "arm64"):  "mac-os-arm64",
    ("Linux", "AMD64"):   "linux",
    ("Linux", "i386"):    "linux-i386",
}


def _current_platform():
    system = platform.system()
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        arch = "AMD64"
    elif machine in ("x86", "i386", "i686"):
        arch = "x86"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = "AMD64"
    return PLATFORM_MAP.get((system, arch))


def _parse_version(ver_str):
    m = re.match(r"(\d+(?:\.\d+)*)", ver_str)
    if m:
        parts = m.group(1).split(".")
        return tuple(int(p) for p in parts[:4])
    return (0,)


class JavaManager:
    def __init__(self, base_dir=None):
        if base_dir is None:
            base_dir = resource_root()
        self.java_dir = os.path.join(base_dir, "java")
        os.makedirs(self.java_dir, exist_ok=True)
        self._session = None

    @property
    def session(self):
        if self._session is None:
            self._session = create_retry_session()
        return self._session

    def _fetch_json(self, url, timeout=15):
        try:
            resp = self.session.get(url, timeout=timeout)
            resp.raise_for_status()
            text = resp.text.strip()
            if not text:
                return None
            return resp.json()
        except Exception:
            return None

    def get_runtime_list(self):
        data = self._fetch_json(MOJANG_RUNTIME_URL)
        if data is None:
            data = self._fetch_json(MOJANG_RUNTIME_FALLBACK)
        if data is None:
            raise RuntimeError(
                "Failed to fetch Java runtime list. "
                "BMCLAPI mirror and Mojang origin both returned empty/invalid response."
            )

        pf = _current_platform()
        if pf is None or pf not in data:
            available = list(data.keys())
            raise RuntimeError(
                f"No Java runtime for current platform.\n"
                f"Current: {platform.system()} {platform.machine()}\n"
                f"Available platforms: {available}"
            )

        platform_data = data[pf]
        versions = []

        for runtime_key, entries in platform_data.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                ver = entry.get("version", {})
                name = ver.get("name", "")
                released = ver.get("released", "")
                if not name:
                    continue

                manifest_url = entry.get("manifest", {}).get("url", "")
                manifest_url = rewrite_url_to_bmclapi(manifest_url)

                versions.append({
                    "name": name,
                    "released": released,
                    "runtime": runtime_key,
                    "manifest_url": manifest_url,
                })

        if not versions:
            raise RuntimeError("No Java runtimes found for current platform.")

        versions.sort(key=lambda v: _parse_version(v["name"]), reverse=True)
        return versions

    def resolve_download(self, runtime_info):
        manifest_url = runtime_info.get("manifest_url", "")
        if not manifest_url:
            raise RuntimeError("No manifest URL in runtime info.")

        resp = self.session.get(manifest_url, timeout=30)
        resp.raise_for_status()
        manifest = resp.json()

        files = manifest.get("files", {})
        download_url = ""
        for key, finfo in files.items():
            if finfo.get("type") == "file":
                dl = finfo.get("downloads", {}).get("raw", {}).get("url", "")
                if dl:
                    download_url = rewrite_url_to_bmclapi(dl)
                    break

        if not download_url:
            candidate_files = list(files.keys())
            if candidate_files:
                first = candidate_files[0]
                dl = files[first].get("downloads", {}).get("raw", {}).get("url", "")
                download_url = rewrite_url_to_bmclapi(dl) if dl else ""

        if not download_url:
            raise RuntimeError("Cannot resolve download URL from manifest.")

        uncompressed = "tar" in download_url.lower() or "tgz" in download_url.lower()
        return download_url, uncompressed

    def download_and_extract(self, runtime_info, progress_callback=None):
        download_url, _ = self.resolve_download(runtime_info)

        ver_name = runtime_info["name"]
        dest_dir = os.path.join(self.java_dir, ver_name)

        if os.path.exists(dest_dir):
            java_exe = self._find_java_in_dir(dest_dir)
            if java_exe:
                return java_exe

        tmp_dir = tempfile.mkdtemp(prefix="openmcl_java_")
        archive_path = os.path.join(tmp_dir, f"java-{ver_name}.tmp")

        try:
            resp = self.session.get(download_url, stream=True, timeout=(15, 300))
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))

            downloaded = 0
            last_pct = -1
            with open(archive_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            pct = downloaded * 100 // max(total, 1)
                            if pct != last_pct and pct % 5 == 0:
                                last_pct = pct
                                progress_callback(pct, f"Downloading Java {ver_name}... {pct}%")

            if not os.path.isfile(archive_path) or os.path.getsize(archive_path) == 0:
                raise RuntimeError(f"Java download failed: empty file for {ver_name}")

            if progress_callback:
                progress_callback(100, f"Extracting Java {ver_name}...")

            extract_dir = os.path.join(tmp_dir, "extracted")
            os.makedirs(extract_dir, exist_ok=True)

            if zipfile.is_zipfile(archive_path):
                with zipfile.ZipFile(archive_path, "r") as zf:
                    zf.extractall(extract_dir)
            else:
                try:
                    is_tar = tarfile.is_tarfile(archive_path)
                except Exception:
                    is_tar = False
                if is_tar:
                    with tarfile.open(archive_path, "r:*") as tf:
                        tf.extractall(extract_dir)
                else:
                    head = b""
                    if os.path.isfile(archive_path):
                        with open(archive_path, "rb") as fh:
                            head = fh.read(16)
                    raise RuntimeError(
                        f"Cannot detect archive format for {ver_name}. "
                        f"File size: {os.path.getsize(archive_path)} bytes, "
                        f"magic: {head.hex() if head else 'empty'}"
                    )

            entries = os.listdir(extract_dir)
            top_dirs = [d for d in entries if os.path.isdir(os.path.join(extract_dir, d))]
            if len(entries) == 1 and len(top_dirs) == 1:
                inner = os.path.join(extract_dir, top_dirs[0])
                os.rename(inner, dest_dir)
            else:
                os.makedirs(dest_dir, exist_ok=True)
                for item in entries:
                    s = os.path.join(extract_dir, item)
                    d = os.path.join(dest_dir, item)
                    if os.path.isdir(s):
                        if os.path.exists(d):
                            shutil.rmtree(d, ignore_errors=True)
                        shutil.copytree(s, d)
                    else:
                        shutil.copy2(s, d)

            java_exe = self._find_java_in_dir(dest_dir)
            if not java_exe:
                raise RuntimeError(f"Java executable not found in extracted archive.")

            if progress_callback:
                progress_callback(100, f"Java {ver_name} installed: {java_exe}")

            return java_exe

        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

    def _find_java_in_dir(self, directory):
        names = ["java.exe", "javaw.exe", "java"]
        for root, dirs, files in os.walk(directory):
            if "bin" in dirs:
                bin_dir = os.path.join(root, "bin")
                for name in names:
                    path = os.path.join(bin_dir, name)
                    if os.path.isfile(path):
                        return os.path.normpath(path)
            for name in names:
                path = os.path.join(root, name)
                if os.path.isfile(path):
                    return os.path.normpath(path)
        return None