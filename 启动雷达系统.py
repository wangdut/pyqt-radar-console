# -*- coding: utf-8 -*-
"""
一键启动：航海雷达系统（雷达控制台 + 目标测试台）
运行方式：双击本文件，或命令行执行  python 启动雷达系统.py
说明：优先使用 pythonw.exe 静默启动，不弹黑色命令行窗口；
     启动前自动检测 5678/5679 端口是否被旧实例占用，避免“新窗口收不到目标”。
"""
import os
import socket
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))

# 优先 pythonw（无控制台窗口），不存在则退回 python
_candidates = [
    os.path.join(os.path.dirname(sys.executable), "pythonw.exe"),
    sys.executable,
]
PYEXE = next((p for p in _candidates if os.path.exists(p)), sys.executable)

APPS = [
    ("雷达控制台", "radar_dashboard.py", 5678),
    ("目标测试台", "radar_tester.py", 5679),
]


def port_busy(port):
    """尝试绑定端口，判断是否已被其他程序占用。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def pids_on_port(port):
    """从 netstat 查出占用指定 UDP 端口的进程 PID。"""
    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "udpa"], text=True, errors="ignore")
    except Exception:
        return []
    pids = set()
    for line in out.splitlines():
        cols = line.split()
        if len(cols) >= 5 and cols[0].upper().startswith("UDP") \
                and (":%d" % port) in cols[1]:
            if cols[-1].isdigit() and int(cols[-1]) > 0:
                pids.add(cols[-1])
    return sorted(pids)


def main():
    print("启动器：%s" % PYEXE)
    # ---- 旧实例检测：端口被占会导致新雷达窗口收不到目标 ----
    busy = [(label, port) for label, script, port in APPS if port_busy(port)]
    if busy:
        desc = "、".join("%s(端口%d)" % t for t in busy)
        print("[警告] 检测到旧实例仍在运行：%s" % desc)
        kill_pids = sorted({p for _, port in busy for p in pids_on_port(port)})
        if kill_pids:
            print("         占用进程 PID：%s" % ", ".join(kill_pids))
        ans = input("是否关闭旧实例并重新启动？[Y/n] ").strip().lower()
        if ans in ("", "y", "yes"):
            for pid in kill_pids:
                subprocess.call(["taskkill", "/PID", pid, "/F"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
                print("[ OK ] 已关闭旧进程 PID %s" % pid)
            time.sleep(0.8)
        else:
            print("已取消启动。若只是目标收不到，请先关闭所有旧的雷达/测试台窗口再启动。")
            return
    for label, script, _port in APPS:
        path = os.path.join(BASE, script)
        if not os.path.exists(path):
            print("[错误] 找不到 %s，请与启动器放在同一目录" % script)
            continue
        subprocess.Popen([PYEXE, path], cwd=BASE)
        print("[ OK ] 已启动 %s (%s)" % (label, script))
        time.sleep(0.6)  # 先雷达后测试台，确保端口 5678 就绪
    print("")
    print("操作提示：")
    print("  · 雷达窗口内  ↑/↓ 加减速   A/D 左右舵")
    print("  · 测试台点击海图选位置，设置航速航向后注入目标")
    print("  · 关闭界面直接点窗口右上角叉号即可")
    input("\n按回车键关闭本启动器窗口（不影响已运行的程序）...")


if __name__ == "__main__":
    main()
