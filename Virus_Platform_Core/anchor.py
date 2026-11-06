# -*- coding: utf-8 -*-
"""样品锚（sample anchor）：把「同一个样品」从路径约定升级成可核对的事实。

为什么需要（2026-10-27）
    流水线的 15 个阶段共用一个样品目录 `run/results/<样品>/0X_*/`，样本身份靠
    「目录名 + 调用者传进来的 r1/r2」约定；而工具页的每次运行落在
    `run/tool_runs/<tool>_<ts>_<uuid6>/`，任务记录里既没有样品字段也没有输入
    摘要。于是「这份 03_assembly 是用哪份 R1 跑的」只能靠人记：
      · `is_step_done()` 只看 `0X_*.done` 在不在，换输入续跑会静默复用旧阶段；
      · `project.json` 的 input 只记文件名（`_manifest_params` 压成 basename），
        没有任何内容摘要；
      · 唯一做过输入指纹的是 fq2fa 的 `input_fingerprint`（路径+大小+mtime_ns），
        只覆盖 ⓪b→① 一行，且注释里写着「只比 mtime 不安全（历史事故）」。
    这里把「输入是谁、产出是谁、第几次、什么状态」落成一个随运行写入的锚：
    `run/anchors/<slug>/anchor.json`（快照）+ `ledger.jsonl`（只追加、hash 链）。

本阶段（第一步）只读
    **只记录、只展示，不拦截任何提交**。所有公开函数都不会抛异常（内部错误写
    `run/anchors/_anchor.log`），因此接线进来不会改变任何既有判定逻辑。
    第二步才在 `run_analysis` 入口加闸（摘要不符 + 已有 .done → 拒绝复用）。

数据模型
    anchor.json
        schema / sample / slug / sample_dir / project / created
        inputs   {r1:{path,size,mtime_ns,sha256,state}, r2:{…}}   ← 最近一次运行
        input_set_id      最近一次输入组合的短 id（sha256 前 10 位）
        input_set_count   历史上输入组合变更的次数（>1 说明换过输入）
        runs[]  {run_id, started, finished, status, stages[], stages_done[],
                 stage, input_set, dur_s, outputs_n}
        ledger {seq, head, updated}
    ledger.jsonl  每行 {seq, ts, kind, run_id, …, prev}
        kind: run.start / stage.begin / stage.done / run.end / run.interrupted /
              input.change / input.digest / verify
        prev = 上一行的 sha256（首行为 ""），用于事后证明链未被改写

产物摘要的取舍
    小于 `HASH_MAX_BYTES`（32 MiB）且计入总量上限的产物记 sha256；其余只记
    size。理由是：锚的价值在「输入可证 + 关键中间产物可证」，把几 GB 的 BAM
    也逐次摘要会让运行结束多等十几秒，收益很低。

兼容性
    纯新增模块，不改 `DIRS` 字典、不改 `project.json` 结构；锚目录放在
    `run/anchors/`（与 `run/results/`、`run/tool_runs/` 并列），因此不会被
    `backfill_all_manifests`（只扫 results 下非 `_` 开头目录）或样品列表误认。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import traceback
import uuid

__all__ = [
    'SCHEMA', 'HASH_MAX_BYTES', 'root', 'slug_of',
    'start_run', 'stage_begin', 'stage_done', 'end_run', 'note',
    'reconcile_interrupted',
    'snapshot', 'active', 'samples_meta', 'verify', 'list_slugs',
]

SCHEMA = 1
HASH_MAX_BYTES = 32 * 1024 * 1024      # 超过此大小的产物只记 size
HASH_CAP_BYTES = 512 * 1024 * 1024     # 单次 run.end 摘要总量上限
CHUNK = 8 * 1024 * 1024
STALE_SEC = 30 * 60                    # 运行中但 30 分钟无事件 → 前端标「疑似中断」

_LOCK = threading.RLock()
_RUNS = {}                             # slug -> run_id（本进程内的活动运行）
_CACHE = None                          # 输入摘要缓存（_digests.json）
_CACHE_DIRTY = False


# --------------------------------------------------------------------------
# 路径与基础工具
# --------------------------------------------------------------------------
def root():
    """锚根目录：`<平台>/run/anchors`（与 results/tool_runs 并列）。"""
    from .config import DIRS
    return os.path.join(os.path.dirname(DIRS['results']), 'anchors')


def slug_of(sample):
    """样品名 → 锚目录名（确定性，跨进程一致）。

    `safe_sample_name` 把非 ASCII/空格等折成 `_` 再裁掉首尾符号：中文样品名
    常常被折成 `1`、`A` 这类短串，两个样品撞进同一目录。折叠有损时补一段
    原名 sha1 前 6 位，既不依赖「先来先占」的 `__2` 后缀（多进程并发的分析
    任务会抢同一个目录），也保证同一路径每次算出同一个 slug。ASCII 名字保持
    与结果目录同名，便于肉眼对照。

    万一目录已被另一个 sample 占用（历史遗留/手工文件），仍追加 `__2`。
    """
    from .utils import safe_sample_name
    raw = str(sample or '').strip()
    base = safe_sample_name(raw) or 'sample'
    if base != raw:
        base = '%s-%s' % (base, hashlib.sha1(raw.encode('utf-8')).hexdigest()[:6])
    cand, i = base, 2
    while True:
        p = os.path.join(root(), cand)
        aj = os.path.join(p, 'anchor.json')
        if not os.path.isfile(aj):
            return cand
        try:
            with open(aj, 'r', encoding='utf-8') as f:
                if json.load(f).get('sample') == sample:
                    return cand
        except Exception:
            return cand
        cand = '%s__%d' % (base, i)
        i += 1


def _log(msg):
    """锚自身出错只写日志：绝不让「记录」影响「分析」。"""
    try:
        d = root()
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, '_anchor.log'), 'a', encoding='utf-8') as f:
            f.write('%s %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg))
    except OSError:
        pass


def _safe(fn, default=None):
    """公开函数的统一兜底：任何异常都不外抛（第一步只读的硬约束）。"""
    try:
        return fn()
    except Exception:
        _log('EXC ' + traceback.format_exc(limit=3).replace('\n', ' | '))
        return default


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%S')


def _read_json(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, obj):
    """原子写：临时文件 + os.replace（写半截时不会毁掉上一次的快照）。

    临时名带随机后缀：后台摘要线程与阶段事件可能同时写同一个 anchor.json，
    共用一个 `xxx.tmp` 会互相踩（先替换者把 tmp 拿走，后写者 os.replace 报
    FileNotFoundError，甚至留下半截 tmp）。
    """
    from .utils import safe_open
    tmp = '%s.%s.tmp' % (path, uuid.uuid4().hex[:8])
    try:
        with safe_open(tmp, 'wt') as f:
            json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=False)
        os.replace(tmp, path)
    finally:
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _short(x, n=10):
    return (x or '')[:n]


# --------------------------------------------------------------------------
# 输入摘要（带缓存：同一个文件只算一次）
# --------------------------------------------------------------------------
def _cache_path():
    return os.path.join(root(), '_digests.json')


def _cache_load():
    global _CACHE
    if _CACHE is None:
        _CACHE = _read_json(_cache_path(), {}) or {}
    return _CACHE


def _cache_save():
    global _CACHE_DIRTY
    try:
        _CACHE_DIRTY = False
        _write_json(_cache_path(), _cache_load())
    except Exception as e:
        _log('cache save failed: %r' % (e,))


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _digest(path, allow_hash=True):
    """文件摘要：{path, size, mtime_ns, sha256, state}。

    state='ok' 已算完；'hashing' 正在后台算；'mtime' 只拿到大小与 mtime
    （超大文件或后台算完前）。缓存键带 size+mtime_ns：文件一旦被替换就不命中。
    """
    p = os.path.abspath(str(path))
    try:
        st = os.stat(p)
    except OSError as e:
        return {'path': p, 'size': 0, 'mtime_ns': 0, 'sha256': '', 'state': 'missing',
                'error': str(e)}
    rec = {'path': p, 'size': st.st_size, 'mtime_ns': st.st_mtime_ns}
    key = p.lower()
    with _LOCK:
        c = _cache_load().get(key) or {}
    if c and c.get('size') == st.st_size and c.get('mtime_ns') == st.st_mtime_ns \
            and c.get('sha256'):
        rec.update(sha256=c['sha256'], state='ok', cached=True)
        return rec
    if not allow_hash:
        rec.update(sha256='', state='mtime')
        return rec
    rec.update(sha256=_sha256_file(p), state='ok')
    _cache_store(rec)
    return rec


def _cache_store(rec):
    global _CACHE_DIRTY
    with _LOCK:
        c = _cache_load()
        c[rec['path'].lower()] = {'size': rec['size'], 'mtime_ns': rec['mtime_ns'],
                                  'sha256': rec['sha256'], 'ts': _now()}
        _CACHE_DIRTY = True
    _safe(_cache_save)


def _hash_async(slug, run_id, sample, keys):
    """后台补算输入摘要（首次提交不因 GB 级 FASTQ 卡住）。"""
    def work():
        try:
            recs = {}
            for k in keys:
                recs[k] = _safe(lambda: _digest(keys[k]['path']),
                                {'state': 'error'})
            # 回填 anchor.json 的 inputs（只覆盖仍在 hashing/mtime 的项）
            def m(d):
                for k, rec in recs.items():
                    slot = d.setdefault('inputs', {}).setdefault(k, {})
                    if slot.get('path') == rec.get('path'):
                        slot.update(rec)
                d['inputs_state'] = ('ok' if all(
                    v.get('state') == 'ok' for v in d.get('inputs', {}).values()
                ) else 'partial')
                return True
            _mut(slug, m)
            _append(slug, 'input.digest', {
                'run_id': run_id,
                'inputs': {k: _short(r.get('sha256'), 16) for k, r in recs.items()},
                'state': 'ok',
            })
            _reconcile_input_set(slug, sample, run_id)
        except Exception:
            _log('hash_async EXC ' + traceback.format_exc(limit=3))
    t = threading.Thread(target=work, name='anchor-hash', daemon=True)
    t.start()


# --------------------------------------------------------------------------
# 账本（只追加 + hash 链）
# --------------------------------------------------------------------------
def _ledger_path(slug):
    return os.path.join(root(), slug, 'ledger.jsonl')


def _append(slug, kind, payload, run_id=''):
    """追加一条账本事件，返回 seq。链头保存在 anchor.json 的 ledger 里。"""
    from .utils import safe_open
    d = os.path.join(root(), slug)
    os.makedirs(d, exist_ok=True)
    path = _ledger_path(slug)
    with _LOCK:
        # 账本只挂在**已存在**的锚快照上：没有 anchor.json 就说明这次 start_run
        # 没落地（或锚根被换过），此时千万别凭空写一条 ledger —— 那会留下一个
        # 只有账本、没有 sample 字段的目录，slug_of 会把它当成「被别人占用」而
        # 再追加 __2，同一个样品从此分裂成两个锚。
        if not os.path.isfile(os.path.join(d, 'anchor.json')):
            _log('skip append: anchor.json 不存在 (%s / %s)' % (slug, kind))
            return 0
        snap = _read_json(os.path.join(d, 'anchor.json'), {}) or {}
        led = snap.get('ledger') or {}
        seq = int(led.get('seq') or 0) + 1
        ev = {'seq': seq, 'ts': _now(), 'kind': kind, 'run_id': run_id,
              'prev': led.get('head') or ''}
        ev.update(payload or {})
        line = json.dumps(ev, ensure_ascii=False, sort_keys=True)
        with safe_open(path, 'at') as f:
            f.write(line + '\n')
        head = hashlib.sha256(line.encode('utf-8')).hexdigest()
        snap['ledger'] = {'seq': seq, 'head': head, 'updated': ev['ts']}
        _write_json(os.path.join(d, 'anchor.json'), snap)
    return seq


# --------------------------------------------------------------------------
# 锚快照
# --------------------------------------------------------------------------
def _anchor_path(slug):
    return os.path.join(root(), slug, 'anchor.json')


def _ensure(sample, sample_dir='', project=''):
    slug = slug_of(sample)
    path = _anchor_path(slug)
    d = _read_json(path)
    if not d:
        d = {'schema': SCHEMA, 'sample': sample, 'slug': slug,
             'sample_dir': sample_dir or '', 'project': project or '',
             'created': _now(),
             'platform': {'python': sys.version.split()[0],
                          'frozen': bool(getattr(sys, 'frozen', False))},
             'inputs': {}, 'inputs_state': 'empty', 'input_set_id': '',
             'input_set_count': 0, 'runs': [], 'ledger': {}}
    else:
        if sample_dir:
            d['sample_dir'] = sample_dir
        if project and not d.get('project'):
            d['project'] = project
    return slug, d


def _save(slug, d):
    os.makedirs(os.path.join(root(), slug), exist_ok=True)
    # 只保留最近 50 次运行，避免 anchor.json 无限增长（账本仍然全量）
    runs = d.get('runs') or []
    if len(runs) > 50:
        d['runs'] = runs[-50:]
    _write_json(_anchor_path(slug), d)


def _mut(slug, fn):
    """锁内做一次 read-modify-write，返回 fn(d) 的结果。

    为什么必须锁：`_hash_async` 的后台线程会在任意时刻回填 inputs 并整份写回
    anchor.json；若它与 `stage_begin/stage_done` 的读改写交叠，后写者会用旧快照
    覆盖新事件（真机上表现为「常驻条停在上一阶段」）。统一走一个 RLock +
    每次重新读盘，写入不再丢事件。
    """
    with _LOCK:
        d = _read_json(_anchor_path(slug))
        if not d:
            return None
        out = fn(d)
        _save(slug, d)
        return out


def _current_run(d):
    for r in reversed(d.get('runs') or []):
        if r.get('status') == 'running':
            return r
    return None


def _run_by_id(d, run_id):
    """按 run_id 找运行记录（**不要求 status=running**）。

    end_run 必须能收尾被 reconcile_interrupted 标成 interrupted 的记录，
    否则「启动收尾」与「真实结束」先后顺序一变，end_run 就永远找不到记录、
    finished/dur_s/outputs 全部丢失（常驻条停在「正在分析」）。
    """
    if not run_id:
        return None
    for r in reversed(d.get('runs') or []):
        if r.get('run_id') == run_id:
            return r
    return None


def _pid_alive(pid):
    """进程是否仍存活。返回 True/False；pid 缺失或无法判定时返回 None。

    Windows 不能用 os.kill(pid, 0)（会走 TerminateProcess），改用
    OpenProcess + GetExitCodeProcess；其它平台用 signal 0。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        # 64 位下必须声明 HANDLE 返回类型，否则默认 c_int 会截断/符号化句柄
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL,
                                    wintypes.DWORD)
        k32.GetExitCodeProcess.restype = wintypes.BOOL
        k32.GetExitCodeProcess.argtypes = (
            wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k32.CloseHandle.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        h = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            if k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == 259            # STILL_ACTIVE
            return None
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _input_set_id(inputs):
    """输入组合短 id：只看 sha256（未算完时退回 size+mtime）。"""
    parts = []
    for k in ('r1', 'r2'):
        rec = (inputs or {}).get(k) or {}
        tag = rec.get('sha256') or ('%s:%s' % (rec.get('size', 0),
                                               rec.get('mtime_ns', 0)))
        parts.append('%s=%s' % (k, tag))
    return 'is-' + _short(hashlib.sha256('|'.join(parts).encode()).hexdigest(), 10)


def _reconcile_input_set(slug, sample, run_id):
    """摘要算完后复核输入组合是否与上一轮一致（变了只记账 + 立旗，不拦）。"""
    box = {}

    def m(d):
        # 摘要没算完就比不出「换没换输入」：等 _hash_async 算完会再调一次
        if (d.get('inputs_state') or '') != 'ok':
            return False
        new_id = _input_set_id(d.get('inputs'))
        old_id = d.get('input_set_id') or ''
        if not old_id:
            d['input_set_id'] = new_id
            return False
        if new_id != old_id:
            d['input_set_id'] = new_id
            d['input_set_count'] = int(d.get('input_set_count') or 0) + 1
            d['input_changed_at'] = _now()
            box.update(new_id=new_id, old_id=old_id, d=d)
            return True
        return False
    if _mut(slug, m):
        d = box.get('d') or {}
        _append(slug, 'input.change', {
            'from': box.get('old_id') or '',
            'to': box.get('new_id') or '',
            'r1': _short((d.get('inputs') or {}).get('r1', {}).get('sha256'), 16),
            'r2': _short((d.get('inputs') or {}).get('r2', {}).get('sha256'), 16),
        }, run_id=run_id)


# --------------------------------------------------------------------------
# 流水线接线（只记录）
# --------------------------------------------------------------------------
def start_run(sample, r1, r2=None, stages=None, sample_dir='', project='',
              logger=None):
    """记录一次运行开始；返回 {run_id, slug, input_set}（失败返回 None）。"""
    def go():
        # 整段在锁内：起跑要读改写同一份 anchor.json，别被后台摘要线程插队
        with _LOCK:
            slug, d = _ensure(sample, sample_dir=sample_dir, project=project)
            # 先只取 size/mtime（毫秒级）；摘要交给后台线程，首次提交不被 GB 级
            # FASTQ 的 sha256 卡住。缓存命中时 _digest 直接回 sha256。
            r1d = _digest(r1, allow_hash=False)
            d['inputs'] = {'r1': r1d}
            if r2:
                d['inputs']['r2'] = _digest(r2, allow_hash=False)
            d['inputs_state'] = ('ok' if all(v.get('sha256')
                                            for v in d['inputs'].values()) else 'hashing')
            run_id = uuid.uuid4().hex[:8]
            iset = _input_set_id(d['inputs'])
            run = {'run_id': run_id, 'started': _now(), 'finished': None,
                   # pid 供 reconcile_interrupted 判活：进程还在就不标 interrupted
                   'pid': os.getpid(),
                   'status': 'running', 'stages': list(stages or []),
                   'stages_done': [], 'stage': '', 'input_set': iset, 'dur_s': None,
                   'outputs_n': 0}
            d.setdefault('runs', []).append(run)
            _save(slug, d)
            _RUNS[slug] = run_id
        _append(slug, 'run.start', {
            'sample': sample, 'sample_dir': d.get('sample_dir') or '',
            'stages': run['stages'], 'input_set': iset,
            'r1': _short(r1d.get('sha256'), 16),
            'r2': _short((d['inputs'].get('r2') or {}).get('sha256'), 16),
            'inputs_state': d['inputs_state'],
        }, run_id=run_id)
        if d['inputs_state'] != 'ok':
            _hash_async(slug, run_id, sample, dict(d['inputs']))
        else:
            _reconcile_input_set(slug, sample, run_id)
        return {'run_id': run_id, 'slug': slug, 'input_set': iset}
    return _safe(go)


def stage_begin(sample, stage):
    """阶段开始（只记事件，供前端显示「现在跑到哪」）。"""
    def go():
        slug = slug_of(sample)
        box = {}

        def m(d):
            r = _current_run(d)
            if r:
                r['stage'] = stage
                box['run_id'] = r.get('run_id') or ''
            return True
        _mut(slug, m)
        _append(slug, 'stage.begin', {'stage': stage},
                run_id=box.get('run_id') or _RUNS.get(slug) or '')
        return True
    return _safe(go, False)


def stage_done(sample, stage, dur_s=None):
    """阶段完成（只记事件 + 时长；产物摘要在 end_run 一次算完）。"""
    def go():
        slug = slug_of(sample)
        box = {}

        def m(d):
            r = _current_run(d)
            if r:
                if stage not in (r.get('stages_done') or []):
                    r.setdefault('stages_done', []).append(stage)
                # stage 不清空：常驻条在阶段间隙也要显示「刚跑完哪一步」
                r['stage_done'] = stage
                box['run_id'] = r.get('run_id') or ''
            return True
        _mut(slug, m)
        _append(slug, 'stage.done', {'stage': stage,
                                     'dur_s': round(dur_s, 2) if dur_s else None},
                run_id=box.get('run_id') or _RUNS.get(slug) or '')
        return True
    return _safe(go, False)


def _produced(sample_dir, since_ts, cap_files=400):
    """本 run 产生的文件（mtime >= since）：小的算 sha256，大的只记 size。"""
    out, hashed, base = [], 0, os.path.abspath(sample_dir)
    for dp, dns, fns in os.walk(base):
        dns[:] = [x for x in dns if not x.startswith('_')]
        for fn in sorted(fns):
            p = os.path.join(dp, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if st.st_mtime < since_ts - 5:
                continue
            rec = {'path': os.path.relpath(p, base).replace('\\', '/'),
                   'size': st.st_size}
            if st.st_size <= HASH_MAX_BYTES and hashed + st.st_size <= HASH_CAP_BYTES:
                dg = _digest(p)
                rec['sha256'] = dg.get('sha256') or ''
                hashed += st.st_size
            out.append(rec)
            if len(out) >= cap_files:
                return out
    return out


def end_run(sample, status='ok', dur_s=None, sample_dir=''):
    """记录运行结束：本 run 产出的文件列表（含关键产物的 sha256）。"""
    def go():
        slug = slug_of(sample)
        run_id = _RUNS.pop(slug, '')
        d = _read_json(_anchor_path(slug))
        since = time.time()
        if d:
            # 先按 run_id 认（不要求 running：reconcile_interrupted 可能已把
            # 本条标成 interrupted）；进程内没有 run_id 时才退回「当前 running」，
            # 再退回最近一条 interrupted（重启后收尾路径）。
            r = _run_by_id(d, run_id) if run_id else _current_run(d)
            if r is None and not run_id:
                runs = d.get('runs') or []
                if runs and (runs[-1].get('status') or '') == 'interrupted':
                    r = runs[-1]
            if r:
                # 只有「本来就没有 run_id」时才认领记录 id：_RUNS 里带进来的
                # id 在锚里找不到时绝不能退而关掉另一条正在跑的记录。
                if not run_id:
                    run_id = r.get('run_id') or ''
                try:
                    since = time.mktime(time.strptime(r['started'],
                                                      '%Y-%m-%dT%H:%M:%S'))
                except (ValueError, OverflowError):
                    pass
        outs = _produced(sample_dir or ((d or {}).get('sample_dir') or ''),
                         since) if (sample_dir or (d or {}).get('sample_dir')) else []

        def m(dd):
            rr = _run_by_id(dd, run_id) if run_id else _current_run(dd)
            if rr:
                rr['status'] = status
                rr['finished'] = _now()
                rr['dur_s'] = round(dur_s, 1) if dur_s else None
                rr['stage'] = ''
                rr['outputs_n'] = len(outs)
                rr['outputs'] = outs[:200]      # 清单进快照，账本里是全量
                return True
            return False
        if d:
            _mut(slug, m)
        _append(slug, 'run.end', {'status': status,
                                  'dur_s': round(dur_s, 1) if dur_s else None,
                                  'outputs': outs,
                                  'outputs_n': len(outs),
                                  'hashed_n': sum(1 for o in outs if o.get('sha256'))},
                run_id=run_id)
        return True
    return _safe(go, False)


def note(sample, kind, **payload):
    """任意补记一条事件（给上层留口子，例如官方的 gate 结论）。"""
    return _safe(lambda: _append(slug_of(sample), kind, payload), False)


def reconcile_interrupted(max_quiet_sec=STALE_SEC):
    """收尾遗留的「running」运行记录（平台启动时调用，2026-11-03）。

    背景：运行记录只被 `end_run` 关闭（正常 ok / 失败 failed 两条路径）。进程
    被杀或平台重启后，anchor.json 里永远留着 `status='running'` 的记录，常驻
    条从此每次打开都显示「正在分析」（点 × 只是本次会话隐藏，刷新就回来）。
    缺的就是「上一进程已经没了」这条收尾路径。

    口径：① 运行记录里带了 pid（2026-11 起）且该进程（**另一个实例**）仍存活
    → 绝不标 interrupted；本进程 pid 不算活（本函数只在启动阶段调用，本进程
    刚起、不可能在跑这批旧记录，同 pid 只可能是上一进程的 PID 被复用）。
    ② 没有 pid 的旧记录退回「账本安静超过 max_quiet_sec（默认 STALE_SEC=30
    分钟）」判定。
    命中后把**所有**未关闭的 running 记录关成 `status='interrupted'`，并按
    正常链路追加一条 `run.interrupted` 账本事件（hash 链保持有效，verify 可过）。

    返回被收尾的样品名列表。
    """
    def go():
        closed = []
        for slug in list_slugs():
            d = _read_json(_anchor_path(slug))
            if not d:
                continue
            open_runs = [x for x in (d.get('runs') or [])
                         if x.get('status') == 'running']
            if not open_runs:
                continue
            # pid 活性优先：只要有一条记录对应的**别的**进程还活着就不收
            # （误标 interrupted 会让 end_run 失去 running 记录，常驻条永久卡住）。
            # 本进程 pid 不算活：reconcile 只在启动阶段调用，本进程刚起、不可能
            # 在跑这批旧记录；记录里出现本进程 pid 只可能是上一进程的 PID 被复用。
            _me = os.getpid()
            pids = [x.get('pid') for x in open_runs if x.get('pid')]
            if any(_pid_alive(p) for p in pids if p != _me):
                continue
            try:
                age = time.time() - time.mktime(time.strptime(
                    (d.get('ledger') or {}).get('updated')
                    or min((x.get('started') or '') for x in open_runs),
                    '%Y-%m-%dT%H:%M:%S'))
            except Exception:
                age = 0        # 时间解析不了 → 按刚活跃处理，不收
            # 有 pid 且全部确认已死 = 强证据，不必再等安静时长；没有 pid
            # 的旧记录只能沿用安静时长兜底（防止误杀另一个实例）。
            if not pids and age < max_quiet_sec:
                continue       # 可能有别的实例正在跑（或刚开始），不动
            # 全部收掉（真机见过 start_run 重复调用留下两条 running 的锚
            # ——GQMIX 2026-11-03：只关最后一条，常驻条仍显示「正在分析」）
            rids = [x.get('run_id') or '' for x in open_runs]

            def m(dd, _rids=rids):
                n = 0
                for rr in dd.get('runs') or []:
                    if rr.get('status') == 'running':
                        rr['status'] = 'interrupted'
                        rr['finished'] = _now()
                        rr['stage'] = ''
                        n += 1
                return n
            _mut(slug, m)
            _append(slug, 'run.interrupted',
                    {'sample': d.get('sample') or slug,
                     'run_ids': rids,
                     'started': min((x.get('started') or '')
                                    for x in open_runs),
                     'quiet_min': round(age / 60.0, 1)},
                    run_id=rids[-1] or '')
            closed.append(d.get('sample') or slug)
        return closed
    return _safe(go, [])


# --------------------------------------------------------------------------
# 读侧
# --------------------------------------------------------------------------
def _summary(slug, d):
    runs = d.get('runs') or []
    run = _current_run(d)
    last = runs[-1] if runs else {}
    status = 'idle'
    stage, started = '', ''
    if run:
        status = 'running'
        stage, started = run.get('stage') or '', run.get('started') or ''
        try:
            age = time.time() - time.mktime(
                time.strptime(d.get('ledger', {}).get('updated') or started,
                              '%Y-%m-%dT%H:%M:%S'))
            if age > STALE_SEC:
                status = 'stale'
        except (ValueError, OverflowError):
            pass
    elif last:
        status = last.get('status') or 'idle'
    inputs = d.get('inputs') or {}
    r1 = (inputs.get('r1') or {})
    r2 = (inputs.get('r2') or {})
    return {
        'slug': slug,
        'run_id': (run or last).get('run_id') or '',
        'sample': d.get('sample') or slug,
        'project': d.get('project') or '',
        'status': status,
        'stage': stage,
        'stage_done': (run or last).get('stage_done') or '',
        'started': started,
        'runs_n': len(runs),
        'stages_done': len((run or last).get('stages_done') or []),
        'stages_total': len((run or last).get('stages') or []) or 15,
        'input_set': d.get('input_set_id') or '',
        'inputs_changed': int(d.get('input_set_count') or 0) > 0,
        'r1_name': os.path.basename(r1.get('path') or ''),
        'r1_sha8': _short(r1.get('sha256'), 8),
        'r1_size': r1.get('size') or 0,
        'r2_name': os.path.basename(r2.get('path') or ''),
        'r2_sha8': _short(r2.get('sha256'), 8),
        'r2_size': r2.get('size') or 0,
        'inputs_state': d.get('inputs_state') or 'empty',
        'ledger_seq': int((d.get('ledger') or {}).get('seq') or 0),
        'finished': (last.get('finished') or ''),
        'dur_s': last.get('dur_s'),
        'outputs_n': last.get('outputs_n') or 0,
    }


def list_slugs():
    def go():
        r = root()
        if not os.path.isdir(r):
            return []
        return sorted(x for x in os.listdir(r)
                      if os.path.isfile(os.path.join(r, x, 'anchor.json')))
    return _safe(go, []) or []


def snapshot(sample_or_slug, ledger_tail=40):
    """单个样品的锚快照（给 /api/anchor/<slug> 与详情弹窗）。"""
    def go():
        slug = sample_or_slug
        if not os.path.isfile(_anchor_path(slug)):
            slug = slug_of(sample_or_slug)
        d = _read_json(_anchor_path(slug))
        if not d:
            return None
        out = _summary(slug, d)
        out['anchor'] = d
        tail = []
        path = _ledger_path(slug)
        if os.path.isfile(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            tail.append(json.loads(line))
            except (OSError, ValueError):
                pass
        out['ledger_tail'] = tail[-int(ledger_tail or 1):]
        return out
    return _safe(go)


def active(limit=6):
    """正在跑的锚（前端底部常驻条用）；带上最近完成的几个。"""
    def go():
        running, recent = [], []
        for slug in list_slugs():
            d = _read_json(_anchor_path(slug))
            if not d:
                continue
            s = _summary(slug, d)
            if s['status'] in ('running', 'stale'):
                running.append(s)
            elif s.get('finished') and s['status'] != 'interrupted':
                # interrupted（启动收尾的遗留运行）不进 recent：常驻条不该
                # 在收尾后再亮 5 分钟「刚完成」误导用户（2026-11-03）
                recent.append(s)
        running.sort(key=lambda x: x.get('started') or '', reverse=True)
        recent.sort(key=lambda x: x.get('finished') or '', reverse=True)
        return {'running': running[:limit], 'recent': recent[:3],
                'now': _now()}
    return _safe(go, {'running': [], 'recent': [], 'now': _now()})


def samples_meta():
    """`/api/samples` 行增强用：slug → 摘要（只读 anchor.json，很便宜）。"""
    def go():
        out = {}
        for slug in list_slugs():
            d = _read_json(_anchor_path(slug))
            if d:
                out[slug] = _summary(slug, d)
        return out
    return _safe(go, {}) or {}


def verify(sample_or_slug):
    """核对链：重算输入摘要 + 复算账本 hash 链，返回差异清单（不改任何文件）。"""
    def go():
        slug = sample_or_slug
        if not os.path.isfile(_anchor_path(slug)):
            slug = slug_of(sample_or_slug)
        d = _read_json(_anchor_path(slug))
        if not d:
            return {'ok': False, 'error': 'anchor 不存在', 'slug': slug}
        problems = []
        # 1) 输入摘要：当前磁盘 vs 锚里记的
        for k in ('r1', 'r2'):
            rec = (d.get('inputs') or {}).get(k) or {}
            if not rec.get('path'):
                continue
            cur = _digest(rec['path'])
            if cur.get('state') != 'ok':
                problems.append('%s 读不到：%s' % (k, rec['path']))
            elif rec.get('sha256') and cur.get('sha256') != rec.get('sha256'):
                problems.append('%s 摘要已变（文件被替换）：%s' % (k, rec['path']))
        # 2) 账本 hash 链
        prev, seq, n = '', 0, 0
        path = _ledger_path(slug)
        if os.path.isfile(path):
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    n += 1
                    ev = json.loads(line)
                    if ev.get('seq') != seq + 1:
                        problems.append('第 %d 行 seq 不连续（%r）' % (n, ev.get('seq')))
                    if (ev.get('prev') or '') != prev:
                        problems.append('第 %d 行 prev 与上一行 hash 不符（链被改动）' % n)
                    prev = hashlib.sha256(line.encode('utf-8')).hexdigest()
                    seq = ev.get('seq') or seq + 1
        return {'ok': not problems, 'slug': slug, 'seq': seq,
                'head': prev, 'problems': problems, 'checked': _now()}
    return _safe(go, {'ok': False, 'error': 'verify 异常'})
