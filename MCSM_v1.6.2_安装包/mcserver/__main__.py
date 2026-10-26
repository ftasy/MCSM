# -*- coding: utf-8 -*-
import sys
from mcserver.app import main

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        # 加上异常捕获，防止后台运行报错时窗口直接闪退，连怎么死的都不知道
        import traceback
        print(f"程序崩溃: {e}")
        traceback.print_exc()
        input("按回车键退出...") 
