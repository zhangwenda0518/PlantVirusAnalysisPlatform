# -*- coding: utf-8 -*-
"""基因组交互轨道的数据层（前端 d3 渲染用）。

与 gbdraw 静态图同源（GenBank，或 FASTA + GFF3），但输出**结构化 JSON**：
记录名、长度、特征（坐标 / 链向 / 标签）与下采样 GC 曲线。
静态 SVG 负责出图归档，交互轨道负责缩放、悬停与定位 —— 两者互补。

口径与 `/api/tool/genoplot_preview` 保持一致：给了 GenBank 时忽略 FASTA
（GenBank 自带序列与注释，再叠一份 FASTA 只会产生两套坐标）。
"""
from __future__ import annotations

__all__ = ['read_track']

_GB_SUFFIX = ('.gb', '.gbk', '.gbff', '.genbank')
_GFF_SUFFIX = ('.gff', '.gff3')
#: 这些类型不画（source 是整条记录的容器，画出来是一条覆盖全长的灰条）
_SKIP_TYPES = {'source'}
#: 标签取值优先级：越靠前越像「用户认识的名字」
_LABEL_KEYS = ('gene', 'locus_tag', 'Name', 'name', 'product', 'note', 'ID')


def _label_of(q: dict) -> str:
    for k in _LABEL_KEYS:
        v = q.get(k)
        if isinstance(v, (list, tuple)):
            v = v[0] if v else ''
        if v:
            return str(v)[:80]
    return ''


def _norm_strand(v) -> int:
    if v in (1, '+', '1', '+1'):
        return 1
    if v in (-1, '-', '-1'):
        return -1
    return 0


def _gc_track(seq, windows: int) -> list[list[float]]:
    """下采样 GC 曲线：返回 [[起点 bp, GC 比例], ...]。"""
    n = len(seq)
    if n <= 0:
        return []
    win = max(20, n // max(1, windows))
    out = []
    for start in range(0, n, win):
        chunk = seq[start:start + win]
        s = str(chunk).upper()
        gc = s.count('G') + s.count('C')
        at = s.count('A') + s.count('T')
        denom = gc + at
        if denom:
            out.append([start, round(gc / denom, 4)])
    return out


def _from_genbank(path: str, max_records: int, max_features: int,
                  gc_windows: int):
    from Bio import SeqIO
    records, truncated = [], False
    for rec in SeqIO.parse(path, 'genbank'):
        if len(records) >= max_records:
            truncated = True
            break
        feats, cut = [], False
        for f in rec.features:
            ftype = str(f.type)
            if ftype in _SKIP_TYPES:
                continue
            try:
                start = int(f.location.start) + 1      # 0-based half-open → 1-based
                end = int(f.location.end)
            except Exception:
                continue
            if end < start:
                continue
            if len(feats) >= max_features:
                cut = True
                break
            q = {k: v for k, v in f.qualifiers.items()}
            feats.append({
                'type': ftype,
                'start': start,
                'end': end,
                'strand': _norm_strand(f.location.strand),
                'label': _label_of(q),
                'product': str(q.get('product', [''])[0]
                               if isinstance(q.get('product'), list)
                               else q.get('product', ''))[:80],
            })
        truncated = truncated or cut
        records.append({
            'name': rec.id or rec.name or 'record',
            'description': (rec.description or '')[:120],
            'length': len(rec.seq),
            'features': feats,
            'gc': _gc_track(rec.seq, gc_windows),
        })
    return records, truncated


def _parse_gff(path: str) -> dict:
    """GFF3 → {seqid: [feature, ...]}；列不合法的行直接跳过。"""
    out = {}
    with open(path, 'r', encoding='utf-8', errors='replace') as fh:
        for line in fh:
            if not line or line.startswith('#'):
                continue
            cols = line.rstrip('\n').split('\t')
            if len(cols) < 9:
                continue
            seqid, ftype, start, end, strand, attrs = (
                cols[0], cols[2], cols[3], cols[4], cols[6], cols[8])
            if ftype in _SKIP_TYPES:
                continue
            try:
                start, end = int(start), int(end)
            except ValueError:
                continue
            q = {}
            for part in attrs.split(';'):
                part = part.strip()
                if not part or '=' not in part:
                    continue
                k, v = part.split('=', 1)
                q[k] = v
            out.setdefault(seqid, []).append({
                'type': ftype, 'start': start, 'end': end,
                'strand': _norm_strand(strand),
                'label': _label_of(q), 'product': str(q.get('product', ''))[:80],
            })
    return out


def _from_fasta(fasta: str, gff: str | None, max_records: int,
                max_features: int, gc_windows: int):
    from Bio import SeqIO
    by_seq = _parse_gff(gff) if gff else {}
    records, truncated = [], False
    for rec in SeqIO.parse(fasta, 'fasta'):
        if len(records) >= max_records:
            truncated = True
            break
        feats = by_seq.get(rec.id) or by_seq.get(rec.name) or []
        if len(feats) > max_features:
            feats, truncated = feats[:max_features], True
        records.append({
            'name': rec.id or rec.name or 'record',
            'description': (rec.description or '')[:120],
            'length': len(rec.seq),
            'features': feats,
            'gc': _gc_track(rec.seq, gc_windows),
        })
    return records, truncated


def read_track(fasta=None, ann=None, *, max_records: int = 20,
               max_features: int = 800, gc_windows: int = 400) -> dict:
    """把输入解析成交互轨道数据。

    ann 为 GenBank 时以它为准（与预览端点同口径）；否则 fasta 必填，
    ann 若为 GFF3 则作为注释叠加。
    """
    ann_l = str(ann or '').lower()
    if ann and ann_l.endswith(_GB_SUFFIX):
        recs, cut = _from_genbank(ann, max_records, max_features, gc_windows)
        source = 'genbank'
    elif fasta:
        gff = ann if (ann and ann_l.endswith(_GFF_SUFFIX)) else None
        recs, cut = _from_fasta(fasta, gff, max_records, max_features,
                                gc_windows)
        source = 'fasta+gff' if gff else 'fasta'
    else:
        raise ValueError('需要 GenBank（ann）或 FASTA（fasta）输入')
    return {
        'ok': True,
        'source': source,
        'records': recs,
        'truncated': bool(cut),
        'limits': {'records': max_records, 'features': max_features},
    }
