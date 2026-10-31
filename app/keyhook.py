"""全局低级键盘钩子（WH_KEYBOARD_LL）+ 按键注入。

代码模式需要两件事：
1. 在按键到达目标程序之前拦下它（自动补齐括号/引号时要把原键换成一对字符）；
2. 往目标程序里补发字符（用 KEYEVENTF_UNICODE，不受当前键盘布局影响）。

钩子必须跑在带消息循环的线程里，因此单独起一个线程。
注入的事件带 LLKHF_INJECTED 标记，回调里直接忽略，避免自己吃自己的按键。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
WM_QUIT = 0x0012
LLKHF_INJECTED = 0x10

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK_BACK, VK_RIGHT, VK_LEFT, VK_ESCAPE = 0x08, 0x27, 0x25, 0x1B

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 \
    else ctypes.c_ulong

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD),
                ("flags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD),
                ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


# 修饰键：按住这些键时不做自动补齐（Ctrl+C、Alt+Tab 之类不能被干扰）。
# 注意不含 Shift——( { " 等字符本身就要按 Shift 输入。
def modifier_down() -> bool:
    for vk in (0x11, 0x12, 0x5B, 0x5C):   # Ctrl Alt Win左 Win右
        if user32.GetAsyncKeyState(vk) & 0x8000:
            return True
    return False


def send_chars(text: str):
    """按 unicode 方式注入字符（与键盘布局无关）。"""
    if not text:
        return
    n = len(text) * 2
    arr = (INPUT * n)()
    for i, ch in enumerate(text):
        code = ord(ch)
        arr[i * 2].type = INPUT_KEYBOARD
        arr[i * 2].u.ki = KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, 0)
        arr[i * 2 + 1].type = INPUT_KEYBOARD
        arr[i * 2 + 1].u.ki = KEYBDINPUT(
            0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)
    user32.SendInput(n, ctypes.byref(arr), ctypes.sizeof(INPUT))


def send_vk(vk: int):
    """注入一个按键（按下 + 抬起），只支持无需字符的键（方向键等）。"""
    arr = (INPUT * 2)()
    arr[0].type = INPUT_KEYBOARD
    arr[0].u.ki = KEYBDINPUT(vk, 0, 0, 0, 0)
    arr[1].type = INPUT_KEYBOARD
    arr[1].u.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP, 0, 0)
    user32.SendInput(2, ctypes.byref(arr), ctypes.sizeof(INPUT))


class KeyboardHook:
    """全局键盘钩子。

    callback(vk, is_down) -> bool：返回 True 表示吞掉这次按键。
    只在“按下”且非注入事件时回调；按住不放的重复按下由调用方用
    self.repeated(vk) 判断。
    """

    def __init__(self, callback):
        self._cb = callback
        self._proc = HOOKPROC(self._proc_impl)
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._hook = None
        self._down: set[int] = set()

    # ------------------------------------------------------------------ #
    def _proc_impl(self, nCode, wParam, lParam):
        try:
            if nCode == 0:                      # HC_ACTION
                kb = ctypes.cast(lParam,
                                 ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if not (kb.flags & LLKHF_INJECTED):
                    down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                    up = wParam in (WM_KEYUP, WM_SYSKEYUP)
                    if down:
                        was_down = kb.vkCode in self._down
                        self._down.add(kb.vkCode)
                        if not was_down and self._cb(int(kb.vkCode), True):
                            return 1
                    elif up:
                        self._down.discard(kb.vkCode)
                        self._cb(int(kb.vkCode), False)
        except Exception:
            pass
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _run(self):
        self._tid = int(kernel32.GetCurrentThreadId())
        # WH_KEYBOARD_LL 且钩子过程在本进程内：hMod 必须为 NULL（传模块句柄会报 126）
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc,
                                              None, 0)
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    # ------------------------------------------------------------------ #
    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="bt-keyhook")
        self._thread.start()
        # 等钩子装好（最多 ~0.5s）
        for _ in range(50):
            if self._hook:
                return True
            threading.Event().wait(0.01)
        return bool(self._hook)

    def stop(self):
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        self._tid = 0
        self._thread = None
        self._hook = None
        self._down.clear()

    @property
    def running(self) -> bool:
        return bool(self._hook) and self._thread is not None \
            and self._thread.is_alive()
