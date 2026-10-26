# MCSM v1.7.3

**重写初始化页：修复已装识别 / 插件弹窗崩溃 / 按钮动效**

## 修复内容

1. **已装模组显示为"未装"**
   - 已装判定从精确匹配改为归一化子串包含：`ferritecore-6.0.1-fabric.jar` 现在能正确标出"✓已装 FerriteCore"，spark、lithium 等带版本尾巴的文件名不再漏判。
   - 文件名→名称猜测补全：先剥加载器段再剥版本段（`ferritecore-6.0.1-fabric → ferritecore`，`lithium-fabric-mc1.20.1-0.11.2 → lithium`），配置页/浏览页显示更规范的名字。

2. **插件弹窗 AttributeError 崩溃**
   - "选择要装哪些插件/添加插件"报 `AttributeError('ModBrowseDialog' has no attribute 'ed_q')` 的根因：双分组初始化时 `setChecked(True)` 触发切组回调，而搜索框控件还没创建。
   - 已把插件分支挪到信号连接之前，并给切组回调加 getattr 容错 —— 插件弹窗现在能正常打开。

3. **按钮悬停/点击动效真正落地**
   - `_themed` 不再静默吞异常：样式套用失败会打印日志并弹窗提示。
   - 初始化弹窗新增 `showEvent`：窗口真正显示时强制再套一次主题样式，确保 `QPushButton` 悬停高亮 / 按下回弹效果不丢失。

4. **初始化页界面规整**
   - 分区（识别结果/服务端核心/Java/内存/性能与自动化/下载/日志）间距统一；模组插件选择行平铺；按钮动效与主界面一致。
   - 已定稿特性全部保留：版本净化、G/MB 内存切换、Paper 隐藏模组行、插件放开所有加载器、异步加载器版本列表。

---

## v1.7.3-4（真机复测补丁）

5. **子按钮悬停/点击动效彻底修复**
   - 真机复测发现：初始化弹窗里「刷新版本列表 / 刷新 / 选择要装哪些… / 浏览… / 重新扫描 / 下载 Java」仍无悬停/按下动效，只有「开始 / Cancel」正常。
   - 根因：`theme.apply_palette` 给 `QScrollArea` 的 viewport 写了**局部样式表**（仅背景/字色）。Qt 里任何带局部样式表的控件都会成为样式表"分界点"，上层弹窗的 `QPushButton:hover / :pressed` 规则无法级联进滚动区内部。
   - 修复：viewport 现在套**完整主题样式表**（+ 一条自身底色覆盖保住白底修复），动效规则能送进滚动区；`CreateDialog.__init__` 末尾显式调用 `self.setStyleSheet(theme.stylesheet(None))` 强制覆盖全弹窗。

6. **MC 版本自动识别多级回退**
   - 优先级：① `level.dat` 存档（最准）→ ② 核心 jar 内部 `version.json` / `install.properties`（原版 bundler、Paper、Fabric、Forge/NeoForge 全覆盖，读不出再退 jar 文件名）→ ③ 文件夹名正则（`1.20.1forge → 1.20.1`、`26.2push → 26.2`）。
   - 全部失败：版本框留空，提示「未能自动识别版本，请手动填写」。

---

## v1.8.0

**分离双视图（打开/翻页不再卡 3-4 秒）+ 推荐清单重构 + 配置页补全 + 更新检测修复**

## 修复内容

1. **「选择模组/插件」打开与翻页 3-4 秒延迟彻底解决**
   - 根因：打开弹窗即发网络搜索，且内置推荐每项都要联网拉图标（`project_info` 并发请求），翻页在推荐模式下也会强行走网络。
   - 方案：`QTabWidget` 物理分离【推荐安装】/【全库搜索】两个独立视图。
     - 【推荐安装】**只读本地清单**（`MOD_TARGETS` / `PLUGIN_TARGETS`），**绝对不发任何网络请求**（连图标也不拉，统一占位），同步渲染毫秒级完成；分页在内存中完成（每页 20 个）。
     - 【全库搜索】只在**用户搜索、回车、或切到该 Tab**时才发网络请求；翻页携带正确 `offset`。
     - 全库等待时列表中央显示 **"正在搜索..."**，不再白屏。

2. **推荐模组清单重构（Cupboard 生态 + 第三方登录优先）**
   - 新增分组 **「① Cupboard 生态」**：`cupboard`（描述：提供 JSON 配置框架与崩溃追踪的核心前置），Paper 服推荐区可见。
   - **第三方登录优先**：`AkiYggdrasil`（1.21.1 NeoForge，放 neoforge 组最前）、`MultiYggdrasil`（Forge/Fabric/NeoForge，fabric/forge/neoforge 组最前）。
   - `cloth-config` 已从 `MOD_TARGETS` 本体移除。

3. **推荐插件清单新增 + 前置绑定**
   - 新增内置 `PLUGIN_TARGETS`：`multilogin` 最前（第三方登录第一）、`vault` 经济基础、`luckperms`、`essentialsx`（`req=["vault","luckperms"]` 绑死）、`coreprotect` / `ledger` 防熊插件。
   - `install_plugins` 复用 `resolve_selection(plugin=True)`：勾选后自动**补前置 + 拓扑排序**，先装前置再装依赖，缺前置的插件跳过并提示。

4. **配置页补全（语音聊天 + 插件配置）**
   - **Simple Voice Chat**：配置路径升级为 `config/voicechat/voicechat-server.properties`（旧路径自动回退兼容）；新增**分组功能开关**（`disable_groups`），连同端口 / 语音距离 / 广播范围 / 开关一起读写。
   - **新增「插件配置」页签**（在「插件」管理页之后）：LuckPerms 简单配置界面（存储方式 h2/SQLite/MySQL、server、默认组、MySQL 连接参数，读写 `config/LuckPerms/config.yml` 并自动备份），EssentialsX 一键生成基础配置。

5. **「检查更新」网络异常修复**
   - 确认 `GITHUB_REPO="ftasy/MCSM"` 已配置；修复 `check_latest` 中 URL 二次 `%` 格式化导致的 TypeError。
   - 补充超时（10s）与退避重试（最多 3 次，1.5/3/6 秒），5xx/连接类异常自动重试，4xx 不重试。

## 下载说明

- **ZIP 版（推荐）**：解压后运行 `MCSM.exe`，无需安装，绿色免安装。
- **EXE 版**：直接运行 `MCSM.exe`（含运行库，首次启动略慢）。
- 软件支持在线检查更新：顶栏「检查更新」会自动跳转到本仓库 Release 页面。

---

## v1.8.2

**最终版重构：剔除冲突模组、纯平铺标签化、零延迟翻页与页签重排**

## 重构内容

1. **推荐模组清单 MOD_TARGETS 纯平铺（彻底去掉分组标题）**
   - 所有 `group` 一律置空，界面不再有「=== 前置库 ===」这类大标题行；卡片像 PCL2 一样一个接一个往下排。
   - 每个条目描述末尾带彩色小标签：`[第三方登录]`（蓝）、`[前置库]` / `[Cupboard附属]`（橙）、`[优化]`（绿）、`[监控]`（紫）、`[崩溃分析]` / `[防熊]`（红）、`[工具]` / `[建筑]`（青）、`[权限]`（黄）、`[聊天]` / `[聊天扩展]`（粉）等。
   - **第三方登录排最前**：`multiyggdrasil`、`akiyggdrasil`（1.21.1 NeoForge）。
   - **Cupboard 生态**：`cupboard`（核心前置）+ `fastasyncworldsave` / `structureessentials` / `recipeessentials` / `connectivity` / `chunksending`。
   - **冲突项已剔除**：`packetfixer` 不装（Connectivity 功能更全，防卡包更强）；`morechathistory` 不装（Chat Patches 包含聊天记录上限，功能更强）。
   - 性能/工具项：`clumps`、`tabtps`、`crashassistant`、`spark`、`chunky`、`luckperms`、`chatpatches`。
   - 注：Cupboard 生态插件及 `crashassistant` / `essentialsxchat` / `multilogin` / `akiyggdrasil` 在 Modrinth 暂无发布，下载失败会在日志提示，请按指引手动安装 jar。

2. **推荐插件清单 PLUGIN_TARGETS 平铺 + 前置严格绑定**
   - 顺序：`multilogin`（第三方登录第一）→ `luckperms` → `vault` → `essentialsx`（`req=["vault","luckperms"]`）→ `essentialsxchat`（`req=["essentialsx"]`）→ `coreprotect`（防熊）→ `worldedit`（建筑）→ `worldguard`（区域保护）→ `tabtps` → `spark`（监控）。
   - 插件服推荐列表 = 插件清单 + Cupboard 生态，去重平铺合并，下载时 `resolve_selection` 自动先装前置。

3. **全库搜索翻页彻底零网络**
   - 搜索改为**一次拉取 `limit=100`**，结果与图标全部缓存到内存（`_search_cache` / `_icons_cache`）。
   - 上一页 / 下一页直接读缓存切片，**不再发任何网络请求**；只有点「搜索」或回车才重新请求。
   - 等待时列表中央仍显示「正在搜索...」。

4. **Fabric loader 稳定版优先**
   - `fabric_loader_candidates` 排序修复：稳定版全部排在测试版之前，同 stable 内按版本号新→旧，避免 beta 排前面导致取到旧稳定版。

5. **配置页页签重排 + 合并**
   - `ServerConfigDialog` 页签顺序固定为：**登录 → server.properties → 核心 → 模组 → 插件 → 模组与插件配置 → 备份 → 白名单 → 启动设置 → 性能优化**。
   - 原「插件配置」与「模组配置」合并为**「模组与插件配置」**一个页签（模组配置在上、插件配置在下），不再单独占两个页签。

## 下载说明

- **ZIP 版（推荐）**：解压后运行 `MCSM.exe`，无需安装，绿色免安装。
- **EXE 版**：直接运行 `MCSM.exe`（含运行库，首次启动略慢）。
- 软件支持在线检查更新：顶栏「检查更新」会自动跳转到本仓库 Release 页面。

---

## v1.8.4

**终极重构：按批加载异步翻页（100 条/批）、彻底铲除图标、强行补全 Cupboard 生态**

## 重构内容

1. **全库搜索改为『按批加载』异步翻页（不再死页面）**
   - `batch_size=100`（每次请求条数）、`page_size=20`（UI 每页显示条数）。
   - `current_batch` 缓存当前 100 条，批内第 1~5 页切换**零网络秒切**。
   - 批内第 5 页再点「下一页」：显示 **"正在搜索下一批..."**，自动请求 `offset += 100`，返回后替换批次并定位到新批次第 1 页。
   - 批内第 1 页点「上一页」且 `offset > 0`：显示 **"正在搜索上一批..."**，请求 `offset -= 100`，返回后定位到上一批次最后 1 页（第 5 页）。
   - 按钮可用性按公式实时计算，无论翻多少页都正常加载、不会死页面。

2. **彻底移除所有图标与 `?` 占位**
   - 删除全部 `setIcon` 相关绘制；左侧 `?` 问号占位已铲除。
   - 复选框与文字直接顶格靠左对齐，纯文字紧凑排版，翻页更快更干净。

3. **强行补全 Cupboard 生态（Fabric / Forge / NeoForge 三组）**
   - 三个模组组均加入：`cupboard`、`fastasyncworldsave`、`structureessentials`、`recipeessentials`、`connectivity`、`chunksending`，标签统一 `[Cupboard生态]`（橙色）。
   - **冲突项绝对剔除**：代码里没有任何 `packetfixer`（与 connectivity 冲突，Connectivity 防卡包更强）；没有任何 `morechathistory`（chatpatches 已包含聊天记录上限功能，只保留 chatpatches）。
   - 补全：`crash-assistant`（`[崩溃分析]`）、`simple-voice-chat`（`[语音]`）在 Fabric / Forge 组就位。
   - 注：Cupboard 生态插件及 `crash-assistant` 在 Modrinth 暂无发布，下载失败会在日志提示，请按指引手动安装 jar。

## 下载说明

- **ZIP 版（推荐）**：解压后运行 `MCSM.exe`，无需安装，绿色免安装。
- **EXE 版**：直接运行 `MCSM.exe`（含运行库，首次启动略慢）。
- 软件支持在线检查更新：顶栏「检查更新」会自动跳转到本仓库 Release 页面。
