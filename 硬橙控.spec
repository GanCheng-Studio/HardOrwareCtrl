# -*- mode: python ; coding: utf-8 -*-
import base64
import io
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['pystray._win32']
hiddenimports += collect_submodules('winrt')

# ---------------------------------------------------------------------------
# 品牌图标：不以图片文件形式存在于仓库。
# - 本机构建：图标数据内嵌于 app/_branding_data.py（gitignored），
#   构建期在内存中生成 branding.dll（Win32 资源 DLL）供 exe 嵌入图标。
# - 开源构建：无 _branding_data.py 时回退到代码绘制的默认图标。
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(SPECPATH)
sys.path.insert(0, SPECPATH)
import brandlib  # noqa: E402

from PIL import Image, ImageDraw  # noqa: E402

WORK = os.path.join(SPECPATH, '_work')
os.makedirs(WORK, exist_ok=True)


def _canvas_logo():
    """返回统一的应用图标图像（裁边 -> 512 画布），逻辑同 app/common.py。"""
    bd = os.path.join(ROOT, 'app', '_branding_data.py')
    raw = None
    if os.path.exists(bd):
        ns = {}
        with open(bd, encoding='utf-8') as f:
            exec(compile(f.read(), bd, 'exec'), ns)
        raw = Image.open(io.BytesIO(
            base64.b64decode(ns['LOGO_PNG_B64']))).convert('RGBA')
        bbox = raw.getbbox()
        if bbox:
            raw = raw.crop(bbox)
    else:
        # 开源默认图标：橙底电池扳手（与 common._placeholder_logo 一致）
        base = 512
        img = Image.new('RGBA', (base, base), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        m = int(base * 0.03)
        d.rounded_rectangle([m, m, base - m, base - m], radius=110,
                            fill=(234, 122, 30, 255))
        d.rounded_rectangle([96, 200, 346, 312], radius=24,
                            outline=(255, 255, 255, 255), width=16)
        d.rounded_rectangle([346, 232, 396, 280], radius=10,
                            fill=(255, 255, 255, 255))
        d.rounded_rectangle([118, 222, 232, 290], radius=12,
                            fill=(255, 255, 255, 255))
        d.ellipse([300, 90, 420, 210], outline=(255, 255, 255, 255), width=16)
        d.line([(340, 190), (400, 250)], fill=(255, 255, 255, 255), width=18)
        raw = img

    base = 512
    inner = int(base * 0.94)
    scale = min(inner / raw.width, inner / raw.height)
    raw = raw.resize((max(1, round(raw.width * scale)),
                      max(1, round(raw.height * scale))), Image.LANCZOS)
    canvas = Image.new('RGBA', (base, base), (0, 0, 0, 0))
    canvas.paste(raw, ((base - raw.width) // 2, (base - raw.height) // 2),
                 raw)
    return canvas


_tmp_ico = os.path.join(WORK, 'branding.ico')
_canvas_logo().save(_tmp_ico, format='ICO', sizes=[
    (16, 16), (20, 20), (24, 24), (32, 32), (40, 40),
    (48, 48), (64, 64), (128, 128), (256, 256)])
# 品牌图标封装为 Win32 资源 DLL（非图片文件；构建产物，gitignored）
_app_ico_dll = brandlib.make_resource_dll(
    open(_tmp_ico, 'rb').read(), os.path.join(WORK, 'branding.dll'))
# PyInstaller 仅接受 .ico/.exe 作为图标源（不支持从 dll 嵌入），
# 故 exe 图标用构建期临时 ico（仅存在于 build/_work，不进项目源码树）
_app_ico = _tmp_ico

a = Analysis(
    ['../main.py'],
    pathex=[],
    binaries=[],
    datas=[],  # Logo 不以文件形式打包：内嵌于 app/_branding_data.py
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='硬橙控',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[_app_ico],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='硬橙控',
)
