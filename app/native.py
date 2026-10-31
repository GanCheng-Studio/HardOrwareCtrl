"""Win32 / Winsock 原生能力（纯 ctypes，无额外依赖）。

提供三组能力：
1. RFCOMM 串口连接（AF_BTH）——用于访问设备的厂商私有服务（如杰理 SPP）。
2. 蓝牙服务开关（bthprops.BluetoothSetServiceState）——重启 HFP 链路以迫使设备重报电量。
3. 进程枚举——判断厂商客户端是否正在运行。
"""
from __future__ import annotations

import ctypes
import socket
import struct
import uuid
from ctypes import wintypes

# --------------------------------------------------------------------------- #
# Winsock AF_BTH（蓝牙 RFCOMM）
# --------------------------------------------------------------------------- #
AF_BTH = 32
BTHPROTO_RFCOMM = 3

_ws2 = ctypes.WinDLL("ws2_32", use_last_error=True)
_ws2.socket.restype = ctypes.c_void_p
_ws2.socket.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
_ws2.connect.restype = ctypes.c_int
_ws2.send.restype = ctypes.c_int
_ws2.recv.restype = ctypes.c_int
_ws2.closesocket.restype = ctypes.c_int
_ws2.closesocket.argtypes = [ctypes.c_void_p]


class _SOCKADDR_BTH(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("addressFamily", ctypes.c_ushort),
                ("btAddr", ctypes.c_ulonglong),
                ("serviceClassId", ctypes.c_ubyte * 16),
                ("port", ctypes.c_ulong)]


class _WSADATA(ctypes.Structure):
    _fields_ = [("wVersion", ctypes.c_ushort), ("wHighVersion", ctypes.c_ushort),
                ("szDescription", ctypes.c_char * 257),
                ("szSystemStatus", ctypes.c_char * 129),
                ("iMaxSockets", ctypes.c_ushort), ("iMaxUdpDg", ctypes.c_ushort),
                ("lpVendorInfo", ctypes.c_char_p)]


_wsadata = _WSADATA()
_ws_started = _ws2.WSAStartup(0x0202, ctypes.byref(_wsadata)) == 0


def rfcomm_connect(mac: int, service_uuid: str, timeout: int = 12) -> int:
    """按 service UUID 连接蓝牙 RFCOMM（让系统自己做 SDP 查通道号）。失败抛 OSError。"""
    s = _ws2.socket(AF_BTH, socket.SOCK_STREAM, BTHPROTO_RFCOMM)
    if not s or s == -1:
        raise OSError("socket() 失败")
    tv = struct.pack("<ii", timeout, 0)
    _ws2.setsockopt(ctypes.c_void_p(s), socket.SOL_SOCKET, socket.SO_RCVTIMEO, tv, 8)
    _ws2.setsockopt(ctypes.c_void_p(s), socket.SOL_SOCKET, socket.SO_SNDTIMEO, tv, 8)
    addr = _SOCKADDR_BTH()
    addr.addressFamily = AF_BTH
    addr.btAddr = int(mac)
    addr.serviceClassId = (ctypes.c_ubyte * 16)(*uuid.UUID(service_uuid).bytes_le)
    addr.port = 0
    r = _ws2.connect(ctypes.c_void_p(s), ctypes.byref(addr), ctypes.sizeof(addr))
    if r != 0:
        err = _ws2.WSAGetLastError()
        _ws2.closesocket(ctypes.c_void_p(s))
        raise OSError(f"RFCOMM 连接失败（WSA 错误 {err}）")
    return s


def rfcomm_send(s, data: bytes) -> int:
    return _ws2.send(ctypes.c_void_p(s), data, len(data), 0)


def rfcomm_recv(s, n: int = 512) -> bytes:
    buf = ctypes.create_string_buffer(n)
    r = _ws2.recv(ctypes.c_void_p(s), buf, n, 0)
    return b"" if r <= 0 else buf.raw[:r]


def rfcomm_close(s):
    try:
        _ws2.closesocket(ctypes.c_void_p(s))
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 蓝牙设备 / 服务状态（bthprops.cpl）
# --------------------------------------------------------------------------- #
_bth = ctypes.WinDLL("bthprops.cpl", use_last_error=True)


class _SYSTEMTIME(ctypes.Structure):
    _fields_ = [("wYear", wintypes.WORD), ("wMonth", wintypes.WORD),
                ("wDayOfWeek", wintypes.WORD), ("wDay", wintypes.WORD),
                ("wHour", wintypes.WORD), ("wMinute", wintypes.WORD),
                ("wSecond", wintypes.WORD), ("wMilliseconds", wintypes.WORD)]


class _BLUETOOTH_ADDRESS(ctypes.Structure):
    _fields_ = [("ullLong", ctypes.c_ulonglong)]


class _BLUETOOTH_DEVICE_INFO(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD),
                ("Address", _BLUETOOTH_ADDRESS),
                ("ulClassofDevice", ctypes.c_ulong),
                ("fConnected", wintypes.BOOL),
                ("fRemembered", wintypes.BOOL),
                ("fAuthenticated", wintypes.BOOL),
                ("stLastSeen", _SYSTEMTIME),
                ("stLastUsed", _SYSTEMTIME),
                ("szName", ctypes.c_wchar * 248)]


class _FIND_RADIO_PARAMS(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD)]


class _DEVICE_SEARCH_PARAMS(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD),
                ("fReturnAuthenticated", wintypes.BOOL),
                ("fReturnRemembered", wintypes.BOOL),
                ("fReturnUnknown", wintypes.BOOL),
                ("fReturnConnected", wintypes.BOOL),
                ("fIssueInquiry", wintypes.BOOL),
                ("cTimeoutMultiplier", ctypes.c_ubyte),
                ("hRadio", wintypes.HANDLE)]


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, s: str):
        g = uuid.UUID(s)
        return cls(g.time_low, g.time_mid, g.time_hi_version,
                   (ctypes.c_ubyte * 8)(*g.bytes[8:]))


_bth.BluetoothFindFirstRadio.restype = ctypes.c_void_p
_bth.BluetoothFindFirstRadio.argtypes = [ctypes.POINTER(_FIND_RADIO_PARAMS),
                                         ctypes.POINTER(wintypes.HANDLE)]
_bth.BluetoothFindFirstDevice.restype = ctypes.c_void_p
_bth.BluetoothFindFirstDevice.argtypes = [ctypes.POINTER(_DEVICE_SEARCH_PARAMS),
                                          ctypes.POINTER(_BLUETOOTH_DEVICE_INFO)]
_bth.BluetoothFindNextDevice.restype = wintypes.BOOL
_bth.BluetoothFindNextDevice.argtypes = [ctypes.c_void_p,
                                         ctypes.POINTER(_BLUETOOTH_DEVICE_INFO)]
_bth.BluetoothFindDeviceClose.restype = wintypes.BOOL
_bth.BluetoothFindDeviceClose.argtypes = [ctypes.c_void_p]
_bth.BluetoothSetServiceState.restype = ctypes.c_ulong
_bth.BluetoothSetServiceState.argtypes = [wintypes.HANDLE,
                                          ctypes.POINTER(_BLUETOOTH_DEVICE_INFO),
                                          ctypes.POINTER(_GUID), wintypes.DWORD]

SERVICE_DISABLE = 0
SERVICE_ENABLE = 1
GUID_HFP = "0000111e-0000-1000-8000-00805f9b34fb"   # Handsfree
GUID_A2DP = "0000110b-0000-1000-8000-00805f9b34fb"  # AudioSink（音乐播放）


def find_radio():
    p = _FIND_RADIO_PARAMS(dwSize=ctypes.sizeof(_FIND_RADIO_PARAMS))
    h = wintypes.HANDLE()
    fh = _bth.BluetoothFindFirstRadio(ctypes.byref(p), ctypes.byref(h))
    return (fh, h.value) if fh else (None, None)


def iter_devices(hradio) -> list[tuple[int, str, bool]]:
    sp = _DEVICE_SEARCH_PARAMS()
    sp.dwSize = ctypes.sizeof(_DEVICE_SEARCH_PARAMS)
    sp.fReturnAuthenticated = 1
    sp.fReturnRemembered = 1
    sp.fReturnUnknown = 0
    sp.fReturnConnected = 1
    sp.fIssueInquiry = 0
    sp.cTimeoutMultiplier = 1
    sp.hRadio = wintypes.HANDLE(int(hradio))
    info = _BLUETOOTH_DEVICE_INFO()
    info.dwSize = ctypes.sizeof(_BLUETOOTH_DEVICE_INFO)
    fh = _bth.BluetoothFindFirstDevice(ctypes.byref(sp), ctypes.byref(info))
    out = []
    if not fh:
        return out
    try:
        while True:
            out.append((info.Address.ullLong, info.szName, bool(info.fConnected)))
            nxt = _BLUETOOTH_DEVICE_INFO()
            nxt.dwSize = ctypes.sizeof(_BLUETOOTH_DEVICE_INFO)
            if not _bth.BluetoothFindNextDevice(fh, ctypes.byref(nxt)):
                break
            info = nxt
    finally:
        _bth.BluetoothFindDeviceClose(fh)
    return out


def set_service_state(hradio, mac: int, guid_str: str, enable: bool) -> tuple[int, int]:
    info = _BLUETOOTH_DEVICE_INFO()
    info.dwSize = ctypes.sizeof(_BLUETOOTH_DEVICE_INFO)
    info.Address.ullLong = int(mac)
    guid = _GUID.parse(guid_str)
    ret = _bth.BluetoothSetServiceState(
        wintypes.HANDLE(int(hradio)), ctypes.byref(info), ctypes.byref(guid),
        SERVICE_ENABLE if enable else SERVICE_DISABLE)
    return ret, ctypes.get_last_error()


def hfp_cycle(mac: int) -> tuple[bool, str]:
    """断开并重连 HFP 服务，迫使设备重新上报电量。返回 (成功, 说明)。"""
    fh, h = find_radio()
    if not fh:
        return False, "未找到蓝牙适配器"
    try:
        r1, e1 = set_service_state(h, mac, GUID_HFP, False)
        if r1 != 0:
            return False, f"禁用 HFP 失败（错误 {e1}）"
        import time
        time.sleep(2.5)
        r2, e2 = set_service_state(h, mac, GUID_HFP, True)
        if r2 != 0:
            return False, f"启用 HFP 失败（错误 {e2}）"
        return True, "HFP 链路已重连，设备会重新上报电量"
    finally:
        try:
            _bth.BluetoothFindRadioClose(fh)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 进程枚举（客户端是否在运行）
# --------------------------------------------------------------------------- #
TH32CS_SNAPPROCESS = 0x00000002
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)


class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_char * 260)]


def running_processes() -> set[str]:
    """返回正在运行的进程名（小写）。"""
    out: set[str] = set()
    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        return out
    try:
        entry = _PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)
        if _k32.Process32First(snap, ctypes.byref(entry)):
            while True:
                try:
                    out.add(entry.szExeFile.decode("mbcs", "ignore").lower())
                except Exception:
                    pass
                if not _k32.Process32Next(snap, ctypes.byref(entry)):
                    break
    finally:
        _k32.CloseHandle(snap)
    return out
