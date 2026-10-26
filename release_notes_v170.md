# MCSM v1.7.0

## 1. 选择模组视图重构（双分组）
- 顶部新增『内置推荐 / 全库搜索』切换
- 内置推荐按当前服版本 + 加载器过滤兼容性，剔除纯客户端模组（Sodium / Cloth Config 等），分组标题平铺
- 全库搜索强制过滤纯客户端模组
- 已装模组显示绿色「✓已装」，默认不勾选（勾选可覆盖更新）

## 2. 多源登录修复
- 第三方源支持多选：LittleSkin / MUA Union / 自定义可同时勾选
- 保存防呆：多源共存必须至少勾选一个源；未检测到 MultiYggdrasil / MultiLogin 组件时自动询问下载
- 修复保存后选中串台：刷新后按 server_dir 绝对路径精准回显，不再按行号

## 3. 插件支持放开所有加载器
- Fabric / Forge / NeoForge 服也能勾选下载插件、配置页显示插件页签，插件版本按实际加载器过滤

## 4. 新增可配置模组：Simple Voice Chat
- 模组配置页签新增语音聊天：端口（默认 24454）、语音距离、广播范围、开关
- 生成 / 回写 config/simple-voice-chat/server.properties

## 5. 按钮动效加强
- 主界面导航按钮悬停 / 按下高亮增强，文字提亮，按下下沉回弹
- 全局按钮悬停 / 按下底色加深

## 6. 复核
- QTimeEdit 导入、do_analyze / do_display 非模态化确认

下载：ZIP 源码包 + 绿色版 EXE（需连 _internal 一起拷贝）。
