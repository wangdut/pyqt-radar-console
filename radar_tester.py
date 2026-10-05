# -*- coding: utf-8 -*-
"""
雷达目标测试台 (PyQt5) —— 独立于雷达控制台的专用测试工具
用途：选取本船真实降生位置(经纬度)、加载真实海岸线/岛屿、手动投放模拟目标。
通信：UDP 注入雷达(127.0.0.1:5678)，接收雷达本船动态回报(端口 5679)。
运行：先启动 radar_dashboard.py，再运行本程序。
操作：①搜索地名或点[点选降生点]后在海图点击，设定本船起点并加载真实地图
     ②[放置目标]模式下点击海图选位置，设航速航向后 [添加目标]
     ③鼠标滚轮缩放海图比例尺
地图数据 Natural Earth (public domain)，地名检索 © Photon/OpenStreetMap。
"""
import math
import sys
import time

from PyQt5.QtCore import Qt, QPointF, QRectF, QTimer
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt5.QtNetwork import QHostAddress, QUdpSocket
from PyQt5.QtWidgets import (QApplication, QDoubleSpinBox, QFrame, QGridLayout,
                             QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QPlainTextEdit, QPushButton,
                             QSpinBox, QVBoxLayout, QWidget)

import osm_client
import osm_geo

FONT_DIG = QFont("Consolas", 10)
FONT_SMALL = QFont("Consolas", 8)

PORT_TO_RADAR = 5678   # 注入目标
PORT_FROM_RADAR = 5679  # 本船动态回报
DEFAULT_SPAWN = (39.00, 118.05)  # 初始降生：渤海湾天津近海 (lat, lon)
SUGGEST_SCALE = 12.0   # 降生时建议给雷达的量程

QSS = """
QWidget {
    background-color: #0a0f14; color: #b8ccd7;
    font-family: "Consolas", "Microsoft YaHei", monospace; font-size: 13px;
}
QFrame#Panel { background-color: #0e151c; border: 1px solid #1c303a; border-radius: 8px; }
QLabel#Title { color: #ffc108; font-size: 18px; font-weight: bold; letter-spacing: 2px; }
QLabel#Section { color: #00bcd4; font-weight: bold; font-size: 12px; letter-spacing: 2px;
    border: none; background: transparent; }
QLabel#Hint { color: #4d7a8a; font-size: 11px; }
QLabel#OwnInfo { color: #9fd8e8; font-size: 12px; font-weight: bold; }
QPushButton { background-color: #12222e; border: 1px solid #2a4b5e; border-radius: 5px;
    padding: 6px 12px; color: #9fd8e8; font-weight: bold; }
QPushButton:hover { background-color: #1a3444; border-color: #00bcd4; }
QPushButton:checked { background-color: #004d5c; border-color: #00e5ff; color: #00e5ff; }
QPushButton#AddBtn { background-color: #004d5c; border-color: #00e5ff; color: #00e5ff; }
QPushButton#SpawnBtn { background-color: #3a2d00; border-color: #ffc108; color: #ffd54f; }
QPushButton#DangerBtn { border-color: #7c2d3a; color: #ff8899; }
QLineEdit, QDoubleSpinBox, QSpinBox { background-color: #12222e; border: 1px solid #2a4b5e;
    border-radius: 5px; padding: 4px 8px; color: #d7f3ff; }
QListWidget { background-color: #0a1219; border: 1px solid #1c303a; border-radius: 5px; padding: 2px; }
QListWidget::item { padding: 4px 6px; border-bottom: 1px solid #132029; }
QListWidget::item:selected { background-color: #003541; color: #00e5ff; }
QPlainTextEdit { background-color: #070d12; border: 1px solid #1c303a; border-radius: 5px;
    color: #7fa8b8; font-size: 11px; }
QScrollBar:vertical { background: #0a0f14; width: 8px; }
QScrollBar::handle:vertical { background: #1c303a; border-radius: 4px; min-height: 20px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
"""


class WorldChart(QWidget):
    """真实海图：渲染海岸线/岛屿/地名（Natural Earth），滚轮缩放，点击选投或降生。"""

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner
        self.pick = None
        self.setMinimumSize(430, 430)

    def _geom(self):
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        R = min(w, h) / 2.0 - 14.0
        span = self.owner.chart_span
        return cx, cy, R, R / span

    def world_to_screen(self, x, y):
        cx, cy, R, k = self._geom()
        return QPointF(cx + (x - self.owner.own_x) * k,
                       cy - (y - self.owner.own_y) * k)

    def screen_to_world(self, sx, sy):
        cx, cy, R, k = self._geom()
        return (self.owner.own_x + (sx - cx) / k,
                self.owner.own_y - (sy - cy) / k)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy, R, k = self._geom()
        w, h = self.width(), self.height()
        span = self.owner.chart_span
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#08161f"))
        p.drawRoundedRect(QRectF(0, 0, w, h), 8, 8)
        p.setClipRect(QRectF(0, 0, w, h))
        # 网格（自适应步长）
        step = self._grid_step(span)
        p.setFont(FONT_SMALL)
        gx0 = math.floor((self.owner.own_x - span) / step) * step
        gy0 = math.floor((self.owner.own_y - span) / step) * step
        gx = gx0
        while gx <= self.owner.own_x + span:
            a = self.world_to_screen(gx, self.owner.own_y)
            p.setPen(QPen(QColor(40, 80, 100, 70), 1))
            p.drawLine(QPointF(a.x(), 0), QPointF(a.x(), h))
            p.setPen(QPen(QColor(90, 140, 160, 120), 1))
            p.drawText(QRectF(a.x() + 2, 2, 70, 12), Qt.AlignLeft, "E%d" % gx)
            gx += step
        gy = gy0
        while gy <= self.owner.own_y + span:
            a = self.world_to_screen(self.owner.own_x, gy)
            p.setPen(QPen(QColor(40, 80, 100, 70), 1))
            p.drawLine(QPointF(0, a.y()), QPointF(w, a.y()))
            p.setPen(QPen(QColor(90, 140, 160, 120), 1))
            p.drawText(QRectF(2, a.y() - 12, 70, 12), Qt.AlignLeft, "N%d" % gy)
            gy += step
        # 真实地图
        self._draw_map(p, w, h)
        # 视图比例尺圈
        p.setPen(QPen(QColor(0, 229, 255, 45), 1, Qt.DotLine))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), R, R)
        # 降生锚点 (0,0)
        if self.owner.geo is not None:
            a = self.world_to_screen(0.0, 0.0)
            p.setPen(QPen(QColor(255, 213, 79, 200), 1.4))
            p.drawLine(QPointF(a.x() - 6, a.y()), QPointF(a.x() + 6, a.y()))
            p.drawLine(QPointF(a.x(), a.y() - 6), QPointF(a.x(), a.y() + 6))
            p.drawEllipse(a, 4, 4)
        # 目标
        for name, (x, y, spd, hdg) in self.owner.targets.items():
            pt = self.world_to_screen(x, y)
            if not (-20 < pt.x() < w + 20 and -20 < pt.y() < h + 20):
                continue
            lw = getattr(self.owner, "listw", None)
            sel_item = (lw is not None and lw.currentItem() is not None and
                        lw.currentItem().text().startswith(name))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 193, 8, 235) if sel_item else QColor(120, 255, 190, 220))
            p.drawEllipse(pt, 4.5, 4.5)
            v = math.radians(hdg)
            p.setPen(QPen(QColor(160, 255, 210, 200), 1.4))
            p.drawLine(pt, QPointF(pt.x() + math.sin(v) * 14, pt.y() - math.cos(v) * 14))
            p.setPen(QPen(QColor(210, 240, 255, 230), 1))
            p.setFont(FONT_DIG)
            p.drawText(QRectF(pt.x() + 8, pt.y() - 20, 120, 14), Qt.AlignLeft, name)
        # 选点十字
        if self.pick:
            pt = self.world_to_screen(*self.pick)
            p.setPen(QPen(QColor(255, 193, 8, 210), 1.2, Qt.DashLine))
            p.drawLine(QPointF(pt.x() - 10, pt.y()), QPointF(pt.x() + 10, pt.y()))
            p.drawLine(QPointF(pt.x(), pt.y() - 10), QPointF(pt.x(), pt.y() + 10))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(pt, 7, 7)
        # 本船
        p.save()
        p.translate(cx, cy)
        p.rotate(self.owner.own_hdg)
        p.setPen(QPen(QColor(0, 229, 255, 230), 1.4))
        p.setBrush(QColor(0, 90, 110, 220))
        p.drawPolygon([QPointF(0, -11), QPointF(6, 9), QPointF(0, 4), QPointF(-6, 9)])
        p.restore()
        p.setPen(QPen(QColor(0, 229, 255, 230), 1))
        p.setFont(FONT_DIG)
        p.drawText(QRectF(cx + 11, cy - 20, 120, 16), Qt.AlignLeft, "本船")
        # 状态角标
        p.setPen(QPen(QColor(0, 229, 255, 170), 1))
        p.setFont(FONT_DIG)
        p.drawText(10, 20, "视图半径 %.0f 海里   滚轮缩放" % span)
        p.end()

    def _grid_step(self, span):
        for s in (1, 2, 5, 10, 20, 50):
            if span / s <= 8:
                return s
        return 100

    def _draw_map(self, p, w, h):
        sh = self.owner.shapes
        if not sh:
            return
        land_fill = QColor(41, 71, 50, 235)
        land_edge = QColor(140, 224, 168, 220)
        for poly in sh["land"]:
            path = QPainterPath()
            started = False
            for (x, y) in poly:
                pt = self.world_to_screen(x, y)
                if not started:
                    path.moveTo(pt)
                    started = True
                else:
                    path.lineTo(pt)
            if started:
                p.setPen(QPen(land_edge, 1.0))
                p.setBrush(land_fill)
                p.drawPath(path)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(160, 240, 200, 200), 1.4))
        for line in sh["coast"]:
            path = QPainterPath()
            started = False
            for (x, y) in line:
                pt = self.world_to_screen(x, y)
                if not started:
                    path.moveTo(pt)
                    started = True
                else:
                    path.lineTo(pt)
            if started:
                p.drawPath(path)
        p.setFont(FONT_SMALL)
        p.setPen(QPen(QColor(210, 235, 220, 220), 1))
        for (name, x, y) in sh["places"]:
            if not name:
                continue
            pt = self.world_to_screen(x, y)
            if 0 < pt.x() < w and 0 < pt.y() < h:
                p.drawText(QRectF(pt.x() + 4, pt.y() - 8, 140, 12), Qt.AlignLeft, name)

    def wheelEvent(self, ev):
        d = ev.angleDelta().y()
        if d == 0:
            return
        factor = 1.2 if d < 0 else (1.0 / 1.2)
        self.owner.set_chart_span(self.owner.chart_span * factor)
        ev.accept()

    def mousePressEvent(self, ev):
        wx, wy = self.screen_to_world(ev.x(), ev.y())
        if self.owner.mode == "spawn" and self.owner.geo is not None:
            lon, lat = self.owner.geo.to_ll(wx, wy)
            self.owner.spawn_at(lat, lon)
            return
        self.pick = (wx, wy)
        self.owner.on_pick(wx, wy)
        self.update()


class Tester(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("雷达目标测试台 —— 真实海图 · 降生 · 目标注入")
        self.resize(1080, 680)
        # 本船/地图状态
        self.own_x = self.own_y = 0.0
        self.own_hdg = self.own_spd = 0.0
        self.last_own_rx = 0.0
        self.geo = None
        self.shapes = None
        self.map_thread = None
        self.search_thread = None
        self.chart_span = 16.0
        self.mode = "target"
        self.anchor = None          # (lon, lat)
        self.map_cov = 0.0
        self._spawn_acc = 0.0
        self.targets = {}
        self._seq = 0
        # 网络
        self.tx = QUdpSocket(self)
        self.rx = QUdpSocket(self)
        self.rx_ok = self.rx.bind(QHostAddress.Any, PORT_FROM_RADAR)
        self.rx.readyRead.connect(self._read_own)
        # 界面
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(10)
        root.addWidget(self._build_header())
        body = QHBoxLayout()
        body.setSpacing(10)
        root.addLayout(body, 1)
        self.chart = WorldChart(self)
        body.addWidget(self.chart, 1)
        body.addWidget(self._build_side(), 0)
        # 定时器
        t = QTimer(self)
        t.timeout.connect(self._tick)
        t.start(200)
        self._log("info", "测试台启动。目标注入端口 %d，本船回报监听端口 %d"
                  % (PORT_TO_RADAR, PORT_FROM_RADAR))
        if not self.rx_ok:
            self._log("warn", "端口 %d 被占用（旧测试台未关），无法收到本船动态"
                      % PORT_FROM_RADAR)
        self.spawn_at(*DEFAULT_SPAWN)
        self._log("info", "① 搜索地名或[点选降生点]设定起点 ②[放置目标]模式点击海图投放 ③滚轮缩放")

    # ---------- 界面 ----------
    def _build_header(self):
        bar = QFrame()
        bar.setObjectName("Panel")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 6, 14, 6)
        t = QLabel("◈ 雷达目标测试台")
        t.setObjectName("Title")
        self.lbl_link = QLabel()
        self.lbl_link.setStyleSheet("color:#ff5252; font-weight:bold; background:transparent;")
        self.lbl_own = QLabel()
        self.lbl_own.setObjectName("OwnInfo")
        lay.addWidget(t)
        lay.addSpacing(16)
        lay.addWidget(self.lbl_own)
        lay.addStretch(1)
        lay.addWidget(self.lbl_link)
        return bar

    def _build_side(self):
        side = QWidget()
        side.setFixedWidth(340)
        col = QVBoxLayout(side)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)
        col.addWidget(self._build_spawn_panel())
        col.addWidget(self._build_target_panel())
        col.addWidget(self._build_list_panel(), 1)
        col.addWidget(self._build_log_panel())
        return side

    def _build_spawn_panel(self):
        panel = QFrame()
        panel.setObjectName("Panel")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(12, 10, 12, 10)
        title = QLabel("▍本船降生（经纬度 · 真实地图）")
        title.setObjectName("Section")
        pl.addWidget(title)
        # 地名检索
        row = QHBoxLayout()
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("输入地名，如：天津港 / 青岛 / 海峡")
        self.ed_search.returnPressed.connect(self.search_place)
        btn_s = QPushButton("检索")
        btn_s.clicked.connect(self.search_place)
        row.addWidget(self.ed_search)
        row.addWidget(btn_s)
        pl.addLayout(row)
        self.list_search = QListWidget()
        self.list_search.setFixedHeight(72)
        self.list_search.itemDoubleClicked.connect(self._on_search_pick)
        pl.addWidget(self.list_search)
        # 手动经纬度
        g = QGridLayout()
        g.setHorizontalSpacing(6)
        kl1 = QLabel("纬度")
        kl2 = QLabel("经度")
        for kl in (kl1, kl2):
            kl.setStyleSheet("color:#5b7c8d; background:transparent;")
        self.spn_lat = QDoubleSpinBox()
        self.spn_lat.setRange(-89.9, 89.9)
        self.spn_lat.setDecimals(4)
        self.spn_lat.setValue(DEFAULT_SPAWN[0])
        self.spn_lat.setSuffix(" °N")
        self.spn_lon = QDoubleSpinBox()
        self.spn_lon.setRange(-179.9, 179.9)
        self.spn_lon.setDecimals(4)
        self.spn_lon.setValue(DEFAULT_SPAWN[1])
        self.spn_lon.setSuffix(" °E")
        g.addWidget(kl1, 0, 0); g.addWidget(self.spn_lat, 0, 1)
        g.addWidget(kl2, 1, 0); g.addWidget(self.spn_lon, 1, 1)
        pl.addLayout(g)
        row2 = QHBoxLayout()
        self.btn_spawn_coord = QPushButton("⚑ 降生到坐标")
        self.btn_spawn_coord.setObjectName("SpawnBtn")
        self.btn_spawn_coord.clicked.connect(self._spawn_from_spins)
        self.btn_mode = QPushButton("🖱 点选降生点")
        self.btn_mode.setCheckable(True)
        self.btn_mode.clicked.connect(self._toggle_mode)
        row2.addWidget(self.btn_spawn_coord)
        row2.addWidget(self.btn_mode)
        pl.addLayout(row2)
        self.lbl_map = QLabel("地图：加载中…")
        self.lbl_map.setObjectName("Hint")
        pl.addWidget(self.lbl_map)
        btn_reload = QPushButton("↻ 重新加载地图")
        btn_reload.clicked.connect(self.reload_map)
        pl.addWidget(btn_reload)
        return panel

    def _build_target_panel(self):
        panel = QFrame()
        panel.setObjectName("Panel")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(12, 10, 12, 10)
        title = QLabel("▍目标参数（点击海图选位置）")
        title.setObjectName("Section")
        pl.addWidget(title)
        grid = QGridLayout()
        grid.setVerticalSpacing(6)
        grid.setHorizontalSpacing(8)
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("留空自动生成")
        self.spn_x = QDoubleSpinBox()
        self.spn_y = QDoubleSpinBox()
        for s in (self.spn_x, self.spn_y):
            s.setRange(-500, 500)
            s.setDecimals(2)
            s.setSingleStep(0.5)
        self.spn_x.setSuffix(" 海里")
        self.spn_y.setSuffix(" 海里")
        self.spn_spd = QDoubleSpinBox()
        self.spn_spd.setRange(0, 40)
        self.spn_spd.setDecimals(1)
        self.spn_spd.setValue(10)
        self.spn_spd.setSuffix(" 节")
        self.spn_hdg = QSpinBox()
        self.spn_hdg.setRange(0, 359)
        self.spn_hdg.setSuffix("°")
        rows = [("名称", self.ed_name), ("位置·东 X", self.spn_x),
                ("位置·北 Y", self.spn_y), ("航速", self.spn_spd),
                ("航向", self.spn_hdg)]
        for i, (key, wgt) in enumerate(rows):
            kl = QLabel(key)
            kl.setStyleSheet("color:#5b7c8d; background:transparent;")
            grid.addWidget(kl, i, 0)
            grid.addWidget(wgt, i, 1)
        pl.addLayout(grid)
        btn_add = QPushButton("＋ 添加目标（发送到雷达）")
        btn_add.setObjectName("AddBtn")
        btn_add.clicked.connect(self.add_target)
        pl.addWidget(btn_add)
        row = QHBoxLayout()
        btn_del = QPushButton("删除选中")
        btn_del.setObjectName("DangerBtn")
        btn_del.clicked.connect(self.del_selected)
        btn_clr = QPushButton("清空全部")
        btn_clr.setObjectName("DangerBtn")
        btn_clr.clicked.connect(self.clr_all)
        row.addWidget(btn_del)
        row.addWidget(btn_clr)
        pl.addLayout(row)
        return panel

    def _build_list_panel(self):
        panel = QFrame()
        panel.setObjectName("Panel")
        p2 = QVBoxLayout(panel)
        p2.setContentsMargins(12, 10, 12, 10)
        t2 = QLabel("▍已投放目标（选中高亮，[删除选中]移除）")
        t2.setObjectName("Section")
        t2.setWordWrap(True)
        p2.addWidget(t2)
        self.listw = QListWidget()
        p2.addWidget(self.listw, 1)
        return panel

    def _build_log_panel(self):
        panel = QFrame()
        panel.setObjectName("Panel")
        p3 = QVBoxLayout(panel)
        p3.setContentsMargins(12, 10, 12, 10)
        t3 = QLabel("▍操作日志")
        t3.setObjectName("Section")
        p3.addWidget(t3)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(200)
        self.log.setFixedHeight(96)
        p3.addWidget(self.log)
        return panel

    # ---------- 降生 / 地图 ----------
    def spawn_at(self, lat, lon):
        lat = max(-89.9, min(89.9, lat))
        lon = max(-179.9, min(179.9, lon))
        self.anchor = (lon, lat)
        self.geo = osm_geo.GeoRegion(lon, lat)
        self.own_x = self.own_y = self.own_hdg = self.own_spd = 0.0
        self.last_own_rx = 0.0
        self.targets.clear()
        self._refresh_list()
        self.chart.pick = None
        self.list_search.clear()
        self.spn_lat.setValue(lat)
        self.spn_lon.setValue(lon)
        self.reload_map()
        self._send("SPAWN,%.6f,%.6f,%.1f" % (lat, lon, SUGGEST_SCALE))
        self._spawn_acc = 0.0
        self._log("info", "本船降生 %0.4f%s %0.4f%s（海域已切换，旧目标已清除）" % (
            abs(lat), "N" if lat >= 0 else "S", abs(lon), "E" if lon >= 0 else "W"))
        self.chart.update()

    def _spawn_from_spins(self):
        self.spawn_at(self.spn_lat.value(), self.spn_lon.value())

    def _toggle_mode(self):
        self.mode = "spawn" if self.btn_mode.isChecked() else "target"
        self.btn_mode.setText("🖱 点击海图降生…" if self.mode == "spawn" else "🖱 点选降生点")
        if self.mode != "spawn":
            self.btn_mode.setChecked(False)
        self._log("info", "海图点击模式：%s" % ("选取降生点" if self.mode == "spawn" else "放置目标"))

    def reload_map(self):
        if not self.geo:
            return
        half = max(self.chart_span * 1.6, 14.0)
        self.map_cov = half
        bbox = self.geo.bbox_of(0.0, 0.0, half)
        if self.map_thread is not None and self.map_thread.isRunning():
            try:
                self.map_thread.done.disconnect()
            except TypeError:
                pass
            self.map_thread.wait(300)
        self.map_thread = osm_client.MapThread(bbox, self)
        self.map_thread.done.connect(self._on_map)
        self.map_thread.start()
        self.lbl_map.setText("地图：加载中…（半径 %.0f 海里）" % half)

    def _on_map(self, bbox, geo):
        self.shapes = osm_client.to_world_shapes(geo, self.geo) if geo else None
        if self.shapes is None:
            self.lbl_map.setText("地图：获取失败（网络/超时），可点[重新加载地图]")
            self._log("warn", "地图数据获取失败（网络/超时）")
        else:
            self.lbl_map.setText("地图：已加载  海岸/陆地 %d段 · 地名 %d处 © Natural Earth"
                                 % (len(self.shapes["coast"]) + len(self.shapes["land"]),
                                    len(self.shapes["places"])))
            self._log("ok", "真实地图就绪（海岸/陆地 %d 段，地名 %d）"
                      % (len(self.shapes["coast"]) + len(self.shapes["land"]),
                         len(self.shapes["places"])))
        self.chart.update()

    def set_chart_span(self, span):
        self.chart_span = max(2.0, min(60.0, span))
        if self.chart_span > self.map_cov * 0.7:
            self.reload_map()
        self.chart.update()

    # ---------- 地名检索 ----------
    def search_place(self):
        q = self.ed_search.text().strip()
        if not q:
            return
        self._log("info", "检索地名：%s" % q)
        if self.search_thread is not None and self.search_thread.isRunning():
            self.search_thread.wait(300)
        self.search_thread = osm_client.SearchThread(q, self)
        self.search_thread.done.connect(self._on_search)
        self.search_thread.start()

    def _on_search(self, q, results):
        self.list_search.clear()
        if not results:
            self._log("warn", "未检索到地名：%s（可直接用经纬度框降生）" % q)
            return
        for lat, lon, name in results:
            it = QListWidgetItem("%0.4f, %0.4f  %s" % (lat, lon, name))
            it.setData(Qt.UserRole, (lat, lon))
            self.list_search.addItem(it)
        self._log("info", "检索到 %d 个结果，双击任一项在此降生" % len(results))

    def _on_search_pick(self, item):
        lat, lon = item.data(Qt.UserRole)
        self.spawn_at(lat, lon)

    # ---------- 目标 ----------
    def on_pick(self, x, y):
        self.spn_x.setValue(x)
        self.spn_y.setValue(y)

    def add_target(self):
        name = self.ed_name.text().strip()
        if not name:
            self._seq += 1
            name = "SIM-%02d" % self._seq
        x = self.spn_x.value()
        y = self.spn_y.value()
        spd = self.spn_spd.value()
        hdg = self.spn_hdg.value()
        self.targets[name] = (x, y, spd, hdg)
        self._refresh_list()
        self._send("ADD,%s,%.2f,%.2f,%.1f,%d" % (name, x, y, spd, hdg))
        self._log("info", "已注入 %s  东%.1f 北%.1f 海里  %03d°/%.1f节"
                  % (name, x, y, hdg, spd))
        self.ed_name.clear()

    def del_selected(self):
        it = self.listw.currentItem()
        if it is None:
            self._log("warn", "请先在列表中选中目标")
            return
        name = it.data(Qt.UserRole)
        self.targets.pop(name, None)
        self._refresh_list()
        self._send("DEL,%s" % name)
        self._log("info", "已删除目标 %s" % name)

    def clr_all(self):
        self.targets.clear()
        self._refresh_list()
        self._send("CLR")
        self._log("info", "已清空全部目标")

    def _refresh_list(self):
        cur = self.listw.currentItem().data(Qt.UserRole) if \
            self.listw.currentItem() else None
        self.listw.clear()
        for name, (x, y, spd, hdg) in self.targets.items():
            it = QListWidgetItem("%s   东%+.1f 北%+.1f   %03d°/%.1f节"
                                 % (name, x, y, hdg, spd))
            it.setFont(FONT_DIG)
            it.setData(Qt.UserRole, name)
            if name == cur:
                it.setSelected(True)
            self.listw.addItem(it)
        self.chart.update()

    def _send(self, payload):
        self.tx.writeDatagram((payload + "\n").encode("utf-8"),
                              QHostAddress.LocalHost, PORT_TO_RADAR)

    _LOG_STYLE = {"info": "color:#7fa8b8;", "warn": "color:#ffc108;",
                  "ok": "color:#4ddb77;"}

    def _log(self, level, msg):
        ts = time.strftime("%H:%M:%S")
        self.log.appendHtml('<span style="%s">[%s] %s</span>'
                            % (self._LOG_STYLE.get(level, ""), ts, msg))

    # ---------- 网络/周期 ----------
    def _read_own(self):
        while self.rx.hasPendingDatagrams():
            data = self.rx.readDatagram(4096)[0]
            text = bytes(data).decode("utf-8", "ignore")
            for line in text.splitlines():
                parts = line.strip().split(",")
                if len(parts) >= 5 and parts[0] == "OWN":
                    try:
                        self.own_x = float(parts[1])
                        self.own_y = float(parts[2])
                        self.own_hdg = float(parts[3])
                        self.own_spd = float(parts[4])
                        self.last_own_rx = time.time()
                    except ValueError:
                        pass

    def _tick(self):
        online = time.time() - self.last_own_rx < 2.0
        if online:
            self.lbl_link.setText("● 雷达已连接")
            self.lbl_link.setStyleSheet(
                "color:#4ddb77; font-weight:bold; background:transparent;")
        else:
            self.lbl_link.setText("○ 雷达未连接（请先启动雷达控制台）")
            self.lbl_link.setStyleSheet(
                "color:#ff5252; font-weight:bold; background:transparent;")
        # 本船经纬度（由锚点+世界坐标反算）
        if self.geo is not None:
            lon, lat = self.geo.to_ll(self.own_x, self.own_y)
            self.lbl_own.setText("本船 %0.4f%s %0.4f%s｜航向%03d°｜航速%.0f节" % (
                abs(lat), "N" if lat >= 0 else "S",
                abs(lon), "E" if lon >= 0 else "W",
                self.own_hdg, self.own_spd))
        # 持续同步降生点（雷达若晚启动会收到并加载地图）
        self._spawn_acc += 0.2
        if self._spawn_acc >= 2.5 and self.anchor:
            self._spawn_acc = 0.0
            self._send("SPAWN,%.6f,%.6f,%.1f" % (
                self.anchor[1], self.anchor[0], SUGGEST_SCALE))
        self.chart.update()


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    win = Tester()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
