# -*- coding: utf-8 -*-
"""列表视图：图标卡片、表格、拖动排序、右键菜单与图标预取。

从 app.py 的 MainWindow 拆出，作为 mixin 混入主窗口。
"""

import os
import sys
import json
import re
import traceback

from PySide6.QtGui import QFont, QColor, QPixmap, QPainter
from PySide6.QtCore import Qt, QSize, QPoint, QEvent
from PySide6.QtWidgets import (
    QLabel, QListWidget, QListWidgetItem, QTableWidgetItem)
from .core import scanner, versions, install
from .dialogs import Worker, _track
from .core.palette import PAL, LOADER_DOT_COLORS


class ViewsMixin:
    """列表视图：图标卡片、表格、拖动排序、右键菜单与图标预取。"""

    # ---------------- 列表视图：图标 / 表格 / 排序 ----------------
    def _icon_for(self, e, px=64):
        """给服务器做图标：优先用存档里的 icon.png。

        没有就用加载器首字母画一个彩色圆角方块。
        """
        from PySide6.QtGui import QPixmap, QPainter, QBrush
        # 0) 用户自己指定的图标（右键『设置图标』存进 servers.json）
        #    优先级最高 —— 自己选的当然要盖过自动识别的
        custom = (self._saved(e).get("icon") or "").strip()
        if custom and os.path.isfile(custom):
            pm = QPixmap(custom)
            if not pm.isNull():
                return pm.scaled(px, px, Qt.KeepAspectRatio,
                                Qt.SmoothTransformation)
        # 1) 服务器根目录的 server-icon.png
        for base in (e.get("server_dir"), e.get("folder")):
            if not base:
                continue
            for nm in ("server-icon.png", "icon.png"):
                p = os.path.join(base, nm)
                if os.path.isfile(p):
                    pm = QPixmap(p)
                    if not pm.isNull():
                        return pm.scaled(px, px, Qt.KeepAspectRatio,
                                        Qt.SmoothTransformation)
        # 2) 存档目录里的 icon.png
        wd = e.get("world_dir")
        if wd:
            p = os.path.join(wd, "icon.png")
            if os.path.isfile(p):
                pm = QPixmap(p)
                if not pm.isNull():
                    return pm.scaled(px, px, Qt.KeepAspectRatio,
                                    Qt.SmoothTransformation)
        # 3) 画一个
        col = LOADER_DOT_COLORS.get(
            (e.get("loader") or "").lower(), "#607d8b")
        pm = QPixmap(px, px)
        pm.fill(Qt.transparent)
        from PySide6.QtGui import QColor
        ptr = QPainter(pm)
        ptr.setRenderHint(QPainter.Antialiasing)
        ptr.setBrush(QBrush(QColor(col)))
        ptr.setPen(Qt.NoPen)
        # ★ 原来写死 (0, 0, 64, 64) —— QPixmap 是 px x px，
        #   px != 64 时圆角矩形要么缩在左上角（px > 64），
        #   要么被裁掉右下角（px < 64）。改成按 px 铺满整张位图。
        # ★ 圆角半径也按 px 缩放：原来写死 12，px=24 时圆角占 50%
        #   看着像圆形，px=136 时占 9% 看着像方形。改成 px/8、
        #   夹在 4~16 之间，各尺寸视觉一致。
        rad = max(4, min(16, px // 8))
        ptr.drawRoundedRect(0, 0, px, px, rad, rad)
        ptr.setPen(QColor(PAL.WHITE))
        f = QFont()
        f.setPointSize(max(9, int(px * 0.34)))
        f.setBold(True)
        ptr.setFont(f)
        txt = (e.get("loader") or "?")[:1].upper()
        ptr.drawText(pm.rect(), Qt.AlignCenter, txt)
        ptr.end()
        return pm

    def _clean_ver(self, e):
        """★ V1.7.1-3：显示用版本净化 —— Paper 构建号等
        不像 MC 版本的值显示层再兜底清洗一次。"""
        try:
            v = e.get("version")
            if not v:
                return None
            cv = scanner.clean_mc_version(v)
            return cv or v
        except Exception:
            return e.get("version")

    def _tip_for(self, e):
        """鼠标悬停时弹出的注释（PCL 风格）。

        ★ 图标下面是精简的一两行，完整信息放这里 ——
          目录、Java、端口、存档名这些不占版面，但很常用。
        """
        L = []
        name = self._display_name(e) or e.get("world_name") or "?"
        L.append(name)
        bits = []
        if e.get("version"):
            bits.append("MC %s" % self._clean_ver(e))
        if e.get("loader"):
            bits.append(versions.LOADER_LABEL.get(e["loader"],
                                                  e["loader"]))
        if bits:
            L.append(" / ".join(bits))
        if e.get("world_name") and e.get("world_name") != name:
            L.append("存档名：%s" % e["world_name"])
        if e.get("dir"):
            L.append("目录：%s" % e["dir"])
        jv = e.get("java") or e.get("java_path")
        if jv:
            L.append("Java：%s" % (jv if len(str(jv)) < 48
                                   else "..." + str(jv)[-45:]))
        # ★ 改用带有缓存的函数，防止悬停时疯狂读盘卡死界面
        port = self._port_of_cached(e)
        if port:
            L.append("端口：%s" % port)
        if e.get("regions"):
            L.append("Region：%s 个" % e["regions"])
        if self._key(e) in self.procs:
            L.append("● 运行中")
        elif e.get("notes"):
            L.append("● 未初始化：%s" % (e.get("notes") or "")[:60])
        return "\n".join(L)

    def _item_text(self, e, as_lines=False):
        """按显示设置拼出图标下面的文字。

        as_lines=True 时返回纯文本列表（给 delegate 用）；
        False 时返回富文本 HTML（保留兼容，实际已无人调用）。
        """
        show = self.disp.get("show", None)
        if show is None:
            show = ["name", "version", "loader", "status"]
        lines = []
        # 用文件夹名（导入时已清洗过），不用 level.dat 里的世界名 ——
        # 后者可能带 §b§l 颜色码和冒号，显示出来是乱码
        name = self._display_name(e) or e.get("world_name") or "?"
        if "name" in show:
            lines.append(name)
        sub = []
        if "version" in show and e.get("version"):
            sub.append(self._clean_ver(e))
        if "loader" in show and e.get("loader"):
            sub.append(versions.LOADER_LABEL.get(
                e["loader"], e["loader"]))
        if "world" in show and e.get("world_name"):
            sub.append(e["world_name"])
        if "regions" in show:
            sub.append("%d region" % (e.get("regions") or 0))
        if sub:
            lines.append(" · ".join(sub))
        if "status" in show:
            if self._key(e) in self.procs:
                # ★ V0.5.0：运行中有监控文本就显示"在线: 3/20 | 延迟: 2ms"
                ping = self._ping_text.get(self._key(e))
                if ping:
                    lines.append(ping)
                else:
                    lines.append("● 运行中")
            elif e.get("notes"):
                lines.append("● 未初始化")
        # ★ 一个都没勾时不能崩（IndexError），至少显示名称
        if not lines:
            lines = [str(self._display_name(e)
                         or e.get("world_name") or "?")]
        if as_lines:
            return lines
        out = "<b>%s</b>" % lines[0]
        for ln in lines[1:]:
            out += "<br><span style='font-size:9pt'>%s</span>" % ln
        return out

    def _sorted_entries(self):
        """按当前排序方式返回 entries（不改 self.entries 本身）。"""
        mode = self.sort_mode
        if mode == "custom":
            order = self.disp.get("order", {})
            def k(e):
                kk = self._key(e)
                return (order.get(kk, 9999),
                        str(self._display_name(e) or ""))
            return sorted(self.entries, key=k)
        if mode == "version":
            from .core import versions as _V
            def vk(e):
                # ★ 用 versions.mc_version_key（只取版本主体、补齐长度、
                #   预发布排后面），不再 findall 抓所有数字。
                #   旧写法在版本号带后缀时长度不一致，
                #   会排出"1.21.10 夹着 1.21.11"这种乱序。
                # 同版本时用名称兜底，保证顺序稳定。
                return (_V.mc_version_key(e.get("version")),
                        str(self._display_name(e) or "").lower())
            return sorted(self.entries, key=vk)
        if mode == "recent":
            def rk(e):
                t = self._saved(e).get("last_start")
                return -(float(t) if t else 0)
            return sorted(self.entries, key=rk)
        if mode == "size":
            # 按文件夹大小排序，大的在前（缓存 60 秒）。
            # ★ 未缓存时先给占位值，绝不在这里同步扫盘卡主线程 ——
            #   真正的大小由 _on_sort_changed 触发的后台 Worker
            #   （_prefetch_sizes）算完写缓存后自动刷新。
            import time as _t
            cache = self._size_cache
            now = _t.time()

            def sk(e):
                key = self._key(e)
                cached = cache.get(key)
                if cached and now - cached.get("t", 0) < 60:
                    return -cached["s"]
                return -1  # 未缓存：占位，等后台算完 _fill_views 刷新
            return sorted(self.entries, key=sk)
        if mode == "mtime":
            # 按文件夹最后修改时间，新的在前
            def mk(e):
                d = e.get("server_dir") or e.get("folder") or ""
                try:
                    return -os.path.getmtime(d)
                except Exception:
                    return 0
            return sorted(self.entries, key=mk)
        # name
        return sorted(self.entries,
                      key=lambda e: str(self._display_name(e) or "").lower())

    def _view_entries(self):
        """当前真正显示在列表里的条目 = 排序结果 + 搜索框筛选。

        ★ 表格行号 / 图标序号都是"按这个顺序填的"，所以任何
          "第 N 行是哪台服"的映射都必须走这里。以前全部直接用
          _sorted_entries()，一旦加了筛选，行号就会对不上 ——
          选中的是一台服、操作的却是另一台。
        """
        items = self._sorted_entries()
        q = (getattr(self, "filter_text", "") or "").strip().lower()
        if not q:
            return items
        words = q.split()
        out = []
        for e in items:
            hay = " ".join(str(x or "") for x in (
                self._display_name(e), e.get("name"), e.get("version"),
                e.get("loader"),
                versions.LOADER_LABEL.get(e.get("loader"), ""),
                e.get("world_name"), e.get("notes"),
            )).lower()
            if all(w in hay for w in words):
                out.append(e)
        return out

    def _focus_filter(self):
        """Ctrl+F：聚焦服务器搜索框。"""
        try:
            self.ed_filter.setFocus()
            self.ed_filter.selectAll()
        except Exception:
            pass

    def _on_filter_changed(self, text):
        """搜索框内容变化 -> 重建两个视图。"""
        self.filter_text = (text or "").strip()
        self._fill_views()
        n = self.icon_view.count() if self.view_mode == "icon" \
            else self.table.rowCount()
        total = len(self.entries)
        if self.filter_text:
            self.statusBar().showMessage(
                "筛选『%s』：%d / %d 台" % (self.filter_text, n, total), 4000)
            if n == 0:
                self.statusBar().showMessage(
                    "没有匹配『%s』的服务器（按 Esc 清空筛选）"
                    % self.filter_text, 6000)

    def _fill_icons(self):
        """填充图标视图。

        文字不再用 setItemWidget(QLabel)，而是塞进 UserRole+3
        交给 IconDelegate 画 —— 这样才能做悬停/点击动画
        （QListWidget 自带的 IconMode 画不了动画）。
        """
        self.icon_view.clear()
        sz = int(self.disp.get("icon", 110))
        # 图标本体随"图标大小"一起放大（以前写死 64，
        # 于是调大小只把卡片撑大、文字变多，图没跟着长）
        px = max(24, int(sz * 0.62))
        self.icon_view._icon_px = px
        for e in self._view_entries():
            it = QListWidgetItem()
            pm = self._icon_for(e, px)
            if isinstance(pm, QPixmap) and not pm.isNull():
                it.setData(Qt.DecorationRole, pm)
            it.setData(Qt.UserRole, self._key(e))
            it.setData(Qt.UserRole + 2, e.get("loader") or "unknown")
            # ★ 以前走 _item_text 拿富文本，再用 re.sub 剥标签、
            #   split("\n") —— 但 _item_text 的分隔符是 <br> 不是 \n，
            #   re.sub 把 <br> 一起删了，split 找不到换行，
            #   多行信息全合并成 1 行，图标下面就显示一整串。
            #   现在直接让 _item_text 返回 list，delegate 拿到真正的行列表。
            lines = self._item_text(e, as_lines=True)
            it.setData(Qt.UserRole + 3, lines)
            it.setToolTip(self._tip_for(e))   # 悬停注释（PCL 风格）
            self.icon_view.addItem(it)
            it.setSizeHint(QSize(sz + 16,
                                 px + 34 + 17 * max(1, len(lines))))
        # 网格对齐，拖动时落点才跟视觉一致
        try:
            self.icon_view.setGridSize(
                QSize(sz + 18, px + 38 + 17 * 3))
        except Exception:
            pass

    def _icon_pressed(self, index):
        """按下时通知 delegate 做缩小动画。"""
        try:
            self.icon_delegate.set_press(index.row())
        except Exception:
            pass

    def eventFilter(self, obj, event):
        """图标区的动画回弹 + 自己实现的拖动排序。"""
        try:
            from PySide6.QtCore import QEvent
            # ★ 这里以前写 getattr(x, "viewport", None) —— 那拿到的是
            #   "方法对象"，不是真正的 viewport 控件，于是下面的
            #   "obj is vp_icon" 永远是 False，整个 eventFilter
            #   一次都没生效过（连点击回弹动画也是假的）。必须调用它。
            try:
                vp_icon = self.icon_view.viewport()
            except Exception:
                vp_icon = None
            try:
                vp_tab = self.table.viewport()
            except Exception:
                vp_tab = None
            t = event.type()
            # ★ v2.3.2：搜索框里按 Esc = 清空筛选
            if obj is getattr(self, "ed_filter", None) \
                    and t == QEvent.KeyPress:
                if event.key() == Qt.Key_Escape:
                    self.ed_filter.clear()
                    return True
            # ★ 这里以前写的是 QEvent.MouseButtonMove —— 那是 Qt3 的
            #   老名字，PySide6 里根本没有，一取就 AttributeError，
            #   而下面又有 try/except 把异常吞了，于是拖动逻辑
            #   从来没执行过（表现就是"完全拖不动"）。Qt6 里按住
            #   移动的鼠标事件就是 MouseMove。
            MOVE = (QEvent.MouseButtonPress, QEvent.MouseMove,
                    QEvent.MouseButtonRelease)
            if t in MOVE:
                pos = self._ev_pos(event)
                if obj is vp_icon and self.view_mode == "icon":
                    if t == QEvent.MouseButtonPress:
                        self._drag_begin(pos)
                    elif t == QEvent.MouseMove:
                        self._drag_move(pos)
                    else:
                        self._drag_end()
                elif obj is vp_tab and self.view_mode == "table":
                    if t == QEvent.MouseButtonPress:
                        self._tdrag_begin(pos)
                    elif t == QEvent.MouseMove:
                        self._tdrag_move(pos)
                    else:
                        self._tdrag_end()
            if obj is vp_icon:
                if t == QEvent.MouseButtonRelease:
                    self.icon_delegate.clear_press()
                elif t == QEvent.Leave:
                    self.icon_delegate.leave()
                    self._drag_end()
        except Exception:
            # ★ 以前这里直接 pass，于是"取了不存在的枚举"这类错误
            #   永远看不见，拖动功能静悄悄失效。现在第一次出错时
            #   打出来（不重复刷屏）。
            if not getattr(self, "_ef_warned", False):
                self._ef_warned = True
                try:
                    import traceback
                    sys.stderr.write("[eventFilter] %s\n"
                                     % traceback.format_exc())
                except Exception:
                    pass
        return super().eventFilter(obj, event)

    # ---------------- 拖动排序（自己实现） ----------------
    # 为什么不用 Qt 内置的 InternalMove：见上面 icon_view 初始化处的
    # 注释 —— 内置那套在这会"按住拖没反应"。这里直接接管鼠标，
    # 拖到哪就插到哪，松手立刻保存。
    DRAG_SLOP2 = 36          # 位移超过 ~6px 才算拖动，避免点一下就乱动

    def _ev_pos(self, event):
        """Qt6 用 position()，老写法 pos() 兜底。"""
        try:
            return event.position().toPoint()
        except Exception:
            try:
                return event.pos()
            except Exception:
                return QPoint(0, 0)

    def _drag_begin(self, pos):
        self._drag = None
        # ★ 筛选状态下只显示了一部分，拖动后存下来的 order 会
        #   把隐藏的服务器挤到错误的相对位置（可见项只拿到 0..N）。
        #   直接不允许拖，比悄悄弄乱顺序好。
        if (getattr(self, "filter_text", "") or "").strip():
            return
        it = self.icon_view.itemAt(pos)
        if it is None:
            return
        self._drag = {"item": it, "start": QPoint(pos), "on": False}

    def _drag_move(self, pos):
        d = getattr(self, "_drag", None)
        if not d:
            return
        if not d["on"]:
            dx = pos.x() - d["start"].x()
            dy = pos.y() - d["start"].y()
            if dx * dx + dy * dy < self.DRAG_SLOP2:
                return
            d["on"] = True
            try:
                d["item"].setData(Qt.UserRole + 4, True)
                self.icon_view.setCursor(Qt.ClosedHandCursor)
            except Exception:
                pass
        cur = self.icon_view.row(d["item"])
        tgt = self.icon_view.itemAt(pos)
        if tgt is None or tgt is d["item"]:
            return
        tr = self.icon_view.row(tgt)
        if cur < 0 or tr < 0 or cur == tr:
            return
        # takeItem 拿出来的还是同一个 item 对象，不会丢数据
        taken = self.icon_view.takeItem(cur)
        self.icon_view.insertItem(tr, taken)
        self.icon_view.setCurrentItem(taken)
        # 动画进度按行号存的，拖动后行号变了，清一下免得乱跳
        self.icon_delegate.hover_row = tr
        self.icon_view.viewport().update()

    def _drag_end(self):
        d = getattr(self, "_drag", None)
        self._drag = None
        if not d:
            return
        try:
            d["item"].setData(Qt.UserRole + 4, False)
            self.icon_view.unsetCursor()
        except Exception:
            pass
        if not d["on"]:
            return
        self._save_order()
        try:
            self.statusBar().showMessage("顺序已保存", 2500)
        except Exception:
            pass

    # ---- 表格视图：拖动行排序 ----
    def _tdrag_begin(self, pos):
        self._tdrag = None
        if (getattr(self, "filter_text", "") or "").strip():
            return
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        self._tdrag = {"item": self.table.item(idx.row(), 0),
                       "start": QPoint(pos), "on": False}

    def _table_row_of(self, it):
        if it is None:
            return -1
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0) is it:
                return r
        return -1

    def _tdrag_move(self, pos):
        d = getattr(self, "_tdrag", None)
        if not d:
            return
        if not d["on"]:
            dx = pos.x() - d["start"].x()
            dy = pos.y() - d["start"].y()
            if dx * dx + dy * dy < self.DRAG_SLOP2:
                return
            d["on"] = True
            self.table.setCursor(Qt.ClosedHandCursor)
        cur = self._table_row_of(d["item"])
        idx = self.table.indexAt(pos)
        if cur < 0 or not idx.isValid():
            return
        tr = idx.row()
        if tr == cur:
            return
        self._table_move_row(cur, tr)

    def _table_move_row(self, frm, to):
        n = self.table.columnCount()
        items = [self.table.takeItem(frm, c) for c in range(n)]
        self.table.removeRow(frm)
        t = to if to <= frm else to - 1
        t = max(0, min(t, self.table.rowCount()))
        self.table.insertRow(t)
        for c in range(n):
            self.table.setItem(t, c, items[c] if items[c]
                               is not None else QTableWidgetItem(""))
        self.table.setRowHeight(t, 26)
        self.table.selectRow(t)

    def _tdrag_end(self):
        d = getattr(self, "_tdrag", None)
        self._tdrag = None
        if not d:
            return
        try:
            self.table.unsetCursor()
        except Exception:
            pass
        if d["on"]:
            self._save_order()
            try:
                self.statusBar().showMessage("顺序已保存", 2500)
            except Exception:
                pass

    def _icon_menu(self, pos):
        """列表右键菜单 —— 图标视图和表格视图共用。

        为什么要加：
          换成图标视图以后，很多操作只能回顶部工具栏找，
          感觉"功能变少了"。右键把最常用的几个放在手边。
        """
        # 不用 sender()：直接按当前视图取，逻辑更明确
        if getattr(self, "view_mode", "table") == "icon":
            view = self.icon_view
            it = view.itemAt(pos)
            if it is not None:
                view.setCurrentItem(it)
            e = self._icon_entry(it) if it is not None else None
        else:
            view = self.table
            idx = view.indexAt(pos)
            if not idx.isValid():
                return
            view.selectRow(idx.row())
            # ★ B2：selectRow 之后 selectionModel 已经更新，不必再查一遍；
            #   顺序必须和 _fill_table 一致 -> 走 _view_entries()（含筛选）。
            sorted_e = self._view_entries()
            e = (sorted_e[idx.row()]
                 if idx.row() < len(sorted_e) else None)
        if e is None:
            e = self._cur_entry()
        if not e:
            return
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        running = self._key(e) in self.procs
        a_start = m.addAction("启动") if not running else None
        a_stop = m.addAction("停止") if running else None
        m.addSeparator()
        a_cfg = m.addAction("服务器配置")
        a_init = m.addAction("初始化服务端")
        m.addSeparator()
        a_bk = m.addAction("备份管理")
        a_mod = m.addAction("模组管理")
        a_log = m.addAction("分析日志")
        m.addSeparator()
        a_rename = m.addAction("重命名服务器")
        a_icon = m.addAction("设置服务器图标...")
        a_clricon = m.addAction("清除自定义图标")
        a_bat = m.addAction("重新生成 run.bat")
        a_open = m.addAction("打开文件夹")
        a_del = m.addAction("删除服务器")
        act = m.exec(view.viewport().mapToGlobal(pos))
        if not act:
            return
        if act is a_start:
            # ★ 右键菜单是"针对被右键的这一台"的上下文菜单，
            #   传 entry 明确目标 —— 否则多选时会把选中的全部一起启动。
            self.do_start(entry=e)
        elif act is a_stop:
            self.do_stop(entry=e)
        elif act is a_cfg:
            self.do_config()
        elif act is a_init:
            self.do_create()
        elif act is a_bk:
            self.do_backup()
        elif act is a_mod:
            self.do_mods()
        elif act is a_log:
            self.do_analyze()
        elif act is a_icon:
            self.do_edit_icon()
        elif act is a_rename:
            self.do_rename_server()
        elif act is a_clricon:
            self.do_clear_icon()
        elif act is a_bat:
            self.do_gen_bat()
        elif act is a_open:
            self.do_open_dir()
        elif act is a_del:
            self.do_delete_server()

    def _cur_entry(self):
        """当前选中的那个服务器（不管现在是图标视图还是表格视图）。"""
        entry = None
        if getattr(self, "view_mode", "table") == "icon":
            it = self.icon_view.currentItem()
            if it is not None:
                entry = self._icon_entry(it)
            if entry is None:
                for it2 in self.icon_view.selectedItems():
                    entry = self._icon_entry(it2)
                    if entry:
                        break
        else:
            # ★ 表格行号是"按 _view_entries() 填的"，
            #   这里必须同样按排序 + 筛选后的顺序取，否则排序非 custom
            #   或开着筛选时会拿到错的服务器。
            rows = self.table.selectionModel().selectedRows()
            sorted_e = self._view_entries()
            if rows and rows[0].row() < len(sorted_e):
                entry = sorted_e[rows[0].row()]
        return entry

    def _icon_entry(self, it):
        """图标项 -> entry"""
        if it is None:
            return None
        k = it.data(Qt.UserRole)
        for e in self.entries:
            if self._key(e) == k:
                return e
        return None

    def _selected_entries(self):
        """当前选中的所有服务器（图标视图 / 表格视图都支持 Ctrl/Shift 多选）。

        ★ V2.3.3：批量启停用。返回 [] 表示没选中任何项，
          调用方再回退到 _cur_entry()（单选上下文）。
        """
        out, seen = [], set()
        if getattr(self, "view_mode", "table") == "icon":
            # 按行号排序：selectedItems() 返回的是"选中顺序"，
            # 批量启停按界面从上到下的顺序更符合直觉。
            items = sorted(self.icon_view.selectedItems() or [],
                           key=lambda it: self.icon_view.row(it))
            for it in items:
                e = self._icon_entry(it)
                if e and self._key(e) not in seen:
                    seen.add(self._key(e))
                    out.append(e)
        else:
            sorted_e = self._view_entries()
            for r in self.table.selectionModel().selectedRows():
                i = r.row()
                if 0 <= i < len(sorted_e):
                    e = sorted_e[i]
                    if self._key(e) not in seen:
                        seen.add(self._key(e))
                        out.append(e)
        return out

    def _save_order(self, *a):
        """拖动后保存自定义顺序，并自动切到『自定义顺序』。

        为什么自动切：按版本/名称排序时拖动不会生效
        （下次刷新按排序规则又排回去了），
        用户会以为"拖动坏了"。这里检测到拖动就直接切成自定义，
        让拖的结果真的保留下来。
        """
        order = {}
        if getattr(self, "view_mode", "table") == "table":
            for r in range(self.table.rowCount()):
                it = self.table.item(r, 0)
                k = it.data(Qt.UserRole) if it is not None else None
                if k:
                    order[k] = r
        else:
            for i in range(self.icon_view.count()):
                it = self.icon_view.item(i)
                k = it.data(Qt.UserRole)
                if k:
                    order[k] = i
        if not order:
            return
        self.disp["order"] = order
        # 拖动了 -> 排序方式必须是自定义，否则白拖
        if (self.cb_sort.currentData() or "custom") != "custom":
            idx = self.cb_sort.findData("custom")
            if idx >= 0:
                self.cb_sort.blockSignals(True)
                self.cb_sort.setCurrentIndex(idx)
                self.cb_sort.blockSignals(False)
                self.sort_mode = "custom"
                self.disp["sort"] = "custom"
        self._save_data()
        try:
            self.statusBar().showMessage(
                "顺序已保存（排序方式已切为『自定义顺序』）", 3000)
        except Exception:
            pass

    def _on_sort_changed(self):
        self.sort_mode = self.cb_sort.currentData() or "custom"
        self.disp["sort"] = self.sort_mode
        self._fill_views()
        self._save_data()
        if self.sort_mode == "custom":
            self.statusBar().showMessage(
                "自定义顺序：拖动图标即可调整，会自动保存", 4000)
        elif self.sort_mode == "size":
            # ★ 按大小排序：后台线程算目录大小，绝不卡死主线程
            self._prefetch_sizes()

    def _prefetch_sizes(self):
        """按大小排序时，后台计算所有服务器目录大小（写进 _size_cache）。"""
        if getattr(self, "_size_worker_running", False):
            return
        self._size_worker_running = True
        self.statusBar().showMessage("正在计算各服务器大小 ...")
        entries = list(self.entries)
        import time as _t

        def job(on_log=None):
            from .core import install as _inst
            now = _t.time()
            out = {}
            for e in entries:
                d = e.get("server_dir") or e.get("folder") or ""
                key = self._key(e)
                try:
                    out[key] = _inst.server_size(d)
                except Exception:
                    out[key] = 0
            return {"sizes": out, "t": now}

        w = _track(self, Worker(job, {}, self))
        self._size_worker = w
        w.done.connect(self._on_sizes_done)
        w.start()

    def _on_sizes_done(self, ok, res):
        self._size_worker_running = False
        try:
            w, self._size_worker = getattr(self, "_size_worker", None), None
            if w is not None:
                self._forget_worker(w)
        except Exception:
            pass
        if ok and res:
            sizes = res.get("sizes") or {}
            t = res.get("t", 0)
            for k, s in sizes.items():
                self._size_cache[k] = {"s": s, "t": t}
            self._fill_views()
            self.statusBar().showMessage(
                "大小计算完成，已按大小排序", 4000)
        else:
            self.statusBar().showMessage(
                "大小计算失败，保持当前顺序", 4000)

    def _switch_view(self, mode):
        self.view_mode = mode
        self.disp["view"] = mode
        self.icon_view.setVisible(mode == "icon")
        self.table.setVisible(mode == "table")
        self._save_data()
        self._update_draghint()
