r"""托盘图标管理：总控托盘 + 每设备托盘。

左键行为：
- 总控图标：打开主面板
- 设备图标：打开该设备详情页

右键菜单：
- 总控：打开主面板 / 设置 / 刷新全部 / 退出软件
- 设备：详情 / 手动刷新 / 低电量提醒开关 / 在托盘显示开关 / 删除设备

显示位置（Windows 11）：
- Explorer 按「exe 路径 + uID」把托盘图标登记到注册表
  HKCU\Control Panel\NotifyIconSettings，IsPromoted=1 常驻任务栏、
  0 折叠进溢出区 (^)
- pystray 传参字段名有误（hID 而非 uID），所有图标 uID 都是 0，会挤进
  同一条设置记录，导致无法一个折叠一个常驻。这里给每个图标注入独立的
  稳定 uID（tray.py），创建后由 traypin 写注册表：总控常驻、设备折叠。
  用户之后仍可手动拖动调整。
"""
from __future__ import annotations

import threading
import uuid
import zlib

import pystray

from .common import make_device_icon, make_master_icon
from .version import VERSION
from . import traypin

_GUID_NS = uuid.uuid5(uuid.NAMESPACE_URL, "battery-tray.app")
# uID 只要「每个图标唯一且每次启动一致」即可；用 uuid5 生成稳定的命名空间，
# 再从中派生。总控固定 0x1000，设备用 crc32(dev_id) 避开 0x1000。
MASTER_UID = 0x1000


def device_uid(dev_id: str) -> int:
    u = 0x1001 + zlib.crc32(f"battery-tray/{dev_id}".encode()) % 0x6FFF
    return u if u != MASTER_UID else MASTER_UID + 1


class TrayIcon(pystray.Icon):
    """给 Shell_NotifyIcon 注入稳定 uID 的托盘图标。

    pystray._win32.Icon._message 里 `hID=id(self)` 与结构体字段 uID 不匹配，
    被 ctypes 静默丢弃 → 所有图标 uID=0 → Explorer 合并为一条托盘设置记录。
    这里补充正确的 uID 字段。
    """

    _uid: int = 0

    def _message(self, code, flags, **kwargs):
        kwargs["uID"] = self._uid
        super()._message(code, flags, **kwargs)


class TrayManager:
    """所有回调 cb(name, *args) 会被投递到主程序的事件队列（线程安全）。"""

    def __init__(self, emit):
        # emit(event_name, *args) -> App._emit
        self._emit = emit
        self.master: pystray.Icon | None = None
        self.device_icons: dict[str, pystray.Icon] = {}
        self._device_state: dict[str, dict] = {}  # dev_id -> cfg 快照（供菜单勾选状态）

    # ------------------------------------------------------------------ #
    def start(self, devices: list[dict]):
        menu = pystray.Menu(
            pystray.MenuItem("打开主面板", self._on_open_main, default=True),
            pystray.MenuItem("刷新全部", lambda *a: self._emit("refresh_all")),
            pystray.MenuItem("设置", lambda *a: self._emit("open_settings")),
            pystray.MenuItem(f"关于 · {VERSION}",
                             lambda *a: self._emit("open_about")),
            pystray.MenuItem("退出软件", self._on_quit),
        )
        self.master = TrayIcon(
            "battery_tray_master", make_master_icon(),
            "蓝牙电量管家（左键打开主面板）", menu)
        self.master._uid = MASTER_UID
        self.master.run_detached()
        for dev in devices:
            if dev.get("show_in_tray", True):
                self.show_device(dev, None)
        self._schedule_promote()

    def stop(self):
        for icon in list(self.device_icons.values()):
            try:
                icon.stop()
            except Exception:
                pass
        self.device_icons.clear()
        if self.master:
            try:
                self.master.stop()
            except Exception:
                pass
            self.master = None

    # ------------------------------------------------------------------ #
    def show_device(self, dev: dict, battery, precision: str | None = None,
                    style: str = "square"):
        """创建/更新设备托盘图标。battery: int | None；precision: exact/coarse/none"""
        dev_id = dev["id"]
        self._device_state[dev_id] = dict(dev)
        self._style = style
        coarse = precision == "coarse"
        val = "未知" if battery is None else str(battery) + "%"
        suffix = "（≈ 系统粗略值）" if coarse and battery is not None else ""
        title = f"{dev['name']}  电量: {val}{suffix}"
        if dev_id not in self.device_icons:
            menu = pystray.Menu(
                pystray.MenuItem("设备详情", lambda *a, _id=dev_id: self._emit("open_detail", _id),
                                 default=True),
                pystray.MenuItem("手动刷新电量", lambda *a, _id=dev_id: self._emit("refresh_one", _id)),
                pystray.MenuItem("打开系统设置",
                                 lambda *a, _id=dev_id: self._emit("open_sys_settings", _id)),
                pystray.MenuItem("低电量提醒",
                                 lambda *a, _id=dev_id: self._emit("toggle_alert", _id),
                                 checked=lambda item, _id=dev_id: self._is_alert_on(_id)),
                pystray.MenuItem("在托盘显示",
                                 lambda *a, _id=dev_id: self._emit("toggle_show", _id),
                                 checked=lambda item, _id=dev_id: self._is_show_on(_id)),
                pystray.MenuItem("删除设备（不影响系统连接）",
                                 lambda *a, _id=dev_id: self._emit("remove_device", _id)),
            )
            icon = TrayIcon(f"dev_{dev_id}",
                            make_device_icon(battery, coarse, style),
                            title, menu)
            icon._uid = device_uid(dev_id)
            self.device_icons[dev_id] = icon
            threading.Thread(target=icon.run_detached, daemon=True).start()
            self._schedule_promote()
        else:
            icon = self.device_icons[dev_id]
            try:
                icon.icon = make_device_icon(battery, coarse, style)
                icon.title = title
                icon.update_menu()
            except Exception:
                pass

    def hide_device(self, dev_id: str):
        icon = self.device_icons.pop(dev_id, None)
        if icon:
            try:
                icon.stop()
            except Exception:
                pass

    def _schedule_promote(self):
        """延迟应用常驻/折叠策略（Explorer 写入注册表条目有延迟）。"""
        targets = [(MASTER_UID, 1)] + [
            (device_uid(d), 0) for d in self.device_icons]
        t = threading.Timer(2.0, lambda: traypin.apply(targets))
        t.daemon = True
        t.start()

    def notify_low(self, dev: dict, battery: int):
        icon = self.device_icons.get(dev["id"]) or self.master
        if icon:
            try:
                icon.notify(f"{dev['name']} 电量仅剩 {battery}%，请及时充电。",
                            title="低电量提醒")
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    def _is_alert_on(self, dev_id: str) -> bool:
        return bool(self._device_state.get(dev_id, {}).get("low_battery_alert", True))

    def _is_show_on(self, dev_id: str) -> bool:
        return bool(self._device_state.get(dev_id, {}).get("show_in_tray", True))

    def _on_open_main(self, *a):
        self._emit("open_main")

    def _on_quit(self, *a):
        self._emit("quit")
