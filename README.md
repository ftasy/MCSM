# MCSM · MC 服务器管理器

一个面向 Windows 的 **Minecraft 服务器可视化管理工具**。图形界面，开箱即用：
扫描本机服务端 → 初始化新服（下核心 / 下模组 / 下插件）→ 启停服务器并看控制台
→ 改 `server.properties` → 备份/恢复存档 → 装/删模组 → 分析崩溃日志 → 换主题。

> 不是命令行工具，打开点按钮就能用。当前版本 **v1.8.6**，支持在线检查更新。

---

## ✨ 功能特性

- **多加载器一键建服**：Fabric / Forge / NeoForge / Paper（含 1.26 系新版本）
- **智能版本识别**：自动从存档（`level.dat`）→ 服务端核心（jar 内 `version.json`）→ 文件夹名，多级回退识别 MC 版本，兜底提示手动填写
- **推荐模组 / 插件库**：内置精选清单（Cupboard 生态、第三方登录、性能优化等），卡片式勾选，标签平铺无分组标题
- **双源下载引擎**：Modrinth + CurseForge 全库搜索（100 条/批异步翻页、批内翻页零网络秒切）；**只在 CurseForge 发布的模组**（如 Cupboard 生态）自动走 CF 引擎，无需 API key（走 MCIM 镜像）
- **前置自动绑定**：`essentialsx → vault + luckperms` 等依赖自动补齐，冲突模组自动剔除（如 packetfixer / morechathistory）
- **启停管理**：一键启动 / 停止 / 重启，实时控制台输出
- **配置可视化**：`server.properties`、核心参数、模组与插件配置、语音聊天（Simple Voice Chat）、白名单、备份、启动参数、性能优化——全图形化编辑
- **崩溃分析**：崩溃日志自动解析，给出可能原因
- **更新检测**：内置"检查更新"直达 GitHub Releases
- **深色 / 浅色主题**：一键切换

## 📥 下载

| 方式 | 说明 |
|---|---|
| **ZIP 源码包（推荐）** | 解压后双击 `安装.bat` 一键安装（自动装 Python / PySide6、建快捷方式）；或直接运行 `start_manager.bat` |
| **EXE 免安装版** | 解压即用，含运行库，首次启动略慢 |
| **在线更新** | 软件顶栏「检查更新」自动提示最新 Release |

所有安装包统一托管在 **GitHub Releases**：
👉 https://github.com/ftasy/MCSM/releases/latest

## 🚀 快速开始

1. **安装**：解压 ZIP → 双击 `安装.bat`（或直接用 EXE 版）。
2. **扫描**：顶部『扫描』，选择存放服务器的总文件夹（如 `D:\MCServers`）。
3. **初始化新服**：选中目录 → 『初始化服务端』→ 选核心 / 版本 / 模组 → 开始。
4. **启动**：点『启动』，控制台实时显示日志；『停止』安全关服。

详细图文说明见仓库内 `使用说明.txt`。

## 📁 目录结构

```
mcserver/
├── __main__.py        # 程序入口
├── app.py             # 主窗口 / 主题 / 托盘
├── dialogs.py         # 所有对话框（初始化、模组浏览、配置页……）
├── core/
│   ├── install.py     # 推荐清单（MOD_TARGETS / PLUGIN_TARGETS）+ 下载编排
│   ├── modman.py      # Modrinth / CurseForge 双源 API 封装
│   ├── sources.py     # 镜像 / API key / cf_search / cf_download
│   ├── scanner.py     # 服务端扫描与版本识别
│   ├── server.py      # 服务器启停 / 控制台 / server.properties
│   └── updater.py     # 检查更新
├── modconf.py         # 模组 / 插件配置生成与读写
├── pack.py            # 打包 ZIP 安装包
└── build_exe.bat      # PyInstaller 打包脚本
```

## 🔨 本地构建

```bat
rem 1. 打包 EXE（PyInstaller，输出 dist\MCSM\MCSM.exe）
build_exe.bat

rem 2. 生成 ZIP 安装包（MCSM_vX.Y.Z_安装包.zip）
python -c "import pack; pack.pack()"
```

## 🧰 技术栈

- Python 3.9+ · PySide6 (Qt) · PyInstaller
- 数据源：Modrinth API · CurseForge API（MCIM 镜像免 key）

## 📜 版本历史

| 版本 | 亮点 |
|---|---|
| **v1.9.6** | ServerCore 预设三档真正生效（闭包修复，目标 MSPT 50→40→35 联动）；拖放已存在服务器自动选中+询问直接启动；防火墙放行去重；启动防重入；控制台普通信息调亮 |
| **v1.9.5** | 推荐列表真实适配探测：Modrinth/CF/GitHub 三来源逐项校验，不适配项直接禁勾+红字原因（MultiYggdrasil 最高 26.2、MultiLogin 仅 Velocity），结果落盘缓存 24h |
| **v1.9.4** | 下载卡顿根治：版本查询双源回退（MCIM 404 假阳性立即换官方）+ CDN 双路回退；MultiLogin 改走 GitHub Releases（无 Paper 版时明确提示，不再 404）；总结论区分网络类原因 |
| **v1.9.3** | Cupboard 生态硬编码 CF 项目 id 根治安装失败；auto 模式按实际生效源判断免 key；连写 slug 拆词重搜；无适配版本报友好提示 |
| **v1.8.6** | Cupboard 生态强制 CurseForge 下载源；全库搜索双源下拉（Modrinth / CurseForge）；CF 搜索 loader 映射补全（Forge=1 / Fabric=4 / NeoForge=6）；CF 来源模组放宽版本过滤 |
| v1.8.4 | 全库搜索 100 条/批异步翻页（批内 5 页零网络）；铲除全部图标与 `?` 占位；Cupboard 生态补进四模组组 |
| v1.8.2 | 推荐清单纯平铺标签化；缓存翻页；loader 稳定版优先 |
| v1.8.0 | 分离双视图（打开/翻页不再卡 3-4 秒）；推荐清单重构；配置页补全；更新检测修复 |
| v1.7.3 | CreateDialog 子按钮悬停动效；MC 版本多级回退识别 |

完整更新日志见 Releases 说明与仓库内 `更新日志.txt`。

## 📄 许可证

[MIT License](LICENSE)
