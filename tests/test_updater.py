# -*- coding: utf-8 -*-
"""自动更新链单测（2026-11-06 修复配套）。

覆盖：sha256 fail-closed / full 档缺包不降级 / 换装 edition 一致性 /
apply_update.bat 换入与还原 build_info.json、mcp_server.py / Ed25519
清单签名验签 / 暂存目录原子替换（解压中断不留半成品）。

只写 pytest tmp_path 与隔离 run 根（conftest 已设 VP_RUN_ROOT），
不触碰真实安装根、真实 run/updates 与网络。
"""
import base64
import json
import os
import zipfile

import pytest

from Virus_Platform_Core import updater


# ------------------------------------------------------------------
# 工具
# ------------------------------------------------------------------
_SHA = 'a' * 64
_URLS = ['https://github.com/x/y/releases/download/v9.9.9/pkg.zip']


def _manifest(edition='minimal', version='9.9.9', sha=_SHA):
    pkg = {'size': 123, 'urls': list(_URLS)}
    if sha is not None:
        pkg['sha256'] = sha
    return {'version': version, 'notes_zh': '测试版本',
            'packages': {edition: pkg}}


def _patch_basics(monkeypatch, mf, edition='minimal'):
    monkeypatch.setattr(updater, 'fetch_manifest',
                        lambda force=False: (mf, None))
    monkeypatch.setattr(updater, 'current_version', lambda: '1.0.0')
    monkeypatch.setattr(updater, 'current_edition', lambda: edition)
    # 默认不启用签名，避免本机 platform.json 干扰
    monkeypatch.setattr(updater, '_sign_pubkey', lambda: '')


def _keypair():
    serialization = pytest.importorskip('cryptography.hazmat.primitives.'
                                        'serialization')
    ed25519 = pytest.importorskip('cryptography.hazmat.primitives.'
                                  'asymmetric.ed25519')
    sk = ed25519.Ed25519PrivateKey.generate()
    pub = sk.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return sk, pub


def _signed(mf, sk):
    sig = sk.sign(updater.canonical_manifest_bytes(mf))
    out = dict(mf)
    out['signature'] = {'alg': 'ed25519',
                        'value': base64.b64encode(sig).decode('ascii')}
    return out


def _make_update_zip(path, version='9.9.9', edition='minimal'):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('VirusPlatform.exe', b'exe')
        z.writestr('_internal/app.txt', 'x')
        z.writestr('mcp_server.py', '# mcp')
        z.writestr('build_info.json', json.dumps(
            {'version': version, 'edition': edition}))
    return path


def _make_stage(root, version='9.9.9', edition='minimal'):
    stage = root / 'staged'
    stage.mkdir()
    (stage / 'VirusPlatform.exe').write_bytes(b'exe')
    (stage / '_internal').mkdir()
    (stage / '_internal' / 'app.txt').write_text('x', encoding='utf-8')
    (stage / 'mcp_server.py').write_text('# mcp', encoding='utf-8')
    (stage / 'build_info.json').write_text(json.dumps(
        {'version': version, 'edition': edition}), encoding='utf-8')
    return stage


# ------------------------------------------------------------------
# 1. sha256 fail-closed
# ------------------------------------------------------------------
def test_download_package_rejects_empty_sha256(tmp_path):
    dest = str(tmp_path / 'pkg.zip')
    with pytest.raises(RuntimeError, match='sha256'):
        updater._download_package(_URLS, dest, expected_sha256='')
    assert not os.path.exists(dest)
    with pytest.raises(RuntimeError, match='sha256'):
        updater._download_package(_URLS, dest, expected_sha256='   ')


def test_check_update_missing_sha256_is_not_ok(monkeypatch):
    _patch_basics(monkeypatch, _manifest(sha=None))
    info = updater.check_update(force=True)
    assert info['ok'] is False
    assert info['has_update'] is False
    assert 'sha256' in info['error']


def test_check_update_malformed_sha256_is_not_ok(monkeypatch):
    _patch_basics(monkeypatch, _manifest(sha='not-a-sha256'))
    info = updater.check_update(force=True)
    assert info['ok'] is False
    assert info['has_update'] is False
    assert '格式非法' in info['error']


def test_check_update_missing_package_is_not_ok(monkeypatch):
    mf = {'version': '9.9.9', 'packages': {}}
    _patch_basics(monkeypatch, mf)
    info = updater.check_update(force=True)
    assert info['ok'] is False
    assert info['has_update'] is False
    assert 'minimal' in info['error']


# ------------------------------------------------------------------
# 2. full 档不静默降级为 minimal
# ------------------------------------------------------------------
def test_check_update_full_missing_package_rejected(monkeypatch):
    """full 用户遇到只有 minimal 包的清单：拒绝，不下发 minimal 包。"""
    _patch_basics(monkeypatch, _manifest(edition='minimal'),
                  edition='full')
    info = updater.check_update(force=True)
    assert info['ok'] is False
    assert info['has_update'] is False
    assert 'full' in info['error']
    # 关键：包信息不得回退成 minimal 的内容
    assert info['package']['sha256'] == ''
    assert info['package']['urls'] == []


def test_check_update_minimal_uses_minimal_package(monkeypatch):
    _patch_basics(monkeypatch, _manifest(edition='minimal'),
                  edition='minimal')
    info = updater.check_update(force=True)
    assert info['ok'] is True
    assert info['package']['sha256'] == _SHA


def test_download_package_rejects_untrusted_info():
    """ok=False 的清单（缺包/缺 sha256）不得进入下载流程。"""
    info = {'ok': False, 'has_update': True, 'latest': '9.9.9',
            'error': '清单缺少 full 版本档的更新包，拒绝把 full 版静默降级为 minimal'}
    with pytest.raises(RuntimeError, match='full'):
        updater.download_package(info)


# ------------------------------------------------------------------
# 3. 换装前 edition 一致性
# ------------------------------------------------------------------
def test_install_rejects_edition_mismatch(tmp_path, monkeypatch):
    stage = _make_stage(tmp_path, edition='minimal')
    monkeypatch.setattr(updater, '_is_frozen', lambda: True)
    monkeypatch.setattr(updater, 'current_staged',
                        lambda: (str(stage), '9.9.9'))
    monkeypatch.setattr(updater, 'current_edition', lambda: 'full')
    monkeypatch.setattr(updater, 'tasks_active', lambda: [])
    monkeypatch.setattr(updater, '_write_apply_bat',
                        lambda *a, **k: pytest.fail('跨档不应生成换装脚本'))
    r = updater.install_update(mode='none')
    assert r['ok'] is False
    assert '版本档' in r['error']


# ------------------------------------------------------------------
# 4. apply_update.bat：build_info.json / mcp_server.py 换入与还原
# ------------------------------------------------------------------
def test_apply_bat_swaps_and_restores_build_info(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, '_UPDATES_DIR', str(tmp_path / 'updates'))
    stage = _make_stage(tmp_path)
    bat = updater._write_apply_bat(str(stage), '9.9.9', 'none')
    with open(bat, encoding='utf-8', newline='') as f:
        content = f.read()

    # 防御检查：缺 build_info.json 不换装
    assert 'if not exist "%STAGED%\\build_info.json" goto restore' in content
    # 换入 build_info.json
    assert 'move /y "%STAGED%\\build_info.json" "%BUILDINFO%"' in content
    # 备份与还原 build_info.json（还原只处理本轮备份成功的项）
    assert 'ren "%BUILDINFO%" "build_info.json.old"' in content
    assert 'if "%BACKED_BUILDINFO%"=="1"' in content
    assert 'del /f /q "%BUILDINFO%"' in content
    assert 'ren "%BUILDINFO%.old" "build_info.json"' in content
    # mcp_server.py 的备份与还原（原先无 .old，换入失败无法还原）
    assert 'ren "%MCP%" "mcp_server.py.old"' in content
    assert 'if "%BACKED_MCP%"=="1"' in content
    assert 'ren "%MCP%.old" "mcp_server.py"' in content
    # _internal 换入失败还原前先删新件
    assert 'rd /s /q "%INTERNAL%"' in content
    # FAILED 行带失败步骤
    assert 'echo FAILED [%FAILSTEP%] >' in content
    # bat 必须是干净的 CRLF（不能出现 '\r\r\n' 双 CR）
    assert content.endswith('\r\n')
    assert '\n' not in content.replace('\r\n', '')
    with open(bat, 'rb') as f:
        raw = f.read()
    assert b'\r\r\n' not in raw
    assert raw.count(b'\r\n') == raw.count(b'\n')


# ------------------------------------------------------------------
# 5. Ed25519 清单签名
# ------------------------------------------------------------------
def test_manifest_signature_verified(monkeypatch):
    sk, pub = _keypair()
    mf = _signed(_manifest(), sk)
    monkeypatch.setattr(updater, '_sign_pubkey', lambda: pub.hex())
    ok, msg, state = updater._verify_manifest_signature(mf)
    assert ok is True
    assert state == 'verified'
    assert msg == ''


def test_manifest_signature_base64_pubkey(monkeypatch):
    sk, pub = _keypair()
    mf = _signed(_manifest(), sk)
    monkeypatch.setattr(
        updater, '_sign_pubkey',
        lambda: base64.b64encode(pub).decode('ascii'))
    ok, _msg, state = updater._verify_manifest_signature(mf)
    assert ok is True and state == 'verified'


def test_manifest_signature_tampered_rejected(monkeypatch):
    sk, pub = _keypair()
    mf = _signed(_manifest(), sk)
    mf['notes_zh'] = '被篡改的说明'
    monkeypatch.setattr(updater, '_sign_pubkey', lambda: pub.hex())
    ok, msg, state = updater._verify_manifest_signature(mf)
    assert ok is False
    assert state == 'invalid'
    assert '签名' in msg


def test_manifest_unsigned_rejected_when_pubkey_configured(monkeypatch):
    _, pub = _keypair()
    monkeypatch.setattr(updater, '_sign_pubkey', lambda: pub.hex())
    ok, msg, state = updater._verify_manifest_signature(_manifest())
    assert ok is False
    assert state == 'invalid'
    assert 'signature' in msg


def test_manifest_signature_optional_when_no_pubkey(monkeypatch):
    monkeypatch.setattr(updater, '_sign_pubkey', lambda: '')
    ok, msg, state = updater._verify_manifest_signature(_manifest())
    assert ok is True
    assert state == 'unsigned'
    assert '未启用签名校验' in msg


def test_check_update_verifies_signature(monkeypatch):
    sk, pub = _keypair()
    _patch_basics(monkeypatch, _signed(_manifest(), sk))
    monkeypatch.setattr(updater, '_sign_pubkey', lambda: pub.hex())
    info = updater.check_update(force=True)
    assert info['ok'] is True
    assert info['signature'] == 'verified'
    assert info['warning'] == ''

    # 篡改后 check_update 必须整体拒绝
    mf = _signed(_manifest(), sk)
    mf['notes_zh'] = 'tampered'
    _patch_basics(monkeypatch, mf)
    monkeypatch.setattr(updater, '_sign_pubkey', lambda: pub.hex())
    info = updater.check_update(force=True)
    assert info['ok'] is False
    assert '签名' in info['error']


# ------------------------------------------------------------------
# 6. 暂存原子性（解压中断不留半成品）
# ------------------------------------------------------------------
def _fake_download_writer(zip_path):
    def _fake(urls, dest, progress=None, expected_sha256=''):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(zip_path, 'rb') as src, open(dest, 'wb') as dst:
            dst.write(src.read())
        return dest
    return _fake


def test_download_package_stages_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, '_UPDATES_DIR', str(tmp_path / 'updates'))
    zp = _make_update_zip(str(tmp_path / 'src.zip'))
    monkeypatch.setattr(updater, '_download_package',
                        _fake_download_writer(zp))
    info = {'ok': True, 'has_update': True, 'latest': '9.9.9',
            'current': '1.0.0',
            'package': {'sha256': _SHA, 'urls': list(_URLS)}}
    stage, ver = updater.download_package(info)
    assert ver == '9.9.9'
    assert os.path.isdir(stage)
    assert os.path.isfile(os.path.join(stage, 'build_info.json'))
    assert not os.path.exists(stage + '.tmp')


def test_download_package_cleans_partial_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, '_UPDATES_DIR', str(tmp_path / 'updates'))
    zp = _make_update_zip(str(tmp_path / 'src.zip'))
    monkeypatch.setattr(updater, '_download_package',
                        _fake_download_writer(zp))

    def boom(zip_path, dest):
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, 'half.txt'), 'w', encoding='utf-8') as f:
            f.write('half')
        raise RuntimeError('模拟解压中断')

    monkeypatch.setattr(updater, '_safe_extract_zip', boom)
    info = {'ok': True, 'has_update': True, 'latest': '9.9.9',
            'current': '1.0.0',
            'package': {'sha256': _SHA, 'urls': list(_URLS)}}
    with pytest.raises(RuntimeError, match='模拟解压中断'):
        updater.download_package(info)
    stage = updater.staged_dir('9.9.9')
    assert not os.path.exists(stage)
    assert not os.path.exists(stage + '.tmp')


# ------------------------------------------------------------------
# 7. 手动安装遇忙：后台等待线程会继续重试（不再只是提示）
# ------------------------------------------------------------------
def test_request_install_returns_busy_when_background_alive(monkeypatch):
    class _Alive:
        def is_alive(self):
            return True

    monkeypatch.setattr(updater, '_install_thread', _Alive())
    r = updater.request_install('web')
    assert r == {'ok': True, 'downloading': True}


def test_wait_idle_then_install_retries(monkeypatch):
    calls = []

    def fake_install(mode='web'):
        calls.append(mode)
        if len(calls) < 3:
            return {'ok': False, 'awaiting_idle': True}
        return {'ok': True, 'version': '9.9.9'}

    monkeypatch.setattr(updater, 'install_update', fake_install)
    monkeypatch.setattr(updater.time, 'sleep', lambda _s: None)
    monkeypatch.setattr(updater, 'request_shutdown', lambda: None)
    r = updater._wait_idle_then_install('web')
    assert r['ok'] is True
    assert len(calls) == 3
