"""开机静默启动：在用户「启动」文件夹里放一个 VBS 启动项。

`路径 = %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup`
VBS 用窗口样式 0 拉起 pythonw + --silent，所以开机后不弹主窗口、只驻留托盘，
也不会有控制台黑框闪过。删除该文件即取消开机启动。
"""
from __future__ import annotations

import os
import sys

LINK_NAME = "蓝牙电量管家.vbs"


def startup_dir() -> str:
    appdata = os.environ.get("APPDATA") or os.path.expanduser(
        r"~\AppData\Roaming")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu",
                        "Programs", "Startup")


def script_path() -> str:
    return os.path.join(startup_dir(), LINK_NAME)


def is_enabled() -> bool:
    return os.path.isfile(script_path())


def _pythonw() -> str:
    exe = sys.executable
    d = os.path.dirname(exe)
    for name in ("pythonw.exe", "python.exe"):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return exe


def _main_py() -> str:
    # app/autostart.py → 项目根目录/main.py
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "main.py")


def enable() -> tuple[bool, str]:
    """创建静默启动项，返回 (成功, 说明)。"""
    try:
        d = startup_dir()
        os.makedirs(d, exist_ok=True)
        pw, main = _pythonw(), _main_py()
        workdir = os.path.dirname(main)
        # 纯 ASCII 内容，避免 VBS 在非 Unicode 解析下出现乱码
        vbs = (
            'Set sh = CreateObject("WScript.Shell")\r\n'
            f'sh.CurrentDirectory = "{workdir}"\r\n'
            f'sh.Run """{pw}"" ""{main}"" --silent", 0, False\r\n'
        )
        with open(script_path(), "w", encoding="ascii", newline="") as f:
            f.write(vbs)
        return True, script_path()
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def disable() -> tuple[bool, str]:
    """删除启动项，返回 (成功, 说明)。"""
    p = script_path()
    try:
        if os.path.isfile(p):
            os.remove(p)
        return True, p
    except Exception as e:  # noqa: BLE001
        return False, str(e)
