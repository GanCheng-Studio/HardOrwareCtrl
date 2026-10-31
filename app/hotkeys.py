"""全局热键：键盘设备一键切换中/英输入法。

- 快捷键由用户在设备详情页「按下实际的组合键」录制，不受预设限制
- 独立线程 + RegisterHotKey(NULL, ...)（热键消息投递到线程队列，无需窗口）
- 设备快捷键变更时通过 PostThreadMessage 通知线程重新注册
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_HOTKEY = 0x0312
WM_APP_RELOAD = 0x8000          # WM_APP：让热键线程重读配置
MOD_ALT, MOD_CONTROL, MOD_SHIFT = 0x1, 0x2, 0x4
MOD_NOREPEAT = 0x4000

# 兼容旧配置里存的预设名称（新版改为录制，存 {name, mods, vk}）
LEGACY: dict[str, tuple[int, int]] = {
    "F7": (0, 0x70), "F8": (0, 0x71), "F9": (0, 0x72),
    "Ctrl+F9": (MOD_CONTROL, 0x72), "Ctrl+F10": (MOD_CONTROL, 0x73),
    "Alt+F1": (MOD_ALT, 0x70), "Ctrl+Alt+E": (MOD_CONTROL | MOD_ALT, 0x45),
    "Ctrl+Alt+空格": (MOD_CONTROL | MOD_ALT, 0x20),
}

# 键名（用于显示）
_VK_NAMES = {0x20: "Space", 0x0D: "Enter", 0x09: "Tab", 0x1B: "Esc",
             0x08: "Backspace", 0xC0: "`", 0xBD: "-", 0xBB: "=",
             0xDB: "[", 0xDD: "]", 0xDC: "\\", 0xBA: ";", 0xDE: "'",
             0xBC: ",", 0xBE: ".", 0xBF: "/"}
for _i in range(0x70, 0x88):          # F1..F24
    _VK_NAMES[_i] = f"F{_i - 0x6F}"

# 纯修饰键（不能单独作为热键）
_MODIFIER_VKS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0x14, 0x90, 0x91}


def key_name(vk: int) -> str:
    if 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x60 <= vk <= 0x69:            # 小键盘 0-9
        return f"Num{vk - 0x60}"
    return _VK_NAMES.get(vk, f"VK{vk:X}")


def display_name(mods: int, vk: int) -> str:
    parts = []
    if mods & MOD_CONTROL:
        parts.append("Ctrl")
    if mods & MOD_ALT:
        parts.append("Alt")
    if mods & MOD_SHIFT:
        parts.append("Shift")
    parts.append(key_name(vk))
    return "+".join(parts)


def normalize(spec) -> dict | None:
    """配置里的 hotkey → {name, mods, vk}；无效返回 None。"""
    if not spec:
        return None
    if isinstance(spec, dict):
        try:
            vk = int(spec.get("vk") or 0)
            mods = int(spec.get("mods") or 0) & (MOD_ALT | MOD_CONTROL | MOD_SHIFT)
        except (TypeError, ValueError):
            return None
        if not vk:
            return None
        return {"name": spec.get("name") or display_name(mods, vk),
                "mods": mods, "vk": vk}
    if isinstance(spec, str) and spec in LEGACY:
        mods, vk = LEGACY[spec]
        return {"name": spec, "mods": mods, "vk": vk}
    return None


def is_modifier_key(vk: int) -> bool:
    return vk in _MODIFIER_VKS


def current_mods() -> int:
    """实时读取当前按住的修饰键（Ctrl/Alt/Shift）。"""
    m = 0
    if user32.GetAsyncKeyState(0x11) & 0x8000:   # VK_CONTROL
        m |= MOD_CONTROL
    if user32.GetAsyncKeyState(0x12) & 0x8000:   # VK_MENU
        m |= MOD_ALT
    if user32.GetAsyncKeyState(0x10) & 0x8000:   # VK_SHIFT
        m |= MOD_SHIFT
    return m


class HotkeyManager:
    """按设备注册全局热键；触发时回调 on_trigger(dev_id)（在热键线程中）。"""

    def __init__(self, on_trigger):
        self._on_trigger = on_trigger
        self._desired: dict[tuple[int, int], str] = {}   # (mods, vk) -> dev_id
        self._registered: dict[int, tuple[tuple[int, int], str]] = {}
        self._failed: list[str] = []
        self._tid = 0
        self._started = False

    def start(self):
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._run, daemon=True,
                         name="hotkey-worker").start()

    def update(self, devices: list[dict]):
        """重新收集键盘设备上配置的快捷键（可从任意线程调用）。"""
        desired: dict[tuple[int, int], str] = {}
        for d in devices:
            if d.get("dev_type") != "keyboard":
                continue
            spec = normalize(d.get("hotkey"))
            if not spec:
                continue
            key = (spec["mods"], spec["vk"])
            desired.setdefault(key, d["id"])
        self._desired = desired
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_APP_RELOAD, 0, 0)

    @property
    def failed(self) -> list[str]:
        """注册失败（被其他程序占用）的快捷键显示名。"""
        return list(self._failed)

    # ------------------------------------------------------------------ #
    def _run(self):
        self._tid = kernel32.GetCurrentThreadId()
        self._reload()
        msg = wt.MSG()
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r <= 0:      # WM_QUIT 或错误
                self._unregister_all()
                break
            if msg.message == WM_HOTKEY:
                info = self._registered.get(msg.wParam)
                if info:
                    try:
                        self._on_trigger(info[1])
                    except Exception:
                        pass
            elif msg.message == WM_APP_RELOAD:
                self._reload()

    def _reload(self):
        self._unregister_all()
        nid = 1
        self._failed.clear()
        for (mods, vk), dev_id in self._desired.items():
            if user32.RegisterHotKey(None, nid, mods | MOD_NOREPEAT, vk):
                self._registered[nid] = ((mods, vk), dev_id)
            else:
                self._failed.append(display_name(mods, vk))   # 被占用
            nid += 1

    def _unregister_all(self):
        for hid in self._registered:
            user32.UnregisterHotKey(None, hid)
        self._registered.clear()
