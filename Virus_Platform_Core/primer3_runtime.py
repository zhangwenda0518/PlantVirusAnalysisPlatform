# -*- coding: utf-8 -*-
"""primer3-py 的安全接入层（全平台唯一的 primer3 导入点）。

为什么需要这一层（2026-09-20 实测）：
  primer3-py 的 C 扩展（p3helpers/thermoanalysis pyd）在 import 时要一次性
  申请大块内存做 k-mer 表；**可用内存不足时该分配失败 → pyd 内部未检查
  → 段错误（0xC0000005）直接杀死整个进程**，Python 层无法捕获。
  内存充裕时一切正常——所以必须先探测、再导入。

探测方式：在**子进程**里 import primer3（冻结分发 = exe --cli importprobe，
源码模式 = python -c "import primer3"）。子进程崩了只标记「不可用」，
平台主进程毫发无损；调用方按 RuntimeError 优雅降级（引物热力学/T7 引物
子步提示不可用，其余功能不受影响）。

结果缓存：进程内只探测一次（'ok'/'no'）；探测 OK 后本进程内直接导入。
"""

import os
import subprocess
import sys

_state = None      # None=未探测 'ok'='可用' 'no'='不可用'
_err = ''
_mod = None


def _child_cmd():
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--cli', 'importprobe', 'primer3']
    return [sys.executable, '-c', 'import primer3']


def _probe_once():
    """子进程探测。返回 True=可安全导入。"""
    global _state, _err
    try:
        r = subprocess.run(_child_cmd(), capture_output=True,
                           text=True, errors='replace', timeout=180,
                           creationflags=getattr(
                               subprocess, 'CREATE_NO_WINDOW', 0))
        ok = (r.returncode == 0)
        if not ok:
            tail = ((r.stderr or '') + (r.stdout or '')).strip()[-160:]
            _err = f'子进程导入失败 (exit {r.returncode}) {tail}'
    except Exception as e:
        _err = repr(e)
        ok = False
    _state = 'ok' if ok else 'no'
    return ok


def get_module():
    """返回已导入的 primer3 模块。不可用时抛 RuntimeError（调用方降级）。"""
    global _state, _mod, _err
    if _state == 'ok':
        return _mod
    if _state == 'no':
        raise RuntimeError('primer3 不可用（' + (_err or '导入探测失败') + '）')
    # 未探测：先子进程探一次；探测失败直接降级，不再本进程导入
    if not _probe_once():
        _state = 'no'
        raise RuntimeError('primer3 不可用：子进程导入探测失败（'
                           '常见诱因：可用内存不足时 pyd 初始化失败）'
                           + ('｜' + _err if _err else ''))
    import primer3 as m
    # 分发版里可能存在「半截 primer3」（只有 thermoanalysis.pyd、
    # 没有 bindings 子模块）—— 必须连 bindings 一起验才算可用。
    if not hasattr(m, 'bindings'):
        _state = 'no'
        _err = 'primer3.bindings 缺失（分发版未携带完整 primer3-py）'
        raise RuntimeError(_err)
    _mod = m
    _state = 'ok'
    return m


def is_available():
    """探测并缓存结果（供调用方在不导入的前提下判断是否降级）。"""
    try:
        get_module()
        return True
    except RuntimeError:
        return False
