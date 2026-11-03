# MCSM · MC 服务器管理器

一个面向 Windows 的 **Minecraft 服务器可视化管理工具**。图形界面，开箱即用：
扫描本机服务端 → 初始化新服（下核心 / 下模组 / 下插件）→ 启停服务器并看控制台
→ 改 `server.properties` → 备份/恢复存档 → 装/删模组 → 分析崩溃日志 → 换主题。

> 不是命令行工具，打开点按钮就能用。当前版本 **v2.3.3**，支持在线检查更新。

---

## ✨ 功能特性

- **多加载器一键建服**：Fabric / Forge / NeoForge / Paper（含新版本系列）
- **智能版本识别**：自动从存档（`level.dat`）→ 服务端核心（jar 内 `version.json`）→ 文件夹名，多级回退识别 MC 版本，兜底提示手动填写
- **推荐模组 / 插件库**：内置精选清单（Cupboard 生态、第三方登录、性能优化等），卡片式勾选，标签平铺无分组标题
- **双源下载引擎**：Modrinth + CurseForge 全库搜索（100 条/批异步翻页、批内翻页零网络秒切）；**只在 CurseForge 发布的模组**（如 Cupboard 生态）自动走 CF 引擎，无需 API key（走 MCIM 镜像）
- **前置自动绑定**：`essentialsx → vault + luckperms` 等依赖自动补齐，冲突模组自动剔除（如 packetfixer / morechathistory）
- **启停管理**：一键启动 / 停止 / 重启，实时控制台输出（支持多开页签 + 浮动窗口）；**支持 Ctrl/Shift 多选批量启动 / 批量停止**；启动前自动检查所在盘剩余空间，不足时拒绝启动以免世界存档被写坏
- **日志搜索**：控制台 `Ctrl+F` 搜索、命中高亮与数量统计；「只看问题」一键筛出 WARN / ERROR 与报错堆栈
- **列表搜索**：服务器多时按名称 / 版本 / 加载器筛选（多关键字空格分隔，如 `fabric 1.20`）
- **数据安全**：所有配置与列表落盘走"临时文件 + fsync + 原子替换"，断电或强杀不会写出空文件；保留 3 份 `.bak` 快照，主文件损坏自动恢复
- **配置可视化**：`server.properties`、核心参数、模组与插件配置、语音聊天、白名单、备份、启动参数、性能优化——全图形化编辑
- **崩溃分析**：崩溃日志自动解析（规则库 + 可选 AI 深度诊断），给出可能原因
- **更新检测**：内置"检查更新"直达 GitHub Releases
- **深色 / 浅色主题**：一键切换

## 📥 下载

| 方式 | 说明 |
|---|---|
| **EXE 免安装版（推荐）** | `MCSM_vX.Y.Z_portable.zip`，解压即用，含运行库，首次启动略慢 |
| **在线更新** | 软件顶栏「检查更新」自动提示最新 Release |

所有发布包统一托管在 **GitHub Releases**：
👉 https://github.com/ftasy/MCSM/releases/latest

> 自 v2.3.0 起只发布免安装版（EXE 便携包），不再维护安装器产物。
> 想从源码跑：`pip install PySide6` 后 `python -m mcserver` 即可。

## 🚀 快速开始

1. **安装**：解压 `MCSM_vX.Y.Z_portable.zip` → 运行 `MCSM\MCSM.exe`。
2. **扫描**：顶部『选择目录』，选存放服务器的总文件夹（如 `D:\MCServers`）。
3. **初始化新服**：选中目录 → 『初始化服务端』→ 选核心 / 版本 / 模组 → 开始。
4. **启动**：点『启动』，控制台实时显示日志；『停止』安全关服。

详细图文说明见仓库内 `使用说明.txt`。

### ⌨️ 快捷键

| 快捷键 | 作用 |
|---|---|
| `Ctrl+F` | 焦点在控制台时搜索日志，否则聚焦服务器搜索框 |
| `回车` / `Shift+回车` | 搜索框内：下一个 / 上一个匹配（自动绕回） |
| `Esc` | 关闭搜索并恢复全部日志 / 清空列表筛选 |
| `↑` / `↓` | 控制台指令历史 |
| `F5` | 重新扫描总目录 |

## 📁 目录结构

界面层按职责拆成 **7 个 mixin**（主窗口只做组装），对话框按功能拆成 **6 个子模块**，
业务逻辑全部放在不依赖 Qt 的 `core/` 里。

```
MCSM/
├── mcserver/                    # 主程序包
│   ├── __main__.py              # 入口（python -m mcserver）
│   ├── app.py                   # MainWindow 组装（Mixin）+ main() 启动流程
│   ├── app_views.py             # 视图：图标卡片 / 表格 / 拖动排序 / 右键菜单
│   ├── app_settings.py          # 显示设置：外观页签、主题、数据源、字号行高
│   ├── app_servers.py           # 单服操作：改名 / 换图标、备份、导入（拖放 / 地图 / 整合包）
│   ├── app_library.py           # 服务器资料：模组、核心、诊断、servers.json 读写、扫描
│   ├── app_actions.py           # 操作入口：初始化、生成 run.bat、统一搬迁、正版登录准备
│   ├── app_console.py           # 控制台与监控：多页签 / 浮动窗 / 进程挂接 / 端口 / 防火墙
│   ├── app_process.py           # 进程生命周期：启动、日志读取、退出处理、停止
│   ├── widgets.py               # 可复用控件：Console / FloatConsole / NavButton / 主题助手
│   ├── dialog_workers.py        # 对话框用的后台线程（Worker / IconWorker / LoaderVerWorker）
│   ├── const.py                 # 跨模块共享常量（servers.json、-Xmx 正则）
│   ├── updater.py               # 检查更新
│   ├── dialogs/                 # 各种对话框
│   │   ├── __init__.py          # 门面：统一导出，旧写法 from .dialogs import CreateDialog 仍可用
│   │   ├── common.py            # 通用：LogView 日志控件、主题配色、YAML / 内存单位助手
│   │   ├── create.py            # 初始化向导：JavaPick / CreateDialog
│   │   ├── config.py            # 服务器配置窗：server.properties / 启动参数 / 模组页签
│   │   ├── server_ops.py        # 运维窗：删除服务器 / 导出诊断 / 加载器更新 / 移除核心
│   │   ├── mods.py              # 模组窗：选择 / 全库浏览 / Modrinth / 模组配置 / 分析
│   │   └── backup.py            # 备份与搬迁：BackupDialog / MigrateDialog
│   └── core/                    # 业务层（纯 Python，不依赖 Qt）
│       ├── net.py               # 统一网络请求（UA / 超时 / 重试 / JSON）
│       ├── install.py           # 核心与模组下载编排（推荐清单 MOD_TARGETS）
│       ├── modman.py            # 已装模组识别、启停、更新检查
│       ├── sources.py           # 下载源：Modrinth 官方 / MCIM 镜像 / CurseForge
│       ├── scanner.py           # 扫描服务器目录、识别版本与存档
│       ├── versions.py          # MC 版本 → Java / 加载器支持映射表
│       ├── java.py              # Java 环境探测
│       ├── builder.py           # 生成 server.properties / run.bat / eula.txt
│       ├── props.py             # server.properties 读写（保留注释与顺序）
│       ├── backup.py            # 存档备份 / 恢复
│       ├── migrate.py           # 分散地图统一搬迁
│       ├── modconf.py           # 模组配置文件生成与读写
│       ├── loaderup.py          # 加载器升级、Fabric API 更新
│       ├── modpack.py           # 整合包一键导入
│       ├── analyze.py           # 离线日志诊断（规则库）
│       ├── ai_diag.py           # AI 深度诊断
│       ├── monitor.py           # 实时状态探测（CPU / 内存 / 在线人数）
│       ├── theme.py             # 主题引擎：字体 / 配色 / 背景图
│       ├── icons.py             # 服务器图标绘制与交互动画
│       └── nbt.py               # 极简 NBT 解析（读 level.dat）
├── icons/                       # 内置图标资源
├── 版本归档/                    # 历史版本发布包
├── MCSM.spec                    # PyInstaller 配置（--onedir）
├── build_exe.bat                # 打包 EXE（Python 路径可参数化）
├── pack.py                      # 打 ZIP 源码包
├── setup.ps1 / uninstall.ps1    # 安装 / 卸载脚本
├── 安装.bat / 卸载.bat          # 双击即用的安装 / 卸载入口
├── start_manager.bat            # 源码方式直接启动
├── 换图标.bat                   # 自定义程序图标
├── 使用说明.txt                 # 图文使用说明
├── 更新日志.txt                 # 完整更新日志
└── README.md
```

## 🔨 本地构建

### 打包 EXE

`build_exe.bat` 不再写死 Python 路径，按下面的顺序自动查找：

1. 命令行第一个参数
2. 环境变量 `MCSM_PYTHON`
3. `py` 启动器（`py -3.14` / `py -3`）
4. PATH 里的 `python`
5. 常见安装目录（`%LOCALAPPDATA%\Programs\Python`、`C:\PythonXX`、`%ProgramFiles%\PythonXX`）

```bat
rem 自动探测 Python 并打包（输出 dist\MCSM\MCSM.exe）
build_exe.bat

rem 指定解释器
build_exe.bat "D:\Python314\python.exe"

rem 或者用环境变量
set MCSM_PYTHON=D:\Python314\python.exe
build_exe.bat

rem 只清构建产物
build_exe.bat clean
```

脚本会自动检查并安装缺失的 `PySide6` / `PyInstaller`，打包完成后会打印实际使用的解释器路径。

### 打包 ZIP 源码包

```bat
python -c "import pack; pack.pack()"
```

> 版本号在 `mcserver/__init__.py`（`__version__`）和 `pack.py`（`VERSION`）两处，发版时一起改。

## 🧰 技术栈

- Python 3.9+ · PySide6 (Qt) · PyInstaller
- 数据源：Modrinth API · CurseForge API（MCIM 镜像免 key）

## 🏗 代码组织约定

- **`core/` 不 import Qt**：所有网络、文件、版本、备份逻辑都在这里，可单独测试/复用。
- **写文件一律走 `core/fsutil.py`**：`atomic_write_text` / `atomic_write_json` 会先写同目录临时文件再 `os.replace` 原子替换，用户数据（`servers.json`、`server.properties`、各种 YAML）必须用它们；`keep_backup=True` 还会顺带留 3 份 `.bak`。**不要再用 `open(path, "w")` 直接写**——中途断电/被杀/盘满会把原文件清成空。
- **`app_*.py` 是 mixin**：每个文件一个 `XxxMixin`，方法里的 `self` 就是主窗口，新增功能按职责往对应文件里加即可，不要在 `app.py` 里继续堆代码。
- **`dialogs/` 按功能分文件**：跨模块复用的助手放 `common.py`；新增对话框时优先 import `common` 里的助手，不要复制粘贴。
- **`dialogs/__init__.py` 是门面**：外部统一 `from .dialogs import XxxDialog`，子模块之间的依赖保持 `common → server_ops → mods → create → backup → config` 的单向顺序，避免循环导入。

## 📜 版本历史

| 版本 | 亮点 |
|---|---|
| **v2.3.1** | 结构拆分：`app.py` 5168 行 → 7 个 mixin（主窗口仅 569 行）；`dialogs.py` 8762 行 → `dialogs/` 6 个子模块；修掉拆分遗留的启动崩溃（`QSizePolicy` 等未导入）与 4 处隐藏 NameError（备份回调 `k`、整合包导入 `select_new`、run.bat 兜底 `bat`、下载重试 `RETRY`）、选模组对话框 `KeyError: 'default'` |
| **v2.3.3** | 批量启停 + 磁盘预检：列表支持多选批量启动 / 停止；启动前检查所在盘剩余空间（低于"内存上限 ×1.5"或 2GB 时拒绝并提示）；统一色板 `core/palette.py` 收拢 140+ 处硬编码颜色；`dialogs/` 再拆（新增 `mod_pick`/`mod_analyze`/`mod_source`/`mod_search`/`mod_browse`，`mods.py` 3350 行 → 5 个模块）；新增 `BaseDialog` 收掉 11 份重复 closeEvent；`app_settings` 主题刷新去重（对话框循环里每次重算样式 → 只算一次） |
| **v2.3.0** | 全面性能优化：60 处正则预编译、`os.scandir` 替代 `os.listdir`、6 处文件句柄泄漏修复；新增 `core/net.py` 统一网络层 |
| v1.9.8 | 修复"点启动控制台无输出"：Java 自动扫描补全所有盘符，缺 Java 时可手动选 `java.exe` 并记住 |
| v1.9.7 | 紧急修复：启动流程异常导致防重入锁死（点启动没反应/按钮禁用） |
| v1.9.6 | ServerCore 预设三档真正生效；拖放已存在服务器自动选中；启动防重入；控制台普通信息调亮 |
| v1.9.5 | 推荐列表真实适配探测：Modrinth/CF/GitHub 三来源逐项校验，不适配项禁勾 + 红字原因 |
| v1.8.6 | Cupboard 生态强制 CurseForge 下载源；全库搜索双源下拉；CF 搜索 loader 映射补全 |

完整更新日志见 Releases 说明与仓库内 `更新日志.txt`。

## 📄 许可证

[MIT License](LICENSE)
