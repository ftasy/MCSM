# -*- coding: utf-8 -*-
"""服务器资料：模组、核心、诊断、数据读写、扫描与信息面板。

从 app.py 的 MainWindow 拆出，作为 mixin 混入主窗口。
"""

import os
import json

from PySide6.QtGui import QColor
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox, QTableWidgetItem
from .core import scanner, versions, builder, fsutil
from .dialogs import (
    ModDialog, DeleteServerDialog, DiagDialog, AnalyzeDialog, Worker, _track)
from . import updater
from .const import DATA_FILE


class LibraryMixin:
    """服务器资料：模组、核心、诊断、数据读写、扫描与信息面板。"""

    # ---------------- 模组 ----------------
    def do_mods(self):
        e = self._cur()
        if not e:
            QMessageBox.information(self, "提示", "先选中一个服务器")
            return
        self._style_dlg(ModDialog(e, self)).exec()


    # ---------------- 核心 ----------------
    def _warn_bad_levelname(self, e):
        """level-name 含非法字符会导致服务端启动崩溃，提前预警。"""
        sd = e.get("server_dir") or e["folder"]
        p = os.path.join(sd, "server.properties")
        if not os.path.isfile(p):
            return
        try:
            d = {}
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        d[k.strip()] = v
        except Exception:
            return
        ln = d.get("level-name")
        if not ln:
            return
        okd, why = builder.validate_level_name(ln)
        if okd:
            return
        fixed = builder.safe_level_name(ln)
        self.console.append(
            "[警告] level-name 有问题：%s" % why)
        self.console.append(
            "        当前值: %s" % ln)
        r = QMessageBox.warning(
            self, "level-name 会导致启动崩溃",
            "server.properties 里的 level-name 有问题：\n\n"
            "  %s\n\n"
            "当前值：%s\n\n"
            "MC 会拿它拼文件夹路径，含 : 这类字符会直接崩。\n\n"
            "是否改成安全的名字？\n  %s"
            % (why, ln, fixed),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if r != QMessageBox.Yes:
            return
        try:
            with open(p, "r", encoding="utf-8",
                      errors="replace") as f:
                lines = f.read().splitlines()
            out = []
            for line in lines:
                if line.strip().startswith("level-name="):
                    out.append("level-name=%s" % fixed)
                else:
                    out.append(line)
            fsutil.atomic_write_text(p, "\n".join(out) + "\n",
                                     encoding="utf-8", newline="\n",
                                     keep_backup=True)
            self.console.append(
                "[OK] level-name 已改为: %s" % fixed)
            self.do_scan()
        except Exception as ex:
            self.console.append("[失败] 改写 level-name: %r" % ex)


    # ---------------- 诊断 / 删除 ----------------
    def do_analyze(self, entry=None):
        """离线日志诊断。

        ★ V1.6.0：可选 entry —— 崩溃智能引导时直接对准崩掉的那台服，
          不依赖用户当前选中。
        """
        e = entry if entry is not None else self._cur()
        if not e:
            QMessageBox.information(self, "提示", "先选中一个服务器")
            return
        # 把本软件这个服的控制台输出也一起喂进去
        txt = self._console_text(e)
        dlg = self._style_dlg(AnalyzeDialog(
            e, all_entries=self.entries, console_text=txt, parent=self))
        # ★ V1.6.3：非模态 —— 分析日志时主界面不再死锁
        dlg.setWindowModality(Qt.NonModal)
        self._dlg_refs = getattr(self, "_dlg_refs", [])
        self._dlg_refs.append(dlg)

        def _drop_analyze(_r=0):
            try:
                self._dlg_refs.remove(dlg)
            except ValueError:
                pass

        dlg.finished.connect(_drop_analyze)
        dlg.show()

    # ---------------- V1.6.0：检查更新 ----------------
    def do_check_update(self):
        """后台检查 GitHub 最新版（Worker 线程，不卡 UI）。"""
        if getattr(self, "_checking_update", False):
            return
        self._checking_update = True
        self.statusBar().showMessage("正在检查更新...", 0)
        w = _track(self, Worker(updater.check_latest, {}, self))
        w.done.connect(self._on_update_done)
        w.start()

    def _on_update_done(self, ok, res):
        self._checking_update = False
        self.statusBar().showMessage("就绪", 3000)
        if not ok:
            self.statusBar().showMessage(
                "检查更新失败（网络异常或未配置仓库）", 5000)
            return
        tag, url, is_new = res
        if not is_new or not tag:
            QMessageBox.information(self, "检查更新",
                                    "当前已是最新版本")
            return
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("发现新版本")
        box.setText("发现新版本 %s，是否前往下载？" % tag)
        dl = box.addButton("前往下载", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)

        def _go(btn):
            if btn is dl and url:
                QDesktopServices.openUrl(QUrl(url))

        box.buttonClicked.connect(_go)
        box.exec()

    def _console_text(self, e):
        """取某个服务器自己的控制台全文。

        ★ 以前 do_diag 直接 self.console.toPlainText() ——
          那个是第 0 页「总输出」，装的是本软件自己的日志，
          不是服务器日志。于是规则诊断一直在拿软件日志去匹配
          「Failed to load function」「Address already in use」
          这类服务端报错 —— 永远匹配不上，等于白喂。
          do_analyze 反而取对了（用 self.consoles[key]）。
          这里抽出来统一，两处都走同一条路。
        """
        try:
            c = self.consoles.get(self._key(e))
            if c is None:
                return ""
            w = getattr(c, "out", None) or getattr(c, "text", None)
            if w is not None and hasattr(w, "toPlainText"):
                return w.toPlainText() or ""
        except Exception:
            pass
        return ""

    def do_diag(self):
        e = self._cur()
        if not e:
            QMessageBox.information(self, "提示", "先选中一个服务器")
            return
        txt = self._console_text(e)
        self._style_dlg(
            DiagDialog(e, console_text=txt, parent=self)).exec()

    def do_delete_server(self):
        e = self._cur()
        if not e:
            QMessageBox.information(self, "提示", "先选中一个服务器")
            return
        k = self._key(e)
        if k in self.procs:
            QMessageBox.warning(self, "还在运行",
                                "请先停止这个服务器再删除")
            return
        dlg = self._style_dlg(DeleteServerDialog(e, self))
        if dlg.exec():
            # 从列表移除
            self.root = self.ed_root.text().strip()
            self.do_scan()
            self.statusBar().showMessage("服务器已删除")

    # ---------------- 数据 ----------------
    def _data_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", DATA_FILE)

    def _load_data(self):
        try:
            p = self._data_path()
            if os.path.isfile(p):
                # 主文件损坏（半截/空）时自动退回 .bak，别让用户白丢列表
                d = fsutil.read_json_fallback(p)
                if d is None:
                    raise ValueError("servers.json 与 .bak 都无法解析")
                self.data = d
                if "display" in self.data:
                    self.disp.update(self.data["display"])
                if "servers" not in self.data:
                    self.data["servers"] = {}
        except Exception:
            pass

    def _save_data(self):
        """保存 servers.json。返回 (ok, err)。

        ★ 以前这里 except: pass —— 全项目最不该静默的地方。
          servers.json 装的是服务器列表、自定义排序、显示设置、
          最近开服时间。写不进去（目录只读、文件被占用、盘满）
          界面却毫无反应，你会以为排好的顺序已经记住了，
          重启一看全没了。
          现在写失败会在状态栏红字提示，并弹一次窗说明原因。
        """
        p = self._data_path()
        try:
            d0 = os.path.dirname(p)
            if d0:
                os.makedirs(d0, exist_ok=True)
            # ★ 原子写 + .bak：服务器列表绝不能被写坏
            fsutil.atomic_write_json(p, self.data, keep_backup=True)
            if getattr(self, "_save_failed", False):
                self._save_failed = False
            return (True, p)
        except Exception as e:
            err = "%s  (%s)" % (repr(e), p)
            print("[数据] 保存失败: %s" % err)
            try:
                self.statusBar().showMessage(
                    "⚠ 设置没保存上（%s），重启后会丢失" % repr(e), 15000)
            except Exception:
                pass
            # 只弹一次：_save_data 调用点很多，每次都弹会疯
            if not getattr(self, "_save_failed", False):
                self._save_failed = True
                try:
                    QMessageBox.warning(
                        self, "设置没保存上",
                        "服务器列表设置没能写入文件，重启后会丢失"
                        "（比如刚排好的顺序、显示设置）。\n\n"
                        "原因：%s\n\n"
                        "常见情况：文件夹只读、文件被别的程序占用、磁盘已满。"
                        % err)
                except Exception:
                    pass
            return (False, err)

    # ---------------- 扫描 ----------------
    def pick_and_scan(self):
        d = QFileDialog.getExistingDirectory(self, "选择总目录",
                                             self.root or "")
        if not d:
            return
        self.root = d
        self.ed_root.setText(d)
        self.data["root"] = d
        self._save_data()
        self.do_scan()

    def do_scan(self, then=None, quiet=False):
        """扫描总目录。放在后台线程跑，几十个服也不卡界面。

        then:  扫描完成后的回调（导入/删除后要选中新条目时用）。
        quiet: 不弹框（启动时自动扫描用；总目录不在了也不该卡启动）。
        """
        if not self.root or not os.path.isdir(self.root):
            if quiet:
                self.statusBar().showMessage(
                    "总目录不存在或未选择，请点『扫描』重新选择")
            else:
                QMessageBox.information(self, "提示", "请先选择总目录")
            return
        old = getattr(self, "_scan_worker", None)
        if old is not None and old.isRunning():
            # 已经在扫了：不重复开线程；等这次结束后再按【最新的 root】扫一次
            self._scan_then = then
            self._scan_again = True
            return
        self._scan_then = then
        self._scan_again = False
        root = self.root
        self._scan_root_used = root
        self.statusBar().showMessage("扫描中...")

        def job(on_log=None):
            return scanner.scan_root(root)

        w = _track(self, Worker(job, {}, self))
        self._scan_worker = w
        w.done.connect(self._on_scan_done)
        w.start()

    def _forget_worker(self, w):
        """线程跑完后从 self._threads 里摘掉，别越攒越多。"""
        lst = getattr(self, "_threads", None) or []
        try:
            lst.remove(w)
        except ValueError:
            pass

    def _on_scan_done(self, ok, res):
        w, self._scan_worker = getattr(self, "_scan_worker", None), None
        if w is not None:
            self._forget_worker(w)
        # 扫描期间 root 变过 / 又点了扫描：拿旧结果没意义，按新 root 再扫一次
        used = getattr(self, "_scan_root_used", None)
        stale = bool(self._scan_again) or bool(
            used and self.root
            and os.path.abspath(used) != os.path.abspath(self.root))
        if stale:
            self._scan_again = False
            cb = getattr(self, "_scan_then", None)
            self.do_scan(then=cb)
            return
        if not ok:
            self.statusBar().showMessage("扫描失败：%s" % res)
            self._log_console("扫描失败：%s" % res)
            return
        self.entries = res or []
        # ★ 右键重命名支持：扫描后把 servers.json 里的 display_name
        #   回填到条目上，图标和表格都显示用户改的名字
        for en in self.entries:
            saved = self.data.get("servers", {}).get(self._key(en), {})
            if saved.get("display_name"):
                en["display_name"] = saved["display_name"]
        self._fill_views()
        self.statusBar().showMessage(
            "扫描完成，共 %d 个" % len(self.entries))
        if self.sort_mode == "size":
            self._prefetch_sizes()  # ★ 扫描完若在按大小排序，后台补算
        cb, self._scan_then = getattr(self, "_scan_then", None), None
        if cb:
            try:
                cb()
            except Exception:
                pass

    def _update_draghint(self):
        """排序栏右侧的一行小字，告诉用户现在能不能拖。"""
        try:
            if getattr(self, "sort_mode", "custom") == "custom":
                what = "图标" if getattr(self, "view_mode", "icon") == "icon" \
                    else "行"
                self.lb_draghint.setText(
                    "自定义顺序：直接拖动%s就能调整" % what)
            else:
                self.lb_draghint.setText("拖一下会自动切到自定义顺序")
        except Exception:
            pass

    def _fill_views(self):
        """同时刷新图标视图和表格视图。

        ★ 为什么要有这个函数：
          以前各处分别调 _fill_icons() / _fill_table()，
          漏掉其中一个就成了"扫完一片空白"。
          do_scan() 就只调了 _fill_table()，
          而默认视图是图标 -> 用户看到的就是一个空面板。
          现在统一走这里，不会再漏。
        """
        self._fill_icons()
        self._fill_table()
        self._update_draghint()
        # 兜底：万一图标视图一个都没画出来（委托异常/尺寸算错），
        # 自动退回表格视图 —— 宁可样式朴素，也不能给用户一个空面板。
        try:
            if (getattr(self, "view_mode", "table") == "icon"
                    and self.icon_view.count() == 0
                    and self.table.rowCount() > 0):
                self._switch_view("table")
                self.statusBar().showMessage(
                    "图标视图渲染异常，已自动退回表格视图")
        except Exception:
            pass

    def _fill_table(self):
        self.table.setRowCount(0)
        for i, e in enumerate(self._view_entries()):
            r = self.table.rowCount()
            self.table.insertRow(r)
            # ★ 一次性算好这一行的悬停注释，所有 6 个单元格共享同一个
            #   字符串。以前内层循环每格都调一次 _tip_for(e) ——
            #   一行 6 次，几十行就几百次重复拼接；碰上端口缓存过期
            #   还会连带 6 次读盘。现在每行只算一次。
            tip = self._tip_for(e)
            vals = [
                e.get("name", ""),
                e.get("version") or "?",
                versions.LOADER_LABEL.get(e.get("loader"),
                                          e.get("loader") or "-"),
                e.get("world_name") or "-",
                str(e.get("regions") or 0),
                "未初始化" if e.get("notes") else "就绪",
            ]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 5 and v == "未初始化":
                    it.setForeground(QColor("#c62828"))
                if c == 0:
                    # 拖动排序后要按这个 key 存顺序
                    it.setData(Qt.UserRole, self._key(e))
                it.setToolTip(tip)   # 整行共享同一份，不再每格重算
                self.table.setItem(r, c, it)
            
            # ★ 把行高设置挪到列循环外面，这一行填完了统一设一次就行
            self.table.setRowHeight(r, int(self.disp.get("row", 30)))
        # 运行中的服要显示"运行中"（扫描不会清掉运行列表）
        self._refresh_status()
        self.table.selectRow(0)
        # 图标视图同步选中第一行
        try:
            if self.icon_view.count():
                self.icon_view.setCurrentRow(0)
        except Exception:
            pass

    def on_sel(self):
        """选中变了 -> 刷新右侧信息。

        ★ 以前的 bug：这里只读【表格】的 selectionModel，
          图标视图点一下 rows 是空的，直接 return，
          于是"选图标时右上角信息不跟着动"。
          现在按当前可见的视图取值。
        """
        e = None
        if self.view_mode == "icon":
            it = self.icon_view.currentItem()
            if it is not None:
                e = self._icon_entry(it)
            if e is None:
                for it2 in self.icon_view.selectedItems():
                    e = self._icon_entry(it2)
                    if e:
                        break
        else:
            rows = self.table.selectionModel().selectedRows()
            # ★ 同样按 _view_entries() 取，与 _fill_table 的填充顺序一致
            sorted_e = self._view_entries()
            if rows and rows[0].row() < len(sorted_e):
                e = sorted_e[rows[0].row()]
        if not e:
            return
        # 选中某个服时，自动切到它的控制台页签
        try:
            c = self.consoles.get(self._key(e))
            if c is not None:
                self.console_tabs.setCurrentIndex(
                    self.console_tabs.indexOf(c))
            self._refresh_actions()
        except Exception:
            pass
        need = versions.need_java(e.get("version")) if e.get("version") else None
        fmt = versions.pack_format(e.get("version")) if e.get("version") else None
        newmeta = versions.uses_new_packmeta(e.get("version")) \
            if e.get("version") else None
        self._fill_info_detail(e, need, fmt, newmeta)

        running = self._key(e) in self.procs
        self.act_stop.setEnabled(running)
        self.act_start.setEnabled(not running)
        self.act_float_console.setEnabled(running)

    def _fill_info_detail(self, e, need=None, fmt=None, newmeta=None):
        """信息面板：服务器详情视图（未运行时显示）。

        ★ V2.0.1.1：抽出来供 _update_info_monitor 停止后恢复用。
        """
        lines = [
            "<b>%s</b>" % (e.get("world_name")
                           or self._display_name(e)),
            "目录: %s" % e.get("folder"),
            "服务端目录: %s" % (e.get("server_dir") or "-"),
            "存档: %s" % (e.get("world_dir") or "-"),
            "版本: %s" % (e.get("version") or "未知"),
            "加载器: %s" % (e.get("loader") or "未安装"),
            "目录布局: %s" % e.get("layout"),
        ]
        if need:
            lines.append("需要 Java: <b>%d</b>" % need)
        if fmt:
            lines.append("数据包格式: %d（%s）" % (
                fmt,
                "新写法 min/max_format" if newmeta else "旧写法 pack_format"))
        if e.get("seed") is not None:
            lines.append("种子: %s" % e["seed"])
        if e.get("difficulty"):
            lines.append("难度: %s / 模式: %s" % (e["difficulty"],
                                                  e.get("gamemode")))
        if e.get("notes"):
            lines.append("<font color='#c62828'>提示: %s</font>"
                         % "；".join(e["notes"]))
        self.info.setHtml("<br>".join(lines))

    def _update_info_monitor(self):
        """★ V2.0.1.1：信息面板监测视图。

        当前选中的服务器在运行时，把"信息"面板替换成实时监测
        （在线/延迟/CPU/内存）；停止或停止并备份后自动恢复详情视图。
        """
        try:
            e = self._cur()
            if not e:
                return
            k = self._key(e)
            if k not in self.procs:
                self._fill_info_detail(e)
                return
            txt = self._ping_text.get(k) or "运行中"
            cpu, mem = getattr(self, "_mon_extra", {}).get(k, (None, None))
            name = e.get("world_name") or self._display_name(e)
            lines = ["<b>%s</b>" % name,
                     "<font color='#2e7d32'>● 运行中</font>",
                     txt]
            if cpu is not None:
                lines.append("CPU: <b>%.0f%%</b>" % cpu)
            if mem is not None:
                lines.append("内存: <b>%.0f MB</b>" % mem)
            self.info.setHtml("<br>".join(lines))
        except Exception:
            pass

    def _display_name(self, e):
        """界面显示名：优先用户自定义重命名（display_name），否则原名。"""
        return e.get("display_name") or e.get("name") or ""

    def _key(self, e):
        return e.get("folder", "")

    def _cur(self):
        # 图标视图优先（当前可见的那个）
        try:
            if self.icon_view.isVisible():
                its = self.icon_view.selectedItems()
                if its:
                    e = self._icon_entry(its[0])
                    if e is not None:
                        return e
                # ★ 加一层兜底：如果由于某种原因 selectedItems 为空，
                #   但焦点在某个图标上（currentItem），也把它算作选中。
                it = self.icon_view.currentItem()
                if it is not None:
                    e = self._icon_entry(it)
                    if e is not None:
                        return e
        except Exception:
            pass
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        # ★ 必须按 _view_entries() 取：表格行号是排序 + 筛选后填的，
        #   直接用 self.entries 会在排序非 custom 或开着筛选时
        #   拿错服务器 —— 启动/停止/配置/删除都会作用到别的服。
        sorted_e = self._view_entries()
        if rows[0].row() < len(sorted_e):
            return sorted_e[rows[0].row()]
        return None
