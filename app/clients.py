"""厂商客户端检测与引导。

用途：
- 设备连上后，判断该品牌的官方客户端是否已安装（注册表 / 商店应用 / 安装目录 / 运行进程）。
- 未安装时引导用户打开官方下载页，并说明“该客户端是否提供 PC 端电量接口”。
- 安装后，协议层会尝试从客户端本地接口取电量（见 vendor_protocols.client_bridge）。

注意：检测只读，不安装、不修改任何东西。
"""
from __future__ import annotations

import os
import re
import winreg

from .native import running_processes

# --------------------------------------------------------------------------- #
# 厂商客户端目录（可按需扩充）
# --------------------------------------------------------------------------- #
CATALOG: dict[str, dict] = {
    "philips": {
        "name": "Philips Headphones（飞利浦耳机 App）",
        "platform": "Android / iOS",
        "has_pc_app": False,
        "pc_note": "飞利浦官方只为耳机提供手机 App，没有 Windows 桌面客户端；"
                   "该 App 通过蓝牙厂商通道与耳机通信，桌面端无同等接口。",
        "download_url": "https://www.philips.com.cn/c-w/consumer-support/support-software-and-drivers.html",
        "registry": [r"Philips\s*Headphones", r"飞利浦.*耳机", r"Philips.*Headset"],
        "paths": [r"%LOCALAPPDATA%\Philips\Headphones",
                  r"%PROGRAMFILES%\Philips\Headphones",
                  r"%PROGRAMFILES(X86)%\Philips\Headphones"],
        "store": [r"PhilipsHeadphones", r"Philips.*Headphone"],
        "processes": ["philipsheadphones.exe", "philipsheadset.exe"],
    },
    "sony": {
        "name": "Sony | Headphones Connect",
        "platform": "Android / iOS（PC 无独立 App）",
        "has_pc_app": False,
        "pc_note": "索尼耳机 App 仅手机端；PC 端电量走标准 GATT 电量服务，无需客户端。",
        "download_url": "https://www.sony.com/electronics/support/headphones-headphones",
        "registry": [r"Headphones Connect", r"Sony.*Headphones"],
        "paths": [r"%LOCALAPPDATA%\Sony\HeadphonesConnect"],
        "store": [r"Sony.*Headphones"],
        "processes": ["headphonesconnect.exe"],
    },
    "bose": {
        "name": "Bose Music / Bose Connect",
        "platform": "Android / iOS / Windows(部分)",
        "has_pc_app": True,
        "pc_note": "部分 Bose 产品有 Windows 端程序，但通常不对外提供电量接口。",
        "download_url": "https://www.bose.cn/support.html",
        "registry": [r"Bose\s*(Music|Connect|Updater)"],
        "paths": [r"%LOCALAPPDATA%\Bose", r"%PROGRAMFILES%\Bose"],
        "store": [r"Bose"],
        "processes": ["bosemusic.exe", "boseconnect.exe"],
    },
    "jbl": {
        "name": "JBL Headphones App",
        "platform": "Android / iOS",
        "has_pc_app": False,
        "pc_note": "JBL 官方 App 仅手机端。",
        "download_url": "https://www.jbl.com.cn/support.html",
        "registry": [r"JBL.*Headphones"],
        "paths": [r"%LOCALAPPDATA%\JBL"],
        "store": [r"JBL.*Headphones"],
        "processes": [],
    },
    "logitech": {
        "name": "Logitech G HUB / Logi Options+",
        "platform": "Windows",
        "has_pc_app": True,
        "pc_note": "罗技桌面客户端会在本地保存设备状态（含电量），可尝试桥接读取。",
        "download_url": "https://www.logitech.com.cn/zh-cn/software",
        "registry": [r"Logitech G HUB", r"Logi Options\+", r"Logitech Unifying"],
        "paths": [r"%PROGRAMFILES%\LGHUB", r"%LOCALAPPDATA%\LGHUB",
                  r"%PROGRAMFILES%\LogiOptionsPlus"],
        "store": [r"Logitech.*G\s*HUB"],
        "processes": ["lghub.exe", "logioptionsplus.exe", "logioptionsplus_agent.exe"],
    },
    "sennheiser": {
        "name": "Sennheiser Smart Control",
        "platform": "Android / iOS",
        "has_pc_app": False,
        "pc_note": "森海官方 App 仅手机端。",
        "download_url": "https://www.sennheiser.com.cn/zh-cn/service-support",
        "registry": [r"Smart Control", r"Sennheiser"],
        "paths": [r"%LOCALAPPDATA%\Sennheiser"],
        "store": [r"Sennheiser"],
        "processes": [],
    },
    "edifier": {
        "name": "Edifier Connect",
        "platform": "Android / iOS",
        "has_pc_app": False,
        "pc_note": "漫步者 App 仅手机端。",
        "download_url": "https://www.edifier.com/cn/zh/support",
        "registry": [r"Edifier"],
        "paths": [r"%LOCALAPPDATA%\Edifier"],
        "store": [r"Edifier"],
        "processes": [],
    },
    "huawei": {
        "name": "华为智慧生活 / AI Life",
        "platform": "Android / iOS / Windows",
        "has_pc_app": True,
        "pc_note": "华为 AI Life 有 PC 版，但设备电量接口未公开。",
        "download_url": "https://consumer.huawei.com/cn/support/",
        "registry": [r"AI Life", r"华为智慧生活", r"Huawei.*AI"],
        "paths": [r"%LOCALAPPDATA%\Huawei\AILife"],
        "store": [r"Huawei.*AILife"],
        "processes": ["ailife.exe"],
    },
}

# 型号/品牌关键词 → 厂商 key
_BRAND_HINTS = [
    ("philips", ["philips", "飞利浦", "taa", "tab", "shb", "tah"]),
    ("sony", ["sony", "索尼", "wh-1000", "wf-1000", "wi-1000"]),
    ("bose", ["bose", "qc", "quietcomfort"]),
    ("jbl", ["jbl", "tune", "live pro"]),
    ("logitech", ["logitech", "罗技", "mx master", "mx anywhere", "g pro"]),
    ("sennheiser", ["sennheiser", "森海塞尔", "momentum"]),
    ("edifier", ["edifier", "漫步者"]),
    ("huawei", ["huawei", "华为", "freebuds"]),
]

_UNINSTALL_ROOTS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]

_APPX_ROOTS = [
    (winreg.HKEY_CURRENT_USER,
     r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion"
     r"\AppModel\Repository\Packages"),
    (winreg.HKEY_LOCAL_MACHINE,
     r"SOFTWARE\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages"),
]


def vendor_key_for(*texts: str) -> str:
    """按设备名/型号/品牌猜测厂商 key，未知返回空串。"""
    blob = " ".join(t or "" for t in texts).lower()
    if not blob.strip():
        return ""
    best, best_len = "", 0
    for key, hints in _BRAND_HINTS:
        for h in hints:
            if h in blob and len(h) > best_len:
                best, best_len = key, len(h)
    return best


def _match_any(patterns: list[str], text: str) -> bool:
    for p in patterns:
        try:
            if re.search(p, text, re.I):
                return True
        except re.error:
            continue
    return False


def _scan_registry(cfg: dict) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    for root, path in _UNINSTALL_ROOTS:
        try:
            with winreg.OpenKey(root, path) as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        sub = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, sub) as sk:
                            def val(name):
                                try:
                                    return winreg.QueryValueEx(sk, name)[0]
                                except OSError:
                                    return ""
                            name = str(val("DisplayName") or "")
                            if not name or not _match_any(cfg["registry"], name):
                                continue
                            if name in seen:
                                continue
                            seen.add(name)
                            found.append({"kind": "桌面程序", "name": name,
                                          "version": str(val("DisplayVersion") or ""),
                                          "path": str(val("InstallLocation") or "")})
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def _scan_appx(cfg: dict) -> list[dict]:
    found: list[dict] = []
    for root, path in _APPX_ROOTS:
        try:
            with winreg.OpenKey(root, path) as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        pkg = winreg.EnumKey(key, i)
                    except OSError:
                        continue
                    if _match_any(cfg["store"], pkg):
                        found.append({"kind": "商店应用", "name": pkg,
                                      "version": "", "path": ""})
                    if len(found) >= 6:
                        return found
        except OSError:
            continue
    return found


def _scan_paths(cfg: dict) -> list[dict]:
    found = []
    for p in cfg.get("paths", []):
        real = os.path.expandvars(p)
        if "%" in real:
            continue
        if os.path.exists(real):
            found.append({"kind": "安装目录", "name": os.path.basename(real) or real,
                          "version": "", "path": real})
    return found


def detect(key: str) -> dict:
    """检测指定厂商客户端。返回 {key,name,platform,has_pc_app,pc_note,download_url,
    installed:[...], running:[...], found:bool}"""
    cfg = CATALOG.get(key)
    if not cfg:
        return {"key": key, "name": "未收录的厂商", "found": False,
                "installed": [], "running": [], "platform": "", "has_pc_app": False,
                "pc_note": "", "download_url": ""}
    installed = _scan_registry(cfg) + _scan_appx(cfg) + _scan_paths(cfg)
    # 去重
    uniq, seen = [], set()
    for it in installed:
        k = (it["kind"], it["name"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(it)
    procs = running_processes()
    running = [p for p in cfg.get("processes", []) if p in procs]
    return {
        "key": key,
        "name": cfg["name"],
        "platform": cfg.get("platform", ""),
        "has_pc_app": bool(cfg.get("has_pc_app")),
        "pc_note": cfg.get("pc_note", ""),
        "download_url": cfg.get("download_url", ""),
        "installed": uniq[:8],
        "running": running,
        "found": bool(uniq or running),
    }


def open_download(key: str) -> tuple[bool, str]:
    """打开官方下载/支持页。"""
    import webbrowser
    cfg = CATALOG.get(key)
    if not cfg or not cfg.get("download_url"):
        return False, "该厂商没有收录下载地址"
    url = cfg["download_url"]
    try:
        webbrowser.open(url)
        return True, url
    except Exception as e:  # noqa: BLE001
        return False, f"打开浏览器失败：{e}"
