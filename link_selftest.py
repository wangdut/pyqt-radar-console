# -*- coding: utf-8 -*-
"""链路自检：在雷达运行时执行，验证 UDP 注入与本船回报是否畅通。
用法：python radar_dashboard.py 启动后，另开终端运行 python link_selftest.py
"""
import socket
import sys
import time

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.bind(("127.0.0.1", 5679))  # 占位模拟测试台的本船监听端口
except OSError:
    print("端口 5679 已被测试台占用，请先关闭测试台再自检")
    sys.exit(1)

s.sendto(b"ADD,TST-99,4.00,3.00,10.0,270\n", ("127.0.0.1", 5678))
print("已发送测试目标 TST-99（东4 北3 海里，航向270°/10节）")
s.settimeout(3.0)
ok_own = False
t0 = time.time()
while time.time() - t0 < 3:
    try:
        data, _ = s.recvfrom(1024)
        line = data.decode("utf-8").strip()
        if line.startswith("OWN"):
            ok_own = True
            print("收到本船回报:", line)
            break
    except socket.timeout:
        break
s.sendto(b"DEL,TST-99\n", ("127.0.0.1", 5678))
print("已发送删除指令")
if ok_own:
    print("自检通过：目标注入与动态回报链路正常")
else:
    print("自检失败：未收到雷达回报，请确认 radar_dashboard.py 正在运行")
    sys.exit(2)
