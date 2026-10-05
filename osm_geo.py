# -*- coding: utf-8 -*-
"""经纬度 <-> 平面海里坐标 投影工具（等距圆柱/Plate Carrée 切平面近似）。

雷达仿真内部使用“世界海里坐标”(x=东, y=北)，降生点(lat0, lon0)为原点。
小范围(海图量程)下与真实球面误差可忽略；相比 Mercator，等距圆柱投影在
大范围/世界视图下形变小、极地区不爆炸，支持把海图缩小到整张世界地图。
"""
import math

NM_M = 1852.0            # 1 海里 = 1852 米
R = 6378137.0            # WGS84 赤道半径（米）
NM_PER_DEG = 2.0 * math.pi * R / NM_M / 360.0   # 赤道处 1 度 ≈ 60.1 海里


class GeoRegion:
    """以 (lon0, lat0) 为原点的局部投影。to_world 返回 (东海里, 北海里)。"""

    def __init__(self, lon0, lat0):
        self.lon0, self.lat0 = float(lon0), float(lat0)
        self.k = math.cos(math.radians(self.lat0))   # 经度圈尺度修正
        self.dx = NM_PER_DEG * max(1e-6, self.k)     # 每度经度 -> 海里
        self.dy = NM_PER_DEG                          # 每度纬度 -> 海里

    def to_world(self, lon, lat):
        return ((lon - self.lon0) * self.dx, (lat - self.lat0) * self.dy)

    def to_ll(self, x, y):
        return (self.lon0 + x / self.dx, self.lat0 + y / self.dy)

    def bbox_of(self, x, y, half_nm):
        """以世界点为中心、半径 half_nm 海里的经纬度范围 (S, W, N, E)。"""
        lon1, lat1 = self.to_ll(x - half_nm, y - half_nm)
        lon2, lat2 = self.to_ll(x + half_nm, y + half_nm)
        return (min(lat1, lat2), min(lon1, lon2), max(lat1, lat2), max(lon1, lon2))

    def bbox_of_ll(self, lon, lat, half_nm):
        return self.bbox_of(*self.to_world(lon, lat), half_nm=half_nm)

    def latlon_text(self, x, y):
        lon, lat = self.to_ll(x, y)
        return "%0.4f%s %0.4f%s" % (
            abs(lat), "N" if lat >= 0 else "S",
            abs(lon), "E" if lon >= 0 else "W")
