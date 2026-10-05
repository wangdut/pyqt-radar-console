# -*- coding: utf-8 -*-
"""OpenStreetMap 真实地图数据获取（Overpass API，免费公开数据，© OpenStreetMap contributors）。

- fetch_region(bbox): 拉取海岸线/水域/居民点；大区域自动拆成瓦片网格并发拉取（避免 Overpass 504），
  按瓦片磁盘缓存（osm_cache/），缩放/平移可部分复用，离线可复用
- geocode(q): 地名检索（经纬度），Photon 优先、Nominatim 回退
所有网络调用均为阻塞式，请放到后台线程执行；失败返回 None，绝不抛出。
"""
import hashlib
import json
import math
import os
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "osm_cache")

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
HEADERS = {
    "User-Agent": "PyQtRadarConsole/1.0 (local simulation demo, low request volume)",
    "Content-Type": "application/x-www-form-urlencoded",
}
NOMINATIM = "https://nominatim.openstreetmap.org/search"
PHOTON = "https://photon.komoot.io/api/"


def _cache_path(bbox):
    key = "%.5f,%.5f,%.5f,%.5f" % tuple(bbox)
    name = hashlib.md5(key.encode("utf-8")).hexdigest()[:16]
    return os.path.join(CACHE_DIR, "osm_%s.json" % name)


def _query(bbox):
    s, w, n, e = ["%.5f" % v for v in bbox]
    return (
        "[out:json][timeout:50];\n"
        "(way(%s,%s,%s,%s)[\"natural\"=\"coastline\"];\n"
        " way(%s,%s,%s,%s)[\"natural\"=\"water\"];\n"
        " way(%s,%s,%s,%s)[\"landuse\"~\"reservoir|pond\"];\n"
        " node(%s,%s,%s,%s)[\"place\"~\"^(city|town|suburb|village|hamlet|island|islet)$\"];\n"
        ");\nout geom;"
        % (s, w, n, e, s, w, n, e, s, w, n, e, s, w, n, e)
    )


def _parse(elements):
    """-> {'coast':[开弧], 'land':[闭合陆域环], 'water':[水域环], 'places':[{name,lat,lon,place}]}"""
    geo = {"coast": [], "land": [], "water": [], "places": []}
    for el in elements:
        t = el.get("type")
        if t == "way":
            geom = el.get("geometry") or []
            pts = [(g["lon"], g["lat"]) for g in geom if "lon" in g and "lat" in g]
            if len(pts) < 2:
                continue
            tags = el.get("tags") or {}
            closed = pts[0] == pts[-1]
            if tags.get("natural") == "coastline":
                if closed:
                    geo["land"].append(pts)
                else:
                    geo["coast"].append(pts)
            elif tags.get("natural") == "water" or tags.get("landuse"):
                geo["water"].append(pts)
        elif t == "node":
            tags = el.get("tags") or {}
            if "place" in tags and "lat" in el and "lon" in el:
                geo["places"].append({
                    "name": tags.get("name", ""),
                    "place": tags.get("place", ""),
                    "lat": el["lat"], "lon": el["lon"]})
    return geo


def fetch_region(bbox, timeout=70, min_cell=0.5, max_cells=3):
    """bbox=(S,W,N,E) 度。大区域自动沿固定网格拆瓦片拉取（单瓦片过大时 Overpass 会 504）。
    返回合并后的地理 dict；全部失败返回 None。部分瓦片成功也返回（地图不完整但可用）。"""
    s, w, n, e = bbox
    span = max(n - s, e - w)
    cell = max(min_cell, span / max_cells)      # 瓦片边长（度），对齐网格保证缓存可复用
    i0, i1 = int(math.floor(w / cell)), int(math.floor(e / cell))
    j0, j1 = int(math.floor(s / cell)), int(math.floor(n / cell))
    tiles = [(j * cell, i * cell, (j + 1) * cell, (i + 1) * cell)
             for j in range(j0, j1 + 1) for i in range(i0, i1 + 1)]
    if len(tiles) == 1:
        geo = _fetch_tile(tiles[0], timeout)
    else:
        with ThreadPoolExecutor(max_workers=2) as ex:
            geos = list(ex.map(lambda t: _fetch_tile(t, timeout), tiles))
        geo = None
        for g in geos:
            if g is None:
                continue
            if geo is None:
                geo = g
            else:
                for k in ("coast", "land", "water", "places"):
                    geo[k].extend(g[k])
    return geo


def _fetch_tile(bbox, timeout):
    """单瓦片：先查磁盘缓存，未命中则逐端点请求；无数据/失败返回 None。"""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
    except OSError:
        pass
    cp = _cache_path(bbox)
    if os.path.exists(cp):
        try:
            with open(cp, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    data = urllib.parse.urlencode({"data": _query(bbox)}).encode("utf-8")
    for ep in OVERPASS_ENDPOINTS:
        req = urllib.request.Request(ep, data=data, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read().decode("utf-8", "ignore"))
        except Exception:
            continue
        geo = _parse(body.get("elements", []))
        try:   # 空瓦片（开阔海域）也落盘，避免重复请求
            with open(cp, "w", encoding="utf-8") as f:
                json.dump(geo, f, ensure_ascii=False)
        except OSError:
            pass
        return geo
    return None


def geocode(q, count=6, timeout=20):
    """地名检索 -> [(lat, lon, display_name), ...]；优先 Photon，失败回退 Nominatim。"""
    ua = {"User-Agent": HEADERS["User-Agent"]}
    try:
        url = PHOTON + "?" + urllib.parse.urlencode({"q": q, "limit": count})
        with urllib.request.urlopen(urllib.request.Request(url, headers=ua),
                                    timeout=timeout) as r:
            rows = json.loads(r.read().decode("utf-8", "ignore"))
        out = []
        for ft in rows.get("features", []):
            coords = ft.get("geometry", {}).get("coordinates")
            if not coords:
                continue
            lon, lat = coords[0], coords[1]
            pr = ft.get("properties", {})
            name = ", ".join([v for v in (pr.get("name"), pr.get("city"),
                                          pr.get("state"), pr.get("country")) if v])
            out.append((float(lat), float(lon), name or "?"))
        if out:
            return out
    except Exception:
        pass
    url = NOMINATIM + "?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "limit": count})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=ua),
                                    timeout=timeout) as r:
            rows = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        return []
    out = []
    for it in rows:
        try:
            out.append((float(it["lat"]), float(it["lon"]),
                        it.get("display_name", "")))
        except (KeyError, ValueError):
            continue
    return out
