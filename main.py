"""蓝牙电量管家 —— 入口。

- 每台蓝牙设备的电量以「彩色背景数字」显示在系统托盘：
  >=80 绿色，>=50 黄色，>=30 黄色，其余红色（配色规则见 app/common.py）。
- 总控托盘：左键打开主面板，右键可设置/刷新/退出。
- 设备托盘：左键打开详情页，右键低电量提醒/手动刷新/隐藏/删除。
"""
import socket
import sys

_SINGLETON_PORT = 52341


def _acquire_singleton() -> socket.socket | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", _SINGLETON_PORT))
        return s
    except OSError:
        pass
    # bind 失败可能是上一个实例刚退出（TIME_WAIT）。确认是否真有活实例：
    # connect 得上才算在运行，否则强制重绑。
    try:
        s.connect(("127.0.0.1", _SINGLETON_PORT))
        s.close()
        return None
    except OSError:
        s.close()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", _SINGLETON_PORT))
            return s
        except OSError:
            return None


def main():
    # --windowed 打包后没有控制台，sys.stdout/stderr 为 None，
    # 任何 print 都会炸。统一重定向到空设备。
    import os
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    try:
        _main()
    except Exception:
        import traceback
        try:
            base = os.path.join(os.environ.get("LOCALAPPDATA", "."),
                                "BatteryTray")
            os.makedirs(base, exist_ok=True)
            with open(os.path.join(base, "crash.log"), "a",
                      encoding="utf-8") as f:
                import time
                f.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                traceback.print_exc(file=f)
        except Exception:
            pass
        raise


def _main():
    lock = _acquire_singleton()
    if lock is None:
        print("蓝牙电量管家已在运行（请查看系统托盘）。")
        sys.exit(0)
    # 独立任务栏身份：让任务栏/分组显示应用自己的图标，而不是 pythonw 的
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "BatteryTray.App")
    except Exception:
        pass
    from app.app import BatteryApp
    app = BatteryApp(silent="--silent" in sys.argv)
    app.run()


if __name__ == "__main__":
    main()
