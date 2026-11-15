from PyQt5.QtWidgets import (QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QLabel,
                             QListWidget, QListWidgetItem)
from PyQt5.QtCore import Qt, QTimer, QRectF, QPointF, QSize, QThread, pyqtSignal
from PyQt5.QtGui import QPainter, QColor, QPen, QFont, QBrush, QPixmap

import math

from core.minimap import MinimapEngine, block_color

# 优先用 QOpenGLWidget 做 GPU 渲染(无需 PyOpenGL)；无 GL 环境时自动回退到普通 QWidget。
try:
    from PyQt5.QtWidgets import QOpenGLWidget as _GLWidget
    _USE_GL = True
except Exception:  # 兼容无 OpenGL 的环境
    _GLWidget = QWidget
    _USE_GL = False


ZOOM_LEVELS = [1, 2, 4, 8]
ZOOM_LABELS = ["1:1", "1:2", "1:4", "1:8"]

# 顶部标签按钮：四个显示模式（缩放改由鼠标滚轮控制）
VIEW_MODES = [("terrain", "地形"), ("cave", "空洞"), ("ore", "矿石"), ("entity", "实体")]


class MinimapCanvas(_GLWidget):
    PAN_ZOOM_QSS = """
        QPushButton { background: #181820; color: #a0a0c0; border: 1px solid #303040;
                      padding: 3px 6px; font-size: 10px; min-width: 22px; }
        QPushButton:hover { background: #222230; color: #c0c0e0; border-color: #404058; }
        QPushButton:checked { background: #283048; color: #e0e0f0; border-color: #485078; }
    """

    CHUNKS_PER_TICK = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self._engine = None
        self._player_x = 0.0
        self._player_z = 0.0
        self._player_valid = False
        self._offset_x = 0.0
        self._offset_z = 0.0
        self._zoom_level = 0
        self._zoom = ZOOM_LEVELS[0]
        self._drag_start = None
        self._dragging = False
        self._mode = "terrain"

        self._map_pixmap = None
        self._chunk_queue = []
        self._chunk_done = set()
        self._snap_offset_x = 0.0
        self._snap_offset_z = 0.0
        self._snap_zoom = 1
        self._snap_tile = 1
        self._render_complete = False

        self._render_timer = QTimer(self)
        self._render_timer.setInterval(0)
        self._render_timer.timeout.connect(self._tick_chunks)

        self.setMinimumSize(300, 300)
        self.setStyleSheet("background: #000008;")
        self.setMouseTracking(True)
        if _USE_GL:
            # GPU 直接写屏：关闭部分更新，整帧合成更高效
            self.setUpdateBehavior(_GLWidget.NoPartialUpdate)

    def initializeGL(self):
        pass

    def _height_getter(self, ent):
        """按实体 Y 高度返回 0..1 的亮度因子：越高越亮、越低越暗。"""
        y = ent.get("y")
        if not isinstance(y, (int, float)):
            return 0.6
        # 主世界常用 Y 范围约 -64..320，归一化后越低越暗、越高越亮
        t = (y - (-64.0)) / 384.0
        return max(0.15, min(1.0, t))

    def set_engine(self, engine):
        self._engine = engine
        self._invalidate()

    def update_position(self, player_x, player_z, player_valid):
        self._player_x = player_x
        self._player_z = player_z
        self._player_valid = player_valid
        if player_valid and not self._dragging:
            if self._offset_x != player_x or self._offset_z != player_z:
                self._offset_x = player_x
                self._offset_z = player_z
                self._invalidate()
        self.update()

    def set_zoom(self, level):
        self._zoom_level = level
        self._zoom = ZOOM_LEVELS[level]
        self._invalidate()

    def set_mode(self, mode):
        if mode != self._mode:
            self._mode = mode
            self._invalidate()

    def _invalidate(self):
        self._render_timer.stop()
        self._chunk_queue = []
        self._chunk_done.clear()
        self._map_pixmap = None
        self._render_complete = False
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start = event.pos()
            self._dragging = False
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._drag_start is not None:
            dx = event.pos().x() - self._drag_start.x()
            dy = event.pos().y() - self._drag_start.y()
            if abs(dx) >= 3 or abs(dy) >= 3:
                self._dragging = True
            if self._dragging:
                self._offset_x -= dx * self._zoom
                self._offset_z -= dy * self._zoom
                self._drag_start = event.pos()
                self._invalidate()

    def mouseReleaseEvent(self, event):
        if self._drag_start is not None:
            was_dragging = self._dragging
            self._drag_start = None
            self.setCursor(Qt.ArrowCursor)
            # 拖拽结束后若已定位到玩家，立刻重新居中跟随，避免“地图不跟人”
            if was_dragging and self._player_valid:
                self._offset_x = self._player_x
                self._offset_z = self._player_z
                self._invalidate()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0 and self._zoom_level > 0:
            self.set_zoom(self._zoom_level - 1)
        elif delta < 0 and self._zoom_level < len(ZOOM_LEVELS) - 1:
            self.set_zoom(self._zoom_level + 1)

    def _world_to_screen(self, wx, wz):
        sx = (wx - self._offset_x) / self._zoom + self.width() / 2.0
        sz = (wz - self._offset_z) / self._zoom + self.height() / 2.0
        return sx, sz

    def _screen_to_world(self, sx, sz):
        wx = (sx - self.width() / 2.0) * self._zoom + self._offset_x
        wz = (sz - self.height() / 2.0) * self._zoom + self._offset_z
        return wx, wz

    def _ensure_pixmap(self, w, h):
        if self._map_pixmap is None or self._map_pixmap.width() != w or self._map_pixmap.height() != h:
            self._map_pixmap = QPixmap(QSize(w, h))
        self._map_pixmap.fill(QColor(8, 8, 16))

    def _kick_render(self):
        if self._render_complete:
            return
        if self._render_timer.isActive():
            return
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return
        if self._engine is None:
            self.update()
            return
        self._snap_offset_x = self._offset_x
        self._snap_offset_z = self._offset_z
        self._snap_zoom = self._zoom
        self._snap_tile = max(1, 8 // self._zoom)
        self._ensure_pixmap(w, h)
        self._chunk_queue = []
        self._chunk_done.clear()
        tile_size = self._snap_tile
        min_wx, min_wz = self._screen_to_world(0, 0)
        max_wx, max_wz = self._screen_to_world(w, h)
        min_wx = int(min_wx) - tile_size
        max_wx = int(max_wx) + tile_size
        min_wz = int(min_wz) - tile_size
        max_wz = int(max_wz) + tile_size
        min_wx = (min_wx // tile_size) * tile_size
        min_wz = (min_wz // tile_size) * tile_size
        for wx in range(min_wx, max_wx + 1, tile_size):
            for wz in range(min_wz, max_wz + 1, tile_size):
                self._chunk_queue.append(("tile", wx, wz, tile_size))
        if self._chunk_queue:
            self._render_timer.start()

    def _snap_world_to_screen(self, wx, wz):
        sx = (wx - self._snap_offset_x) / self._snap_zoom + self.width() / 2.0
        sz = (wz - self._snap_offset_z) / self._snap_zoom + self.height() / 2.0
        return sx, sz

    def _tick_chunks(self):
        if not self._chunk_queue:
            self._render_timer.stop()
            self._render_complete = True
            self.update()
            return
        if self._map_pixmap is None:
            self._render_timer.stop()
            return
        painter = QPainter(self._map_pixmap)
        painter.setRenderHint(QPainter.Antialiasing, False)
        processed = 0
        while self._chunk_queue and processed < self.CHUNKS_PER_TICK:
            _, wx, wz, tile_size = self._chunk_queue.pop(0)
            sx, sz = self._snap_world_to_screen(wx, wz)
            nsx, nsz = self._snap_world_to_screen(wx + tile_size, wz + tile_size)
            draw_w = max(2, int(nsx - sx + 1))
            draw_h = max(2, int(nsz - sz + 1))
            try:
                if self._mode == "entity":
                    # 实体模式不显示地形，仅用虚空深底衬托实体标记
                    color = QColor(8, 8, 16)
                else:
                    name, rgb = self._engine.get_tile_mode(self._mode, wx, wz)
                    color = QColor(rgb[0], rgb[1], rgb[2])
            except Exception:
                color = QColor(20, 20, 28)
            painter.fillRect(int(sx), int(sz), draw_w, draw_h, color)
            processed += 1
        painter.end()
        self.update()
        if not self._chunk_queue:
            self._render_timer.stop()
            self._render_complete = True

    def paintGL(self):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w = self.width()
        h = self.height()
        p.fillRect(0, 0, w, h, QColor(8, 8, 16))

        if self._engine is None:
            p.setPen(QColor(80, 80, 96))
            p.setFont(QFont("Segoe UI", 11))
            p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, "No world loaded")
            p.end()
            return

        if not self._player_valid and self._map_pixmap is None:
            if not self._chunk_queue and not self._render_complete:
                p.end()
                self._kick_render()
                return

        if self._map_pixmap is not None:
            p.drawPixmap(0, 0, self._map_pixmap)

        if not self._player_valid:
            p.setPen(QColor(80, 80, 96))
            p.setFont(QFont("Segoe UI", 11))
            info = "Scanning memory..."
            if self._engine is not None and self._engine.scan_attempts > 0:
                info = "Scan #{}, {} regions, {:,.0f} doubles".format(
                    self._engine.scan_attempts,
                    self._engine.scan_region_count,
                    self._engine.scan_doubles)
                err = self._engine.scan_error
                if err:
                    info += "\n" + err
            p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, info)

        if self._chunk_queue:
            bar_w = int(w * 0.6)
            bar_h = 3
            bar_x = (w - bar_w) // 2
            bar_y = h - 8
            total = len(self._chunk_queue) + len(self._chunk_done)
            pct = len(self._chunk_done) / max(1, total)
            p.fillRect(bar_x, bar_y, bar_w, bar_h, QColor(30, 30, 40))
            p.fillRect(bar_x, bar_y, int(bar_w * pct), bar_h, QColor(60, 100, 160))
            p.setPen(QColor(120, 140, 180))
            p.setFont(QFont("Consolas", 8))
            p.drawText(bar_x, bar_y - 3, "loading {}/{} chunks".format(len(self._chunk_done), total))

        # 实体模式：玩家白色唯一(黑外圈+方向指针)；其余玩家/实体一律蓝色按高度定亮度。
        if self._engine is not None and self._mode == "entity":
            ent_size = max(4, min(12, 8 // self._zoom))
            for o in list(self._engine.players) + list(self._engine.entities):
                ex, ez = self._world_to_screen(o["x"], o["z"])
                if 0 <= ex <= w and 0 <= ez <= h:
                    bur = self._height_getter(o)
                    p.setPen(QPen(QColor(int(bur * 40), int(bur * 60), int(50 + 120 * bur)), 1))
                    p.setBrush(QColor(int(40 * bur), int(110 * bur), int(255 * bur), 220))
                    p.drawEllipse(QPointF(ex, ez), ent_size, ent_size)
        if self._player_valid and self._mode == "entity":
            px, pz = self._world_to_screen(self._player_x, self._player_z)
            size = max(8, min(24, 24 // self._zoom))
            half = size // 2
            p.setPen(QPen(QColor(0, 0, 0, 220), 3))
            p.setBrush(QColor(255, 255, 255))
            p.drawEllipse(QPointF(px, pz), half + 1, half + 1)
            p.setPen(QPen(QColor(0, 0, 0, 200), 1))
            p.drawLine(int(px), int(pz), int(px), int(pz - size - 4))
            p.drawLine(int(px), int(pz), int(px + size - 2), int(pz))
            p.setPen(QPen(QColor(0, 0, 0, 160), 1))
            p.setBrush(QColor(255, 255, 255, 150))
            p.drawEllipse(QPointF(px, pz), size, size)

        p.setPen(QPen(QColor(40, 40, 56, 160), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(0, 0, w - 1, h - 1)

        p.setFont(QFont("Consolas", 9))
        p.setPen(QColor(160, 160, 200))
        p.drawText(6, 14, "X: {:.1f}  Z: {:.1f}".format(self._player_x, self._player_z))
        zoom_str = "Zoom: {}   E:{}  P:{}".format(
            ZOOM_LABELS[self._zoom_level],
            len(self._engine.entities) if self._engine else 0,
            len(self._engine.players) if self._engine else 0)
        p.drawText(6, 30, zoom_str)
        if self._engine is not None:
            dbg = "D:{}M  R:{}".format(
                self._engine.scan_doubles // 1000000,
                self._engine.scan_region_count)
            p.drawText(6, 46, dbg)

        p.end()

        self._kick_render()


class ScanWorker(QThread):
    position_updated = pyqtSignal(float, float, bool)

    def __init__(self, engine, pid, parent=None):
        super().__init__(parent)
        self._engine = engine
        self._pid = pid
        self._active = True

    def run(self):
        while self._active:
            try:
                self._engine.scan_player_position(self._pid)
                self.position_updated.emit(
                    self._engine.player_x,
                    self._engine.player_z,
                    self._engine.player_valid,
                )
            except Exception:
                self.position_updated.emit(0.0, 0.0, False)
            if self._engine.player_valid:
                self.msleep(2000)
            else:
                self.msleep(150)

    def stop(self):
        self._active = False


class MinimapWidget(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._engine = None
        self._pid = None
        self._scan_worker = None
        self._pos_label = QLabel("X: ---  Z: ---")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        bar = QHBoxLayout()
        bar.setContentsMargins(4, 1, 4, 1)
        bar.setSpacing(2)

        self._mode_btns = []
        for mode, label in VIEW_MODES:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setToolTip("地形/空洞/矿石 视图可通过鼠标滚轮缩放")
            btn.setStyleSheet(MinimapCanvas.PAN_ZOOM_QSS)
            btn.clicked.connect(lambda checked, m=mode: self._set_mode(m))
            self._mode_btns.append(btn)
            bar.addWidget(btn)
        self._mode_btns[0].setChecked(True)

        bar.addStretch()
        self._pos_label.setStyleSheet("color: #707080; font-size: 10px;")
        bar.addWidget(self._pos_label)

        layout.addLayout(bar)

        self._canvas = MinimapCanvas()
        layout.addWidget(self._canvas, 1)

        self._entity_list = QListWidget()
        self._entity_list.setMaximumHeight(140)
        self._entity_list.setStyleSheet("""
            QListWidget { background: #0c0c14; color: #a0a0c0; border: 1px solid #202030;
                          font-family: Consolas; font-size: 10px; }
            QListWidget::item { padding: 1px 4px; }
        """)
        layout.addWidget(self._entity_list)

    def _ensure_scanner(self):
        if self._engine is not None and self._pid is not None:
            if self._scan_worker is None or not self._scan_worker.isRunning():
                if self._scan_worker is not None:
                    self._scan_worker.stop()
                    self._scan_worker.wait(500)
                self._scan_worker = ScanWorker(self._engine, self._pid, self)
                self._scan_worker.position_updated.connect(self._on_position_updated)
                self._scan_worker.start()

    def set_pid(self, pid):
        self._pid = pid
        if pid is not None:
            self._ensure_scanner()
        else:
            self._stop_scanner()

    def set_engine(self, engine):
        self._engine = engine
        self._canvas.set_engine(engine)
        self._ensure_scanner()

    def _set_mode(self, mode):
        for btn, (m, _label) in zip(self._mode_btns, VIEW_MODES):
            btn.setChecked(m == mode)
        self._canvas.set_mode(mode)

    def _on_position_updated(self, x, z, valid):
        self._canvas.update_position(x, z, valid)
        if valid:
            self._pos_label.setText("X: {:.0f}  Z: {:.0f}".format(x, z))
        else:
            self._pos_label.setText("X: ---  Z: ---")
        self._refresh_entity_list()

    def _refresh_entity_list(self):
        self._entity_list.clear()
        if self._engine is None:
            return
        players = self._engine.players
        entities = self._engine.entities
        px = int(self._engine.player_x) >> 4
        pz = int(self._engine.player_z) >> 4

        if players:
            header = QListWidgetItem("--- Players ({}) ---".format(len(players)))
            header.setFlags(Qt.NoItemFlags)
            header.setForeground(QColor(0, 200, 240))
            self._entity_list.addItem(header)
            for ent in players:
                ex = int(ent["x"]) >> 4
                ez = int(ent["z"]) >> 4
                dx = ex - px
                dz = ez - pz
                line = "  Chunk ({:+d},{:+d})  X={:.0f} Z={:.0f}  votes={}".format(
                    dx, dz, ent["x"], ent["z"], ent["count"])
                item = QListWidgetItem(line)
                item.setForeground(QColor(0, 180, 220))
                self._entity_list.addItem(item)

        if entities:
            header = QListWidgetItem("--- Entities ({}) ---".format(len(entities)))
            header.setFlags(Qt.NoItemFlags)
            header.setForeground(QColor(200, 60, 200))
            self._entity_list.addItem(header)
            for ent in entities:
                ex = int(ent["x"]) >> 4
                ez = int(ent["z"]) >> 4
                dx = ex - px
                dz = ez - pz
                line = "  Chunk ({:+d},{:+d})  X={:.0f} Z={:.0f}  votes={}".format(
                    dx, dz, ent["x"], ent["z"], ent["count"])
                item = QListWidgetItem(line)
                item.setForeground(QColor(180, 60, 180))
                self._entity_list.addItem(item)

        if not players and not entities:
            item = QListWidgetItem("  (no entities detected)")
            item.setForeground(QColor(60, 60, 80))
            item.setFlags(Qt.NoItemFlags)
            self._entity_list.addItem(item)

    def _stop_scanner(self):
        if self._scan_worker is not None:
            self._scan_worker.stop()
            self._scan_worker.wait(500)
            self._scan_worker = None

    def stop(self):
        self._stop_scanner()


class MinimapDialog(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("OpenMCL Minimap")
        self.setMinimumSize(480, 540)
        self.resize(520, 600)
        self.setStyleSheet("""
            QWidget { background: #0a0a10; }
        """)
        self._minimap = MinimapWidget()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._minimap)

    def set_world(self, world_dir):
        engine = MinimapEngine(world_dir)
        self._minimap.set_engine(engine)

    def set_pid(self, pid):
        self._minimap.set_pid(pid)

    def stop(self):
        self._minimap.stop()

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)