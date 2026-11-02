# -*- coding: utf-8 -*-
"""AI 工作台 P2 回归测试（2026-11-02）。

覆盖剩余 4 项：
  1) 闸门 pending 事件 / pending_gate() 必须带数值 deadline + timeout_s
     （前端闸门卡倒计时、刷新后 hydrate 都依赖它）；
  2) 并发闸按语言无关的 origin 标记计数，保留旧中文前缀兜底；
  3) 工具执行单校验失败不得留下空运行目录；
  4) 设置页不得把档案名/插件名拼进 inline onclick（data-name + 事件委托）。

风格与 tests/test_ai_hardening.py 一致：unittest + unittest.mock.patch，
不真调模型、不真起任务、不写 run/ 产物。
"""
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from Virus_Platform_Core.ai_agent import broadcast, executors  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_HTML = ROOT / 'webapp' / 'templates' / 'settings.html'


class GateDeadlineTests(unittest.TestCase):
    """闸门卡倒计时：pending 事件与 pending_gate 都必须给出期限。"""

    STAGE = 'kvsuite'
    CFG = {'enabled': True, 'gate': True, 'gate_stages': [STAGE],
           'gate_timeout_min': 15}

    def _patch_gate(self, events):
        def fake_publish(ev_type, **fields):
            fields = dict(fields)
            fields['id'] = len(events) + 1
            fields['type'] = ev_type
            events.append(fields)
            return fields

        return (
            mock.patch.object(broadcast, '_ai_cfg', lambda: dict(self.CFG)),
            mock.patch.object(broadcast, '_ai_assess',
                              lambda sample, stage: '建议继续（固定桩）'),
            mock.patch.object(broadcast, 'publish', fake_publish),
        )

    def test_pending_event_has_numeric_deadline(self):
        events = []
        p_cfg, p_assess, p_pub = self._patch_gate(events)
        t0 = time.time()
        with p_cfg, p_assess, p_pub:
            # 0.1s 超时：走真实等待路径但立刻返回，不等 15 分钟
            action = broadcast.gate_point('P2_SAMPLE', self.STAGE,
                                          timeout_s=0.1)
        self.assertEqual(action, 'timeout')
        pending = [e for e in events if e.get('status') == 'pending']
        self.assertEqual(len(pending), 1, events)
        ev = pending[0]
        self.assertIsInstance(ev.get('timeout_s'), (int, float))
        self.assertAlmostEqual(float(ev['timeout_s']), 0.1, places=3)
        self.assertIsInstance(ev.get('deadline'), (int, float))
        self.assertGreater(ev['deadline'], t0)
        self.assertLessEqual(ev['deadline'], time.time() + 1)
        # 终态事件不需要期限字段（保持原样）
        final = [e for e in events if e.get('status') == 'timeout']
        self.assertEqual(len(final), 1)

    def test_pending_gate_exposes_deadline_and_timeout(self):
        events = []
        out = []
        p_cfg, p_assess, p_pub = self._patch_gate(events)

        def run_gate():
            try:
                with p_cfg, p_assess, p_pub:
                    out.append(broadcast.gate_point(
                        'P2_SAMPLE2', self.STAGE, timeout_s=5.0))
            except Exception as e:              # pragma: no cover
                out.append(e)

        th = threading.Thread(target=run_gate, daemon=True)
        th.start()
        entry = None
        try:
            end = time.time() + 3
            while time.time() < end:
                got = broadcast.pending_gate('P2_SAMPLE2', self.STAGE)
                if got:
                    entry = got[0]
                    break
                time.sleep(0.01)
            self.assertIsNotNone(entry, 'pending_gate 未返回待决闸门')
            self.assertIsInstance(entry.get('deadline'), (int, float))
            self.assertIsInstance(entry.get('timeout_s'), (int, float))
            self.assertGreater(float(entry['deadline']), time.time())
        finally:
            broadcast.resolve_gate('P2_SAMPLE2', self.STAGE, 'continue')
            th.join(timeout=5)
        self.assertEqual(out, ['continue'])


class ConcurrencyOriginTests(unittest.TestCase):
    """并发闸不能只靠中文前缀 'AI执行·'（英文界面任务名不是它）。"""

    def test_english_title_counted_by_origin(self):
        tasks = [{'id': 't1', 'name': 'AI run·S1', 'status': 'running',
                  'origin': 'ai'}]
        self.assertEqual(executors._count_ai_active(tasks), 1)

    def test_legacy_chinese_prefix_fallback(self):
        tasks = [{'id': 't2', 'name': 'AI执行·S1', 'status': 'running'}]
        self.assertEqual(executors._count_ai_active(tasks), 1)

    def test_mixed_and_finished_records(self):
        tasks = [
            {'id': 't1', 'name': 'AI run·S1', 'status': 'running',
             'origin': 'ai'},
            {'id': 't2', 'name': 'AI执行·S2', 'status': 'running'},
            {'id': 't3', 'name': 'AI run·S3', 'status': 'done',
             'origin': 'ai'},                       # 终态不算
            {'id': 't4', 'name': '用户手动任务', 'status': 'running'},
            {},
        ]
        self.assertEqual(executors._count_ai_active(tasks), 2)
        self.assertEqual(executors._count_ai_active([]), 0)
        self.assertEqual(executors._count_ai_active(None), 0)

    def test_action_record_marked_origin(self):
        rec, err = executors.create_action(
            'run_tool', '', params={'tool': 'convert', 'input': 'x.fq'},
            reason='P2 origin')
        self.assertIsNone(err)
        try:
            self.assertEqual(rec.get('origin'), 'ai')
        finally:
            executors.resolve(rec['id'], 'reject')

    def test_below_limit_approve_not_rejected(self):
        rec, err = executors.create_action(
            'run_tool', '', params={'tool': 'convert', 'input': 'x.fq'},
            reason='P2 未超限')
        self.assertIsNone(err)
        with mock.patch.object(executors, 'MAX_AI_RUNS', 2), \
                mock.patch.object(executors, '_ai_active_count',
                                  lambda: 1), \
                mock.patch.object(executors, '_dispatch',
                                  side_effect=RuntimeError('boom')):
            _r, e = executors.resolve(rec['id'], 'approve')
        self.assertIsNone(e, e)          # 未超限不得被并发闸挡回 pending


class ToolRunDirCleanupTests(unittest.TestCase):
    """校验失败（job 工厂抛错）不得留下空运行目录。"""

    def test_invalid_params_leave_no_run_dir(self):
        from Virus_Platform_Core.web import state as web_state
        from Virus_Platform_Core.web import tools_api
        import Virus_Platform_Core.utils as vp_utils

        def fake_job(ctx):
            raise RuntimeError('缺少必填参数: input（input）')

        with tempfile.TemporaryDirectory() as td:
            fake_reg = {'faketool': {'title': '假工具', 'title_en': 'fake',
                                     'need_db': False, 'job': fake_job}}
            with mock.patch.object(web_state, 'tool_runs_root',
                                   lambda: td), \
                    mock.patch.object(vp_utils, 'check_path',
                                      lambda p, **kw: p), \
                    mock.patch.object(tools_api, 'TOOL_REGISTRY', fake_reg):
                with self.assertRaises(RuntimeError) as cm:
                    executors._launch_tool('faketool', {})
            self.assertIn('缺少必填参数', str(cm.exception))
            self.assertEqual(os.listdir(td), [],
                             '校验失败后不应留下空运行目录')


class SettingsInlineHandlerTests(unittest.TestCase):
    """档案名/插件名不得拼进 inline onclick（单引号会失效/可注入）。"""

    @classmethod
    def setUpClass(cls):
        cls.text = SETTINGS_HTML.read_text(encoding='utf-8')

    def test_no_name_in_inline_onclick(self):
        for bad in ('onclick="aiUseProfile(', 'onclick="aiPfUse(',
                    'onclick="aiPfDel(',
                    'onclick="pluginUninstall(',
                    'onclick="pluginInstallAvail('):
            self.assertNotIn(bad, self.text)

    def test_data_attributes_and_delegation(self):
        self.assertIn('data-name="${esc(p.name)}"', self.text)
        self.assertIn("box.addEventListener('click'", self.text)
        # 原有函数保留可复用（数据属性只是调用方式变了）
        for fn in ('async function aiPfUse(name)',
                   'async function aiPfDel(name)',
                   'async function pluginUninstall(name)',
                   'async function pluginInstallAvail(name)'):
            self.assertIn(fn, self.text)


if __name__ == '__main__':
    unittest.main()
