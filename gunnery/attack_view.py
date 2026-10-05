"""攻击模块场景 —— 第一人称主炮射击对决。

玩法：鼠标移动调整主炮方位/俯仰（考虑重力下垂，需抬高补偿），
滚轮缩放瞄准镜，1/2/3 选弹，空格发射当前弹种（主炮一轮齐射 3 发，
鱼雷无需俯仰直发），F 键随时补射鱼雷；
W/A/S/D 真实操船（改变本船航速/航向并推进世界坐标）。

本场景与雷达控制台共享一份可变 state（本船/敌舰世界坐标、双方血量）：
敌舰与雷达目标同一匀速直线规则，每帧按世界坐标重算相对几何，因此
测距/方位与雷达完全一致；本船操舵会真实写回世界，退出后可在雷达看到。
对外仅暴露 AttackView（QDialog），通过 battle_closed(str) 信号回传战报。
"""

import math
import random
import time

from PyQt5.QtCore import Qt, QTimer, QPointF, QRectF, pyqtSignal
from PyQt5.QtGui import (QColor, QFont, QFontMetrics, QLinearGradient,
                         QPainter, QPainterPath, QPen, QRadialGradient)
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton

from . import sfx
from .ballistics import (KN_TO_MS, drop_meters, drop_moa, time_of_flight,
                         torpedo_time)
from .enemy import SHIP_LEN, EnemyShip, draw_warship
from .weapons import (AP_DAMAGE, HE_DAMAGE, HE_NEARMISS, SALVO, TORP_DAMAGE,
                      WEAPON_ORDER, WEAPONS)


def _norm(a):
    """角度归一化到 [-π, π]。"""
    return (a + math.pi) % (2 * math.pi) - math.pi

FONT_HUD = QFont("Consolas", 10)
FONT_BIG = QFont("Consolas", 16)
for _f in (FONT_HUD, FONT_BIG):
    try:
        _f.setFamilies(["Consolas", "Microsoft YaHei", "SimSun"])
    except AttributeError:
        pass

MS_TO_MOA = 1000.0 / 60.0          # 毫弧度 -> 密位(moa) 显示
RAD_TO_MOA = 180.0 * 60.0 / math.pi   # 弧度 -> 角分(moa)


class AttackView(QDialog):
    """主炮对决全屏场景。"""
    battle_closed = pyqtSignal(str)          # 战报文本

    TICK = 1.0 / 30.0

    def __init__(self, state=None, parent=None):
        """state: 与雷达控制台共享的可变战斗状态（本船/敌舰世界坐标+血量）；
        为 None 时生成一份随机交战状态（单测/独立运行）。"""
        super().__init__(parent)
        self.setWindowTitle("⚔ 主炮对决 —— 攻击模块")
        self.resize(1280, 800)
        self.setMouseTracking(True)
        self.setCursor(Qt.BlankCursor)         # 炮战视野不需要鼠标指针/点击
        self.setStyleSheet("background:#04080e;")
        self._last_mouse = None
        self.keys = set()                    # 当前按住的键（WASD 连续操船）
        self._fs_done = False                # 全屏仅首次 show 时强制一次
        # 共享可变 state（每帧与退出时写回世界坐标/血量）
        if state is None:
            state = self._random_state()
        self.state = state
        self.own_world = {"x": state["own_x"], "y": state["own_y"],
                          "hdg": state["own_hdg"], "spd": state["own_spd"]}
        self.own_hp = state["own_hp"]
        self.enemy = EnemyShip(
            x=state["enemy_x"], y=state["enemy_y"],
            course=state["enemy_course"], speed=state["enemy_speed"],
            name=state["enemy_name"], hp=state["enemy_hp"])
        self._sync_relative()                # 依世界坐标填 enemy.rng/brg
        self.enemy_flash = 0.0               # 敌舰开火炮口闪光计时
        # 瞄准
        self.aim_brg = self.enemy.brg        # rad（相对本船艏向）
        self.aim_elev = 0.0                  # rad 抬高量
        self.zoom = 6.0
        # 武器
        self.weapon = "ap"
        self.last_shot = -99.0               # 主炮上次齐射时刻
        self.last_torp = -99.0               # 鱼雷上次发射时刻（与主炮装填独立）
        self.torps = []                      # 鱼雷仿真 {pos,dir,t0}
        self.shots = []                      # 飞行中炮弹 {t_land,aim...}
        self.effects = []                    # 视觉特效
        self.recoil = 0.0
        self.muzzle = 0.0
        self.shake = 0.0                   # 中弹/爆炸屏幕震动强度（0~1）
        self.over_t = 0.0                  # 战斗结束后经过时间（自动返回倒计时）
        self.hit_flash = 0.0               # 本船中弹红闪
        self.time0 = time.time()
        self.over = state.get("over")        # None|'win'|'lose'
        self._log = ["锁定真实目标：%s，方位 %+0.0f°，距离 %.1f km"
                     % (self.enemy.name, math.degrees(self.enemy.brg),
                        self.enemy.rng / 1000)]
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(int(self.TICK * 1000))
        self._closed = False               # 防止自动退出与手动退出重复触发
        self._build_exit()

    @staticmethod
    def _random_state():
        """独立运行/单测的兼容退路：构造一份随机交战 state。"""
        rng_m = random.uniform(5200, 9000)
        brg = random.uniform(-0.4, 0.4)          # 相对船首方位
        dist_nm = rng_m / 1852.0
        return {
            "own_x": 0.0, "own_y": 0.0, "own_hdg": 0.0, "own_spd": 12.0,
            "enemy_x": math.sin(brg) * dist_nm, "enemy_y": math.cos(brg) * dist_nm,
            "enemy_course": random.uniform(0, 360), "enemy_speed": random.uniform(14, 22),
            "enemy_name": "敌军舰", "own_hp": 1000.0, "enemy_hp": 1000.0,
            "over": None,
        }

    def _sync_relative(self):
        """由世界坐标重算敌舰相对本船的方位/距离（与雷达同一几何）。"""
        ow, e = self.own_world, self.enemy
        dx, dy = e.x - ow["x"], e.y - ow["y"]
        e.rng = max(300.0, math.hypot(dx, dy) * 1852.0)
        e.brg = _norm(math.atan2(dx, dy) - math.radians(ow["hdg"]))

    def _sync_state(self):
        """将世界坐标/血量回写到共享 state（供雷达读回与下次续战）。"""
        s, ow, e = self.state, self.own_world, self.enemy
        s["own_x"], s["own_y"], s["own_hdg"], s["own_spd"] = (
            ow["x"], ow["y"], ow["hdg"], ow["spd"])
        s["enemy_x"], s["enemy_y"] = e.x, e.y
        s["enemy_course"], s["enemy_speed"], s["enemy_name"] = (
            e.course, e.speed, e.name)
        s["own_hp"], s["enemy_hp"], s["over"] = self.own_hp, e.hp, self.over

    def showEvent(self, ev):
        """首次显示后强制真全屏：延到事件循环，在 exec_() 弹出后仍生效，
        盖住 Windows 任务栏。"""
        super().showEvent(ev)
        if not self._fs_done:
            self._fs_done = True
            QTimer.singleShot(0, self.showFullScreen)

    # ---------------- UI 辅助 ----------------
    def _build_exit(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.btn_exit = QPushButton("↩ 撤离战场 (Esc)")
        self.btn_exit.setCursor(Qt.PointingHandCursor)
        # 不参与键盘焦点：否则空格/回车会触发按钮导致误退出
        self.btn_exit.setFocusPolicy(Qt.NoFocus)
        self.btn_exit.setAutoDefault(False)
        self.btn_exit.setDefault(False)
        self.btn_exit.setStyleSheet(
            "QPushButton{background:#13202b;color:#8fd8c8;border:1px solid"
            "#2c5a66;border-radius:4px;padding:5px 14px;"
            "font:10pt 'Microsoft YaHei';}"
            "QPushButton:hover{background:#1c3242;}")
        self.btn_exit.clicked.connect(self._quit)
        lay.addStretch(1)
        lay.addWidget(self.btn_exit, 0, Qt.AlignRight | Qt.AlignBottom)

    def _focus_f(self):
        """焦距（像素）：决定角->像素增益与敌舰透视大小。"""
        return self.width() * 0.55 * self.zoom

    def _logadd(self, s):
        self._log.append(s)
        if len(self._log) > 5:
            self._log.pop(0)

    # ---------------- 输入 ----------------
    def mouseMoveEvent(self, ev):
        # 增益 2.2×：视场转动快于鼠标位移，消除“不跟手”的拖沓感
        if self._last_mouse is not None and not self.over:
            dx = ev.pos().x() - self._last_mouse.x()
            dy = ev.pos().y() - self._last_mouse.y()
            f = self._focus_f()
            self.aim_brg = min(1.2, max(-1.2, self.aim_brg + dx * 2.2 / f))
            self.aim_elev = min(0.15, max(0.0,
                                          self.aim_elev - dy * 2.2 / f))
        self._last_mouse = ev.pos()

    def wheelEvent(self, ev):
        self.zoom = min(14.0, max(1.5,
                                  self.zoom * (1.25 if ev.angleDelta().y() > 0
                                               else 0.8)))

    def keyPressEvent(self, ev):
        k = ev.key()
        self.keys.add(k)                       # WASD 连续操船靠此集合驱动
        if k == Qt.Key_Escape:
            self._quit()
            return
        if self.over:
            if k in (Qt.Key_Enter, Qt.Key_Return):
                self._quit()
            return
        if k == Qt.Key_1:
            self.weapon = "ap"
        elif k == Qt.Key_2:
            self.weapon = "he"
        elif k == Qt.Key_3:
            self.weapon = "torp"
        elif k == Qt.Key_Space:
            self._fire()                      # 空格发射当前选中的武器
        elif k == Qt.Key_F:
            self._fire_torpedo()
        elif k == Qt.Key_Up:
            self.aim_elev = min(0.15, self.aim_elev + 0.0004)
        elif k == Qt.Key_Down:
            self.aim_elev = max(0.0, self.aim_elev - 0.0004)
        elif k == Qt.Key_Left:
            self.aim_brg -= 0.0006
        elif k == Qt.Key_Right:
            self.aim_brg += 0.0006

    def keyReleaseEvent(self, ev):
        self.keys.discard(ev.key())

    # ---------------- 本船操舵航向 ----------------
    def _apply_helm(self, dt):
        """W/S 加减速、A/D 左右舵（与雷达控制台同手感），直接作用于
        本船世界航向/航速并推进世界坐标；相对敌舰几何由 _sync_relative
        据此重算——因此操舵是真实机动（写回世界），而非仅特效。"""
        k, ow = self.keys, self.own_world
        if Qt.Key_A in k:
            ow["hdg"] = (ow["hdg"] - 25.0 * dt) % 360.0   # 左舵
        if Qt.Key_D in k:
            ow["hdg"] = (ow["hdg"] + 25.0 * dt) % 360.0   # 右舵
        if Qt.Key_W in k:
            ow["spd"] = min(40.0, ow["spd"] + 8.0 * dt)
        if Qt.Key_S in k:
            ow["spd"] = max(0.0, ow["spd"] - 10.0 * dt)
        h = math.radians(ow["hdg"])
        ow["x"] += math.sin(h) * ow["spd"] * dt / 3600.0
        ow["y"] += math.cos(h) * ow["spd"] * dt / 3600.0

    # ---------------- 射击 ----------------
    def _reload_left(self, key=None):
        w = WEAPONS[key or self.weapon]
        t0 = self.last_torp if w.kind == "torpedo" else self.last_shot
        return max(0.0, w.reload - (time.time() - t0))

    def _fire(self):
        """空格统一扳机：按当前弹药分发——主炮齐射，鱼雷直发（无需俯仰）。"""
        if WEAPONS[self.weapon].kind == "torpedo":
            self._fire_torpedo()
        else:
            self._fire_main()

    def _fire_main(self):
        w = WEAPONS[self.weapon]
        if w.kind != "shell" or self._reload_left() > 0:
            return
        if self.enemy.rng > w.rng_max:
            self._logadd("⚠ 超出 %s 有效射程 %.0f km" % (w.name,
                                                         w.rng_max / 1000))
            return
        now = time.time()
        tof = time_of_flight(self.enemy.rng, w.speed)
        for i in range(SALVO):                      # 一轮齐射 SALVO 发，扇形散布
            off = i - (SALVO - 1) / 2.0             # -1 0 +1：左/中/右三弹可见分离
            self.shots.append({"w": self.weapon, "t0": now, "tof": tof,
                               "brg": self.aim_brg + off * 0.0018,
                               "elev": self.aim_elev + off * 0.0006})
        self.last_shot = now
        self.recoil = 1.0
        self.muzzle = 0.22
        sfx.fire()
        self._logadd("%s 齐射 ×%d！" % (w.name, SALVO))

    def _fire_torpedo(self):
        w = WEAPONS["torp"]
        if self._reload_left("torp") > 0:           # 鱼雷用自身装填时间，与主炮无关
            return
        if self.enemy.rng > w.rng_max:
            self._logadd("⚠ 超出鱼雷射程 %.0f km" % (w.rng_max / 1000))
            return
        # 以发射瞬间的绝对方位角（本船航向+瞄准相对方位）在世界系惯性直行，
        # 之后本船操舵不影响已发出的鱼雷
        crs = math.radians(self.own_world["hdg"]) + self.aim_brg
        self.torps.append({"x": self.own_world["x"] * 1852.0,
                           "y": self.own_world["y"] * 1852.0,
                           "vx": math.sin(crs) * w.speed,
                           "vy": math.cos(crs) * w.speed, "d": 0.0})
        self.last_torp = time.time()
        sfx.torp()
        self._logadd("鱼雷射出！航速 %.0f 节，预计航行 %.0f 秒"
                     % (w.speed / KN_TO_MS,
                        torpedo_time(self.enemy.rng, w.speed)))

    def _sfx_flash(self):
        pass

    # ---------------- 主循环 ----------------
    def _tick(self):
        now = time.time()
        dt = self.TICK
        self.shake = max(0.0, self.shake - dt * 2.2)
        self.hit_flash = max(0.0, self.hit_flash - dt * 1.6)
        if self.over:
            # 战斗结束：爆炸/沉没动画继续，3.5 秒后自动返回雷达控制台
            self._effects_update(dt)
            if self.enemy.hp <= 0:
                self.enemy.sinking += dt
            self.over_t += dt
            if self.over_t >= 3.5:
                self._quit()
                return
            self.update()
            return
        # 本船操船（WASD）：更新航速/航向并推进世界坐标
        self._apply_helm(dt)
        # 敌舰按与雷达目标同一规则直线推进，随后重算相对几何
        self.enemy.integrate(dt)
        self._sync_relative()
        # 敌舰还击计时
        fired = self.enemy.step_fire(dt)
        if fired and self.enemy.hp > 0:
            self.enemy_flash = 0.3
            self._enemy_shoot()
        # 炮弹落点判定
        for sh in [s for s in self.shots if now - s["t0"] >= s["tof"]]:
            self.shots.remove(sh)
            self._resolve_shell(sh, now)
        # 鱼雷推进
        self._torp_step(dt)
        self._effects_update(dt)
        self.recoil = max(0.0, self.recoil - dt * 4)
        self.muzzle = max(0.0, self.muzzle - dt)
        self.enemy_flash = max(0.0, self.enemy_flash - dt)
        # 胜负：敌舰血量归零→沉没动画满 2.6s 后判胜；本船归零判负
        # （旧 bug：sinking 被每帧赋值重置，永远达不到阈值，导致无法胜利返回）
        if self.enemy.hp <= 0:
            self.enemy.sinking += dt
            if self.over is None and self.enemy.sinking > 2.6:
                self.over = "win"
                self.over_t = 0.0
                sfx.sink()
        elif self.own_hp <= 0 and self.over is None:
            self.over = "lose"
            self.over_t = 0.0
            sfx.sink()
        self._sync_state()
        self.update()

    def _enemy_shoot(self):
        """敌舰还击：命中率 10~30%（随距离），命中伤害 5~100（偏低端）。"""
        hit = random.random() < self.enemy.hit_rate()
        flight = 2.2 + self.enemy.rng / 8000.0          # 视觉弹道飞行秒数
        self.effects.append({"type": "incoming", "t0": time.time(),
                             "dur": flight, "hit": hit})
        sfx.incoming()

    def _enemy_hit_landed(self, eff):
        roll = random.random()
        if roll < 0.6:
            dmg = random.uniform(5, 30)
        elif roll < 0.9:
            dmg = random.uniform(30, 60)
        else:
            dmg = random.uniform(60, 100)
        self.own_hp = max(0.0, self.own_hp - dmg)
        self.hit_flash = 1.0
        self.shake = min(1.0, 0.3 + dmg / 90.0)     # 中弹震动与伤害成正比
        # 本船舷侧起火爆炸特效（屏幕空间位置，重创更大更响）
        self.effects.append({"type": "own_hit", "t0": time.time(),
                             "dur": 1.5, "fx": random.uniform(-0.30, 0.30),
                             "fy": random.uniform(0.76, 0.93),
                             "big": dmg >= 60})
        if dmg >= 60:
            sfx.explode()
        else:
            sfx.hit()
        self._logadd("⚠ 被敌弹命中！本船损伤 -%d（余 %.0f）" % (dmg,
                                                                self.own_hp))

    def _resolve_shell(self, sh, now):
        """炮弹落地：横向看方位差 + 提前量，纵向看抬高与重力下垂是否补偿。"""
        w = WEAPONS[sh["w"]]
        t = sh["tof"]
        drop = drop_meters(t)
        rng = self.enemy.rng
        # 敌舰在弹丸飞行期间的预测位置（用当前实测，玩家需自行量敌舰航速）
        lat_m = (sh["brg"] - self.enemy.brg) * rng
        vert_m = sh["elev"] * rng - drop          # 抬高补偿量 - 下垂
        miss = math.hypot(lat_m, vert_m - 8.0)    # 瞄准舰体中部高度 8m
        if w.splash and miss > w.splash:
            self._splash(sh, lat_m, vert_m)
            return
        if abs(lat_m) > SHIP_LEN / 2.0 or not (-6.0 <= vert_m <= 36.0):
            if w.splash and miss <= w.splash * 2.2:
                dmg = random.uniform(*HE_NEARMISS)
                self._hit_fx("近失", dmg, lat_m, vert_m)
            else:
                self._splash(sh, lat_m, vert_m)
            return
        u = 0.5 + lat_m / SHIP_LEN                # 沿舰长 0艉~1
        zone = EnemyShip.zone_of(u, vert_m) or "艏艉"
        table = AP_DAMAGE if sh["w"] == "ap" else HE_DAMAGE
        lo, hi = table[zone]
        if sh["w"] == "ap" and zone == "水线舰体" and random.random() < 0.3:
            zone, (lo, hi) = "弹药库", AP_DAMAGE["弹药库"]
        dmg = random.uniform(lo, hi)
        self._hit_fx(zone, dmg, lat_m, vert_m)

    def _hit_fx(self, zone, dmg, lat_m, vert_m):
        self.enemy.hp = max(0.0, self.enemy.hp - dmg)
        self.effects.append({"type": "hit", "t0": time.time(), "dur": 1.6,
                             "lat": lat_m, "vert": vert_m,
                             "text": "%s 命中 -%d" % (zone, dmg)})
        sfx.hit() if dmg < 120 else sfx.explode()   # 重创命中改响爆炸（winsound 单通道不叠加）
        self._logadd("命中 %s！伤害 -%d（敌舰余 %.0f）" % (zone, dmg,
                                                          self.enemy.hp))

    def _splash(self, sh, lat_m, vert_m):
        self.effects.append({"type": "splash", "t0": time.time(), "dur": 2.0,
                             "lat": lat_m, "vert": vert_m,
                             "text": "落点 远%.0fm" %
                                     (abs(vert_m - 8) if vert_m < 8
                                      else abs(lat_m))})
        sfx.splash()

    def _torp_step(self, dt):
        """鱼雷世界坐标直线推进；命中判定用与敌舰的世界距离。"""
        v = WEAPONS["torp"].speed
        e = self.enemy
        ex, ey = e.x * 1852.0, e.y * 1852.0
        for tp in list(self.torps):
            tp["x"] += tp["vx"] * dt
            tp["y"] += tp["vy"] * dt
            tp["d"] += v * dt
            if math.hypot(tp["x"] - ex, tp["y"] - ey) < 55.0:
                self.torps.remove(tp)
                self.enemy.hp = max(0.0, self.enemy.hp - TORP_DAMAGE)
                self.effects.append({"type": "torp_explosion",
                                     "t0": time.time(), "dur": 2.4,
                                     "lat": 0, "vert": 0,
                                     "text": "鱼雷命中 -400"})
                self.shake = max(self.shake, 0.35)   # 爆炸震动传及本船
                sfx.explode()
                self._logadd("★ 鱼雷命中敌舰水线！-400（余 %.0f）"
                             % self.enemy.hp)
            elif tp["d"] > WEAPONS["torp"].rng_max:
                self.torps.remove(tp)
                self._logadd("鱼雷航程耗尽，未命中")

    def _effects_update(self, dt):
        now = time.time()
        for eff in list(self.effects):
            if eff["type"] == "incoming" and \
                    now - eff["t0"] >= eff["dur"]:
                self.effects.remove(eff)
                if eff["hit"]:
                    self._enemy_hit_landed(eff)
                else:
                    self.effects.append({"type": "own_splash",
                                         "t0": now, "dur": 1.6})
                    self._logadd("敌弹近失，水柱落在本船附近")
        self.effects = [e for e in self.effects
                        if now - e["t0"] < e["dur"]]

    # ---------------- 绘制 ----------------
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.shake > 0:                  # 中弹震动：短暂随机平移整个画面
            p.translate(random.uniform(-9, 9) * self.shake,
                        random.uniform(-7, 7) * self.shake)
        w, h = self.width(), self.height()
        f = self._focus_f()
        cx, horizon = w / 2.0, h * 0.52
        # 抬高炮口 → 视线向上 → 海面/敌舰在视场内整体下沉（符号修正）
        vh = horizon + self.aim_elev * f
        self._paint_seascape(p, w, h, cx, vh)
        self._paint_enemy(p, cx, horizon, f)
        self._paint_shots(p, cx, horizon, f, w, h)
        self._paint_torps(p, cx, horizon, f, w, h)
        self._paint_effects(p, cx, horizon, f, w, h)
        self._paint_gun(p, w, h, cx)
        self._paint_reticle(p, cx, horizon, f, w, h)
        self._paint_hud(p, w, h)
        if self.hit_flash > 0:
            p.fillRect(self.rect().adjusted(-20, -20, 20, 20),
                       QColor(255, 40, 30, int(140 * min(1.0,
                                                          self.hit_flash))))
        if self.over:
            self._paint_over(p, w, h)
        p.end()

    def _paint_seascape(self, p, w, h, cx, horizon):
        sky = QLinearGradient(0, 0, 0, horizon)
        sky.setColorAt(0, QColor(8, 16, 34))
        sky.setColorAt(0.75, QColor(38, 52, 74))
        sky.setColorAt(1, QColor(92, 84, 76))
        p.fillRect(QRectF(0, 0, w, horizon), sky)
        sea = QLinearGradient(0, horizon, 0, h)
        sea.setColorAt(0, QColor(24, 48, 66))
        sea.setColorAt(0.4, QColor(10, 30, 48))
        sea.setColorAt(1, QColor(4, 12, 24))
        p.fillRect(QRectF(0, horizon, w, h - horizon), sea)
        p.setPen(QPen(QColor(150, 190, 210, 90), 1))
        p.drawLine(QPointF(0, horizon), QPointF(w, horizon))
        t = time.time() - self.time0
        for i in range(7):                        # 海面波纹高光
            yy = horizon + (i + 1) ** 1.8 / 7 ** 1.8 * (h - horizon) * 0.9
            ph = math.sin(t * 0.8 + i * 1.7) * 60
            p.setPen(QPen(QColor(120, 180, 200, 16 + i), 1))
            for x0 in range(-100 + int(ph) % 200, w, 200):
                p.drawLine(QPointF(x0, yy), QPointF(x0 + 60 + i * 14, yy))

    def _enemy_screen(self, cx, horizon, f):
        """敌舰水线屏幕坐标 + 每米像素。船始终贴在海面上：
        水线位于视线的俯仰 0 线（vh）下方 atan(枪高/距离) 处。"""
        e = self.enemy
        s = f / e.rng
        ex = cx + (e.brg - self.aim_brg) * f
        ey = horizon + self.aim_elev * f + 10.0 * s   # 炮口高约 10m 带来的俯角
        return ex, ey, s

    def _paint_enemy(self, p, cx, horizon, f):
        e = self.enemy
        if e.rng > 40000:
            return
        ex, ey, s = self._enemy_screen(cx, horizon, f)
        s = min(s, 6.0)
        if not (-200 < ex < self.width() + 200):
            return
        p.save()
        p.translate(ex, ey)
        if e.brg < 0:                             # 左舷目标：镜像呈艉向
            p.scale(-1, 1)
        p.translate(-ex, -ey)
        draw_warship(p, ex, ey, s, max(0.0, e.hp) / 1000.0,
                     time.time() - self.time0)
        p.restore()
        if self.enemy_flash > 0:                  # 敌舰炮口闪光
            fr = self.enemy_flash / 0.3
            g = QRadialGradient(QPointF(ex - SHIP_LEN * s * 0.3, ey - 12 * s),
                                14 * s * fr + 4)
            g.setColorAt(0, QColor(255, 240, 180, int(230 * fr)))
            g.setColorAt(1, QColor(255, 160, 40, 0))
            p.setPen(QPen(Qt.NoPen))
            p.setBrush(g)
            p.drawEllipse(QPointF(ex - SHIP_LEN * s * 0.3, ey - 12 * s),
                          14 * s + 4, 14 * s + 4)

    def _paint_torps(self, p, cx, horizon, f, w, h):
        """鱼雷航迹：世界系惯性直线换算为相对本船方位后绘制，
        本船转向时已发出的鱼雷保持自己的航迹而不跟着扫。"""
        ow = self.own_world
        ox, oy = ow["x"] * 1852.0, ow["y"] * 1852.0
        hd = math.radians(ow["hdg"])
        for tp in self.torps:
            rel = _norm(math.atan2(tp["x"] - ox, tp["y"] - oy) - hd)
            k = min(1.0, tp["d"] / 4000.0)
            x = cx + (rel - self.aim_brg) * f * (1 - k * 0.55)
            y = h - (h - horizon) * k * 0.92
            p.setPen(QPen(QColor(200, 230, 255, 130), 1))
            p.drawLine(QPointF(x, y + 16), QPointF(x, y))
            p.setPen(QPen(QColor(230, 245, 255, 200), 2.5))
            p.drawPoint(QPointF(x, y))
            p.setPen(QPen(QColor(160, 200, 220, 60), 1))
            p.drawEllipse(QPointF(x, y), 5 + 3 * math.sin(time.time() * 6),
                          3)

    def _paint_effects(self, p, cx, horizon, f, w, h):
        now = time.time()
        ex, ey, s = self._enemy_screen(cx, horizon, f)
        for e in self.effects:
            k = (now - e["t0"]) / e["dur"]
            if e["type"] in ("splash", "hit"):
                px = ex - e["lat"] * s
                py = ey - e["vert"] * s
                if e["type"] == "splash":
                    self._water_column(p, px, min(py, ey + 60),
                                       10 + 26 * k, QColor(190, 225, 245),
                                       1.0 - k)
                else:
                    self._explosion(p, px, py, k, scale=1.0)
                if e.get("text"):
                    self._popup(p, px, py - 34 - 26 * k, e["text"],
                                QColor(255, 120, 90) if e["type"] == "hit"
                                else QColor(150, 200, 220), 1.0 - k)
            elif e["type"] == "torp_explosion":     # 鱼雷命中：巨型爆炸
                px = ex
                py = ey - 6 * s
                sc = max(1.2, min(3.2, 10000.0 / max(self.enemy.rng, 800.0)))
                self._explosion(p, px, py, k, scale=sc * 1.4)
                self._water_column(p, px, min(py, ey + 60),
                                   (40 + 90 * k) * sc, QColor(215, 238, 250),
                                   1.0 - k)
                if e.get("text"):
                    self._popup(p, px, py - 70 * sc - 30 * k, e["text"],
                                QColor(255, 170, 80), 1.0 - k)
            elif e["type"] == "own_hit":           # 本船中弹：舷侧火光
                px = cx + e["fx"] * w
                py = e["fy"] * h
                self._explosion(p, px, py, k,
                                scale=2.3 if e["big"] else 1.4)
            elif e["type"] == "incoming":          # 来袭弹：由远及近的白点
                px = ex + (cx - ex) * k * k
                py = ey + (h * 0.86 - ey) * k * k
                p.setPen(QPen(QColor(255, 250, 235, 210), 1.6 + 2 * k))
                p.drawLine(QPointF(px, py),
                           QPointF(px - (cx - ex) * 0.03 * k,
                                   py - (h * 0.86 - ey) * 0.03 * k))
            elif e["type"] == "own_splash":
                self._water_column(p, w * 0.5 + math.sin(e["t0"]) * w * 0.3,
                                   h * 0.8, 26 + 50 * k,
                                   QColor(200, 230, 250), 1.0 - k)

    def _explosion(self, p, x, y, k, scale=1.0):
        """通用分层爆炸：冲击波圈（快速扩散消失）→ 白核橙边火球 →
        上升消停黑烟。k 为 0~1 进度，scale 控制尺寸。"""
        p.setPen(QPen(Qt.NoPen))
        if k < 0.5:                             # 冲击波圈
            rr = (16 + 110 * k) * scale
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 240, 200, int(190 * (1 - k / 0.5))), 2))
            p.drawEllipse(QPointF(x, y), rr, rr * 0.6)
        if k < 0.6:                             # 火球阶段
            kf = k / 0.6
            r = (30 + 62 * kf) * scale
            g = QRadialGradient(QPointF(x, y), r)
            g.setColorAt(0, QColor(255, 255, 230, int(255 * (1 - kf))))
            g.setColorAt(0.4, QColor(255, 185, 70, int(235 * (1 - kf))))
            g.setColorAt(0.75, QColor(230, 80, 30, int(170 * (1 - kf))))
            g.setColorAt(1, QColor(80, 30, 15, 0))
            p.setPen(QPen(Qt.NoPen))
            p.setBrush(g)
            p.drawEllipse(QPointF(x, y), r, r * 0.85)
        ks = min(1.0, k / 0.9)                  # 浓烟滞留上升
        r2 = (22 + 86 * k) * scale
        g2 = QRadialGradient(QPointF(x, y - 26 * scale * ks), r2)
        g2.setColorAt(0, QColor(46, 40, 36, int(160 * (1 - ks))))
        g2.setColorAt(1, QColor(26, 22, 20, 0))
        p.setPen(QPen(Qt.NoPen))
        p.setBrush(g2)
        p.drawEllipse(QPointF(x, y - 26 * scale * ks), r2, r2 * 0.78)

    def _water_column(self, p, x, y, hgt, col, alpha):
        a = max(0, int(200 * alpha))
        grad = QLinearGradient(0, y - hgt, 0, y + hgt * 0.25)
        c1 = QColor(col)
        c1.setAlpha(a)
        c2 = QColor(col)
        c2.setAlpha(0)
        grad.setColorAt(0, c2)
        grad.setColorAt(0.5, c1)
        grad.setColorAt(1, c2)
        p.setPen(QPen(Qt.NoPen))
        p.setBrush(grad)
        path = QPainterPath()
        path.moveTo(x - hgt * 0.16, y + hgt * 0.2)
        path.quadTo(x - hgt * 0.05, y - hgt * 0.7, x, y - hgt)
        path.quadTo(x + hgt * 0.05, y - hgt * 0.7, x + hgt * 0.16,
                    y + hgt * 0.2)
        path.closeSubpath()
        p.drawPath(path)

    def _popup(self, p, x, y, text, col, alpha):
        p.setFont(FONT_HUD)
        c = QColor(col)
        c.setAlpha(int(255 * max(0.0, min(1.0, alpha))))
        p.setPen(QPen(c, 1))
        p.drawText(QRectF(x - 130, y - 14, 260, 20), Qt.AlignCenter, text)

    def _gun_geometry(self, w, h, cx):
        """本船炮几何：方位角→炮管倾角、俯仰→抬升（限幅），
        返回 (az_deg, lift, 炮口屏幕坐标) 供绘制与弹丸起点共用。"""
        az_deg = max(-38.0, min(38.0, math.degrees(self.aim_brg) * 2.4))
        lift = min(90.0, self.aim_elev * 1200.0)
        mz_x = cx + math.sin(math.radians(az_deg)) * 230.0
        mz_y = h - 26 - 250.0 - lift
        return az_deg, lift, mz_x, mz_y

    def _paint_shots(self, p, cx, horizon, f, w, h):
        """飞行中炮弹：从本船炮口飞向目标的抛物线光点 + 淡烟迹。"""
        if not self.shots:
            return
        now = time.time()
        ex, ey, s = self._enemy_screen(cx, horizon, f)
        _, _, x0, y0 = self._gun_geometry(w, h, cx)
        for sh in self.shots:
            # 每发按自身方位/俯仰散布落点——齐射三发呈可见的三条抛物线
            tx = ex - (sh["brg"] - self.enemy.brg) * f
            ty = (ey - 8.0 * s) - (sh["elev"] - self.aim_elev) * f
            k = min(1.0, (now - sh["t0"]) / sh["tof"])
            arc = min(h * 0.40, max(70.0, drop_meters(sh["tof"]) * s * 0.45))

            def pos(u, _x0=x0, _y0=y0, _tx=tx, _ty=ty, _a=arc):
                return (_x0 + (_tx - _x0) * u,
                        _y0 + (_ty - _y0) * u - math.sin(math.pi * u) * _a)
            p.setPen(QPen(Qt.NoPen))
            for j in range(1, 6):                   # 递减烟迹
                u = k - j * 0.04
                if u <= 0:
                    break
                px_, py_ = pos(u)
                p.setBrush(QColor(215, 220, 228, int(85 * (1 - j / 6.0))))
                p.drawEllipse(QPointF(px_, py_), 2.4 + j, 2.4 + j)
            px_, py_ = pos(k)
            g = QRadialGradient(QPointF(px_, py_), 8)
            g.setColorAt(0, QColor(255, 255, 225, 255))
            g.setColorAt(0.5, QColor(255, 190, 90, 180))
            g.setColorAt(1, QColor(255, 150, 40, 0))
            p.setBrush(g)
            p.drawEllipse(QPointF(px_, py_), 8, 8)

    def _paint_gun(self, p, w, h, cx):
        """屏幕下方本船主炮剪影：炮座转盘环 + 梯形缩厚装甲炮塔
        （金属渐变/顶盖/测距仪鼓包/装甲缝高光）+ 双炮身（耳轴座、
        炮尾衬套、变细炮身带高光、炮口箍）；方位→炮塔前整体左右
        旋转，俯仰→炮口上抬（限幅），另然后坐/炮口焰。"""
        base_y = h - 26
        az_deg, lift, _, _ = self._gun_geometry(w, h, cx)
        rec = self.recoil * 16
        p.setPen(QPen(Qt.NoPen))
        # 炮舰甲板：梯形剪影 + 甲板边缘线
        deck = QLinearGradient(0, base_y - 6, 0, h)
        deck.setColorAt(0, QColor(24, 36, 48))
        deck.setColorAt(1, QColor(7, 12, 20))
        path = QPainterPath()
        path.moveTo(cx - 340, h)
        path.lineTo(cx - 235, base_y - 6)
        path.lineTo(cx + 235, base_y - 6)
        path.lineTo(cx + 340, h)
        path.closeSubpath()
        p.setBrush(deck)
        p.drawPath(path)
        p.setPen(QPen(QColor(74, 96, 116, 110), 1))
        p.drawLine(QPointF(cx - 235, base_y - 6),
                   QPointF(cx + 235, base_y - 6))
        # 炮座转盘环（两层椭圆）
        p.setPen(QPen(QColor(10, 16, 24), 1))
        p.setBrush(QColor(18, 28, 40))
        p.drawEllipse(QPointF(cx, base_y - 10), 104, 15)
        p.setBrush(QColor(30, 44, 58))
        p.drawEllipse(QPointF(cx, base_y - 16), 86, 12)
        # 炮塔主体：梯形缩厚装甲，顶亮底暗金属渐变
        body = QLinearGradient(0, base_y - 110, 0, base_y - 24)
        body.setColorAt(0, QColor(58, 76, 94))
        body.setColorAt(0.55, QColor(32, 46, 62))
        body.setColorAt(1, QColor(13, 20, 30))
        t = QPainterPath()
        t.moveTo(cx - 92, base_y - 24)
        t.lineTo(cx - 74, base_y - 96)
        t.lineTo(cx + 74, base_y - 96)
        t.lineTo(cx + 92, base_y - 24)
        t.closeSubpath()
        p.setPen(QPen(QColor(8, 14, 22), 1.5))
        p.setBrush(body)
        p.drawPath(t)
        # 顶盖与测距仪鼓包
        p.setPen(QPen(QColor(10, 16, 26), 1))
        p.setBrush(QColor(64, 84, 104))
        p.drawRoundedRect(QRectF(cx - 76, base_y - 108, 152, 14), 4, 4)
        p.setBrush(QColor(46, 62, 80))
        p.drawRoundedRect(QRectF(cx - 20, base_y - 122, 40, 16), 5, 5)
        # 装甲缝高光
        p.setPen(QPen(QColor(126, 156, 182, 60), 1))
        p.drawLine(QPointF(cx - 70, base_y - 92), QPointF(cx + 70, base_y - 92))
        p.drawLine(QPointF(cx - 88, base_y - 32), QPointF(cx + 88, base_y - 32))
        # 火炮：方位整体旋转，俯仰/后坐沿纵深方向抬升
        p.save()
        p.translate(cx, base_y - 60)
        p.rotate(az_deg)
        piv_y = -40.0                                   # 耳轴轴心（局部坐标）
        tip_y = piv_y - (178.0 + lift) - rec             # 炮口：俯仰抬高+后坐
        for xb in (-27.0, 27.0):                         # 左右炮身轴
            tip_x = xb * 0.62                             # 炮口略微内收
            p.setPen(QPen(QColor(10, 16, 24), 1))
            p.setBrush(QColor(38, 52, 68))                # 耳轴座/炮盾
            p.drawRoundedRect(QRectF(xb - 14, piv_y - 14, 28, 30), 5, 5)
            p.setBrush(QColor(54, 72, 92))                # 炮尾衬套环
            p.drawRoundedRect(QRectF(xb - 9, piv_y - 24, 18, 12), 3, 3)
            # 炮身：深色底 + 上表面高光条（金属圆柱感），向纵深变细
            p.setPen(QPen(QColor(22, 32, 44), 13, Qt.SolidLine, Qt.FlatCap))
            p.drawLine(QPointF(xb, piv_y - 16), QPointF(tip_x, tip_y + 12))
            p.setPen(QPen(QColor(26, 36, 50), 10, Qt.SolidLine, Qt.FlatCap))
            p.drawLine(QPointF(tip_x, tip_y + 12), QPointF(tip_x, tip_y))
            p.setPen(QPen(QColor(88, 112, 138, 150), 3, Qt.SolidLine,
                          Qt.FlatCap))
            p.drawLine(QPointF(xb - 3.6, piv_y - 16),
                       QPointF(tip_x - 3.6, tip_y + 10))
            # 炮口箍：加厚段 + 亮端面刻线
            p.setPen(QPen(QColor(16, 26, 38), 17, Qt.SolidLine, Qt.FlatCap))
            p.drawLine(QPointF(tip_x, tip_y + 14), QPointF(tip_x, tip_y))
            p.setPen(QPen(QColor(120, 150, 178, 200), 2))
            p.drawLine(QPointF(tip_x - 8, tip_y + 2),
                       QPointF(tip_x + 8, tip_y + 2))
            if self.muzzle > 0:                           # 炮口焰
                fr = self.muzzle / 0.22
                g = QRadialGradient(QPointF(tip_x, tip_y - 6), 34 * fr + 6)
                g.setColorAt(0, QColor(255, 250, 200, int(255 * fr)))
                g.setColorAt(0.4, QColor(255, 170, 60, int(200 * fr)))
                g.setColorAt(1, QColor(255, 100, 20, 0))
                p.setPen(QPen(Qt.NoPen))
                p.setBrush(g)
                p.drawEllipse(QPointF(tip_x, tip_y - 6), 34 * fr + 6,
                              30 * fr + 5)
        p.restore()

    def _paint_reticle(self, p, cx, horizon, f, w, h):
        """瞄准镜：圆形遮罩 + 分划 + 俯仰梯 + 测距标尺 + 锁定框。"""
        rr = min(w, h) * 0.44
        # 镜外暗角
        path = QPainterPath()
        path.addRect(QRectF(0, 0, w, h))
        circ = QPainterPath()
        circ.addEllipse(QPointF(cx, horizon), rr, rr)
        p.setPen(QPen(Qt.NoPen))
        p.setBrush(QColor(2, 6, 10, 200))
        p.drawPath(path.subtracted(circ))
        col = QColor(0, 255, 160, 190)
        p.setPen(QPen(col, 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, horizon), rr, rr)
        p.setPen(QPen(QColor(0, 255, 160, 70), 1))
        p.drawEllipse(QPointF(cx, horizon), rr - 5, rr - 5)
        # 十字 + 密位刻线
        p.setPen(QPen(col, 1.4))
        gap = 14
        for sgn in (-1, 1):
            p.drawLine(QPointF(cx + sgn * gap, horizon),
                       QPointF(cx + sgn * rr * 0.42, horizon))
            p.drawLine(QPointF(cx, horizon + sgn * gap),
                       QPointF(cx, horizon + sgn * rr * 0.42))
        p.setPen(QPen(QColor(0, 255, 160, 130), 1))
        step = f * 0.001                          # 1 mrad 像素
        mult = max(1, int(math.ceil(11.0 / max(step, 1e-6))))  # 刻线≥11px 间距
        n = int(rr * 0.4 / max(step * mult, 2))
        for i in range(1, min(n, 40) + 1):
            d = i * step * mult
            tick = 4 if i % 5 else 7
            p.drawLine(QPointF(cx + d, horizon - tick),
                       QPointF(cx + d, horizon + tick))
            p.drawLine(QPointF(cx - d, horizon - tick),
                       QPointF(cx - d, horizon + tick))
        # 俯仰梯（左侧，moa）：以俯仰 0 线（随抬炮下沉）为基准，
        # 梯距自适应保证每条刻线间隔 ≥9px 不糊连
        t = QFont(FONT_HUD)
        t.setPixelSize(11)
        p.setFont(t)
        px_per_moa = f * math.pi / 10800.0      # 1 角分对应的像素高度
        moa_step = max(2.0, math.ceil(9.0 / max(px_per_moa, 1e-6) / 2.0) * 2.0)
        y0 = horizon + self.aim_elev * f        # 俯仰 0 线随场景同步下沉
        i = 0
        while y0 - i * moa_step * px_per_moa > horizon - rr * 0.4:
            yy = y0 - i * moa_step * px_per_moa
            lab = "%d" % int(i * moa_step)
            p.setPen(QPen(QColor(0, 255, 160, 160), 1))
            p.drawLine(QPointF(cx - 26, yy), QPointF(cx - 14, yy))
            p.drawText(QRectF(cx - 60, yy - 8, 30, 16), Qt.AlignRight, lab)
            i += 1
        # 当前抬高读数指针（指向俯仰 0 线上方当前视线处）
        elev_moa = self.aim_elev * RAD_TO_MOA
        py_ = y0 - elev_moa / moa_step * px_per_moa * moa_step
        p.setPen(QPen(QColor(255, 220, 90, 230), 1.6))
        p.drawLine(QPointF(cx - 30, py_), QPointF(cx - 12, py_))
        # 测距视距线（敌舰舰长 210m 夹比，卡在水线高度）
        s = f / self.enemy.rng if self.enemy.rng else 0
        _, wly, _ = self._enemy_screen(cx, horizon, f)
        half = min(rr * 0.5, SHIP_LEN * s / 2)
        p.setPen(QPen(QColor(0, 255, 160, 90), 1, Qt.DashLine))
        p.drawLine(QPointF(cx - half, wly - 6),
                   QPointF(cx - half, wly + 6))
        p.drawLine(QPointF(cx + half, wly - 6),
                   QPointF(cx + half, wly + 6))
        # 目标锁定框
        ex, ey, _ = self._enemy_screen(cx, horizon, f)
        box = SHIP_LEN * s / 2
        if box > 6 and abs(ex - cx) < rr and abs(ey - horizon) < rr:
            lock = QColor(255, 70, 70, 235) if \
                abs(ex - cx) < rr * 0.4 else QColor(0, 255, 160, 150)
            p.setPen(QPen(lock, 1.6))
            p.setBrush(Qt.NoBrush)
            for c_x, c_y, dx_, dy_ in (
                    (ex - box, ey - box * 0.22, 1, 1),
                    (ex + box, ey - box * 0.22, -1, 1),
                    (ex - box, ey + box * 0.06, 1, -1),
                    (ex + box, ey + box * 0.06, -1, -1)):
                p.drawLine(QPointF(c_x, c_y),
                           QPointF(c_x + dx_ * 10, c_y))
                p.drawLine(QPointF(c_x, c_y),
                           QPointF(c_x, c_y + dy_ * 10))
            if abs(ex - cx) < rr * 0.4:
                p.setFont(t)
                p.setPen(QPen(QColor(255, 90, 90, 230), 1))
                p.drawText(QRectF(ex + box + 6, ey - box * 0.2 - 8,
                                  160, 16), Qt.AlignLeft,
                           "◤ LOCK %s" % self.enemy.name)

    def _paint_hud(self, p, w, h):
        e = self.enemy
        now = time.time()
        tof = time_of_flight(e.rng, WEAPONS[self.weapon].speed) \
            if WEAPONS[self.weapon].kind == "shell" else \
            torpedo_time(e.rng, WEAPONS["torp"].speed)
        t = QFont(FONT_HUD)
        t.setPixelSize(13)
        p.setFont(t)
        # 左上：战术数据（鱼雷无弹道下垂，需抬高恒为 0）
        shell = WEAPONS[self.weapon].kind == "shell"
        p.setPen(QPen(QColor(120, 255, 200, 230), 1))
        lines = [
            "敌舰距离  %6.2f km" % (e.rng / 1000),
            "敌舰方位  %+6.1f°   航速 %2.0f 节" % (
                math.degrees(e.brg), e.speed),
            "弹丸飞行  %5.1f s    下垂 %5.1f m" % (tof, drop_meters(tof))
            if shell else
            "鱼雷航渡  %5.1f s    直航无下垂" % tof,
            "需抬高    %5.1f moa   当前 %5.1f" % (
                drop_moa(e.rng, tof) * 0.001 * RAD_TO_MOA,
                self.aim_elev * RAD_TO_MOA) if shell else
            "需抬高    %5.1f moa   （鱼雷无需俯仰）" % 0.0,
            "本船航速 %3.0f 节  航向 %03.0f°（WASD 操船）" % (
                self.own_world["spd"], self.own_world["hdg"] % 360.0),
        ]
        self._panel(p, 14, 14, 356, 16 + 18 * len(lines))
        for i, s in enumerate(lines):
            p.setPen(QPen(QColor(140, 235, 255, 220), 1))
            p.drawText(QRectF(26, 20 + i * 18, 336, 16), Qt.AlignLeft, s)
        # 右上：弹药面板
        x0 = w - 254
        self._panel(p, x0, 14, 240, 118)
        p.setPen(QPen(QColor(255, 220, 120, 230), 1))
        p.drawText(QRectF(x0 + 12, 20, 220, 16), Qt.AlignLeft,
                   "弹药 [1穿甲 2高爆 3鱼雷]")
        for i, key in enumerate(WEAPON_ORDER):
            wp = WEAPONS[key]
            sel = key == self.weapon
            p.setPen(QPen(QColor(0, 255, 160, 255) if sel
                          else QColor(90, 130, 150, 200), 1))
            p.drawText(QRectF(x0 + 12, 40 + i * 18, 230, 16), Qt.AlignLeft,
                       "%s %s  射程%.0fkm %s" % (
                           "▶" if sel else " ", wp.name, wp.rng_max / 1000,
                           "装填中" if self._reload_left() > 0 and sel
                           else ""))
        # 装填进度条
        rl = self._reload_left()
        wtot = WEAPONS[self.weapon].reload
        if rl > 0:
            p.setPen(QPen(Qt.NoPen))
            p.setBrush(QColor(255, 170, 60, 160))
            p.drawRect(x0 + 12, 100, int(216 * (1 - rl / wtot)), 6)
        else:
            p.setPen(QPen(QColor(0, 255, 140, 230), 1))
            p.drawText(QRectF(x0 + 12, 94, 220, 16), Qt.AlignLeft,
                       "■ 装填完成 可开火")
        # 双方血量条
        self._hpbar(p, 14, h - 66, "本船 HP", self.own_hp, QColor(0, 200, 255))
        self._hpbar(p, 14, h - 34, "敌舰 HP", e.hp, QColor(255, 90, 80))
        # 飞行中指示
        if self.shots:
            s0 = min(self.shots, key=lambda s: s["t0"] + s["tof"])
            left = s0["tof"] - (now - s0["t0"])
            p.setPen(QPen(QColor(255, 240, 140, 240), 1))
            p.setFont(FONT_BIG)
            p.drawText(QRectF(w / 2 - 120, h * 0.62, 240, 24),
                       Qt.AlignCenter, "◈ 弹丸飞行中 %.1fs" % max(0, left))
            p.setFont(t)
        # 战况日志（上移避开右下角撤离按钮）
        p.setPen(QPen(QColor(150, 190, 210, 190), 1))
        for i, s in enumerate(self._log):
            p.drawText(QRectF(w - 560, h - 78 - (len(self._log) - 1 - i) * 16,
                              546, 14), Qt.AlignRight, s)
        # 操作提示
        p.setPen(QPen(QColor(110, 150, 170, 170), 1))
        p.drawText(QRectF(14, h - 96, 520, 14), Qt.AlignLeft,
                   "鼠标=瞄准  滚轮=变焦  ↑↓←→=微调  空格=发射当前弹种  F=鱼雷  "
                   "W/S=加减速  A/D=左/右舵  Esc=撤离")

    def _panel(self, p, x, y, w, h):
        p.setPen(QPen(QColor(0, 255, 160, 60), 1))
        p.setBrush(QColor(4, 14, 20, 190))
        p.drawRoundedRect(QRectF(x, y, w, h), 6, 6)

    def _hpbar(self, p, x, y, label, val, col):
        self._panel(p, x, y, 300, 24)
        p.setPen(QPen(QColor(180, 220, 240, 220), 1))
        p.setFont(FONT_HUD)
        p.drawText(QRectF(x + 8, y + 4, 70, 16), Qt.AlignLeft, label)
        p.setPen(QPen(Qt.NoPen))
        p.setBrush(QColor(30, 44, 56))
        p.drawRect(x + 80, y + 7, 160, 10)
        p.setBrush(col)
        p.drawRect(x + 80, y + 7, int(160 * max(0.0, val) / 1000.0), 10)
        p.setPen(QPen(QColor(220, 240, 255, 220), 1))
        p.drawText(QRectF(x + 246, y + 4, 48, 16), Qt.AlignRight,
                   "%d" % max(0, val))

    def _paint_over(self, p, w, h):
        p.fillRect(self.rect(), QColor(2, 6, 12, 190))
        win = self.over == "win"
        p.setFont(FONT_BIG)
        p.setPen(QPen(QColor(0, 255, 160) if win else QColor(255, 90, 80), 1))
        p.drawText(QRectF(0, h * 0.36, w, 40), Qt.AlignCenter,
                   "★ 敌舰被击沉 ★" if win else "✚ 本船战沉 ✚")
        t = QFont(FONT_HUD)
        t.setPixelSize(14)
        p.setFont(t)
        p.setPen(QPen(QColor(190, 225, 240, 230), 1))
        msg = ("敌舰沉没！本船剩余血量 %d/1000" % self.own_hp) if win else \
              ("本船被击沉…… 剩余敌血量 %d/1000" % self.enemy.hp)
        p.drawText(QRectF(0, h * 0.36 + 48, w, 24), Qt.AlignCenter, msg)
        p.drawText(QRectF(0, h * 0.36 + 78, w, 20), Qt.AlignCenter,
                   "%.1f 秒后自动返回雷达控制台（Enter 立即返回）"
                   % max(0.0, 3.5 - self.over_t))

    # ---------------- 退出 ----------------
    def _quit(self):
        if self._closed:
            return
        self._closed = True
        self.timer.stop()
        self._sync_state()          # 世界坐标/血量回写共享 state（下次续战/雷达同步）
        if self.over == "win":
            report = "战斗胜利：击沉敌舰，本船损伤 %d%%" % \
                     int((1000 - self.own_hp) / 10)
        elif self.over == "lose":
            report = "战斗失败：本船战沉（敌舰余 %.0f HP）" % self.enemy.hp
        else:
            report = "已撤离战场（未分胜负）"
        self.battle_closed.emit(report)
        self.accept()
