# -*- coding: utf-8 -*-
"""地图加载共享层：把经纬度地理数据投影成“世界海里坐标”图形，并提供后台拉取线程。
雷达控制台与目标测试台共用。主数据源 Natural Earth（本地秒开），缺数据时回退 Overpass/OSM。
"""
from PyQt5.QtCore import QThread, pyqtSignal

import nr_data
import osm_fetch
import osm_geo


def to_world_shapes(geo, region):
    """geo(osm_fetch 输出) + region(GeoRegion) -> 世界坐标图形集合。
    返回 {'coast':[[ (x,y)... ]], 'land':[...], 'water':[...],
          'places':[(name, x, y)...]}
    """
    if geo is None:
        return None

    def conv(lines):
        out = []
        for ln in lines:
            pts = [region.to_world(lon, lat) for lon, lat in ln]
            out.append(pts)
        return out

    shapes = {
        "coast": conv(geo.get("coast", [])),
        "land": conv(geo.get("land", [])),
        "water": conv(geo.get("water", [])),
        "places": [],
    }
    for p in geo.get("places", []):
        x, y = region.to_world(p["lon"], p["lat"])
        shapes["places"].append((p["name"], x, y))
    return shapes


class MapThread(QThread):
    """后台加载指定经纬度范围，完成发射 done(bbox, geo|None)。"""
    done = pyqtSignal(object, object)

    def __init__(self, bbox, parent=None):
        super().__init__(parent)
        self.bbox = bbox

    def run(self):
        if nr_data.available():
            geo = nr_data.fetch_bbox(self.bbox)   # 本地 Natural Earth，毫秒~秒级
        else:
            geo = osm_fetch.fetch_region(self.bbox)  # 回退 Overpass/OSM（慢）
        self.done.emit(self.bbox, geo)


class SearchThread(QThread):
    """后台地名检索，完成发射 done(query, [(lat,lon,name), ...])。"""
    done = pyqtSignal(str, object)

    def __init__(self, query, parent=None):
        super().__init__(parent)
        self.query = query

    def run(self):
        self.done.emit(self.query, osm_fetch.geocode(self.query))
