# -*- coding: utf-8 -*-
"""显示设置：外观页签、主题切换、数据源与列表显示项。

从 app.py 的 MainWindow 拆出，作为 mixin 混入主窗口。
"""

import json

from PySide6.QtGui import QFont, QColor
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFontComboBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QSlider, QSpinBox, QTabWidget, QTextEdit, QVBoxLayout, QWidget)
from .core import theme
from .widgets import (
    Console, NavButton, _th, _CONSOLE_STALE_BG, _CONSOLE_STALE_INFO)
from .core.palette import PAL


class SettingsMixin:
    """显示设置：外观页签、主题切换、数据源与列表显示项。"""

    # ---------------- 显示设置 ----------------
    def _apply_display(self):
        f = QFont()
        f.setPointSize(int(self.disp.get("font", 13)))
        self.table.setFont(f)
        hh = self.table.horizontalHeader()
        hf = QFont()
        hf.setPointSize(int(self.disp.get("font", 13)))
        hf.setBold(True)
        hh.setFont(hf)
        try:
            self.table.verticalHeader().setDefaultSectionSize(
                int(self.disp.get("row", 30)))
        except Exception:
            pass
        for r in range(self.table.rowCount()):
            self.table.setRowHeight(r, int(self.disp.get("row", 30)))

    def _wrap(self, w):
        """把一个已带布局的 widget 变成页签页。"""
        return w

    def _console_colors_tab(self):
        """V1.1.0：控制台配色页签（info / warn / error / chat）。

        返回 {"widget": 页签widget, "result": 取色函数}。
        """
        cur = self.disp.get("console_colors") or {}
        # ★ V2.0.3：旧默认/历史测试值迁移到当前主题色（与信息面板一致）。
        #   用户真正自定义过的颜色保留。
        th_fg, th_bg = Console._theme_colors()
        if (cur.get("bg") or "").lower() in _CONSOLE_STALE_BG:
            cur = dict(cur)
            cur["bg"] = th_bg
        if (cur.get("info") or "").lower() in _CONSOLE_STALE_INFO:
            cur = dict(cur)
            cur["info"] = th_fg
        w = QWidget()
        vv = QVBoxLayout(w)

        tip = QLabel(
            "自定义控制台背景与各类型日志的颜色。\n"
            "不设置就跟随主题（和信息面板配色一致）。\n"
            "背景选浅色时，与背景太接近的文字会自动调整保证可读。\n"
            "报错堆栈（Exception / Caused by / at net.minecraft...）\n"
            "会使用『错误』的颜色并加粗。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:%s;" % PAL.MUTED)
        vv.addWidget(tip)

        f = QFormLayout()
        items = [
            ("bg", "背景"),
            ("info", "普通信息"),
            ("warn", "警告"),
            ("error", "错误"),
            ("chat", "玩家聊天"),
        ]
        btns = {}
        from PySide6.QtWidgets import QColorDialog
        from PySide6.QtGui import QColor

        def _txt_on(hx):
            """色块上的文字色：按亮度自动黑/白，保证可读。"""
            try:
                from .core import theme as _th
                return _th.ensure_contrast(PAL.WHITE, hx, 4.5)
            except Exception:
                return PAL.WHITE

        def _default(key):
            # ★ V2.0.3：默认背景/普通信息跟随主题（和信息面板一致）
            if key == "bg":
                return th_bg
            if key == "info":
                return th_fg
            return Console.DEFAULT_COLORS.get(key, PAL.TEXT_DIM)

        def _mk(key, label):
            default = _default(key)
            hexs = cur.get(key) or default
            b = QPushButton(hexs)
            b.setStyleSheet(
                "QPushButton{background:%s; color:%s;"
                "border:1px solid rgba(128,128,128,90);"
                "border-radius:4px; padding:4px 10px;}"
                "QPushButton:hover{font-weight:bold;}"
                % (hexs, _txt_on(hexs)))
            b.setToolTip("点击打开颜色选择器，选中后按钮显示该颜色色块")
            b.setMinimumHeight(28)

            def pick():
                col = QColorDialog.getColor(
                    QColor(hexs), self,
                    "选择『%s』的颜色" % label)
                if col.isValid():
                    hx = col.name()
                    b.setText(hx)
                    b.setStyleSheet(
                        "QPushButton{background:%s; color:%s;"
                        "border:1px solid rgba(128,128,128,90);"
                        "border-radius:4px; padding:4px 10px;}"
                        "QPushButton:hover{font-weight:bold;}"
                        % (hx, _txt_on(hx)))

            b.clicked.connect(pick)
            return b

        for key, label in items:
            btns[key] = _mk(key, label)
            f.addRow("%s:" % label, btns[key])

        b_reset = QPushButton("恢复默认配色")
        b_reset.clicked.connect(
            lambda: _reset(btns, items))
        vv.addLayout(f)

        def _reset(bts, its):
            for key, label in its:
                d = _default(key)
                bts[key].setText(d)
                bts[key].setStyleSheet(
                    "QPushButton{background:%s; color:%s;"
                    "border:1px solid rgba(128,128,128,90);"
                    "border-radius:4px; padding:4px 10px;}"
                    "QPushButton:hover{font-weight:bold;}"
                    % (d, _txt_on(d)))

        vv.addWidget(b_reset)
        vv.addStretch(1)

        def _result():
            out = {}
            for key, label in items:
                out[key] = btns[key].text().strip() or _default(key)
            return out

        return {"widget": w, "result": _result}

    def _appearance_tab(self):
        """外观/主题设置页。"""
        from .core import theme as _th
        cur = dict(_th.get())

        w = QWidget()
        v = QVBoxLayout(w)

        # ---- 字体 ----
        gf = QGroupBox("字体")
        ff = QFormLayout(gf)
        cb_fam = QFontComboBox()
        if cur.get("font_family"):
            cb_fam.setCurrentFont(QFont(cur["font_family"]))
        # ★ QFontComboBox 默认给的是 "Sans Serif"。以前不管用户动没动过，
        #   点 OK 都把这个默认值写进配置 —— 中文 Windows 上会把界面字体
        #   换成不适配中文的族，表现就是"改了颜色字体反而变丑/像没生效"。
        #   只有用户真的选过才写入。
        _touched = {"font": False}
        cb_fam.currentFontChanged.connect(
            lambda f: _touched.__setitem__("font", True))
        ff.addRow("字体:", cb_fam)
        sp_fs = QSpinBox()
        sp_fs.setRange(0, 24)
        sp_fs.setSpecialValueText("默认")
        sp_fs.setValue(int(cur.get("font_size") or 0))
        ff.addRow("字号:", sp_fs)

        # ---- 字体效果：粗体 ----
        he = QHBoxLayout()
        cb_bold = QCheckBox("粗体")
        cb_bold.setChecked(bool(int(cur.get("font_bold") or 0)))
        cb_bold.setToolTip("全局字体加粗（按钮、标签、表格都会变粗）")
        he.addWidget(cb_bold)
        he.addStretch(1)
        ff.addRow("效果:", he)
        v.addWidget(gf)

        # ---- 颜色 ----
        gc = QGroupBox("颜色")
        fc = QFormLayout(gc)
        picks = {}

        def mk(key, label):
            b = QPushButton("")
            b.setFixedWidth(70)

            def paint():
                b.setStyleSheet(
                    "background:%s; border:1px solid #888;"
                    % cur[key])
            paint()

            def pick():
                c = QColorDialog.getColor(QColor(cur[key]), w,
                                          label)
                if c.isValid():
                    cur[key] = c.name()
                    paint()
            b.clicked.connect(pick)
            picks[key] = (b, paint)
            fc.addRow(label + ":", b)

        mk("fg", "字体颜色")
        mk("bg", "背景颜色")
        mk("accent", "强调色")
        v.addWidget(gc)

        # ---- 背景图 ----
        gi = QGroupBox("背景图片（只套在主窗口上）")
        vi = QVBoxLayout(gi)
        hb = QHBoxLayout()
        ed_img = QLineEdit(cur.get("bg_image") or "")
        ed_img.setPlaceholderText("不选就是纯色背景")
        hb.addWidget(ed_img, 1)
        b_img = QPushButton("选择...")

        def pick_img():
            f, _ = QFileDialog.getOpenFileName(
                w, "选背景图", "", "图片 (*.png *.jpg *.jpeg *.bmp)")
            if f:
                ed_img.setText(f)
        b_img.clicked.connect(pick_img)
        hb.addWidget(b_img)
        b_clr = QPushButton("清除")

        def clr_img():
            ed_img.setText("")
        b_clr.clicked.connect(clr_img)
        hb.addWidget(b_clr)
        vi.addLayout(hb)

        cb_mode = QComboBox()
        cb_mode.addItem("拉伸铺满（常用）", "stretch")
        cb_mode.addItem("平铺重复", "tile")
        i = cb_mode.findData(cur.get("bg_mode") or "stretch")
        cb_mode.setCurrentIndex(i if i >= 0 else 0)
        vi.addWidget(cb_mode)

        sl = QSlider(Qt.Horizontal)
        sl.setRange(0, 100)
        sl.setValue(int(cur.get("bg_opacity") or 88))
        lbl_op = QLabel("")

        def show_op(n):
            lbl_op.setText("遮罩浓度 %d%%（越高文字越清楚、图越暗）"
                           % n)
        sl.valueChanged.connect(show_op)
        show_op(sl.value())
        vi.addWidget(sl)
        vi.addWidget(lbl_op)
        v.addWidget(gi)

        # ---- 预设 ----
        gp = QGroupBox("预设")
        hp = QHBoxLayout(gp)
        PRESETS = list(_th.PRESETS)
        for name, fg, bg, ac in PRESETS:
            b = QPushButton(name)

            def go(fg=fg, bg=bg, ac=ac, name=name):
                cur["fg"], cur["bg"], cur["accent"] = fg, bg, ac
                # ★ 深浅切换时"项目常驻字色"要跟着换：
                #   浅色档是黑字，直接用到墨绿底上等于消失。
                try:
                    mode = _th.preset_apply(name)[3]
                    cur["item_idle_mode"] = mode
                    j = cb_idle.findData(mode)
                    if j >= 0:
                        cb_idle.setCurrentIndex(j)
                except Exception:
                    pass
                for k in ("fg", "bg", "accent"):
                    if k in picks:
                        picks[k][1]()
            b.clicked.connect(go)
            hp.addWidget(b)

        def reset_def():
            for k in ("fg", "bg", "accent"):
                cur[k] = _th.DEFAULT[k]
                if k in picks:
                    picks[k][1]()
            cur["item_idle_mode"] = _th.DEFAULT["item_idle_mode"]
            try:
                j = cb_idle.findData(_th.DEFAULT["item_idle_mode"])
                if j >= 0:
                    cb_idle.setCurrentIndex(j)
            except Exception:
                pass
            cb_fam.setCurrentFont(QFont(""))
            sp_fs.setValue(int(_th.DEFAULT["font_size"]))
            # setCurrentFont 会触发 currentFontChanged，这里要明确表示
            # "恢复系统默认"而不是"用户选了 Sans Serif"
            cur["font_family"] = ""
            _touched["font"] = False
        b_def = QPushButton("恢复默认")
        b_def.setToolTip("恢复成默认浅灰白配色 + 系统默认字体")
        b_def.clicked.connect(reset_def)
        hp.addWidget(b_def)
        v.addWidget(gp)

        # ---- 选中与悬停效果 ----
        gp2 = QGroupBox("选中与悬停效果")
        fp2 = QFormLayout(gp2)

        cb_pcl = QCheckBox("启用选中 / 悬停样式")
        cb_pcl.setChecked(int(cur.get("pcl") or 0) == 1)
        cb_pcl.setToolTip(
            "启用后：\n"
            " · 页签常驻白字，选中换成主题色并套一圈底\n"
            " · 列表项目常驻暗色字，选中换成主题色 + 左右两条竖杠\n"
            " · 鼠标悬停弹出注释")
        fp2.addRow("", cb_pcl)

        cb_tab_idle = QComboBox()
        cb_tab_idle.addItem("白色", PAL.WHITE)
        cb_tab_idle.addItem("黑色", PAL.BLACK)
        cb_tab_idle.addItem("跟随主字体色", "fg")
        ti = (cur.get("tab_idle") or PAL.WHITE)
        ix = cb_tab_idle.findData(ti)
        cb_tab_idle.setCurrentIndex(ix if ix >= 0 else 0)
        cb_tab_idle.setToolTip("页签【未选中】时的字色。\n"
                               "浅色主题下会自动压深，不会白底白字。")
        fp2.addRow("页签常驻字色:", cb_tab_idle)

        cb_tab_box = QCheckBox("选中的页签套一圈底")
        cb_tab_box.setChecked(int(cur.get("tab_sel_box") or 0) == 1)
        cb_tab_box.setToolTip("勾选后选中的页签套一圈底；不勾则用主题色淡底。")
        fp2.addRow("", cb_tab_box)

        cb_idle = QComboBox()
        for k, lbl in _th.ITEM_IDLE_MODES:
            cb_idle.addItem(lbl, k)
        ii = cb_idle.findData(cur.get("item_idle_mode") or "dark")
        cb_idle.setCurrentIndex(ii if ii >= 0 else 0)
        cb_idle.setToolTip(
            "列表项目【未选中】时的字色。\n"
            "深色主题下不会用纯黑 —— 那样对比度只有 1.4，等于看不见，\n"
            "会自动换成'暗一些但仍读得清'的灰。")
        fp2.addRow("列表常驻:", cb_idle)

        cb_bars = QComboBox()
        cb_bars.addItem("不画", 0)
        cb_bars.addItem("只在左侧", 1)
        cb_bars.addItem("左右各一条（推荐）", 2)
        # ★ 用 is not None 判断，否则用户选 0 会被 or 2 强制变成 2
        sb_val = cur.get("sel_bars")
        ib = cb_bars.findData(int(sb_val) if sb_val is not None else 1)
        cb_bars.setCurrentIndex(ib if ib >= 0 else 1)
        cb_bars.setToolTip("选中项两侧的竖杠。\n"
                           "竖杠是委托画上去的：Qt 样式表里给 item 写\n"
                           "border-left/right 渲染不稳定，容易被忽略。")
        fp2.addRow("选中竖杠:", cb_bars)

        cb_tip = QCheckBox("鼠标悬停时显示注释")
        cb_tip.setChecked(int(cur.get("hover_tip") or 0) == 1)
        cb_tip.setToolTip("悬停在服务器图标/行上，弹出目录、Java、\n"
                          "端口、存档名等完整信息。")
        fp2.addRow("", cb_tip)
        v.addWidget(gp2)
        v.addStretch(1)

        def result():
            fg = cur["fg"]
            bg = cur["bg"]
            # ★ 对比度保护：字体色和背景色太接近就自动把字体色推远。
            #   "白色"不单指 #ffffff，#f5f5f5 / #eeeeee 这类近白
            #   配浅底一样看不清 —— 所以按 WCAG 相对亮度算，
            #   不是简单比字符串。
            fg2, changed = _th.ensure_contrast(fg, bg)
            if changed:
                cur["fg"] = fg2
                if "fg" in picks:
                    picks["fg"][1]()
                result.adjusted = (fg, fg2)
            else:
                result.adjusted = None
            fam = (cb_fam.currentFont().family()
                   if _touched["font"]
                   else (cur.get("font_family") or ""))
            # ★ 系统里没有的字体名会让 DirectWrite 反复创建字体面失败
            #   （界面发涩、像卡住），这里直接不写。
            if fam and not _th.valid_font(fam):
                result.bad_font = fam
                fam = ""
            else:
                result.bad_font = None
            return {
                # 没动过字体下拉框就保持原值（空 = 系统默认）
                "font_family": fam,
                "font_size": sp_fs.value(),
                "fg": fg2, "bg": bg,
                "accent": cur["accent"],
                "bg_image": ed_img.text().strip(),
                "bg_mode": cb_mode.currentData() or "stretch",
                "bg_opacity": sl.value(),
                "font_bold": 1 if cb_bold.isChecked() else 0,
                # ---- 选中与悬停效果 ----
                "pcl": 1 if cb_pcl.isChecked() else 0,
                "tab_idle": (cb_tab_idle.currentData()
                             if cb_tab_idle.currentData() != "fg"
                             else (cur.get("fg") or PAL.WHITE)),
                "tab_sel_box": 1 if cb_tab_box.isChecked() else 0,
                "item_idle_mode": cb_idle.currentData() or "dark",
                "sel_bars": cb_bars.currentData() or 0,
                "hover_tip": 1 if cb_tip.isChecked() else 0,
            }
        result.adjusted = None
        result.bad_font = None
        w.result = result
        return w

    def _style_dlg(self, dlg):
        """给对话框套当前主题。

        ★ 为什么要显式套一层？
          1) app 级样式只在启动时设过一次，改完主题后
             新弹出来的窗口还是旧配色（甚至系统默认色）。
          2) Qt 的样式表类型选择器（QDialog{...}）对
             Python 里自定义的子类未必匹配得上，
             显式 setStyleSheet 是唯一稳的做法。
        """
        try:
            from .core import theme as _th
            _th.load()
            dlg.setStyleSheet(_th.stylesheet(None))
            # ★ 再兜一层调色板：Windows 的系统调色板 Window 是纯白，
            #   stylesheet 没覆盖到的容器会露出白底 + 白字。
            _th.apply_palette(dlg)
        except Exception:
            pass
        return dlg

    def _force_console_dark(self):
        """V2.0.1.1：锁死所有 Console 输出区深底。

        根因：QTextEdit 的可见区域是内部 viewport，主题样式表的
        QLineEdit,QTextEdit{background-color:浅色} 会作用到 viewport，
        而 out 自身的 QSS 管不到 viewport（真实渲染实测）。必须对
        viewport 直接 setStyleSheet + setPalette 才能锁住背景。

        ★ V2.0.2：背景色改为读「显示设置 → 控制台配色 → 背景」
          （默认 #101010）；默认文字色按背景自动对比调整。
        """
        try:
            from PySide6.QtGui import QPalette as _QP
            from PySide6.QtGui import QColor as _QC
            cc = self.disp.get("console_colors") or {}
            # ★ V2.0.3：默认/旧值跟随主题（和信息面板一致）
            th_fg, th_bg = Console._theme_colors()
            _bg = cc.get("bg") or th_bg
            if _bg.lower() in _CONSOLE_STALE_BG:
                _bg = th_bg
            _info = cc.get("info") or th_fg
            if _info.lower() in _CONSOLE_STALE_INFO:
                _info = th_fg
            try:
                from .core import theme as _th
                _info, _ = _th.ensure_contrast(_info, _bg, 4.5)
            except Exception:
                pass
            _cons = list(getattr(self, "consoles", {}).values())
            _g = getattr(self, "console", None)
            if _g is not None:
                _cons.append(_g)
            for _c in _cons:
                try:
                    if _c is None or not hasattr(_c, "out"):
                        continue
                    _o = _c.out
                    _vp = _o.viewport()
                    if _vp is not None:
                        _vp.setStyleSheet(
                            "QWidget{background:%s;color:%s;}"
                            % (_bg, _info))
                        _vp.setAutoFillBackground(True)
                        _pal = _vp.palette()
                        _pal.setColor(_QP.Base, _QC(_bg))
                        _pal.setColor(_QP.Text, _QC(_info))
                        _pal.setColor(_QP.Button, _QC(PAL.CONSOLE_BTN))
                        _pal.setColor(_QP.Highlight, _QC(PAL.CONSOLE_HIGHLIGHT))
                        _vp.setPalette(_pal)
                        _vp.update()
                    _o.update()
                except Exception:
                    pass
        except Exception:
            pass

    def _apply_theme(self):
        """把主题套到整个程序上。"""
        from .core import theme as _th
        _th.load()
        # ★ 字体走 QApplication.setFont —— stylesheet 里的
        #   font-family 对不少控件不生效，这一条一定生效。
        # ★ 顺序很重要：先定 QApplication 字体，再套 stylesheet。
        #   反过来（先 stylesheet 后 apply_font）时，取消粗体会失效：
        #   setStyleSheet 那一刻 app.font() 还是上一次的粗体，Qt 会按
        #   这个旧字体重新 polish，把粗体“钉”到控件上；之后再
        #   app.setFont(非粗体) 也改不动这些已被样式表覆盖的控件 ——
        #   表现就是“取消勾选粗体没反应”，要再点一次 OK 才恢复。
        _th.apply_font()
        css = _th.stylesheet("QMainWindow")
        self.setStyleSheet(css)
        # ★ 通用样式只算一次：以前在下面的对话框循环里每个窗口算一遍，
        #   打开着的对话框越多越慢（stylesheet() 每次都要拼几百行 CSS）。
        css_all = _th.stylesheet(None)
        # 同时刷新 app 级样式，让【之后】新建的窗口也用新主题
        try:
            QApplication.instance().setStyleSheet(css_all)
        except Exception:
            pass
        _th.apply_palette(self)
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QDialog) and w is not self:
                try:
                    w.setStyleSheet(css_all)
                except Exception:
                    pass
        # ★ 顶栏按钮是自绘的（悬停/按下动画），换主题后要重画
        for _b in self.findChildren(NavButton):
            try:
                _b.update()
            except Exception:
                pass
        # ★ 强制重画图标/表格。
        #   图标卡片上的文字（粗体）是委托自绘的，
        #   不主动 update 的话可能一直停在旧样子 ——
        #   表现就是"取消勾选粗体后没变化"。
        
        # ★ 清空委托里的主题缓存，让竖杠、颜色等立刻生效（不用切页面）
        try:
            if hasattr(self, "icon_delegate"):
                self.icon_delegate._th_cache["t"] = 0
            if hasattr(self, "row_delegate"):
                self.row_delegate._th_cache["t"] = 0
        except Exception:
            pass

        # ★ V2.0.1.1：apply_palette 会把主题浅色写进 palette，覆盖
        #   Console 输出区的深色。QTextEdit 可见区域是 viewport，
        #   必须对 viewport 直接 setStyleSheet + setPalette 锁深。
        self._force_console_dark()
            
        for _v in (getattr(self, "icon_view", None),
                   getattr(self, "table", None)):
            try:
                if _v is not None:
                    _v.viewport().update()
            except Exception:
                pass

    def do_sources(self):
        """下载源全局设置（镜像 / 官方源 / CF Key）+ 并发测速。

        ★ 全局入口：服务器管理界面与模组/插件浏览界面共用同一配置
          （sources.json），这里打开 SourceDialog 保存后立即并发测速，
          各源延迟显示在主界面日志面板。
        """
        try:
            from .core import sources as _src
            from .dialogs import SourceDialog, _themed
            dlg = _themed(SourceDialog(self))
            if not dlg.exec():
                return
            mirror, key = dlg.values()
            _src.set_mirror(mirror)
            _src.set_cf_key(key)
            ok_s, err_s = _src.save()
            if not ok_s:
                QMessageBox.warning(
                    self, "没保存上",
                    "下载源设置没能写入文件，重启后会回到之前的源。\n\n%s"
                    % err_s)
            self._log_console("[源] 已切换到：%s" % _src.describe())
            if key:
                self._log_console(
                    "[源] CurseForge API Key 已保存（本地 sources.json）")
            # ★ 并发测速：probe_all_latency 并行请求，显示各源延迟 ms
            self._log_console("[源] 正在并发测速 ...")
            res = _src.probe_all_latency(timeout=8)
            for name, okk, ms, why in res:
                lab = "MCIM 镜像" if name == "mcim" else "官方源"
                if okk:
                    self._log_console("[源] %s 可用（%d ms）" % (lab, ms))
                else:
                    self._log_console("[源] %s 不可用（%s）" % (lab, why))
            if not any(r[1] for r in res):
                QMessageBox.warning(
                    self, "都不通",
                    "镜像和官方源都请求失败了。\n\n已自动保持在官方源。")
            else:
                self._log_console("[源] 测速完成，当前源：%s"
                                  % _src.describe())
        except Exception as e:
            self._log_console("[错误] 下载源设置：%r" % e)

    def do_display(self):
        # ★ 去掉以前那个保底的 dlg = QDialog(self) ——
        #   它从来没被 exec/show 过，也不传给任何控件，纯泄漏。
        #   真正显示的是下面的 dlg2。
        v = QWidget()
        vv = QVBoxLayout(v)

        # ---- 视图模式 ----
        gv = QGroupBox("视图模式")
        fv = QFormLayout(gv)
        cb_view = QComboBox()
        cb_view.addItem("图标视图（一个个方块，可拖动排序）", "icon")
        cb_view.addItem("表格视图（原来的列表）", "table")
        iv = cb_view.findData(self.disp.get("view", "icon"))
        cb_view.setCurrentIndex(iv if iv >= 0 else 0)
        fv.addRow("", cb_view)
        vv.addWidget(gv)

        # ---- 图标上显示什么 ----
        gs = QGroupBox("图标上显示哪些信息（勾选的才显示）")
        vs = QVBoxLayout(gs)
        default_show = ["name", "version", "loader", "status"]
        cur_show = self.disp.get("show") or default_show
        SHOW_ITEMS = [
            ("name", "服务器名称", "文件夹/世界名，建议一直开着"),
            ("version", "MC 版本", "如 1.21.10"),
            ("loader", "加载器", "Fabric / Forge / Paper ..."),
            ("world", "存档名", "世界里那个名字"),
            ("regions", "Region 数", "地图大小"),
            ("status", "状态", "运行中 / 未初始化"),
        ]
        show_boxes = {}
        for key, label, desc in SHOW_ITEMS:
            cb = QCheckBox(label)
            cb.setChecked(key in cur_show)
            cb.setToolTip(desc)
            show_boxes[key] = cb
            vs.addWidget(cb)
        # 名称关掉的话图标就认不出了，给个提示
        lbl_warn = QLabel("")
        lbl_warn.setStyleSheet("color:%s;" % PAL.DANGER)
        lbl_warn.setWordWrap(True)
        vs.addWidget(lbl_warn)

        def _chk():
            lbl_warn.setText(
                "⚠ 全都不勾的话图标上什么都没有，认不出是哪个服。"
                if not any(c.isChecked() for c in show_boxes.values())
                else "")
        for cb in show_boxes.values():
            cb.stateChanged.connect(_chk)
        _chk()
        vv.addWidget(gs)

        f = QFormLayout()
        sp_font = QSpinBox()
        sp_font.setRange(8, 28)
        sp_font.setValue(int(self.disp.get("font", 13)))
        sp_font.setSuffix(" pt")
        f.addRow("列表字号:", sp_font)
        sz = QSpinBox()
        sz.setRange(70, 220)
        sz.setValue(int(self.disp.get("icon", 110)))
        sz.setSuffix(" px")
        f.addRow("图标大小:", sz)
        sp_row = QSpinBox()
        sp_row.setRange(20, 64)
        sp_row.setValue(int(self.disp.get("row", 30)))
        sp_row.setSuffix(" px")
        f.addRow("表格行高:", sp_row)
        vv.addLayout(f)          # ★ v 是 QWidget，不是布局 —— 以前写成
                                 #   v.addLayout 一点『显示设置』就崩

        # ★ V1.9.0：全库搜索图标显隐开关（默认关闭 —— 纯文字极速渲染）
        cb_icons = QCheckBox("显示模组图标（开启后异步加载，不阻塞列表）")
        cb_icons.setChecked(bool(self.disp.get("show_icons", False)))
        cb_icons.setToolTip(
            "默认关闭：全库搜索列表只显示文字，翻页最快；\n"
            "开启：图标异步加载，不阻塞列表渲染。")
        vv.addWidget(cb_icons)

        # ★ V2.0.1：停止时是否询问备份（默认询问；关闭则点停止直接停）
        cb_stop_ask = QCheckBox(
            "点「停止」时询问是否备份（关闭后直接停止，不弹窗）")
        cb_stop_ask.setChecked(bool(self.disp.get("stop_ask_backup", True)))
        cb_stop_ask.setToolTip(
            "默认开启：点停止会先问要不要备份存档；\n"
            "关闭：点停止直接优雅停机，不弹窗。\n"
            "需要备份请用工具栏的「停止并备份」。")
        vv.addWidget(cb_stop_ask)

        vv.addWidget(QLabel(
            "提示：排序在列表上方的下拉框里选；"
            "选『自定义顺序』后可以直接拖动图标调整。"))
        vv.addStretch(1)

        # ---- 外观页签 ----
        tabs = QTabWidget()
        tabs.addTab(self._wrap(v), "列表")
        ap = self._appearance_tab()
        tabs.addTab(ap, "外观 / 主题")

        # ★ V1.1.0：控制台配色页签
        cc = self._console_colors_tab()
        tabs.addTab(cc["widget"], "控制台配色")

        vb = QVBoxLayout()
        vb.addWidget(tabs, 1)
        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        vb.addWidget(btns)

        dlg2 = QDialog(self)
        dlg2.setWindowTitle("显示设置")
        dlg2.resize(520, 620)
        dlg2.setLayout(vb)
        # ★ 以前把 accepted/rejected 连到了上面那个"保底"的 dlg，
        #   而 exec() 的是 dlg2 —— 于是点 OK / Cancel 都等于
        #   accept 了一个从没显示过的窗口，dlg2.exec() 永远返回
        #   Rejected，所有设置都保存不了。必须连到 dlg2。
        btns.accepted.connect(dlg2.accept)
        btns.rejected.connect(dlg2.reject)

        # ★ V1.6.3：非模态打开 —— 主界面不再被"显示设置"死锁
        dlg2.setWindowModality(Qt.NonModal)

        def _apply_display_ok():
            self.disp["font"] = sp_font.value()
            self.disp["row"] = sp_row.value()
            self.disp["icon"] = sz.value()
            self.disp["view"] = cb_view.currentData() or "icon"
            self.disp["show"] = [k for k, cb in show_boxes.items()
                                 if cb.isChecked()]
            # ★ V1.8.10：全库搜索图标开关
            self.disp["show_icons"] = cb_icons.isChecked()
            # ★ V2.0.1：停止时是否询问备份
            self.disp["stop_ask_backup"] = cb_stop_ask.isChecked()
            # ★ V1.1.0：保存控制台配色
            self.disp["console_colors"] = cc["result"]()
            self.data["display"] = self.disp
            # ★ 主题保存失败不能连累其它设置 —— 以前这里 theme 未导入，
            #   一保存就 NameError，导致字体/图标大小/视图全都存不下来。
            # ★ 保存失败必须让用户知道。
            #   以前 theme.save 静默吞错 + 这里不看返回值：
            #   界面照样弹"已应用"，但磁盘上没写进去，
            #   重启后全部回到旧值 —— 就是"改了没用"。
            try:
                ok_t, err_t = theme.save(ap.result())
            except Exception as e:
                ok_t, err_t = (False, repr(e))
            if not ok_t:
                print("[主题] 保存失败: %s" % err_t)
                QMessageBox.warning(
                    self, "主题没保存上",
                    "外观设置没能写入文件，重启后会回到旧的样子。\n\n"
                    "原因：%s\n\n"
                    "其它设置（字体大小、图标、显示项）不受影响。"
                    % err_t)
            # 对比度/字体无效的提示要放在这里，不能闷声改掉
            adj = getattr(ap.result, "adjusted", None)
            badf = getattr(ap.result, "bad_font", None)
            if adj or badf:
                msg = []
                if adj:
                    msg.append(
                        "字体色 %s 和背景色太接近（看不清），"
                        "已自动调成 %s" % (adj[0], adj[1]))
                if badf:
                    msg.append("字体 %s 本机没有，已忽略" % badf)
                QMessageBox.information(self, "已自动调整", "\n".join(msg))
            self._save_data()
            self._apply_theme()
            self._switch_view(self.disp["view"])
            self._apply_display()
            self._fill_views()
            # ★ V1.1.0：把新配色广播给所有控制台（含每服控制台）
            try:
                cc_colors = self.disp.get("console_colors") or None
                for _c in [self.console] + list(self.consoles.values()):
                    try:
                        _c.set_colors(cc_colors)
                    except Exception:
                        pass
            except Exception:
                pass
            from .core import theme as _t2
            _t2.load()
            self.statusBar().showMessage(
                "已应用 — 字体 %s / %s，前景 %s，背景 %s，强调 %s"
                % (_t2.get("font_family") or "系统默认",
                   ("%dpt" % int(_t2.get("font_size") or 0))
                   if int(_t2.get("font_size") or 0) > 0 else "默认字号",
                   _t2.get("fg"), _t2.get("bg"), _t2.get("accent")),
                8000)
            print("[主题] 字体=%r 字号=%s 前景=%s 背景=%s 强调=%s"
                  % (_t2.get("font_family"), _t2.get("font_size"),
                     _t2.get("fg"), _t2.get("bg"), _t2.get("accent")))
            print("[主题] 配置文件: %s" % _t2.path())

        dlg2.accepted.connect(_apply_display_ok)
        # ★ V1.6.3：持有引用防 GC 回收（非模态窗口没人持有会被回收）
        self._dlg_refs = getattr(self, "_dlg_refs", [])
        self._dlg_refs.append(dlg2)

        def _drop_display(_r=0):
            try:
                self._dlg_refs.remove(dlg2)
            except ValueError:
                pass

        dlg2.finished.connect(_drop_display)
        dlg2.show()
