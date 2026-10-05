"""弹道物理 —— 飞行时间 / 重力下垂 / 提前量（纯函数，可单测）。

近似模型：远射程舰炮弹速高、万米内衰减很小，飞行时间按接近初速
的匀速估算；垂直下垂 drop = ½·g·t²·DROP_SCALE，DROP_SCALE 为平直弹道
调校系数（玩法需要比裸自由落体更直的弹道，仍保留重力随距离
二次增长的特征）；不考虑风力与科氏偏移。
"""

import math

from .weapons import G

DRAG_FACTOR = 1.0        # 平均速度/初速：大口径远射程舰炮万米内衰减很小
DROP_SCALE = 0.11        # 平直弹道下垂系数（再降至旧值 50%，6km 仅需抬高 ≈20moa）
KN_TO_MS = 0.514444      # 节 -> m/s


def time_of_flight(dist_m, v0):
    """炮弹飞行时间（s）；距离 0 时为 0。"""
    if dist_m <= 0:
        return 0.0
    return dist_m / (v0 * DRAG_FACTOR)


def torpedo_time(dist_m, v):
    """鱼雷直线航行时间（s）。"""
    if dist_m <= 0:
        return 0.0
    return dist_m / v


def drop_meters(t):
    """重力下垂（m，含平直弹道调校系数）。"""
    return 0.5 * G * t * t * DROP_SCALE


def drop_moa(dist_m, t):
    """以目标距离平面计，需要抬高的密位角（毫弧度）。"""
    if dist_m <= 0:
        return 0.0
    return drop_meters(t) / dist_m * 1000.0     # mrad


def lead_meters(spd_ms, t, cut_angle_rad):
    """横向提前量（m）：敌速 × 飞行时间 × 相对视线切向分量。"""
    return spd_ms * t * math.sin(cut_angle_rad)


def intercept_time(own_pos, tgt_pos, tgt_vel, torp_speed):
    """鱼雷直线拦截解算：返回 (命中用时 s, 拦截点) 或 None（追不上）。"""
    dx, dy = tgt_pos[0] - own_pos[0], tgt_pos[1] - own_pos[1]
    vx, vy = tgt_vel
    a = vx * vx + vy * vy - torp_speed * torp_speed
    b = 2.0 * (dx * vx + dy * vy)
    c = dx * dx + dy * dy
    if abs(a) < 1e-9:
        if abs(b) < 1e-9:
            return None
        t = -c / b
    else:
        disc = b * b - 4 * a * c
        if disc < 0:
            return None
        sq = math.sqrt(disc)
        cand = [x for x in ((-b + sq) / (2 * a), (-b - sq) / (2 * a))
                if x > 0]
        if not cand:
            return None
        t = min(cand)
    if t <= 0:
        return None
    return t, (own_pos[0] + dx + vx * t, own_pos[1] + dy + vy * t)
