import os
import json
import subprocess
import tempfile
import shutil
import zipfile
import requests


def _hide_window():
    """Windows 下隐藏子进程命令窗口（跑 Java 安装器时不再弹 cmd）。"""
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


from pathlib import Path

from .downloader import create_retry_session

requests.packages.urllib3.disable_warnings()

BMCLAPI_BASE = "https://bmclapi2.bangbang93.com"
BMCL_FORGE = f"{BMCLAPI_BASE}/forge"
BMCL_FABRIC_META = f"{BMCLAPI_BASE}/fabric-meta"
BMCL_NEOFORGE = f"{BMCLAPI_BASE}/neoforge"
BMCL_MAVEN = f"{BMCLAPI_BASE}/maven"
BMCL_OPTIFINE = f"{BMCLAPI_BASE}/optifine"

ORIGIN_FORGE_MAVEN = "https://maven.minecraftforge.net"
ORIGIN_FABRIC_MAVEN = "https://maven.fabricmc.net"
ORIGIN_FABRIC_META = "https://meta.fabricmc.net/v2"
ORIGIN_QUILT_META = "https://meta.quiltmc.org/v3"
ORIGIN_QUILT_MAVEN = "https://maven.quiltmc.org/repository/release"

NEOFORGE_RAW_MAVEN = "https://maven.neoforged.net/releases"


class ModLoaderManager:
    def __init__(self, version_manager, downloader):
        self.vm = version_manager
        self.downloader = downloader
        self._session = None

    @property
    def mirror(self):
        return getattr(self.vm, "preferred_mirror", "fastmc")

    @property
    def forge_base(self):
        return BMCL_FORGE

    @property
    def neoforge_base(self):
        return BMCL_NEOFORGE

    @property
    def optifine_base(self):
        return BMCL_OPTIFINE

    @property
    def fabric_meta(self):
        if self.mirror == "origin":
            return ORIGIN_FABRIC_META
        return BMCL_FABRIC_META

    @property
    def quilt_meta(self):
        return ORIGIN_QUILT_META

    @property
    def session(self):
        if self._session is None:
            self._session = create_retry_session()
        return self._session

    def _requests_get(self, url, **kw):
        return self.session.get(url, timeout=30, **kw)

    # ── Forge ───────────────────────────────────────

    def _merge_into_instance(self, mcversion, new_json, new_version_dir,
                              log_callback=None, openmcl_meta=None):
        """Merge a modloader version JSON into the base version, overwriting it."""
        base_json_path = os.path.join(
            self.vm.minecraft_dir, "versions", mcversion, f"{mcversion}.json")
        base = {}
        if os.path.exists(base_json_path):
            with open(base_json_path, "r", encoding="utf-8") as f:
                base = json.load(f)

        original_id = new_json.get("id")
        merged = dict(new_json)
        merged["id"] = mcversion

        inherits_from = merged.pop("inheritsFrom", None)
        if inherits_from and inherits_from != original_id:
            inherited = self.vm.load_version_json(inherits_from)
            if inherited:
                for key in inherited:
                    if key == "id" or key == "libraries":
                        continue
                    if key == "arguments":
                        for sub in ("game", "jvm"):
                            inh_list = inherited.get("arguments", {}).get(sub, [])
                            merged_list = merged.setdefault("arguments", {}).setdefault(sub, [])
                            existing_str = {a for a in merged_list if isinstance(a, str)}
                            for item in inh_list:
                                if isinstance(item, str) and item in existing_str:
                                    continue
                                merged_list.append(item)
                    elif key not in merged:
                        merged[key] = inherited[key]
        else:
            vanilla = self.vm.fetch_version_json(mcversion)
            arg_source = vanilla if vanilla and "arguments" in vanilla else base
            for key in base:
                if key == "id" or key == "libraries":
                    continue
                if key == "arguments":
                    for sub in ("game", "jvm"):
                        src_list = arg_source.get("arguments", {}).get(sub, [])
                        merged_list = merged.setdefault("arguments", {}).setdefault(sub, [])
                        existing_str = {a for a in merged_list if isinstance(a, str)}
                        for item in src_list:
                            if isinstance(item, str) and item in existing_str:
                                continue
                            merged_list.append(item)
                elif key not in merged:
                    merged[key] = base[key]

        existing_names = {l.get("name", "") for l in merged.get("libraries", [])}
        vanilla = self.vm.fetch_version_json(mcversion)
        vanilla_libs = vanilla.get("libraries", []) if vanilla else []
        vanilla_names = {l.get("name", "") for l in vanilla_libs}
        for lib in vanilla_libs:
            if lib.get("name", "") not in existing_names:
                merged.setdefault("libraries", []).append(lib)

        base_ml = base.get("_openmcl", {}).get("modloader", {}).get("type", "")
        new_ml = openmcl_meta.get("type", "") if openmcl_meta else ""
        if base_ml and new_ml and base_ml != new_ml and new_ml != "optifine":
            old_patterns = {
                "forge": ["net.minecraftforge:", "cpw.mods:", "net.minecraftforge.fmlloader:"],
                "neoforge": ["net.neoforged:", "cpw.mods:", "net.neoforged.fmlloader:"],
                "fabric": ["net.fabricmc:"],
                "quilt": ["org.quiltmc:"],
            }
            skip_prefixes = set(old_patterns.get(base_ml, []))
        else:
            skip_prefixes = set()

        for lib in base.get("libraries", []):
            name = lib.get("name", "")
            if not name or name in existing_names or name in vanilla_names:
                continue
            if any(name.startswith(p) for p in skip_prefixes):
                continue
            existing_names.add(name)
            merged.setdefault("libraries", []).append(lib)

        oml = merged.setdefault("_openmcl", {})
        if openmcl_meta:
            ml_type = openmcl_meta.get("type")
            ml_ver = openmcl_meta.get("version", "")
            if ml_type == "optifine":
                oml["optifine"] = ml_ver
            elif ml_type:
                oml["modloader"] = {"type": ml_type, "version": ml_ver}

        os.makedirs(os.path.dirname(base_json_path), exist_ok=True)
        with open(base_json_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2)

        fabric_cache = os.path.join(
            self.vm.minecraft_dir, "versions", mcversion, ".fabric")
        if os.path.exists(fabric_cache):
            import shutil as _shutil
            _shutil.rmtree(fabric_cache, ignore_errors=True)

        if new_version_dir and os.path.isdir(new_version_dir):
            try:
                shutil.rmtree(new_version_dir, ignore_errors=True)
            except Exception:
                pass

        if log_callback:
            log_callback(f"Merged modloader into instance {mcversion}")
        return mcversion

    def get_forge_mc_versions(self):
        url = f"{self.forge_base}/minecraft"
        resp = self._requests_get(url)
        resp.raise_for_status()
        return [v for v in resp.json() if v]

    def get_forge_builds(self, mcversion):
        url = f"{self.forge_base}/minecraft/{mcversion}"
        resp = self._requests_get(url)
        resp.raise_for_status()
        builds = []
        for b in resp.json():
            builds.append({
                "mcversion": b.get("mcversion"),
                "version": b.get("version"),
                "build": b.get("build"),
                "branch": b.get("branch"),
                "modified": b.get("modified"),
            })
        builds.sort(key=lambda x: x.get("build", 0), reverse=True)
        return builds

    def install_forge(self, java_path, mcversion, forge_info, log_callback=None,
                      progress_callback=None):
        ver = forge_info["version"]
        installer_url = (
            f"{ORIGIN_FORGE_MAVEN}/net/minecraftforge/forge/"
            f"{mcversion}-{ver}/forge-{mcversion}-{ver}-installer.jar"
        )

        if log_callback:
            log_callback(f"Downloading Forge {ver} installer...")

        tmp = tempfile.mkdtemp(prefix="openmcl_forge_")
        installer_path = os.path.join(tmp, f"forge-{mcversion}-{ver}-installer.jar")

        try:
            self.downloader.download_file(installer_url, installer_path,
                                          progress_callback=progress_callback)

            forge_id = None
            version_json = None
            uses_install_server = False
            uses_install_client = False
            universal_jar_entry = None
            with zipfile.ZipFile(installer_path, "r") as z:
                names = z.namelist()

                universal_candidates = [
                    n for n in names
                    if ("universal" in n and n.endswith(".jar")
                        and "/" not in n)
                ]
                if universal_candidates:
                    universal_jar_entry = universal_candidates[0]

                has_install_profile = "install_profile.json" in names
                has_version_json = "version.json" in names

                if has_install_profile:
                    profile = json.loads(z.read("install_profile.json").decode("utf-8"))
                    vi = profile.get("versionInfo")
                    if isinstance(vi, dict) and vi.get("id"):
                        version_json = vi
                        forge_id = vi.get("id")
                    uses_install_client = True
                    if not universal_jar_entry:
                        install = profile.get("install", {})
                        fp = install.get("filePath", "")
                        if fp and fp in names:
                            universal_jar_entry = fp
                elif has_version_json:
                    version_json = json.loads(z.read("version.json").decode("utf-8"))
                    forge_id = version_json.get("id")
                    if not universal_jar_entry:
                        uses_install_server = True

            if forge_id is None:
                forge_id = f"{mcversion}-forge-{ver}"

            version_dir = os.path.join(self.vm.minecraft_dir, "versions", forge_id)
            os.makedirs(version_dir, exist_ok=True)

            if uses_install_server:
                if log_callback:
                    log_callback(f"Installing Forge libraries for {forge_id}...")
                cmd = [
                    java_path, "-jar", installer_path,
                    "--installServer", self.vm.minecraft_dir,
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, creationflags=_hide_window(),
                                      timeout=600, cwd=tmp,
                                      encoding="utf-8", errors="replace")
                log_text = (proc.stdout or "") + (proc.stderr or "")
                if log_callback:
                    for line in log_text.split("\n")[:10]:
                        if line.strip():
                            log_callback(f"  [Forge] {line.strip()}")
                if proc.returncode != 0:
                    raise RuntimeError(
                        f"Forge installer failed (code {proc.returncode}).\n{log_text[-500:]}")
            elif uses_install_client:
                if log_callback:
                    log_callback(f"Running Forge {ver} installer (client)...")
                cmd = [
                    java_path, "-jar", installer_path,
                    "--installClient", self.vm.minecraft_dir,
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, creationflags=_hide_window(),
                                      timeout=600, cwd=tmp,
                                      encoding="utf-8", errors="replace")
                log_text = (proc.stdout or "") + (proc.stderr or "")
                if log_callback:
                    for line in log_text.split("\n")[:10]:
                        if line.strip():
                            log_callback(f"  [Forge] {line.strip()}")
                if proc.returncode != 0:
                    raise RuntimeError(
                        f"Forge installer failed (code {proc.returncode}).\n{log_text[-500:]}")
                forge_json_path = os.path.join(version_dir, f"{forge_id}.json")
                if os.path.exists(forge_json_path):
                    with open(forge_json_path, "r", encoding="utf-8") as f:
                        version_json = json.load(f)
            else:
                if log_callback:
                    log_callback(f"Extracting Forge libraries for {forge_id}...")
                if universal_jar_entry is not None:
                    maven_path = "net/minecraftforge/forge/" + universal_jar_entry
                    lib_path = os.path.join(
                        self.vm.minecraft_dir, "libraries",
                        maven_path.replace("/", os.sep),
                    )
                    os.makedirs(os.path.dirname(lib_path), exist_ok=True)
                    with zipfile.ZipFile(installer_path, "r") as z:
                        with z.open(universal_jar_entry) as src:
                            with open(lib_path, "wb") as dst:
                                dst.write(src.read())
                    if log_callback:
                        log_callback(f"  Extracted {universal_jar_entry}")

            if version_json is not None:
                result = self._merge_into_instance(
                    mcversion, version_json, version_dir, log_callback,
                    openmcl_meta={"type": "forge", "version": ver})
                if uses_install_client:
                    try:
                        shutil.rmtree(version_dir, ignore_errors=True)
                    except Exception:
                        pass
                return result

            if log_callback:
                log_callback(f"Forge {ver} installed for {mcversion}")
            return mcversion

        finally:
            try:
                shutil.rmtree(tmp, ignore_errors=True)
            except Exception:
                pass

    # ── Fabric ──────────────────────────────────────

    def get_fabric_loader_versions(self):
        url = f"{self.fabric_meta}/v2/versions/loader"
        resp = self._requests_get(url)
        resp.raise_for_status()
        return resp.json()

    def get_fabric_installer_versions(self):
        url = f"{self.fabric_meta}/v2/versions/installer"
        resp = self._requests_get(url)
        resp.raise_for_status()
        return resp.json()

    def install_fabric(self, java_path, mcversion, loader_version=None,
                       log_callback=None, progress_callback=None):
        if loader_version is None:
            loaders = self.get_fabric_loader_versions()
            loader_version = loaders[0]["version"] if loaders else "0.16.7"

        installer_versions = self.get_fabric_installer_versions()
        installer_ver = installer_versions[0]["version"] if installer_versions else "1.0.0"

        installer_url = (
            f"{ORIGIN_FABRIC_MAVEN}/net/fabricmc/fabric-installer/"
            f"{installer_ver}/fabric-installer-{installer_ver}.jar"
        )

        if log_callback:
            log_callback(f"Downloading Fabric installer {installer_ver}...")

        tmp = tempfile.mkdtemp(prefix="openmcl_fabric_")
        installer_path = os.path.join(tmp, "fabric-installer.jar")

        try:
            self.downloader.download_file(installer_url, installer_path,
                                          progress_callback=progress_callback)

            if log_callback:
                log_callback(f"Running Fabric installer for {mcversion} / loader {loader_version}...")

            cmd = [
                java_path, "-jar", installer_path,
                "client",
                "-dir", self.vm.minecraft_dir,
                "-mcversion", mcversion,
                "-loader", loader_version,
                "-noprofile",
            ]

            proc = subprocess.run(cmd, capture_output=True, text=True, creationflags=_hide_window(),
                                  timeout=120, cwd=tmp,
                                  encoding="utf-8", errors="replace")
            log_text = (proc.stdout or "") + (proc.stderr or "")
            if log_callback:
                for line in log_text.split("\n")[:20]:
                    if line.strip():
                        log_callback(f"  [Fabric] {line.strip()}")
            if proc.returncode != 0:
                raise RuntimeError(
                    f"Fabric installer failed (code {proc.returncode}).\n{log_text[-500:]}")

            fabric_id = f"fabric-loader-{loader_version}-{mcversion}"
            fabric_version_dir = os.path.join(
                self.vm.minecraft_dir, "versions", fabric_id)
            fabric_json_path = os.path.join(fabric_version_dir, f"{fabric_id}.json")
            if os.path.exists(fabric_json_path):
                with open(fabric_json_path, "r", encoding="utf-8") as f:
                    fabric_json = json.load(f)
                return self._merge_into_instance(
                    mcversion, fabric_json, fabric_version_dir, log_callback,
                    openmcl_meta={"type": "fabric", "version": loader_version})

            if log_callback:
                log_callback(f"Fabric Loader {loader_version} installed for {mcversion}")
            return mcversion

        finally:
            try:
                shutil.rmtree(tmp, ignore_errors=True)
            except Exception:
                pass

    # ── NeoForge ───────────────────────────────────

    def get_neoforge_versions(self, mcversion=None):
        if mcversion:
            url = f"{self.neoforge_base}/list/{mcversion}"
        else:
            url = f"{BMCLAPI_BASE}/forge/minecraft"
            resp = self._requests_get(url)
            resp.raise_for_status()
            mc_versions = [v for v in resp.json() if v]
            result = []
            for mv in mc_versions:
                try:
                    sub_result = self.get_neoforge_versions(mv)
                    result.extend(sub_result)
                except Exception:
                    pass
            result.sort(key=lambda x: tuple(
                int(p) for p in (x.get("version", "0").split("."))), reverse=True)
            return result

        resp = self._requests_get(url)
        resp.raise_for_status()
        data = resp.json()
        result = []
        for item in data:
            result.append({
                "mcversion": item.get("mcversion"),
                "version": item.get("version"),
                "rawVersion": item.get("rawVersion"),
                "installerPath": item.get("installerPath", ""),
            })
        result.sort(key=lambda x: tuple(
            int(p) for p in (x.get("version", "0").replace("-beta", "").replace("-alpha", "").split("."))
            if p.isdigit()), reverse=True)
        return result

    def install_neoforge(self, java_path, mcversion, neoforge_info,
                         log_callback=None, progress_callback=None):
        installer_path_rel = neoforge_info.get("installerPath", "")
        if installer_path_rel:
            installer_url = f"{NEOFORGE_RAW_MAVEN}{installer_path_rel}"
        else:
            raw = neoforge_info.get("rawVersion") or f"{mcversion}-{neoforge_info['version']}"
            installer_url = (
                f"{NEOFORGE_RAW_MAVEN}/net/neoforged/neoforge/"
                f"{raw}/neoforge-{raw}-installer.jar"
            )

        if log_callback:
            log_callback(f"Downloading NeoForge {neoforge_info['version']} installer...")

        tmp = tempfile.mkdtemp(prefix="openmcl_neoforge_")
        jar_name = os.path.basename(installer_url) or "neoforge-installer.jar"
        installer_path = os.path.join(tmp, jar_name)

        try:
            self.downloader.download_file(installer_url, installer_path,
                                          progress_callback=progress_callback)

            neoforge_id = f"{mcversion}-neoforge-{neoforge_info['version']}"
            version_json = None
            uses_install_server = False
            universal_jar_entry = None
            with zipfile.ZipFile(installer_path, "r") as z:
                names = z.namelist()
                if "version.json" in names:
                    version_json = json.loads(z.read("version.json").decode("utf-8"))
                    neoforge_id = version_json.get("id", neoforge_id)
                    uses_install_server = True
                elif "install_profile.json" in names:
                    profile = json.loads(z.read("install_profile.json").decode("utf-8"))
                    vi = profile.get("versionInfo")
                    if vi:
                        version_json = vi
                        neoforge_id = vi.get("id", neoforge_id)
                    install = profile.get("install", {})
                    fp = install.get("filePath", "")
                    if fp and fp in names:
                        universal_jar_entry = fp

            version_dir = os.path.join(self.vm.minecraft_dir, "versions", neoforge_id)
            os.makedirs(version_dir, exist_ok=True)

            if uses_install_server:
                if log_callback:
                    log_callback(f"Installing NeoForge libraries for {neoforge_id}...")
                cmd = [
                    java_path, "-jar", installer_path,
                    "--installServer", self.vm.minecraft_dir,
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, creationflags=_hide_window(),
                                      timeout=600, cwd=tmp,
                                      encoding="utf-8", errors="replace")
                log_text = (proc.stdout or "") + (proc.stderr or "")
                if log_callback:
                    for line in log_text.split("\n")[:10]:
                        if line.strip():
                            log_callback(f"  [NeoForge] {line.strip()}")
                if proc.returncode != 0:
                    raise RuntimeError(
                        f"NeoForge installer failed (code {proc.returncode}).\n{log_text[-500:]}")
            else:
                if log_callback:
                    log_callback(f"Extracting NeoForge libraries for {neoforge_id}...")
                if universal_jar_entry is not None:
                    maven_path = "net/neoforged/neoforge/" + universal_jar_entry
                    lib_path = os.path.join(
                        self.vm.minecraft_dir, "libraries",
                        maven_path.replace("/", os.sep),
                    )
                    os.makedirs(os.path.dirname(lib_path), exist_ok=True)
                    with zipfile.ZipFile(installer_path, "r") as z:
                        with z.open(universal_jar_entry) as src:
                            with open(lib_path, "wb") as dst:
                                dst.write(src.read())
                    if log_callback:
                        log_callback(f"  Extracted {universal_jar_entry}")

            if version_json is not None:
                return self._merge_into_instance(
                    mcversion, version_json, version_dir, log_callback,
                    openmcl_meta={"type": "neoforge", "version": neoforge_info['version']})

            if log_callback:
                log_callback(f"NeoForge {neoforge_info['version']} installed for {mcversion}")
            return mcversion

        finally:
            try:
                shutil.rmtree(tmp, ignore_errors=True)
            except Exception:
                pass

    # ── Quilt ──────────────────────────────────────

    def get_quilt_loader_versions(self):
        url = f"{self.quilt_meta}/versions/loader"
        resp = self._requests_get(url)
        resp.raise_for_status()
        return resp.json()

    def install_quilt(self, java_path, mcversion, loader_version=None,
                      log_callback=None, progress_callback=None):
        if loader_version is None:
            loaders = self.get_quilt_loader_versions()
            loader_version = loaders[0]["version"] if loaders else "0.26.0"

        installer_url = (
            f"{ORIGIN_QUILT_MAVEN}/org/quiltmc/quilt-installer/"
            f"0.10.0/quilt-installer-0.10.0.jar"
        )

        if log_callback:
            log_callback(f"Downloading Quilt installer...")

        tmp = tempfile.mkdtemp(prefix="openmcl_quilt_")
        installer_path = os.path.join(tmp, "quilt-installer.jar")

        try:
            self.downloader.download_file(installer_url, installer_path,
                                          progress_callback=progress_callback)

            if log_callback:
                log_callback(f"Running Quilt installer for {mcversion} / loader {loader_version}...")

            cmd = [
                java_path, "-jar", installer_path,
                "install",
                "client",
                mcversion,
                loader_version,
                "--install-dir=" + self.vm.minecraft_dir,
                "--no-profile",
            ]

            proc = subprocess.run(cmd, capture_output=True, text=True, creationflags=_hide_window(),
                                  timeout=120, cwd=tmp,
                                  encoding="utf-8", errors="replace")
            log_text = (proc.stdout or "") + (proc.stderr or "")
            if log_callback:
                for line in log_text.split("\n")[:20]:
                    if line.strip():
                        log_callback(f"  [Quilt] {line.strip()}")
            if proc.returncode != 0:
                raise RuntimeError(
                    f"Quilt installer failed (code {proc.returncode}).\n{log_text[-500:]}")

            quilt_id = f"quilt-loader-{loader_version}-{mcversion}"
            quilt_version_dir = os.path.join(
                self.vm.minecraft_dir, "versions", quilt_id)
            quilt_json_path = os.path.join(quilt_version_dir, f"{quilt_id}.json")
            if os.path.exists(quilt_json_path):
                with open(quilt_json_path, "r", encoding="utf-8") as f:
                    quilt_json = json.load(f)
                return self._merge_into_instance(
                    mcversion, quilt_json, quilt_version_dir, log_callback,
                    openmcl_meta={"type": "quilt", "version": loader_version})

            if log_callback:
                log_callback(f"Quilt Loader {loader_version} installed for {mcversion}")
            return mcversion

        finally:
            try:
                shutil.rmtree(tmp, ignore_errors=True)
            except Exception:
                pass

    # ── OptiFine ───────────────────────────────────

    def get_optifine_versions(self, mcversion):
        url = f"{self.optifine_base}/{mcversion}"
        resp = self._requests_get(url)
        resp.raise_for_status()
        data = resp.json()
        result = []
        for item in data:
            result.append({
                "mcversion": item.get("mcversion"),
                "type": item.get("type"),
                "patch": item.get("patch"),
                "filename": item.get("filename"),
                "version": f"{item.get('type')}_{item.get('patch')}",
            })
        result.sort(key=lambda x: x.get("patch", ""), reverse=True)
        return result

    def install_optifine(self, java_path, mcversion, optifine_info,
                         log_callback=None, progress_callback=None):
        from .launcher import extract_base_mc_version
        base_mc = extract_base_mc_version(mcversion)

        filename = optifine_info.get("filename", "")
        if not filename:
            t = optifine_info["type"]
            patch = optifine_info["patch"]
            filename = f"OptiFine_{base_mc}_{t}_{patch}.jar"

        t = optifine_info.get("type", "")
        patch = optifine_info.get("patch", "")
        manual_url = f"https://optifine.net/downloads"

        if log_callback:
            log_callback(f"Downloading OptiFine {t}_{patch}...")

        tmp = tempfile.mkdtemp(prefix="openmcl_optifine_")
        jar_name = filename
        installer_path = os.path.join(tmp, jar_name)

        try:
            installer_urls = [
                f"{self.optifine_base}/{base_mc}/{t}/{patch}",
                f"{self.optifine_base}/{filename}",
                f"https://optifine.net/downloadx?f={filename}",
            ]

            downloaded_ok = False
            for url in installer_urls:
                try:
                    self.downloader.download_file(url, installer_path,
                                                  progress_callback=progress_callback)
                    with open(installer_path, "rb") as f:
                        magic = f.read(4)
                    if magic[:2] == b"PK":
                        downloaded_ok = True
                        break
                    else:
                        if log_callback:
                            log_callback(f"  Invalid jar from {url.split('/')[2]}, trying next...")
                        os.remove(installer_path)
                except Exception:
                    continue

            if not downloaded_ok:
                raise RuntimeError(
                    f"OptiFine download failed.\n\n"
                    f"OptiFine must be downloaded manually from:\n{manual_url}\n\n"
                    f"Download the file '{filename}' and run it with Java."
                )

            if log_callback:
                log_callback(f"Running OptiFine installer for Minecraft {mcversion}...")

            cmd = [
                java_path, "-jar", installer_path,
            ]

            proc = subprocess.run(cmd, capture_output=True, text=True, creationflags=_hide_window(),
                                  timeout=300, cwd=tmp,
                                  encoding="utf-8", errors="replace")
            log_text = (proc.stdout or "") + (proc.stderr or "")
            if log_callback:
                for line in log_text.split("\n")[:20]:
                    if line.strip():
                        log_callback(f"  [OptiFine] {line.strip()}")
            if proc.returncode != 0:
                raise RuntimeError(
                    f"OptiFine installer failed (code {proc.returncode}).\n{log_text[-500:]}")

            optifine_prefix = f"{mcversion}-OptiFine_"
            versions_root = os.path.join(self.vm.minecraft_dir, "versions")
            optifine_version_dir = None
            optifine_json = None
            for entry in os.listdir(versions_root):
                if entry.startswith(optifine_prefix):
                    candidate_dir = os.path.join(versions_root, entry)
                    if os.path.isdir(candidate_dir):
                        candidate_json = os.path.join(candidate_dir, f"{entry}.json")
                        if os.path.exists(candidate_json):
                            with open(candidate_json, "r", encoding="utf-8") as f:
                                optifine_json = json.load(f)
                            optifine_version_dir = candidate_dir
                            break

            if optifine_json is not None:
                return self._merge_into_instance(
                    mcversion, optifine_json, optifine_version_dir, log_callback,
                    openmcl_meta={"type": "optifine", "version": f"{t}_{patch}"})

            if log_callback:
                log_callback(f"OptiFine {t}_{patch} installed for {mcversion}")
            return mcversion

        finally:
            try:
                shutil.rmtree(tmp, ignore_errors=True)
            except Exception:
                pass