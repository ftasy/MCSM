# -*- coding: utf-8 -*-
"""进程生命周期：启动、日志读取、退出处理、停止与强制结束。

从 app.py 的 MainWindow 拆出，作为 mixin 混入主窗口。
"""

import os
import sys
import traceback
import logging

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox
from .core import versions, java as javacore, builder, install, fsutil
from .dialogs import Worker
from .const import _XMX_RE


class ProcessMixin:
    """进程生命周期：启动、日志读取、退出处理、停止与强制结束。"""

    # ★ V2.3.6：退出时"要留给后台继续跑"的 QProcess 放这里。
    #   QProcess 是主窗口的子对象，主窗口销毁会连带销毁它 ——
    #   Qt 会打印 "QProcess: Destroyed while process is still running"，
    #   子进程的 stdin/stdout 管道也会被关掉，服务器可能莫名其妙挂掉。
    #   退出时把要保留的进程 setParent(None) + 断开信号 + 存进这里，
    #   保证 java 进程完完整整活下来（这正是"关掉管理器、服务器照跑"的前提）。
    _LIVE_PROCS = []

    def do_start(self, auto_restart=False, entry=None):
        # ★ A2：自动重启时传 entry，不再靠 selectRow 抢用户当前选中的服
        # ★ V2.3.3：支持批量 —— 没传 entry 时按"当前选中"启动：
        #   多选 = 批量启动；单选/没选 = 启动当前那个。
        if entry is not None:
            self._start_one(entry, auto_restart)
            return
        entries = self._selected_entries() or []
        if not entries:
            e = self._cur_entry()
            if e:
                entries = [e]
        if not entries:
            QMessageBox.information(self, "提示", "先选中一个服务器")
            return
        if len(entries) == 1:
            self._start_one(entries[0], auto_restart)
        else:
            self._start_batch(entries, auto_restart)

    def _start_one(self, e, auto_restart=False):
        """启动单个服务器（原 do_start 的单服逻辑）。"""
        if not e:
            return
        k = self._key(e)
        if k in self.procs:
            return
        # ★ V2.3.3：启动前先确认磁盘空间 —— MC 启动后要写世界/日志，
        #   盘满了写入失败会把世界存档写坏。空间不够直接跳过并提示。
        if not self._check_disk_space(e):
            self._restore_start_btn(e)
            return
        # ★ V1.6.0：点击启动立即给反馈并禁用按钮，防止狂点。
        #   真正启动成功后 _refresh_actions() 会重设按钮；
        #   提前中止的路径各自调用 _restore_start_btn 恢复。
        # ★ V1.9.11：移除 v1.9.6 的 _starting 防重入锁 —— 它的
        #   _finish_start 中间层让启动路径变复杂，且锁的释放时机
        #   一旦出错就会"点启动完全没反应"。恢复最直接的链路：
        #   do_start → _prepare_auth → _go_start_now（v1.9.5 结构）。
        self.statusBar().showMessage(
            "正在启动: %s ..." % (e.get("name") or e.get("folder")), 0)
        try:
            self.act_start.setEnabled(False)
        except Exception:
            pass
        sd = e.get("server_dir") or e["folder"]
        bat = os.path.join(sd, "run.bat")
        jar = None
        if e.get("jars"):
            jar = os.path.join(sd, e["jars"][0])
        if not os.path.isfile(bat) and not (jar and os.path.isfile(jar)):
            QMessageBox.warning(self, "无法启动",
                                "没有 run.bat 也没有服务端 jar，"
                                "请先『初始化服务端』")
            self._restore_start_btn(e)
            return

        if not self._check_port_conflict(e):
            self._restore_start_btn(e)
            return

        # ★ V0.4.0：防火墙自动放行 server-port（静默，失败不打断启动）
        self._firewall_allow(e)

        # ★ V1.3.0：登录环境准备（正版/离线 即时处理；
        #   多源共存 需下载登录组件时先走 Worker，完成后自动继续启动）
        self._prepare_auth(e, lambda: self._finish_start(e, auto_restart))

    def _start_batch(self, entries, auto_restart=False):
        """★ V2.3.3 批量启动；★ V2.5.0 跳过原因逐台点名。"""
        started = 0
        skipped = []
        for e in entries:
            name = e.get("name") or e.get("folder") or "?"
            if self._key(e) in self.procs:
                skipped.append("%s：已在运行" % name)
                continue
            if not self._check_disk_space(e):
                skipped.append("%s：磁盘可用空间不足" % name)
                continue
            self._start_one(e, auto_restart)
            started += 1
        self._log_console(
            "[批量启动] 已在队列启动 %d 个，跳过 %d 个"
            % (started, len(skipped)))
        for line in skipped:
            self._log_console("  [跳过] " + line)
        self.statusBar().showMessage(
            "批量启动：启动 %d，跳过 %d（详情看输出）"
            % (started, len(skipped)), 5000)

    def _disk_free_gb(self, e):
        """服务器所在盘的剩余可用空间（GB），取不到返回 None。

        ★ 健壮性：先用绝对路径并确认目录存在再查；否则在某些
          虚拟化/相对路径环境下 shutil.disk_usage 可能异常或长时间阻塞。
        """
        try:
            import os as _os
            import shutil
            sd = e.get("server_dir") or e.get("folder")
            if not sd:
                return None
            sd = _os.path.abspath(sd)
            if not _os.path.isdir(sd):
                return None
            return shutil.disk_usage(sd).free / (1024 ** 3)
        except Exception:
            return None

    def _check_disk_space(self, e):
        """★ V2.3.3：启动前磁盘预检。

        需要：至少 max(2 GB, 内存上限 × 1.5) 的剩余空间
        （内存镜像 + 世界/日志写入余量）。不够则提示并拒绝启动，
        避免写入失败损坏世界存档。
        """
        try:
            sv = self._saved(e)
            xmx = sv.get("xmx", 4096) or 4096   # MB
            need = max(2.0, xmx / 1024.0 * 1.5)
            free = self._disk_free_gb(e)
            if free is None:
                return True   # 取不到就不拦，不挡正常启动
            if free < need:
                name = e.get("name") or e.get("folder")
                msg = ("磁盘空间不足：「%s」所在盘只剩 %.1f GB 可用，"
                       "低于建议的 %.1f GB（含内存镜像 + 世界存档）。\n\n"
                       "强行启动可能因写入失败损坏世界存档，已跳过。"
                       % (name, free, need))
                self._log_console("[!] " + msg)
                QMessageBox.warning(self, "磁盘空间不足", msg)
                return False
        except Exception:
            return True
        return True

    def _finish_start(self, e, auto_restart):
        """登录环境就绪后的启动延续（★ V1.9.11：无锁版本）。

        直接调 _go_start_now；异常一律留痕（manager.log 完整堆栈 +
        控制台红字），绝不静默吞掉 —— 这是"点启动没反应"的根治。
        """
        try:
            self._go_start_now(e, auto_restart)
        except Exception:
            # 异常路径：恢复按钮/状态栏，绝不把启动按钮永久禁用。
            try:
                import traceback as _tb
                _tb.print_exc()
                logging.exception("[启动异常] %s 启动流程出错：",
                                  e.get("name") or e.get("folder"))
            except Exception:
                pass
            try:
                self._restore_start_btn(e)
            except Exception:
                pass
            try:
                self._log_console(
                    "[启动异常] 启动流程出错，详见 manager.log 或上方堆栈")
            except Exception:
                pass

    def _go_start_now(self, e, auto_restart=False):
        k = self._key(e)
        if k in self.procs:
            return
        sd = e.get("server_dir") or e["folder"]
        bat = os.path.join(sd, "run.bat")   # ★ 修：兜底分支要用，之前漏定义
        v = e.get("version")
        need = versions.need_java(v) if v else None
        sv = self._saved(e)
        jpath = sv.get("java")
        # ★ V1.9.10：启动过程逐步诊断 —— 卡在哪一步，控制台直接看到
        try:
            self._log_console("[启动] 第 1 步：查找 Java %s ..."
                              % (need or "（任意版本）"))
        except Exception:
            pass
        if not jpath:
            j = javacore.pick_for(need)
            if not j:
                # ★ V2.0.1.2：首次没找到 → 强制全盘重扫一次再决定
                #   （第一次扫描偶尔因缓存/环境失败，重扫可自愈，
                #    避免用户无谓地被弹"手动选择 java.exe"）
                try:
                    j = javacore.pick_for(
                        need, javas=javacore.find_all(force=True))
                except Exception:
                    j = None
            if not j:
                # ★ V1.9.8：自动扫描没找到时，给"手动选择 java.exe"入口
                #   （很多用户把 JDK 装在 D:\JDK 这类自定义位置，扫描可能漏）
                if not self._ask_pick_java(e, need):
                    self._restore_start_btn(e)
                return
            jpath = j["path"]
            # ★ V2.0.1.2：把自动找到的 Java 记进该服配置，
            #   下次启动直接使用，不再每次依赖扫描结果
            try:
                sv["java"] = jpath
                self._save_data()
            except Exception:
                pass
            try:
                self._log_console("[启动] 自动扫描选中：%s（Java %d）"
                                  % (jpath, j["major"]))
            except Exception:
                pass
        else:
            try:
                info = javacore.probe(jpath)
                if not info:
                    raise ValueError("bad saved java")
                self._log_console("[启动] 使用已保存 Java：%s（Java %d）"
                                  % (jpath, info[0]))
            except Exception:
                # ★ V2.0.1.2：已保存的 Java 失效（如相对路径/被卸载），
                #   回退自动扫描，而不是带着坏路径硬启动
                self._log_console("[启动] 已保存的 Java 不可用，重新扫描 ...")
                j = javacore.pick_for(need)
                if not j:
                    try:
                        j = javacore.pick_for(
                            need, javas=javacore.find_all(force=True))
                    except Exception:
                        j = None
                if not j:
                    if not self._ask_pick_java(e, need):
                        self._restore_start_btn(e)
                    return
                jpath = j["path"]
                try:
                    sv["java"] = jpath
                    self._save_data()
                except Exception:
                    pass
                self._log_console("[启动] 重新扫描选中：%s（Java %d）"
                                  % (jpath, j["major"]))

        # ★ V1.9.12：启动主体（曾因 v1.9.8 结构错误被误放进
        #   _ask_pick_java 的 return 之后变成死代码 —— 选完 Java 后
        #   什么都不做，这就是"点启动没反应"的真相。现在移回这里）。
        # ★ 读取 Aikar 优化开关
        use_aikar = sv.get("aikar", False)
        raw_xms = sv.get("xms", 1024)
        raw_xmx = sv.get("xmx", 4096)
        # Aikar 官方强烈建议 Xms 和 Xmx 保持一致
        if use_aikar:
            raw_xmx = raw_xms
        xms = "%dM" % raw_xms
        xmx = "%dM" % raw_xmx

        launch_mode = "jar"
        # ★ V1.9.9：jar_name 必须初始化为 None，由 find_launch 覆盖，
        #   否则会抛 NameError（历史 bug 根因）。
        jar_name = None
        args_rel = None
        try:
            L = install.find_launch(sd)
            launch_mode = L.get("launch") or "jar"
            args_rel = L.get("args_rel")
            jar_name = (os.path.join(sd, L["jar"])
                        if L.get("jar") else None)
            try:
                self._log_console("[启动] 第 2 步：核心检测 OK（%s：%s）"
                                  % (launch_mode,
                                     jar_name or args_rel or "?"))
            except Exception:
                pass
        except Exception:
            pass

        # ★ V1.9.12（终版）：用 QProcess 启动 java，输出流进
        #   软件内右下角控制台页签（用户明确要的形态：主页点启动 →
        #   右下角输出窗口新建该服务器的监控页签并滚动显示服务器日志）。
        proc = QProcess(self)
        # ★ V2.5.0：实测 FailedToStart 是在 proc.start() 返回【之前】
        #   同步 emit 的。以前 self.procs[k] = proc 写在 start() 之后，
        #   _start_failed 反查不到 key，随后又把失败的 proc 写进字典 ——
        #   这台服就永久卡在"运行中"，点停止还会访问已 deleteLater 的对象。
        #   现在：进程一创建就登记 + 带上 key，失败清理不再依赖遍历反查。
        try:
            proc._mcsm_key = k
            proc._mcsm_failed = False
            proc._auto_restart = bool(auto_restart)
        except Exception:
            pass
        self.procs[k] = proc
        self._is_stopping[k] = False
        proc.setWorkingDirectory(sd)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyRead.connect(lambda p=proc: self._read(p))
        proc.finished.connect(lambda code, st, p=proc: self._finished(p, code))
        # ★ A1：FailedToStart 不走 finished，不接这个信号 UI 会卡死在"运行中"
        proc.errorOccurred.connect(
            lambda err, p=proc: self._start_failed(p, err))
        # ★ V1.9.10：进程真正起来后打印 PID —— 确认"到了哪一步"
        try:
            proc.started.connect(
                lambda p=proc: self._log_console(
                    "[启动] 进程已启动 PID=%d" % (p.processId() or 0)))
        except Exception:
            pass

        if launch_mode == "argsfile" and args_rel:
            # 新版 Forge：java @user_jvm_args.txt @...\win_args.txt
            uj = os.path.join(sd, "user_jvm_args.txt")
            aikar_args = builder.AIKAR_FLAGS if use_aikar else []
            try:
                if not os.path.isfile(uj):
                    body = "-Xms%s\n-Xmx%s\n" % (xms, xmx)
                    if aikar_args:
                        body += "\n".join(aikar_args) + "\n"
                    fsutil.atomic_write_text(uj, body, encoding="utf-8")
                elif aikar_args:
                    with open(uj, "r", encoding="utf-8") as f:
                        c = f.read()
                    need = False
                    if "-XX:+UseG1GC" not in c:
                        c = c.rstrip("\n") + "\n" + "\n".join(aikar_args) + "\n"
                        need = True
                    m = _XMX_RE.search(c)
                    if m and m.group(1) != ("%s" % xms):
                        c = _XMX_RE.sub("-Xmx%s" % xms, c)
                        need = True
                    if need:
                        fsutil.atomic_write_text(uj, c, encoding="utf-8")
            except Exception:
                pass
            # ★ 绝对路径即可。QProcess 会自动为带空格的参数加双引号，
            #   如果这里手动加了双引号，反而会被转义成 "\"@...\"" 导致 Java 认不出。
            proc.start(jpath, ["@" + uj,
                               "@" + os.path.join(sd, args_rel),
                               "nogui"])
        elif launch_mode == "jar" and jar_name and os.path.isfile(jar_name):
            java_args = ["-Xms%s" % xms, "-Xmx%s" % xmx]
            if use_aikar:
                java_args.extend(builder.AIKAR_FLAGS)
            java_args.extend(["-jar", jar_name, "nogui"])
            proc.start(jpath, java_args)
        elif os.path.isfile(bat):
            # 兜底：实在判断不出来才走 bat
            proc.start(bat, [])
        else:
            # ★ V2.5.0：进程已经提前登记进 procs，这个"没核心"的早退
            #   分支必须把登记撤销，否则同样会留下永久"运行中"的假状态。
            self.procs.pop(k, None)
            self._cleanup_proc_state(proc, k)
            try:
                proc.deleteLater()
            except Exception:
                pass
            QMessageBox.warning(self, "无法启动",
                                "没找到可启动的核心，"
                                "请先『初始化服务端』")
            self._restore_start_btn(e)
            return

        # ★ V2.5.0：start() 返回后先确认没有启动失败。
        #   _start_failed 已在同步 emit 里完成清理并 deleteLater，
        #   这里绝不能继续往下把控制台设成"运行中"（老 bug 的核心）。
        if bool(getattr(proc, "_mcsm_failed", False)):
            return
        try:
            _pid = proc.processId()
            if _pid:
                fsutil.atomic_write_text(os.path.join(sd, ".mcsm_pid"),
                                         str(_pid), encoding="utf-8",
                                         newline="")
        except Exception:
            pass
        # ★ 只有手动启动才重置崩溃计数，自动重启不能重置，否则会无限循环
        if not auto_restart:
            self.auto_restart_count[k] = 0

        # 记录最近开服时间（"按最近开服"排序要用）
        try:
            import time as _t
            sv = self.data.setdefault("servers", {}).setdefault(k, {})
            sv["last_start"] = _t.time()
            self._save_data()

            # ★ 开启定时备份
            if sv.get("auto_backup"):
                interval = sv.get("backup_interval", 30) * 60 * 1000
                if k in self.auto_backup_timers:
                    self.auto_backup_timers[k].stop()
                timer = QTimer(self)
                timer.timeout.connect(lambda: self._do_auto_backup(k, e))
                timer.start(interval)
                self.auto_backup_timers[k] = timer
                self._log_console(f"[备份] 已开启定时备份，每 {sv.get('backup_interval')} 分钟一次")

            # ★ V0.5.0：每日定时重启（每分钟检查一次，命中就倒计时重启）
            rt = sv.get("auto_restart_time") or ""
            if rt:
                if k in self._restart_timers:
                    self._restart_timers[k].stop()
                rt_timer = QTimer(self)
                rt_timer.timeout.connect(lambda: self._check_restart_time(k, e))
                rt_timer.start(60000)
                self._restart_timers[k] = rt_timer
                self._log_console("[定时重启] 已开启，每天 %s 自动重启"
                                  % rt)
                # 启动后立即检查一次（配置时间刚好到点的场景）
                self._check_restart_time(k, e)
        except Exception:
            pass

        # ★ 每个服务器一个独立控制台页签（右下角输出窗口）。
        #   新建/切到该服务器的监控页签，服务器输出滚动显示在这里。
        c = self._console_tab(k, e.get("name") or "服务器")
        c.proc = proc
        c.set_running(True)
        c.clear()
        c.append("=== 启动 %s ===" % (e.get("name")))
        self.console_tabs.setCurrentIndex(self.console_tabs.indexOf(c))

        self._refresh_actions()
        self._refresh_status(e)
        self._warn_bad_levelname(e)
        self.statusBar().showMessage(
            "运行中: %s（共 %d 个在运行）"
            % (e.get("name"), len(self.procs)))

    def _ask_pick_java(self, e, need):
        """自动扫描没找到 Java 时，让用户手动指定 java.exe。
        返回 True=已选好（路径已存进配置），False=取消。★ V1.9.8
        """
        box = QMessageBox(self)
        box.setWindowTitle("没找到 Java")
        box.setIcon(QMessageBox.Warning)
        box.setText(
            "自动扫描没找到 Java %s，无法启动。\n\n"
            "可以手动指定 java.exe 的位置，\n"
            "它一般在自己解压/安装的 JDK 目录下的 bin 里。\n\n"
            "选择后会自动记住，下次直接使用。" % (need or "（任意版本）"))
        pick = box.addButton("手动选择 java.exe ...", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not pick:
            return False
        from PySide6.QtWidgets import QFileDialog
        # ★ V2.4.0：起始目录别再假设 C 盘 —— 有些精简系统/改盘符的
        #   机器没有 ProgramFiles 或 C:，会直接弹到"此电脑"甚至报错。
        _start = os.environ.get("ProgramFiles") or os.path.expanduser("~")
        if not os.path.isdir(_start):
            _start = os.path.expanduser("~")
        jp, _ = QFileDialog.getOpenFileName(
            self, "选择 java.exe", _start,
            "Java 可执行文件 (java.exe);;所有文件 (*)")
        if not jp:
            return False
        info = javacore.probe(jp)
        if info is None:
            QMessageBox.warning(self, "无效的 Java",
                                "所选文件无法作为 java 运行，请重新选择。")
            return False
        major = info[0]
        if need and major < need:
            r = QMessageBox.question(
                self, "Java 版本偏低",
                "所选 Java 主版本为 %d，这个服需要 Java %d。\n"
                "继续用吗？" % (major, need),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return False
        sv = self._saved(e)
        sv["java"] = jp
        try:
            self._save_data()
        except Exception:
            pass
        self._log_console("[Java] 已指定 %s (Java %d)" % (jp, major))
        return True

    # ★ V1.9.12：下方原为 v1.9.8 结构错误遗留的死代码（误放在
    #   _ask_pick_java 的 return 之后，永不执行）—— 已整体移回
    #   _go_start_now 并重写为"独立窗口启动"。此处不再保留。

    def _read(self, proc):
        # ★ 把 readAll 和 state() 一起放进 try ——
        #   当前调用链上 proc 一定活着，但这是"靠时机"兜底，
        #   不是"代码"兜底。把 state() 也包进来，将来新增调用点
        #   不会因为 proc 已被 deleteLater 而抛 RuntimeError。
        try:
            data = proc.readAll().data()
            state = proc.state()
        except Exception:
            return
        # ★ 隐患 1：进程已退出时 readAll() 通常返回空，
        #   此时不能早退 —— 否则下面"残留 buf 吐出来"那段永远走不到，
        #   _finished 里调 self._read(proc) 的兜底就是空转。
        #   进程还在跑、本轮又没数据，那才是真没事可做。
        if not data and state != QProcess.NotRunning:
            return
            
        # 获取或创建这个进程专属的缓冲区
        buf = self._proc_buffers.setdefault(proc, bytearray())
        buf.extend(data)
        
        # 按 \n 切分，只取完整的行（防止 QProcess 截断中文字符）
        # ★ C2：用 find + del 原地删除，替代 bytearray.split(b"\n", 1)。
        #   以前每次 split 都会把剩余内容整体复制一遍 ——
        #   一次 readyRead 如果带进来几 MB（log-ips=true / debug 日志），
        #   要按行数做几万次 O(n) 复制，整体就是 O(n²)，控制台会明显卡。
        #   现在 buf 只在原位切片，剩余内容不复制。
        #   顺带把 self._console_of(proc) 提到循环外，每行不再查一次。
        c = self._console_of(proc) or self.console
        while True:
            pos = buf.find(b"\n")
            if pos < 0:
                break
            # ★ bytearray 切片本身就是新对象，.decode() 直接支持
            #   bytearray；再包一层 bytes() 是多一次 O(pos) 复制。
            line_bytes = buf[:pos]
            del buf[:pos + 1]

            # 针对这一行完整的数据进行解码
            line_str = ""
            for enc in ("utf-8", "gbk"):
                try:
                    line_str = line_bytes.decode(enc) + "\n"
                    break
                except Exception:
                    continue
            else:
                line_str = line_bytes.decode("utf-8", "replace") + "\n"

            c.append(line_str)
            self._float_broadcast(c, line_str)
            
        # 如果进程关闭，缓冲区还有残留且没换行的内容，也要吐出来
        if state == QProcess.NotRunning and buf:
            # ★ C1：跟主循环保持一致，先 utf-8 再 gbk，
            #   避免最后半行 GBK 中文被切在 \n 前打成 �
            line_str = ""
            for enc in ("utf-8", "gbk"):
                try:
                    line_str = buf.decode(enc) + "\n"
                    break
                except Exception:
                    continue
            else:
                line_str = buf.decode("utf-8", "replace") + "\n"
            # c 在主循环之前已经算过（proc 没变，_console_of 结果一样）
            c.append(line_str)
            self._float_broadcast(c, line_str)
            self._proc_buffers.pop(proc, None)

    def _check_restart_time(self, k, e):
        """每分钟检查一次，到点就触发 60 秒倒计时重启。"""
        try:
            sv = self.data.get("servers", {}).get(k, {})
            rt = sv.get("auto_restart_time") or ""
        except Exception:
            return
        if not rt or k not in self.procs:
            return
        if k in self._restart_countdown or k in self._plan_restart:
            return
        import time as _t
        try:
            now = _t.localtime()
            hhmm = "%02d:%02d" % (now.tm_hour, now.tm_min)
        except Exception:
            return
        if hhmm == rt:
            self._start_planned_restart(k, e)

    def _start_planned_restart(self, k, e):
        """写公告 + 60 秒后 stop；_finished 里检测 _plan_restart 自动再启动。"""
        proc = self.procs.get(k)
        if proc is None:
            return
        self._plan_restart[k] = True
        self._restart_countdown[k] = True
        self._log_console("[定时重启] 『%s』将在 60 秒后重启！"
                          % (e.get("name") or ""))
        try:
            proc.write(("say [系统] 服务器将在 60 秒后重启！\r\n")
                       .encode("utf-8"))
        except Exception:
            pass

        def _do_stop():
            self._restart_countdown.pop(k, None)
            p = self.procs.get(k)
            if p is None:
                return
            try:
                p.write(b"stop\r\n")
            except Exception:
                pass

        QTimer.singleShot(60000, _do_stop)

    def _finished(self, proc, code):
        # ★ Bug 4 修复：进程退出前把残留的缓冲区吐完（可能包含崩溃的最后一行）
        try:
            self._read(proc)
        except Exception:
            pass

        key = None
        for k, p in list(self.procs.items()):
            if p is proc:
                key = k
                del self.procs[k]
                break

        # ★ 多开修复：进程结束第一时间清掉状态标记，
        #   防止残留的 _is_stopping 把崩溃误判成"主动停止"、
        #   残留的 _port_cache 让悬停显示旧端口。
        #   （is_stopping 提前取出来，供下面自动重启判断使用）
        if key:
            for en in self.entries:
                if self._key(en) == key:
                    _sd = en.get("server_dir") or en.get("folder", "")
                    try: os.remove(os.path.join(_sd, ".mcsm_pid"))
                    except Exception: pass
                    break
        _t = self._log_tailers.pop(key, None)
        if _t: _t.stop()
        is_stopping = self._is_stopping.pop(key, None)
        self._port_cache.pop(key, None)
        # ★ V0.5.0：计划重启标记提前取出；顺带清掉监控/倒计时状态
        is_plan = self._plan_restart.pop(key, False)
        self._ping_text.pop(key, None)
        getattr(self, "_mon_extra", {}).pop(key, None)
        self._restart_countdown.pop(key, None)
        if key in self._restart_timers:
            self._restart_timers[key].stop()
            del self._restart_timers[key]

        c = self._console_of(proc) or self.console
        c.append("=== 已退出，代码 %d ===" % code)
        if code != 0:
            c.append("[!] 退出码不是 0，说明是崩溃/异常退出。"
                     "往上翻看红色报错。")
        # 只锁【这一个】控制台的输入框，不影响其它还在跑的服
        c.set_running(False)
        c.proc = None

        self._refresh_actions()
        if key:
            for en in self.entries:
                if self._key(en) == key:
                    self._refresh_status(en)
                    break
        # ★ V2.0.1.1：服务器退出后信息面板恢复详情视图
        try:
            self._update_info_monitor()
        except Exception:
            pass
        if self.procs:
            self.statusBar().showMessage(
                "已停止（还有 %d 个在运行）" % len(self.procs), 4000)
        else:
            self.statusBar().showMessage("已停止", 3000)
        # ★ 删除下面 4 行暴力覆盖，信任 _refresh_actions() 
        # self.act_stop.setEnabled(False)
        # self.act_start.setEnabled(True)
        # self.statusBar().showMessage("已停止")

        # ★ 停止定时备份
        if key in self.auto_backup_timers:
            self.auto_backup_timers[key].stop()
            del self.auto_backup_timers[key]

        # ★ V0.5.0：计划重启 —— 不弹窗、不计数，3 秒后自动再启动
        if is_plan:
            for en in self.entries:
                if self._key(en) == key:
                    self._log_console(
                        "[定时重启] 正在重新启动『%s』..." % en.get("name"))
                    QTimer.singleShot(
                        3000, lambda: self._try_restart(key, en))
                    break

        # ★ 自动重启逻辑
        elif code != 0 and not is_stopping:
            sv = self.data.get("servers", {}).get(key, {})
            if sv.get("auto_restart"):
                cnt = self.auto_restart_count.get(key, 0)
                if cnt < 5:
                    self.auto_restart_count[key] = cnt + 1
                    self._log_console(f"[!] 服务器异常退出，10 秒后自动重启（第 {cnt+1} 次）")
                    for en in self.entries:
                        if self._key(en) == key:
                            QTimer.singleShot(10000, lambda: self._try_restart(key, en))
                            break
                else:
                    self._log_console("[!] 自动重启次数已达 5 次，暂停重启。请检查日志。")
                    # ★ V1.6.0：自动重启已放弃 → 智能引导打开日志分析
                    self._ask_crash_diag(key)
            else:
                # ★ V1.6.0：没开自动重启 → 崩溃后智能引导
                self._log_console("[!] 服务器异常退出（未开启自动重启）。")
                self._ask_crash_diag(key)
        else:
            if key:  # ★ 建议 6 修复：防止 None 写入脏数据
                self.auto_restart_count[key] = 0

        # ★ 第三处修改：精准提取当前结束的服务器进行备份
        e = getattr(self, "pending_backups", {}).pop(key, None)
        if e is not None:
            self._after_stop_backup(e)

        # ★ 清理 QProcess 对象和日志缓冲区，防止内存泄漏
        self._proc_buffers.pop(proc, None)
        try:
            proc.deleteLater()
        except Exception:
            pass

        # ★ 多开修复：进程结束强制全量重绘，彻底刷新
        #   "运行中/已停止" 状态，避免界面残留旧状态
        try:
            self._fill_views()
        except Exception:
            pass

    def _cleanup_proc_state(self, proc, key):
        """启动失败时清掉所有按 key 挂着的状态（与 _finished 对齐）。

        ★ V2.5.0：老 _start_failed 只删了 procs 和 auto_backup_timers，
          定时重启 QTimer、.mcsm_pid、_is_stopping、pending_backups
          全留着 —— 定时器空转到窗口关闭，残留的 _is_stopping 还会把
          下一次真崩溃误判成"主动停止"。
        """
        if not key:
            return
        for attr in ("_restart_timers", "auto_backup_timers"):
            d = getattr(self, attr, None) or {}
            t = d.pop(key, None)
            if t is not None:
                try:
                    t.stop()
                except Exception:
                    pass
        d = getattr(self, "_log_tailers", None) or {}
        t = d.pop(key, None)
        if t is not None:
            try:
                t.stop()
            except Exception:
                pass
        for attr in ("_is_stopping", "_port_cache", "_ping_text",
                     "_restart_countdown", "_plan_restart", "_mon_extra",
                     "pending_backups"):
            try:
                (getattr(self, attr, None) or {}).pop(key, None)
            except Exception:
                pass
        for en in list(getattr(self, "entries", []) or []):
            try:
                if self._key(en) == key:
                    _sd = en.get("server_dir") or en.get("folder", "")
                    try:
                        os.remove(os.path.join(_sd, ".mcsm_pid"))
                    except Exception:
                        pass
                    break
            except Exception:
                pass

    def _start_failed(self, proc, err):
        """QProcess 启动失败（java 路径失效 / 权限不足 / 被安全软件拦）。

        FailedToStart 不会发出 finished 信号，所以 _finished 里的清理
        逻辑不会走。不接这个信号的话，procs 里会留一条永远不退出的
        记录，界面卡在"运行中"，按钮也全错乱。
        """
        try:
            from PySide6.QtCore import QProcess as _QP
            if err != _QP.ProcessError.FailedToStart:
                return
        except Exception:
            return

        # ★ V2.5.0：属性在 proc 创建时就写好了（_auto_restart 也提前），
        #   不依赖 deleteLater 之后还能不能读 Python 属性。
        is_auto = bool(getattr(proc, "_auto_restart", False))
        key = getattr(proc, "_mcsm_key", None)
        if key is None:
            for k, p in list(self.procs.items()):
                if p is proc:
                    key = k
                    break
        try:
            proc._mcsm_failed = True
        except Exception:
            pass
        if key and self.procs.get(key) is proc:
            self.procs.pop(key, None)
        self._cleanup_proc_state(proc, key)

        c = self._console_of(proc) or self.console
        c.append("[!] 服务器未能启动：java 找不到 / 权限不足 / 被拦截")
        c.set_running(False)
        c.proc = None

        self._proc_buffers.pop(proc, None)
        try:
            proc.deleteLater()
        except Exception:
            pass

        self._refresh_actions()
        if key:
            for en in self.entries:
                if self._key(en) == key:
                    self._refresh_status(en)
                    break

        # ★ 隐患 3：自动重启失败不弹窗，避免抢用户操作别的服的焦点。
        #   手动启动失败照旧弹，用户能马上看到并去修 Java。
        if not is_auto:
            QMessageBox.warning(
                self, "启动失败",
                "服务器进程没能起来。\n\n"
                "多半是 Java 路径失效（重装或移动过 Java），\n"
                "或者安全软件拦了 java.exe。\n\n"
                "可以到『服务器配置 → 启动设置』里重新选 Java。")
            # ★ V1.6.0：启动直接失败 → 智能引导打开日志分析
            self._ask_crash_diag(key)
        else:
            self._log_console(
                "[!] 自动重启失败：java 找不到 / 权限不足 / 被拦截。"
                "已停止自动重启。")

    def do_stop(self, entry=None):
        # ★ V2.3.3：支持批量 —— 多选 = 批量停止；单选/没选 = 停止当前。
        #   传了 entry（双击 / 右键菜单）就只停这一台，不看当前多选。
        if entry is not None:
            self._stop_one(entry)
            return
        entries = self._selected_entries() or []
        if not entries:
            e = self._cur_entry()
            if e:
                entries = [e]
        if not entries:
            QMessageBox.information(self, "提示", "先选中一个服务器")
            return
        if len(entries) == 1:
            self._stop_one(entries[0])
        else:
            self._stop_batch(entries)

    def _stop_one(self, e):
        """停止单个服务器（原 do_stop 的单服逻辑）。"""
        if not e:
            return
        k = self._key(e)
        proc = self.procs.get(k)
        if not proc:
            # ★ V1.1.0：没在运行直接提示，不弹备份确认框
            QMessageBox.information(self, "提示", "这个服没在运行")
            return
        # ★ V2.0.1：是否弹「停止后备份？」由显示设置勾选决定
        #   （disp["stop_ask_backup"]，默认 True；关闭则直接停止）
        ask = self.disp.get("stop_ask_backup", True)
        # ★ V2.3.6：接管回来的服务器没有控制通道（上个管理器的管道
        #   已经随它一起没了），"停止"只能直接结束进程 —— 不会保存世界。
        #   必须先说清楚，否则用户以为存盘了就把服关了，世界直接损坏。
        try:
            from ..widgets import ReattachedProcess as _RA
            if isinstance(proc, _RA):
                r = QMessageBox.question(
                    self, "强制停止（接管状态）",
                    "「%s」是上次管理器退出后接管回来的，"
                    "控制通道已经断了 —— 停止会**直接结束进程，不保存世界**。\n\n"
                    "建议先在游戏里执行 save-all 再停。\n\n"
                    "确定要强制结束它吗？"
                    % (e.get("name") or e.get("folder")),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if r != QMessageBox.Yes:
                    return
                # ★ V2.4.1：接管服的"停止"就是直接结束 —— 不再走
                #   write("stop") + 等 8 秒的老路径。以前那条路对
                #   ReattachedProcess 毫无作用（管道早断了），只能干等
                #   3 秒一次的日志轮询才发现"其实早死了"，表现就是
                #   "点停止卡很久才显示已退出"。现在立刻结束 + 立刻收尾。
                self._is_stopping[k] = True
                self._log_console(
                    "=== 已强制结束接管的进程（PID=%s）==="
                    % proc.processId())
                try:
                    proc.kill()
                except Exception:
                    pass
                self._finished(proc, 0)
                return
        except Exception:
            pass
        if ask:
            r = QMessageBox.question(
                self, "停止服务器",
                "是否在停止后备份存档？\n\n"
                "· 是：服务器退出后自动备份存档\n"
                "  （格式按『服务器配置 → 备份』里的备份格式）\n"
                "· 否：只停止，不备份\n\n"
                "（可在『显示设置 → 列表』勾选关闭此弹窗）",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r == QMessageBox.Yes:
                self.do_stop_backup(k, e, proc)
                return
        self._is_stopping[k] = True
        # ★ 立刻给反馈。以前点了停止什么提示都没有，
        #   服务器其实在慢慢保存世界，用户会以为卡住了。
        self._log_console("=== 正在停止，等待世界保存完毕 ===")
        self.statusBar().showMessage("正在停止 %s ..." % e.get("name"))
        self.act_stop.setEnabled(False)
        self.act_stop_backup.setEnabled(False)
        self.act_float_console.setEnabled(False)
        try:
            proc.write(b"stop\n")
        except Exception:
            pass
        # 8 秒还没退出就强杀（防止卡死时永远不退出）
        QTimer.singleShot(8000, lambda: self._kill(proc, e))

    def _stop_batch(self, entries):
        """★ V2.3.3：批量停止。停止所有运行中的，跳过没运行的。

        批量时不再逐个弹"停止后备份"——那是单服场景的精细操作，
        批量属于"我明确要全停"，直接停（不备份）。
        """
        stopped = skipped = 0
        for e in entries:
            k = self._key(e)
            proc = self.procs.get(k)
            if not proc:
                skipped += 1
                continue
            self._is_stopping[k] = True
            self._log_console("=== 正在停止 %s，等待世界保存完毕 ==="
                              % (e.get("name") or e.get("folder")))
            try:
                proc.write(b"stop\n")
            except Exception:
                pass
            QTimer.singleShot(8000, lambda p=proc, ev=e: self._kill(p, ev))
            stopped += 1
        self.statusBar().showMessage(
            "批量停止：停止 %d，跳过 %d" % (stopped, skipped), 4000)
        self._refresh_actions()

    def _kill(self, proc, e=None):
        """强杀。走到这里说明 8 秒了还没自己退出。"""
        try:
            if proc.state() != QProcess.NotRunning:
                self._log_console(
                    "[!] 8 秒未退出，强制结束。"
                    "（如果每次都这样，可能是插件/模组卡住退出流程）")
                proc.kill()
        except Exception:
            pass

    def closeEvent(self, ev):
        # ★ V2.3.6：已经决定退出（等服务器停完再退出的那条路径），
        #   直接清理放行，别再弹询问框。
        if getattr(self, "_exiting", False):
            self._exiting = False
            self._cleanup_and_exit(ev)
            return
        # ★ 1. 先检查后台任务（线程：搬迁/导入/扫描）
        busy = []
        for attr, label in (("_migrate_worker", "统一搬迁"),
                            ("_import_worker", "导入地图"),
                            ("_scan_worker", "扫描")):
            w = getattr(self, attr, None)
            if w is not None and w.isRunning():
                busy.append(label)
        if busy:
            r = QMessageBox.question(
                self, "后台任务还在跑",
                "正在后台执行：%s。\n\n"
                "强行退出可能留下复制了一半的文件夹"
                "（之后可以手动删掉重来）。\n确定退出？"
                % "、".join(busy))
            if r != QMessageBox.Yes:
                ev.ignore()
                return

        # ★ 2. 再检查运行中的服务器（进程：MC 服务端）
        #   ★ V2.3.6：以前这里直接弹一个信息框 + 给每个服务器开一个
        #     cmd 窗口 tail 日志 —— 那些 cmd 是独立进程，主窗口关了
        #     它们还在跑（用户看到的"程序还在跑"），而且关不掉。
        #     现在改成让用户明确选：保留服务器退出 / 全部停止并退出，
        #     cmd 日志窗改为可选（默认不开）。
        if self.procs:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Question)
            box.setWindowTitle("服务器还在运行")
            box.setText("有 %d 个服务器仍在运行。" % len(self.procs))
            box.setInformativeText(
                "· 退出并保留服务器：服务器继续在后台跑，\n"
                "  重新打开管理器会自动接管它们"
                "（运行状态、日志、停止按钮都恢复）\n"
                "· 全部停止并退出：先给服务器发 stop，"
                "等它保存完世界再退出\n\n"
                "选哪个都不会让服务器'跟着管理器一起消失' —— "
                "关掉管理器后它们仍是独立进程。")
            try:
                chk = box.setCheckBox(
                    "为每个服务器开一个独立日志窗口（cmd，退出后仍开着）")
                chk.setChecked(False)
            except Exception:
                chk = None
            btn_keep = box.addButton("退出并保留服务器",
                                     QMessageBox.AcceptRole)
            btn_stop = box.addButton("全部停止并退出",
                                     QMessageBox.DestructiveRole)
            btn_cancel = box.addButton("取消", QMessageBox.RejectRole)
            box.setDefaultButton(btn_keep)
            box.exec()
            clicked = box.clickedButton()
            if clicked is btn_cancel:
                ev.ignore()
                return
            if clicked is btn_stop:
                # 给每个服务器发 stop（优雅停止：服务器会保存世界）
                for _k, _proc in list(self.procs.items()):
                    try:
                        _proc.write(b"stop\n")
                    except Exception:
                        pass
                self._log_console(
                    "[退出] 已向 %d 个服务器发送停止指令，"
                    "等它们保存完世界后自动退出。" % len(self.procs))
                self.statusBar().showMessage(
                    "正在停止服务器，保存完世界后自动退出 ...", 5000)
                # ★ 等服务器真的停下再退出 —— 直接退出会销毁 QProcess，
                #   管道一关，java 可能来不及存盘。
                self._exit_wait_left = 15.0
                self._exiting = True
                ev.ignore()
                QTimer.singleShot(500, self._wait_stop_then_exit)
                return
            else:
                # 保留服务器：可选给每个开一个独立 cmd 日志窗
                if chk is not None and chk.isChecked():
                    for _k, _proc in list(self.procs.items()):
                        try:
                            self._spawn_exit_console(_k, _proc)
                        except Exception:
                            pass
                # ★ V2.0.12：进程解耦 —— 退出时不再给服务器发 stop，也不强杀。
                #   java 服务器本就是独立进程，QProcess 只是控制通道；
                #   管理器退出后它继续运行（用户明确要求：
                #   "启动器无响应后结果服务器还在运行"要作为常态行为）。
                #   这里只清掉引用、停掉自动重启标记，避免退出后定时器误触发。
                for k in list(self.procs.keys()):
                    self._is_stopping[k] = True
                # ★ V2.3.6：把要保留的 QProcess 脱离主窗口 + 断开信号，
                #   存进全局存活集合 —— 免得主窗口销毁时连带把 java 进程带走。
                for _k, _proc in list(self.procs.items()):
                    try:
                        _proc.setParent(None)
                    except Exception:
                        pass
                    for _sig in ("finished", "readyRead", "errorOccurred",
                                 "started"):
                        try:
                            getattr(_proc, _sig).disconnect()
                        except Exception:
                            pass
                    try:
                        ProcessMixin._LIVE_PROCS.append(_proc)
                    except Exception:
                        pass
                self.procs.clear()

        self._cleanup_and_exit(ev)

    def _cleanup_and_exit(self, ev):
        """★ V2.3.6：真正的收尾（两条退出路径共用）。

        关掉独立控制台窗口 —— 它们是顶层窗口，只要还有一个可见，
        QApplication 就不会退出，表现就是"点了关闭，程序还在跑"。
        """
        # ★ 关掉所有"调出控制台"的独立窗口
        try:
            for _w in list((getattr(self, "float_consoles", {}) or {}).values()):
                try:
                    _w.close()
                except Exception:
                    pass
            self.float_consoles.clear()
        except Exception:
            pass

        # ★ V2.5.0：分析日志等窗口是 NonModal 顶层窗口，和浮动控制台
        #   一样会挡住 QApplication 退出。老收尾只关 float_consoles，
        #   开着分析窗口点关闭会"窗口没了但进程还在"。
        try:
            for _d in list(getattr(self, "_dlg_refs", []) or []):
                try:
                    _d.close()
                except Exception:
                    pass
            self._dlg_refs = []
        except Exception:
            pass

        # ★ 停掉所有还在跑的后台线程（os._exit 不会走 aboutToQuit，
        #   所以这一步必须在下面显式做完）
        try:
            from .dialogs import _stop_all_threads
            _stop_all_threads()
        except Exception:
            pass

        # ★ 停掉所有的定时备份定时器
        for timer in self.auto_backup_timers.values():
            try:
                timer.stop()
            except Exception:
                pass
        self.auto_backup_timers.clear()

        # ★ V2.5.0：保存结果要看一眼，失败至少落日志，
        #   别让"明明没存上却安静退出"。
        try:
            _ok, _err = self._save_data()
            if not _ok and not getattr(self, "_block_autosave", False):
                logging.warning("[退出] 数据没保存上：%s", _err)
        except Exception as e:
            logging.warning("[退出] 保存异常：%r", e)
        ev.accept()
        # ★ V2.4.1：保留了服务器进程时必须 os._exit ——
        #   实测 QProcess 的析构函数会 kill 子进程（哪怕已经
        #   setParent(None) + 断信号 + 放进 _LIVE_PROCS）。
        #   正常退出路径是：解释器关闭 → 清空全局引用 → QProcess
        #   析构 → java 被杀 —— 这就是"选了退出并保留，后台却
        #   看不到服务器进程"的根因。os._exit(0) 跳过一切析构，
        #   java 才能真正留下来（.mcsm_pid 早已写好，重开可接管）。
        #   数据都在 accept 前用原子写落盘了，这里跳过清理是安全的。
        if getattr(ProcessMixin, "_LIVE_PROCS", None):
            try:
                sys.stdout.flush()
                sys.stderr.flush()
                logging.shutdown()
            except Exception:
                pass
            os._exit(0)

    def _wait_stop_then_exit(self):
        """等服务器都停下再退出（最多等 _exit_wait_left 秒）。"""
        try:
            alive = []
            for _k, _p in list(self.procs.items()):
                try:
                    if _p.state() != QProcess.NotRunning:
                        alive.append(_k)
                except Exception:
                    pass
            if alive:
                left = getattr(self, "_exit_wait_left", 15.0) - 0.5
                self._exit_wait_left = max(0.0, left)
                if left <= 0:
                    # 超时：强制结束剩下的（至少别卡住不退）
                    for _k in alive:
                        try:
                            self.procs[_k].kill()
                        except Exception:
                            pass
                    self._log_console(
                        "[退出] 等待超时，已强制结束 %d 个服务器进程。" % len(alive))
                else:
                    QTimer.singleShot(500, self._wait_stop_then_exit)
                    return
        except Exception:
            pass
        # 全停 / 出错 → 真正退出
        try:
            self.close()
        except Exception:
            pass
