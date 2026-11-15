import os
import json
import re
import time
import socket
import requests

VERSION_MANIFEST_URL = "https://bmclapi2.bangbang93.com/mc/game/version_manifest_v2.json"
LAUNCHERMETA_ORIGINS = [
    "https://launchermeta.mojang.com",
    "https://launcher.mojang.com",
]
LAUNCHERMETA_MIRROR = "https://bmclapi2.bangbang93.com"
LIBRARIES_ORIGIN = "https://libraries.minecraft.net"
LIBRARIES_MIRROR = "https://bmclapi2.bangbang93.com/maven"
ASSETS_ORIGIN = "https://resources.download.minecraft.net"
ASSETS_MIRROR = "https://bmclapi2.bangbang93.com/assets"

BMCLAPI_MAVEN = "https://bmclapi2.bangbang93.com/maven"
MAVEN_ORIGINS = [
    "https://libraries.minecraft.net",
    "https://maven.minecraftforge.net",
    "https://maven.fabricmc.net",
    "https://maven.neoforged.net",
    "https://maven.quiltmc.org",
]

MAVEN_PACKAGE_ORIGIN_MAP = {
    "/net/minecraftforge/": "https://maven.minecraftforge.net",
    "/net/fabricmc/": "https://maven.fabricmc.net",
    "/net/neoforged/": "https://maven.neoforged.net",
    "/org/quiltmc/": "https://maven.quiltmc.org",
}

FASTMC_LAUNCHERMETA = "https://launchermeta.fastmcmirror.org"
FASTMC_LAUNCHER = "https://launcher.fastmcmirror.org"
FASTMC_RESOURCES = "https://resources.fastmcmirror.org"
FASTMC_LIBRARIES = "https://libraries.fastmcmirror.org"


def rewrite_url_to_bmclapi(url):
    for origin in LAUNCHERMETA_ORIGINS:
        if url.startswith(origin):
            return url.replace(origin, LAUNCHERMETA_MIRROR, 1)
    for origin in MAVEN_ORIGINS:
        if url.startswith(origin):
            return url.replace(origin, BMCLAPI_MAVEN, 1)
    if url.startswith(ASSETS_ORIGIN):
        return url.replace(ASSETS_ORIGIN, ASSETS_MIRROR, 1)
    return url


def rewrite_url_to_fastmc(url):
    for origin in LAUNCHERMETA_ORIGINS:
        if url.startswith(origin):
            if "launchermeta" in origin:
                return url.replace(origin, FASTMC_LAUNCHERMETA, 1)
            else:
                return url.replace(origin, FASTMC_LAUNCHER, 1)
    if url.startswith(LIBRARIES_ORIGIN):
        return url.replace(LIBRARIES_ORIGIN, FASTMC_LIBRARIES, 1)
    if url.startswith(ASSETS_ORIGIN):
        return url.replace(ASSETS_ORIGIN, FASTMC_RESOURCES, 1)
    return url


def bmclapi_to_origin(url):
    if url.startswith(BMCLAPI_MAVEN):
        rel = url[len(BMCLAPI_MAVEN):]
        for pkg_prefix, origin in MAVEN_PACKAGE_ORIGIN_MAP.items():
            if rel.startswith(pkg_prefix):
                return origin + rel
        return LIBRARIES_ORIGIN + rel
    if ASSETS_MIRROR in url:
        return url.replace(ASSETS_MIRROR, ASSETS_ORIGIN, 1)
    if LAUNCHERMETA_MIRROR in url:
        for origin in LAUNCHERMETA_ORIGINS:
            if origin in url:
                return url
        path_after = url.split(LAUNCHERMETA_MIRROR, 1)[1] if LAUNCHERMETA_MIRROR in url else ""
        if path_after.startswith("/mc/") or path_after.startswith("/version/"):
            return url.replace(LAUNCHERMETA_MIRROR, LAUNCHERMETA_ORIGINS[0], 1)
    return url


def fastmc_to_origin(url):
    for prefix, origin in [(FASTMC_LIBRARIES, LIBRARIES_ORIGIN),
                            (FASTMC_RESOURCES, ASSETS_ORIGIN),
                            (FASTMC_LAUNCHERMETA, LAUNCHERMETA_ORIGINS[0]),
                            (FASTMC_LAUNCHER, LAUNCHERMETA_ORIGINS[1])]:
        if url.startswith(prefix):
            return url.replace(prefix, origin, 1)
    return url


def mirror_to_origin(url):
    origin = bmclapi_to_origin(url)
    if origin == url:
        origin = fastmc_to_origin(url)
    return origin


MIRROR_NAMES = {
    "fastmc": "FastMinecraftMirror",
    "bmclapi": "BMCLAPI",
    "origin": "Mojang Official",
}


def rewrite_url_for_mirror(url, mirror):
    if mirror == "bmclapi":
        return rewrite_url_to_bmclapi(url)
    if mirror == "fastmc":
        return rewrite_url_to_fastmc(url)
    return url


def get_mirror_chain(url, preferred="fastmc"):
    origin = mirror_to_origin(url)
    if not origin or origin == url:
        origin = url

    chain = []
    ordered = [preferred] + [m for m in ["fastmc", "bmclapi", "origin"] if m != preferred]
    for m in ordered:
        mu = rewrite_url_for_mirror(origin, m)
        if mu and mu not in chain:
            chain.append(mu)
    return chain


def _is_likely_mirror(url):
    return any(k in url for k in ("bmclapi", "fastmcmirror"))


class VersionManager:
    def __init__(self, minecraft_dir=None, preferred_mirror="fastmc"):
        if minecraft_dir is None:
            minecraft_dir = os.path.join(os.path.expanduser("~"), ".openmcl")
        self.minecraft_dir = minecraft_dir
        self.versions_dir = os.path.join(minecraft_dir, "versions")
        self.assets_dir = os.path.join(minecraft_dir, "assets")
        self.libraries_dir = os.path.join(minecraft_dir, "libraries")
        self._version_manifest = None
        self.preferred_mirror = preferred_mirror

        os.makedirs(self.versions_dir, exist_ok=True)
        os.makedirs(self.assets_dir, exist_ok=True)
        os.makedirs(self.libraries_dir, exist_ok=True)

    def _get(self, url, timeout=30, stream=False, fallback_url=None):
        if _is_likely_mirror(url) and not fallback_url:
            fallback_url = mirror_to_origin(url)

        if fallback_url and fallback_url != url:
            urls = get_mirror_chain(fallback_url, preferred=self.preferred_mirror)
        else:
            urls = get_mirror_chain(url, preferred=self.preferred_mirror)

        last_err = None
        for idx, try_url in enumerate(urls):
            is_fallback = (idx > 0)
            max_attempts = 2 if is_fallback else 3
            dl_timeout = (10, 30) if is_fallback else timeout
            for attempt in range(max_attempts):
                try:
                    resp = requests.get(try_url, timeout=dl_timeout, stream=stream, verify=False)
                    resp.raise_for_status()
                    return resp
                except (requests.ConnectionError, requests.Timeout,
                        ConnectionResetError, ConnectionAbortedError,
                        OSError) as e:
                    last_err = e
                    if attempt < max_attempts - 1:
                        time.sleep(0.5 * (2 ** attempt))
                        continue
                    break
                except requests.HTTPError:
                    break
        raise last_err or RuntimeError(f"Request failed: {url}")

    def fetch_version_manifest(self):
        import json as _json
        urls = ["https://bmclapi2.bangbang93.com/mc/game/version_manifest_v2.json",
                "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json",
                "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json",
                "https://launchermeta.fastmcmirror.org/mc/game/version_manifest_v2.json"]
        last_err = None
        for url in urls:
            for attempt in range(3):
                try:
                    resp = requests.get(url, timeout=30, verify=False)
                    resp.raise_for_status()
                    if not resp.text.strip():
                        raise RuntimeError("Empty response from " + url)
                    self._version_manifest = resp.json()
                    return self._version_manifest
                except Exception as e:
                    last_err = e
                    if attempt < 2:
                        import time
                        time.sleep(1)
        raise last_err or RuntimeError("All mirrors failed for version manifest")

    def get_version_list(self):
        if self._version_manifest is None:
            self.fetch_version_manifest()
        return self._version_manifest.get("versions", [])

    def get_latest_release(self):
        if self._version_manifest is None:
            self.fetch_version_manifest()
        return self._version_manifest.get("latest", {}).get("release", "")

    def get_latest_snapshot(self):
        if self._version_manifest is None:
            self.fetch_version_manifest()
        return self._version_manifest.get("latest", {}).get("snapshot", "")

    def get_version_info(self, version_id):
        versions = self.get_version_list()
        for v in versions:
            if v["id"] == version_id:
                return v
        return None

    def fetch_version_json(self, version_id):
        info = self.get_version_info(version_id)
        if info is None:
            raise ValueError(f"Version {version_id} not found in manifest")
        url = rewrite_url_to_bmclapi(info["url"])
        resp = self._get(url, timeout=60, fallback_url=info["url"])
        data = resp.json()
        data = self._rewrite_version_json_urls(data)
        return data

    def _rewrite_version_json_urls(self, data):
        downloads = data.get("downloads", {})
        for key in ("client", "server", "client_mappings", "server_mappings"):
            if key in downloads and "url" in downloads[key]:
                downloads[key]["url"] = rewrite_url_to_bmclapi(downloads[key]["url"])

        asset_index = data.get("assetIndex", {})
        if "url" in asset_index:
            asset_index["url"] = rewrite_url_to_bmclapi(asset_index["url"])

        for lib in data.get("libraries", []):
            for dl_type in ("artifact",):
                artifact = lib.get("downloads", {}).get(dl_type, {})
                if "url" in artifact:
                    artifact["_original_url"] = artifact["url"]
                if "path" in artifact:
                    artifact["url"] = self._build_bmclapi_library_url(artifact)
            for classifier in lib.get("downloads", {}).get("classifiers", {}).values():
                if "url" in classifier:
                    classifier["_original_url"] = classifier["url"]
                if "path" in classifier:
                    classifier["url"] = self._build_bmclapi_library_url(classifier)

        return data

    def _build_bmclapi_library_url(self, artifact):
        path = artifact.get("path", "")
        if path:
            return f"https://bmclapi2.bangbang93.com/maven/{path}"
        return artifact.get("url", "")

    def get_version_json_path(self, version_id):
        return os.path.join(self.versions_dir, version_id, f"{version_id}.json")

    def save_version_json(self, version_id, data):
        version_dir = os.path.join(self.versions_dir, version_id)
        os.makedirs(version_dir, exist_ok=True)
        path = os.path.join(version_dir, f"{version_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return path

    def load_version_json(self, version_id):
        path = self.get_version_json_path(version_id)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_installed_versions(self):
        versions = []
        if not os.path.isdir(self.versions_dir):
            return versions
        for name in os.listdir(self.versions_dir):
            json_path = os.path.join(self.versions_dir, name, f"{name}.json")
            if os.path.isfile(json_path):
                versions.append(name)
        return versions

    def get_version_jar_path(self, version_id):
        return os.path.join(self.versions_dir, version_id, f"{version_id}.jar")

    def get_asset_index_path(self, asset_index_id):
        return os.path.join(self.assets_dir, "indexes", f"{asset_index_id}.json")

    def get_asset_object_path(self, hash_value):
        return os.path.join(self.assets_dir, "objects", hash_value[:2], hash_value)

    def get_library_path(self, package, name, version):
        parts = package.split(".")
        path = os.path.join(self.libraries_dir, *parts, name, version)
        return path

    def _should_download_lib(self, lib):
        name = lib.get("name", "")
        downloads = lib.get("downloads", {})
        artifact = downloads.get("artifact", {})
        if not artifact.get("path"):
            classifiers = downloads.get("classifiers", {})
            has_native = any(k.startswith("natives-") for k in classifiers)
            if classifiers and has_native and not artifact:
                return False
            maven_rel = self._maven_path(name)
            if maven_rel:
                return True
            return False
        rules = lib.get("rules", [])
        if rules:
            import platform
            current_os = platform.system().lower()
            if current_os == "windows":
                current_os = "windows"
            allowed = False
            for rule in rules:
                action = rule.get("action", "allow")
                os_cond = rule.get("os", {})
                os_name = os_cond.get("name", "")
                if os_name and os_name != current_os:
                    continue
                if action == "allow":
                    allowed = True
                elif action == "disallow":
                    allowed = False
            if not allowed and rules:
                return False
        return True

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

    def _get_lib_path(self, lib):
        artifact = lib.get("downloads", {}).get("artifact", {})
        path = artifact.get("path", "")
        if path:
            return os.path.join(self.libraries_dir, path)
        return ""

    def _download_lib(self, lib):
        import requests as _requests
        artifact = lib.get("downloads", {}).get("artifact", {})
        url = artifact.get("url", "")
        path = artifact.get("path", "")
        if not url or not path:
            maven_rel = self._maven_path(lib.get("name", ""))
            if maven_rel:
                path = maven_rel
                url_from_json = lib.get("url", "")
                if url_from_json:
                    url = url_from_json.rstrip("/") + "/" + maven_rel
                else:
                    url = "https://libraries.minecraft.net/" + maven_rel
            else:
                return
        dest = os.path.join(self.libraries_dir, path)
        if os.path.isfile(dest):
            return
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        bmcl_url = rewrite_url_to_bmclapi(url)
        final_url = bmcl_url
        r = None
        for try_url in [bmcl_url, url, artifact.get("_original_url", "")]:
            if not try_url:
                continue
            try:
                r = _requests.get(try_url, timeout=30)
                if r.status_code == 200:
                    break
            except Exception:
                continue
        if r is None or r.status_code != 200:
            raise IOError(f"Failed to download library {path}")
        with open(dest, "wb") as f:
            f.write(r.content)

    def get_natives_dir(self, version_id):
        return os.path.join(self.versions_dir, version_id, "natives")

    def get_version_game_dir(self, version_id):
        return os.path.join(self.versions_dir, version_id)


def network_available(host="8.8.8.8", port=53, timeout=2.0):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except (OSError, socket.error):
        pass

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(("223.5.5.5", port))
        s.close()
        return True
    except (OSError, socket.error):
        return False