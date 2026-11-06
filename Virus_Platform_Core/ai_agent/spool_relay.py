# -*- coding: utf-8 -*-
"""子进程事件回传的接收端：tail `run/ai_audit/spool/*.jsonl`，并进本地事件缓冲。

为什么需要它
------------
对话坞发起的跑批走 `mcp_call` → `mcp_console` 用 stdio 拉起的
`mcp_server.py` 子进程；流水线在**子进程**里跑，`broadcast` 的环形缓冲也
是子进程自己的一份。于是「📖 阶段开始 / ✔ 阶段完成 / 🏁 全部完成」这些事件
只落到审计盘，网页进程的 SSE 流看不到，对话坞里自然没有卡片。

回传方式选的是**文件 spool**而不是 HTTP 回推：
  - 子进程 stdout 已被 JSON-RPC 占用，加不了旁路；
  - 网页端口可能顺延、可能重启，子进程不该关心；
  - 文件追加失败也不影响分析本身（broadcast 里已静默）。

协议
----
  子进程：父进程拉起时注入 `VP_EVENT_SPOOL=<dir>`；`broadcast.publish`
          顺手把每条事件追加到 `<dir>/<pid>.jsonl`（一行一条 JSON）。
  本模块：每 `POLL_SEC` 秒扫一遍目录，按「文件内字节偏移」增量读新行，
          解析后 `broadcast.ingest()`（保留原 ts、打上 relay=True）。
          启动时先把已有文件的偏移定位到文件末尾 —— 只关心**连上之后**的
          事件，不把上次运行的历史重放一遍。

边界
----
  - 线程 daemon，异常一律吞掉（回传坏了不能影响网页）；
  - 文件被删/被截断（复用 pid）时偏移归零重读；
  - 单个事件行 > MAX_LINE 字节视为损坏，跳过；
  - 启动时清掉 24h 前的 spool 文件（它们对应的进程早没了）。
"""
import json
import os
import threading
import time

POLL_SEC = 0.4
MAX_LINE = 256 * 1024
STALE_SEC = 24 * 3600

_thread = None
_stop = threading.Event()
_offsets = {}                      # path -> 已读到的字节位置
_fingerprints = {}                 # path -> (mtime, size)：识别截断/文件被换掉
_seen_files = set()
_baseline = None                   # 首次扫描时刻：早于它的文件视为历史，不补播


def _spool_root():
    try:
        from Virus_Platform_Core.config import DIRS
        return os.path.join(DIRS['ai_audit'], 'spool')
    except Exception:
        from Virus_Platform_Core.config import PLATFORM_ROOT
        return os.path.join(PLATFORM_ROOT, 'run', 'ai_audit', 'spool')


def _cleanup(root):
    """删掉 24h 前的回传文件（对应子进程早已退出）。"""
    now = time.time()
    try:
        names = os.listdir(root)
    except OSError:
        return
    for n in names:
        if not n.endswith('.jsonl'):
            continue
        p = os.path.join(root, n)
        try:
            if now - os.path.getmtime(p) > STALE_SEC:
                os.remove(p)
        except OSError:
            pass


def _read_new(path):
    """从上次偏移读到末尾，返回 (事件列表, 新偏移)。半行不算（等它写完）。

    截断判定（2026-11-06）：只比 size < offset 会漏——PID 被复用、新进程的
    文件恰好长得比旧 offset 长时，旧 offset 落在新文件中间，从半行开始
    解析会丢掉第一条事件甚至把半行当坏行跳过。现在每个路径额外记
    (mtime, size) 指纹：mtime 回退或 size 变小都视为"文件被重建"，offset
    归零重读；解析只在最后一个换行处截断，绝不把半行交给 json.loads。
    """
    try:
        st = os.stat(path)
        size = st.st_size
        mtime = st.st_mtime
    except OSError:
        # 文件消失：清掉水位与指纹，等它重建时从 0 读
        _offsets.pop(path, None)
        _fingerprints.pop(path, None)
        return [], 0
    prev = _fingerprints.get(path)
    truncated = bool(prev is not None and (
        mtime < prev[0] - 1e-6 or size < prev[1]))
    off = _offsets.get(path)
    if off is None:
        # 首次见到这个文件：服务启动前就存在的当历史跳过（不回放旧事故）；
        # 启动后新建的（例如刚拉起 MCP 子进程）从 0 读，别把它的头几条丢掉。
        try:
            born = os.path.getctime(path)
        except OSError:
            born = 0
        if _baseline is not None and born < _baseline:
            _offsets[path] = size
            _fingerprints[path] = (mtime, size)
            return [], size
        off = 0
    if truncated or size < off:    # 截断 / 重建 / PID 复用：从头读
        off = 0
    if size == off:
        _fingerprints[path] = (mtime, size)
        return [], off
    try:
        with open(path, 'rb') as f:
            f.seek(off)
            chunk = f.read(size - off)
    except OSError:
        return [], off
    cut = chunk.rfind(b'\n')
    if cut < 0:                    # 只有半行，等下次（offset 不动）
        _fingerprints[path] = (mtime, size)
        return [], off
    chunk = chunk[:cut + 1]
    events = []
    for raw in chunk.splitlines():
        if not raw.strip() or len(raw) > MAX_LINE:
            continue
        try:
            ev = json.loads(raw.decode('utf-8', 'replace'))
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get('type'):
            events.append(ev)
    new_off = off + len(chunk)
    _fingerprints[path] = (mtime, size)
    return events, new_off


def poll_once():
    """扫一遍 spool 目录；返回本次并入的事件数（测试直接调它，不用起线程）。"""
    global _baseline
    from Virus_Platform_Core.ai_agent import broadcast
    root = _spool_root()
    try:
        os.makedirs(root, exist_ok=True)
    except OSError:
        return 0
    if not _seen_files:
        _cleanup(root)
    if _baseline is None:
        _baseline = time.time()
    n = 0
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return 0
    for name in names:
        if not name.endswith('.jsonl'):
            continue
        path = os.path.join(root, name)
        _seen_files.add(path)
        events, off = _read_new(path)
        _offsets[path] = off
        for ev in events:
            try:
                if broadcast.ingest(ev):
                    n += 1
            except Exception:
                pass
    return n


def _loop():
    while not _stop.wait(POLL_SEC):
        try:
            poll_once()
        except Exception:
            pass


def start_relay():
    """幂等启动后台 relay 线程（网页进程启动时调一次）。"""
    global _thread
    if _thread is not None and _thread.is_alive():
        return _thread
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True,
                               name='ai-event-relay')
    _thread.start()
    return _thread


def stop_relay():
    _stop.set()


def reset_for_test():
    """测试用：清空读取水位（下次 poll 会重新定位到文件末尾）。"""
    global _baseline
    _offsets.clear()
    _fingerprints.clear()
    _seen_files.clear()
    _baseline = None
