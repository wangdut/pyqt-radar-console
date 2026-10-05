# -*- coding: utf-8 -*-
"""经纬度 <-> 海里平面坐标 投影工具（等角切平面近似，基于 Web Mercator/EPSG:3857）。

雷达仿真内部使用“世界海里坐标”(x=东, y=北)，降生点(lat0, lon0)为原点。
Mercator 是等角投影，切平面修正后局部角度/形状保持正确，适合海图展示。
"""
import math

NM_M = 1852.0            # 1 海里 = 1852 米
R = 6378137.0            # WGS84 赤道半径（米）


def merc_x(lon):
    return math.radians(lon) * R


def merc_y(lat):
    lat = math.radians(max(-89.999, min(89.999, lat)))
    return math.log(math.tan(math.pi / 4.0 + lat / 2.0)) * R


def inv_lon(x):
    return math.degrees(x / R)


def inv_lat(y):
    return math.degrees(2.0 * math.atan(math.exp(y / R)) - math.pi / 2.0)


class GeoRegion:
    """以 (lon0, lat0) 为原点的局部投影。to_world 返回 (东海里, 北海里)。"""

    def __init__(self, lon0, lat0):
        self.lon0, self.lat0 = lon0, lat0
        self.mx0, self.my0 = merc_x(lon0), merc_y(lat0)
        self.k = math.cos(math.radians(lat0))   # 该纬度处赤道尺度->真实尺度修正

    def to_world(self, lon, lat):
        return ((merc_x(lon) - self.mx0) * self.k / NM_M,
                (merc_y(lat) - self.my0) * self.k / NM_M)

    def to_ll(self, x, y):
        return (inv_lon(self.mx0 + x * NM_M / self.k),
                inv_lat(self.my0 + y * NM_M / self.k))

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
