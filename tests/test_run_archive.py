# -*- coding: utf-8 -*-
"""工具运行列表 / 归档接口的单测（不碰真实 run/tool_runs）。

覆盖两件事：

1. `/api/tool/runs` 的 `per=N` —— 这是「刷新后结果被收走」的根因回归：
   默认接口只给最近 30 条，align / hostremoval 这类老模块的最近一次运行
   早就被挤出 30 名，前端恢复时查不到 → 页面空着。`per=1` 必须每个模块
   各给最近一次。
2. 归档 / 恢复 / 永久删除：归档只移目录、可恢复；批量按「每模块保留
   keep 次」。

全部在平台内的临时目录里跑（check_path(in_platform=True) 要求），用
monkeypatch 把 tool_runs 根指过去，结束后整棵删掉。
"""
import os
import shutil
import time
import uuid

import pytest

from Virus_Platform_Core.config import PLATFORM_ROOT

RUNDIR = os.path.join(PLATFORM_ROOT, 'run', 'tool_runs')


@pytest.fixture()
def sandbox(monkeypatch):
    from Virus_Platform_Core.web import tools_api

    root = os.path.join(RUNDIR, '_zz_arch_test_' + uuid.uuid4().hex[:8])
    os.makedirs(root)
    monkeypatch.setattr(tools_api, '_tool_runs_root', lambda: root)
    monkeypatch.setattr(tools_api, '_run_real_dir',
                        lambda name: os.path.join(root, name))
    monkeypatch.setattr(tools_api, '_RUNS_CACHE', {})
    try:
        yield root, tools_api
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _mk_run(root, name, size=64, mtime=None):
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'out.txt'), 'w', encoding='utf-8') as fh:
        fh.write('x' * size)
    if mtime:
        os.utime(d, (mtime, mtime))
    return d


def _client():
    from app import app
    return app.test_client()


def test_per_prefix_returns_latest_of_old_modules(sandbox):
    """per=1 必须把老模块的最近一次带回来（默认 30 条会漏）。"""
    root, _ = sandbox
    now = time.time()
    # 40 个「新」模块运行，把列表头部占满
    for i in range(40):
        _mk_run(root, 'convert_20261028_%06d_aaaaaa' % (100000 + i), mtime=now - i)
    # 一个「老」模块运行，mtime 最旧 → 默认列表必然挤不进去
    _mk_run(root, 'align_20260901_010101_bbbbbb', mtime=now - 100000)

    c = _client()
    flat = c.get('/api/tool/runs').get_json()
    assert len(flat) == 30
    assert not any(r['name'].startswith('align_') for r in flat), \
        '默认列表本就不该包含它（这正是 bug 的成因）'

    grouped = c.get('/api/tool/runs?per=1').get_json()
    names = [r['name'] for r in grouped]
    assert 'align_20260901_010101_bbbbbb' in names
    prefixes = [r.get('prefix') for r in grouped]
    assert 'align' in prefixes and 'convert' in prefixes
    # 每个前缀只出现一次
    assert len(prefixes) == len(set(prefixes))

    # limit / prefix 过滤
    only = c.get('/api/tool/runs?per=1&prefix=align').get_json()
    assert [r['name'] for r in only] == ['align_20260901_010101_bbbbbb']


def test_with_totals_true_count_per_prefix(sandbox):
    """with_totals=1：runs 仍按 per 封顶，totals 给全盘真实条数；
    缺省（不带该参数）保持旧形态（纯数组），旧调用方零感知。"""
    root, _ = sandbox
    now = time.time()
    for i in range(10):
        _mk_run(root, 'convert_20261028_%06d_eeeeee' % (200000 + i),
                mtime=now - i)
    _mk_run(root, 'align_20260901_010101_ffffff', mtime=now - 100000)

    c = _client()
    # 缺省 = 旧形态纯数组（缓存键含 with_totals，两形态互不串）
    flat = c.get('/api/tool/runs').get_json()
    assert isinstance(flat, list)

    # with_totals：runs 按每前缀 per=8 封顶（convert 8 + align 1 = 9 条），
    # totals 给全盘真数
    body = c.get('/api/tool/runs?per=8&with_totals=1').get_json()
    assert isinstance(body, dict)
    n_convert = sum(1 for r in body['runs'] if r['name'].startswith('convert_'))
    assert n_convert == 8, 'convert 仍按 per=8 封顶'
    assert len(body['runs']) == 9
    assert body['totals']['convert'] == 10
    assert body['totals']['align'] == 1
    assert body['total'] == 11

    # prefix 过滤不影响 totals（totals 永远是全盘口径）
    body2 = c.get('/api/tool/runs?per=8&with_totals=1&prefix=align').get_json()
    assert [r['name'] for r in body2['runs']] == ['align_20260901_010101_ffffff']
    assert body2['totals']['convert'] == 10 and body2['totals']['align'] == 1


def test_archive_restore_purge(sandbox):
    root, _ = sandbox
    name = 'fastp_20261028_120000_cccccc'
    d = _mk_run(root, name, size=128)
    c = _client()

    r = c.post('/api/tool/runs/%s/archive' % name)
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body['ok'] is True and body['external_output'] is False
    assert not os.path.isdir(d), '登记目录应已移走'
    assert os.path.isdir(os.path.join(root, '_archive', name))

    assert not any(x['name'] == name
                   for x in c.get('/api/tool/runs?per=8').get_json())
    arch = c.get('/api/tool/runs/archived').get_json()
    hit = [x for x in arch if x['name'] == name]
    assert hit and hit[0]['bytes'] >= 128 and hit[0]['files'] == 1

    # 重复归档 → 409
    assert c.post('/api/tool/runs/%s/archive' % name).status_code == 409

    r = c.post('/api/tool/runs/%s/unarchive' % name)
    assert r.status_code == 200
    assert os.path.isdir(d)
    assert not any(x['name'] == name
                   for x in c.get('/api/tool/runs/archived').get_json())

    # 再归档后永久删除
    assert c.post('/api/tool/runs/%s/archive' % name).status_code == 200
    r = c.post('/api/tool/runs/archived/purge',
               json={'names': [name], 'confirm': 'purge'})
    assert r.status_code == 200
    assert name in r.get_json()['removed']
    assert not os.path.isdir(os.path.join(root, '_archive', name))


def test_bulk_archive_keeps_latest_per_module(sandbox):
    root, _ = sandbox
    now = time.time()
    names = []
    for i in range(4):
        n = 'fastp_2026102%d_12000%d_dddddd' % (i, i)
        _mk_run(root, n, mtime=now - i * 10)     # i=0 最新
        names.append(n)
    _mk_run(root, 'align_20261028_120000_eeeeee', mtime=now - 1)

    c = _client()
    r = c.post('/api/tool/runs/archive',
               json={'keep': 1, 'prefixes': ['fastp'], 'confirm': 'archive'})
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    assert d['kept'] == 1
    assert sorted(d['moved']) == sorted(names[1:])
    assert os.path.isdir(os.path.join(root, names[0]))
    # 未指定 prefixes 的模块不受影响
    assert os.path.isdir(os.path.join(root, 'align_20261028_120000_eeeeee'))

    # keep=0 → 整个模块归档
    r = c.post('/api/tool/runs/archive',
               json={'keep': 0, 'prefixes': ['fastp'], 'confirm': 'archive'})
    assert r.status_code == 200
    assert names[0] in r.get_json()['moved']


def test_bulk_archive_requires_confirm_token(sandbox):
    """回归 2026-11-02：空 body 的 POST 曾直接把整个 run/tool_runs 归档并永久删除。

    令牌只拦"没打算做这件事的调用方"（巡检脚本的空 POST、误点的 curl），
    不是鉴权；因此 keep/names 缺省时也必须先过令牌这一关。
    """
    root, _ = sandbox
    now = time.time()
    keep_me = 'fastp_20261028_120000_hhhhhh'
    _mk_run(root, keep_me, mtime=now)
    for i in range(3):
        _mk_run(root, 'fastp_2026102%d_12000%d_iiiiii' % (i, i), mtime=now - i - 1)

    c = _client()
    # 四种"忘了带令牌"的写法都必须被挡在动数据之前
    for body in ({}, {'keep': 1}, {'names': ['x']}, {'confirm': 'wrong'}):
        r = c.post('/api/tool/runs/archive', json=body)
        assert r.status_code == 400, (body, r.status_code)
        assert 'confirm' in r.get_data(as_text=True)
    assert os.path.isdir(os.path.join(root, keep_me))

    r = c.post('/api/tool/runs/archived/purge', json={})
    assert r.status_code == 400
    r = c.post('/api/tool/runs/archived/purge', json={'names': ['x']})
    assert r.status_code == 400
    r = c.post('/api/tool/runs/archived/purge', json={'names': ['x'], 'confirm': 'archive'})
    assert r.status_code == 400

    # 带上正确令牌才生效
    r = c.post('/api/tool/runs/archive', json={'keep': 0, 'prefixes': ['fastp'],
                                               'confirm': 'archive'})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert keep_me in r.get_json()['moved']


def test_archive_rejects_bad_names_and_missing(sandbox):
    c = _client()
    for bad in ('..', '../x', '_hidden', 'a b'):
        assert c.post('/api/tool/runs/%s/archive' % bad).status_code in (400, 404)
    assert c.post('/api/tool/runs/nosuch_20261028_000000_ffffff/archive').status_code in (400, 404)


def test_archive_refuses_running_task(sandbox):
    root, tools_api = sandbox
    name = 'contigs_20261028_130000_gggggg'
    d = _mk_run(root, name)
    tm = tools_api.tm
    tid = 'zz-test-' + uuid.uuid4().hex[:6]
    with tm.lock:
        tm.tasks[tid] = {'id': tid, 'name': '假任务', 'status': 'running',
                         'out_dir': d}
    try:
        r = _client().post('/api/tool/runs/%s/archive' % name)
        assert r.status_code == 409
        assert '正在跑' in r.get_data(as_text=True)
        assert os.path.isdir(d)
    finally:
        with tm.lock:
            tm.tasks.pop(tid, None)
