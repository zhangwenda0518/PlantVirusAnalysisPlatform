"""对话坞产物卡片（2026-11-02）。

用户诉求：「对话框应该给出 html 文件及其他文件图片的渲染，我可以直接打开」。
这条链路由三段组成，任何一段松掉用户都只能看到裸路径：

  1. ``Virus_Platform_Core/ai_agent/artifacts.py`` 从工具返回的 JSON 与回答正文
     里认出真实落盘的文件（只认平台目录内、真实存在的），给出 kind/size/url；
  2. ``chat.run_chat`` 在**每一条**结束路径上下发 ``artifacts`` 事件（早期实现只
     挂在「工具轮次用完」的兜底分支上，正常回答根本收不到卡片）；
  3. ``webapp/static/app-ai.js`` 渲染卡片 + 弹层预览。

这里覆盖 1 与 2。3 是浏览器行为，靠 ``node --check`` 与手工验证。
"""
import json
import os

import pytest

from Virus_Platform_Core.ai_agent import artifacts as A


def _parse(sse):
    """把 ``data: {...}\\n\\n`` 拆回 dict。"""
    assert sse.startswith('data: '), sse[:40]
    return json.loads(sse[6:])


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """把「平台根」指向临时目录：产物判定要真实存在且在根内才算，
    直接往仓库 run/results 里写文件会污染真实结果目录。"""
    root = tmp_path
    d = root / 'run' / 'results' / 'demo' / '07_report'
    d.mkdir(parents=True)
    html = d / 'report_v2.html'
    html.write_text('<html><body>hi</body></html>', encoding='utf-8')
    png = root / 'run' / 'results' / 'demo' / '05_phylo' / 'tree.png'
    png.parent.mkdir(parents=True)
    png.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 64)
    tsv = root / 'run' / 'results' / 'demo' / '03c_consensus' / 'coverage.tsv'
    tsv.parent.mkdir(parents=True)
    tsv.write_text('node\tmean_depth\nNODE_1\t9.54\n', encoding='utf-8')
    monkeypatch.setattr(A, '_roots', lambda: [str(root)])
    return {'root': str(root), 'html': str(html), 'png': str(png), 'tsv': str(tsv)}


def test_kind_of_known_extensions():
    assert A.kind_of('x.html') == 'html'
    assert A.kind_of('x.PNG') == 'image'
    assert A.kind_of('x.tsv') == 'table'
    assert A.kind_of('x.faa') == 'text'
    assert A.kind_of('x.pdf') == 'pdf'
    assert A.kind_of('x.bin') == 'file'


def test_collect_accepts_real_files_and_orders_by_kind(sandbox):
    items = A.collect([sandbox['tsv'], sandbox['html'], sandbox['png']])
    assert [i['kind'] for i in items] == ['html', 'image', 'table']
    it = items[0]
    assert it['name'] == 'report_v2.html'
    assert it['size'] > 0
    assert it['url'].startswith('/api/fs/raw?path=')
    assert it['rel'].replace('\\', '/').endswith('report_v2.html')


def test_collect_rejects_outside_and_missing(sandbox):
    assert A.collect([r'C:\Windows\win.ini']) == []
    assert A.collect([os.path.join(sandbox['root'], 'nope.html')]) == []


def test_from_text_finds_absolute_path(sandbox):
    text = '报告已生成：%s（约 1.5 MB），可以直接打开。' % sandbox['html']
    items = A.collect(A.from_text(text))
    assert [i['name'] for i in items] == ['report_v2.html']


def test_from_text_finds_relative_path(sandbox):
    text = '图在 run/results/demo/05_phylo/tree.png 这一层。'
    items = A.collect(A.from_text(text))
    assert [i['name'] for i in items] == ['tree.png']


def test_from_text_stops_at_chinese_punctuation(sandbox):
    """中文括号/逗号不能吞进路径（`report.html（983,662 字节…` 的教训）。"""
    got = A.from_text('见 %s（983,662 字节，生成时间 2026-11-02）' % sandbox['html'])
    assert got and got[0].rstrip('\\/') == sandbox['html']


def test_from_text_ignores_plain_prose():
    assert A.from_text('没有路径的一段话，只有「结果中心」这样的词也不算。') == []
    assert A.from_text('') == []


def test_from_json_blob_walks_nested_paths(sandbox):
    blob = json.dumps({'status': 'ok',
                       'result': {'html': sandbox['html'],
                                  'figures': [sandbox['png']]}},
                      ensure_ascii=False)
    items = A.collect(A.from_json_blob(blob))
    assert {i['name'] for i in items} == {'report_v2.html', 'tree.png'}


def test_from_json_blob_tolerates_garbage():
    assert A.from_json_blob('这不是 JSON') == []
    assert A.from_json_blob('{"a": 1') == []
    assert A.from_json_blob('') == []


def test_describe_counts_by_kind(sandbox):
    items = A.collect([sandbox['html'], sandbox['png']])
    s = A.describe(items)
    assert '2 个产物' in s and 'html' in s and 'image' in s


def test_finish_dedupes(sandbox):
    assert len(A.finish([sandbox['html'], sandbox['html']])) == 1


def test_output_root_brief_two_branches():
    s = A.output_root_brief()
    assert s.startswith('输出目录')
    assert ('平台默认' in s) or ('用户自定义' in s)


def test_sensitive_paths_never_become_cards(tmp_path, monkeypatch):
    """2026-11-06：run/ai_local.json 与 run/ai_sessions/ 不得进产物卡片。

    平台根在 _roots() 里，这两类路径天然命中；以前模型在正文/工具 JSON
    里提到它们就能生成可点开的卡片，点开即经 /api/fs/raw 返回明文 Key。
    """
    import Virus_Platform_Core.config as cfgmod
    root = tmp_path
    (root / 'run' / 'ai_sessions').mkdir(parents=True)
    local = root / 'run' / 'ai_local.json'
    local.write_text('{"api_key": "sk-leak"}', encoding='utf-8')
    sess = root / 'run' / 'ai_sessions' / 'c-abc.jsonl'
    sess.write_text('{"role": "user"}\n', encoding='utf-8')
    monkeypatch.setattr(cfgmod, 'PLATFORM_ROOT', str(root))
    monkeypatch.setattr(cfgmod, 'RUN_ROOT', str(root / 'run'))
    monkeypatch.setattr(A, '_roots', lambda: [str(root)])
    assert A.collect([str(local)]) == []
    assert A.collect([str(sess)]) == []
    # 普通产物仍可成卡片
    ok = root / 'report.html'
    ok.write_text('<html/>', encoding='utf-8')
    assert [x['name'] for x in A.collect([str(ok)])] == ['report.html']


# ── chat 侧：每一条结束路径都要下发 artifacts 事件 ───────────────────────
def _stream_of(evs):
    def _s(*a, **kw):
        for e in evs:
            yield e
    return _s


def test_chat_emits_artifacts_on_normal_finish(monkeypatch, sandbox):
    """正常回答（模型没调工具、直接给结论）也必须带卡片。

    早期实现把 artifacts 事件挂在「工具轮次用完」的兜底分支上，正常结束
    直接 return，用户永远看不到卡片——这就是本测试要钉住的行为。
    """
    from Virus_Platform_Core.ai_agent import chat

    text = '已生成结论文档：%s' % sandbox['html']
    monkeypatch.setattr(chat, 'chat_stream', _stream_of([
        {'type': 'delta', 'content': text},
        {'type': 'finish', 'reason': 'stop'},
    ]))
    evs = [_parse(x) for x in
           chat.run_chat({'model': 'stub', 'max_tokens': 512},
                         [{'role': 'user', 'content': '报告在哪'}])]
    types = [e.get('type') for e in evs]
    assert 'artifacts' in types, types
    art = [e for e in evs if e.get('type') == 'artifacts'][0]
    assert art['items'] and art['items'][0]['name'] == 'report_v2.html'
    assert 'summary' in art
    # 卡片必须在 done 之前下发，否则前端已经收尾
    assert types.index('artifacts') < types.index('done')


def test_chat_without_paths_sends_no_artifacts(monkeypatch):
    from Virus_Platform_Core.ai_agent import chat

    monkeypatch.setattr(chat, 'chat_stream', _stream_of([
        {'type': 'delta', 'content': '这个问题不需要文件。'},
        {'type': 'finish', 'reason': 'stop'},
    ]))
    evs = [_parse(x) for x in
           chat.run_chat({'model': 'stub', 'max_tokens': 512},
                         [{'role': 'user', 'content': '你好'}])]
    types = [e.get('type') for e in evs]
    assert 'artifacts' not in types
    assert types[-1] == 'done'


def test_chat_emits_artifacts_when_provider_fails(monkeypatch):
    """上游报错时也要以 done 收尾（卡片允许为空，但不能只丢一个 error）。"""
    from Virus_Platform_Core.ai_agent import chat
    from Virus_Platform_Core.ai_agent.provider import AIProviderError

    def _boom(*a, **kw):
        raise AIProviderError('upstream down', status=500)
    monkeypatch.setattr(chat, 'chat_stream', _boom)
    evs = [_parse(x) for x in
           chat.run_chat({'model': 'stub', 'max_tokens': 64},
                         [{'role': 'user', 'content': 'hi'}])]
    types = [e.get('type') for e in evs]
    assert 'error' in types
    assert types[-1] == 'done'


# ----------------------------------------------------------------------
# 播报事件的产物卡片（2026-11-02）：整轮完成 / AI 播报正文里的路径
# ----------------------------------------------------------------------
def _mk_sample(root, name='S1'):
    import os
    s = os.path.join(root, name)
    os.makedirs(os.path.join(s, '07_report'), exist_ok=True)
    open(os.path.join(s, '07_report', 'report_v2.html'), 'w',
         encoding='utf-8').write('<html><body>r</body></html>')
    os.makedirs(os.path.join(s, '05_phylo', 'Potexvirus_pepini'), exist_ok=True)
    open(os.path.join(s, '05_phylo', 'Potexvirus_pepini', 'tree.png'), 'wb').write(b'\x89PNG\r\n')
    os.makedirs(os.path.join(s, '09_genome_plots'), exist_ok=True)
    open(os.path.join(s, '09_genome_plots', 'NODE_1.circular.svg'), 'w',
         encoding='utf-8').write('<svg/>')
    return s


def test_run_done_event_carries_report_cards(tmp_path, monkeypatch):
    """跑完就播报 + 就能点开：run_done 事件必须自带产物卡片，报告排第一。"""
    import Virus_Platform_Core.config as cfg
    from Virus_Platform_Core.ai_agent import artifacts as A
    from Virus_Platform_Core.ai_agent import broadcast as B

    _mk_sample(str(tmp_path))
    monkeypatch.setitem(cfg.DIRS, 'results', str(tmp_path))
    monkeypatch.setattr(A, '_roots', lambda: [str(tmp_path)])
    monkeypatch.setattr(B, '_ai_cfg', lambda: {'enabled': False})

    B.note_run_done('S1')
    ev = B._events[-1]
    assert ev['type'] == 'run_done' and ev['sample'] == 'S1'
    items = ev.get('artifacts') or []
    assert items, 'run_done 必须带产物卡片'
    assert items[0]['kind'] == 'html'
    assert items[0]['name'] == 'report_v2.html'
    kinds = [x['kind'] for x in items]
    assert 'image' in kinds                      # 进化树 / 基因组图
    assert len(items) <= 8
    # SSE 走 json.dumps：这里钉住事件可序列化（避免 Path/自定义对象漏进来）
    import json
    assert '"artifacts"' in json.dumps(ev, ensure_ascii=False)


def test_run_done_without_outputs_sends_empty_cards(tmp_path, monkeypatch):
    import Virus_Platform_Core.config as cfg
    from Virus_Platform_Core.ai_agent import broadcast as B
    monkeypatch.setitem(cfg.DIRS, 'results', str(tmp_path))
    monkeypatch.setattr(B, '_ai_cfg', lambda: {'enabled': False})
    B.note_run_done('nope')
    ev = B._events[-1]
    assert ev['type'] == 'run_done' and ev.get('artifacts') == []


def test_text_artifacts_only_inside_platform(tmp_path, monkeypatch):
    from Virus_Platform_Core.ai_agent import artifacts as A
    from Virus_Platform_Core.ai_agent import broadcast as B
    p = tmp_path / 'x.html'
    p.write_text('<html/>', encoding='utf-8')
    monkeypatch.setattr(A, '_roots', lambda: [str(tmp_path)])
    got = B._text_artifacts('报告见 %s ；另见 C:\\Windows\\win.ini' % p)
    assert [x['name'] for x in got] == ['x.html']
    assert B._text_artifacts('') == []
    assert B._text_artifacts('没有路径的一句话') == []

# ----------------------------------------------------------------------
# 阶段完成 / 执行单完成的产物卡片（2026-11-02 追加）
# ----------------------------------------------------------------------
def _mk_stage(root, name='S1'):
    """一个样品里只有 05_phylo：一张图 + 一个比对 + 一份 summary。"""
    import os
    s = os.path.join(root, name, '05_phylo')
    os.makedirs(os.path.join(s, 'Potexvirus_pepini'), exist_ok=True)
    open(os.path.join(s, 'Potexvirus_pepini', 'tree.png'), 'wb').write(b'\x89PNG\r\n')
    open(os.path.join(s, 'aln.fasta'), 'w', encoding='utf-8').write('>a\nACGT\n')
    open(os.path.join(s, 'summary.json'), 'w', encoding='utf-8').write('{}')
    open(os.path.join(s, 'group.tsv'), 'w', encoding='utf-8').write('g\tn\n')
    return s


def _sandbox_stage(tmp_path, monkeypatch):
    import Virus_Platform_Core.config as cfg
    from Virus_Platform_Core.ai_agent import artifacts as A
    _mk_stage(str(tmp_path))
    monkeypatch.setitem(cfg.DIRS, 'results', str(tmp_path))
    monkeypatch.setattr(A, '_roots', lambda: [str(tmp_path)])


def test_stage_artifacts_only_visuals_and_tables(tmp_path, monkeypatch):
    """阶段卡片只给「点开就能看」的：图/表。summary.json、aln.fasta 不铺满对话坞。"""
    from Virus_Platform_Core.ai_agent import broadcast as B
    _sandbox_stage(tmp_path, monkeypatch)
    items = B._stage_artifacts('S1', 'phylo', limit=3)
    names = [x['name'] for x in items]
    assert names and names[0] == 'tree.png'
    assert 'group.tsv' in names
    assert 'summary.json' not in names and 'aln.fasta' not in names
    assert B._stage_artifacts('S1', 'nosuchstage') == []


def test_stage_done_event_carries_cards(tmp_path, monkeypatch):
    from Virus_Platform_Core.ai_agent import broadcast as B
    _sandbox_stage(tmp_path, monkeypatch)
    monkeypatch.setattr(B, '_ai_cfg', lambda: {'enabled': False})
    B.note_stage_done('S1', 'phylo')
    ev = B._events[-1]
    assert ev['type'] == 'stage_done'
    items = ev.get('artifacts') or []
    assert [x['name'] for x in items][:1] == ['tree.png']
    import json
    assert '"artifacts"' in json.dumps(ev, ensure_ascii=False)


def test_action_artifacts_for_rerun_stage(tmp_path, monkeypatch):
    """执行单重跑某阶段 → 卡片就是那个阶段的产物。"""
    from Virus_Platform_Core.ai_agent import executors as E
    _sandbox_stage(tmp_path, monkeypatch)
    rec = {'id': 'act_1', 'action': 'rerun_stage', 'sample': 'S1',
           'stages': ['phylo'], 'params': {}, 'ts': 0}
    assert [x['name'] for x in E._action_artifacts(rec)][:1] == ['tree.png']
    # 状态不是 done 时不算（由 _snapshot_publish 控制）
    assert E._action_artifacts({'id': 'x', 'action': 'create_sample'}) == []


def test_snapshot_publish_attaches_artifacts_only_on_done(tmp_path, monkeypatch):
    from Virus_Platform_Core.ai_agent import executors as E
    _sandbox_stage(tmp_path, monkeypatch)
    seen = {}
    monkeypatch.setattr(E, '_publish', lambda r, event='ai_action': seen.update(r))
    monkeypatch.setattr(E, '_audit', lambda r: None)
    rec = {'id': 'act_2', 'action': 'rerun_stage', 'sample': 'S1',
           'stages': ['phylo'], 'params': {}, 'ts': 0}
    E._snapshot_publish(rec, 'done')
    assert [x['name'] for x in seen['artifacts']][:1] == ['tree.png']
    # 刷新页面走 /api/ai/actions 对账，靠 _snapshot 把卡片带回来
    assert 'artifacts' in E._snapshot(rec)
    E._snapshot_publish(rec, 'launched')
    assert 'artifacts' not in rec            # 非终态不背这个包袱
