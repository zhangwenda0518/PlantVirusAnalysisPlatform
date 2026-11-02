# -*- coding: utf-8 -*-
"""AI 分析台的文件预览接口 + 附件摘要（2026-10-30）。

/api/fs/read|raw 只读平台目录内的文件；越界路径必须 400。
附件摘要只读用户显式附加的文件，文本给预览、缺失给可读错误。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from flask import Flask  # noqa: E402


@pytest.fixture()
def client():
    import Virus_Platform_Core.web.pages as pg
    app = Flask(__name__)
    app.register_blueprint(pg.bp)
    return app.test_client()


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
