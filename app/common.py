"""通用常量与工具：电量配色、地址解析、图标绘制、设备类型。"""
from __future__ import annotations

import os
import re

from PIL import Image, ImageDraw, ImageFont

# 用户配色规则：>=80 绿色，>=50 黄色，>=30 黄色，其余红色
GREEN = (34, 197, 94)
YELLOW = (234, 179, 8)
RED = (239, 68, 68)
UNKNOWN = (100, 116, 139)

# --------------------------------------------------------------------------- #
# 设备类型
# --------------------------------------------------------------------------- #
DEVICE_TYPES = {
    "mouse":    ("鼠标", "Mouse"),
    "keyboard": ("键盘", "Keyboard"),
    "headset":  ("头戴式耳机", "Headset"),
    "earbuds":  ("入耳式耳机", "Earbuds"),
    "gamepad":  ("游戏手柄", "Gamepad"),
    "dongle":   ("接收器 / 拓展坞", "Dongle"),
    "speaker":  ("扬声器", "Speaker"),
    "generic":  ("通用外设", "Generic"),
}

# 各类型对应的 Windows 设置页
SYS_SETTINGS_URI = {
    "mouse":    "ms-settings:mousetouch",
    "keyboard": "ms-settings:keyboard",
    "headset":  "ms-settings:sound",
    "earbuds":  "ms-settings:sound",
    "gamepad":  "ms-settings:bluetooth",
    "dongle":   "ms-settings:bluetooth",
    "generic":  "ms-settings:bluetooth",
}

# 设置按钮文案
SYS_SETTINGS_LABEL = {
    "mouse":    "打开鼠标设置",
    "keyboard": "打开键盘设置",
    "headset":  "打开声音设置",
    "earbuds":  "打开声音设置",
    "gamepad":  "打开蓝牙设置",
    "dongle":   "打开蓝牙设置",
    "generic":  "打开蓝牙设置",
}


def device_type_name(tkey: str) -> str:
    zh, en = DEVICE_TYPES.get(tkey or "generic", DEVICE_TYPES["generic"])
    return f"{zh} ({en})"


def guess_device_type(name: str) -> str:
    """按设备名猜测类型（识别不出返回 generic）。"""
    n = (name or "").lower()
    rules = [
        (("mouse", "鼠标"), "mouse"),
        (("keyboard", "键盘"), "keyboard"),
        (("gamepad", "手柄", "controller", "joy"), "gamepad"),
        (("earbuds", "buds", "入耳", "tws"), "earbuds"),
        (("headset", "headphone", "耳机", "headset"), "headset"),
        (("dongle", "receiver", "接收器", "拓展坞", "dock", "hub"), "dongle"),
    ]
    for keys, t in rules:
        if any(k in n for k in keys):
            return t
    return "generic"


def _icon_canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def make_type_icon(tkey: str, size: int = 40,
                   color: tuple = (150, 160, 175, 255)) -> Image.Image:
    """绘制设备类型图标（256px 画布绘制后缩放，平滑边缘）。"""
    img, d = _icon_canvas()
    c = color
    w = 10  # 线宽
    t = tkey if tkey in DEVICE_TYPES else "generic"

    if t == "mouse":
        d.rounded_rectangle([78, 40, 178, 216], radius=50, outline=c, width=w)
        d.line([(128, 40), (128, 110)], fill=c, width=w)
        d.rounded_rectangle([118, 78, 138, 122], radius=10, fill=c)
    elif t == "keyboard":
        d.rounded_rectangle([36, 78, 220, 178], radius=16, outline=c, width=w)
        for r in range(2):
            for col in range(6):
                x = 58 + col * 26
                y = 100 + r * 32
                d.rounded_rectangle([x, y, x + 16, y + 16], radius=4, fill=c)
        d.rounded_rectangle([92, 148, 164, 160], radius=4, fill=c)
    elif t == "headset":
        d.arc([52, 44, 204, 200], start=180, end=360, fill=c, width=w)
        d.rounded_rectangle([44, 128, 84, 196], radius=14, fill=c)
        d.rounded_rectangle([172, 128, 212, 196], radius=14, fill=c)
        d.rounded_rectangle([52, 150, 76, 190], radius=8, fill=(0, 0, 0, 0))
    elif t == "earbuds":
        for cx in (88, 168):
            d.ellipse([cx - 34, 56, cx + 34, 150], fill=c)
            d.rounded_rectangle([cx - 13, 130, cx + 13, 204], radius=13, fill=c)
            d.ellipse([cx - 16, 84, cx + 8, 116], fill=(30, 30, 30, 255))
    elif t == "gamepad":
        d.rounded_rectangle([36, 88, 220, 180], radius=44, fill=c)
        # 十字键
        d.rounded_rectangle([72, 112, 88, 156], radius=4, fill=(30, 30, 30, 255))
        d.rounded_rectangle([60, 124, 100, 144], radius=4, fill=(30, 30, 30, 255))
        # 按键
        d.ellipse([168, 112, 186, 130], fill=(30, 30, 30, 255))
        d.ellipse([186, 134, 204, 152], fill=(30, 30, 30, 255))
    elif t == "dongle":
        d.rounded_rectangle([92, 96, 164, 216], radius=12, outline=c, width=w)
        d.rounded_rectangle([104, 40, 152, 96], radius=4, fill=c)
        d.line([(112, 130), (144, 130)], fill=c, width=w)
        d.line([(112, 160), (144, 160)], fill=c, width=w)
    elif t == "speaker":
        d.rounded_rectangle([86, 106, 124, 174], radius=10, fill=c)
        d.polygon([(120, 118), (120, 162), (166, 194), (166, 86)], fill=c)
        d.arc([174, 100, 212, 180], start=-55, end=55, fill=c, width=w)
        d.arc([190, 82, 240, 198], start=-55, end=55, fill=c, width=w)
    else:  # generic：芯片
        d.rounded_rectangle([76, 76, 180, 180], radius=12, outline=c, width=w)
        for i in range(3):
            p = 100 + i * 30
            d.line([(p, 76), (p, 48)], fill=c, width=w)
            d.line([(p, 180), (p, 208)], fill=c, width=w)
            d.line([(76, p), (48, p)], fill=c, width=w)
            d.line([(180, p), (208, p)], fill=c, width=w)

    return img.resize((size, size), Image.LANCZOS)


def battery_color(pct) -> tuple:
    """按规则返回 (R,G,B)。pct 为 None 返回灰色。"""
    if pct is None:
        return UNKNOWN
    if pct >= 80:
        return GREEN
    if pct >= 50:
        return YELLOW
    if pct >= 30:
        return YELLOW
    return RED


def battery_hex(pct) -> str:
    r, g, b = battery_color(pct)
    return f"#{r:02x}{g:02x}{b:02x}"


def parse_bt_address(dev_id: str):
    """从 Windows AEP 设备 Id 中解析蓝牙 MAC 地址（返回 int，失败返回 None）。"""
    m = re.search(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", dev_id or "")
    if not m:
        return None
    return int(m.group(0).replace(":", ""), 16)


def _load_font(size: int, bold: bool = True):
    if bold:
        names = ("segoeuib.ttf", "arialbd.ttf", "arial.ttf")
    else:
        names = ("segoeui.ttf", "segoeuisl.ttf", "arial.ttf")
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def make_device_icon(pct, coarse: bool = False, style: str = "square") -> Image.Image:
    """设备托盘图标。

    style:
      - "square": 彩色圆角方块 + 白色数字（默认）
      - "pill":   电池形状 — 机身轮廓 + 电量条按比例填充，含 % 号
      - "number": 透明底彩色粗体数字（描边保证可读）
    coarse=True 表示数据来自系统粗略通道（HFP），加 "≈" 区分。
    """
    text = "?" if pct is None else str(pct)
    if coarse and pct is not None:
        text = "≈" + text

    if style == "pill":
        # 电池形状：深色机身 + 右侧正极凸头，电量条按实际比例动态填充，
        # 细体小号数字居中（参照 Windows 任务栏电池样式）
        W, H = 96, 48
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        edge = (176, 180, 188, 255)
        d.rounded_rectangle([1, 8, 76, 40], radius=8,
                            outline=edge, width=3, fill=(22, 24, 28, 255))
        d.rounded_rectangle([79, 19, 91, 29], radius=3, fill=edge)
        if pct is not None:
            pct = max(0, min(100, pct))
            x0, x1 = 5, 73
            fw = round((x1 - x0) * pct / 100)
            if fw >= 3:
                d.rounded_rectangle([x0, 12, x0 + fw, 36], radius=4,
                                    fill=battery_color(pct))
        font = _load_font(19 if len(text) <= 3 else 15, bold=False)
        bbox = d.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text(((77 - tw) / 2 - bbox[0], (48 - th) / 2 - bbox[1]), text,
               font=font, fill=(245, 246, 248, 255))
        return img

    if style == "number":
        W = H = 64
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        font = _load_font(34 if len(text) <= 3 else 26)
        bbox = d.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = (W - tw) / 2 - bbox[0]
        y = (H - th) / 2 - bbox[1]
        d.text((x, y), text, font=font, fill=battery_color(pct),
               stroke_width=3, stroke_fill=(20, 22, 26, 230))
        return img

    # square（默认）
    W = H = 64
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, W - 2, H - 2], radius=14, fill=battery_color(pct))
    size = 30 if len(text) <= 2 else 24
    font = _load_font(size)
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((W - tw) / 2 - bbox[0], (H - th) / 2 - bbox[1]), text,
           font=font, fill=(255, 255, 255, 255))
    return img


def make_master_icon() -> Image.Image:
    """总控托盘图标：统一 AppLogo（assets/logo.png）。"""
    return logo_image(64)


def _res_base() -> str:
    """资源根目录：PyInstaller 打包后取 _MEIPASS，源码运行取项目根。"""
    import sys
    base = getattr(sys, "_MEIPASS", "")
    if base and os.path.isdir(os.path.join(base, "assets")):
        return base
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_LOGO_CACHE: dict[int, Image.Image] = {}
_LOGO_RAW: Image.Image | None = None


def _placeholder_logo() -> Image.Image:
    """默认应用图标（代码绘制，可随开源仓库分发）：橙底电池扳手。
    如需自定义品牌，把 logo.png 放回 assets/ 即可自动启用。"""
    base = 512
    img = Image.new("RGBA", (base, base), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = int(base * 0.03)
    d.rounded_rectangle([m, m, base - m, base - m], radius=110,
                        fill=(234, 122, 30, 255))
    # 电池机身
    d.rounded_rectangle([96, 200, 346, 312], radius=24,
                        outline=(255, 255, 255, 255), width=16)
    d.rounded_rectangle([346, 232, 396, 280], radius=10,
                        fill=(255, 255, 255, 255))
    d.rounded_rectangle([118, 222, 232, 290], radius=12,
                        fill=(255, 255, 255, 255))
    # 扳手
    d.ellipse([300, 90, 420, 210], outline=(255, 255, 255, 255), width=16)
    d.line([(340, 190), (400, 250)], fill=(255, 255, 255, 255), width=18)
    return img


def _logo_raw() -> Image.Image:
    """AppLogo 加载：优先内嵌 base64（app/_branding_data.py，gitignore），
    其次 assets/logo.png 文件，都没有则用占位图标（开源构建可用）。"""
    global _LOGO_RAW
    if _LOGO_RAW is None:
        raw = None
        try:  # 内嵌数据（私有，不进仓库、不落盘）
            from app import _branding_data  # type: ignore
            import base64
            import io
            raw = Image.open(io.BytesIO(
                base64.b64decode(_branding_data.LOGO_PNG_B64))).convert("RGBA")
        except Exception:
            pass
        if raw is None:  # 本地文件（开发者自备，gitignore）
            p = os.path.join(_res_base(), "assets", "logo.png")
            try:
                raw = Image.open(p).convert("RGBA")
            except Exception:
                raw = None
        if raw is None:
            _LOGO_RAW = _placeholder_logo()
            return _LOGO_RAW
        bbox = raw.getbbox()
        if bbox:
            raw = raw.crop(bbox)
        # 统一缩到 512 的方形画布，内容占比 94%（16px 小图也能看清主体）
        base = 512
        inner = int(base * 0.94)
        scale = min(inner / raw.width, inner / raw.height)
        raw = raw.resize((max(1, round(raw.width * scale)),
                          max(1, round(raw.height * scale))), Image.LANCZOS)
        canvas = Image.new("RGBA", (base, base), (0, 0, 0, 0))
        canvas.paste(raw, ((base - raw.width) // 2, (base - raw.height) // 2),
                     raw)
        _LOGO_RAW = canvas
    return _LOGO_RAW


def logo_image(size: int) -> Image.Image:
    """统一 AppLogo：assets/logo.png（自动裁透明边），按需缩放缓存。"""
    if size in _LOGO_CACHE:
        return _LOGO_CACHE[size]
    img = _logo_raw()
    if size and size != img.width:
        img = img.resize((size, size), Image.LANCZOS)
    _LOGO_CACHE[size] = img
    return img


def make_app_icon() -> Image.Image:
    """应用图标：统一 AppLogo（供标题栏 / 关于窗口缩放使用）。"""
    return logo_image(256)


def app_ico_path() -> str:
    """生成/复用应用 .ico，供窗口与任务栏使用。
    缓存文件名带内容指纹，品牌图变更后自动重建；并清理旧版缓存。"""
    import base64
    import hashlib

    base = os.path.join(os.environ.get("LOCALAPPDATA", "."), "BatteryTray")
    os.makedirs(base, exist_ok=True)
    # 清理旧版缓存（历史遗留的 logo.ico 可能是旧品牌图）
    legacy = os.path.join(base, "logo.ico")
    if os.path.exists(legacy):
        try:
            os.remove(legacy)
        except Exception:
            pass
    raw = logo_image(0)
    digest = hashlib.md5(raw.tobytes()).hexdigest()[:10]
    p = os.path.join(base, f"app-{digest}.ico")
    if not os.path.exists(p):
        raw.save(p, format="ICO", sizes=[
            (16, 16), (20, 20), (24, 24), (32, 32), (40, 40),
            (48, 48), (64, 64), (128, 128), (256, 256)])
    return p
