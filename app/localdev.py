"""本地外设：声音输出设备枚举与默认切换（comtypes 实现，无新依赖之外只装 comtypes）。

- 枚举：MMDeviceAPI IMMDeviceEnumerator（eRender / ACTIVE）
- 切默认：PolicyConfigClient::IPolicyConfig::SetDefaultEndpoint（三个角色都设）
"""
from __future__ import annotations

from ctypes import POINTER, Structure, Union, c_int, c_ulong, c_ushort, c_void_p, c_wchar_p

import comtypes
from comtypes import COMMETHOD, GUID, HRESULT, IUnknown
from comtypes.client import CreateObject

import ctypes
ole32 = ctypes.oledll.ole32  # PropVariantClear 用

CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
CLSID_PolicyConfigClient = GUID("{870af99c-171d-4f9e-af0d-e63df40c2bc9}")
IID_PolicyConfig = GUID("{F8679F50-850A-41CF-9C72-430F290290C8}")
IID_PolicyConfig_Win7 = GUID("{CA286FC3-91FD-42C3-8E9B-CAAFA66242E3}")


class PROPERTYKEY(Structure):
    _fields_ = [("fmtid", GUID), ("pid", c_ulong)]


PKEY_Device_FriendlyName = PROPERTYKEY(
    GUID("{a45c254e-df1c-4efd-8020-67d146a850e0}"), 14)
PKEY_Device_DeviceDesc = PROPERTYKEY(
    GUID("{a45c254e-df1c-4efd-8020-67d146a850e0}"), 2)


class PROPVARIANT(Structure):
    class _U(Union):
        _fields_ = [("lVal", c_ulong), ("pwszVal", c_wchar_p)]

    _fields_ = [("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort),
                ("r3", c_ushort), ("u", _U)]


def _clear_pv(pv: PROPVARIANT):
    try:
        ole32.PropVariantClear(pv)
    except Exception:
        pass


class IPropertyStore(IUnknown):
    _iid_ = GUID("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_ulong), "cProps")),
        COMMETHOD([], HRESULT, "GetAt", (["in"], c_ulong, "iProp"),
                  (["out"], POINTER(PROPERTYKEY), "pkey")),
        COMMETHOD([], HRESULT, "GetValue", (["in"], POINTER(PROPERTYKEY), "key"),
                  (["out"], POINTER(PROPVARIANT), "pv")),
        COMMETHOD([], HRESULT, "SetValue", (["in"], POINTER(PROPERTYKEY), "key"),
                  (["in"], POINTER(PROPVARIANT), "pv")),
        COMMETHOD([], HRESULT, "Commit"),
    ]


class IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Activate", (["in"], GUID, "iid"),
                  (["in"], c_ulong, "dwClsCtx"),
                  (["in"], POINTER(c_ulong), "pParams"),
                  (["out"], POINTER(POINTER(IUnknown)), "ppInterface")),
        COMMETHOD([], HRESULT, "OpenPropertyStore",
                  (["in"], c_ulong, "stgmAccess"),
                  (["out"], POINTER(POINTER(IPropertyStore)), "ppProperties")),
        COMMETHOD([], HRESULT, "GetId", (["out"], POINTER(c_wchar_p), "ppstrId")),
        COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(c_ulong), "pdwState")),
    ]


class IMMDeviceCollection(IUnknown):
    _iid_ = GUID("{0BD7A1BE-7A1A-44DB-8397-CC5392387B5E}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_ulong), "pcDevices")),
        COMMETHOD([], HRESULT, "Item", (["in"], c_ulong, "nDevice"),
                  (["out"], POINTER(POINTER(IMMDevice)), "ppDevice")),
    ]


class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        COMMETHOD([], HRESULT, "EnumAudioEndpoints",
                  (["in"], c_int, "dataFlow"), (["in"], c_ulong, "dwStateMask"),
                  (["out"], POINTER(POINTER(IMMDeviceCollection)), "ppDevices")),
        COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint",
                  (["in"], c_int, "dataFlow"), (["in"], c_int, "role"),
                  (["out"], POINTER(POINTER(IMMDevice)), "ppEndpoint")),
        COMMETHOD([], HRESULT, "GetDevice", (["in"], c_wchar_p, "pwstrId"),
                  (["out"], POINTER(POINTER(IMMDevice)), "ppDevice")),
        COMMETHOD([], HRESULT, "RegisterEndpointNotificationCallback",
                  (["in"], POINTER(IUnknown), "pClient")),
        COMMETHOD([], HRESULT, "UnregisterEndpointNotificationCallback",
                  (["in"], POINTER(IUnknown), "pClient")),
    ]


class IPolicyConfig(IUnknown):
    _iid_ = IID_PolicyConfig
    _methods_ = [
        COMMETHOD([], HRESULT, "GetMixFormat", (["in"], c_wchar_p, "pwstrDeviceId"),
                  (["out"], POINTER(POINTER(IUnknown)), "ppFormat")),
        COMMETHOD([], HRESULT, "GetDeviceFormat", (["in"], c_wchar_p, "pwstrDeviceId"),
                  (["in"], c_int, "bDefault"), (["out"], POINTER(POINTER(IUnknown)), "ppFormat")),
        COMMETHOD([], HRESULT, "ResetDeviceFormat", (["in"], c_wchar_p, "pwstrDeviceId")),
        COMMETHOD([], HRESULT, "SetDeviceFormat", (["in"], c_wchar_p, "pwstrDeviceId"),
                  (["in"], POINTER(IUnknown), "pEndpointFormat"),
                  (["in"], POINTER(IUnknown), "pMixFormat")),
        COMMETHOD([], HRESULT, "GetProcessingPeriod", (["in"], c_wchar_p, "pwstrDeviceId"),
                  (["in"], c_int, "bDefault"), (["out"], POINTER(c_void_p), "ppmftDefault")),
        COMMETHOD([], HRESULT, "SetProcessingPeriod", (["in"], c_wchar_p, "pwstrDeviceId"),
                  (["in"], c_void_p, "ppmftPeriod")),
        COMMETHOD([], HRESULT, "GetShareMode", (["in"], c_wchar_p, "pwstrDeviceId"),
                  (["out"], POINTER(c_ulong), "pMode")),
        COMMETHOD([], HRESULT, "SetShareMode", (["in"], c_wchar_p, "pwstrDeviceId"),
                  (["in"], c_ulong, "mode")),
        COMMETHOD([], HRESULT, "GetPropertyValue", (["in"], c_wchar_p, "pwstrDeviceId"),
                  (["in"], POINTER(PROPERTYKEY), "key"),
                  (["in"], c_int, "bFxStore"), (["out"], POINTER(PROPVARIANT), "pv")),
        COMMETHOD([], HRESULT, "SetPropertyValue", (["in"], c_wchar_p, "pwstrDeviceId"),
                  (["in"], c_int, "bFxStore"), (["in"], POINTER(PROPERTYKEY), "key"),
                  (["in"], POINTER(PROPVARIANT), "pv")),
        COMMETHOD([], HRESULT, "SetDefaultEndpoint", (["in"], c_wchar_p, "wszDeviceId"),
                  (["in"], c_int, "eRole")),
        COMMETHOD([], HRESULT, "SetEndpointVisibility", (["in"], c_wchar_p, "pwstrDeviceId"),
                  (["in"], c_int, "bVisible")),
    ]


# 数据流 / 状态 / 角色
E_RENDER = 0
DEVICE_STATE_ACTIVE = 1
_ROLES = (0, 1, 2)          # console / multimedia / communications


def _device_name(dev: IMMDevice) -> str:
    try:
        store = dev.OpenPropertyStore(0)   # STGM_READ
    except Exception:
        return "未命名音频设备"
    try:
        for key in (PKEY_Device_FriendlyName, PKEY_Device_DeviceDesc):
            try:
                pv = store.GetValue(key)
                if pv.vt == 31 and pv.u.pwszVal:
                    return str(pv.u.pwszVal)
            finally:
                _clear_pv(pv)
    finally:
        store.Release()
    return "未命名音频设备"


def list_audio_outputs() -> dict:
    """枚举所有可用的声音输出设备。返回 {"devices": [...], "error": ...}。"""
    try:
        comtypes.CoInitialize()
    except Exception:
        pass
    try:
        en = CreateObject(CLSID_MMDeviceEnumerator, interface=IMMDeviceEnumerator)
        coll = en.EnumAudioEndpoints(E_RENDER, DEVICE_STATE_ACTIVE)
        default_id = ""
        try:
            default_id = en.GetDefaultAudioEndpoint(E_RENDER, 0).GetId()
        except Exception:
            pass
        devices = []
        for i in range(coll.GetCount()):
            dev = coll.Item(i)
            try:
                did = dev.GetId()
                devices.append({"id": did, "name": _device_name(dev),
                                "default": did == default_id})
            finally:
                dev.Release()
        return {"devices": devices, "error": None}
    except Exception as e:  # noqa: BLE001
        return {"devices": [], "error": str(e)}


def set_default_output(dev_id: str) -> dict:
    """把指定输出设备设为系统默认（三个角色都设，等效系统「设为默认设备」）。"""
    if not dev_id:
        return {"ok": False, "error": "设备 id 为空"}
    try:
        comtypes.CoInitialize()
    except Exception:
        pass
    for iid in (IID_PolicyConfig, IID_PolicyConfig_Win7):
        try:
            IPolicyConfig._iid_ = iid
            pc = CreateObject(CLSID_PolicyConfigClient, interface=IPolicyConfig)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"无法创建 PolicyConfig：{e}"}
        try:
            for role in _ROLES:
                pc.SetDefaultEndpoint(dev_id, role)
            pc.Release()
            return {"ok": True, "error": None}
        except Exception as e:  # noqa: BLE001
            err = str(e)
        finally:
            try:
                pc.Release()
            except Exception:
                pass
    return {"ok": False, "error": err or "SetDefaultEndpoint 调用失败"}
