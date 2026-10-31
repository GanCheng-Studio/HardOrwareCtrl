"""托盘图标「常驻任务栏 / 折叠到溢出区」控制（Windows 11）。

Windows 11 把每个托盘图标的显示位置保存在
HKCU\\Control Panel\\NotifyIconSettings\\<hash> 下：
  - ExecutablePath / UID  图标的身份键（Explorer 按 exe 路径 + uID 登记）
  - IsPromoted            1 = 常驻任务栏，0 / 缺失 = 折叠进右下角溢出区 (^)

同一 exe 的图标若 uID 相同会挤进同一条记录，无法一个折叠一个常驻；
tray.py 为每个图标注入了独立的稳定 uID，本模块启动后按 (exe, UID)
找到对应注册表项并写入期望状态。Explorer 监听此键，通常无需重启即可生效。
"""
from __future__ import annotations

import os
import sys
import threading
import winreg

_NIS = r"Control Panel\NotifyIconSettings"
_MAX_RETRY = 15      # Explorer 登记图标条目有延迟，轮询重试几分钟
_RETRY_WAIT = 10.0


def apply(targets: list[tuple[int, int]]):
    """在后台线程写入 [(uid, promoted), ...]，uid 为图标注册的 uID。"""
    threading.Thread(target=_worker, args=(list(targets),),
                     daemon=True).start()


def _worker(targets):
    for _ in range(_MAX_RETRY):
        if _write_once(targets):
            return
        threading.Event().wait(_RETRY_WAIT)


def _write_once(targets) -> bool:
    """写一次注册表；返回是否所有目标 UID 的条目都已找到。

    只在条目还没有 IsPromoted 值时写入默认状态（总控常驻、设备折叠），
    绝不覆盖用户手动拖动产生的设置。
    """
    exe = os.path.normcase(sys.executable)

    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _NIS)
    except OSError:
        return True   # 打不开（老系统等）：跳过

    subkeys, i = [], 0
    while True:
        try:
            subkeys.append(winreg.EnumKey(root, i))
            i += 1
        except OSError:
            break
    winreg.CloseKey(root)

    pending = {uid for uid, _ in targets}
    promoted = dict(targets)
    for sk in subkeys:
        if not pending:
            break
        try:
            k = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, f"{_NIS}\\{sk}", 0,
                winreg.KEY_READ | winreg.KEY_SET_VALUE)
        except OSError:
            continue
        try:
            try:
                exe_reg, _ = winreg.QueryValueEx(k, "ExecutablePath")
            except OSError:
                continue
            if os.path.normcase(str(exe_reg)) != exe:
                continue
            try:
                uid, _ = winreg.QueryValueEx(k, "UID")
            except OSError:
                continue
            uid = int(uid)
            if uid in pending:
                try:
                    winreg.QueryValueEx(k, "IsPromoted")
                except OSError:
                    # 首次登记：写入默认显示状态，之后尊重用户手动调整
                    winreg.SetValueEx(k, "IsPromoted", 0, winreg.REG_DWORD,
                                      promoted[uid])
                pending.discard(uid)
        finally:
            winreg.CloseKey(k)
    return not pending
