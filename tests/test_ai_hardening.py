# -*- coding: utf-8 -*-
"""AI 工作台加固回归测试（2026-11-02 全面审查后补）。

覆盖审查里"0 测试"的几条硬伤，防止再退化：
  1) 执行单事件必须带多步计划字段（否则对话坞永远看不到「第 2/3 步」）；
  2) `_dispatch` 必须把上次运行参数（base_params）作底、显式参数覆盖；
  3) MCP 层必须拦住破坏面 CLI（tool-runs clean / db-migrate apply）；
  4) `_tsv_head` 不能再只给前 8 列（kvsuite 汇总表 22 列，覆盖度在第 9-16 列）；
  5) 工具参数提示以 TOOL_PARAM_SCHEMAS 为主，job 源码要求的键补为必填；
  6) 网关重复拼名（`mcp_callmcp_call`）要归一；
  7) 执行单的守护线程要能把任务失败写回 error 并推 failed。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from Virus_Platform_Core.ai_agent import (    # noqa: E402
    broadcast, digest, executors, provider, tools as ai_tools)


class ActionEventTests(unittest.TestCase):
    """_publish 的字段白名单必须覆盖对话坞需要的步骤字段。"""

    def test_step_fields_published(self):
        rec = {'id': 'act_9', 'action': 'plan', 'title': '多步计划',
               'sample': 'S1', 'status': 'running',
               'steps': [{'action': 'run_tool'}],
               'step': 2, 'step_total': 3, 'step_action': 'run_tool',
               'step_title': '运行工具', 'result_sample': 'S1'}
        got = {}

        def fake_publish(ev, **fields):
            got['ev'] = ev
            got.update(fields)

        with mock.patch.object(broadcast, 'publish', fake_publish):
            executors._publish(rec, 'ai_action')
        self.assertEqual(got.get('aid'), 'act_9')
        self.assertEqual(got.get('step'), 2)
        self.assertEqual(got.get('step_total'), 3)
        self.assertEqual(got.get('step_title'), '运行工具')
        self.assertEqual(got.get('steps'), [{'action': 'run_tool'}])


class DispatchParamsTests(unittest.TestCase):
    """「原参数重试」要真的用上一次的参数，而不是设置页默认值。"""

    def test_base_params_merged_and_overridden(self):
        rec = {'action': 'analysis', 'sample': 'S1',
               'base_params': {'assembly_mode': 'spades', 'threads': 4},
               'params': {'threads': 8}}
        captured = {}

        def fake_launch(sample, stages, body):
            captured['sample'] = sample
            captured['stages'] = stages
            captured['body'] = body
            return 'tid123'

        with mock.patch.object(executors, '_launch_analysis', fake_launch):
            self.assertEqual(executors._dispatch(rec), 'tid123')
        self.assertEqual(captured['body']['assembly_mode'], 'spades')
        self.assertEqual(captured['body']['threads'], 8)


class McpGuardTests(unittest.TestCase):
    """破坏面 CLI 不得由模型不经批准触发（AGENTS.md §5/§7）。"""

    def test_tool_runs_clean_blocked(self):
        r = ai_tools._mcp_denied('vp_cli_submit',
                                 {'command': 'tool-runs',
                                  'args': {'action': 'clean'}, 'confirm': True})
        self.assertTrue(r, 'tool-runs clean 必须被拦')

    def test_db_migrate_apply_blocked(self):
        r = ai_tools._mcp_denied('vp_cli_submit',
                                 {'command': 'db-migrate',
                                  'args': {'action': 'apply'}, 'confirm': True})
        self.assertTrue(r, 'db-migrate apply 必须被拦')

    def test_readonly_cli_allowed(self):
        self.assertFalse(ai_tools._mcp_denied(
            'vp_cli_submit', {'command': 'kv-list', 'args': {}}))
        self.assertFalse(ai_tools._mcp_denied(
            'vp_cli_submit', {'command': 'tool-runs',
                              'args': {'action': 'list'}}))
        self.assertFalse(ai_tools._mcp_denied('vp_list_samples', {}))


class TsvHeadTests(unittest.TestCase):
    """汇总表列数：以前只给 8 列，覆盖度/深度全被丢掉。"""

    def test_all_columns_kept(self):
        cols = [f'c{i}' for i in range(22)]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'x.tsv'
            p.write_text('\t'.join(cols) + '\n'
                         + '\t'.join(str(i) for i in range(22)) + '\n',
                         encoding='utf-8')
            out = digest._tsv_head(p)
        self.assertEqual(len(out['header']), 22)
        self.assertEqual(len(out['rows'][0]), 22)
        self.assertNotIn('dropped_columns', out)

    def test_csv_delimiter_detected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'x.csv'
            p.write_text('a,b,c\n1,2,3\n', encoding='utf-8')
            out = digest._tsv_head(p)
        self.assertEqual(out['header'], ['a', 'b', 'c'])
        self.assertEqual(out['rows'][0], ['1', '2', '3'])

    def test_wide_table_truncated_with_note(self):
        cols = [f'c{i}' for i in range(40)]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'w.tsv'
            p.write_text('\t'.join(cols) + '\n'
                         + '\t'.join('1' for _ in cols) + '\n',
                         encoding='utf-8')
            out = digest._tsv_head(p)
        self.assertEqual(len(out['header']), digest.MAX_COLS)
        self.assertEqual(len(out['dropped_columns']), 40 - digest.MAX_COLS)
        self.assertTrue(out.get('note'))


class ToolParamTests(unittest.TestCase):
    """参数提示：以 schema 为主，不再正则猜出一堆假必填。"""

    @staticmethod
    def _by_key(tool):
        # 真实调用点（_list_platform_tools）会把 TOOL_REGISTRY 里的 job 工厂
        # 一起传进来，job 源码校验而 catalog 漏登记的必填项才补得上。
        try:
            from Virus_Platform_Core.web.tools_api import TOOL_REGISTRY
            job = (TOOL_REGISTRY.get(tool) or {}).get('job')
        except Exception:
            job = None
        rows = ai_tools._tool_params(tool, job) or []
        return {r['key']: r for r in rows}

    def test_kvsuite_params_not_all_required(self):
        rows = self._by_key('kvsuite')
        self.assertIn('reads', rows)
        req = [k for k, v in rows.items() if v.get('required')]
        # 三选一（reads / srr / 单端）不能同时算必填
        self.assertLessEqual(len(req), 2, f'kvsuite 必填过多: {req}')

    def test_pdprep_requires_files(self):
        rows = self._by_key('pdprep')
        req = [k for k, v in rows.items() if v.get('required')]
        self.assertIn('fasta', req)
        self.assertIn('meta', req)

    def test_every_param_has_type(self):
        for tool in ('kvsuite', 'pdprep', 'hostprep'):
            for k, v in self._by_key(tool).items():
                self.assertTrue(v.get('type'), f'{tool}.{k} 缺 type')


class ToolNameJoinTests(unittest.TestCase):
    """网关会把同一函数名重复拼进每个分片（mcp_callmcp_call）。"""

    def test_duplicate_name_collapsed(self):
        self.assertEqual(provider._join_name('mcp_call', 'mcp_call'),
                         'mcp_call')
        self.assertEqual(provider._join_name('mcp_', 'call'), 'mcp_call')
        self.assertEqual(provider._join_name('', 'propose_action'),
                         'propose_action')
        self.assertEqual(provider._join_name('propose_action', ''), 'propose_action')


class WatchTerminalTests(unittest.TestCase):
    """单动作执行单必须被推到真实终态，失败要把任务 error 写回。"""

    def _run(self, rec, snap, ok):
        events = []

        class FakeTm:
            def snapshot(self, rid, log_lines=0, include_result=False):
                return dict(snap)

        fake_tasks = mock.MagicMock()
        fake_tasks.tm = FakeTm()
        with mock.patch.dict(sys.modules,
                            {'Virus_Platform_Core.web.tasks': fake_tasks}), \
                mock.patch.object(executors, '_wait_task',
                                  lambda *a, **k: ok), \
                mock.patch.object(executors, '_snapshot_publish',
                                  lambda r, st, **k: events.append(st)), \
                mock.patch.object(broadcast, 'publish', lambda *a, **k: None):
            executors._watch_to_terminal(rec, 'tid9', timeout=5, interval=0.01)
        return events

    def test_failed_task_marks_action_failed(self):
        rec = {'id': 'act_1', 'action': 'run_tool', 'tool': 'kvsuite',
               'status': 'launched'}
        events = self._run(rec, {'status': 'failed',
                                 'error': 'known_virus_suite 退出码 1'}, False)
        self.assertEqual(events, ['failed'])
        self.assertIn('退出码 1', rec.get('error') or '')

    def test_done_task_marks_action_done(self):
        rec = {'id': 'act_2', 'action': 'run_tool', 'status': 'launched'}
        events = self._run(rec, {'status': 'done'}, True)
        self.assertEqual(events, ['done'])


class ResolveRaceTests(unittest.TestCase):
    """批准/驳回必须一次性生效（以前双击会各起一个执行线程，跑两遍）。"""

    def _draft(self):
        rec, err = executors.create_action(
            'run_tool', '', params={'tool': 'convert', 'input': 'x.fq'},
            reason='并发测试')
        self.assertIsNone(err)
        return rec

    def test_double_approve_second_turned_away(self):
        rec = self._draft()
        with mock.patch.object(executors, '_dispatch',
                               side_effect=RuntimeError('boom')):
            _r1, e1 = executors.resolve(rec['id'], 'approve')
            _r2, e2 = executors.resolve(rec['id'], 'approve')
        self.assertIsNone(e1)
        self.assertIn('已处理', e2 or '')

    def test_reject_is_final(self):
        rec = self._draft()
        r1, e1 = executors.resolve(rec['id'], 'reject')
        self.assertIsNone(e1)
        self.assertEqual(r1.get('status'), 'rejected')
        _r2, e2 = executors.resolve(rec['id'], 'approve')
        self.assertIn('已处理', e2 or '')

    def test_bad_decision_rejected(self):
        rec = self._draft()
        _r, e = executors.resolve(rec['id'], 'maybe')
        self.assertIn('approve', e or '')
        # 状态不能被改动
        self.assertEqual((executors.get_action(rec['id']) or {}).get('status'),
                         'pending')
        executors.resolve(rec['id'], 'reject')


class TaskOriginTests(unittest.TestCase):
    """任务记录上的 origin='ai' 必须落盘、进快照、可随归档恢复。

    背景（2026-11-02）：并发闸原先只认任务名前缀 'AI执行·'，英文界面的
    "AI run·…" 一律漏数；曾用进程内 `_AI_TIDS` 兜底，但重启即失忆。
    现在 origin 写进 tasks/<tid>.json 与 snapshot，跨重启仍在。
    """

    def _rec(self, **kw):
        base = {'id': 'abc123abc123', 'name': 'AI run·report', 'status': 'done',
                'stage': '', 'pct': 1.0, 'msg': '', 'error': '',
                'log': [], 'started': 1.0, 'finished': 2.0, 'result': None,
                'result_preview': None, 'eta': None, 'cancel': None, 'procs': [],
                'weight': 'heavy', 'log_file': None, 'link': None,
                'restart': None, 'out_dir': None, 'origin': 'ai'}
        base.update(kw)
        return base

    def test_rec_json_keeps_origin(self):
        from Virus_Platform_Core.web.tasks import TaskManager
        d = TaskManager._rec_json(self._rec())
        self.assertEqual(d.get('origin'), 'ai')
        self.assertEqual(d.get('name'), 'AI run·report')

    def test_snapshot_exposes_origin(self):
        from Virus_Platform_Core.web.tasks import tm
        tid = 'testorigin01'
        tm.tasks[tid] = self._rec(id=tid, status='running')
        try:
            snap = tm.snapshot(tid)
        finally:
            tm.tasks.pop(tid, None)
        self.assertIsNotNone(snap)
        self.assertEqual(snap.get('origin'), 'ai')

    def test_start_accepts_origin(self):
        """start() 的 origin 形参必须落到记录上（防参数被吞）。"""
        import inspect
        from Virus_Platform_Core.web.tasks import TaskManager
        sig = inspect.signature(TaskManager.start)
        self.assertIn('origin', sig.parameters)

    def test_count_ai_active_uses_origin(self):
        """并发闸以 origin 为准：英文名任务也计入，重启后仍准。"""
        tasks = [{'id': 'a', 'name': 'AI run·report', 'status': 'running',
                  'origin': 'ai'},
                 {'id': 'b', 'name': '人工跑报告', 'status': 'running'}]
        self.assertEqual(executors._count_ai_active(tasks), 1)

    def test_legacy_prefix_still_counted(self):
        """老记录（修复前落盘、没有 origin）仍靠中文前缀兜底。"""
        tasks = [{'id': 'c', 'name': 'AI执行·report', 'status': 'running'},
                 {'id': 'd', 'name': '人工跑报告', 'status': 'running'}]
        self.assertEqual(executors._count_ai_active(tasks), 1)

    def test_queued_snapshot_counts_as_running(self):
        """排队中的任务快照对外呈现 running —— 并发闸因此也算上它。"""
        from Virus_Platform_Core.web.tasks import tm
        tid = 'testorigin02'
        tm.tasks[tid] = self._rec(id=tid, status='queued')
        try:
            snap = tm.snapshot(tid)
        finally:
            tm.tasks.pop(tid, None)
        self.assertEqual((snap or {}).get('status'), 'running')


if __name__ == '__main__':
    unittest.main()
