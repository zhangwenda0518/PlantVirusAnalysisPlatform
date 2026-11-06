# -*- coding: utf-8 -*-
"""任务系统 / 管线状态机回归（2026-11-06 修复批）。

覆盖四处「状态被判错」的缺陷：

  ① A1 成功后 A3 天际线必崩：stage_clock 的 res['stats'] 是 list，旧实现
     对它调 .get('drt') → AttributeError。断言 stage_coalescent 能读到
     顶层 drt 且不抛异常。
  ② A2 双引擎都失败被上报成「正常的 0 次迁移」：只有至少一个引擎真的跑完、
     计数为 0 才允许 ok=True；两个引擎都异常时必须 ok=False + 真实 error。
  ③ 下载批次排队中被取消，_finalize 仍按 done=0 判 failed，把 cancelled
     覆盖掉（与 paused 早退对称）。
  ④ anchor 的 end_run 只认 status=running：reconcile_interrupted 把记录标
     interrupted 后，end_run 永远关不回（finished/dur_s/outputs 全丢）。

这些用例零外部依赖、不联网；run/ 由 tests/conftest.py 隔离到临时目录。
"""
import os
import shutil
import threading
import uuid

import pytest

from Virus_Platform_Core import anchor
from Virus_Platform_Core.config import RUN_ROOT


def _platform_out_dir():
    """产物目录必须在平台写根内（safe_open 的 in_platform 守卫）。

    conftest 把 RUN_ROOT 隔离到临时目录，所以这里不会污染真实 run/。
    """
    d = os.path.join(RUN_ROOT, '_pytest_task_fixes', uuid.uuid4().hex[:8])
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------- ① A3 / DRT
def test_stage_coalescent_reads_top_level_drt(monkeypatch, tmp_path):
    """stats 是 list 时不能 .get('drt')；DRT 必须从 res 顶层取到。"""
    from Virus_Platform_Core import phylodyn_local as pl

    monkeypatch.setattr(
        pl.tt, 'check_tt_env',
        lambda *a, **k: {'ok': False, 'problems': ['测试环境未装 TreeTime']})
    clock = {'stats': [{'method': 'TreeTime', 'rate': 1.2}],
             'drt': {'passed': False},
             'outputs': {}}
    res = pl.stage_coalescent(clock, {'outputs': {}}, str(tmp_path))
    # 走到 TreeTime 不可用的早退分支：说明 DRT 闸门那两行没有崩
    assert res['ok'] is False
    assert res.get('drt_failed') is True
    assert 'TreeTime' in (res.get('error') or '')


# ------------------------------------------------------- ② A2 双引擎失败判定
def test_stage_phylogeo_both_engines_failed_is_not_zero_migration(
        monkeypatch, tmp_path):
    """ML 与 Fitch 都异常 → ok=False，且 error 里两个原因都在。"""
    from Virus_Platform_Core import phylodyn_local as pl
    from Virus_Platform_Core import phylogeo as pg

    tree = tmp_path / 'timetree.nwk'
    tree.write_text('(A:1,B:2);', encoding='utf-8')
    states = tmp_path / 'states.csv'
    states.write_text('name,region\nA,X\nB,Y\n', encoding='utf-8')

    monkeypatch.setattr(
        pl.tt, 'check_tt_env',
        lambda *a, **k: {'ok': True, 'exe': 'fake-treetime'})
    monkeypatch.setattr(
        pl.tt, 'run_mugration',
        lambda *a, **k: {'ok': False, 'error': 'ml boom', 'warnings': []})
    monkeypatch.setattr(pl.tt, 'parse_annotated_newick', lambda _s: object())

    def _fitch_boom(*_a, **_k):
        raise RuntimeError('fitch boom')

    monkeypatch.setattr(pg, 'fitch_mugration', _fitch_boom)

    out = _platform_out_dir()
    try:
        res = pl.stage_phylogeo({'outputs': {'timetree_nwk': str(tree)}},
                                {'outputs': {'states': str(states)}}, out)
        assert res['ok'] is False
        assert '地理重构全部失败' in (res.get('error') or '')
        assert 'ml boom' in res['error']
        assert 'fitch boom' in res['error']
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_stage_phylogeo_zero_migration_ok_when_fitch_runs(
        monkeypatch, tmp_path):
    """一个引擎跑完、计数为 0 → 仍是「正常 0 次迁移」（ok=True）。"""
    from Virus_Platform_Core import phylodyn_local as pl
    from Virus_Platform_Core import phylogeo as pg

    tree = tmp_path / 'timetree.nwk'
    tree.write_text('(A:1,B:2);', encoding='utf-8')
    states = tmp_path / 'states.csv'
    states.write_text('name,region\nA,X\nB,X\n', encoding='utf-8')

    monkeypatch.setattr(
        pl.tt, 'check_tt_env',
        lambda *a, **k: {'ok': True, 'exe': 'fake-treetime'})
    monkeypatch.setattr(
        pl.tt, 'run_mugration',
        lambda *a, **k: {'ok': False, 'error': 'ml boom', 'warnings': []})
    monkeypatch.setattr(pl.tt, 'parse_annotated_newick', lambda _s: object())
    monkeypatch.setattr(pg, 'fitch_mugration',
                        lambda *_a, **_k: (None, [], 0))
    monkeypatch.setattr(pl.tt, 'node_dates_from_branches',
                        lambda *_a, **_k: ({}, None))
    monkeypatch.setattr(pl.tt, 'iter_pre', lambda _t: iter(()))

    out = _platform_out_dir()
    try:
        res = pl.stage_phylogeo({'outputs': {'timetree_nwk': str(tree)}},
                                {'outputs': {'states': str(states)}}, out)
        assert res['ok'] is True
        assert res.get('zero_migration') is True
        assert res.get('error') is None
    finally:
        shutil.rmtree(out, ignore_errors=True)


# ------------------------------------- ⑥ 下载桥：外部任务暂停可被任务中心看见
def test_external_task_snapshot_exposes_pausable_and_paused():
    """外部任务（下载批次）的 pausable/paused 必须透传到快照，否则卡片上
    永远没有「暂停/继续」按钮（旧 _ext_snapshot 写死 False）。"""
    from Virus_Platform_Core.web.tasks import TaskManager

    state = {'status': 'running'}
    tm = TaskManager()
    tid = tm.attach('下载·测试', lambda: dict(state), pausable=True,
                    on_pause=lambda: True, on_resume=lambda: True)
    snap = tm.snapshot(tid)
    assert snap['pausable'] is True
    assert snap['paused'] is False
    state['status'] = 'paused'
    snap = tm.snapshot(tid)
    assert snap['status'] == 'paused'
    assert snap['paused'] is True
    tm.detach('下载·测试')


# --------------------------------------------------------- ⑤ RTT 日期列优先级
def test_rtt_date_trait_prefers_date_over_header_order():
    """日期列按优先级选，不按表头顺序：year 在前也要先选 date 类。"""
    from Virus_Platform_Core.web.tool_jobs import _pick_rtt_date_trait

    assert _pick_rtt_date_trait(['year', 'date', 'region']) == 'date'
    assert _pick_rtt_date_trait(['decimal_year', '采样日期']) == '采样日期'
    assert _pick_rtt_date_trait(['release_date', 'year']) == 'release_date'
    assert _pick_rtt_date_trait(['region']) == 'year'   # 无日期列 → 旧默认
    assert _pick_rtt_date_trait([]) == 'year'


# ------------------------------------------------- ③ 排队中取消不被 _finalize 覆盖
def test_finalize_keeps_cancelled_queued_batch(tmp_path):
    from Virus_Platform_Core.public_data import DownloadManager

    m = DownloadManager.__new__(DownloadManager)
    m.lock = threading.RLock()
    m.batches = {}
    m.procs = {}
    m._spd = {}
    b = {'id': 'cancelled-q', 'status': 'cancelled', 'dir': str(tmp_path),
         'convert_sra': False, 'done': 0, 'failed': 0,
         'files': [{'acc': 'SRR000001', 'status': 'cancelled',
                    'out': str(tmp_path / 'SRR000001.fastq.gz')}]}
    m.batches['cancelled-q'] = b

    m._finalize('cancelled-q')
    assert b['status'] == 'cancelled'
    assert b['done'] == 0 and b['failed'] == 0


# ------------------------------------------------- ④ anchor：end_run 收尾 interrupted
def _seed_run(tmp_path, sample):
    r1 = tmp_path / f'{sample}_R1.fastq'
    r1.write_text('@r1\nACGT\n+\nIIII\n', encoding='utf-8')
    info = anchor.start_run(sample, str(r1), sample_dir=str(tmp_path))
    assert info and info.get('run_id')
    return info


def test_end_run_closes_interrupted_record(tmp_path):
    """reconcile 已标 interrupted 的记录，end_run 仍要能补齐终态。"""
    sample = 'pytest_anchor_interrupted'
    info = _seed_run(tmp_path, sample)

    def _mark_interrupted(d):
        for r in d.get('runs') or []:
            if r.get('run_id') == info['run_id']:
                r['status'] = 'interrupted'
        return True

    anchor._mut(info['slug'], _mark_interrupted)

    anchor.end_run(sample, 'ok', 1.5, str(tmp_path))
    d = anchor._read_json(anchor._anchor_path(info['slug']))
    run = next(r for r in d['runs'] if r.get('run_id') == info['run_id'])
    assert run['status'] == 'ok'
    assert run['finished']
    assert run['dur_s'] == 1.5


def test_reconcile_skips_live_pid_and_closes_dead_one(tmp_path):
    """pid 存活 → 不标 interrupted；pid 已死 → 可收尾。"""
    import subprocess
    import sys

    sample = 'pytest_anchor_pid_live'
    info = _seed_run(tmp_path, sample)

    # 起一个**别的**常驻进程当「另一个实例」；reconcile 不能动它的运行记录
    child = subprocess.Popen([sys.executable, '-c',
                              'import time; time.sleep(60)'])
    try:
        def _live_pid(d):
            for r in d.get('runs') or []:
                if r.get('run_id') == info['run_id']:
                    r['pid'] = child.pid
            return True

        anchor._mut(info['slug'], _live_pid)

        def _age_ledger(d):
            # 把「安静时长」做成很久以前，确保判定只能靠 pid 活性
            d.setdefault('ledger', {})['updated'] = '2000-01-01T00:00:00'
            return True

        anchor._mut(info['slug'], _age_ledger)
        assert sample not in anchor.reconcile_interrupted(max_quiet_sec=1)
    finally:
        child.terminate()
        try:
            child.wait(timeout=10)
        except Exception:
            child.kill()

    def _dead_pid(d):
        for r in d.get('runs') or []:
            if r.get('run_id') == info['run_id']:
                r['pid'] = 999999          # 几乎不可能存活的 pid
        return True

    anchor._mut(info['slug'], _dead_pid)
    assert sample in anchor.reconcile_interrupted(max_quiet_sec=1)
    d = anchor._read_json(anchor._anchor_path(info['slug']))
    run = next(r for r in d['runs'] if r.get('run_id') == info['run_id'])
    assert run['status'] == 'interrupted'
