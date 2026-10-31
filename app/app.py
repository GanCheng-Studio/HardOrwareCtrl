"""应用编排：配置、蓝牙 worker、托盘、GUI、定时刷新、事件队列。"""
from __future__ import annotations

import queue
import threading
import time

from . import clients, hid2g, native, protocols, vendor_protocols
from . import common
from .battery import BluetoothWorker
from .codemode import CodeModeEngine
from .config import Config
from .hotkeys import HotkeyManager
from .tray import TrayManager
from .ui import (MainWindow, ScanWindow, LinkAddWindow, DetailWindow,
                 SettingsWindow, ClientPromptWindow)


class BatteryApp:
    def __init__(self, silent: bool = False):
        self.config = Config()
        self.worker = BluetoothWorker()
        self.tray = TrayManager(self._emit)
        self.hotkeys = HotkeyManager(
            lambda dev_id: self._emit("hotkey_toggle", dev_id))
        self.codemode = CodeModeEngine(self)
        self.ui_queue: queue.Queue = queue.Queue()
        self.status: dict[str, dict] = {}   # dev_id -> battery/connected/error/updated
        self._alerted: dict[str, int] = {}  # dev_id -> 上次提醒时的电量档
        self.windows: dict[str, object] = {}

        MainWindow.instance = MainWindow(self, silent=silent)
        self.main = MainWindow.instance
        self.tray.start(self.config.devices)
        self.hotkeys.start()
        self._sync_hotkeys()
        self.codemode.apply_settings()
        self._emit("render")
        self._poll_queue()
        self._schedule_refresh(initial=True)

    # ------------------------------------------------------------------ #
    def run(self):
        self.main.mainloop()

    def _emit(self, event: str, *args):
        """任何线程均可投递事件到 UI 队列。"""
        self.ui_queue.put((event, args))

    def _poll_queue(self):
        while True:
            try:
                event, args = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                handler = getattr(self, f"on_{event}", None)
                if handler:
                    handler(*args)
            except Exception as e:  # noqa: BLE001
                self.main.set_status(f"事件处理出错: {e}")
        self.main.after(150, self._poll_queue)

    # ------------------------------------------------------------------ #
    # 刷新
    # ------------------------------------------------------------------ #
    def _schedule_refresh(self, initial=False):
        interval = max(15, int(self.config.settings.get("refresh_interval_sec", 60))) * 1000
        self.main.after(500 if initial else interval, self.refresh_all_async)

    def refresh_all_async(self):
        self.refresh_async()

    def refresh_async(self, ids=None):
        """刷新全部设备；传 ids 时只刷新指定设备（批量刷新 / 单设备刷新）。"""
        only = None if ids is None else {i for i in ids}

        def work():
            try:
                res = self._refresh_once(only)
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                res = {"__error__": str(e)}
            self._emit("refresh_done", res)

        threading.Thread(target=work, daemon=True).start()

    def _refresh_once(self, only_ids=None) -> dict:
        listed = self.worker.list_devices()
        enum = listed.get("devices", []) if "error" not in listed else []
        mode = self.config.settings.get("battery_source_mode", "prefer_exact")
        want_usb = any(self._transport(d) in ("2.4g", "usb")
                       and (only_ids is None or d["id"] in only_ids)
                       for d in self.config.devices)
        usb_list = None
        if want_usb:
            try:
                usb_list = hid2g.list_devices(include_bluetooth=False).get("devices", [])
            except Exception:
                usb_list = []
        results = {}
        for dev in self.config.devices:
            if only_ids is not None and dev["id"] not in only_ids:
                continue
            tp = self._transport(dev)
            if tp in ("2.4g", "usb"):
                results[dev["id"]] = self._refresh_usb(dev, usb_list or [])
                continue
            info = self._match_device(dev, enum)
            r = vendor_protocols.read_battery(dev, info, self.worker, mode)
            results[dev["id"]] = {
                "battery": r.get("battery"),
                "precision": r.get("precision", "none"),
                "source": r.get("source", ""),
                "detail": r.get("detail", ""),
                "need_client": r.get("need_client", False),
                "client": r.get("client") or {},
                "connected": bool(info.get("connected")),
                "name": info.get("name") or dev["name"],
                "updated": time.strftime("%H:%M:%S"),
            }
        return results

    def _transport(self, dev: dict) -> str:
        """设备连接方式：bt 蓝牙 / 2.4g 无线接收器 / usb 有线 USB。"""
        tp = (dev.get("transport") or "").lower()
        return tp if tp in ("2.4g", "usb") else "bt"

    def _refresh_usb(self, dev: dict, usb_list: list[dict]) -> dict:
        """2.4G / USB 外设：从 HID 直接读（Windows 设置页对这些设备不给电量）。"""
        hit = None
        key = (dev.get("hid_key") or "").lower()
        if key:
            for u in usb_list:
                if (u.get("instance_id") or "").lower() == key:
                    hit = u
                    break
            if hit is None:      # 换了 USB 口后实例 id 会变，按 VID/PID 段重新对上
                base = key.split("#")[0]
                for u in usb_list:
                    if (u.get("instance_id") or "").lower().startswith(base):
                        hit = u
                        break
        if hit is None:
            for u in usb_list:
                if (u.get("name") or "").lower() == (dev.get("name") or "").lower():
                    hit = u
                    break
        if hit is None:
            return {"battery": None, "precision": "none", "source": "",
                    "detail": "设备未插入（接收器已拔出或已关机）",
                    "need_client": False, "client": {},
                    "connected": False, "name": dev["name"],
                    "updated": time.strftime("%H:%M:%S")}
        conn = bool(hit.get("connected", True))
        return {
            "battery": hit.get("battery"),
            "precision": hit.get("precision", "none"),
            "source": hit.get("source", ""),
            "detail": hit.get("detail", ""),
            "need_client": False, "client": {},
            "connected": conn,
            "name": hit.get("name") or dev["name"],
            "transport_name": hit.get("transport_name", ""),
            "updated": time.strftime("%H:%M:%S"),
        }

    def _match_device(self, dev: dict, enum: list[dict]) -> dict:
        """把配置的设备匹配到系统枚举端点，并回填地址/容器 id。"""
        # 1) 地址精确匹配
        for e in enum:
            if dev.get("address") is not None and e.get("address") == dev["address"]:
                self._rebind(dev, e)
                return e
        # 2) 容器 id 匹配（跨经典/LE 端点）
        for e in enum:
            if dev.get("container_id") and e.get("container_id") \
                    and e["container_id"] == dev["container_id"]:
                self._rebind(dev, e)
                return e
        # 3) 名称匹配（扫描添加的是广播 RPA，地址会轮换，靠名称重新绑定）
        name = (dev.get("name") or "").strip().lower()
        if name:
            for e in sorted(enum, key=lambda x: not x.get("connected")):
                if (e.get("name") or "").strip().lower() == name:
                    self._rebind(dev, e)
                    return e
        return {}

    def _rebind(self, dev: dict, e: dict):
        changed = False
        if not dev.get("container_id") and e.get("container_id"):
            dev["container_id"] = e["container_id"]
            changed = True
        if e.get("address") is not None and dev.get("address") != e["address"] \
                and not e.get("is_le"):
            # 回填经典蓝牙稳定地址（扫描拿到的是会轮换的 RPA）
            dev["address"] = e["address"]
            changed = True
        if changed:
            self.config.save()

    def on_refresh_done(self, results: dict):
        err = results.pop("__error__", None)
        if err:
            self.main.set_status(f"刷新失败：{err}")
            return
        known = {d["id"] for d in self.config.devices}
        for k in list(self.status):
            if k not in known:
                self.status.pop(k)
        self.status.update(results)
        threshold = int(self.config.settings.get("low_battery_threshold", 30))
        prompt_enabled = bool(self.config.settings.get("prompt_client_once", True))
        for dev in list(self.config.devices):
            st = results.get(dev["id"])
            if not st:
                continue
            if dev.get("show_in_tray", True):
                self.tray.show_device(dev, st["battery"], st.get("precision"),
                                      self._tray_style(dev))
            else:
                self.tray.hide_device(dev["id"])
            b = st["battery"]
            if b is not None and dev.get("low_battery_alert", True) and b <= threshold:
                level = 20 if b <= 20 else 30
                if self._alerted.get(dev["id"]) != level:
                    self._alerted[dev["id"]] = level
                    self.tray.notify_low(dev, b)
            # 缺少精确协议 → 首次连上时引导安装厂商客户端
            if (prompt_enabled and st.get("need_client") and st.get("connected")
                    and not dev.get("client_prompted")):
                dev["client_prompted"] = True
                self.config.save()
                self._emit("client_prompt", dev["id"])
        n_exact = sum(1 for s in self.status.values() if s.get("precision") == "exact")
        n_coarse = sum(1 for s in self.status.values() if s.get("precision") == "coarse")
        self.main.set_status(
            f"已刷新 {len(results)} 台设备 · 精确 {n_exact} 台 · 粗略 {n_coarse} 台 · "
            f"上次更新 {time.strftime('%H:%M:%S')}")
        self.on_render()

    # ------------------------------------------------------------------ #
    # 设备管理
    # ------------------------------------------------------------------ #
    def add_device(self, **kw) -> dict:
        dev = self.config.add_device(**kw)
        self._sync_hotkeys()
        return dev

    def on_remove_device(self, dev_id: str):
        self.remove_devices([dev_id])

    # 供 UI 按钮 / 右键菜单直接调用的别名
    def remove_device(self, dev_id: str):
        self.on_remove_device(dev_id)

    def toggle_show(self, dev_id: str):
        self.on_toggle_show(dev_id)

    def toggle_alert(self, dev_id: str):
        self.on_toggle_alert(dev_id)
        self.on_render()

    def remove_devices(self, ids: list[str]):
        """批量移除设备（只删本软件记录，不动系统蓝牙配对）。"""
        wanted = set(ids)
        if not wanted:
            return
        before = len(self.config.devices)
        for dev_id in wanted:
            win = self.windows.get(f"detail:{dev_id}")
            if win is not None:
                try:
                    win.destroy()
                except Exception:
                    pass
            self.tray.hide_device(dev_id)
            self.status.pop(dev_id, None)
            self._alerted.pop(dev_id, None)
        self.config.devices = [d for d in self.config.devices
                               if d["id"] not in wanted]
        self.config.save()
        n = before - len(self.config.devices)
        self._sync_hotkeys()
        self.main.clear_selection()
        self.on_render()
        self.main.set_status(
            f"已移除 {n} 台设备（系统蓝牙连接不受影响）。" if n
            else "没有可移除的设备。")

    def on_toggle_alert(self, dev_id: str):
        dev = self.config.find(dev_id)
        if dev:
            dev["low_battery_alert"] = not dev.get("low_battery_alert", True)
            self.config.save()
            self.tray._device_state[dev_id] = dict(dev)

    def on_toggle_show(self, dev_id: str):
        dev = self.config.find(dev_id)
        if not dev:
            return
        dev["show_in_tray"] = not dev.get("show_in_tray", True)
        self.config.save()
        self.tray._device_state[dev_id] = dict(dev)
        if dev["show_in_tray"]:
            st = self.status.get(dev_id, {})
            self.tray.show_device(dev, st.get("battery"), st.get("precision"),
                                  self._tray_style(dev))
        else:
            self.tray.hide_device(dev_id)
        self.on_render()

    def on_set_type(self, dev_id: str, tkey: str):
        dev = self.config.find(dev_id)
        if not dev:
            return
        dev["dev_type"] = tkey if tkey in common.DEVICE_TYPES else "generic"
        self.config.save()
        self.tray._device_state[dev_id] = dict(dev)
        self._sync_hotkeys()
        self.on_render()
        self.main.set_status(f"设备类型已设为 {common.device_type_name(dev['dev_type'])}")

    def _tray_style(self, dev: dict) -> str:
        """该设备托盘图标外观：设备自身设置优先，否则跟随全局设置。"""
        return dev.get("tray_style") or self.config.settings.get(
            "tray_style", "square")

    def on_set_tray_style(self, dev_id: str, style: str):
        dev = self.config.find(dev_id)
        if not dev:
            return
        dev["tray_style"] = style if style in ("square", "pill", "number") else ""
        self.config.save()
        self.tray._device_state[dev_id] = dict(dev)
        if dev.get("show_in_tray", True):
            st = self.status.get(dev_id, {})
            self.tray.show_device(dev, st.get("battery"), st.get("precision"),
                                  self._tray_style(dev))
        self.main.set_status("托盘图标外观已更新。")

    def on_open_sys_settings(self, dev_id: str):
        dev = self.config.find(dev_id)
        if not dev:
            return
        import os
        uri = common.SYS_SETTINGS_URI.get(dev.get("dev_type", "generic"),
                                          "ms-settings:bluetooth")
        try:
            os.startfile(uri)  # noqa: S606
        except Exception as e:  # noqa: BLE001
            self.main.set_status(f"无法打开系统设置：{e}")

    def open_volume_mixer(self):
        """打开老版音量合成器（SndVol.exe，任何 Windows 版本都自带）。"""
        import os
        sndvol = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                              "System32", "SndVol.exe")
        try:
            os.startfile(sndvol)  # noqa: S606
        except Exception as e:  # noqa: BLE001
            self.main.set_status(f"无法打开音量合成器：{e}")

    def open_game_controllers(self):
        """打开经典「游戏控制器」面板（joy.cpl）：测试 / 校准手柄。"""
        import os
        import subprocess
        windir = os.environ.get("WINDIR", r"C:\Windows")
        ctl = os.path.join(windir, "System32", "control.exe")
        try:
            subprocess.Popen([ctl, "joy.cpl"])
            self.main.set_status("已打开游戏控制器面板，可测试按键与校准摇杆。")
            return
        except Exception:
            pass
        try:
            os.startfile(os.path.join(windir, "System32", "joy.cpl"))  # noqa: S606
            self.main.set_status("已打开游戏控制器面板。")
        except Exception as e:  # noqa: BLE001
            self.main.set_status(f"无法打开游戏控制器：{e}")

    def set_english_layout(self):
        """切到英语（美国）输入法——游戏打字不再被中文输入法接管。

        做法：SPI_SETDEFAULTINPUTLANG 持久写入默认布局（"永久"，重启后仍生效），
        再广播 WM_INPUTLANGCHANGEREQUEST 让现有窗口立即切换。
        切回中文按 Win+空格，或用键盘设备详情页配置的切换快捷键。
        """
        import ctypes
        user32 = ctypes.windll.user32
        HKL_ENUS = 0x04090409  # 英语（美国）· 美式键盘
        hkl = ctypes.c_void_p(HKL_ENUS)
        # 0x005A = SPI_SETDEFAULTINPUTLANG；0x03 = UPDATEINIFILE | SENDCHANGE
        ok = user32.SystemParametersInfoW(0x005A, 0, ctypes.byref(hkl), 0x03)
        # 广播给所有顶层窗口立即生效
        user32.PostMessageW(0xFFFF, 0x0050, 0, HKL_ENUS)
        if ok:
            self.main.set_status(
                "已切换为英语（美国）输入法（系统默认，重启后仍生效）。"
                "切回中文：Win+空格，或按切换快捷键。")
        else:
            self.main.set_status("切换输入法失败，请手动用 Win+空格 切换。")

    def toggle_layout(self):
        """中/英互切：按前台窗口当前布局判断方向（供全局快捷键调用）。"""
        import ctypes
        user32 = ctypes.windll.user32
        HKL_ENUS = 0x04090409
        HKL_CN = 0x08040804   # 中文（简体）· 微软拼音
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return
        pid = ctypes.c_uint()
        tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        lang = user32.GetKeyboardLayout(tid) & 0xFFFF
        target = HKL_CN if lang == 0x0409 else HKL_ENUS
        user32.PostMessageW(hwnd, 0x0050, 0, target)  # WM_INPUTLANGCHANGEREQUEST
        to = "英语（美国）" if target == HKL_ENUS else "中文"
        self.main.set_status(f"已把当前窗口输入法切换为 {to}。")

    def on_hotkey_toggle(self, dev_id: str):
        dev = self.config.find(dev_id)
        self.toggle_layout()
        name = f"（{dev['name']} 的快捷键）" if dev else ""
        self.main.set_status(f"输入法已切换{name}。")

    def on_set_hotkey(self, dev_id: str, spec):
        """spec: {name, mods, vk}（录制结果）或 ""（清除）。"""
        dev = self.config.find(dev_id)
        if not dev:
            return
        from .hotkeys import normalize
        n = normalize(spec)
        dev["hotkey"] = n or ""
        self.config.save()
        self._sync_hotkeys()
        if n:
            self.main.set_status(
                f"已设置快捷键 {n['name']}：在任意程序中按下即可 中/英 互切输入法。")
        else:
            self.main.set_status("已取消切换快捷键。")

    def _sync_hotkeys(self):
        self.hotkeys.update(self.config.devices)
        failed = self.hotkeys.failed
        if failed:
            self.main.set_status(
                f"快捷键 {'、'.join(failed)} 注册失败（可能被其他程序占用），"
                "请在设备详情页换一个。")

    # ------------------------------------------------------------------ #
    # 窗口
    # ------------------------------------------------------------------ #
    # 供 UI 按钮直接调用的别名
    def open_scan(self):
        self.on_open_scan()

    def open_link_add(self):
        self.on_open_link_add()

    def open_settings(self):
        self.on_open_settings()

    def open_local_manager(self):
        """本地外设管理器（声音输出切换 / 内置键盘代码模式）。"""
        from .ui import LocalManagerWindow
        win = self.windows.get("localmgr")
        if win is not None:
            try:
                win.deiconify()
                win.lift()
                win.focus_force()
                return
            except Exception:
                pass
        LocalManagerWindow(self)

    def open_about(self):
        """关于窗口（版本 / 更新内容 / 作者 / 项目主页）。"""
        from .ui import AboutWindow
        win = self.windows.get("about")
        if win is not None:
            try:
                win.deiconify()
                win.lift()
                win.focus_force()
                return
            except Exception:
                pass
        AboutWindow(self)

    def on_open_about(self):
        """托盘菜单「关于」。"""
        self.open_about()

    def on_localmgr_audio(self, win, res: dict):
        try:
            win.on_audio(res)
        except Exception:
            import traceback
            traceback.print_exc()

    def on_toggle_code_mode(self):
        """切换代码模式总开关（本地外设管理器里的内置键盘开关）。"""
        cm = self.config.settings.setdefault("code_mode", {})
        cm["enabled"] = not cm.get("enabled")
        self.config.save()
        self.on_code_mode_changed()

    def open_code_mode(self):
        """代码模式设置（从键盘设备详情页 / 设置页进入）。"""
        from .codemode_ui import CodeModeWindow
        win = self.windows.get("codemode")
        if win is not None:
            try:
                win.deiconify()
                win.lift()
                win.focus_force()
                return
            except Exception:
                pass
        CodeModeWindow(self)

    def on_code_mode_changed(self):
        """设置保存后重新应用（按需启停键盘钩子）。"""
        self.codemode.apply_settings()
        cm = self.config.settings.get("code_mode") or {}
        if cm.get("enabled") and not cm.get("apps"):
            self.main.set_status("代码模式已开启 · 还未配置生效软件（先去设置里添加）")
        elif cm.get("enabled") and cm.get("apps"):
            running = self.codemode.running()
            self.main.set_status(
                f"代码模式已启用 · {len(cm['apps'])} 个软件 · "
                + ("钩子运行中" if running else "钩子未启动"))
        else:
            self.main.set_status("代码模式已关闭。")

    def open_keyboard_props(self):
        """打开 Windows 经典「键盘属性」（重复延迟 / 重复速度 / 光标闪烁）。"""
        import os
        import subprocess
        windir = os.environ.get("WINDIR", r"C:\Windows")
        try:
            subprocess.Popen([os.path.join(windir, "System32", "control.exe"),
                              "main.cpl,@1"])
            self.main.set_status("已打开键盘属性（经典面板）。")
            return
        except Exception:
            pass
        try:
            os.startfile("ms-settings:keyboard")  # noqa: S606
            self.main.set_status("已打开键盘设置。")
        except Exception as e:  # noqa: BLE001
            self.main.set_status(f"无法打开键盘属性：{e}")

    def open_detail(self, dev_id: str):
        self.on_open_detail(dev_id)

    def on_open_main(self):
        self.main.deiconify()
        self.main.lift()
        self.main.focus_force()

    def on_open_detail(self, dev_id: str):
        dev = self.config.find(dev_id)
        if not dev:
            return
        # 复用已打开的详情窗口（设备托盘左键直达详情，不经过主面板）
        win = self.windows.get(f"detail:{dev_id}")
        if win is not None:
            try:
                win.deiconify()
                win.lift()
                win.focus_force()
                return
            except Exception:
                pass
        DetailWindow(self, dev_id)

    def on_open_scan(self):
        ScanWindow(self)

    def on_open_link_add(self):
        LinkAddWindow(self)

    def on_open_settings(self):
        SettingsWindow(self)

    def on_render(self):
        devs = sorted(self.config.devices, key=lambda d: d["name"])
        self.main.render(devs, self.status)
        for key, win in list(self.windows.items()):
            if not key.startswith("detail:"):
                continue
            try:
                win.render()
            except Exception:
                pass

    def on_scan_result(self, win: ScanWindow, res: dict):
        win.on_scan_result(res)

    # ---------------- 2.4G / USB 外设 ----------------
    def scan_usb_async(self, win):
        """扫描本机 USB / 2.4G 外设（HID 直读，不走蓝牙）。"""
        def work():
            try:
                res = hid2g.list_devices(include_bluetooth=False)
            except Exception as e:  # noqa: BLE001
                res = {"error": str(e), "devices": []}
            self._emit("usb_scan_result", win, res)

        threading.Thread(target=work, daemon=True).start()

    def on_usb_scan_result(self, win, res: dict):
        try:
            win.on_usb_result(res)
        except Exception:
            import traceback
            traceback.print_exc()

    def on_set_transport(self, dev_id: str, tp: str):
        """手动指定连接方式（自动判断失误时的兜底）。"""
        dev = self.config.find(dev_id)
        if not dev:
            return
        dev["transport"] = tp if tp in ("bt", "2.4g", "usb") else ""
        self.config.save()
        self.refresh_async([dev_id])
        label = {"bt": "蓝牙", "2.4g": "2.4G 无线", "usb": "USB 有线"}.get(tp, "自动判断")
        self.main.set_status(f"连接方式已设为「{label}」，正在重新读取电量…")
        self.on_render()

    def on_link_result(self, win: LinkAddWindow, res: dict):
        win.on_link_result(res)

    def on_quit(self):
        try:
            self.codemode.hook.stop()
        except Exception:
            pass
        self.tray.stop()
        self.main.destroy()

    # 供 pystray 菜单直接回调（线程安全转发）
    def on_refresh_all(self):
        self.refresh_all_async()

    def on_refresh_one(self, dev_id: str):
        self.refresh_async([dev_id])

    # ------------------------------------------------------------------ #
    # 厂商客户端 / 协议
    # ------------------------------------------------------------------ #
    def _info_of(self, dev: dict) -> dict:
        listed = self.worker.list_devices()
        enum = listed.get("devices", []) if "error" not in listed else []
        return self._match_device(dev, enum)

    def on_client_prompt(self, dev_id: str):
        dev = self.config.find(dev_id)
        if not dev:
            return
        # 厂商在 Windows 上没有官方客户端时，不弹 APP 引导（避免无效选项）
        c = clients.detect(vendor_protocols.vendor_key(dev))
        if not c.get("has_pc_app"):
            return
        ClientPromptWindow(self, dev_id)

    def on_open_vendor_download(self, dev_id: str):
        dev = self.config.find(dev_id)
        if not dev:
            return
        key = vendor_protocols.vendor_key(dev)
        ok, msg = clients.open_download(key)
        self.main.set_status(("已打开官方下载页：" if ok else "无法打开下载页：") + str(msg))

    def on_check_client(self, dev_id: str):
        def work():
            dev = self.config.find(dev_id)
            if not dev:
                return
            info = vendor_protocols.client_info(dev, force=True)
            self._emit("client_checked", dev_id, info)

        threading.Thread(target=work, daemon=True).start()

    def on_client_checked(self, dev_id: str, info: dict):
        for win in list(self.windows.values()):
            if hasattr(win, "on_client_checked"):
                try:
                    win.on_client_checked(info)
                except Exception:
                    pass
        n = len(info.get("installed", []))
        self.main.set_status(f"客户端检测：{info.get('name') or '未识别厂商'} · "
                             f"{'已安装 ' + str(n) + ' 项' if n else '未安装'}")

    def on_probe_device(self, dev_id: str):
        def work():
            dev = self.config.find(dev_id)
            if not dev:
                return
            info = self._info_of(dev)
            report = vendor_protocols.deep_probe(dev, info, self.worker)
            path = vendor_protocols.save_report(report)
            self._emit("probe_done", dev_id, report, path)

        self.main.set_status("正在探测设备协议通道（约 5-15 秒）…")
        threading.Thread(target=work, daemon=True).start()

    def on_probe_done(self, dev_id: str, report: dict, path: str):
        for win in list(self.windows.values()):
            if hasattr(win, "on_probe_done"):
                try:
                    win.on_probe_done(report, path)
                except Exception:
                    pass
        okn = sum(1 for c in report.get("channels", []) if c.get("ok"))
        self.main.set_status(f"协议探测完成：{okn}/{len(report.get('channels', []))} "
                             f"个通道可用 · 报告已保存 {path}")

    def on_hfp_refresh(self, dev_id: str):
        dev = self.config.find(dev_id)
        if not dev or dev.get("address") is None:
            self.main.set_status("该设备没有可用的蓝牙地址，无法强制刷新。")
            return

        def work():
            ok, msg = native.hfp_cycle(dev["address"])
            self._emit("hfp_done", dev_id, ok, msg)

        self.main.set_status("正在重连 HFP 链路以迫使设备重新上报电量…")
        threading.Thread(target=work, daemon=True).start()

    def on_hfp_done(self, dev_id: str, ok: bool, msg: str):
        self.main.set_status(msg)
        if ok:
            self.main.after(2500, self.refresh_all_async)
