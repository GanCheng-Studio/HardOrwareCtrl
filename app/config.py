"""配置持久化（JSON）。"""
from __future__ import annotations

import json
import os

APP_NAME = "BatteryTray"

DEFAULT_SETTINGS = {
    "low_battery_threshold": 30,   # 低电量提醒阈值
    "refresh_interval_sec": 60,    # 自动刷新间隔
    # 电量来源策略：prefer_exact（精确优先，无精确源时显示系统粗略值并标注）
    #               exact_only（只显示精确值，否则显示 --）
    #               system_only（只用系统值）
    "battery_source_mode": "prefer_exact",
    "prompt_client_once": True,    # 检测到缺少厂商协议时提示一次
    # 托盘数字外观：square（圆角方块）/ pill（胶囊）/ number（纯数字）
    "tray_style": "square",
    # 主界面布局：tile（磁贴）/ list（列表）
    "main_view": "tile",
    # 开机静默启动（启动文件夹里放 VBS 启动项）
    "autostart": False,
    # 代码模式（详见 app/codemode.py、app/codemode_ui.py）
    "code_mode": {},
}

DEFAULT_CODE_MODE = {
    "enabled": False,        # 代码模式总开关
    "autopair": True,        # 自动补齐括号 / 引号（总开关）
    "auto_switch": True,     # 按上下文自动切换中英文（总开关）
    "chinese_punct": True,   # 切中文时顺带请求中文标点（IMM 输入法有效）
    "terminal_zh": True,     # 进入终端 / bash 时切中文
    "apps": [],              # [{name, exe, lang, autopair, auto_switch}]
    "langs": {},             # lang -> {autopair, auto_switch, never_english}
    "custom_langs": [],      # [{key, name, base}]
}

DEFAULT_DEVICE = {
    "id": "",               # 内部唯一 id（addr:XXXX 或 link:slug 或 usb:...）
    "name": "",
    "dev_type": "generic",  # 设备类型（mouse/keyboard/headset/earbuds/gamepad/dongle/generic）
    # 连接方式：bt 蓝牙 / 2.4g 无线接收器 / usb 有线 USB / "" 自动判断
    "transport": "",
    "hid_key": "",          # 2.4G / USB 设备：HID 物理设备键（用于刷新时定位）
    "vid": 0,
    "pid": 0,
    "address": None,        # 蓝牙 MAC（int），扫描添加时记录
    "container_id": "",     # Windows 容器 Id，用于跨端点匹配
    "link": "",             # 官网产品链接
    "model": "",            # 解析出的型号
    "brand": "",            # 品牌（链接解析或手动）
    "vendor": "",           # 厂商 key（philips/sony/...），空则按名称推断
    "protocol": "gatt_battery",
    "show_in_tray": True,
    "low_battery_alert": True,
    "tray_style": "",      # 该设备托盘图标外观：square/pill/number，空 = 跟随全局设置
    "hotkey": "",          # 键盘设备：切换中/英输入法的全局快捷键（F8、Ctrl+F9…），空 = 未启用
    "vendor_battery": {},   # 厂商协议读到的电量缓存 {battery,source,ts}
    "client_prompted": False,
}


def config_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


class Config:
    def __init__(self):
        self.settings = dict(DEFAULT_SETTINGS)
        self.settings["code_mode"] = dict(DEFAULT_CODE_MODE)
        self.devices: list[dict] = []
        self.load()

    def load(self):
        try:
            with open(config_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            self.settings.update(data.get("settings") or {})
            self.devices = data.get("devices") or []
        except Exception:
            pass
        cm = dict(DEFAULT_CODE_MODE)
        cm.update(self.settings.get("code_mode") or {})
        self.settings["code_mode"] = cm

    def save(self):
        try:
            with open(config_path(), "w", encoding="utf-8") as f:
                json.dump({"settings": self.settings, "devices": self.devices},
                          f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def find(self, dev_id: str):
        for d in self.devices:
            if d["id"] == dev_id:
                return d
        return None

    def add_device(self, **kw) -> dict:
        dev = dict(DEFAULT_DEVICE)
        dev.update(kw)
        if not dev["id"]:
            if dev.get("hid_key"):
                dev["id"] = f"hid:{dev['hid_key']}"
            elif dev.get("address"):
                dev["id"] = f"addr:{dev['address']:012X}"
            else:
                dev["id"] = f"link:{abs(hash(dev.get('name', 'dev'))) % 10 ** 10}"
        # 防重复：同 id / 同地址 / 同 HID 设备不重复添加
        for d in self.devices:
            if d["id"] == dev["id"]:
                return d
            if dev.get("address") is not None and d.get("address") == dev["address"]:
                return d
            if dev.get("hid_key") and d.get("hid_key") == dev["hid_key"]:
                return d
        self.devices.append(dev)
        self.save()
        return dev

    def remove_device(self, dev_id: str):
        self.devices = [d for d in self.devices if d["id"] != dev_id]
        self.save()
