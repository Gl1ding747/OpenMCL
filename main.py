import sys


import os
import traceback

_CRASH_LOG = os.path.join(os.path.expanduser("~"), ".openmcl", "crash.log")
try:
    os.makedirs(os.path.dirname(_CRASH_LOG), exist_ok=True)
except Exception:
    pass


def _handle_crash(et, ev, tb):
    try:
        with open(_CRASH_LOG, "a", encoding="utf-8") as f:
            f.write("=" * 60 + "\n")
            f.write("argv: " + " ".join(sys.argv) + "\n")
            traceback.print_exception(et, ev, tb, file=f)
    except Exception:
        pass


sys.excepthook = _handle_crash

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QLabel
from PyQt5.QtGui import QFont, QPalette, QColor

# ── Global flat dark QSS ──────────────────────────────
GLOBAL_QSS = r"""
/* === ALL WIDGETS === */
* {
    font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
}

/* === MAIN WINDOW + DIALOGS === */
QMainWindow, QDialog {
    background-color: #121214;
}

/* === LABELS === */
QLabel {
    color: #c0c0c0;
    background: transparent;
}

/* === PUSH BUTTONS === */
QPushButton {
    background: #1e1e22;
    color: #c0c0c0;
    border: 1px solid #2a2a30;
    padding: 7px 15px;
    outline: none;
}
QPushButton:hover {
    background: #282830;
    border-color: #3a3a42;
    color: #e0e0e0;
}
QPushButton:pressed {
    background: #16161a;
    border-color: #222228;
}
QPushButton:disabled {
    background: #18181c;
    color: #505050;
    border-color: #222226;
}

/* === LINE EDIT === */
QLineEdit {
    background: #1c1c20;
    color: #e0e0e0;
    border: 1px solid #2a2a30;
    padding: 6px 10px;
    selection-background-color: #3a3a44;
}
QLineEdit:focus {
    border-color: #40404a;
}
QLineEdit:disabled {
    background: #16161a;
    color: #606060;
}

/* === COMBO BOX === */
QComboBox {
    background: #1c1c20;
    color: #e0e0e0;
    border: 1px solid #2a2a30;
    padding: 6px 10px;
    min-height: 20px;
}
QComboBox:hover {
    border-color: #3a3a42;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #808088;
    margin-right: 8px;
}
QComboBox QAbstractItemView {
    background: #1c1c20;
    color: #e0e0e0;
    border: 1px solid #2a2a30;
    selection-background-color: #2a2a34;
    selection-color: #ffffff;
    outline: none;
    padding: 4px;
}

/* === SPIN BOX === */
QSpinBox, QDoubleSpinBox {
    background: #1c1c20;
    color: #e0e0e0;
    border: 1px solid #2a2a30;
    padding: 6px 10px;
}
QSpinBox:hover, QDoubleSpinBox:hover {
    border-color: #3a3a42;
}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    background: #222228;
    border: none;
    width: 18px;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background: #2a2a34;
}
QSpinBox::up-arrow {
    image: none;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-bottom: 4px solid #808088;
    margin-bottom: 2px;
}
QSpinBox::down-arrow {
    image: none;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-top: 4px solid #808088;
    margin-top: 2px;
}

/* === CHECK BOX === */
QCheckBox {
    color: #c0c0c0;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    background: #1c1c20;
    border: 1px solid #2a2a30;
}
QCheckBox::indicator:hover {
    border-color: #3a3a42;
}
QCheckBox::indicator:checked {
    background: #2a2a34;
    border-color: #505060;
}
QCheckBox::indicator:checked:hover {
    background: #343440;
}
QCheckBox::indicator:disabled {
    background: #16161a;
    border-color: #1e1e24;
}

/* === GROUP BOX === */
QGroupBox {
    color: #909098;
    border: 1px solid #202028;
    margin-top: 14px;
    padding-top: 16px;
    font-weight: bold;
    font-size: 11px;
    letter-spacing: 1px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}

/* === FRAME === */
QFrame {
    background: transparent;
}

/* === SCROLL AREA === */
QScrollArea {
    background: transparent;
    border: none;
}
QScrollBar:vertical {
    background: #121214;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #2a2a34;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #363642;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    background: #121214;
    height: 8px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: #2a2a34;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover {
    background: #363642;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

/* === LIST WIDGET === */
QListWidget {
    background: #1a1a1e;
    color: #c0c0c0;
    border: 1px solid #202028;
    outline: none;
}
QListWidget::item {
    padding: 6px 10px;
    border: none;
}
QListWidget::item:selected {
    background: #282830;
    color: #ffffff;
}
QListWidget::item:hover {
    background: #222228;
}

/* === TAB WIDGET === */
QTabWidget::pane {
    border: none;
    background: #121214;
}
QTabBar::tab {
    background: transparent;
    color: #808088;
    padding: 12px 32px;
    border: none;
    border-bottom: 2px solid transparent;
    font-family: "Microsoft YaHei Light";
    font-size: 12px;
    letter-spacing: 0.5px;
}
QTabBar::tab:selected {
    color: #f0f0f0;
    border-bottom: 2px solid #808088;
}
QTabBar::tab:hover {
    color: #d0d0d0;
    background: rgba(255,255,255,0.03);
}

/* === TEXT EDIT === */
QTextEdit, QPlainTextEdit {
    background: #141418;
    color: #d0d0d0;
    border: 1px solid #1e1e24;
    selection-background-color: #2a2a34;
    selection-color: #ffffff;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 9pt;
}
QTextEdit:focus, QPlainTextEdit:focus {
    border-color: #2a2a34;
}

/* === PROGRESS BAR === */
QProgressBar {
    background: #1a1a1e;
    border: 1px solid #202028;
    color: #e0e0e0;
    text-align: center;
    height: 20px;
}
QProgressBar::chunk {
    background: #2a2a34;
}

/* === SPLITTER === */
QSplitter::handle {
    background: #1a1a1e;
}
QSplitter::handle:horizontal {
    width: 1px;
}
QSplitter::handle:vertical {
    height: 1px;
}

/* === TOOLTIP === */
QToolTip {
    background: #1c1c20;
    color: #e0e0e0;
    border: 1px solid #2a2a30;
    padding: 4px 8px;
}

/* === MENU === */
QMenu {
    background: #1c1c20;
    color: #e0e0e0;
    border: 1px solid #2a2a30;
    padding: 4px;
}
QMenu::item {
    padding: 6px 24px;
}
QMenu::item:selected {
    background: #2a2a34;
}

/* === SCROLL AREA WRAPPER === */
QScrollArea#contentScroll {
    background: #121214;
}

/* === DIALOG BUTTON BOX === */
QDialogButtonBox QPushButton {
    min-width: 80px;
}
"""

import os, runpy, traceback

_CRASH_LOG = os.path.join(os.path.expanduser("~"), ".openmcl", "crash.log")

def _excepthook(etype, value, tb):
    try:
        with open(_CRASH_LOG, "a", encoding="utf-8") as f:
            f.write("\n========== CRASH ==========\n")
            traceback.print_exception(etype, value, tb, file=f)
    except Exception:
        pass
sys.excepthook = _excepthook

def _maybe_run_plugin():
    """打包后 exe 以 --run-plugin <path> 重入，用内置 Python 环境跑插件。"""
    if not getattr(sys, "frozen", False):
        return False
    if "--run-plugin" not in sys.argv:
        return False
    try:
        path = sys.argv[sys.argv.index("--run-plugin") + 1]
    except IndexError:
        path = ""
    if not path or not os.path.isfile(path):
        sys.stderr.write("--run-plugin: invalid path: %r\n" % path)
        return True
    pdir = os.path.dirname(os.path.abspath(path))
    sys.path.insert(0, pdir)
    # plugins 根目录（插件所在目录的上一级），保证插件能 import plugin_api 等公共模块
    parent = os.path.dirname(pdir)
    if parent and os.path.isdir(parent):
        sys.path.insert(0, parent)
    os.chdir(pdir)
    runpy.run_path(path, run_name="__main__")
    return True

# Apply early
QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)


from ui.main_window import MainWindow
from i18n import init_lang


def _run_plugin_from_argv():
    """打包运行时：exe 收到 --run-plugin 参数时，用内置解释器执行插件。"""
    if not getattr(sys, "frozen", False):
        return
    if "--run-plugin" not in sys.argv:
        return
    try:
        i = sys.argv.index("--run-plugin")
        path = sys.argv[i + 1]
    except Exception:
        sys.exit(2)
    if not os.path.isfile(path):
        sys.exit(2)
    import runpy
    pdir = os.path.dirname(os.path.abspath(path))
    sys.path.insert(0, pdir)
    try:
        os.chdir(pdir)
        runpy.run_path(path, run_name="__main__")
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
    sys.exit(0)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("OpenMCL")
    app.setApplicationVersion("0.1.0")
    if _maybe_run_plugin():
        sys.exit(0)

    splash = QLabel("OpenMCL")
    splash.setWindowFlags(Qt.SplashScreen)
    splash.setAlignment(Qt.AlignCenter)
    splash.setFixedSize(240, 48)
    splash.setStyleSheet("background:#1c1c20;color:#808080;font-size:14px;")
    splash.show()
    app.processEvents()

    import json, os
    config_path = os.path.join(os.path.expanduser("~"), ".openmcl", "config.json")
    lang = "en"
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            lang = cfg.get("language", "en")
        except Exception:
            pass
    init_lang(lang)

    font = QFont("Microsoft YaHei", 9)
    app.setFont(font)
    app.setStyle("Fusion")
    app.setStyleSheet(GLOBAL_QSS)

    window = MainWindow()
    splash.close()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()