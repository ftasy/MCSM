# MCSM · MC 服务器管理器

一个面向 Windows 的 **Minecraft 服务器可视化管理工具**。图形界面，开箱即用：
扫描本机服务端 → 初始化新服（下核心 / 下模组 / 下插件）→ 启停服务器并看控制台
→ 改 `server.properties` → 备份/恢复存档 → 装/删模组 → 分析崩溃日志 → 换主题。

> 不是命令行工具，打开点按钮就能用。当前版本 **v2.5.0**，支持在线检查更新。

---

## ✨ 功能特性

- **多加载器一键建服**：Fabric / Forge / NeoForge / Paper（含新版本系列）
- **智能版本识别**：自动从存档（`level.dat`）→ 服务端核心（jar 内 `version.json`）→ 文件夹名，多级回退识别 MC 版本，兜底提示手动填写
- **推荐模组 / 插件库**：内置精选清单（Cupboard 生态、第三方登录、性能优化等），卡片式勾选，标签平铺无分组标题；**按 MC 版本隔离**（不适配本版本的模组默认不显示，可一键切回全部），卡片带真实模组图标
- **双源下载引擎**：Modrinth + CurseForge 全库搜索（100 条/批异步翻页、批内翻页零网络秒切）；**只在 CurseForge 发布的模组**（如 Cupboard 生态）自动走 CF 引擎，无需 API key（走 MCIM 镜像）
- **前置自动绑定**：`essentialsx → vault + luckperms` 等依赖自动补齐，冲突模组自动剔除（如 packetfixer / morechathistory）
- **启停管理**：一键启动 / 停止 / 重启，实时控制台输出（支持多开页签 + 浮动窗口）；**支持 Ctrl/Shift 多选批量启动 / 批量停止**；启动前自动检查所在盘剩余空间，不足时拒绝启动以免世界存档被写坏
- **日志搜索**：控制台 `Ctrl+F` 搜索、命中高亮与数量统计；「只看问题」一键筛出 WARN / ERROR 与报错堆栈
- **列表搜索**：服务器多时按名称 / 版本 / 加载器筛选（多关键字空格分隔，如 `fabric 1.20`）
- **数据安全**：所有配置与列表落盘走"临时文件 + fsync + 原子替换"，断电或强杀不会写出空文件；保留 3 份 `.bak` 快照，主文件损坏自动恢复
- **绿色可搬运**：`servers.json` / `sources.json` / `theme.json` 等全部写在 **EXE 同级目录**（源码运行则是项目根），整个文件夹拷到别的电脑/U 盘就能接着用；若软件放在 Program Files 等只读位置，自动回落 `%LOCALAPPDATA%\MCSM`，不会静默丢配置
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
│   ├── paths.py                 # 统一路径解析：软件目录 / 只读回落 / 老位置自动迁移
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
| **v2.5.0** | 稳定性整修 + 体验升级：① 修「启动失败后服务器永久卡在运行中」（实测 `FailedToStart` 在 `start()` 返回前同步发出，进程登记却写在之后）；② 修「预发布版误报更新」「恢复存档没拦运行中的服且回滚不彻底」「换源下载把两个 CDN 字节流拼成坏文件」「配置读坏被静默写回空列表」；③ **启动前系统端口探测**、**一键检查全部更新改批量+后台**（100 个模组请求数 300+ → 2）、**备份/恢复/删除改后台+进度+取消**、控制台「导出/清空」、首次使用引导、表格「占用」列、工具栏一键切换主题；④ 搜索防抖、图标/Java/模组元数据缓存、状态轮询池化、退出路径与线程停止加固 |
| **v2.4.1** | 六项反馈整修：① 修「退出并保留后后台找不到服务器进程」—— QProcess 析构会杀子进程，保留路径改用 `os._exit(0)` 跳过析构；② 修「重启误报运行中 + 停止卡很久」—— PID 检测改三重校验（句柄+退出码+映像名含 java），接管服停止改立即结束；③ 修「初始化装的不是最新版」—— MCIM 镜像版本顺序是乱的，所有挑版本显式按 `date_published` 倒序；④ **模组更新直接下载替换**（走源+哈希校验，旧 jar 进 `.mcsm_replaced/`，不再开浏览器）；⑤ 服务器配置的模组/插件表**加勾选列**（可全选、批量启用/禁用/查更新/下更新）；⑥ 模组浏览两页勾选互通 + 切换镜像源不再清空已搜结果与勾选 |
| **v2.4.0** | 安装体验 + 可移植性：① 安装可**取消**（下载线程每 1MB 自查，1 秒内停，已下文件保留）；② 进度条显示**网速与已下载量**（`8.4 MB / 30.1 MB · 2.3 MB/s`）；③ 推荐页/全库页新增**只看未装**；④ **CurseForge 也做哈希校验**（algo 1=SHA1 / 2=MD5）；⑤ **配置路径统一** —— 以前 5 个模块各按 `__file__` 数不同层数，打包后全落进 `_internal/`，换机器/覆盖升级就丢服务器列表、主题、镜像源，现在统一到 `mcserver/paths.py`（EXE 目录，不可写回落 `%LOCALAPPDATA%\MCSM`，老文件自动迁移）；⑥ 修掉搬迁目标写死 `G:\MCServers`、路径检查用 CWD、GitHub 插件 `UnboundLocalError` 等本机假设 |
| **v2.3.9** | 模组安装链路整修：① 修「全库搜索装的模组一个都装不上」（meta 为 None 时 `effective_slug` 抛异常/返回空 slug）；② **自动补前置** —— 读 Modrinth 版本元数据的 required 依赖，递归 2 层、已装与纯客户端跳过，装 Create 会自动带 Fabric API；③ 下载完按 **sha512/sha1 校验**，坏文件删掉换源重下，不允许坏 jar 落进 mods；④ 安装**进度条** n/N + 当前模组名；⑤ 同 mod id 的旧版 jar **自动归档**到 `.mcsm_replaced/`（只搬家不删）；⑥ 推荐页装完「✓已装」立即刷新 |
| **v2.3.1** | 结构拆分：`app.py` 5168 行 → 7 个 mixin（主窗口仅 569 行）；`dialogs.py` 8762 行 → `dialogs/` 6 个子模块；修掉拆分遗留的启动崩溃（`QSizePolicy` 等未导入）与 4 处隐藏 NameError（备份回调 `k`、整合包导入 `select_new`、run.bat 兜底 `bat`、下载重试 `RETRY`）、选模组对话框 `KeyError: 'default'` |
| **v2.3.8** | 两条通道都能**自己选 + 当场测**：下载源设置里新增「测一下两条通道」按钮，分别显示 API 搜索 / 文件下载各两个源的延迟与可用性，并告诉用户自动模式在当前网络下会选哪个组合；启动后自动模式下后台自动重测一次（结果只在内存，换 VPN / 换网络会重新测，不沿用旧结论）|
| **v2.3.7** | ① 修 v2.3.4 引入的图标回归：Modrinth CDN 只认项目 id，用 slug 拼地址一律 400，24 个坏请求把全库搜索图标挤掉了 —— 改成一次批量 API 查 id + 本地缓存、整页共用一个下载线程、失败可自愈。② 镜像源把 **API 信息源** 与 **文件下载源** 拆成两个独立开关（实测 API 走 MCIM 快、下文件官方 CDN 反而快，自动模式分别测速各挑最快）；UA 跟随版本号 |
| **v2.3.6** | 修两个真问题：① 关闭启动器后程序还在跑（退出时弹的 cmd 日志窗改可选、独立控制台窗口会被关掉，QApplication 才能真正退出）；退出确认框重做为「退出并保留服务器 / 全部停止并退出（等存盘）/ 取消」。② 重启后不自动接管 —— 根因是接管检测跑在目录扫描完成之前，列表还是空的；改成扫描完成后再接管，并提示接管到了几个。 接管后的限制（日志可读、不能发指令、停止=直接结束进程）也写进控制台了 |
| **v2.3.5** | 『下载源设置』残留窗口根治：改成单例（整个进程只有一个窗口）；进模组浏览时自动收掉还挂着的残留窗；测速提示框写明"与搜索无关"。已在 v2.0.3 / v2.3.1 / v2.3.5 三个版本上实测：模组搜索每个动作都零弹窗，并固化成自动化回归测试 |
| **v2.3.4** | 界面修正：① 主页搜索框与排序控件不再隔着一大段空白；② 内置推荐模组按 MC 版本隔离（探测确认不适配的直接不显示，可切回全部）；③ 推荐卡片显示真实模组图标（原来全是首字母色块）；④ 搜索过程不再有任何弹窗，「下载源设置」改为单例（不会再叠出好几个窗口） |
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
