"""敌舰 —— 世界坐标运动的战列舰：与雷达目标同一匀速直线规则、
≥15 秒一轮还击、10~30% 命中率（距离越近越准），以及精细的侧视军舰
建模与分部位命中判定。相对本船的方位/距离由攻击场景每帧按本船位姿回写。

纯逻辑 + QPainter 绘制，不依赖雷达控制台。
"""

import math
import random

from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import QColor, QLinearGradient, QPen, QPolygonF

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
def draw_warship(p, cx, water_y, s, hp_ratio, t=0.0):
    """以水线中点 (cx, water_y)、比例 s 像素/米 绘制战列舰侧视图。"""
    L = SHIP_LEN * s
    list_ang = 0.0
    if hp_ratio < 0.35:
        list_ang = 2.5 + 3.5 * (0.35 - hp_ratio) / 0.35   # 倾斜
    if hp_ratio < 0.15:                                    # 艂沉
        water_y += 4 * s * (0.15 - hp_ratio) / 0.15 * 3
    p.save()
    p.translate(cx, water_y)
    p.rotate(list_ang)
    x0 = -L / 2.0

    def X(u):
        return x0 + u * L

    def Y(m):
        return -m * s

    # 船体（艏缘弧线 + 艉）
    body = QLinearGradient(0, Y(12), 0, Y(-6))
    body.setColorAt(0, QColor(96, 108, 122))
    body.setColorAt(1, QColor(52, 60, 72))
    p.setBrush(body)
    p.setPen(QPen(QColor(28, 34, 42), max(1.0, s)))
    path = QPolygonF([
        QPointF(X(0.00), Y(7)),            # 艉楼后缘
        QPointF(X(0.02), Y(9)),
        QPointF(X(0.30), Y(9.5)),
        QPointF(X(0.75), Y(10)),           # 甲板舏升
        QPointF(X(0.93), Y(12)),
        QPointF(X(0.995), Y(15)),          # 艏楼
        QPointF(X(0.96), Y(2)),            # 艏缘下收
        QPointF(X(0.90), Y(-6.5)),         # 艏水线
        QPointF(X(0.06), Y(-6.5)),         # 船底
        QPointF(X(0.00), Y(-2)),
    ])
    p.drawPolygon(path)
    # 迷彩双色带
    p.setPen(QPen(Qt.NoPen))
    p.setBrush(QColor(70, 82, 96, 150))
    p.drawRect(int(X(0.05)), int(Y(9.4)), int(L * 0.5), int(3.2 * s))
    # —— 主炮塔 x3（前二超射 + 后一）——
    for u, ang in ((0.885, 0.0), (0.815, 0.0), (0.105, 180.0)):
        _turret(p, X(u), Y(10), s, ang)
    # —— 副炮 x2 ——
    for u in (0.70, 0.24):
        _turret(p, X(u), Y(10.5), s * 0.55, 0.0 if u > 0.5 else 180.0)
    # —— 舰桥塔楼（ Pagoda 式）——
    p.setBrush(QColor(84, 94, 108))
    p.setPen(QPen(QColor(30, 36, 44), max(1.0, s)))
    p.drawRect(int(X(0.615)), int(Y(16)), int(9 * s), int(6.5 * s))
    p.drawRect(int(X(0.635)), int(Y(24)), int(6.5 * s), int(8 * s))
    p.drawRect(int(X(0.652)), int(Y(30)), int(4 * s), int(6 * s))   # 罗经舰桥
    p.drawRect(int(X(0.66)), int(Y(34)), int(2.2 * s), int(4 * s))  # 射击指挥塔
    # 桅杆 + 横桁
    p.setPen(QPen(QColor(40, 46, 54), max(1.0, 0.8 * s)))
    p.drawLine(QPointF(X(0.668), Y(34)), QPointF(X(0.668), Y(44)))
    p.drawLine(QPointF(X(0.645), Y(40)), QPointF(X(0.692), Y(40)))  # 横桁
    # 烟囱 x2（外倾梯形）
    for u in (0.545, 0.42):
        p.setBrush(QColor(74, 66, 62))
        p.setPen(QPen(QColor(30, 28, 26), max(1.0, s)))
        p.drawPolygon(QPolygonF([
            QPointF(X(u), Y(20.5)), QPointF(X(u + 0.035), Y(20.5)),
            QPointF(X(u + 0.048), Y(9.5)), QPointF(X(u + 0.012), Y(9.5))]))
        p.setPen(QPen(Qt.NoPen))
        p.setBrush(QColor(20, 18, 16))
        p.drawRect(int(X(u + 0.002)), int(Y(21)), int(3.4 * s), int(1.2 * s))
    # 高炮群 + 桅杆吊杆
    p.setPen(QPen(QColor(46, 52, 60), max(1.0, 0.7 * s)))
    for u in (0.76, 0.58, 0.48, 0.32):
        p.drawLine(QPointF(X(u), Y(10.5)), QPointF(X(u), Y(12.6)))
        p.drawEllipse(QPointF(X(u), Y(13.2)), 1.1 * s, 1.1 * s)
    # 艉吊杆/起重机
    p.drawLine(QPointF(X(0.17), Y(10)), QPointF(X(0.115), Y(16)))
    # 水线红褐色带
    p.setPen(QPen(Qt.NoPen))
    p.setBrush(QColor(96, 44, 38, 200))
    p.drawRect(int(X(0.01)), int(Y(-0.8)), int(L * 0.95), int(2.0 * s))
    # —— 战损：烟雾/火焰 ——
    if hp_ratio < 0.62:
        _smoke(p, X(0.5), Y(20), s, (0.62 - hp_ratio) * 2.4, t)
    if hp_ratio < 0.38:
        _fire(p, X(0.66), Y(12), s, t)
        _smoke(p, X(0.28), Y(14), s, (0.38 - hp_ratio) * 3.0, t * 1.3)
    if hp_ratio < 0.15:
        _fire(p, X(0.35), Y(8), s * 1.4, t * 0.8)
    p.restore()


def _turret(p, x, y, s, ang):
    p.save()
    p.translate(x, y)
    p.rotate(ang)
    p.setBrush(QColor(88, 98, 112))
    p.setPen(QPen(QColor(28, 34, 42), max(1.0, s * 0.5)))
    p.drawPolygon(QPolygonF([
        QPointF(-4.2 * s, 0), QPointF(4.2 * s, 0), QPointF(3.4 * s, -3.4 * s),
        QPointF(-3.4 * s, -3.4 * s)]))
    p.setPen(QPen(QColor(40, 46, 54), max(1.2, s * 0.9)))
    for dy in (-2.2, -1.2):                             # 双联装炮管
        p.drawLine(QPointF(3.6 * s, dy * s), QPointF(12.5 * s, dy * s * 1.06))
    p.restore()


def _smoke(p, x, y, s, k, t):
    n = int(6 + 8 * min(1.0, k))
    for i in range(n):
        f = (t * 0.35 + i * 0.37) % 1.0
        r = (5 + 26 * f) * s * (0.7 + 0.3 * k)
        c = QColor(30, 30, 34, int(110 * (1.0 - f)))
        p.setPen(QPen(Qt.NoPen))
        p.setBrush(c)
        p.drawEllipse(QPointF(
            x + math.sin(i * 2.1 + t) * 8 * s, y - f * 60 * s), r, r * 0.8)


def _fire(p, x, y, s, t):
    for i in range(5):
        f = (t * 2.2 + i * 0.4) % 1.0
        c = QColor(255, int(120 + 100 * f), 30, int(200 * (1.0 - f)))
        p.setPen(QPen(Qt.NoPen))
        p.setBrush(c)
        r = (3 + 7 * f) * s
        p.drawEllipse(QPointF(
            x + random.Random(i * 7 + int(t * 8)).uniform(-6, 6) * s,
            y - f * 16 * s), r, r * 1.3)
