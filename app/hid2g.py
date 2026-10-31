"""USB / 2.4G 无线外设识别与电量读取（纯 ctypes，无第三方依赖）。

为什么需要这个模块
------------------
Windows 设置页只对**蓝牙**设备显示电量；2.4G（自带 USB 接收器）的设备在系统里
只是一个 USB HID 复合设备，系统不会给出任何电量，必须由应用直接问设备要。

三条读取通道
------------
1. 标准 HID Battery Strength —— 功能报告里的 Usage Page 0x06 / Usage 0x20，
   主机用 HidP_GetUsageValue 解析。部分 2.4G 键鼠 / 手柄实现了这一页。
2. Logitech HID++ 2.0 —— 0x1000 电量状态（放电百分比）、0x1001 电压（粗略换算）。
3. Logitech HID++ 1.0 —— 寄存器 0x0D 电量里程（真实百分比）、0x07 电量等级（4 档）。

识别逻辑
--------
枚举 HID 设备 → 按「物理父设备」归组（同一个接收器插出来的键鼠是同一物理设备）→
标记连接方式（2.4G 接收器 / USB 有线 / 蓝牙 HID）→ 把能读到的电量带出来。
读不到电量时如实返回「该设备不上报电量」，绝不编造数值。
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

# --------------------------------------------------------------------------- #
# Win32 绑定
# --------------------------------------------------------------------------- #
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
cfgmgr = ctypes.WinDLL("cfgmgr32", use_last_error=True)

GENERIC_WRITE = 0x40000000
GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3
INVALID_HANDLE = ctypes.c_void_p(-1).value

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10

HIDP_REPORT_TYPE_FEATURE = 2
HIDP_STATUS_SUCCESS = 0x00110000


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    def __str__(self):
        d4 = "".join(f"{b:02x}" for b in self.Data4)
        return f"{{{self.Data1:08x}-{self.Data2:04x}-{self.Data3:04x}-{d4[:4]}-{d4[4:]}}}"


class _DEVPROPKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", wintypes.DWORD)]


class _SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", _GUID),
                ("DevInst", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]


class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", _GUID),
                ("Flags", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]


class _HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wintypes.ULONG), ("VendorID", wintypes.USHORT),
                ("ProductID", wintypes.USHORT), ("VersionNumber", wintypes.USHORT)]


class _HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", wintypes.USHORT), ("UsagePage", wintypes.USHORT),
                ("InputReportByteLength", wintypes.USHORT),
                ("OutputReportByteLength", wintypes.USHORT),
                ("FeatureReportByteLength", wintypes.USHORT),
                ("Reserved", wintypes.USHORT * 17),
                ("NumberLinkCollectionNodes", wintypes.ULONG),
                ("NumberInputButtonCaps", wintypes.USHORT),
                ("NumberInputValueCaps", wintypes.USHORT),
                ("NumberInputDataIndices", wintypes.USHORT),
                ("NumberOutputButtonCaps", wintypes.USHORT),
                ("NumberOutputValueCaps", wintypes.USHORT),
                ("NumberOutputDataIndices", wintypes.USHORT),
                ("NumberFeatureButtonCaps", wintypes.USHORT),
                ("NumberFeatureValueCaps", wintypes.USHORT),
                ("NumberFeatureDataIndices", wintypes.USHORT)]


GUID_DEVINTERFACE_HID = _GUID(
    0x4D1E55B2, 0xF16F, 0x11CF,
    (ctypes.c_ubyte * 8)(0x88, 0xCB, 0x00, 0x11, 0x11, 0x00, 0x00, 0x30))

_K_FMT = 0xA45C254E
DEVPKEY_NAME = _DEVPROPKEY(_GUID(_K_FMT, 0xDF1C, 0x4EFD,
                                 (ctypes.c_ubyte * 8)(0x80, 0x20, 0x67, 0xD1,
                                                      0x46, 0xA8, 0x50, 0xE0)), 14)
DEVPKEY_DESC = _DEVPROPKEY(_GUID(_K_FMT, 0xDF1C, 0x4EFD,
                                 (ctypes.c_ubyte * 8)(0x80, 0x20, 0x67, 0xD1,
                                                      0x46, 0xA8, 0x50, 0xE0)), 2)
DEVPKEY_CONTAINER = _DEVPROPKEY(_GUID(0x8C7ED206, 0x3F8A, 0x4827,
                                      (ctypes.c_ubyte * 8)(0xB3, 0xAB, 0xAE, 0x9E,
                                                           0x1F, 0xAE, 0xFC, 0x6C)), 2)

setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(_GUID), wintypes.LPCWSTR,
                                          wintypes.HWND, wintypes.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_GUID), wintypes.DWORD,
    ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(_SP_DEVINFO_DATA)]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(_SP_DEVINFO_DATA), wintypes.LPWSTR,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
setupapi.SetupDiGetDevicePropertyW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(_SP_DEVINFO_DATA), ctypes.POINTER(_DEVPROPKEY),
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]

k32.CreateFileW.restype = wintypes.HANDLE
k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                            wintypes.HANDLE]
k32.CloseHandle.argtypes = [wintypes.HANDLE]
k32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                          ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]

hid.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(_HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(_HIDP_CAPS)]
hid.HidD_GetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
hid.HidD_SetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
hid.HidD_GetInputReport.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
hid.HidD_GetProductString.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
hid.HidD_GetManufacturerString.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
hid.HidP_GetUsageValue.argtypes = [wintypes.INT, wintypes.USHORT, wintypes.USHORT,
                                   wintypes.USHORT, ctypes.POINTER(wintypes.ULONG),
                                   ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG]

cfgmgr.CM_Get_Device_IDW.argtypes = [wintypes.DWORD, wintypes.LPWSTR, wintypes.ULONG,
                                     wintypes.ULONG]
cfgmgr.CM_Get_Parent.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD,
                                 wintypes.ULONG]
cfgmgr.CM_Locate_DevNodeW.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.LPCWSTR,
                                      wintypes.ULONG]
cfgmgr.CM_Get_DevNode_PropertyW.argtypes = [
    wintypes.DWORD, ctypes.POINTER(_DEVPROPKEY), ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG), wintypes.ULONG]


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _open_hid(path: str, write: bool = False):
    """打开 HID 集合。write=True 用于 HID++ 收发（需要写权限）。"""
    access = (GENERIC_READ | GENERIC_WRITE) if write else 0
    h = k32.CreateFileW(path, access, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                        OPEN_EXISTING, 0, None)
    if h == INVALID_HANDLE and write:
        h = k32.CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                            None, OPEN_EXISTING, 0, None)
    if h == INVALID_HANDLE:
        h = k32.CreateFileW(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                            OPEN_EXISTING, 0, None)
    return None if h == INVALID_HANDLE else h


def _close(h):
    if h:
        try:
            k32.CloseHandle(h)
        except Exception:
            pass


def _hid_string(h, fn) -> str:
    buf = ctypes.create_unicode_buffer(128)
    try:
        if fn(h, ctypes.cast(buf, ctypes.c_void_p), 256):
            return buf.value.strip("\x00 ").strip()
    except Exception:
        pass
    return ""


def _capture(func, *args, timeout=1.2, default=None):
    """HID 读取偶尔会卡在驱动里，用带超时的小线程兜住，绝不阻塞刷新。"""
    import threading
    box = {}

    def run():
        try:
            box["v"] = func(*args)
        except Exception as e:  # noqa: BLE001
            box["e"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return default
    return box.get("v", default)


def _dev_prop(hdev, di, key):
    t = wintypes.DWORD()
    need = wintypes.DWORD()
    buf = ctypes.create_string_buffer(1024)
    ok = setupapi.SetupDiGetDevicePropertyW(hdev, ctypes.byref(di), ctypes.byref(key),
                                            ctypes.byref(t), buf, 1024,
                                            ctypes.byref(need), 0)
    if not ok or need.value == 0:
        return None
    raw = buf.raw[:need.value]
    if t.value == 0x12:      # DEVPROP_TYPE_STRING
        return raw.decode("utf-16-le", "ignore").split("\x00")[0]
    if t.value == 0x19:      # DEVPROP_TYPE_STRING_LIST
        return raw.decode("utf-16-le", "ignore").split("\x00")[0]
    if t.value == 0x0D:      # DEVPROP_TYPE_GUID
        return str(_GUID.from_buffer_copy(raw[:16]))
    return None


def _parent_chain(devinst: int, depth: int = 6) -> list[str]:
    out, cur = [], devinst
    for _ in range(depth):
        buf = ctypes.create_unicode_buffer(512)
        if cfgmgr.CM_Get_Device_IDW(cur, buf, 512, 0) != 0:
            break
        out.append(buf.value)
        parent = wintypes.DWORD()
        if cfgmgr.CM_Get_Parent(ctypes.byref(parent), cur, 0) != 0:
            break
        cur = parent.value
    return out


def _node_prop(node: int, key) -> str:
    t = wintypes.DWORD()
    size = wintypes.ULONG(512)
    buf = ctypes.create_string_buffer(512)
    if cfgmgr.CM_Get_DevNode_PropertyW(node, ctypes.byref(key), ctypes.byref(t),
                                       buf, ctypes.byref(size), 0) != 0:
        return ""
    if t.value in (0x12, 0x19):
        return buf.raw[:size.value].decode("utf-16-le", "ignore").split("\x00")[0]
    return ""


def _node_name(instance_id: str) -> str:
    """USB 设备节点自己的名字（如 “Logitech USB Input Device”）——比 HID 接口名有用。"""
    if not instance_id:
        return ""
    node = _devnode_of(instance_id)
    if not node:
        return ""
    return _node_prop(node, DEVPKEY_NAME) or _node_prop(node, DEVPKEY_DESC)


def _devnode_of(instance_id: str):
    node = wintypes.DWORD()
    if cfgmgr.CM_Locate_DevNodeW(ctypes.byref(node), instance_id, 0) == 0:
        return node.value
    return 0


# --------------------------------------------------------------------------- #
# HID 集合枚举
# --------------------------------------------------------------------------- #
def _enum_collections() -> list[dict]:
    """枚举所有 HID 顶级集合，带 caps / 厂商 / 父设备链。"""
    hdev = setupapi.SetupDiGetClassDevsW(ctypes.byref(GUID_DEVINTERFACE_HID), None, None,
                                         DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if not hdev or hdev == INVALID_HANDLE:
        return []
    out: list[dict] = []
    idx = 0
    try:
        while True:
            did = _SP_DEVICE_INTERFACE_DATA()
            did.cbSize = ctypes.sizeof(did)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                    hdev, None, ctypes.byref(GUID_DEVINTERFACE_HID), idx,
                    ctypes.byref(did)):
                break
            idx += 1
            need = wintypes.DWORD()
            di = _SP_DEVINFO_DATA()
            di.cbSize = ctypes.sizeof(di)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(did), None, 0, ctypes.byref(need), ctypes.byref(di))
            if need.value <= 0:
                continue
            buf = ctypes.create_string_buffer(need.value)
            # SP_DEVICE_INTERFACE_DETAIL_DATA_W.cbSize = 8（64 位）
            ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = 8
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                    hdev, ctypes.byref(did), buf, need.value, ctypes.byref(need),
                    ctypes.byref(di)):
                continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)

            iid = ctypes.create_unicode_buffer(512)
            n = wintypes.DWORD()
            setupapi.SetupDiGetDeviceInstanceIdW(hdev, ctypes.byref(di), iid, 512,
                                                 ctypes.byref(n))
            item = {
                "path": path,
                "instance_id": iid.value,
                "friendly": _dev_prop(hdev, di, DEVPKEY_NAME) or "",
                "desc": _dev_prop(hdev, di, DEVPKEY_DESC) or "",
                "container_id": _dev_prop(hdev, di, DEVPKEY_CONTAINER) or "",
                "chain": _parent_chain(di.DevInst),
                "vid": 0, "pid": 0, "product": "", "manufacturer": "",
                "usage_page": 0, "usage": 0,
                "in_len": 0, "out_len": 0, "feat_len": 0,
            }
            h = _open_hid(path)
            if h:
                attrs = _HIDD_ATTRIBUTES()
                attrs.Size = ctypes.sizeof(attrs)
                if hid.HidD_GetAttributes(h, ctypes.byref(attrs)):
                    item["vid"], item["pid"] = attrs.VendorID, attrs.ProductID
                item["product"] = _hid_string(h, hid.HidD_GetProductString)
                item["manufacturer"] = _hid_string(h, hid.HidD_GetManufacturerString)
                pre = ctypes.c_void_p()
                if hid.HidD_GetPreparsedData(h, ctypes.byref(pre)):
                    caps = _HIDP_CAPS()
                    if hid.HidP_GetCaps(pre, ctypes.byref(caps)) == HIDP_STATUS_SUCCESS:
                        item.update(usage_page=caps.UsagePage, usage=caps.Usage,
                                    in_len=caps.InputReportByteLength,
                                    out_len=caps.OutputReportByteLength,
                                    feat_len=caps.FeatureReportByteLength)
                    hid.HidD_FreePreparsedData(pre)
                _close(h)
            out.append(item)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return out


# --------------------------------------------------------------------------- #
# Logitech HID++ 协议
# --------------------------------------------------------------------------- #
LT_VID = 0x046D
HIDPP_SHORT = 0x10   # 7 字节
HIDPP_LONG = 0x11    # 20 字节一
E_OK = 0


def _hidpp_write(h, frame: bytes, size: int) -> bool:
    buf = (ctypes.c_ubyte * size)()
    for i, b in enumerate(frame[:size]):
        buf[i] = b
    written = wintypes.DWORD()
    try:
        ok = k32.WriteFile(h, buf, size, ctypes.byref(written), None)
        return bool(ok)
    except Exception:
        return False


def _hidpp_read(h, rid: int, size: int):
    """取回一条报告：先试 input report（设备主动上报），再试 feature report（接收器缓存）。"""
    for attempt in range(10):
        buf = (ctypes.c_ubyte * size)()
        buf[0] = rid
        try:
            if hid.HidD_GetInputReport(h, buf, size):
                got = bytes(buf)
                if got[0] == rid and got[1] != 0:
                    return got
        except Exception:
            pass
        try:
            buf2 = (ctypes.c_ubyte * size)()
            buf2[0] = rid
            if hid.HidD_GetFeature(h, buf2, size):
                got = bytes(buf2)
                if got[0] == rid and got[1] != 0:
                    return got
        except Exception:
            pass
        time.sleep(0.012 + attempt * 0.004)
    return None


def _hidpp_txn(h, dev_idx: int, byte2: int, byte3: int, params: bytes = b"",
               long_msg: bool = True):
    """发一条 HID++ 请求并等回显匹配的响应。返回字节串或 None。"""
    rid = HIDPP_LONG if long_msg else HIDPP_SHORT
    size = 20 if long_msg else 7
    frame = bytearray(size)
    frame[0] = rid
    frame[1] = dev_idx & 0xFF
    frame[2] = byte2 & 0xFF
    frame[3] = byte3 & 0xFF
    for i, b in enumerate(params[:size - 4]):
        frame[4 + i] = b
    if not _hidpp_write(h, bytes(frame), size):
        return None
    for _ in range(8):
        resp = _hidpp_read(h, rid, size)
        if not resp:
            return None
        if resp[1] == frame[1] and resp[2] == frame[2] and resp[3] == frame[3]:
            return resp
        time.sleep(0.01)
    return None


def _feature_index(h, dev_idx: int, swid: int, feature_id: int) -> int:
    """HID++ 2.0：用 ROOT(0x0000) 解析特性的运行时索引；0 表示不支持。"""
    r = _hidpp_txn(h, dev_idx, 0x00, (0 << 4) | swid,
                   bytes([feature_id >> 8, feature_id & 0xFF]))
    if not r or r[2] == 0xFF:      # 0xFF = 错误应答
        return 0
    return r[4]


def _h2_battery(h, dev_idx: int, swid: int):
    """HID++ 2.0 电量：0x1000 放电百分比 → 0x1001 电压换算。"""
    idx = _feature_index(h, dev_idx, swid, 0x1000)
    if idx:
        r = _hidpp_txn(h, dev_idx, idx, (0 << 4) | swid)
        if r and r[2] != 0xFF:
            level, status = r[4], r[6]
            # status: 0 放电 / 1 充电 / 2 快满 / 3 已满 / 4 慢充
            if status == 3:
                return {"battery": 100, "precision": "exact",
                        "source": "Logitech HID++ 2.0（电量状态）"}
            if 0 <= level <= 100:
                return {"battery": level, "precision": "exact",
                        "source": "Logitech HID++ 2.0（电量状态）"}
    idx = _feature_index(h, dev_idx, swid, 0x1001)
    if idx:
        r = _hidpp_txn(h, dev_idx, idx, (0 << 4) | swid)
        if r and r[2] != 0xFF:
            mv = (r[4] << 8) | r[5]
            if 3000 <= mv <= 5000:
                return {"battery": _mv_to_pct(mv), "precision": "coarse",
                        "source": f"Logitech HID++ 2.0（电压 {mv} mV 换算）"}
    return None


def _mv_to_pct(mv: int) -> int:
    """锂电池电压 → 百分比（经验曲线，仅用于粗略估计）。"""
    table = [(3550, 5), (3650, 15), (3700, 30), (3750, 45), (3800, 55),
             (3850, 65), (3900, 72), (3950, 78), (4000, 85), (4050, 90),
             (4100, 95), (4200, 100)]
    for v, p in table:
        if mv <= v:
            return p
    return 100


def _h10_battery(h, dev_idx: int):
    """HID++ 1.0 电量：寄存器 0x0D（真实百分比）→ 0x07（4 档）。"""
    r = _hidpp_txn(h, dev_idx, 0x81, 0x0D, b"", long_msg=False)
    if r and r[2] == 0x81 and 0 <= r[5] <= 100 and r[6] in (0, 0x40, 0x80, 0xC0):
        return {"battery": r[5], "precision": "exact",
                "source": "Logitech HID++ 1.0（电量里程 0x0D）"}
    r = _hidpp_txn(h, dev_idx, 0x81, 0x07, b"", long_msg=False)
    if r and r[2] == 0x81:
        lvl = r[5]
        if 1 <= lvl <= 7:
            pct = {1: 10, 2: 15, 3: 35, 4: 45, 5: 65, 6: 75, 7: 100}[lvl]
            return {"battery": pct, "precision": "coarse",
                    "source": "Logitech HID++ 1.0（电量等级 0x07，4 档）"}
    return None


def _h2_name(h, dev_idx: int, swid: int) -> str:
    """HID++ 2.0 DEVICE_NAME(0x1005)：fn0 取长度，fn1 取每 16 字节一片。"""
    idx = _feature_index(h, dev_idx, swid, 0x0005)
    if not idx:
        return ""
    r = _hidpp_txn(h, dev_idx, idx, (0 << 4) | swid)
    if not r or r[2] == 0xFF:
        return ""
    ln = r[4]
    if not (0 < ln <= 64):
        return ""
    out = b""
    pos = 0
    while len(out) < ln and pos < 4:
        r = _hidpp_txn(h, dev_idx, idx, (1 << 4) | swid, bytes([pos]))
        if not r or r[2] == 0xFF:
            break
        chunk = bytes(r[5:5 + min(16, ln - len(out))])
        if not chunk:
            break
        out += chunk
        pos += 16
    return out.decode("utf-8", "ignore").strip("\x00").strip()


def _logitech_battery(h, dev_idx: int, swid: int = 5):
    try:
        r = _h2_battery(h, dev_idx, swid)
        if r:
            return r
    except Exception:
        pass
    try:
        return _h10_battery(h, dev_idx)
    except Exception:
        return None


def _logitech_scan(col: dict) -> list[dict]:
    """Logitech 接收器：逐个通道问电量，一个接收器可带多台设备。"""
    h = _open_hid(col["path"], write=True)
    if not h:
        return []
    found = []
    try:
        # 0xFF = 接收器自身 / 直连设备；1~6 = 接收器里的配对通道
        for dev_idx in (0xFF, 1, 2, 3, 4, 5, 6):
            info = _logitech_battery(h, dev_idx)
            if not info:
                continue
            name = ""
            try:
                name = _h2_name(h, dev_idx, 5)
            except Exception:
                name = ""
            info.update(dev_idx=dev_idx, name=name)
            found.append(info)
    finally:
        _close(h)
    return found


# --------------------------------------------------------------------------- #
# 标准 HID Battery Strength（0x06 / 0x20）
# --------------------------------------------------------------------------- #
def _hid_battery(col: dict):
    h = _open_hid(col["path"])
    if not h:
        return None
    pre = ctypes.c_void_p()
    try:
        if not hid.HidD_GetPreparsedData(h, ctypes.byref(pre)):
            return None
        caps = _HIDP_CAPS()
        if hid.HidP_GetCaps(pre, ctypes.byref(caps)) != HIDP_STATUS_SUCCESS:
            return None
        if caps.FeatureReportByteLength < 2:
            return None
        size = caps.FeatureReportByteLength
        for rid in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12):
            buf = (ctypes.c_ubyte * size)()
            buf[0] = rid
            try:
                if not hid.HidD_GetFeature(h, buf, size):
                    continue
            except Exception:
                continue
            val = wintypes.ULONG()
            st = hid.HidP_GetUsageValue(HIDP_REPORT_TYPE_FEATURE, 0x06, 0, 0x20,
                                        ctypes.byref(val), pre, buf, size)
            if st == HIDP_STATUS_SUCCESS and 0 <= val.value <= 100:
                return {"battery": int(val.value), "precision": "exact",
                        "source": "标准 HID 电量页（Battery Strength 0x06/0x20）"}
        return None
    finally:
        if pre:
            hid.HidD_FreePreparsedData(pre)
        _close(h)


# --------------------------------------------------------------------------- #
# 归组 / 分类
# --------------------------------------------------------------------------- #
WIRELESS_HINTS = ("receiver", "dongle", "wireless", "2.4g", "2.4 g", "unifying",
                  "lightspeed", "bolt", "无线", "接收器", "2.4", "g wireless")
KNOWN_RECEIVER_PIDS = {0xC52B, 0xC532, 0xC534, 0xC535, 0xC537, 0xC539, 0xC53F,
                       0xC547, 0xC548, 0xC54D, 0xC52E, 0xC52F, 0xC531}
TYPE_USAGE = {0x02: "mouse", 0x06: "keyboard", 0x05: "gamepad", 0x04: "gamepad"}


def _usb_parent(chain: list[str]) -> str:
    """取整台 USB 设备（不是某个 MI_x 接口、也不是根集线器）的实例 id。"""
    usb = [c for c in chain if c.upper().startswith("USB\\")]
    for c in usb:
        if "&MI_" not in c.upper() and "ROOT_HUB" not in c.upper():
            return c
    for c in usb:
        if "&MI_" not in c.upper():
            return c
    return usb[0] if usb else ""


def _is_bluetooth(chain: list[str]) -> bool:
    for c in chain:
        u = c.upper()
        if "BTHENUM" in u or "BTHLE" in u or "BTHHFENUM" in u:
            return True
    return False


# 内置 HID 里对用户毫无意义的系统集合（传感器 / 控制中心 / 虚拟设备等）
_INTERNAL_NOISE = ("传感器", "sensor", "用户控制", "系统控制", "供应商定义",
                   "无线通信控制", "consumer control", "system control",
                   "vendor-defined", "便携式设备", "hidclass",
                   "converteddevice", "virtual", "umdf", "collection")


def _looks_wireless(*texts) -> bool:
    blob = " ".join(t or "" for t in texts).lower()
    return any(k in blob for k in WIRELESS_HINTS)


def _wireless_score(items: list[dict], name: str) -> int:
    """判断「USB 设备其实是 2.4G 接收器」的把握程度。

    有线外设很少同时具备：厂商自定义集合 + 键鼠集合 + 无线字样的名字。
    """
    score = 0
    first = items[0]
    if first["pid"] in KNOWN_RECEIVER_PIDS and first["vid"] == LT_VID:
        score += 3
    if _looks_wireless(name, *(x["product"] for x in items),
                       *(x["friendly"] for x in items),
                       *(x["desc"] for x in items)):
        score += 2
    usages = {x["usage"] for x in items if x["usage_page"] == 0x01}
    has_vendor = any(x["usage_page"] == 0xFF00 for x in items)
    if has_vendor and usages & {0x02, 0x06, 0x04}:
        score += 1
    if has_vendor and first["vid"] == LT_VID and any(
            x["out_len"] >= 7 for x in items if x["usage_page"] == 0xFF00):
        score += 2
    # 注：键鼠同体也可能是「游戏鼠标的多媒体键」，不足以单独判成 2.4G
    return score


def _clean_name(*cands) -> str:
    generic = ("符合 hid 标准的", "hid-compliant", "usb 输入设备", "usb input device",
               "usb 复合设备", "usb composite device", "hid keyboard device",
               "hid-compliant mouse")
    for c in cands:
        c = (c or "").strip()
        if not c:
            continue
        if any(c.lower().startswith(g) or c.lower() == g for g in generic):
            continue
        return c
    for c in cands:
        if (c or "").strip():
            return c.strip()
    return ""


def _dev_type_of(items: list[dict], name: str) -> str:
    n = (name or "").lower()
    usages = {x["usage"] for x in items if x["usage_page"] == 0x01}
    if "mouse" in n or "鼠标" in n:
        return "mouse"
    if "keyboard" in n or "键盘" in n:
        return "keyboard"
    if "gamepad" in n or "手柄" in n or "controller" in n:
        return "gamepad"
    if "headset" in n or "headphone" in n or "耳机" in n:
        return "headset"
    for u in (0x02, 0x06, 0x05, 0x04):     # mouse > keyboard > gamepad
        if u in usages:
            return TYPE_USAGE[u]
    if any(x["usage_page"] == 0xFF00 for x in items):
        return "dongle"
    return "generic"


def _group(cols: list[dict]) -> list[dict]:
    """把一个接收器插出来的多个 HID 集合归成一个物理设备。"""
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for c in cols:
        key = _usb_parent(c["chain"]) or \
            c["instance_id"].upper().split("&COL")[0]
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(c)

    out = []
    for key in order:
        items = groups[key]
        first = items[0]
        chain = first["chain"]
        bt = _is_bluetooth(chain)
        usb_iid = _usb_parent(chain)
        usb = bool(usb_iid)
        node_name = _node_name(usb_iid) if usb_iid else ""
        name = _clean_name(*(x["product"] for x in items), node_name,
                           *(x["friendly"] for x in items),
                           *(x["desc"] for x in items)) or "未命名外设"
        if bt:
            transport, kind = "bt", "蓝牙"
        elif usb:
            # 严格度放低：USB 外设默认按 2.4G 无线处理，
            # 只有名字明确写 wired / 有线 才算 USB 有线。
            n = name.lower()
            if "wired" in n or "有线" in name:
                transport, kind = "usb", "USB 有线"
            else:
                transport, kind = "2.4g", "2.4G 无线"
        else:
            transport, kind = "other", "内置"

        # 能读电量的集合优先级：厂商页(FF00) 且带输出报告 → 有 feature 报告 → 其余
        ordered = sorted(items, key=lambda x: (x["usage_page"] != 0xFF00,
                                               x["out_len"] < 7,
                                               x["feat_len"] == 0))
        out.append({
            "key": key,
            "instance_id": first["instance_id"],
            "container_id": first["container_id"],
            "path": ordered[0]["path"],
            "paths": [x["path"] for x in items],
            "name": name,
            "transport": transport,
            "transport_name": kind,
            "vid": first["vid"], "pid": first["pid"],
            "dev_type": _dev_type_of(items, name),
            "usage_pages": sorted({x["usage_page"] for x in items}),
            "logitech": first["vid"] == LT_VID,
            "connected": True,
            "battery": None, "precision": "none", "source": "",
            "detail": "", "slots": [],
            "_cols": items,
        })
    return out


def _read_entry(entry: dict) -> dict:
    """给一个物理设备读电量（Logitech 接收器会连每台配对设备一起读）。"""
    cols = entry.get("_cols") or []
    # 1) Logitech HID++：接收器里的每台设备都有独立电量
    if entry.get("logitech"):
        for c in cols:
            if c["usage_page"] != 0xFF00 or c["out_len"] < 7:
                continue
            slots = _capture(_logitech_scan, c, timeout=8.0, default=None)
            if slots:
                paired = [s for s in slots if s.get("dev_idx") != 0xFF]
                direct = [s for s in slots if s.get("dev_idx") == 0xFF]
                if paired:
                    entry["slots"] = paired
                    s = paired[0]
                    entry.update(battery=s["battery"], precision=s["precision"],
                                 source=s["source"],
                                 detail=f"接收器通道 {s['dev_idx']}，同接收器共 "
                                        f"{len(paired)} 台设备")
                    return entry
                if direct:
                    s = direct[0]
                    entry.update(battery=s["battery"], precision=s["precision"],
                                 source=s["source"])
                    return entry
    # 2) 标准 HID 电量页（Battery Strength）
    for c in cols:
        if not c["feat_len"]:
            continue
        r = _capture(_hid_battery, c, timeout=3.0, default=None)
        if r:
            entry.update(r)
            return entry
    entry["detail"] = "该设备不向外提供电量（接收器与驱动都没有上报）"
    return entry


def _public(entry: dict, suffix: str = "", name: str = "", battery=None,
            precision=None, source=None) -> dict:
    d = {k: v for k, v in entry.items() if not k.startswith("_")}
    if suffix:
        d["key"] = f"{entry['key']}#{suffix}"
        d["instance_id"] = f"{entry['key']}#{suffix}"
    if name:
        d["name"] = name
        d["dev_type"] = _dev_type_of(entry.get("_cols") or [], name)
    if battery is not None:
        d["battery"] = battery
    if precision is not None:
        d["precision"] = precision
    if source is not None:
        d["source"] = source
    d.pop("slots", None)
    return d


def list_devices(include_bluetooth: bool = True) -> dict:
    """枚举当前所有 USB / 2.4G 外设，并带上能读到的电量。"""
    try:
        cols = _enum_collections()
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "devices": []}
    out: list[dict] = []
    for e in _group(cols):
        # 内置设备（I2C 触控板 / 虚拟 HID 等）也列出来，只是不做电量读取；
        # 但传感器、系统控制之类的纯内部集合对用户没意义，直接跳过。
        if e["transport"] == "other":
            blob = f"{e['name']} {e['key']}".lower()
            if any(k in blob for k in _INTERNAL_NOISE):
                continue
            out.append(_public(e))
            continue
        if not include_bluetooth and e["transport"] == "bt":
            continue
        if e["transport"] in ("usb", "2.4g"):
            try:
                _read_entry(e)
            except Exception as ex:  # noqa: BLE001
                e["detail"] = f"读取失败：{ex}"
            # 有线设备不可能上报电池电量 → 能报就是无线（2.4G 接收器）
            if e["transport"] == "usb" and e["battery"] is not None:
                e["transport"], e["transport_name"] = "2.4g", "2.4G 无线"
        if e.get("slots"):
            for s in e["slots"]:
                nm = s.get("name") or f"{e['name']} · 通道 {s['dev_idx']}"
                out.append(_public(e, suffix=str(s["dev_idx"]), name=nm,
                                   battery=s["battery"], precision=s["precision"],
                                   source=s["source"]))
        else:
            out.append(_public(e))
    out.sort(key=lambda x: (x["transport"] != "2.4g", x["name"].lower()))
    return {"devices": out}


def _find(entries: list[dict], instance_id: str, path: str, name: str):
    """按实例 id / 路径 / 名称定位物理设备，返回 (entry, slot 号或 0)。"""
    slot = 0
    base = instance_id or ""
    if "#" in base:
        base, _, tail = base.partition("#")
        if tail.isdigit():
            slot = int(tail)
    hit = None
    if base:
        for e in entries:
            if e["key"].lower() == base.lower():
                hit = e
                break
        if hit is None:
            for e in entries:
                if base.lower() in e["key"].lower() or e["key"].lower() in base.lower():
                    hit = e
                    break
    if hit is None and path:
        for e in entries:
            if path in e.get("paths", []):
                hit = e
                break
    if hit is None and name:
        for e in entries:
            if e["name"].lower() == name.lower():
                hit = e
                break
    return hit, slot


def read_battery(instance_id: str = "", path: str = "", name: str = "") -> dict:
    """刷新单个 2.4G / USB 设备：定位 → 读电量。设备拔掉时 connected=False。"""
    empty = {"battery": None, "precision": "none", "source": "",
             "detail": "设备未插入（接收器已拔出或已关机）", "connected": False}
    try:
        cols = _enum_collections()
    except Exception as e:  # noqa: BLE001
        return {**empty, "detail": f"枚举失败：{e}"}
    entries = _group(cols)
    hit, slot = _find(entries, instance_id, path, name)
    if hit is None:
        return empty
    _read_entry(hit)
    if slot:
        for s in hit.get("slots", []):
            if s["dev_idx"] == slot:
                return {"battery": s["battery"], "precision": s["precision"],
                        "source": s["source"],
                        "detail": f"接收器通道 {slot}", "connected": True}
        return {"battery": None, "precision": "none", "source": "",
                "detail": f"接收器通道 {slot} 当前无设备（已关机或未配对）",
                "connected": False}
    return {"battery": hit["battery"], "precision": hit["precision"],
            "source": hit["source"], "detail": hit["detail"], "connected": True}


def device_key_of(instance_id: str) -> str:
    """实例 id → 稳定的设备 id（去掉随 USB 端口变化的尾部）。"""
    iid = (instance_id or "").strip()
    if not iid:
        return ""
    base = iid.upper().split("&COL")[0]
    parts = base.split("\\")
    if len(parts) >= 2:
        # USB\VID_xxx&PID_xxx\5&2f1a3c4d&0&6 → 端口号会变，只保留 VID/PID 段
        return f"usb:{parts[0].upper()}\\{parts[1].upper()}"
    return f"usb:{base.upper()}"
