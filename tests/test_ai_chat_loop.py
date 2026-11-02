# -*- coding: utf-8 -*-
"""AI 对话工具循环（2026-10-30 修复）：重复轮询拦截 + 轮次用完仍要出结论。

背景：用户截图里模型查了两次状态、准备提交预筛时被 MAX_TOOL_ROUNDS=3
掐断，只回了一句「工具调用轮次超限，已截断本次回答」。这里用脚本化的
chat_stream 验证三条行为，不依赖真实模型/网络。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from Virus_Platform_Core.ai_agent import chat  # noqa: E402


def _user():
    return [{'role': 'user', 'content': 'hi'}]


def _parse(lines):
    """run_chat yield 的是 SSE 文本帧（data: {...}），测试里先解析回事件。"""
    out = []
    for line in lines:
        line = str(line).strip()
        if not line.startswith('data:'):
            continue
        try:
            out.append(json.loads(line[5:].strip()))
        except ValueError:
            continue
    return out


def _text(evs):
    return ''.join(e.get('content') or '' for e in evs
                   if e.get('type') == 'delta')


def test_duplicate_tool_calls_are_skipped(monkeypatch):
    dispatched = []
    seen_convo = []

    def fake_dispatch(name, args):
        dispatched.append((name, args))
        return 'same result'

    def fake_stream(cfg, convo, tools=None, **kw):
        seen_convo.append('\n'.join(m.get('content') or '' for m in convo))
        if tools and len(seen_convo) <= 4:
            yield {'type': 'tool_calls', 'tool_calls': [
                {'id': 'c%d' % len(seen_convo),
                 'function': {'name': 'get_meta_results',
                              'arguments': '{"collection": "X"}'}}]}
        else:
            yield {'type': 'delta', 'content': '结论'}

    monkeypatch.setattr(chat.ai_tools, 'dispatch', fake_dispatch)
    monkeypatch.setattr(chat, 'chat_stream', fake_stream)

    evs = _parse(chat.run_chat({'model': 'm'}, _user()))
    # 前两次正常执行，第 3、4 次相同调用被跳过
    assert len(dispatched) == chat.MAX_SAME_CALL
    assert 'STOP' in seen_convo[-2] or 'STOP' in seen_convo[-1]
    assert '结论' in _text(evs)


def test_round_cap_still_returns_summary(monkeypatch):
    calls = {'n': 0}

    def fake_stream(cfg, convo, tools=None, **kw):
        calls['n'] += 1
        if tools:
            # 每轮参数都不同，避免触发重复调用拦截
            yield {'type': 'tool_calls', 'tool_calls': [
                {'id': 'c%d' % calls['n'],
                 'function': {'name': 'read_result_table',
                              'arguments': json.dumps({'n': calls['n']})}}]}
        else:
            yield {'type': 'delta', 'content': '收尾总结'}

    monkeypatch.setattr(chat.ai_tools, 'dispatch', lambda n, a: 'ok')
    monkeypatch.setattr(chat, 'chat_stream', fake_stream)

    evs = _parse(chat.run_chat({'model': 'm'}, _user()))
    assert '收尾总结' in _text(evs)
    assert not any('轮次超限' in (e.get('message') or '') for e in evs)
    # 工具轮次正好用满 + 一次无工具收尾
    assert calls['n'] == chat.MAX_TOOL_ROUNDS + 2


def test_round_budget_is_reasonable():
    # 3 轮会截断正常的"查状态→再查→提交"链路
    assert chat.MAX_TOOL_ROUNDS >= 6
