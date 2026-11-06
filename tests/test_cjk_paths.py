# -*- coding: utf-8 -*-
"""中文 / 非 ASCII 路径单测（2026-11-06 测试覆盖补强）。

平台口径「中文路径已自动处理」此前没有 pytest 层锁：本文件用隔离 run 根
（conftest 已设 VP_RUN_ROOT）锁两件事：safe_open 读写、输出根含中文时的
DIRS 平移 / 落盘回读 / 文件落盘全链路。
"""
import os

from Virus_Platform_Core import config as cfgmod
from Virus_Platform_Core import utils


def test_safe_open_cjk_roundtrip():
    """平台内中文目录读写（走隔离 run 根，符合测试隔离契约）。"""
    import shutil
    from Virus_Platform_Core.config import DIRS
    d = os.path.join(DIRS['results'], '中文样品αβ')
    os.makedirs(d, exist_ok=True)
    fp = os.path.join(d, '序列信息.txt')
    try:
        with utils.safe_open(fp, 'w') as f:
            f.write('第一行\n中文内容 ✓\n')
        with utils.safe_open(fp, 'r') as f:
            assert '中文内容 ✓' in f.read()
        assert os.path.isfile(fp)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_output_root_cjk_roundtrip(tmp_path, monkeypatch):
    """输出根含中文：DIRS 平移、platform.json 回读、文件落盘三层走通。

    pytest 临时目录在 C:\\Users\\...\\Temp 下，会被 _ensure_not_system_dir
    的系统目录守卫拒绝——本用例测的是中文路径处理不是守卫本身，补丁放行。"""
    monkeypatch.setattr(cfgmod, '_ensure_not_system_dir', lambda path: None)
    monkeypatch.setattr(cfgmod, 'PLATFORM_ROOT', str(tmp_path))
    monkeypatch.setattr(cfgmod, 'CONFIG_FILE', str(tmp_path / 'platform.json'))
    monkeypatch.setattr(cfgmod, '_OUTPUT_ROOT', '')
    monkeypatch.setattr(cfgmod, '_ACTIVE_HOST_DB', '')
    # apply_output_root 在 VP_RUN_ROOT 激活时故意不生效（测试隔离契约，
    # config.py:685）——本用例要测真实切换链，临时移除该环境变量。
    monkeypatch.delenv('VP_RUN_ROOT', raising=False)
    c = cfgmod.Config()
    cjk_root = os.path.join(str(tmp_path), '输出_根α')
    c.set_output_root(cjk_root)
    from Virus_Platform_Core.config import DIRS
    assert DIRS['results'] == os.path.join(cjk_root, 'results')
    assert c.output_root == cjk_root
    probe = os.path.join(DIRS['tool_runs'], '_tmp', '探针.txt')
    os.makedirs(os.path.dirname(probe), exist_ok=True)
    with utils.safe_open(probe, 'w') as f:
        f.write('ok')
    assert os.path.isfile(probe)
    c.save()
    c2 = cfgmod.Config()
    assert c2.output_root == cjk_root, '中文输出根必须能落盘回读'
    c.set_output_root('')
    assert DIRS['results'] != os.path.join(cjk_root, 'results')
