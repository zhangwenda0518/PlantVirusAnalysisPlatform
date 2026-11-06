# -*- coding: utf-8 -*-
"""平台自举层的两个「会静默失效」的缺陷回归（2026-11-02 全面测试发现）。

① srna-target 探测缺失 —— 模块声明了这个键，但 detect_tools() 的候选表
   里没有条目，于是 cfg.tool_status() 永远取到空串，mirna 模块 100% 判缺，
   哪怕 3rd/tools/srna-target/ 下三套引擎一应俱全。后果是 mirna 永远跑不起来，
   且报错文案指向「插件源未提供」，把排障引向完全错误的方向。

② vp_ascii_root 自引用环 —— hmm_annot._ascii_path() 用
   tempfile.gettempdir() 建 junction，而 ensure_usable_tempdir() 会把
   tempdir 重定向到 <平台根>/run/tmp。junction 于是落在被链接树的内部，
   任何遍历平台根的操作都会无限递归，路径嵌套到撞上 MAX_PATH 后，
   所有文件操作报 FileNotFoundError（具体症状：selfcheck 第⑥项依赖审计
   直接抛异常，整项失效）。

两个都只做只读探测/路径计算，不建任务、不落产物。
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from Virus_Platform_Core import config as cfg_mod   # noqa: E402
from Virus_Platform_Core import hmm_annot           # noqa: E402


# ---------------------------------------------------------------- srna-target

def test_srna_target_is_detected_when_installed():
    """三件套齐备时必须探测到；缺一件必须整体判不可用。

    只命中 miRanda 就放行是危险的：bin_engines.run_* 在引擎缺失时静默
    返回 None，共识等级会悄悄失真（LOW→VERY HIGH）却不报错。
    """
    base = os.path.join(cfg_mod.PLATFORM_ROOT, '3rd', 'tools', 'srna-target')
    need = [
        os.path.join(base, 'miranda', 'miranda.exe'),
        os.path.join(base, 'rnahybrid', 'RNAhybrid.exe'),
        os.path.join(base, 'pita', 'pita_prediction.pl'),
        os.path.join(base, 'pita', 'perl', 'bin', 'perl.exe'),
    ]
    have_all = all(os.path.isfile(p) for p in need)
    assert cfg_mod._srna_target_complete() is have_all
    if have_all:
        assert cfg_mod.detect_tools().get('srna-target'), \
            '三件套齐备却没探测到 srna-target（探测表漏登记会复现该缺陷）'
    if not have_all:
        assert not cfg_mod.detect_tools().get('srna-target'), \
            '引擎不全却判为可用，会让 mirna 静默降级'


def test_srna_target_partial_marks_missing(tmp_path):
    """缺任一引擎 → 整体判不可用（用临时目录注入时，不碰真实安装）。"""
    base = tmp_path / 'srna-target'
    paths = [
        base / 'miranda' / 'miranda.exe',
        base / 'rnahybrid' / 'RNAhybrid.exe',
        base / 'pita' / 'pita_prediction.pl',
        base / 'pita' / 'perl' / 'bin' / 'perl.exe',
    ]
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'x')
    assert cfg_mod._srna_target_complete(str(base)) is True
    paths[1].unlink()                      # 拿掉 RNAhybrid
    assert cfg_mod._srna_target_complete(str(base)) is False


def test_srna_target_declared_by_module_has_detect_entry():
    """module_deps 声明的每个工具键，detect_tools() 都必须能给出条目。

    这正是本缺陷的通用形态：声明了却探测不到 = 该模块永远判缺。
    """
    from Virus_Platform_Core import module_deps
    tools = cfg_mod.detect_tools()
    declared = set()
    for names in module_deps.MODULE_TOOLS.values():
        # 允许「任一组」（嵌套列表）：展平后再逐键校验
        declared.update(module_deps._flat_keys(names))
    # 用 tool_status() 而不是 tools：插件层「填缺」后的最终视图才是执行层
    # 真正看到的；tools 只是本机探测结果。
    status = cfg_mod.get_config().tool_status()
    unknown = sorted(k for k in declared if k not in tools and k not in status)
    assert not unknown, f'模块声明了探测表里没有的工具键：{unknown}'


# ------------------------------------------------------------- ascii 自引用环


def test_usable_tempdir_never_prefers_platform_root(monkeypatch, tmp_path):
    """系统临时目录可用时，ensure_usable_tempdir 绝不能选平台根内部。

    这是 2026-11-02 那个自引用环的**更深的根因**：原实现写的是
    `if _usable(tempfile.tempdir)`，而模块首次导入时 `tempfile.tempdir` 还是
    None，`_usable(None)` 直接返回 False —— 于是**哪怕系统临时目录完全可用**，
    也会一路落到 `run/tmp` 兜底分支，把 tempfile.gettempdir() 改到平台根内部。
    `hmm_annot._ascii_path()` 用它建 junction，链接就落进被链接树自己，
    形成自引用环（selfcheck 第⑥项依赖审计撞 MAX_PATH）。

    修法：没显式设过 tempfile.tempdir 时问环境变量要真值（_sys_tempdir）。
    """
    outside = tmp_path / 'sys_temp'
    outside.mkdir()
    monkeypatch.setattr(cfg_mod.tempfile, 'tempdir', None, raising=False)
    monkeypatch.setenv('TEMP', str(outside))
    monkeypatch.delenv('TMPDIR', raising=False)
    monkeypatch.delenv('TMP', raising=False)
    got = cfg_mod.ensure_usable_tempdir()
    assert got, '系统临时目录可用时必须返回它，不能返回 None'
    root = os.path.normcase(os.path.abspath(cfg_mod.PLATFORM_ROOT))
    assert not os.path.normcase(os.path.abspath(got)).startswith(root + os.sep), \
        f'可用系统临时目录时仍选了平台根内部：{got}（会造出自引用环）'


def test_ascii_link_never_inside_platform_root(monkeypatch, tmp_path):
    """junction 落在被链接树内部时，必须弃用并回退，绝不造环。"""
    monkeypatch.setattr(hmm_annot, '_ASCII_LINK', None)
    # 把 tempdir 重定向到「平台根内部」，精确复现 ensure_usable_tempdir()
    # 在系统临时目录不可用时的结局。
    inside = os.path.join(cfg_mod.PLATFORM_ROOT, 'run', 'tmp')
    monkeypatch.setattr(hmm_annot.__dict__.get('tempfile', None) or __import__('tempfile'),
                        'tempdir', inside, raising=False)
    outside = tmp_path / 'outside_temp'
    outside.mkdir()
    monkeypatch.setenv('TEMP', str(outside))
    path = os.path.join(cfg_mod.PLATFORM_ROOT, '数据库', 'x.hmm')
    got = hmm_annot._ascii_path(path)
    link = hmm_annot._ASCII_LINK
    if link:
        assert not os.path.normcase(os.path.abspath(link)).startswith(
            os.path.normcase(os.path.abspath(cfg_mod.PLATFORM_ROOT)) + os.sep), \
            f'ASCII 链接落在平台根内部，会形成自引用环：{link}'
    else:
        # 不能建链接时宁可原样返回，也不能返回一个环内路径
        assert got == path
    monkeypatch.setattr(hmm_annot, '_ASCII_LINK', None)


def test_ascii_link_fallback_inside_root_is_rejected(monkeypatch):
    """回归（2026-11-06）：回退分支 TEMP/TMP 也在平台根内时不得建 junction。

    首选 link 取自 tempfile.gettempdir()，回退取自 TEMP/TMP。系统 temp 不可用
    时 config.ensure_usable_tempdir() 会把 tempdir **和** TEMP/TMP 一并改写成
    <平台根>/run/tmp，于是回退拿到的仍是平台根内路径。修复前只对首选分支判环，
    回退后不再判 → `mklink /J <根>/run/tmp/vp_ascii_root <根>` 造出自引用环。
    修复后必须直接返回原路径，且绝不调用 mklink。
    """
    monkeypatch.setattr(hmm_annot, '_ASCII_LINK', None)
    inside_tmp = os.path.join(cfg_mod.PLATFORM_ROOT, 'run', 'tmp')
    monkeypatch.setattr(tempfile, 'tempdir', inside_tmp, raising=False)
    monkeypatch.setenv('TEMP', inside_tmp)
    monkeypatch.setenv('TMP', inside_tmp)

    calls = []

    def _fake_run(args, *a, **kw):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, 'run', _fake_run)
    path = os.path.join(cfg_mod.PLATFORM_ROOT, '数据库', 'z.hmm')
    try:
        got = hmm_annot._ascii_path(path)
        assert got == path, '不能返回环内的 ASCII 别名路径'
        assert hmm_annot._ASCII_LINK == '', '回退仍落在平台根内时必须放弃建链'
        assert not calls, f'不应调用 mklink 造环：{calls}'
    finally:
        monkeypatch.setattr(hmm_annot, '_ASCII_LINK', None)


def test_existing_reparse_point_inside_root_is_rejected(monkeypatch):
    """平台根内若已存在坏的 vp_ascii_root 链接，不得再被采用。"""
    real = os.path.join(cfg_mod.PLATFORM_ROOT, 'run', 'tmp', 'vp_ascii_root')
    if not os.path.isdir(real):
        import pytest
        pytest.skip('本机 run/tmp/vp_ascii_root 不存在，跳过')
    monkeypatch.setattr(hmm_annot, '_ASCII_LINK', None)
    got = hmm_annot._ascii_path(os.path.join(cfg_mod.PLATFORM_ROOT, '数据库', 'y.hmm'))
    assert '/run/tmp/vp_ascii_root/' not in got.replace('\\', '/'), \
        '仍在使用环内的 ASCII 链接'
    monkeypatch.setattr(hmm_annot, '_ASCII_LINK', None)
