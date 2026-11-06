# -*- coding: utf-8 -*-
"""AI 分析台的文件预览接口 + 附件摘要（2026-10-30）。

/api/fs/read|raw 只读平台目录内的文件；越界路径必须 400。
附件摘要只读用户显式附加的文件，文本给预览、缺失给可读错误。
"""
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 与 test_ai_durable / test_ai_feedback 同款自隔离：RUN_ROOT 指向自己的
# 临时目录。否则裸 pytest 多文件同进程时，前面 AI 测试 import 期设的
# VP_RUN_ROOT 会被本文件继承，/api/fs/roots 按存在性过滤后 tool 根丢失。
os.environ['VP_NO_RECOVER'] = '1'
os.environ['VP_RUN_ROOT'] = tempfile.mkdtemp(prefix='vp_fsp_')

from flask import Flask  # noqa: E402


@pytest.fixture()
def client():
    from Virus_Platform_Core.config import DIRS
    # 端点按目录存在性过滤根；临时根下这些目录未建会让断言空挂。
    # 按 config 实际生效的 DIRS 建齐（兜住 config 已被前序测试导入的情况）。
    for _k in ('results', 'tool_runs', 'downloads'):
        _d = DIRS.get(_k)
        if _d:
            os.makedirs(_d, exist_ok=True)
    import Virus_Platform_Core.web.pages as pg
    app = Flask(__name__)
    app.register_blueprint(pg.bp)
    return app.test_client()


def test_ai_artifacts_endpoint_maps_text_to_cards(client):
    """旧历史回填：正文里的平台内路径 → 产物卡片；系统路径必须被丢弃。"""
    import os
    from Virus_Platform_Core.config import PLATFORM_ROOT
    d = os.path.join(PLATFORM_ROOT, 'run', 'tmp')
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, '_t_art_card.html')
    with open(p, 'w', encoding='utf-8') as fh:
        fh.write('<!doctype html><p>card</p>')
    r = None
    try:
        r = client.post('/api/ai/artifacts',
                        json={'texts': ['报告在 %s ，另见 C:\\Windows\\win.ini' % p]})
        assert r.status_code == 200
        res = r.get_json()['results']
        assert len(res) == 1
        names = [x['name'] for x in res[0]]
        assert '_t_art_card.html' in names
        assert all('win.ini' not in n for n in names)
        assert res[0][0]['kind'] == 'html'
        assert '/api/fs/raw?path=' in res[0][0]['url']
    finally:
        try:
            r.close()
        except Exception:
            pass
        try:
            os.remove(p)
        except OSError:
            pass


def test_ai_artifacts_endpoint_rejects_bad_payload(client):
    assert client.post('/api/ai/artifacts', json={'texts': 'nope'}).status_code == 400
    r = client.post('/api/ai/artifacts', json={'text': 'nothing here'})
    assert r.status_code == 200 and r.get_json()['results'] == [[]]


def test_read_text_in_platform(client):
    r = client.get('/api/fs/read?path=examples/example_acmv.fasta')
    assert r.status_code == 200
    d = r.get_json()
    assert d['size'] > 0
    assert d['text'].startswith('>')
    assert d['truncated'] is False


def test_read_rejects_outside_platform(client):
    r = client.get('/api/fs/read?path=../../Windows/win.ini')
    assert r.status_code == 400
    assert '路径' in r.get_json()['error']


def test_read_rejects_directory(client):
    r = client.get('/api/fs/read?path=examples')
    assert r.status_code == 400
    assert '不是文件' in r.get_json()['error']


def test_roots_endpoint(client):
    r = client.get('/api/fs/roots')
    assert r.status_code == 200
    ids = [x['id'] for x in r.get_json()]
    assert 'sample' in ids and 'tool' in ids and 'examples' in ids


def test_raw_serves_bytes(client):
    r = client.get('/api/fs/raw?path=examples/example_acmv.fasta')
    assert r.status_code == 200
    assert r.data.startswith(b'>ACMV')


def test_raw_html_is_inline_so_iframe_can_preview(client):
    """对话坞的 HTML 预览用 <iframe src=/api/fs/raw?...>。

    send_file 若带 attachment 头，浏览器会改成下载，iframe 只剩空白——
    用户会以为预览坏了。这里钉住 inline + text/html。
    """
    import os
    from Virus_Platform_Core.config import PLATFORM_ROOT
    d = os.path.join(PLATFORM_ROOT, 'run', 'tmp')
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, '_t_inline.html')
    with open(p, 'w', encoding='utf-8') as fh:
        fh.write('<!doctype html><meta charset="utf-8"><p>hi</p>')
    r = None
    try:
        r = client.get('/api/fs/raw', query_string={'path': p})
        assert r.status_code == 200
        assert 'html' in r.headers.get('Content-Type', '')
        disp = r.headers.get('Content-Disposition', '')
        assert 'attachment' not in disp.lower(), disp
        assert r.data.startswith(b'<!doctype html>')
    finally:
        # send_file 在 Windows 上会握住文件句柄：不先 close 就删不掉（实测残留）
        try:
            r.close()
        except Exception:
            pass
        try:
            os.remove(p)
        except OSError:
            pass


def test_attach_digest_preview_and_missing():
    from Virus_Platform_Core.ai_agent.chat import _attach_digest
    d = _attach_digest(['examples/example_acmv.fasta', 'no/such/file.fa'])
    assert d[0]['name'] == 'example_acmv.fasta'
    assert d[0]['size'] > 0
    assert d[0]['preview'].startswith('>')
    assert 'error' in d[1]


def test_attach_digest_caps_count():
    from Virus_Platform_Core.ai_agent.chat import _ATTACH_MAX, _attach_digest
    paths = ['examples/example_acmv.fasta'] * (_ATTACH_MAX + 3)
    assert len(_attach_digest(paths)) == _ATTACH_MAX


def test_workspace_digest_lists_files(tmp_path):
    (tmp_path / 'A_R1.fastq.gz').write_bytes(b'x' * 10)
    (tmp_path / 'sub').mkdir()
    from Virus_Platform_Core.ai_agent.chat import _workspace_digest
    d = _workspace_digest(str(tmp_path))
    names = {e['name']: e['type'] for e in d['entries']}
    assert names.get('A_R1.fastq.gz') == 'file'
    assert names.get('sub') == 'dir'
    assert d['truncated'] is False
    f = [e for e in d['entries'] if e['name'] == 'A_R1.fastq.gz'][0]
    assert f['size'] == 10 and f['path'].endswith('A_R1.fastq.gz')


def test_workspace_digest_missing():
    from Virus_Platform_Core.ai_agent.chat import _workspace_digest
    assert _workspace_digest('') is None
    assert 'error' in _workspace_digest('Z:/definitely/not/here')
