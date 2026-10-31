"""厂商协议层：决定“电量从哪读”，并标注数据可信度。

设计原则
--------
1. 精确优先：能走厂商/标准精确通道时，绝不用系统粗略值。
2. 诚实标注：每个电量值都带 source + precision，界面上区分“精确”与“≈ 系统粗略”。
3. 可扩展：新厂商 = 新增一个探测函数 / 在 protocols.json 里加型号，不动 UI。

通道优先级
----------
exact   : 厂商客户端桥接 → 厂商私有协议 → BLE GATT BAS(0x180F)
coarse  : 经典蓝牙 HFP HF Indicators（Windows 设置页用的就是这条，可能滞后/不准）
"""
from __future__ import annotations

import json
import os
import time

from . import clients
from .native import rfcomm_connect, rfcomm_recv, rfcomm_send, rfcomm_close

EXACT = "exact"
COARSE = "coarse"
NONE = "none"

JL_VENDOR_UUID = "fe010000-1234-5678-abcd-00805f9b34fb"
SPP_UUID = "00001101-0000-1000-8000-00805f9b34fb"
HFP_UUID = "0000111e-0000-1000-8000-00805f9b34fb"

PRECISION_LABEL = {EXACT: "精确", COARSE: "≈ 系统粗略", NONE: "无数据"}

_CLIENT_CACHE: dict[str, tuple[float, dict]] = {}
_CLIENT_TTL = 300.0


# --------------------------------------------------------------------------- #
# 客户端检测（带缓存）
# --------------------------------------------------------------------------- #
def client_info(dev: dict, force: bool = False) -> dict:
    key = dev.get("vendor") or clients.vendor_key_for(
        dev.get("name", ""), dev.get("model", ""), dev.get("brand", ""))
    if not key:
        return {"key": "", "name": "", "found": False, "installed": [],
                "running": [], "pc_note": "", "has_pc_app": False,
                "download_url": "", "platform": ""}
    now = time.time()
    hit = _CLIENT_CACHE.get(key)
    if hit and not force and now - hit[0] < _CLIENT_TTL:
        return hit[1]
    info = clients.detect(key)
    _CLIENT_CACHE[key] = (now, info)
    return info


def vendor_key(dev: dict) -> str:
    return dev.get("vendor") or clients.vendor_key_for(
        dev.get("name", ""), dev.get("model", ""), dev.get("brand", ""))


# --------------------------------------------------------------------------- #
# 通道 1：厂商客户端桥接
# --------------------------------------------------------------------------- #
def client_bridge(dev: dict) -> dict:
    """尝试从厂商客户端取电量。

    真实可用的形态有三种：本地 HTTP 端口、共享内存/日志文件、厂商 SDK。
    目前只实现“可判定”的部分——检测客户端是否在跑，并如实说明是否有可用接口，
    避免伪造数值。
    """
    c = client_info(dev)
    if not c.get("key"):
        return {"ok": False, "reason": "未识别厂商，无客户端可桥接"}
    if not c.get("found"):
        return {"ok": False, "reason": f"未检测到 {c['name']}",
                "need_client": True}
    if not c.get("has_pc_app"):
        return {"ok": False,
                "reason": f"{c['name']} 只有手机端（{c.get('platform')}），PC 上无法桥接",
                "need_client": True}
    if not c.get("running"):
        return {"ok": False, "reason": f"{c['name']} 已安装但未运行", "need_client": False}
    return {"ok": False,
            "reason": f"{c['name']} 正在运行，但未提供可读的本地电量接口",
            "need_client": False}


# --------------------------------------------------------------------------- #
# 通道 2：BLE GATT 标准电量服务
# --------------------------------------------------------------------------- #
def gatt_battery(worker, info: dict, dev: dict) -> dict:
    if not info.get("is_le"):
        return {"ok": False, "reason": "设备没有 BLE 端点（纯经典蓝牙）"}
    addr = dev.get("address")
    if addr is None:
        return {"ok": False, "reason": "缺少蓝牙地址"}
    r = worker.read_gatt_battery(addr)
    if "error" in r:
        return {"ok": False, "reason": r["error"]}
    if r.get("battery") is None:
        return {"ok": False, "reason": "设备未暴露 GATT 电量服务(0x180F)"}
    return {"ok": True, "battery": int(r["battery"]), "source": "GATT 电量服务 (0x180F)"}


# --------------------------------------------------------------------------- #
# 通道 3：杰理 RCSP（JL_SPP 厂商协议）—— 真协议实现，设备未开放时如实返回
# --------------------------------------------------------------------------- #
def _rcsp_frame(opcode: int, payload: bytes = b"", resp: bool = True) -> bytes:
    head = opcode | (0x0200 if resp else 0)
    return (b"\xfe\xdc\xba" + head.to_bytes(2, "little")
            + len(payload).to_bytes(2, "big") + payload + b"\xef")


def _rcsp_parse(buf: bytes) -> dict | None:
    """从字节流里找 RCSP 帧；返回 {opcode, is_resp, payload}。"""
    i = buf.find(b"\xfe\xdc\xba")
    if i < 0 or len(buf) < i + 7:
        return None
    head = int.from_bytes(buf[i + 3:i + 5], "little")
    ln_be = int.from_bytes(buf[i + 5:i + 7], "big")
    ln_le = int.from_bytes(buf[i + 5:i + 7], "little")
    for ln in (ln_be, ln_le):
        if 0 <= ln <= 512 and len(buf) >= i + 7 + ln:
            return {"opcode": head & 0xFF, "is_resp": bool(head & 0x0100),
                    "payload": buf[i + 7:i + 7 + ln]}
    return None


def jl_rcsp_battery(mac: int, uuid_str: str) -> dict:
    """按杰理 RCSP 协议请求系统信息（电量在第 1 字节）。"""
    try:
        s = rfcomm_connect(mac, uuid_str, timeout=10)
    except OSError as e:
        return {"ok": False, "reason": str(e)}
    try:
        time.sleep(0.4)
        rfcomm_recv(s, 512)  # 清空可能存在的推送
        # SYS_INFO_GET(0x07) + ATTR_TYPE_SYS_INFO(1)
        probe = _rcsp_frame(0x07, b"\x01")
        rfcomm_send(s, probe)
        deadline = time.time() + 3.0
        buf = b""
        while time.time() < deadline:
            chunk = rfcomm_recv(s, 512)
            if not chunk:
                continue
            buf += chunk
            if buf == probe:  # 回环口：原样回显
                return {"ok": False, "reason": "该服务是回环/调试口（原样回显），非 RCSP"}
            fr = _rcsp_parse(buf)
            if fr and fr["opcode"] == 0x07 and fr["payload"]:
                p = fr["payload"]
                if len(p) >= 2 and 0 <= p[1] <= 100:
                    return {"ok": True, "battery": int(p[1]),
                            "source": "厂商 RCSP 协议（杰理 SYS_INFO）"}
                return {"ok": False, "reason": f"响应格式未知：{p.hex(' ')}"}
        return {"ok": False, "reason": "通道静默（固件未启用 RCSP 或需要鉴权）"}
    finally:
        rfcomm_close(s)


# --------------------------------------------------------------------------- #
# 通道 4：HFP HF Indicators（系统粗略值）
# --------------------------------------------------------------------------- #
def hfp_battery(worker, info: dict) -> dict:
    if not info.get("container_id"):
        return {"ok": False, "reason": "缺少容器 id"}
    r = worker.read_classic_battery(info["container_id"])
    if "error" in r:
        return {"ok": False, "reason": r["error"]}
    if r.get("battery") is None:
        return {"ok": False, "reason": "设备未通过 HFP 上报电量"}
    return {"ok": True, "battery": int(r["battery"]),
            "source": "HFP HF Indicators（系统值）"}


# --------------------------------------------------------------------------- #
# 组合读取：刷新时调用
# --------------------------------------------------------------------------- #
def read_battery(dev: dict, info: dict, worker, mode: str = "prefer_exact") -> dict:
    """返回 {battery, precision, source, detail, client, need_client}"""
    result = {"battery": None, "precision": NONE, "source": "",
              "detail": "", "need_client": False, "client": {}}
    c = client_info(dev)
    result["client"] = c

    if not info.get("connected"):
        result["detail"] = "设备未连接"
        return result

    # 1) 精确通道
    exact = None
    for fn, label in ((lambda: gatt_battery(worker, info, dev), "gatt"),):
        try:
            r = fn()
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "reason": str(e)}
        if r.get("ok"):
            exact = r
            break

    if exact is None:
        cached = dev.get("vendor_battery") or {}
        if cached.get("battery") is not None and \
                time.time() - float(cached.get("ts", 0)) < 900:
            exact = {"ok": True, "battery": int(cached["battery"]),
                     "source": cached.get("source", "厂商协议（缓存）")}

    if exact:
        result.update(battery=exact["battery"], precision=EXACT,
                      source=exact["source"], detail="")
        return result

    # 2) 粗略通道
    hfp = hfp_battery(worker, info)
    if hfp.get("ok"):
        result.update(battery=hfp["battery"], precision=COARSE,
                      source=hfp["source"],
                      detail="系统 HFP 上报值，可能滞后或不准确")
    else:
        result["detail"] = hfp.get("reason", "无法读取电量")

    # 3) 是否需要引导安装厂商客户端
    if result["precision"] != EXACT:
        if c.get("key") and not c.get("found"):
            result["need_client"] = True
        elif c.get("key") and c.get("found") and not c.get("has_pc_app"):
            result["detail"] = (result["detail"] + " · " if result["detail"] else "") \
                               + "该厂商无 PC 客户端，无法取得更精确数据"

    if mode == "exact_only" and result["precision"] != EXACT:
        result["battery"] = None
    return result


# --------------------------------------------------------------------------- #
# 深度探测：把设备所有可用通道摸一遍，产出报告（用于新增协议）
# --------------------------------------------------------------------------- #
def deep_probe(dev: dict, info: dict, worker) -> dict:
    mac = dev.get("address") or info.get("address")
    report = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "device": {"name": dev.get("name"), "model": dev.get("model", ""),
                   "address": f"{mac:012X}" if mac else "",
                   "is_le": bool(info.get("is_le")),
                   "connected": bool(info.get("connected"))},
        "vendor": vendor_key(dev),
        "client": client_info(dev, force=True),
        "channels": [],
    }

    def add(name, ok, detail, extra=None):
        item = {"channel": name, "ok": bool(ok), "detail": detail}
        if extra:
            item.update(extra)
        report["channels"].append(item)

    if mac is None:
        add("基础", False, "设备没有可用蓝牙地址")
        return report

    # 1) SDP 服务表
    sdp = worker.rfcomm_services(mac)
    svcs = sdp.get("services", [])
    if "error" in sdp:
        add("SDP 服务表", False, sdp["error"])
    else:
        add("SDP 服务表", True,
            "发现 %d 个服务：%s" % (len(svcs), ", ".join(
                f"{s['name'] or s['uuid'][:8]}" for s in svcs)), {"services": svcs})

    # 2) 厂商私有 SPP 通道探测
    for s in svcs:
        u = s["uuid"].lower()
        if u in (HFP_UUID, "0000110b-0000-1000-8000-00805f9b34fb",
                 "0000110c-0000-1000-8000-00805f9b34fb",
                 "0000110e-0000-1000-8000-00805f9b34fb",
                 "00001200-0000-1000-8000-00805f9b34fb"):
            continue
        r = jl_rcsp_battery(mac, u)
        add(f"厂商通道 {s['name'] or u[:8]}", r.get("ok"), r.get("reason") or r.get("source", ""),
            {"battery": r.get("battery")})

    # 3) GATT
    if info.get("is_le"):
        dump = worker.gatt_dump(mac)
        if "error" in dump:
            add("GATT 服务表", False, dump["error"])
        else:
            total = sum(len(x["chars"]) for x in dump.get("services", []))
            add("GATT 服务表", True,
                "发现 %d 个服务 / %d 个特征" % (len(dump.get("services", [])), total),
                {"gatt": dump})
    else:
        add("GATT 服务表", False, "纯经典蓝牙设备，没有 LE/GATT 通道")

    # 4) HFP 系统值
    h = hfp_battery(worker, info)
    add("HFP HF Indicators", h.get("ok"), h.get("reason") or h.get("source", ""),
        {"battery": h.get("battery")})

    # 5) 客户端
    c = report["client"]
    add("厂商客户端", bool(c.get("found")),
        (f"已安装：{', '.join(i['name'] for i in c.get('installed', []))}"
         if c.get("found") else f"未安装（{c.get('name') or '未收录厂商'}，"
                                f"平台：{c.get('platform') or '未知'}）"))
    return report


def save_report(report: dict) -> str:
    base = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                        "BatteryTray")
    os.makedirs(base, exist_ok=True)
    name = (report.get("device", {}).get("name") or "device").replace(" ", "_")
    path = os.path.join(base, f"protocol-report-{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return path
