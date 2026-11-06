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
import os
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
        """pdprep 已合并四种来源，fasta 只在 source=files 时必填。

        2026-11-02 起提交时按 source 校验（tool_jobs._tool_job_pdprep 顶部）：
          files   → fasta + meta 两个服务器路径；
          manual  → fasta_text + meta_text（默认来源，不落文件）；
          genbank → accessions / term / species / taxid / gb_files 至少一个。
        因此参数面不能再把 fasta/meta 标成全局必填（否则 manual/genbank
        的合法提交会被模型判成缺参），但替代必填约束必须写在参数说明里，
        并且运行时空提交要当场 400，不能建一个必然失败的任务。
        """
        rows = self._by_key('pdprep')
        req = [k for k, v in rows.items() if v.get('required')]
        self.assertNotIn('fasta', req)
        self.assertNotIn('meta', req)
        # 替代输入面：manual 粘贴两份文本
        for k in ('fasta_text', 'meta_text'):
            self.assertIn(k, rows)
            self.assertIn('manual', rows[k]['desc'])
        # genbank 来源的检索键至少要有（缺一即视为来源声明丢失）
        self.assertTrue(
            {'accessions', 'term', 'species', 'taxid', 'gb_files'} & set(rows),
            'genbank 来源的检索键缺失')
        # fasta/meta 的必填条件必须钉死在说明里，别让参数空转
        self.assertIn('source=files', rows['fasta']['desc'])
        self.assertIn('source=files', rows['meta']['desc'])
        self.assertIn('files|manual|genbank', rows['source']['desc'])
        # 运行时兜底：默认 manual 缺粘贴文本、files 缺路径都必须 abort 400
        from types import SimpleNamespace
        from werkzeug.exceptions import HTTPException
        from Virus_Platform_Core.web import tool_jobs
        for params in ({}, {'source': 'files'}):
            with self.assertRaises(HTTPException) as cm:
                tool_jobs._tool_job_pdprep(SimpleNamespace(p=params))
            self.assertEqual(cm.exception.code, 400, params)

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


class QaToolBoundaryTests(unittest.TestCase):
    """2026-11-06 P0：qa 模式必须真的不发 tools，且 dispatch 有兜底拦截。

    缺陷证据：`_plan_tools(False)` 实测仍返回 17 个工具（含 propose_action、
    mcp_call），use_tools=False 也不拦 ai_tools.dispatch。
    """

    def test_qa_request_has_no_tools_and_blocks_dispatch(self):
        from Virus_Platform_Core.ai_agent import chat, tools as ai_tools
        seen = {}
        dispatched = []

        def fake_stream(ai_cfg, convo, tools=None, max_tokens=None, **kw):
            seen.setdefault('tools_seen', []).append(tools)
            if not seen.get('tool_emitted'):
                seen['tool_emitted'] = True
                yield {'type': 'tool_calls', 'tool_calls': [
                    {'id': 'tc-qa', 'type': 'function',
                     'function': {'name': 'read_result_table',
                                  'arguments': '{}'}}]}
                return
            yield {'type': 'delta', 'content': '回答'}
            yield {'type': 'finish', 'reason': 'stop'}

        with mock.patch.object(chat, 'chat_stream', fake_stream), \
                mock.patch.object(chat, '_qa_system_prompt',
                                  lambda question='': 'qa stub'), \
                mock.patch.object(ai_tools, 'dispatch',
                                  lambda n, a: dispatched.append(n) or '{}'):
            evs = list(chat.run_chat({'model': 'stub', 'max_tokens': 32},
                                     [{'role': 'user', 'content': '你好'}],
                                     mode='qa'))
        self.assertTrue(seen.get('tools_seen'))
        self.assertTrue(all(t is None for t in seen['tools_seen']),
                        seen['tools_seen'])
        self.assertEqual(dispatched, [])
        self.assertTrue(any('"done"' in str(e) for e in evs))

    def test_plan_tools_false_still_returns_many_tools(self):
        """记录缺陷成因：allow_plan=False 只删 plan 动作，不是"不给工具"。"""
        from Virus_Platform_Core.ai_agent import chat
        self.assertGreater(len(chat._plan_tools(False)), 1)


class AiKeyProfileTests(unittest.TestCase):
    """2026-11-06 P0：空 Key 档案不得沿用/回落顶层 Key。"""

    def _sandbox(self):
        import json
        import tempfile
        from Virus_Platform_Core import config as cfgmod
        td = tempfile.TemporaryDirectory(prefix='vp_aikey_')
        self.addCleanup(td.cleanup)
        os.makedirs(os.path.join(td.name, 'run'), exist_ok=True)
        old_p, old_c = cfgmod.PLATFORM_ROOT, cfgmod.CONFIG_FILE
        cfgmod.PLATFORM_ROOT = td.name
        cfgmod.CONFIG_FILE = os.path.join(td.name, 'platform.json')

        def _restore():
            cfgmod.PLATFORM_ROOT, cfgmod.CONFIG_FILE = old_p, old_c
        self.addCleanup(_restore)
        return cfgmod, td.name

    def test_active_profile_never_falls_back_to_top_level_key(self):
        import json
        cfgmod, root = self._sandbox()
        with open(cfgmod._ai_local_path(), 'w', encoding='utf-8') as f:
            json.dump({'api_key': 'sk-top-A-secret',
                       'provider_keys': {
                           'B': '${VP_PROFILE_KEY_NOT_SET_XYZ}'}}, f)
        with open(cfgmod.CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump({'ai': {'active_profile': 'B'}}, f)
        c = cfgmod.Config()
        self.assertEqual(c.ai.get('active_profile'), 'B')
        self.assertEqual(c.ai.get('api_key'), '',
                         '档案 B 的 Key 展开为空时必须置空，绝不回落顶层 Key')

    def test_active_profile_uses_own_key(self):
        import json
        cfgmod, root = self._sandbox()
        with open(cfgmod._ai_local_path(), 'w', encoding='utf-8') as f:
            json.dump({'api_key': 'sk-top-A-secret',
                       'provider_keys': {'B': 'sk-B-own'}}, f)
        with open(cfgmod.CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump({'ai': {'active_profile': 'B'}}, f)
        c = cfgmod.Config()
        self.assertEqual(c.ai.get('api_key'), 'sk-B-own')

    def test_activate_profile_without_key_returns_400(self):
        import json
        cfgmod, root = self._sandbox()
        with open(cfgmod._ai_local_path(), 'w', encoding='utf-8') as f:
            json.dump({'provider_keys': {'B': ''}}, f)
        import app as appmod
        from Virus_Platform_Core.web.state import cfg
        old_ai = dict(cfg.ai)
        self.addCleanup(lambda: setattr(cfg, 'ai', old_ai))
        cfg.ai = dict(cfg.ai,
                      providers=[{'name': 'B', 'base_url': 'https://b/v1',
                                  'model': 'm', 'temperature': 0.3,
                                  'max_tokens': 2048}],
                      active_profile='A', api_key='sk-from-profile-A')
        appmod.app.config['TESTING'] = True
        with appmod.app.test_client() as c:
            r = c.post('/api/ai/profiles/activate', json={'name': 'B'})
        body = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 400, body)
        self.assertIn('没有可用 API Key', body)
        self.assertEqual(cfg.ai.get('api_key'), 'sk-from-profile-A',
                         '激活失败不得改写当前配置')

    def test_activate_profile_with_unset_env_placeholder_returns_400(self):
        import json
        cfgmod, root = self._sandbox()
        with open(cfgmod._ai_local_path(), 'w', encoding='utf-8') as f:
            json.dump({'provider_keys': {
                'B': '${VP_PROFILE_KEY_NOT_SET_XYZ}'}}, f)
        import app as appmod
        from Virus_Platform_Core.web.state import cfg
        old_ai = dict(cfg.ai)
        self.addCleanup(lambda: setattr(cfg, 'ai', old_ai))
        cfg.ai = dict(cfg.ai,
                      providers=[{'name': 'B', 'base_url': 'https://b/v1',
                                  'model': 'm', 'temperature': 0.3,
                                  'max_tokens': 2048}],
                      api_key='sk-from-profile-A')
        appmod.app.config['TESTING'] = True
        with appmod.app.test_client() as c:
            r = c.post('/api/ai/profiles/activate', json={'name': 'B'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('没有可用 API Key', r.get_data(as_text=True))


class AiKeyEnvTests(unittest.TestCase):
    """2026-11-06 P1：设置页保存/测试连接必须先展开 ${ENV_VAR}。"""

    def test_save_rejects_unset_env_placeholder(self):
        import app as appmod
        from Virus_Platform_Core.web.state import cfg
        old_ai = dict(cfg.ai)
        self.addCleanup(lambda: setattr(cfg, 'ai', old_ai))
        appmod.app.config['TESTING'] = True
        with appmod.app.test_client() as c:
            r = c.post('/api/ai/config', json={
                'base_url': 'https://x/v1', 'model': 'm',
                'api_key': '${VP_NOT_SET_SAVE_XYZ}'})
        body = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 400, body)
        self.assertIn('环境变量 VP_NOT_SET_SAVE_XYZ 未设置', body)
        self.assertEqual(cfg.ai.get('api_key'), old_ai.get('api_key'),
                         '校验失败不得改动已存 Key')


class SystemDirGuardTests(unittest.TestCase):
    """2026-11-06 P2：output_root 等系统目录守卫。"""

    def _guard(self):
        from Virus_Platform_Core.config import _ensure_not_system_dir
        return _ensure_not_system_dir

    def test_drive_root_rejected(self):
        if os.name != 'nt':
            self.skipTest('仅 Windows 盘符语义')
        for p in ('C:\\', 'D:\\'):
            with self.assertRaises(ValueError):
                self._guard()(p)

    def test_system_segments_rejected_on_any_drive(self):
        for p in (r'D:\Windows',
                  r'D:\Windows\System32',
                  r'E:\Program Files\App',
                  r'E:\Program Files (x86)\App',
                  r'C:\ProgramData\x',
                  r'C:\Users\someone',
                  r'C:\Users\someone\AppData\Local'):
            with self.assertRaises(ValueError, msg=p):
                self._guard()(p)

    def test_normal_data_dir_allowed(self):
        drv = os.path.splitdrive(os.path.abspath(os.getcwd()))[0] or 'C:'
        self._guard()(drv + os.sep + 'VirusPlatform-data-test')  # 不抛即可


class SpoolRelayTruncateTests(unittest.TestCase):
    """2026-11-06 P2：PID 复用 / 文件重建时 offset 必须归零重读。"""

    def test_mtime_regression_resets_offset(self):
        import json
        from Virus_Platform_Core.ai_agent import spool_relay as R
        d = tempfile.mkdtemp(prefix='vp_spool_')
        p = os.path.join(d, '4242.jsonl')
        with open(p, 'w', encoding='utf-8') as f:
            f.write(json.dumps({'type': 'a', 'n': 1}) + '\n')
        old_mtime = os.path.getmtime(p)
        R._offsets.clear()
        R._fingerprints.clear()
        R._baseline = None
        events, off = R._read_new(p)
        self.assertEqual([e['type'] for e in events], ['a'])
        self.assertGreater(off, 0)
        # 新内容更长（size >= 旧 offset），但 mtime 回退 = 文件被重建/PID 复用
        with open(p, 'w', encoding='utf-8') as f:
            f.write(json.dumps({'type': 'b', 'pad': 'x' * 30}) + '\n')
            f.write(json.dumps({'type': 'c', 'pad': 'y' * 30}) + '\n')
        os.utime(p, (old_mtime - 10, old_mtime - 10))
        events, off = R._read_new(p)
        self.assertEqual([e['type'] for e in events], ['b', 'c'],
                         'mtime 回退必须从头重读，不能从旧 offset 的半行开始')
        R._offsets.clear()
        R._fingerprints.clear()
        R._baseline = None


class UpdateFlagParseTests(unittest.TestCase):
    """2026-11-06 P2：字符串 "false" 不能把自动更新打开。"""

    def test_string_false_is_false(self):
        from Virus_Platform_Core.web.settings import _parse_strict_bool
        self.assertIs(_parse_strict_bool('false'), False)
        self.assertIs(_parse_strict_bool('0'), False)
        self.assertIs(_parse_strict_bool(False), False)
        self.assertIs(_parse_strict_bool('true'), True)
        self.assertIs(_parse_strict_bool('1'), True)
        self.assertIs(_parse_strict_bool(True), True)

    def test_other_values_rejected(self):
        from Virus_Platform_Core.web.settings import _parse_strict_bool
        for v in ('no', 'yes', 2, None, [], {}):
            with self.assertRaises(ValueError, msg=repr(v)):
                _parse_strict_bool(v)


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


class ResolveStagesOverrideTests(unittest.TestCase):
    """批准时可用请求里的阶段列表覆盖 AI 起草的 stages（2026-11-03）。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        (self.root / 'S1').mkdir()          # create_action 要求样品目录存在
        self._r1 = self.root / 'r1.fastq'
        self._r1.write_text('@r\nACGT\n+\nIIII\n', encoding='ascii')
        for p in (
                mock.patch.dict('Virus_Platform_Core.config.DIRS',
                                {'results': str(self.root)}),
                # 只验证批准前的覆盖落点，不真起分析任务
                mock.patch.object(executors, '_execute', lambda rec: None),
                mock.patch.object(executors, '_ai_active_count', lambda: 0)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._td.cleanup)

    def _draft(self, action='rerun_stage', stages=('kvsuite',), params=None):
        rec, err = executors.create_action(
            action, 'S1', stages=list(stages) if stages else None,
            params=params, reason='阶段覆盖测试')
        self.assertIsNone(err)
        self.addCleanup(self._drop, rec['id'])
        return rec

    @staticmethod
    def _drop(aid):
        """收尾：未处理的单子一律驳回，别让 pending 攒到上限。"""
        if (executors.get_action(aid) or {}).get('status') == 'pending':
            executors.resolve(aid, 'reject')

    def test_approve_with_override_updates_both_reads(self):
        rec = self._draft()
        got, err = executors.resolve(
            rec['id'], 'approve', [' assembly ', 'hostana', 'assembly'])
        self.assertIsNone(err)
        self.assertEqual(got['stages'], ['assembly', 'hostana'])   # 去重保序
        self.assertEqual(got['params']['stages'], ['assembly', 'hostana'])
        self.assertEqual(got['stages_override'],
                         {'from': ['kvsuite'], 'to': ['assembly', 'hostana']})

    def test_empty_stages_keeps_pending(self):
        rec = self._draft()
        got, err = executors.resolve(rec['id'], 'approve', [])
        self.assertIsNone(got)
        self.assertIn('不能为空', err or '')
        cur = executors.get_action(rec['id'])
        self.assertEqual(cur['status'], 'pending')      # 校验失败不翻状态
        self.assertEqual(cur['stages'], ['kvsuite'])
        self.assertNotIn('stages_override', cur)

    def test_unknown_stage_rejected(self):
        rec = self._draft()
        got, err = executors.resolve(rec['id'], 'approve', ['nosuchstage'])
        self.assertIsNone(got)
        self.assertIn('未知阶段', err or '')
        self.assertIn('nosuchstage', err or '')
        self.assertEqual((executors.get_action(rec['id']) or {}).get('status'),
                         'pending')

    def test_unsupported_action_rejected(self):
        rec = self._draft(action='create_sample', stages=None,
                          params={'r1': str(self._r1), 'sample': 'S1'})
        got, err = executors.resolve(rec['id'], 'approve', ['assembly'])
        self.assertIsNone(got)
        self.assertIn('不支持阶段覆盖', err or '')
        self.assertEqual((executors.get_action(rec['id']) or {}).get('status'),
                         'pending')

    def test_legacy_call_without_override_unchanged(self):
        rec = self._draft()
        got, err = executors.resolve(rec['id'], 'approve')
        self.assertIsNone(err)
        self.assertEqual(got['stages'], ['kvsuite'])
        self.assertNotIn('stages_override', got)
        self.assertNotIn('stages', got['params'])


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


class RevealKeyMaskTests(unittest.TestCase):
    """ai.reveal_key 口径（2026-11-06 内部版）：开启回显明文；缺省掩码。"""

    def test_masked_by_default(self):
        from Virus_Platform_Core.web.ai_assist import _masked
        out = _masked({'api_key': 'sk-abcdefgh1234'})
        self.assertEqual(out['api_key'], '••••1234')
        self.assertTrue(out['api_key_set'])

    def test_reveal_returns_plaintext(self):
        from Virus_Platform_Core.web.ai_assist import _masked
        out = _masked({'api_key': 'sk-abcdefgh1234', 'reveal_key': True})
        self.assertEqual(out['api_key'], 'sk-abcdefgh1234')
        self.assertTrue(out['api_key_set'])

    def test_max_tokens_default_unified(self):
        """配置默认 = 页面默认 = provider 兜底 = 32768；旧默认 8192 迁移。"""
        from Virus_Platform_Core import config as C
        self.assertEqual(C.Config().ai.get('max_tokens'), 32768)
        self.assertTrue(C.Config().ai.get('reveal_key'))


if __name__ == '__main__':
    unittest.main()
