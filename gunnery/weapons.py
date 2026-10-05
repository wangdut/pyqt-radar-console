"""武器数据表 —— 二战战列舰主炮/鱼雷参数与分部位伤害（纯数据，无 UI 依赖）。"""

G = 9.81  # m/s²


class Weapon:
    def __init__(self, key, name, kind, speed, rng_max, reload,
                 splash=0.0, desc=""):
        self.key = key
        self.name = name
        self.kind = kind            # "shell" | "torpedo"
        self.speed = speed          # 出膛/航速 m/s
        self.rng_max = rng_max      # 有效射程 m
        self.reload = reload        # 装填间隔 s
        self.splash = splash        # 近失有效半径 m（0=必须直击）
        self.desc = desc


# 406mm Mk7 主炮：AP/HE 初速 762 m/s，最大射程 38.9 km（取有效 35 km）
# 九三式鱼雷：49 节 ≈ 25.2 m/s，战时有效射程约 3 km
WEAPONS = {
    "ap": Weapon("ap", "穿甲弹", "shell", 762.0, 35000, 12.0, 0.0,
                 "直击装甲区毁灭性伤害，跳弹/非穿深区伤害低"),
    "he": Weapon("he", "高爆弹", "shell", 762.0, 28000, 9.0, 80.0,
                 "破片杀伤面广，适合打击上层建筑，可近失"),
    "torp": Weapon("torp", "鱼雷", "torpedo", 25.2, 3000, 25.0, 0.0,
                   "49 节航速，命中舰体水线以下造成 400 点毁灭伤害"),
}
WEAPON_ORDER = ("ap", "he", "torp")

# 伤害表：部位 -> (最小, 最大)，按落点相对敌舰轮廓判定
# —— 穿甲弹（AP）
AP_DAMAGE = {
    "弹药库": (100, 100), "主炮塔": (70, 85), "舰桥": (50, 65),
    "烟囱": (35, 50), "水线舰体": (40, 60), "上层建筑": (10, 25),
    "艏艉": (5, 15),
}
# —— 高爆弹（HE）：面广但单发偏低
HE_DAMAGE = {
    "弹药库": (35, 45), "主炮塔": (45, 60), "舰桥": (40, 55),
    "烟囱": (30, 45), "水线舰体": (25, 40), "上层建筑": (20, 35),
    "艏艉": (8, 18),
}
TORP_DAMAGE = 400          # 鱼雷一发命中
HE_NEARMISS = (5, 15)      # 高爆近失破片
