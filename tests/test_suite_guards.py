# -*- coding: utf-8 -*-
"""tests/_run_all.py 三条机器守卫的自测（G1 并发/实例、G2 归账、G3 复验）。

为什么要有这个文件：这三条规矩此前只写在 docs/DEVELOPMENT_NOTES.md 里，
靠人记得住；结果两天内同一套件分数摇摆 5 次（98/107 → 106/107 → 104/109 →
109/109 → 107/107），每次都要人去手工复跑来判"到底真红还是环境红"。
把它们做成函数并用测试锁住行为，才算真的"机器可拦"。

这里只测**纯判定逻辑**（不跑真实套件、不启动平台、不碰真实 run/），
所以是毫秒级、可反复跑；端到端效果由批量跑本身体现。
"""
import io
import os
import socket
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _run_all as ra                                        # noqa: E402

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- G3 判定
@pytest.mark.parametrize('first, retry, skipped, expect', [
    (True, None, False, 'PASS'),        # 首跑就过
    (False, True, False, 'FLAKY'),      # 首跑红、单跑绿 → 不是真失败
    (False, False, False, 'FAIL'),      # 两次都红 → 真失败
    (False, None, True, 'FAIL'),        # 关掉复验，只能按首跑算
])
def test_judge_retry_semantics(first, retry, skipped, expect):
    assert ra._judge(first, retry, retry_skipped=skipped) == expect


@pytest.mark.parametrize('timed_out, rc, expect', [
    (False, 0, 'PASS'),
    (False, 1, 'FAIL'),
    (True, None, 'TIMEOUT'),
])
def test_status_of(timed_out, rc, expect):
    assert ra._status_of(timed_out, rc) == expect


def test_no_retry_switch(monkeypatch):
    monkeypatch.delenv('VP_NO_RETRY', raising=False)
    assert ra._no_retry(set()) is False
    assert ra._no_retry({'--no-retry'}) is True
    monkeypatch.setenv('VP_NO_RETRY', '1')
    assert ra._no_retry(set()) is True


# ---------------------------------------------------------------- G2 归账
def test_diff_dirs_only_reports_new_entries():
    before = {'results': {'a', 'b'}, 'tool_runs': set()}
    after = {'results': {'a', 'b', 'it_x'}, 'tool_runs': {'mirna_1'}}
    diff = ra._diff_dirs(before, after)
    assert diff['results'] == ['it_x']
    assert diff['tool_runs'] == ['mirna_1']
    assert ra._describe_residue(diff) == 'results+1 tool_runs+1'


def test_diff_dirs_handles_removed_entries():
    """用例删掉了旧产物不算"污染"，只报新增。"""
    diff = ra._diff_dirs({'results': {'a', 'b'}, 'tool_runs': set()},
                         {'results': {'a'}, 'tool_runs': set()})
    assert diff['results'] == []
    assert ra._describe_residue(diff) == ''


def test_describe_residue_empty():
    assert ra._describe_residue({'results': [], 'tool_runs': []}) == ''


# ---------------------------------------------------------------- G1 实例探测
def test_test_needs_live_instance_real_files():
    """仓库里真实用例的分类：写死 8765 的算，自起服务的算不。"""
    live = os.path.join(TESTS_DIR, '_check_sdt_live.py')
    if os.path.isfile(live):
        assert ra._test_needs_live_instance(live) is True
    # 自起 werkzeug 的页面巡检：docstring 里那句 `[--base http://127.0.0.1:8765]`
    # 只是用法提示，不能因此被当成"需要外部实例"（否则无实例时白丢一个用例）。
    console = os.path.join(TESTS_DIR, '_check_pages_console.py')
    if os.path.isfile(console):
        assert ra._test_needs_live_instance(console) is False
    pure = os.path.join(TESTS_DIR, 'test_utils_pure.py')
    if os.path.isfile(pure):
        assert ra._test_needs_live_instance(pure) is False


def test_live_url_detection_is_not_fooled_by_docstring(tmp_path):
    p = tmp_path / 'x.py'
    p.write_text('# 用法: python x.py [--base http://127.0.0.1:8765]\n'
                 "PORT = 8765\n", encoding='utf-8')
    assert ra._test_needs_live_instance(str(p)) is False
    p.write_text("BASE = 'http://127.0.0.1:8765/tools'\n", encoding='utf-8')
    assert ra._test_needs_live_instance(str(p)) is True


def test_live_url_detection_missing_file():
    assert ra._test_needs_live_instance(
        os.path.join(TESTS_DIR, '_does_not_exist_.py')) is False


def test_port_listening_true_and_false():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.bind(('127.0.0.1', 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        assert ra._port_listening(port) is True
    finally:
        srv.close()
    # 关掉后同一端口不再有监听（极端竞态下可能被别人抢走，故用宽松断言）
    assert ra._port_listening(port, timeout=0.2) in (True, False)
    assert ra._port_listening(0) is False          # 非法端口不抛异常


def test_pid_alive():
    assert ra._pid_alive(str(os.getpid())) is True
    assert ra._pid_alive('') is False
    assert ra._pid_alive('not-a-pid') is False
    assert ra._pid_alive('2147483646') is False   # 几乎不可能存在的 pid


# ---------------------------------------------------------------- G1 互斥锁
@pytest.fixture()
def lock_path(tmp_path, monkeypatch):
    p = tmp_path / '._run_all.lock'
    monkeypatch.setattr(ra, '_LOCK_PATH', str(p))
    return p


def test_lock_acquire_and_release(lock_path):
    assert ra._acquire_lock() is True
    assert lock_path.read_text(encoding='utf-8') == str(os.getpid())
    ra._release_lock()
    assert not lock_path.exists()


def test_lock_refuses_when_holder_alive(lock_path):
    """持锁进程还活着 → 必须拒绝，这是并发假红的唯一硬拦截点。"""
    lock_path.write_text(str(os.getpid()), encoding='utf-8')
    assert ra._acquire_lock() is False


def test_lock_takes_over_stale_holder(lock_path):
    """持锁进程已死 → 陈旧锁应被接管，不能把人永久挡在门外。"""
    lock_path.write_text('2147483646', encoding='utf-8')
    assert ra._acquire_lock() is True
    assert lock_path.read_text(encoding='utf-8') == str(os.getpid())


# ---------------------------------------------------------------- 隔离根
def test_keep_real_run_root_switch(monkeypatch):
    monkeypatch.delenv('VP_KEEP_RUN_ROOT', raising=False)
    assert ra._keep_real_run_root() is False
    for v in ('1', 'true', 'yes'):
        monkeypatch.setenv('VP_KEEP_RUN_ROOT', v)
        assert ra._keep_real_run_root() is True
    monkeypatch.setenv('VP_KEEP_RUN_ROOT', '0')
    assert ra._keep_real_run_root() is False


def test_suite_run_root_creates_all_dirs(monkeypatch):
    import shutil
    monkeypatch.delenv('VP_KEEP_RUN_ROOT', raising=False)
    root = ra._suite_run_root()
    try:
        assert root and os.path.isdir(root)
        for name in ra._RUN_SUBDIRS:
            assert os.path.isdir(os.path.join(root, name)), name
        for name in ra._TOOL_RUNS_FIXED:
            assert os.path.isdir(os.path.join(root, 'tool_runs', name)), name
        # 隔离根本身必须不在真实 run/ 下面
        assert os.path.abspath(root) != os.path.abspath(
            os.path.join(ra.ROOT, 'run'))
    finally:
        shutil.rmtree(os.path.dirname(root), ignore_errors=True)


def test_suite_run_root_respects_keep(monkeypatch):
    monkeypatch.setenv('VP_KEEP_RUN_ROOT', '1')
    assert ra._suite_run_root() is None


def test_child_env_sets_and_strips(monkeypatch):
    env = ra._child_env(run_root=r'C:\tmp\vp_suite_x\run')
    assert env['VP_RUN_ROOT'] == r'C:\tmp\vp_suite_x\run'
    monkeypatch.setenv('CODEBUDDY_SAFE_DELETE_BUDGET', '3')
    monkeypatch.setenv('SOME_SAFE_DELETE_FLAG', '1')
    env = ra._child_env(run_root='')
    assert not [k for k in env if 'SAFE_DELETE' in k or k.startswith('CODEBUDDY_')]
    assert 'VP_RUN_ROOT' not in env          # 空 root 不能把宿主残留值传下去


def test_child_env_defaults_to_active_root(monkeypatch):
    monkeypatch.setitem(ra._ACTIVE_RUN_ROOT, 'path', r'C:\tmp\vp_active\run')
    assert ra._child_env()['VP_RUN_ROOT'] == r'C:\tmp\vp_active\run'
    monkeypatch.setitem(ra._ACTIVE_RUN_ROOT, 'path', None)
