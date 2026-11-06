# -*- coding: utf-8 -*-
"""单服操作：改名 / 换图标、备份、导入（拖放 / 地图 / 整合包）。

从 app.py 的 MainWindow 拆出，作为 mixin 混入主窗口。
"""

import os
import json
import shutil
import traceback

from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt, QProcess, QTimer
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox
from .core import migrate, backup
from .dialogs import ServerConfigDialog, Worker, _track


class ServerOpsMixin:
    """单服操作：改名 / 换图标、备份、导入（拖放 / 地图 / 整合包）。"""

    # ---------------- 每服配置 ----------------
    def _saved(self, e):
        return self.data.get("servers", {}).get(self._key(e), {})

    def do_edit_icon(self):
        """给单个服务器换图标（右键『设置服务器图标』）。

        图片会复制一份进服务器目录，存成 manager_icon.png：
          · 不叫 server-icon.png，避免把服务器在游戏里的图标也改了
          · 复制而不是只记路径 —— 原图删了/移动了图标不会失效
        """
        e = self._cur_entry()
        if not e:
            QMessageBox.information(self, "提示", "先选中一个服务器")
            return
        from PySide6.QtWidgets import QFileDialog
        p, _ = QFileDialog.getOpenFileName(
            self, "选一张图片做图标", "",
            "图片 (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.ico)")
        if not p:
            return
        from PySide6.QtGui import QPixmap
        pm = QPixmap(p)
        if pm.isNull():
            QMessageBox.warning(self, "失败", "这个文件不是能识别的图片")
            return
        base = e.get("server_dir") or e.get("folder") or ""
        if not base or not os.path.isdir(base):
            QMessageBox.warning(self, "失败", "服务器目录不存在")
            return
        dst = os.path.join(base, "manager_icon.png")
        try:
            pm.scaled(128, 128, Qt.KeepAspectRatio,
                      Qt.SmoothTransformation).save(dst, "PNG")
        except Exception as ex:
            QMessageBox.warning(self, "失败", "保存图标失败: %r" % ex)
            return
        k = self._key(e)
        self.data.setdefault("servers", {}).setdefault(k, {})["icon"] = dst
        self._save_data()
        self._fill_views()
        self.statusBar().showMessage(
            "已设置 %s 的图标" % (e.get("name") or ""))

    def do_rename_server(self):
        """右键重命名：给服务器起个好记的名字（不碰文件夹名）。

        存进 servers.json 的 display_name；图标视图和表格视图
        扫描后都会用这个字段覆盖显示（见 _display_name）。
        """
        e = self._cur_entry()
        if not e:
            QMessageBox.information(self, "提示", "先选中一个服务器")
            return
        old = e.get("display_name") or e.get("name") or ""
        new, ok = QInputDialog.getText(
            self, "重命名服务器",
            "给这个服务器起个好记的名字（不会改文件夹名）：",
            text=old)
        if not ok:
            return
        new = (new or "").strip()
        if not new:
            QMessageBox.warning(self, "名字不能为空", "请输入一个名字。")
            return
        e["display_name"] = new
        k = self._key(e)
        self.data.setdefault("servers", {}).setdefault(k, {})[
            "display_name"] = new
        self._save_data()
        self._fill_views()
        self.statusBar().showMessage("已重命名为：%s" % new, 4000)

    def do_clear_icon(self):
        """清掉自定义图标，回到自动识别（存档 icon.png 或首字母块）。"""
        e = self._cur_entry()
        if not e:
            QMessageBox.information(self, "提示", "先选中一个服务器")
            return
        k = self._key(e)
        sv = self.data.get("servers", {}).get(k)
        if not sv or not sv.get("icon"):
            QMessageBox.information(self, "提示", "这个服务器没有自定义图标")
            return
        sv.pop("icon", None)
        try:
            old = sv.get("_icon_path")
        except Exception:
            old = None
        self._save_data()
        self._fill_views()
        self.statusBar().showMessage("已清除自定义图标，恢复自动识别")

    # ★ V1.6.0：启动流程提前中止时恢复按钮/状态栏
    def _restore_start_btn(self, e=None):
        try:
            self.act_start.setEnabled(True)
        except Exception:
            pass
        if e is not None:
            self.statusBar().showMessage("就绪", 3000)

    # ★ V1.6.0：双击卡片/行 = 未运行就启动，运行中就停止
    #   （服务器配置改从右键菜单进）
    def _double_click_icon(self, item):
        key = (item.data(Qt.UserRole) or "") if item is not None else ""
        for en in self.entries:
            if self._key(en) == key:
                self._toggle_run(en)
                return

    def _double_click_row(self, idx):
        try:
            row = idx.row()
            en = self._view_entries()[row]
        except Exception:
            return
        self._toggle_run(en)

    def _toggle_run(self, e):
        # ★ V2.3.3：双击 = 只操作这一台。
        #   传 entry 明确目标，否则多选状态下会把选中的全部一起启停
        #   （Ctrl + 双击时很容易误触发）。
        k = self._key(e)
        if k in self.procs:
            self.do_stop(entry=e)
        else:
            self.do_start(entry=e)

    # ★ V1.6.0：崩溃/启动失败后的非模态智能引导
    def _ask_crash_diag(self, key=None):
        """弹非模态 QMessageBox：要不要打开『日志分析』找原因。"""
        try:
            en = None
            if key is not None:
                for x in self.entries:
                    if self._key(x) == key:
                        en = x
                        break
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("服务器异常")
            box.setText("检测到服务器异常退出，是否为您自动打开"
                        "『日志分析』寻找原因？")
            yes = box.addButton("是，打开日志分析", QMessageBox.AcceptRole)
            no = box.addButton("否", QMessageBox.RejectRole)
            box.setAttribute(Qt.WA_DeleteOnClose)

            def _on(btn):
                try:
                    if btn is yes:
                        self.do_analyze(en)
                finally:
                    try:
                        box.close()
                    except Exception:
                        pass

            box.buttonClicked.connect(_on)
            box.show()  # 非模态：不阻塞主界面，其它服务器还能操作
        except Exception:
            pass

    def do_config(self):
        e = self._cur()
        if not e:
            QMessageBox.information(self, "提示", "先选中一个服务器")
            return
        # ★ V1.6.2：外层 try/except —— 防止后台静默崩溃导致窗体出不来
        try:
            saved = self._saved(e)
            dlg = self._style_dlg(
                ServerConfigDialog(e, saved=saved, parent=self))
        except Exception as ex:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self, "打开服务端配置失败",
                "程序在创建配置窗口时遇到了错误：\n\n%s\n\n"
                "详细追踪：\n%s" % (ex, traceback.format_exc()[-500:]))
            return

        # ★ V1.6.2：非模态打开 —— 用户改配置时主界面仍可操作
        dlg.setWindowModality(Qt.NonModal)
        self._dlg_refs = getattr(self, "_dlg_refs", [])
        self._dlg_refs.append(dlg)

        def _on_config_done():
            k = self._key(e)
            # ★ Bug 3 修复：merge，不再整条覆盖（保留 icon、last_start 等）
            sv = self.data.setdefault("servers", {}).setdefault(k, {})
            sv.update(dlg.result_info)
            self._save_data()
            # ★ V1.7.0-2：保存后刷新并按 server_dir 精准回显选中，
            #   绝不再按行号 setCurrentRow（排序变化会导致选中串台）
            self.do_scan(then=lambda: self._select_entry_by_dir(e))
            self.statusBar().showMessage("已保存 %s" % e.get("name"))
            try:
                self._dlg_refs.remove(dlg)
            except ValueError:
                pass

        dlg.finished.connect(lambda _r: _on_config_done())
        dlg.show()

    def _select_entry_by_dir(self, e):
        """★ V1.7.0-2：按服务器绝对路径在刷新后的列表里精准定位并选中。

        兼容图标视图和表格视图；找不到就保持现状（不强制跳到第一行）。
        ★ v2.3.2：目标被搜索框筛掉时自动清空筛选再找一次 ——
          否则导入/初始化完成后"列表里什么都没选中"，
          用户会以为操作失败了。
        """
        target = os.path.abspath(
            e.get("server_dir") or e.get("folder") or "")
        for attempt in (0, 1):
            try:
                for i, en in enumerate(self._view_entries()):
                    d = os.path.abspath(
                        en.get("server_dir") or en.get("folder") or "")
                    if d != target:
                        continue
                    self.table.selectRow(i)
                    if self.view_mode == "icon" \
                            and self.icon_view.count() > i:
                        self.icon_view.setCurrentRow(i)
                    self.on_sel()
                    return True
            except Exception:
                return False
            if attempt == 0 and (getattr(self, "filter_text", "") or "").strip():
                ed = getattr(self, "ed_filter", None)
                if ed is not None:
                    ed.clear()          # 触发 _on_filter_changed 重填视图
                    # ★ V2.5.0：搜索框有 160ms 防抖，光 clear() 会让
                    #   重填晚到、把下面这次定位选中又冲掉。程序化
                    #   清空必须立刻生效，并取消挂着的定时器。
                    try:
                        t = getattr(self, "_filter_timer", None)
                        if t is not None:
                            t.stop()
                        self._apply_filter_now()
                    except Exception:
                        pass
                else:
                    self.filter_text = ""
                    self._fill_views()
                continue
            return False
        return False


    # ---------------- 备份 ----------------
    def do_stop_backup(self, k=None, e=None, proc=None):
        """停止服务器，等它退出后自动备份存档。

        ★ V1.1.0：支持从 do_stop 传入 (k, e, proc)，
          不传则按原来的方式取当前选中服务器。
        """
        if e is None:
            e = self._cur()
        if not e:
            return
        if k is None:
            k = self._key(e)
        if proc is None:
            proc = self.procs.get(k)
        if not proc:
            QMessageBox.information(self, "提示", "这个服没在运行")
            return
        
        # ★ 标记为主动停止，防止退出时触发自动重启
        self._is_stopping[k] = True
        # ★ 第二处修改：按服的 key 记录备份任务
        self.pending_backups[k] = e
        
        self._log_console("=== 正在停止，停止后将自动备份 ===")
        try:
            proc.write(b"stop\n")
        except Exception:
            pass

        def safe_kill():
            try:
                # ★ 多开修复：进程已经在超时前正常退出了（对象被销毁/
                #   已 NotRunning），就别再 9 秒后强杀一次，否则会报错
                if proc.state() == QProcess.NotRunning:
                    return
                self._kill(proc)
            except RuntimeError:
                pass  # 对象已销毁，安全忽略
        QTimer.singleShot(9000, safe_kill)

    def _after_stop_backup(self, e):
        """服务器退出后执行备份（放入后台线程，防止卡死UI）。"""
        k = self._key(e)      # ★ 修：on_done 里要用 k 定位控制台页签
        sd = e.get("server_dir") or e["folder"]
        worlds = backup.find_worlds(sd)
        if not worlds:
            self._log_console("[备份] 没找到存档，跳过")
            return
        wd = worlds[0]
        self._log_console("[备份] 开始后台备份存档: %s，请稍候..." % wd["rel"])
        # ★ V1.1.0：备份格式跟随服务器配置
        sv = self._saved(e)
        compress = (sv.get("backup_format", "folder") == "zip")
        
        # 把备份操作放到后台 Worker 线程
        def job(on_log=None):
            return backup.backup_world(wd["path"], sd,
                                       compress=compress, on_log=on_log)

        w = _track(self, Worker(job, {}, self))
        w.log.connect(lambda m: self._log_console("[备份] " + m))
        
        # 备份完成后的回调
        def on_done(ok, res):
            # ★ V2.0.1：完成消息同时写进该服务器的控制台页签
            c = self.consoles.get(k)
            if not ok:
                self._log_console("[备份] 失败: %r" % res)
                self._log_console("[备份] 备份未完成 —— 但服务器已经关闭。")
                self.statusBar().showMessage("备份失败（服务器已关闭）", 6000)
                if c is not None:
                    c.append("[备份] 失败：备份未完成，但服务器已关闭。")
                return

            r = res
            self._log_console("[备份] 完成: %s (%.1f MB)" % (os.path.basename(r["path"]), r["size_mb"]))
            self._log_console("[备份] 备份已完成。")
            self._log_console("=== 服务器已关闭 ===")
            self.statusBar().showMessage("备份已完成，服务器已关闭", 6000)
            if c is not None:
                c.append("=== 备份完成: %s (%.1f MB) ==="
                         % (os.path.basename(r["path"]), r["size_mb"]))
                c.append("=== 服务器已关闭 ===")
            # ★ V2.0.1：非模态弹窗，明确提示备份已完成（置顶，防被遮挡）
            try:
                from PySide6.QtWidgets import QMessageBox
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Information)
                box.setWindowTitle("备份完成")
                box.setText("服务器「%s」已停止，备份完成。\n\n"
                            "%s（%.1f MB）"
                            % (e.get("name", ""),
                               os.path.basename(r["path"]),
                               r["size_mb"]))
                box.setStandardButtons(QMessageBox.Ok)
                box.setWindowModality(Qt.NonModal)
                box.setWindowFlag(Qt.WindowStaysOnTopHint, True)
                box.show()
            except Exception:
                pass
            
        def on_done_and_cleanup(ok, res):
            self._forget_worker(w)  # ★ 清理线程对象
            on_done(ok, res)
            
        w.done.connect(on_done_and_cleanup)
        w.start()

    def _do_auto_backup(self, k, e):
        """定时备份的执行逻辑。"""
        if k not in self.procs:
            return
        sd = e.get("server_dir") or e["folder"]
        worlds = backup.find_worlds(sd)
        if not worlds:
            return
        wd = worlds[0]
        self._log_console(f"[备份] 触发定时备份: {wd['rel']} ...")

        # ★ V2.0.12：服务器运行时直接复制会拿到写了一半的区块文件
        #   （这就是"服务器运行时备份复制不了内容"的根因）。
        #   先通过控制台发 save-all flush（原版 / Fabric / Paper 均支持），
        #   把内存里的存档先刷进磁盘，再开始复制。
        try:
            proc = self.procs.get(k)
            if proc is not None and proc.state() == QProcess.Running:
                proc.write(b"save-all flush\n")
                self._log_console("[备份] 已发送 save-all flush，等待写入磁盘 ...")
        except Exception:
            pass

        # 把备份放到后台，且使用 5 个循环覆盖的命名规则以防占满空间
        def job(on_log=None):
            import time as _t
            _t.sleep(2.5)  # 给 save-all flush 落盘时间（后台线程，不卡界面）
            return backup.backup_world(wd["path"], sd, note="auto",
                                       compress=True, on_log=on_log)
                                       
        w = _track(self, Worker(job, {}, self))
        w.log.connect(lambda m: self._log_console("[备份] " + m))
        
        def _on_auto_bk_done(ok, res):
            self._forget_worker(w)  # ★ 清理线程对象，防止长年挂机内存泄漏
            self._log_console(
                f"[备份] 定时备份完成" if ok else f"[备份] 定时备份失败: {res}")
                
        w.done.connect(_on_auto_bk_done)
        w.start()

    def _try_restart(self, key, entry):
        """10 秒后尝试自动重启。

        ★ 不碰 UI：以前为了"选中正确行"会强制 selectRow，
          用户正在操作 B 服时 A 崩溃 10 秒后自动重启，
          会把焦点从 B 抢到 A。现在直接把 entry 传给 do_start。
        """
        if key in self.procs:
            return  # 已经有人手动开了
        self._log_console(f"=== 正在自动重启 {entry.get('name')} ===")
        self.do_start(auto_restart=True, entry=entry)

    def _log_console(self, msg):
        self.console.append(msg)

    # ---------------- 导入 ----------------
    # ---------------- 拖放导入 ----------------
    def dragEnterEvent(self, e):
        """外部拖东西进来：文件夹 / zip 就接，其它不接。"""
        try:
            if e.mimeData().hasUrls():
                e.acceptProposedAction()
                return
        except Exception:
            pass
        return super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        try:
            if e.mimeData().hasUrls():
                e.acceptProposedAction()
                return
        except Exception:
            pass
        return super().dragMoveEvent(e)

    def dropEvent(self, e):
        """把拖进来的地图文件夹 / 压缩包交给导入。"""
        try:
            urls = e.mimeData().urls()
        except Exception:
            return super().dropEvent(e)
        paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
        paths = [p for p in paths if p]
        if not paths:
            return super().dropEvent(e)
        e.acceptProposedAction()
        self._import_dropped(paths)

    def _import_dropped(self, paths):
        """处理拖进来的东西（统一走 migrate.plan_import，避免结构错乱）。

        ★ V0.4.0：zip 会先检测是不是整合包（Modrinth/CurseForge），
          是就走整合包导入；剩下的才当地图导入。
        """
        if not self.root:
            QMessageBox.information(self, "提示", "请先选择总目录")
            return
        root = self.root
        self.statusBar().showMessage("导入中 ...")

        # 整合包分流（本地 zip 读取，很快）
        rest = []
        for p in paths:
            if os.path.isfile(p) and p.lower().endswith(".zip"):
                try:
                    from .core import modpack as _mp
                    if _mp.detect_kind(p):
                        self._import_modpack_path(p)
                        continue
                except Exception:
                    pass
            rest.append(p)
        if not rest:
            return
        paths = rest

        def job(on_log=None):
            import tempfile
            ok_n = 0
            msgs = []
            existed = []  # ★ V1.9.6：记录"已在列表里"的服务器（用于引导）
            for p in paths:
                base = os.path.basename(os.path.normpath(p)) or p
                src_dir = p
                
                # 如果是 zip，先解压到临时目录（★ V1.4.0：用系统 tar 提速）
                is_tmp = False
                if os.path.isfile(p) and p.lower().endswith(".zip"):
                    try:
                        src_dir = tempfile.mkdtemp(prefix="mcs_import_")
                        is_tmp = True
                        from .core import modpack as _mp
                        _mp._fast_extract(p, src_dir)
                    except Exception as ex:
                        msgs.append("[失败] %s（解压失败：%s）" % (base, str(ex)[:40]))
                        continue
                elif not os.path.isdir(p):
                    msgs.append("[跳过] %s（不是文件夹也不是 zip）" % base)
                    continue
                
                try:
                    plan = migrate.plan_import(src_dir, root)
                    if plan["kind"] == "none":
                        msgs.append("[跳过] %s（没找到 level.dat）" % base)
                        continue
                    if os.path.exists(plan["server_dir"]):
                        msgs.append("[跳过] %s（已在列表）" % plan["server_name"])
                        existed.append(dict(plan))
                        continue
                    
                    ok, msg = migrate.do_import(plan, copy=True, on_log=on_log)
                    if ok:
                        ok_n += 1
                        msgs.append("[OK] %s -> %s" % (base, plan["server_name"]))
                    else:
                        msgs.append("[失败] %s（%s）" % (base, msg))
                finally:
                    if is_tmp:
                        import shutil
                        shutil.rmtree(src_dir, ignore_errors=True)
            return ok_n, msgs, existed

        w = _track(self, Worker(job, {}, self))
        self._import_worker = w
        w.log.connect(self._log_console)
        w.done.connect(self._after_drop_import)
        w.start()

    # (废弃的 _import_one 已删除)

    def _after_drop_import(self, ok, res):
        w, self._import_worker = getattr(self, "_import_worker", None), None
        if w is not None:
            self._forget_worker(w)
        if not ok:
            self.statusBar().showMessage("导入失败")
            QMessageBox.warning(self, "拖放导入", "导入失败：%s" % res)
            return
        ok_n, msgs, existed = res
        txt = "\n".join(msgs)
        self._log_console("=== 拖放导入 ===\n" + txt)

        if ok_n:
            self.do_scan()
            QMessageBox.information(
                self, "拖放导入",
                txt + "\n\n已导入 %d 个。" % ok_n)
            return

        # ★ V1.9.6：拖进来的东西已在列表里 → 不再只报"跳过"。
        #   直接选中它，并问要不要现在就启动（用户拖进来多半是想运行）。
        if existed:
            plan = existed[0]
            entry = None
            try:
                tdir = os.path.abspath(plan.get("server_dir") or "")
                for en in self.entries:
                    if os.path.abspath(
                            en.get("server_dir") or en.get("folder")
                            or "") == tdir:
                        entry = en
                        break
            except Exception:
                entry = None
            target = entry or dict(plan)
            self._select_entry_by_dir(target)
            self.statusBar().showMessage(
                "『%s』已在列表中，可直接启动" % plan["server_name"], 4000)
            r = QMessageBox.question(
                self, "已在列表中",
                "『%s』已经在你的服务器列表里，"
                "不用重新导入。\n\n"
                "已经帮你选中它了 —— 要现在直接启动吗？\n\n"
                "（选『否』就停留在列表，可以先用『服务器配置』"
                "看看它）"
                % plan["server_name"],
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if r == QMessageBox.Yes:
                self.do_start(entry=target)
            return

        QMessageBox.warning(self, "拖放导入失败",
                            "没有成功导入任何地图，请检查：\n" + txt)
        QMessageBox.information(
            self, "拖放导入",
            "没导入成功。可以改用工具栏『导入地图』。")

    def do_import(self):
        """导入地图：建「版本-地图名」文件夹，地图放进去。

        目标结构：
            总目录/1.21.3-Frozen-Crag/
                ├── Frozen-Crag/     <- 世界内容
                └── (核心稍后装在这层)
        """
        if not self.root:
            QMessageBox.information(self, "提示", "请先选择总目录")
            return
        d = QFileDialog.getExistingDirectory(
            self, "选择要导入的地图文件夹", self.root)
        if not d:
            return

        name = os.path.basename(d.rstrip("\\/"))
        try:
            plan = migrate.plan_import(d, self.root)
        except Exception as ex:
            QMessageBox.critical(self, "识别失败", repr(ex))
            return

        if plan["kind"] == "none":
            QMessageBox.warning(
                self, "不像 MC 世界",
                "在这个文件夹里没找到 level.dat：\n  %s\n\n"
                "请选包含 level.dat 的世界文件夹，"
                "或包含它的父文件夹。" % d)
            return

        if os.path.exists(plan["server_dir"]):
            QMessageBox.warning(
                self, "已存在",
                "总目录下已经有：\n  %s" % plan["server_name"])
            return

        r = QMessageBox.question(
            self, "确认导入",
            "源文件夹: %s\n\n"
            "识别结果：\n"
            "  版本: %s\n"
            "  地图: %s\n"
            "  世界: %s 个 region\n\n"
            "将创建：\n"
            "  %s/\n"
            "    └── %s/   <- 地图内容放这里\n\n"
            "（核心和配置文件之后会装在外层，"
            "不再和地图文件混在一起）\n\n继续？"
            % (name,
               plan["version"] or "未知",
               plan["world_name"] or "-",
               self._region_count(plan["world_src"]),
               plan["server_name"], plan["world_subdir"]))
        if r != QMessageBox.Yes:
            return

        # ★ 复制大世界可能要几十秒，放后台线程，别冻住界面
        self.statusBar().showMessage("导入中 ...")

        def job(on_log=None):
            return migrate.do_import(plan, copy=True, on_log=on_log)

        w = _track(self, Worker(job, {}, self))
        self._import_worker = w
        w.log.connect(self._log_console)
        w.done.connect(lambda ok, res: self._after_import(ok, res, plan))
        w.start()

    def _after_import(self, ok, res, plan):
        w, self._import_worker = getattr(self, "_import_worker", None), None
        if w is not None:
            self._forget_worker(w)
        if not ok:
            self.statusBar().showMessage("导入失败")
            QMessageBox.critical(self, "失败", str(res))
            return
        okk, msg = res
        if not okk:
            self.statusBar().showMessage("导入失败")
            QMessageBox.warning(self, "导入失败", msg)
            return
        self.statusBar().showMessage("导入完成")

        def select_new():
            # 选中新导入的那个（兼容图标视图和表格视图）
            # ★ 统一走 _select_entry_by_dir：它按视图实际顺序
            #   （_view_entries）定位行，被筛选挡住时会自动清空筛选。
            self._select_entry_by_dir({"server_dir": plan["server_dir"]})
            QMessageBox.information(
                self, "完成",
                "已导入为：%s\n\n"
                "结构：\n"
                "  %s/\n"
                "    └── %s/\n\n"
                "接下来选中它，点『初始化服务端』装核心。\n"
                "核心会装在外层，level-name 会自动指向 %s。"
                % (plan["server_name"], plan["server_name"],
                   plan["world_subdir"], plan["world_subdir"]))

    # ---------------- V0.4.0：整合包一键导入 ----------------
    def do_import_modpack(self):
        """选一个整合包 zip（Modrinth / CurseForge）导入。"""
        if not self.root:
            QMessageBox.information(self, "提示", "请先选择总目录")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择整合包 zip",
            "", "整合包 (*.zip);;所有文件 (*)")
        if not path:
            return
        self._import_modpack_path(path)

    def _import_modpack_path(self, path):
        """把整合包 zip 交给后台 Worker 导入（网络请求不进主线程）。"""
        root = self.root
        self.statusBar().showMessage("正在导入整合包 ...")
        self._forget_worker(getattr(self, "_modpack_worker", None))

        def job(on_log=None):
            from .core import modpack as _mp
            return _mp.install_modpack(path, root, on_log=on_log)

        w = _track(self, Worker(job, {}, self))
        self._modpack_worker = w
        w.log.connect(lambda m: self.statusBar().showMessage(str(m)))
        w.done.connect(self._modpack_done)
        w.start()

    def _modpack_done(self, ok, res):
        w, self._modpack_worker = (
            getattr(self, "_modpack_worker", None), None)
        if w is not None:
            self._forget_worker(w)
        if not ok:
            self.statusBar().showMessage("整合包导入失败")
            QMessageBox.critical(self, "导入整合包失败",
                                 "导入失败：\n%s" % str(res)[:300])
            return
        sd = res.get("server_dir") or ""
        fail = res.get("mods_fail") or []
        ok_n = len(res.get("mods_ok") or [])
        msg = ("整合包已导入：%s\n\n"
               "目录: %s\n"
               "MC %s · %s\n"
               "模组：成功 %d 个" % (
                   res.get("name"), sd,
                   res.get("mc_version"), res.get("loader"), ok_n))
        if fail:
            msg += "\n\n失败 %d 个：\n%s" % (
                len(fail), "\n".join("· %s" % f for f in fail[:15]))
        msg += "\n\n接下来选中它，点『初始化服务端』完善配置，然后就能启动。"
        QMessageBox.information(self, "整合包导入完成", msg)
        self.statusBar().showMessage("整合包导入完成")

        def select_me():
            self._select_entry_by_dir({"server_dir": sd})

        self.do_scan(then=select_me)

    def _region_count(self, world_dir):
        if not world_dir or not os.path.isdir(world_dir):
            return "-"
        n = 0
        for base in ("region",
                     os.path.join("dimensions", "minecraft",
                                  "overworld", "region")):
            rd = os.path.join(world_dir, base)
            if os.path.isdir(rd):
                try:
                    n += sum(1 for f in os.scandir(rd)
                             if f.name.endswith(".mca"))
                except Exception:
                    pass
        if n == 0:
            # 深一层找
            try:
                for root, dirs, files in os.walk(world_dir):
                    if os.path.basename(root) == "region":
                        n += sum(1 for f in files
                                 if f.endswith(".mca"))
                    if root.count(os.sep) - world_dir.count(os.sep) > 3:
                        dirs[:] = []
            except Exception:
                pass
        return n
