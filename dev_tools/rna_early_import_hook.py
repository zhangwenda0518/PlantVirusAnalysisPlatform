# -*- coding: utf-8 -*-
"""冻结分发专用启动钩子：①钉住可写临时目录；②预载 ViennaRNA。

为什么①（2026-10-28 实抓）：
  PyInstaller 6.22 的 matplotlib 启动钩子（pyi_rth_mplconfig）在**进程第一条
  指令**就调 `secure_mkdtemp()`；它在 Windows 上用带受限 DACL 的
  CreateDirectoryW 建目录，一旦被拒，`except PermissionError: continue` 会
  重试 TMP_MAX(1e4) 次 —— 现象是满 CPU 空转、永不开端口（py-spy 实测栈：
  secure_mkdtemp → _pyi_rthhook → mplconfig:45）。系统 TEMP 不可写的机器上
  老版本是直接崩（FileNotFoundError: No usable temporary directory）。

  两层处理：
  1) spec 里已把 `pyi_rth_mplconfig` 从 a.scripts 摘掉 —— 它是为 **onefile**
     写的（_MEIxxxx 每次不同会让 matplotlib 的 fontList.cache 失效）；我们
     是 onedir，_MEIPASS 固定，这条钩子本来就没有价值，摘掉即根治。
  2) 本钩子（排在所有 PyInstaller 自带钩子之前）**实测**挑一个真能建文件的
     目录：逐个候选 os.makedirs + 建删探针目录，第一个成功的才拿去设
     `tempfile.tempdir` 和 TMPDIR/TEMP/TMP。探测过程写进 run\\_boot.log
     （≤16KB 覆盖写），下次再出问题不用再猜。

  候选顺序：<程序目录>\\run\\tmp → %LOCALAPPDATA%\\VirusPlatform\\tmp →
  系统 TEMP\\VirusPlatform → <程序目录>。都不行就什么都不设（交给上层报
  原始错误，不要用假路径掩盖）。

为什么②（2026-09-18 排查结论）：
  RNA 包的 __init__ 用 `except: raise ImportError(...)` 掩盖真实错误；
  经探针逐层复现，在完整应用里 _RNA.pyd 的 DllMain 会报「初始化例程失败」
  —— 依赖解析全部正常（MSVCP140 已随包，见 spec 的 _binaries），失败与
  **加载时机**相关：dsrna_* 在 web 全栈导入之后才首次 import RNA 就会挂，
  在进程最早期 import 则必成功（多轮 PyInstaller 探针实证）。
  因此用 runtime hook 在引导期预载，绕开时机问题。源码模式不需要本钩子
  （System32 的运行库无冲突）。若未来 RNA 包修复或换版本，可重测后移除。
"""

import os
import sys
import tempfile

_LOG_LINES = []


def _base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _log(msg):
    _LOG_LINES.append(str(msg))


def _flush_log(base):
    """把探测记录写到第一个写得动的地方；失败就算了（不能因为它再崩一次）。"""
    data = '\n'.join(_LOG_LINES) + '\n'
    for path in (os.path.join(base, 'run', '_boot.log'),
                 os.path.join(base, '_boot.log')):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(data)
            return
        except OSError:
            continue


def _probe_dir(path):
    """真建真删一个目录，能成立才返回 None，否则返回异常文本。"""
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        return 'makedirs: %r' % (exc,)
    probe = os.path.join(path, 'pyi_probe_%d' % os.getpid())
    try:
        os.mkdir(probe)
        os.rmdir(probe)
    except OSError as exc:
        return 'mkdir: %r' % (exc,)
    return None


def _temp_candidates(base):
    local = os.environ.get('LOCALAPPDATA') or ''
    sys_tmp = os.environ.get('TEMP') or ''
    out = [os.path.join(base, 'run', 'tmp')]
    if local:
        out.append(os.path.join(local, 'VirusPlatform', 'tmp'))
    if sys_tmp:
        out.append(os.path.join(sys_tmp, 'VirusPlatform'))
    out.append(base)
    seen, uniq = set(), []
    for p in out:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def _pin_temp_dir():
    base = _base_dir()
    chosen = None
    for cand in _temp_candidates(base):
        err = _probe_dir(cand)
        _log('TEMP_CAND %s -> %s' % (cand, 'OK' if err is None else 'FAIL ' + err))
        if err is None and chosen is None:
            chosen = cand
    if chosen is None:
        _log('TEMP none usable; leaving tempfile.tempdir unset')
        _flush_log(base)
        return base, None
    for key in ('TMPDIR', 'TEMP', 'TMP'):
        try:
            os.environ[key] = chosen
        except (KeyError, ValueError):
            pass
    # 关键一行：tempfile 优先用这个值，不再调用 _get_default_tempdir() 探测
    tempfile.tempdir = chosen
    _log('TEMP pinned: %s' % chosen)
    _flush_log(base)
    return base, chosen


def _defuse_mplconfig_hook(base):
    """兜底：即便 mplconfig 钩子还在（老包/未来改版），也别让它空转。"""
    try:
        import _pyi_rth_utils.tempfile as _pt
        _pt.secure_mkdtemp = tempfile.mkdtemp
        _log('secure_mkdtemp -> tempfile.mkdtemp')
    except Exception as exc:                # noqa: BLE001
        _log('patch secure_mkdtemp skipped: %r' % (exc,))
    try:
        mpl = os.path.join(base, 'run', 'mplconfig')
        os.makedirs(mpl, exist_ok=True)
        os.environ['MPLCONFIGDIR'] = mpl
        _log('MPLCONFIGDIR=%s' % mpl)
    except OSError as exc:
        _log('MPLCONFIGDIR skipped: %r' % (exc,))


_BASE, _TMP = _pin_temp_dir()
_defuse_mplconfig_hook(_BASE)

try:
    import RNA  # noqa: E402,F401
    _log('RNA preloaded')
except Exception as exc:                    # noqa: BLE001
    # 预载失败不该拖死整个平台：dsrna / miRNA 会自行降级，其余功能照跑
    _log('RNA preload failed: %r' % (exc,))

_flush_log(_BASE)
