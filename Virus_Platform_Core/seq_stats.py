# -*- coding: utf-8 -*-
"""seqkit stats 口径的序列基本统计（FASTA / FASTQ）。

用途：组装结果（contigs.filtered.fasta）落盘后输出一份与
`seqkit stats -a -T` 完全同列的基本统计，供任务卡关键数字、模块结果页与
下游表格直接使用。

优先级：
1. 纯 Python 流式实现（默认；四分位数采用 statistics.quantiles 的 inclusive
   口径；N50_num 按标准 L50 计，不用 seqkit 当前版本里“不同长度数”的口径）；
2. Python 解析失败时退回平台捆绑的 `3rd/bin/seqkit.exe`（stats -a -T -b）。

对外：
    compute(path) -> {'columns', 'row', 'source'}
    write_stats(path, out_dir, stem='contigs', logger=None)
        -> {'metrics': {列名: 值}, 'chips': {任务卡键: 值},
            'tsv': 路径, 'json': 路径, 'source': 'seqkit'|'python'}
"""
from __future__ import annotations

import gzip
import json
import os
import statistics
import subprocess

COLUMNS = [
    'file', 'format', 'type', 'num_seqs', 'sum_len', 'min_len', 'avg_len',
    'max_len', 'Q1', 'Q2', 'Q3', 'sum_gap', 'N50', 'N50_num',
    'Q20(%)', 'Q30(%)', 'AvgQual', 'GC(%)', 'sum_n',
]

#: 任务卡 chips 的键名（result 顶层）→ seqkit 列
_CHIP_MAP = (
    ('num_seqs', 'n_contigs'),
    ('sum_len', 'total_bp'),
    ('avg_len', 'avg_len'),
    ('max_len', 'max_len'),
    ('N50', 'N50'),
    ('N50_num', 'N50_num'),
    ('GC(%)', 'GC(%)'),
    ('sum_n', 'sum_n'),
)

_GAP_LETTERS = frozenset('-.')


def _opener(path):
    return gzip.open if str(path).lower().endswith('.gz') else open


def _seqkit_exe():
    """平台捆绑/ PATH 里的 seqkit；没有返回 None。"""
    try:
        from Virus_Platform_Core.config import get_config
        exe = get_config().tool('seqkit')
    except Exception:
        return None
    return exe if exe and os.path.isfile(exe) else None


def _seqkit_stats(path):
    exe = _seqkit_exe()
    if not exe:
        return None
    try:
        cp = subprocess.run(
            [exe, 'stats', '-a', '-T', '-b', str(path)],
            capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=900)
    except (OSError, subprocess.SubprocessError):
        return None
    if cp.returncode != 0:
        return None
    lines = [ln for ln in (cp.stdout or '').splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    cols = lines[0].split('\t')
    row = lines[1].split('\t')
    if len(cols) < len(COLUMNS):
        return None
    # 列顺序以 seqkit 实际输出为准；只取标准 19 列，避免版本差异多出的列
    return {'columns': cols[:len(COLUMNS)], 'row': row[:len(COLUMNS)],
            'source': 'seqkit'}


def _guess_type(sample):
    """DNA / RNA / Protein 的简化判定（seqkit alphabet guess 的近似）。"""
    letters = {c for c in str(sample).upper()
               if c.isalpha()}
    if not letters:
        return 'DNA'
    if letters <= set('ACGTUNRYKMSWBDHV'):
        return 'RNA' if ('U' in letters and 'T' not in letters) else 'DNA'
    aa = set('ACDEFGHIKLMNPQRSTVWY')
    if len(letters & aa) / float(len(letters)) >= 0.8:
        return 'Protein'
    return 'Unlimit'


def _quartiles(lengths):
    s = sorted(lengths)
    n = len(s)
    if not n:
        return 0, 0, 0
    if n == 1:
        return s[0], s[0], s[0]
    try:
        q = statistics.quantiles(s, n=4, method='inclusive')
        return tuple(int(round(v)) for v in q)
    except Exception:
        return s[0], s[n // 2], s[-1]


def _n50(lengths, total):
    if not lengths or total <= 0:
        return 0, 0
    target = total / 2.0
    acc = 0
    for i, ln in enumerate(sorted(lengths, reverse=True), 1):
        acc += ln
        if acc >= target:
            return int(ln), i
    return int(max(lengths)), len(lengths)


def _base_counts(seq):
    """单条序列 → (长度, gaps, N 数, GC 数)。大小写不敏感。"""
    s = seq.upper()
    return (len(s), sum(1 for c in s if c in _GAP_LETTERS),
            sum(1 for c in s if c in ('N', 'X')),
            sum(1 for c in s if c in ('G', 'C')))


def _fasta_stats(path):
    lengths = []
    gap = n_amb = gc = total = 0
    sample = []
    sample_len = 0
    cur_len = 0
    seen = False
    with _opener(path)(path, 'rt', encoding='utf-8', errors='replace') as fh:
        for line in fh:
            if line.startswith('>'):
                if seen:
                    lengths.append(cur_len)
                cur_len = 0
                seen = True
                continue
            s = line.strip()
            if not s:
                continue
            ln, g, n, c = _base_counts(s)
            cur_len += ln
            total += ln
            gap += g
            n_amb += n
            gc += c
            if sample_len < 10000:
                sample.append(s)
                sample_len += len(s)
    if seen:
        lengths.append(cur_len)
    return lengths, total, gap, n_amb, gc, ''.join(sample)[:10000], 'FASTA', (0, 0, 0.0)


def _fastq_stats(path):
    lengths = []
    gap = n_amb = gc = total = 0
    q20 = q30 = 0
    qsum = 0.0
    sample = []
    sample_len = 0
    seen = False
    cur_len = 0
    with _opener(path)(path, 'rt', encoding='utf-8', errors='replace') as fh:
        idx = 0
        seq = qual = ''
        for line in fh:
            idx = idx % 4
            if idx == 1:
                seq = line.rstrip('\r\n')
            elif idx == 3:
                qual = line.rstrip('\r\n')
                ln, g, n, c = _base_counts(seq)
                lengths.append(ln)
                total += ln
                gap += g
                n_amb += n
                gc += c
                qs = [ord(ch) - 33 for ch in qual]
                if len(qs) < ln:                    # 质量行缺失/过短时按 0 计
                    qs += [0] * (ln - len(qs))
                q20 += sum(1 for q in qs[:ln] if q >= 20)
                q30 += sum(1 for q in qs[:ln] if q >= 30)
                qsum += sum(qs[:ln])
                if sample_len < 10000:
                    sample.append(seq)
                    sample_len += len(seq)
                seen = True
            idx += 1
    qq = 'FASTQ'
    if total:
        return (lengths, total, gap, n_amb, gc, ''.join(sample)[:10000], qq,
                (q20 * 100.0 / total, q30 * 100.0 / total, qsum / total))
    return lengths, total, gap, n_amb, gc, ''.join(sample)[:10000], qq, (0, 0, 0.0)


def _python_stats(path):
    """纯 Python 兜底。列与 seqkit stats -a 一致，四分位数为近似口径。"""
    fmt = 'FASTA'
    with _opener(path)(path, 'rt', encoding='utf-8', errors='replace') as fh:
        for line in fh:
            if line.startswith('@'):
                fmt = 'FASTQ'
                break
            if line.startswith('>'):
                fmt = 'FASTA'
                break
    if fmt == 'FASTQ':
        lengths, total, gap, n_amb, gc, sample, fmt, quals = _fastq_stats(path)
    else:
        lengths, total, gap, n_amb, gc, sample, fmt, quals = _fasta_stats(path)
    num = len(lengths)
    mn = min(lengths) if lengths else 0
    mx = max(lengths) if lengths else 0
    avg = (total / float(num)) if num else 0.0
    q1, q2, q3 = _quartiles(lengths)
    n50, n50n = _n50(lengths, total)
    gc_pct = (gc * 100.0 / total) if total else 0.0
    q20, q30, avgq = quals
    row = [
        os.path.basename(str(path)), fmt, _guess_type(sample), str(num),
        str(total), str(mn), f'{avg:.1f}', str(mx), str(q1), str(q2), str(q3),
        str(gap), str(n50), str(n50n), str(int(round(q20))),
        str(int(round(q30))), f'{avgq:.2f}', f'{gc_pct:.2f}', str(n_amb),
    ]
    return {'columns': list(COLUMNS), 'row': row, 'source': 'python'}


def compute(path):
    """统计一个 FASTA/FASTQ（支持 .gz）。

    默认走 Python（N50_num 是标准 L50）；Python 解析失败时才退回 seqkit，
    避免 seqkit 某些版本 N50_num 按“不同长度数”统计的偏差。
    """
    try:
        return _python_stats(path)
    except Exception:
        info = _seqkit_stats(path)
        if info:
            return info
        raise


def _num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return float(v)
        except (TypeError, ValueError):
            return v


def chip_stats(metrics):
    """把统计列映射成任务卡关键数字（result 顶层键，数值转回 int/float）。"""
    out = {}
    for src, dst in _CHIP_MAP:
        v = metrics.get(src)
        if v is None or v == '':
            continue
        out[dst] = _num(v)
    return out


def write_stats(path, out_dir, stem='contigs', logger=None):
    """写 <stem>_stats.tsv / <stem>_stats.json，返回指标与 chips。

    任何一步失败都抛异常，由调用方决定是否只记日志跳过。
    """
    info = compute(path)
    cols, row = info['columns'], info['row']
    os.makedirs(out_dir, exist_ok=True)
    tsv = os.path.join(out_dir, f'{stem}_stats.tsv')
    with open(tsv, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\t'.join(cols) + '\n')
        fh.write('\t'.join(str(x) for x in row) + '\n')
    metrics = {c: v for c, v in zip(cols, row)}
    js = os.path.join(out_dir, f'{stem}_stats.json')
    with open(js, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump({'input': str(path), 'source': info['source'],
                   'stats': metrics}, fh, ensure_ascii=False, indent=2)
    if logger:
        try:
            logger.log(
                f'序列统计（{info["source"]}）：{metrics.get("num_seqs")} 条 · '
                f'{metrics.get("sum_len")} bp · N50 {metrics.get("N50")} · '
                f'GC {metrics.get("GC(%)")}%')
        except Exception:
            pass
    return {'metrics': metrics, 'chips': chip_stats(metrics),
            'tsv': tsv, 'json': js, 'source': info['source']}
