# -*- coding: utf-8 -*-
"""2026-11-04 AI 工作台能力提升（T1–T7）专项测试。

覆盖：
  1) 天花板常量：工具轮次 / 历史条数 / 生成预算 / Config 默认 max_tokens
  2) 历史摘要压缩 _history_digest（不再静默丢记忆）
  3) 工具结果分页：read_result_table / read_result_file 的 offset 与 has_more
  4) next_actions：合法块提取+删除、坏 JSON 原文保留、非法 action 静默拒绝
  5) 系统提示新规则 17–19（中英模板）与 .format 花括号安全

隔离方式同 tests/test_ai_durable.py：任何平台模块导入前先把运行期根
VP_RUN_ROOT 指到临时目录，测试产物不落真实 run/。
"""
import json
import os
import sys
import tempfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ['VP_NO_RECOVER'] = '1'
os.environ.setdefault('VP_RUN_ROOT', tempfile.mkdtemp(prefix='vp_boost_'))
# 常量断言要求默认值：清掉宿主 shell 可能残留的环境覆盖
os.environ.pop('VP_AI_MAX_TOOL_ROUNDS', None)

from Virus_Platform_Core import config as cfgmod  # noqa: E402
from Virus_Platform_Core.ai_agent import chat  # noqa: E402
from Virus_Platform_Core.ai_agent import tools as ai_tools  # noqa: E402

SAMPLE = '自检样品'


def _write_sample_file(relpath, text):
    """在隔离的 results/<样品>/ 下写一个测试文件。"""
    from Virus_Platform_Core.config import DIRS
    path = os.path.join(DIRS['results'], SAMPLE, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text)
    return path


def _next_block(payload):
    return '结论：检出 3 种病毒。\n\n```next_actions\n' + payload + '\n```\n'


def _sse_events(lines):
    out = []
    for line in lines:
        line = str(line).strip()
        if line.startswith('data:'):
            try:
                out.append(json.loads(line[5:].strip()))
            except ValueError:
                pass
    return out


# ===== 1) 常量 ======================================================
def test_constants():
    assert chat.MAX_TOOL_ROUNDS == 24          # 10 → 24（长链路多轮）
    assert chat.MAX_HISTORY == 32              # 20 → 32
    assert chat._BUDGET_MAX == 16384           # 8192 → 16384


def test_config_default_max_tokens(monkeypatch, tmp_path):
    # 隔离平台根下的 platform.json，确保读到的是 __init__ 的默认值
    monkeypatch.setattr(cfgmod, 'CONFIG_FILE',
                        str(tmp_path / 'no-such-platform.json'))
    c = cfgmod.Config()
    assert c.ai.get('max_tokens') == 8192      # 2048 → 8192


# ===== 2) 历史摘要压缩 ==============================================
def test_history_digest_short_history_is_empty():
    msgs = [{'role': 'user' if i % 2 == 0 else 'assistant',
             'content': f'短消息 {i}'} for i in range(chat.MAX_HISTORY)]
    assert chat._history_digest(msgs) == ''
    assert chat._history_digest([]) == ''


def test_history_digest_compresses_dropped_messages():
    msgs = []
    for i in range(chat.MAX_HISTORY + 8):
        role = 'user' if i % 2 == 0 else 'assistant'
        msgs.append({'role': role, 'content': f'第{i}条 ' + 'x' * 200})
    digest = chat._history_digest(msgs)
    assert digest
    assert len(digest) <= 1200
    assert '第0条' in digest                   # 最早一条被压缩带入
    assert '\n' not in digest                  # 单行
    assert '；' in digest


# ===== 3) 工具结果分页 ==============================================
def test_read_result_table_pagination():
    lines = ['id\tvalue'] + [f'r{i}\tv{i}' for i in range(50)]
    _write_sample_file('page.tsv', '\n'.join(lines) + '\n')

    res = json.loads(ai_tools.dispatch('read_result_table', {
        'sample': SAMPLE, 'relpath': 'page.tsv', 'max_rows': 10}))
    assert res['offset'] == 0
    assert res['n_total'] == 50
    assert res['has_more'] is True
    assert [r[0] for r in res['rows']] == [f'r{i}' for i in range(10)]

    res = json.loads(ai_tools.dispatch('read_result_table', {
        'sample': SAMPLE, 'relpath': 'page.tsv', 'max_rows': 10,
        'offset': 10}))
    assert res['offset'] == 10
    assert res['rows'][0][0] == 'r10'
    assert res['has_more'] is True

    res = json.loads(ai_tools.dispatch('read_result_table', {
        'sample': SAMPLE, 'relpath': 'page.tsv', 'max_rows': 10,
        'offset': 45}))
    assert [r[0] for r in res['rows']] == [f'r{i}' for i in range(45, 50)]
    assert res['has_more'] is False


def test_read_result_file_pagination():
    text = ''.join(str(i % 10) for i in range(1200))
    _write_sample_file('notes.txt', text)
    # max_chars 下限 500：offset=10 取 [10:510]，后面还有；offset=700 取尾段
    res = json.loads(ai_tools.dispatch('read_result_file', {
        'kind': 'sample', 'name': SAMPLE, 'relpath': 'notes.txt',
        'max_chars': 500, 'offset': 10}))
    assert res['offset'] == 10
    assert res['total_chars'] == 1200
    assert res['head'] == text[10:510]
    assert res['has_more'] is True

    res = json.loads(ai_tools.dispatch('read_result_file', {
        'kind': 'sample', 'name': SAMPLE, 'relpath': 'notes.txt',
        'max_chars': 500, 'offset': 700}))
    assert res['head'] == text[700:1200]
    assert res['has_more'] is False


def test_tool_specs_expose_offset():
    by_name = {t['function']['name']: t['function']
               for t in ai_tools.TOOLS_SPEC}
    for name in ('read_result_table', 'read_tool_run_file',
                 'read_prescreen_file', 'read_result_file'):
        props = by_name[name]['parameters']['properties']
        assert props.get('offset', {}).get('type') == 'integer', name
        assert 'offset' in by_name[name]['description'], name


# ===== 4) next_actions 解析 ==========================================
def test_parse_next_actions_valid():
    payload = json.dumps([{
        'label': '重跑鉴定', 'action': 'rerun_stage', 'sample': 'S1',
        'stages': ['kvsuite'], 'reason': '覆盖率偏低'}], ensure_ascii=False)
    raw = _next_block(payload)
    actions, cleaned = chat._parse_next_actions(raw)
    assert len(actions) == 1
    assert actions[0]['action'] == 'rerun_stage'
    assert actions[0]['label'] == '重跑鉴定'
    assert 'next_actions' not in cleaned
    assert '```' not in cleaned
    assert '结论' in cleaned


def test_parse_next_actions_fills_default_label():
    payload = json.dumps([{'action': 'retry_failed', 'reason': '缺参数'}])
    actions, _cleaned = chat._parse_next_actions(_next_block(payload))
    assert actions and actions[0]['label'] == 'retry_failed'


def test_parse_next_actions_bad_json_keeps_text():
    raw = _next_block('[{bad json')
    actions, cleaned = chat._parse_next_actions(raw)
    assert actions == []
    assert cleaned == raw                      # 原文保留，不下发事件


def test_parse_next_actions_rejects_unknown_action():
    payload = json.dumps([{'action': 'delete_everything', 'reason': 'x'}])
    raw = _next_block(payload)
    actions, cleaned = chat._parse_next_actions(raw)
    assert actions == []
    assert cleaned == raw


def test_parse_next_actions_rejects_too_many_or_no_reason():
    too_many = json.dumps([{'action': 'retry_failed', 'reason': 'x'}] * 4)
    assert chat._parse_next_actions(_next_block(too_many))[0] == []
    no_reason = json.dumps([{'action': 'retry_failed', 'reason': ''}])
    assert chat._parse_next_actions(_next_block(no_reason))[0] == []


def test_parse_next_actions_uses_last_block():
    first = json.dumps([{'action': 'retry_failed', 'reason': '旧'}])
    last = json.dumps([{'action': 'rerun_stage', 'reason': '新',
                        'stages': ['kvsuite']}])
    raw = '开头\n' + _next_block(first) + '\n中间\n' + _next_block(last)
    actions, cleaned = chat._parse_next_actions(raw)
    assert len(actions) == 1 and actions[0]['reason'] == '新'
    assert 'rerun_stage' not in cleaned         # 只删最后一个块
    assert '中间' in cleaned
    assert 'retry_failed' in cleaned            # 前面的块按规格保留


def test_run_chat_emits_next_actions(monkeypatch):
    def fake_stream(cfg, convo, tools=None, **kw):
        yield {'type': 'delta', 'content': _next_block(json.dumps(
            [{'action': 'retry_failed', 'reason': '测试'}]))}

    monkeypatch.setattr(chat, 'chat_stream', fake_stream)
    evs = _sse_events(chat.run_chat({'model': 'm'},
                                    [{'role': 'user', 'content': 'hi'}]))
    na = [e for e in evs if e.get('type') == 'next_actions']
    assert len(na) == 1
    assert na[0]['actions'][0]['action'] == 'retry_failed'
    assert any(e.get('type') == 'done' for e in evs)


def test_run_chat_bad_next_actions_is_silent(monkeypatch):
    def fake_stream(cfg, convo, tools=None, **kw):
        yield {'type': 'delta', 'content': _next_block('{not json')}

    monkeypatch.setattr(chat, 'chat_stream', fake_stream)
    evs = _sse_events(chat.run_chat({'model': 'm'},
                                    [{'role': 'user', 'content': 'hi'}]))
    assert not [e for e in evs if e.get('type') == 'next_actions']
    assert any(e.get('type') == 'done' for e in evs)


# ===== 5) 系统提示规则 ==============================================
def test_system_rules_contain_new_rules():
    for tpl in (chat._SYSTEM_RULES_ZH, chat._SYSTEM_RULES_EN):
        assert '澄清' in tpl                   # 规则 17 澄清优先
        assert 'next_actions' in tpl           # 规则 19 结构化建议
        assert '17.' in tpl and '18.' in tpl and '19.' in tpl


def test_system_rules_format_safe():
    # 规则 19 的 JSON 示例必须写成 {{ }}，否则 str.format 会 KeyError
    for tpl in (chat._SYSTEM_RULES_ZH, chat._SYSTEM_RULES_EN):
        out = tpl.format(context='{"platform": 0}', capabilities='cap',
                         lang_name='简体中文')
        assert '"action"' in out
        assert '{{' not in out and '}}' not in out


# ===== 6) 工具事件明细（T6 顺带覆盖） ==============================
def test_tool_detail_shape():
    detail = chat._tool_detail(
        'read_result_table',
        {'sample': SAMPLE, 'relpath': 'x.tsv', 'ignored': '第三键不出现'},
        json.dumps({'rows': [['r0'], ['r1']]}))
    assert detail.startswith('read_result_table(')
    assert '2 行' in detail
    assert 'ignored' not in detail             # 只取前 2 个键
    assert len(detail) <= 120

    detail = chat._tool_detail('read_result_file', {},
                               json.dumps({'head': 'abc'}))
    assert '3 字' in detail
    detail = chat._tool_detail('list_samples', {}, json.dumps({'a': 1, 'b': 2}))
    assert '2 键' in detail


# ===== 7) 读文件越界判定（2026-11-06） =============================
def test_path_within_uses_separator_boundary():
    """同前缀兄弟目录不能互读：S1 不能读 S1evil 下的文件。"""
    base = os.path.join(tempfile.gettempdir(), 'vp_prefix_guard')
    root = os.path.join(base, 'S1')
    sibling = os.path.join(base, 'S1evil')
    assert ai_tools._path_within(os.path.join(root, 'x.txt'), root) is True
    assert ai_tools._path_within(root, root) is True
    assert ai_tools._path_within(os.path.join(sibling, 'secret.txt'), root) is False


def test_read_result_file_rejects_absolute_relpath():
    """relpath 传绝对路径时必须在拼接/前缀判断之前拒绝。"""
    sibling = os.path.join(DIRS_RESULTS(), SAMPLE + 'evil', 'secret.txt')
    res = json.loads(ai_tools.dispatch('read_result_file', {
        'kind': 'sample', 'name': SAMPLE, 'relpath': sibling}))
    assert '绝对路径' in res.get('error', ''), res


def test_read_result_table_rejects_absolute_relpath():
    sibling = os.path.join(DIRS_RESULTS(), SAMPLE + 'evil', 'secret.tsv')
    res = json.loads(ai_tools._safe_read_table_under(
        os.path.join(DIRS_RESULTS(), SAMPLE), sibling, 5))
    assert '绝对路径' in res.get('error', ''), res


def DIRS_RESULTS():
    from Virus_Platform_Core.config import DIRS
    return DIRS['results']
