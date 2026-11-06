# -*- coding: utf-8 -*-
"""vendor_suvtk -- suvtk 0.2.0 的仓库内 vendor 子集（进程内调用）。

Provenance
----------
来源仓库 : https://github.com/LanderDC/suvtk
上游版本 : 0.2.0（commit d8ef68f）
提取日期 : 2026-11-02
提取文件 : comments.py / table2asn.py / gbk2tbl.py / utils.py
上游许可 : GNU General Public License v3（全文见本目录 LICENSE）

本仓库为什么 vendor
-------------------
服务器磁盘紧张（约 98% 占用），不再安装 suvtk 包及其 CLI 依赖（rich-click 等）。
28.ncbi_submission 只需要 comments / table2asn / gbk2tbl 三个子命令的核心逻辑，
因此把这四个文件复制进仓库，去掉 rich_click 装饰器后进程内直调，便于改源码。

修改约定
--------
* 每处修改都有 ``# [ncbisub-vendor]`` 行注释；
* 逐条登记见 ``PATCHES.md``，可直接与上游 0.2.0 做 diff；
* 保持 Python 3.8 兼容（服务器 gunicorn 使用 /usr/bin/python3.8）。

注意
----
vendor 代码不是 suvtk 官方发行版；行为差异以 PATCHES.md 为准。
本包运行时依赖 ``pandas`` 与 ``biopython``（gbk2tbl 使用 Bio.SeqIO）。
"""

__version__ = "0.2.0"

# [ncbisub-vendor] 只导出本仓库实际使用的四个模块；上游 __init__ 还会
# 导入 co_occurrence / features / taxonomy 等未 vendor 的子命令。
# 这里用 PEP 562 的模块 __getattr__ 做惰性导出：
#   * `python -m vendor_suvtk.gbk2tbl` 不会先被 __init__ 预导入（无 runpy 警告）；
#   * 只做 gbk2tbl 时不需要 pandas（comments/table2asn 才需要）。
_LAZY_MODULES = ("utils", "comments", "gbk2tbl", "table2asn")

__all__ = list(_LAZY_MODULES) + ["__version__"]


def __getattr__(name):
    if name in _LAZY_MODULES:
        import importlib

        module = importlib.import_module("." + name, __name__)
        globals()[name] = module
        return module
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
