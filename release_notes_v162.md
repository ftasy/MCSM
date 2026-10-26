# MCSM v1.6.2 — 紧急修复版

> 修复：UI 阻塞、CMD 黑框弹窗、暗色主题下卡片文字不可见

## 主要更新

### 1. 彻底消除后台 CMD 黑框弹窗
- 全局 `subprocess` 调用统一加 `CREATE_NO_WINDOW`：tar 解压、防火墙 powershell、`java -version`、Forge/NeoForge installer、explorer 等。
- `os.system` 全部改为 `subprocess.run`，不再弹黑框。

### 2. 修复主窗口阻塞（彻底异步化）
- `do_create` / `do_config` 改为非模态打开（`dlg.show()`），下载、初始化、改配置时主界面照常可操作。
- 对话框关闭后由 `finished` 信号处理保存与列表刷新，杜绝静默崩溃。
- `do_config` 外层加异常捕获弹窗，窗体打不开不再后台静默失败。

### 3. 模组/插件卡片暗色主题适配
- 卡片底色跟随主题、标题用主题字体色，彻底解决"黑字黑底看不见"。
- 去掉"① 前置"分组标题，卡片全部平铺。

### 4. 加载器版本下拉框初始为空修复
- 初始化完成自动联动查询版本清单；网络未返回时显示"正在查询版本..."并禁用，回调成功后再填充启用。

### 5. 控制台配色设置界面优化
- 颜色按钮改为色块显示，点击弹出 `QColorDialog`，即时生效；保留"恢复默认配色"。

### 6. 服务端配置 YAML 交互加固
- `server.properties` 读取失败回退默认值；YAML 页签单项异常自动跳过，打开配置窗口绝不卡死。

## 安装
- 源码包：解压后运行 `start_manager.bat`。
- EXE 版：`dist\MCSM\MCSM.exe`（无需安装 Python，双击即用）。

## 下载
- 安装包：`MCSM_v1.6.2_安装包.zip`
- EXE：`dist/MCSM/MCSM.exe`
