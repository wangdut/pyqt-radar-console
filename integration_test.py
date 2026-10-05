# -*- coding: utf-8 -*-
"""集成自检：单进程内同时实例化雷达与测试台，验证 UDP 双向链路 + OSM 降生全链路。
运行：python integration_test.py（需先关闭旧的雷达/测试台窗口，保证端口空闲）
"""
import sys
import time

from PyQt5.QtWidgets import QApplication

import radar_dashboard as rd
import radar_tester as rt

app = QApplication(sys.argv)

scope = rd.RadarScope()          # 雷达端（不显示窗口）
tester = rt.Tester()             # 测试台（不显示窗口，构造时已 spawn_at 默认渤海湾）

# 等待 sockets/降生指令发出
for _ in range(20):
    app.processEvents()
    time.sleep(0.02)

# ---------- 1. 目标注入链路 ----------
tester.ed_name.setText("LINK-OK")
tester.spn_x.setValue(3.0)
tester.spn_y.setValue(2.0)
tester.spn_spd.setValue(8.0)
tester.spn_hdg.setValue(45)
tester.add_target()

t0 = time.time()
while time.time() - t0 < 2.0:
    app.processEvents()
    time.sleep(0.02)

names = [c.name for c in scope.contacts]
print("雷达端收到目标:", names if names else "（空！）")
print("测试台收到本船回报:", "是 (own=%0.2f,%0.2f hdg=%0.0f spd=%0.0f)"
      % (tester.own_x, tester.own_y, tester.own_hdg, tester.own_spd)
      if time.time() - tester.last_own_rx < 3 else "否")

c0 = next((c for c in scope.contacts if c.name == "LINK-OK"), None)
ok1 = c0 is not None and abs(c0.x - 3.0) < 0.05 and abs(c0.y - 2.0) < 0.05
print("目标->雷达:", "通过" if ok1 else "失败")

# ---------- 2. 删除链路 ----------
tester.listw.setCurrentRow(0)
tester.del_selected()
t0 = time.time()
while time.time() - t0 < 1.0:
    app.processEvents()
    time.sleep(0.02)
ok2 = "LINK-OK" not in [c.name for c in scope.contacts]
print("删除->雷达同步移除:", "通过" if ok2 else "失败")

# ---------- 3. SPAWN 降生链路（tester 构造时已向雷达广播锚点）----------
for _ in range(80):
    app.processEvents()
    time.sleep(0.05)
    if scope.region is not None:
        break
ok3 = (scope.region is not None and scope.spawn_ll is not None and
       abs(scope.spawn_ll[0] - rt.DEFAULT_SPAWN[0]) < 0.01 and
       abs(scope.spawn_ll[1] - rt.DEFAULT_SPAWN[1]) < 0.01)
print("SPAWN->雷达锚点同步:",
      ("通过 %.4f,%.4f" % scope.spawn_ll) if ok3 else "失败")

# ---------- 4. 幂等：同锚点重复 SPAWN 不得清空目标 ----------
tester.ed_name.setText("KEEP-ME")
tester.spn_x.setValue(-4.0)
tester.spn_y.setValue(6.0)
tester.add_target()
t0 = time.time()
while time.time() - t0 < 1.5 and not any(c.name == "KEEP-ME" for c in scope.contacts):
    app.processEvents()
    time.sleep(0.02)
scope.spawn(*rt.DEFAULT_SPAWN, scale_nm=12.0)   # 直接调用等价于收到重复 SPAWN
for _ in range(10):
    app.processEvents()
    time.sleep(0.02)
ok4 = any(c.name == "KEEP-ME" for c in scope.contacts)
print("同锚点重复SPAWN幂等(目标保留):", "通过" if ok4 else "失败")

# ---------- 5. 异锚点 SPAWN 应重置海域 ----------
scope.spawn(rt.DEFAULT_SPAWN[0] + 0.5, rt.DEFAULT_SPAWN[1] + 0.5)
for _ in range(10):
    app.processEvents()
    time.sleep(0.02)
ok5 = (not any(c.name == "KEEP-ME" for c in scope.contacts) and
       abs(scope.spawn_ll[0] - (rt.DEFAULT_SPAWN[0] + 0.5)) < 1e-6)
print("异锚点SPAWN重置:", "通过" if ok5 else "失败")
scope.spawn(*rt.DEFAULT_SPAWN, scale_nm=12.0)   # 切回默认海域，等待地图

# ---------- 6. OWN 七字段（含经纬度）格式 ----------
lat_lon_ok = False
if scope.region is not None:
    scope.own = [1.0, 2.0]
    captured = {}
    orig = scope.tx.writeDatagram
    scope.tx.writeDatagram = lambda d, a, p: captured.update(msg=bytes(d).decode())
    scope._send_own()
    scope.tx.writeDatagram = orig
    parts = captured.get("msg", "").strip().split(",")
    lat_lon_ok = len(parts) == 7 and parts[0] == "OWN"
    if lat_lon_ok:
        rlat, rlon = float(parts[5]), float(parts[6])
        elon, elat = scope.region.to_ll(1.0, 2.0)   # to_ll 返回 (lon, lat)
        lat_lon_ok = abs(rlat - elat) < 1e-5 and abs(rlon - elon) < 1e-5
        print("OWN经纬度字段: %.6f,%.6f" % (rlat, rlon))
ok6 = lat_lon_ok
print("OWN七字段经纬度:", "通过" if ok6 else "失败")

# ---------- 7. 地图加载（Natural Earth 本地，首次解析约 1s）----------
t0 = time.time()
while time.time() - t0 < 20 and not (scope.map_loaded and tester.shapes):
    app.processEvents()
    time.sleep(0.1)
ok7 = scope.map_loaded and scope.shapes is not None
if ok7:
    print("地图->雷达: 通过 海岸%d段 陆地%d段 地名%d个"
          % (len(scope.shapes["coast"]), len(scope.shapes["land"]),
             len(scope.shapes["places"])))
else:
    print("地图->雷达: 失败（nrdata/ 缺失且 Overpass 不可达？）")
ok7t = tester.shapes is not None
print("地图->测试台:", "通过" if ok7t else "失败/未就绪")
print("陆地抽样点数:", [len(r) for r in (scope.shapes["land"] if ok7 else [])][:6])

# ---------- 8. 反向数据链：雷达->测试台 DEL（攻击击沉后同步移除） ----------
tester.ed_name.setText("SINK-ME")
tester.spn_x.setValue(2.0)
tester.spn_y.setValue(1.5)
tester.spn_spd.setValue(6.0)
tester.spn_hdg.setValue(90)
tester.add_target()
t0 = time.time()
while time.time() - t0 < 1.5 and not any(c.name == "SINK-ME" for c in scope.contacts):
    app.processEvents()
    time.sleep(0.02)
# 模拟攻击模块击沉后雷达向测试台回传 DEL
scope._send_cmd("DEL,SINK-ME")
t0 = time.time()
while time.time() - t0 < 1.0 and "SINK-ME" in tester.targets:
    app.processEvents()
    time.sleep(0.02)
ok8 = "SINK-ME" not in tester.targets
print("雷达->测试台击沉同步DEL:", "通过" if ok8 else "失败")

all_ok = ok1 and ok2 and ok3 and ok4 and ok5 and ok6 and ok7 and ok7t and ok8
print("==>", "全部通过" if all_ok else "存在失败项")
sys.exit(0 if all_ok else 1)
