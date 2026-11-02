# -*- coding: utf-8 -*-
"""AI 执行单的动作注册表与参数校验（不执行任何真实任务）。

覆盖 2026-10-29 新增的两类动作：
  · build_host_db —— 宿主基因组数据库构建（genome + taxid）；
  · plan —— 多步计划（steps 顺序执行，不允许 download_runs 混进来）。
所有用例创建后立即 reject，不会触发真实建库/组装。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from Virus_Platform_Core.ai_agent import executors as ex  # noqa: E402

GENOME = os.path.join(ROOT, 'examples', 'example_acmv.fasta')


def _mk(action, params, sample='', stages=None):
    rec, err = ex.create_action(action, sample, stages, params,
                                'unit test', 'test')
    if rec:
        ex.resolve(rec['id'], 'reject')
    return rec, err


def test_actions_registered():
    assert 'build_host_db' in ex.ACTIONS
    assert 'plan' in ex.ACTIONS
    assert 'download_runs' not in ex.PLAN_ACTIONS


@pytest.mark.skipif(not os.path.isfile(GENOME), reason='缺少示例 FASTA')
def test_build_host_db_ok():
    rec, err = _mk('build_host_db', {'genome': GENOME, 'taxid': 3702})
    assert err is None and rec
    assert rec['params']['genome'] == GENOME
    assert rec['params']['taxid'] == 3702
    assert rec['status'] == 'rejected'


def test_build_host_db_requires_genome():
    rec, err = _mk('build_host_db', {'taxid': 3702})
    assert rec is None and 'genome' in err


def test_build_host_db_requires_numeric_taxid():
    rec, err = _mk('build_host_db', {'genome': GENOME, 'taxid': 'abc'})
    assert rec is None and 'taxid' in err


def test_plan_rejects_empty():
    rec, err = _mk('plan', {'steps': []})
    assert rec is None and 'steps' in err


def test_plan_rejects_download_runs():
    rec, err = _mk('plan', {'steps': [{'action': 'download_runs'}]})
    assert rec is None and 'download_runs' in err


def test_plan_rejects_run_tool_without_tool():
    rec, err = _mk('plan', {'steps': [{'action': 'run_tool', 'params': {}}]})
    assert rec is None and 'tool' in err


def test_plan_accepts_ordered_steps(tmp_path):
    r1 = tmp_path / 'Unit_R1.fastq'
    r1.write_text('@r\nACGT\n+\nIIII\n', encoding='ascii')
    steps = [
        {'action': 'create_sample',
         'params': {'r1': str(r1), 'sample': 'UnitPlan'}},
        {'action': 'run_tool', 'sample': 'UnitPlan',
         'params': {'tool': 'assemble', 'r1': str(r1)}},
        {'action': 'build_host_db',
         'params': {'genome': GENOME, 'taxid': 3702}},
    ]
    rec, err = _mk('plan', {'steps': steps})
    assert err is None and rec
    assert [s['action'] for s in rec['steps']] == [
        'create_sample', 'run_tool', 'build_host_db']
    assert rec['step_total'] == 3
    assert rec['status'] == 'rejected'


def test_plan_step_limit():
    steps = [{'action': 'rerun_stage', 'sample': 'S', 'stages': ['assembly']}] \
        * (ex.PLAN_MAX_STEPS + 1)
    rec, err = _mk('plan', {'steps': steps})
    assert rec is None and '最多' in err


def _plan_rec(steps):
    return {'id': 'act_plan_test', 'action': 'plan', 'status': 'executing',
            'steps': steps, 'step_total': len(steps), 'error': ''}


def _quiet(monkeypatch):
    monkeypatch.setattr(ex, '_publish', lambda rec, event='ai_action': None)
    monkeypatch.setattr(ex, '_audit', lambda rec: None)


def test_plan_runs_steps_in_order(monkeypatch):
    calls = []
    monkeypatch.setattr(ex, '_dispatch',
                        lambda step: calls.append(step['action']) or 'tid-x')
    monkeypatch.setattr(ex, '_wait_task', lambda tid: True)
    _quiet(monkeypatch)
    rec = _plan_rec([{'action': 'create_sample', 'params': {}},
                     {'action': 'run_tool', 'tool': 'assemble',
                      'tool_params': {}},
                     {'action': 'build_host_db', 'params': {}}])
    ex._run_plan(rec)
    assert calls == ['create_sample', 'run_tool', 'build_host_db']
    assert rec['status'] == 'done'
    assert rec['step'] == 3


def test_plan_stops_at_first_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(ex, '_dispatch',
                        lambda step: calls.append(step['action']) or 'tid-x')
    monkeypatch.setattr(ex, '_wait_task', lambda tid: False)
    _quiet(monkeypatch)
    # create_sample 是同步动作（dispatch 即完成），「等待失败→停」的契约
    # 只对异步步骤成立——用 build_host_db（真实 tm 任务）做第一步。
    rec = _plan_rec([{'action': 'build_host_db', 'params': {}},
                     {'action': 'run_tool', 'tool': 'assemble',
                      'tool_params': {}}])
    ex._run_plan(rec)
    assert rec['status'] == 'failed'
    assert calls == ['build_host_db']          # 第二步不得启动
    assert '第 1' in rec['error']


def test_plan_sync_create_sample_skips_wait(monkeypatch):
    """create_sample 同步步骤：不得把样品名当任务 id 去轮询（曾卡 6 小时）。"""
    waited = []

    def fake_dispatch(step):
        if step['action'] == 'create_sample':
            return 'UnitPlan'                # 样品名，非任务 id
        return 'tid-real-1'

    monkeypatch.setattr(ex, '_dispatch', fake_dispatch)
    monkeypatch.setattr(ex, '_wait_task',
                        lambda tid: waited.append(tid) or True)
    _quiet(monkeypatch)
    rec = _plan_rec([{'action': 'create_sample', 'params': {}},
                     {'action': 'run_tool', 'tool': 'assemble',
                      'tool_params': {}}])
    ex._run_plan(rec)
    assert waited == ['tid-real-1']          # 等的是真任务，不是样品名
    assert rec['status'] == 'done'
    assert rec['step'] == 2


def test_execute_create_sample_marks_done(monkeypatch, tmp_path):
    """独立 create_sample 执行单：同步完成后直接 done，不停在 launched。

    2026-11-02 契约变更：create_sample 的返回值是**样品名**，不再写进
    rec['tid']（AI 拿样品名去 wait_task 会轮空到超时），改存 result_sample。
    这里锁两件事：状态必须 done、样品名必须落进 result_sample。
    """
    monkeypatch.setattr(ex, '_dispatch', lambda rec: 'UnitPlan')
    monkeypatch.setattr(ex, '_snapshot_publish',
                        lambda rec, status: rec.__setitem__('status', status))
    rec = {'id': 'act_cs', 'action': 'create_sample', 'status': 'executing',
           'params': {}, 'error': ''}
    ex._execute(rec)
    assert rec['status'] == 'done'
    assert rec['result_sample'] == 'UnitPlan'
    assert not rec.get('tid')             # 样品名不写进任务 id 字段
    assert not rec.get('result_task')     # 也绝不能当任务 id 去 wait_task


def test_snapshot_keeps_result_sample():
    """_snapshot 必须带回 result_sample，否则刷新页面后卡片丢掉样品名。"""
    snap = ex._snapshot({'id': 'a1', 'action': 'create_sample',
                         'status': 'done', 'result_sample': 'S1'})
    assert snap['result_sample'] == 'S1'


def test_plan_dispatch_error_marks_failed(monkeypatch):
    def boom(step):
        raise RuntimeError('缺参数')
    monkeypatch.setattr(ex, '_dispatch', boom)
    _quiet(monkeypatch)
    rec = _plan_rec([{'action': 'run_tool', 'tool': 'assemble',
                      'tool_params': {}}])
    ex._run_plan(rec)
    assert rec['status'] == 'failed'
    assert '启动失败' in rec['error']

