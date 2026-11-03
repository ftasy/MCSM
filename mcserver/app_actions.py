# -*- coding: utf-8 -*-
"""操作入口：初始化服务端、生成 run.bat、统一搬迁、正版登录准备。

从 app.py 的 MainWindow 拆出，作为 mixin 混入主窗口。
"""

import os
import sys
import traceback

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox
from .core import versions, java as javacore, builder, migrate, install, modman
from .dialogs import CreateDialog, MigrateDialog, Worker, _track


class ActionsMixin:
    """操作入口：初始化服务端、生成 run.bat、统一搬迁、正版登录准备。"""

    # ---------------- 操作 ----------------
    def do_create(self):
        e = self._cur()
        if not e:
            QMessageBox.information(self, "提示", "请先在列表里选中一个服务器")
            return
            
        try:
            dlg = self._style_dlg(CreateDialog(e, parent=self))
        except Exception as ex:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self, "打开初始化窗口失败",
                "程序在创建初始化窗口时遇到了错误：\n\n%s\n\n"
                "详细追踪：\n%s" % (ex, traceback.format_exc()[-500:]))
            return

        # ★ V1.6.2：非模态打开 —— 用户在下载/初始化时仍可操作主界面
        dlg.setWindowModality(Qt.NonModal)
        # 保持引用，防止对话框被 GC 回收导致闪退
        self._dlg_refs = getattr(self, "_dlg_refs", [])
        self._dlg_refs.append(dlg)

        def _on_create_done():
            info = getattr(dlg, "result_info", None) or {}
            if info:
                # ★ 只更新 java / xms / xmx 三个字段。
                #   以前整条覆盖会清掉用户之前保存的
                #   last_start / auto_restart / auto_backup /
                #   backup_interval / icon 等设置 ——
                #   表现就是"我开了自动重启，初始化服务端后没了"。
                sv = self.data.setdefault("servers", {}).setdefault(
                    self._key(e), {})
                sv["java"] = info.get("java")
                sv["xms"] = info.get("xms", 1024)
                sv["xmx"] = info.get("xmx", 4096)
                # ★ 自动化三项（初始化对话框里默认全勾，可在此覆盖）
                sv["aikar"] = bool(info.get("aikar", True))
                sv["auto_restart"] = bool(info.get("auto_restart", True))
                sv["auto_backup"] = bool(info.get("auto_backup", True))
                self._save_data()

            # ★ 扫描完成后自动重新选中该服务器，刷新右侧信息面板
            target = os.path.abspath(e.get("folder") or "")
            def select_me():
                for i, en in enumerate(self._sorted_entries()):
                    if os.path.abspath(en.get("folder") or "") == target:
                        self.table.selectRow(i)
                        if self.view_mode == "icon" and self.icon_view.count() > i:
                            self.icon_view.setCurrentRow(i)
                        self.on_sel()  # 强制刷新信息面板
                        break

            self.do_scan(then=select_me)
            try:
                self._dlg_refs.remove(dlg)
            except ValueError:
                pass

        dlg.finished.connect(lambda _r: _on_create_done())
        dlg.show()

    def do_gen_bat(self):
        e = self._cur()
        if not e:
            return
        sd = e.get("server_dir") or e["folder"]
        v = e.get("version")
        if not v:
            QMessageBox.warning(self, "缺版本", "该存档读不出版本，"
                                                "请先用『初始化服务端』")
            return
        need = versions.need_java(v)
        sv = self._saved(e)
        jpath = sv.get("java")
        if not jpath:
            j = javacore.pick_for(need)
            if not j:
                QMessageBox.warning(self, "缺 Java",
                                    "没找到 Java %s（本服需要 %s）" % (need, need))
                return
            jpath = j["path"]
        xms = "%dM" % sv.get("xms", 1024)
        xmx = "%dM" % sv.get("xmx", 4096)
        # ★ 不能只看 jars，新版 Forge 没有启动 jar（用 args 文件）。
        #   必须走 install.find_launch 判断真实的启动方式。
        L = install.find_launch(sd)
        launch_mode = L.get("launch") or "jar"
        jar = L.get("jar") or ((e.get("jars") or ["server.jar"])[0])
        args_rel = L.get("args_rel")

        # ★ 防呆：如果是 jar 模式，校验文件到底存不存在
        if launch_mode == "jar":
            jar_path = os.path.join(sd, jar)
            if not os.path.isfile(jar_path):
                QMessageBox.warning(
                    self, "核心缺失", 
                    "没找到核心文件：\n%s\n\n请先点击『初始化服务端』下载核心。" % jar_path
                )
                return

        bat = builder.build_run_bat(
            jpath, jar, xms, xmx,
            launch=launch_mode, args_rel=args_rel,
            pause=sv.get("pause", "error"),
            aikar=sv.get("aikar", False))
        p = os.path.join(sd, "run.bat")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(bat)
        self.statusBar().showMessage("已生成 %s" % p)
        QMessageBox.information(self, "完成", "已生成:\n%s" % p)

    def do_migrate(self):
        dlg = self._style_dlg(MigrateDialog(self.root, self))
        if not dlg.exec():
            return
        v = dlg.values()
        plans = migrate.plan(v["src"], v["dst"])
        if not plans:
            QMessageBox.information(self, "提示", "没有可搬迁的文件夹")
            return
        # ★ 搬迁可能是几十 GB 的复制，放后台线程，别冻住界面
        self.statusBar().showMessage(
            "搬迁中 ...（%d 个文件夹）" % len(plans))

        def job(on_log=None):
            def prog(i, n, name):
                if on_log:
                    on_log("(%d/%d) %s" % (i + 1, n, name))
            # ★ 把 on_log 一并传进去，方便在复制大文件时看到动态
            return migrate.do_migrate(plans, copy=v["copy"],
                                      on_progress=prog, on_log=on_log)

        w = _track(self, Worker(job, {}, self))
        self._migrate_worker = w
        w.log.connect(self._log_console)
        w.done.connect(lambda ok, res: self._after_migrate(ok, res, v))
        w.start()

    def _after_migrate(self, ok, res, v):
        w, self._migrate_worker = getattr(
            self, "_migrate_worker", None), None
        if w is not None:
            self._forget_worker(w)
        if not ok:
            self.statusBar().showMessage("搬迁失败")
            QMessageBox.critical(self, "搬迁失败", str(res))
            return
        fails = ["%s: %s" % (name, msg)
                 for name, good, msg in res if not good]
        self.root = v["dst"]
        self.ed_root.setText(self.root)
        self.data["root"] = self.root
        self._save_data()
        self.do_scan()
        if fails:
            QMessageBox.warning(self, "部分失败", "\n".join(fails))
        else:
            self.statusBar().showMessage("搬迁完成")
            QMessageBox.information(self, "完成", "搬迁完成")

    def do_open_dir(self):
        e = self._cur()
        if not e:
            return
        d = e.get("server_dir") or e["folder"]
        try:
            if sys.platform == "win32":
                os.startfile(d)
            else:
                import subprocess
                subprocess.Popen(["xdg-open", d])
        except Exception as ex:
            QMessageBox.warning(self, "打不开", repr(ex))

    # ---------------- V1.3.0 多源登录准备 ----------------
    def _prepare_auth(self, e, then):
        """按 auth_type 准备登录环境；全部就绪后回调 then() 继续启动。"""
        sd = e.get("server_dir") or e["folder"]
        sv = self._saved(e)
        auth_type = sv.get("auth_type") or "offline"
        # ★ V1.7.0-2：多源共存支持多选第三方源（auth_urls 列表优先，
        #   兼容旧版单值 auth_url）
        auth_urls = sv.get("auth_urls") or []
        if not auth_urls:
            old = (sv.get("auth_url") or "").strip()
            if old:
                auth_urls = [old]
        auth_url = (auth_urls[0] if auth_urls else "").strip()
        loader = (e.get("loader") or "").lower()
        v = e.get("version") or ""
        online = auth_type in ("mojang", "multi")
        self._set_online_mode(sd, online)

        if auth_type != "multi":
            # 正版 / 离线：清理可能残留的登录模组/插件（改名保留）
            self._cleanup_auth_components(sd)
            then()
            return

        # ===== 多源共存 =====
        if loader == "paper":
            comp = install.AUTH_COMPONENTS["paper"]
            need = not self._has_auth_jar(
                sd, "plugins", ["multilogin"])
        else:
            comp = install.AUTH_COMPONENTS["mod"]
            need = not self._has_auth_jar(
                sd, "mods", ["multiyggdrasil", "akiyggdrasil"])
        if not need:
            self._write_auth_cfg(e, auth_url, auth_urls[1:])
            self._auth_multi_hint(e)
            then()
            return

        # 早期 Forge 兼容性提示
        if loader == "forge" and self._is_early_forge(v):
            QMessageBox.information(
                self, "早期版本可能不兼容",
                "当前是较早期的 Forge 版本，多源登录模组"
                "（MultiYggdrasil）可能不兼容。\n\n"
                "若启动后第三方玩家无法登录，请改用正版或离线模式。")

        r = QMessageBox.question(
            self, "安装多源登录组件（启动暂停中）",
            "『%s』已开启多源共存，启动前需要 %s 登录组件。\n\n"
            "将自动从 Modrinth 下载并配置（后台进行，不卡界面）。\n"
            "继续吗？\n\n（选『否』则直接启动，第三方玩家将无法登录）"
            % (e.get("name") or "", comp["name"]),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if r != QMessageBox.Yes:
            then()
            return
        self._log_console("=== 正在下载多源登录组件 %s ===" % comp["name"])
        w = _track(self, Worker(self._dl_auth_worker, {
            "sd": sd, "v": v, "loader": loader, "comp": comp}, self))
        w.log.connect(lambda m: self._log_console(m))
        w.done.connect(
            lambda ok, res: self._on_auth_dl(ok, res, e, auth_url,
                                             auth_urls[1:], then))
        w.start()

    def _dl_auth_worker(self, sd="", v="", loader="", comp=None, on_log=None):
        return install.install_auth_component(
            sd, v, loader, comp["kind"], comp["slug"], on_log=on_log)

    def _on_auth_dl(self, ok, res, e, auth_url, extra_urls, then):
        if ok and (res or {}).get("ok"):
            self._log_console("[登录组件] 安装完成: %s" % (res or {}).get("name"))
            self._write_auth_cfg(e, auth_url, extra_urls)
        else:
            self._log_console("[登录组件] 安装失败: %s"
                              % ((res or {}).get("reason") or "?"))
            loader = (e.get("loader") or "").lower()
            comp = (install.AUTH_COMPONENTS["paper"] if loader == "paper"
                    else install.AUTH_COMPONENTS["mod"])
            if comp.get("hint"):
                QMessageBox.information(
                    self, "需要手动安装 %s" % comp["name"],
                    "%s\n\n%s" % (comp["name"], comp["hint"]))
        self._auth_multi_hint(e)
        then()

    def _auth_multi_hint(self, e):
        """Paper 多源共存：建议把 enforce-secure-profile 设为 false。"""
        if (e.get("loader") or "").lower() != "paper":
            return
        try:
            p = os.path.join(e.get("server_dir") or e["folder"],
                             "server.properties")
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    for ln in f:
                        if ln.strip().startswith(
                                "enforce-secure-profile=false"):
                            return  # 已设置过就不再提示
        except Exception:
            pass
        QMessageBox.information(
            self, "建议关闭强制安全档案",
            "Paper 多源共存建议把 server.properties 里的\n"
            "『enforce-secure-profile』设为 false，\n"
            "否则部分外置登录玩家可能被拒绝。\n\n"
            "（可在『服务器配置』里修改后重启）")

    def _has_auth_jar(self, sd, folder, prefixes):
        d = os.path.join(sd, folder)
        try:
            if not os.path.isdir(d):
                return False
            for fn in os.listdir(d):
                low = fn.lower()
                if low.endswith(".jar") and not low.endswith(".disabled"):
                    for p in prefixes:
                        if low.startswith(p):
                            return True
        except Exception:
            pass
        return False

    def _cleanup_auth_components(self, sd):
        """正版/离线模式下，把残留的登录模组/插件改名 .disabled（不删除）。"""
        for folder in ("mods", "plugins"):
            try:
                for m in modman.scan_mods_dir(os.path.join(sd, folder)):
                    n = (m.get("filename") or "").lower()
                    if (n.startswith("multiyggdrasil")
                            or n.startswith("akiyggdrasil")
                            or n.startswith("multilogin")):
                        if m.get("enabled"):
                            try:
                                modman.set_enabled(m["path"], False)
                            except Exception:
                                pass
            except Exception:
                pass

    def _set_online_mode(self, sd, online):
        """改 server.properties 的 online-mode（无该行则追加）。"""
        p = os.path.join(sd, "server.properties")
        try:
            if not os.path.isfile(p):
                return
            with open(p, "r", encoding="utf-8") as f:
                lines = f.readlines()
            val = "true" if online else "false"
            hit = False
            for i, ln in enumerate(lines):
                if ln.strip().startswith("online-mode="):
                    lines[i] = "online-mode=%s\n" % val
                    hit = True
                    break
            if not hit:
                lines.append("online-mode=%s\n" % val)
            with open(p, "w", encoding="utf-8") as f:
                f.writelines(lines)
        except Exception:
            pass

    def _write_auth_cfg(self, e, auth_url, extra_urls=None):
        """写多源登录配置：mods 类 -> config/multi-yggdrasil.ini。

        ★ V1.7.0-2：多选第三方源时，第一个源作为 yggdrasilApiRoot
          （MultiYggdrasil 单 root 格式），其余源以注释写入提示，
          方便服主手动扩展（Paper 的 MultiLogin 由插件自身管理多服务）。
        """
        loader = (e.get("loader") or "").lower()
        if loader == "paper":
            return  # MultiLogin 配置由插件首次启动自动生成
        if not auth_url:
            return
        sd = e.get("server_dir") or e["folder"]
        cfg_dir = os.path.join(sd, "config")
        try:
            os.makedirs(cfg_dir, exist_ok=True)
        except Exception:
            return
        path = os.path.join(cfg_dir, "multi-yggdrasil.ini")
        extra = [u.strip().replace('"', "") for u in (extra_urls or [])
                 if u and u.strip() != auth_url]
        body = (
            "allowOfficial = true\n"
            "allowYggdrasil = true\n"
            'yggdrasilApiRoot = "%s"\n' % auth_url.replace('"', "")
        )
        if extra:
            body += ("\n; 其余第三方源（MultiYggdrasil 单 root，"
                     "如需多源请手动扩展）：\n"
                     + "".join("; yggdrasilApiRoot = \"%s\"\n" % u
                               for u in extra))
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    old = f.read()
                if auth_url in old:
                    return
                QMessageBox.information(
                    self, "配置文件已存在",
                    "config/multi-yggdrasil.ini 已存在且 API 地址与当前设置"
                    "不同，为避免覆盖你的自定义配置，本次没有写入。\n\n"
                    "如需更换皮肤站，请手动修改该文件。")
                return
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
            self._log_console("[登录] 已生成 multi-yggdrasil.ini")
        except Exception:
            pass

    def _is_early_forge(self, v):
        """极早期 Forge（MultiYggdrasil 支持 1.14+，更早不兼容）。"""
        try:
            from .core import versions as _v
            return (_v.mc_version_key(v, newest_first=False)
                    < _v.mc_version_key("1.14", newest_first=False))
        except Exception:
            return False
