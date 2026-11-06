# -*- coding: utf-8 -*-
"""MCP 工具参数守卫与路径越界单测（2026-11-06 测试覆盖补强）。

_check_mcp_tools（批量套件）已覆盖工具注册/归档回读/越界读取；本文件把
同类守卫落成 pytest 单测：_safe_rel / _read_run_file_impl / _sample_list_impl
直接调 impl（技能卡 §4b「薄包装+实现分离」专为测试留的口）。
"""
import json
import os

import pytest

mcp = pytest.importorskip('mcp_server')


# ------------------------------------------------------------------
# _safe_rel：相对路径安全化
# ------------------------------------------------------------------
def test_safe_rel_rejects_bad_paths():
    # 只测 _safe_rel 自身契约（拒绝 .. 段与空值）；绝对路径由下游
    # _read_text_under 的前缀检查拦截（见 rejects_traversal 用例）。
    for bad in ('../secrets.txt', 'a/../../b', '', '   ', '..'):
        with pytest.raises(ValueError):
            mcp._safe_rel(bad)


def test_safe_rel_accepts_normal():
    assert mcp._safe_rel('logs/x.log') == 'logs/x.log'
    assert mcp._safe_rel('/lead-slash.txt') == 'lead-slash.txt'
    assert mcp._safe_rel('a\\b.txt') == 'a/b.txt'


# ------------------------------------------------------------------
# _read_run_file_impl：运行名与路径双重守卫
# ------------------------------------------------------------------
def test_read_run_file_rejects_invalid_run_name():
    for bad in ('../evil', 'a/b', 'a b', ''):
        with pytest.raises(ValueError, match='无效的运行名'):
            mcp._read_run_file_impl(bad, 'x.txt', 8)


def test_read_run_file_rejects_missing_run(tmp_path):
    with pytest.raises(ValueError, match='运行不存在'):
        mcp._read_run_file_impl('no_such_run_xyz', 'x.txt', 8)


def test_read_run_file_rejects_traversal(tmp_path, monkeypatch):
    # 假运行目录必须建在隔离 run 根下：check_path 只放行平台/输出目录内
    from Virus_Platform_Core.config import DIRS
    base = os.path.join(DIRS['tool_runs'], 'pg_run_xyz')
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, 'x.txt'), 'w', encoding='utf-8') as f:
        f.write('data')
    monkeypatch.setattr(mcp, 'run_real_dir', lambda name: base)
    for bad in ('../secrets.txt', 'a/../../b', 'C:/abs.txt'):
        with pytest.raises(ValueError):
            mcp._read_run_file_impl('pg_run_xyz', bad, 8)
    ok = mcp._read_run_file_impl('pg_run_xyz', 'x.txt', 8)
    assert ok is not None
    import shutil
    shutil.rmtree(base, ignore_errors=True)


def test_read_run_file_rejects_binary(tmp_path, monkeypatch):
    from Virus_Platform_Core.config import DIRS
    base = os.path.join(DIRS['tool_runs'], 'pg_run_bin')
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, 'plot.png'), 'wb') as f:
        f.write(b'PNG')
    monkeypatch.setattr(mcp, 'run_real_dir', lambda name: base)
    with pytest.raises(ValueError, match='二进制'):
        mcp._read_run_file_impl('pg_run_bin', 'plot.png', 8)
    import shutil
    shutil.rmtree(base, ignore_errors=True)


# ------------------------------------------------------------------
# _sample_list_impl：坏样品只降级单条目（input_error 可见）
# ------------------------------------------------------------------
def test_sample_list_input_error_visible(monkeypatch):
    # load_sample_input 自身对坏 manifest 有兜底，这里直接令其抛错，
    # 验证列表条目的 input_error 可见性（列表不因单样品 500）。
    import shutil
    from Virus_Platform_Core.config import DIRS
    import Virus_Platform_Core.pipeline as pipeline_mod
    bad = os.path.join(DIRS['results'], '样品_坏manifest')

    def _boom(_d):
        raise RuntimeError('boom')

    monkeypatch.setattr(pipeline_mod, 'load_sample_input', _boom)
    os.makedirs(bad, exist_ok=True)
    try:
        out = mcp._sample_list_impl(limit=100, offset=0)
        entry = next(e for e in out['samples']
                     if e['sample'] == '样品_坏manifest')
        assert entry.get('input_error') == 'RuntimeError: boom'
    finally:
        shutil.rmtree(bad, ignore_errors=True)


def test_sample_list_limit_clamp():
    out = mcp._sample_list_impl(limit=-5, offset=0)
    assert out['count'] >= 0
    assert out['count'] <= 1  # max(1, min(limit,100)) → 至少 1、至多 100
