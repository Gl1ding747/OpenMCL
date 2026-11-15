"""统一资源路径解析。

在源码运行（`__file__` 有效）与 PyInstaller 打包（`sys.frozen`）两种场景下，
都能正确定位 OpenMCL 的各类外部资源（aria2 / java / ui 下的插件 / config.json 等）。

打包建议把可变目录（aria2、plugins、java 等）放在可执行文件同级目录，
这样用户可扩展、升级也不互相干扰。
"""

import os
import sys


def resource_root():
    """返回 OpenMCL 的资源根目录。

    - 打包运行（frozen）：可执行文件所在目录；
    - 源码运行：项目根目录（core 的上级）。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(rel=""):
    """返回资源根目录下 rel 的绝对路径。"""
    if not rel:
        return resource_root()
    root = resource_root()
    if getattr(sys, "frozen", False):
        # 依次在 exe 同级目录与打包解压目录(_internal/_MEIPASS)中查找：
        # 可扩展目录(plugins、java、aria2 等)放 exe 同级，静态资源(config、i18n)可用 --add-data 打进包内
        for base in _frozen_bases(root):
            cand = os.path.join(base, rel)
            if os.path.exists(cand):
                return cand
    return os.path.join(root, rel)


def _frozen_bases(root):
    """frozen 运行时可能的资源根：exe 同级 → PyInstaller 解压目录。"""
    bases = [root]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(meipass)
    internal = os.path.join(root, "_internal")
    if os.path.isdir(internal):
        bases.append(internal)
    return bases