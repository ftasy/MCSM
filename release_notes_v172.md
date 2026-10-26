# MCSM v1.7.2

## 1. 补齐 PySide6 控件导入（根因修复）
- ModBrowseDialog 双分组切换用的 QRadioButton 一直漏导，弹窗一构造就 NameError、点击「选择模组/添加模组」毫无反应（异常被事件循环静默吞掉）——现已补上导入
- 复核全局控件导入：QTimeEdit 等全部就位

## 2. 修复「添加模组/插件」点击无反应与静默闪退
- `_add_mod_here` / `_add_plugin_here` 改用 `self._mod_dialog` / `self._plugin_dialog` 持有弹窗引用，防止局部变量被 GC 回收导致窗口一闪没
- 外层包 try/except：异常打印到日志并弹窗提示，不再静默
- show() 后调用 raise_() + activateWindow()，弹窗保证置顶

## 3. 初始化界面（CreateDialog）布局与动效规整
- 自动化分组框改名为「性能与自动化」，Aikar 优化 / 自动重启 / 定时备份统一收纳，界面层级更清楚
- 滚动内容统一留出 8px 边距、组间 10px 间距，内存（G/MB）区域补内边距，控件不再贴边/溢出
- 按钮悬停/按下动效沿用全局主题样式（v1.7.1 已强制应用）

下载：ZIP 源码包 + 绿色版 EXE（需连 _internal 一起拷贝）。
