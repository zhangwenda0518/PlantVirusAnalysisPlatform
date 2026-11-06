# -*- coding: utf-8 -*-
"""2026-11-04 AI 回答反馈（👍👎）与「建议按钮」起草接口的专项测试。

覆盖三块：
  1) ai_agent/feedback.py：追加/读回、非法 verdict、非法 conv_id、
     msg_idx 非整数、note 截断、空文件 recent()==[]；
  2) POST/GET /api/ai/feedback：写读闭环、空体 400、坏 verdict 400；
  3) POST /api/ai/actions/create：create_sample 最小合法参数起草成功、
     缺 reason 400、未知 action 400（全部只产生待批执行单，不执行）。

隔离：与 test_ai_durable.py 同款——VP_RUN_ROOT 指向临时目录
（config 在导入期定死 RUN_ROOT），反馈 JSONL 与执行单审计盘都落在
临时根下，不会碰真实 run/。
"""
import os
import sys
import tempfile

import pytest

# 控制台编码兜底（Windows GBK 下 emoji 打印会炸）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ['VP_NO_RECOVER'] = '1'
os.environ['VP_RUN_ROOT'] = tempfile.mkdtemp(prefix='vp_fb_')

from Virus_Platform_Core.ai_agent import feedback  # noqa: E402


@pytest.fixture(scope='module')
def client():
    """进程内 Flask 客户端（模块级复用，避免反复建 app）。"""
    import app as appmod
    appmod.app.config['TESTING'] = True
    with appmod.app.test_client() as c:
        yield c


# ------------------------------------------------------------------
# 1) feedback.py 本体
# ------------------------------------------------------------------

def test_append_and_recent_roundtrip():
    r = feedback.append('conv-abc123', 3, 'down', '表格行数对不上')
    assert r['ok'] is True
    assert r['conv_id'] == 'conv-abc123'
    assert r['idx'] == 3 and r['verdict'] == 'down'
    assert r['note'] == '表格行数对不上'
    assert isinstance(r['ts'], str) and r['ts']

    # 原始记录可读回
    raw = feedback.items(5)
    hit = [x for x in raw if x.get('conv_id') == 'conv-abc123']
    assert hit and hit[-1]['idx'] == 3 and hit[-1]['verdict'] == 'down'

    # 人读格式：emoji + conv + 序号 + 理由 + (月-日 时:分)
    lines = feedback.recent(5)
    line = next(x for x in lines if 'conv-abc123' in x)
    assert line.startswith('👎')
    assert '#3' in line and '表格行数对不上' in line
    assert '(' in line and ')' in line


def test_append_rejects_bad_verdict_and_conv_id():
    assert feedback.append('conv-ok0001', 1, 'meh')['ok'] is False
    assert feedback.append('conv-ok0001', 1, 'UP')['ok'] is False
    assert feedback.append('../evil', 1, 'up')['ok'] is False
    assert feedback.append('short', 1, 'up')['ok'] is False
    assert feedback.append('conv-ok0001', 'abc', 'up')['ok'] is False


def test_append_truncates_note():
    feedback.append('conv-note001', 7, 'up', 'x' * 900)
    rec = feedback.items(1)[0]
    assert rec['conv_id'] == 'conv-note001'
    assert len(rec['note']) == 500


def test_recent_and_items_on_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback, '_FILE',
                        str(tmp_path / 'not_yet.jsonl'))
    assert feedback.items() == []
    assert feedback.recent() == []


# ------------------------------------------------------------------
# 2) /api/ai/feedback
# ------------------------------------------------------------------

def test_api_feedback_post_then_get(client):
    r = client.post('/api/ai/feedback', json={
        'conv_id': 'conv-api001', 'msg_idx': 2, 'verdict': 'up',
        'note': '回答很清楚'})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body['ok'] is True and body['conv_id'] == 'conv-api001'

    r = client.get('/api/ai/feedback?limit=50')
    assert r.status_code == 200
    items = r.get_json()['items']
    assert any(x.get('conv_id') == 'conv-api001' and x.get('idx') == 2
               for x in items)


def test_api_feedback_rejects_empty_and_bad_verdict(client):
    r = client.post('/api/ai/feedback', json={})
    assert r.status_code == 400
    r = client.post('/api/ai/feedback', json={
        'conv_id': 'conv-api002', 'msg_idx': 1, 'verdict': 'meh'})
    assert r.status_code == 400


# ------------------------------------------------------------------
# 3) /api/ai/actions/create（建议按钮 → 待批执行单）
# ------------------------------------------------------------------

def test_api_action_create_minimal_create_sample(client, tmp_path):
    fastq = tmp_path / 'UnitFb_R1.fastq'
    fastq.write_text('@r\nACGT\n+\nIIII\n', encoding='ascii')
    r = client.post('/api/ai/actions/create', json={
        'action': 'create_sample',
        'params': {'sample': 'UnitFeedback', 'r1': str(fastq)},
        'reason': '单测：验证建议按钮可直接起草执行单'})
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    assert d['created'] is True
    assert d['action'] == 'create_sample'
    assert d['status'] == 'pending'
    assert d['id'].startswith('act_')
    assert d['sample'] == 'UnitFeedback'
    assert d['note'] == '执行单已创建，等待用户在对话坞批准；批准前不会运行'
    assert d['params']['r1'] == str(fastq)

    # source 口径与对话坞内提案区分开；收尾驳回，不留待批单
    from Virus_Platform_Core.ai_agent import executors
    rec = executors.get_action(d['id'])
    assert rec and rec['source'] == 'chat_next'
    executors.resolve(d['id'], 'reject')


def test_api_action_create_requires_reason(client):
    r = client.post('/api/ai/actions/create', json={
        'action': 'create_sample', 'params': {'r1': 'x'}})
    assert r.status_code == 400
    r = client.post('/api/ai/actions/create', json={})
    assert r.status_code == 400


def test_api_action_create_rejects_unknown_action(client):
    r = client.post('/api/ai/actions/create', json={
        'action': 'rm_rf_everything', 'reason': '单测：非法动作必须被挡'})
    assert r.status_code == 400
