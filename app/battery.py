"""蓝牙设备枚举与电量读取。

三条读取通道：
1. 经典蓝牙（A2DP/HFP 耳机音箱等）：蓝牙 AEP → 容器 → PnpObject 读
   DEVPKEY {104EA319-6EE2-4701-BD47-8DDBF425BBE5} 2（HFP HF Indicators 电量）。
2. 蓝牙 LE：GATT Battery Service (0x180F / 0x2A19) 直读。
3. 广播扫描：BluetoothLEAdvertisementWatcher（添加设备用）。

所有 WinRT 调用运行在专用线程的 asyncio 事件循环中，外部通过
BluetoothWorker.call(coro_fn, *args) 同步调用。
"""
from __future__ import annotations

import asyncio
import threading
import uuid
import re

# 经典蓝牙电量属性（HFP HF Indicators，社区/实测验证）
BATT_GUID = "{104EA319-6EE2-4701-BD47-8DDBF425BBE5}"
BATT_KEY = BATT_GUID + " 2"

BATTERY_SERVICE_UUID = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_CHAR_UUID = "00002a19-0000-1000-8000-00805f9b34fb"

_MAC_RE = re.compile(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}")


def parse_bt_address(dev_id: str):
    """从 AEP 设备 Id 解析蓝牙 MAC（取最后一个 MAC，即对端地址）。"""
    ms = _MAC_RE.findall(dev_id or "")
    if not ms:
        return None
    return int(ms[-1].replace(":", ""), 16)


def _cm_id_from_info_id(dev_id: str):
    r"""DeviceInformation id -> CM 设备实例 id。

    \\?\BTHENUM#{0000111E-...}_HCIBYPASS...#7&253b31b0&1&D4..._C00000000#{bd41df2d-...}
    -> BTHENUM\{0000111E-...}_HCIBYPASS...\7&253b31b0&1&D4..._C00000000
    """
    if not dev_id.startswith("\\\\?\\"):
        return None
    body = dev_id[4:]
    # 仅去掉末尾的 #{interface-guid}（可能还带 \service 等后缀）
    idx = body.rfind("#{")
    if idx != -1:
        body = body[:idx]
    return body.replace("#", "\\")


def _unwrap(v):
    """winrt.system.Object（装箱值）-> Python 值。"""
    if v is None:
        return None
    try:
        from winrt.windows.foundation import IPropertyValue
        pv = v.as_(IPropertyValue)
        t = pv.type
        if t == 11:
            return pv.get_boolean()
        if t in (3, 16):  # Guid（不同系统版本枚举值有差异，双分支 + 回退）
            try:
                return str(pv.get_guid())
            except Exception:
                return pv.get_string()
        if t == 7:
            return pv.get_uint32()
        if t == 5:
            return pv.get_int32()
        if t == 12:
            return pv.get_string()
        if t == 0:
            return None
        # 其余数值类型逐个尝试
        for getter in ("get_uint8", "get_int16", "get_uint16", "get_int32",
                       "get_uint32", "get_int64", "get_uint64"):
            try:
                return getattr(pv, getter)()
            except Exception:
                continue
        return None
    except Exception:
        return None


class BluetoothWorker:
    """后台线程 + asyncio 事件循环，串行执行 WinRT 协程。"""

    def __init__(self):
        self._loop = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="bt-worker")
        self._thread.start()
        self._ready.wait(10)

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def call(self, coro_fn, *args, timeout=30):
        """同步调用协程；失败返回 {'error': ...}。"""
        if not self._loop:
            return {"error": "worker 未启动"}
        fut = asyncio.run_coroutine_threadsafe(coro_fn(*args), self._loop)
        try:
            return fut.result(timeout)
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}

    # ------------------------------------------------------------------ #
    # 枚举蓝牙设备（经典 + LE 端点）
    # ------------------------------------------------------------------ #
    async def _list_devices(self):
        from winrt.windows.devices.bluetooth import BluetoothDevice, BluetoothLEDevice
        from winrt.windows.devices.enumeration import DeviceInformation

        props = ["System.ItemNameDisplay", "System.Devices.Aep.IsConnected",
                 "System.Devices.Aep.IsPaired", "System.Devices.Aep.ContainerId"]
        out, seen = [], set()
        for selector, is_le in (
                (BluetoothDevice.get_device_selector(), False),
                (BluetoothLEDevice.get_device_selector(), True),
        ):
            try:
                devs = await DeviceInformation.find_all_async(selector, props)
            except Exception:
                continue
            for d in devs:
                if d.id in seen:
                    continue
                seen.add(d.id)
                p = d.properties
                out.append({
                    "aep_id": d.id,
                    "name": d.name or _unwrap(p.get("System.ItemNameDisplay")) or "未命名设备",
                    "address": parse_bt_address(d.id),
                    "container_id": _unwrap(p.get("System.Devices.Aep.ContainerId")) or "",
                    "connected": bool(_unwrap(p.get("System.Devices.Aep.IsConnected"))),
                    "paired": bool(_unwrap(p.get("System.Devices.Aep.IsPaired"))),
                    "is_le": is_le,
                    "battery_prop": None,
                })
        return {"devices": out}

    def list_devices(self):
        return self.call(self._list_devices)

    # ------------------------------------------------------------------ #
    # 通道 1：经典蓝牙 —— 容器内 PNP 设备的 HFP 电量属性
    # ------------------------------------------------------------------ #
    async def _read_classic_battery(self, container_id: str):
        from winrt.windows.devices.enumeration import DeviceInformation
        from winrt.windows.devices.enumeration.pnp import PnpObject, PnpObjectType

        if not container_id:
            return {"battery": None, "error": "无容器 id"}
        try:
            devs = await DeviceInformation.find_all_async(
                f"System.Devices.ContainerId:={{{container_id}}}",
                ["System.ItemNameDisplay"])
        except Exception as e:
            return {"battery": None, "error": str(e)}

        # 优先 HFP(111e)/HID 类接口设备，其余 BTHENUM 兜底
        candidates = []
        for d in devs:
            if "BTHENUM" not in d.id and "BTHLE" not in d.id:
                continue
            cm = _cm_id_from_info_id(d.id)
            if not cm:
                continue
            prio = 0 if ("111E" in cm.upper() or "1124" in cm.upper() or "Dev_" in cm) else 1
            candidates.append((prio, cm))
        candidates.sort()

        for _, cm in candidates:
            try:
                o = await PnpObject.create_from_id_async(
                    PnpObjectType.DEVICE, cm, [BATT_KEY])
                v = _unwrap(o.properties.get(BATT_KEY))
                try:
                    o.close()
                except Exception:
                    pass
                if isinstance(v, int) and 0 <= v <= 100:
                    return {"battery": v}
            except Exception:
                continue
        return {"battery": None, "error": "未找到电量属性（设备可能未上报电量）"}

    def read_classic_battery(self, container_id: str):
        return self.call(self._read_classic_battery, container_id)

    # ------------------------------------------------------------------ #
    # 通道 2：蓝牙 LE —— GATT 电量服务直读
    # ------------------------------------------------------------------ #
    async def _read_gatt_battery(self, address: int):
        from winrt.windows.devices.bluetooth import BluetoothLEDevice
        from winrt.windows.storage.streams import DataReader

        dev = await BluetoothLEDevice.from_bluetooth_address_async(int(address))
        if dev is None:
            return {"battery": None, "error": "无法连接（设备可能不支持 LE 或未开机）"}
        try:
            res = await dev.get_gatt_services_for_uuid_async(
                uuid.UUID(BATTERY_SERVICE_UUID))
            for svc in res.services:
                chars = await svc.get_characteristics_for_uuid_async(
                    uuid.UUID(BATTERY_CHAR_UUID))
                for ch in chars.characteristics:
                    rr = await ch.read_value_async()
                    if rr.status == 0:  # Success
                        reader = DataReader.from_buffer(rr.value)
                        return {"battery": int(reader.read_byte())}
            return {"battery": None, "error": "设备未暴露 GATT 电量服务"}
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def read_gatt_battery(self, address: int):
        return self.call(self._read_gatt_battery, address)

    # ------------------------------------------------------------------ #
    # BLE 广播扫描（添加设备用）
    # ------------------------------------------------------------------ #
    async def _scan_le(self, duration: float = 8.0):
        from winrt.windows.devices.bluetooth.advertisement import (
            BluetoothLEAdvertisementWatcher,
        )

        found: dict[int, dict] = {}

        def on_received(sender, args):
            addr = args.bluetooth_address
            adv = args.advertisement
            rec = found.setdefault(addr, {"address": addr, "name": "", "rssi": -999})
            name = adv.local_name or ""
            if name and (not rec["name"] or name != rec["name"]):
                rec["name"] = name
            try:
                rec["rssi"] = max(rec["rssi"], args.raw_signal_strength_in_d_bm)
            except Exception:
                pass

        watcher = BluetoothLEAdvertisementWatcher()
        try:
            watcher.scanning_mode = 1  # Active
        except Exception:
            pass
        token = watcher.add_received(on_received)
        watcher.start()
        try:
            await asyncio.sleep(duration)
        finally:
            try:
                watcher.stop()
            except Exception:
                pass
            try:
                watcher.remove_received(token)
            except Exception:
                pass
        out = sorted(found.values(), key=lambda r: -r["rssi"])
        out = [r for r in out if r["name"]]
        return {"devices": out}

    def scan_le(self, duration: float = 8.0):
        return self.call(self._scan_le, duration, timeout=duration + 20)

    # ------------------------------------------------------------------ #
    # SDP：经典蓝牙服务表（找厂商私有通道）
    # ------------------------------------------------------------------ #
    async def _rfcomm_services(self, mac: int):
        from winrt.windows.devices.bluetooth import BluetoothDevice

        dev = await BluetoothDevice.from_bluetooth_address_async(int(mac))
        if dev is None:
            return {"error": "无法打开设备（可能未连接）", "services": []}
        out = []
        try:
            res = await dev.get_rfcomm_services_async()
            for s in res.services:
                item = {"uuid": str(s.service_id.uuid), "name": ""}
                try:
                    attrs = await s.get_sdp_raw_attributes_async()
                    raw = attrs.get(0x0100)
                    if raw is not None:
                        b = bytes(raw)
                        text = "".join(chr(c) for c in b if 32 <= c < 127)
                        item["name"] = text.lstrip("%\x06\x05\x00")[:40]
                except Exception:
                    pass
                out.append(item)
                try:
                    s.close()
                except Exception:
                    pass
        except Exception as e:  # noqa: BLE001
            return {"error": f"SDP 查询失败：{e}", "services": []}
        finally:
            try:
                dev.close()
            except Exception:
                pass
        return {"services": out}

    def rfcomm_services(self, mac: int):
        return self.call(self._rfcomm_services, mac)

    # ------------------------------------------------------------------ #
    # GATT 全量导出（找厂商私有电量特征）
    # ------------------------------------------------------------------ #
    async def _gatt_dump(self, address: int):
        from winrt.windows.devices.bluetooth import BluetoothLEDevice
        from winrt.windows.storage.streams import DataReader

        dev = await BluetoothLEDevice.from_bluetooth_address_async(int(address))
        if dev is None:
            return {"error": "无法连接 LE 设备"}
        out = []
        try:
            res = await dev.get_gatt_services_async()
            for svc in res.services:
                item = {"uuid": str(svc.uuid), "chars": []}
                try:
                    cr = await svc.get_characteristics_async()
                except Exception:
                    out.append(item)
                    continue
                for ch in cr.characteristics:
                    cinfo = {"uuid": str(ch.uuid), "value": None, "error": ""}
                    try:
                        rr = await ch.read_value_async()
                        if rr.status == 0 and rr.value is not None:
                            buf = bytearray(rr.value.length)
                            DataReader.from_buffer(rr.value).read_bytes(buf)
                            cinfo["value"] = bytes(buf)
                            cinfo["hex"] = bytes(buf).hex(" ")
                    except Exception as e:  # noqa: BLE001
                        cinfo["error"] = str(e)[:60]
                    item["chars"].append(cinfo)
                out.append(item)
        finally:
            try:
                dev.close()
            except Exception:
                pass
        return {"services": out}

    def gatt_dump(self, address: int):
        return self.call(self._gatt_dump, address, timeout=60)
