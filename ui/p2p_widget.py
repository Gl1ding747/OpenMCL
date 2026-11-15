"""
P2P mesh panel (Client-Server model), built on P2PServer / P2PClient.

  Client: only a code box + Connect / Disconnect. Enter the peer's code and go.
  Server: only Start/Stop + Generate Code + connection management (auto IP detect).

Rectangular dark theme, English UI, matching the rest of the app.
"""

from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QLabel, QLineEdit, QTextEdit, QListWidget,
                             QListWidgetItem, QApplication, QStackedWidget)
from PyQt5.QtCore import Qt, pyqtSignal

from core.p2p_link import P2PServer, P2PClient, local_ip

_MODE = """
QPushButton {
    background: transparent; color: #606068; border: none;
    padding: 4px 10px; font-size: 10px; letter-spacing: 1px;
}
QPushButton:checked { color: #c0c0c0; border-bottom: 1px solid #606068; }
QPushButton:hover { color: #909090; }
"""
_LBL = "color: #808088; font-size: 12px;"
_SEC = "color: #808088; font-size: 11px; letter-spacing: 1px;"
_INPUT = """
QLineEdit, QTextEdit {
    background: #1a1a20; color: #c0c0c0; border: 1px solid #2a2a32;
    padding: 5px 8px; font-size: 11px;
}
QTextEdit { font-family: Consolas, monospace; }
"""
_LIST = """
QListWidget {
    background: #1a1a20; border: 1px solid #2a2a32;
    color: #c0c0c0; font-size: 11px; padding: 4px;
}
QListWidget::item { padding: 4px; }
QListWidget::item:selected { background: #282830; color: #e8e8e8; }
"""
_ACCENT = """
QPushButton {
    background: #282830; color: #e8e8e8; border: 1px solid #383840;
    padding: 9px 12px; font-size: 12px; font-weight: bold;
}
QPushButton:hover { background: #32323c; border-color: #4a4a54; }
QPushButton:disabled { color: #606068; background: #181820; border-color: #222228; }
"""
_GRAY = """
QPushButton {
    background: transparent; color: #808088; border: 1px solid #222228;
    padding: 5px 10px; font-size: 11px;
}
QPushButton:hover { background: #222228; color: #c0c0c0; border-color: #303038; }
"""
_RED = """
QPushButton {
    background: transparent; color: #e07878; border: 1px solid #3a2222;
    padding: 5px 10px; font-size: 11px;
}
QPushButton:hover { background: #2a1a1a; border-color: #5a3030; }
"""
_GREEN = "color: #50c878; font-size: 12px;"
_DIM = "color: #505058; font-size: 11px;"
_ERR = "color: #e06060; font-size: 12px;"


class P2PWidget(QWidget):
    _cli_state = pyqtSignal(str)
    _srv_state = pyqtSignal(str)
    _srv_clients = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = "client"
        self._server = None
        self._client = None

        self._cli_state.connect(self._apply_cli, Qt.QueuedConnection)
        self._srv_state.connect(self._apply_srv_state, Qt.QueuedConnection)
        self._srv_clients.connect(self._apply_clients, Qt.QueuedConnection)
        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(6)

        sw = QHBoxLayout()
        sw.setSpacing(2)
        sw.addStretch()
        self._cli_btn = QPushButton("Client")
        self._cli_btn.setCheckable(True)
        self._cli_btn.setChecked(True)
        self._cli_btn.setStyleSheet(_MODE)
        self._cli_btn.clicked.connect(lambda: self._set_mode("client"))
        sw.addWidget(self._cli_btn)
        self._srv_btn = QPushButton("Server")
        self._srv_btn.setCheckable(True)
        self._srv_btn.setStyleSheet(_MODE)
        self._srv_btn.clicked.connect(lambda: self._set_mode("server"))
        sw.addWidget(self._srv_btn)
        sw.addStretch()
        l.addLayout(sw)

        self._stack = QStackedWidget()
        self._build_client(self._stack)
        self._build_server(self._stack)
        l.addWidget(self._stack, 1)

        self._status = QLabel("Ready")
        self._status.setStyleSheet(_DIM)
        self._status.setWordWrap(True)
        l.addWidget(self._status)

    def _build_client(self, stack):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        t = QLabel("Enter the server's code, then Connect. Everything else "
                   "is filled automatically.")
        t.setStyleSheet(_DIM)
        t.setWordWrap(True)
        v.addWidget(t)

        row = QHBoxLayout()
        row.setSpacing(4)
        pl = QLabel("Local listen port")
        pl.setStyleSheet(_LBL)
        row.addWidget(pl)
        self._cli_port = QLineEdit("25566")
        self._cli_port.setStyleSheet(_INPUT)
        self._cli_port.setFixedWidth(64)
        row.addStretch()
        v.addLayout(row)

        self._code_text = QTextEdit()
        self._code_text.setPlaceholderText("Paste the connection code here")
        self._code_text.setStyleSheet(_INPUT)
        self._code_text.setFixedHeight(76)
        v.addWidget(self._code_text)

        b = QHBoxLayout()
        b.setSpacing(6)
        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setStyleSheet(_ACCENT)
        self._connect_btn.clicked.connect(self._on_connect)
        self._connect_btn.setMinimumHeight(38)
        b.addWidget(self._connect_btn, 3)
        self._disc_btn = QPushButton("Disconnect")
        self._disc_btn.setStyleSheet(_RED)
        self._disc_btn.clicked.connect(self._on_disconnect)
        self._disc_btn.setEnabled(False)
        b.addWidget(self._disc_btn, 1)
        v.addLayout(b)

        stack.addWidget(page)

    def _build_server(self, stack):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        t = QLabel("Your detected IP: %s" % local_ip())
        t.setStyleSheet(_GREEN)
        v.addWidget(t)

        row = QHBoxLayout()
        row.setSpacing(4)
        hl = QLabel("Service to expose (forward to)")
        hl.setStyleSheet(_LBL)
        hl.setWordWrap(True)
        row.addWidget(hl, 1)
        self._srv_host = QLineEdit("127.0.0.1")
        self._srv_host.setStyleSheet(_INPUT)
        self._srv_host.setFixedWidth(130)
        row.addWidget(self._srv_host)
        self._srv_port = QLineEdit("25565")
        self._srv_port.setStyleSheet(_INPUT)
        self._srv_port.setFixedWidth(64)
        row.addWidget(self._srv_port)
        v.addLayout(row)

        b = QHBoxLayout()
        b.setSpacing(6)
        self._start_btn = QPushButton("Start Service")
        self._start_btn.setStyleSheet(_ACCENT)
        self._start_btn.clicked.connect(self._on_start)
        b.addWidget(self._start_btn, 1)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setStyleSheet(_RED)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop_server)
        b.addWidget(self._stop_btn, 1)
        v.addLayout(b)

        sc = QLabel("CONNECTION CODE")
        sc.setStyleSheet(_SEC)
        v.addWidget(sc)
        self._code_out = QTextEdit()
        self._code_out.setReadOnly(True)
        self._code_out.setPlaceholderText("Start, then Generate Code")
        self._code_out.setStyleSheet(_INPUT)
        self._code_out.setFixedHeight(44)
        v.addWidget(self._code_out)

        cb = QHBoxLayout()
        cb.setSpacing(6)
        self._gen_btn = QPushButton("Generate Code")
        self._gen_btn.setStyleSheet(_GRAY)
        self._gen_btn.setEnabled(False)
        self._gen_btn.clicked.connect(self._on_gen_code)
        cb.addWidget(self._gen_btn)
        copy_btn = QPushButton("Copy")
        copy_btn.setStyleSheet(_GRAY)
        copy_btn.clicked.connect(self._on_copy_code)
        cb.addWidget(copy_btn)
        cb.addStretch()
        v.addLayout(cb)

        cl = QLabel("CONNECTED CLIENTS (0)")
        cl.setStyleSheet(_SEC)
        v.addWidget(cl)
        self._clients_lbl = cl
        self._clients = QListWidget()
        self._clients.setStyleSheet(_LIST)
        self._clients.setFixedHeight(96)
        v.addWidget(self._clients)

        mb = QHBoxLayout()
        mb.setSpacing(6)
        dc_btn = QPushButton("Disconnect Selected")
        dc_btn.setStyleSheet(_RED)
        dc_btn.clicked.connect(self._on_disc_client)
        mb.addWidget(dc_btn)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setStyleSheet(_GRAY)
        refresh_btn.clicked.connect(self._refresh_clients)
        mb.addWidget(refresh_btn)
        mb.addStretch()
        v.addLayout(mb)

        stack.addWidget(page)

    # ------------------------------------------------------------------ mode
    def _set_mode(self, mode):
        if mode == self._mode:
            return
        self._mode = mode
        self._cli_btn.setChecked(mode == "client")
        self._srv_btn.setChecked(mode == "server")
        self._stack.setCurrentIndex(0 if mode == "client" else 1)

    # ================================================================== client
    def _on_connect(self):
        code = self._code_text.toPlainText().strip()
        if not code:
            self._set_status("Enter the connection code first.", False)
            self._code_text.setFocus()
            return
        try:
            port = int(self._cli_port.text().strip() or "25566")
        except ValueError:
            port = 25566
        if self._client is None:
            self._client = P2PClient(
                listen_host="127.0.0.1", listen_port=port,
                on_state=lambda s, **k: self._cli_state.emit(s))
        else:
            self._client.listen_port = port
        try:
            self._connect_btn.setEnabled(False)
            self._set_status("Connecting…", False)
            self._client.connect_from_code(code)
            self._connect_btn.setEnabled(False)
            self._disc_btn.setEnabled(True)
            self._set_status("Connected. Local apps can now use the port.", True)
        except ValueError:
            self._connect_btn.setEnabled(True)
            self._set_status("Not recognized: this isn't an OpenMCL code.", False)
        except Exception as e:
            self._connect_btn.setEnabled(True)
            self._set_status("Connect failed: %s" % e, False)

    def _on_disconnect(self):
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
        self._connect_btn.setEnabled(True)
        self._disc_btn.setEnabled(False)
        self._set_status("Disconnected.", False)

    def _apply_cli(self, state):
        if state == "channel_open":
            self._set_status("DataChannel open - forwarding active.", True)
        elif state == "target_error":
            self._set_status("The tunnel is up but the local service is unreachable.",
                             False)

    # ================================================================== server
    def _on_start(self):
        try:
            host = self._srv_host.text().strip() or "127.0.0.1"
            port = int(self._srv_port.text().strip() or "25565")
        except ValueError:
            port = 25565
        try:
            self._server = P2PServer(
                target_host=host, target_port=port,
                on_state=lambda s, **k: self._srv_state.emit(s),
                on_clients=lambda info: self._srv_clients.emit(info))
            self._server.start()
            self._start_btn.setEnabled(False)
            self._stop_btn.setEnabled(True)
            self._gen_btn.setEnabled(True)
            self._set_status("Service started on signal port %d." %
                             self._server._active_port, True)
        except Exception as e:
            self._set_status("Start failed: %s" % e, False)

    def _on_stop_server(self):
        if self._server is not None:
            try:
                self._server.stop()
            except Exception:
                pass
            self._server = None
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._gen_btn.setEnabled(False)
        self._code_out.clear()
        self._clients.clear()
        self._clients_lbl.setText("CONNECTED CLIENTS (0)")
        self._set_status("Service stopped.", False)

    def _on_gen_code(self):
        if self._server is None:
            return
        try:
            code = self._server.generate_code()
            self._code_out.setPlainText(code)
            QApplication.clipboard().setText(code)
            self._set_status("Code generated & copied to clipboard.", True)
        except Exception as e:
            self._set_status("Generate failed: %s" % e, False)

    def _on_copy_code(self):
        text = self._code_out.toPlainText().strip()
        if text:
            QApplication.clipboard().setText(text)
            self._set_status("Code copied to clipboard.", True)
        else:
            self._set_status("Nothing to copy yet.", False)

    def _apply_srv_state(self, state):
        if state == "channel_open":
            self._set_status("A client connected - forwarding active.", True)
        elif state == "target_error":
            self._set_status("Forwarding target unreachable.", False)

    def _apply_clients(self, info):
        if self._server is None:
            return
        self._clients.clear()
        for c in (info or []):
            item = QListWidgetItem("  #%d   -   %s" % (c["id"], c["state"]))
            item.setData(Qt.UserRole, c["id"])
            self._clients.addItem(item)
        self._clients_lbl.setText("CONNECTED CLIENTS (%d)" % len(info or []))

    def _refresh_clients(self):
        if self._server is not None:
            try:
                self._srv_clients.emit(self._server.clients_info())
            except Exception:
                pass

    def _on_disc_client(self):
        if self._server is None:
            return
        item = self._clients.currentItem()
        if item is None:
            self._set_status("Select a client to disconnect.", False)
            return
        cid = item.data(Qt.UserRole)
        self._server.disconnect(cid)
        self._set_status("Client #%d disconnected." % cid, False)

    # ------------------------------------------------------------------ status
    def _set_status(self, text, ok):
        self._status.setText(text)
        if text.lower().startswith(("enter", "not ", "failed", "no ", "connect failed",
                                    "start failed", "service stopped", "disconnected",
                                    "select a", "nothing", "generate failed", "the tunnel")):
            self._status.setStyleSheet(_ERR)
        else:
            self._status.setStyleSheet(_GREEN if ok else _DIM)

    def _unload(self):
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
        if self._server is not None:
            try:
                self._server.stop()
            except Exception:
                pass