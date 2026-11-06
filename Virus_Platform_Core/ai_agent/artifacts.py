"""对话坞产物卡片（2026-11-02）。

用户在对话坞里问「报告在哪 / 图在哪」时，模型过去只能回一段**裸路径**：
点不开、看不出是什么、还得自己去资源管理器里翻。这个模块把「这一轮到底
产出了/提到了哪些文件」整理成结构化清单，随 SSE 的 ``artifacts`` 事件下发，
前端渲染成可点的卡片（图片内联、HTML 内嵌预览或新标签打开、表格前几行）。

三条硬约束（与平台既有安全口径一致）：

1. **只认平台目录内的文件**：平台根、results / tool_runs / downloads /
   meta_search / logan / submissions / logs（这些随「输出目录」设置变化）、
   示例目录。工作空间在平台之外时，只有落在上述根里的文件才会出现。
2. **只读**：这里只做 ``os.path.isfile`` / ``getsize``，不复制、不改写、
   不遍历目录树。
3. **不猜**：路径必须**真实存在**才进清单——解析出来的字符串宁可丢掉，
   也不当成"应该有这个文件"渲染出来（否则卡片点开是 404）。

模块不 import Flask：``url`` 字段只是拼给前端的相对地址，
``/api/fs/raw`` 仍是唯一出口，仍由它做越界校验。
"""

import json
import os
import re
import time
from urllib.parse import quote

# 渲染方式：image 内联 / html 内嵌 iframe / table 前几行 / text 纯文本 / pdf 打开
_KIND = {
    '.html': 'html', '.htm': 'html',
    '.png': 'image', '.jpg': 'image', '.jpeg': 'image',
    '.gif': 'image', '.svg': 'image', '.webp': 'image', '.bmp': 'image',
    '.pdf': 'pdf',
    '.tsv': 'table', '.csv': 'table',
    '.md': 'text', '.txt': 'text', '.log': 'text', '.json': 'text',
    '.faa': 'text', '.ffn': 'text', '.fasta': 'text', '.fa': 'text',
    '.fastq': 'text', '.fq': 'text', '.aln': 'text', '.nwk': 'text',
    '.gff': 'text', '.gff3': 'text', '.vcf': 'text', '.bed': 'text',
}

# 卡片排序：报告优先，其次图，再次表
_RANK = {'html': 0, 'pdf': 1, 'image': 2, 'table': 3, 'text': 4}

MAX_ITEMS = 10                    # 一轮最多给几张卡（再多就成文件管理器了）
MAX_BYTES = 512 * 1024 * 1024     # 超过 512MB 的不进卡片（打不开也没意义）
_PREVIEW_LIMIT = 40               # 从一段工具 JSON 里最多捞 40 个候选再排序

# Windows 绝对路径 / 平台相对路径。故意保守：不匹配空白、引号与中日韩标点，
# 避免把「见 D:\a\b.html（983,662 字节）与 D:\a\c.html」整句吞成一个路径。
# 注意不能排除全部非 ASCII —— 平台根目录本身就可能含中文（如 D:\桌面\…）。
_CJK_PUNCT = '\u3000\u3001\u3002\uff08\uff09\uff0c\uff1b\uff1a\uff01\uff1f' \
             '\u300a\u300b\u3010\u3011\u300c\u300d\u300e\u300f' \
             '\u201c\u201d\u2018\u2019\u2026\u00b7\uff5e\u2014\u2013'
_RE_WIN = re.compile(r'[A-Za-z]:[\\/][^\s<>"\'|*?\r\n' + _CJK_PUNCT + r']+')
_RE_REL = re.compile(r'(?:^|[\s(（\'"])((?:run[\\/](?:results|tool_runs|downloads|logs|tasks)'
                     r'|examples)[\\/][^\s<>"\'|*?\r\n' + _CJK_PUNCT + r']+)')
_TRAIL = ' \t\r\n.,;:!?、。，；：）)】」』"\'`>' + _CJK_PUNCT


def _roots():
    """允许出现的根目录（跟着「输出目录」设置走）。"""
    out = []
    try:
        from Virus_Platform_Core.config import DIRS, PLATFORM_ROOT
    except Exception:                     # pragma: no cover - 极端import失败
        return out
    out.append(PLATFORM_ROOT)
    for key in ('results', 'tool_runs', 'downloads', 'meta_search',
                'logan', 'submissions', 'logs', 'examples'):
        p = DIRS.get(key)
        if p:
            out.append(p)
    seen, uniq = set(), []
    for r in out:
        try:
            n = os.path.normcase(os.path.normpath(os.path.abspath(r)))
        except (OSError, ValueError):
            continue
        if n and n not in seen:
            seen.add(n)
            uniq.append(os.path.normpath(os.path.abspath(r)))
    return uniq


def _inside(path, roots=None):
    try:
        p = os.path.normcase(os.path.normpath(os.path.abspath(path)))
    except (OSError, ValueError):
        return False
    for r in (roots if roots is not None else _roots()):
        rn = os.path.normcase(r)
        if p == rn or p.startswith(rn + os.sep):
            return True
    return False


def _sensitive_paths():
    """永不进卡片的敏感路径：AI Key 真身与会话镜像（2026-11-06）。

    _roots() 含平台根，`run/ai_local.json` 天然落在里面；以前模型只要在
    正文/工具 JSON 里提到它就能生成一张可点开的卡片，点开即经
    /api/fs/raw 返回明文 Key。这里在候选与构造两层都排除。
    """
    out = []
    try:
        from Virus_Platform_Core.config import RUN_ROOT, _ai_local_path
        out.append(_ai_local_path())
        out.append(os.path.join(RUN_ROOT, 'ai_sessions'))
    except Exception:                     # pragma: no cover - 极端import失败
        return out
    uniq = []
    for p in out:
        try:
            n = os.path.normcase(os.path.realpath(os.path.abspath(p)))
        except (OSError, ValueError):
            continue
        if n:
            uniq.append(n)
    return uniq


def _is_sensitive(path, sensitive=None):
    try:
        p = os.path.normcase(os.path.realpath(os.path.abspath(path)))
    except (OSError, ValueError):
        return False
    for sp in (sensitive if sensitive is not None else _sensitive_paths()):
        if p == sp or p.startswith(sp + os.sep):
            return True
    return False


def kind_of(path):
    return _KIND.get(os.path.splitext(str(path))[1].lower(), 'file')


def _item(path):
    """把一个真实存在的文件变成卡片 dict；不可用则返回 None。"""
    ap = os.path.abspath(path)
    if _is_sensitive(ap):
        return None
    try:
        if not os.path.isfile(ap):
            return None
        size = os.path.getsize(ap)
        mtime = os.path.getmtime(ap)
    except OSError:
        return None
    if size > MAX_BYTES:
        return None
    kind = kind_of(ap)
    name = os.path.basename(ap)
    rel = ''
    for r in _roots():
        try:
            if os.path.normcase(ap).startswith(os.path.normcase(r) + os.sep):
                rel = os.path.relpath(ap, r)
                break
        except ValueError:                # 跨盘符
            continue
    return {
        'path': ap,
        'name': name,
        'rel': rel.replace(os.sep, '/'),
        'dir': os.path.dirname(rel).replace(os.sep, '/') if rel else '',
        'kind': kind,
        'ext': os.path.splitext(name)[1].lower().lstrip('.'),
        'size': size,
        'mtime': time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime)),
        'url': '/api/fs/raw?path=' + quote(ap, safe=''),
    }


def collect(paths, limit=MAX_ITEMS):
    """把一批路径整理成卡片清单（去重、按渲染价值排序）。"""
    roots = _roots()
    sensitive = _sensitive_paths()
    seen, items = set(), []
    for raw in (paths or []):
        p = str(raw or '').strip()
        if not p:
            continue
        ap = p if os.path.isabs(p) else os.path.join(
            roots[0] if roots else os.getcwd(), p)
        if _is_sensitive(ap, sensitive):
            continue
        if not _inside(ap, roots):
            continue
        key = os.path.normcase(os.path.normpath(ap))
        if key in seen:
            continue
        seen.add(key)
        it = _item(ap)
        if it:
            items.append(it)
    items.sort(key=lambda x: (_RANK.get(x['kind'], 9), x['name'].lower()))
    return items[:limit] if limit else items


def from_text(text):
    """从回答正文里捞路径（模型常把产物写成裸路径）。"""
    if not text:
        return []
    found = []
    for m in _RE_WIN.finditer(str(text)):
        found.append(m.group(0).rstrip(_TRAIL))
    for m in _RE_REL.finditer(str(text)):
        found.append(m.group(1).rstrip(_TRAIL))
    return found


def _walk_paths(node, acc):
    if len(acc) >= _PREVIEW_LIMIT:
        return
    if isinstance(node, dict):
        for v in node.values():
            _walk_paths(v, acc)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _walk_paths(v, acc)
    elif isinstance(node, str):
        s = node.strip()
        if not s or len(s) > 400:
            return
        # 只认「像路径并且扩展名可渲染」的字符串，避免把普通文本当文件
        if kind_of(s) == 'file':
            return
        if '\\' in s or '/' in s:
            acc.append(s)


def from_json_blob(text):
    """从工具返回的 JSON 里捞路径（html / 图片 / 表格等键值）。"""
    if not text:
        return []
    s = str(text).strip()
    if not s or s[0] not in '{[':
        return []
    try:
        obj = json.loads(s)
    except ValueError:
        return []
    acc = []
    _walk_paths(obj, acc)
    return acc


def finish(paths, limit=MAX_ITEMS):
    """一句话搞定：解析 + 去重 + 排序 + 裁剪。"""
    return collect(paths, limit=limit)


def describe(items):
    """给模型看的一行摘要（回灌进下一轮上下文，避免它重复贴路径）。"""
    if not items:
        return ''
    kinds = {}
    for it in items:
        kinds[it['kind']] = kinds.get(it['kind'], 0) + 1
    parts = [f'{k} {n}' for k, n in sorted(kinds.items(), key=lambda kv: kv[0])]
    return f'{len(items)} 个产物卡片（{"、".join(parts)}）'


def output_root_brief():
    """当前「输出目录」设置 → 给系统提示用的一句话。"""
    try:
        from Virus_Platform_Core.config import PLATFORM_ROOT, get_output_root
        root = (get_output_root() or '').strip()
    except Exception:                     # pragma: no cover
        return ''
    if not root:
        return f'输出目录：平台默认（{os.path.join("run", "results")}）'
    where = os.path.join(root, 'results')
    return (f'输出目录：用户自定义 {root}（样品结果在 {where}，'
            f'工具运行在同一根的 tool_runs 下）')


def is_platform_default_output():
    try:
        from Virus_Platform_Core.config import get_output_root
        return not (get_output_root() or '').strip()
    except Exception:                     # pragma: no cover
        return True
