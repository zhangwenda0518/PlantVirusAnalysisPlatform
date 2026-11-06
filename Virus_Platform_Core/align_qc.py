# -*- coding: utf-8 -*-
"""重组分析前的序列预筛：去不完整片段 / 低质量序列。

对齐参照管道 ``virome_phylo_pipeline/utils/align_qc.py``——**同一套判据、
同一组文件名**（``align_qc_report.tsv`` / ``clean.fasta`` / ``removed.fasta``），
方便与那边逐条对账。唯一差别：参照管道自带 MAFFT，而平台上 RDP5 与建树
都要求**已比对**输入，所以本模块只吃比对后的 FASTA，不做比对。

判据（默认值与参照管道一致）::

    len_ratio = 去 gap 长度 / 参考长度   <  min_length(0.90)  → 不完整片段
    gap_ratio = gap 数 / 比对列数        >  max_gap(0.10)     → 比对质量差
    n_ratio   = N 数 / 比对列数          >  max_n(0.05)       → 低质量
    identity  = 与参考的 pairwise identity（SDT 公式，双方非 gap 才计入）
                低于 (1 - max_identity_drop) * 100（默认 98%） → 群体外离群

参考序列：显式给 ID（子串匹配）则用它；否则取**去 gap 长度中位数**那条，
与参照管道同。⚠ identity 一栏**只在显式参考命中时才参与判定**——参照管道
同样是 `if args.reference:` 才取 ref_seq；98% 这条线是给「同一变异株 / 同一次
爆发」的近等序列用的，拿它去卡一个病毒种的全部株系会误杀（实测 CMV RNA3
十条跨亚组序列会被剔掉 7/10）。

为什么必须做这步：RDP5 是对**同源等长**序列做重组检测，混进片段会让
全比对出现大片 gap，断点坐标与 p 值都会被带偏；而且片段本身就是「缺失
数据」，任何重组信号都不可信。所以进 RDP5 前先按长度比剔掉不完整序列。
"""
from __future__ import annotations

import csv
import os
from collections import Counter

from .utils import iter_fasta

# RDP5 能接受的最少序列数（官方下限 4）；预筛后不足这个数就没必要往下跑
MIN_SEQS = 4

DEFAULT_MIN_LENGTH = 0.90
DEFAULT_MAX_GAP = 0.10
DEFAULT_MAX_N = 0.05
DEFAULT_MAX_IDENTITY_DROP = 0.02


def pairwise_identity(s1, s2):
    """与参照管道同口径（**仅 '-' 为 gap**），结果 = 百分比 0-100。

    公式本体委托 `seq_identity.pair_identity`（2026-11-04 全平台统一）。
    本模块 `qc_alignment` 对输入强制等长（不等长直接 raise），而等长时
    `len(s1) - gaps`（gaps 按 '-' 计）≡ 双方均非 gap 的列数，故与
    `pair_identity(gap_chars='-')` 逐位等价；这里显式传 `gap_chars='-'`
    只为保住参照管道口径（该管道里 '.' 算碱基、不算 gap，与 GAP_CHARS 不同）。
    空分母（无可比位点）→ 0.0（与矩阵页一致，区别于 SDT 页的 NaN）。
    """
    from .seq_identity import pair_identity
    return pair_identity(s1, s2, gap_chars='-', empty=0.0)


def _read_alignment(path):
    """读比对 → [(header, id, seq)]。header 原样保留（写回时不丢描述）。"""
    out = []
    for header, seq in iter_fasta(path):
        if not seq:
            continue
        tokens = header.split()
        out.append((header, tokens[0] if tokens else header, seq.upper()))
    return out


def qc_alignment(aln_path, out_dir, reference_id=None,
                 min_length=DEFAULT_MIN_LENGTH, max_gap=DEFAULT_MAX_GAP,
                 max_n=DEFAULT_MAX_N,
                 max_identity_drop=DEFAULT_MAX_IDENTITY_DROP,
                 logger=None):
    """比对 FASTA → 逐条质检 → 剔除不合格 → 写 clean/removed/报告。

    返回摘要 dict（含 n_in / n_keep / n_removed / reasons / 三个产物路径）。
    序列长度不一致（=输入其实没比对）直接抛 ValueError——比让 RDP5 报
    错更早、更明确。
    """
    recs = _read_alignment(aln_path)
    if not recs:
        raise ValueError('比对文件为空或无有效序列')
    lengths = {len(s) for _h, _i, s in recs}
    if len(lengths) > 1:
        lo, hi = min(lengths), max(lengths)
        raise ValueError(
            f'输入不是比对好的序列（长度 {lo}–{hi} bp 不一致，共 {len(lengths)} 种）。'
            '请先用「序列比对」卡做 MAFFT 比对，或改用已比对的 FASTA。')

    out_dir = str(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    n_cols = len(recs[0][2])

    # 参考：显式 ID（子串匹配）→ 否则长度中位数那条
    ref, ref_explicit = None, False
    if reference_id:
        ref = next((r for r in recs if reference_id in r[1]), None)
        ref_explicit = ref is not None
        if ref is None and logger:
            logger.log(f'[warn] 未找到参考序列 {reference_id}，改用长度中位数基准')
    if ref is None:
        order = sorted(range(len(recs)), key=lambda k: len(recs[k][2].replace('-', '')))
        ref = recs[order[len(recs) // 2]]
    ref_len = len(ref[2].replace('-', ''))
    # ⚠ identity 判据**只在显式参考命中时生效**（与参照管道一致：那边
    # `if args.reference:` 才取 ref_seq，否则 identity=None 整个跳过）。
    # 理由：98% 这条线是给「同一变异株 / 同一次爆发」的近等序列用的；拿它
    # 去卡一个病毒种的全部株系（跨亚组 identity 常只有 85–95%）会误杀大半
    # 样本——实测 CMV RNA3 十条跨亚组序列能被剔掉 7/10。
    ref_seq = ref[2] if ref_explicit else None

    report, keep, removed = [], [], []
    reasons = Counter()
    for header, sid, seq in recs:
        raw_len = len(seq.replace('-', ''))
        gap_ratio = seq.count('-') / n_cols if n_cols else 1.0
        n_ratio = seq.count('N') / n_cols if n_cols else 1.0
        len_ratio = raw_len / ref_len if ref_len else 1.0
        identity = pairwise_identity(seq, ref_seq) if ref_seq else None

        issues = []
        if len_ratio < min_length:
            issues.append(f'长度不足 ({len_ratio:.2f} < {min_length})')
        if gap_ratio > max_gap:
            issues.append(f'gap过多 ({gap_ratio:.2f} > {max_gap})')
        if n_ratio > max_n:
            issues.append(f'N过多 ({n_ratio:.2f} > {max_n})')
        if identity is not None:
            expected = (1 - max_identity_drop) * 100
            if identity < expected:
                issues.append(f'与参考identity过低 ({identity:.1f}% < {expected:.1f}%)')

        reason = '; '.join(issues)
        report.append({
            'id': sid, 'raw_length': raw_len, 'aln_length': n_cols,
            'len_ratio': round(len_ratio, 4), 'gap_ratio': round(gap_ratio, 4),
            'n_ratio': round(n_ratio, 4),
            'identity_to_ref': (round(identity, 2) if identity is not None else ''),
            'status': 'REMOVE' if issues else 'KEEP', 'reason': reason,
        })
        if issues:
            removed.append((header, seq))
            for i in issues:
                reasons[i.split(' ')[0]] += 1
        else:
            keep.append((header, seq))

    report_path = os.path.join(out_dir, 'align_qc_report.tsv')
    with open(report_path, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(report[0].keys()), delimiter='\t')
        w.writeheader()
        w.writerows(report)

    def _write(path, rows):
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            for header, seq in rows:
                f.write(f'>{header}\n')
                for i in range(0, len(seq), 70):
                    f.write(seq[i:i + 70] + '\n')

    clean_path = os.path.join(out_dir, 'clean.fasta')
    removed_path = os.path.join(out_dir, 'removed.fasta')
    _write(clean_path, keep)
    _write(removed_path, removed)

    summary = {
        'n_in': len(recs), 'n_keep': len(keep), 'n_removed': len(removed),
        'reasons': dict(reasons),
        'ref_id': ref[1], 'ref_len': ref_len, 'aln_len': n_cols,
        'ref_explicit': ref_explicit, 'identity_checked': ref_seq is not None,
        'removed': [r for r in report if r['status'] == 'REMOVE'],
        'min_seqs': MIN_SEQS, 'enough': len(keep) >= MIN_SEQS,
        'clean': clean_path, 'removed_fasta': removed_path,
        'report': report_path,
        'thresholds': {'min_length': min_length, 'max_gap': max_gap,
                       'max_n': max_n, 'max_identity_drop': max_identity_drop},
    }
    if logger:
        logger.log(f'序列预筛：{len(recs)} 条 → 保留 {len(keep)}、剔除 {len(removed)}'
                   f'（参考 {ref[1]}，{ref_len} bp；比对 {n_cols} 列）', 'PLAN')
        for r in report:
            if r['status'] == 'REMOVE':
                logger.log(f"  剔除 {r['id']}: 长度 {r['raw_length']}"
                           f"（比参考 {r['len_ratio']:.2f}）→ {r['reason']}", 'WARN')
    return summary
