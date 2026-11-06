# -*- coding: utf-8 -*-
"""事实抽取层：目录 → 统一的报告模型（report model）。

这是 report_kit 的**唯一数据入口**。渲染层（render.py / sections.py）
不允许自己去 os.path 逛目录，只能消费本模块产出的 dict——这样
「哪一句结论来自哪个文件」可以逐条追溯（每条 finding/gap 都带 evidence）。

模型结构（全 dict，JSON 可序列化，便于 report_v2_meta.json 与测试）：

    {
      'generated': '2026-11-02 09:50',
      'layout': {kind, root, out_dir, inputs[], names_zh},
      'sample': {name, project, input{r1,r2}, params{}, stages_done[],
                 last_status, last_duration_s, created},
      'profile': {key, title, subtitle, stages[], modules[], desc},
      'stages': [stage_view],           # 实际产出的阶段（按流程顺序）
      'metrics': {kpi, funnel, ...},    # 跨阶段汇总
      'modules': {mod: {...}},          # 工具箱单跑的事实
      'findings': [finding],            # 结论（带 evidence）
      'gaps':     [gap],                # 口径边界 / 缺口 / 风险
      'contradictions': [c],
      'evidence': [relpath],            # 报告引用的全部文件
      'verdict': {'level': 'ok|warn|fail|none', 'text': '...'},
      'tools': [{name, version, path}],
    }

    stage_view = {
      'key', 'title', 'num', 'dir', 'status': 'done|partial|missing',
      'files': [{'rel','name','kind','size'}],
      'summary': {...},                  # 该阶段 summary.json 原文（可能有裁剪）
      'facts': [...],                    # 人读的事实条目
      'notes': [...],                    # 读取过程中的异常/告警原文
      'figures': [...],                  # 可内嵌的图（绝对路径，同 stem 已去重）
      'figure_twins': {主图: 孪生图},     # PNG↔SVG 伴生路径（页面格式切换用）
      'duration_s': float|None,
    }

红线：所有数值来自落盘文件；读不到就是 None，**绝不估算**。口径冲突
（例如「记录位点数」vs「真实变异数」）在这一层标记，在渲染层并列展示。
"""
from __future__ import annotations

import glob
import json
import os
import re
import time

from ..io_utils import fnum, read_dicts

# ------------------------------------------------------------------
# 常量
# ------------------------------------------------------------------

#: 样品流程阶段的展示顺序与中文名（数字与平台 UI 的圈码一致：
#: ⓪b 与 ③b/③c 是「插在阶段之间」的补跑阶段，平台界面沿用同款叫法）
STAGE_VIEW = [
    # (阶段键, 界面叫法（含英文括号时会被拆掉）, 短名, 运行日志里的标签)
    ('subsample', '⓪a 下采样', '下采样', '下采样'),
    ('fastp',    '⓪ 质控', '质控', '质控'),
    ('fq2fa',    '⓪b 序列转换', '序列转换', '序列转换'),
    ('host',     '① 宿主去除', '宿主去除', '宿主去除'),
    ('kvsuite',  '②b 已知病毒识别与定量', '已知病毒定量', '已知病毒识别与定量'),
    ('assembly', '③ 组装·分类·提取', '组装', '组装·分类·提取'),
    ('hostana',  '④ 宿主预测', '宿主预测', '宿主预测'),
    ('verify',   '③b 候选序列验证', '序列验证', '候选序列验证'),
    ('consensus', '③c 共识序列与变异', '共识/变异', '共识序列与变异'),
    ('orf',      '⑥ ORF 预测', 'ORF', 'ORF 预测'),
    ('orfa',     '⑥b ORF 功能注释', 'ORF 注释', 'ORF 功能注释'),
    ('phylo',    '⑦ 进化树与 SDT', '系统发育', '进化树与 SDT'),
    ('primer',   '⑧ 引物设计', '引物', '引物设计'),
    ('gbdraw',   '⑨ 基因组图', '基因组图', '基因组图'),
    ('report',   '⑩ 报告', '报告', '可视化报告'),
]
#: 各阶段较完整的界面叫法（报告标题里用，括号内容不丢）
STAGE_FULL = {
    'fastp': '⓪ 质控（fastp）',
    'fq2fa': '⓪b 序列转换（FASTQ→FASTA）',
    'hostana': '④ 宿主预测（ICTV）',
    'gbdraw': '⑨ 基因组图（gbdraw/DFV）',
    'report': '⑩ 可视化报告',
}
STAGE_ORDER = [k for k, _z, _s, _l in STAGE_VIEW]
STAGE_ZH = {k: z for k, z, _s, _l in STAGE_VIEW}
STAGE_SHORT = {k: s for k, _z, s, _l in STAGE_VIEW}
STAGE_LABEL = {k: l for k, _z, _s, l in STAGE_VIEW}

#: 阶段目录名（与 STAGE_ORDER 一一对应；②b 的目录名取自 kv_stage.STAGE_DIR）
STAGE_DIR = {
    'subsample': '00_prep', 'fastp': '00_prep', 'fq2fa': '00_prep',
    'host': '01_host_removal', 'kvsuite': '02b_kvsuite',
    'assembly': '03_assembly', 'hostana': '08_host_analysis',
    'verify': '03b_verify', 'consensus': '03c_consensus', 'orf': '04_orf',
    'orfa': '04b_orf_annot', 'phylo': '05_phylo', 'primer': '06_primer',
    'gbdraw': '09_genome_plots', 'report': '07_report',
}

#: 判定某阶段「跑过」的产物指纹（任一存在即算 done）
STAGE_FINGERPRINT = {
    # ⓪a 下采样实际写的是 00_prep/subsample.json + sub_R1.fastq.gz
    # （pipeline.py:_stage_subsample）；旧值 stats.json/summary.json 属 ①/其他阶段，
    # 导致 ⓪a 阶段指纹永远判 missing。
    'subsample': ['subsample.json', 'sub_R1.fastq.gz'],
    'fastp': ['fastp_report.json', 'fastp.json'],
    'fq2fa': ['conv_R1.fa.gz', 'fq2fa.json'],
    'host': ['stats.json'],
    'kvsuite': ['summary.json', 'identify/all_viruses.summary.tsv',
                'viral_R1.fastq.gz'],
    'assembly': ['summary.json', 'contigs.fasta', 'spades.log'],
    'hostana': ['summary.json'],
    'verify': ['summary.json', 'calls.tsv'],
    'consensus': ['summary.json', 'coverage.tsv'],
    'orf': ['summary.json'],
    'orfa': ['summary.json'],
    'phylo': ['summary.json'],
    'primer': ['summary.json'],
    'gbdraw': ['summary.json'],
    'report': ['report.html'],
}
#: 阶段对应的 summary 文件名（首个存在的即用）
STAGE_SUMMARY = {
    'subsample': 'subsample.json', 'fastp': 'fastp_report.json',
    'fq2fa': 'fq2fa.json', 'host': 'stats.json',
    'kvsuite': 'summary.json', 'assembly': 'summary.json',
    'hostana': 'summary.json', 'verify': 'summary.json',
    'consensus': 'summary.json', 'orf': 'summary.json',
    'orfa': 'summary.json', 'phylo': 'summary.json',
    'primer': 'summary.json', 'gbdraw': 'summary.json',
}

FIGURE_EXTS = ('.png', '.jpg', '.jpeg', '.webp', '.svg')
TABLE_EXTS = ('.tsv', '.csv')
DATA_EXTS = ('.json',)
MAX_FIGURES_PER_STAGE = 6
MAX_STAGE_FILES = 40


# ------------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------------

def _read_json(path, notes=None, rel=''):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            return json.load(fh)
    except Exception as e:
        if notes is not None:
            notes.append(f'{rel or path} 解析失败（已跳过）：{type(e).__name__}: {e}')
        return None


def _rel(root, path):
    try:
        return os.path.relpath(path, root).replace('\\', '/')
    except ValueError:
        return path


def _fmt(v, digits=2, unit=''):
    """数值 → 人读字符串；None → '—'（渲染层统一用它，不再写 None）。"""
    if v is None or v == '':
        return '—'
    try:
        f = float(v)
    except (TypeError, ValueError):
        return f'{v}{unit}'
    if abs(f - round(f)) < 1e-9 and abs(f) >= 1000:
        return f'{int(round(f)):,}{unit}'
    if abs(f - round(f)) < 1e-9:
        return f'{int(round(f))}{unit}'
    return f'{f:.{digits}f}{unit}'


def _pct(v, digits=2):
    if v is None or v == '':
        return '—'
    try:
        return f'{float(v):.{digits}f}%'
    except (TypeError, ValueError):
        return str(v)


def _safe_len(seq):
    try:
        return len(seq)
    except TypeError:
        return None


def read_fasta_stats(path):
    """轻量 FASTA 统计：条数 / 总长 / 最长。文件缺失返回 None。"""
    if not os.path.isfile(path):
        return None
    n, total, longest, length = 0, 0, 0, 0
    name = ''
    try:
        fh = open(path, 'rt', encoding='utf-8', errors='replace')
    except OSError:
        return None
    with fh:
        for line in fh:
            if line.startswith('>'):
                if n:
                    total += length
                    longest = max(longest, length)
                n += 1
                name = line[1:].strip().split()[0] if len(line) > 1 else ''
                length = 0
            else:
                length += len(line.strip())
        if n:
            total += length
            longest = max(longest, length)
    return {'n': n, 'total_bp': total, 'longest': longest, 'first': name}


# ------------------------------------------------------------------
# 目录识别：样品结果目录 vs 工具箱运行目录
# ------------------------------------------------------------------

def _looks_like_sample(root):
    """样品目录判定：有 project.json，或同时有多个 0X_* 阶段目录。"""
    if os.path.isfile(os.path.join(root, 'project.json')):
        return True
    dirs = {n for n in os.listdir(root)
            if os.path.isdir(os.path.join(root, n))}
    known = set(STAGE_DIR.values()) | {'04c_contig_annot'}
    return len(dirs & known) >= 2


def _tool_run_name(root):
    """工具运行目录名形如 `<tool>_20261102_030036_67debb`。"""
    base = os.path.basename(os.path.normpath(root))
    m = re.match(r'^([a-z0-9]+?)_(\d{8}_\d{6})(?:_([0-9a-f]{4,8}))?$', base)
    return (m.group(1), m.group(2), m.group(3) or '') if m else None


def _stage_dirs_present(root):
    """样品目录下实际存在的阶段目录集合。"""
    out = {}
    for k, d in STAGE_DIR.items():
        p = os.path.join(root, d)
        if os.path.isdir(p):
            out[k] = p
    return out


# ------------------------------------------------------------------
# 任务档案（profile）识别
# ------------------------------------------------------------------
#: 档案定义：命中的条件按「阶段指纹」表达，order 从宽到窄匹配。
#: 命中后决定：报告标题、章节重点、以及「这个任务到底要回答什么问题」。
PROFILES = [
    {'key': 'full', 'title': '全流程病毒诊断报告',
     'subtitle': '从原始 FASTQ 到可提交序列的完整证据链',
     'need': ['kvsuite', 'assembly'],
     'blocks': ['summary', 'funnel', 'stages', 'viro', 'gaps', 'qa',
                'cmds', 'methods', 'next'],
     'questions': ['样品里有什么病毒？', '量有多少？', '序列能不能用？',
                   '还缺什么才能下结论？']},
    {'key': 'assembly', 'title': '组装与序列验证报告',
     'subtitle': '把 reads 拼成 contigs，再判定每一条是什么',
     'need': ['assembly'],
     'blocks': ['summary', 'funnel', 'stages', 'viro', 'gaps', 'qa',
                'cmds', 'methods', 'next'],
     'questions': ['拼出了哪些序列？', '每条序列是什么？', '哪些能当基因组用？']},
    {'key': 'phylo', 'title': '系统发育定位报告',
     'subtitle': '把样品序列放回参考树，看它站哪一枝',
     'need': ['phylo'],
     'blocks': ['summary', 'stages', 'viro', 'gaps', 'qa', 'cmds',
                'methods', 'next'],
     'questions': ['样品序列与哪条参考最近？', '能否据此归种？',
                   '树的拓扑有多可信？']},
    {'key': 'primer', 'title': '引物与检测方案报告',
     'subtitle': '给检出目标设计可用于实验室验证的引物',
     'need': ['primer'],
     'blocks': ['summary', 'stages', 'viro', 'gaps', 'qa', 'cmds',
                'methods', 'next'],
     'questions': ['设计出几对引物？', '特异性怎么验？', '下一步做哪个实验？']},
    {'key': 'diagnose', 'title': '已知病毒快速诊断报告',
     'subtitle': '不组装，直接回答「这株植物带了什么已知病毒」',
     'need': ['kvsuite'],
     'blocks': ['summary', 'funnel', 'stages', 'viro', 'gaps', 'qa',
                'cmds', 'methods', 'next'],
     'questions': ['检出哪些已知病毒？', '哪个是高丰度真信号？',
                   '哪些只是背景/低信号？']},
    {'key': 'prep', 'title': '数据预处理报告',
     'subtitle': '质控与去宿主：决定下游还能看到多少病毒',
     'need': ['host'],
     'blocks': ['summary', 'funnel', 'stages', 'gaps', 'qa', 'cmds',
                'methods', 'next'],
     'questions': ['宿主污染占多少？', '去掉宿主后还剩多少 reads？',
                   '有没有把病毒一起丢掉的风险？']},
    {'key': 'tool', 'title': '工具箱单步分析报告',
     'subtitle': '单次工具运行的输入、参数与结论',
     'need': [],
     'blocks': ['summary', 'stages', 'gaps', 'cmds', 'methods', 'next'],
     'questions': ['这次跑的是什么？', '结果说明什么？', '下一步接哪个工具？']},
]


def detect_profile(root):
    """识别目录属于哪类任务 → profile dict（含命中的阶段）。"""
    root = os.path.abspath(str(root))
    if not os.path.isdir(root):
        raise FileNotFoundError(f'目录不存在: {root}')

    tr = _tool_run_name(root)
    if tr and not _looks_like_sample(root):
        tool, ts, _uid = tr
        prof = dict(next(p for p in PROFILES if p['key'] == 'tool'))
        prof.update({'stages': [], 'dirs': {}, 'tool': tool, 'stamp': ts})
        return prof

    dirs = _stage_dirs_present(root)
    hit = set()
    for k, p in dirs.items():
        fp = STAGE_FINGERPRINT.get(k) or []
        if any(os.path.exists(os.path.join(p, f)) for f in fp):
            hit.add(k)

    for prof in PROFILES:
        if prof['key'] == 'tool':
            continue
        if all(s in hit for s in prof['need']):
            out = dict(prof)
            out.update({'stages': [k for k in STAGE_ORDER if k in hit],
                        'dirs': dirs, 'tool': None, 'stamp': ''})
            return out

    # 兜底：认不出的目录按「有阶段目录就按阶段列报」处理。
    # 2026-11-02：排除 report 阶段——07_report/ 只是报告产物目录，把它算成
    # 「跑过 ⑩」会让同一目录每生成一次报告就多出一个阶段（自观测）。
    out = dict(next(p for p in PROFILES if p['key'] == 'tool'))
    out.update({'title': '目录结果报告', 'tool': None, 'stamp': '',
                'stages': [k for k in STAGE_ORDER
                           if k in dirs and k != 'report'],
                'dirs': dirs})
    return out


# ------------------------------------------------------------------
# 各阶段事实抽取
# ------------------------------------------------------------------

def _stage_files(root, sdir, notes):
    """列出阶段目录下的产物（按类型粗分），并给出人读条目。"""
    files = []
    if not os.path.isdir(sdir):
        return files
    for dirpath, dirnames, filenames in os.walk(sdir):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith('.') and d not in ('logs',)]
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            ext = os.path.splitext(fn)[1].lower()
            kind = ('figure' if ext in FIGURE_EXTS else
                    'table' if ext in TABLE_EXTS else
                    'data' if ext in DATA_EXTS else
                    'seq' if ext in ('.fasta', '.fa', '.fna', '.faa', '.ffn',
                                     '.gff', '.gff3', '.gff2', '.vcf', '.nwk',
                                     '.pdf', '.aln', '.m8', '.tsv.gz')
                    else 'other')
            files.append({'rel': _rel(root, p), 'name': fn, 'kind': kind,
                          'size': size, 'path': p})
            if len(files) >= MAX_STAGE_FILES:
                break
        if len(files) >= MAX_STAGE_FILES:
            break
    return files


def _figure_pair_rank(path):
    """同 stem 孪生图的优先级：位图优先（报告内嵌 PNG），SVG 只当伴生。"""
    ext = os.path.splitext(path)[1].lower()
    order = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg')
    try:
        return order.index(ext)
    except ValueError:
        return len(order)


def pair_figures(paths, limit=None):
    """图路径 → (主图列表, {主图: 孪生路径})，按 stem 去重。

    平台静态图统一 PNG+SVG 双格式（viz._save_fig）。若两种载体各算一个条目，
    report_v2 的图表配额会被孪生图吃掉：GQMIX 的 07_report 有 3 对（6 条目），
    逐阶段深读只留前 4 条 → 只剩 2 对，`stage_funnel_retention` 整图丢失；
    ⑦ 的 3 棵树同样只出 2 棵。这里把同目录同 stem 的图并成一个条目，
    位图优先，SVG 记录在 figure_twins 里供页面 PNG/SVG 切换。

    limit 在**去重后**才截断（先截断会按旧口径丢整对）。
    """
    groups, order = {}, []
    for p in paths or []:
        p = str(p)
        key = (os.path.dirname(p), os.path.splitext(os.path.basename(p))[0])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(p)
    mains, twins = [], {}
    for key in order:
        cand = sorted(groups[key], key=lambda x: (_figure_pair_rank(x), x))
        main = cand[0]
        if len(cand) > 1:
            twins[main] = cand[1]
        mains.append(main)
    if limit is not None:
        return mains[:limit], twins
    return mains, twins


def _facts_subsample(s, files, notes):
    f = []
    if not s:
        return f
    # 00_prep/subsample.json（pipeline.py:_stage_subsample）的实际字段是
    # n_pairs / mode / seed / prop / inputs；旧版本曾写 pairs / fraction。
    # 两种都认，否则阶段摘要读到了却一条事实都摘不出来。
    pairs = s.get('pairs') if s.get('pairs') is not None else s.get('n_pairs')
    if pairs is not None:
        f.append(('保留 read 对', _fmt(pairs), '下采样后实际进入流程的 reads'))
    frac = (s.get('fraction') if s.get('fraction') is not None
            else s.get('prop'))
    if frac:
        f.append(('抽样比例', _fmt(frac, 4), '按比例随机抽取'))
    if s.get('mode'):
        f.append(('抽样模式', s.get('mode'), 'count=按条数抽样 / proportion=按比例'))
    if s.get('seed') not in (None, ''):
        f.append(('随机种子', s.get('seed'), '复现同一次抽样用'))
    return f


def _facts_fastp(s, files, notes):
    f = []
    if not s:
        return f
    sm = (s.get('summary') or {})
    before = (sm.get('before_filtering') or {})
    after = (sm.get('after_filtering') or {})
    if s.get('reads_before') is not None:
        f.append(('质控前 reads', _fmt(s.get('reads_before')), 'fastp 原始读数'))
    elif before.get('total_reads') is not None:
        f.append(('质控前 reads', _fmt(before.get('total_reads')), 'fastp 原始读数'))
    if s.get('reads_after') is not None:
        f.append(('质控后 reads', _fmt(s.get('reads_after')), '过 Q/长度过滤后'))
    elif after.get('total_reads') is not None:
        f.append(('质控后 reads', _fmt(after.get('total_reads')), '过 Q/长度过滤后'))
    q30 = s.get('q30_rate') or after.get('q30_rate')
    if q30 is not None:
        try:
            q30v = float(q30)
            q30s = f'{q30v * 100:.2f}%' if q30v <= 1 else f'{q30v:.2f}%'
        except (TypeError, ValueError):
            q30s = str(q30)
        f.append(('Q30 比例', q30s, '碱基质量 ≥ Q30 的比例'))
    dup = s.get('dup_rate') or after.get('duplication', {}).get('rate')
    if dup is not None:
        try:
            dv = float(dup)
            f.append(('重复率', f'{dv * 100:.2f}%' if dv <= 1 else f'{dv:.2f}%',
                      'fastp 估计的 read 重复比例'))
        except (TypeError, ValueError):
            pass
    return f


def _facts_fq2fa(s, files, notes):
    f = []
    for ff in files:
        if ff['kind'] == 'other' and ff['name'].endswith(('.fa.gz', '.fa')):
            st = read_fasta_stats(ff['path']) if False else None  # gz 不展开
            if st:
                f.append((ff['name'], _fmt(st['n']), 'FASTA 条目数'))
    return f


def _facts_host(s, files, notes):
    f = []
    if not s:
        return f
    se = bool(s.get('single_end'))
    tp = fnum(s.get('total_pairs'))
    kp = fnum(s.get('kept_pairs'))
    if tp is not None:
        f.append(('输入 read 对', _fmt(tp), '去宿主前的双端对数'))
    if kp is not None:
        f.append(('保留 read 对', _fmt(kp), '未被判为宿主的序列'))
    ratio = fnum(s.get('host_ratio'))
    if ratio is not None:
        f.append(('宿主占比', f'{ratio * 100:.2f}%', '被判为宿主基因组的比例'))
    if kp is not None and tp:
        keep_ratio = kp / tp * 100.0
        f.append(('保留率', f'{keep_ratio:.2f}%', '宿主去除后的留存比例'))
    for k, label in (('host_name', '宿主物种'), ('host_taxid', '宿主 taxid'),
                     ('db', '宿主库'), ('classifier', '分类器')):
        if s.get(k):
            f.append((label, str(s[k]), '① 使用的宿主判定依据'))
    if se:
        f.append(('数据形态', '单端（single-end）', 'kept_pairs 即单条 reads 计数'))
    return f


def _facts_kvsuite(s, files, notes):
    f = []
    if not s:
        return f
    for k, label, hint in (
            ('viral_reads', '病毒 reads', '被判定属于病毒基因组的 reads'),
            ('n_species', '检出物种数', '通过阈值的主检结果'),
            ('passed', '通过阈值物种', '通过 min_cov/min_depth/min_reads/泊松阈值'),
            ('rejected', '剔除物种', '命中但未过阈值'),
    ):
        if s.get(k) is not None:
            f.append((label, _fmt(s.get(k)), hint))
    sp = s.get('species_detected')
    if isinstance(sp, list) and sp:
        f.append(('检出物种', '、'.join(str(x.get('species') or x.get('name') or x)
                                    if isinstance(x, dict) else str(x)
                                    for x in sp[:6]) +
                  ('…' if len(sp) > 6 else ''), '主检表逐行可查'))
    if s.get('viral_reads_quality'):
        f.append(('病毒 reads 质量', str(s.get('viral_reads_quality')),
                  '质量列不是原始测序值时应警惕'))
    return f


def _facts_assembly(s, files, notes):
    f = []
    if not s:
        return f
    for k, label, hint in (
            ('contigs', 'contigs 数', '组装产出的序列条数'),
            ('total_bp', '总长度', '所有 contigs 碱基数之和'),
            ('n50', 'N50', '长度加权中位数'),
            ('longest', '最长 contig', '单条最长序列长度'),
            ('engine', '组装引擎', '本次使用的组装器与模式'),
    ):
        if s.get(k) is not None:
            f.append((label, _fmt(s.get(k)), hint))
    vc = s.get('viral_contigs')
    if isinstance(vc, list):
        f.append(('判为病毒的 contigs', str(len(vc)), '经 kunpeng 分类后保留'))
        if vc:
            f.append(('病毒 contig 名', ', '.join(str(x) for x in vc[:8]) +
                      ('…' if len(vc) > 8 else ''), '后续③c/⑥/⑨ 的输入'))
    return f


def _facts_hostana(s, files, notes):
    f = []
    if not s:
        return f
    if s.get('n_contigs') is not None:
        f.append(('参与预测的 contigs', _fmt(s.get('n_contigs')), '④ 的输入条数'))
    cats = s.get('categories') or {}
    if cats:
        f.append(('宿主类别分布', '；'.join(f'{k} {v}' for k, v in cats.items()),
                  'ICTV 级联后的最终宿主'))
    meth = s.get('decision_methods') or {}
    if meth:
        f.append(('判定方式', '；'.join(f'{k} {v}' for k, v in meth.items()),
                  'ICTV_Meta_Agree=元数据与分类一致，ICTV_Prob=仅概率'))
    if s.get('n_host_warn') is not None:
        f.append(('宿主告警', _fmt(s.get('n_host_warn')), '宿主与 metadata 冲突条数'))
    return f


def _facts_verify(s, files, notes):
    f = []
    if not s:
        return f
    calls = s.get('calls') or {}
    if calls:
        f.append(('判定分布', '；'.join(f'{k} {v}' for k, v in calls.items()),
                  'known=已知种，domain_only=只命中蛋白域，novel=新序列'))
    if s.get('n_calls') is not None:
        f.append(('参与验证序列', _fmt(s.get('n_calls')), '③b 的输入条数'))
    if s.get('db_versions'):
        f.append(('数据库版本', _fmt_db(s.get('db_versions')), '比对库快照'))
    return f


def _facts_consensus(s, files, notes):
    f = []
    if not s:
        return f
    for k, label, hint in (
            ('n_refs', '共识序列条数', '有参考可映射的 contigs'),
            ('n_mapped_reads', '映射 reads', '实际比对到参考上的 reads'),
            ('n_variants', '变异记录位点', '含低覆盖 N 位点，不等于真实变异数'),
            ('n_snv', '纯 SNV 位点', '主等位占比达阈值的单核苷酸变异'),
            ('n_isnv', 'iSNV 位点', '同一位置存在次等位，可能为混侵染/准种'),
    ):
        if s.get(k) is not None:
            f.append((label, _fmt(s.get(k)), hint))
    return f


def _facts_orf(s, files, notes):
    f = []
    if not s:
        return f
    if s.get('min_aa') is not None:
        f.append(('最短 ORF 阈值', f"{s.get('min_aa')} aa", '低于此长度的 ORF 被丢弃'))
    for eng in ('pyrodigal', 'pyrodigal_rv', 'orfipy'):
        v = s.get(eng)
        if isinstance(v, dict):
            n = v.get('genes', v.get('n_orfs'))
            drop = v.get('dropped_short')
            txt = f'{_fmt(n)} 个基因' + (f'（丢弃 {drop} 条短 ORF）'
                                        if drop else '')
            f.append((eng, txt, '同一批序列的正向/反向模型分别预测，'
                                '两套结果是同一批 ORF 的两套坐标'))
    if s.get('n_orfs') is not None:
        f.append(('ORF 总数（orfipy）', _fmt(s.get('n_orfs')), '六框预测计数'))
    return f


def _facts_orfa(s, files, notes):
    f = []
    if not s:
        return f
    for k, label, hint in (
            ('n_orfs', '输入 ORF 数', '⑥ 的产物'),
            ('n_annotated', '注释成功', '至少一层库命中'),
            ('n_families', '命中科数', '蛋白层 + 结构域层合计'),
            ('model', 'ORF 模型', '与⑥保持一致的基因模型'),
            ('engine', '引擎', '序列同源搜索工具'),
    ):
        if s.get(k) is not None:
            f.append((label, _fmt(s.get(k)), hint))
    fam = s.get('families') or {}
    if fam:
        f.append(('命中科', '；'.join(f'{k} {v}' for k, v in fam.items()),
                  '④b 的注释结论'))
    cats = s.get('categories') or {}
    if cats:
        f.append(('功能类别', '；'.join(f'{k} {v}' for k, v in cats.items()),
                  '按蛋白功能粗分'))
    ev = s.get('evidence') or {}
    if ev:
        f.append(('证据层级', '；'.join(f'{k} {v}' for k, v in ev.items()),
                  'seq=序列同源，pfam=结构域，bfvd=病毒域'))
    return f


def _facts_phylo(s, files, notes):
    f = []
    if not s:
        return f
    groups = s.get('groups') or []
    if isinstance(groups, list) and groups:
        f.append(('分组数', str(len(groups)),
                  '；'.join(str(g.get('group') or g.get('name') or '') for g in groups
                           if isinstance(g, dict)) or '按病毒种拆分'))
        for g in groups[:6]:
            if not isinstance(g, dict):
                continue
            name = g.get('group') or g.get('name') or '组'
            n_seq = g.get('n_seqs') or g.get('n_contigs')
            best = g.get('best_identity') or g.get('best')
            tr = g.get('trim') if isinstance(g.get('trim'), dict) else None
            extra = []
            if n_seq is not None:
                extra.append(f'{_fmt(n_seq)} 条序列')
            if best is not None:
                extra.append(f'最高 identity {_fmt(best)}%')
            if tr is not None:
                extra.append(('已清剪 %s→%s 列' % (_fmt(tr.get('cols_before')),
                                                 _fmt(tr.get('cols_after')))
                              if tr.get('applied') else
                              '未清剪（%s 列）' % _fmt(tr.get('cols_before'))))
            f.append((str(name), '，'.join(extra) or '见 05_phylo', '系统发育分组'))
    if s.get('tree_tool'):
        f.append(('建树工具', str(s.get('tree_tool')), '⑦ 使用的算法'))
    return f


def _facts_primer(s, files, notes):
    f = []
    if not s:
        return f
    if s.get('n_primers') is not None:
        f.append(('设计出引物', _fmt(s.get('n_primers')), '可直接下单合成的对数'))
    if s.get('reason'):
        f.append(('未出引物的原因', str(s.get('reason')), '平台给出的判定原文'))
    if s.get('specificity_checked') is not None:
        f.append(('特异性检查', '已做' if s.get('specificity_checked') else '未做',
                  '未做时不能承诺引物只扩目标'))
    if s.get('mode'):
        f.append(('设计模式', str(s.get('mode')), '保守区/特异区等策略'))
    return f


def _facts_gbdraw(s, files, notes):
    f = []
    if not s:
        return f
    plots = s.get('plots') or []
    if isinstance(plots, list):
        f.append(('出图数', str(len(plots)), '成功出图的基因组图'))
        names = []
        for p in plots[:6]:
            if isinstance(p, dict):
                names.append(str(p.get('file') or p.get('name') or ''))
            else:
                names.append(str(p))
        if names:
            f.append(('图的文件', '、'.join(n for n in names if n), '见 09_genome_plots'))
    if s.get('engine'):
        f.append(('绘图引擎', str(s.get('engine')), 'gbdraw / dna_features_viewer'))
    return f


def _fmt_db(d):
    if not isinstance(d, dict):
        return str(d)
    parts = []
    for k, v in d.items():
        if isinstance(v, dict):
            vs = ', '.join(f'{kk}={vv}' for kk, vv in list(v.items())[:3])
            parts.append(f'{k}({vs})')
        else:
            parts.append(f'{k}={v}')
    return '；'.join(parts)


FACT_EXTRACTORS = {
    'subsample': _facts_subsample, 'fastp': _facts_fastp,
    'fq2fa': _facts_fq2fa, 'host': _facts_host, 'kvsuite': _facts_kvsuite,
    'assembly': _facts_assembly, 'hostana': _facts_hostana,
    'verify': _facts_verify, 'consensus': _facts_consensus,
    'orf': _facts_orf, 'orfa': _facts_orfa, 'phylo': _facts_phylo,
    'primer': _facts_primer, 'gbdraw': _facts_gbdraw,
}


# ------------------------------------------------------------------
# 阶段耗时：从运行日志解析（产物不含耗时，只能从日志原文取）
# ------------------------------------------------------------------

_STAGE_DUR_RE = re.compile(
    r'\[STAGE\]\s*──+\s*(?P<label>[^─]+?)\s*完成（实际\s*(?P<dur>[^）]+)）')
_DUR_PARSE = {'秒': 1.0, '分': 60.0, '小时': 3600.0}


def _parse_dur(txt):
    """'1分55秒' / '12秒' / '0秒' → 秒（浮点）；认不出返回 None。"""
    txt = (txt or '').strip()
    m = re.findall(r'(\d+(?:\.\d+)?)\s*(小时|分|秒)', txt)
    if not m:
        m2 = re.match(r'^(\d+(?:\.\d+)?)$', txt)
        return float(m2.group(1)) if m2 else None
    total = 0.0
    for num, unit in m:
        total += float(num) * _DUR_PARSE[unit]
    return total


def _label_to_stage(label):
    """日志里的阶段标签（'① 宿主去除'）→ 阶段键。

    先按「圈码 + 中文标签」比对（最稳，不依赖日志里的英文括号：日志实际写
    `⓪b 序列转换(FASTQ→FASTA)`，界面叫法却是 `⓪b 序列转换`），再退回
    圈码前缀匹配。
    """
    lab = (label or '').strip()
    for k, _zh, _short, core in STAGE_VIEW:
        if core and lab.endswith(core):
            return k
    for k, _zh, _short, core in STAGE_VIEW:
        if core and lab.startswith(core):
            return k
    for k, zh, _short, _core in STAGE_VIEW:
        code = zh.split(' ', 1)[0]
        if code and lab.startswith(code):
            return k
    return None


def _stage_durations(root, notes):
    """扫 root/logs/*.log 里最新的 AI 运行日志，取每阶段实际耗时。"""
    logdir = os.path.join(root, 'logs')
    out = {}
    if not os.path.isdir(logdir):
        return out, None
    logs = sorted(glob.glob(os.path.join(logdir, '*.log')),
                  key=lambda p: os.path.getmtime(p), reverse=True)
    for lp in logs[:6]:
        got = {}
        try:
            with open(lp, encoding='utf-8', errors='replace') as fh:
                for line in fh:
                    m = _STAGE_DUR_RE.search(line)
                    if not m:
                        continue
                    key = _label_to_stage(m.group('label'))
                    dur = _parse_dur(m.group('dur'))
                    if key and dur is not None:
                        got[key] = dur
        except OSError:
            continue
        if got:
            out.update(got)
            return out, _rel(root, lp)
    return out, None


# ------------------------------------------------------------------
# 工具箱运行：事实抽取
# ------------------------------------------------------------------

MODULE_FACTS = {
    'sdt': ('SDT 序列相似度', '序列一致性矩阵与分布'),
    'align': ('多序列比对', 'MAFFT 比对 + trimAl 清剪'),
    'quicktree': ('快速建树', 'FastTree ML 树'),
    'identity': ('一致性与相似度', '比对后 identity/similarity 表'),
    'genoplot': ('基因组可视化', 'gbdraw / DFV 出图'),
    'contigs': ('contig 分类', 'kunpeng 分类 contig 来源'),
    'kvsuite': ('已知病毒识别与定量', 'salmon 定量 + pandepth 覆盖'),
    'structcmp': ('结构域比较', '蛋白域组合比较'),
    'primer': ('引物设计', '保守区引物'),
    'mirna': ('miRNA 靶向预测', '多算法 miRNA-病毒互作'),
    'rdp': ('重组检测', 'RDP5 事件扫描'),
    'rtt': ('Tip-dating', 'TreeTime 时间树'),
    'phylodyn': ('群体动力学', 'BEAST 贝叶斯系统动力学'),
    'phylogeo': ('系统地理', '迁移事件推断'),
    # 2026-11-04 修正：原条目误写为「密码子适配」，与 CONAN-SWIFT 检测设计
    # 不是同一语义（conan 工具运行目录 report_v2 标题曾因此显示错误主题）。
    'conan': ('CONAN-SWIFT 检测设计（Cas3）', '负组同源位点双重失配 crRNA 设计'),
    'cricon': ('CRISPR+CONAN 联合检测设计',
               '双路线编排：交叉审核 + 合并订购表与验证清单'),
    'lamp': ('LAMP 引物组设计', '六引物组 + 环引物（等温扩增）'),
    'pdprep': ('系统发育数据准备', '序列整理与元数据'),
    'verify': ('序列验证', 'blastx/BFVD/CDD 判定'),
    'orf': ('ORF 预测', 'pyrodigal 基因预测'),
    'orfa': ('ORF 功能注释', '多层库注释'),
    'assemble': ('组装', '组装器直接调用'),
    'fastp': ('质控', 'fastp 质控'),
    'hostremoval': ('去宿主', 'kunpeng 宿主去除'),
    'convert': ('格式转换', '序列格式互转'),
}

_MODULE_JSON_HINT = {
    'sdt': 'sdt_matrix.json', 'align': None, 'quicktree': None,
    'identity': 'identity.json', 'genoplot': 'summary.json',
    'contigs': 'summary.json', 'kvsuite': None, 'structcmp': 'identity_matrix.json',
    'primer': 'summary.json', 'mirna': 'results.json', 'rdp': 'events.json',
    'rtt': 'summary.json', 'phylodyn': 'phylodyn_summary.json',
    'phylogeo': 'summary.json', 'conan': 'summary.json',
    'cricon': 'summary.json',
    'lamp': 'summary.json',
    'pdprep': 'summary.json', 'verify': 'summary.json', 'orf': 'summary.json',
    'orfa': 'summary.json',
    'cmpchain': 'run_summary.json',
}


def _facts_module(tool, base, notes):
    """工具箱运行的事实：从 run.log 的 $ 命令行 + 各工具 json 摘要。"""
    f = []
    jname = _MODULE_JSON_HINT.get(tool)
    data = None
    jpath = os.path.join(base, jname) if jname else None
    if jpath and not os.path.isfile(jpath):
        hits = glob.glob(os.path.join(base, '**', jname), recursive=True) if jname else []
        jpath = hits[0] if hits else None
    if jpath:
        data = _read_json(jpath, notes, _rel(base, jpath))

    if tool == 'sdt' and data:
        f.append(('序列数', _fmt(data.get('n')), '参与 SDT 计算的序列'))
        f.append(('比对对数', _fmt(data.get('pairs')), '逐对独立比对'))
        if data.get('seqtype'):
            f.append(('序列类型', str(data.get('seqtype')), 'nt=核酸，aa=蛋白'))
        if data.get('aligner'):
            f.append(('比对器', str(data.get('aligner')), '影响 identity 数值'))
        if data.get('aligner_note'):
            notes.append(str(data.get('aligner_note')))
        mat = data.get('matrix') or []
        names = data.get('names') or []
        if mat and names:
            vals = [v for row in mat for v in row if isinstance(v, (int, float))]
            off = [v for i, row in enumerate(mat) for j, v in enumerate(row)
                   if i != j and isinstance(v, (int, float))]
            if off:
                f.append(('最高 identity', f'{max(off):.2f}%',
                          '排除对角线的最高值'))
                f.append(('最低 identity', f'{min(off):.2f}%', '最不相似的一对'))
            if vals:
                f.append(('数值个数', str(len(vals)), '矩阵元素总数'))
    elif tool == 'align':
        for ff in ('input.fasta', 'aln.fasta', 'aln.trim.fasta'):
            p = os.path.join(base, ff)
            st = read_fasta_stats(p)
            if st:
                cols = None
                try:
                    with open(p, encoding='utf-8', errors='replace') as fh:
                        seq = ''.join(l.strip() for l in fh if not l.startswith('>'))
                        nseq = max(st['n'], 1)
                        cols = len(seq) // nseq
                except OSError:
                    cols = None
                f.append((ff, f"{st['n']} 条 × {cols if cols else '—'} 列",
                          '输入/比对/清剪后的三份 FASTA'))
    elif tool == 'kvsuite':
        ident = os.path.join(base, 'kvsuite', 'identify')
        tsv = os.path.join(ident, 'all_viruses.summary.tsv')
        rows = read_dicts(tsv)
        if rows:
            f.append(('主检物种数', str(len(rows)), 'all_viruses.summary.tsv'))
            top = rows[0]
            nm = (top.get('Species') or top.get('species') or
                  top.get('reference') or top.get('ref') or '')
            f.append(('首位物种', str(nm), '按丰度排序的第一行'))
    elif tool == 'primer' and data:
        f.append(('引物对数', _fmt(data.get('n_primers')), '可直接合成'))
        if data.get('reason'):
            f.append(('未出引物原因', str(data.get('reason')), '平台判定原文'))
    elif tool == 'genoplot' and data:
        plots = data.get('plots') or []
        f.append(('出图数', str(len(plots)), 'summary.plots'))
    elif data and isinstance(data, dict):
        for k, label in (('n_seqs', '序列数'), ('n', '序列数'),
                         ('n_orfs', 'ORF 数'), ('n_annotated', '注释成功'),
                         ('n_events', '事件数'), ('engine', '引擎'),
                         ('status', '状态')):
            if data.get(k) is not None:
                f.append((label, _fmt(data.get(k)), jname or ''))
    return f, data


def _run_cmd(base, notes):
    """run.log 第一行形如 `[03:29:02] $ <cmd...>`，取出真实命令行。"""
    p = os.path.join(base, 'run.log')
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                s = line.strip()
                if ' $ ' in s or s.startswith('$ '):
                    cmd = s.split(' $ ', 1)[1] if ' $ ' in s else s[2:]
                    return cmd.strip()
    except OSError:
        return None
    return None


def _run_log_warns(base):
    """run.log 里 WARN/ERROR 原文（给「坑」区引用）。"""
    p = os.path.join(base, 'run.log')
    out = []
    if not os.path.isfile(p):
        return out
    try:
        with open(p, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                if '[WARN]' in line or '[WARNING]' in line or '[ERROR]' in line:
                    out.append(line.rstrip())
                if len(out) >= 8:
                    break
    except OSError:
        pass
    return out


# ------------------------------------------------------------------
# findings / gaps：把事实转成「能下判断的句子」
# ------------------------------------------------------------------

def _finding(text, evidence, level='ok'):
    return {'text': text, 'evidence': list(evidence), 'level': level}


def _gap(kind, title, detail, evidence=(), severity='warn'):
    return {'kind': kind, 'title': title, 'detail': detail,
            'evidence': list(evidence), 'severity': severity}


def _stage_of(stages, key):
    for st in stages:
        if st['key'] == key:
            return st
    return None


def _build_findings(model):
    """按阶段组合生成结论。每条结论必须挂 evidence（相对路径）。"""
    stages, notes = model['stages'], []
    F = []
    sd = model['sample']

    host = _stage_of(stages, 'host')
    if host and host['summary']:
        s = host['summary']
        keep = fnum(s.get('kept_pairs'))
        ratio = fnum(s.get('host_ratio'))
        if keep is not None and ratio is not None:
            F.append(_finding(
                f"去宿主后保留 {_fmt(keep)} 对 reads，宿主占比 {ratio * 100:.2f}%"
                f"——这一步决定了后面所有分析的「有效视野」：病毒只可能在这 "
                f"{_fmt(keep)} 对里出现，宿主读数再漂亮也轮不到它。",
                [host['dir'] + '/stats.json']))

    kv = _stage_of(stages, 'kvsuite')
    if kv and kv['summary']:
        s = kv['summary']
        nsp = s.get('passed')
        F.append(_finding(
            f"已知病毒定量检出 {_fmt(nsp)} 个通过阈值的物种，病毒 reads "
            f"{_fmt(s.get('viral_reads'))}——这是「哪些已知病毒在场」的正向证据，"
            f"阈值与逐物种覆盖见 ②b 明细表。",
            [kv['dir'] + '/summary.json']))
        sp = s.get('species_detected') or []
        if isinstance(sp, list):
            deep = [x for x in sp if isinstance(x, dict) and
                    str(x.get('coverage_level') or x.get('level') or '').lower()
                    in ('high', '中', 'medium')]
            if deep:
                F.append(_finding(
                    '其中高/中等级 ' + '、'.join(
                        str(x.get('species') or x.get('name')) for x in deep[:4]) +
                    ' 属于「覆盖+深度」双达标，可作为主判定；其余低等级物种'
                    '只能算「有 reads 命中」，不能直接写成检出。',
                    [kv['dir'] + '/summary.json'], 'warn'))

    asm = _stage_of(stages, 'assembly')
    if asm and asm['summary']:
        s = asm['summary']
        F.append(_finding(
            f"组装得到 {_fmt(s.get('contigs'))} 条 contigs、总长 "
            f"{_fmt(s.get('total_bp'))} bp，其中 "
            f"{_fmt(_safe_len(s.get('viral_contigs')))} 条被判为病毒来源。"
            f"contigs 数量级决定了后续是「拼出完整基因组」还是「只有碎片证据」。",
            [asm['dir'] + '/summary.json']))

    ver = _stage_of(stages, 'verify')
    if ver and ver['summary']:
        calls = ver['summary'].get('calls') or {}
        if calls:
            F.append(_finding(
                '序列验证判定：' + '、'.join(f'{k} {v}' for k, v in calls.items()) +
                '。known 才代表「能对应到已知病毒种」，domain_only 只说明含'
                '某类病毒的结构域，不能据此外推到物种。',
                [ver['dir'] + '/summary.json']))

    con = _stage_of(stages, 'consensus')
    if con and con['summary']:
        s = con['summary']
        nref = fnum(s.get('n_refs'))
        F.append(_finding(
            f"生成 {_fmt(nref)} 条共识序列，映射 reads "
            f"{_fmt(s.get('mapped_reads'))}；变异表里 "
            f"{_fmt(s.get('n_variants'))} 个位点中只有 "
            f"{_fmt((fnum(s.get('n_snv')) or 0) + (fnum(s.get('n_isnv')) or 0))} "
            f"个含真实多态（SNV {_fmt(s.get('n_snv'))} + iSNV "
            f"{_fmt(s.get('n_isnv'))}），其余多为低覆盖写成 N 的位置——"
            f"这两个数不能混着说。",
            [con['dir'] + '/summary.json', con['dir'] + '/coverage.tsv']))

    orf = _stage_of(stages, 'orf')
    orfa = _stage_of(stages, 'orfa')
    if orfa and orfa['summary']:
        s = orfa['summary']
        F.append(_finding(
            f"ORF 注释 {_fmt(s.get('n_annotated'))}/{_fmt(s.get('n_orfs'))} 成功，"
            f"命中 {_fmt(s.get('n_families'))} 个病毒科"
            + (f"（{'、'.join(list((s.get('families') or {}).keys())[:4])}）"
               if s.get('families') else '') +
            '。注释成功只说明蛋白同源关系成立，不代表整段序列就是该病毒的完整基因组。',
            [orfa['dir'] + '/summary.json']))

    ph = _stage_of(stages, 'phylo')
    if ph and ph['summary']:
        groups = ph['summary'].get('groups') or []
        F.append(_finding(
            f"系统发育分 {len(groups)} 组建树"
            + (f"（{'、'.join(str(g.get('group') or '') for g in groups[:5] if isinstance(g, dict))}）"
               if groups else '') +
            '。树回答的是「跟谁最近」，不回答「是不是新种」——后者需要'
            '全基因组序列相似度阈值与更多参考株。',
            [ph['dir'] + '/summary.json']))

    pr = _stage_of(stages, 'primer')
    if pr and pr['summary']:
        s = pr['summary']
        n = fnum(s.get('n_primers'))
        if n == 0:
            F.append(_finding(
                '引物设计未产出可用引物（' + str(s.get('reason') or '保守区不足')
                + '）。因此本报告不能提供 qPCR/RT-PCR 验证方案，'
                  '需要先补齐完整或近完整的目标基因组。',
                [pr['dir'] + '/summary.json'], 'warn'))

    gb = _stage_of(stages, 'gbdraw')
    if gb and gb['summary']:
        F.append(_finding(
            f"基因组图成功出图 {_fmt(_safe_len(gb['summary'].get('plots')))} 张。"
            f"图只能用于展示已注释的 ORF 排布，出图失败通常意味着该序列没有"
            f"可用的注释特征。",
            [gb['dir'] + '/summary.json']))

    if not F:
        F.append(_finding('本次输入未包含可下结论的分析阶段产物，报告仅列目录与口径。',
                          [], 'warn'))
    return F


def _build_gaps(model):
    """证据缺口 / 口径边界。这一节是「反幻觉」的主要载体。"""
    stages = model['stages']
    G = []

    if model['profile']['key'] == 'full' or _stage_of(stages, 'fastp') is None:
        if _stage_of(stages, 'host') and _stage_of(stages, 'fastp') is None:
            G.append(_gap(
                'pipeline', '没有质控（fastp）层',
                '本样品跳过了 ⓪ 质控，漏斗第一层「输入 reads」是由去宿主的'
                '对数×2 估算出来的，不是 fastp 的实测值；比对质量与重复率也'
                '因此没有记录。',
                ['01_host_removal/stats.json']))

    kv = _stage_of(stages, 'kvsuite')
    if kv and kv['summary'] and kv['summary'].get('viral_reads_quality'):
        q = str(kv['summary']['viral_reads_quality'])
        if 'no_qual' in q or '占位' in q:
            G.append(_gap(
                'data', '病毒 reads 没有真实质量值',
                '②b 从 salmon 的 BAM 里抽 reads 时，BAM 的 QUAL 是 `*`，'
                'samtools fastq 会写出占位质量符。这些 reads 送进 ③ 组装、'
                '③c 变异调用时，质量过滤形同虚设，**碱基错误率无从判断**。',
                ['02b_kvsuite/summary.json'], 'high'))

    con = _stage_of(stages, 'consensus')
    if con and con['summary']:
        s = con['summary']
        nref = fnum(s.get('n_refs'))
        if nref:
            low = _low_coverage_refs(model)
            if low:
                G.append(_gap(
                    'coverage', f'{len(low)} 条共识序列平均深度 ≤2×，共识几乎全是 N',
                    '这些序列只能当「contig 序列」用（长度与成分可信），'
                    '不能当共识基因组用于变异分析或提交：' +
                    '；'.join(f"{n} {d}×/called {c}%" for n, d, c in low[:6]) +
                    '。',
                    ['03c_consensus/coverage.tsv'], 'high'))

    ver = _stage_of(stages, 'verify')
    if ver and ver['summary']:
        calls = ver['summary'].get('calls') or {}
        n_un = calls.get('unclassified')
        if n_un:
            G.append(_gap(
                'identity', f'{n_un} 条序列无法归入任何已知类群',
                'unclassified 可能是新病毒，也可能是非病毒/嵌合/低复杂度序列。'
                '在没有全长序列与更多参考株之前，不能写成「新病毒」。',
                ['03b_verify/calls.tsv']))

    orf = _stage_of(stages, 'orf')
    if orf and orf['summary']:
        s = orf['summary']
        a = s.get('pyrodigal') or {}
        b = s.get('pyrodigal_rv') or {}
        if isinstance(a, dict) and isinstance(b, dict) and a.get('genes') == b.get('genes'):
            G.append(_gap(
                'metric', 'ORF 计数口径：两套模型是同一批 ORF',
                f"summary 里 pyrodigal.genes={a.get('genes')} 与 "
                f"pyrodigal_rv.genes={b.get('genes')} 是**同一批 ORF 的两套坐标**"
                f"（同一条序列正/反向模型各跑一次），把它们相加会得到 2 倍虚高。"
                f"本报告按「独立 ORF 数 = {a.get('genes')}」计。",
                ['04_orf/summary.json'], 'info'))

    orfa = _stage_of(stages, 'orfa')
    if orfa and orfa['summary']:
        s = orfa['summary']
        libs = s.get('pfam_libs')
        if libs == []:
            G.append(_gap(
                'annot', 'Pfam 结构域层没有生效',
                'summary 的 pfam_libs 为空，注释证据全部来自序列同源层；'
                '对无同源参照的 ORF，结构域证据缺失会低估注释率。',
                ['04b_orf_annot/summary.json']))

    ph = _stage_of(stages, 'phylo')
    if ph and ph['summary']:
        for g in (ph['summary'].get('groups') or [])[:8]:
            if not isinstance(g, dict):
                continue
            tr = g.get('trim')
            if isinstance(tr, dict) and tr.get('applied') is False and tr.get('cols_before'):
                G.append(_gap(
                    'phylo', f"进化树分组 {g.get('group') or ''} 未做清剪",
                    f"trimAl 未生效，建树用的是 {_fmt(tr.get('cols_before'))} 列"
                    f"未清剪比对；短序列会在长枝上外拉，拓扑不适合直接作为"
                    f"物种归属结论。",
                    ['05_phylo/summary.json']))

    pr = _stage_of(stages, 'primer')
    if pr and pr['summary'] and fnum(pr['summary'].get('n_primers')) == 0:
        G.append(_gap(
            'primer', '没有可用引物，且未做特异性检查',
            '引物数 0 意味着无法给出 RT-PCR/qPCR 验证方案；'
            'specificity 也未运行，即便后续设计出引物也不能承诺只扩目标。',
            ['06_primer/summary.json']))

    gb = _stage_of(stages, 'gbdraw')
    if gb and gb['summary']:
        plots = gb['summary'].get('plots') or []
        n_viral = None
        asm = _stage_of(stages, 'assembly')
        if asm and asm['summary']:
            n_viral = _safe_len(asm['summary'].get('viral_contigs'))
        if n_viral and len(plots) < n_viral:
            G.append(_gap(
                'viz', f'病毒 contigs {n_viral} 条，只剩 {len(plots)} 张图',
                '出图失败通常是该序列没有可用注释特征（GFF 为空）或长度过短，'
                '属于展示层缺失，不影响序列本身的可信度。',
                ['09_genome_plots/summary.json'], 'info'))

    for st in stages:
        for n in st['notes'][:2]:
            G.append(_gap('parse', f"{st['title']}：读取异常", n,
                          [f"{st['dir']}/"], 'info'))

    return G


def _low_coverage_refs(model):
    """共识覆盖表里平均深度 ≤2× 的参考 → [(name, depth, called_pct)]。"""
    out = []
    con = _stage_of(model['stages'], 'consensus')
    if not con or not con['summary']:
        return out
    root = model['layout']['root']
    p = os.path.join(root, con['dir'], 'coverage.tsv')
    for r in (read_dicts(p, default=[]) or []):
        d = fnum(r.get('mean_depth'))
        if d is None or d > 2.0:
            continue
        name = (r.get('ref') or r.get('reference') or r.get('seq') or
                r.get('contig') or '')
        called = r.get('called_pct')
        try:
            called_s = f'{float(called):.1f}'
        except (TypeError, ValueError):
            called_s = '—'
        out.append((str(name), _fmt(d), called_s))
    return out


def _read_tsv_rows(path, limit=None):
    """读 tsv（表头 + 行 dict）；文件不存在返回 []。

    2026-11-02：read_dicts 缺文件返回的是 default（None），不是 []。以前这里
    直接透传，文件缺失时调用方 `for r in rows` 会抛
    `TypeError: 'NoneType' object is not iterable`——只在「某个阶段有
    summary.json 但没产出对应 tsv」的目录上才炸，真样品不会触发，所以藏得住。
    """
    rows = read_dicts(path, default=[]) or []
    return rows[:limit] if limit else rows


def _short(node):
    """NODE_1_length_2418_cov_4.587207 → NODE_1（表格里省列宽）。"""
    s = str(node or '')
    return s.split('_length_')[0] if '_length_' in s else s


def _num(v, d=2):
    x = fnum(v)
    return '—' if x is None else f'{x:.{d}f}'


def _build_contradictions(model):
    """跨阶段交叉核对：同一件事两处数字/判定对不上，就列出来。

    这类"说得通但数字对不上"的问题最容易在写论文时被审稿人抓出来，
    所以不指望自动化能解释原因，只负责**如实并列**并给出该信哪个。
    全部规则都是"两处都有产物才比较"，缺一边就跳过，不编。
    """
    st = {s['key']: s for s in model['stages']}
    root = model['layout']['root']
    C = []

    # ① 组装 blastn 首选命中 vs ②b 定量结果：定量看得见某病毒，组装却没拼出来
    kv = (st.get('kvsuite') or {}).get('summary') or {}
    asm = (st.get('assembly') or {}).get('summary') or {}
    if kv.get('species_detected') and asm.get('contigs'):
        hits = _read_tsv_rows(os.path.join(
            root, st['assembly']['dir'], 'contig_blast.tsv'))
        best = [str(r.get('blast_top_hit') or r.get('accession') or
                    r.get('subject') or '').strip() for r in hits]
        best = [b for b in best if b]
        ref_rows = _read_tsv_rows(os.path.join(
            root, st['kvsuite']['dir'], 'identify',
            'all_viruses.summary.tsv'))
        if ref_rows and best:
            seen = set(best)
            strong = []
            for r in ref_rows:
                acc = str(r.get('Accession') or '').strip()
                if not acc or acc in seen:
                    continue
                cov = fnum(r.get('Coverage(%)'))
                dep = fnum(r.get('MeanDepth'))
                if cov is not None and cov >= 80 and dep and dep >= 50:
                    strong.append((acc, cov, dep,
                                   fnum(r.get('Uniq_Reads')),
                                   str(r.get('Species') or '')))
            for acc, cov, dep, reads, sp in strong:
                C.append({
                    'title': f'{sp or acc}（{acc}）定量覆盖 {cov:.1f}%、'
                             f'深度 {dep:.2f}×，但没有任何 contig 以它为首选命中',
                    'a': ('②b 定量（salmon + pandepth）',
                          f'覆盖 {cov:.2f}%、深度 {dep:.2f}×'
                          + (f'、{reads:.0f} 条唯一 read' if reads else '')),
                    'b': ('③ 组装 blastn 首选命中',
                          f'{len(best)} 条 contig 的首选命中里没有它'),
                    'why': '两条通路筛的是不同的东西：定量按参考序列归一化抽 reads，'
                           '任何落在参考上的片段都算；组装还要过 contig 长度/覆盖度过滤'
                           '并把 reads 拼成一条一致序列。所以"定量看得见、组装拼不出"'
                           '在低深度数据里是常态，不代表哪一边算错了。',
                    'use': '讨论"有这条病毒"时引用定量；讨论"能不能拿到这条病毒的序列"'
                           '时引用组装——两句话不能互相替代。',
                    'evidence': ['02b_kvsuite/identify/all_viruses.summary.tsv',
                                 '03_assembly/contig_blast.tsv'],
                })

    # ② 组装核酸命中 vs ORF 注释：同一条 contig 两套命名（病毒名 vs 参考株）
    #    仅在「注释命中的蛋白确实来自组装命中的那几个参考基因组」时才算矛盾；
    #    直接拿 NC_ 核酸号去比 NP_ 蛋白号必然不相等，那是命名空间不同，不是矛盾。
    orfa = (st.get('orfa') or {}).get('summary') or {}
    if orfa.get('families') and asm.get('contigs'):
        hits = _read_tsv_rows(os.path.join(
            root, st['assembly']['dir'], 'contig_blast.tsv'))
        asm_pairs = [(str(r.get('contig') or ''), str(r.get('blast_top_hit') or ''),
                      str(r.get('blast_species') or '')) for r in hits]
        ann = _read_tsv_rows(os.path.join(
            root, st['orfa']['dir'], 'orf_annotation.tsv'))
        if asm_pairs and ann:
            asm_species = {sp for _c, _acc, sp in asm_pairs if sp}
            ann_species = {str(r.get('organism') or '') for r in ann}
            ann_species.discard('')
            # 组装物种串里带参考株全名，注释只给病毒名 → 用"包含"判同一物种
            mixed = []
            for a in sorted(ann_species):
                for full in sorted(asm_species):
                    if a and a in full and full != a:
                        mixed.append((a, full))
                        break
            if mixed:
                a1, f1 = mixed[0]
                C.append({
                    'title': f'同一条序列两种叫法：组装写「{f1}」，注释写「{a1}」',
                    'a': ('③ 组装 blastn（核酸全长比对）', f1),
                    'b': ('⑥b ORF 注释（Diamond 比对蛋白库）', a1),
                    'why': 'blastn 用核酸全长找最像的**基因组记录**，所以带上参考株全名；'
                           'ORF 注释用蛋白找最像的**蛋白记录**，只能给到病毒名。'
                           '两者指向同一个病毒，只是精度不同。',
                    'use': '写"检出哪种病毒"时用注释里的病毒名；写"与哪条参考最接近、'
                           '相似度多少"时用组装的登录号与 identity。',
                    'evidence': ['03_assembly/contig_blast.tsv',
                                 '04b_orf_annot/orf_annotation.tsv'],
                })

    # ③ ORF 计数：summary 里两套模型 vs 漏斗 ORF 层
    orf = (st.get('orf') or {}).get('summary') or {}
    pg = orf.get('pyrodigal') or {}
    rv = orf.get('pyrodigal_rv') or {}
    g1, g2 = fnum(pg.get('genes')), fnum(rv.get('genes'))
    if g1 and g2:
        fun = {r['key']: r['value'] for r in model['metrics'].get('funnel') or []}
        n_orf = fnum(fun.get('orf_genes'))
        if n_orf is not None and g1 == g2 and n_orf == g1:
            C.append({
                'title': f'ORF 数量两种写法：{g1:.0f} 还是 {g1 + g2:.0f}？',
                'a': ('⑥ summary（pyrodigal 与 pyrodigal_rv 两套结果）',
                      f'各 {g1:.0f} 个，把两套相加就会写成 {g1 + g2:.0f}'),
                'b': ('漏斗 ORF 层 / ⑥b 注释表',
                      f'{n_orf:.0f} 个（同一批 ORF 只算一次）'),
                'why': '两套模型是对同一批 contigs 换方向/换参数的两次预测，'
                       '产出的 ORF 集合高度重叠，相加会虚高——这正是 2026-11 修掉的'
                       '一个口径错误（此前漏斗报过 2 倍）。',
                'use': '引用"几个 ORF"时用漏斗/⑥b 的 n_orfs；不要引用两套模型之和。',
                'evidence': ['04_orf/summary.json', '07_report/stage_funnel.tsv'],
            })

    # ④ 宿主置信度 vs 低置信计数
    ha = (st.get('hostana') or {}).get('summary') or {}
    prev = ha.get('rows_preview') or []
    lows = [r for r in prev
            if 'low' in str(r.get('confidence') or '').lower()]
    if prev and lows and not ha.get('n_low_confidence'):
        C.append({
            'title': f'宿主预测：表里 {len(lows)} 条标着 Low，汇总却说低置信 0 条',
            'a': ('④ 明细表 confidence 列',
                  '、'.join(sorted({str(r.get('confidence')) for r in lows}))),
            'b': ('④ summary.n_low_confidence', '0'),
            'why': '明细里的 Low 来自分类器对**这一个 contig** 的置信度，'
                   '汇总的 n_low_confidence 只统计**触发低置信告警**（需要人工复核）的条数，'
                   '两者判定标准不同，不是同一件事。',
            'use': '向别人展示时说明"标 Low 是单条序列的置信度"，不要被"低置信 0 条"误导'
                   '成"全部高置信"。',
            'evidence': ['08_host_analysis/summary.json'],
        })
    return C


def _build_tool_table(model):
    """工具版本表：配置里探测到的可执行文件 + 版本。"""
    rows = []
    for t in model.get('tools') or []:
        rows.append({
            'name': t.get('name'),
            'version': t.get('version') or '未探测',
            'path': t.get('path') or '',
        })
    return rows


# ------------------------------------------------------------------
# 主入口
# ------------------------------------------------------------------

def collect(target, logger=None):
    """目录 → 报告模型（纯读，不写盘）。"""
    root = os.path.abspath(str(target))
    if not os.path.isdir(root):
        raise FileNotFoundError(f'目录不存在: {root}')

    prof = detect_profile(root)
    # 判定要按 profile 判，不只看 prof['tool']——认不出样品的目录会走 tool 档案
    # 但 tool 字段为 None。kind 与 out_dir 必须用**同一个**判定：此前 kind 只
    # 看 prof['tool']（认不出 → 'sample'），out_dir 却按 prof['key']=='tool'
    # 写在根下，于是证据链接按 sample 加 '../' → 越界到平台根之外。
    is_tool = bool(prof.get('tool')) or prof.get('key') == 'tool'
    model = {
        'generated': time.strftime('%Y-%m-%d %H:%M'),
        'layout': {'kind': 'tool_run' if is_tool else 'sample',
                   'root': root, 'target': root,
                   # 样品报告落在 07_report/（与 report.html 同处，页面统一从
                   # 07_report 取报告）；工具箱运行目录本身就是输出目录。
                   # 2026-11-02：认不出样品的目录会走 tool 档案但 tool 字段为
                   # None，此时若仍写 07_report/，等于自己造出一个「阶段目录」，
                   # 下一次扫描就会把它当成 report 阶段算进阶段数（同一目录两次
                   # 生成报告，阶段数从 0 变 1）。工具运行目录一律写在根下。
                   'out_dir': root if is_tool
                   else os.path.join(root, '07_report'),
                   'inputs': [], 'names_zh': STAGE_ZH},
        'sample': {},
        'profile': {k: prof.get(k) for k in
                    ('key', 'title', 'subtitle', 'questions', 'blocks', 'tool')},
        'stages': [], 'metrics': {}, 'modules': {}, 'findings': [],
        'gaps': [], 'contradictions': [], 'evidence': [], 'verdict': {},
        'tool_table': [],
        'tools': [], 'run_log': None,
    }
    model['profile']['stages'] = prof.get('stages') or []

    # ---------- 样品目录 ----------
    if prof['tool'] is None:
        pj = _read_json(os.path.join(root, 'project.json'))
        if pj:
            model['sample'] = {
                'name': pj.get('project') or os.path.basename(root),
                'created': pj.get('created'),
                'last_run': pj.get('last_run'),
                'last_status': pj.get('last_status'),
                'last_duration_s': pj.get('last_duration_s'),
                'input': pj.get('input') or {},
                'params': pj.get('params') or {},
                'stages_done': pj.get('stages_done') or [],
            }
            for k in ('r1', 'r2'):
                p = (model['sample']['input'] or {}).get(k)
                if p:
                    model['layout']['inputs'].append(str(p))
                    try:
                        model['layout']['inputs'].append(
                            os.path.join(os.path.dirname(p), ''))
                    except Exception:
                        pass
        else:
            model['sample'] = {'name': os.path.basename(root)}

        durs, logp = _stage_durations(root, [])
        model['run_log'] = logp
        if logp:
            model['layout']['inputs'].append(os.path.join(root, logp))

        dirs = prof['dirs']
        for key in STAGE_ORDER:
            if key not in dirs:
                continue
            sdir = dirs[key]
            rel = _rel(root, sdir)
            notes = []
            files = _stage_files(root, sdir, notes)
            if not files and key not in ('report',):
                continue
            sname = STAGE_SUMMARY.get(key)
            summary = (_read_json(os.path.join(sdir, sname), notes,
                                  f'{rel}/{sname}') if sname else None)
            fig, fig_twins = pair_figures(
                [ff['path'] for ff in files if ff['kind'] == 'figure'],
                MAX_FIGURES_PER_STAGE)
            facts = FACT_EXTRACTORS.get(key, lambda *a: [])(summary, files, notes)
            model['stages'].append({
                'key': key, 'title': STAGE_FULL.get(key, STAGE_ZH[key]),
                'short': STAGE_SHORT[key], 'dir': rel,
                'status': 'done' if summary is not None or files else 'partial',
                'files': [{'rel': f['rel'], 'name': f['name'],
                           'kind': f['kind'], 'size': f['size']} for f in files],
                'summary': summary, 'facts': facts, 'notes': notes,
                'figures': fig, 'figure_twins': fig_twins,
                'duration_s': durs.get(key),
            })
            for f in files:
                if f['kind'] in ('figure', 'table', 'data'):
                    model['layout']['inputs'].append(f['path'])

        # 跨阶段汇总
        model['metrics']['funnel'] = _funnel_rows(model)
        model['metrics']['kpi'] = _kpi_rows(model)
        model['findings'] = _build_findings(model)
        model['gaps'] = _build_gaps(model)
        model['modules'] = {}
        model['contradictions'] = _build_contradictions(model)
        model['tool_table'] = _build_tool_table(model)

    # ---------- 工具箱运行目录 ----------
    else:
        base = root
        tool = prof['tool']
        notes = []
        files = _stage_files(root, root, notes)
        cmd = _run_cmd(base, notes)
        facts, data = _facts_module(tool, base, notes)
        title, desc = MODULE_FACTS.get(tool, (tool, ''))
        model['sample'] = {'name': os.path.basename(root), 'tool': tool}
        model['profile'].update({'title': f'{title} · 单步报告',
                                 'subtitle': desc or '工具箱单次运行'})
        fig_main, fig_twins = pair_figures(
            [f['path'] for f in files if f['kind'] == 'figure'],
            MAX_FIGURES_PER_STAGE)
        model['stages'].append({
            'key': tool, 'title': title, 'short': title, 'dir': '.',
            'status': 'done', 'files': [{'rel': f['rel'], 'name': f['name'],
                                         'kind': f['kind'], 'size': f['size']}
                                        for f in files],
            'summary': data, 'facts': facts, 'notes': notes,
            'figures': fig_main, 'figure_twins': fig_twins,
            'duration_s': None, 'cmd': cmd,
            'warns': _run_log_warns(base),
        })
        model['modules'][tool] = {'facts': facts, 'data': data, 'cmd': cmd}
        model['layout']['inputs'] += [f['path'] for f in files
                                      if f['kind'] in ('figure', 'table', 'data')]
        model['findings'] = [_finding(
            f'本次运行的是「{title}」（{desc}）。'
            + ('命令行与参数见 §C 与 §G，结论只覆盖这一步，不能外推为全流程诊断。'),
            [f['rel'] for f in files if f['kind'] == 'data'][:3])]
        model['gaps'] = [_gap('scope', '单步结果的作用边界',
                              '工具箱单步只回答本工具的问题（如相似度/比对/出图），'
                              '不产生病毒鉴定与定量结论；需要完整诊断请走业务流程。',
                              [], 'info')]
        if notes:
            model['gaps'] += [_gap('parse', '读取异常', n, [], 'info')
                              for n in notes[:4]]

    model['evidence'] = _collect_evidence(model)
    model['verdict'] = _verdict(model)
    model['tools'] = _tool_versions(model)
    if logger:
        logger.log(f"报告模型：{model['profile']['title']}，"
                   f"{len(model['stages'])} 个阶段，"
                   f"{len(model['findings'])} 条结论，{len(model['gaps'])} 条缺口")
    return model


def _funnel_rows(model):
    """流程漏斗：与 stage_funnel.FUNNEL_CHAIN 同口径，但只取已产出的层。"""
    from ..stage_funnel import FUNNEL_CHAIN, _orf_count
    root = model['layout']['root']
    rows = []
    for key, zh, en, fn in FUNNEL_CHAIN:
        try:
            value, note, src = fn(root)
        except Exception:
            value, note, src = None, '', ''
        if value is None:
            continue
        rows.append({'key': key, 'zh': zh, 'en': en, 'value': value,
                     'note': note, 'src': src})
    return rows


def _kpi_rows(model):
    from ..stage_funnel import kpi_stats
    try:
        return [{'label': a, 'value': b, 'note': c}
                for a, b, c in kpi_stats(model['layout']['root'])]
    except Exception:
        return []


def _collect_evidence(model):
    """报告中引用到的全部证据文件（去重、排序、只留存在的）。"""
    root = model['layout']['root']
    seen, out = set(), []

    def add(rel):
        if not rel:
            return
        rel = str(rel).replace('\\', '/')
        if rel in seen:
            return
        p = os.path.join(root, rel)
        if not os.path.exists(p):
            return
        seen.add(rel)
        out.append(rel)

    for st in model['stages']:
        for f in st['files']:
            if f['kind'] in ('figure', 'table', 'data'):
                add(f['rel'])
    for f in model['findings'] + model['gaps']:
        for e in f.get('evidence') or []:
            add(e)
    return out


def _verdict(model):
    sev = [g['severity'] for g in model['gaps']]
    if 'high' in sev:
        return {'level': 'warn', 'text': '可下结论，但有高强度缺口需先补'}
    if 'warn' in sev:
        return {'level': 'note', 'text': '结论成立，口径边界已在 §E 列明'}
    return {'level': 'ok', 'text': '各阶段产物齐全，未发现阻断性缺口'}


#: 版本表：工具名 → (config 键, 版本探测参数, 版本行正则)
#: 正则都按各工具真实输出校准过：mmseqs 打印 "Version: 18-8f2f5"（带日期码）；
#: mafft 的 `--version` 返回的是 man 文档序号（65001），真实版本要靠
#: `mafft --help` 里的 "MAFFT v7.520"；kunpeng 的 0.x 版本号与二进制名
#: （kun_peng-v0.7.12）经常不一致，只信它自己打印的行。
_TOOL_SPECS = [
    ('salmon', 'salmon', ['--version'], r'salmon\s+v?([\d.]+)'),
    ('SPAdes', 'spades', ['--version'], r'v?([\d.]+(?:-dev)?)'),
    ('DIAMOND', 'diamond', ['version'], r'([\d.]+(?:\.\d+)+)'),
    (('MMseqs2', 'mmseqs', ['--version'],
      r'MMseqs2\s+Version:\s*([0-9a-f]{7,})'),
     ('MMseqs2', 'mmseqs', ['version'], r'(?:Version:\s*)?v?(\d+[-.][\w.\-]*)')),
    ('BLAST+', 'blastn', ['-version'], r'blastn:\s*([\d.+]+)'),
    (('MAFFT', 'mafft', ['--help'], r'(?:MAFFT\s*)?v(\d+\.\d+[\w.\-]*)'),
     ('MAFFT', 'mafft', ['--version'], r'v?(\d+\.\d+[\w.\-]*)')),
    ('trimAl', 'trimal', ['--version'], r'v?(\d+\.\d+[\w.\-]*)'),
    ('FastTree', 'fasttree', ['-help'], r'FastTree\s+([\w.\-]+)'),
    ('kunpeng', 'kunpeng', ['--version'], r'(\d+\.\d+[\w.\-]*)'),
    ('gbdraw', 'gbdraw', ['--version'], r'(\d+\.\d+[\w.\-]*)'),
    ('samtools', 'samtools', ['--version'], r'samtools\s+(\d+\.\d+[\w.\-]*)'),
    ('seqkit', 'seqkit', ['version'], r'v?(\d+\.\d+[\w.\-]*)'),
    ('minimap2', 'minimap2', ['--version'], r'(\d+\.\d+[\w.\-]*)'),
]


def _tool_versions(model, timeout=6.0):
    """探测外部工具版本；探测失败就写「未探测」，不猜。"""
    import shutil
    import subprocess
    out = []
    used = {st['key'] for st in model['stages']}
    want = set()
    if 'kvsuite' in used or 'kvsuite' in model['modules']:
        want |= {'salmon', 'samtools', 'seqkit'}
    if 'assembly' in used:
        want |= {'SPAdes', 'kunpeng', 'BLAST+'}
    if 'verify' in used:
        want |= {'DIAMOND', 'MMseqs2'}
    if 'orfa' in used:
        want |= {'DIAMOND'}
    if 'phylo' in used:
        want |= {'MAFFT', 'trimAl', 'FastTree'}
    if 'gbdraw' in used:
        want |= {'gbdraw'}
    if 'consensus' in used:
        want |= {'minimap2', 'samtools'}
    if not want:
        want = {'salmon', 'SPAdes', 'DIAMOND'}
    try:
        from ..config import get_config
        cfg = get_config()
    except Exception:
        cfg = None
    for spec in _TOOL_SPECS:
        # 一个工具可有多种探测方式（如 MAFFT：先 --help 找 "MAFFT v7.x"，
        # 再退回 --version）；按顺序试，第一个解析出合法版本号的就用。
        variants = spec if isinstance(spec[0], tuple) else (spec,)
        disp = variants[0][0]
        if disp not in want:
            continue
        path = ''
        try:
            path = cfg.tool(variants[0][1]) if cfg else ''
        except Exception:
            path = shutil.which(variants[0][1]) or ''
        ver = ''
        for _, _key, args, rx in variants:
            if not path or ver:
                continue
            try:
                cp = subprocess.run([path] + args, capture_output=True,
                                    text=True, encoding='utf-8',
                                    errors='replace', timeout=timeout,
                                    creationflags=getattr(
                                        subprocess, 'CREATE_NO_WINDOW', 0))
                blob = (cp.stdout or '') + (cp.stderr or '')
                m = re.search(rx, blob)
                if m:
                    ver = m.group(1)
            except Exception:
                ver = ''
        out.append({'name': disp, 'version': ver or '未探测',
                    'path': path or '未找到'})
    return out
