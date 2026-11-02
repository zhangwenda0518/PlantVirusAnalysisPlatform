# -*- coding: utf-8 -*-
"""回归：带点的 contig 名走「直接输入序列」分析不能再 400。

背景（2026-10-28 实抓）：组装出来的 contig 名几乎都带点
（NODE_15048_length_278_cov_1.892683）。直接输入序列模式用 contig 名派生
缓存目录，而 `_analysis_out()` 的运行名白名单只允许 [A-Za-z0-9_-]，
两边对点的处理不一致 → BLASTN / BLASTX / Primer 一律
400「无效的运行名」，前端表现为点了没反应。

本测试只验证「请求被接受 + 读写路径一致」，不真跑 DIAMOND：
把 tm.start 换成桩，任务不会入队。
"""
import pytest

DOTTED = 'NODE_15048_length_278_cov_1.892683'
SEQ = 'ATGC' * 70


def _client():
    from app import app
    return app.test_client()


@pytest.fixture()
def stub_start(monkeypatch):
    from Virus_Platform_Core.web import tool_results
    monkeypatch.setattr(tool_results.tm, 'start', lambda *a, **k: 'stub-task')
    return tool_results


def test_seq_cache_name_has_no_dots():
    from Virus_Platform_Core.web.tool_results import _seq_cache_name
    name = _seq_cache_name(DOTTED)
    assert '.' not in name and ' ' not in name
    assert len(name) <= 30
    # 与 _analysis_out 的运行名白名单一致
    import re as _re
    assert _re.fullmatch(r'[A-Za-z0-9_\-]{1,80}', name + '_abcdef123456_local')


@pytest.mark.parametrize('action', ['blastn', 'blastx', 'primer'])
def test_dotted_contig_sequence_input_accepted(action, stub_start):
    r = _client().post('/api/tool/analyze', json={
        'run': '_seq_input', 'contig': DOTTED, 'action': action,
        'seq': SEQ, 'engine': 'local'})
    assert r.status_code == 200, (action, r.get_data(as_text=True))
    assert r.get_json().get('task')


def test_read_path_uses_same_name(stub_start):
    """未运行时读接口应该是 404（不是 400），说明读写路径一致。"""
    r = _client().get('/api/tool/analysis', query_string={
        'contig': DOTTED, 'action': 'blastn', 'seq': SEQ, 'engine': 'local'})
    assert r.status_code == 404, r.get_data(as_text=True)


def test_run_mode_still_validates_run_name(stub_start):
    """从运行目录取序列的分支仍要拒绝非法运行名（收紧没被放松）。"""
    r = _client().post('/api/tool/analyze', json={
        'run': '..\\evil', 'contig': DOTTED, 'action': 'blastn'})
    assert r.status_code in (400, 404)
