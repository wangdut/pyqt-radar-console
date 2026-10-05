# -*- coding: utf-8 -*-
"""一次性下载 Natural Earth 10m 矢量（公共领域），存到 nrdata/。用完可删。"""
import os
import time
import urllib.request

BASES = [
    "https://cdn.jsdelivr.net/gh/nvkelso/natural-earth-vector@master/geojson/",
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/",
]
FILES = ["ne_10m_land.geojson", "ne_10m_coastline.geojson",
         "ne_10m_populated_places.geojson"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nrdata")
os.makedirs(OUT, exist_ok=True)
HDRS = {"User-Agent": "PyQtRadarConsole/1.0"}

for name in FILES:
    dst = os.path.join(OUT, name)
    if os.path.exists(dst) and os.path.getsize(dst) > 100000:
        print("skip", name, os.path.getsize(dst), flush=True)
        continue
    for base in BASES:
        t = time.time()
        try:
            req = urllib.request.Request(base + name, headers=HDRS)
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            with open(dst, "wb") as f:
                f.write(data)
            print("OK %s %.1fMB in %.0fs from %s" % (
                name, len(data) / 1048576.0, time.time() - t,
                base.split("/")[2]), flush=True)
            break
        except Exception as e:
            print("FAIL %s %s %s" % (name, base.split("/")[2],
                                     str(e)[:70]), flush=True)
print("NR_DOWNLOAD_DONE", flush=True)
