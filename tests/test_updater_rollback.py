# -*- coding: utf-8 -*-
"""换装回滚/清理链单测（2026-11-06 测试覆盖补强）。

覆盖 `_cleanup_stale` 三分支：FAILED 标记清除（新旧格式）、OK 标记清
.old 备份、过期 staged/zip/part 残件清理。其中残件清理分支此前因缩进
错误（elif 嵌在 isdir 块内）从未生效，本组测试锁住修复后行为。
"""
import os

from Virus_Platform_Core import updater


def _patch(tmp_path, monkeypatch, cur='1.0.0'):
    updates = tmp_path / 'updates'
    updates.mkdir()
    monkeypatch.setattr(updater, '_UPDATES_DIR', str(updates))
    monkeypatch.setattr(updater, 'current_version', lambda: cur)
    monkeypatch.setattr(updater, 'PLATFORM_ROOT', str(tmp_path))
    return updates


def test_failed_marker_new_format_cleared(tmp_path, monkeypatch):
    updates = _patch(tmp_path, monkeypatch)
    m = updates / 'last_apply.txt'
    m.write_text('FAILED [步骤3] 换入 _internal 失败', encoding='utf-8')
    updater._cleanup_stale()
    assert not m.exists()


def test_failed_marker_old_format_cleared(tmp_path, monkeypatch):
    updates = _patch(tmp_path, monkeypatch)
    m = updates / 'last_apply.txt'
    m.write_text('FAILED', encoding='utf-8')
    updater._cleanup_stale()
    assert not m.exists()


def test_ok_marker_clears_old_backups(tmp_path, monkeypatch):
    updates = _patch(tmp_path, monkeypatch)
    f_old = tmp_path / 'VirusPlatform.exe.old'
    f_old.write_bytes(b'exe')
    d_old = tmp_path / '_internal.old'
    (d_old / 'webapp').mkdir(parents=True)
    (d_old / 'webapp' / 'x.txt').write_text('x', encoding='utf-8')
    (updates / 'last_apply.txt').write_text('OK', encoding='utf-8')
    updater._cleanup_stale()
    assert not f_old.exists() and not d_old.exists()
    assert not (updates / 'last_apply.txt').exists()


def test_ok_marker_keeps_current_files(tmp_path, monkeypatch):
    updates = _patch(tmp_path, monkeypatch)
    keep = tmp_path / 'VirusPlatform.exe'
    keep.write_bytes(b'exe-new')
    (updates / 'last_apply.txt').write_text('OK', encoding='utf-8')
    updater._cleanup_stale()
    assert keep.exists()


def test_stale_staged_dirs_and_parts(tmp_path, monkeypatch):
    updates = _patch(tmp_path, monkeypatch, cur='1.0.0')
    stale = updates / '1.0.0'
    stale.mkdir()
    (stale / 'x.bin').write_bytes(b'x')
    newer = updates / '9.9.9'
    newer.mkdir()
    zipf = updates / 'pkg.zip'
    zipf.write_bytes(b'z')
    part = updates / 'pkg.zip.part'
    part.write_bytes(b'p')
    junk = updates / 'notes'
    junk.mkdir()
    updater._cleanup_stale()
    assert not stale.exists()
    assert newer.exists()
    assert not zipf.exists()
    assert not part.exists()
    assert not junk.exists()
