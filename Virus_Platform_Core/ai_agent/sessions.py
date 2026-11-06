# -*- coding: utf-8 -*-
"""AI 对话会话的服务端留存（2026-11-03，借鉴 shim 的 append-only JSONL）。

为什么需要：对话坞的历史原来只存浏览器 localStorage——换浏览器、清缓存、
重装系统就全没了。这里把每段会话镜像落盘到 run/ai_sessions/<conv_id>.jsonl，
localStorage 仍是即时备份，服务端是跨浏览器/跨清理的持久层。

写入口径（只追加语义）：
    POST {conv_id, msgs} 每次带**全量**消息列表（前端 _saveHistory 的同一份
    裁剪结果），服务端与盘上已有行做前缀比对（role / content / partial /
    artifacts 全部纳入比较）：
      · 盘上是 incoming 的前缀（正常聊天只会往后长）→ 只追加尾部，文件永不
        改写——崩溃最坏丢最后半行，前面的行永远完好；
      · 前缀不再吻合（重试截断 / 清空重聊）→ 临时文件整份写好 + os.replace
        原子替换，同样不会出现"写一半"的中间态。

防线：conv_id 白名单 [A-Za-z0-9_-]{6,64}（防路径穿越）；单条 content 截断
24K 字符；条数上限 200；artifacts 只留 {path|href} 形状的对象。
读侧 GET 原样返回行数组，不做任何推断。
"""
import json
import os
import re
import threading

from ..config import RUN_ROOT

_LOCK = threading.Lock()
_DIR = os.path.join(RUN_ROOT, 'ai_sessions')
_ID_RE = re.compile(r'^[A-Za-z0-9_\-]{6,64}$')
_MAX_MSGS = 200
_MAX_CHARS = 24000


def _path(conv_id):
    return os.path.join(_DIR, str(conv_id) + '.jsonl')


def _norm_msg(m):
    """清洗一条前端消息 → 可落盘的 dict；不合法返回 None。"""
    if not isinstance(m, dict):
        return None
    role = m.get('role')
    content = str(m.get('content') or '')
    if role not in ('user', 'assistant') or not content.strip():
        return None
    rec = {'role': role, 'content': content[:_MAX_CHARS]}
    arts = m.get('artifacts')
    if isinstance(arts, list) and arts:
        clean = [a for a in arts if isinstance(a, dict)
                 and (a.get('path') or a.get('href'))][:12]
        if clean:
            rec['artifacts'] = clean
    if m.get('partial'):
        rec['partial'] = True
    return rec


def _same_msg(old, new):
    """两条消息是否完全等价（含 partial / artifacts 元数据）。

    2026-11-06 加固：以前只比 role/content。前端「先落最终文本、再把产物
    卡片挂到同一条消息上二次保存」时，前缀判定为相等 → 走只追加快路径、
    tail 为空直接返回，artifacts 永远落不了盘；partial 标记残留同理。
    元数据差异也视为前缀断裂 → 原子重写（整份文件很短，代价可忽略）。
    """
    return (old.get('role') == new.get('role')
            and old.get('content') == new.get('content')
            and bool(old.get('partial')) == bool(new.get('partial'))
            and (old.get('artifacts') or []) == (new.get('artifacts') or []))


def _read_lines(path):
    out = []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass                  # 半行（崩溃残留）：跳过
    except OSError:
        return []
    return out


def read(conv_id):
    """整段会话的消息列表（不存在/不合法 → 空列表）。"""
    if not _ID_RE.fullmatch(str(conv_id or '')):
        return []
    return _read_lines(_path(conv_id))


def save(conv_id, msgs):
    """镜像保存一段会话（快路径只追加，前缀断裂时原子重写）。"""
    if not _ID_RE.fullmatch(str(conv_id or '')):
        return {'ok': False, 'error': 'conv_id 不合法（6~64 位字母/数字/_/-）'}
    if not isinstance(msgs, list):
        return {'ok': False, 'error': 'msgs 须为数组'}
    clean = [m for m in (_norm_msg(x) for x in msgs) if m]
    if len(clean) > _MAX_MSGS:
        clean = clean[-_MAX_MSGS:]
    with _LOCK:
        os.makedirs(_DIR, exist_ok=True)
        path = _path(conv_id)
        old = _read_lines(path)
        # 快路径：盘上内容是 incoming 的前缀 → 只追加尾部。
        # 逐条比较 role/content/partial/artifacts（见 _same_msg）。
        if len(old) <= len(clean) and all(
                _same_msg(o, c) for o, c in zip(old, clean[:len(old)])):
            tail = clean[len(old):]
            if not tail:
                return {'ok': True, 'n': len(clean), 'appended': 0}
            with open(path, 'a', encoding='utf-8') as f:
                for m in tail:
                    f.write(json.dumps(m, ensure_ascii=False,
                                       sort_keys=True) + '\n')
            return {'ok': True, 'n': len(clean), 'appended': len(tail)}
        # 前缀断裂（重试/清空）：临时文件写好再原子替换
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            for m in clean:
                f.write(json.dumps(m, ensure_ascii=False,
                                   sort_keys=True) + '\n')
        os.replace(tmp, path)
        return {'ok': True, 'n': len(clean), 'rewritten': True}


def list_convs(limit=30):
    """全部会话的摘要（历史菜单以后可用；按修改时间倒序）。"""
    try:
        names = os.listdir(_DIR)
    except OSError:
        return []
    rows = []
    for fn in names:
        if not fn.endswith('.jsonl'):
            continue
        cid = fn[:-6]
        if not _ID_RE.fullmatch(cid):
            continue
        p = _path(cid)
        try:
            st = os.stat(p)
        except OSError:
            continue
        lines = _read_lines(p)
        first_q = next((m.get('content', '')[:40]
                        for m in lines if m.get('role') == 'user'), '')
        rows.append({'conv_id': cid, 'n': len(lines), 'mtime': st.st_mtime,
                     'first': first_q})
    rows.sort(key=lambda x: x['mtime'], reverse=True)
    return rows[:max(1, min(100, int(limit or 30)))]
