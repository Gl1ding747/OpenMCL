from PyQt5.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QLineEdit, QTextEdit, QFileDialog,
                             QListWidget, QListWidgetItem, QProgressBar, QScrollArea, QWidget,
                             QComboBox)
from PyQt5.QtCore import Qt, QEasingCurve, QPropertyAnimation, QRect, QPoint, pyqtSignal, QThread, QTimer
from PyQt5.QtGui import QFont, QColor
import os

from i18n import tr

STYLE = """
SidePanel {
    background: #18181e;
    border-right: 1px solid #2a2a30;
}
"""

TASK_BAR_STYLE = """
QProgressBar {
    background: #222228; border: none; height: 4px; border-radius: 2px;
}
QProgressBar::chunk {
    background: #3a3a80; border-radius: 2px;
}
"""

class SidePanel(QFrame):
    result_ready = pyqtSignal(object)

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("sidePanel")
        self.setStyleSheet(STYLE)
        self.setFixedWidth(320)
        self._function_width = 560
        self._normal_width = 320
        self.setVisible(False)
        self._anim = None
        self._mode = None
        self._result = None
        self._line_input = None
        self._text_edit = None
        self._tasks = {}
        self._task_widgets = {}
        self._collapsed = False
        self._function_widget = None

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        self._top_bar = QHBoxLayout()
        self._hide_btn = QPushButton("\u2014")
        self._hide_btn.setFixedSize(28, 28)
        self._hide_btn.setStyleSheet("""
            QPushButton { background: transparent; color: #505058; border: none; font-size: 16px; }
            QPushButton:hover { color: #a0a0a8; }
        """)
        self._hide_btn.clicked.connect(self._toggle_collapse)
        self._top_bar.addStretch()
        self._top_bar.addWidget(self._hide_btn)
        layout.addLayout(self._top_bar)

        self._header = QVBoxLayout()
        self._back_btn = QPushButton("\u2190 " + tr("Back"))
        self._back_btn.setFixedHeight(32)
        self._back_btn.setStyleSheet("""
            QPushButton { background: transparent; color: #808088; border: none;
                          font-size: 13px; padding: 4px 0; text-align: left; }
            QPushButton:hover { color: #d0d0d0; }
        """)
        self._back_btn.clicked.connect(self._dismiss)
        self._header.addWidget(self._back_btn)

        self._title_lbl = QLabel()
        self._title_lbl.setStyleSheet("color: #e0e0e0; font-size: 18px; font-weight: 300; padding: 4px 0;")
        self._header.addWidget(self._title_lbl)
        layout.addLayout(self._header)

        self._task_scroll = QScrollArea(self)
        self._task_scroll.setWidgetResizable(True)
        self._task_scroll.setFrameShape(QFrame.NoFrame)
        self._task_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._task_container = QWidget()
        self._task_container.setStyleSheet("background: transparent;")
        self._task_layout = QVBoxLayout(self._task_container)
        self._task_layout.setContentsMargins(0, 0, 0, 0)
        self._task_layout.setSpacing(8)
        self._task_layout.addStretch()
        self._task_scroll.setWidget(self._task_container)
        layout.addWidget(self._task_scroll, 1)

        self._body = QVBoxLayout()
        self._body.setSpacing(12)
        layout.addLayout(self._body)

        self._buttons = QHBoxLayout()
        self._buttons.setSpacing(10)
        layout.addLayout(self._buttons)

        layout.addStretch()

    def _toggle_collapse(self):
        if self._collapsed:
            self._collapsed = False
            self._hide_btn.setText("\u2014")
            self._task_scroll.show()
            self._header_widget().show() if hasattr(self, '_header_widget') else None
            for w in self._find_all_widgets(self._body):
                w.show()
            for w in self._find_all_widgets(self._buttons):
                w.show()
            self._back_btn.show()
            self._title_lbl.show()
        else:
            self._collapsed = True
            self._hide_btn.setText("+")
            self._task_scroll.hide()
            for w in self._find_all_widgets(self._body):
                w.hide()
            for w in self._find_all_widgets(self._buttons):
                w.hide()
            self._back_btn.hide()
            self._title_lbl.hide()

    @staticmethod
    def _find_all_widgets(layout):
        result = []
        for i in range(layout.count()):
            w = layout.itemAt(i).widget()
            if w:
                result.append(w)
        return result

    def _clear_body(self):
        while self._body.count():
            item = self._body.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _clear_buttons(self):
        while self._buttons.count():
            item = self._buttons.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _clear_tasks(self):
        for tid in list(self._task_widgets):
            self._remove_task_widget(tid)
        self._tasks.clear()

    def _mod_stop_search(self):
        if hasattr(self, '_mod_search_thread') and self._mod_search_thread:
            try:
                self._mod_search_thread.result.disconnect()
            except Exception:
                pass
            self._mod_search_thread.quit()
            self._mod_search_thread.wait(100)
            self._mod_search_thread = None
        if hasattr(self, '_mod_vers_thread') and self._mod_vers_thread:
            try:
                self._mod_vers_thread.result.disconnect()
            except Exception:
                pass
            self._mod_vers_thread.quit()
            self._mod_vers_thread.wait(100)
            self._mod_vers_thread = None

    def _remove_task_widget(self, task_id):
        if task_id in self._task_widgets:
            container = self._task_widgets.pop(task_id)
            idx = self._task_layout.indexOf(container)
            if idx >= 0:
                self._task_layout.takeAt(idx)
            container.deleteLater()

    def _btn_style(self, accent=False):
        if accent:
            return """QPushButton { background: #3a3a80; color: #f0f0f0; border: none;
                       padding: 10px 20px; font-size: 13px; }
                    QPushButton:hover { background: #4a4a90; }
                    QPushButton:pressed { background: #2a2a60; }"""
        return """QPushButton { background: transparent; color: #808088; border: 1px solid #3a3a40;
                   padding: 10px 20px; font-size: 13px; }
                QPushButton:hover { color: #d0d0d0; border-color: #5a5a60; }
                QPushButton:pressed { background: #1a1a2a; }"""

    def show_message(self, message_type, title, text):
        self._mode = "message"
        self._collapsed = False
        self._hide_btn.setText("\u2014")
        self._clear_body()
        self._clear_buttons()
        self._title_lbl.setText(title)
        self._task_scroll.hide()
        self._back_btn.show()
        self._title_lbl.show()

        msg = QLabel(text)
        msg.setWordWrap(True)
        msg.setStyleSheet("color: #b0b0b8; font-size: 14px; padding: 4px 0;")
        self._body.addWidget(msg)
        self._body.addStretch()

        if message_type == "question":
            yes_btn = QPushButton(tr("Yes"))
            yes_btn.setStyleSheet(self._btn_style(accent=True))
            yes_btn.clicked.connect(lambda: self._finish(True))
            self._buttons.addWidget(yes_btn)
            no_btn = QPushButton(tr("No"))
            no_btn.setStyleSheet(self._btn_style())
            no_btn.clicked.connect(lambda: self._finish(False))
            self._buttons.addWidget(no_btn)
        else:
            ok_btn = QPushButton(tr("OK"))
            ok_btn.setStyleSheet(self._btn_style(accent=True))
            ok_btn.clicked.connect(lambda: self._finish(None))
            self._buttons.addWidget(ok_btn)

        self._slide_in()

    def show_input(self, title, label, default="", multiline=False):
        self._mode = "input"
        self._collapsed = False
        self._hide_btn.setText("\u2014")
        self._clear_body()
        self._clear_buttons()
        self._title_lbl.setText(title)
        self._task_scroll.hide()
        self._back_btn.show()
        self._title_lbl.show()

        lbl = QLabel(label)
        lbl.setStyleSheet("color: #a0a0a8; font-size: 13px;")
        self._body.addWidget(lbl)

        if multiline:
            self._text_edit = QTextEdit()
            self._text_edit.setPlainText(default)
            self._text_edit.setStyleSheet("""QTextEdit { background: #222228; color: #e0e0e0;
                border: 1px solid #3a3a40; padding: 8px; font-size: 13px; }""")
            self._body.addWidget(self._text_edit, 1)
            self._line_input = None
        else:
            self._text_edit = None
            self._line_input = QLineEdit(default)
            self._line_input.setStyleSheet("""QLineEdit { background: #222228; color: #e0e0e0;
                border: 1px solid #3a3a40; padding: 10px 12px; font-size: 13px; }""")
            self._body.addWidget(self._line_input)

        self._body.addStretch()

        ok_btn = QPushButton(tr("OK"))
        ok_btn.setStyleSheet(self._btn_style(accent=True))
        ok_btn.clicked.connect(self._on_input_ok)
        self._buttons.addWidget(ok_btn)
        cancel_btn = QPushButton(tr("Cancel"))
        cancel_btn.setStyleSheet(self._btn_style())
        cancel_btn.clicked.connect(self._dismiss)
        self._buttons.addWidget(cancel_btn)

        self._slide_in()

    def show_form(self, title, fields):
        self._mode = "form"
        self._collapsed = False
        self._hide_btn.setText("\u2014")
        self._clear_body()
        self._clear_buttons()
        self._title_lbl.setText(title)
        self._task_scroll.hide()
        self._back_btn.show()
        self._title_lbl.show()

        self._form_fields = []
        for label, default, is_password in fields:
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #a0a0a8; font-size: 13px;")
            self._body.addWidget(lbl)

            le = QLineEdit(default)
            if is_password:
                le.setEchoMode(QLineEdit.Password)
            le.setStyleSheet("""QLineEdit { background: #222228; color: #e0e0e0;
                border: 1px solid #3a3a40; padding: 10px 12px; font-size: 13px; }""")
            self._body.addWidget(le)
            self._form_fields.append(le)

        self._body.addStretch()

        ok_btn = QPushButton(tr("OK"))
        ok_btn.setStyleSheet(self._btn_style(accent=True))
        ok_btn.clicked.connect(self._on_form_ok)
        self._buttons.addWidget(ok_btn)
        cancel_btn = QPushButton(tr("Cancel"))
        cancel_btn.setStyleSheet(self._btn_style())
        cancel_btn.clicked.connect(self._dismiss)
        self._buttons.addWidget(cancel_btn)

        self._slide_in()

    def _on_form_ok(self):
        result = {i: le.text() for i, le in enumerate(self._form_fields)}
        self._finish(result)

    def show_progress(self, title, text=""):
        if self._mode in ("message", "input", "form"):
            self._clear_body()
            self._clear_buttons()
        self._mode = "task"
        self._collapsed = False
        self._hide_btn.setText("\u2014")
        self._back_btn.hide()
        self._title_lbl.hide()
        self._task_scroll.show()

        for w in self._find_all_widgets(self._body): w.hide()
        for w in self._find_all_widgets(self._buttons): w.hide()

        import time
        tid = str(int(time.time() * 1000))
        self._tasks[tid] = {"title": title, "text": text}
        self._add_task_widget(tid, title, text)
        self._slide_in()
        return tid

    def update_progress(self, task_id, text):
        if task_id in self._tasks:
            self._tasks[task_id]["text"] = text
            if task_id in self._task_widgets:
                container = self._task_widgets[task_id]
                lbl = container.findChild(QLabel, "task_text")
                if lbl:
                    lbl.setText(text)

    def update_progress_bar(self, task_id, minimum, maximum, value):
        if task_id in self._task_widgets:
            container = self._task_widgets[task_id]
            bar = container.findChild(QProgressBar)
            if bar:
                bar.setRange(minimum, maximum)
                bar.setValue(value)

    def finish_progress(self, task_id):
        self._remove_task_widget(task_id)
        self._tasks.pop(task_id, None)
        if not self._tasks:
            self._dismiss()

    def hide_panel(self):
        self._dismiss()

    def _add_task_widget(self, tid, title, text):
        container = QWidget()
        container.setStyleSheet("background: #202028; border-radius: 6px;")
        vl = QVBoxLayout(container)
        vl.setContentsMargins(12, 10, 12, 8)
        vl.setSpacing(4)

        t = QLabel(title)
        t.setStyleSheet("color: #d0d0d0; font-size: 13px; font-weight: 500; background: transparent;")
        vl.addWidget(t)

        s = QLabel(text)
        s.setObjectName("task_text")
        s.setStyleSheet("color: #606068; font-size: 11px; background: transparent;")
        vl.addWidget(s)

        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        bar.setStyleSheet(TASK_BAR_STYLE)
        vl.addWidget(bar)

        close_btn = QPushButton("\u2715")
        close_btn.setFixedSize(20, 20)
        close_btn.setStyleSheet("""
            QPushButton { background: transparent; color: #505058; border: none; font-size: 12px; }
            QPushButton:hover { color: #d0d0d0; }
        """)
        close_btn.clicked.connect(lambda: self.finish_progress(tid))

        top = QHBoxLayout()
        top.addWidget(t)
        top.addStretch()
        top.addWidget(close_btn)

        vl.insertLayout(0, top)
        vl.takeAt(1)
        vl.insertWidget(2, s)

        idx = self._task_layout.count() - 1
        self._task_layout.insertWidget(idx, container)
        self._task_widgets[tid] = container

    def _on_input_ok(self):
        if self._line_input:
            text = self._line_input.text()
        elif self._text_edit:
            text = self._text_edit.toPlainText()
        else:
            text = ""
        self._finish(text)

    def _finish(self, result):
        self._result = result
        self._slide_out()
        self.result_ready.emit(result)

    def _dismiss(self):
        if self._mode == "function" and self._function_widget:
            self.setFixedWidth(self._normal_width)
            if hasattr(self, '_function_scroll') and self._function_scroll:
                self._function_scroll.takeWidget()
                self._body.removeWidget(self._function_scroll)
                self._function_scroll.deleteLater()
                self._function_scroll = None
            self._function_widget.hide()
            self._function_widget = None
        self._result = None
        self._clear_tasks()
        self._mod_stop_search()
        self._slide_out()
        self.result_ready.emit(None)

    def _slide_in(self):
        parent = self.parent()
        if parent is None:
            return
        ph = parent.height()
        self.setGeometry(-self.width(), 0, self.width(), ph)
        self.show()
        self.raise_()
        if self._anim and self._anim.state() == QPropertyAnimation.Running:
            self._anim.stop()
        self._anim = QPropertyAnimation(self, b"pos")
        self._anim.setDuration(280)
        self._anim.setStartValue(QPoint(-self.width(), 0))
        self._anim.setEndValue(QPoint(0, 0))
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.start()

    def _slide_out(self):
        if self._anim and self._anim.state() == QPropertyAnimation.Running:
            self._anim.stop()
        self._anim = QPropertyAnimation(self, b"pos")
        self._anim.setDuration(200)
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(QPoint(-self.width(), 0))
        self._anim.setEasingCurve(QEasingCurve.InCubic)
        self._anim.finished.connect(self.hide)
        self._anim.start()

    def show_function_widget(self, widget):
        self._mode = "function"
        self._collapsed = False
        self._hide_btn.setText("\u2014")
        self.setFixedWidth(self._function_width)
        self._clear_body()
        self._clear_buttons()
        self._title_lbl.setText("Tools")
        self._task_scroll.hide()
        self._back_btn.show()
        self._title_lbl.show()

        self._function_widget = widget
        self._function_scroll = QScrollArea()
        self._function_scroll.setWidgetResizable(True)
        self._function_scroll.setFrameShape(QFrame.NoFrame)
        self._function_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._function_scroll.setWidget(widget)
        self._body.addWidget(self._function_scroll, 1)
        self._slide_in()

    def show_mod_browser(self, title, mc_version, loader, mods_dir, aria2_mgr):
        self._mode = "mod_browser"
        self._collapsed = False
        self._hide_btn.setText("\u2014")
        self._clear_body()
        self._clear_buttons()
        self._title_lbl.setText(title)
        self._task_scroll.hide()
        self._back_btn.show()
        self._title_lbl.show()

        self._mod_mc_version = mc_version
        self._mod_loader = loader
        self._mods_dir = mods_dir
        self._mod_aria2 = aria2_mgr
        self._mod_results = []
        self._mod_last_search_results = []
        self._mod_showing_versions = False
        self._mod_selected_version = None
        self._mod_name_edit = None

        search_layout = QHBoxLayout()
        self._mod_search_input = QLineEdit()
        self._mod_search_input.setPlaceholderText(tr("Search Modrinth..."))
        self._mod_search_input.returnPressed.connect(self._on_mod_search)
        self._mod_search_input.setStyleSheet("""QLineEdit { background: #222228; color: #e0e0e0;
            border: 1px solid #3a3a40; padding: 8px 10px; font-size: 12px; }""")
        search_layout.addWidget(self._mod_search_input, 1)

        search_btn = QPushButton(tr("Search"))
        search_btn.setFixedWidth(60)
        search_btn.setStyleSheet("""QPushButton { background: #2a2a3a; color: #a0a0a8;
            border: 1px solid #3a3a40; padding: 6px; font-size: 12px; }
            QPushButton:hover { color: #d0d0d0; }""")
        search_btn.clicked.connect(self._on_mod_search)
        search_layout.addWidget(search_btn)
        self._body.addLayout(search_layout)

        info_lbl = QLabel(f"MC: {mc_version}  |  Loader: {loader or 'none'}")
        info_lbl.setStyleSheet("color: #606068; font-size: 11px; padding: 2px 0;")
        self._body.addWidget(info_lbl)

        self._mod_result_list = QListWidget()
        self._mod_result_list.setStyleSheet("""QListWidget { background: #1a1a22; color: #c0c0c8;
            border: 1px solid #2a2a30; font-size: 12px; }
            QListWidget::item { padding: 6px 8px; border-bottom: 1px solid #222230; }
            QListWidget::item:selected { background: #2a2a40; color: #e0e0e0; }""")
        self._mod_result_list.currentRowChanged.connect(self._on_mod_result_selected)
        self._body.addWidget(self._mod_result_list, 1)

        self._mod_detail_label = QLabel("")
        self._mod_detail_label.setWordWrap(True)
        self._mod_detail_label.setStyleSheet("color: #808088; font-size: 11px; padding: 4px 0;")
        self._body.addWidget(self._mod_detail_label)

        self._body.addStretch()

        self._mod_install_btn = QPushButton(tr("Install"))
        self._mod_install_btn.setStyleSheet(self._btn_style(accent=True))
        self._mod_install_btn.hide()
        self._mod_install_btn.clicked.connect(self._on_mod_install)
        self._buttons.addWidget(self._mod_install_btn)

        self._mod_version_back_btn = QPushButton(tr("← Back"))
        self._mod_version_back_btn.setStyleSheet(self._btn_style())
        self._mod_version_back_btn.clicked.connect(self._on_mod_version_back)
        self._mod_version_back_btn.hide()
        self._buttons.addWidget(self._mod_version_back_btn)

        self._mod_version_back_btn = QPushButton(tr("← Back"))
        self._mod_version_back_btn.setStyleSheet(self._btn_style())
        self._mod_version_back_btn.clicked.connect(self._on_mod_version_back)
        self._mod_version_back_btn.hide()
        self._buttons.addWidget(self._mod_version_back_btn)

        self._mod_version_back_btn = QPushButton(tr("← Back"))
        self._mod_version_back_btn.setStyleSheet(self._btn_style())
        self._mod_version_back_btn.clicked.connect(self._on_mod_version_back)
        self._mod_version_back_btn.hide()
        self._buttons.addWidget(self._mod_version_back_btn)

        self._mod_close_btn = QPushButton(tr("Close"))
        self._mod_close_btn.setStyleSheet(self._btn_style())
        self._mod_close_btn.clicked.connect(self._dismiss)
        self._buttons.addWidget(self._mod_close_btn)

        self._slide_in()
        QTimer.singleShot(300, lambda: self._mod_search_input.setFocus())

    def _on_mod_search(self):
        query = self._mod_search_input.text().strip()
        self._mod_result_list.clear()
        self._mod_detail_label.setText(tr("Searching..."))
        self._mod_results = []

        facets = []
        if self._mod_mc_version:
            facets.append([f"versions:{self._mod_mc_version}"])
        if self._mod_loader:
            facets.append([f"categories:{self._mod_loader}"])

        class _ModrinthSearch(QThread):
            result = pyqtSignal(list, str)
            def __init__(self, q, f):
                super().__init__()
                self.q = q
                self.f = f
            def run(self):
                try:
                    import requests, json
                    params = {"query": self.q, "limit": 20, "index": "relevance"}
                    if self.f:
                        params["facets"] = json.dumps(self.f)
                    r = requests.get("https://api.modrinth.com/v2/search", params=params,
                                     timeout=20, headers={"User-Agent": "OpenMCL/1.0"})
                    r.raise_for_status()
                    hits = r.json().get("hits", [])
                    self.result.emit(hits, "")
                except Exception as e:
                    self.result.emit([], str(e))

        self._mod_search_thread = _ModrinthSearch(query, facets)
        self._mod_search_thread.result.connect(self._on_mod_search_done)
        self._mod_search_thread.start()

    def _on_mod_search_done(self, hits, error):
        self._mod_results = hits
        self._mod_last_search_results = hits
        self._mod_result_list.clear()
        if error:
            self._mod_detail_label.setText(f"Error: {error}")
            return
        if not hits:
            self._mod_detail_label.setText(tr("No results found."))
            return
        self._mod_detail_label.setText(f"{len(hits)} results")
        for hit in hits:
            title = hit.get("title", hit.get("slug", "?"))
            desc = hit.get("description", "")[:80]
            item = QListWidgetItem(f"{title}\n{desc}")
            item.setData(Qt.UserRole, hit)
            self._mod_result_list.addItem(item)

    def _on_mod_result_selected(self, row):
        if self._mod_showing_versions:
            self._on_mod_version_selected(row)
            return
        if row < 0 or row >= len(self._mod_results):
            self._mod_install_btn.hide()
            return
        hit = self._mod_results[row]
        pid = hit.get("project_id", "")
        self._mod_detail_label.setText(tr("Loading versions..."))

        class _ModrinthVersions(QThread):
            result = pyqtSignal(dict, list, str)
            def __init__(self, p):
                super().__init__()
                self.p = p
            def run(self):
                try:
                    import requests
                    r = requests.get(f"https://api.modrinth.com/v2/project/{self.p}",
                                     timeout=20, headers={"User-Agent": "OpenMCL/1.0"})
                    r.raise_for_status()
                    proj = r.json()
                    r2 = requests.get(f"https://api.modrinth.com/v2/project/{self.p}/version",
                                      timeout=20, headers={"User-Agent": "OpenMCL/1.0"})
                    r2.raise_for_status()
                    vers = r2.json()
                    self.result.emit(proj, vers, "")
                except Exception as e:
                    self.result.emit({}, [], str(e))

        self._mod_vers_thread = _ModrinthVersions(pid)
        self._mod_vers_thread.result.connect(self._on_mod_versions_done)
        self._mod_vers_thread.start()

    def _on_mod_versions_done(self, project, versions, error):
        self._mod_showing_versions = True
        self._mod_selected_version = None
        self._mod_install_btn.hide()
        self._mod_detail_label.hide()
        self._mod_version_back_btn.show()
        self._mod_all_versions = versions
        self._mod_result_list.clear()
        if error:
            self._mod_result_list.addItem(f"Error: {error}")
            return
        self._mod_project = project
        compatible = []
        other = []
        for v in versions:
            gv = v.get("game_versions", [])
            vl = v.get("loaders", [])
            if self._mod_mc_version and self._mod_mc_version not in gv:
                other.append(v)
            elif self._mod_loader and self._mod_loader not in vl:
                other.append(v)
            else:
                compatible.append(v)
        shown = compatible + other
        for v in shown:
            gv_str = ", ".join(v.get("game_versions", [])[:4])
            ld_str = ", ".join(v.get("loaders", [])[:3]) or "any"
            types = v.get("version_type", "release")
            prefix = "  " if v in other else ""
            item = QListWidgetItem(
                f"{prefix}{v.get('version_number', '?')}  |  MC {gv_str}  |  {ld_str}")
            item.setData(Qt.UserRole, v)
            item.setToolTip(str(v.get("name", "")))
            self._mod_result_list.addItem(item)
        if compatible:
            self._mod_result_list.insertItem(0,
                QListWidgetItem(f"--- Compatible ({len(compatible)}) ---"))
        if other:
            self._mod_result_list.insertItem(
                len(compatible) + 1,
                QListWidgetItem(f"--- Other ({len(other)}) ---"))
        self._mod_detail_label.setText(
            f"{project.get('title', '?')}: {len(versions)} versions, "
            f"{len(compatible)} compatible")

    def _on_mod_version_selected(self, row):
        v = self._mod_result_list.item(row)
        if not v:
            return
        vdata = v.data(Qt.UserRole)
        if not isinstance(vdata, dict):
            return
        self._mod_selected_version = vdata
        files = vdata.get("files", [])
        fnames = ", ".join(f.get("filename", "?") for f in files[:3])
        self._mod_detail_label.show()
        self._mod_detail_label.setText(
            f"{self._mod_project.get('title', '?')} v{vdata.get('version_number', '?')}\n"
            f"Files: {fnames}\n"
            f"Type: {vdata.get('version_type', '?')}")
        self._mod_install_btn.show()

    def _on_mod_version_back(self):
        self._mod_showing_versions = False
        self._mod_version_back_btn.hide()
        self._mod_selected_version = None
        self._mod_install_btn.hide()
        self._mod_result_list.clear()
        self._mod_detail_label.show()
        if self._mod_last_search_results:
            for hit in self._mod_last_search_results:
                title = hit.get("title", hit.get("slug", "?"))
                desc = hit.get("description", "")[:80]
                item = QListWidgetItem(f"{title}\n{desc}")
                item.setData(Qt.UserRole, hit)
                self._mod_result_list.addItem(item)
            self._mod_detail_label.setText(f"{len(self._mod_last_search_results)} results")
        else:
            self._mod_detail_label.setText(tr("No results."))

    def _on_mod_install(self):
        if not self._mod_selected_version:
            return
        vdata = self._mod_selected_version
        files = vdata.get("files", [])
        if not files:
            self._mod_detail_label.setText(tr("No files in version."))
            return
        primary = files[0]
        url = primary.get("url", "")
        fname = primary.get("filename", "mod.jar")
        target = os.path.join(self._mods_dir, fname)

        class _ModInstallThread(QThread):
            progress = pyqtSignal(str)
            done = pyqtSignal(bool, str)
            def __init__(self, url, target, aria2):
                super().__init__()
                self.url = url
                self.target = target
                self.aria2 = aria2
            def run(self):
                try:
                    self.progress.emit("Downloading mod...")
                    os.makedirs(os.path.dirname(self.target), exist_ok=True)
                    if self.aria2 and self.aria2.is_running():
                        succ, fail = self.aria2.batch_download(
                            [(self.url, self.target, "")],
                            progress_callback=lambda *a: None)
                        if succ == 1:
                            self.done.emit(True, os.path.basename(self.target))
                        else:
                            self._fallback_http()
                    else:
                        self._fallback_http()
                except Exception as e:
                    self.done.emit(False, str(e))
            def _fallback_http(self):
                try:
                    import requests
                    r = requests.get(self.url, timeout=120,
                                     headers={"User-Agent": "OpenMCL/1.0"})
                    r.raise_for_status()
                    with open(self.target, "wb") as f:
                        f.write(r.content)
                    self.done.emit(True, os.path.basename(self.target))
                except Exception as e:
                    self.done.emit(False, str(e))

        tid = self.show_progress("Mod Install", f"Downloading {fname}...")
        self._mod_install_btn.hide()
        self._mod_close_btn.hide()

        self._mod_dl_thread = _ModInstallThread(url, target, self._mod_aria2)
        self._mod_dl_thread.progress.connect(lambda t: self.update_progress(tid, t))
        self._mod_dl_thread.done.connect(lambda ok, msg: self._on_mod_install_done(tid, ok, msg))
        self._mod_dl_thread.start()

    def _on_mod_install_done(self, tid, ok, msg):
        if ok:
            self.update_progress(tid, f"Installed: {msg}")
            self.finish_progress(tid)
            self._dismiss()
        else:
            self.update_progress(tid, f"Failed: {msg}")
            self._mod_install_btn.show()
            self._mod_close_btn.show()