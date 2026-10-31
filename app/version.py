"""应用元信息（关于界面 / 版本标识统一从这里取）。"""

APP_NAME = "硬橙控"
APP_NAME_EN = "HardOrwareCtrl"
VERSION = "Alpha-0.1.0"
CHANNEL = "Alpha"
RELEASE_NOTE = "第一个版本——梦开始的地方"
AUTHOR = "GanCheng1145"
GITHUB_URL = "https://github.com/GanCheng-Studio"
GITHUB_LABEL = "GanCheng-Studio"


def version_line() -> str:
    return f"{APP_NAME} {VERSION}"
