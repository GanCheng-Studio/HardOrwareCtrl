"""tkinter 主面板：磁贴设备列表、扫描添加、链接添加、详情页、设置。

视觉规范：VS Code Dark+ 主题，圆角按钮 / 圆角卡片，ClearType 平滑字体。
"""
from __future__ import annotations

import math
import threading
import tkinter as tk
from tkinter import ttk, font as tkfont, messagebox

from .common import (battery_hex, make_type_icon, make_app_icon,
                     device_type_name, guess_device_type, DEVICE_TYPES,
                     SYS_SETTINGS_LABEL)
from . import protocols
from . import hotkeys
from . import autostart
from . import localdev
from .version import (APP_NAME, VERSION, RELEASE_NOTE, AUTHOR, GITHUB_URL,
                      GITHUB_LABEL)
from .hid2g import device_key_of

# --------------------------------------------------------------------------- #
# VS Code Dark+ 配色
# --------------------------------------------------------------------------- #
BG = "#1e1e1e"           # 编辑器背景
PANEL = "#252526"        # 侧栏 / 卡片
BAR = "#323233"          # 标题栏
HOVER = "#2a2d2e"        # 列表悬停
INPUT = "#3a3d41"        # 输入框 / 次级按钮
INPUT_HOVER = "#45494e"
BORDER = "#3c3c3c"
FG = "#d4d4d4"
FG_DIM = "#858585"
ACCENT = "#0e639c"       # VS Code 主按钮蓝
ACCENT_HOVER = "#1177bb"
STATUS = "#007acc"       # 状态栏蓝
SEL = "#094771"          # 列表选中
DANGER = "#a1260d"
DANGER_HOVER = "#b9412c"
OK = "#3fb950"

FONT = ("Microsoft YaHei UI", 10)
FONT_B = ("Microsoft YaHei UI", 10, "bold")
FONT_S = ("Microsoft YaHei UI", 9)
FONT_TAG = ("Microsoft YaHei UI", 8)
FONT_TITLE = ("Microsoft YaHei UI", 21, "bold")
FONT_BIG = ("Microsoft YaHei UI", 36, "bold")


# --------------------------------------------------------------------------- #
# 圆角绘制与组件
# --------------------------------------------------------------------------- #
def _rrect(x1, y1, x2, y2, r, seg=6):
    """圆角矩形多边形顶点（四段四分之一圆弧采样）。"""
    r = max(1, min(r, (x2 - x1) // 2, (y2 - y1) // 2))
    pts = []

    def arc(cx, cy, a0, a1):
        for i in range(seg + 1):
            a = math.radians(a0 + (a1 - a0) * i / seg)
            pts.extend([cx + r * math.cos(a), cy + r * math.sin(a)])

    arc(x2 - r, y1 + r, -90, 0)
    arc(x2 - r, y2 - r, 0, 90)
    arc(x1 + r, y2 - r, 90, 180)
    arc(x1 + r, y1 + r, 180, 270)
    return pts


class RoundBtn(tk.Canvas):
    """圆角按钮（自绘 Canvas）。支持 configure(state="disabled")。"""

    def __init__(self, master, text, command=None, bg=INPUT, fg=FG,
                 hover=INPUT_HOVER, font=FONT_S, padx=14, pady=6,
                 radius=8, width=None):
        f = tkfont.Font(font=font)
        w = width or (f.measure(text) + padx * 2)
        h = f.metrics("linespace") + pady * 2
        super().__init__(master, width=w, height=h, bg=master["bg"],
                         highlightthickness=0, cursor="hand2")
        self._cmd = command
        self._bg, self._hover_bg, self._fg = bg, hover, fg
        self._font, self._radius, self._text = font, radius, text
        self._enabled = True
        self._hovering = False
        self._draw()
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))

    def _draw(self):
        self.delete("all")
        w = int(self["width"])
        h = int(self["height"])
        bg = self._bg
        if self._enabled and self._hovering:
            bg = self._hover_bg
        self.create_polygon(_rrect(1, 1, w - 2, h - 2, self._radius),
                            fill=bg, outline="")
        self.create_text(w / 2, h / 2, text=self._text, font=self._font,
                         fill=self._fg if self._enabled else "#6b6b6b")

    def _click(self, _):
        if self._enabled and self._cmd:
            self._cmd()

    def _set_hover(self, h):
        self._hovering = h
        self._draw()

    def set_text(self, text: str):
        """更新按钮文字（自动重算宽度）。"""
        self._text = text
        f = tkfont.Font(font=self._font)
        self.configure(width=f.measure(text) + 28)
        self._draw()

    def configure(self, cnf=None, **kw):  # noqa: A003
        if "bg" in kw:
            self._bg = kw.pop("bg")
            self._draw()
        if "hover" in kw:
            self._hover_bg = kw.pop("hover")
            self._draw()
        if "state" in kw:
            self._enabled = kw.pop("state") != "disabled"
            if not self._enabled:
                self._hovering = False
                self.configure(cursor="arrow")
            else:
                self.configure(cursor="hand2")
            self._draw()
        if kw:
            super().configure(**kw)

    def state(self):  # noqa: A003
        return "normal" if self._enabled else "disabled"


class Card(tk.Frame):
    """圆角卡片：Canvas 画圆角矩形，inner frame 内嵌（颜色一致、视觉无缝）。

    先放置 inner（窗口条目常驻），再随 inner/canvas 的 Configure 双向适配尺寸。
    """

    def __init__(self, master, bg=PANEL, radius=10, border=BORDER,
                 padx=14, pady=12):
        super().__init__(master, bg=master["bg"])
        self._bg, self._border, self._r = bg, border, radius
        self._inset = max(1, radius // 2)
        self.canvas = tk.Canvas(self, bg=master["bg"], highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg, padx=padx, pady=pady)
        self._win = self.canvas.create_window(self._inset, self._inset,
                                              window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)

    def _on_inner(self, e):
        self.canvas.itemconfigure(self._win, height=e.height)
        self.canvas.configure(height=e.height + self._inset * 2)
        self._redraw()

    def _on_canvas(self, e):
        self.canvas.itemconfigure(self._win, width=max(1, e.width - self._inset * 2))
        self._redraw()

    def _redraw(self):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w <= 1 or h <= 1:
            return
        self.canvas.delete("bgrect")
        self.canvas.create_polygon(_rrect(0, 0, w - 1, h - 1, self._r),
                                   fill=self._bg, outline=self._border,
                                   width=1, tags="bgrect")


class PopupMenu(tk.Toplevel):
    """自绘深色右键菜单。

    原生 tk.Menu 弹窗由系统绘制，不跟随应用深色主题（浅色系统下会是白底黑字），
    这里用无边框 Toplevel + Label 自绘，配色与主界面一致。
    items: [(text, command) | None(分隔线)]，command 为 None 表示禁用行。
    """

    def __init__(self, master, items, x, y, min_width=190):
        super().__init__(master)
        self.withdraw()
        self.overrideredirect(True)
        self.configure(bg=BORDER)
        body = tk.Frame(self, bg=PANEL)
        body.pack(padx=1, pady=1)
        for it in items:
            if it is None:
                tk.Frame(body, bg=BORDER, height=1).pack(fill="x", padx=10,
                                                         pady=4)
                continue
            text, cmd = it
            danger = text.startswith("删除") or text.startswith("批量删除")
            fg = ("#f48771" if danger else FG) if cmd else "#6b6b6b"
            row = tk.Frame(body, bg=PANEL,
                           cursor="hand2" if cmd else "arrow")
            row.pack(fill="x")
            lb = tk.Label(row, text=text, bg=PANEL, fg=fg, font=FONT_S,
                          anchor="w", padx=14, pady=6)
            lb.pack(fill="x")
            if cmd:
                for w in (row, lb):
                    w.bind("<Enter>", lambda e, r=row, l=lb:
                           (r.configure(bg=ACCENT), l.configure(bg=ACCENT,
                                                                fg="#ffffff")))
                    w.bind("<Leave>", lambda e, r=row, l=lb, c=fg:
                           (r.configure(bg=PANEL), l.configure(bg=PANEL, fg=c)))
                    w.bind("<Button-1>", lambda e, f=cmd: self._run(f))
        self.update_idletasks()
        w = max(min_width, self.winfo_reqwidth())
        h = self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(4, min(x, sw - w - 12))
        y = max(4, min(y, sh - h - 52))
        self.geometry(f"{w}x{h}+{int(x)}+{int(y)}")
        self.deiconify()
        self.lift()
        try:
            self.attributes("-topmost", True)
            self.focus_force()
        except Exception:
            pass
        self.bind("<FocusOut>", lambda e: self._close())
        self.bind("<Escape>", lambda e: self._close())

    def _run(self, cmd):
        self._close()
        cmd()

    def _close(self):
        try:
            self.destroy()
        except Exception:
            pass


class RoundTile(tk.Canvas):
    """圆角设备磁贴（Canvas 自绘，悬停高亮，支持多选 / 右键菜单）。"""

    W, H, R = 216, 158, 12

    def __init__(self, master, on_click=None, on_right=None):
        super().__init__(master, width=self.W, height=self.H,
                         bg=master["bg"], highlightthickness=0, cursor="hand2")
        self._on_click = on_click
        self._on_right = on_right
        self._hover = False
        self._args = None
        self.dev_id = ""
        self.selected = False
        self.bind("<Button-1>", lambda e: self._on_click and self._on_click(e))
        self.bind("<Button-3>", lambda e: self._on_right and self._on_right(e))
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        # 网格拉伸（weight=1 + nsew）会把控件撑得比固定绘制区大，
        # 必须按实际尺寸重绘，否则鼠标进入控件边缘的空白也会误触高亮
        self.bind("<Configure>", lambda e: self._args and self.render_tile(
            *self._args))

    def _set_hover(self, h):
        self._hover = h
        if self._args:
            self.render_tile(*self._args)

    def render_tile(self, pct_text, color, name, sub, badge, bcolor, connected,
                    type_img=None):
        self._args = (pct_text, color, name, sub, badge, bcolor, connected,
                      type_img)
        self._type_img = type_img
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1:
            w = self.W
        if h <= 1:
            h = self.H
        bg = HOVER if self._hover else PANEL
        border = ACCENT_HOVER if self._hover else BORDER
        if self.selected:
            # 多选：选中底色 + 蓝色描边 + 右下角对勾
            bg = SEL
            border = "#1e8ad6"
        self.create_polygon(_rrect(1, 1, w - 2, h - 2, self.R),
                            fill=bg, outline=border,
                            width=2 if self.selected else 1)
        self.create_text(16, 16, text=pct_text, font=FONT_TITLE, fill=color,
                         anchor="nw")
        dot = OK if connected else "#4b5563"
        self.create_oval(w - 30, 28, w - 18, 40, fill=dot, outline="")
        img = self._type_img
        if img is not None:
            self.create_image(18, 66, image=img, anchor="nw")
            tx = 62
        else:
            tx = 16
        self.create_text(tx, 68, text=name, font=FONT_B, fill=FG,
                         anchor="nw", width=w - tx - 18)
        self.create_text(16, 108, text=sub, font=FONT_S, fill=FG_DIM,
                         anchor="nw", width=w - 34)
        self.create_text(16, h - 26, text=badge, font=FONT_TAG, fill=bcolor,
                         anchor="nw")
        if self.selected:
            self.create_oval(w - 34, h - 38, w - 14, h - 18,
                             fill="#1e8ad6", outline="")
            self.create_text(w - 24, h - 28, text="✓", font=FONT_S,
                             fill="#ffffff")


# --------------------------------------------------------------------------- #
# 窗口装饰：图标 / 深色标题栏
# --------------------------------------------------------------------------- #
def _apply_window_chrome(win, caption=BAR):
    """设置窗口图标 + Win11 深色标题栏（失败静默）。"""
    def job():
        try:
            from .common import app_ico_path
            win.iconbitmap(app_ico_path())
        except Exception:
            pass
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            if not hwnd:
                return
            val = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(val), 4)          # 深色标题栏
            r, g, b = (int(caption[i:i + 2], 16) for i in (1, 3, 5))
            col = ctypes.c_int((b << 16) | (g << 8) | r)  # COLORREF
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 35, ctypes.byref(col), 4)          # 标题栏颜色
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 36, ctypes.byref(col), 4)          # 边框颜色
        except Exception:
            pass
    win.after(60, job)


def _style():
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure(".", background=BG, foreground=FG, font=FONT)
    style.configure("TButton", background=INPUT, foreground=FG, padding=6,
                    borderwidth=0)
    style.map("TButton", background=[("active", INPUT_HOVER)])
    style.configure("TEntry", fieldbackground=INPUT, foreground=FG,
                    insertcolor=FG, borderwidth=0, padding=4)
    style.configure("TCombobox", fieldbackground=INPUT, foreground=FG,
                    background=INPUT, arrowcolor=FG, bordercolor=BORDER,
                    lightcolor=INPUT, darkcolor=INPUT, insertcolor=FG)
    style.map("TCombobox", fieldbackground=[("readonly", INPUT)],
              foreground=[("readonly", FG)],
              background=[("readonly", INPUT)],
              arrowcolor=[("active", "#ffffff")])
    # 下拉列表深色
    style.configure("TCombobox.Listbox", background=PANEL, foreground=FG,
                    fieldbackground=PANEL)
    style.configure("Vertical.TScrollbar", background=INPUT,
                    troughcolor=BG, bordercolor=BG, arrowcolor=FG_DIM)
    style.map("Vertical.TScrollbar", background=[("active", INPUT_HOVER)])
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure("Dim.TLabel", background=BG, foreground=FG_DIM)
    style.configure("Panel.TFrame", background=PANEL)
    return style


def _entry(master, textvariable=None, **kw):
    kw.setdefault("bg", INPUT)
    kw.setdefault("fg", FG)
    kw.setdefault("relief", "flat")
    kw.setdefault("insertbackground", FG)
    kw.setdefault("highlightthickness", 1)
    kw.setdefault("highlightbackground", BORDER)
    kw.setdefault("highlightcolor", ACCENT_HOVER)
    kw.setdefault("font", FONT)
    return tk.Entry(master, textvariable=textvariable, **kw)


def type_choices() -> list[str]:
    return ["自动检测"] + [device_type_name(k) for k in DEVICE_TYPES]


def type_key_from_choice(name: str) -> str | None:
    """选中的类型名 → key；"自动检测" 返回 None。"""
    for k in DEVICE_TYPES:
        if device_type_name(k) == name:
            return k
    return None


class MainWindow(tk.Tk):
    def __init__(self, app, silent: bool = False):
        super().__init__()
        self.app = app
        self.title("硬橙控")
        self.geometry("780x540")
        self.minsize(580, 400)
        self.configure(bg=BG)
        _style()
        # ttk Combobox 下拉列表深色化
        for opt, val in (("*TCombobox*Listbox.background", PANEL),
                         ("*TCombobox*Listbox.foreground", FG),
                         ("*TCombobox*Listbox.selectBackground", SEL),
                         ("*TCombobox*Listbox.selectForeground", "#ffffff")):
            self.option_add(opt, val)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._type_imgs: dict[str, "ImageTk.PhotoImage"] = {}
        # 多选状态 / 最近一次渲染数据（供右键菜单与批量操作使用）
        self._devices: list[dict] = []
        self._status: dict[str, dict] = {}
        self._selected: set[str] = set()
        self._anchor: str | None = None
        self._menu: PopupMenu | None = None
        self._build()
        _apply_window_chrome(self)
        if silent:
            # 开机静默启动：只驻留托盘，不弹主窗口
            self.withdraw()

    def type_photo(self, tkey: str, size: int = 36):
        """设备类型图标（PhotoImage，带缓存）。"""
        key = f"{tkey}:{size}"
        if key not in self._type_imgs:
            try:
                from PIL import ImageTk
                self._type_imgs[key] = ImageTk.PhotoImage(
                    make_type_icon(tkey, size))
            except Exception:
                return None
        return self._type_imgs.get(key)

    # ------------------------------------------------------------------ #
    def _build(self):
        # VS Code 蓝色状态栏（底部）
        self.status_bar = tk.Frame(self, bg=STATUS, height=26)
        self.status_bar.pack(side="bottom", fill="x")
        self.status_bar.pack_propagate(False)
        self.status_var = tk.StringVar(value="就绪")
        tk.Label(self.status_bar, textvariable=self.status_var, bg=STATUS,
                 fg="#ffffff", font=FONT_S, anchor="w").pack(side="left", padx=10)

        # 标题栏
        bar = tk.Frame(self, bg=BAR, pady=8, padx=12)
        bar.pack(fill="x")
        try:
            from PIL import Image, ImageTk
            self._img = ImageTk.PhotoImage(
                make_app_icon().resize((22, 22), Image.LANCZOS))
            tk.Label(bar, image=self._img, bg=BAR).pack(side="left", padx=(0, 8))
        except Exception:
            pass
        tk.Label(bar, text="硬橙控·主页", font=("Microsoft YaHei UI", 12, "bold"),
                 bg=BAR, fg=FG).pack(side="left")
        # 版本徽标（点击打开「关于」）
        ver = tk.Label(bar, text=VERSION, bg=BAR, fg=FG_DIM, font=FONT_S,
                       cursor="hand2")
        ver.pack(side="left", padx=(8, 0))
        ver.bind("<Button-1>", lambda _e: self.app.open_about())
        ver.bind("<Enter>", lambda _e: ver.configure(fg=FG))
        ver.bind("<Leave>", lambda _e: ver.configure(fg=FG_DIM))
        for text, cmd in (("设置", self.app.open_settings),
                          ("刷新全部", self.app.refresh_all_async),
                          ("添加产品链接", self.app.open_link_add),
                          ("添加已连接设备", self.app.open_scan),
                          ("本地外设管理器", self.app.open_local_manager)):
            RoundBtn(bar, text, cmd, bg=INPUT, hover=INPUT_HOVER).pack(
                side="right", padx=(6, 0))

        # 多选操作条（默认隐藏，有选中项时才显示）
        self.sel_bar = tk.Frame(self, bg=SEL)
        self.sel_var = tk.StringVar(value="")
        tk.Label(self.sel_bar, textvariable=self.sel_var, bg=SEL,
                 fg="#cfe3ff", font=FONT_S).pack(side="left", padx=12, pady=6)
        RoundBtn(self.sel_bar, "刷新所选", self._batch_refresh,
                 bg=INPUT, hover=INPUT_HOVER).pack(side="right", padx=(6, 12),
                                                  pady=4)
        RoundBtn(self.sel_bar, "删除所选", self._batch_delete,
                 bg=DANGER, hover=DANGER_HOVER, fg="#ffffff").pack(
            side="right", pady=4)
        RoundBtn(self.sel_bar, "全选", self._select_all,
                 bg=INPUT, hover=INPUT_HOVER).pack(side="right", padx=(6, 0),
                                                  pady=4)
        RoundBtn(self.sel_bar, "取消选择", self._clear_selection,
                 bg=INPUT, hover=INPUT_HOVER).pack(side="right", padx=(6, 0),
                                                  pady=4)

        # 可滚动磁贴区
        wrap = tk.Frame(self, bg=BG)
        self.wrap = wrap
        wrap.pack(fill="both", expand=True, padx=14, pady=12)
        self.canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.grid_frame = tk.Frame(self.canvas, bg=BG)
        self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw",
                                  tags="inner")
        self.grid_frame.bind("<Configure>",
                             lambda e: self.canvas.configure(
                                 scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure("inner", width=e.width))
        self.canvas.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(
            int(-e.delta / 120), "units"))
        # 空白处右键：全选 / 取消选择 / 刷新全部
        for w in (self.canvas, self.grid_frame):
            w.bind("<Button-3>", self._on_bg_right)
        # 批量操作快捷键
        self.bind("<Delete>", lambda e: self._batch_delete())
        self.bind("<Control-a>", lambda e: self._select_all())
        self.bind("<Escape>", lambda e: self._clear_selection())

    def _on_close(self):
        # 关闭窗口 = 最小化到托盘
        self.withdraw()

    # ------------------------------------------------------------------ #
    def set_status(self, text: str):
        self.status_var.set(text)

    def render(self, devices: list[dict], status: dict[str, dict]):
        self._devices = list(devices)
        self._status = status or {}
        self._selected &= {d["id"] for d in self._devices}
        self._update_sel_bar()
        for w in self.grid_frame.winfo_children():
            w.destroy()
        if not devices:
            tk.Label(self.grid_frame,
                     text="还没有设备\n\n点击右上角「扫描添加设备」发现蓝牙 / 2.4G 无线外设，\n或粘贴官网产品页链接添加。",
                     bg=BG, fg=FG_DIM, font=("Microsoft YaHei UI", 11),
                     justify="center").pack(pady=70)
            return
        if self.app.config.settings.get("main_view", "tile") == "list":
            self._render_list(devices, status)
            return
        cols = max(1, (self.grid_frame.winfo_width() or 740) // 232)
        for i, dev in enumerate(devices):
            r, c = divmod(i, max(cols, 1))
            self._tile(dev, status.get(dev["id"], {}), r, c)

    def _render_list(self, devices: list[dict], status: dict[str, dict]):
        """列表视图：紧凑行，每行 图标+名称+徽标+电量，点击进详情。"""
        for dev in devices:
            st = status.get(dev["id"], {})
            battery = st.get("battery")
            precision = st.get("precision") or "none"
            color = battery_hex(battery)
            if battery is None:
                pct_text = "？"
            elif precision == "exact":
                pct_text = f"{battery}%"
            else:
                pct_text = f"≈{battery}%"
            if not st.get("connected"):
                badge, bcolor = "未连接", "#6e7681"
            elif precision == "exact":
                badge, bcolor = "● 精确", OK
            elif precision == "coarse":
                badge, bcolor = "≈ 粗略", "#d29922"
            else:
                badge, bcolor = "无数据", "#6e7681"
            sel = dev["id"] in self._selected
            bgc = SEL if sel else PANEL
            row = tk.Frame(self.grid_frame, bg=bgc, cursor="hand2")
            row.pack(fill="x", padx=6, pady=3)
            inner = tk.Frame(row, bg=bgc)
            inner.pack(fill="x", padx=12, pady=7)
            tkey = dev.get("dev_type") or guess_device_type(dev.get("name", ""))
            img = self.type_photo(tkey, 24)
            if img is not None:
                tk.Label(inner, image=img, bg=bgc).pack(
                    side="left", padx=(0, 10))
            tk.Label(inner, text=dev["name"], bg=bgc, fg=FG,
                     font=FONT_B).pack(side="left")
            _tp = (dev.get("transport") or "").lower()
            tk.Label(inner, text={"2.4g": "2.4G 无线", "usb": "USB 有线"}.get(_tp, "蓝牙"),
                     bg=bgc, fg=FG_DIM, font=FONT_S).pack(side="left", padx=(8, 0))
            tk.Label(inner, text=badge, bg=bgc, fg=bcolor,
                     font=FONT_S).pack(side="right", padx=(0, 14))
            tk.Label(inner, text=pct_text, bg=bgc, fg=color,
                     font=FONT_B).pack(side="right")
            for w in [row, inner] + list(inner.winfo_children()):
                w.bind("<Button-1>",
                       lambda e, d=dev: self._on_tile_click(e, d))
                w.bind("<Button-3>",
                       lambda e, d=dev: self._on_tile_right(e, d))

    def _tile(self, dev, st, r, c):
        battery = st.get("battery")
        precision = st.get("precision") or "none"
        color = battery_hex(battery)
        if battery is None:
            pct_text = "？"
        elif precision == "exact":
            pct_text = f"{battery}%"
        else:
            pct_text = f"≈{battery}%"
        state = "已连接" if st.get("connected") else "未连接"
        _tp = (dev.get("transport") or "").lower()
        _tpname = {"2.4g": "2.4G 无线", "usb": "USB 有线"}.get(_tp, "蓝牙")
        if not st.get("connected"):
            badge, bcolor = "未连接", "#6e7681"
        elif precision == "exact":
            badge, bcolor = "● 精确电量", OK
        elif precision == "coarse":
            badge, bcolor = "≈ 系统粗略值", "#d29922"
        else:
            badge, bcolor = "无电量数据", "#6e7681"
        sub = f"{state} · {_tpname}"
        tkey = dev.get("dev_type") or guess_device_type(dev.get("name", ""))
        tile = RoundTile(self.grid_frame,
                         on_click=lambda e, d=dev: self._on_tile_click(e, d),
                         on_right=lambda e, d=dev: self._on_tile_right(e, d))
        tile.dev_id = dev["id"]
        tile.selected = dev["id"] in self._selected
        tile.render_tile(pct_text, color, dev["name"], sub, badge, bcolor,
                         bool(st.get("connected")),
                         self.type_photo(tkey, 36))
        # 固定小卡片尺寸，从左到右一个个摆放，不随窗口拉伸铺满整行
        tile.grid(row=r, column=c, padx=6, pady=6)

    # ------------------------------------------------------------------ #
    # 多选 / 右键菜单 / 批量操作
    # ------------------------------------------------------------------ #
    def _update_sel_bar(self):
        n = len(self._selected)
        if not n:
            self.sel_var.set("")
            if getattr(self, "_sel_shown", False):
                self.sel_bar.pack_forget()
                self._sel_shown = False
            return
        self.sel_var.set(f"已选择 {n} 台设备")
        if not getattr(self, "_sel_shown", False):
            self.sel_bar.pack(fill="x", padx=14, pady=(10, 0),
                              before=self.wrap)
            self._sel_shown = True

    def _after_selection_change(self):
        self._update_sel_bar()
        self.render(self._devices, self._status)

    def _select_all(self):
        if not self._devices:
            return
        self._selected = {d["id"] for d in self._devices}
        self._after_selection_change()

    def _clear_selection(self):
        if not self._selected:
            return
        self._selected.clear()
        self._anchor = None
        self._after_selection_change()

    def clear_selection(self):
        """清空选中（外部调用；不触发重绘，由调用方负责）。"""
        self._selected.clear()
        self._anchor = None
        self._update_sel_bar()

    def _on_tile_click(self, e, dev):
        """左键：普通点击进详情；Ctrl 切换选中；Shift 连选。"""
        state = getattr(e, "state", 0)
        ctrl = bool(state & 0x0004)
        shift = bool(state & 0x0001)
        did = dev["id"]
        if ctrl:
            if did in self._selected:
                self._selected.discard(did)
            else:
                self._selected.add(did)
            self._anchor = did
            self._after_selection_change()
        elif shift and self._anchor:
            ids = [d["id"] for d in self._devices]
            if self._anchor in ids and did in ids:
                i, j = sorted((ids.index(self._anchor), ids.index(did)))
                self._selected |= set(ids[i:j + 1])
            self._after_selection_change()
        else:
            self.app.open_detail(did)

    def _on_tile_right(self, e, dev):
        """右键：未选中则先选中它；多选时给批量菜单，否则给单设备菜单。"""
        did = dev["id"]
        if did not in self._selected:
            self._selected = {did}
            self._anchor = did
            self._after_selection_change()
        if len(self._selected) > 1:
            self._show_batch_menu(e.x_root, e.y_root)
        else:
            self._show_device_menu(e.x_root, e.y_root, dev)

    def _on_bg_right(self, e):
        self._popup([
            ("全选", self._select_all),
            ("取消选择", self._clear_selection),
            None,
            ("刷新全部设备", self.app.refresh_all_async),
        ], e.x_root, e.y_root)

    def _popup(self, items, x, y):
        try:
            if self._menu is not None:
                self._menu.destroy()
        except Exception:
            pass
        self._menu = PopupMenu(self, items, x, y)

    def _show_device_menu(self, x, y, dev):
        did = dev["id"]
        tray_on = dev.get("show_in_tray", True)
        alert_on = dev.get("low_battery_alert", True)
        st = self._status.get(did, {})
        battery = st.get("battery")
        head = f"{dev['name']} · " + ("?" if battery is None else f"{battery}%")
        self._popup([
            (head, None),
            None,
            ("打开详情页", lambda: self.app.open_detail(did)),
            ("刷新此设备电量", lambda: self.app.refresh_async([did])),
            None,
            (("✓ 在托盘中显示" if tray_on else "　 在托盘中显示"),
             lambda: self.app.toggle_show(did)),
            (("✓ 低电量提醒" if alert_on else "　 低电量提醒"),
             lambda: self.app.toggle_alert(did)),
            None,
            ("删除此设备（不影响系统连接）",
             lambda: self._delete_devices([did])),
        ], x, y)

    def _show_batch_menu(self, x, y):
        ids = sorted(self._selected)
        names = [d["name"] for d in self._devices if d["id"] in ids]
        head = "、".join(names[:3]) + ("…" if len(names) > 3 else "")
        n = len(ids)
        self._popup([
            (f"已选 {n} 台：{head}", None),
            None,
            (f"批量刷新电量（{n} 台）", lambda: self.app.refresh_async(ids)),
            (f"批量删除（{n} 台）", lambda: self._delete_devices(ids)),
            None,
            ("全选", self._select_all),
            ("取消选择", self._clear_selection),
        ], x, y)

    def _batch_refresh(self):
        ids = sorted(self._selected)
        if ids:
            self.app.refresh_async(ids)

    def _batch_delete(self):
        if self._selected:
            self._delete_devices(sorted(self._selected))

    def _delete_devices(self, ids):
        ids = [i for i in ids if any(d["id"] == i for d in self._devices)]
        if not ids:
            return
        names = [d["name"] for d in self._devices if d["id"] in ids]
        if len(ids) == 1:
            msg = (f"确定要从软件中移除「{names[0]}」吗？\n\n"
                   "只移除本软件的记录，不影响系统蓝牙配对与连接。")
        else:
            msg = (f"确定要从软件中移除以下 {len(ids)} 台设备吗？\n\n" +
                   "、".join(names[:10]) + ("…" if len(names) > 10 else "") +
                   "\n\n只移除本软件的记录，不影响系统蓝牙配对与连接。")
        if not messagebox.askyesno("删除设备", msg, parent=self):
            return
        self._selected.clear()
        self._anchor = None
        self.app.remove_devices(ids)


class _ToplevelBase(tk.Toplevel):
    def __init__(self, app, title, w=460, h=380):
        super().__init__(MainWindow.instance)
        self.app = app
        self.title(title)
        self.geometry(f"{w}x{h}")
        self.configure(bg=BG)
        self.transient(MainWindow.instance)
        _apply_window_chrome(self)
        self._type_imgs: dict[str, "ImageTk.PhotoImage"] = {}

    def circled_photo(self, tkey: str, size: int = 40):
        """圆形深色底 + 类型图标（扫描列表卡片用，带缓存）。"""
        key = f"circ:{tkey}:{size}"
        if key not in self._type_imgs:
            try:
                from PIL import Image, ImageDraw, ImageTk
                icon = make_type_icon(tkey, size=max(10, int(size * 0.58)),
                                      color=(206, 213, 222, 255))
                img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
                d = ImageDraw.Draw(img)
                d.ellipse([0, 0, size - 1, size - 1], fill=(42, 48, 55, 255))
                off = (size - icon.width) // 2
                img.alpha_composite(icon, (off, off))
                self._type_imgs[key] = ImageTk.PhotoImage(img)
            except Exception:
                return None
        return self._type_imgs.get(key)

    def row(self, parent, label, width=10):
        fr = tk.Frame(parent, bg=BG)
        fr.pack(fill="x", pady=4)
        tk.Label(fr, text=label, width=width, anchor="e", bg=BG, fg=FG_DIM,
                 font=FONT_S).pack(side="left", padx=(0, 8))
        return fr


class ScanWindow(_ToplevelBase):
    """扫描：蓝牙（广播 + 已配对）与本机 USB / 2.4G 外设一起列出。

    列表为卡片式：左侧圆形类型图标，名称加粗，下面一行是连接方式；
    已添加过的设备右侧带「已在设备库中」徽标；点选的卡片描绿边。
    """

    BADGE_FG = "#e3b341"
    BADGE_BD = "#7a5b13"

    def __init__(self, app):
        super().__init__(app, "扫描添加设备", 620, 560)
        tk.Label(self,
                 text="蓝牙：扫描附近正在广播的设备（约 8 秒）\n"
                      "2.4G：直接列出插在电脑上的接收器与其下挂设备（不用等广播）",
                 bg=BG, fg=FG_DIM, font=FONT_S, justify="left").pack(
            anchor="w", padx=16, pady=(14, 6))
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=16)
        self.scan_btn = RoundBtn(bar, "重新扫描", self._scan, bg=ACCENT,
                                 hover=ACCENT_HOVER, fg="#ffffff", font=FONT_B,
                                 padx=16, pady=6)
        self.scan_btn.pack(side="left")
        self.hint = tk.Label(bar, text="", bg=BG, fg=FG_DIM, font=FONT_S)
        self.hint.pack(side="left", padx=10)

        # 设备类型
        tf = tk.Frame(self, bg=BG)
        tf.pack(fill="x", padx=16, pady=(2, 0))
        tk.Label(tf, text="设备类型", bg=BG, fg=FG_DIM, font=FONT_S).pack(
            side="left", padx=(0, 8))
        self.type_var = tk.StringVar(value="自动检测")
        ttk.Combobox(tf, textvariable=self.type_var, state="readonly",
                     values=type_choices(), font=FONT_S, width=22).pack(side="left")

        # 可滚动卡片列表
        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=True, padx=16, pady=10)
        self.canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=BG)
        self._iw = self.canvas.create_window((0, 0), window=self.inner,
                                             anchor="nw")
        self.inner.bind(
            "<Configure>",
            lambda _e: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")))
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure(self._iw, width=e.width))
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", padx=16, pady=(0, 14))
        RoundBtn(foot, "添加选中设备", self._add, font=FONT, padx=16,
                 pady=6).pack(side="right")
        self._found: list[dict] = []      # 蓝牙
        self._usb: list[dict] = []        # 2.4G / USB
        self._rows: list[dict] = []       # 列表行 → 数据
        self._sel: int = -1
        self._bt_done = False
        self._usb_done = False
        self._scan()

    def _on_close(self):
        try:
            self.unbind_all("<MouseWheel>")
        except Exception:
            pass
        self.destroy()

    def _on_wheel(self, e):
        if e.delta:
            self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

    # ---------------- 扫描 ----------------
    def _scan(self):
        self.scan_btn.configure(state="disabled")
        self.hint.configure(text="扫描中…")
        for w in self.inner.winfo_children():
            w.destroy()
        self._found, self._usb, self._rows, self._sel = [], [], [], -1
        self._bt_done = self._usb_done = False
        self.app.scan_usb_async(self)

        def work():
            res = self.app.worker.scan_le(8)
            self.app._emit("scan_result", self, res)

        threading.Thread(target=work, daemon=True).start()

    def on_scan_result(self, res: dict):
        self._bt_done = True
        self._found = res.get("devices", []) if "error" not in res else []
        if "error" in res:
            self.hint.configure(text=f"蓝牙扫描失败: {res['error']}")
        # 同时列出系统已配对的蓝牙设备
        listed = self.app.worker.list_devices()
        paired = listed.get("devices", []) if "error" not in listed else []
        seen_addr = {d["address"] for d in self._found if d.get("address")}
        seen_names = {d["name"].strip().lower() for d in self._found
                      if d.get("name")}
        for p in paired:
            nm = (p.get("name") or "").strip().lower()
            if p.get("address") and p["address"] in seen_addr:
                continue
            if nm and nm in seen_names:
                continue
            if nm:
                seen_names.add(nm)
            self._found.append({"address": p.get("address"), "name": p["name"],
                                "paired": True, "connected": p.get("connected"),
                                "container_id": p.get("container_id", "")})
        self._render()

    def on_usb_result(self, res: dict):
        self._usb_done = True
        self._usb = res.get("devices", []) if "error" not in res else []
        if "error" in res:
            self.hint.configure(text=f"USB 枚举失败: {res['error']}")
        self._render()

    # ---------------- 渲染 ----------------
    def _in_library(self, source: str, d: dict) -> bool:
        devs = self.app.config.devices
        if source == "bt":
            return any(x.get("address") is not None and d.get("address") is not None
                       and x["address"] == d["address"] for x in devs)
        base = device_key_of(d.get("instance_id", ""))
        if not base:
            return False
        for x in devs:
            if x.get("transport") in ("2.4g", "usb") and \
                    device_key_of(x.get("hid_key", "")) == base:
                return True
        return False

    def _make_row(self, idx: int, tkey: str, name: str, sub: str,
                  in_lib: bool):
        row = tk.Frame(self.inner, bg=PANEL, highlightthickness=1,
                       highlightbackground=BORDER)
        row.pack(fill="x", padx=2, pady=3)
        photo = self.circled_photo(tkey, 40)
        ic = tk.Label(row, image=photo, bg=PANEL) if photo else \
            tk.Label(row, text="·", bg=PANEL, fg=FG_DIM, font=FONT_B)
        ic.pack(side="left", padx=(12, 10), pady=8)
        txt = tk.Frame(row, bg=PANEL)
        txt.pack(side="left", fill="x", expand=True, pady=7)
        tk.Label(txt, text=name, bg=PANEL, fg="#e8e8e8", font=FONT_B,
                 anchor="w").pack(fill="x")
        tk.Label(txt, text=sub, bg=PANEL, fg=FG_DIM, font=FONT_S,
                 anchor="w").pack(fill="x")
        if in_lib:
            f = tkfont.Font(font=FONT_S)
            bw = f.measure("已在设备库中") + 20
            badge = tk.Canvas(row, bg=PANEL, highlightthickness=0,
                              width=bw, height=24)
            badge.pack(side="right", padx=(8, 12))
            badge.create_polygon(_rrect(1, 1, bw - 1, 23, 8), fill="",
                                 outline=self.BADGE_BD, width=1)
            badge.create_text(bw // 2, 12, text="已在设备库中",
                              fill=self.BADGE_FG, font=FONT_S)
        widgets = [row, ic, txt, *txt.winfo_children()]
        for wgt in widgets:
            wgt.bind("<Button-1>", lambda _e, i=idx: self._select(i))
            wgt.bind("<Double-Button-1>", lambda _e: self._add())
        return {"frame": row, "widgets": widgets}

    def _select(self, idx: int):
        self._sel = idx
        for i, r in enumerate(self._rows):
            color = OK if i == idx else BORDER
            for wgt in r["widgets"]:
                try:
                    wgt.configure(highlightbackground=color)
                except tk.TclError:
                    pass

    def _render(self):
        if self._bt_done and self._usb_done:
            self.scan_btn.configure(state="normal")
            if self.hint.cget("text") == "扫描中…":
                self.hint.configure(text="")
        for w in self.inner.winfo_children():
            w.destroy()
        self._rows, self._sel = [], -1
        for d in self._usb:
            tkey = d.get("dev_type") or guess_device_type(d.get("name", ""))
            name = d["name"]
            if d.get("battery") is not None:
                name = f"{name}"          # 电量在详情里看，卡片保持简洁
            sub = d.get("transport_name") or "USB"
            in_lib = self._in_library("usb", d)
            self._rows.append(self._make_row(len(self._rows), tkey, name,
                                             sub, in_lib))
            self._rows[-1]["data"] = {"source": "usb", "data": d}
        for d in self._found:
            tkey = guess_device_type(d.get("name", ""))
            sub = "蓝牙"
            if d.get("connected"):
                sub = "蓝牙 · 已连接"
            elif d.get("paired"):
                sub = "蓝牙 · 已配对"
            in_lib = self._in_library("bt", d)
            self._rows.append(self._make_row(len(self._rows), tkey,
                                             d["name"], sub, in_lib))
            self._rows[-1]["data"] = {"source": "bt", "data": d}

    # ---------------- 添加 ----------------
    def _add(self):
        if not (0 <= self._sel < len(self._rows)):
            return
        row = self._rows[self._sel]["data"]
        d = row["data"]
        if row["source"] == "usb":
            tkey = type_key_from_choice(self.type_var.get()) \
                or d.get("dev_type") or guess_device_type(d.get("name", ""))
            dev = self.app.add_device(
                name=d["name"], dev_type=tkey, transport=d.get("transport") or "2.4g",
                hid_key=d.get("instance_id", ""), vid=d.get("vid", 0),
                pid=d.get("pid", 0))
        else:
            tkey = type_key_from_choice(self.type_var.get()) \
                or guess_device_type(d.get("name", ""))
            dev = self.app.add_device(name=d["name"], address=d.get("address"),
                                      container_id=d.get("container_id", ""),
                                      dev_type=tkey, transport="bt")
        self.app.refresh_all_async()
        self.destroy()
        self.app.open_detail(dev["id"])


class AboutWindow(_ToplevelBase):
    """关于：版本、更新内容、作者与项目主页。"""

    def __init__(self, app):
        super().__init__(app, "关于", 470, 440)
        app.windows["about"] = self

        # ---- 头部：图标 + 名称 + 版本徽标 ----
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=22, pady=(22, 14))
        try:
            from PIL import Image, ImageTk
            self._icon = ImageTk.PhotoImage(
                make_app_icon().resize((64, 64), Image.LANCZOS))
            tk.Label(head, image=self._icon, bg=BG).pack(side="left",
                                                         padx=(0, 16))
        except Exception:
            pass
        box = tk.Frame(head, bg=BG)
        box.pack(side="left", anchor="w")
        tk.Label(box, text=APP_NAME, bg=BG, fg="#e8e8e8",
                 font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
        f = tkfont.Font(font=FONT_S)
        bw = f.measure(VERSION) + 24
        badge = tk.Canvas(box, bg=BG, highlightthickness=0,
                          width=bw, height=22)
        badge.pack(anchor="w", pady=(5, 0))
        badge.create_polygon(_rrect(1, 1, bw - 1, 21, 10), fill="",
                             outline=ACCENT_HOVER, width=1)
        badge.create_text(bw // 2, 11, text=VERSION, fill="#4daafc",
                          font=FONT_S)

        # ---- 更新内容 ----
        card = tk.Frame(self, bg=PANEL, highlightthickness=1,
                        highlightbackground=BORDER)
        card.pack(fill="x", padx=22)
        tk.Label(card, text="更新内容", bg=PANEL, fg=FG_DIM, font=FONT_S,
                 anchor="w").pack(fill="x", padx=16, pady=(13, 2))
        tk.Label(card, text=RELEASE_NOTE, bg=PANEL, fg="#e8e8e8",
                 font=("Microsoft YaHei UI", 12, "bold"), anchor="w").pack(
            fill="x", padx=16, pady=(0, 14))

        # ---- 作者 / 项目主页 ----
        info = tk.Frame(self, bg=BG)
        info.pack(fill="x", padx=22, pady=(16, 0))
        self._line(info, "作者", AUTHOR)
        gh = tk.Frame(info, bg=BG)
        gh.pack(fill="x", pady=4)
        tk.Label(gh, text="作者主页", width=7, anchor="e", bg=BG, fg=FG_DIM,
                 font=FONT_S).pack(side="left", padx=(0, 10))
        link = tk.Label(gh, text=GITHUB_LABEL, bg=BG, fg="#4daafc",
                        font=FONT_S, cursor="hand2")
        link.pack(side="left")
        link.bind("<Button-1>", lambda _e: self._open_github())
        link.bind("<Enter>", lambda _e: link.configure(fg="#79c0ff"))
        link.bind("<Leave>", lambda _e: link.configure(fg="#4daafc"))

        tk.Label(self, text="蓝牙 / 2.4G 无线外设电量监控 · Python + tkinter + pystray",
                 bg=BG, fg=FG_DIM, font=FONT_S).pack(pady=(18, 0))

        # ---- 底部按钮 ----
        foot = tk.Frame(self, bg=BG)
        foot.pack(side="bottom", fill="x", padx=22, pady=(0, 18))
        RoundBtn(foot, "关闭", self.destroy, font=FONT, padx=18,
                 pady=6).pack(side="right")
        RoundBtn(foot, "打开 GitHub", self._open_github, bg=ACCENT,
                 hover=ACCENT_HOVER, fg="#ffffff", font=FONT,
                 padx=16, pady=6).pack(side="right", padx=(0, 8))
        RoundBtn(foot, "复制版本信息", self._copy_info, font=FONT_S,
                 padx=12, pady=6).pack(side="left")

    def destroy(self):
        try:
            self.app.windows.pop("about", None)
        except Exception:
            pass
        super().destroy()

    @staticmethod
    def _line(parent, label: str, value: str):
        fr = tk.Frame(parent, bg=BG)
        fr.pack(fill="x", pady=4)
        tk.Label(fr, text=label, width=7, anchor="e", bg=BG, fg=FG_DIM,
                 font=FONT_S).pack(side="left", padx=(0, 10))
        tk.Label(fr, text=value, bg=BG, fg=FG, font=FONT_S).pack(side="left")

    def _open_github(self):
        import os
        import webbrowser
        try:
            os.startfile(GITHUB_URL)  # noqa: S606
        except Exception:
            webbrowser.open(GITHUB_URL)

    def _copy_info(self):
        text = (f"{APP_NAME} {VERSION}\n"
                f"更新内容：{RELEASE_NOTE}\n"
                f"作者：{AUTHOR}\n"
                f"GitHub：{GITHUB_URL}")
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.app.main.set_status("版本信息已复制到剪贴板。")
        except Exception:
            pass


class LocalManagerWindow(_ToplevelBase):
    """本地外设管理器：系统声音输出切换、内置键盘代码模式开关。"""

    def __init__(self, app):
        super().__init__(app, "本地外设管理器", 500, 610)
        app.windows["localmgr"] = self

        tk.Label(self, text="声音输出（点击切换系统默认输出）", bg=BG,
                 fg=FG_DIM, font=FONT_S).pack(anchor="w", padx=16,
                                              pady=(14, 4))
        self.audio_box = tk.Frame(self, bg=BG)
        self.audio_box.pack(fill="x", padx=16)

        tk.Label(self, text="内置键盘", bg=BG, fg=FG_DIM,
                 font=FONT_S).pack(anchor="w", padx=16, pady=(14, 4))
        self._build_keyboard_row()

        foot = tk.Frame(self, bg=BG)
        foot.pack(side="bottom", fill="x", padx=16, pady=(6, 14))
        RoundBtn(foot, "打开系统声音设置", self._open_sound_settings,
                 font=FONT_S, padx=12, pady=5).pack(side="left")
        RoundBtn(foot, "刷新", self._load_audio, font=FONT_S, padx=12,
                 pady=5).pack(side="right")
        self._load_audio()

    # ---------------- 声音输出 ----------------
    def _load_audio(self):
        for w in self.audio_box.winfo_children():
            w.destroy()
        tk.Label(self.audio_box, text="读取中…", bg=BG, fg=FG_DIM,
                 font=FONT_S).pack(anchor="w", pady=4)

        def work():
            res = localdev.list_audio_outputs()
            self.app._emit("localmgr_audio", self, res)

        threading.Thread(target=work, daemon=True).start()

    def on_audio(self, res: dict):
        for w in self.audio_box.winfo_children():
            w.destroy()
        if res.get("error"):
            tk.Label(self.audio_box, text=f"读取失败：{res['error']}",
                     bg=BG, fg="#d29922", font=FONT_S).pack(anchor="w", pady=4)
            return
        if not res.get("devices"):
            tk.Label(self.audio_box, text="没有可用的声音输出设备",
                     bg=BG, fg=FG_DIM, font=FONT_S).pack(anchor="w", pady=4)
            return
        for d in res["devices"]:
            self._make_audio_row(d)

    def _make_audio_row(self, d: dict):
        row = tk.Frame(self.audio_box, bg=PANEL, highlightthickness=1,
                       highlightbackground=BORDER)
        row.pack(fill="x", pady=3)
        low = d["name"].lower()
        tkey = "headset" if any(k in low for k in
                                ("耳机", "headphone", "headset", "earbud")) \
            else "speaker"
        photo = self.circled_photo(tkey, 36)
        ic = tk.Label(row, image=photo, bg=PANEL) if photo else \
            tk.Label(row, text="·", bg=PANEL, fg=FG_DIM, font=FONT_B)
        ic.pack(side="left", padx=(12, 10), pady=7)
        tk.Label(row, text=d["name"], bg=PANEL, fg="#e8e8e8",
                 font=FONT_B, anchor="w").pack(side="left", fill="x",
                                               expand=True)
        if d.get("default"):
            f = tkfont.Font(font=FONT_S)
            bw = f.measure("当前默认") + 20
            badge = tk.Canvas(row, bg=PANEL, highlightthickness=0,
                              width=bw, height=24)
            badge.pack(side="right", padx=(8, 12))
            badge.create_polygon(_rrect(1, 1, bw - 1, 23, 8), fill="",
                                 outline=OK, width=1)
            badge.create_text(bw // 2, 12, text="当前默认", fill=OK,
                              font=FONT_S)
        if not d.get("default"):
            widgets = [row, ic]
            for wgt in widgets:
                wgt.bind("<Button-1>", lambda _e, dev=d: self._set_default(dev))
                wgt.configure(cursor="hand2")

    def _set_default(self, d: dict):
        self.app.main.set_status(f"正在切换声音输出到「{d['name']}」…")

        def work():
            r = localdev.set_default_output(d["id"])
            if not r.get("ok"):
                self.app.main.set_status(f"切换失败：{r.get('error')}")
            res = localdev.list_audio_outputs()
            self.app._emit("localmgr_audio", self, res)
            if r.get("ok"):
                self.app.main.set_status(f"声音输出已切换到「{d['name']}」")

        threading.Thread(target=work, daemon=True).start()

    def _open_sound_settings(self):
        import os
        try:
            os.startfile("ms-settings:sound")  # noqa: S606
        except Exception:
            import subprocess
            subprocess.Popen(["control.exe", "mmsys.cpl"])

    # ---------------- 内置键盘 ----------------
    def _build_keyboard_row(self):
        row = tk.Frame(self, bg=PANEL, highlightthickness=1,
                       highlightbackground=BORDER)
        row.pack(fill="x", padx=16)

        top = tk.Frame(row, bg=PANEL)
        top.pack(fill="x")
        photo = self.circled_photo("keyboard", 36)
        ic = tk.Label(top, image=photo, bg=PANEL) if photo else \
            tk.Label(top, text="·", bg=PANEL, fg=FG_DIM, font=FONT_B)
        ic.pack(side="left", padx=(12, 10), pady=(9, 5))
        txt = tk.Frame(top, bg=PANEL)
        txt.pack(side="left", fill="x", expand=True, pady=(9, 5))
        tk.Label(txt, text="内置键盘", bg=PANEL, fg="#e8e8e8",
                 font=FONT_B, anchor="w").pack(fill="x")
        tk.Label(txt, text="代码模式 · 括号补齐 / 中英文自动切换",
                 bg=PANEL, fg=FG_DIM, font=FONT_S, anchor="w").pack(fill="x")
        self.cm_btn = RoundBtn(top, "开启代码模式", self._toggle_cm,
                               bg=ACCENT, hover=ACCENT_HOVER, fg="#ffffff",
                               font=FONT_S, padx=12, pady=5)
        self.cm_btn.pack(side="right", padx=(8, 12), pady=(9, 5))

        # 动作行：游戏用英文 / 代码模式设置
        acts = tk.Frame(row, bg=PANEL)
        acts.pack(fill="x", padx=(58, 12), pady=(0, 11))
        RoundBtn(acts, "切换英文输入（游戏）", self._game_english, font=FONT_S,
                 padx=12, pady=4).pack(side="left")
        RoundBtn(acts, "设置代码模式", self._open_cm_settings, font=FONT_S,
                 padx=12, pady=4).pack(side="left", padx=(8, 0))
        self.kb_hint = tk.Label(acts, text="", bg=PANEL, fg=FG_DIM,
                                font=FONT_S, anchor="w")
        self.kb_hint.pack(side="left", padx=(10, 0), fill="x", expand=True)
        self._sync_cm()

    def _game_english(self):
        """把系统默认输入法切到英语（美国）——游戏里打字不被中文输入法接管。"""
        self.app.set_english_layout()
        self.kb_hint.configure(text="已切到英语（美国）· Win+空格 切回")

    def _open_cm_settings(self):
        self.app.open_code_mode()

    def _cm_enabled(self) -> bool:
        return bool((self.app.config.settings.get("code_mode") or {})
                    .get("enabled"))

    def _sync_cm(self):
        on = self._cm_enabled()
        self.cm_btn.set_text("关闭代码模式" if on else "开启代码模式")
        self.cm_btn.configure(bg=INPUT if on else ACCENT,
                              hover=INPUT_HOVER if on else ACCENT_HOVER)

    def _toggle_cm(self):
        self.app.on_toggle_code_mode()
        self._sync_cm()


class LinkAddWindow(_ToplevelBase):
    """粘贴官网产品页链接 → 解析型号 → 匹配协议 → 添加。"""

    def __init__(self, app):
        super().__init__(app, "粘贴官网链接添加设备", 540, 430)
        tk.Label(self, text="粘贴设备官网产品页链接（如 Sony / 华为 / 小米商城的商品页）",
                 bg=BG, fg=FG_DIM, font=FONT_S).pack(anchor="w", padx=16,
                                                     pady=(14, 6))
        url_fr = tk.Frame(self, bg=BG)
        url_fr.pack(fill="x", padx=16)
        self.url_var = tk.StringVar()
        _entry(url_fr, self.url_var).pack(side="left", fill="x", expand=True,
                                          ipady=6, padx=(0, 8))
        self.parse_btn = RoundBtn(url_fr, "解析", self._parse, bg=ACCENT,
                                  hover=ACCENT_HOVER, fg="#ffffff",
                                  font=FONT_B, padx=16, pady=6)
        self.parse_btn.pack(side="right")

        self.result = tk.Text(self, bg=PANEL, fg=FG, relief="flat", font=FONT_S,
                              height=8, wrap="word", highlightthickness=1,
                              highlightbackground=BORDER,
                              highlightcolor=BORDER, state="disabled",
                              padx=10, pady=8)
        self.result.pack(fill="both", expand=True, padx=16, pady=10)

        name_fr = self.row(self, "设备名称", 8)
        self.name_var = tk.StringVar()
        _entry(name_fr, self.name_var).pack(side="left", fill="x",
                                            expand=True, ipady=4)
        type_fr = self.row(self, "设备类型", 8)
        self.type_var = tk.StringVar(value="自动检测")
        ttk.Combobox(type_fr, textvariable=self.type_var, state="readonly",
                     values=type_choices(), font=FONT_S).pack(
            side="left", fill="x", expand=True)
        proto_fr = self.row(self, "电量协议", 8)
        self.proto_var = tk.StringVar()
        protos = protocols.list_protocols()
        self._proto_map = {p["name"]: p["id"] for p in protos}
        cb = ttk.Combobox(proto_fr, textvariable=self.proto_var, state="readonly",
                          values=[p["name"] for p in protos], font=FONT_S)
        cb.current(0)
        cb.pack(side="left", fill="x", expand=True)

        RoundBtn(self, "确认添加", self._add, bg=ACCENT, hover=ACCENT_HOVER,
                 fg="#ffffff", font=FONT_B, padx=20, pady=6).pack(
            anchor="e", padx=16, pady=(2, 14))
        self._resolved: dict | None = None

    def _parse(self):
        url = self.url_var.get().strip()
        if not url.startswith(("http://", "https://")):
            self._show("请输入以 http(s):// 开头的链接。")
            return
        self.parse_btn.configure(state="disabled")
        self._show("正在抓取并解析页面…")

        def work():
            res = protocols.resolve_link(url)
            self.app._emit("link_result", self, res)

        threading.Thread(target=work, daemon=True).start()

    def on_link_result(self, res: dict):
        self.parse_btn.configure(state="normal")
        self._resolved = res
        proto = protocols.protocol_name(res.get("protocol", "gatt_battery"))
        self.proto_var.set(proto)
        title = res.get("title") or res.get("model") or ""
        if not self.name_var.get():
            self.name_var.set(title[:40] or "新设备")
        lines = [f"页面标题：{title or '（未能提取）'}"]
        if res.get("site"):
            lines.append(f"来源站点：{res['site']}")
        if res.get("model"):
            lines.append(f"匹配型号：{res['brand']} {res['model']}")
        lines.append(f"电量协议：{proto}")
        if res.get("note"):
            lines.append(f"说明：{res['note']}")
        if res.get("error"):
            lines.append(f"错误：{res['error']}")
        lines.append("提示：添加后，设备连接上电脑时即可读取电量。")
        self._show("\n".join(lines))

    def _show(self, text):
        self.result.configure(state="normal")
        self.result.delete("1.0", "end")
        self.result.insert("1.0", text)
        self.result.configure(state="disabled")

    def _add(self):
        name = self.name_var.get().strip() or "新设备"
        proto = self._proto_map.get(self.proto_var.get(), "gatt_battery")
        tkey = type_key_from_choice(self.type_var.get()) \
            or guess_device_type(f"{name} {self.url_var.get()}")
        link = self.url_var.get().strip()
        model = ""
        if self._resolved:
            model = f"{self._resolved.get('brand', '')} {self._resolved.get('model', '')}".strip()
        dev = self.app.add_device(name=name, link=link, model=model,
                                  protocol=proto, dev_type=tkey)
        self.app.refresh_all_async()
        self.destroy()
        self.app.open_detail(dev["id"])


class DetailWindow(_ToplevelBase):
    def __init__(self, app, dev_id):
        super().__init__(app, "设备详情", 520, 680)
        self.dev_id = dev_id
        self._client: dict = {}
        self._probe: tuple[dict, str] | None = None
        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both", expand=True)
        app.windows[f"detail:{dev_id}"] = self
        self.render()

    def destroy(self):
        try:
            self.app.windows.pop(f"detail:{self.dev_id}", None)
        except Exception:
            pass
        super().destroy()

    # ------------------------------------------------------------------ #
    def on_client_checked(self, info: dict):
        self._client = info or {}
        self.render()

    def on_probe_done(self, report: dict, path: str):
        self._probe = (report, path)
        self.render()

    def _btn(self, parent, text, cmd, accent=False, danger=False):
        bg = ACCENT if accent else (DANGER if danger else INPUT)
        hv = ACCENT_HOVER if accent else (DANGER_HOVER if danger else INPUT_HOVER)
        fg = "#ffffff" if (accent or danger) else FG
        b = RoundBtn(parent, text, cmd, bg=bg, hover=hv, fg=fg, font=FONT_S,
                     padx=10, pady=5, radius=6)
        b.pack(side="left", padx=(0, 6), pady=2)
        return b

    def render(self):
        for w in self.body.winfo_children():
            w.destroy()
        self._capturing = False
        self._key_bind = None
        dev = self.app.config.find(self.dev_id)
        if not dev:
            tk.Label(self.body, text="设备已删除", bg=BG, fg=FG_DIM).pack(pady=40)
            return
        st = self.app.status.get(self.dev_id, {})
        battery = st.get("battery")
        precision = st.get("precision") or "none"
        color = battery_hex(battery)
        if battery is None:
            pct = "未知"
        elif precision == "exact":
            pct = f"{battery}%"
        else:
            pct = f"≈{battery}%"

        top = tk.Frame(self.body, bg=BG)
        top.pack(fill="x", padx=18, pady=(16, 2))
        tk.Label(top, text=pct, font=FONT_BIG, bg=BG, fg=color).pack(side="left")
        conn = tk.Canvas(top, width=14, height=14, bg=BG, highlightthickness=0)
        conn.pack(side="right", anchor="s", pady=12)
        conn.create_oval(1, 1, 13, 13, fill=OK if st.get("connected") else "#4b5563",
                         outline="")
        tk.Label(top, text="已连接" if st.get("connected") else "未连接",
                 bg=BG, fg=FG_DIM, font=FONT).pack(side="right", anchor="s",
                                                   padx=(0, 4), pady=8)

        tkey = dev.get("dev_type") or guess_device_type(dev.get("name", ""))
        nrow = tk.Frame(self.body, bg=BG)
        nrow.pack(fill="x", padx=18)
        img = self.app.main.type_photo(tkey, 44)
        self._type_img = img  # 防 GC
        if img is not None:
            tk.Label(nrow, image=img, bg=BG).pack(side="left", padx=(0, 8))
        tk.Label(nrow, text=dev["name"],
                 font=("Microsoft YaHei UI", 13, "bold"), bg=BG, fg=FG,
                 anchor="w", wraplength=400).pack(side="left", fill="x",
                                                  expand=True)
        info = ["最近更新：" + str(st.get("updated", "--"))]
        _tp = (dev.get("transport") or "").lower()
        if _tp == "2.4g":
            info.append("连接方式：2.4G 无线（USB 接收器）")
        elif _tp == "usb":
            info.append("连接方式：USB 有线")
        if dev.get("address") is not None:
            info.append(f"蓝牙地址：{dev['address']:012X}")
        if dev.get("model"):
            info.insert(0, f"型号：{dev['model']}")
        tk.Label(self.body, text="\n".join(info), bg=BG, fg=FG_DIM, font=FONT_S,
                 justify="left", anchor="w").pack(fill="x", padx=18, pady=(4, 2))

        # 设备类型
        trow = tk.Frame(self.body, bg=BG)
        trow.pack(fill="x", padx=18, pady=(2, 4))
        tk.Label(trow, text="设备类型", bg=BG, fg=FG_DIM, font=FONT_S).pack(
            side="left", padx=(0, 8))
        self.type_var = tk.StringVar(value=device_type_name(tkey))
        cb = ttk.Combobox(trow, textvariable=self.type_var, state="readonly",
                          values=[device_type_name(k) for k in DEVICE_TYPES],
                          font=FONT_S, width=20)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", self._on_type_change)

        # 托盘外观（按设备分别设置）
        TRAY_STYLE_NAMES = {"square": "圆角方块 — 彩色底 + 白色数字",
                            "pill": "电池 — 轮廓 + 电量条按比例填充",
                            "number": "纯数字 — 彩色粗体数字加描边"}
        NAME_TO_TRAY_STYLE = {v: k for k, v in TRAY_STYLE_NAMES.items()}
        srow = tk.Frame(self.body, bg=BG)
        srow.pack(fill="x", padx=18, pady=(2, 4))
        tk.Label(srow, text="托盘外观", bg=BG, fg=FG_DIM, font=FONT_S).pack(
            side="left", padx=(0, 8))
        cur = dev.get("tray_style") or ""
        self.tray_style_var = tk.StringVar(
            value=TRAY_STYLE_NAMES.get(cur, "跟随全局设置"))
        scb = ttk.Combobox(srow, textvariable=self.tray_style_var,
                           state="readonly",
                           values=["跟随全局设置"] + list(TRAY_STYLE_NAMES.values()),
                           font=FONT_S, width=26)
        scb.pack(side="left")
        scb.bind("<<ComboboxSelected>>", lambda _e: self.app.on_set_tray_style(
            self.dev_id, NAME_TO_TRAY_STYLE.get(self.tray_style_var.get(), "")))

        # 连接方式：蓝牙 / 2.4G 无线接收器 / USB 有线（自动判断失误时可手动改）
        TRANSPORT_NAMES = {"": "自动判断", "bt": "蓝牙", "2.4g": "2.4G 无线接收器",
                           "usb": "USB 有线"}
        NAME_TO_TRANSPORT = {v: k for k, v in TRANSPORT_NAMES.items()}
        tprow = tk.Frame(self.body, bg=BG)
        tprow.pack(fill="x", padx=18, pady=(2, 4))
        tk.Label(tprow, text="连接方式", bg=BG, fg=FG_DIM, font=FONT_S).pack(
            side="left", padx=(0, 8))
        self.transport_var = tk.StringVar(
            value=TRANSPORT_NAMES.get((dev.get("transport") or "").lower(),
                                      "自动判断"))
        tcb = ttk.Combobox(tprow, textvariable=self.transport_var,
                           state="readonly", values=list(TRANSPORT_NAMES.values()),
                           font=FONT_S, width=18)
        tcb.pack(side="left")
        tcb.bind("<<ComboboxSelected>>", lambda _e: self.app.on_set_transport(
            self.dev_id, NAME_TO_TRANSPORT.get(self.transport_var.get(), "")))
        if dev.get("vid"):
            tk.Label(tprow, text=f"VID:PID {dev['vid']:04X}:{dev.get('pid', 0):04X}",
                     bg=BG, fg=FG_DIM, font=FONT_S).pack(side="left", padx=(10, 0))

        # 键盘设备：切换中/英输入法的全局快捷键（按键自己录制）
        if tkey == "keyboard":
            hkrow = tk.Frame(self.body, bg=BG)
            hkrow.pack(fill="x", padx=18, pady=(2, 4))
            tk.Label(hkrow, text="切换快捷键", bg=BG, fg=FG_DIM,
                     font=FONT_S).pack(side="left", padx=(0, 8))
            spec = hotkeys.normalize(dev.get("hotkey"))
            self._capturing = False
            self._key_bind = None
            self.hk_btn = RoundBtn(
                hkrow, spec["name"] if spec else "点击后按键设置",
                self._capture_hotkey, bg=INPUT, hover=INPUT_HOVER, fg=FG,
                font=FONT_S, padx=10, pady=4, radius=6)
            self.hk_btn.pack(side="left")
            RoundBtn(hkrow, "清除", self._clear_hotkey, bg=INPUT,
                     hover=INPUT_HOVER, fg=FG_DIM, font=FONT_S, padx=8, pady=4,
                     radius=6).pack(side="left", padx=(8, 0))
            self.hk_hint = tk.Label(
                hkrow, text="（按下在 中/英 之间切换，支持任意组合键）",
                bg=BG, fg=FG_DIM, font=FONT_S)
            self.hk_hint.pack(side="left", padx=(8, 0))

        # ---------------- 电量来源与精度 ---------------- #
        card = Card(self.body, pady=12)
        card.pack(fill="x", padx=18, pady=4)
        p = card.inner
        tk.Label(p, text="⚡ 电量来源与精度", font=FONT_B, bg=PANEL,
                 fg=FG).pack(anchor="w")
        source = st.get("source") or "—"
        prec_txt = {"exact": "精确（厂商协议 / 标准 GATT）",
                    "coarse": "≈ 系统粗略值（HFP，Windows 设置页用的就是这条，可能滞后）",
                    "none": "不可用"}.get(precision, "不可用")
        pc = {"exact": OK, "coarse": "#d29922"}.get(precision, FG_DIM)
        tk.Label(p, text=f"通道：{source}", bg=PANEL, fg=FG, font=FONT_S,
                 anchor="w", wraplength=440, justify="left").pack(
            fill="x", pady=(6, 0))
        tk.Label(p, text=f"精度：{prec_txt}", bg=PANEL, fg=pc, font=FONT_S,
                 anchor="w", wraplength=440, justify="left").pack(fill="x")
        if st.get("detail"):
            tk.Label(p, text=st["detail"], bg=PANEL, fg=FG_DIM, font=FONT_S,
                     anchor="w", wraplength=440, justify="left").pack(fill="x")

        c = self._client or st.get("client") or {}
        # 只有厂商在 Windows 上确实有官方客户端时才显示 APP 相关选项
        if c.get("key") and c.get("has_pc_app"):
            n = len(c.get("installed", []))
            state = f"已安装 {n} 项" if n else "未安装"
            if c.get("running"):
                state += "（运行中）"
            line = f"官方客户端：{c.get('name')} · {state}"
            if c.get("platform"):
                line += f" · 平台 {c['platform']}"
            tk.Label(p, text=line, bg=PANEL, fg=FG_DIM, font=FONT_S, anchor="w",
                     wraplength=440, justify="left").pack(fill="x", pady=(6, 0))
            btns = tk.Frame(p, bg=PANEL)
            btns.pack(fill="x", pady=(10, 0))
            self._btn(btns, "打开官方下载页",
                      lambda: self.app.on_open_vendor_download(self.dev_id))
            self._btn(btns, "重新检测客户端",
                      lambda: self.app.on_check_client(self.dev_id))
        btns2 = tk.Frame(p, bg=PANEL)
        btns2.pack(fill="x", pady=(2, 0))
        self._btn(btns2, "探测设备协议通道",
                  lambda: self.app.on_probe_device(self.dev_id))
        self._btn(btns2, "强制设备重报电量",
                  lambda: self.app.on_hfp_refresh(self.dev_id))

        if self._probe:
            report, path = self._probe
            lines = []
            for ch in report.get("channels", []):
                lines.append(f"{'✓' if ch.get('ok') else '×'} {ch['channel']}：{ch['detail']}")
            tk.Label(p, text="\n".join(lines)[:900], bg=PANEL, fg=FG_DIM,
                     font=FONT_TAG, anchor="w", justify="left",
                     wraplength=440).pack(fill="x", pady=(8, 0))
            tk.Label(p, text=f"报告已保存：{path}", bg=PANEL, fg="#6e7681",
                     font=FONT_TAG, anchor="w", wraplength=440).pack(fill="x")

        # ---------------- 托盘 / 提醒 ---------------- #
        opt_card = Card(self.body, pady=10)
        opt_card.pack(fill="x", padx=18, pady=8)
        o = opt_card.inner
        show_var = tk.BooleanVar(value=bool(dev.get("show_in_tray", True)))
        alert_var = tk.BooleanVar(value=bool(dev.get("low_battery_alert", True)))
        tk.Checkbutton(o, text="在系统托盘中显示电量", variable=show_var,
                       command=lambda: self.app.toggle_show(dev["id"]),
                       bg=PANEL, fg=FG, activebackground=PANEL,
                       selectcolor=INPUT, font=FONT_S).pack(anchor="w")
        tk.Checkbutton(o, text="低电量提醒（气泡通知）", variable=alert_var,
                       command=lambda: self.app.toggle_alert(dev["id"]),
                       bg=PANEL, fg=FG, activebackground=PANEL,
                       selectcolor=INPUT, font=FONT_S).pack(anchor="w")

        foot = tk.Frame(self.body, bg=BG)
        foot.pack(fill="x", padx=18, pady=10)
        # 按设备类型提供的快捷操作（单独一行，避免按钮挤在一排）
        extra = []
        if tkey in ("headset", "earbuds"):
            extra.append(("打开音量合成器", self.app.open_volume_mixer))
        if tkey == "keyboard":
            extra += [("键盘属性", self.app.open_keyboard_props),
                      ("切换英文输入（游戏）", self.app.set_english_layout),
                      ("代码模式设置", self.app.open_code_mode)]
        if tkey == "gamepad":
            extra.append(("游戏控制器校准", self.app.open_game_controllers))
        if extra:
            trow = tk.Frame(self.body, bg=BG)
            trow.pack(fill="x", padx=18, pady=(0, 2), before=foot)
            for text, cmd in extra:
                self._btn(trow, text, cmd)
        self._btn(foot, "手动刷新",
                  lambda: self.app.refresh_async([self.dev_id]))
        self._btn(foot, SYS_SETTINGS_LABEL.get(tkey, "打开蓝牙设置"),
                  lambda: self.app.on_open_sys_settings(self.dev_id))
        self._btn(foot, "删除设备（不影响系统连接）",
                  lambda: self.app.remove_device(self.dev_id), danger=True)

    def _on_type_change(self, _e=None):
        tkey = type_key_from_choice(self.type_var.get())
        if tkey:
            self.app.on_set_type(self.dev_id, tkey)

    def _on_hotkey_change(self, _e=None):
        name = self.hotkey_var.get()
        self.app.on_set_hotkey(self.dev_id, "" if name == "无" else name)

    # ---- 快捷键录制 ---------------------------------------------------- #
    def _capture_hotkey(self):
        if self._capturing:
            return
        self._capturing = True
        self.hk_btn.set_text("请按下组合键…")
        self.hk_hint.configure(text="按下后立即生效，Esc 取消", fg="#d29922")
        self.focus_force()
        self._key_bind = self.bind("<KeyPress>", self._on_key_capture)

    def _cancel_capture(self):
        self._capturing = False
        if self._key_bind is not None:
            try:
                self.unbind("<KeyPress>", self._key_bind)
            except Exception:
                pass
            self._key_bind = None
        dev = self.app.config.find(self.dev_id) or {}
        spec = hotkeys.normalize(dev.get("hotkey"))
        if getattr(self, "hk_btn", None) is not None:
            self.hk_btn.set_text(spec["name"] if spec else "点击后按键设置")
            self.hk_hint.configure(text="（按下在 中/英 之间切换，支持任意组合键）",
                                   fg=FG_DIM)

    def _on_key_capture(self, e):
        vk = int(getattr(e, "keycode", 0) or 0)
        if vk == 0x1B:                       # Esc 取消
            self._cancel_capture()
            return "break"
        if vk == 0 or hotkeys.is_modifier_key(vk):
            return "break"                   # 只按下修饰键，等真正的键
        mods = hotkeys.current_mods()
        # 单字母/数字键会被游戏抢占，要求配合修饰键；F1-F24 可单用
        if mods == 0 and not (0x70 <= vk <= 0x87):
            self.hk_hint.configure(
                text="单键容易被游戏占用，请配合 Ctrl / Alt / Shift 使用",
                fg="#d29922")
            return "break"
        name = hotkeys.display_name(mods, vk)
        self.app.on_set_hotkey(self.dev_id,
                               {"name": name, "mods": mods, "vk": vk})
        self._cancel_capture()               # 从配置读回并显示
        return "break"

    def _clear_hotkey(self):
        self.app.on_set_hotkey(self.dev_id, "")
        if getattr(self, "hk_btn", None) is not None:
            self.hk_btn.set_text("点击后按键设置")


class ClientPromptWindow(_ToplevelBase):
    """设备缺少精确电量协议时，引导安装/打开厂商客户端。"""

    def __init__(self, app, dev_id):
        super().__init__(app, "需要厂商协议", 500, 370)
        self.dev_id = dev_id
        dev = app.config.find(dev_id) or {}
        st = app.status.get(dev_id, {})
        c = st.get("client") or {}

        tk.Label(self, text="这台设备的电量只能从系统粗略通道读取",
                 bg=BG, fg=FG, font=("Microsoft YaHei UI", 12, "bold")).pack(
            anchor="w", padx=18, pady=(16, 2))
        tk.Label(self, text=dev.get("name", ""), bg=BG, fg="#4fc1ff",
                 font=FONT_B).pack(anchor="w", padx=18)

        msg = ["当前数值来自 HFP 上报（Windows 设置页用的同一条），可能不准或滞后。",
               "要拿到厂商精确电量，需要厂商自己的协议通道。"]
        if c.get("pc_note"):
            msg.append(c["pc_note"])
        if c.get("download_url"):
            msg.append("点击下方按钮打开官方下载/支持页，安装后再点「重新检测」。")
        tk.Label(self, text="\n".join(msg), bg=BG, fg=FG_DIM, font=FONT_S,
                 justify="left", anchor="w", wraplength=460).pack(
            fill="x", padx=18, pady=8)

        self.status = tk.Label(self, text="", bg=BG, fg="#d29922", font=FONT_S,
                               anchor="w", wraplength=460, justify="left")
        self.status.pack(fill="x", padx=18)

        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=18, pady=14)

        def btn(text, cmd, accent=False):
            RoundBtn(row, text, cmd,
                     bg=ACCENT if accent else INPUT,
                     hover=ACCENT_HOVER if accent else INPUT_HOVER,
                     fg="#ffffff" if accent else FG,
                     font=FONT_B if accent else FONT_S,
                     padx=12, pady=6, radius=6).pack(
                side="left" if accent else "left", padx=(0, 8))

        btn("打开官方下载页", lambda: app.on_open_vendor_download(dev_id), accent=True)
        btn("重新检测客户端", lambda: app.on_check_client(dev_id))
        btn("探测协议通道", lambda: app.on_probe_device(dev_id))
        RoundBtn(row, "知道了", self.destroy, fg=FG_DIM).pack(side="right")

    # app 检测完成后回调
    def on_client_checked(self, info: dict):
        if info.get("found"):
            self.status.configure(
                text=f"已检测到 {info.get('name')}（{len(info.get('installed', []))} 项），"
                     f"正在尝试从客户端取电量…", fg=OK)
        else:
            self.status.configure(
                text=f"仍未检测到 {info.get('name')}，可先安装或查看官方支持页。",
                fg="#d29922")


class SettingsWindow(_ToplevelBase):
    def __init__(self, app):
        super().__init__(app, "设置", 480, 672)
        s = app.config.settings
        tk.Label(self, text="低电量提醒阈值 (%)", bg=BG, fg=FG, font=FONT).pack(
            anchor="w", padx=18, pady=(16, 4))
        self.thresh = tk.StringVar(value=str(s.get("low_battery_threshold", 30)))
        tk.Spinbox(self, from_=5, to=90, textvariable=self.thresh, width=8,
                   bg=INPUT, fg=FG, relief="flat", insertbackground=FG,
                   highlightthickness=1, highlightbackground=BORDER,
                   highlightcolor=ACCENT_HOVER,
                   buttonbackground=INPUT_HOVER).pack(anchor="w", padx=18)

        tk.Label(self, text="自动刷新频率（秒）", bg=BG, fg=FG, font=FONT).pack(
            anchor="w", padx=18, pady=(12, 4))
        self.interval = tk.StringVar(value=str(s.get("refresh_interval_sec", 60)))
        tk.Spinbox(self, from_=15, to=3600, textvariable=self.interval, width=8,
                   bg=INPUT, fg=FG, relief="flat", insertbackground=FG,
                   highlightthickness=1, highlightbackground=BORDER,
                   highlightcolor=ACCENT_HOVER,
                   buttonbackground=INPUT_HOVER).pack(anchor="w", padx=18)

        # 主界面布局
        tk.Label(self, text="主界面布局", bg=BG, fg=FG, font=FONT).pack(
            anchor="w", padx=18, pady=(14, 4))
        self.view = tk.StringVar(value=s.get("main_view", "tile"))
        for val, text in (("tile", "磁贴 — 固定小卡片，从左到右一个个摆放"),
                          ("list", "列表 — 紧凑行，一屏显示更多设备")):
            tk.Radiobutton(self, text=text, variable=self.view,
                           value=val, bg=BG, fg=FG_DIM, activebackground=BG,
                           selectcolor=INPUT, font=FONT_S, anchor="w").pack(
                fill="x", padx=26)

        # 开机启动
        self.autostart = tk.BooleanVar(value=autostart.is_enabled())
        tk.Checkbutton(
            self, text="开机自动启动（静默，只驻留托盘不弹窗口）",
            variable=self.autostart, bg=BG, fg=FG, activebackground=BG,
            activeforeground=FG, selectcolor=INPUT, font=FONT_S, anchor="w",
            highlightthickness=0, bd=0).pack(fill="x", padx=18, pady=(14, 0))

        # 数据来源策略
        tk.Label(self, text="电量来源策略", bg=BG, fg=FG, font=FONT).pack(
            anchor="w", padx=18, pady=(14, 4))
        self.mode = tk.StringVar(
            value=s.get("battery_source_mode", "prefer_exact"))
        for val, text, hint in (
                ("prefer_exact", "精确优先（推荐）",
                 "有厂商/GATT 精确值时用它；否则显示系统粗略值并标注 ≈"),
                ("exact_only", "只显示精确值",
                 "拿不到精确值时显示 --，绝不显示系统那个可能不准的数字"),
                ("system_only", "只用系统值", "始终显示 HFP 上报值（可能与实际偏差）")):
            tk.Radiobutton(self, text=f"{text} — {hint}", variable=self.mode,
                           value=val, bg=BG, fg=FG_DIM, activebackground=BG,
                           selectcolor=INPUT, font=FONT_S, wraplength=430,
                           justify="left", anchor="w").pack(fill="x", padx=26)

        # 代码模式入口
        cmrow = tk.Frame(self, bg=BG)
        cmrow.pack(fill="x", padx=18, pady=(14, 0))
        tk.Label(cmrow, text="代码模式（自动补齐括号/引号 + 中英文切换）", bg=BG,
                 fg=FG, font=FONT).pack(anchor="w")
        RoundBtn(cmrow, "打开代码模式设置…", self.app.open_code_mode,
                 bg=INPUT, hover=INPUT_HOVER, font=FONT_S).pack(
            anchor="w", pady=(6, 0))

        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", padx=18, pady=16)
        RoundBtn(foot, f"关于 {VERSION}", self.app.open_about, bg=INPUT,
                 hover=INPUT_HOVER, font=FONT_S, padx=12,
                 pady=6).pack(side="left")
        RoundBtn(foot, "保存", self._save, bg=ACCENT, hover=ACCENT_HOVER,
                 fg="#ffffff", font=FONT_B, padx=20, pady=6).pack(side="right")

    def _save(self):
        try:
            self.app.config.settings["low_battery_threshold"] = max(5, min(90, int(self.thresh.get())))
            self.app.config.settings["refresh_interval_sec"] = max(15, min(3600, int(self.interval.get())))
        except ValueError:
            pass
        self.app.config.settings["battery_source_mode"] = self.mode.get()
        self.app.config.settings["main_view"] = self.view.get()
        # 开机启动：勾选则写启动项，取消则删除
        want = bool(self.autostart.get())
        ok, info = (autostart.enable() if want else autostart.disable())
        self.app.config.settings["autostart"] = autostart.is_enabled()
        self.app.config.save()
        if not ok:
            self.app.main.set_status(
                f"{'设置' if want else '取消'}开机启动失败：{info}")
        else:
            self.app.main.set_status(
                "已开启开机静默启动（启动项：%s）" % info if want
                else "已取消开机启动。")
        self.app.refresh_all_async()
        self.destroy()


# 让 Toplevel 挂在主窗口上
MainWindow.instance: "MainWindow | None" = None
