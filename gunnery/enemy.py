"""敌舰 —— 世界坐标运动的战列舰：与雷达目标同一匀速直线规则、
≥15 秒一轮还击、10~30% 命中率（距离越近越准），以及精细的侧视军舰
建模与分部位命中判定。相对本船的方位/距离由攻击场景每帧按本船位姿回写。

纯逻辑 + QPainter 绘制，不依赖雷达控制台。
"""

import math
import random

from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import QColor, QLinearGradient, QPen, QPolygonF, QRadialGradient

SHIP_LEN = 210.0        # m
SHIP_H = 36.0           # m 上层建筑总高（桅顶）
FREEBOARD = 8.0         # m 干舷


class EnemyShip:
    """世界坐标敌舰：位置(海里)/真航向(度)/航速(节)，与雷达控制台
    Contact 同一运动规则（匀速直线）；相对本船的方位/距离由场景
    每帧回写，保证与雷达测得的几何完全一致。"""

    def __init__(self, x=0.0, y=0.0, course=0.0, speed=0.0, name=None,
                 hp=1000.0, rng=6000.0, brg=0.0):
        self.x = float(x)                 # 世界坐标（海里，东/北）
        self.y = float(y)
        self.course = float(course)       # 真航向（度）
        self.speed = float(speed)         # 航速（节）
        self.name = name or "敌军舰"
        self.hp = max(0.0, float(hp))     # 血量（由持久化 state 注入）
        # 相对本船（场景每帧回写，仅供绘制/命中/测距使用）
        self.rng = max(300.0, float(rng))
        self.brg = float(brg)
        self._next_fire = random.uniform(15.0, 18.0)  # 首发也遵守≥15秒
        self.alive = True
        self.sinking = 0.0                # 沉没动画进度

    # ---------------- 运动 / 开火 ----------------
    def integrate(self, dt):
        """按当前航向/航速直线推进（与雷达目标一致）。"""
        h = math.radians(self.course)
        self.x += math.sin(h) * self.speed * dt / 3600.0
        self.y += math.cos(h) * self.speed * dt / 3600.0

    def step_fire(self, dt):
        """还击计时（≥15 秒一轮）；返回本帧是否开火。"""
        self._next_fire -= dt
        if self._next_fire <= 0:
            self._next_fire = random.uniform(15.0, 24.0)
            return True
        return False

    # ---------------- AI ----------------
    def hit_rate(self):
        """命中率：12km→10%，3km→30%，线性内插并夹取（基于相对距离）。"""
        r = (12000.0 - self.rng) / (12000.0 - 3000.0)
        return min(0.30, max(0.10, 0.10 + 0.20 * r))

    # ---------------- 命中判定 ----------------
    @staticmethod
    def zone_of(u, y):
        """u: 沿舰长 0(艉)~1(艏)；y: 高度 m(水面 0)。返回部位名或 None。"""
        if y < -7.0 or y > SHIP_H:
            return None
        if (0.06 <= u <= 0.16 or 0.84 <= u <= 0.95) and 3.0 <= y <= 14.0:
            return "主炮塔"
        if 0.62 <= u <= 0.74 and 3.0 <= y <= 34.0:
            return "舰桥"
        if 0.36 <= u <= 0.58 and 3.0 <= y <= 22.0:
            return "烟囱"
        if 0.20 <= u <= 0.80 and -7.0 <= y <= 3.0:
            return "弹药库"          # 水线装甲带/弹药库区
        if 0.16 <= u <= 0.84 and 3.0 < y <= 10.0:
            return "上层建筑"
        if -7.0 <= y <= 12.0:
            return "艏艉"
        return None


# ---------------- 精细侧视建模（艏朝右） ----------------
def draw_warship(p, cx, water_y, s, hp_ratio, t=0.0, sink_t=0.0):
    """以水线中点 (cx, water_y)、比例 s 像素/米 绘制战列舰侧视图。
    新版：更精细的船体曲线、舰艏浪、装甲带、宝塔式舰桥、三联装炮塔、
    高炮群、烟囱排烟、战损火焰与浓烟、沉没倾斜与艏沉动画。
    sink_t: 沉没动画累计秒数，用于加剧倾斜/下沉与最终爆炸。"""
    L = SHIP_LEN * s
    list_ang = 0.0
    sink_off = 0.0
    # 血量不足时预先倾斜，沉没动画叠加最终倾覆
    if hp_ratio < 0.35:
        list_ang = 2.5 + 3.5 * (0.35 - hp_ratio) / 0.35
    if hp_ratio < 0.15:
        sink_off = 4 * s * (0.15 - hp_ratio) / 0.15 * 3
    # 沉没进程：0~2.6s 内逐渐加大倾斜与下沉
    if sink_t > 0:
        prog = min(1.0, sink_t / 2.6)
        list_ang += 12.0 * prog * prog
        sink_off += 16 * s * prog * prog
    water_y += sink_off
    p.save()
    p.translate(cx, water_y)
    p.rotate(list_ang)
    x0 = -L / 2.0

    def X(u):
        return x0 + u * L

    def Y(m):
        return -m * s

    dead = hp_ratio <= 0.0
    dying = hp_ratio < 0.08 and not dead

    # ---- 舰艏/舰艉浪花（仅在舰体周围水面） ----
    _wake_foam(p, X, Y, L, s, t)

    # ---- 船体：艏缘弧线 + 艉 + 舭部隆起 ----
    hull_grad = QLinearGradient(0, Y(12), 0, Y(-8))
    hull_grad.setColorAt(0, QColor(108, 120, 134))
    hull_grad.setColorAt(0.45, QColor(74, 84, 98))
    hull_grad.setColorAt(0.78, QColor(52, 62, 76))
    hull_grad.setColorAt(1, QColor(38, 46, 58))
    p.setBrush(hull_grad)
    p.setPen(QPen(QColor(24, 30, 38), max(1.0, s)))
    path = QPolygonF([
        QPointF(X(0.00), Y(6.5)),          # 艉柱
        QPointF(X(0.02), Y(8.5)),
        QPointF(X(0.28), Y(9.2)),
        QPointF(X(0.72), Y(9.8)),          # 平行中体
        QPointF(X(0.90), Y(11.0)),         # 艏楼甲板起升
        QPointF(X(0.965), Y(13.5)),        # 艏楼顶
        QPointF(X(0.995), Y(15.0)),        # 艏柱顶
        QPointF(X(0.975), Y(3.0)),         # 前倾艏柱
        QPointF(X(0.93), Y(-6.0)),         # 球鼻艏水线
        QPointF(X(0.86), Y(-7.2)),         # 水线最前
        QPointF(X(0.10), Y(-7.0)),         # 船底
        QPointF(X(0.02), Y(-3.0)),
        QPointF(X(0.00), Y(-1.0)),
    ])
    p.drawPolygon(path)

    # 干舷红褐色防污带 / 水线带
    p.setPen(QPen(Qt.NoPen))
    p.setBrush(QColor(112, 56, 48, 210))
    p.drawRect(int(X(0.015)), int(Y(-0.5)), int(L * 0.94), int(2.0 * s))
    # 黑色水线
    p.setBrush(QColor(28, 32, 40, 200))
    p.drawRect(int(X(0.01)), int(Y(-1.6)), int(L * 0.96), int(1.0 * s))

    # 甲板舷墙 / 舷缘
    p.setPen(QPen(QColor(46, 56, 68), max(0.8, s * 0.6)))
    p.setBrush(QColor(88, 100, 114))
    deck_y = Y(9.6)
    p.drawRect(int(X(0.03)), int(deck_y), int(L * 0.89), int(1.0 * s))

    # 船舷迷彩：深浅双色不规则块
    p.setPen(QPen(Qt.NoPen))
    p.setBrush(QColor(64, 78, 92, 130))
    p.drawPolygon(QPolygonF([
        QPointF(X(0.08), Y(8.8)), QPointF(X(0.42), Y(8.4)),
        QPointF(X(0.38), Y(5.0)), QPointF(X(0.06), Y(4.5))]))
    p.setBrush(QColor(92, 104, 118, 110))
    p.drawPolygon(QPolygonF([
        QPointF(X(0.50), Y(8.5)), QPointF(X(0.86), Y(8.9)),
        QPointF(X(0.82), Y(5.2)), QPointF(X(0.48), Y(4.8))]))

    # 舷号（远距离过小时不绘制文字）
    if s >= 0.6:
        p.setPen(QPen(QColor(220, 230, 240, 180), max(1.0, s * 0.6)))
        p.drawText(QRectF(X(0.72), Y(8.2), 16 * s, 5 * s), Qt.AlignCenter, "B-65")

    # ---- 主炮塔 x3（前二超射 + 后一） ----
    for u, ang in ((0.875, 0.0), (0.805, 0.0), (0.105, 180.0)):
        _turret_main(p, X(u), Y(10.2), s, ang)
    # ---- 副炮 x4 ----
    for u in (0.72, 0.62, 0.30, 0.20):
        _turret_secondary(p, X(u), Y(10.5), s * 0.52,
                          0.0 if u > 0.5 else 180.0)

    # ---- 舰桥塔楼（Pagoda 式，多层退台） ----
    tower_colors = (QColor(96, 106, 120), QColor(84, 94, 108),
                    QColor(72, 82, 96), QColor(64, 74, 88))
    tower_blocks = [
        (0.600, 16.0, 11.0, 6.5),   # 底层舰桥
        (0.620, 23.0, 8.5, 7.0),    # 二层
        (0.640, 30.0, 6.0, 7.0),    # 罗经舰桥
        (0.655, 36.0, 4.0, 6.0),    # 射击指挥塔
    ]
    for i, (u, h, w, dh) in enumerate(tower_blocks):
        p.setBrush(tower_colors[i])
        p.setPen(QPen(QColor(32, 38, 46), max(0.8, s * 0.5)))
        p.drawRect(int(X(u)), int(Y(h)), int(w * s), int(dh * s))
        # 窗列
        p.setPen(QPen(QColor(160, 190, 210, 140), max(0.5, s * 0.3)))
        for j in range(1, int(w / 1.8)):
            px = X(u) + j * 1.6 * s
            p.drawLine(QPointF(px, Y(h) + 1.2 * s),
                       QPointF(px, Y(h - dh) + 1.2 * s))

    # 主桅杆 + 横桁 + 测距仪
    p.setPen(QPen(QColor(44, 52, 62), max(1.0, 0.9 * s)))
    p.drawLine(QPointF(X(0.662), Y(36)), QPointF(X(0.662), Y(48)))
    p.drawLine(QPointF(X(0.640), Y(44)), QPointF(X(0.684), Y(44)))
    p.drawLine(QPointF(X(0.650), Y(42)), QPointF(X(0.674), Y(42)))
    # 后桅
    p.drawLine(QPointF(X(0.18), Y(10)), QPointF(X(0.18), Y(24)))
    p.drawLine(QPointF(X(0.16), Y(22)), QPointF(X(0.20), Y(22)))

    # ---- 烟囱 x2（外倾梯形 + 帽檐） ----
    for u in (0.535, 0.405):
        p.setBrush(QColor(82, 74, 70))
        p.setPen(QPen(QColor(34, 30, 28), max(1.0, s)))
        p.drawPolygon(QPolygonF([
            QPointF(X(u), Y(21.0)), QPointF(X(u + 0.038), Y(21.0)),
            QPointF(X(u + 0.050), Y(9.0)), QPointF(X(u + 0.010), Y(9.0))]))
        # 帽檐
        p.setBrush(QColor(60, 54, 50))
        p.drawRect(int(X(u - 0.003)), int(Y(21.5)), int(5.0 * s), int(1.4 * s))
        # 烟
        _smoke(p, X(u + 0.025), Y(22), s * 0.9, 0.6, t * 0.7)

    # ---- 高炮群 + 探照灯 ----
    p.setPen(QPen(QColor(52, 60, 70), max(0.8, 0.6 * s)))
    for u in (0.78, 0.66, 0.58, 0.47, 0.35, 0.26):
        p.drawLine(QPointF(X(u), Y(10.5)), QPointF(X(u), Y(12.0)))
        p.setBrush(QColor(70, 80, 92))
        p.drawEllipse(QPointF(X(u), Y(12.4)), 1.0 * s, 1.0 * s)
    # 探照灯
    for u in (0.74, 0.32):
        p.setPen(QPen(QColor(60, 70, 80), max(0.6, s * 0.4)))
        p.setBrush(QColor(180, 200, 220, 160))
        p.drawEllipse(QPointF(X(u), Y(11.2)), 1.4 * s, 1.4 * s)

    # 舰艉吊杆/起重机
    p.setPen(QPen(QColor(56, 64, 74), max(0.8, s * 0.6)))
    p.drawLine(QPointF(X(0.16), Y(10.0)), QPointF(X(0.10), Y(16.0)))
    p.drawLine(QPointF(X(0.10), Y(16.0)), QPointF(X(0.14), Y(18.0)))

    # ---- 战损：烟雾/火焰/碎片 ----
    dmg = 1.0 - hp_ratio
    if hp_ratio < 0.75:
        _smoke(p, X(0.55), Y(22), s, dmg * 1.6, t)
    if hp_ratio < 0.50:
        _fire(p, X(0.66), Y(14), s, t)
        _smoke(p, X(0.30), Y(16), s, dmg * 2.2, t * 1.2)
        _sparks(p, X(0.66), Y(14), s, t)
    if hp_ratio < 0.25:
        _fire(p, X(0.36), Y(10), s * 1.3, t * 0.85)
        _smoke(p, X(0.82), Y(18), s, dmg * 2.8, t * 0.9)
        _sparks(p, X(0.36), Y(10), s, t * 0.8)
    if dead or dying:
        # 沉没时更大的锅炉爆炸与黑烟
        _big_fire(p, X(0.58), Y(18), s * 2.0, t)
        _smoke(p, X(0.58), Y(24), s * 1.6, 3.5, t * 0.6)
        _debris(p, X(0.5), Y(16), s, t)

    # 沉没时油膜
    if sink_off > 0:
        _oil_slick(p, X(0.5), Y(-2.5), s, min(1.0, sink_off / (18 * s)), t)

    p.restore()


def _turret_main(p, x, y, s, ang):
    """三联装主炮塔：带装甲切面、炮尾鼓包、炮管俯仰。"""
    p.save()
    p.translate(x, y)
    p.rotate(ang)
    # 炮塔座圈
    p.setPen(QPen(QColor(28, 34, 42), max(0.8, s * 0.4)))
    p.setBrush(QColor(78, 88, 102))
    p.drawEllipse(QPointF(0, 0), 4.6 * s, 1.6 * s)
    # 炮塔主体（梨形装甲）
    body = QLinearGradient(0, -5 * s, 0, 0.5 * s)
    body.setColorAt(0, QColor(100, 112, 126))
    body.setColorAt(1, QColor(66, 76, 90))
    p.setBrush(body)
    p.setPen(QPen(QColor(22, 28, 36), max(0.8, s * 0.45)))
    p.drawPolygon(QPolygonF([
        QPointF(-4.8 * s, 0), QPointF(4.8 * s, 0),
        QPointF(4.0 * s, -5.2 * s), QPointF(0.8 * s, -6.0 * s),
        QPointF(-0.8 * s, -6.0 * s), QPointF(-4.0 * s, -5.2 * s)]))
    # 顶盖
    p.setBrush(QColor(88, 100, 114))
    p.drawRect(int(-3.6 * s), int(-5.8 * s), int(7.2 * s), int(1.1 * s))
    # 炮尾鼓包
    p.setBrush(QColor(72, 82, 96))
    p.drawEllipse(QPointF(0, -3.2 * s), 2.0 * s, 1.4 * s)
    # 三联装炮管
    p.setPen(QPen(QColor(36, 44, 54), max(1.0, s * 0.7)))
    for dy in (-1.6, 0.0, 1.6):
        # 炮管身
        p.setPen(QPen(QColor(44, 54, 66), max(1.1, s * 0.8)))
        p.drawLine(QPointF(0, dy * s),
                   QPointF(13.5 * s, dy * s * 1.03))
        # 高光
        p.setPen(QPen(QColor(110, 130, 152, 180), max(0.35, s * 0.25)))
        p.drawLine(QPointF(0.5 * s, (dy - 0.35) * s),
                   QPointF(12.5 * s, (dy * 1.03 - 0.35) * s))
        # 炮口箍
        p.setPen(QPen(QColor(30, 38, 48), max(1.5, s * 1.0)))
        p.drawLine(QPointF(12.8 * s, dy * s * 1.03),
                   QPointF(13.8 * s, dy * s * 1.035))
    p.restore()


def _turret_secondary(p, x, y, s, ang):
    """双联装副炮塔。"""
    p.save()
    p.translate(x, y)
    p.rotate(ang)
    p.setBrush(QColor(92, 102, 116))
    p.setPen(QPen(QColor(30, 36, 44), max(0.7, s * 0.4)))
    p.drawPolygon(QPolygonF([
        QPointF(-3.2 * s, 0), QPointF(3.2 * s, 0),
        QPointF(2.6 * s, -2.8 * s), QPointF(-2.6 * s, -2.8 * s)]))
    p.setPen(QPen(QColor(42, 50, 60), max(0.9, s * 0.6)))
    for dy in (-0.9, 0.9):
        p.drawLine(QPointF(0, dy * s), QPointF(8.5 * s, dy * s * 1.02))
    p.restore()


def _wake_foam(p, X, Y, L, s, t):
    """水线附近的碎浪与航行尾迹。"""
    p.setPen(QPen(Qt.NoPen))
    base_a = int(90 + 40 * math.sin(t * 1.5))
    # 舰艏浪
    c = QColor(200, 230, 245, base_a)
    p.setBrush(c)
    for i in range(8):
        f = (i / 7.0)
        px = X(0.98) + f * 14 * s
        py = Y(-1.0) + math.sin(t * 3 + i) * 1.2 * s
        r = (2.0 + 3.5 * (1 - f)) * s
        p.drawEllipse(QPointF(px, py), r, r * 0.55)
    # 舰艉尾流
    c2 = QColor(185, 220, 240, int(base_a * 0.7))
    p.setBrush(c2)
    for i in range(10):
        f = (i / 9.0)
        px = X(0.00) - f * 22 * s
        py = Y(-1.5) + math.sin(t * 2.5 + i * 0.8) * (1.0 + f * 2) * s
        r = (1.5 + 2.5 * (1 - f)) * s
        p.drawEllipse(QPointF(px, py), r * (1 + f * 0.6), r * 0.5)
    # 水线泡沫带
    c3 = QColor(215, 240, 250, int(base_a * 0.35))
    p.setBrush(c3)
    for i in range(24):
        u = 0.04 + i * 0.038
        px = X(u) + math.sin(t * 2 + i * 1.3) * 1.5 * s
        py = Y(-0.8) + math.cos(t * 2.7 + i) * 0.6 * s
        r = (0.6 + 0.9 * abs(math.sin(i * 2.1))) * s
        p.drawEllipse(QPointF(px, py), r, r * 0.45)


def _oil_slick(p, x, y, s, k, t):
    """沉没时水面扩散的油膜。"""
    p.setPen(QPen(Qt.NoPen))
    n = int(5 + 6 * k)
    for i in range(n):
        f = (i / max(1, n - 1)) * k
        r = (8 + 50 * f) * s
        c = QColor(30, 32, 36, int(120 * (1 - f)))
        p.setBrush(c)
        px = x + math.sin(t * 0.3 + i * 2.1) * 6 * s * f
        py = y + math.cos(t * 0.25 + i * 1.7) * 2 * s * f
        p.drawEllipse(QPointF(px, py), r, r * 0.5)


def _smoke(p, x, y, s, k, t):
    n = int(7 + 10 * min(1.0, k))
    for i in range(n):
        f = (t * 0.30 + i * 0.37) % 1.0
        r = (5 + 30 * f) * s * (0.7 + 0.3 * min(1.0, k))
        c = QColor(38, 38, 42, int(130 * (1.0 - f) * min(1.0, k)))
        p.setPen(QPen(Qt.NoPen))
        p.setBrush(c)
        drift = math.sin(i * 2.1 + t * 0.6) * 10 * s * f
        rise = f * 65 * s
        p.drawEllipse(QPointF(x + drift, y - rise), r, r * 0.82)


def _fire(p, x, y, s, t):
    for i in range(6):
        f = (t * 2.4 + i * 0.35) % 1.0
        c = QColor(255, int(110 + 110 * f), 20, int(210 * (1.0 - f)))
        p.setPen(QPen(Qt.NoPen))
        p.setBrush(c)
        r = (2.5 + 8.5 * f) * s
        seed = i * 7 + int(t * 8)
        rx = random.Random(seed).uniform(-7, 7) * s
        p.drawEllipse(QPointF(x + rx, y - f * 20 * s), r, r * 1.35)


def _big_fire(p, x, y, s, t):
    """爆炸级大火：多层火球。"""
    core = QRadialGradient(QPointF(x, y), 22 * s)
    core.setColorAt(0, QColor(255, 255, 230, 230))
    core.setColorAt(0.35, QColor(255, 160, 40, 200))
    core.setColorAt(0.75, QColor(230, 60, 20, 120))
    core.setColorAt(1, QColor(80, 20, 10, 0))
    p.setPen(QPen(Qt.NoPen))
    p.setBrush(core)
    p.drawEllipse(QPointF(x, y), 22 * s, 18 * s)
    for i in range(8):
        f = (t * 3.0 + i * 0.25) % 1.0
        c = QColor(255, int(90 + 120 * f), 15, int(190 * (1.0 - f)))
        p.setBrush(c)
        r = (4 + 14 * f) * s
        px = x + math.sin(i * 2.7 + t * 2.0) * 10 * s * f
        py = y - f * 28 * s
        p.drawEllipse(QPointF(px, py), r, r * 1.5)


def _sparks(p, x, y, s, t):
    """飞溅的火星/碎片。"""
    p.setPen(QPen(Qt.NoPen))
    for i in range(12):
        f = (t * 1.8 + i * 0.31) % 1.0
        ang = i * 0.92 + math.sin(t + i) * 0.5
        dist = 6 + 22 * f
        px = x + math.cos(ang) * dist * s
        py = y - 8 * f * s + math.sin(ang) * dist * s * 0.4
        c = QColor(255, int(180 + 60 * f), 40, int(220 * (1.0 - f)))
        p.setBrush(c)
        r = (0.8 + 1.6 * (1.0 - f)) * s
        p.drawEllipse(QPointF(px, py), r, r)


def _debris(p, x, y, s, t):
    """舰体残骸/碎片漂浮。"""
    p.setPen(QPen(Qt.NoPen))
    p.setBrush(QColor(60, 64, 72, 180))
    for i in range(10):
        seed = i * 13 + int(t * 5)
        rng = random.Random(seed)
        dx = rng.uniform(-35, 35) * s
        dy = rng.uniform(-18, 18) * s
        r = rng.uniform(1.0, 2.8) * s
        p.drawEllipse(QPointF(x + dx, y + dy), r, r * 0.7)
