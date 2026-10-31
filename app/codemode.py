"""代码模式：在配置好的 IDE 里自动补齐括号/引号，并按上下文切换中英文输入。

工作机制：
1. 全局低级键盘钩子（keyhook）先看到每一次按键；
2. 用一个小状态机按“当前语言”的语法规则跟踪光标是否在字符串里
   （字符串定界符 / 转义 / 行注释 / 块注释）；
3. 在代码上下文（不在字符串里）自动补一对括号或引号，并把光标放到中间；
   再按同一个闭合符时直接跳过（不会出现 `""`）；
4. 进出字符串、或切到终端时，切换前台窗口的键盘布局（英文 ↔ 中文），
   只在这种“状态跳变”时切换，不做常驻锁定，用户随时可以手动 Win+空格。

注意：中文“标点”本身由输入法决定，这里只能对支持 IMM 的输入法做尽力而为的
IME_CMODE_SYMBOL 设置；微软拼音等 TSF 输入法可能忽略该请求（不影响其它功能）。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import threading
import time

from .keyhook import (KeyboardHook, modifier_down, send_chars, send_vk,
                      VK_LEFT, VK_RIGHT)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

HKL_EN = 0x04090409          # 英语（美国）· 美式键盘
HKL_ZH = 0x08040804          # 中文（简体）· 微软拼音
WM_INPUTLANGCHANGEREQUEST = 0x0050
IME_CMODE_SYMBOL = 0x0400    # 中文标点（IMM 输入法）

# --------------------------------------------------------------------------- #
# 语言规则
# --------------------------------------------------------------------------- #
def _lang(name, quotes, line_comments=(), block_comments=(), brackets=True,
          never_english=False):
    pairs = {}
    if brackets:
        pairs.update({"{": "}", "[": "]", "(": ")"})
    for q in quotes:
        if len(q) == 1:
            pairs[q] = q
    return {
        "name": name,
        "quotes": sorted(quotes, key=len, reverse=True),
        "line_comments": list(line_comments),
        "block_comments": list(block_comments),
        "pairs": pairs,
        "never_english": never_english,
    }


LANGUAGES = {
    "python": _lang("Python", ['"""', "'''", '"', "'"], ["#"],
                    [('"""', '"""'), ("'''", "'''")]),
    "cpp": _lang("C++", ['"', "'"], ["//"], [("/*", "*/")]),
    "c": _lang("C", ['"', "'"], ["//"], [("/*", "*/")]),
    "csharp": _lang("C#", ['"', "'"], ["//"], [("/*", "*/")]),
    "javascript": _lang("JavaScript", ['"', "'", "`"], ["//"],
                        [("/*", "*/")]),
    "java": _lang("Java", ['"', "'"], ["//"], [("/*", "*/")]),
    "html": _lang("HTML", ['"', "'"], [], [("<!--", "-->")]),
    "php": _lang("PHP", ['"', "'"], ["//", "#"], [("/*", "*/")]),
    "epl": _lang("易语言", ['"'], ["'"], [("/*", "*/")], never_english=True),
    "generic": _lang("通用（C 风格）", ['"', "'"], ["//"], [("/*", "*/")]),
}

LANG_ORDER = ["python", "cpp", "c", "csharp", "javascript", "java", "html",
              "php", "epl"]

# 终端 / Shell：进入后按用户设置默认切中文（写中文注释、提交信息）
TERMINAL_EXES = {
    "windowsterminal.exe", "openconsole.exe", "conhost.exe", "wt.exe",
    "cmd.exe", "powershell.exe", "pwsh.exe", "mintty.exe", "bash.exe",
    "wsl.exe", "wslhost.exe", "alacritty.exe", "wezterm-gui.exe",
    "conemu64.exe", "conemu.exe", "tabby.exe", "fluentterminal.exe",
}

# 预设 IDE（用于“添加已安装的预设”）
PRESETS = [
    {"name": "Visual Studio", "lang": "csharp", "paths": [
        r"%ProgramFiles(x86)%\Microsoft Visual Studio\*\*\Common7\IDE\devenv.exe",
        r"%ProgramFiles%\Microsoft Visual Studio\*\*\Common7\IDE\devenv.exe",
    ]},
    {"name": "VS Code", "lang": "python", "paths": [
        r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
        r"%ProgramFiles%\Microsoft VS Code\Code.exe",
        r"%ProgramFiles(x86)%\Microsoft VS Code\Code.exe",
    ]},
    {"name": "JetBrains IDE", "lang": "java", "exes": [
        "pycharm64.exe", "idea64.exe", "clion64.exe", "webstorm64.exe",
        "goland64.exe", "rider64.exe", "phpstorm64.exe", "datagrip64.exe",
        "rubymine64.exe", "rustrover64.exe", "studio64.exe",
    ], "paths": [
        r"%LOCALAPPDATA%\Programs\*\bin\*.exe",
        r"%ProgramFiles%\JetBrains\*\bin\*.exe",
    ]},
    {"name": "Dev-C++", "lang": "cpp", "paths": [
        r"%ProgramFiles(x86)%\Embarcadero\Dev-Cpp\devcpp.exe",
        r"%ProgramFiles%\Embarcadero\Dev-Cpp\devcpp.exe",
        r"%ProgramFiles(x86)%\Dev-Cpp\devcpp.exe",
        r"%ProgramFiles%\Dev-Cpp\devcpp.exe",
    ]},
]


def detect_presets() -> list[dict]:
    """扫描常见安装路径，返回本机实际存在的预设应用。"""
    import glob
    jb_names = {
        "pycharm64.exe": "PyCharm", "idea64.exe": "IntelliJ IDEA",
        "clion64.exe": "CLion", "webstorm64.exe": "WebStorm",
        "goland64.exe": "GoLand", "rider64.exe": "Rider",
        "phpstorm64.exe": "PhpStorm", "datagrip64.exe": "DataGrip",
        "rubymine64.exe": "RubyMine", "rustrover64.exe": "RustRover",
        "studio64.exe": "Android Studio",
    }
    found, seen = [], set()
    for p in PRESETS:
        for pat in p.get("paths", []):
            full = os.path.expandvars(pat)
            for path in glob.glob(full):
                if not os.path.isfile(path):
                    continue
                base = os.path.basename(path).lower()
                if p.get("exes") and base not in p["exes"]:
                    continue
                key = path.lower()
                if key in seen:
                    continue
                seen.add(key)
                name = jb_names.get(base) or p["name"]
                found.append({"name": name, "exe": path, "lang": p["lang"]})
    return found


# --------------------------------------------------------------------------- #
# 前台窗口 / 输入法
# --------------------------------------------------------------------------- #
def foreground() -> tuple[int, str]:
    """返回 (前台窗口句柄, 进程 exe 名小写)。"""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0, ""
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)   # QUERY_LIMITED
    if not h:
        return hwnd, ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return hwnd, os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(h)
    return hwnd, ""


def current_hkl(hwnd: int) -> int:
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    return int(user32.GetKeyboardLayout(tid)) & 0xFFFFFFFF


def _set_punctuation(hwnd: int, chinese: bool) -> bool:
    """尽力把输入法切到中文/英文标点（只对 IMM 输入法有效）。"""
    try:
        imm32 = ctypes.WinDLL("imm32")
        himc = imm32.ImmGetContext(hwnd)
        if not himc:
            return False
        conv, sent = ctypes.c_ulong(), ctypes.c_ulong()
        ok = imm32.ImmGetConversionStatus(himc, ctypes.byref(conv),
                                          ctypes.byref(sent))
        if ok:
            val = conv.value | IME_CMODE_SYMBOL if chinese \
                else conv.value & ~IME_CMODE_SYMBOL
            imm32.ImmSetConversionStatus(himc, val, sent.value)
        imm32.ImmReleaseContext(hwnd, himc)
        return bool(ok)
    except Exception:
        return False


def switch_layout(hwnd: int, chinese: bool, punctuation: bool = True) -> bool:
    """把前台窗口切到 中文 / 英文 键盘布局。返回是否真的发生了切换。"""
    if not hwnd:
        return False
    want = HKL_ZH if chinese else HKL_EN
    changed = False
    try:
        if current_hkl(hwnd) != want:
            user32.PostMessageW(hwnd, WM_INPUTLANGCHANGEREQUEST, 0, want)
            changed = True
    except Exception:
        pass
    if punctuation and chinese:
        _set_punctuation(hwnd, True)
    elif punctuation:
        _set_punctuation(hwnd, False)
    return changed


# --------------------------------------------------------------------------- #
# 字符映射（美式布局）
# --------------------------------------------------------------------------- #
_BASE = {0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
         0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'", 0x20: " "}
_SHIFT = {0xBA: ":", 0xBB: "+", 0xBC: "<", 0xBD: "_", 0xBE: ">", 0xBF: "?",
          0xC0: "~", 0xDB: "{", 0xDC: "|", 0xDD: "}", 0xDE: '"'}
_SHIFT_DIGIT = {0x31: "!", 0x32: "@", 0x33: "#", 0x34: "$", 0x35: "%",
                0x36: "^", 0x37: "&", 0x38: "*", 0x39: "(", 0x30: ")"}
_ENTER_VKS = {0x0D}
_BACK_VKS = {0x08}


def vk_to_char(vk: int) -> str | None:
    shift = bool(user32.GetAsyncKeyState(0x10) & 0x8000)
    if 0x41 <= vk <= 0x5A:
        ch = chr(vk)
        return ch if shift else ch.lower()
    if 0x30 <= vk <= 0x39:
        return _SHIFT_DIGIT[vk] if shift else chr(vk)
    if vk in _BASE:
        return _SHIFT[vk] if shift else _BASE[vk]
    return None


# --------------------------------------------------------------------------- #
# 语法状态机
# --------------------------------------------------------------------------- #
class Session:
    """跟踪某个窗口里“光标是否在字符串内”。"""

    def __init__(self, lang_key: str, rules: dict | None = None):
        self.lang_key = lang_key
        self.rules = rules or LANGUAGES.get(lang_key, LANGUAGES["generic"])
        self.str_delim: str | None = None
        self.escaped = False
        self.block_end: str | None = None
        self.in_line = False
        self.pending: list[str] = []   # 自动补进去、光标停在前面的闭合符
        self.tail = ""                 # 最近的字符（识别多字符定界符）
        self.last_ts = 0.0

    # -- 状态 ---------------------------------------------------------- #
    @property
    def in_string(self) -> bool:
        return self.str_delim is not None

    @property
    def in_comment(self) -> bool:
        return self.in_line or self.block_end is not None

    @property
    def escape(self) -> str:
        return "\\"

    def reset(self):
        self.str_delim = None
        self.escaped = False
        self.block_end = None
        self.in_line = False
        self.pending.clear()
        self.tail = ""

    # -- 输入喂给状态机 ------------------------------------------------- #
    def feed(self, ch: str):
        self.last_ts = time.monotonic()
        if ch in ("\n", "\r"):
            self.in_line = False
            self.tail = ""
            return
        if self.in_line:
            return
        if self.block_end:
            self.tail = (self.tail + ch)[-len(self.block_end):]
            if self.tail == self.block_end:
                self.block_end = None
                self.tail = ""
            return
        if self.str_delim:
            d = self.str_delim
            if self.escaped:
                self.escaped = False
                self.tail = ""
                return
            if ch == self.escape:
                self.escaped = True
                return
            if len(d) == 1:
                if ch == d:
                    self.str_delim = None
                self.tail = ""
                return
            self.tail = (self.tail + ch)[-len(d):]
            if self.tail == d:
                self.str_delim = None
                self.tail = ""
            return
        # 普通代码区
        probe = self.tail + ch
        for start, end in self.rules["block_comments"]:
            if probe.endswith(start):
                self.block_end = end
                self.tail = ""
                return
        for pre in self.rules["line_comments"]:
            if probe.endswith(pre):
                self.in_line = True
                self.tail = ""
                return
        for q in self.rules["quotes"]:
            if probe.endswith(q):
                self.str_delim = q
                self.tail = ""
                return
        self.tail = probe[-3:]

    def open_string(self, delim: str):
        """自动补引号后，光标已在这个字符串里。"""
        self.str_delim = delim
        self.escaped = False
        self.tail = ""


# --------------------------------------------------------------------------- #
# 引擎
# --------------------------------------------------------------------------- #
DEFAULT_LANG_SETTINGS = {"autopair": True, "auto_switch": True}


def lang_settings(cfg: dict, key: str) -> dict:
    out = dict(DEFAULT_LANG_SETTINGS)
    rules = LANGUAGES.get(key, LANGUAGES["generic"])
    out["never_english"] = bool(rules.get("never_english", False))
    out.update((cfg.get("langs") or {}).get(key) or {})
    return out


def resolve_rules(cfg: dict, key: str) -> dict:
    if key in LANGUAGES:
        return LANGUAGES[key]
    for c in cfg.get("custom_langs") or []:
        if c.get("key") == key:
            base = LANGUAGES.get(c.get("base") or "generic",
                                 LANGUAGES["generic"])
            rules = dict(base)
            rules["name"] = c.get("name") or key
            return rules
    return LANGUAGES["generic"]


class CodeModeEngine:
    """把钩子、状态机、输入法切换串起来。"""

    def __init__(self, app):
        self.app = app
        self.hook = KeyboardHook(self._on_key)
        self._session: Session | None = None
        self._profile: dict | None = None
        self._exe = ""
        self._hwnd = 0
        self._applied: str | None = None
        self._stop = threading.Event()
        self._watch: threading.Thread | None = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    @property
    def cfg(self) -> dict:
        cm = self.app.config.settings.setdefault("code_mode", {})
        return cm

    def running(self) -> bool:
        return self.hook.running

    def apply_settings(self):
        """配置变化后调用：按需启停钩子与前台监视线程。"""
        cfg = self.cfg
        want = bool(cfg.get("enabled")) and bool(cfg.get("apps"))
        with self._lock:
            if want:
                self.hook.start()
                self._start_watch()
            else:
                self._stop_watch()
                self.hook.stop()
                self._session = None
                self._profile = None
                self._exe = ""
                self._applied = None

    # ------------------------------------------------------------------ #
    def _start_watch(self):
        if self._watch and self._watch.is_alive():
            return
        self._stop.clear()
        self._watch = threading.Thread(target=self._watch_loop, daemon=True,
                                       name="bt-codemode-watch")
        self._watch.start()

    def _stop_watch(self):
        self._stop.set()
        self._watch = None

    def _watch_loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                pass
            self._stop.wait(0.35)

    def _profile_for(self, exe: str) -> dict | None:
        if not exe:
            return None
        for a in (self.cfg.get("apps") or []):
            base = os.path.basename((a.get("exe") or "").replace("/", "\\"))
            if base.lower() == exe:
                return a
        return None

    def _tick(self, force: bool = False):
        hwnd, exe = foreground()
        with self._lock:
            self._hwnd = hwnd
            if exe != self._exe or force:
                self._exe = exe
                prof = self._profile_for(exe) if exe else None
                self._profile = prof
                if prof:
                    rules = resolve_rules(self.cfg, prof.get("lang", "generic"))
                    self._session = Session(prof.get("lang", "generic"), rules)
                    self._applied = None
                    self._apply_mode(force=True)
                else:
                    self._session = None
                    self._applied = None

    # ------------------------------------------------------------------ #
    def _mode_of(self, sess: Session, prof: dict) -> str:
        cfg = self.cfg
        ls = lang_settings(cfg, prof.get("lang", "generic"))
        # 易语言这类“永不切英文”（用户可关掉该开关）
        if ls.get("never_english"):
            return "zh"
        if self._is_terminal(prof):
            return "zh" if cfg.get("terminal_zh", True) else "en"
        return "zh" if sess.in_string else "en"

    @staticmethod
    def _is_terminal(prof: dict) -> bool:
        base = os.path.basename((prof.get("exe") or "").replace("/", "\\"))
        return base.lower() in TERMINAL_EXES

    def _auto_switch_on(self, prof: dict) -> bool:
        cfg = self.cfg
        if not cfg.get("auto_switch", True):
            return False
        ls = lang_settings(cfg, prof.get("lang", "generic"))
        return bool(prof.get("auto_switch", True)) and bool(ls.get("auto_switch",
                                                                  True))

    def _apply_mode(self, force: bool = False):
        sess, prof = self._session, self._profile
        if not sess or not prof:
            return
        if not self._auto_switch_on(prof):
            return
        want = self._mode_of(sess, prof)
        if want == self._applied and not force:
            return
        self._applied = want
        hwnd = self._hwnd or user32.GetForegroundWindow()
        switch_layout(hwnd, chinese=(want == "zh"),
                      punctuation=bool(self.cfg.get("chinese_punct", True)))

    # ------------------------------------------------------------------ #
    def _autopair_on(self, prof: dict) -> bool:
        cfg = self.cfg
        if not cfg.get("autopair", True):
            return False
        ls = lang_settings(cfg, prof.get("lang", "generic"))
        return bool(prof.get("autopair", True)) and bool(ls.get("autopair", True))

    def _on_key(self, vk: int, down: bool) -> bool:
        """钩子回调：返回 True 表示吞掉按键。"""
        if not down:
            return False
        with self._lock:
            sess, prof = self._session, self._profile
            if not sess or not prof:
                return False
            if modifier_down():
                return False
            ch = vk_to_char(vk)
            if vk in _ENTER_VKS:
                sess.feed("\n")
                return False
            if ch is None:
                return False
            try:
                # 1) 跳过自动补好的闭合符
                if sess.pending and ch == sess.pending[-1]:
                    sess.pending.pop()
                    send_vk(VK_RIGHT)
                    sess.feed(ch)
                    self._apply_mode()
                    return True
                # 2) 自动补齐（只在代码上下文，字符串里交给输入法）
                if (self._autopair_on(prof) and not sess.in_string
                        and not sess.in_comment and ch in sess.rules["pairs"]):
                    closer = sess.rules["pairs"][ch]
                    send_chars(ch + closer)
                    send_vk(VK_LEFT)         # 光标回到一对字符中间
                    sess.pending.append(closer)
                    if ch in sess.rules["quotes"]:
                        sess.open_string(ch)
                    else:
                        sess.tail = ch
                    self._apply_mode()
                    return True
                # 3) 普通按键：只更新状态
                sess.feed(ch)
                self._apply_mode()
            except Exception:
                pass
            return False
