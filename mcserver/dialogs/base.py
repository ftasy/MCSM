# -*- coding: utf-8 -*-
"""对话框基类：统一的窗口图标 + 关闭时兜底停线程。

★ 为什么要有这个基类
  拆分前 dialogs.py 里 11 个对话框各自抄了一份完全相同的 closeEvent：

      try: _stop_threads(self)
      except Exception: pass
      try: super().closeEvent(e)
      except Exception: pass

  这段代码删掉一行就是"点关闭闪退"（对话框销毁了后台线程还在跑，
  Qt 直接 abort），属于必须一字不差的关键逻辑，抄 11 份就迟早会漏。
  现在统一收在 BaseDialog.closeEvent 里，子类不用再写；
  个别子控件自己挂了线程的（如 JavaPick 的扫描线程），
  只需声明 `_stop_children = ("java_pick",)`。
"""

from PySide6.QtWidgets import QDialog

from ..dialog_workers import _stop_threads
from ..widgets import app_icon


class BaseDialog(QDialog):
    """所有长任务对话框的基类。"""

    #: 关闭窗口时，除自身之外还要一起停线程的子控件属性名
    _stop_children = ()

    def __init__(self, parent=None):
        super().__init__(parent)
        # 窗口图标（跟主程序一致）
        try:
            self.setWindowIcon(app_icon())
        except Exception:
            pass

    def closeEvent(self, e):
        """关闭前先停掉后台线程。

        ★ 不停的话：对话框销毁了、线程还在跑，
          Qt 会直接 abort 整个程序 —— 也就是"点了就闪退"。
        """
        for obj in self._stop_targets():
            try:
                _stop_threads(obj)
            except Exception:
                pass
        try:
            super().closeEvent(e)
        except Exception:
            pass

    def _stop_targets(self):
        """自身 + _stop_children 里列出且确实存在的子控件。"""
        out = [self]
        for name in self._stop_children:
            child = getattr(self, name, None)
            if child is not None:
                out.append(child)
        return out
