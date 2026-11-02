# -*- coding: utf-8 -*-
"""AI 代理自举层测试：agent_guide + agent_catalog（只读，不执行任何真实任务）。

对应 2026-11-01 新增的发现层四件套与指南/技能卡；顺带锁死「参数表唯一权威
在 agent_catalog，mcp_server 只 import」这条单一事实源约定。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from Virus_Platform_Core import agent_catalog as ac  # noqa: E402
from Virus_Platform_Core import agent_guide as ag    # noqa: E402


def test_guide_readable():
    g = ag.read_guide()
    assert 'VirusPlatform' in g
    assert '计划 ≠ 已执行' in g
    assert 'agent-ops' in g
    assert len(g) > 500


def test_guide_fallback_when_missing():
    """指南文件缺失时不抛异常，回退内嵌兜底版。"""
    orig = ag._guide_path
    ag._guide_path = lambda: os.path.join(os.getcwd(), '__no_such_AGENTS__.md')
    try:
        g = ag.read_guide()
        assert '内嵌兜底版' in g
    finally:
        ag._guide_path = orig


def test_skill_has_frontmatter_and_pointers():
    s = ag.read_skill()
    assert s.lstrip().startswith('---')
    assert 'agent-guide' in s and 'agent-ops' in s
    assert '计划 ≠ 已执行' in s


def test_catalog_shape():
    d = ac.list_operations()
    assert d['total'] >= 50
    assert {x['kind'] for x in d['items']} == {'tool', 'stage', 'action'}
    for x in d['items']:
        assert x['id'] and x['title'] and x['kind'] in ('tool', 'stage', 'action')
        assert 'invoke' in x


def test_catalog_has_known_ops():
    ids = {x['id'] for x in ac.list_operations()['items']}
    assert {'assemble', 'kvsuite', 'identify', 'plan', 'create_sample'} <= ids
    stage_ids = {x['id'] for x in ac.list_operations('stage')['items']}
    assert {'assembly', 'kvsuite', 'report'} <= stage_ids


def test_search_zh_en():
    assert any(x['id'] == 'assembly' for x in ac.search('组装')['items'])
    assert any(x['id'] == 'kvsuite' for x in ac.search('kvsuite')['items'])
    assert ac.search('')['total'] == 0
    # 空白串安全
    assert ac.search('   ')['total'] == 0


def test_schema_tools():
    s = ac.parameter_schema('assemble')
    assert 'r1' in s['required']
    assert any(p['key'] == 'mode' for p in s['params'])
    # kvsuite 是「三选一」口径：表不标 required，note 说明缺失当场报
    s = ac.parameter_schema('kvsuite')
    assert any(p['key'] == 'samples' for p in s['params'])
    assert '当场报' in s['note']


def test_schema_stage_and_action():
    s = ac.parameter_schema('phylo')
    assert 'assembly_mode' in s['note']
    s = ac.parameter_schema('plan')
    assert 'steps' in s['required']


def test_action_risk_exposed():
    """2026-11-02：执行单动作的批准门槛（risk）必须随目录暴露。

    外部 agent 靠它判断「能不能进自动重试白名单」，目录不报就只能猜。
    """
    from Virus_Platform_Core.ai_agent.executors import ACTIONS
    acts = ac.list_operations('action')['items']
    assert acts
    for it in acts:
        assert it['risk'] == ACTIONS[it['id']].get('risk', 'confirm')
        assert isinstance(it['in_plan'], bool)
    # 与 executors 登记一致：唯一可自动的是原参数重试
    auto = {it['id'] for it in acts if it['risk'] == 'auto_ok'}
    assert auto == {'retry_failed'}
    s = ac.parameter_schema('retry_failed')
    assert s['risk'] == 'auto_ok' and '自动重试' in s['note']
    assert ac.parameter_schema('download_runs')['risk'] == 'confirm'


def test_describe_unknown_safe():
    assert ac.describe('nope') is None
    assert ac.parameter_schema('nope') is None


def test_param_table_single_source():
    """参数表唯一权威迁移约定：mcp_server 只 import，不再持有原表。"""
    src = open(os.path.join(ROOT, 'mcp_server.py'), encoding='utf-8').read()
    assert ('from Virus_Platform_Core.agent_catalog import '
            'TOOL_PARAM_SCHEMAS as _TOOL_PARAM_SCHEMAS') in src
    assert '_TOOL_PARAM_SCHEMAS = {' not in src
