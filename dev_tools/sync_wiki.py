# -*- coding: utf-8 -*-
"""把仓库根目录 wiki/*.md 与 wiki/assets/ 同步到 webapp/static/wiki/。

为什么要有静态副本：`/help` 读的是 README.md，而组概览里的模块文档由浏览器
直接 fetch 静态文件（`/static/wiki/<名>.md`）。静态目录是 Flask 的静态根，
onedir 分发版里它就是外部数据文件 —— 改文档只替换文件即可生效，不用重打 exe。
文档里 `![alt](assets/xxx.png)` 引用的图片同样需要静态副本：源目录
`wiki/assets/` 会递归镜像到 `webapp/static/wiki/assets/`（md 里写 GitHub wiki
兼容的相对路径，前端渲染时再改写到静态路径）。

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
ASSETS = 'assets'               # wiki/assets/：文档示例图（PNG）


def _sync_md():
    """复制 md，并清理源目录已删除的静态副本。"""
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
    return copied, removed


def _asset_files(base):
    """递归列出 base 下所有文件的相对路径（统一 / 分隔，便于比较）。"""
    out = set()
    if not os.path.isdir(base):
        return out
    for root, _dirs, files in os.walk(base):
        for name in files:
            full = os.path.join(root, name)
            out.add(os.path.relpath(full, base).replace(os.sep, '/'))
    return out


def _sync_assets():
    """递归复制 wiki/assets/ → static/wiki/assets/，并清理静态侧孤儿文件。"""
    src = os.path.join(SRC, ASSETS)
    dst = os.path.join(DST, ASSETS)
    copied = 0
    for rel in sorted(_asset_files(src)):
        sp = os.path.join(src, rel.replace('/', os.sep))
        dp = os.path.join(dst, rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(dp), exist_ok=True)
        shutil.copyfile(sp, dp)
        copied += 1
    # 源里已删除的图片，静态侧同样清掉，避免文档里挂着死图
    keep = _asset_files(src)
    removed = 0
    for rel in sorted(_asset_files(dst)):
        if rel not in keep:
            os.remove(os.path.join(dst, rel.replace('/', os.sep)))
            removed += 1
    # 清掉因此变空的子目录（assets 根目录本身保留）
    for root, _dirs, _files in os.walk(dst, topdown=False):
        if root != dst and not os.listdir(root):
            os.rmdir(root)
    return copied, removed


def main():
    if not os.path.isdir(SRC):
        print(f'没有找到 {SRC}')
        return 1
    os.makedirs(DST, exist_ok=True)
    copied, removed = _sync_md()
    print(f'copied {copied}, removed {removed} -> {DST}')
    acopied, aremoved = _sync_assets()
    print(f'assets: copied {acopied}, removed {aremoved} -> {os.path.join(DST, ASSETS)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
