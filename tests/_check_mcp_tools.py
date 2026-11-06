# -*- coding: utf-8 -*-
"""MCP 工具面自检（进程内，不拉起 stdio/http 服务，不动真实数据）。

背景（2026-11-02，用户要求「mcp 也全面增强」）：
  · MCP 是外部 agent 的唯一稳定入口，但此前只有 24 个工具：任务不能手动
    归档/恢复、归档任务读不到、长任务只能自己轮询、样品阶段状态与样品
    产物都拿不到、没有自检、清单不能过滤。
  · 这次补齐 8 个工具（vp_wait_task / vp_sample_stages / vp_read_sample_file
    / vp_archive_task / vp_unarchive_task / vp_archive_tool_run /
    vp_restore_tool_run / vp_selfcheck）并给清单加过滤参数。
  · 本守卫只做**进程内**检查：工具确实注册、参数确实生效、归档往返真的
    搬文件、越界读取真的被拒。只在本进程内改 DIRS['tasks'] 与
    tool_runs_root()，不碰真实 run/tasks 与 run/tool_runs。

用法：python tests/_check_mcp_tools.py
"""
import asyncio
import os
import shutil
import sys
import tempfile
import threading
import time
from collections import deque

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('VP_NO_RECOVER', '1')

FAILS = []


def check(ok, msg):
    print(('  ✔ ' if ok else '  ✘ ') + msg)
    if not ok:
        FAILS.append(msg)


NEW_TOOLS = ('vp_wait_task', 'vp_sample_stages', 'vp_read_sample_file',
             'vp_archive_task', 'vp_unarchive_task', 'vp_archive_tool_run',
             'vp_restore_tool_run', 'vp_selfcheck')


def main():
    import mcp_server as M
    from Virus_Platform_Core.web import tasks as T

    # ---- 1. 工具注册面 ----
    try:
        tools = asyncio.run(M.mcp.list_tools())
    except Exception:                            # noqa: BLE001
        tools = asyncio.run(asyncio.wait_for(M.mcp.list_tools(), 10))
    names = {t.name for t in tools}
    check(len(names) >= 32, f'工具数 {len(names)} ≥ 32（新增 8 个）')
    missing = [n for n in NEW_TOOLS if n not in names]
    check(not missing, '新工具全部注册' + (f'（缺 {missing}）' if missing else ''))
    for n in ('vp_get_task', 'vp_run_tool', 'vp_pipeline_start',
              'vp_read_run_file', 'vp_list_tasks'):
        check(n in names, f'既有工具仍在: {n}')

    # 工具描述不能空（schema 就是给模型看的说明书）
    no_doc = [t.name for t in tools if not (t.description or '').strip()]
    check(not no_doc, '所有工具都有 description' +
          (f'（空 {no_doc}）' if no_doc else ''))

    # ---- 2. 清单过滤 ----
    d = M._tool_list_impl(query='引物')
    check(d['count'] >= 1 and d['total'] >= d['count'],
          f"关键词过滤命中 {d['count']}/${d['total']}")
    light = M._tool_list_impl(weight='light')
    check(all(t['weight'] == 'light' for t in light['tools']),
          'weight=light 过滤只回 light 档')
    try:
        M._tool_list_impl(weight='bogus')
        check(False, 'weight 非法值应报错')
    except ValueError as e:
        check('heavy 或 light' in str(e), f'weight 非法值报错文案: {e}')
    paged = M._tool_list_impl(limit=3, offset=2)
    check(paged['count'] == 3, 'limit/offset 分页生效')

    # ---- 3. 任务清单过滤与快照口径 ----
    try:
        M._task_list(10, False, status='bogus')
        check(False, 'status 非法值应报错')
    except ValueError as e:
        check('queued/running/done/failed/cancelled' in str(e),
              f'status 非法值报错文案: {e}')
    try:
        M._task_snapshot('ffffffffffff', log_lines=0)
        check(False, '未知任务应报错')
    except ValueError as e:
        check('vp_list_tasks' in str(e), f'未知任务提示查列表: {e}')

    # ---- 4. 任务归档/恢复（本进程内临时目录）----
    old_tm = M.tm
    old_tasks_dir = T.DIRS['tasks']
    # 任务归档走 safe_open（只允许平台内路径），临时目录必须落在平台里，
    # 否则 Windows 的 TMP 若指向 C:\Users\...\Temp 会被判「路径越界」。
    tmp_base = os.path.join(ROOT, 'run', 'tmp')
    os.makedirs(tmp_base, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='vp_mcp_task_', dir=tmp_base)
    try:
        T.DIRS['tasks'] = tmp
        m = T.TaskManager()
        rec = {'id': 'beef00000001', 'name': 'MCP 自检任务', 'status': 'done',
               'stage': '结束', 'pct': 1.0, 'msg': '完成', 'error': '',
               'log': deque(['a', 'b'], maxlen=500),
               'started': time.time() - 20, 'finished': time.time() - 2,
               'cancel': threading.Event(), 'procs': [], 'weight': 'light',
               'log_file': None, 'link': None, 'out_dir': None,
               'restart': None, 'result': {'n': 1}}
        m.tasks[rec['id']] = rec
        m.order.insert(0, rec['id'])
        m._persist(rec)
        M.tm = m

        snap = M._task_snapshot(rec['id'], log_lines=5)
        check(snap['status'] == 'done' and snap['archived'] is False,
              '运行列表快照: done / archived=false')
        waited = M._wait_task_impl(rec['id'], timeout_sec=5, poll_sec=1,
                                   log_lines=3)
        check(waited['timed_out'] is False and waited['status'] == 'done',
              'vp_wait_task 终态立即返回')

        a = M._archive_task_impl(rec['id'])
        check(a['status'] == 'ok' and a['result']['archived'] is True,
              'vp_archive_task 归档成功')
        check(rec['id'] not in m.tasks, '归档后移出内存')
        snap = M._task_snapshot(rec['id'], log_lines=3)
        check(snap['archived'] is True, '归档任务仍可 vp_get_task 读到')
        lst = M._task_list(10, include_archived=True)
        check(any(t.get('id') == rec['id'] for t in lst.get('archived') or []),
              'include_archived 能看到归档任务')

        u = M._unarchive_task_impl(rec['id'])
        check(u['status'] == 'ok' and u['result']['archived'] is False,
              'vp_unarchive_task 恢复成功')
        check(rec['id'] in m.tasks, '恢复后回到内存')

        try:
            M._unarchive_task_impl(rec['id'])
            check(False, '重复恢复应报错')
        except ValueError as e:
            check('归档记录不存在' in str(e), f'重复恢复报错: {e}')
    finally:
        M.tm = old_tm
        T.DIRS['tasks'] = old_tasks_dir
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- 5. 工具箱运行归档/恢复（临时 tool_runs 根）----
    old_root = M.tool_runs_root
    tmp2 = tempfile.mkdtemp(prefix='vp_mcp_run_', dir=tmp_base)
    try:
        M.tool_runs_root = lambda: tmp2
        run = 'kvsuite_20260101_000000_abcd'
        os.makedirs(os.path.join(tmp2, run, 'sub'), exist_ok=True)
        with open(os.path.join(tmp2, run, 'sub', 'x.tsv'), 'w',
                  encoding='utf-8') as f:
            f.write('a\tb\n1\t2\n')

        brief = M._run_list_impl(10, 0)
        check(brief['count'] == 1 and brief['runs'][0]['run'] == run,
              '活区运行列表可读')

        a = M._archive_tool_run_impl(run)
        check(a['status'] == 'ok', 'vp_archive_tool_run 归档成功')
        check(not os.path.isdir(os.path.join(tmp2, run)), '活区已移走')
        check(os.path.isdir(os.path.join(tmp2, '_archive', run)), '归档区已就位')
        arch = M._run_list_impl(10, 0, include_archived=True)
        check(arch['archived_total'] == 1 and
              arch['archived'][0]['archived'] is True, '归档清单可见')

        got = M._read_run_file_impl(run, 'sub/x.tsv', 64, archived=True)
        check(got['result']['archived'] is True and
              'a\tb' in got['result']['text'], '归档运行产物仍可读')

        r = M._restore_tool_run_impl(run)
        check(r['status'] == 'ok', 'vp_restore_tool_run 恢复成功')
        check(os.path.isdir(os.path.join(tmp2, run)), '恢复后回到活区')
        try:
            M._restore_tool_run_impl(run)
            check(False, '重复恢复应报错')
        except ValueError as e:
            check('不存在' in str(e), f'重复恢复报错: {e}')
    finally:
        M.tool_runs_root = old_root
        shutil.rmtree(tmp2, ignore_errors=True)

    # ---- 6. 样品阶段与样品产物（真实样品，只读）----
    results = T.DIRS['results']
    samples = [n for n in os.listdir(results)
               if os.path.isdir(os.path.join(results, n))] if os.path.isdir(
                   results) else []
    if samples:
        name = 'cli_fulltest' if 'cli_fulltest' in samples else samples[0]
        st = M._sample_stages_impl(name)
        rows = st['result']['stages']
        check(len(rows) == 15, f'vp_sample_stages 回 15 个阶段（{name}）')
        check(sum(st['result']['counts'].values()) == len(rows),
              'counts 与阶段数一致')
        check(all(r['status'] in ('done', 'skipped', 'failed', 'ready',
                                  'blocked', 'unavailable') for r in rows),
              '阶段状态取值合法')
        f = os.path.join('02b_kvsuite', 'identify', 'all_viruses.summary.tsv')
        if os.path.isfile(os.path.join(results, name, f)):
            got = M._read_sample_file_impl(name, f, 64)
            check(got['status'] == 'ok' and 'text' in got['result'],
                  'vp_read_sample_file 读到真实产物')
        try:
            M._read_sample_file_impl(name, '../../secrets.txt', 8)
            check(False, '越界路径应被拒')
        except ValueError as e:
            check('相对路径' in str(e), f'越界路径被拒: {e}')
        try:
            M._read_sample_file_impl('no_such_sample_xyz', 'a.txt', 8)
            check(False, '不存在的样品应报错')
        except ValueError as e:
            check('样品不存在' in str(e), f'不存在样品报错: {e}')
    else:
        print('  - 跳过样品检查（run/results 为空）')

    # ---- 6b. 结论文档 vp_build_report（临时目录，不碰真实样品与产物）----
    tmp3 = tempfile.mkdtemp(prefix='vp_mcp_rep_', dir=tmp_base)
    try:
        # 手工摆一个「最小 full 档案」目录：只有 ②b 与 ③ 的 summary.json，
        # 缺其余阶段——正好验证报告在字段大面积缺失时不崩、按已有阶段裁章节。
        import json as _json
        os.makedirs(os.path.join(tmp3, '02b_kvsuite'), exist_ok=True)
        os.makedirs(os.path.join(tmp3, '03_assembly'), exist_ok=True)
        with open(os.path.join(tmp3, '02b_kvsuite', 'summary.json'), 'w',
                  encoding='utf-8') as f:
            _json.dump({'stage': 'kvsuite', 'engine': 'salmon',
                        'viral_reads': 1200, 'passed': 2, 'rejected': 1,
                        'species_detected': ['A virus', 'B viroid'],
                        'species_with_reads': ['A virus', 'B viroid', 'C']},
                       f, ensure_ascii=False)
        with open(os.path.join(tmp3, '03_assembly', 'summary.json'), 'w',
                  encoding='utf-8') as f:
            _json.dump({'stage': 'assembly', 'contigs': 7, 'total_bp': 9999,
                        'viral_contigs': ['NODE_1_length_5000_cov_9.0']},
                       f, ensure_ascii=False)

        r = M._build_report_impl(tmp3, digest_kb=16)
        ok = r.get('status') == 'ok'
        check(ok, f"vp_build_report 在信息不全的目录上也能出报告（{r.get('summary')}）")
        if ok:
            res = r['result']
            check(res['profile'] == 'full',
                  f"档案按已有阶段判为 full（实际 {res['profile']}）")
            check(os.path.isfile(res['html']) and res['size'] > 2000,
                  f"落盘且体积合理（{res['size']} 字节）")
            check((res.get('digest') or '').strip() != '', 'digest 返回纯文本正文')
            check(os.path.isfile(os.path.join(
                os.path.dirname(res['html']), 'report_v2_meta.json')),
                'meta.json 与 html 同目录一并写出')
            # 复用与强重建
            r2 = M._build_report_impl(tmp3, digest_kb=0)
            check(r2['result']['reused'] is True, '输入未变时复用（reused=True）')
            r3 = M._build_report_impl(tmp3, force=True, digest_kb=0)
            check(r3['result']['reused'] is False, 'force=True 强制重建')

        # 错误以 status=error 形式返回（不抛异常，符合 MCP 信封口径）
        e1 = M._build_report_impl('no_such_sample_xyz', digest_kb=0)
        check(e1.get('status') == 'error' and '不存在' in e1.get('summary', ''),
              f"不存在目标回 status=error: {e1.get('summary')}")
        e2 = M._build_report_impl(r'C:\Windows', digest_kb=0)
        check(e2.get('status') == 'error' and '平台根内' in e2.get('summary', ''),
              f"出界目录被拒: {e2.get('summary')}")
        e3 = M._build_report_impl(tmp3, kind='bogus', digest_kb=0)
        check(e3.get('status') == 'error', 'kind 非法回 status=error')
    finally:
        shutil.rmtree(tmp3, ignore_errors=True)

    # ---- 7. 自检报告 ----
    rep = M._selfcheck_report()
    check(isinstance(rep.get('checks'), list) and rep['checks'],
          f"vp_selfcheck 报告 {len(rep.get('checks') or [])} 项")
    check(rep['ok'] is True, '当前环境自检通过（problems 为空）')

    print()
    if FAILS:
        print(f'✘ MCP 工具面自检未通过：{len(FAILS)} 项')
        for f in FAILS:
            print('   - ' + f)
        return 1
    print('✔ MCP 工具面自检通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
