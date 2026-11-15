import os
import json
import hashlib
import shutil
import time
import platform
import requests
import zipfile
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.version_manager import (
    rewrite_url_to_bmclapi, rewrite_url_to_fastmc, rewrite_url_for_mirror,
    mirror_to_origin, get_mirror_chain, _is_likely_mirror,
)
from core.aria2_manager import Aria2Manager

requests.packages.urllib3.disable_warnings()
requests.packages.urllib3.util.ssl_.DEFAULT_CIPHERS = "ALL:@SECLEVEL=1"


RETRY_STRATEGY = Retry(
    total=5,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
    raise_on_status=False,
)


def create_retry_session():
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=RETRY_STRATEGY)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.verify = False
    return session


class Downloader:
    def __init__(self, version_manager, aria2_manager=None):
        self.vm = version_manager
        self._session = None
        self._aria2 = aria2_manager

    @property
    def session(self):
        if self._session is None:
            self._session = create_retry_session()
        return self._session

    def download_file(self, url, dest_path, sha1=None, progress_callback=None,
                       fallback_url=None, use_aria2=True):
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        if os.path.exists(dest_path) and sha1:
            if self._verify_sha1(dest_path, sha1):
                if progress_callback:
                    progress_callback(os.path.basename(dest_path), os.path.getsize(dest_path), os.path.getsize(dest_path))
                return True

        urls = get_mirror_chain(url, preferred=self.vm.preferred_mirror)
        if fallback_url and fallback_url != url:
            fb_chain = get_mirror_chain(fallback_url, preferred=self.vm.preferred_mirror)
            for u in fb_chain:
                if u not in urls:
                    urls.append(u)

        last_error = None
        errors = []
        for idx, try_url in enumerate(urls):
            is_fallback = (idx > 0)
            dl_timeout = (10, 30) if is_fallback else (15, 120)
            for attempt in range(3 if is_fallback else 5):
                try:
                    return self._do_download(try_url, dest_path, sha1,
                                             progress_callback, timeout=dl_timeout,
                                             use_aria2=use_aria2)
                except (requests.ConnectionError, requests.Timeout,
                        ConnectionResetError, ConnectionAbortedError,
                        OSError) as e:
                    last_error = e
                    errors.append(f"{try_url}: {e}")
                    if attempt < (2 if is_fallback else 4):
                        time.sleep(0.5 * (2 ** attempt))
                        continue
                    break
                except requests.HTTPError as e:
                    status = e.response.status_code if e.response is not None else 0
                    errors.append(f"{try_url}: HTTP {status}")
                    if status in (403, 404):
                        break
                    raise
                except Exception as e:
                    errors.append(f"{try_url}: {e}")
                    raise

        msg = "; ".join(errors[-3:]) if errors else "no response"
        raise last_error or RuntimeError(f"Download failed: {msg}")

    def _do_download(self, url, dest_path, sha1, progress_callback, timeout=(15, 120),
                      use_aria2=True):
        if use_aria2 and self._aria2 and self._aria2.is_running():
            try:
                return self._aria2.download_single(
                    url, dest_path, sha1=sha1,
                    progress_callback=progress_callback)
            except Exception as e:
                pass

        resp = requests.get(url, stream=True, timeout=timeout, verify=False)
        resp.raise_for_status()
        total_size = int(resp.headers.get("content-length", 0))
        downloaded = 0

        temp_path = dest_path + ".tmp"
        try:
            with open(temp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total_size > 0:
                            progress_callback(os.path.basename(dest_path), downloaded, total_size)
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

        if sha1:
            if not self._verify_sha1(temp_path, sha1):
                os.remove(temp_path)
                raise IOError(f"SHA1 mismatch for {url}")

        if os.path.exists(dest_path):
            os.remove(dest_path)
        os.rename(temp_path, dest_path)

        if progress_callback:
            progress_callback(os.path.basename(dest_path), total_size, total_size)
        return True

    def _verify_sha1(self, filepath, expected):
        if expected is None:
            return True
        sha1 = hashlib.sha1()
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                sha1.update(chunk)
        return sha1.hexdigest() == expected

    def download_version_json(self, version_id):
        data = self.vm.fetch_version_json(version_id)
        self.vm.save_version_json(version_id, data)
        return data

    def download_client_jar(self, version_id, progress_callback=None):
        data = self.vm.load_version_json(version_id)
        if data is None:
            data = self.download_version_json(version_id)

        client_info = data.get("downloads", {}).get("client", {})
        if not client_info:
            raise ValueError(f"No client download info for version {version_id}")

        url = rewrite_url_for_mirror(client_info["url"], self.vm.preferred_mirror)
        sha1 = client_info.get("sha1")
        dest = self.vm.get_version_jar_path(version_id)
        try:
            self.download_file(url, dest, sha1=sha1, progress_callback=progress_callback)
        except Exception:
            url2 = f"https://bmclapi2.bangbang93.com/version/{version_id}/client"
            self.download_file(url2, dest, sha1=sha1, progress_callback=progress_callback)
        return dest

    def download_asset_index(self, version_id, progress_callback=None):
        data = self.vm.load_version_json(version_id)
        if data is None:
            data = self.download_version_json(version_id)

        asset_index_info = data.get("assetIndex", {})
        url = asset_index_info.get("url")
        asset_index_id = asset_index_info.get("id")
        sha1 = asset_index_info.get("sha1")

        if not url:
            inherited = data.get("inheritsFrom")
            while inherited:
                pdata = self.vm.load_version_json(inherited)
                if pdata:
                    aii = pdata.get("assetIndex", {})
                    url = aii.get("url")
                    asset_index_id = aii.get("id")
                    sha1 = aii.get("sha1")
                    if url:
                        break
                    inherited = pdata.get("inheritsFrom")
                else:
                    break

        if not url:
            raise ValueError(f"No asset index for version {version_id}")

        dest = os.path.join(self.vm.assets_dir, "indexes", f"{asset_index_id}.json")
        self.download_file(url, dest, sha1=sha1, progress_callback=progress_callback)
        return dest

    def download_assets(self, version_id, progress_callback=None, overall_progress=None):
        data = self.vm.load_version_json(version_id)
        if data is None:
            data = self.download_version_json(version_id)

        asset_index_id = data.get("assetIndex", {}).get("id")
        if not asset_index_id:
            inherited = data.get("inheritsFrom")
            while inherited:
                pdata = self.vm.load_version_json(inherited)
                if pdata:
                    asset_index_id = pdata.get("assetIndex", {}).get("id")
                    if asset_index_id:
                        break
                    inherited = pdata.get("inheritsFrom")
                else:
                    break
        if not asset_index_id:
            asset_index_id = version_id

        asset_index_path = self.vm.get_asset_index_path(asset_index_id)

        if not os.path.exists(asset_index_path):
            self.download_asset_index(version_id, progress_callback=progress_callback)

        with open(asset_index_path, "r", encoding="utf-8") as f:
            asset_index = json.load(f)

        objects = asset_index.get("objects", {})
        total = len(objects)

        if self._aria2 and self._aria2.is_running():
            return self._download_assets_aria2(objects, total, overall_progress)
        else:
            return self._download_assets_requests(objects, total, overall_progress)

    def _download_assets_aria2(self, objects, total, overall_progress):
        tasks = []
        skipped = 0

        for name, info in objects.items():
            h = info["hash"]
            size = info["size"]
            dest = self.vm.get_asset_object_path(h)
            origin = f"https://resources.download.minecraft.net/{h[:2]}/{h}"
            url = rewrite_url_for_mirror(origin, self.vm.preferred_mirror)

            if os.path.exists(dest) and os.path.getsize(dest) == size:
                skipped += 1
                if overall_progress:
                    overall_progress(skipped, total)
                continue

            tasks.append((url, dest, h))

        if not tasks:
            if overall_progress:
                overall_progress(total, total)
            return True, []

        completed = skipped
        failed_list = []

        def aria_progress(done, all_count, clen, tlen, speed):
            current = completed + done
            if overall_progress:
                overall_progress(current, total)

        successes, failures = self._aria2.batch_download(tasks, progress_callback=aria_progress)
        for _, dest in failures:
            failed_list.append(dest)

        self._aria2.clear_completed()
        return len(failures) == 0, failed_list

    def _download_assets_requests(self, objects, total, overall_progress):
        completed = 0
        failed = []

        def download_one(name, info):
            nonlocal completed
            h = info["hash"]
            size = info["size"]
            dest = self.vm.get_asset_object_path(h)
            origin = f"https://resources.download.minecraft.net/{h[:2]}/{h}"
            url = rewrite_url_for_mirror(origin, self.vm.preferred_mirror)
            fallback = f"https://resources.download.minecraft.net/{h[:2]}/{h}"

            if os.path.exists(dest) and os.path.getsize(dest) == size:
                completed += 1
                if overall_progress:
                    overall_progress(completed, total)
                return True

            try:
                self.download_file(url, dest, sha1=h, fallback_url=fallback)
                completed += 1
                if overall_progress:
                    overall_progress(completed, total)
                return True
            except Exception:
                completed += 1
                if overall_progress:
                    overall_progress(completed, total)
                return False

        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = {}
            for name, info in objects.items():
                future = pool.submit(download_one, name, info)
                futures[future] = name

            for future in as_completed(futures):
                if not future.result():
                    failed.append(futures[future])

        return len(failed) == 0, failed

    def download_libraries(self, version_id, progress_callback=None, overall_progress=None):
        data = self.vm.load_version_json(version_id)
        if data is None:
            data = self.download_version_json(version_id)

        libraries = list(data.get("libraries", []))

        inherited = data.get("inheritsFrom")
        visited = set()
        while inherited and inherited not in visited:
            visited.add(inherited)
            parent_data = self.vm.load_version_json(inherited)
            if parent_data:
                libraries.extend(parent_data.get("libraries", []))
                inherited = parent_data.get("inheritsFrom")
            else:
                break

        total = len(libraries)

        if self._aria2 and self._aria2.is_running():
            return self._download_libraries_aria2(libraries, total, overall_progress)
        else:
            return self._download_libraries_requests(libraries, total, overall_progress)

    def _download_libraries_aria2(self, libraries, total, overall_progress):
        tasks = []
        skipped = 0

        for lib in libraries:
            if "downloads" not in lib or "artifact" not in lib["downloads"]:
                skipped += 1
                if overall_progress:
                    overall_progress(skipped, total)
                continue

            artifact = lib["downloads"]["artifact"]
            url = rewrite_url_to_bmclapi(artifact["url"])
            sha1 = artifact.get("sha1")
            size = artifact.get("size", 0)
            path = artifact["path"]
            dest = os.path.join(self.vm.libraries_dir, path)

            if os.path.exists(dest) and os.path.getsize(dest) == size:
                skipped += 1
                if overall_progress:
                    overall_progress(skipped, total)
                continue

            tasks.append((url, dest, sha1))

        if not tasks:
            if overall_progress:
                overall_progress(total, total)
            return True, []

        completed = skipped
        failed_list = []

        def aria_progress(done, all_count, clen, tlen, speed):
            current = completed + done
            if overall_progress:
                overall_progress(current, total)

        successes, failures = self._aria2.batch_download(tasks, progress_callback=aria_progress)

        retry_tasks = {}
        for lib in libraries:
            artifact = lib.get("downloads", {}).get("artifact", {})
            if artifact:
                dest = os.path.join(self.vm.libraries_dir, artifact.get("path", ""))
                for _, fdest in failures:
                    if fdest == dest:
                        fallback = artifact.get("_original_url") or artifact["url"]
                        if fallback:
                            retry_tasks[dest] = fallback
                        break

        for dest, fb_url in retry_tasks.items():
            if fb_url:
                try:
                    url = rewrite_url_to_bmclapi(fb_url) if "bmclapi" not in fb_url else fb_url
                    self.download_file(url, dest, sha1=None, fallback_url=fb_url)
                    if dest in [f for _, f in failures]:
                        failures = [(u, d) for u, d in failures if d != dest]
                except Exception:
                    pass
        for _, dest in failures:
            failed_list.append(dest)

        self._aria2.clear_completed()
        return len(failures) == 0, failed_list

    def _download_libraries_requests(self, libraries, total, overall_progress):
        completed = 0
        failed = []

        def _get_lib_info(lib):
            name = lib.get("name", "")
            dl = lib.get("downloads", {})
            art = dl.get("artifact", {})
            if art.get("path"):
                url = rewrite_url_to_bmclapi(art["url"])
                return (url, art.get("sha1"), art.get("size", 0),
                        art["path"], art.get("_original_url"))
            maven_rel = Downloader._maven_path(name)
            if maven_rel:
                base = lib.get("url", "")
                if base:
                    url = base.rstrip("/") + "/" + maven_rel
                else:
                    url = "https://libraries.minecraft.net/" + maven_rel
                url = rewrite_url_to_bmclapi(url)
                return (url, None, 0, maven_rel, None)
            return None

        def download_one(lib):
            nonlocal completed
            info = _get_lib_info(lib)
            if info is None:
                completed += 1
                if overall_progress:
                    overall_progress(completed, total)
                return True
            url, sha1, size, path, fallback = info
            dest = os.path.join(self.vm.libraries_dir, path)
            if os.path.exists(dest) and os.path.getsize(dest) == size:
                completed += 1
                if overall_progress:
                    overall_progress(completed, total)
                return True
            try:
                self.download_file(url, dest, sha1=sha1,
                                   progress_callback=(lambda n, d, t: None),
                                   fallback_url=fallback)
                completed += 1
                if overall_progress:
                    overall_progress(completed, total)
                return True
            except Exception:
                completed += 1
                if overall_progress:
                    overall_progress(completed, total)
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {}
            for lib in libraries:
                future = pool.submit(download_one, lib)
                futures[future] = lib.get("name", "unknown")

            for future in as_completed(futures):
                if not future.result():
                    failed.append(futures[future])

        return len(failed) == 0, failed

    @staticmethod
    def _maven_path(name):
        parts = name.split(":")
        if len(parts) < 3:
            return None
        group, artifact, version = parts[0], parts[1], parts[2]
        classifier = parts[3] if len(parts) >= 4 else ""
        jar_name = f"{artifact}-{version}"
        if classifier:
            jar_name += f"-{classifier}"
        jar_name += ".jar"
        return os.path.join(group.replace(".", os.sep), artifact, version, jar_name)

    def extract_natives(self, version_id, progress_callback=None):
        data = self.vm.load_version_json(version_id)
        if data is None:
            data = self.download_version_json(version_id)

        system = platform.system().lower()
        if system == "windows":
            system = "windows"
        elif system == "darwin":
            system = "osx"
        else:
            system = "linux"

        arch = platform.machine().lower()
        if arch in ("amd64", "x86_64"):
            arch = "64"
        elif arch in ("i386", "i686", "x86"):
            arch = "32"
        elif arch in ("aarch64", "arm64"):
            arch = "64"
        else:
            arch = "64"

        natives_dir = self.vm.get_natives_dir(version_id)
        if os.path.exists(natives_dir):
            shutil.rmtree(natives_dir)
        os.makedirs(natives_dir, exist_ok=True)

        all_libraries = list(data.get("libraries", []))
        inherited = data.get("inheritsFrom")
        visited = set()
        while inherited and inherited not in visited:
            visited.add(inherited)
            parent_data = self.vm.load_version_json(inherited)
            if parent_data is None:
                parent_data = self.download_version_json(inherited)
            if parent_data:
                all_libraries.extend(parent_data.get("libraries", []))
                inherited = parent_data.get("inheritsFrom")
            else:
                break

        for lib in all_libraries:
            name_parts = (lib.get("name", "")).split(":")
            native_from_name = None
            if len(name_parts) >= 4 and name_parts[3].startswith("natives-"):
                native_from_name = name_parts[3]

            classifiers = lib.get("downloads", {}).get("classifiers", {})
            native_key = f"natives-{system}"

            if arch == "64" and f"natives-{system}-{arch}" in classifiers:
                native_key = f"natives-{system}-{arch}"
            elif arch == "32" and f"natives-{system}-{arch}" in classifiers:
                native_key = f"natives-{system}-{arch}"
            elif native_key not in classifiers and f"natives-{system}" not in classifiers:
                native_key_alt = f"natives-{system.replace('windows', 'windows')}"
                if native_key_alt in classifiers:
                    native_key = native_key_alt

            jar_path = None
            sha1 = None
            url = None
            fallback_url = None

            classifier_info = classifiers.get(native_key)
            if classifier_info is not None:
                jar_path = os.path.join(self.vm.libraries_dir, classifier_info["path"])
                sha1 = classifier_info.get("sha1")
                url = rewrite_url_to_bmclapi(classifier_info["url"])
                fallback_url = classifier_info.get("_original_url")

            if not jar_path and native_from_name:
                parts = native_from_name.split("-")
                if len(parts) >= 2:
                    nb_system = parts[1]
                    if nb_system == system:
                        artifact = lib.get("downloads", {}).get("artifact")
                        if artifact and artifact.get("path"):
                            jar_path = os.path.join(
                                self.vm.libraries_dir, artifact["path"])
                            sha1 = artifact.get("sha1")
                            u = artifact.get("url", "")
                            url = rewrite_url_to_bmclapi(u) if u else ""
                            fallback_url = artifact.get("_original_url")
                        else:
                            maven_rel = self._maven_path(lib.get("name", ""))
                            if maven_rel:
                                jar_path = os.path.join(
                                    self.vm.libraries_dir, maven_rel)
                                sha1 = lib.get("sha1")
                                u = lib.get("url", "")
                                url = rewrite_url_to_bmclapi(u) if u else ""

            if not jar_path:
                continue

            if not os.path.exists(jar_path):
                self.download_file(url, jar_path, sha1=sha1,
                                   fallback_url=fallback_url)

            if os.path.exists(jar_path):
                try:
                    with zipfile.ZipFile(jar_path, "r") as zf:
                        for member in zf.namelist():
                            if member.endswith("/"):
                                continue
                            if ".sha1" in member or ".git" in member:
                                continue
                            is_native = any(
                                member.lower().endswith(ext)
                                for ext in (".dll", ".so", ".dylib", ".jnilib")
                            )
                            if not is_native:
                                continue
                            target = os.path.join(natives_dir, os.path.basename(member))
                            if not os.path.exists(target):
                                with zf.open(member) as src:
                                    with open(target, "wb") as dst:
                                        dst.write(src.read())
                except Exception:
                    pass

        return natives_dir