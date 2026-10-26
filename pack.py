# -*- coding: utf-8 -*-
"""一键打包脚本：自动剔除缓存和用户配置，生成干净的发布包。"""

import os
import zipfile
import time

# 版本号（用来给压缩包命名）
VERSION = "v1.8.0"
# 产品名（MCSM = Minecraft Server Manager）
APP_NAME = "MCSM"
OUT_ZIP = f"{APP_NAME}_{VERSION}_安装包.zip"

# 绝对不要打进包里的目录和文件
EXCLUDE_DIRS = {"__pycache__", ".git", ".idea", ".vscode",
                "build", "dist"}  # ★ PyInstaller 产物不进源码包
# ★ V1.6.3：用户解压安装包测试留下的 MCSM_vX.Y_安装包/ 目录
#   绝不能递归打进新包（否则包里有包、体积膨胀）。
EXCLUDE_PREFIX_DIRS = ("MCSM_v",)
EXCLUDE_EXTS = {".pyc", ".pyo", ".zip", ".rar", ".7z", ".log"}
EXCLUDE_FILES = {
    "servers.json",          # 用户服务器列表
    "theme.json",            # 用户主题
    "sources.json",          # 用户下载源配置
    "mc_versions_cache.json",# 版本缓存
    "安装日志.txt",          # 测试产生的安装日志
    "pack.py",               # 打包脚本自己不需要进包
}

def pack():
    print(f"=== 开始打包 {VERSION} ===")

    # 如果旧包存在，先删掉
    if os.path.exists(OUT_ZIP):
        os.remove(OUT_ZIP)
        print(f"已删除旧的打包文件: {OUT_ZIP}")

    count = 0
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk("."):
            # 过滤掉不需要的目录（原地修改 dirs，影响 os.walk 遍历）
            dirs[:] = [d for d in dirs
                       if d not in EXCLUDE_DIRS
                       and not d.startswith(EXCLUDE_PREFIX_DIRS)]

            for f in files:
                # 过滤掉不需要的后缀和文件名
                if f in EXCLUDE_FILES:
                    continue
                if any(f.endswith(ext) for ext in EXCLUDE_EXTS):
                    continue

                fp = os.path.join(root, f)
                # 写入 zip，去掉前面的 ./ 路径
                arcname = os.path.relpath(fp, ".")
                zf.write(fp, arcname)
                count += 1

    print(f"=== 打包完成！ ===")
    print(f"共打包 {count} 个文件")
    print(f"文件保存在: {os.path.abspath(OUT_ZIP)}")
    input("\n按回车键退出...")

if __name__ == "__main__":
    pack()
