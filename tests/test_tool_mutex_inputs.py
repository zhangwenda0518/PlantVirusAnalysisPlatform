# -*- coding: utf-8 -*-
"""工具箱「互斥型输入」空提交必须在提交时就 400（2026-11-02 全面测试发现）。

背景：32 个工具箱工具里有一批的输入是「多选一」——consensus 的参考可以是
contigs 运行 / kvsuite 运行 / 独立 FASTA；kvchain 的样本可以是样品 / 直填
测序数据 / 样本表；pdprep 按 source 分支各有必填；verify 是运行或 FASTA。
这类工具的 schema 里 required=[] 是对的（没有任何**单个**键永远必填），
但「一个都不给」时如果没有入口校验，就会建出一个必然失败的任务：
用户看到一张 pct=1、status=failed 的卡，真正的错误文案埋在任务 error 里，
比当场 400 差得多——而且空转一个任务目录。

本测试锁的是这条契约：上表 9 个互斥型工具，空 params 提交一律 400，
且响应体里带中文原因（不是 Flask 默认的英文 400 页面）。

为什么用 test_client 而不是真起服务：这里只验提交入口的这一层，任务体
根本不该被执行（400 在 factory 期就返回），所以不需要跑任务线程。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 9 个互斥型工具（schema required 为空，但至少需要一个输入）
MUTEX_TOOLS = [
    'consensus', 'genoplot', 'kvchain', 'kvsuite', 'mirna',
    'orfa', 'pdprep', 'phylodyn', 'verify',
]


def _client():
    from app import app
    return app.test_client()


@pytest.mark.parametrize('tool', MUTEX_TOOLS)
def test_empty_params_rejected_with_400(tool):
    """空 params → 400，且提示是中文（说明是入口校验而非 Flask 兜底）。"""
    c = _client()
    r = c.post('/api/tool/run', json={'tool': tool, 'params': {}})
    assert r.status_code == 400, (
        '%s 空提交返回 %s（应为 400）——会建出一个必然失败的任务'
        % (tool, r.status_code))
    body = r.get_json() or {}
    msg = body.get('error') or ''
    assert any('\u4e00' <= ch <= '\u9fff' for ch in msg), \
        '%s 的 400 没有中文原因，用户看不出该填什么：%r' % (tool, msg)


def test_empty_submit_creates_no_task_dir():
    """400 之后不该留下任务目录：空转任务会污染任务中心与磁盘。"""
    from Virus_Platform_Core.web import state
    c = _client()
    before = set(os.listdir(state.DIRS['tool_runs'])) \
        if os.path.isdir(state.DIRS['tool_runs']) else set()
    for tool in MUTEX_TOOLS:
        c.post('/api/tool/run', json={'tool': tool, 'params': {}})
    after = set(os.listdir(state.DIRS['tool_runs'])) \
        if os.path.isdir(state.DIRS['tool_runs']) else set()
    new = after - before
    assert not new, '空提交仍然建出了运行目录：%s' % sorted(new)[:5]


def test_valid_input_still_accepted():
    """反向确认：给了合法的互斥输入之一，不能被新校验误伤。

    phylodyn / mirna 这两个用最小合法输入验证（不真跑，只看不被 400 拦）；
    提交后立刻取消，避免留下长时间任务。
    """
    c = _client()
    # mirna：mirna_text + virus_text 是「二选一」里的合法组合
    r = c.post('/api/tool/run', json={'tool': 'mirna', 'params': {
        'mirna_text': 'UGGAAGGGUAUCUAGACUUGAG',
        'virus_text': 'ATGCGTACGTTAGCTAGCTAGC',
    }})
    assert r.status_code != 400, \
        '合法的 mirna 输入被新校验误伤：%s' % r.get_json()
    tid = (r.get_json() or {}).get('task')
    if tid:
        c.post('/api/task/%s/cancel' % tid)
