import os
import json
import shutil
import subprocess
import platform
import re
import glob as glob_mod


_JAVA_CACHE = None


def _hide_window():
    """Windows 下隐藏子进程命令窗口（检测/启动 Java 时不再弹 cmd）。"""
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def find_java(force=False):
    global _JAVA_CACHE
    if _JAVA_CACHE is not None and not force:
        return _JAVA_CACHE

    candidates = []

    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidates.append(os.path.join(java_home, "bin", "java.exe" if os.name == "nt" else "java"))

    path_ext = os.environ.get("PATH", "")
    for p in path_ext.split(os.pathsep):
        exe = os.path.join(p, "java.exe" if os.name == "nt" else "java")
        if os.path.isfile(exe):
            candidates.append(exe)

    if os.name == "nt":
        common_roots = [
            os.environ.get("ProgramFiles", "C:\\Program Files"),
            os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"),
            os.environ.get("ProgramW6432", "C:\\Program Files"),
            "C:\\Program Files\\Eclipse Adoptium",
            "C:\\Program Files\\Eclipse Foundation",
            "C:\\Program Files\\Microsoft",
            os.path.join(os.path.expanduser("~"), ".jdks"),
            os.path.join(os.path.expanduser("~"), ".sdkman"),
        ]
        for root in common_roots:
            if not os.path.isdir(root):
                continue
            try:
                for item in os.listdir(root):
                    java_path = os.path.join(root, item, "bin", "java.exe")
                    if os.path.isfile(java_path):
                        candidates.append(java_path)
                    java_path = os.path.join(root, item, "jre", "bin", "java.exe")
                    if os.path.isfile(java_path):
                        candidates.append(java_path)
            except PermissionError:
                continue

        for pattern in [
            "C:\\Program Files\\Java\\jdk*\\bin\\java.exe",
            "C:\\Program Files\\Java\\jre*\\bin\\java.exe",
            "C:\\Program Files (x86)\\Java\\jdk*\\bin\\java.exe",
            "C:\\Program Files (x86)\\Java\\jre*\\bin\\java.exe",
        ]:
            for p in glob_mod.glob(pattern):
                candidates.append(p)
    else:
        for root in ["/usr/lib/jvm", "/usr/local/lib/jvm", "/Library/Java/JavaVirtualMachines"]:
            if not os.path.isdir(root):
                continue
            try:
                for item in os.listdir(root):
                    java_path = os.path.join(root, item, "bin", "java")
                    if os.path.isfile(java_path):
                        candidates.append(java_path)
                    java_path = os.path.join(root, item, "jre", "bin", "java")
                    if os.path.isfile(java_path):
                        candidates.append(java_path)
            except PermissionError:
                continue

    valid = []
    seen = set()
    for c in candidates:
        c = os.path.abspath(c)
        if c in seen:
            continue
        seen.add(c)
        try:
            result = subprocess.run([c, "-version"], capture_output=True, text=True, timeout=10, creationflags=_hide_window())
            output = result.stdout + result.stderr
            version_match = re.search(r'(\d+\.\d+\.\d+[_\d]*)', output)
            version_str = version_match.group(1) if version_match else "unknown"
            arch_match = re.search(r'64-[Bb]it|64 [Bb]it', output)
            arch = "64" if arch_match else "32"
            valid.append({
                "path": c,
                "version": version_str,
                "arch": arch,
            })
        except Exception:
            continue

    valid.sort(key=lambda x: _version_key(x["version"]), reverse=True)
    _JAVA_CACHE = valid
    return valid


def _version_key(v):
    try:
        parts = v.replace("_", ".").split(".")
        return tuple(int(p) for p in parts[:4])
    except Exception:
        return (0,)


def get_java_major_version(java_path):
    try:
        result = subprocess.run(
            [java_path, "-version"],
            capture_output=True, text=True, timeout=10,
            creationflags=_hide_window()
        )
        output = result.stdout + result.stderr
        m = re.search(r'version "(\d+)(?:\.(\d+))?', output)
        if m:
            major = int(m.group(1))
            if major == 1:
                return int(m.group(2))
            return major
    except Exception:
        pass
    return 0


def get_mc_required_java(mc_version_str):
    base = extract_base_mc_version(mc_version_str)
    return _get_mc_required_java_from_str(base)


def _get_mc_required_java_from_str(ver_str):
    try:
        parts = [int(x) for x in ver_str.split(".")]
        if len(parts) < 2:
            return 0
        major = parts[0]
        minor = parts[1]
        patch = parts[2] if len(parts) > 2 else 0

        if major > 1 or (major == 1 and minor >= 21) or (major == 1 and minor == 20 and patch >= 5):
            return 21
        if (major == 1 and minor >= 18) or major > 1:
            return 17
        if major == 1 and minor == 17:
            return 16
        return 8
    except Exception:
        return 0


def extract_base_mc_version(version_id):
    import re
    m = re.match(r'^(\d+\.\d+(?:\.\d+)?)', version_id)
    if m:
        return m.group(1)
    m = re.search(r'(\d+\.\d+(?:\.\d+)?)$', version_id)
    if m:
        return m.group(1)
    return version_id


def get_mc_required_java_for_version(vm, version_id):
    base_mc = extract_base_mc_version(version_id)
    required = _get_mc_required_java_from_str(base_mc)
    if required > 0:
        return required
    data = vm.load_version_json(version_id)
    if data:
        java_ver = data.get("javaVersion", {}).get("majorVersion", 0)
        if java_ver:
            return java_ver
        inherited = data.get("inheritsFrom")
        while inherited:
            pdata = vm.load_version_json(inherited)
            if pdata:
                jv = pdata.get("javaVersion", {}).get("majorVersion", 0)
                if jv:
                    return jv
                inherited = pdata.get("inheritsFrom")
            else:
                break
    return 0


def find_java_for_mc(mc_version_str):
    all_java = find_java()
    required = get_mc_required_java(mc_version_str)
    if required == 0 or not all_java:
        return all_java

    compatible = []
    incompatible = []
    for j in all_java:
        j_major = get_java_major_version(j["path"]) if j["version"] != "unknown" else 0
        if j_major >= required:
            j["jvm_major"] = j_major
            compatible.append(j)
        else:
            j["jvm_major"] = j_major
            incompatible.append(j)

    return compatible + incompatible


class MinecraftLauncher:
    def __init__(self, version_manager, downloader, account_manager):
        self.vm = version_manager
        self.downloader = downloader
        self.am = account_manager

    def _get_system_native_name(self):
        system = platform.system().lower()
        if system == "windows":
            return "windows"
        elif system == "darwin":
            return "osx"
        else:
            return "linux"

    def _get_arch_name(self):
        machine = platform.machine().lower()
        if machine in ("amd64", "x86_64"):
            return "64"
        elif machine in ("i386", "i686", "x86"):
            return "32"
        elif machine in ("aarch64", "arm64"):
            return "64"
        return "64"

    def _get_os_name_for_rule(self):
        system = platform.system().lower()
        if system == "windows":
            return "windows"
        elif system == "darwin":
            return "osx"
        else:
            return "linux"

    def _check_rule(self, rule):
        action = rule.get("action", "allow")

        features = rule.get("features", {})
        if features:
            if features.get("is_demo_user"):
                return action != "allow"
            if features.get("has_custom_resolution"):
                return action == "allow"
            return action != "allow"

        os_restriction = rule.get("os", {})
        if not os_restriction:
            return action == "allow"

        rule_os_name = os_restriction.get("name")
        if rule_os_name and rule_os_name != self._get_os_name_for_rule():
            return action != "allow"

        rule_arch = os_restriction.get("arch")
        if rule_arch and rule_arch != self._get_arch_name():
            return action != "allow"

        return action == "allow"

    def _check_rules(self, rules):
        if not rules:
            return True
        allowed = False
        for rule in rules:
            if self._check_rule(rule):
                allowed = True
            else:
                return False
        return allowed

    def _setup_shared_saves(self, version_id, data):
        oml = data.get("_openmcl", {})
        if not oml.get("shared_saves"):
            return
        shared_dir = os.path.join(self.vm.minecraft_dir, "shared_saves")
        os.makedirs(shared_dir, exist_ok=True)
        saves_dir = os.path.join(
            self.vm.get_version_game_dir(version_id), "saves")
        if os.path.islink(saves_dir):
            try:
                os.unlink(saves_dir)
            except Exception:
                pass
        if os.path.isdir(shared_dir) and os.listdir(shared_dir):
            self._sync_dirs(shared_dir, saves_dir)

    def _count_files(self, src_dir):
        count = 0
        for root, dirs, files in os.walk(src_dir):
            count += len(files)
        return max(count, 1)

    def _sync_dirs(self, src_dir, dst_dir, progress_callback=None):
        count = self._count_files(src_dir)
        copied = [0]
        for root, dirs, files in os.walk(src_dir):
            rel = os.path.relpath(root, src_dir)
            target_dir = os.path.join(dst_dir, rel)
            os.makedirs(target_dir, exist_ok=True)
            for name in files:
                sf = os.path.join(root, name)
                df = os.path.join(target_dir, name)
                try:
                    mtime_src = os.path.getmtime(sf)
                except OSError:
                    mtime_src = 0
                try:
                    mtime_dst = os.path.getmtime(df)
                except OSError:
                    mtime_dst = 0
                if mtime_src <= mtime_dst:
                    copied[0] += 1
                    continue
                try:
                    shutil.copy2(sf, df)
                except Exception:
                    pass
                copied[0] += 1
                if progress_callback:
                    progress_callback(copied[0], count)

    def sync_shared_saves(self, version_id, direction="to_shared", progress_callback=None):
        data = self.vm.load_version_json(version_id)
        if not data:
            return False
        oml = data.get("_openmcl", {})
        if not oml.get("shared_saves"):
            return False
        shared_dir = os.path.join(self.vm.minecraft_dir, "shared_saves")
        os.makedirs(shared_dir, exist_ok=True)
        saves_dir = os.path.join(
            self.vm.get_version_game_dir(version_id), "saves")
        if os.path.islink(saves_dir):
            try:
                os.unlink(saves_dir)
            except Exception:
                pass
        os.makedirs(saves_dir, exist_ok=True)
        if direction == "to_shared":
            self._sync_dirs(saves_dir, shared_dir, progress_callback)
        else:
            self._sync_dirs(shared_dir, saves_dir, progress_callback)
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

    def get_classpath(self, version_id):
        data = self.vm.load_version_json(version_id)
        if data is None:
            raise ValueError(f"Version {version_id} not installed")

        classpath_parts = []
        missing = []
        seen_artifacts = set()

        all_libraries = list(data.get("libraries", []))
        inherited = data.get("inheritsFrom")
        visited = set()
        while inherited and inherited not in visited:
            visited.add(inherited)
            parent_data = self.vm.load_version_json(inherited)
            if parent_data:
                all_libraries.extend(parent_data.get("libraries", []))
                inherited = parent_data.get("inheritsFrom")
            else:
                break

        for lib in all_libraries:
            rules = lib.get("rules")
            if rules and not self._check_rules(rules):
                continue

            name = lib.get("name", "")
            parts = name.split(":") if name else []
            classifier = parts[3] if len(parts) >= 4 else ""
            if classifier and classifier.startswith("natives-"):
                continue
            artifact_key = ":".join(parts[:2]) if len(parts) >= 2 else name
            if classifier:
                artifact_key = artifact_key + ":" + classifier
            if artifact_key and artifact_key in seen_artifacts:
                continue

            artifact = lib.get("downloads", {}).get("artifact")
            mrel = artifact.get("path") if artifact else None
            if not mrel:
                mrel = self._maven_path(name)
            if not mrel:
                continue

            seen_artifacts.add(artifact_key)
            path = os.path.join(self.vm.libraries_dir, mrel)
            if os.path.exists(path):
                classpath_parts.append(os.path.abspath(path))
            else:
                n = name or os.path.basename(mrel)
                missing.append(n)

        jar_path = self.vm.get_version_jar_path(version_id)
        found_jar = os.path.exists(jar_path)
        if not found_jar:
            inherited = data.get("inheritsFrom")
            while inherited and not found_jar:
                inherited_jar = self.vm.get_version_jar_path(inherited)
                if os.path.exists(inherited_jar):
                    jar_path = inherited_jar
                    found_jar = True
                    break
                parent_data = self.vm.load_version_json(inherited)
                inherited = parent_data.get("inheritsFrom") if parent_data else None

        if found_jar:
            classpath_parts.append(os.path.abspath(jar_path))
        else:
            missing.append("client.jar")

        return classpath_parts, missing

    def _resolve_arguments(self, args, variables, features=None):
        if features is None:
            features = {}

        resolved = []
        for arg in args:
            if isinstance(arg, str):
                resolved.append(self._replace_vars(arg, variables))
            elif isinstance(arg, dict):
                rules = arg.get("rules", [])
                if not self._check_rules(rules):
                    continue

                value_list = arg.get("value", [])
                if isinstance(value_list, str):
                    resolved.append(self._replace_vars(value_list, variables))
                elif isinstance(value_list, list):
                    for val in value_list:
                        resolved.append(self._replace_vars(val, variables))
            elif isinstance(arg, list):
                for val in arg:
                    resolved.append(self._replace_vars(val, variables))
        return resolved

    def _replace_vars(self, s, variables):
        if not isinstance(s, str):
            return s
        for key, value in variables.items():
            s = s.replace(key, str(value))
        return s

    def build_launch_command(self, version_id, java_path="java",
                              max_memory=2048, min_memory=None,
                              jvm_args=None, game_args_list=None,
                              width=None, height=None,
                              fullscreen=False,
                              server=None, port=None):

        data = self.vm.load_version_json(version_id)
        if data is None:
            raise ValueError(f"Version {version_id} not installed. Please download it first.")

        account = self.am.current_account
        if account is None:
            raise ValueError("No account selected. Please login first.")

        natives_dir = os.path.abspath(self.vm.get_natives_dir(version_id))

        self.downloader.extract_natives(version_id)

        self._setup_shared_saves(version_id, data)

        classpath_parts, missing_libs = self.get_classpath(version_id)
        classpath = os.pathsep.join(classpath_parts)

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
        assets_dir = os.path.abspath(self.vm.assets_dir)
        game_dir = os.path.abspath(self.vm.get_version_game_dir(version_id))

        main_class = data.get("mainClass")
        if not main_class:
            raise ValueError(f"No main class defined for version {version_id}")

        if main_class == "net.minecraft.launchwrapper.Launch":
            found_launchwrapper = any(
                "launchwrapper" in p.lower() or "forge" in os.path.basename(p).lower()
                for p in classpath_parts)
            if not found_launchwrapper:
                import logging
                logging.warning(
                    "Forge main class detected but no launchwrapper/forge jar in "
                    "classpath. Falling back to vanilla main class.")
                main_class = "net.minecraft.client.main.Main"

        if missing_libs:
            sample = missing_libs[:5]
            detail = ", ".join(sample)
            if len(missing_libs) > 5:
                detail += f" ... and {len(missing_libs) - 5} more"
            raise RuntimeError(
                f"{len(missing_libs)} library file(s) missing: {detail}. "
                "Reinstall the version to fix this."
            )

        variables = {
            "${auth_player_name}": account.username,
            "${version_name}": version_id,
            "${game_directory}": game_dir,
            "${assets_root}": assets_dir,
            "${game_assets}": assets_dir,
            "${assets_index_name}": asset_index_id,
            "${auth_uuid}": account.uuid,
            "${auth_access_token}": account.access_token,
            "${user_type}": "mojang",
            "${version_type}": data.get("type", "release"),
            "${natives_directory}": natives_dir,
            "${library_directory}": os.path.abspath(self.vm.libraries_dir),
            "${classpath_separator}": os.pathsep,
            "${launcher_name}": "OpenMCL",
            "${launcher_version}": "0.1.0",
            "${classpath}": classpath,
            "${clientid}": "openMCL",
            "${auth_xuid}": "",
            "${user_properties}": "{}",
            "${resolution_width}": str(width or 854),
            "${resolution_height}": str(height or 480),
        }

        cmd = [java_path]

        if max_memory and max_memory > 0:
            cmd.append(f"-Xmx{max_memory}m")
        if min_memory and min_memory > 0:
            cmd.append(f"-Xms{min_memory}m")

        cmd.append(f"-Djava.library.path={natives_dir}")
        cmd.append(f"-Dorg.lwjgl.librarypath={natives_dir}")

        encoding = "UTF-8"
        cmd.append(f"-Dfile.encoding={encoding}")
        cmd.append("-Djava.rmi.server.useCodebaseOnly=true")
        cmd.append("-Dcom.sun.jndi.rmi.object.trustURLCodebase=false")
        cmd.append("-Dcom.sun.jndi.cosnaming.object.trustURLCodebase=false")
        cmd.append("-Dlog4j2.formatMsgNoLookups=true")

        cmd.append(f"-Dminecraft.client.jar={self.vm.get_version_jar_path(version_id)}")

        if platform.system() == "Windows":
            cmd.append("-Djava.net.useSystemProxies=true")

        java_major = get_java_major_version(java_path)
        if java_major >= 17:
            add_opens_pairs = [
                ("java.base/java.lang", "ALL-UNNAMED"),
                ("java.base/java.lang.invoke", "ALL-UNNAMED"),
                ("java.base/java.lang.reflect", "ALL-UNNAMED"),
                ("java.base/java.io", "ALL-UNNAMED"),
                ("java.base/java.net", "ALL-UNNAMED"),
                ("java.base/sun.security.util", "ALL-UNNAMED"),
                ("java.base/java.text", "ALL-UNNAMED"),
                ("java.desktop/java.awt.font", "ALL-UNNAMED"),
            ]
            for module_pkg, target in add_opens_pairs:
                spec = f"{module_pkg}={target}"
                if spec not in cmd:
                    cmd.append("--add-opens")
                    cmd.append(spec)

        if jvm_args:
            for arg in jvm_args:
                cmd.append(arg)

        arguments_block = data.get("arguments", {})
        jvm_args_list = arguments_block.get("jvm", [])

        if jvm_args_list:
            resolved_jvm = self._resolve_arguments(jvm_args_list, variables)
            for arg in resolved_jvm:
                cmd.append(arg)

        has_cp = any(
            (a == "-cp" or a == "-classpath"
             or a.startswith("-cp ") or a.startswith("-classpath "))
            for a in cmd)
        if not has_cp:
            cmd.append("-cp")
            cmd.append(classpath)

        cmd.append(main_class)

        game_args = arguments_block.get("game", [])
        minecraft_args = data.get("minecraftArguments")
        all_game_args = []

        if minecraft_args:
            tokens = minecraft_args.split()
            for token in tokens:
                all_game_args.append(self._replace_vars(token, variables))

        if game_args:
            features = {}
            if width and height:
                features["has_custom_resolution"] = True
            resolved_game = self._resolve_arguments(game_args, variables, features)
            all_game_args.extend(resolved_game)

        if server:
            all_game_args.extend(["--server", server])
            all_game_args.extend(["--port", str(port or 25565)])

        if fullscreen:
            all_game_args.append("--fullscreen")

        if width and height:
            w_arg = str(width)
            h_arg = str(height)
            try:
                wi = all_game_args.index("--width")
                all_game_args[wi + 1] = w_arg
            except ValueError:
                all_game_args.extend(["--width", w_arg])
            try:
                hi = all_game_args.index("--height")
                all_game_args[hi + 1] = h_arg
            except ValueError:
                all_game_args.extend(["--height", h_arg])

        if game_args_list:
            for arg in game_args_list:
                all_game_args.append(arg)

        cmd.extend(all_game_args)

        return cmd

    def launch(self, version_id, java_path="java",
               max_memory=2048, min_memory=None,
               jvm_args=None, game_args_list=None,
               width=None, height=None,
               fullscreen=False, callback=None):

        cmd = self.build_launch_command(
            version_id=version_id,
            java_path=java_path,
            max_memory=max_memory,
            min_memory=min_memory,
            jvm_args=jvm_args,
            game_args_list=game_args_list,
            width=width,
            height=height,
            fullscreen=fullscreen,
        )

        game_dir = os.path.abspath(self.vm.get_version_game_dir(version_id))

        env = os.environ.copy()

        from core.process_isolation import ProcessIsolation
        self._process_isolation = ProcessIsolation()

        process = subprocess.Popen(
            cmd,
            cwd=game_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=_hide_window(),
        )

        self._process_isolation.isolate_process(process.pid)

        if callback:
            import threading
            def read_output():
                for line in iter(process.stdout.readline, ""):
                    callback(line.rstrip("\n"))
                process.stdout.close()

            t = threading.Thread(target=read_output, daemon=True)
            t.start()

        return process