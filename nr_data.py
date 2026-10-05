# -*- coding: utf-8 -*-
"""Natural Earth 10m 矢量海陆数据源（公共领域，无需 key、无需联网）。

数据文件 nrdata/*.geojson 由 dl_natural_earth.py 一次性下载（约 38MB）。
fetch_bbox(bbox) 输出与 osm_fetch.fetch_region 同构，可直接经
osm_client.to_world_shapes 投影为世界坐标；首次调用解析加载内存（约数秒），
之后每次均为毫秒级本地计算 —— 地图"秒开"。
来源标注：Natural Earth (public domain)；地名含中文名 NAME_ZH。
"""
import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nrdata")

_land_rings = None     # [(minlon, minlat, maxlon, maxlat, pts)]
_coast_lines = None    # [(minlon, minlat, maxlon, maxlat, pts)]
_places = None         # [(lon, lat, name, pop)]


def available():
    """核心海陆数据文件是否就绪（缺文件时上层回退 Overpass）。"""
    return (os.path.exists(os.path.join(DATA_DIR, "ne_10m_land.geojson")) and
            os.path.exists(os.path.join(DATA_DIR, "ne_10m_coastline.geojson")))


def _load():
    global _land_rings, _coast_lines, _places
    if _land_rings is None:
        _land_rings = []
        for f in _features("ne_10m_land.geojson"):
            g = f["geometry"]
            polys = g["coordinates"] if g["type"] == "MultiPolygon" \
                else [g["coordinates"]]
            for poly in polys:
                pts = [(p[0], p[1]) for p in poly[0]]      # 只取外环（大陆/岛屿轮廓）
                if len(pts) >= 4:
                    _land_rings.append(_index(pts))
    if _coast_lines is None:
        _coast_lines = []
        for f in _features("ne_10m_coastline.geojson"):
            g = f["geometry"]
            lines = g["coordinates"] if g["type"] == "MultiLineString" \
                else [g["coordinates"]]
            for ln in lines:
                pts = [(p[0], p[1]) for p in ln]
                if len(pts) >= 2 and pts[0] != pts[-1]:    # 闭合岛环由 land 提供
                    _coast_lines.append(_index(pts))
    if _places is None:
        _places = []
        for f in _features("ne_10m_populated_places.geojson"):
            pr = f.get("properties") or {}
            coords = (f.get("geometry") or {}).get("coordinates")
            if not coords:
                continue
            name = pr.get("NAME_ZH") or pr.get("NAME") or ""
            try:
                pop = int(pr.get("POP_MAX") or 0)
            except (TypeError, ValueError):
                pop = 0
            if name:
                _places.append((float(coords[0]), float(coords[1]), name, pop))


def _features(name):
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fp:
        return json.load(fp).get("features", [])


def _index(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys), pts)


def _hit(idx, w, s, e, n):
    return not (idx[2] < w or idx[0] > e or idx[3] < s or idx[1] > n)


def _clip_closed(pts, w, s, e, n):
    """Sutherland-Hodgman 矩形裁剪，保持闭合环（用于陆地填充）。"""
    res = pts
    for axis, val, keep_ge in ((0, w, True), (0, e, False),
                               (1, s, True), (1, n, False)):
        if not res:
            return []
        out = []
        prev = res[-1]
        pv = prev[axis]
        pin = (pv >= val) if keep_ge else (pv <= val)
        for cur in res:
            cv = cur[axis]
            cin = (cv >= val) if keep_ge else (cv <= val)
            if cin:
                if not pin:
                    t = (val - pv) / (cv - pv)
                    out.append((prev[0] + (cur[0] - prev[0]) * t,
                                prev[1] + (cur[1] - prev[1]) * t))
                out.append(cur)
            elif pin:
                t = (val - pv) / (cv - pv)
                out.append((prev[0] + (cur[0] - prev[0]) * t,
                            prev[1] + (cur[1] - prev[1]) * t))
            prev, pv, pin = cur, cv, cin
        res = out
    return res


def _cut_open(pts, w, s, e, n):
    """开弧按窗口内外连续性切成窗内子段（海岸线描边）。"""
    segs = []
    cur = []
    for (x, y) in pts:
        inside = w <= x <= e and s <= y <= n
        if inside:
            cur.append((x, y))
        elif cur:
            if len(cur) >= 2:
                segs.append(cur)
            cur = []
    if len(cur) >= 2:
        segs.append(cur)
    return segs


def fetch_bbox(bbox, max_places=40):
    """bbox=(S,W,N,E) 度 -> {'coast','land','water','places'}；结构同 osm_fetch。
    失败/缺数据返回 None。"""
    try:
        _load()
        s, w, n, e = bbox
        pad = 0.2 * max(n - s, e - w)
        W, S, E, N = w - pad, s - pad, e + pad, n + pad
        geo = {"coast": [], "land": [], "water": [], "places": []}
        for idx in _land_rings:
            if _hit(idx, W, S, E, N):
                ring = _clip_closed(idx[4], W, S, E, N)
                if len(ring) >= 3:
                    geo["land"].append(ring)
        for idx in _coast_lines:
            if _hit(idx, W, S, E, N):
                geo["coast"].extend(_cut_open(idx[4], W, S, E, N))
        for (lon, lat, name, pop) in _places:
            if W <= lon <= E and S <= lat <= N:
                geo["places"].append({"name": name, "place": "city",
                                      "lat": lat, "lon": lon, "pop": pop})
        geo["places"].sort(key=lambda p: -p["pop"])
        del geo["places"][max_places:]
        return geo
    except Exception:
        return None
