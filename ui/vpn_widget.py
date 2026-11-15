import os
import sys
import json
import subprocess
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QLabel, QComboBox, QLineEdit, QFrame,
                             QMessageBox, QInputDialog, QApplication,
                             QStackedWidget, QFileDialog, QTextEdit,
                             QPlainTextEdit)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QThread, QProcess
from PyQt5.QtGui import QFont

from core.vpn_manager import (
    load_servers, save_servers, generate_psk,
    check_platform, vpn_exists, create_client_vpn, remove_client_vpn,
    connect_vpn, disconnect_vpn, get_connection_status, get_vpn_ip,
    start_vpn_server, stop_vpn_server, get_server_status,
    get_local_ip, get_public_ip,
)

from ui.p2p_widget import P2PWidget

ROW_STYLE = "color: #808088; font-size: 12px;"
VAL_STYLE = "color: #c0c0c0; font-size: 13px; font-weight: bold;"
VAL_BLUE = "color: #7090d0; font-size: 13px; font-weight: bold;"
VAL_GREEN = "color: #50c878; font-size: 13px; font-weight: bold;"
VAL_DIM = "color: #505058; font-size: 11px;"
TITLE_STYLE = "color: #505058; font-size: 10px; font-weight: bold; letter-spacing: 2px; padding: 4px 0;"

BTN_STYLE = """
QPushButton {
    background: transparent; color: #808088; border: 1px solid #222228;
    padding: 6px 10px; font-size: 11px; letter-spacing: 0.5px;
}
QPushButton:hover {
    background: #222228; color: #c0c0c0; border-color: #303038;
}
"""
ACCENT_STYLE = """
QPushButton {
    background: #282830; color: #e8e8e8; border: 1px solid #383840;
    padding: 8px 12px; font-size: 12px; font-weight: bold;
}
QPushButton:hover {
    background: #32323c; border-color: #4a4a54;
}
"""
STOP_STYLE = """
QPushButton {
    background: #2a1e1e; color: #e06060; border: 1px solid #382828;
    padding: 8px 12px; font-size: 12px; font-weight: bold;
}
QPushButton:hover {
    background: #342828; border-color: #483030;
}
"""
COMBO_STYLE = """
QComboBox {
    background: #1a1a20; color: #c0c0c0; border: 1px solid #2a2a32;
    padding: 4px 8px; font-size: 12px;
}
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView {
    background: #1a1a20; color: #c0c0c0; border: 1px solid #2a2a32;
    selection-background-color: #2a2a32; font-size: 12px;
}
"""
LINE_STYLE = """
QLineEdit {
    background: #1a1a20; color: #c0c0c0; border: 1px solid #2a2a32;
    padding: 4px 8px; font-size: 12px;
}
"""
MODE_BTN_STYLE = """
QPushButton {
    background: transparent; color: #606068; border: none;
    padding: 4px 10px; font-size: 10px; letter-spacing: 1px;
}
QPushButton:checked {
    color: #c0c0c0; border-bottom: 1px solid #606068;
}
QPushButton:hover {
    color: #909090;
}
"""
ICON_BTN_STYLE = """
QPushButton {
    background: transparent; color: #808088; border: 1px solid #222228;
    padding: 4px 8px; font-size: 12px;
}
QPushButton:hover {
    background: #222228; color: #c0c0c0; border-color: #303038;
}
"""


class VpnPollWorker(QThread):
    result = pyqtSignal(str, str)

    def __init__(self, server_name, parent=None):
        super().__init__(parent)
        self._server = server_name

    def run(self):
        status = get_connection_status(self._server)
        ip = ""
        if status == "Connected":
            ip = get_vpn_ip(self._server)
        self.result.emit(status, ip)


class VpnServerWorker(QThread):
    finished = pyqtSignal(bool, object)

    def __init__(self, action, psk="", parent=None):
        super().__init__(parent)
        self._action = action
        self._psk = psk

    def run(self):
        if self._action == "start":
            ok, result = start_vpn_server(self._psk)
            self.finished.emit(ok, result)
        elif self._action == "stop":
            ok, msg = stop_vpn_server()
            self.finished.emit(ok, msg)


class VpnClientWorker(QThread):
    finished = pyqtSignal(bool, str, str)

    def __init__(self, action, name="", psk="", parent=None):
        super().__init__(parent)
        self._action = action
        self._name = name
        self._psk = psk

    def run(self):
        if self._action == "connect":
            if not vpn_exists(self._name):
                ok, msg = create_client_vpn(self._name, self._name, self._psk)
                if not ok:
                    self.finished.emit(False, msg, "")
                    return
            ok, msg = connect_vpn(self._name, "openmclvpn", self._psk)
            self.finished.emit(ok, msg, "")
        elif self._action == "disconnect":
            ok, msg = disconnect_vpn(self._name)
            self.finished.emit(ok, msg, "")


class McServerProcess(QProcess):
    server_output = pyqtSignal(str)
    server_stopped = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProcessChannelMode(QProcess.MergedChannels)
        self.readyReadStandardOutput.connect(self._on_output)

    def _on_output(self):
        data = self.readAllStandardOutput().data().decode("utf-8", errors="replace")
        for line in data.splitlines():
            if line.strip():
                self.server_output.emit(line.strip())


class VpnSidebarWidget(QFrame):
    status_changed = pyqtSignal(bool, str)

    def __init__(self, parent=None, initial_mode="p2p",
                 show_mode_buttons=False, show_vpn=True, show_mc=True):
        super().__init__(parent)
        self.setObjectName("vpnPanel")
        self.setStyleSheet("QFrame#vpnPanel { background: transparent; border: none; }")
        self._expanded = True
        self._mode = "p2p"
        self._connected = False
        self._server_running = False
        self._mc_running = False
        self._current_ip = ""
        self._psk = ""
        self._servers = load_servers()
        self._mc_process = None
        self._initial_mode = initial_mode
        self._show_mode_buttons = show_mode_buttons
        self._show_vpn = show_vpn
        self._show_mc = show_mc
        self._setup_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_status)
        self._timer.start(5000)
        if not show_mode_buttons:
            self.set_mode(initial_mode)
        else:
            self._restore_server_state()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        header_row = QHBoxLayout()
        header_row.setSpacing(5)

        self._toggle_btn = QPushButton("\u25bc")
        self._toggle_btn.setFixedSize(18, 18)
        self._toggle_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #505058; border: none; font-size: 10px; }"
            "QPushButton:hover { color: #808088; }")
        self._toggle_btn.setCursor(Qt.PointingHandCursor)
        self._toggle_btn.clicked.connect(self._toggle_expand)
        header_row.addWidget(self._toggle_btn)

        title = QLabel("MultiplayerHelper")
        title.setStyleSheet(TITLE_STYLE)
        header_row.addWidget(title)
        header_row.addStretch()
        self._title_label = title

        self._status_dot = QLabel("\u25cf")
        self._status_dot.setStyleSheet("color: #404048; font-size: 10px;")
        header_row.addWidget(self._status_dot)

        self._status_label = QLabel("Offline")
        self._status_label.setStyleSheet("color: #505058; font-size: 11px;")
        header_row.addWidget(self._status_label)

        self._status_dot.setVisible(self._show_vpn)
        self._status_label.setVisible(self._show_vpn)

        layout.addLayout(header_row)

        self._content = QWidget()
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 6, 0, 0)
        content_layout.setSpacing(6)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(2)
        mode_row.addStretch()

        self._svr_btn = QPushButton("Server")
        self._svr_btn.setCheckable(True)
        self._svr_btn.setStyleSheet(MODE_BTN_STYLE)
        self._svr_btn.setCursor(Qt.PointingHandCursor)
        self._svr_btn.clicked.connect(lambda: self._set_mode("server"))
        self._svr_btn.setVisible(False)  # 旧 L2TP 模式已由 P2P 取代
        mode_row.addWidget(self._svr_btn)

        self._cli_btn = QPushButton("Client")
        self._cli_btn.setCheckable(True)
        self._cli_btn.setChecked(True)
        self._cli_btn.setStyleSheet(MODE_BTN_STYLE)
        self._cli_btn.setCursor(Qt.PointingHandCursor)
        self._cli_btn.clicked.connect(lambda: self._set_mode("client"))
        self._cli_btn.setVisible(False)  # 旧 L2TP 模式已由 P2P 取代
        mode_row.addWidget(self._cli_btn)

        self._p2p_btn = QPushButton("P2P")
        self._p2p_btn.setCheckable(True)
        self._p2p_btn.setStyleSheet(MODE_BTN_STYLE)
        self._p2p_btn.setCursor(Qt.PointingHandCursor)
        self._p2p_btn.clicked.connect(lambda: self._set_mode("p2p"))
        if not self._show_mode_buttons:
            self._p2p_btn.setVisible(False)
        mode_row.addWidget(self._p2p_btn)

        mode_row.addStretch()
        content_layout.addSpacing(0)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: transparent;")

        self._server_page = self._build_server_page()
        self._client_page = self._build_client_page()
        self._p2p_page = P2PWidget()

        self._stack.addWidget(self._server_page)
        self._stack.addWidget(self._client_page)
        self._stack.addWidget(self._p2p_page)
        self._stack.setCurrentIndex(1)

        # ---- VPN region (server/client connection) ----
        self._vpn_block = QWidget()
        vb = QVBoxLayout(self._vpn_block)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(6)
        if self._show_mode_buttons:
            vb.addLayout(mode_row)
        vb.addWidget(self._stack)
        content_layout.addWidget(self._vpn_block)

        # ---- MC server region ----
        self._mc_block = QWidget()
        ml = QVBoxLayout(self._mc_block)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(4)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("border: none; background: #202028; max-height: 1px;")
        ml.addWidget(sep)

        self._build_mc_server_section(ml)

        content_layout.addWidget(self._mc_block)

        self._vpn_block.setVisible(self._show_vpn)
        self._mc_block.setVisible(self._show_mc)

        layout.addWidget(self._content)

    def _build_server_page(self):
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        l = QVBoxLayout(page)
        l.setContentsMargins(0, 4, 0, 0)
        l.setSpacing(6)

        psk_lbl = QLabel("Pre-Shared Key")
        psk_lbl.setStyleSheet(ROW_STYLE)
        l.addWidget(psk_lbl)

        self._svr_psk = QLineEdit()
        self._svr_psk.setStyleSheet(LINE_STYLE)
        self._svr_psk.setReadOnly(True)
        self._svr_psk.setEchoMode(QLineEdit.Password)
        self._svr_psk.setPlaceholderText("Click Generate")
        l.addWidget(self._svr_psk)

        psk_btns = QHBoxLayout()
        psk_btns.setSpacing(6)
        gen_btn = QPushButton("Generate Key")
        gen_btn.setStyleSheet(ICON_BTN_STYLE)
        gen_btn.setCursor(Qt.PointingHandCursor)
        gen_btn.clicked.connect(self._gen_server_psk)
        psk_btns.addWidget(gen_btn)
        copy_btn = QPushButton("Copy")
        copy_btn.setStyleSheet(ICON_BTN_STYLE)
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.clicked.connect(self._copy_server_psk)
        psk_btns.addWidget(copy_btn)
        psk_btns.addStretch()
        l.addLayout(psk_btns)

        l.addSpacing(4)

        self._svr_local = QLabel("")
        self._svr_local.setStyleSheet(VAL_STYLE)
        self._svr_local.setWordWrap(True)
        l.addWidget(self._svr_local)

        self._svr_public = QLabel("")
        self._svr_public.setStyleSheet(VAL_BLUE)
        self._svr_public.setWordWrap(True)
        l.addWidget(self._svr_public)

        self._svr_info = QLabel("")
        self._svr_info.setStyleSheet(VAL_DIM)
        self._svr_info.setWordWrap(True)
        l.addWidget(self._svr_info)

        l.addSpacing(2)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._start_btn = QPushButton("Start Server")
        self._start_btn.setStyleSheet(ACCENT_STYLE)
        self._start_btn.setCursor(Qt.PointingHandCursor)
        self._start_btn.clicked.connect(self._on_start_server)
        btn_row.addWidget(self._start_btn)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setStyleSheet(STOP_STYLE)
        self._stop_btn.setCursor(Qt.PointingHandCursor)
        self._stop_btn.clicked.connect(self._on_stop_server)
        self._stop_btn.setVisible(False)
        btn_row.addWidget(self._stop_btn)
        btn_row.addStretch()
        l.addLayout(btn_row)

        l.addStretch()
        return page

    def _build_client_page(self):
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        l = QVBoxLayout(page)
        l.setContentsMargins(0, 4, 0, 0)
        l.setSpacing(6)

        srv_lbl = QLabel("Server Address")
        srv_lbl.setStyleSheet(ROW_STYLE)
        l.addWidget(srv_lbl)

        srv_row = QHBoxLayout()
        srv_row.setSpacing(4)
        self._cli_server = QComboBox()
        self._cli_server.setStyleSheet(COMBO_STYLE)
        self._cli_server.setEditable(True)
        self._cli_server.setCursor(Qt.PointingHandCursor)
        self._refresh_combo()
        self._cli_server.currentTextChanged.connect(self._on_client_server_changed)
        srv_row.addWidget(self._cli_server, 1)
        add_btn = QPushButton("+")
        add_btn.setFixedSize(26, 26)
        add_btn.setStyleSheet(ICON_BTN_STYLE)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._add_server)
        srv_row.addWidget(add_btn)
        del_btn = QPushButton("-")
        del_btn.setFixedSize(26, 26)
        del_btn.setStyleSheet(ICON_BTN_STYLE)
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.clicked.connect(self._del_server)
        srv_row.addWidget(del_btn)
        l.addLayout(srv_row)

        psk_lbl = QLabel("Pre-Shared Key")
        psk_lbl.setStyleSheet(ROW_STYLE)
        l.addWidget(psk_lbl)

        self._cli_psk = QLineEdit()
        self._cli_psk.setStyleSheet(LINE_STYLE)
        self._cli_psk.setEchoMode(QLineEdit.Password)
        self._cli_psk.setPlaceholderText("Paste server key here")
        l.addWidget(self._cli_psk)

        psk_btns = QHBoxLayout()
        psk_btns.setSpacing(6)
        paste_btn = QPushButton("Paste")
        paste_btn.setStyleSheet(ICON_BTN_STYLE)
        paste_btn.setCursor(Qt.PointingHandCursor)
        paste_btn.clicked.connect(self._paste_client_psk)
        psk_btns.addWidget(paste_btn)
        psk_btns.addStretch()
        l.addLayout(psk_btns)

        self._cli_ip = QLabel("")
        self._cli_ip.setStyleSheet(VAL_GREEN)
        self._cli_ip.setWordWrap(True)
        l.addWidget(self._cli_ip)

        l.addSpacing(2)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._conn_btn = QPushButton("Connect")
        self._conn_btn.setStyleSheet(ACCENT_STYLE)
        self._conn_btn.setCursor(Qt.PointingHandCursor)
        self._conn_btn.clicked.connect(self._on_client_connect)
        btn_row.addWidget(self._conn_btn)
        self._disc_btn = QPushButton("Disconnect")
        self._disc_btn.setStyleSheet(STOP_STYLE)
        self._disc_btn.setCursor(Qt.PointingHandCursor)
        self._disc_btn.clicked.connect(self._on_client_disconnect)
        self._disc_btn.setVisible(False)
        btn_row.addWidget(self._disc_btn)
        btn_row.addStretch()
        l.addLayout(btn_row)

        l.addStretch()
        return page

    def _build_mc_server_section(self, layout):
        title_lbl = QLabel("MC Server")
        title_lbl.setStyleSheet(TITLE_STYLE)
        layout.addWidget(title_lbl)
        layout.addSpacing(2)

        jar_row = QHBoxLayout()
        jar_row.setSpacing(4)
        self._mc_jar = QLineEdit()
        self._mc_jar.setStyleSheet(LINE_STYLE)
        self._mc_jar.setPlaceholderText("server.jar path")
        jar_row.addWidget(self._mc_jar, 1)
        browse_btn = QPushButton("...")
        browse_btn.setFixedSize(28, 28)
        browse_btn.setStyleSheet(ICON_BTN_STYLE)
        browse_btn.setCursor(Qt.PointingHandCursor)
        browse_btn.clicked.connect(self._browse_mc_jar)
        jar_row.addWidget(browse_btn)
        layout.addLayout(jar_row)

        mem_row = QHBoxLayout()
        mem_row.setSpacing(4)
        mem_lbl = QLabel("RAM (GB)")
        mem_lbl.setStyleSheet(ROW_STYLE)
        mem_row.addWidget(mem_lbl)
        self._mc_mem = QLineEdit("2")
        self._mc_mem.setStyleSheet(LINE_STYLE)
        self._mc_mem.setFixedWidth(40)
        self._mc_mem.setAlignment(Qt.AlignCenter)
        mem_row.addWidget(self._mc_mem)
        mem_row.addStretch()
        layout.addLayout(mem_row)

        layout.addSpacing(2)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._mc_start_btn = QPushButton("Start MC Server")
        self._mc_start_btn.setStyleSheet(ACCENT_STYLE)
        self._mc_start_btn.setCursor(Qt.PointingHandCursor)
        self._mc_start_btn.clicked.connect(self._on_mc_start)
        btn_row.addWidget(self._mc_start_btn)
        self._mc_stop_btn = QPushButton("Stop")
        self._mc_stop_btn.setStyleSheet(STOP_STYLE)
        self._mc_stop_btn.setCursor(Qt.PointingHandCursor)
        self._mc_stop_btn.clicked.connect(self._on_mc_stop)
        self._mc_stop_btn.setVisible(False)
        btn_row.addWidget(self._mc_stop_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._mc_status = QLabel("")
        self._mc_status.setStyleSheet(VAL_DIM)
        self._mc_status.setWordWrap(True)
        layout.addWidget(self._mc_status)

        layout.addSpacing(2)

        self._mc_console_header = QPushButton("Console \u25b8")
        self._mc_console_header.setStyleSheet(ROW_STYLE)
        self._mc_console_header.setCursor(Qt.PointingHandCursor)
        self._mc_console_header.setCheckable(True)
        layout.addWidget(self._mc_console_header)

        self._mc_console_body = QWidget()
        self._mc_console_body.setVisible(False)
        self._mc_console_header.toggled.connect(self._mc_console_body.setVisible)
        cl = QVBoxLayout(self._mc_console_body)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(4)

        self._mc_log = QPlainTextEdit()
        self._mc_log.setReadOnly(True)
        self._mc_log.setMaximumBlockCount(2000)
        self._mc_log.setStyleSheet(
            "QPlainTextEdit { background: #1f1f24; color: #c8c8d0; border: 1px solid #3a3a40;"
            " border-radius: 4px; font-family: Consolas, monospace; font-size: 11px; }")
        cl.addWidget(self._mc_log)

        cmd_row = QHBoxLayout()
        cmd_row.setSpacing(4)
        self._mc_cmd = QLineEdit()
        self._mc_cmd.setStyleSheet(LINE_STYLE)
        self._mc_cmd.setPlaceholderText("Type a server command (e.g. stop, say hi, list)...")
        self._mc_cmd.returnPressed.connect(self._mc_send_command)
        cmd_row.addWidget(self._mc_cmd, 1)
        self._mc_send_btn = QPushButton("Send")
        self._mc_send_btn.setStyleSheet(ACCENT_STYLE)
        self._mc_send_btn.setCursor(Qt.PointingHandCursor)
        self._mc_send_btn.clicked.connect(self._mc_send_command)
        cmd_row.addWidget(self._mc_send_btn)
        cl.addLayout(cmd_row)

        layout.addWidget(self._mc_console_body)

    def _browse_mc_jar(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select server.jar", "", "JAR files (*.jar)")
        if path:
            self._mc_jar.setText(path)

    def _agree_eula(self, dir_path):
        """自动同意 Minecraft EULA：确保 eula.txt 中 eula=true，
        否则服务端会提示 "You need to agree to the EULA" 并退出。"""
        try:
            eula_path = os.path.join(dir_path, "eula.txt")
            eula = ""
            if os.path.isfile(eula_path):
                with open(eula_path, "r", encoding="utf-8") as f:
                    eula = f.read()
            # 已同意则无需改动
            if "eula=true" in eula:
                return
            if not eula.strip():
                content = (
                    "#By changing the setting below to TRUE you are indicating your agreement to our EULA"
                    " (https://aka.ms/MinecraftEULA).\n"
                    "#generated-by-openmcl\n"
                    "eula=true\n"
                )
                with open(eula_path, "w", encoding="utf-8") as f:
                    f.write(content)
            else:
                # 已有文件但未同意：仅改写 eula 字段为 true，其余注释保留
                import re
                if re.search(r"(?m)^\s*eula\s*=", eula):
                    content = re.sub(r"(?m)^\s*eula\s*=.*$", "eula=true", eula)
                else:
                    content = eula.rstrip("\n") + "\neula=true\n"
                with open(eula_path, "w", encoding="utf-8") as f:
                    f.write(content)
        except Exception as e:
            print("Failed to write eula.txt:", e)

    def _on_mc_start(self):
        jar = self._mc_jar.text().strip()
        if not jar or not os.path.isfile(jar):
            QMessageBox.warning(self, "MC Server", "Please select a valid server.jar file.")
            return
        try:
            mem_gb = int(self._mc_mem.text().strip())
        except ValueError:
            mem_gb = 2
        mem_mb = max(512, mem_gb * 1024)

        jar_dir = os.path.dirname(os.path.abspath(jar))

        # 自动同意 EULA，避免服务端启动即退出
        self._agree_eula(jar_dir)

        self._mc_start_btn.setEnabled(False)
        self._mc_start_btn.setVisible(False)
        self._mc_stop_btn.setVisible(True)
        self._mc_running = True
        self._mc_status.setText("Starting server...")

        self._mc_process = McServerProcess(self)
        self._mc_process.server_output.connect(self._on_mc_output)
        self._mc_process.finished.connect(self._on_mc_finished)
        self._mc_process.setWorkingDirectory(jar_dir)

        java_exe = "java"
        try:
            from core.launcher import find_java
            found = find_java()
            if found:
                first = found[0]
                if isinstance(first, dict):
                    java_exe = first.get("path") or "java"
                elif isinstance(first, (list, tuple)):
                    java_exe = first[0]
                else:
                    java_exe = first
        except Exception:
            pass

        args = [f"-Xmx{mem_mb}M", f"-Xms{min(512, mem_mb)}M", "-jar", jar, "--nogui"]
        self._mc_process.start(java_exe, args)

    def _on_mc_output(self, line):
        self._mc_status.setText(f"Running: {line}")
        if hasattr(self, "_mc_log"):
            self._mc_log.appendPlainText(line)
            sb = self._mc_log.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _mc_send_command(self, cmd=None):
        text = self._mc_cmd.text().strip()
        if not text:
            return
        if hasattr(self, "_mc_log"):
            self._mc_log.appendPlainText(f"> {text}")
        if self._mc_process and self._mc_process.state() != QProcess.NotRunning:
            self._mc_process.write((text + "\n").encode("utf-8"))
        self._mc_cmd.clear()

    def _on_mc_finished(self, exit_code, exit_status):
        self._mc_running = False
        self._mc_start_btn.setEnabled(True)
        self._mc_start_btn.setVisible(True)
        self._mc_stop_btn.setVisible(False)
        self._mc_status.setText(f"Server stopped (exit {exit_code})")
        self._mc_process = None

    def _on_mc_stop(self):
        if self._mc_process and self._mc_process.state() != QProcess.NotRunning:
            self._mc_process.write(b"stop\n")
            self._mc_status.setText("Stopping server...")
            self._mc_stop_btn.setEnabled(False)

    def set_title_text(self, text):
        if hasattr(self, "_title_label"):
            self._title_label.setText(text)

    def set_mode(self, mode):
        self._set_mode(mode)

    def _set_mode(self, mode):
        self._mode = mode
        if mode == "server":
            self.set_title_text("Server")
            self._svr_btn.setChecked(True)
            self._cli_btn.setChecked(False)
            self._p2p_btn.setChecked(False)
            self._stack.setCurrentIndex(0)
            self._refresh_server_status()
        elif mode == "p2p":
            self.set_title_text("P2P")
            self._svr_btn.setChecked(False)
            self._cli_btn.setChecked(False)
            self._p2p_btn.setChecked(True)
            self._stack.setCurrentIndex(2)
        else:
            self.set_title_text("MultiplayerHelper")
            self._svr_btn.setChecked(False)
            self._cli_btn.setChecked(True)
            self._p2p_btn.setChecked(False)
            self._stack.setCurrentIndex(1)
            self._on_client_server_changed(self._cli_server.currentText())

    def _restore_server_state(self):
        # 旧的 L2TP 模式已由 P2P 取代，统一恢复到 P2P 页
        self._set_mode("p2p")

    def _refresh_server_status(self):
        status = get_server_status()
        if status.get("running"):
            self._server_running = True
            self._status_dot.setStyleSheet("color: #50c878; font-size: 10px;")
            self._status_label.setText("Running")
            self._status_label.setStyleSheet("color: #50c878; font-size: 11px;")
            self._start_btn.setVisible(False)
            self._stop_btn.setVisible(True)
            local = status.get("local_ip", "")
            public = status.get("public_ip", "")
            if local:
                self._svr_local.setText(f"Local IP: {local}")
            if public:
                self._svr_public.setText(f"Public IP: {public}")
                self._svr_info.setText("Ports: UDP 500, 4500")
            else:
                self._svr_public.setText("")
                self._svr_info.setText("Cannot get public IP. Check network.")
        else:
            self._server_running = False
            self._status_dot.setStyleSheet("color: #404048; font-size: 10px;")
            self._status_label.setText("Stopped")
            self._status_label.setStyleSheet("color: #505058; font-size: 11px;")
            self._start_btn.setVisible(True)
            self._stop_btn.setVisible(False)
            if not self._svr_local.text():
                self._svr_local.setText(f"Local IP: {get_local_ip()}")

    def _gen_server_psk(self):
        self._psk = generate_psk()
        self._svr_psk.setText(self._psk)

    def _copy_server_psk(self):
        if not self._svr_psk.text():
            self._gen_server_psk()
        QApplication.clipboard().setText(self._svr_psk.text())

    def _on_start_server(self):
        if not check_platform():
            QMessageBox.warning(self, "Warning", "Windows only.")
            return
        psk = self._svr_psk.text()
        if not psk:
            psk = generate_psk()
            self._svr_psk.setText(psk)
        self._psk = psk
        self._start_btn.setEnabled(False)
        self._start_btn.setText("Starting...")
        self._svr_worker = VpnServerWorker("start", psk)
        self._svr_worker.finished.connect(self._on_server_started)
        self._svr_worker.start()

    def _on_server_started(self, ok, result):
        self._start_btn.setEnabled(True)
        self._start_btn.setText("Start Server")
        if ok:
            local = result.get("local_ip", "")
            public = result.get("public_ip", "")
            self._svr_local.setText(f"Local IP: {local}")
            if public:
                self._svr_public.setText(f"Public IP: {public}")
                self._svr_info.setText("Ports: UDP 500, 4500, 1701")
            self._server_running = True
            self._status_dot.setStyleSheet("color: #50c878; font-size: 10px;")
            self._status_label.setText("Running")
            self._status_label.setStyleSheet("color: #50c878; font-size: 11px;")
            self._start_btn.setVisible(False)
            self._stop_btn.setVisible(True)
            self.status_changed.emit(True, "server")
            info = f"Share this with your friends:\n\nKey: {self._psk}\nLocal IP: {local}"
            if public:
                info += f"\nPublic IP: {public}"
            info += "\nPorts: UDP 500, 4500, 1701"
            QMessageBox.information(self, "Server Started", info)
        else:
            QMessageBox.warning(self, "Failed", str(result))

    def _on_stop_server(self):
        self._stop_btn.setEnabled(False)
        self._stop_btn.setText("Stopping...")
        self._svr_worker = VpnServerWorker("stop")
        self._svr_worker.finished.connect(self._on_server_stopped)
        self._svr_worker.start()

    def _on_server_stopped(self, ok, msg):
        self._stop_btn.setEnabled(True)
        self._stop_btn.setText("Stop")
        self._server_running = False
        self._status_dot.setStyleSheet("color: #404048; font-size: 10px;")
        self._status_label.setText("Stopped")
        self._status_label.setStyleSheet("color: #505058; font-size: 11px;")
        self._start_btn.setVisible(True)
        self._stop_btn.setVisible(False)
        self._svr_public.setText("")
        self._svr_info.setText("")
        self.status_changed.emit(False, "server")

    def _poll_status(self):
        if not self._show_vpn:
            return
        if self._mode == "server":
            self._refresh_server_status()
        else:
            name = self._cli_server.currentText()
            if name:
                self._worker = VpnPollWorker(name)
                self._worker.result.connect(self._apply_client_status)
                self._worker.start()

    def _apply_client_status(self, status, ip):
        if status == "Connected":
            self._connected = True
            self._status_dot.setStyleSheet("color: #50c878; font-size: 10px;")
            self._status_label.setText("Connected")
            self._status_label.setStyleSheet("color: #50c878; font-size: 11px;")
            self._conn_btn.setVisible(False)
            self._disc_btn.setVisible(True)
            if ip:
                self._current_ip = ip
                self._cli_ip.setText(f"VPN IP: {ip}")
        elif status == "NotFound":
            self._connected = False
            self._status_dot.setStyleSheet("color: #404048; font-size: 10px;")
            self._status_label.setText("Offline")
            self._status_label.setStyleSheet("color: #505058; font-size: 11px;")
            self._conn_btn.setVisible(True)
            self._disc_btn.setVisible(False)
            self._cli_ip.setText("")
        else:
            self._connected = False
            self._status_dot.setStyleSheet("color: #d0a040; font-size: 10px;")
            self._status_label.setText("Disconnected")
            self._status_label.setStyleSheet("color: #808060; font-size: 11px;")
            self._conn_btn.setVisible(True)
            self._disc_btn.setVisible(False)
            self._cli_ip.setText("")

    def _on_client_connect(self):
        name = self._cli_server.currentText().strip()
        if not name:
            return
        psk = self._cli_psk.text()
        if not psk:
            QMessageBox.warning(self, "Warning", "Please enter the Pre-Shared Key.")
            return
        if not check_platform():
            QMessageBox.warning(self, "Warning", "Windows only.")
            return
        self._conn_btn.setEnabled(False)
        self._conn_btn.setText("Connecting...")
        self._cli_worker = VpnClientWorker("connect", name, psk)
        self._cli_worker.finished.connect(self._on_client_connected)
        self._cli_worker.start()

    def _on_client_connected(self, ok, msg, ip):
        self._conn_btn.setEnabled(True)
        self._conn_btn.setText("Connect")
        if ok:
            self._apply_client_status("Connected", "")
        else:
            QMessageBox.warning(self, "Connection Failed", msg)

    def _on_client_disconnect(self):
        name = self._cli_server.currentText().strip()
        if not name:
            return
        self._disc_btn.setEnabled(False)
        self._disc_btn.setText("Disconnecting...")
        self._cli_worker = VpnClientWorker("disconnect", name)
        self._cli_worker.finished.connect(self._on_client_disconnected)
        self._cli_worker.start()

    def _on_client_disconnected(self, ok, msg, ip):
        self._disc_btn.setEnabled(True)
        self._disc_btn.setText("Disconnect")
        self._apply_client_status("Disconnected", "")

    def _on_client_server_changed(self, text):
        if not text:
            return
        for s in self._servers:
            if s["name"] == text:
                self._cli_psk.setText(s.get("psk", ""))
                return
        self._cli_psk.setText("")

    def _refresh_combo(self):
        self._cli_server.blockSignals(True)
        self._cli_server.clear()
        for s in self._servers:
            self._cli_server.addItem(s["name"])
        self._cli_server.blockSignals(False)

    def _add_server(self):
        addr, ok = QInputDialog.getText(self, "Add Server", "Server address (IP / domain):")
        if not ok or not addr.strip():
            return
        for s in self._servers:
            if s["name"] == addr.strip():
                QMessageBox.warning(self, "Duplicate", "Server already exists.")
                return
        self._servers.append({"name": addr.strip(), "psk": ""})
        save_servers(self._servers)
        self._refresh_combo()
        self._cli_server.setCurrentText(addr.strip())

    def _del_server(self):
        name = self._cli_server.currentText()
        if not name:
            return
        r = QMessageBox.question(self, "Remove", f"Remove {name}?",
                                 QMessageBox.Yes | QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        disconnect_vpn(name)
        remove_client_vpn(name)
        self._servers = [s for s in self._servers if s["name"] != name]
        save_servers(self._servers)
        self._refresh_combo()
        self._apply_client_status("NotFound", "")

    def _paste_client_psk(self):
        clipboard = QApplication.clipboard().text()
        if clipboard:
            self._cli_psk.setText(clipboard)
            name = self._cli_server.currentText().strip()
            if name:
                found = False
                for s in self._servers:
                    if s["name"] == name:
                        s["psk"] = clipboard
                        found = True
                        break
                if not found:
                    self._servers.append({"name": name, "psk": clipboard})
                save_servers(self._servers)

    def _toggle_expand(self):
        self._expanded = not self._expanded
        self._content.setVisible(self._expanded)
        self._toggle_btn.setText("\u25bc" if self._expanded else "\u25b6")

    def _unload(self):
        self._timer.stop()
        if hasattr(self, "_p2p_page"):
            self._p2p_page._unload()
        if self._mc_process and self._mc_process.state() != QProcess.NotRunning:
            self._mc_process.write(b"stop\n")
            self._mc_process.waitForFinished(3000)
            self._mc_process.terminate()
        for attr in ('_worker', '_svr_worker', '_cli_worker'):
            w = getattr(self, attr, None)
            if w is not None and w.isRunning():
                w.quit()
                w.wait(500)