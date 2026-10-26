# -*- coding: utf-8 -*-
"""服务器图标的绘制与交互动画（悬停放大 / 点击回弹）。

QListWidget 自带的 IconMode 没法做动画，所以自己画：
  · 悬停：图标放大 + 外发光 + 卡片底色
  · 按下：先缩小，松手回弹
  · 每张卡片各自记录动画进度，用定时器统一推进

进度存在 delegate 里，行号是 key —— 拖动排序后行号会变，
所以只在"没拖动时"依赖行号，视觉上够用。
"""

from PySide6.QtCore import (Qt, QRect, QTimer, QRectF, QSize)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QPainter,
                           QPainterPath, QPen, QBrush, QPixmap,
                           QRadialGradient, QLinearGradient)
from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QToolTip

# ★ 主题模块必须在【顶层】导入，不能在 paint() 里 import。
#   程序退出时 Qt 还会触发最后一次绘制，此时 Python 已进入
#   模块清理阶段，paint 里的 import 会撞上正在被清理的模块对象，
#   表现为解释器退出时崩溃：
#       Fatal Python error: none_dealloc: deallocating None
#   （实测：把 import 挪到顶层后，同样的操作不再崩溃。）
from . import theme as _thm

# 加载器配色（用来画没图标时的字母块）
LOADER_COLORS = {
    "fabric":   "#4a9eff",
    "forge":    "#e08a3c",
    "neoforge": "#a96ce0",
    "paper":    "#3ec46d",
    "vanilla":  "#9a7b5a",
    "unknown":  "#8a8a8a",
}

LOADER_LETTER = {
    "fabric": "F", "forge": "F", "neoforge": "N",
    "paper": "P", "vanilla": "V", "unknown": "?",
}


class IconDelegate(QStyledItemDelegate):
    """带悬停/点击动画的图标委托。"""

    HOVER_SCALE = 0.10      # 悬停放大比例
    PRESS_SCALE = -0.06     # 按下缩小
    GLOW = 14               # 外发光半径

    def __init__(self, view, parent=None):
        super().__init__(parent)
        self.view = view
        self.hover_row = -1
        self.press_row = -1
        # row -> 0..1 的动画进度
        self._hover_p = {}
        self._press_p = {}
        self._timer = QTimer(self)
        self._timer.setInterval(16)      # ~60fps
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        view.setMouseTracking(True)
        view.entered.connect(self._on_entered)
        try:
            vp = view.viewport()
            vp.setMouseTracking(True)
        except Exception:
            pass

    # ---------- 交互 ----------
    def _on_entered(self, index):
        self.hover_row = index.row() if index.isValid() else -1

    def set_press(self, row):
        self.press_row = row

    def clear_press(self):
        self.press_row = -1

    def leave(self):
        self.hover_row = -1

    def _tick(self):
        """推进动画；没人需要动时不做无用重绘。"""
        changed = False
        rows = set(self._hover_p) | set(self._press_p)
        rows.add(self.hover_row)
        rows.add(self.press_row)
        for r in list(rows):
            if r < 0:
                continue
            hp = self._hover_p.get(r, 0.0)
            tgt = 1.0 if r == self.hover_row else 0.0
            nh = hp + (tgt - hp) * 0.18
            if abs(nh - hp) > 0.002:
                self._hover_p[r] = nh
                changed = True
            elif tgt == 0 and hp:
                self._hover_p[r] = 0.0
                changed = True

            pp = self._press_p.get(r, 0.0)
            ptgt = 1.0 if r == self.press_row else 0.0
            np = pp + (ptgt - pp) * 0.30
            if abs(np - pp) > 0.002:
                self._press_p[r] = np
                changed = True
            elif ptgt == 0 and pp:
                self._press_p[r] = 0.0
                changed = True
        if changed:
            try:
                self.view.viewport().update()
            except Exception:
                pass

    def hover_progress(self, row):
        return self._hover_p.get(row, 0.0)

    def press_progress(self, row):
        return self._press_p.get(row, 0.0)

    # ---------- 绘制 ----------
    def sizeHint(self, option, index):
        d = self.icon_size()
        # 图标 + 上下留白 + 文字行数
        lines = self._text_lines(index)
        return QSize(d + 26, d + 26 + 18 * max(1, len(lines)))

    def icon_size(self):
        return getattr(self.view, "_icon_px", 72)

    def _text_lines(self, index):
        d = index.data(Qt.UserRole + 3)
        if isinstance(d, (list, tuple)):
            return [x for x in d if x]
        return []

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        rect = option.rect
        row = index.row()
        hp = self.hover_progress(row)
        pp = self.press_progress(row)
        selected = bool(option.state & QStyle.State_Selected)

        # 悬停卡片底
        if hp > 0.01:
            path = QPainterPath()
            path.addRoundedRect(QRectF(rect).adjusted(3, 3, -3, -3), 8, 8)
            painter.fillPath(path, QColor(255, 255, 255,
                                          int(28 * hp)))
            pen = QPen(QColor(74, 158, 255, int(150 * hp)))
            pen.setWidth(1)
            painter.setPen(pen)
            painter.drawPath(path)

        # ★ PCL 风格选中：卡片整体一圈淡底，配合左右竖杠
        if selected:
            th0 = self._theme()
            ac = QColor(th0.get("sel") or "#4a9eff")
            sp = QPainterPath()
            sp.addRoundedRect(QRectF(rect).adjusted(3, 3, -3, -3), 8, 8)
            painter.fillPath(sp, QColor(ac.red(), ac.green(), ac.blue(),
                                        int(70 * (0.55 + 0.45 * hp))))

        # 正在被拖动的卡片：蓝色虚线框 + 半透明，和别的区分开
        dragging = bool(index.data(Qt.UserRole + 4))
        if dragging:
            dp = QPainterPath()
            dp.addRoundedRect(QRectF(rect).adjusted(2, 2, -2, -2), 9, 9)
            painter.fillPath(dp, QColor(74, 158, 255, 55))
            dpen = QPen(QColor(74, 158, 255, 230))
            dpen.setWidth(2)
            dpen.setStyle(Qt.DashLine)
            painter.setPen(dpen)
            painter.drawPath(dp)
            painter.setPen(Qt.NoPen)
            painter.setOpacity(0.72)

        # 缩放：悬停放大 + 按下缩小
        scale = 1.0 + self.HOVER_SCALE * hp + self.PRESS_SCALE * pp
        d = self.icon_size()
        cx = rect.center().x()
        icon_top = rect.top() + 12
        side = d * scale
        ir = QRectF(cx - side / 2.0,
                    icon_top + (d - side) / 2.0, side, side)

        # 外发光
        if hp > 0.02:
            g = QRadialGradient(ir.center(), side * 0.75)
            glow = QColor(74, 158, 255, int(90 * hp))
            g.setColorAt(0.0, glow)
            g.setColorAt(1.0, QColor(74, 158, 255, 0))
            painter.setBrush(QBrush(g))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(ir.adjusted(-self.GLOW, -self.GLOW,
                                            self.GLOW, self.GLOW))

        pm = self._pixmap(index, int(side))
        if pm and not pm.isNull():
            painter.drawPixmap(ir.toRect(), pm)
        else:
            self._draw_letter(painter, ir, index)

        # 文字（只支持粗体）
        lines = self._text_lines(index)
        if lines:
            th = self._theme()
            f = QFont(option.font)
            # ★ 两个方向都要设：取消粗体时 option.font 可能还带着
            #   上一次的粗体（Qt 的 QFont 会继承），只写 if: setBold(True)
            #   的话关不掉。
            f.setBold(bool(int(th.get("font_bold") or 0)))
            fm = QFontMetrics(f)
            painter.setFont(f)
            y = rect.top() + 12 + d + 16
            # ★ PCL 层次：未选中用"常驻暗色"，选中才变主题色
            if selected and th.get("sel"):
                txt_col = QColor(th["sel"])
            elif th.get("idle"):
                txt_col = QColor(th["idle"])
            else:
                txt_col = QColor(th.get("fg") or
                                 option.palette.text().color().name())
            flags = (Qt.AlignHCenter | Qt.AlignVCenter
                     | Qt.TextSingleLine)
            for i, t in enumerate(lines[:3]):
                t = fm.elidedText(t, Qt.ElideRight, rect.width() - 10)
                tr = QRect(rect.left() + 4, y + i * 16,
                           rect.width() - 8, 16)
                painter.setPen(txt_col)
                painter.drawText(tr, flags, t)

        # ★ 选中竖杠：PCL 里左侧一条，这里默认左右各一条。
        #   画在最上层，才不会被图标/文字盖住。
        if selected:
            th0 = self._theme()
            mode = int(th0.get("bars") or 0)
            if mode:
                ac = QColor(th0.get("sel") or "#4a9eff")
                painter.setPen(Qt.NoPen)
                painter.setBrush(ac)
                w = max(3, int(rect.height() * 0.055))
                hh = rect.height() - 12
                yy = rect.top() + 6
                if mode >= 1:      # 左
                    painter.drawRoundedRect(
                        QRectF(rect.left() + 3, yy, w, hh), 2, 2)
                if mode >= 2:      # 右
                    painter.drawRoundedRect(
                        QRectF(rect.right() - 3 - w, yy, w, hh), 2, 2)

        painter.restore()

    def helpEvent(self, event, view, option, index):
        """鼠标悬停在图标上时弹出注释（PCL 风格）。

        ★ 内容来自 item 的 ToolTipRole，由填充列表时写好；
          这里只负责"该不该弹"和"弹在哪"。
        """
        try:
            if not _thm.hover_tip_enabled():
                return False
        except Exception:
            return False
        if not index.isValid():
            return False
        tip = index.data(Qt.ToolTipRole)
        if not tip:
            return False
        try:
            QToolTip.showText(event.globalPos(), str(tip),
                              view, option.rect)
        except Exception:
            return False
        return True

    _th_cache = {"t": 0, "rev": -1}

    def _theme(self):
        """取当前主题（带缓存，避免每行都读配置）。

        ★ 以前只按时间缓存 1 秒，结果：
          改完主题后界面立刻重画的那一次（<1s）用的还是【旧值】，
          而之后不一定再有重绘 —— 于是永远停在旧样子。
          表现就是"取消勾选粗体，点了 OK 却没变化"。
          现在同时比对 theme.rev()，主题一变立刻失效。
        """
        import time
        now = time.time()
        c = IconDelegate._th_cache
        try:
            r = _thm.rev()
        except Exception:
            r = 0
        if (c.get("rev") != r or now - c.get("t", 0) > 5.0
                or "d" not in c):
            try:
                d = _thm.get()
                d["idle"] = _thm.item_idle_color()
                d["sel"] = _thm.item_sel_color()
                d["bars"] = _thm.sel_bar_mode()
            except Exception:
                d = {"font_bold": 0, "fg": "#f2f2f2",
                     "idle": "#b0b0b0", "sel": "#4a9eff", "bars": 2}
            c["d"] = d
            c["t"] = now
            c["rev"] = r
        return c["d"]

    def _pixmap(self, index, size):
        pm = index.data(Qt.DecorationRole)
        if isinstance(pm, QPixmap) and not pm.isNull():
            return pm.scaled(size, size, Qt.KeepAspectRatio,
                             Qt.SmoothTransformation)
        return None

    def _draw_letter(self, painter, ir, index):
        loader = (index.data(Qt.UserRole + 2) or "unknown").lower()
        color = QColor(LOADER_COLORS.get(loader, "#8a8a8a"))
        path = QPainterPath()
        path.addRoundedRect(ir, 10, 10)
        g = QLinearGradient(ir.topLeft(), ir.bottomRight())
        g.setColorAt(0, color.lighter(125))
        g.setColorAt(1, color.darker(125))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(g))
        painter.drawPath(path)

        letter = LOADER_LETTER.get(loader, "?")
        f = QFont()
        f.setBold(True)
        f.setPointSize(max(10, int(ir.width() * 0.42)))
        painter.setFont(f)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(ir, Qt.AlignCenter, letter)


class RowDelegate(QStyledItemDelegate):
    """表格视图的行装饰：选中时左右各一条竖杠 + 悬停注释。

    ★ 为什么表格也要单独写个委托？
      QTableWidget::item:selected 的 stylesheet 里写 border-left/right
      在 Qt 里渲染不稳定（各版本表现不一致，经常被忽略）。
      自己画最靠谱：先交给父类把底色和文字画好，再叠竖杠。
    """

    BAR_W = 4

    def __init__(self, view=None, parent=None):
        super().__init__(parent)
        # ★ 不保存 view 的强引用：程序退出时 Qt 可能先销毁 view，
        #   delegate 后销毁时再碰它就是"访问已释放对象"，
        #   表现为解释器退出阶段崩溃（none_dealloc: deallocating None）。
        #   列数从 index.model() 取，一样准，还不持有引用。
        self.view = None

    def _cols(self, index):
        try:
            return index.model().columnCount() - 1
        except Exception:
            return 0

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if not (option.state & QStyle.State_Selected):
            return
        try:
            mode = _thm.sel_bar_mode()
            if not mode:
                return
            ac = QColor(_thm.item_sel_color())
        except Exception:
            mode, ac = 2, QColor("#4a9eff")
        last = self._cols(index)
        r = option.rect
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(ac)
        hh = r.height() - 4
        yy = r.top() + 2
        if mode >= 1 and index.column() == 0:
            painter.drawRect(QRect(int(r.left() + 1), int(yy),
                                   self.BAR_W, int(hh)))
        if mode >= 2 and index.column() == last:
            painter.drawRect(QRect(int(r.right() - self.BAR_W - 1),
                                   int(yy), self.BAR_W, int(hh)))
        painter.restore()

    def helpEvent(self, event, view, option, index):
        try:
            if not _thm.hover_tip_enabled():
                return False
        except Exception:
            return False
        if not index.isValid():
            return False
        tip = index.data(Qt.ToolTipRole)
        if not tip:
            return False
        try:
            QToolTip.showText(event.globalPos(), str(tip),
                              view, option.rect)
        except Exception:
            return False
        return True



