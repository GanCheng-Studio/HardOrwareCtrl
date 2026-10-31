"""代码模式设置窗口：应用清单（IDE）、语言规则、补齐与中英文切换开关。"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk, filedialog

from . import codemode
from .ui import (BG, PANEL, INPUT, INPUT_HOVER, BORDER, FG, FG_DIM, ACCENT,
                 DANGER_HOVER, OK, FONT, FONT_B, FONT_S, FONT_TAG, RoundBtn,
                 Card, MainWindow, _apply_window_chrome)


class _NameDialog(tk.Toplevel):
    """输入名字的小弹窗（跟随深色主题，不用系统 simpledialog）。"""

    def __init__(self, parent, title, prompt, on_ok):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)
        _apply_window_chrome(self)
        tk.Label(self, text=prompt, bg=BG, fg=FG_DIM, font=FONT_S).pack(
            anchor="w", padx=16, pady=(14, 4))
        self.var = tk.StringVar()
        ent = tk.Entry(self, textvariable=self.var, bg=INPUT, fg=FG,
                       insertbackground=FG, relief="flat", font=FONT,
                       width=28)
        ent.pack(padx=16, pady=(0, 10), ipady=4)
        ent.focus_set()
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=16, pady=(0, 14))
        ok = lambda: (self._ok(on_ok), None)[1]  # noqa: E731
        RoundBtn(bar, "确定", ok, bg=ACCENT, hover="#1177bb", fg="#ffffff",
                 font=FONT_S).pack(side="right")
        RoundBtn(bar, "取消", self.destroy, bg=INPUT, hover=INPUT_HOVER,
                 font=FONT_S).pack(side="right", padx=(0, 8))
        self.bind("<Return>", lambda e: ok())
        self.bind("<Escape>", lambda e: self.destroy())

    def _ok(self, on_ok):
        name = self.var.get().strip()
        if name:
            on_ok(name)
        self.destroy()


class CodeModeWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(MainWindow.instance)
        self.app = app
        self.title("代码模式设置")
        self.geometry("760x800")
        self.minsize(680, 620)
        self.configure(bg=BG)
        self.transient(MainWindow.instance)
        _apply_window_chrome(self)
        self.cfg: dict = app.config.settings.setdefault(
            "code_mode", dict(codemode_defaults()))
        cfgd = codemode_defaults()
        for k, v in cfgd.items():
            self.cfg.setdefault(k, v)
        self._app_rows: list[dict] = []
        self._lang_rows: list[dict] = []
        try:
            app.windows["codemode"] = self
        except Exception:
            pass
        self._build()
        self.render_apps()
        self.render_langs()
        self._tick_status()

    def destroy(self):
        try:
            self.app.windows.pop("codemode", None)
        except Exception:
            pass
        super().destroy()

    # ------------------------------------------------------------------ #
    def _build(self):
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=18, pady=(14, 4))
        tk.Label(head, text="代码模式", bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 14, "bold")).pack(side="left")
        self.enabled = tk.BooleanVar(value=bool(self.cfg.get("enabled")))
        tk.Checkbutton(head, text="启用", variable=self.enabled, bg=BG, fg=FG,
                       activebackground=BG, activeforeground=FG,
                       selectcolor=INPUT, font=FONT_B, bd=0,
                       highlightthickness=0).pack(side="left", padx=(14, 0))
        self.status_lbl = tk.Label(head, text="", bg=BG, fg=FG_DIM,
                                    font=FONT_TAG)
        self.status_lbl.pack(side="right")

        tk.Label(self, text="在配置的 IDE 里自动补齐括号/引号，并按“是否在字符串内”"
                           "切换中英文；不在字符串内保持英文（不锁定，随时可手动切换）。",
                 bg=BG, fg=FG_DIM, font=FONT_S, justify="left",
                 wraplength=710).pack(anchor="w", padx=18, pady=(0, 8))

        # ---- 总开关 ---- #
        card = Card(self, pady=10)
        card.pack(fill="x", padx=18, pady=(0, 8))
        g = card.inner
        self.ap = tk.BooleanVar(value=bool(self.cfg.get("autopair", True)))
        self.sw = tk.BooleanVar(value=bool(self.cfg.get("auto_switch", True)))
        self.cp = tk.BooleanVar(value=bool(self.cfg.get("chinese_punct", True)))
        self.tz = tk.BooleanVar(value=bool(self.cfg.get("terminal_zh", True)))
        opts = (("自动补齐括号 / 引号", self.ap),
                ("按上下文自动切换中英文", self.sw),
                ("切中文时请求中文标点", self.cp),
                ("进入终端 / bash 时切中文", self.tz))
        row1 = tk.Frame(g, bg=PANEL)
        row1.pack(fill="x")
        row2 = tk.Frame(g, bg=PANEL)
        row2.pack(fill="x")
        for i, (text, var) in enumerate(opts):
            tk.Checkbutton(row1 if i < 2 else row2, text=text, variable=var,
                           bg=PANEL, fg=FG, activebackground=PANEL,
                           activeforeground=FG, selectcolor=INPUT,
                           font=FONT_S, bd=0, highlightthickness=0).pack(
                side="left", padx=(0, 22), pady=1)

        # ---- 应用列表 ---- #
        acard = Card(self, pady=10)
        acard.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        a = acard.inner
        top = tk.Frame(a, bg=PANEL)
        top.pack(fill="x")
        tk.Label(top, text="启用的软件", bg=PANEL, fg=FG,
                 font=FONT_B).pack(side="left")
        tk.Label(top, text="（在这里的软件里才生效；按主程序 exe 匹配）", bg=PANEL,
                 fg=FG_DIM, font=FONT_TAG).pack(side="left", padx=(6, 0))
        self.default_lang = tk.StringVar(value="Python")
        ttk.Combobox(top, textvariable=self.default_lang, state="readonly",
                     values=self._lang_displays(), width=10,
                     font=FONT_TAG).pack(side="right", padx=(6, 0))
        tk.Label(top, text="新加应用默认语言", bg=PANEL, fg=FG_DIM,
                 font=FONT_TAG).pack(side="right")

        self.app_list = self._scroll(a, 150)
        bbar = tk.Frame(a, bg=PANEL)
        bbar.pack(fill="x", pady=(6, 0))
        RoundBtn(bbar, "＋ 选择主程序添加", self._add_by_file, bg=ACCENT,
                 hover="#1177bb", fg="#ffffff", font=FONT_S).pack(side="left")
        RoundBtn(bbar, "＋ 添加已安装的预设", self._add_presets, bg=INPUT,
                 hover=INPUT_HOVER, font=FONT_S).pack(side="left", padx=(8, 0))
        self.app_hint = tk.Label(bbar, text="", bg=PANEL, fg=FG_DIM,
                                 font=FONT_TAG)
        self.app_hint.pack(side="left", padx=(10, 0))

        # ---- 语言规则 ---- #
        lcard = Card(self, pady=10)
        lcard.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        l = lcard.inner
        ltop = tk.Frame(l, bg=PANEL)
        ltop.pack(fill="x")
        tk.Label(ltop, text="语言规则", bg=PANEL, fg=FG,
                 font=FONT_B).pack(side="left")
        tk.Label(ltop, text="（决定字符串定界符，以及该语言是否补齐 / 是否切中英文）",
                 bg=PANEL, fg=FG_DIM, font=FONT_TAG).pack(side="left",
                                                          padx=(6, 0))
        RoundBtn(ltop, "＋ 添加自定义语言", self._add_custom_lang, bg=INPUT,
                 hover=INPUT_HOVER, font=FONT_TAG, padx=8, pady=3).pack(
            side="right")
        self.lang_list = self._scroll(l, 170)

        # ---- 底部 ---- #
        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", padx=18, pady=(0, 14))
        RoundBtn(foot, "保存并应用", self._save, bg=ACCENT, hover="#1177bb",
                 fg="#ffffff", font=FONT_B).pack(side="right")
        RoundBtn(foot, "关闭", self.destroy, bg=INPUT, hover=INPUT_HOVER,
                 font=FONT_S).pack(side="right", padx=(0, 8))
        tk.Label(foot, text="备注：易语言默认“永不切英文”，可在语言规则里关掉。",
                 bg=BG, fg=FG_DIM, font=FONT_TAG).pack(side="left")

    # ------------------------------------------------------------------ #
    def _scroll(self, parent, height):
        wrap = tk.Frame(parent, bg=PANEL, height=height)
        wrap.pack(fill="x")
        wrap.pack_propagate(False)
        cv = tk.Canvas(wrap, bg=PANEL, highlightthickness=0, height=height)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(cv, bg=PANEL)
        cv.create_window((0, 0), window=inner, anchor="nw", tags="inner")
        inner.bind("<Configure>",
                   lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>",
                lambda e: cv.itemconfigure("inner", width=e.width))
        cv.bind_all("<MouseWheel>",
                    lambda e, c=cv: c.yview_scroll(int(-e.delta / 120), "units"))
        return inner

    # ---- 语言名映射 ---------------------------------------------------- #
    def _all_langs(self) -> dict[str, str]:
        out = {k: v["name"] for k, v in codemode.LANGUAGES.items()}
        for c in (self.cfg.get("custom_langs") or []):
            out[c["key"]] = c.get("name") or c["key"]
        return out

    def _lang_displays(self) -> list[str]:
        return list(self._all_langs().values())

    def _lang_map(self) -> dict[str, str]:
        return {v: k for k, v in self._all_langs().items()}

    def _lang_display(self, key: str) -> str:
        return self._all_langs().get(key) or codemode.LANGUAGES["generic"]["name"]

    # ---- 应用行 -------------------------------------------------------- #
    def render_apps(self):
        for w in self.app_list.winfo_children():
            w.destroy()
        self._app_rows.clear()
        apps = self.cfg.get("apps") or []
        if not apps:
            tk.Label(self.app_list, text="还没有添加软件。点下面按钮选择 IDE 的主程序 exe。",
                     bg=PANEL, fg=FG_DIM, font=FONT_S).pack(pady=18)
            return
        for spec in apps:
            self._app_row(spec)

    def _app_row(self, spec: dict):
        row = tk.Frame(self.app_list, bg=PANEL)
        row.pack(fill="x", pady=1)
        tk.Label(row, text=(spec.get("name") or "")[:18], width=16,
                 anchor="w", bg=PANEL, fg=FG, font=FONT_S).pack(side="left")
        tk.Label(row, text=os.path.basename(spec.get("exe") or "")[:22],
                 width=20, anchor="w", bg=PANEL, fg=FG_DIM,
                 font=FONT_TAG).pack(side="left")
        var = tk.StringVar(value=self._lang_display(spec.get("lang", "generic")))
        cb = ttk.Combobox(row, textvariable=var, state="readonly",
                          values=self._lang_displays(), width=11,
                          font=FONT_TAG)
        cb.pack(side="left", padx=(2, 6))
        ap = tk.BooleanVar(value=bool(spec.get("autopair", True)))
        sw = tk.BooleanVar(value=bool(spec.get("auto_switch", True)))
        for text, v in (("补齐", ap), ("中英", sw)):
            tk.Checkbutton(row, text=text, variable=v, bg=PANEL, fg=FG_DIM,
                           activebackground=PANEL, activeforeground=FG,
                           selectcolor=INPUT, font=FONT_TAG, bd=0,
                           highlightthickness=0).pack(side="left")
        RoundBtn(row, "×", lambda s=spec: self._del_app(s), bg=INPUT,
                 hover=DANGER_HOVER, fg=FG_DIM, font=FONT_TAG, padx=7, pady=1,
                 radius=5).pack(side="right", padx=(4, 0))
        self._app_rows.append({"spec": spec, "lang": var, "ap": ap, "sw": sw})

    def _del_app(self, spec: dict):
        apps = self.cfg.get("apps") or []
        self.cfg["apps"] = [a for a in apps if a is not spec]
        self.render_apps()
        self._hint(self.app_hint, f"已移除 {spec.get('name', '')}")

    def _add_app(self, name: str, exe: str, lang: str | None = None) -> bool:
        apps = self.cfg.setdefault("apps", [])
        base = os.path.basename(exe).lower()
        if any(os.path.basename(a.get("exe", "")).lower() == base for a in apps):
            return False
        key = self._lang_map().get(self.default_lang.get(), "generic")
        apps.append({"name": name, "exe": exe, "lang": lang or key,
                     "autopair": True, "auto_switch": True})
        return True

    def _add_by_file(self):
        path = filedialog.askopenfilename(
            parent=self, title="选择 IDE 主程序",
            filetypes=[("可执行程序", "*.exe"), ("所有文件", "*.*")])
        if not path:
            return
        name = os.path.splitext(os.path.basename(path))[0]
        ok = self._add_app(name, path)
        self.render_apps()
        self._hint(self.app_hint, "已添加" if ok else "该程序已在列表中")

    def _add_presets(self):
        found = codemode.detect_presets()
        added = 0
        for p in found:
            if self._add_app(p["name"], p["exe"], p["lang"]):
                added += 1
        self.render_apps()
        if not found:
            self._hint(self.app_hint, "没找到已安装的预设，请手动选择主程序")
        else:
            self._hint(self.app_hint, f"已添加 {added} 个（共扫描到 {len(found)} 个）")

    # ---- 语言行 -------------------------------------------------------- #
    def render_langs(self):
        for w in self.lang_list.winfo_children():
            w.destroy()
        self._lang_rows.clear()
        keys = [k for k in codemode.LANG_ORDER] + \
               [c["key"] for c in (self.cfg.get("custom_langs") or [])]
        names = self._all_langs()
        for key in keys:
            rules = codemode.resolve_rules(self.cfg, key)
            ls = codemode.lang_settings(self.cfg, key)
            row = tk.Frame(self.lang_list, bg=PANEL)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=names.get(key, key), width=16, anchor="w",
                     bg=PANEL, fg=FG, font=FONT_S).pack(side="left")
            tk.Label(row, text=" ".join(rules["quotes"]) or "—", width=14,
                     anchor="w", bg=PANEL, fg=FG_DIM,
                     font=FONT_TAG).pack(side="left")
            ap = tk.BooleanVar(value=bool(ls.get("autopair", True)))
            sw = tk.BooleanVar(value=bool(ls.get("auto_switch", True)))
            nv = tk.BooleanVar(value=bool(ls.get("never_english", False)))
            for text, v in (("补齐", ap), ("切中英", sw)):
                tk.Checkbutton(row, text=text, variable=v, bg=PANEL, fg=FG_DIM,
                               activebackground=PANEL, activeforeground=FG,
                               selectcolor=INPUT, font=FONT_TAG, bd=0,
                               highlightthickness=0).pack(side="left",
                                                          padx=(0, 4))
            only_epl = bool(codemode.LANGUAGES.get(key, {})
                            .get("never_english", False))
            cb = tk.Checkbutton(row, text="永不切英文", variable=nv, bg=PANEL,
                                fg=FG_DIM if only_epl else "#5a5a5a",
                                activebackground=PANEL, activeforeground=FG,
                                selectcolor=INPUT, font=FONT_TAG, bd=0,
                                highlightthickness=0,
                                state="normal" if only_epl else "disabled")
            cb.pack(side="left")
            self._lang_rows.append({"key": key, "ap": ap, "sw": sw, "nv": nv})

    def _add_custom_lang(self):
        def ok(name):
            key = "custom_" + str(abs(hash(name)) % 10 ** 8)
            self.cfg.setdefault("custom_langs", []).append(
                {"key": key, "name": name, "base": "generic"})
            self.render_langs()
            self.render_apps()
        _NameDialog(self, "添加语言", "语言名称（字符串按 C 风格引号识别）", ok)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _hint(lbl, text):
        lbl.configure(text=text)

    def _tick_status(self):
        try:
            if self.cfg.get("enabled") and self.cfg.get("apps"):
                run = self.app.codemode.running()
                self.status_lbl.configure(
                    text=("钩子运行中" if run else "钩子未启动"),
                    fg=OK if run else "#d29922")
            else:
                self.status_lbl.configure(text="未启用", fg=FG_DIM)
            self.after(1200, self._tick_status)
        except Exception:
            pass

    def _save(self):
        cm = self.cfg
        cm["enabled"] = bool(self.enabled.get())
        cm["autopair"] = bool(self.ap.get())
        cm["auto_switch"] = bool(self.sw.get())
        cm["chinese_punct"] = bool(self.cp.get())
        cm["terminal_zh"] = bool(self.tz.get())
        disp2key = self._lang_map()
        apps = []
        for r in self._app_rows:
            spec = dict(r["spec"])
            spec["lang"] = disp2key.get(r["lang"].get(), "generic")
            spec["autopair"] = bool(r["ap"].get())
            spec["auto_switch"] = bool(r["sw"].get())
            apps.append(spec)
        cm["apps"] = apps
        langs = dict(cm.get("langs") or {})
        for r in self._lang_rows:
            langs[r["key"]] = {"autopair": bool(r["ap"].get()),
                               "auto_switch": bool(r["sw"].get()),
                               "never_english": bool(r["nv"].get())}
        cm["langs"] = langs
        self.app.config.save()
        self.app.on_code_mode_changed()
        self.destroy()


def codemode_defaults() -> dict:
    from .config import DEFAULT_CODE_MODE
    return dict(DEFAULT_CODE_MODE)
