# -*- coding: utf-8 -*-
"""chain_report — 「一键全流程」规范分析报告（材料 / 方法 / 结果）。

输入是一个一键流程的汇总目录（含 `_chain.json`，即 prepchain / kvchain /
virchain / anochain 的运行目录），输出同目录下的 `chain_report.html`：

    一、材料    —— 输入样本、数据量、运行环境（宿主库 / fastp / 参考库）
    二、方法    —— 流程总述、实际执行的步骤与工具、参数表
    三、结果    —— 样本状态、检出病毒、组装/验证计数、失败样本、覆盖深度图
    四、产物与可追溯 —— 关键文件清单（相对路径 + 大小）
    五、局限与口径    —— 数字来源、未做湿验证、阈值影响等

设计纪律（与 report_kit 一致）：

- 所有数字都从落盘产物读（`_chain.json` / `samples.tsv` / identify 汇总表），
  读不到就写「—」，**不估、不补、不编**；
- 单文件 HTML、内嵌 CSS 与图片，离线可开、可直接打印成 PDF；
- 报告只写自己两个文件（`chain_report.html` / `chain_report_meta.json`），
  不碰任何分析产物。

CLI：
    python -m Virus_Platform_Core.chain_report <run 名|目录> [--force] [--open]
"""
from __future__ import annotations

import base64
import csv
import glob
import json
import os
import time

OUT_NAME = 'chain_report.html'
META_NAME = 'chain_report_meta.json'
SCHEMA = 1

#: 单个流程的固定说明（方法学叙述用；与 chains.py 的实现保持一致）
_CHAIN = {
    'prepchain': {
        'title': '样本处理 · 一键前处理',
        'short': '一键前处理',
        'goal': '把 SRA / FASTQ 原始测序数据整理为不含宿主序列的洁净 reads 与 '
                'FASTA，供后续组装、病毒鉴定与定量直接使用。',
        'steps': [
            ('SRA → FASTQ', 'sracha 解码 .sra；单/双端由解码结果决定'),
            ('质控', 'fastp 过滤低质量碱基与接头；可选 --dedup 去 PCR 重复'),
            ('FASTQ → FASTA', '将 kept reads 转 FASTA，作为分类加速输入'),
            ('宿主去除', 'kunpeng 分类，保留非宿主 reads（kept_*.fastq.gz）'),
        ],
    },
    'kvchain': {
        'title': '病毒定量与共识 · 一键分析',
        'short': '一键分析（全流程）',
        'goal': '对每个样本完成「已知病毒识别 → 过滤确认 → 共识序列 → 变异注释」'
                '的完整定量流程，产物可直接用于报告与 NCBI 提交。',
        'steps': [
            ('识别与定量', 'minibwa 比对 + EM 定量（或 salmon），给出每个参考的'
                           '覆盖度、平均深度与比对 reads'),
            ('过滤确认', '按覆盖度、平均深度、reads 数、泊松比阈值筛选“确认存在”的病毒'),
            ('共识序列', '按确认结果生成共识序列并做 QC'),
            ('变异检测', 'bcftools + SnpEff 注释变异位点与效应，输出可视化图'),
        ],
    },
    'virchain': {
        'title': '病毒识别和分类分析 · 一键分析',
        'short': '一键分析',
        'goal': '从去宿主序列出发：先组装为 contigs，再鉴定其中的病毒序列，'
                '最后验证候选序列。',
        'steps': [
            ('① 病毒组装', 'SPAdes（rnaviral 模式）或 MEGAHIT 组装，输出 contigs'),
            ('② 病毒鉴定', 'kunpeng 病毒库对 contigs 分类，提取病毒 contigs'),
            ('③ 候选序列验证', 'blastx / BFVD 结构比对验证候选'),
        ],
    },
    'annochain': {
        'title': '病毒注释分析 · 一键：注释→图谱→NCBI 提交准备',
        'short': '一键：注释→图谱→提交',
        'goal': '对输入核酸 FASTA 一步完成 ORF 预测、功能注释、基因组图谱，'
                '并自动建立 NCBI 提交项目（表行 / 关联注释 / sequences.fsa / '
                'featuretable.tbl / 提交包），元数据到提交页人工完善。',
        'steps': [
            ('kunpeng 分类（可选）', "classify='1' 时链首执行：kunpeng 病毒库"
                                    '分类，提交表 organism/family 自动回填；'
                                    '病毒库未就绪自动降级为手动填写'),
            ('① ORF 预测', 'pyrodigal(-rv) 按最小氨基酸长度预测开放阅读框'),
            ('② 功能注释', '蛋白对照 PVDB 病毒蛋白库（DIAMOND/MMseqs2/blastp）'
                           '+ HMM/CDD/BFVD 域证据 + 类别投票 + ICTV 映射'),
            ('③ 基因组图谱', 'gbdraw 圈图 + 线图（自动带 ORF 注释）'),
            ('④ NCBI 提交准备', '建同名提交项目 → 分类表/FASTA 头建表行 → '
                                 '关联注释运行 → 导出 sequences.fsa → 生成 '
                                 'featuretable.tbl → 打包 submission_package.zip'),
        ],
    },
}

#: identify 汇总表里关心的列（其余列留在原表，报告只取这些）
_HIT_COLS = [
    ('Sample', '样本'), ('Species', '物种'), ('Accession', '登录号'),
    ('Length', '基因组长度'), ('Coverage(%)', '覆盖度 %'),
    ('MeanDepth', '平均深度'), ('EM_Reads', '比对 reads'),
]

_MAX_FIGURES = 4
_MAX_EVIDENCE = 80
#: 步骤摘要优先展示的键（其余短值也收，长路径一律不进表）
_SUMMARY_KEYS = ('stage', 'engine', 'n_confirmed', 'n_variants',
                 'n_variant_genomes', 'n_variant_skipped', 'n_contigs',
                 'n_viral', 'total_bp', 'n_samples', 'n_seqs', 'kept_reads')

# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _fmt_bytes(n):
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return '—'
    if n <= 0:
        return '—'
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return ('%d %s' % (n, unit)) if unit == 'B' else ('%.1f %s' % (n, unit))
        n /= 1024.0
    return '—'


def _fmt_num(v, nd=2):
    if v is None or v == '':
        return '—'
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f - round(f)) < 1e-9:
        return '%d' % int(round(f))
    return ('%.' + str(nd) + 'f') % f


def _esc(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def _read_json(path):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return None


def _read_tsv(path, limit=None):
    """读 TSV → (header, rows[list of dict])；大文件只读前 limit 行。"""
    try:
        with open(path, encoding='utf-8', errors='replace', newline='') as fh:
            rd = csv.DictReader(fh, delimiter='\t')
            rows = []
            for i, row in enumerate(rd):
                if limit is not None and i >= limit:
                    break
                rows.append(dict(row))
            return list(rd.fieldnames or []), rows
    except Exception:
        return [], []


def _rel(path, base):
    try:
        return os.path.relpath(path, base).replace('\\', '/')
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# 采集
# ---------------------------------------------------------------------------


def _sample_hits(sdir, sample):
    """从样本目录里找 identify 汇总表，返回命中行。"""
    cands = [os.path.join(sdir, 'identify_summary.tsv')]
    cands += glob.glob(os.path.join(sdir, 'step_*', 'kvsuite',
                                    'identify', 'all_viruses.summary.tsv'))
    for p in cands:
        if os.path.isfile(p):
            _, rows = _read_tsv(p)
            out = []
            for r in rows:
                if not any((r.get(k) or '').strip() for k in
                           ('Species', 'Accession')):
                    continue
                out.append({
                    'sample': sample,
                    'species': (r.get('Species') or '').strip(),
                    'accession': (r.get('Accession') or '').strip(),
                    'length': r.get('Length'),
                    'coverage': r.get('Coverage(%)'),
                    'depth': r.get('MeanDepth'),
                    'reads': r.get('EM_Reads'),
                    'host': (r.get('Host') or '').strip(),
                })
            return out
    return []


def _sample_figures(sdir, sample):
    """覆盖深度图（每个样本最多一张）。"""
    pats = [os.path.join(sdir, 'step_*', 'kvsuite', 'plots', '*', '*_depth.png'),
            os.path.join(sdir, 'kvsuite', 'plots', '*', '*_depth.png')]
    for pat in pats:
        hits = sorted(glob.glob(pat))
        if hits:
            p = hits[0]
            cap = (os.path.basename(p).replace('_depth.png', '')
                   .replace('_', ' '))
            return [{'path': p, 'caption': '%s 覆盖深度' % cap}]
    return []


def _walk_evidence(base, max_depth=2):
    rows = []
    base_len = len(os.path.abspath(base)) + 1
    for root, dirs, files in os.walk(base):
        depth = root[len(base):].count(os.sep)
        # 不进入 step_* 符号链接目录（内容已通过样本链接与本表覆盖）
        dirs[:] = [d for d in dirs
                   if not d.startswith('step_')
                   and depth < max_depth]
        for fn in files:
            p = os.path.join(root, fn)
            if fn in (OUT_NAME, META_NAME):
                continue
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            rows.append({'rel': os.path.abspath(p)[base_len:].replace('\\', '/'),
                         'size': size})
    rows.sort(key=lambda r: (-r['size'], r['rel']))
    return rows


def collect_chain(run_dir):
    """采集一键流程目录 → 报告模型（只读，不写盘）。"""
    run_dir = os.path.abspath(str(run_dir))
    meta_path = os.path.join(run_dir, '_chain.json')
    meta = _read_json(meta_path)
    if not isinstance(meta, dict):
        raise FileNotFoundError('不是一键流程目录（缺 _chain.json）: %s' % run_dir)

    chain = (meta.get('chain') or '').strip()
    info = _CHAIN.get(chain, {})
    run_name = meta.get('run') or os.path.basename(run_dir)

    # ---- 样本清单（新旧版本兼容：meta.samples → samples.tsv → 单样本） ----
    samples = []
    for s in (meta.get('samples') or []):
        if isinstance(s, dict):
            samples.append(dict(s))
    if not samples:
        tsv = os.path.join(run_dir, 'samples.tsv')
        if os.path.isfile(tsv):
            _, rows = _read_tsv(tsv)
            for r in rows:
                samples.append({
                    'name': (r.get('sample') or '').strip(),
                    'kind': 'fastq', 'pe': bool((r.get('R2') or '').strip()),
                    'r1': (r.get('R1') or '').strip(),
                    'r2': (r.get('R2') or '').strip(),
                    'size': r.get('bytes') or 0,
                    'status': (r.get('status') or '').strip() or 'done',
                    'n_confirmed': r.get('confirmed') or None,
                    'error': (r.get('error') or '').strip(),
                    'links': [x for x in (r.get('files') or '').split(';') if x],
                })
    single = False
    if not samples:
        single = True
        samples = [{
            'name': run_name, 'kind': 'fastq', 'pe': False,
            'r1': meta.get('input') or '', 'r2': meta.get('input2') or '',
            'size': 0, 'status': 'done' if meta.get('done') else 'running',
            'links': [],
        }]

    # ---- 逐样本：产物大小、命中、图 ----
    hits = []
    figures = []
    n_ok = n_failed = 0
    for s in samples:
        name = s.get('name') or ''
        sdir = run_dir if single else os.path.join(run_dir, name)
        if s.get('status') == 'failed':
            n_failed += 1
        else:
            n_ok += 1
        files = []
        for link in (s.get('links') or []):
            p = os.path.join(sdir, link)
            if os.path.isfile(p):
                try:
                    files.append({'name': link, 'size': os.path.getsize(p)})
                except OSError:
                    pass
        s['files'] = files
        if chain == 'kvchain' and os.path.isdir(sdir):
            hits.extend(_sample_hits(sdir, name))
            if len(figures) < _MAX_FIGURES:
                figures.extend(_sample_figures(sdir, name))

    # ---- 物种汇总 ----
    by_species = {}
    for h in hits:
        key = h['species'] or h['accession'] or '未命名'
        rec = by_species.setdefault(key, {'n': 0, 'samples': set(),
                                          'max_cov': 0.0, 'acc': h['accession']})
        rec['n'] += 1
        rec['samples'].add(h['sample'])
        try:
            cov = float(h['coverage'])
        except (TypeError, ValueError):
            cov = 0.0
        rec['max_cov'] = max(rec['max_cov'], cov)

    # ---- 步骤表 ----
    steps = []
    for st in (meta.get('steps') or []):
        if not isinstance(st, dict):
            continue
        summary = st.get('summary') or {}
        kv = ''
        if isinstance(summary, dict):
            # 只挑有意义的计数/引擎，路径与长文本留在_chain.json里
            pick = [k for k in _SUMMARY_KEYS if k in summary]
            pick += [k for k in summary
                     if k not in _SUMMARY_KEYS
                     and not isinstance(summary[k], (list, dict))
                     and len(str(summary[k])) <= 24]
            parts = []
            for k in pick[:6]:
                v = summary[k]
                if v in (None, '', [], {}):
                    continue
                parts.append('%s=%s' % (k, _fmt_num(v)
                                        if not isinstance(v, str) else v))
            kv = '；'.join(parts)
        steps.append({
            'key': st.get('key') or '', 'run': st.get('run') or '',
            'title': st.get('title') or '', 'summary': kv,
            'skipped': st.get('skipped') or '',
            'output': st.get('output') or [],
        })

    params = meta.get('params') or {}

    gaps = []
    for s in samples:
        if s.get('status') == 'failed':
            gaps.append('样本 %s 失败：%s' % (s.get('name'),
                                             (s.get('error') or '见运行日志')[:120]))
    for st in steps:
        if st['skipped']:
            gaps.append('%s 未执行：%s' % (st['title'], st['skipped']))
    if chain == 'kvchain' and not hits and n_ok:
        gaps.append('未检出任何已知病毒（汇总表为空）：可能是阴性样本，'
                    '或参考库未覆盖该病毒；阈值偏严也会导致漏检。')
    if chain == 'prepchain':
        if meta.get('host_db_ready') is False:
            gaps.append('宿主数据库未就绪，宿主去除步骤被跳过——产物仍含宿主 reads。')
        if meta.get('fastp_available') is False:
            gaps.append('fastp 未安装，质控步骤被跳过——reads 未做质量过滤。')

    model = {
        'schema': SCHEMA,
        'chain': chain,
        'label': meta.get('label') or info.get('title') or chain,
        'title': info.get('title') or meta.get('label') or chain,
        'short': info.get('short') or chain,
        'goal': info.get('goal') or '',
        'pipeline': info.get('steps') or [],
        'run': run_name,
        'dir': run_dir,
        'mode': meta.get('mode') or ('single' if single else 'batch'),
        'input_dir': meta.get('input_dir') or '',
        'input': meta.get('input') or '',
        'input2': meta.get('input2') or '',
        'created': meta.get('created') or '',
        'done': bool(meta.get('done')),
        'params': params,
        'n_samples': len(samples),
        'n_ok': n_ok,
        'n_failed': n_failed,
        'samples': samples,
        'steps': steps,
        'hits': hits,
        'by_species': by_species,
        'species_n': len(by_species),
        'figures': figures,
        'gaps': gaps,
        'evidence': _walk_evidence(run_dir),
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'version': _platform_version(),
    }
    return model


def _platform_version():
    try:
        from Virus_Platform_Core import __version__
        return str(__version__)
    except Exception:
        return ''


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

_CSS = """
:root { --ink:#1b1d1c; --soft:#5b625e; --line:#e2ddd3; --paper:#f7f6f3;
        --green:#17513c; --green2:#2f7a5c; --warn:#9a5b00; --bad:#8f2d2d;
        --card:#ffffff; }
* { box-sizing: border-box; }
body { margin:0; background:var(--paper); color:var(--ink);
       font:15px/1.75 "Segoe UI","Microsoft YaHei",system-ui,sans-serif; }
.page { max-width: 980px; margin: 0 auto; padding: 40px 44px 64px; }
.cover { border-top: 6px solid var(--green); padding-top: 26px; margin-bottom: 26px; }
.kicker { color: var(--green2); letter-spacing:.14em; font-size:12px;
          text-transform: uppercase; font-weight:600; }
h1 { font-family: Georgia,"Songti SC","SimSun",serif; font-size: 30px;
     line-height:1.35; margin: 10px 0 8px; color: var(--green); }
.sub { color: var(--soft); font-size: 13.5px; }
.verdict { margin-top: 18px; padding: 12px 16px; border-left: 4px solid var(--green2);
           background: #eef5f0; border-radius: 4px; font-size: 14.5px; }
.verdict.ok { border-color: var(--green2); }
.verdict.warn { border-color: var(--warn); background:#fdf6ea; }
.toc { border:1px solid var(--line); background:var(--card); border-radius:6px;
       padding: 12px 18px; margin: 22px 0 30px; font-size: 13.5px; color:var(--soft); }
.toc a { color: var(--green); text-decoration:none; margin-right: 18px; }
h2 { font-family: Georgia,"Songti SC","SimSun",serif; font-size: 21px;
     color: var(--green); margin: 40px 0 14px; padding-bottom: 6px;
     border-bottom: 1px solid var(--line); }
h2 .no { display:inline-block; width: 30px; color: var(--green2); }
h3 { font-size: 15.5px; margin: 22px 0 8px; color: var(--ink); }
p { margin: 8px 0; }
.muted { color: var(--soft); font-size: 13px; }
.cards { display:flex; flex-wrap: wrap; gap: 12px; margin: 14px 0 6px; }
.card { flex: 1 1 150px; background: var(--card); border:1px solid var(--line);
        border-radius: 6px; padding: 12px 14px; }
.card .k { font-size: 12px; color: var(--soft); }
.card .v { font-size: 22px; font-weight: 600; color: var(--green); margin-top: 2px; }
table { width:100%; border-collapse: collapse; margin: 10px 0 6px;
        background: var(--card); font-size: 13px; }
th, td { border: 1px solid var(--line); padding: 6px 9px; text-align: left;
         vertical-align: top; }
th { background: #eff3ee; font-weight: 600; color: #24352c; }
tr:nth-child(even) td { background: #fbfaf8; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
td.mono { font-family: Consolas,"Courier New",monospace; font-size: 12px; }
.tag { display:inline-block; padding: 1px 8px; border-radius: 10px;
       font-size: 11.5px; border:1px solid var(--line); white-space: nowrap; }
.tag.ok { color: var(--green); border-color:#b9d5c6; background:#eef6f1; }
.tag.bad { color: var(--bad); border-color:#e0bcbc; background:#fbefef; }
img.fig { max-width: 100%; border:1px solid var(--line); border-radius: 4px;
          background:#fff; margin-top: 8px; }
figure { margin: 18px 0; }
figcaption { color: var(--soft); font-size: 12.5px; margin-top: 4px; }
ul { margin: 8px 0 8px 22px; }
li { margin: 4px 0; }
footer { margin-top: 48px; border-top: 1px solid var(--line); padding-top: 12px;
         color: var(--soft); font-size: 12px; }
code { font-family: Consolas,monospace; font-size: 12.5px; background:#f1efe9;
       padding: 1px 5px; border-radius: 3px; }
@media print {
  body { background: #fff; }
  .page { max-width: none; padding: 0 6mm; }
  h2 { page-break-after: avoid; }
  table, figure { page-break-inside: avoid; }
  .toc { display: none; }
}
"""


def _stat_cards(items):
    cells = ''.join('<div class="card"><div class="k">%s</div>'
                    '<div class="v">%s</div></div>' % (_esc(k), _esc(v))
                    for k, v in items)
    return '<div class="cards">%s</div>' % cells


def _table(headers, rows, num_cols=(), mono_cols=()):
    th = ''.join('<th%s>%s</th>' % (' class="num"' if i in num_cols else '',
                                    _esc(h))
                 for i, h in enumerate(headers))
    body = []
    for r in rows:
        tds = []
        for i, cell in enumerate(r):
            cls = []
            if i in num_cols:
                cls.append('num')
            if i in mono_cols:
                cls.append('mono')
            tds.append('<td%s>%s</td>'
                       % (' class="%s"' % ' '.join(cls) if cls else '',
                          cell if cell is not None else '—'))
        body.append('<tr>%s</tr>' % ''.join(tds))
    return '<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>' % (
        th, ''.join(body))


def _section(no, sid, title, body):
    return ('<section id="%s"><h2><span class="no">%s</span>%s</h2>%s</section>'
            % (sid, no, _esc(title), body))


def _verdict(model):
    chain = model['chain']
    ok, failed = model['n_ok'], model['n_failed']
    if chain == 'kvchain':
        n_conf = sum(int(s.get('n_confirmed') or 0) for s in model['samples'])
        txt = ('共 %d 个样本（成功 %d / 失败 %d），检出 %d 个病毒种、%d 条参考命中，'
               '其中确认存在 %d 条。'
               % (model['n_samples'], ok, failed, model['species_n'],
                  len(model['hits']), n_conf))
    elif chain == 'prepchain':
        kept = sum(1 for s in model['samples']
                   for f in s.get('files') or [] if 'kept_' in f['name'])
        fa = sum(1 for s in model['samples']
                 for f in s.get('files') or [] if f['name'].endswith('.fasta'))
        txt = ('共 %d 个样本（成功 %d / 失败 %d），产出 %d 份去宿主 reads、'
               '%d 份 FASTA。' % (model['n_samples'], ok, failed, kept, fa))
    elif chain == 'virchain':
        parts = []
        for st in model['steps']:
            if st['key'] == 'assembly' and st['summary']:
                parts.append('组装：' + st['summary'])
            if st['key'] == 'contigs' and st['summary']:
                parts.append('病毒鉴定：' + st['summary'])
        txt = ('一键流程共 %d 步；%s' % (len(model['steps']),
                                        '；'.join(parts) if parts else '无步骤摘要'))
    else:
        txt = '一键流程共 %d 步，样本数 %d（成功 %d / 失败 %d）。' % (
            len(model['steps']), model['n_samples'], ok, failed)
    cls = 'ok' if failed == 0 else 'warn'
    return '<div class="verdict %s">%s</div>' % (cls, _esc(txt))


def _sec_materials(model):
    rows = [
        ['流程', _esc(model['title'])],
        ['运行', '<code>%s</code>' % _esc(model['run'])],
        ['模式', '目录批量' if model['mode'] == 'batch' else '单样本'],
        ['输入', _esc(model['input_dir'] or model['input'] or '—')
         + ((' / ' + _esc(model['input2'])) if model.get('input2') else '')],
        ['样本数', '%d（成功 %d / 失败 %d）'
         % (model['n_samples'], model['n_ok'], model['n_failed'])],
        ['开始时间', _esc(model['created'] or '—')],
        ['状态', '已完成' if model['done'] else '未完成（缺 done 标记）'],
        ['平台版本', _esc(model['version'] or '—')],
    ]
    if model['chain'] == 'prepchain':
        rows.append(['宿主库 / fastp', '宿主库%s · fastp %s'
                     % ('就绪' if model.get('params', {}).get('host_db_ready')
                        else '未就绪或未记录','可用' if model.get('params', {})
                        .get('fastp_available') else '未安装或未记录')])
    body = ['<h3>1.1 运行概况</h3>',
            _table(['项目', '内容'], rows)]
    srows = []
    for s in model['samples']:
        st = ('<span class="tag ok">完成</span>'
              if s.get('status') != 'failed'
              else '<span class="tag bad">失败</span>')
        srows.append([
            _esc(s.get('name') or '—'),
            '双端' if s.get('pe') else '单端',
            '<span title="%s">%s</span>' % (_esc(s.get('r1') or ''),
                                            _esc(os.path.basename(s.get('r1') or '') or '—')),
            '<span title="%s">%s</span>' % (_esc(s.get('r2') or ''),
                                            _esc(os.path.basename(s.get('r2') or '') or '—'))
            if s.get('r2') else '—',
            _esc(_fmt_bytes(s.get('size'))),
            st,
        ])
    body += ['<h3>1.2 样本清单</h3>',
             _table(['样本', '类型', 'R1', 'R2', '数据量', '状态'], srows,
                    num_cols=(4,))]
    body.append('<p class="muted">数据量为输入文件字节数合计（压缩后）。</p>')
    return ''.join(body)


def _sec_methods(model):
    body = ['<h3>2.1 流程总述</h3>',
            '<p>%s</p>' % _esc(model['goal'])]
    if model['pipeline']:
        rows = [[_esc(a), _esc(b)] for a, b in model['pipeline']]
        body.append(_table(['环节', '实现'], rows))
    if model['steps']:
        rows = []
        for st in model['steps']:
            if st['skipped']:
                state = '<span class="tag bad">跳过</span>'
            elif st['run']:
                state = '<span class="tag ok">完成</span>'
            else:
                state = '—'
            rows.append([
                _esc(st['title'] or st['key']),
                state,
                _esc(st['summary'] or '—'),
                '<code>%s</code>' % _esc(st['run'] or '—'),
            ])
        body += ['<h3>2.2 实际执行的步骤</h3>',
                 _table(['步骤', '状态', '摘要', '运行目录'], rows)]
    params = model.get('params') or {}
    if params:
        rows = [[_esc(k), _esc(v if not isinstance(v, (list, dict))
                            else json.dumps(v, ensure_ascii=False))]
                for k, v in params.items()]
        body += ['<h3>2.3 参数</h3>', _table(['参数', '取值'], rows)]
    else:
        body += ['<h3>2.3 参数</h3>',
                 '<p class="muted">该次运行未记录参数表（旧版本运行或单样本模式）。'
                 '阈值与开关以各步骤运行目录的日志为准。</p>']
    return ''.join(body)


def _sec_results(model):
    _n = [0]

    def h3(title):
        _n[0] += 1
        return '<h3>3.%d %s</h3>' % (_n[0], title)

    cards = [('样本（成功/失败）', '%d（%d / %d）' % (model['n_samples'],
                                                    model['n_ok'],
                                                    model['n_failed']))]
    if model['chain'] == 'kvchain':
        cards += [('检出病毒种', model['species_n']),
                  ('参考命中', len(model['hits'])),
                  ('确认存在', sum(int(s.get('n_confirmed') or 0)
                                   for s in model['samples']))]
    elif model['chain'] == 'prepchain':
        cards += [('去宿主 reads', sum(1 for s in model['samples']
                                       for f in s.get('files') or []
                                       if 'kept_' in f['name'])),
                  ('FASTA', sum(1 for s in model['samples']
                                for f in s.get('files') or []
                                if f['name'].endswith('.fasta')))]
    body = [h3('结果总览'), _stat_cards(cards)]

    if model['chain'] == 'kvchain' and model['hits']:
        rows = []
        for hh in model['hits'][:200]:
            rows.append([
                _esc(hh['sample']), _esc(hh['species'] or '—'),
                '<code>%s</code>' % _esc(hh['accession'] or '—'),
                '<span class="num">%s</span>' % _esc(_fmt_num(hh['length'], 0)),
                _esc(_fmt_num(hh['coverage'])),
                _esc(_fmt_num(hh['depth'])),
                _esc(_fmt_num(hh['reads'], 0)),
            ])
        body += [h3('检出病毒（识别汇总）'),
                 _table(['样本', '物种', '登录号', '基因组长度', '覆盖度 %',
                         '平均深度', '比对 reads'], rows,
                        num_cols=(3, 4, 5, 6), mono_cols=(2,))]
        if model['by_species']:
            rows = []
            for sp, rec in sorted(model['by_species'].items(),
                                  key=lambda kv: (-len(kv[1]['samples']),
                                                  kv[0])):
                rows.append([_esc(sp), '<code>%s</code>' % _esc(rec['acc'] or '—'),
                             '%d' % len(rec['samples']),
                             _esc('%.2f' % rec['max_cov'])])
            body += [h3('物种汇总'),
                     _table(['物种', '代表登录号', '检出样本数', '最高覆盖度 %'],
                            rows, num_cols=(2, 3), mono_cols=(1,))]

    if model['chain'] == 'prepchain':
        rows = []
        for s in model['samples']:
            kept = [f for f in s.get('files') or [] if 'kept_' in f['name']]
            fa = [f for f in s.get('files') or [] if f['name'].endswith('.fasta')]
            rows.append([
                _esc(s.get('name') or '—'),
                '双端' if s.get('pe') else '单端',
                '%d' % len(kept),
                _esc(_fmt_bytes(sum(f['size'] for f in kept))),
                '%d' % len(fa),
                '；'.join('<code>%s</code>' % _esc(f['name']) for f in kept)
                or '—',
            ])
        body += [h3('样本处置结果'),
                 _table(['样本', '类型', 'kept reads', 'kept 体积', 'FASTA',
                         '产物'], rows, num_cols=(2, 3, 4))]

    # 失败样本
    failed = [s for s in model['samples'] if s.get('status') == 'failed']
    if failed:
        rows = [[_esc(s.get('name')), _esc((s.get('error') or '见运行日志')[:300])]
                for s in failed]
        body += [h3('失败样本'), _table(['样本', '错误'], rows)]

    # 图
    if model['figures']:
        figs = []
        for f in model['figures']:
            try:
                with open(f['path'], 'rb') as fh:
                    b64 = base64.b64encode(fh.read()).decode('ascii')
            except Exception:
                continue
            figs.append('<figure><img class="fig" alt="%s" src="data:image/png;'
                        'base64,%s"><figcaption>%s</figcaption></figure>'
                        % (_esc(f['caption']), b64, _esc(f['caption'])))
        if figs:
            body += [h3('覆盖深度'),
                     '<p class="muted">每个样本仅展示第一条参考的深度分布；'
                     '其余见各样本目录 <code>kvsuite/plots/</code>。</p>',
                     ''.join(figs)]
    return ''.join(body)


def _sec_evidence(model):
    rows = []
    for e in model['evidence'][:_MAX_EVIDENCE]:
        rows.append(['<code>%s</code>' % _esc(e['rel']),
                     _esc(_fmt_bytes(e['size']))])
    body = ['<h3>4.1 关键文件</h3>',
            _table(['相对路径', '大小'], rows, num_cols=(1,))]
    if len(model['evidence']) > _MAX_EVIDENCE:
        body.append('<p class="muted">共 %d 个文件，表中仅列最大的 %d 个。</p>'
                    % (len(model['evidence']), _MAX_EVIDENCE))
    body.append('<p class="muted">完整清单：<code>%s</code>（流程清单）与 '
                '<code>samples.tsv</code>（逐样本状态）；各步骤运行目录在 '
                '<code>tool_runs/</code> 下，可按 <code>_chain.json</code> 的 '
                'steps[].run 回查。</p>'
                % _esc(os.path.join(model['run'], '_chain.json')))
    return ''.join(body)


def _sec_limits(model):
    items = [
        '本报告由平台自动生成，所有数字均读自落盘产物（见第四节）；'
        '未做任何实验验证，结论仅供下一步分析与实验设计参考。',
        '覆盖度/深度等阈值会直接影响“确认存在”的判定；阈值写在参数表与各步骤日志中。',
        '混合样本、低载量病毒或参考库未收录的病毒可能漏检；'
        '阴性结果不等于样本不含病毒。',
    ]
    for g in model['gaps']:
        items.append(g)
    return '<ul>%s</ul>' % ''.join('<li>%s</li>' % _esc(x) for x in items)


def render_chain_html(model):
    h = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         '<title>%s · %s · 规范分析报告</title>' % (_esc(model['label']),
                                                    _esc(model['run'])),
         '<style>%s</style></head><body><div class="page">' % _CSS]
    h.append('<header class="cover">'
             '<div class="kicker">VirusPlatform · 一键全流程 · 规范报告</div>'
             '<h1>%s</h1><div class="sub">运行 <code>%s</code> · %s · '
             '%d 个样本</div>%s</header>'
             % (_esc(model['title']), _esc(model['run']),
                _esc(model['created'] or ''), model['n_samples'],
                _verdict(model)))
    h.append('<nav class="toc">目录：'
             '<a href="#materials">一、材料</a>'
             '<a href="#methods">二、方法</a>'
             '<a href="#results">三、结果</a>'
             '<a href="#evidence">四、产物与可追溯</a>'
             '<a href="#limits">五、局限与口径</a></nav>')
    h.append(_section('一', 'materials', '材料', _sec_materials(model)))
    h.append(_section('二', 'methods', '方法', _sec_methods(model)))
    h.append(_section('三', 'results', '结果', _sec_results(model)))
    h.append(_section('四', 'evidence', '产物与可追溯', _sec_evidence(model)))
    h.append(_section('五', 'limits', '局限与口径', _sec_limits(model)))
    h.append('<footer>生成时间 %s · 平台版本 %s · 目录 <code>%s</code></footer>'
             % (_esc(model['generated']), _esc(model['version'] or '—'),
                _esc(model['dir'])))
    h.append('</div></body></html>')
    return ''.join(h)


# ---------------------------------------------------------------------------
# 对外
# ---------------------------------------------------------------------------


def build_chain_report(run_or_dir, force=False, out_path=None, logger=None):
    """生成（或复用）一键流程规范报告。

    run_or_dir：`tool_runs/` 下的运行名，或任意含 `_chain.json` 的目录。
    返回 {'html','meta','model','reused'}。
    """
    run_dir = str(run_or_dir)
    if not os.path.isdir(run_dir):
        from Virus_Platform_Core.web.state import run_real_dir
        run_dir = run_real_dir(run_dir)
    model = collect_chain(run_dir)
    html_path = out_path or os.path.join(model['dir'], OUT_NAME)
    meta_path = os.path.join(os.path.dirname(html_path), META_NAME)
    if not force and _fresh(html_path, model):
        if logger:
            logger.log('复用既有 %s（产物未变更）' % OUT_NAME)
        return {'html': html_path, 'meta': meta_path, 'model': model,
                'reused': True}
    html = render_chain_html(model)
    tmp = html_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(html)
    os.replace(tmp, html_path)
    with open(meta_path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(_meta_of(model), fh, ensure_ascii=False, indent=1)
    if logger:
        logger.log('规范报告：%s（%.0f KB）'
                   % (html_path, os.path.getsize(html_path) / 1024.0))
    return {'html': html_path, 'meta': meta_path, 'model': model,
            'reused': False}


def _fresh(html_path, model):
    if not os.path.isfile(html_path):
        return False
    try:
        mt = os.path.getmtime(html_path)
    except OSError:
        return False
    return mt >= _newest_mtime(model['dir'])


def _newest_mtime(base):
    newest = 0.0
    for root, dirs, files in os.walk(base):
        depth = root[len(base):].count(os.sep)
        dirs[:] = [d for d in dirs if not d.startswith('step_')
                   and depth < 2]
        for fn in files:
            if fn in (OUT_NAME, META_NAME):
                continue
            try:
                newest = max(newest, os.path.getmtime(os.path.join(root, fn)))
            except OSError:
                continue
    return newest


def _meta_of(model):
    return {
        'schema': SCHEMA,
        'kind': 'chain',
        'chain': model['chain'],
        'run': model['run'],
        'dir': model['dir'],
        'mode': model['mode'],
        'n_samples': model['n_samples'],
        'n_ok': model['n_ok'],
        'n_failed': model['n_failed'],
        'n_steps': len(model['steps']),
        'n_hits': len(model['hits']),
        'species_n': model['species_n'],
        'n_figures': len(model['figures']),
        'n_evidence': len(model['evidence']),
        'gaps': model['gaps'],
        'created': model['created'],
        'generated': model['generated'],
    }


def main(argv=None):
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    target = argv[0]
    force = '--force' in argv
    try:
        out = build_chain_report(target, force=force)
    except Exception as e:                      # CLI 边界：原文转述
        print('[失败] %s: %s' % (type(e).__name__, e), file=sys.stderr)
        return 1
    print(out['html'])
    if '--open' in argv:
        try:
            os.startfile(out['html'])
        except Exception:
            pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
