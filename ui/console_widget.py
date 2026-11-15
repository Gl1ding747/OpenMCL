from PyQt5.QtWidgets import QWidget, QVBoxLayout, QTextEdit, QPushButton, QHBoxLayout
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont, QColor
from i18n import tr


class ConsoleWidget(QWidget):
    _append_signal = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self._init_ui()
        self._append_signal.connect(self._do_append)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setFont(QFont("Consolas", 9))
        self.text_area.setFrameShape(QTextEdit.NoFrame)
        self.text_area.setStyleSheet("QTextEdit { background: #121214; border: none; }")

        self.clear_btn = QPushButton(tr("Clear"))
        self.clear_btn.clicked.connect(self.clear)
        self.clear_btn.setStyleSheet(
            "QPushButton { color: #808088; background: transparent; border: none; padding: 2px 6px; }"
            "QPushButton:hover { color: #c0c0c8; }")
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self.clear_btn)
        layout.addLayout(btn_row)
        layout.addWidget(self.text_area, 1)

    def _do_append(self, text, color):
        self.text_area.setTextColor(QColor(color) if color else QColor("#e8e8e8"))
        self.text_area.append(text)
        self.text_area.ensureCursorVisible()

    def append(self, text, color=None):
        self._append_signal.emit(text, color or "")

    def append_info(self, text):
        self._append_signal.emit(text, "#4fc1ff")

    def append_warning(self, text):
        self._append_signal.emit(text, "#dcdcaa")

    def append_error(self, text):
        self._append_signal.emit(text, "#f44747")

    def append_success(self, text):
        self._append_signal.emit(text, "#6a9955")

    def clear(self):
        self.text_area.clear()