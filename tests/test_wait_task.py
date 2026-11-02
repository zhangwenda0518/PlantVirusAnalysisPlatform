# -*- coding: utf-8 -*-
"""wait_task 工具（有界等待）的行为验证：不依赖真实任务/网络。

tm（任务管理器）被替换成脚本化快照源；时钟走假时钟，测试里"超时"瞬间到达。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from Virus_Platform_Core.ai_agent import tools as ai_tools  # noqa: E402


class FakeTm:
    def __init__(self, statuses):
        self._statuses = list(statuses)
        self.i = 0

    def snapshot(self, tid, log_lines=0, include_result=True):
        st = self._statuses[min(self.i, len(self._statuses) - 1)]
        self.i += 1
        out = {'id': tid, 'name': '任务X', 'status': st,
               'stage': 'fastp', 'pct': 0.5}
        if st == 'done':
            out['result'] = 'PASS 12 / 30'
        return out


class FakeClock:
    """每次读时间 +5 秒：把"超时"瞬间送到。"""

    def __init__(self):
        self.t = 1000.0

    def time(self):
        self.t += 5
        return self.t

    def sleep(self, s):
        pass


def test_wait_task_done(monkeypatch):
    import Virus_Platform_Core.web.tasks as tasks_mod
    monkeypatch.setattr(tasks_mod, 'tm', FakeTm(['running', 'done']))
    monkeypatch.setattr(ai_tools, 'time', FakeClock())
    out = json.loads(ai_tools._wait_task_tool({'tid': 't1', 'timeout_sec': 240}))
    assert out['status'] == 'done'
    assert out.get('result') == 'PASS 12 / 30' or         out.get('result_preview') == 'PASS 12 / 30'


def test_wait_task_timeout_reports_running(monkeypatch):
    import Virus_Platform_Core.web.tasks as tasks_mod
    monkeypatch.setattr(tasks_mod, 'tm', FakeTm(['running']))
    monkeypatch.setattr(ai_tools, 'time', FakeClock())
    out = json.loads(ai_tools._wait_task_tool({'tid': 't1', 'timeout_sec': 30}))
    assert out['status'] in ('running', 'queued')
    assert '超时' in out['hint'] or 'timeout' in out['hint'].lower()


def test_wait_task_requires_tid():
    out = json.loads(ai_tools._wait_task_tool({}))
    assert 'error' in out


def test_wait_task_done_stops_polling(monkeypatch):
    import Virus_Platform_Core.web.tasks as tasks_mod
    calls = {'n': 0}

    class OneShot(FakeTm):
        def __init__(self):
            super().__init__(['done'])

        def snapshot(self, tid, log_lines=0, include_result=True):
            calls['n'] += 1
            return {'id': tid, 'name': 'X', 'status': 'done', 'stage': '',
                    'pct': 1, 'result': 'ok'}

    monkeypatch.setattr(tasks_mod, 'tm', OneShot())
    monkeypatch.setattr(ai_tools, 'time', FakeClock())
    out = json.loads(ai_tools._wait_task_tool({'tid': 't', 'timeout_sec': 240}))
    assert out['status'] == 'done'
    assert calls['n'] == 1          # 终态后不再轮询
