# -*- coding: utf-8 -*-
"""pytest 共享引导：把平台根加进 sys.path，并把运行期根目录 run/ 隔离到临时目录。

为什么要有隔离（2026-11-02 实测教训）
--------------------------------------
pytest 里的用例大多通过 `app.test_client()` 进程内跑平台，而平台所有运行期
数据都写在 `DIRS[...]`（默认 `<平台根>/run/`）里。于是「跑一次单测」就等于
「往真实 run/ 里写一批样品/任务/工具运行目录」。实测一天下来 run/tool_runs
积累 331 个一次性目录（convert_/fastp_/primer_/pdprep_…），与真实产物混在
一起，事后无法分辨哪个是测试留下的。

带来的后果不只是脏：
  ① 两个套件并发跑时互相删对方产物 → 「单跑全过、批量随机红」的假红；
  ② 某个用例留下的样品/集合被下一个用例「借」走 → 换个顺序就变红；
  ③ 真实数据被测试清场逻辑误删的风险。

做法：在**任何** `Virus_Platform_Core` / `app` 导入之前，把 `VP_RUN_ROOT`
指向系统临时目录下的独立根（config._run_root() 在导入期读取它，定死 RUN_ROOT）。
退出时对比真实 run/ 的前后清单——真被写了就大声报出来（见 _isolation_report）。

想对真实 run/ 跑（例如验证线上数据）：设 VP_KEEP_RUN_ROOT=1，
或自己先设好 VP_RUN_ROOT 指向别处（tests/_run_all.py 就是这么给子进程分配的）。
"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 运行期子目录名（与 config.DIRS 的 run/ 系列同一套口径；此处只建空目录，
# 让用例不必自己 makedirs —— 很多用例直接 open(os.path.join(DIRS['logs'], ...))）
_RUN_SUBDIRS = (
    'results', 'tool_runs', 'downloads', 'logan', 'submissions', 'meta_search',
    'tasks', 'logs', 'ai_audit', 'uploads', 'fastq', 'gb_collections',
    'ncbi_refs',
)
_TOOL_RUNS_FIXED = ('_archive', '_tmp', '_scripts', '_reports')


def _keep_real_run_root():
    return (os.environ.get('VP_KEEP_RUN_ROOT') or '').strip() not in ('', '0')


def _isolate_run_root():
    """把 VP_RUN_ROOT 指到临时隔离根；返回隔离根路径（未隔离时返回 None）。"""
    if _keep_real_run_root():
        return None
    cur = (os.environ.get('VP_RUN_ROOT') or '').strip()
    if cur:
        # 外层（tests/_run_all.py）已经分配了隔离根，沿用，不另建
        root = cur
    else:
        root = os.path.join(
            tempfile.gettempdir(),
            'vp_pytest_run_%d_%d' % (os.getpid(), int(time.time())))
        os.environ['VP_RUN_ROOT'] = root
    for name in _RUN_SUBDIRS:
        d = os.path.join(root, name)
        os.makedirs(d, exist_ok=True)
        if name == 'tool_runs':
            for sub in _TOOL_RUNS_FIXED:
                os.makedirs(os.path.join(d, sub), exist_ok=True)
    return root


ISOLATED_RUN_ROOT = _isolate_run_root()


def _real_run_snapshot():
    """真实 run/ 下 results 与 tool_runs 的条目名集合（用于前后对账）。"""
    real = os.path.join(ROOT, 'run')
    snap = {}
    for name in ('results', 'tool_runs'):
        d = os.path.join(real, name)
        try:
            snap[name] = set(os.listdir(d))
        except OSError:
            snap[name] = set()
    return snap


_REAL_BEFORE = _real_run_snapshot() if ISOLATED_RUN_ROOT else {}


def pytest_sessionfinish(session, exitstatus):
    """收尾对账：隔离开着却仍写了真实 run/ 的用例，单独点名。

    不直接判红（有些用例确实要读真实数据，见 VP_KEEP_RUN_ROOT 说明），
    但必须留下证据——否则"测试脏了真实目录"又会变成下一轮排查的迷雾。
    """
    if not ISOLATED_RUN_ROOT or not _REAL_BEFORE:
        return
    after = _real_run_snapshot()
    lines = ['隔离根: %s' % ISOLATED_RUN_ROOT, '真实 run/: %s' % os.path.join(ROOT, 'run')]
    dirty = False
    for name in ('results', 'tool_runs'):
        added = sorted(after[name] - _REAL_BEFORE.get(name, set()))
        if added:
            dirty = True
            lines.append('  [新增 %d] run/%s: %s' % (len(added), name,
                                                    ', '.join(added[:20])))
    if not dirty:
        lines.append('  真实 run/ 未被写入（隔离有效）')
    report = os.path.join(ROOT, 'tests', '_isolation_report.txt')
    try:
        with open(report, 'w', encoding='utf-8', newline='\n') as f:
            f.write('\n'.join(lines) + '\n')
    except OSError:
        pass
    if dirty:
        sys.stderr.write('[隔离告警] 有用例写进了真实 run/，见 %s\n' % report)
