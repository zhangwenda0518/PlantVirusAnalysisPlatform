# -*- coding: utf-8 -*-
"""组概览 wiki 文档的守护测试。

三件事必须一直成立：
  1. `webapp/static/wiki/*.md` 与仓库根 `wiki/*.md` 逐字节一致
     （改文档忘了跑 `python dev_tools/sync_wiki.py` 时这里会红）；
  2. `index.json` 把每一篇文档恰好映射一次（不多、不少、不重复），
     否则组概览里会漏文档或同一篇出现两次；
  3. 每个导航分组都有映射，且映射到的文件真实存在。
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


def test_mapped_docs_are_readable():
    for slug in [s for arr in _index()['groups'].values() for s in arr]:
        p = os.path.join(STATIC, slug + '.md')
        assert os.path.getsize(p) > 200, f'{slug}.md 太小，可能不是正文'
