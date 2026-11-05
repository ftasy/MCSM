# -*- coding: utf-8 -*-
"""控制台与监控：多控制台页签、浮动窗口、进程挂接、端口 / 防火墙。

从 app.py 的 MainWindow 拆出，作为 mixin 混入主窗口。
"""

import os

from PySide6.QtGui import QColor
from PySide6.QtCore import Qt, QProcess, QTimer, QSize
from PySide6.QtWidgets import QLabel, QMessageBox, QTableWidgetItem
from .core import monitor, fsutil
from .dialogs import Worker, _track
from .widgets import Console, FloatConsole, ReattachedProcess, _fg, _on_bg
from .core.palette import PAL


class ConsoleMixin:
    """控制台与监控：多控制台页签、浮动窗口、进程挂接、端口 / 防火墙。"""

    # ---------------- 启动/停止 ----------------
    # ---------------- 控制台页签（支持多开） ----------------
    def _console_tab(self, key, name, create=True):
        """取某个服务器的控制台页签，没有就新建一个。"""
        c = self.consoles.get(key)
        if c is not None:
            return c
        if not create:
            return None
        c = Console()
        c.proc_key = key
        c.set_colors(self.disp.get("console_colors") or None)  # ★ V1.1.0
        self.consoles[key] = c
        idx = self.console_tabs.addTab(c, name or "服务器")
        self.console_tabs.setCurrentIndex(idx)
        return c

    def _console_of(self, proc):
        """按 QProcess 反查它的控制台。"""
        for c in self.consoles.values():
            if c.proc is proc:
                return c
        return None

    # ★ V2.0.13：调出服务器后台管理台（独立控制台窗口）
    def do_open_float_console(self):
        """为正在运行的服务器打开独立控制台窗口。

        - 关闭该窗口不停止服务器（进程解耦），输入指令直接发给服务器。
        - 同一服务器只保留一个悬浮窗，重复点击只是把它调到前台。
        """
        e = self._cur()
        if not e:
            QMessageBox.information(
                self, "提示", "请先在列表里选中一个服务器")
            return
        key = self._key(e)
        if key not in self.procs:
            QMessageBox.information(
                self, "提示",
                "该服务器未在运行，无法调出控制台。\n请先点『启动』。")
            return
        w = self.float_consoles.get(key)
        if w is not None:
            w.raise_()
            w.activateWindow()
            return
        proc = self.procs[key]
        name = self._display_name(e)
        w = FloatConsole(self, proc, key, name)
        # 灌入主窗页签里已有的输出，窗口打开即有完整上下文
        try:
            c = self._console_of(proc)
            if c is not None:
                w.append(c.toPlainText())
        except Exception:
            pass
        w.closed.connect(
            lambda k=key: self.float_consoles.pop(k, None))
        self.float_consoles[key] = w
        w.show()

    def _float_broadcast(self, c, line_str):
        """★ V2.0.13：把一行输出同步给该服务器的独立控制台窗口。"""
        if not self.float_consoles:
            return
        _wk = getattr(c, "proc_key", None)
        if not _wk:
            return
        _w = self.float_consoles.get(_wk)
        if _w is None:
            return
        try:
            _w.append(line_str)
        except Exception:
            pass


    def _spawn_exit_console(self, key, proc):
        """V2.1.0: Spawn standalone cmd window tailing server log on manager exit."""
        import subprocess as _sp
        e = None
        for en in self.entries:
            if self._key(en) == key:
                e = en
                break
        if not e:
            return
        sd = e.get("server_dir") or e["folder"]
        name = e.get("name") or e["folder"]
        pid = proc.processId() if hasattr(proc, 'processId') else 0
        log_file = os.path.join(sd, "logs", "latest.log")
        bat_path = os.path.join(sd, ".mcsm_console.bat")
        lines = [
            "@echo off",
            "chcp 65001 >nul 2>&1",
            "title %s - server console (MCSM exited)" % name,
            "echo ============================================",
            "echo  Server: %s" % name,
            "echo  PID: %d" % pid,
            "echo  MCSM manager exited, server still running",
            "echo  Reopen MCSM to restore full management",
            "echo ============================================",
            "echo.",
        ]
        if os.path.isfile(log_file):
            lines.append("echo Tailing log (close window won't stop server):")
            lines.append("echo.")
            lines.append("powershell -NoProfile -Command " + chr(34) + "Get-Content -Path " + chr(39) + log_file + chr(39) + " -Wait -Tail 50" + chr(34))
        else:
            lines.append("echo (log not found, server may still be starting)")
            lines.append("pause")
        fsutil.atomic_write_text(bat_path, chr(10).join(lines) + chr(10),
                                 encoding="utf-8")
        _sp.Popen(["cmd", "/c", bat_path], creationflags=getattr(_sp, "CREATE_NEW_CONSOLE", 0))

    def _detect_running_servers(self):
        """V2.1.0: On startup, detect servers still running from previous session.

        ★ V2.3.6：两处加固
          1. 调用时机：以前固定在启动后 500ms，那会儿目录扫描（异步）
             还没跑完、entries 是空的 → 一个服都接管不了。
             现在由 _on_scan_done（扫描完成）调用，这里再兜底重试。
          2. 接管到的进程只能"看日志 + 强杀"（控制通道已经随上个
             管理器一起没了），所以控制台里要写清楚，别让用户以为
             接管了就能发指令。
        """
        # 列表还没扫出来 → 稍后重试（最多 6 次，约 9 秒）
        if not self.entries:
            n = getattr(self, "_detect_retry", 0) + 1
            self._detect_retry = n
            if n <= 6:
                QTimer.singleShot(1500, self._detect_running_servers)
            return
        import ctypes
        kernel32 = ctypes.windll.kernel32
        from .widgets import java_pid_alive
        found = 0
        for e in self.entries:
            sd = e.get("server_dir") or e["folder"]
            pid_file = os.path.join(sd, ".mcsm_pid")
            if not os.path.isfile(pid_file):
                continue
            try:
                with open(pid_file, "r") as f:
                    pid = int(f.read().strip())
            except Exception:
                try: os.remove(pid_file)
                except Exception: pass
                continue
            k = self._key(e)
            if k in self.procs:
                continue
            # ★ V2.4.1：OpenProcess 拿到句柄 ≠ 进程活着（僵尸进程
            #   照样返回句柄、PID 复用更是直接误报）。改用
            #   java_pid_alive：退出码 + 映像名双重校验，
            #   死了的/不是 java 的一律当"没在运行"，顺手清掉 pid 文件。
            if java_pid_alive(pid):
                proc = ReattachedProcess(pid, sd, e.get("name", ""))
                self.procs[k] = proc
                self._is_stopping[k] = False
                c = self._console_tab(k, e.get("name") or "server")
                c.proc = proc
                c.set_running(True)
                c.append(
                    "=== 已接管上次仍在运行的服务器（PID=%d）===\n"
                    "· 日志实时显示中\n"
                    "· 原启动器已退出，控制通道断开：发指令不可用\n"
                    "· 点『停止』会直接结束进程（不保存世界），"
                    "建议先在游戏里执行 save-all\n"
                    "· 想优雅停止请关掉这个管理器后用 run.bat 所在目录"
                    "里的控制台操作" % pid)
                self._tail_log(k, sd, c)
                self._refresh_actions()
                self._refresh_status(e)
                found += 1
            else:
                try: os.remove(pid_file)
                except Exception: pass
        if self.procs:
            self.statusBar().showMessage(
                "已接管 %d 个上次仍在运行的服务器" % found)

    def _tail_log(self, key, server_dir, console):
        """V2.1.0: Tail a reattached server log file."""
        log_file = os.path.join(server_dir, "logs", "latest.log")
        if not os.path.isfile(log_file):
            return
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)
                pos = f.tell()
        except Exception:
            pos = 0
        timer = QTimer(self)
        def _poll():
            nonlocal pos
            try:
                with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(pos)
                    new = f.read()
                    if new:
                        console.append(new)
                        pos = f.tell()
            except Exception:
                pass
            proc = self.procs.get(key)
            if proc and hasattr(proc, '_check_alive'):
                if not proc._check_alive():
                    timer.stop()
                    self._log_tailers.pop(key, None)
                    self._finished(proc, 0)
        timer.timeout.connect(_poll)
        timer.start(3000)
        self._log_tailers[key] = timer

    def _close_console_tab(self, idx):
        if idx <= 0:
            return  # 总输出页不关
        w = self.console_tabs.widget(idx)
        for k, c in list(self.consoles.items()):
            if c is w:
                if k in self.procs:
                    QMessageBox.information(
                        self, "还在运行",
                        "这个服务器还在运行，先停止再关闭页签。")
                    return
                del self.consoles[k]
                break
        self.console_tabs.removeTab(idx)

    def _refresh_actions(self):
        """按【当前选中】的服务器决定按钮可用性。

        以前启动后直接 act_start.setEnabled(False)，
        那是全局的 -> 一旦开了任何一个服，就再也开不了第二个。

        ★ V2.3.3：多选时按"选中集合"整体判断 ——
          只要有一个在跑，"停止"就该可点；只要有一个没跑，"启动"就该可点。
        """
        sel = self._selected_entries() or []
        e = sel[0] if sel else self._cur()
        if not e:
            self.act_start.setEnabled(False)
            self.act_stop.setEnabled(False)
            self.act_stop_backup.setEnabled(False)
            self.act_float_console.setEnabled(False)
            return
        keys = [self._key(x) for x in (sel or [e])]
        states = [k in self.procs for k in keys]
        any_running = any(states)
        any_stopped = not all(states)
        self.act_start.setEnabled(any_stopped)
        self.act_stop.setEnabled(any_running)
        self.act_stop_backup.setEnabled(any_running)
        self.act_float_console.setEnabled(any_running)
        # ★ V2.0.1：选中变化时同步窗口标题监测
        try:
            self._update_title_monitor()
        except Exception:
            pass

    # ---------------- V0.5.0：实时状态监控 ----------------
    def _ping_round(self):
        """每 10 秒：对所有运行中的服务器做一次状态探测（Worker 线程）。

        ★ 探测是网络 IO，绝不能卡主线程；busy 集合防止 10 秒轮次
          没跑完就堆积新一轮。
        """
        if not self.procs:
            return
        by_key = {}
        for en in self.entries:
            by_key[self._key(en)] = en
        for k, e in list(by_key.items()):
            if k not in self.procs or k in self._ping_busy:
                continue
            self._ping_busy.add(k)
            port = self._port_of(e)
            proc = self.procs.get(k)
            pid = 0
            try:
                if proc is not None and proc.state() == QProcess.Running:
                    pid = int(proc.processId() or 0)
            except Exception:
                pid = 0

            def job(port=port, pid=pid, on_log=None):
                r = monitor.live_status(port)
                # ★ V2.0.1：进程 CPU/内存（只对真在跑的 java 进程）
                if r.get("online") and pid > 0:
                    st = monitor.proc_stats(pid)
                    r["cpu_pct"] = st.get("cpu_pct")
                    r["mem_mb"] = st.get("mem_mb")
                return r

            w = _track(self, Worker(job, {}, self))
            # ★ V2.0.1.1：ping 线程异常打到总输出，便于排查"无响应"
            try:
                w.log.connect(
                    lambda m, kk=k: self._log_console(
                        "[ping:%s] %s" % (kk, m)))
            except Exception:
                pass
            w.done.connect(
                lambda ok, res, kk=k: self._on_ping_done(kk, ok, res))
            w.start()

    def _on_ping_done(self, key, ok, res):
        self._ping_busy.discard(key)
        if key not in self.procs:
            self._ping_text.pop(key, None)
            getattr(self, "_mon_extra", {}).pop(key, None)
            self._update_title_monitor()
            return
        # ★ V1.5.0：成功显示"运行中 (在线: X/Y | 延迟: Zms)"，
        #   失败显示"运行中 (无响应)"，状态列一眼看懂
        text = monitor.ping_text(res) if ok else None
        if text:
            self._ping_text[key] = "运行中 (" + text + ")"
            # ★ V2.0.1：CPU/内存供窗口标题监测用
            m = getattr(self, "_mon_extra", {})
            if ok and isinstance(res, dict) \
                    and res.get("cpu_pct") is not None:
                m[key] = (res.get("cpu_pct"), res.get("mem_mb"))
        else:
            self._ping_text[key] = "运行中 (无响应)"
        self._apply_ping_ui(key)
        # ★ V2.0.1.1：标题/信息面板更新各自兜底 —— 任一抛异常
        #   都不能中断另一处（此前 __version__ NameError 曾让
        #   信息面板监测整个失效）
        try:
            self._update_title_monitor()
        except Exception:
            pass
        # ★ V2.0.1.1：信息面板同步实时监测（仅当前选中该服时生效）
        try:
            self._update_info_monitor()
        except Exception:
            pass

    def _update_title_monitor(self):
        """★ V2.0.1：窗口标题 = 版本号 + 运行中服务器的实时监测。

        有服务器在运行 → "MC 服务器管理器 v2.0.0 | 服务器名 在线 X/Y
        | 延迟 Zms | CPU 12% | 内存 1560 MB …"；
        停止后自动恢复纯版本号标题。
        """
        from . import __version__  # ★ V2.0.1.1：此前未导入导致 NameError
        base = "MC 服务器管理器 v%s" % __version__
        running = []
        for en in self.entries:
            k = self._key(en)
            if k not in self.procs:
                continue
            name = en.get("name", "")
            txt = self._ping_text.get(k)
            cpu, mem = getattr(self, "_mon_extra", {}).get(k, (None, None))
            bits = []
            if txt:
                bits.append(txt)
            if cpu is not None:
                bits.append("CPU %.0f%%" % cpu)
            if mem is not None:
                bits.append("内存 %.0f MB" % mem)
            running.append((name, " | ".join(bits)))
        if not running:
            self.setWindowTitle(base)
            return
        parts = []
        for name, info in running[:2]:
            parts.append("%s %s" % (name, info) if info else name)
        extra = " · ".join(parts)
        if len(running) > 2:
            extra += " 等 %d 台" % len(running)
        self.setWindowTitle("%s | %s" % (base, extra))

    def _apply_ping_ui(self, key):
        """把监控文本刷到表格状态列 + 图标卡片文字上。"""
        en = None
        for x in self.entries:
            if self._key(x) == key:
                en = x
                break
        if en is None:
            return
        for i, e2 in enumerate(self._view_entries()):
            if self._key(e2) == key:
                self._set_row_status(i, e2)
                break
        try:
            for r in range(self.icon_view.count()):
                it = self.icon_view.item(r)
                if it is None or (it.data(Qt.UserRole) or "") != key:
                    continue
                lines = self._item_text(en, as_lines=True)
                it.setData(Qt.UserRole + 3, lines)
                sz = int(self.disp.get("icon", 110))
                px = max(24, int(sz * 0.62))
                it.setSizeHint(QSize(sz + 16,
                                     px + 34 + 17 * max(1, len(lines))))
                self.icon_view.viewport().update()
                break
        except Exception:
            pass

    def _refresh_status(self, e=None):
        """刷新"状态"显示：表格刷新那一列，图标视图重画文字。

        ★ 必须按 _view_entries() 的视图顺序定位行：
          _fill_table 是按排序 + 筛选后的顺序填的，如果这里用
          self.entries 的原始顺序，排序非 custom 或开着筛选时
          "运行中/未初始化"会标到别的服务器那一行上。
        """
        sorted_entries = self._view_entries()
        if e is None:
            for i, en in enumerate(sorted_entries):
                self._set_row_status(i, en)
            self._refresh_icon_labels()
            return
        target = self._key(e)
        for i, en in enumerate(sorted_entries):
            if self._key(en) == target:
                self._set_row_status(i, en)
                break
        self._refresh_icon_labels()

    def _refresh_icon_labels(self):
        """图标视图下状态由 IconDelegate 画，让 viewport 重画即可。

        ★ 以前这里用 itemWidget 找 QLabel，但 _fill_icons 早就改成
          UserRole+3 + 委托自绘、从不 setItemWidget —— itemWidget
          永远 None，等于什么都没刷新。改成直接 update viewport。
        """
        try:
            if self.icon_view.isVisible():
                self.icon_view.viewport().update()
        except Exception:
            pass

    def _set_row_status(self, row, e):
        if row >= self.table.rowCount():
            return
        running = self._key(e) in self.procs
        if running:
            ping = self._ping_text.get(self._key(e))
            if ping:
                txt, color = ping, PAL.SUCCESS
            else:
                txt, color = "运行中", PAL.SUCCESS
        elif e.get("notes"):
            txt, color = "未初始化", PAL.DANGER
        else:
            txt, color = "就绪", None
        it = self.table.item(row, 5)
        if it is None:
            it = QTableWidgetItem()
            self.table.setItem(row, 5, it)
        it.setText(txt)
        if color:
            it.setForeground(QColor(_on_bg(color)))
        else:
            it.setForeground(QColor(_fg()))

    def _port_of_cached(self, e):
        import time
        k = self._key(e)
        now = time.time()
        cached = self._port_cache.get(k)
        if cached and now - cached["t"] < 10:  # 缓存 10 秒
            return cached["port"]
        p = self._port_of(e)
        self._port_cache[k] = {"port": p, "t": now}
        return p

    def _port_of(self, e):
        """读这个服的 server-port（默认 25565）。"""
        sd = e.get("server_dir") or e.get("folder") or ""
        p = os.path.join(sd, "server.properties")
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("server-port="):
                        v = line.split("=", 1)[1].strip()
                        return int(v) if v.isdigit() else 25565
        except Exception:
            pass
        return 25565

    def _check_port_conflict(self, e):
        """多开时端口不能撞。撞了 MC 会起不来（Address already in use）。"""
        # ★ B1：走端口缓存，避免每次启动把全部服务器目录的
        #   server.properties 重新读一遍（十几个服时是真的会拖慢）
        port = self._port_of_cached(e)
        for en in self.entries:
            if self._key(en) not in self.procs:
                continue
            if self._port_of_cached(en) != port:
                continue
            r = QMessageBox.warning(
                self, "端口冲突",
                "『%s』正在运行，占用的也是 %d 端口。\n"
                "『%s』用同一个端口会启动失败"
                "（Address already in use）。\n\n"
                "要先改掉其中一个的 server-port 吗？"
                % (en.get("name"), port, e.get("name")),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes)
            if r == QMessageBox.Yes:
                self._suggest_port(e)
                return False
            return True  # 用户坚持，让他试
        return True

    def _firewall_allow(self, e):
        """★ V0.4.0：放行 server-port 的 TCP 入站（Windows 防火墙）。

        防止朋友联机时被防火墙拦截。没有管理员权限 / 不是 Windows
        就静默跳过，绝不让防火墙操作打断服务器启动。
        ★ V1.9.6：会话级去重 —— 同一端口只放行一次（连点启动、
        自动重启都会再次走到这里，之前会重复打印日志 + 重复建规则）。
        """
        if os.name != "nt":
            return
        try:
            port = self._port_of(e)
        except Exception:
            port = None
        if not port:
            return
        try:
            # 已放行过的端口：同一会话内直接跳过（防连点/自动重启重复）
            done = getattr(self, "_fw_done", None)
            if done is None:
                done = self._fw_done = set()
            if port in done:
                return
            done.add(port)
            import threading
            import subprocess as _sp
            # 先查系统规则：已存在同名规则也不重复建（跨会话幂等）
            check_cmd = (
                ["powershell", "-NoProfile", "-Command",
                 "Get-NetFirewallRule -DisplayName 'MCSM_Server_%d'"
                 " -ErrorAction SilentlyContinue | Measure-Object |"
                 " Select-Object -ExpandProperty Count" % port])
            cmd = (
                ["powershell", "-NoProfile", "-Command",
                 "New-NetFirewallRule -DisplayName 'MCSM_Server_%d'"
                 " -Direction Inbound -Protocol TCP -LocalPort %d"
                 " -Action Allow -ErrorAction SilentlyContinue"
                 % (port, port)])

            def _run():
                # ★ V1.4.0：防火墙命令挪到后台线程，主线程绝不同步等待
                # ★ V1.6.2：加 CREATE_NO_WINDOW，禁止弹出黑色 CMD 窗
                try:
                    r = _sp.run(check_cmd, capture_output=True, timeout=20,
                                creationflags=getattr(
                                    _sp, "CREATE_NO_WINDOW", 0))
                    cnt = (r.stdout or b"").strip()
                    try:
                        exists = int(cnt) > 0
                    except Exception:
                        exists = False
                    if exists:
                        return  # 规则已在，不重复建
                except Exception:
                    pass  # 查不到就当作没有，继续放行
                try:
                    _sp.run(cmd, capture_output=True, timeout=30,
                            creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
                except Exception:
                    pass  # 没权限就静默，别影响启动

            threading.Thread(target=_run, daemon=True).start()
            self._log_console("[防火墙] 正在后台放行 TCP %d（MCSM_Server_%d）"
                              % (port, port))
        except Exception:
            pass

    def _suggest_port(self, e):
        """帮忙改成一个没被占用的端口并提示。"""
        used = set()
        for en in self.entries:
            if self._key(en) in self.procs:
                used.add(self._port_of(en))
        port = self._port_of(e)
        while port in used:
            port += 1
        sd = e.get("server_dir") or e.get("folder") or ""
        p = os.path.join(sd, "server.properties")
        try:
            lines = []
            if os.path.isfile(p):
                with open(p, encoding="utf-8", errors="replace") as f:
                    lines = f.read().splitlines()
            out, hit = [], False
            for ln in lines:
                if ln.strip().startswith("server-port="):
                    out.append("server-port=%d" % port); hit = True
                else:
                    out.append(ln)
            if not hit:
                out.append("server-port=%d" % port)
            fsutil.atomic_write_text(p, "\n".join(out) + "\n",
                                     encoding="utf-8", newline="\n",
                                     keep_backup=True)
            self._log_console("[端口] 已改为 %d" % port)
            QMessageBox.information(
                self, "已改端口",
                "『%s』的 server-port 已改成 %d。\n"
                "再点一次『启动』即可。" % (e.get("name"), port))
        except Exception as ex:
            QMessageBox.warning(self, "改端口失败", repr(ex))
