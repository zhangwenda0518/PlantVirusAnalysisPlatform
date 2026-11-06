# -*- coding: utf-8 -*-
"""系统资源监测：整机 CPU / 内存 + 平台自身进程树占用。

为什么单独成模块
----------------
顶栏的「本模块环境」状态条走 ``selfcheck.collect()``（首次要递归 import
全部子模块，数秒级、5 分钟缓存），不能拿来每几秒轮询。这里只做毫秒级
系统调用，供 ``GET /api/sys_metrics`` 使用。

取样口径
--------
- **CPU**：两次取样之间的整机占用率 = (总时间增量 - 空闲增量) / 总时间增量。
  指标是**整机**的（所有进程），与任务管理器的「CPU」一致；平台自身另有
  ``proc.cpu_cores``（平台进程树平均占用了几个核）。
- **内存**：整机 total / used / available；``percent = used / total``。
- **平台进程树**：主进程 + 递归子进程的 RSS 合计、进程数，以及 RSS 前 5 的
  进程（回答「这些占用里平台占多少、是谁占的」）。
- **峰值**：自进程启动以来的最大 CPU / 内存百分比（模块级，不落盘）。

实现优先级
----------
CPU / 内存：psutil → Windows ctypes → Linux /proc → 不可用返回 None。
平台进程树：仅 psutil（宁可不显示，也不做容易出错的进程枚举）。
任何一项失败都只影响该项，不抛异常——状态条不能带崩页面。

线程安全
--------
Flask ``make_server(threaded=True)`` 下多个请求可能并发进入；取样状态
（上次 CPU 基线 / 上次各 PID 的 CPU 时间）用模块锁保护，并把 0.8 秒内的
重复请求合并成同一份快照，避免多开标签页把采样窗口切得过碎。
"""
from __future__ import annotations

import os
import threading
import time

from .utils import fmt_size

# 两次真实采样之间的最小间隔（秒）：多个标签页并发轮询时合并为一次
_MIN_INTERVAL = 0.8
_TOP_PROC = 5

_lock = threading.RLock()
_cpu_prev = None          # (monotonic_ts, busy_seconds, total_seconds)
_proc_prev = {}           # pid -> (monotonic_ts, cpu_seconds)
_peak = {'cpu': 0.0, 'mem': 0.0}
_cache = {'ts': 0.0, 'data': None}


# ------------------------------------------------------------------
# CPU 累计时间（busy / total，单位：秒）
# ------------------------------------------------------------------
def _cpu_times_psutil():
    import psutil
    t = psutil.cpu_times()
    idle = float(getattr(t, 'idle', 0.0)) + float(getattr(t, 'iowait', 0.0))
    vals = [float(v) for v in t]
    total = sum(vals)
    return total - idle, total


def _cpu_times_windows():
    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [('dwLowDateTime', wintypes.DWORD),
                    ('dwHighDateTime', wintypes.DWORD)]

    def _to_sec(ft):
        return ((ft.dwHighDateTime << 32) | ft.dwLowDateTime) / 1e7

    idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
    ok = ctypes.windll.kernel32.GetSystemTimes(
        ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    if not ok:
        raise OSError('GetSystemTimes failed')
    idle_s = _to_sec(idle)
    total = _to_sec(kernel) + _to_sec(user)   # kernel 已包含 idle
    return max(0.0, total - idle_s), total


def _cpu_times_linux():
    with open('/proc/stat', 'r', encoding='ascii', errors='replace') as f:
        parts = f.readline().split()
    vals = [float(x) for x in parts[1:]]
    # user nice system idle iowait irq softirq steal guest guest_nice
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0.0)
    total = sum(vals[:8])
    return max(0.0, total - idle), total


def _read_cpu_times():
    for fn in (_cpu_times_psutil, _cpu_times_windows, _cpu_times_linux):
        try:
            busy, total = fn()
            if total > 0:
                return busy, total
        except Exception:                        # noqa: BLE001
            continue
    return None


# ------------------------------------------------------------------
# 内存
# ------------------------------------------------------------------
def _mem_psutil():
    import psutil
    v = psutil.virtual_memory()
    return int(v.total), int(v.used), int(v.available)


def _mem_windows():
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [('dwLength', ctypes.c_ulong),
                    ('dwMemoryLoad', ctypes.c_ulong),
                    ('ullTotalPhys', ctypes.c_ulonglong),
                    ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong),
                    ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong),
                    ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]

    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        raise OSError('GlobalMemoryStatusEx failed')
    total = int(st.ullTotalPhys)
    avail = int(st.ullAvailPhys)
    return total, total - avail, avail


def _mem_linux():
    info = {}
    with open('/proc/meminfo', 'r', encoding='ascii', errors='replace') as f:
        for line in f:
            key, _, rest = line.partition(':')
            info[key] = int(rest.split()[0]) * 1024
    total = info.get('MemTotal', 0)
    avail = info.get('MemAvailable')
    if avail is None:
        avail = (info.get('MemFree', 0) + info.get('Buffers', 0)
                 + info.get('Cached', 0))
    return total, total - avail, avail


def _read_mem():
    for fn in (_mem_psutil, _mem_windows, _mem_linux):
        try:
            total, used, avail = fn()
            if total > 0:
                return total, max(0, used), max(0, avail)
        except Exception:                        # noqa: BLE001
            continue
    return None


# ------------------------------------------------------------------
# 平台进程树（psutil）
# ------------------------------------------------------------------
def _proc_snapshot(now):
    """平台主进程 + 递归子进程的 RSS / 数量 / CPU（占几核）。

    返回 None 表示 psutil 不可用或枚举失败（前端不显示这一段）。
    """
    try:
        import psutil
    except Exception:                            # noqa: BLE001
        return None
    try:
        root = psutil.Process()
        procs = [root] + root.children(recursive=True)
    except Exception:                            # noqa: BLE001
        return None

    cores = os.cpu_count() or 1
    rss_total = 0
    items = []
    cur = {}
    for p in procs:
        try:
            rss = int(p.memory_info().rss)
        except Exception:                        # noqa: BLE001
            continue
        try:
            name = p.name()
        except Exception:                        # noqa: BLE001
            name = '?'
        rss_total += rss
        items.append({'pid': p.pid, 'name': name, 'rss': rss})
        try:
            ct = p.cpu_times()
            cur[p.pid] = float(getattr(ct, 'user', 0.0)) + \
                float(getattr(ct, 'system', 0.0))
        except Exception:                        # noqa: BLE001
            pass

    # 平台 CPU：逐 PID 的 CPU 秒增量 / 自身墙钟增量 = 该进程平均占用的核数；
    # 累加即平台进程树占用核数（新起/已退出的进程不参与，宁少不多）。
    cpu_cores = None
    prev = _proc_prev
    if prev:
        cores_used = 0.0
        covered = False
        for pid, sec in cur.items():
            old = prev.get(pid)
            if not old:
                continue
            dt = now - old[0]
            ds = sec - old[1]
            if dt > 0 and ds >= 0:
                cores_used += ds / dt
                covered = True
        if covered:
            cpu_cores = round(min(float(cores), cores_used), 2)
    _proc_prev.clear()
    _proc_prev.update({pid: (now, sec) for pid, sec in cur.items()})

    items.sort(key=lambda x: -x['rss'])
    top = [{'pid': x['pid'], 'name': x['name'], 'rss_h': fmt_size(x['rss'])}
           for x in items[:_TOP_PROC]]
    return {'pid': os.getpid(), 'count': len(items), 'rss': rss_total,
            'rss_h': fmt_size(rss_total), 'cpu_cores': cpu_cores,
            'cores': cores, 'top': top}


# ------------------------------------------------------------------
# 对外接口
# ------------------------------------------------------------------
def sample(force=False):
    """返回一份资源快照（0.8 秒内重复调用返回同一份，force 强制重采）。"""
    global _cpu_prev
    with _lock:
        now = time.monotonic()
        if (not force and _cache['data'] is not None
                and now - _cache['ts'] < _MIN_INTERVAL):
            return _cache['data']

        out = {'ok': True, 'ts': int(time.time()), 'cpu': None,
               'mem': None, 'proc': None}

        # ── CPU（需要上一轮基线；首次只建基线，返回 None）──
        ct = _read_cpu_times()
        if ct is not None:
            if _cpu_prev is not None:
                p_ts, p_busy, p_total = _cpu_prev
                dt = now - p_ts
                db = ct[0] - p_busy
                dtot = ct[1] - p_total
                pct = None
                if dt > 0 and dtot > 0 and db >= 0:
                    pct = round(min(100.0, max(0.0, db / dtot * 100.0)), 1)
                    _peak['cpu'] = max(_peak['cpu'], pct)
            else:
                pct = None
            _cpu_prev = (now, ct[0], ct[1])
            out['cpu'] = {'percent': pct, 'cores': os.cpu_count() or 1,
                          'peak': round(_peak['cpu'], 1) if _peak['cpu'] else None}

        # ── 内存 ──
        mem = _read_mem()
        if mem:
            total, used, avail = mem
            pct = round(used / total * 100.0, 1) if total else None
            if pct is not None:
                _peak['mem'] = max(_peak['mem'], pct)
            out['mem'] = {
                'total': total, 'used': used, 'available': avail,
                'percent': pct,
                'total_h': fmt_size(total), 'used_h': fmt_size(used),
                'available_h': fmt_size(avail),
                'peak': round(_peak['mem'], 1) if _peak['mem'] else None,
            }

        # ── 平台进程树 ──
        try:
            out['proc'] = _proc_snapshot(now)
        except Exception:                        # noqa: BLE001
            out['proc'] = None

        _cache['ts'] = now
        _cache['data'] = out
        return out


def reset_peaks():
    """测试/排查用：清空峰值与基线。"""
    global _cpu_prev
    with _lock:
        _cpu_prev = None
        _proc_prev.clear()
        _peak['cpu'] = 0.0
        _peak['mem'] = 0.0
        _cache['ts'] = 0.0
        _cache['data'] = None
