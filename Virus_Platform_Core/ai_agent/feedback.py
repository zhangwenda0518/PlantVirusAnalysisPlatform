# -*- coding: utf-8 -*-
"""AI 回答反馈（👍👎）落盘（2026-11-04，跨会话用户偏好的最低成本形态）。

为什么需要：用户在对话坞给某条回答点 👎 并写一句理由，下个会话里模型
应当知道「这类答复用户不满意」。最省的形态就是一条 append-only JSONL：
不建数据库、不做向量检索、不做归纳总结——只在 build_context 时把
recent() 的最近几条拼进系统上下文。注入钩子在 chat.py（本模块**不**
import chat，避免循环依赖），本模块只负责写入与读回。

存储口径（与 sessions.py 同一套防线）：
    <RUN_ROOT>/ai_feedback.jsonl，只追加，threading.Lock 串行化；
    崩溃最坏丢最后半行（读侧逐行容错，解析不了的行直接跳过）；
    conv_id 白名单 [A-Za-z0-9_-]{6,64}（防路径/注入类脏值）；
    verdict 只认 up / down；note 截 500 字符。

行格式：
    {"ts": "2026-11-04T14:22:31", "conv_id": "c_xxx", "idx": 3,
     "verdict": "down", "note": "表格行数对不上"}

recent() 的人读形态（供系统上下文/排障直接看）：
    👎 conv=c_xxx #3 「表格行数对不上」(11-04 14:22)
"""
import datetime
import json
import os
import re
import threading

from ..config import RUN_ROOT

_LOCK = threading.Lock()
_FILE = os.path.join(RUN_ROOT, 'ai_feedback.jsonl')
_ID_RE = re.compile(r'^[A-Za-z0-9_\-]{6,64}$')
_VERDICTS = ('up', 'down')
_NOTE_MAX = 500
_LABEL = {'up': '👍', 'down': '👎'}


def _now_iso():
    """本地时间的 ISO 8601 字符串（秒级；人读与机器解析都够用）。"""
    return datetime.datetime.now().isoformat(timespec='seconds')


def _read_all():
    """逐行解析整个文件（旧 → 新）；缺文件 / 坏行一律安全跳过。"""
    out = []
    try:
        with open(_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue              # 半行（崩溃残留）：跳过
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        return []
    return out


def append(conv_id, msg_idx, verdict, note=''):
    """追加一条反馈。返回 {'ok': True, ...写入的记录} 或 {'ok': False, 'error': ...}。

    校验失败（conv_id 不合法 / verdict 非 up|down / idx 非整数）一律不落盘；
    写盘失败也返回 ok:False，不抛异常给 HTTP 层。
    """
    cid = str(conv_id or '')
    if not _ID_RE.fullmatch(cid):
        return {'ok': False,
                'error': 'conv_id 不合法（6~64 位字母/数字/_/-）'}
    v = str(verdict or '').strip()
    if v not in _VERDICTS:
        return {'ok': False, 'error': "verdict 只允许 'up' / 'down'"}
    try:
        idx = int(msg_idx)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'msg_idx 须为整数'}
    rec = {'ts': _now_iso(), 'conv_id': cid, 'idx': idx, 'verdict': v,
           'note': str(note or '')[:_NOTE_MAX]}
    line = json.dumps(rec, ensure_ascii=False, sort_keys=True) + '\n'
    with _LOCK:
        try:
            os.makedirs(os.path.dirname(_FILE), exist_ok=True)
            with open(_FILE, 'a', encoding='utf-8') as f:
                f.write(line)
        except OSError as e:
            return {'ok': False, 'error': f'反馈写入失败: {e}'}
    out = {'ok': True}
    out.update(rec)
    return out


def items(n=20):
    """最近 n 条**原始记录**（旧 → 新；缺文件返回 []）。GET 端点直接用。"""
    try:
        n = max(1, min(200, int(n)))
    except (TypeError, ValueError):
        n = 20
    return _read_all()[-n:]


def _fmt(rec):
    """一条原始记录 → 人读单行（系统上下文 / 排障用）。"""
    label = _LABEL.get(str(rec.get('verdict') or ''), '·')
    cid = str(rec.get('conv_id') or '?')
    idx = rec.get('idx')
    note = str(rec.get('note') or '').replace('\r', ' ').replace('\n', ' ')
    try:
        when = datetime.datetime.fromisoformat(str(rec.get('ts'))).strftime(
            '%m-%d %H:%M')
    except (TypeError, ValueError):
        when = str(rec.get('ts') or '')
    tail = f' 「{note}」' if note else ''
    return f'{label} conv={cid} #{idx}{tail}({when})'


def recent(n=8):
    """最近 n 条的人读字符串列表（旧 → 新）；由 build_context 注入上下文。"""
    return [_fmt(r) for r in items(n)]
