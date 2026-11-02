# -*- coding: utf-8 -*-
"""把仓库根目录 wiki/*.md 同步到 webapp/static/wiki/。

为什么要有静态副本：`/help` 读的是 README.md，而组概览里的模块文档由浏览器
直接 fetch 静态文件（`/static/wiki/<名>.md`）。静态目录是 Flask 的静态根，
onedir 分发版里它就是外部数据文件 —— 改文档只替换文件即可生效，不用重打 exe。

映射表在同目录的 index.json（手工维护，tests/test_wiki_docs.py 校验覆盖率）。
本脚本只复制文件，不改 index.json：

    python dev_tools/sync_wiki.py
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'wiki')
DST = os.path.join(ROOT, 'webapp', 'static', 'wiki')
SKIP = {'_Sidebar.md'}          # GitHub wiki 的侧边栏目录，不是正文


def main():
    if not os.path.isdir(SRC):
        print(f'没有找到 {SRC}')
        return 1
    os.makedirs(DST, exist_ok=True)
    copied = 0
    for name in sorted(os.listdir(SRC)):
        if not name.endswith('.md') or name in SKIP:
            continue
        shutil.copyfile(os.path.join(SRC, name), os.path.join(DST, name))
        copied += 1
    # 源里已删除的文档，静态侧也清掉，避免组概览里挂着死链
    keep = {n for n in os.listdir(SRC) if n.endswith('.md') and n not in SKIP}
    removed = 0
    for name in os.listdir(DST):
        if name.endswith('.md') and name not in keep:
            os.remove(os.path.join(DST, name))
            removed += 1
    print(f'copied {copied}, removed {removed} -> {DST}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
