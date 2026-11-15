"""外部实例/整合包导入。

把任意来源的 Minecraft 实例或整合包（可能是结构混乱的目录、zip 压缩包、
含 .minecraft 的整合包等）识别并导入为 OpenMCL 的版本/实例结构：

    <minecraft_dir>/versions/<version_id>/<version_id>.json   （必需，版本定义）
    <minecraft_dir>/versions/<version_id>/<version_id>.jar     （客户端 jar）
    <minecraft_dir>/versions/<version_id>/                    （运行目录：mods/config 等）

识别策略（不假设外部目录层级）：
  - 递归扫描所有 `.json`，用特征字段（mainClass / minecraftArguments / arguments
    + id / libraries / downloads）判定哪一个是 MC 版本定义，从而即使文件乱放也能找到。
  - 客户端 jar 从「同名 jar / client.jar / downloads.client.url / 目录内 jar」推得。
  - 玩家内容（mods、config、saves、options.txt 等）从内容根目录复制进运行目录，
    自动排除 OpenMCL 自己管理的 versions/libraries/assets 等。
"""

import os
import json
import shutil
import zipfile
import tempfile

# 玩家内容白名单（顶层目录/文件），这些是要纳入"运行目录"的项
PLAYER_ITEMS = {
    "mods", "config", "shaderpacks", "resourcepacks", "saves", "screenshots",
    "logs", "crash-reports", "server.dat", "servers.dat", "options.txt",
    "optionsof.txt", "servicedata", "media", "metas", "videos", "local",
    "usercache.json", "usernamecache.json", "banned-ips.json",
    "banned-players.json", "whitelist.json", "ops.json",
}

# OpenMCL 自行管理的目录，导入时整包跳过
EXCLUDE_TOP = {"versions", "libraries", "assets", "indexes", "natives", "__pycache__"}


def _looks_like_version_json(data):
    """粗判是否为一个 Minecraft 版本描述 json。"""
    if not isinstance(data, dict):
        return False
    if not data.get("id"):
        return False
    has_main = ("mainClass" in data
                or "minecraftArguments" in data
                or "arguments" in data)
    if not has_main:
        return False
    return ("libraries" in data or "downloads" in data or "assetIndex" in data)


def _find_version_specs(root):
    """递归扫描，返回所有疑似 MC 版本定义。"""
    specs = []
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            if not f.endswith(".json"):
                continue
            p = os.path.join(dp, f)
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                    data = json.load(fh)
            except Exception:
                continue
            if _looks_like_version_json(data):
                base = f[:-5]
                specs.append({
                    "json": p,
                    "dir": dp,
                    "base": base,
                    "id": data.get("id") or base,
                    "data": data,
                })
    return specs


def _find_client_jar(spec):
    """为版本定义寻找客户端 jar（多种可能，Robust）。"""
    d = spec["dir"]
    base = spec["base"]
    for cand in (os.path.join(d, base + ".jar"),
                 os.path.join(d, "client.jar"),
                 os.path.join(d, base + "-client.jar"),
                 os.path.join(d, "game.jar")):
        if os.path.isfile(cand):
            return cand
    dl = (spec.get("data") or {}).get("downloads", {}) or {}
    client = dl.get("client", {}) or {}
    url = client.get("url")
    if url:
        n = os.path.basename(url.split("?")[0]).strip()
        cand = os.path.join(d, n)
        if n and os.path.isfile(cand):
            return cand
    # 兜底：同目录下任意非 server jar
    try:
        for f in sorted(os.listdir(d)):
            if f.endswith(".jar") and not f.startswith("server") and not f.startswith("installer"):
                return os.path.join(d, f)
    except Exception:
        pass
    return None


def _pick_main(specs):
    """在多个候选中挑最像主版本的那一个。"""
    def score(s):
        sc = 0
        if "/versions/" in s["json"].replace("\\", "/"):
            sc += 100
        if _find_client_jar(s):
            sc += 50
        if (s.get("data") or {}).get("type"):
            sc += 10
        return sc

    return max(specs, key=score)


def _find_content_root(root):
    """确定“玩家内容根目录”（可能是 .minecraft 子目录，也可能是某些杂乱目录）。"""
    mc = os.path.join(root, ".minecraft")
    if os.path.isdir(mc):
        return mc
    best = root
    best_score = -1
    for dp, dn, _fn in os.walk(root):
        score = len([c for c in dn if c in PLAYER_ITEMS])
        if score > best_score:
            best_score = score
            best = dp
    if best_score <= 0:
        return root
    return best


def _copy_content(content_root, game_dir, skip_srcs):
    """把玩家内容复制到运行目录，返回复制的文件数。"""
    copied = 0
    if not os.path.isdir(content_root):
        return copied
    base = content_root.rstrip("\\/") + os.sep
    for dp, dn, fn in os.walk(content_root):
        rel_dir = os.path.relpath(dp, base)
        if rel_dir == ".":
            rel_dir = ""
        top = rel_dir.split(os.sep)[0] if rel_dir else ""
        if top and top in EXCLUDE_TOP:
            dn[:] = []
            continue
        dest_dir = os.path.join(game_dir, rel_dir) if rel_dir else game_dir
        for f in fn:
            src = os.path.abspath(os.path.join(dp, f))
            if src in skip_srcs:
                continue
            try:
                os.makedirs(dest_dir, exist_ok=True)
                shutil.copy2(src, os.path.join(dest_dir, f))
                copied += 1
            except Exception:
                pass
    return copied


def _unique_id(versions_dir, base_id):
    """生成不与现有实例冲突的 id。"""
    i = 1
    n = base_id
    while os.path.exists(os.path.join(versions_dir, n)):
        n = "{}-imported-{}".format(base_id, i)
        i += 1
    return n


def import_external(source, minecraft_dir, target_id=None, progress_cb=None):
    """导入一个外部的目录或 zip。

    返回 dict：ok / version_id / json / jar / copied / message / error
    """
    if progress_cb:
        progress_cb("scan", 0, "解压/扫描来源...")
    tmp = None
    if os.path.isfile(source) and zipfile.is_zipfile(source):
        tmp = tempfile.mkdtemp(prefix="omcl_import_")
        try:
            with zipfile.ZipFile(source) as z:
                z.extractall(tmp)
            root = tmp
        except Exception as e:
            shutil.rmtree(tmp, ignore_errors=True)
            return {"ok": False, "error": "ZIP 解压失败: {}".format(e)}
    else:
        root = os.path.abspath(source)

    try:
        if not os.path.isdir(root):
            return {"ok": False, "error": "来源目录不存在: {}".format(root)}

        specs = _find_version_specs(root)
        if not specs:
            return {
                "ok": False,
                "error": "未识别到 Minecraft 版本定义。"
                         "来源中需要有含 mainClass/arguments 的版本 json。",
            }

        main = _pick_main(specs)
        in_id = (target_id or main["id"] or main["base"] or "imported").strip()
        if not in_id:
            in_id = "imported"

        versions_dir = os.path.join(minecraft_dir, "versions")
        os.makedirs(versions_dir, exist_ok=True)
        out_id = _unique_id(versions_dir, in_id)
        game_dir = os.path.join(versions_dir, out_id)
        os.makedirs(game_dir, exist_ok=True)

        # 写版本定义
        data = dict(main["data"])
        data["id"] = out_id
        data.setdefault("_openmcl", {})["imported"] = True
        with open(os.path.join(game_dir, out_id + ".json"), "w",
                  encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        skip_srcs = {os.path.abspath(main["json"])}

        # 客户端 jar
        jar = _find_client_jar(main)
        if jar:
            try:
                shutil.copy2(jar, os.path.join(game_dir, out_id + ".jar"))
                skip_srcs.add(os.path.abspath(jar))
            except Exception:
                jar = None

        # 玩家内容
        if progress_cb:
            progress_cb("copy", 1, "整理并复制玩家内容...")
        content_root = _find_content_root(root)
        copied = _copy_content(content_root, game_dir, skip_srcs)

        return {
            "ok": True,
            "version_id": out_id,
            "json": main["json"],
            "jar": jar,
            "copied": copied,
            "message": "已导入实例 '{}'（{} 个玩家文件）。若缺少 assets/libraries，启动时会自动下载。".format(
                out_id, copied),
        }
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)