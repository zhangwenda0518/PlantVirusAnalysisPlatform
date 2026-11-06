# -*- coding: utf-8 -*-
"""`python -m Virus_Platform_Core.report_kit <目录> [--force] [--open]` 入口。

此前 __init__.py 与文档都写了这个用法，但包里没有 __main__.py，
命令实际不可执行（No module named ...report_kit.__main__）——补上。
注意 __init__.main 是函数，直接导入调用。
"""
from . import main

raise SystemExit(main())
