# -*- coding: utf-8 -*-
"""本地 CSRF / DNS-rebinding 防护（app._guard_local_origin）单测。

威胁模型：服务只监听 127.0.0.1，但用户浏览器里的恶意网页可以
  ① 跨站 POST 平台的破坏性端点（CSRF，浏览器必带跨站 Origin）；
  ② 把攻击者域名 DNS rebinding 到 127.0.0.1 后读取响应（Host 是攻击者域名）。
两道校验必须分别拦下这两种请求，且不影响正常本机访问。
"""
import pytest


@pytest.fixture()
def client():
    import app as app_module
    app_module.app.config['TESTING'] = True
    with app_module.app.test_client() as c:
        yield c


def test_home_ok(client):
    r = client.get('/')
    assert r.status_code == 200


def test_cross_site_origin_rejected(client):
    """① 恶意网页跨站 POST：Origin 是外部域 → 403。"""
    r = client.post('/api/global/reset',
                    json={'confirm': 'reset'},
                    headers={'Origin': 'https://evil.example.com'})
    assert r.status_code == 403


def test_cross_site_referer_rejected(client):
    r = client.get('/api/samples',
                   headers={'Referer': 'https://evil.example.com/page'})
    assert r.status_code == 403


def test_rebound_host_rejected(client):
    """② DNS rebinding：Host 变成攻击者域名 → 403。"""
    r = client.get('/', headers={'Host': 'evil.example.com'})
    assert r.status_code == 403


def test_same_origin_loopback_allowed(client):
    """正常访问：Host 是回环地址，无 Origin（非浏览器客户端）→ 放行。
    （端点本身可能因参数/状态返回 4xx，但绝不能是 403 的防护拦截。）"""
    r = client.get('/api/samples', headers={'Host': '127.0.0.1:8765'})
    assert r.status_code != 403
    r2 = client.post('/api/global/reset', json={},
                     headers={'Origin': 'http://127.0.0.1:8765'})
    assert r2.status_code != 403


def test_download_file_route_resolves_symlink(client, tmp_path):
    """批次目录里的符号链接不能把平台外文件带出去（realpath 越界 → 404）。"""
    import os
    import shutil
    import uuid

    from Virus_Platform_Core.public_data import _batch_root

    root = _batch_root()
    os.makedirs(root, exist_ok=True)
    bdir = os.path.join(root, 'pytest_' + uuid.uuid4().hex[:8])
    os.makedirs(bdir, exist_ok=True)
    outside = tmp_path / 'outside_secret.txt'
    outside.write_text('secret', encoding='utf-8')
    try:
        try:
            os.symlink(str(outside), os.path.join(bdir, 'evil.fastq.gz'))
        except (OSError, NotImplementedError):
            pytest.skip('当前系统不允许创建符号链接')
        (tmp_path / 'ok.fastq.gz').write_bytes(b'x')
        os.replace(str(tmp_path / 'ok.fastq.gz'),
                   os.path.join(bdir, 'ok.fastq.gz'))
        bid = os.path.basename(bdir)
        assert client.get(f'/downloads/{bid}/ok.fastq.gz').status_code == 200
        # 链接目标在批次目录之外 → 必须 404，而不是把 secret 发出去
        assert client.get(f'/downloads/{bid}/evil.fastq.gz').status_code == 404
    finally:
        shutil.rmtree(bdir, ignore_errors=True)
