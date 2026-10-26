# MCSM v1.6.5 — 修复打包版窗口/任务栏图标

> 修复：打包成 EXE 后窗口标题栏、任务栏图标显示成默认图标

## 主要更新

### 修复窗口图标与任务栏图标变成默认图标
- **根因**：PyInstaller 的 `--icon` 只给 EXE 文件本身嵌图标；运行时**窗口图标**由代码 `app_icon()` 从磁盘加载 `icon.ico`。打包后图标文件没被收进 `_internal`，加载失败 → 窗口/任务栏退回默认图标。
- **修复**：
  - `app_icon()` 新增 PyInstaller 环境路径（`sys._MEIPASS`），打包后也能定位到图标。
  - `build_exe.bat` 增加 `--add-data` 把 `icon.ico` / `icon.png` 打进 `_internal`。
- **效果**：窗口标题栏、任务栏、所有弹窗图标统一显示 MCSM 方块图标（源码版与打包版一致）。

## 下载
- 安装包：`MCSM_v1.6.5_安装包.zip`
- EXE：`dist/MCSM/MCSM.exe`（运行需连同 `_internal` 文件夹一起拷贝）
