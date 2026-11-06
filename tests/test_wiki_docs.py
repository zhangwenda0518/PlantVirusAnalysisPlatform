# -*- coding: utf-8 -*-
"""组概览 wiki 文档的守护测试。

四件事必须一直成立：
  1. `webapp/static/wiki/*.md` 与仓库根 `wiki/*.md` 逐字节一致
     （改文档忘了跑 `python dev_tools/sync_wiki.py` 时这里会红）；
  2. `index.json` 把每一篇文档恰好映射一次（不多、不少、不重复），
     否则组概览里会漏文档或同一篇出现两次；
  3. 每个导航分组都有映射，且映射到的文件真实存在；
  4. `wiki/assets/` 下的每个图片在 `webapp/static/wiki/assets/` 有逐字节
     一致的副本（静态侧多出来的孤儿文件同样报红）。
"""
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIKI = os.path.join(ROOT, 'wiki')
STATIC = os.path.join(ROOT, 'webapp', 'static', 'wiki')
INDEX = os.path.join(STATIC, 'index.json')
SKIP = {'_Sidebar.md'}


def _md_names(folder):
    return sorted(n for n in os.listdir(folder)
                  if n.endswith('.md') and n not in SKIP)


def _index():
    with open(INDEX, encoding='utf-8') as fh:
        return json.load(fh)


def test_static_copies_match_repo_wiki():
    names = _md_names(WIKI)
    assert names, 'wiki/ 下没有文档'
    for name in names:
        src = os.path.join(WIKI, name)
        dst = os.path.join(STATIC, name)
        assert os.path.exists(dst), f'缺少静态副本: {name}（跑 dev_tools/sync_wiki.py）'
        with open(src, 'rb') as a, open(dst, 'rb') as b:
            assert a.read() == b.read(), f'静态副本与 wiki/ 不一致: {name}'


def test_static_has_no_orphans():
    extra = set(_md_names(STATIC)) - set(_md_names(WIKI))
    assert not extra, f'wiki/ 里已删除、静态侧还留着: {sorted(extra)}'


def _asset_files(base):
    out = set()
    if not os.path.isdir(base):
        return out
    for root, _dirs, files in os.walk(base):
        for name in files:
            full = os.path.join(root, name)
            out.add(os.path.relpath(full, base).replace(os.sep, '/'))
    return out


def test_static_asset_copies_match_repo_wiki():
    src_root = os.path.join(WIKI, 'assets')
    dst_root = os.path.join(STATIC, 'assets')
    names = _asset_files(src_root)
    for rel in sorted(names):
        src = os.path.join(src_root, rel.replace('/', os.sep))
        dst = os.path.join(dst_root, rel.replace('/', os.sep))
        assert os.path.exists(dst), f'缺少静态副本: assets/{rel}（跑 dev_tools/sync_wiki.py）'
        with open(src, 'rb') as a, open(dst, 'rb') as b:
            assert a.read() == b.read(), f'静态副本与 wiki/ 不一致: assets/{rel}'
    extra = sorted(_asset_files(dst_root) - names)
    assert not extra, f'wiki/assets 里已删除、静态侧还留着: {extra}'


def test_index_maps_every_doc_exactly_once():
    idx = _index()
    slugs = [s for arr in idx['groups'].values() for s in arr] + list(idx['global'])
    dup = sorted({s for s in slugs if slugs.count(s) > 1})
    assert not dup, f'重复映射: {dup}'
    have = {n[:-3] for n in _md_names(WIKI)}
    missing = sorted(have - set(slugs))
    extra = sorted(set(slugs) - have)
    assert not missing, f'未映射到任何组: {missing}'
    assert not extra, f'映射指向不存在的文档: {extra}'


def test_every_nav_group_has_docs():
    pytest.importorskip('flask')
    from Virus_Platform_Core.web.pages import NAV_GROUPS
    groups = _index()['groups']
    for g in NAV_GROUPS:
        assert g['id'] in groups, f'分组 {g["id"]} 没有 wiki 映射'
        assert groups[g['id']], f'分组 {g["id"]} 的映射是空的'


def test_no_orphan_group_ids():
    """反向守护：映射里的组 id 必须都真实存在于 NAV_GROUPS。

    历史事故：2026-11-04 撤销「溯源与提交/数据提交」独立组（数据提交卡并入
    「病毒注释分析」）时 index.json 没跟上，NCBI-Submission 挂在一个永远
    不再渲染的 submit 组 id 下，整个组概览里点不到这篇文档。正向测试
    （每个导航组有映射）抓不到这种反向孤儿，这里补上。"""
    pytest.importorskip('flask')
    from Virus_Platform_Core.web.pages import NAV_GROUPS
    real = {g['id'] for g in NAV_GROUPS}
    extra = sorted(set(_index()['groups']) - real)
    assert not extra, f'映射指向不存在的导航组（孤儿组 id）: {extra}'


def test_mapped_docs_are_readable():
    for slug in [s for arr in _index()['groups'].values() for s in arr]:
        p = os.path.join(STATIC, slug + '.md')
        assert os.path.getsize(p) > 200, f'{slug}.md 太小，可能不是正文'


# ---- 三级导航结构守护（docs/WIKI_STYLE.md §0.1，2026-11-05） ----
# 模块专页必须有 ≥2 个 H2，且其中 ≥2 个 H2 下各有 ≥2 个 H3——
# 组概览侧栏的三级目录靠它存在（用户明确要求「1 2 3 三级目录」）。
# 豁免：组导读（*-Intro / Public-Data / Compare / Phylodynamics）、
# 全局/知识/管线视角文档（结构另有规范）。
_THREE_LEVEL_EXEMPT = {
    'Home', 'Quick-Start', 'Architecture', 'Pipeline-Overview', 'AI-Assistant',
    'CLI-Reference', 'Settings-Ops', 'FAQ', 'Toolbox',
    'Public-Data', 'Compare', 'Phylodynamics',
    'Sample-Intro', 'Kvsuite-Intro', 'Virus-Intro', 'Annotate-Intro',
    'Detect-Intro', 'Result-Intro',
    'Preprocess', 'Host-Prediction', 'Report', 'Result-Interpretation',
}


def _h2_h3(path):
    h2, cur, counts = 0, None, []
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            if line.startswith('## '):
                if cur is not None:
                    counts.append(cur)
                cur, h2 = 0, h2 + 1
            elif line.startswith('### ') and cur is not None:
                cur += 1
    if cur is not None:
        counts.append(cur)
    return h2, counts


def test_module_docs_have_three_levels():
    for name in _md_names(WIKI):
        slug = name[:-3]
        if slug in _THREE_LEVEL_EXEMPT:
            continue
        h2, counts = _h2_h3(os.path.join(WIKI, name))
        rich = sum(1 for c in counts if c >= 2)
        assert h2 >= 2 and rich >= 2, (
            f'{slug}.md 三级结构不达标（H2={h2}, 各H2下H3数={counts}）；'
            f'按 docs/WIKI_STYLE.md §0.1 重排')
