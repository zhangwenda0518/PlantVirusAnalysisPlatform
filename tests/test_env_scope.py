# -*- coding: utf-8 -*-
"""顶栏「本模块环境」口径守卫（2026-11-05 用户反馈「模块数 / 外部工具不对」）。

三条契约：

① **每张卡都有作用域登记**：导航组里的每个 item 必须在 HASH_TO_SCOPE
   （工具页卡片）或 PATH_SCOPE（独立页）里有条目——漏登记会让点击该卡时
   回退成整组口径（历史：t-loganfetch / t-annochain 就是这样漏的）。
② **模块位分母 = 卡片数**：组概览的「模块 n/n」数的是本组二级模块（卡片），
   与页面可见模块一致；旧口径数去重后的引擎 Python 模块（样本处理 6 张卡
   却显示 3/3），用户无法核对。
③ **独立页面也要有工具口径**：数据资源组的下载/查询/建库页不在
   TOOL_REGISTRY 里，依赖走 PAGE_TOOLS；声明的键必须能被探测表识别。
"""
import pytest

from Virus_Platform_Core import module_deps as md
from Virus_Platform_Core.selfcheck import (HASH_TO_SCOPE, PATH_SCOPE,
                                           SCOPE_DBS, _group_cards)
from Virus_Platform_Core.web.pages import NAV_GROUPS


def test_every_group_card_has_scope_mapping():
    """① 卡片 → 作用域登记完整（防点击回退成组口径）。"""
    missing = []
    for g in NAV_GROUPS:
        for it in g['items']:
            if it.get('id'):
                if it['id'] not in HASH_TO_SCOPE:
                    missing.append((g['id'], it['id']))
            else:
                href = (it.get('href') or '').rstrip('/') or '/'
                if href not in PATH_SCOPE:
                    missing.append((g['id'], it.get('href')))
    assert not missing, f'导航卡片未登记作用域: {missing}'


def test_group_module_count_is_card_count():
    """② 组概览的模块分母 = 卡片数（与页面可见模块一致）。"""
    bad = []
    for g in NAV_GROUPS:
        cards = _group_cards(g['id'])
        if len(cards) != len(g['items']):
            bad.append((g['id'], len(cards), len(g['items'])))
    assert not bad, f'模块计数与卡片数不一致: {bad}'


def test_page_tools_cover_standalone_pages():
    """③ 独立页面的工具依赖已登记（数据资源组曾整组显示『外部工具 —』）。"""
    for page in ('download', 'refdata', 'build', 'submit'):
        assert md.tools_for(page), f'{page} 未登记工具依赖'


def test_all_scope_tool_keys_are_probeable():
    """工具页/阶段 + 独立页面声明的键都必须能在探测表里找到。"""
    from Virus_Platform_Core.config import detect_tools
    known = set(detect_tools()) | set(md._NO_PLUGIN_HINT)
    keys = set()
    for table in (md.MODULE_TOOLS, md.PAGE_TOOLS, md.CARD_TOOLS):
        for ks in table.values():
            # MODULE_TOOLS 允许「任一组」（嵌套列表），展平后再探活
            keys.update(md._flat_keys(ks))
    assert not (keys - known), f'未知工具键: {sorted(keys - known)}'


def test_card_tools_reference_real_cards():
    """卡片专属工具表的键必须是真实卡片 hash。"""
    bad = [c for c in md.CARD_TOOLS if c not in HASH_TO_SCOPE]
    assert not bad, f'CARD_TOOLS 引用了不存在的卡片: {bad}'


def test_scope_db_keys_known():
    """SCOPE_DBS 只能引用自检数据库段里真实存在的库键。"""
    allowed = {'taxonomy', 'host', 'virus', 'kvref', 'homolog'}
    bad = [(m, k) for m, deps in SCOPE_DBS.items()
           for k, _soft in deps if k not in allowed]
    assert not bad, f'未知数据库键: {bad}'


def test_global_db_items_include_kvref():
    """全局自检数据库段必须有『病毒参考库』条目（kv 组依赖它）。"""
    from Virus_Platform_Core.selfcheck import _check_databases
    names = [i['name'] for i in _check_databases()['items']]
    assert any('virusref' in n for n in names), names


def test_pydeps_bit_sits_before_tools(monkeypatch):
    """④ 顶栏五件套：Python 依赖包位插在「外部工具」之前（2026-11-06）。

    两个分支（全局页 / 作用域页）都要保序；pydeps 明细挂在 detail.pydeps。
    pydeps 状态用预置缓存顶掉，不真扫源码树（analyze() 全树 AST 要数秒）。
    """
    import time
    from Virus_Platform_Core import selfcheck as sc
    monkeypatch.setattr(sc, '_PYDEPS_CACHE', {
        'ts': time.time(),
        'data': {'bit': {'id': 'pydeps', 'ok': True, 'warn': False,
                         'summary': '9/9'},
                 'detail': {'scanned': 9, 'na': False,
                            'hard_missing': [], 'optional_missing': []}}})
    base = {'ok': True, 'sections': [
        {'id': 'modules', 'ok': True, 'summary': '8/8', 'items': []},
        {'id': 'tools', 'ok': True, 'summary': '3/3', 'missing': [],
         'fixable': {}},
        {'id': 'databases', 'ok': True, 'summary': '就绪', 'items': []},
        {'id': 'disk', 'ok': True, 'summary': '37.9 GB', 'items': []},
    ]}
    # 全局页分支（工作台：无 path 作用域）
    st = sc.scoped_status(path=None, base=base)
    assert [b['id'] for b in st['bits']] == \
        ['modules', 'pydeps', 'tools', 'db', 'disk']
    assert st['detail']['pydeps']['scanned'] == 9
    # 作用域分支（独立页 /meta）
    st2 = sc.scoped_status(path='/meta', base=base)
    assert [b['id'] for b in st2['bits']] == \
        ['modules', 'pydeps', 'tools', 'db', 'disk']
    assert st2['detail']['pydeps']['hard_missing'] == []


def test_group_page_always_group_scope():
    """⑤ 组页切卡不切口径（2026-11-06 用户反馈：组内切卡出现 1/1 · 全局依赖数）。

    /tools?g=xxx 下无论停在组概览还是任何一张卡，环境条都按**组**口径出数；
    只有不带 g 的直链（/tools#t-xxx）保留单卡口径。
    """
    from Virus_Platform_Core.selfcheck import resolve_scope
    mods, title, gid = resolve_scope(path='/tools', group='kvsuite',
                                     hash_id='t-variant')
    assert gid == 'kvsuite'
    assert title == '病毒定量与共识'
    # 直链（无 g）仍保留单卡口径
    mods2, title2, gid2 = resolve_scope(path='/tools', hash_id='t-variant')
    assert gid2 is None and mods2 == ['kvsuite']
    assert title2 == '病毒变异分析'


def test_group_module_bit_counts_function_cards_only(monkeypatch):
    """⑥ 组口径模块位只数功能卡：⚡一键链卡是包装层，不算模块。

    病毒定量与共识组可见模块 = 3 张功能卡（识别 / 共识 / 变异），
    ⚡一键分析（全流程）不进分母——用户拿页面数模块时对得上。
    """
    import time
    from Virus_Platform_Core import selfcheck as sc
    monkeypatch.setattr(sc, '_SCOPE_PYDEPS_CACHE', {
        'kvsuite': (time.time(),
                    {'bit': {'id': 'pydeps', 'ok': True, 'warn': False,
                             'summary': '8/8'},
                     'detail': {'scanned': 8, 'na': False,
                                'hard_missing': [], 'optional_missing': []}})})
    base = {'ok': True, 'sections': [
        {'id': 'modules', 'ok': True, 'summary': '170/170', 'items': []},
        {'id': 'tools', 'ok': True, 'summary': '38/38', 'missing': [],
         'fixable': {}},
        {'id': 'databases', 'ok': True, 'summary': '就绪', 'items': []},
        {'id': 'disk', 'ok': True, 'summary': '37.9 GB', 'items': []},
    ]}
    cards = sc._group_cards('kvsuite')
    assert len(cards) == 4                        # 3 功能卡 + 1 ⚡链卡
    st = sc.scoped_status(path='/tools', group='kvsuite', base=base)
    mod = next(b for b in st['bits'] if b['id'] == 'modules')
    assert mod['summary'] == '3/3'
    assert st['detail']['pydeps']['scanned'] == 8  # 作用域口径生效，非全局
    assert st['title'] == '病毒定量与共识'


def test_scope_pydeps_counts_scope_packages():
    """⑦ Python 依赖位按作用域出数：数的是本作用域引擎闭包的第三方包。

    kvsuite 闭包（kv_stage + known_virus_suite/** + consensus，到共享底座
    为止）的包数必须远小于全局 50，且引擎真用到的 matplotlib/numpy 在内、
    全部已安装（当前环境）。
    """
    from Virus_Platform_Core.selfcheck import scope_pydeps_status
    d = scope_pydeps_status(['kv_stage', 'consensus'],
                            scope_key='kvsuite-test')
    assert d is not None
    scanned = d['detail']['scanned']
    assert 3 <= scanned < 50, scanned            # 不是全局口径，也不是 0
    assert d['bit']['ok'] is True                # 当前环境全部已装
    assert d['bit']['summary'] == f'{scanned}/{scanned}'
