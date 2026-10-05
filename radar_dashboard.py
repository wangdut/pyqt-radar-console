# -*- coding: utf-8 -*-
"""
RN-9000 航海雷达控制台 (PyQt5)
职责单一：雷达显示 / 扫描 / 目标运算 / 操船。目标由《雷达目标测试台》(radar_tester.py)
通过 UDP 注入（端口 5678），雷达向测试台回报本船动态（端口 5679）。

键盘操船：↑/W 加速  ↓/S 减速  A 左舵  D 右舵（窗口需处于激活状态）
运行：python radar_dashboard.py
"""
import math
import random
import sys
import time

from PyQt5.QtCore import Qt, QTimer, QPointF, QRectF, pyqtSignal
from PyQt5.QtGui import (QColor, QFont, QPainter, QPainterPath, QPen,
                         QRadialGradient)
from PyQt5.QtNetwork import QHostAddress, QUdpSocket
from PyQt5.QtWidgets import (QApplication, QComboBox, QDoubleSpinBox, QFrame,
                             QGridLayout, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QPlainTextEdit, QPushButton,
                             QSizePolicy, QSlider, QSplitter, QVBoxLayout,
                             QWidget)

import nr_data
import osm_client
import osm_geo

FONT_DIG = QFont("Consolas", 10)
FONT_SMALL = QFont("Consolas", 8)

PORT_RX = 5678   # 接收测试台注入的目标
PORT_TX = 5679   # 向测试台回报本船动态

GLOBAL_QSS = """
QWidget {
    background-color: #0a0f14;
    color: #b8ccd7;
    font-family: "Consolas", "Microsoft YaHei", monospace;
    font-size: 13px;
}
QFrame#Panel {
    background-color: #0e151c;
    border: 1px solid #1c303a;
    border-radius: 8px;
}
QFrame#ScopeFrame {
    background-color: #060b0f;
    border: 1px solid #21424f;
    border-radius: 10px;
}
QLabel#Title {
    color: #00e5ff;
    font-size: 19px;
    font-weight: bold;
    letter-spacing: 3px;
}
QLabel#SubTitle { color: #4d7a8a; font-size: 11px; }
QLabel#Section {
    color: #00bcd4; font-weight: bold; font-size: 12px;
    letter-spacing: 2px; border: none; background: transparent;
}
QLabel#CardKey { color: #5b7c8d; font-size: 11px; background: transparent; }
QLabel#CardVal { color: #d7f3ff; font-size: 15px; font-weight: bold; background: transparent; }
QLabel#CardValHi { color: #ff5252; font-size: 15px; font-weight: bold; background: transparent; }
QLabel#CardValMid { color: #ffc108; font-size: 15px; font-weight: bold; background: transparent; }
QLabel#CardValSafe { color: #4ddb77; font-size: 15px; font-weight: bold; background: transparent; }
QPushButton {
    background-color: #12222e;
    border: 1px solid #2a4b5e;
    border-radius: 5px;
    padding: 6px 12px;
    color: #9fd8e8;
    font-weight: bold;
}
QPushButton:hover { background-color: #1a3444; border-color: #00bcd4; }
QPushButton:pressed { background-color: #0b3947; }
QComboBox, QDoubleSpinBox {
    background-color: #12222e; border: 1px solid #2a4b5e;
    border-radius: 5px; padding: 3px 8px; color: #9fd8e8;
}
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background-color: #0e151c; color: #b8ccd7;
    selection-background-color: #004d5c; border: 1px solid #2a4b5e;
}
QSlider::groove:horizontal {
    height: 4px; background: #1c303a; border-radius: 2px;
}
QSlider::sub-page:horizontal { background: #00838f; border-radius: 2px; }
QSlider::handle:horizontal {
    width: 12px; height: 12px; margin: -5px 0;
    background: #00e5ff; border-radius: 6px;
}
QListWidget {
    background-color: #0a1219; border: 1px solid #1c303a;
    border-radius: 5px; padding: 2px;
}
QListWidget::item { padding: 4px 6px; border-bottom: 1px solid #132029; }
QListWidget::item:selected { background-color: #003541; color: #00e5ff; }
QListWidget::item:hover { background-color: #10202b; }
QSplitter::handle {
    background-color: #12222e;
    border: 1px solid #1c303a;
    border-radius: 3px;
    width: 7px;
}
QSplitter::handle:hover { background-color: #00838f; }
QPlainTextEdit {
    background-color: #070d12; border: 1px solid #1c303a;
    border-radius: 5px; color: #7fa8b8; font-size: 11px;
}
QScrollBar:vertical { background: #0a0f14; width: 8px; }
QScrollBar::handle:vertical { background: #1c303a; border-radius: 4px; min-height: 20px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
"""


def norm360(a):
    return a % 360.0


def polar_to_screen(cx, cy, bearing_deg, r_pix):
    b = math.radians(bearing_deg)
    return QPointF(cx + math.sin(b) * r_pix, cy - math.cos(b) * r_pix)


class Contact:
    """运动目标：世界坐标 (x=东, y=北)，单位海里；速度单位节。"""

    def __init__(self, name, x, y, spd, hdg):
        self.name = name
        self.x = x
        self.y = y
        v = math.radians(hdg)
        self.speed = spd
        self.heading = hdg
        self.vx = math.sin(v) * spd
        self.vy = math.cos(v) * spd
        self.last_pass = -10.0          # 上次被扫描线照到的时间
        self.illum = 0.0                # 显示亮度(衰减)
        self.alarm_active = False       # 警戒圈报警状态

    def set_motion(self, x, y, spd, hdg):
        self.x, self.y = x, y
        self.speed, self.heading = spd, hdg
        v = math.radians(hdg)
        self.vx, self.vy = math.sin(v) * spd, math.cos(v) * spd

    def update(self, dt):
        # 匀速直线运动（目标机动由测试台重新注入决定）
        self.x += self.vx * dt / 3600.0
        self.y += self.vy * dt / 3600.0


class RadarScope(QWidget):
    """雷达显示核心：扫描、余辉、目标、报警、UDP 目标接收、键盘操船。"""
    target_selected = pyqtSignal(object)
    alarm_event = pyqtSignal(str, str)   # 级别, 消息
    range_synced = pyqtSignal(float)     # 降生时同步量程到控制条

    TRAIL_DEG = 75.0
    TRAIL_SEG = 46

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 420)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # 可配置参数
        self.scale_nm = 8.0        # 量程(半径，海里)
        self.rpm = 30.0            # 天线转速(转/分)
        self.gain = 1.0            # 增益/亮度
        self.guard_nm = 1.5        # 警戒圈半径
        self.heading_up = False    # 显示模式
        self.running = True
        # 本船状态（世界坐标）
        self.own = [0.0, 0.0]
        self.own_hdg = 0.0
        self.own_spd = 0.0
        # 运行状态
        self.contacts = []
        self.selected = None
        self.sweep = 0.0
        self.keys = set()              # 由主窗口注入按住的键
        self.sim_online = False
        self._last_rx = 0.0
        self._last_t = time.time()
        self._noise = []
        self._own_acc = 0.0
        # 真实地图（Natural Earth，可回退 OSM）
        self.region = None            # GeoRegion（降生点为原点）
        self.shapes = None            # 世界坐标地理图形
        self.map_thread = None
        self.spawn_ll = None          # (lat, lon)
        self.map_loaded = False
        self.map_cov = 0.0            # 已加载地图数据的矩形半宽（海里）
        self.map_cx = 0.0             # 已加载矩形的中心（世界坐标）
        self.map_cy = 0.0
        self._map_bbox = None         # 最近一次取数请求的 bbox（用于丢弃过期回报）
        self._land_cache = None       # (key, 裁剪后陆地环) 渲染缓存
        # UDP：接收目标注入 / 回报本船动态
        self.rx = QUdpSocket(self)
        self.bind_ok = self.rx.bind(QHostAddress.Any, PORT_RX)
        if not self.bind_ok:
            # 延迟到信号连接好、事件循环启动后再告警
            QTimer.singleShot(
                200, lambda: self.alarm_event.emit(
                    "danger", "监听失败！端口 %d 被占用——可能有另一个雷达实例在运行，"
                    "请关闭旧窗口后重新启动" % PORT_RX))
        self.rx.readyRead.connect(self._read_net)
        self.tx = QUdpSocket(self)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)

    # ---------------- 网络协议 ----------------
    def _read_net(self):
        while self.rx.hasPendingDatagrams():
            data = self.rx.readDatagram(4096)[0]
            text = bytes(data).decode("utf-8", "ignore")
            for line in text.splitlines():
                self._handle_cmd(line.strip())

    def _handle_cmd(self, line):
        parts = line.split(",")
        if not parts or not parts[0]:
            return
        cmd = parts[0]
        self._last_rx = time.time()
        self.sim_online = True
        try:
            if cmd == "ADD" and len(parts) >= 6:
                name = parts[1].strip()
                x, y, spd, hdg = (float(parts[2]), float(parts[3]),
                                  float(parts[4]), norm360(float(parts[5])))
                for c in self.contacts:
                    if c.name == name:
                        c.set_motion(x, y, spd, hdg)
                        break
                else:
                    self.contacts.append(Contact(name, x, y, spd, hdg))
                self.alarm_event.emit(
                    "info", "注入目标 %s  东%+.1f 北%+.1f海里  %03d°/%.0f节"
                    % (name, x, y, hdg, spd))
            elif cmd == "DEL" and len(parts) >= 2:
                name = parts[1].strip()
                self.contacts = [c for c in self.contacts if c.name != name]
                if self.selected is not None and self.selected.name == name:
                    self.selected = None
                    self.target_selected.emit(None)
                self.alarm_event.emit("info", "目标 %s 已移除" % name)
            elif cmd == "CLR":
                self.contacts = []
                self.selected = None
                self.target_selected.emit(None)
                self.alarm_event.emit("info", "已全部清除目标")
            elif cmd == "SPAWN" and len(parts) >= 3:
                lat = float(parts[1])
                lon = float(parts[2])
                scale = float(parts[3]) if len(parts) >= 4 else self.scale_nm
                self.spawn(lat, lon, scale)
            elif cmd == "PING":
                self._send_own()
        except ValueError:
            self.alarm_event.emit("warn", "无法解析指令: %s" % line)

    def _send_own(self):
        lat = lon = 0.0
        if self.region is not None:
            lon, lat = self.region.to_ll(self.own[0], self.own[1])
        msg = "OWN,%.3f,%.3f,%.1f,%.1f,%.6f,%.6f\n" % (
            self.own[0], self.own[1], self.own_hdg, self.own_spd, lat, lon)
        self.tx.writeDatagram(msg.encode("utf-8"),
                              QHostAddress.LocalHost, PORT_TX)

    # ---------------- 降生（选择经纬度起点 + 加载真实地图）----------------
    def spawn(self, lat, lon, scale_nm=None):
        # 幂等：测试台会周期性重发同锚点 SPAWN（用于雷达晚启动同步），
        # 同点重复降生直接忽略，避免清空目标/重置本船
        if (self.spawn_ll is not None and self.region is not None and
                abs(self.spawn_ll[0] - lat) < 1e-4 and
                abs(self.spawn_ll[1] - lon) < 1e-4):
            return
        self.spawn_ll = (lat, lon)
        self.region = osm_geo.GeoRegion(lon, lat)
        self.shapes = None
        self.map_loaded = False
        # 降生重置：本船回到世界原点(0,0)，海域切换后清空旧目标
        self.own = [0.0, 0.0]
        self.own_hdg = 0.0
        self.own_spd = 0.0
        self.contacts = []
        self.selected = None
        self.target_selected.emit(None)
        if scale_nm and 0.5 <= scale_nm <= 60:
            self.scale_nm = float(scale_nm)
            self.range_synced.emit(self.scale_nm)
        self.alarm_event.emit(
            "info", "本船降生于 %0.4f%s %0.4f%s，正在加载真实地图…" % (
                abs(lat), "N" if lat >= 0 else "S",
                abs(lon), "E" if lon >= 0 else "W"))
        self.map_cov = max(self.scale_nm * 1.8, 12.0)
        self.map_cx, self.map_cy = 0.0, 0.0
        self._start_map(self.region.bbox_of(0.0, 0.0, self.map_cov))

    def _start_map(self, bbox):
        self._map_bbox = bbox
        if self.map_thread is not None and self.map_thread.isRunning():
            try:
                self.map_thread.done.disconnect()
            except TypeError:
                pass
            # 超时不死等（避免阻塞 UI），旧线程晚到的结果由 _map_bbox 校验丢弃
            self.map_thread.finished.connect(self.map_thread.deleteLater)
            self.map_thread.wait(50)
        self.map_thread = osm_client.MapThread(bbox, self)
        self.map_thread.done.connect(self._on_map_done)
        self.map_thread.start()

    def map_refresh(self):
        """量程调大/本船驶离已加载窗口时，以当前船位为中心重新加载地图
        （仅换数据窗口，不重置本船与目标）。"""
        if self.region is None:
            return
        need = self.scale_nm * 1.35
        ox, oy = self.own[0], self.own[1]
        if max(abs(ox - self.map_cx), abs(oy - self.map_cy)) + need <= self.map_cov:
            return
        self.map_cov = max(self.scale_nm * 1.8, 12.0)
        self.map_cx, self.map_cy = ox, oy
        self._start_map(self.region.bbox_of(ox, oy, self.map_cov))

    def _on_map_done(self, bbox, geo):
        if bbox != self._map_bbox:
            return          # 过期线程的晚到结果，丢弃，避免旧地图覆盖新地图
        shapes = osm_client.to_world_shapes(geo, self.region)
        self.shapes = shapes
        self.map_loaded = shapes is not None
        if self.map_loaded:
            n = len(shapes["coast"]) + len(shapes["land"])
            self.alarm_event.emit(
                "info", "真实地图已加载：海岸线/陆地 %d 段，地名 %d 个"
                % (n, len(shapes["places"])))
        else:
            self.alarm_event.emit(
                "warn", "地图数据获取失败（网络/超时），雷达继续无地图运行")
        self.update()

    # ---------------- 仿真逻辑 ----------------
    def _rel(self, c):
        return c.x - self.own[0], c.y - self.own[1]

    def _disp_brg(self, dx, dy):
        b = math.degrees(math.atan2(dx, dy))
        return norm360(b - self.own_hdg) if self.heading_up else norm360(b)

    def cpa_tcpa(self, c):
        """相对运动求 CPA(海里) / TCPA(分钟)。"""
        dx, dy = self._rel(c)
        rvx = c.vx - self.own_spd * math.sin(math.radians(self.own_hdg))
        rvy = c.vy - self.own_spd * math.cos(math.radians(self.own_hdg))
        vv = rvx * rvx + rvy * rvy
        t = 0.0 if vv < 1e-9 else -(dx * rvx + dy * rvy) / vv  # 小时
        t = max(0.0, t)
        cpa = math.hypot(dx + rvx * t, dy + rvy * t)
        return cpa, t * 60.0

    def threat(self, c):
        rng = math.hypot(*self._rel(c))
        cpa, tcpa = self.cpa_tcpa(c)
        if cpa < 0.4 and tcpa < 10 and rng < self.scale_nm:
            return 2  # 危险
        if cpa < 0.8 and tcpa < 20 and rng < self.scale_nm:
            return 1  # 注意
        return 0

    def _tick(self):
        now = time.time()
        dt = min(0.1, now - self._last_t)
        self._last_t = now
        if not self.running:
            self.update()
            return
        # ---- 键盘操船：↑/W 加速 · ↓/S 减速 · A 左舵 · D 右舵 ----
        k = self.keys
        if Qt.Key_W in k or Qt.Key_Up in k:
            self.own_spd = min(40.0, self.own_spd + 8.0 * dt)
        if Qt.Key_S in k or Qt.Key_Down in k:
            self.own_spd = max(0.0, self.own_spd - 10.0 * dt)
        if Qt.Key_A in k:
            self.own_hdg = norm360(self.own_hdg - 25.0 * dt)
        if Qt.Key_D in k:
            self.own_hdg = norm360(self.own_hdg + 25.0 * dt)
        oh = math.radians(self.own_hdg)
        self.own[0] += math.sin(oh) * self.own_spd * dt / 3600.0
        self.own[1] += math.cos(oh) * self.own_spd * dt / 3600.0
        # ---- 本船动态回报(4Hz) ----
        self._own_acc += dt
        if self._own_acc >= 0.25:
            self._own_acc = 0.0
            self._send_own()
        # ---- 扫描与目标 ----
        prev = self.sweep
        self.sweep = norm360(self.sweep + self.rpm * 6.0 * dt)
        for c in self.contacts:
            c.update(dt)
            dx, dy = self._rel(c)
            tb = self._disp_brg(dx, dy)
            step = norm360(self.sweep - prev)
            if step > 0 and norm360(tb - prev) <= step:
                c.last_pass = now
            c.illum = math.exp(-(now - c.last_pass) / 1.9)
            rng = math.hypot(dx, dy)
            in_guard = rng < self.guard_nm
            if in_guard and not c.alarm_active:
                c.alarm_active = True
                self.alarm_event.emit(
                    "danger", "⚠ %s 闯入警戒圈！距离 %.2f 海里" % (c.name, rng))
            elif not in_guard and c.alarm_active and rng > self.guard_nm * 1.15:
                c.alarm_active = False
                self.alarm_event.emit("info", "%s 已离开警戒圈" % c.name)
        if self.selected is not None and self.selected not in self.contacts:
            self.selected = None
        if self.sim_online and now - self._last_rx > 8.0:
            self.sim_online = False
            self.alarm_event.emit("warn", "目标测试台信号中断（8 秒未收到注入）")
        self.map_refresh()
        self.update()

    # ---------------- 绘制 ----------------
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        R = min(w, h) / 2.0 - 26.0
        if R <= 10:
            return
        p.setFont(FONT_SMALL)
        self._draw_bezel(p, cx, cy, R)
        p.save()
        path = QPainterPath()
        path.addEllipse(QPointF(cx, cy), R, R)
        p.setClipPath(path)
        self._draw_bg(p, cx, cy, R)
        self._draw_map(p, cx, cy, R)
        self._draw_noise(p, cx, cy, R)
        self._draw_rings(p, cx, cy, R)
        self._draw_guard(p, cx, cy, R)
        self._draw_sweep(p, cx, cy, R)
        self._draw_contacts(p, cx, cy, R, w, h)
        self._draw_own(p, cx, cy)
        p.restore()
        self._draw_ticks(p, cx, cy, R)
        self._draw_osd(p, cx, cy, R, w, h)
        p.end()

    def _draw_bezel(self, p, cx, cy, R):
        g = QRadialGradient(QPointF(cx, cy), R + 20)
        g.setColorAt(0.92, QColor("#0d1a22"))
        g.setColorAt(1.0, QColor("#05232b"))
        p.setPen(QPen(QColor("#21424f"), 1.5))
        p.setBrush(g)
        p.drawEllipse(QPointF(cx, cy), R + 16, R + 16)
        p.setPen(QPen(QColor(0, 229, 255, 70), 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), R + 8, R + 8)

    def _draw_bg(self, p, cx, cy, R):
        # 深蓝色海域（接入地图后用于区分陆地/岛屿）
        g = QRadialGradient(QPointF(cx, cy), R)
        g.setColorAt(0.0, QColor("#0b2338"))
        g.setColorAt(0.7, QColor("#081a2b"))
        g.setColorAt(1.0, QColor("#05121f"))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(QPointF(cx, cy), R, R)

    def _w2s(self, x, y, cx, cy, R):
        """世界海里坐标 -> 屏幕点；超出量程返回 None（断开线段）。"""
        dx = x - self.own[0]
        dy = y - self.own[1]
        rng = math.hypot(dx, dy)
        if rng > self.scale_nm * 1.35:
            return None
        brg = self._disp_brg(dx, dy)
        return polar_to_screen(cx, cy, brg, R * rng / self.scale_nm)

    def _w2s_all(self, x, y, cx, cy, R):
        """同 _w2s 但不拒点（配合量程圆裁剪用于陆地填充，避免断线闭合产生割线）。"""
        dx = x - self.own[0]
        dy = y - self.own[1]
        brg = self._disp_brg(dx, dy)
        return polar_to_screen(cx, cy, brg,
                               R * math.hypot(dx, dy) / self.scale_nm)

    def _draw_map(self, p, cx, cy, R):
        sh = self.shapes
        if not sh:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        land_fill = QColor(43, 74, 52, 225)
        land_edge = QColor(140, 224, 168, 210)
        # 闭合陆地/岛屿：先按视口矩形裁剪整环再填充（paintEvent 已裁圆形量程），
        # 不能逐点断线，否则填充自动闭合会产生直线割痕；
        # 裁剪结果缓存，船位/量程/数据未变时直接复用，避免每帧重裁
        need = self.scale_nm * 1.35
        key = (round(self.own[0], 2), round(self.own[1], 2),
               round(self.scale_nm, 3), id(sh["land"]))
        if not self._land_cache or self._land_cache[0] != key:
            xmin, ymin = self.own[0] - need, self.own[1] - need
            xmax, ymax = self.own[0] + need, self.own[1] + need
            rings = []
            for poly in sh["land"]:
                ring = nr_data.clip_closed(poly, xmin, ymin, xmax, ymax)
                if len(ring) >= 3:
                    rings.append(ring)
            self._land_cache = (key, rings)
        for ring in self._land_cache[1]:
            path = QPainterPath()
            path.moveTo(self._w2s_all(ring[0][0], ring[0][1], cx, cy, R))
            for (x, y) in ring[1:]:
                path.lineTo(self._w2s_all(x, y, cx, cy, R))
            path.closeSubpath()
            p.setPen(QPen(land_edge, 1.0))
            p.setBrush(land_fill)
            p.drawPath(path)
        # 开弧海岸线
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(160, 240, 200, 190), 1.4))
        for line in sh["coast"]:
            path = QPainterPath()
            started = False
            for (x, y) in line:
                pt = self._w2s(x, y, cx, cy, R)
                if pt is None:
                    started = False
                    continue
                if not started:
                    path.moveTo(pt)
                    started = True
                else:
                    path.lineTo(pt)
            if not path.isEmpty():
                p.drawPath(path)
        # 地名标注
        p.setFont(FONT_SMALL)
        p.setPen(QPen(QColor(210, 235, 220, 205), 1))
        for (name, x, y) in sh["places"]:
            if not name:
                continue
            pt = self._w2s(x, y, cx, cy, R)
            if pt is None:
                continue
            p.drawText(QRectF(pt.x() + 3, pt.y() - 8, 120, 12),
                       Qt.AlignLeft, name)
        p.restore()

    def _draw_noise(self, p, cx, cy, R):
        # 以归一化极坐标存储，随尺寸自适应；正弦慢闪模拟杂波
        if len(self._noise) < 160:
            for _ in range(160 - len(self._noise)):
                self._noise.append((random.uniform(0, 360),
                                    math.sqrt(random.random()), random.random()))
        now = time.time()
        for a, nr, s in self._noise:
            pt = polar_to_screen(cx, cy, a, R * nr)
            alpha = int(s * 45 * self.gain * (0.4 + 0.6 * math.sin(now * 2 + a)))
            if alpha > 4:
                p.setPen(QPen(QColor(120, 230, 190, min(60, alpha)), 1))
                p.drawPoint(pt)

    def _draw_rings(self, p, cx, cy, R):
        p.setPen(QPen(QColor(60, 200, 160, 55), 1))
        p.setBrush(Qt.NoBrush)
        for i in range(1, 5):
            r = R * i / 4.0
            p.drawEllipse(QPointF(cx, cy), r, r)
            if i < 4:
                pt = polar_to_screen(cx, cy, 45, r)
                p.setPen(QPen(QColor(110, 220, 180, 130), 1))
                p.drawText(QRectF(pt.x() + 3, pt.y() - 12, 60, 14),
                           Qt.AlignLeft, "%.1f" % (self.scale_nm * i / 4.0))
                p.setPen(QPen(QColor(60, 200, 160, 55), 1))
        p.drawLine(QPointF(cx - R, cy), QPointF(cx + R, cy))
        p.drawLine(QPointF(cx, cy - R), QPointF(cx, cy + R))

    def _draw_guard(self, p, cx, cy, R):
        r = R * self.guard_nm / self.scale_nm
        p.setPen(QPen(QColor(255, 82, 82, 110), 1.2, Qt.DashLine))
        p.drawEllipse(QPointF(cx, cy), r, r)
        pt = polar_to_screen(cx, cy, 200, r)
        p.setPen(QPen(QColor(255, 120, 120, 150), 1))
        p.drawText(QRectF(pt.x(), pt.y(), 80, 12), Qt.AlignLeft, "警戒区")

    def _draw_sweep(self, p, cx, cy, R):
        p.setPen(Qt.NoPen)
        seg = self.TRAIL_DEG / self.TRAIL_SEG
        for i in range(self.TRAIL_SEG):
            a0 = self.sweep - (i + 1) * seg
            a1 = self.sweep - i * seg
            frac = 1.0 - i / self.TRAIL_SEG
            alpha = int(80 * (frac ** 2.4) * self.gain)
            if alpha <= 1:
                continue
            path = QPainterPath()
            path.moveTo(cx, cy)
            path.lineTo(polar_to_screen(cx, cy, a0, R))
            path.lineTo(polar_to_screen(cx, cy, a1, R))
            path.closeSubpath()
            p.fillPath(path, QColor(0, 255, 170, alpha))
        tip = polar_to_screen(cx, cy, self.sweep, R)
        p.setPen(QPen(QColor(190, 255, 225, int(220 * self.gain)), 2))
        p.drawLine(QPointF(cx, cy), tip)
        g = QRadialGradient(tip, 12)
        g.setColorAt(0, QColor(180, 255, 220, 120))
        g.setColorAt(1, QColor(180, 255, 220, 0))
        p.setBrush(g)
        p.setPen(Qt.NoPen)
        p.drawEllipse(tip, 12, 12)

    def _draw_label(self, p, pt, text, qcolor, w, h):
        """标签自动避让：靠近右边缘时绘制在点左侧，避免被裁切。"""
        box_w, box_h = 110.0, 15.0
        if pt.x() + 12 + box_w > w - 4:
            rect = QRectF(pt.x() - 12 - box_w, pt.y() - 17, box_w, box_h)
            align = Qt.AlignRight | Qt.AlignVCenter
        else:
            rect = QRectF(pt.x() + 12, pt.y() - 17, box_w, box_h)
            align = Qt.AlignLeft | Qt.AlignVCenter
        rect.moveTop(max(2.0, min(rect.top(), h - 18.0)))
        p.setFont(FONT_DIG)
        p.setPen(QPen(qcolor, 1))
        p.drawText(rect, align, text)

    def _draw_contacts(self, p, cx, cy, R, w, h):
        now = time.time()
        pulse = 0.5 + 0.5 * math.sin(now * 6.5)
        for c in self.contacts:
            dx, dy = self._rel(c)
            rng = math.hypot(dx, dy)
            if rng > self.scale_nm * 1.02:
                continue
            brg = self._disp_brg(dx, dy)
            r_pix = R * rng / self.scale_nm
            pt = polar_to_screen(cx, cy, brg, r_pix)
            b = c.illum * self.gain
            lvl = self.threat(c)
            alarm = c.alarm_active
            if alarm:
                # 警戒区入侵：可呼吸红色亮点 + 扩散警戒环，与普通回波区分
                breath = 0.5 + 0.5 * math.sin(now * 4.2)
                rad = 13 + 11 * breath
                g = QRadialGradient(pt, rad)
                c1 = QColor(255, 60, 60)
                c1.setAlpha(int(100 + 120 * breath))
                g.setColorAt(0, c1)
                c2 = QColor(255, 60, 60)
                c2.setAlpha(0)
                g.setColorAt(1, c2)
                p.setPen(Qt.NoPen)
                p.setBrush(g)
                p.drawEllipse(pt, rad, rad)
                p.setBrush(QColor(255, 90, 90, int(160 + 95 * breath)))
                r0 = 3.4 + 2.8 * breath
                p.drawEllipse(pt, r0, r0)
                p.setPen(QPen(QColor(255, 80, 80, int(40 + 150 * (1.0 - breath))), 1.5))
                p.setBrush(Qt.NoBrush)
                rr = 10 + 26 * breath
                p.drawEllipse(pt, rr, rr)
            else:
                # 辉光
                if b > 0.02:
                    rad = 9 + 9 * b
                    col = (QColor(255, 210, 80) if lvl == 2 and b > 0.5
                           else QColor(120, 255, 190))
                    col.setAlpha(int(170 * b))
                    g = QRadialGradient(pt, rad)
                    g.setColorAt(0, col)
                    outer = QColor(col)
                    outer.setAlpha(0)
                    g.setColorAt(1, outer)
                    p.setPen(Qt.NoPen)
                    p.setBrush(g)
                    p.drawEllipse(pt, rad, rad)
                # 核心点
                core = QColor(220, 255, 235)
                core.setAlpha(int(90 + 165 * min(1.0, b)))
                p.setPen(Qt.NoPen)
                p.setBrush(core)
                p.drawEllipse(pt, 2.6 + 2.2 * b, 2.6 + 2.2 * b)
            # 相对速度矢量(被照亮时可见)
            if b > 0.12:
                rvx = c.vx - self.own_spd * math.sin(math.radians(self.own_hdg))
                rvy = c.vy - self.own_spd * math.cos(math.radians(self.own_hdg))
                scale = 0.22 * R / self.scale_nm
                ep = QPointF(pt.x() + rvx * scale, pt.y() - rvy * scale)
                p.setPen(QPen(QColor(140, 255, 200, int(150 * b)), 1))
                p.drawLine(pt, ep)
            # 标签：名称 + 距离（不再因扫描亮度衰减而丢失，且自动避让边缘）
            if b > 0.25 or c is self.selected or alarm:
                a = 255 if (c is self.selected or alarm) else int(120 + 135 * b)
                self._draw_label(
                    p, pt, "%s  %.1f海里" % (c.name, rng),
                    QColor(255, 110, 110, a) if alarm else QColor(160, 255, 210, a),
                    w, h)
            # 高风险目标：脉冲警示圈（报警时已有呼吸环，不重复叠加）
            if lvl == 2 and b > 0.15 and not alarm:
                p.setPen(QPen(QColor(255, 60, 60, int(120 + 110 * pulse)), 1.4))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(pt, 12 + 4 * pulse, 12 + 4 * pulse)
            if c is self.selected:
                self._draw_brackets(p, pt)

    def _draw_brackets(self, p, pt):
        s, g0 = 11, 5
        p.setPen(QPen(QColor(0, 229, 255, 230), 1.6))
        for sx in (-1, 1):
            for sy in (-1, 1):
                x, y = pt.x() + sx * s, pt.y() + sy * s
                p.drawLine(QPointF(x, y), QPointF(x - sx * g0, y))
                p.drawLine(QPointF(x, y), QPointF(x, y - sy * g0))

    def _draw_own(self, p, cx, cy):
        p.setPen(QPen(QColor(0, 229, 255, 200), 1.4))
        p.setBrush(QColor(0, 60, 70, 160))
        p.save()
        p.translate(cx, cy)
        p.rotate(0.0 if self.heading_up else self.own_hdg)
        tri = QPainterPath()
        tri.moveTo(0, -11)
        tri.lineTo(7, 9)
        tri.lineTo(0, 4)
        tri.lineTo(-7, 9)
        tri.closeSubpath()
        p.drawPath(tri)
        p.restore()
        p.setPen(QPen(QColor(140, 230, 255, 160), 1))
        p.drawEllipse(QPointF(cx, cy), 14, 14)

    def _draw_ticks(self, p, cx, cy, R):
        rot = self.own_hdg if self.heading_up else 0.0
        p.setFont(FONT_SMALL)
        for deg in range(0, 360, 5):
            major = deg % 30 == 0
            r0, r1 = R + 2, R + (12 if major else 6)
            p.setPen(QPen(QColor(110, 220, 180, 200 if major else 90),
                         1.3 if major else 1))
            p.drawLine(polar_to_screen(cx, cy, deg, r0),
                       polar_to_screen(cx, cy, deg, r1))
        for deg in range(0, 360, 30):
            txt = "%03d" % norm360(deg - rot)
            pt = polar_to_screen(cx, cy, deg, R + 22)
            p.setPen(QPen(QColor(150, 240, 200, 220), 1))
            p.drawText(QRectF(pt.x() - 22, pt.y() - 8, 44, 16),
                       Qt.AlignCenter, txt)

    def _draw_osd(self, p, cx, cy, R, w, h):
        p.setFont(FONT_DIG)
        p.setPen(QPen(QColor(0, 229, 255, 170), 1))
        mode = "船首向上" if self.heading_up else "真北向上"
        p.drawText(14, 22, "雷达标绘  %s  量程 %.1f 海里" % (mode, self.scale_nm))
        p.drawText(14, 38, "天线 %d 转/分   增益 %d%%" % (self.rpm, self.gain * 100))
        p.drawText(14, 54, "目标源 %s" % ("在线" if self.sim_online else "离线"))
        if self.region is not None and self.map_loaded:
            lon, lat = self.region.to_ll(self.own[0], self.own[1])
            p.setPen(QPen(QColor(150, 230, 255, 200), 1))
            p.drawText(14, 70, "船位 %0.4f%s %0.4f%s" % (
                abs(lat), "N" if lat >= 0 else "S",
                abs(lon), "E" if lon >= 0 else "W"))
        elif self.region is not None:
            p.drawText(14, 70, "地图加载中…")
        run_txt = "扫描中" if self.running else "已暂停"
        col = QColor(0, 255, 170, 200) if self.running else QColor(255, 193, 8, 220)
        p.setPen(QPen(col, 1))
        p.drawText(w - 130, 22, run_txt)
        p.setPen(QPen(QColor(90, 140, 160, 200), 1))
        p.drawText(w - 130, 38, "罗经 %03d°" % self.own_hdg)
        p.drawText(w - 130, 54, "航速 %0.0f 节" % self.own_spd)
        if not self.bind_ok:
            # 端口被占：红色横幅明示，否则用户无法区分“没目标”和“收不到”
            blink = 0.6 + 0.4 * math.sin(time.time() * 5)
            p.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
            p.setPen(QPen(QColor(255, 60, 60, int(255 * blink)), 1))
            p.drawText(QRectF(cx - 260, cy - R + 10, 520, 26), Qt.AlignCenter,
                       "⚠ 端口 %d 被占用，无法接收目标——请关闭旧雷达实例" % PORT_RX)
        elif not self.running:
            p.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
            p.setPen(QPen(QColor(255, 193, 8, 170), 1))
            p.drawText(QRectF(cx - 100, cy + R - 44, 200, 30),
                       Qt.AlignCenter, "● 扫描已暂停")
        elif not self.contacts:
            p.setFont(QFont("Microsoft YaHei", 10))
            p.setPen(QPen(QColor(0, 229, 255, 120), 1))
            p.drawText(QRectF(cx - 190, cy + R - 40, 380, 24),
                       Qt.AlignCenter, "暂无目标 —— 请运行 radar_tester.py 注入目标")

    # ---------------- 交互 ----------------
    def mousePressEvent(self, ev):
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        R = min(w, h) / 2.0 - 26.0
        best, bestd = None, 18.0
        for c in self.contacts:
            dx, dy = self._rel(c)
            rng = math.hypot(dx, dy)
            if rng > self.scale_nm:
                continue
            pt = polar_to_screen(cx, cy, self._disp_brg(dx, dy), R * rng / self.scale_nm)
            d = math.hypot(pt.x() - ev.x(), pt.y() - ev.y())
            if d < bestd:
                best, bestd = c, d
        self.selected = best
        self.target_selected.emit(best)
        self.update()


# ================= 主窗口 =================
class Dashboard(QWidget):
    THREAT_TXT = {0: ("安全", "#4ddb77"), 1: ("注意", "#ffc108"), 2: ("危险", "#ff5252")}

    def __init__(self):
        super().__init__()
        self.setWindowTitle("RN-9000 航海雷达控制台")
        self.resize(1280, 820)
        self.setFocusPolicy(Qt.StrongFocus)
        self._held = set()
        self._clock_blink = False
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(10)
        root.addWidget(self._build_header())
        split = QSplitter(Qt.Horizontal, self)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(7)
        root.addWidget(split, 1)

        frame = QFrame()
        frame.setObjectName("ScopeFrame")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(8, 8, 8, 8)
        self.scope = RadarScope()
        self.scope.keys = self._held
        self.scope.target_selected.connect(self._on_select)
        self.scope.alarm_event.connect(self._log)
        self.scope.range_synced.connect(self._on_range_synced)
        lay.addWidget(self.scope)
        split.addWidget(frame)
        split.addWidget(self._build_side())
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([880, 360])
        root.addWidget(self._build_controls())

        self._refresh_cards()
        t = QTimer(self)
        t.timeout.connect(self._tick_ui)
        t.start(250)
        clk = QTimer(self)
        clk.timeout.connect(self._tick_clock)
        clk.start(1000)
        self._log("info", "雷达启动，监听目标注入（UDP 端口 %d）" % PORT_RX)
        self._log("info", "键盘操纵：↑/W 加速 · ↓/S 减速 · A 左舵 · D 右舵")

    # ---------- 键盘操船 ----------
    def keyPressEvent(self, ev):
        if ev.isAutoRepeat():
            return
        if ev.key() in (Qt.Key_W, Qt.Key_A, Qt.Key_S, Qt.Key_D,
                        Qt.Key_Up, Qt.Key_Down):
            self._held.add(ev.key())
            ev.accept()
        else:
            super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev):
        if ev.isAutoRepeat():
            return
        self._held.discard(ev.key())
        super().keyReleaseEvent(ev)

    # ---------- 头部 ----------
    def _build_header(self):
        bar = QFrame()
        bar.setObjectName("Panel")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 6, 14, 6)
        t = QLabel("◈ RN-9000 航海雷达控制台")
        t.setObjectName("Title")
        s = QLabel("目标由《雷达目标测试台》注入 · WASD 操船")
        s.setObjectName("SubTitle")
        self.lbl_sys = QLabel()
        self.lbl_sys.setStyleSheet("color:#4ddb77; background:transparent; font-weight:bold;")
        self.lbl_clock = QLabel()
        self.lbl_clock.setStyleSheet(
            "color:#00e5ff; background:transparent; font-size:15px; font-weight:bold;")
        lay.addWidget(t)
        lay.addWidget(s, 0, Qt.AlignBottom)
        lay.addStretch(1)
        lay.addWidget(self.lbl_sys)
        lay.addSpacing(16)
        lay.addWidget(self.lbl_clock)
        return bar

    # ---------- 右侧面板 ----------
    def _build_side(self):
        side = QWidget()
        side.setMinimumWidth(300)   # 总宽度由分隔条拖动调整
        col = QVBoxLayout(side)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)
        col.addWidget(self._build_card_panel())
        col.addWidget(self._build_list_panel(), 1)
        col.addWidget(self._build_log_panel())
        return side

    def _build_card_panel(self):
        panel = QFrame()
        panel.setObjectName("Panel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 10, 12, 10)
        title = QLabel("▍目标信息")
        title.setObjectName("Section")
        lay.addWidget(title)
        grid = QGridLayout()
        grid.setVerticalSpacing(7)
        grid.setHorizontalSpacing(10)
        self._cards = {}
        rows = [("目标", "tid"), ("真方位", "brg"), ("距离", "rng"),
                ("对地航速", "spd"), ("对地航向", "hdg"),
                ("最近会遇距离", "cpa"), ("最近会遇时间", "tcpa"), ("威胁等级", "threat")]
        for i, (key, name) in enumerate(rows):
            r, c = divmod(i, 2)
            kl = QLabel(key)
            kl.setObjectName("CardKey")
            vl = QLabel("--")
            vl.setObjectName("CardVal")
            grid.addWidget(kl, r, c * 2)
            grid.addWidget(vl, r, c * 2 + 1)
            self._cards[name] = vl
        lay.addLayout(grid)
        hint = QLabel("提示：点击雷达面目标或右侧列表可查看详情；拖动面板左侧分隔条可调整栏宽")
        hint.setObjectName("SubTitle")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        return panel

    def _build_list_panel(self):
        panel = QFrame()
        panel.setObjectName("Panel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 10, 12, 10)
        title = QLabel("▍目标列表（▲危险 ◆注意 ○安全，按距离排序）")
        title.setObjectName("Section")
        title.setWordWrap(True)
        lay.addWidget(title)
        self.listw = QListWidget()
        self.listw.itemSelectionChanged.connect(self._on_list_select)
        lay.addWidget(self.listw, 1)
        return panel

    def _build_log_panel(self):
        panel = QFrame()
        panel.setObjectName("Panel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 10, 12, 10)
        title = QLabel("▍告警日志")
        title.setObjectName("Section")
        lay.addWidget(title)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(200)
        self.log.setFixedHeight(120)
        lay.addWidget(self.log)
        return panel

    # ---------- 底部控制台 ----------
    def _build_controls(self):
        bar = QFrame()
        bar.setObjectName("Panel")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(12)

        self.btn_run = QPushButton("⏸ 暂停扫描")
        self.btn_run.clicked.connect(self._toggle_run)
        lay.addWidget(self.btn_run)

        self.btn_mode = QPushButton("显示: 真北向上")
        self.btn_mode.setCheckable(True)
        self.btn_mode.toggled.connect(self._toggle_mode)
        lay.addWidget(self.btn_mode)

        lay.addSpacing(10)
        lay.addWidget(QLabel("量程"))
        self.cmb_range = QComboBox()
        for v in (2, 4, 6, 8, 12, 16):
            self.cmb_range.addItem("%d 海里" % v, v)
        self.cmb_range.setCurrentIndex(3)
        self.cmb_range.currentIndexChanged.connect(self._set_range)
        lay.addWidget(self.cmb_range)

        lay.addWidget(QLabel("转速"))
        self.sld_rpm = QSlider(Qt.Horizontal)
        self.sld_rpm.setRange(8, 90)
        self.sld_rpm.setValue(30)
        self.sld_rpm.setFixedWidth(110)
        self.sld_rpm.valueChanged.connect(
            lambda v: setattr(self.scope, "rpm", float(v)))
        lay.addWidget(self.sld_rpm)
        self.lbl_rpm = QLabel("30 转/分")
        self.sld_rpm.valueChanged.connect(
            lambda v: self.lbl_rpm.setText("%d 转/分" % v))
        lay.addWidget(self.lbl_rpm)

        lay.addWidget(QLabel("增益"))
        self.sld_gain = QSlider(Qt.Horizontal)
        self.sld_gain.setRange(40, 140)
        self.sld_gain.setValue(100)
        self.sld_gain.setFixedWidth(90)
        self.sld_gain.valueChanged.connect(
            lambda v: setattr(self.scope, "gain", v / 100.0))
        lay.addWidget(self.sld_gain)

        lay.addWidget(QLabel("警戒圈"))
        self.spn_guard = QDoubleSpinBox()
        self.spn_guard.setRange(0.2, 6.0)
        self.spn_guard.setSingleStep(0.1)
        self.spn_guard.setValue(1.5)
        self.spn_guard.setSuffix(" 海里")
        self.spn_guard.valueChanged.connect(
            lambda v: setattr(self.scope, "guard_nm", float(v)))
        lay.addWidget(self.spn_guard)

        lay.addStretch(1)
        help_lbl = QLabel("操纵：↑/W 加速 · ↓/S 减速 · A 左舵 · D 右舵")
        help_lbl.setObjectName("SubTitle")
        lay.addWidget(help_lbl)
        return bar

    # ---------- 槽函数 ----------
    def _toggle_run(self):
        self.scope.running = not self.scope.running
        self.btn_run.setText("▶ 恢复扫描" if not self.scope.running else "⏸ 暂停扫描")

    def _toggle_mode(self, checked):
        self.scope.heading_up = checked
        self.btn_mode.setText("显示: 船首向上" if checked else "显示: 真北向上")

    def _set_range(self):
        self.scope.scale_nm = float(self.cmb_range.currentData())
        self.scope.map_refresh()

    def _on_range_synced(self, nm):
        """降生时同步量程下拉框（取最接近的预设项）。"""
        best_i, best_d = 0, 1e9
        for i in range(self.cmb_range.count()):
            d = abs(self.cmb_range.itemData(i) - nm)
            if d < best_d:
                best_i, best_d = i, d
        self.cmb_range.blockSignals(True)
        self.cmb_range.setCurrentIndex(best_i)
        self.cmb_range.blockSignals(False)
        self.scope.map_refresh()

    def _on_select(self, contact):
        if contact is None:
            self.listw.clearSelection()
        else:
            for i in range(self.listw.count()):
                if self.listw.item(i).data(Qt.UserRole) is contact:
                    self.listw.setCurrentRow(i)
                    break
        self._refresh_cards()

    def _on_list_select(self):
        items = self.listw.selectedItems()
        if items:
            self.scope.selected = items[0].data(Qt.UserRole)
            self.scope.update()
            self._refresh_cards()

    _LOG_STYLE = {"danger": "color:#ff5252;", "info": "color:#7fa8b8;",
                  "warn": "color:#ffc108;"}

    def _log(self, level, msg):
        ts = time.strftime("%H:%M:%S")
        self.log.appendHtml(
            '<span style="%s">[%s] %s</span>' % (self._LOG_STYLE.get(level, ""), ts, msg))
        if level == "danger":
            # 警报铃：间隔 260ms 响 3 下
            for i in range(3):
                QTimer.singleShot(i * 260, QApplication.beep)

    # ---------- 周期刷新 ----------
    def _tick_clock(self):
        self._clock_blink = not self._clock_blink
        self.lbl_clock.setText(time.strftime("%Y-%m-%d  %H:%M:%S"))
        led = "●" if self._clock_blink else "○"
        self.lbl_sys.setText("%s 系统正常 · 定位 · 标绘" % led)

    def _tick_ui(self):
        self._refresh_list()
        self._refresh_cards()

    def _refresh_list(self):
        sel = self.scope.selected
        self.listw.blockSignals(True)
        self.listw.clear()
        items = []
        for c in self.scope.contacts:
            dx, dy = self.scope._rel(c)
            rng = math.hypot(dx, dy)
            if rng <= self.scope.scale_nm * 1.1:
                items.append((rng, c, dx, dy))
        items.sort(key=lambda t: t[0])
        for rng, c, dx, dy in items:
            brg = norm360(math.degrees(math.atan2(dx, dy)))
            lvl = self.scope.threat(c)
            mark = {2: "▲", 1: "◆", 0: "○"}[lvl]
            it = QListWidgetItem(
                "%s %s  距%5.2f  位%03d°  速%4.1f节" % (mark, c.name, rng, brg, c.speed))
            it.setFont(FONT_DIG)
            it.setForeground(QColor(self.THREAT_TXT[lvl][1]) if lvl else QColor("#b8ccd7"))
            it.setData(Qt.UserRole, c)
            if c is sel:
                it.setSelected(True)
            self.listw.addItem(it)
        self.listw.blockSignals(False)

    def _refresh_cards(self):
        c = self.scope.selected
        widgets = list(self._cards.values())
        if c is None:
            for wgt in widgets:
                wgt.setText("--")
                wgt.setObjectName("CardVal")
                wgt.style().unpolish(wgt)
                wgt.style().polish(wgt)
            return
        dx, dy = self.scope._rel(c)
        brg = norm360(math.degrees(math.atan2(dx, dy)))
        rng = math.hypot(dx, dy)
        cpa, tcpa = self.scope.cpa_tcpa(c)
        lvl = self.scope.threat(c)
        vals = {
            "tid": c.name,
            "brg": "%03.0f°" % brg,
            "rng": "%.2f 海里" % rng,
            "spd": "%.1f 节" % c.speed,
            "hdg": "%03.0f°" % c.heading,
            "cpa": "%.2f 海里" % cpa,
            "tcpa": "%.1f 分钟" % tcpa,
            "threat": self.THREAT_TXT[lvl][0],
        }
        for name, wgt in self._cards.items():
            wgt.setText(vals[name])
            if name in ("cpa", "tcpa", "threat"):
                wgt.setObjectName({0: "CardValSafe", 1: "CardValMid",
                                   2: "CardValHi"}[lvl])
            else:
                wgt.setObjectName("CardVal")
            wgt.style().unpolish(wgt)
            wgt.style().polish(wgt)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(GLOBAL_QSS)
    win = Dashboard()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
