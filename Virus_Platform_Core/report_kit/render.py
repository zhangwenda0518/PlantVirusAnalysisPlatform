# -*- coding: utf-8 -*-
"""渲染层：模型 → 单文件 HTML。

分工：本模块只管「骨架 + 页面组织 + 图/表/代码的通用构件」，
每一节的正文逻辑放在 sections.py，便于逐节改文字而不动版式。
"""
from __future__ import annotations

import base64
import html
import os
import re
import time

from .styles import CSS, JS, CSS_MOBILE

# ------------------------------------------------------------------
# 转义与通用构件
# ------------------------------------------------------------------


def esc(s):
    """HTML 转义；None → 空串（渲染层绝不把 None 打进正文）。"""
    if s is None:
        return ''
    return html.escape(str(s), quote=True)


def link_evidence(rel, label=None, depth=1):
    """证据文件 → 相对链接。

    depth=报告所在目录相对运行根的层级：样品报告在 <root>/07_report/ 下
    （depth=1），工具运行报告在运行根下（depth=0）。此前无条件加 '../'，
    工具运行报告的证据链接会指到父目录。
    """
    rel = str(rel or '')
    lbl = label or rel
    if not rel:
        return ''
    href = rel if rel.startswith(('#', 'http')) else '../' * depth + rel.lstrip('./')
    return f'<a href="{esc(href)}" title="{esc(rel)}">{esc(lbl)}</a>'


def ev_list(items, prefix=''):
    if not items:
        return ''
    spans = ''.join(f'<span>{esc(x)}</span> ' for x in items if x)
    return f'<span class="ev">{prefix}{spans}</span>'


def table(headers, rows, caption='', num_cols=(), mono_cols=(), hl_rows=()):
    """通用表格。rows 为二维 list；单元格里的 dict 可带 'cls'。

    约定：**表格里的单元格一律当纯文本转义**（数据来自 TSV，可能带尖括号或
    引号），需要富文本时传 dict{'html': ...}——这样才不会出现"半个标签"
    把整个表格撑坏的情况。
    """
    if not rows:
        return ''
    out = ['<div class="tbl-wrap"><table>']
    if caption:
        out.append(f'<caption>{esc(caption)}</caption>')
    out.append('<thead><tr>')
    for i, h in enumerate(headers):
        cls = ' class="num"' if i in num_cols else ''
        out.append(f'<th{cls}>{esc(h)}</th>')
    out.append('</tr></thead><tbody>')
    for ri, row in enumerate(rows):
        if isinstance(row, str):
            # 防御：调用方误传了拼好的 <tr> 字符串（此前 6 处调用点如此，
            # 导致每个字符被拆成一个单元格）。这里按单列渲染兜底，
            # 并在文档里重申约定（真实修复在 sections.py 调用侧）。
            row = [row]
        cls = ' class="hl"' if ri in hl_rows else ''
        out.append(f'<tr{cls}>')
        for ci, cell in enumerate(row):
            base = 'num' if ci in num_cols else (
                'mono' if ci in mono_cols else '')
            extra, content = '', None
            if isinstance(cell, dict):
                extra = cell.get('cls') or ''
                content = cell.get('html')
            if content is None:
                content = esc(cell)
            klass = ' '.join(x for x in (base, extra) if x)
            c = f' class="{klass}"' if klass else ''
            out.append(f'<td{c}>{content}</td>')
        out.append('</tr>')
    out.append('</tbody></table></div>')
    return ''.join(out)


def code_block(text, caption=''):
    body = esc(text.strip())
    cap = f'<div class="small muted" style="margin:10px 0 -6px">{esc(caption)}</div>' if caption else ''
    return f'{cap}<pre data-copy="1">{body}</pre>'


# ------------------------------------------------------------------
# 图片内嵌
# ------------------------------------------------------------------

_MIME = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
         '.webp': 'image/webp', '.gif': 'image/gif'}
_NOIMG_LIMIT = 6 * 1024 * 1024     # 单图 >6 MB 不外嵌（避免 HTML 爆掉）


def img_data_uri(path):
    ext = os.path.splitext(path)[1].lower()
    mime = _MIME.get(ext)
    if not mime:
        return None
    try:
        if os.path.getsize(path) > _NOIMG_LIMIT:
            return None
        with open(path, 'rb') as fh:
            raw = fh.read()
    except OSError:
        return None
    return f'data:{mime};base64,' + base64.b64encode(raw).decode('ascii')


def figure(path, caption='', width=None, root=''):
    """图 → <figure>。位图走 base64 内嵌；SVG 直接内联（平台自产、离线可看）。

    SVG 若非良构（缺 </svg> 等）则跳过不渲染，绝不内嵌残缺矢量。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == '.svg':
        return _svg_figure(path, caption, root)
    uri = img_data_uri(path)
    if not uri:
        return ''
    rel = os.path.relpath(path, root).replace('\\', '/') if root else path
    style = f' style="max-width:{width}px"' if width else ''
    cap = (f'<figcaption>{caption}<span class="muted">　'
           f'{esc(rel)}</span></figcaption>') if caption else ''
    return f'<figure><img src="{uri}" alt="{esc(os.path.basename(path))}"{style}>{cap}</figure>'


_SVG_INLINE_MAX = 4 * 1024 * 1024      # 单个 SVG >4MB 不内联（HTML 体积闸）


def _svg_figure(path, caption='', root=''):
    try:
        if os.path.getsize(path) > _SVG_INLINE_MAX:
            return ''
        with open(path, encoding='utf-8', errors='replace') as fh:
            txt = fh.read()
    except OSError:
        return ''
    i = txt.find('<svg')
    if i < 0 or '</svg>' not in txt[i:]:
        return ''
    svg = txt[i:]
    rel = os.path.relpath(path, root).replace('\\', '/') if root else path
    cap = (f'<figcaption>{caption}<span class="muted">　'
           f'{esc(rel)}</span></figcaption>') if caption else ''
    return (f'<figure><div class="fig-svg">{svg}</div>{cap}</figure>')


# ------------------------------------------------------------------
# 页面骨架
# ------------------------------------------------------------------

def _toc(items):
    """目录：items = [(id, no, title, [(sub_id, sub_title), ...])]"""
    out = ['<nav class="toc"><h2>目录</h2><ol>']
    for sid, no, title, subs in items:
        out.append(f'<li><a href="#{esc(sid)}">{esc(no)}　{esc(title)}</a>')
        if subs:
            out.append('<ol>')
            for ss, st in subs:
                out.append(f'<li class="sub"><a href="#{esc(ss)}">{esc(st)}</a></li>')
            out.append('</ol>')
        out.append('</li>')
    out.append('</ol></nav>')
    return ''.join(out)


def section(sid, no, title, body, cls=''):
    if not body:
        return ''
    return (f'<section id="{esc(sid)}" class="{esc(cls)}">'
            f'<h2 class="sec"><span class="no">{esc(no)}</span>{esc(title)}</h2>'
            f'{body}</section>')


def render_full(model):
    """模型 → 完整 HTML 文本。"""
    from . import sections as S

    blocks = model['profile'].get('blocks') or ['summary', 'stages']
    built = []          # [(id, no, title, html_block, [(sub_id, sub_title)])]

    builders = [
        ('summary', '§1', '执行摘要', S.sec_summary),
        ('funnel', '§2', '流程漏斗与证据链', S.sec_funnel),
        ('stages', '§3', '逐阶段深读', S.sec_stages),
        ('viro', '§4', '病毒学结论与证据链', S.sec_viro),
        ('gaps', '§5', '证据缺口与口径边界', S.sec_gaps),
        ('qa', '§6', 'AI 任务问答卡', S.sec_qa),
        ('cmds', '§7', '可复现命令', S.sec_cmds),
        ('methods', '§8', '方法与数据来源', S.sec_methods),
        ('next', '§9', '下一步', S.sec_next),
    ]
    for key, no, title, fn in builders:
        if key not in blocks:
            continue
        try:
            body, subs = fn(model)
        except Exception as e:      # 单节失败不拖垮整份报告（降级不崩溃）
            body = (f'<div class="note bad">本节渲染失败：'
                    f'{esc(type(e).__name__)}: {esc(e)}</div>')
            subs = []
        if body:
            built.append((key, no, title, body, subs))

    head = _render_head(model)
    cover = _render_cover(model)
    abstract = S.render_abstract(model)

    body = [cover]
    if abstract:
        body.append(abstract)
    for sid, no, title, bh, subs in built:
        body.append(section(sid, no, title, bh))

    toc = _toc([(sid, no, title, subs) for sid, no, title, _b, subs in built])
    footer = _render_footer(model)

    return (f'<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n{head}\n</head>\n'
            f'<body>\n<div class="page">\n{toc}\n<main>\n'
            + '\n'.join(body) +
            f'\n{footer}\n</main>\n</div>\n<script>{JS}</script>\n'
            f'</body>\n</html>\n')


def _render_head(model):
    t = esc(model['profile'].get('title') or '分析报告')
    name = esc((model.get('sample') or {}).get('name') or '')
    return (f'<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<meta name="generator" content="VirusPlatform report_kit">\n'
            f'<title>{name} · {t} · 植物病毒分析平台</title>\n'
            f'<style>{CSS}{CSS_MOBILE}</style>')


def _render_cover(model):
    prof = model['profile']
    sm = model.get('sample') or {}
    name = sm.get('name') or os.path.basename(model['layout']['root'])
    chips = []
    if sm.get('created'):
        chips.append(('建样时间', str(sm.get('created')).replace('T', ' ')))
    if sm.get('last_run'):
        chips.append(('本次运行', str(sm.get('last_run')).replace('T', ' ')))
    if sm.get('last_duration_s') is not None:
        chips.append(('总耗时', f"{float(sm['last_duration_s']):.0f} s"))
    n_stage = len(model['stages'])
    chips.append(('已产出阶段', f'{n_stage} 个'))
    chips.append(('生成时间', model['generated']))
    meta = ''.join(f'<span>{esc(k)}　<b>{esc(v)}</b></span>' for k, v in chips)
    verdict = model.get('verdict') or {}
    vcls = {'ok': 'tag', 'note': 'tag gray', 'warn': 'tag warn',
            'fail': 'tag bad'}.get(verdict.get('level', ''), 'tag gray')
    return (f'<header class="cover">'
            f'<div class="kicker">VirusPlatform · 植物病毒分析平台</div>'
            f'<h1>{esc(name)}　{esc(prof.get("title") or "分析报告")}</h1>'
            f'<p class="subtitle">{esc(prof.get("subtitle") or "")}'
            f'　<span class="{vcls}">{esc(verdict.get("text") or "")}</span></p>'
            f'<div class="meta">{meta}</div>'
            f'</header>')


def _render_footer(model):
    root = model['layout']['root']
    return (f'<footer>'
            f'本报告由 VirusPlatform 的 report_kit 自 <b>{esc(root)}</b> 的落盘产物'
            f'自动生成，所有数字均可按 §8.3 的溯源表逐条回查；'
            f'未在该目录中出现的阶段一律不写结论。'
            f'　生成于 {esc(model["generated"])}。'
            f'</footer>')


def write_report(model, path):
    html_text = render_full(model)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(html_text)
    return path
