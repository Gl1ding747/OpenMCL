import os
import json
import time
import shutil
import socket
import zipfile
import tempfile
import subprocess
import threading
import requests

from core.paths import resource_root

ARIA2_DOWNLOAD_URL = (
    "https://github.com/aria2/aria2/releases/download/"
    "release-1.37.0/aria2-1.37.0-win-64bit-build1.zip"
)

ARIA2_RPC_PORT = 6800
ARIA2_RPC_SECRET = "openmcl_aria2"


def _find_free_port(start=6800):
    for port in range(start, start + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


class Aria2Manager:
    def __init__(self, base_dir=None):
        if base_dir is None:
            base_dir = resource_root()
        self.base_dir = base_dir
        self.aria2_dir = os.path.join(base_dir, "aria2")
        self.aria2_exe = os.path.join(self.aria2_dir, "aria2c.exe")
        self._instances = []
        self._lock = threading.Lock()
        self._rr = 0

    # ── aria2c executable management ──────────────────

    def ensure_aria2c(self, progress_callback=None):
        if os.path.isfile(self.aria2_exe):
            return self.aria2_exe

        os.makedirs(self.aria2_dir, exist_ok=True)
        if progress_callback:
            progress_callback(0, "Downloading aria2c ...")

        tmp = tempfile.mkdtemp(prefix="openmcl_aria2_")
        zip_path = os.path.join(tmp, "aria2.zip")
        try:
            resp = requests.get(ARIA2_DOWNLOAD_URL, stream=True, timeout=60, verify=False)
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(zip_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total:
                        pct = downloaded * 100 // total
                        progress_callback(pct, f"Downloading aria2c ... {pct}%")

            if progress_callback:
                progress_callback(100, "Extracting aria2c ...")

            with zipfile.ZipFile(zip_path, "r") as zf:
                for member in zf.namelist():
                    if member.lower().endswith("aria2c.exe"):
                        zf.extract(member, tmp)
                        src = os.path.join(tmp, member)
                        shutil.copy2(src, self.aria2_exe)
                        break
                else:
                    raise RuntimeError("aria2c.exe not found in archive.")

            if progress_callback:
                progress_callback(100, "aria2c ready.")
        finally:
            try:
                shutil.rmtree(tmp, ignore_errors=True)
            except Exception:
                pass

        if not os.path.isfile(self.aria2_exe):
            raise RuntimeError("Failed to install aria2c.")
        return self.aria2_exe

    # ── Daemon lifecycle ──────────────────────────────

    def start(self, split=8, max_concurrent=16, num_instances=1):
        with self._lock:
            if self._instances:
                return
            self.ensure_aria2c()

        for i in range(num_instances):
            port = _find_free_port(ARIA2_RPC_PORT + i * 2)
            url = f"http://127.0.0.1:{port}/jsonrpc"
            session_dir = os.path.join(self.aria2_dir, f"session_{i}")
            os.makedirs(session_dir, exist_ok=True)

            cmd = [
                self.aria2_exe,
                "--enable-rpc",
                f"--rpc-listen-port={port}",
                f"--rpc-secret={ARIA2_RPC_SECRET}",
                "--rpc-allow-origin-all=true",
                f"--dir={self.base_dir}",
                "--max-connection-per-server=16",
                f"--split={split}",
                "--min-split-size=1M",
                f"--max-concurrent-downloads={max_concurrent}",
                "--continue=true",
                "--max-tries=5",
                "--retry-wait=2",
                "--timeout=15",
                "--connect-timeout=10",
                "--check-certificate=false",
                "--quiet=false",
                "--console-log-level=warn",
                f"--save-session={os.path.join(session_dir, 'aria2.session')}",
                "--save-session-interval=10",
                "--disk-cache=32M",
                "--file-allocation=none",
            ]

            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                proc = subprocess.Popen(
                    cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, startupinfo=startupinfo,
                )
            else:
                proc = subprocess.Popen(
                    cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            inst = {"process": proc, "port": port, "url": url}
            time.sleep(0.8)
            for _ in range(20):
                try:
                    test_payload = {
                        "jsonrpc": "2.0", "id": "openmcl",
                        "method": "aria2.getVersion",
                        "params": [f"token:{ARIA2_RPC_SECRET}"],
                    }
                    resp = requests.post(url, json=test_payload, timeout=10, verify=False)
                    if resp and resp.json().get("result"):
                        break
                except Exception:
                    time.sleep(0.3)
            self._instances.append(inst)

    def stop(self):
        with self._lock:
            for inst in self._instances:
                proc = inst.get("process")
                if proc is None:
                    continue
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=1)
                except Exception:
                    pass
                # aria2 有时 terminate 不会立即退出，超时仍在则强制结束
                if proc.poll() is None:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        pass
                # 关机指令最多尽力而为，进程已退出时静默失败
                try:
                    self._rpc_call_to(inst, "aria2.shutdown")
                except Exception:
                    pass
            self._instances.clear()

    def is_running(self):
        with self._lock:
            for inst in self._instances:
                if inst["process"] is not None and inst["process"].poll() is None:
                    return True
        return False

    # ── JSON-RPC helpers ──────────────────────────────

    def _rpc_call_to(self, instance, method, params=None, gid=None):
        payload = {
            "jsonrpc": "2.0",
            "id": "openmcl",
            "method": method,
            "params": [f"token:{ARIA2_RPC_SECRET}"] + (params or []),
        }
        if gid:
            payload["params"] = [f"token:{ARIA2_RPC_SECRET}", gid]
        resp = requests.post(
            instance["url"], json=payload, timeout=10, verify=False
        )
        resp.raise_for_status()
        return resp.json()

    def _rpc_call(self, method, params=None, gid=None):
        return self._rpc_call_to(self._instances[0], method, params, gid)

    # ── Download API ──────────────────────────────────

    def add_uri(self, url, dest_path, sha1=None, instance_idx=0):
        dir_name = os.path.dirname(dest_path)
        file_name = os.path.basename(dest_path)
        options = {"dir": dir_name, "out": file_name}
        if sha1:
            options["checksum"] = f"sha-1={sha1}"
        inst = self._instances[instance_idx % len(self._instances)]
        resp = self._rpc_call_to(inst, "aria2.addUri", [[url], options])
        return resp.get("result", "")

    def download_single(self, url, dest_path, sha1=None, progress_callback=None, timeout=300):
        with self._lock:
            idx = self._rr % len(self._instances)
            self._rr = (self._rr + 1) % len(self._instances)
        inst = self._instances[idx]

        dir_name = os.path.dirname(dest_path)
        file_name = os.path.basename(dest_path)
        options = {"dir": dir_name, "out": file_name, "allow-overwrite": "true"}
        if sha1:
            options["checksum"] = f"sha-1={sha1}"

        resp = self._rpc_call_to(inst, "aria2.addUri", [[url], options])
        gid = resp.get("result", "")
        if not gid:
            raise RuntimeError(f"aria2 addUri failed for {url}")

        started = time.time()
        last_report = time.time()
        consecutive_errors = 0

        while True:
            elapsed = time.time() - started
            if elapsed > timeout:
                raise RuntimeError(f"aria2 download timed out after {int(elapsed)}s: {file_name}")

            if not self.is_running():
                raise RuntimeError("aria2 process is not running")

            try:
                s = self._rpc_call_to(inst, "aria2.tellStatus", params=[gid])
                consecutive_errors = 0
                st = s.get("result", {})
                status = st.get("status", "")

                if status == "complete":
                    dest_norm = os.path.normpath(dest_path)
                    if os.path.isfile(dest_norm) and os.path.getsize(dest_norm) > 0:
                        if progress_callback:
                            size = os.path.getsize(dest_norm)
                            progress_callback(file_name, size, size)
                        return True
                    path = os.path.normpath(os.path.join(
                        st.get("dir", dir_name),
                        st.get("files", [{}])[0].get("path", file_name),
                    ))
                    if path != dest_norm and os.path.isfile(path) and os.path.getsize(path) > 0:
                        os.makedirs(os.path.dirname(dest_norm), exist_ok=True)
                        if os.path.exists(dest_norm):
                            os.remove(dest_norm)
                        shutil.move(path, dest_norm)
                        if progress_callback:
                            size = os.path.getsize(dest_norm)
                            progress_callback(file_name, size, size)
                        return True
                    raise RuntimeError(
                        f"aria2 completed but file missing: {dest_norm}")

                if status in ("error", "removed"):
                    error_msg = st.get("errorMessage", status)
                    raise RuntimeError(f"aria2 download failed: {error_msg}")

                if progress_callback and time.time() - last_report > 0.5:
                    last_report = time.time()
                    clen = int(st.get("completedLength", 0))
                    tlen = int(st.get("totalLength", 0))
                    progress_callback(file_name, clen, max(tlen, 1))

            except (requests.RequestException, ConnectionError) as e:
                consecutive_errors += 1
                if consecutive_errors > 10:
                    raise RuntimeError(f"aria2 RPC unreachable after {consecutive_errors} attempts: {e}")
            except RuntimeError:
                raise

            time.sleep(0.3)

    def batch_download(self, tasks, progress_callback=None):
        ni = len(self._instances) or 1
        inst_tasks = [[] for _ in range(ni)]
        for i, task in enumerate(tasks):
            inst_tasks[i % ni].append(task)

        all_gids = []
        for idx, tlist in enumerate(inst_tasks):
            inst = self._instances[idx]
            for url, dest_path, sha1 in tlist:
                gid = self.add_uri(url, dest_path, sha1, instance_idx=idx)
                if gid:
                    all_gids.append((gid, dest_path, idx))

        if not all_gids:
            return [], []

        gid_set = set(g[0] for g in all_gids)
        gid_info = {g[0]: (g[1], g[2]) for g in all_gids}
        completed = set()
        last_report = time.time()

        while True:
            all_done = True
            stats = {"active": 0, "total": len(gid_set), "speed": 0}

            for gid in list(gid_set):
                if gid in completed:
                    continue
                dest_path, inst_idx = gid_info[gid]
                inst = self._instances[inst_idx]
                try:
                    s = self._rpc_call_to(inst, "aria2.tellStatus", params=[gid])
                    st = s.get("result", {})
                    status = st.get("status", "")
                    if status == "complete":
                        completed.add(gid)
                    elif status in ("error", "removed"):
                        completed.add(gid)
                    else:
                        all_done = False
                        stats["active"] += 1
                        dl_speed = int(st.get("downloadSpeed", 0))
                        stats["speed"] += dl_speed

                        if progress_callback and time.time() - last_report > 0.5:
                            last_report = time.time()
                            progress_callback(
                                len(completed), len(gid_set),
                                int(st.get("completedLength", 0)),
                                int(st.get("totalLength", 0)),
                                dl_speed,
                            )
                except Exception:
                    all_done = False

            if all_done:
                break

            if progress_callback and time.time() - last_report > 0.5:
                last_report = time.time()
                progress_callback(
                    len(completed), len(gid_set),
                    0, 0,
                    stats["speed"],
                )

            time.sleep(0.5)

        successes = []
        failures = []
        for gid, dest_path, _ in all_gids:
            if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
                successes.append(dest_path)
            else:
                failures.append((gid, dest_path))

        return successes, failures

    def clear_completed(self):
        for inst in self._instances:
            try:
                self._rpc_call_to(inst, "aria2.purgeDownloadResult")
            except Exception:
                pass

    # ── Global speed stats ────────────────────────────

    def get_global_stat(self):
        total = {}
        for inst in self._instances:
            try:
                resp = self._rpc_call_to(inst, "aria2.getGlobalStat")
                result = resp.get("result", {})
                for k, v in result.items():
                    if isinstance(v, (int, float)):
                        total[k] = total.get(k, 0) + v
            except Exception:
                pass
        return total