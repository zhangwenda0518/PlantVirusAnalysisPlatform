# -*- coding: utf-8 -*-
"""LAMP 引物组设计引擎（环介导等温扩增，F3/F2/F1/B1/B2/B3 + 可选 LF/LB）。

净室实现，参数口径参考（仅参数，代码未复制）：
- primer.uni-shine.cn 参数面（闭源在线工具，不进平台；仅取分段长度/
  Tm/GC/间距约束的公开数值口径）
- Notomi et al. 2000（LAMP 原始方法）以来的公共约束：FIP=F1c+TTTT+F2、
  BIP=B1+TTTT+B2c、外引物 Tm 比内引物低 2-3°C、F2-B2 产物 120-180bp

布局（正链 5'→3'）：F3 → F2 → F1 →（核心扩增子区）→ B1 → B2 → B3。
序列方向：F3/F2/F1/LF 取正链正义；B3 取正链反向互补；
FIP = revcomp(F1区) + 'TTTT' + F2区(正义)；BIP = B1区(正义) + 'TTTT' + revcomp(B2区)。

评分独立于 crispr 的 CASPER 21 特征体系（互不干扰）；效用映射复用
crispr_design 的 _u_* 函数族。证据边界（与 t-crispr 同口径）：计算设计
仅支持候选优先级排序，不构成扩增效率、引物间干扰或基质性能的证据；
实验结论须按 L1-L5 逐级建立（LAMP 段：65°C Bst 酶体系、内外引物浓度比、
环引物增益需实验建立）。

工程约定（对齐 crispr_design）：全程离线；进程可取消（cancel 置位抛
RuntimeError）；中文输出统一 utf-8 落盘。
"""
import bisect
import json
import os
import time

from .crispr_design import (CrisprDesignError, _PURE_ACGT,
                            consensus_and_conservation,
                            gc_frac, group_segments, max_homo_run, read_targets,
                            revcomp, three_prime_gc)
from .utils import safe_open

ENGINE_VERSION = '1.0.0'
OUT_SUBDIR = 'lamp'
TOOL_ID = 'lamp'

# 分段参数（len/Tm 区间与最优值；GC 统一 30-65）。
# 2026-11-05 区间对齐 uni-shine 口径（每段 9-10°C 宽、target 为理想值）：
# 原 3°C 窄区间导致多分离物（MSA 保守过滤后候选变稀）时组装链全断
# （ORSV 5 分离物 0 组的根因）。严格性由排序（dev=|Tm-target|）承担，
# 硬筛只做"可接受范围"。
_SEG = {
    'F3': dict(lo=18, hi=24, opt=20, tm=(53.0, 62.5, 52.0)),
    'B3': dict(lo=18, hi=24, opt=20, tm=(53.0, 62.5, 52.0)),
    'F2': dict(lo=18, hi=24, opt=20, tm=(56.0, 66.0, 56.0)),
    'B2': dict(lo=18, hi=24, opt=20, tm=(56.0, 66.0, 56.0)),
    'F1': dict(lo=19, hi=25, opt=22, tm=(59.0, 68.0, 58.0)),
    'B1': dict(lo=19, hi=25, opt=22, tm=(59.0, 68.0, 58.0)),
    'LF': dict(lo=16, hi=24, opt=20, tm=(56.0, 66.0, 56.0)),
    'LB': dict(lo=16, hi=24, opt=20, tm=(56.0, 66.0, 56.0)),
}
_GC_LO, _GC_HI = 40.0, 60.0

# 智能预设（2026-11-05，对齐 uni-shine 的高GC/高AT预设）：GC 区间 + Tm 整体偏移
# 语义：预设只扩不缩（放宽对应侧，永不给默认参数下的可行序列减候选）——
# high_gc 抬 GC 上限 + 抬 Tm 上限；high_at 降 GC 下限（Tm 不动：极 AT 靶标
# 的 Tm 缺口需更长引物/特殊体系，非参数可解，报告注明边界）
_PRESETS = {
    'high_gc': {'gc': (_GC_LO, 75.0), 'tm_offset': 2.0},
    'high_at': {'gc': (20.0, _GC_HI), 'tm_offset': 0.0},
}

# 间距（正链，gap = 相邻区碱基数）。产物 = F2/B2 两 3' 端所夹区
# product = b2_start - f2_end + 1（含 F1/B1 靶区与核心区）
_GAP_F3F2 = (0, 60)           # 2026-11-05 20->40：对齐 uni-shine 严格模式
                              # F3-F2≤60 的口径（取其中值）；cons 过滤使候选
                              # 变稀时 20bp 上限是 ORSV 多分离物 0 组的直接原因
_GAP_F2F1 = (10, 40)          # uni-shine 理想值 40
_GAP_CORE = (40, 120)         # F1-B1（产物核心区；uni-shine 理想 30，我们放宽）
_GAP_B1B2 = (10, 40)          # uni-shine 理想 30
_GAP_B2B3 = (0, 60)           # 镜像 F3-F2
_PRODUCT = (120, 300, 150)     # F2-B2：min/max/opt（上限 300：
                              # uni-shine 100-500 与工艺 120-180 的折中，
                              # opt 150 维持扩增速度最优的排序偏好）
_LINKER = 'TTTT'
_TOPK_SEG = 3                  # 骨架内每段取 |Tm-opt| 前 K
_TOPK_F2B2 = 40                # 骨架对上限（按锚居中度/产物中心度排序）


_TM_P3 = None
_TM_P3_TRIED = False


def _tm(seq):
    """引物 Tm：primer3 calc_tm 优先，失败退 Wallace 公式。

    2026-11-05 性能：primer3 模块句柄模块级缓存（原实现每次调用都走
    get_module() 探测，6613bp×6 长度×8 段 ≈ 32 万次调用 → 260s 超时）。
    """
    global _TM_P3, _TM_P3_TRIED
    if not _TM_P3_TRIED:
        _TM_P3_TRIED = True
        try:
            from . import primer3_runtime
            _TM_P3 = primer3_runtime.get_module()
        except Exception:
            _TM_P3 = None
    if _TM_P3 is not None:
        try:
            return float(_TM_P3.bindings.calc_tm(
                seq, mv_conc=50.0, dv_conc=1.5, dntp_conc=0.8,
                dna_conc=50.0))
        except Exception:
            pass
    a = seq.count('A') + seq.count('T')
    g = seq.count('G') + seq.count('C')
    return 2.0 * a + 4.0 * g


# ---------------------------------------------------------------
# 段候选
# ---------------------------------------------------------------
def _seg_candidates(seq, key, cons=None, cons_min=0.85,
                    gc_lo=None, gc_hi=None, tm_off=0.0):
    """单段全序列候选枚举：位置 x 长度 -> Tm/GC/纯度/保守过滤。

    gc_lo/gc_hi/tm_off（2026-11-05 参数化，对齐 uni-shine 的自定义/预设）：
    None 时用模块默认；tm_off 整体平移该段 Tm target/min/max。
    返回 [{'start', 'len', 'end', 'seq', 'tm', 'dev'}]（dev=|Tm-opt|）。
    """
    sp = _SEG[key]
    gc_lo = _GC_LO if gc_lo is None else gc_lo
    gc_hi = _GC_HI if gc_hi is None else gc_hi
    out = []
    L = len(seq)
    for ln in range(sp['lo'], sp['hi'] + 1):
        for st in range(0, L - ln + 1):
            s = seq[st:st + ln]
            if set(s) - _PURE_ACGT:
                continue
            gc = gc_frac(s) * 100.0
            if not (gc_lo <= gc <= gc_hi):
                continue
            if max_homo_run(s) >= 5:
                continue
            if three_prime_gc(s):
                continue
            tm = _tm(s)
            lo, hi, _opt = sp['tm']
            # tm_off 语义（2026-11-05 标定）：只扩不缩——负偏移放宽下限
            # （低 Tm 候选进，AT 富集靶标场景），正偏移放宽上限（高 Tm
            # 候选进，GC 富集场景），排序目标同步平移
            if tm_off < 0:
                lo += tm_off
            elif tm_off > 0:
                hi += tm_off
            if not (lo <= tm <= hi):
                continue
            if cons is not None:
                w = cons[st:st + ln]
                if w and min(w) < cons_min:
                    continue
            out.append({'start': st, 'len': ln, 'end': st + ln,
                        'seq': s, 'tm': round(tm, 2), 'gc': round(gc, 1),
                        'dev': abs(tm - _opt)})   # 排序目标恒用原 opt：预设只放宽区间、不改已通过候选的排序
    out.sort(key=lambda d: d['dev'])
    return out


def _pick(cands, lo, hi, k=_TOPK_SEG):
    """从候选中筛坐标窗允许的，取 |Tm-opt| 前 k。

    兼容旧调用：cands 可以是 list（O(n) 扫）或 (sorted_by_start, starts)
    二元组（bisect，O(log n + 窗)）。"""
    if isinstance(cands, tuple):
        arr, starts = cands
        i0 = bisect.bisect_left(starts, lo)
        i1 = bisect.bisect_right(starts, hi)
        w = sorted(arr[i0:i1], key=lambda c: c['dev'])
        return w[:k]
    picked = [c for c in cands if lo <= c['start'] <= hi]
    return picked[:k]


def _pick_end(cands, lo, hi, k=_TOPK_SEG):
    """从候选中筛 **end** 坐标窗允许的，取 |Tm-opt| 前 k（F3 专用）。

    F3-F2 的间距语义是 `f2.start - f3.end`，按 start 筛窗会把"起点在窗内、
    终点越界"的候选放进 top-k；它们随后在组装复核时被剔除，却已挤掉
    合法的低排名候选（dev 前 3 截断 → 静默 0 组）。这里直接用 end 筛，
    预筛与组装复核口径一致。cands 为 (按 end 排序的 list, end 列表)。
    """
    if isinstance(cands, tuple):
        arr, ends = cands
        i0 = bisect.bisect_left(ends, lo)
        i1 = bisect.bisect_right(ends, hi)
        return sorted(arr[i0:i1], key=lambda c: c['dev'])[:k]
    return sorted((c for c in cands if lo <= c['end'] <= hi),
                  key=lambda c: c['dev'])[:k]


def _position_capped(cands, per_bucket=15, bucket=100):
    """位置分桶保留候选（2026-11-05）：全局按 dev 截断会让不同 Tm 段的
    top 候选位置互斥（ORSV 多分离物 f3 窗 100% 失败的根因）；每 bucket
    bp 内按 dev 取前 per_bucket 个 → 保住全序列位置多样性，规模可控。"""
    from collections import defaultdict
    buckets = defaultdict(list)
    for c in cands:
        buckets[c['start'] // bucket].append(c)
    out = []
    for lst in buckets.values():
        out.extend(sorted(lst, key=lambda c: c['dev'])[:per_bucket])
    out.sort(key=lambda c: c['dev'])
    return out


def _starts_sorted(cands):
    return sorted(c['start'] for c in cands)


def _ends_sorted(cands):
    return sorted(c['end'] for c in cands)


def _has_in(sorted_starts, lo, hi):
    """坐标窗内是否存在候选（bisect，O(log n)）。"""
    import bisect as _b
    if not sorted_starts:
        return False
    i = _b.bisect_left(sorted_starts, lo)
    return i < len(sorted_starts) and sorted_starts[i] <= hi


# ---------------------------------------------------------------
# 组装与评分
# ---------------------------------------------------------------
def _seginfo(c, seq):
    return {'seq': c['seq'], 'start': c['start'] + 1, 'len': c['len'],
            'tm': c['tm'], 'gc': c['gc']}


def _pair_pct(a, b):
    from .crispr_design import complementarity_pct
    return complementarity_pct(a, b)


def _score_set(parts, product_bp, anchor_span=None, f2_start=0):
    """组合评分（0-100）。罚：Tm 偏差 / 间距偏心 / 二聚体 / 发夹。"""
    from .crispr_design import dna_mfe
    score = 100.0
    for key in ('F3', 'F2', 'F1', 'B1', 'B2', 'B3'):
        score -= min(6.0, 0.8 * parts[key]['dev'])
    # 产物偏离 opt 罚
    dev_p = abs(product_bp - _PRODUCT[2])
    score -= min(8.0, 0.15 * dev_p)
    # 锚居中加成（方案 A）：锚中心贴近产物中心 +0~4
    if anchor_span:
        ac = (anchor_span[0] + anchor_span[1]) / 2.0
        pc = f2_start + product_bp / 2.0
        off = abs(ac - pc) / max(1.0, product_bp)
        score += 4.0 * max(0.0, 1.0 - 2.0 * off)
    # 二聚体快筛（all-pairs，complementarity_pct）
    seqs = {k: parts[k]['seq'] for k in ('F3', 'F2', 'F1', 'B1', 'B2', 'B3')}
    worst_cross = worst_self = 0.0
    keys = list(seqs)
    for i in range(len(keys)):
        worst_self = max(worst_self, _pair_pct(seqs[keys[i]], seqs[keys[i]]))
        for j in range(i + 1, len(keys)):
            worst_cross = max(worst_cross,
                              _pair_pct(seqs[keys[i]], seqs[keys[j]]))
    if worst_cross >= 70:
        score -= 30.0
    elif worst_cross >= 60:
        score -= 12.0
    if worst_self >= 70:
        score -= 20.0
    elif worst_self >= 60:
        score -= 8.0
    return round(max(0.0, score), 1), worst_cross, worst_self


def _qc_refine(sets, top_n=10):
    """入围复核：对前 top_n 组补 dna_mfe 发夹罚分并重排（ViennaRNA 慢，
    只对入围组算——组装阶段纯算术）。缺失时跳过。"""
    from .crispr_design import dna_mfe
    for s in sets[:top_n]:
        pen = 0.0
        for key in ('f3', 'f2', 'f1', 'b1', 'b2', 'b3'):
            mfe = dna_mfe(s[key]['seq'])
            if mfe is not None and mfe < -5.0:
                pen += 4.0
        s['score'] = round(max(0.0, s['score'] - pen), 1)
    sets.sort(key=lambda d: -d['score'])
    return sets


def design_lamp_set(seq, params=None, anchor_span=None, cons=None,
                    cancel=None):
    """单序列 LAMP 引物组设计。返回排序后的组列表（每段坐标 1 基）。

    anchor_span: (a0, a1) 0 基半开，要求 F2-B2 扩增子完全包住（方案 A
    传 crRNA 位点；方案 B 传 None）。cons: 逐列保守分（多分离物时传，
    段窗内 min 保守 < cons_min 的候选剔除）。
    """
    params = params or {}
    use_loop = str(params.get('use_loop', '1')).lower() not in ('0', 'false', 'no')
    # 智能预设 + 自定义覆盖（2026-11-05，对齐 uni-shine 的参数模式）：
    # preset 提供默认，显式 gc_min/gc_max/tm_offset 优先级更高
    _pre = _PRESETS.get(str(params.get('preset') or '').strip().lower()) or {}
    _gc_default = _pre.get('gc', (_GC_LO, _GC_HI))

    def _explicit(v):
        """参数归一：None/空白串 = 未填；0 是显式值（旧实现用 or 会吞掉）。

        web 端不填的数值输入以 '' 提交，这里统一折成 None，再用
        `is not None` 判显式值——否则 preset 的 tm_offset=2.0 会覆盖
        用户显式写的 0。
        """
        if isinstance(v, str):
            v = v.strip()
        return None if v is None or v == '' else v

    def _float_param(raw, default):
        if raw is None:
            return float(default)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(default)

    _gc_lo = _float_param(_explicit(params.get('gc_min')), _gc_default[0])
    _gc_hi = _float_param(_explicit(params.get('gc_max')), _gc_default[1])
    _gc_lo = max(0.0, min(100.0, _gc_lo))
    _gc_hi = max(0.0, min(100.0, _gc_hi))
    if _gc_lo > _gc_hi:
        _gc_lo, _gc_hi = _gc_hi, _gc_lo
    _tm_raw = _explicit(params.get('tm_offset'))
    _tm_off = _float_param(_tm_raw, _pre.get('tm_offset') or 0.0)
    _tm_off = max(-6.0, min(6.0, _tm_off))
    p_min = int(params.get('product_min') or _PRODUCT[0])
    p_max = int(params.get('product_max') or _PRODUCT[1])
    core_min, core_max = _GAP_CORE
    L = len(seq)
    if L < p_min + 60:
        return []

    _cm = float(params.get('cons_min') or 0.85)
    # 候选规模控制（2026-11-05 性能）：Tm 区间放宽后（uni-shine 口径
    # 9-10°C 宽）6613bp 单段候选可达数千，全量参与骨架枚举是分钟级；
    # 用位置分桶保留（每 100bp 取 dev 最优 6 个）——全局截断会让不同
    # Tm 段的 top 候选位置互斥（f3 窗 100% 失败），分桶保住位置多样性
    def _mk(key):
        return _position_capped(
            _seg_candidates(seq, key, cons=cons, cons_min=_cm,
                            gc_lo=_gc_lo, gc_hi=_gc_hi, tm_off=_tm_off))
    cands = {k: _mk(k) for k in ('F3', 'F2', 'F1', 'B1', 'B2', 'B3')}
    cand_loop = _mk('LF') if use_loop else []
    # 按 start 排序的二分索引（_pick bisect 版）；F3 另建 end 索引——
    # F3-F2 的间距语义是 f2.start - f3.end，按 start 筛窗与组装复核口径
    # 不一致（旧缺陷：起点在窗内、终点越界的候选进入 dev top-3，挤掉合法
    # 候选后静默 0 组）。B3/F2-F1/B1-B2 的间距都由后段 start 决定，仍用
    # start 索引。
    _idx = {k: (sorted(v, key=lambda c: c['start']),
                [c['start'] for c in sorted(v, key=lambda c: c['start'])])
            for k, v in cands.items()}
    _f3_by_end = sorted(cands['F3'], key=lambda c: c['end'])
    _idx_end_f3 = (_f3_by_end, [c['end'] for c in _f3_by_end])
    _lp_sorted = sorted(cand_loop, key=lambda c: c['start'])
    _idx_loop = (_lp_sorted, [c['start'] for c in _lp_sorted])

    # ---- 骨架枚举 + 可行性预筛（预筛后才排序截断——2026-11-05 检修：
    # 原实现先按产物中心度截断前 40，前 40 恰好全不可组装 -> 0 组）。
    # 预筛与组装复核必须同语义：F3 按 end 窗、B1 按每个 F1 变体分别判，
    # 否则预筛会误杀"仅对非首位 F1 变体可行"的骨架。
    f1_lo_off = _GAP_F2F1[0]                        # f2_end + 10
    f1_hi_off = _GAP_F2F1[1]                        # f2_end + 40
    b3_lo_off, b3_hi_off = _GAP_B2B3               # b2_end + 0..60
    skeletons = []
    _b2_sorted, _b2_starts = _idx['B2']
    _f3_ends = _ends_sorted(cands['F3'])
    _f1_starts = _starts_sorted(cands['F1'])
    _b3_starts = _starts_sorted(cands['B3'])
    for f2 in cands['F2']:
        # 2026-11-05 性能：预筛一律 bisect 存在性（原 _pick 全列表扫描在
        # 6613bp × 数千候选下是 30 亿次比较级，整轮超 300s）
        if not _has_in(_f1_starts, f2['end'] + f1_lo_off,
                       f2['end'] + f1_hi_off):
            continue
        # 骨架对：B2 只需在 [f2_end+p_min-1, f2_end+p_max-1] 位置窗内
        _i0 = bisect.bisect_left(_b2_starts, f2['end'] + p_min - 1)
        _i1 = bisect.bisect_right(_b2_starts, f2['end'] + p_max - 1)
        for b2 in _b2_sorted[_i0:_i1]:
            prod = b2['start'] - f2['end'] + 1
            if not (p_min <= prod <= p_max):
                continue
            if anchor_span:
                a0, a1 = anchor_span
                if not (f2['end'] < a0 and a1 < b2['start']):
                    continue
            # 预筛：F3 / B3 窗内存在候选（bisect）+ B1 双窗粗交集
            if not _has_in(_f3_ends, f2['start'] - _GAP_F3F2[1],
                           f2['start'] - _GAP_F3F2[0]):
                continue
            if not _has_in(_b3_starts, b2['end'] + b3_lo_off,
                           b2['end'] + b3_hi_off):
                continue
            _f1s_pre = _pick(_idx['F1'], f2['end'] + f1_lo_off,
                             f2['end'] + f1_hi_off)
            if not _f1s_pre:
                continue
            if not any(
                    f1v['end'] + core_min <= c['start']
                    <= f1v['end'] + core_max
                    and b2['start'] - _GAP_B1B2[1] <= c['end']
                    <= b2['start'] - _GAP_B1B2[0]
                    for f1v in _f1s_pre for c in cands['B1']):
                continue
            skeletons.append((f2, b2, prod))
    # 骨架排序：锚/产物居中优先，截断上限（预筛后）
    def _sk_key(t):
        f2, b2, prod = t
        if anchor_span:
            return abs((f2['start'] + prod / 2.0)
                       - ((anchor_span[0] + anchor_span[1]) / 2.0))
        return abs(prod - _PRODUCT[2])
    skeletons.sort(key=_sk_key)
    skeletons = skeletons[:_TOPK_F2B2]
    # 预筛只做存在性剪枝，不负责挑最终候选：上面 F3 用 end 窗、B1 对
    # 全部 top-K F1 变体判存在，与下方组装复核同语义；最终间距仍由组装
    # 阶段逐对复核（防止预筛口径再次与组装漂移）。

    sets_out = []
    for f2, b2, prod in skeletons:
        # F3 按 end 窗取候选：与组装复核的 f2.start - f3.end 同语义
        f3s = _pick_end(_idx_end_f3, f2['start'] - _GAP_F3F2[1],
                        f2['start'] - _GAP_F3F2[0])
        if not f3s:
            continue
        f1s = _pick(_idx['F1'], f2['end'] + f1_lo_off,
                    f2['end'] + f1_hi_off)
        if not f1s:
            continue
        # B1 的 B1B2 间距只依赖固定 b2；core 间距则随每个 F1 变体不同，
        # 必须按 F1 分别取（只按 f1s[0] 取会让其余 F1 变体永远无 B1 可配）
        b1_pool = [c for c in cands['B1']
                   if b2['start'] - _GAP_B1B2[1] <= c['end']
                   <= b2['start'] - _GAP_B1B2[0]]
        if not b1_pool:
            continue
        b3s = _pick(_idx['B3'], b2['end'] + b3_lo_off,
                    b2['end'] + b3_hi_off)
        if not b3s:
            continue
        import itertools
        best = None
        for f3, f1, b3 in itertools.product(f3s[:_TOPK_SEG],
                                            f1s[:_TOPK_SEG],
                                            b3s[:_TOPK_SEG]):
            # B1 core 双窗：按当前 F1 变体取 dev 前 K
            b1s = [c for c in b1_pool
                   if f1['end'] + core_min <= c['start']
                   <= f1['end'] + core_max]
            for b1 in b1s[:_TOPK_SEG]:
                # 间距复核
                if not (_GAP_F3F2[0] <= f2['start'] - f3['end']
                        <= _GAP_F3F2[1]):
                    continue
                if not (_GAP_F2F1[0] <= f1['start'] - f2['end']
                        <= _GAP_F2F1[1]):
                    continue
                if not (core_min <= b1['start'] - f1['end'] <= core_max):
                    continue
                if not (_GAP_B1B2[0] <= b2['start'] - b1['end']
                        <= _GAP_B1B2[1]):
                    continue
                if not (_GAP_B2B3[0] <= b3['start'] - b2['end']
                        <= _GAP_B2B3[1]):
                    continue
                parts = {'F3': f3, 'F2': f2, 'F1': f1, 'B1': b1,
                         'B2': b2, 'B3': b3}
                sc, wc, ws = _score_set(parts, prod, anchor_span,
                                        f2['start'])
                cand_set = {
                    'f3': _seginfo(f3, seq),
                    'f2': _seginfo(f2, seq),
                    'f1': _seginfo(f1, seq),
                    'b1': _seginfo(b1, seq),
                    'b2': _seginfo(b2, seq),
                    'b3': _seginfo(b3, seq),
                    'fip': (revcomp(seq[f1['start']:f1['end']]) + _LINKER
                            + seq[f2['start']:f2['end']]),
                    'bip': (seq[b1['start']:b1['end']] + _LINKER
                            + revcomp(seq[b2['start']:b2['end']])),
                    'product_bp': prod,
                    'f2_b2_span': [f2['start'], b2['end']],
                    'score': sc,
                    'qc': {'max_cross_pct': wc, 'max_self_pct': ws},
                    'route': 'lamp_enum',
                }
                if use_loop:
                    lf = _pick(_idx_loop, f2['end'], f1['start'])
                    lb = _pick(_idx_loop, b1['end'], b2['start'])
                    if lf:
                        cand_set['lf'] = _seginfo(lf[0], seq)
                        cand_set['lf_seq'] = seq[lf[0]['start']:lf[0]['end']]
                    if lb:
                        cand_set['lb'] = _seginfo(lb[0], seq)
                        cand_set['lb_seq'] = seq[lb[0]['start']:lb[0]['end']]
                if best is None or sc > best['score']:
                    best = cand_set
        if best:
            sets_out.append(best)
        if cancel is not None and cancel.is_set():
            raise RuntimeError('用户取消')
    sets_out.sort(key=lambda d: -d['score'])
    return _qc_refine(sets_out, top_n=max(10, _TOPK_F2B2 // 4))

# ---------------------------------------------------------------
# 输出
# ---------------------------------------------------------------
_ORDER_ROWS_HEAD = ['role', 'name', 'seq', 'len', 'tm', 'gc', 'start']


def _order_rows(s):
    """一组 -> 订购行（每引物一行）。"""
    rows = []
    for role in ('f3', 'f2', 'f1', 'b1', 'b2', 'b3'):
        d = s.get(role) or {}
        if d:
            rows.append([role.upper(), role + '_' + str(s.get('rank', '')),
                         d['seq'], d['len'], d['tm'], d['gc'], d['start']])
    rows.append(['FIP', 'fip_' + str(s.get('rank', '')), s['fip'],
                 len(s['fip']), '-', '-', '-'])
    rows.append(['BIP', 'bip_' + str(s.get('rank', '')), s['bip'],
                 len(s['bip']), '-', '-', '-'])
    if s.get('lf_seq'):
        rows.append(['LF', 'lf_' + str(s.get('rank', '')), s['lf_seq'],
                     len(s['lf_seq']), '-', '-', '-'])
    if s.get('lb_seq'):
        rows.append(['LB', 'lb_' + str(s.get('rank', '')), s['lb_seq'],
                     len(s['lb_seq']), '-', '-', '-'])
    return rows


_COLORS = {
    'f3': '#1565c0', 'f2': '#1e88e5', 'f1': '#64b5f6',
    'b1': '#e53935', 'b2': '#c62828', 'b3': '#b71c1c',
    'lf': '#2e7d32', 'lb': '#43a047',
}


def _write_plots(out_root, sets, logger=None):
    """引物组布局图：top 组在序列上的位置色块（PNG+SVG 双格式）。

    对齐平台静态图规范（PNG 主卡，SVG 可切换）；字体临时切
    Microsoft YaHei/SimHei，画完还原。matplotlib 缺失时跳过不报错。
    """
    def _logw(msg):
        if logger:
            try:
                logger(msg, 'WARN')
            except TypeError:
                pass

    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except Exception:
        _logw('matplotlib 不可用，跳过布局图（表格/报告不受影响）')
        return {}
    old = (plt.rcParams['font.sans-serif'],
           plt.rcParams['axes.unicode_minus'])
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei',
                                       'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    files = {}
    try:
        top = [s for s in sets[:5] if s]
        if not top:
            return {}
        fig, ax = plt.subplots(figsize=(10.5, 0.9 * len(top) + 1.2))
        for i, s in enumerate(top):
            y = len(top) - 1 - i
            f2s = s['f2']['start'] - 1
            b2e = s['b2']['start'] - 1 + s['b2']['len']
            # 产物区底衬
            ax.add_patch(plt.Rectangle((f2s, y - 0.28), b2e - f2s, 0.56,
                                       color='#eceff1', zorder=1))
            for key in ('f3', 'f2', 'f1', 'b1', 'b2', 'b3'):
                d = s[key]
                ax.add_patch(plt.Rectangle(
                    (d['start'] - 1, y + 0.06), d['len'], 0.22,
                    color=_COLORS[key], zorder=3))
                ax.text(d['start'] - 1 + d['len'] / 2.0, y + 0.34,
                        key.upper(), ha='center', va='bottom', fontsize=7.5)
            for key in ('lf', 'lb'):
                if s.get(key):
                    d = s[key]
                    ax.add_patch(plt.Rectangle(
                        (d['start'] - 1, y - 0.06), d['len'], 0.12,
                        color=_COLORS[key], zorder=3))
                    ax.text(d['start'] - 1 + d['len'] / 2.0, y - 0.18,
                            key.upper(), ha='center', va='top',
                            fontsize=7)
            ax.text(b2e + 40, y, f"#{i + 1}  {s['product_bp']}bp  "
                                 f"score {s['score']}", va='center',
                    fontsize=8, color='#37474f')
        x_lo = min(s['f3']['start'] - 1 for s in top) - 50
        x_hi = max(s['b2']['start'] - 1 + s['b2']['len'] for s in top)
        ax.set_xlim(max(0, x_lo), x_hi + 300)
        ax.set_ylim(-0.6, len(top) + 0.6)   # 顶部留白给图例
        ax.set_yticks([])
        ax.set_xlabel('序列坐标（1 基）')
        from matplotlib.patches import Patch
        ax.legend(handles=[
            Patch(color=_COLORS['f3'], label='上游：F3/F2/F1（FIP）'),
            Patch(color=_COLORS['b1'], label='下游：B1/B2/B3（BIP）'),
            Patch(color=_COLORS['lf'], label='环引物 LF/LB'),
            Patch(color='#eceff1', label='F2-B2 产物区'),
        ], loc='upper left', fontsize=7.5, frameon=False, ncol=2)
        ax.set_title('LAMP 引物组布局（Top %d）' % len(top))
        for spine in ('top', 'right', 'left'):
            ax.spines[spine].set_visible(False)
        png = os.path.join(out_root, 'primer_layout.png')
        fig.tight_layout()
        fig.savefig(png, dpi=150)
        files['layout_png'] = png
        try:
            svg = os.path.join(out_root, 'primer_layout.svg')
            fig.savefig(svg)
            files['layout_svg'] = svg
        except Exception:
            pass
        plt.close(fig)
    except Exception as e:
        _logw(f'布局图绘制失败（不影响其它产物）: {e}')
    finally:
        (plt.rcParams['font.sans-serif'],
         plt.rcParams['axes.unicode_minus']) = old
    return files


_SV_COLORS = {
    'f3': '#ef9a9a', 'f2': '#ffcc80', 'f1': '#fff59d',
    'b1': '#a5d6a7', 'b2': '#66bb6a', 'b3': '#42a5f5',
    'lf': '#cfd8dc', 'lb': '#b0bec5',
}

_COMP = str.maketrans('ACGTRYMKSWBDHVN', 'TGCAYRKMSWVHDBN')


def _write_seqview(out_root, consensus_by_seg, sets, logger=None):
    """引物-序列对照视图（Sequence View）：逐碱基着色 + 双链对齐。

    对齐 primer.uni-shine.cn 详情页的静态形态：每 80bp 一块，5' Target /
    5' Comp 两行，引物结合区按段着色，块下逐 10bp 坐标。自包含单文件
    HTML（无 JS/CSS 外链），results.html 模块页自动收录到交互页区。
    """
    try:
        top = [s for s in sets[:3] if s]
        if not top:
            return {}
        import html as _html

        def esc(x):
            return _html.escape(str(x or ''), quote=True)

        def _comp(s):
            return s.translate(_COMP)

        def _spans(s):
            sp = {}
            for k in ('f3', 'f2', 'f1', 'b1', 'b2', 'b3'):
                sp[s[k]['start'] - 1] = (s[k]['start'] - 1 + s[k]['len'], k)
            for k in ('lf', 'lb'):
                if s.get(k):
                    sp[s[k]['start'] - 1] = (s[k]['start'] - 1
                                             + s[k]['len'], k)
            return sp

        def _render_row(seg, b0, spans, seq_text):
            parts = []
            i = 0
            while i < len(seg):
                key = None
                for s0, (e0, k) in spans.items():
                    if s0 <= b0 + i < e0:
                        key, hit = k, (s0, e0)
                        break
                if key is None:
                    j = i + 1
                    while j < len(seg) and (b0 + j) not in spans:
                        j += 1
                    parts.append(esc(seq_text[i:j]))
                    i = j
                else:
                    s0, e0 = hit
                    j = min(len(seg), e0 - b0)
                    parts.append(
                        '<span class="svp" style="background:%s">%s</span>'
                        % (_SV_COLORS.get(key, '#eee'),
                           esc(seq_text[i:j])))
                    i = j
            return ''.join(parts)

        def blocks(seq, spans):
            out = []
            for b0 in range(0, len(seq), 80):
                seg = seq[b0:b0 + 80]
                row_t = _render_row(seg, b0, spans, seg)
                row_c = _render_row(seg, b0, spans, _comp(seg))
                coords = ''.join(
                    '<span>%s</span>' % esc(str(b0 + t + 1))
                    for t in range(0, len(seg), 10))
                out.append(
                    '<div class="svb">'
                    '<div class="svr"><span class="svl">5\' Target</span>'
                    '<span class="svs">' + row_t + '</span></div>'
                    '<div class="svr"><span class="svl">5\' Comp</span>'
                    '<span class="svs">' + row_c + '</span></div>'
                    '<div class="svc">' + coords + '</div>'
                    '</div>')
            return ''.join(out)

        secs = []
        for i, s in enumerate(top):
            seq = consensus_by_seg.get(s.get('segment') or 'main') or ''
            if not seq:
                continue
            legend = ('<div class="svleg">' + ''.join(
                '<span class="svli"><i style="background:%s"></i>%s'
                '（%snt · %s°C）</span>' % (
                    _SV_COLORS.get(k, '#eee'), k.upper(),
                    esc(s[k]['len']), esc(s[k]['tm']))
                for k in ('f3', 'f2', 'f1', 'lf', 'b1', 'b2', 'b3', 'lb')
                if s.get(k)) + '</div>')
            secs.append(
                '<h3>#%d（score %s，F2-B2 产物 %sbp）</h3>'
                % (i + 1, s['score'], s['product_bp'])
                + legend + blocks(seq, _spans(s)))

        doc = (
            '<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">\n'
            '<title>LAMP 引物-序列对照</title><style>\n'
            'body{font-family:-apple-system,Segoe UI,Roboto,Microsoft YaHei,'
            'sans-serif;color:#263238;margin:24px;background:#fff}\n'
            'h2{margin:0 0 4px;font-size:20px}\n'
            'h3{margin:22px 0 6px;font-size:15px;color:#37474f}\n'
            '.svleg{font-size:12px;color:#546e7a;margin:4px 0 8px}\n'
            '.svli{margin-right:14px;white-space:nowrap}\n'
            '.svli i{display:inline-block;width:12px;height:12px;'
            'border-radius:2px;margin-right:4px;vertical-align:-1px}\n'
            '.svb{border:1px solid #eceff1;border-radius:6px;'
            'padding:8px 10px;margin:8px 0}\n'
            '.svr{display:flex;align-items:baseline;margin:2px 0}\n'
            '.svl{width:78px;flex:none;font-size:11.5px;color:#90a4ae;'
            'font-family:Consolas,monospace}\n'
            '.svs{font-family:Consolas,monospace;font-size:13px;'
            'letter-spacing:0.6px;white-space:pre}\n'
            '.svp{border-radius:2px;padding:1px 0}\n'
            '.svc{display:flex;font-family:Consolas,monospace;font-size:10.5px;'
            'color:#b0bec5;margin-left:78px;margin-top:2px}\n'
            '.svc span{width:10ch;flex:none}\n'
            '</style></head><body>\n'
            '<h2>引物-序列对照（Sequence View）</h2>\n'
            '<div style="font-size:12px;color:#78909c">色块 = 引物结合区；'
            "5' Target 为正链模板，5' Comp 为其互补链；块下为 1 基坐标。"
            '引物 5′→3′ 序列与坐标见 lamp_sets.tsv。</div>\n'
            + ''.join(secs) + '</body></html>')
        path = os.path.join(out_root, 'sequence_view.html')
        with safe_open(path, 'wt') as f:
            f.write(doc)
        return {'seqview': path}
    except Exception as e:
        if logger:
            try:
                logger('序列对照视图生成失败: %s' % e, 'WARN')
            except TypeError:
                pass
        return {}


def _num_text(v):
    """数值文本：整数不带小数点，浮点按 %g（62.5 不写成 62.50）。"""
    return ('%g' % v) if isinstance(v, float) else str(v)


def _param_int(params, key, default):
    """params 取整数：None/空白串视为未填（0 是显式值，不能被 or 吞掉）。"""
    v = params.get(key)
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return default
    if v is None:
        return default
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _method_lines(params):
    """「方法与参数」正文：逐值由 _SEG/_GAP/_LINKER/_PRODUCT 运行时生成。

    报告是交给实验人员的口径，必须与引擎实现同源。旧版此处写死过另一套
    参数（17-22nt / Tm 59.5-62.5 / FIP=B1c+…），与 _seg_candidates 的
    实际区间长期不符，因此改为从常量生成：常量一改报告自动跟随，不再
    存在第二份会漂移的口径。
    """
    seg = []
    for key in ('F3', 'B3', 'F2', 'B2', 'F1', 'B1'):
        sp = _SEG[key]
        lo, hi, _opt = sp['tm']
        seg.append(f"{key} {sp['lo']}-{sp['hi']}nt "
                   f"Tm {_num_text(lo)}-{_num_text(hi)}")
    lf, lb = _SEG['LF'], _SEG['LB']
    loop = (f"LF {lf['lo']}-{lf['hi']}nt "
            f"Tm {_num_text(lf['tm'][0])}-{_num_text(lf['tm'][1])}、"
            f"LB {lb['lo']}-{lb['hi']}nt "
            f"Tm {_num_text(lb['tm'][0])}-{_num_text(lb['tm'][1])}"
            f"（可选）")
    p_min = _param_int(params, 'product_min', _PRODUCT[0])
    p_max = _param_int(params, 'product_max', _PRODUCT[1])
    return [
        '- 分段：' + '；'.join(seg) + '；' + loop
        + f"；FIP=F1c+{_LINKER}+F2、BIP=B1+{_LINKER}+B2c",
        '- 间距：'
        f"F3-F2 {_GAP_F3F2[0]}-{_GAP_F3F2[1]} / "
        f"F2-F1 {_GAP_F2F1[0]}-{_GAP_F2F1[1]} / "
        f"F1-B1 {_GAP_CORE[0]}-{_GAP_CORE[1]} / "
        f"B1-B2 {_GAP_B1B2[0]}-{_GAP_B1B2[1]} / "
        f"B2-B3 {_GAP_B2B3[0]}-{_GAP_B2B3[1]}；"
        f"F2-B2 产物 {p_min}-{p_max}bp"
        f"（排序偏好 {_PRODUCT[2]}bp）",
        f"- GC：默认 {_num_text(_GC_LO)}-{_num_text(_GC_HI)}%"
        f"（可经 gc_min/gc_max 或 preset 覆盖；tm_offset 只放宽 Tm "
        f"对应侧，不改变 GC 筛选）",
    ]


def write_outputs(out_dir, sets, params, summary, files):
    p = os.path.join(out_dir, 'lamp_sets.tsv')
    cols = ['rank', 'score', 'segment', 'product_bp', 'f3_seq', 'f3_start',
            'f2_seq', 'f2_start', 'f1_start', 'b1_start', 'b2_seq', 'b2_start',
            'b3_seq', 'b3_start', 'fip', 'bip', 'lf_seq', 'lb_seq',
            'max_cross_pct', 'max_self_pct']
    with safe_open(p, 'wt') as f:
        f.write('\t'.join(cols) + '\n')
        for i, s in enumerate(sets, 1):
            row = [i, s['score'], s.get('segment', ''), s['product_bp'],
                   s['f3']['seq'], s['f3']['start'], s['f2']['seq'],
                   s['f2']['start'], s['f1']['start'], s['b1']['start'],
                   s['b2']['seq'], s['b2']['start'], s['b3']['seq'],
                   s['b3']['start'], s['fip'], s['bip'],
                   s.get('lf_seq', ''), s.get('lb_seq', ''),
                   s['qc']['max_cross_pct'], s['qc']['max_self_pct']]
            f.write('\t'.join(str(x) for x in row) + '\n')
    files['sets'] = p

    p = os.path.join(out_dir, 'lamp_order.tsv')
    with safe_open(p, 'wt') as f:
        f.write('\t'.join(_ORDER_ROWS_HEAD) + '\n')
        for i, s in enumerate(sets, 1):
            s['rank'] = i
            for r in _order_rows(s):
                f.write('\t'.join(str(x) for x in r) + '\n')
            f.write('\n')
    files['order'] = p

    p = os.path.join(out_dir, 'report.md')
    lines = ['# LAMP 引物组设计报告 — ' + summary.get('target', '')]
    lines.append('')
    lines.append(f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}，"
                 f"耗时 {summary.get('elapsed_sec')}s")
    lines.append(f"- 输入：{summary.get('n_input_records')} 条 / "
                 f"{summary.get('n_segments')} 个片段组；"
                 f"输出 {summary.get('n_sets_output')} 组")
    lines.append('')
    lines.append('## 推荐组合')
    lines.append('')
    for s in sets[:8]:
        lines.append(f"### #{s.get('rank')}（score {s['score']}，"
                     f"F2-B2 产物 {s['product_bp']}bp）")
        lines.append('')
        lines.append('| 引物 | 序列 (5\'→3\') | 长度 | Tm | GC% | 起 (1基) |')
        lines.append('|---|---|---|---|---|---|')
        for role, d in (('F3', s['f3']), ('F2', s['f2']), ('F1', s['f1']),
                        ('B1', s['b1']), ('B2', s['b2']), ('B3', s['b3'])):
            lines.append(f"| {role} | {d['seq']} | {d['len']} | {d['tm']} | "
                         f"{d['gc']} | {d['start']} |")
        lines.append(f"| FIP | {s['fip']} | {len(s['fip'])} | - | - | - |")
        lines.append(f"| BIP | {s['bip']} | {len(s['bip'])} | - | - | - |")
        if s.get('lf_seq'):
            lines.append(f"| LF | {s['lf_seq']} | {len(s['lf_seq'])} | - | - | - |")
        if s.get('lb_seq'):
            lines.append(f"| LB | {s['lb_seq']} | {len(s['lb_seq'])} | - | - | - |")
        lines.append('')
    lines.append('## 方法与参数')
    lines.extend(_method_lines(params))
    lines.append('- QC：all-pairs 二聚体（complementarity_pct 快筛）、'
                 'DNA 发夹 MFE≥-5、同聚物 <5、3\'端 GC 规避、'
                 '多分离物段窗保守分 ≥' + str(params.get('cons_min') or 0.85))
    lines.append('')
    lines.append('## 证据边界')
    lines.append('')
    lines.append('> 计算设计仅支持候选优先级排序，不构成扩增效率、引物间'
                 '干扰或基质性能的证据；实验结论须按 L1-L5 逐级建立'
                 '（LAMP 段：65°C Bst 酶体系、内外引物浓度比、环引物增益）')
    body = '\n'.join(lines) + '\n'
    with safe_open(p, 'wt') as f:
        f.write(body)
    files['report'] = p


def run_chain(targets, params, out_dir, log=None, prog=None, cancel=None):
    """LAMP 引物组设计（方案 B 独立工具入口，签名对齐 crispr_design）。

    params: input 由调用方传 targets；product_min/product_max/use_loop/
    cons_min/group_by_segment/top_n/threads。
    返回 (summary, result)。
    """
    t0 = time.time()

    def _log(msg, level='INFO'):
        if log:
            try:
                log(msg)
            except TypeError:
                try:
                    log.log(msg, level)
                except TypeError:
                    pass

    def _prog(stage, frac, msg=''):
        if prog:
            try:
                prog(stage, frac, msg)
            except Exception:
                pass

    records = read_targets(targets)
    gflag = str(params.get('group_by_segment', '1')).lower() \
        not in ('0', 'false', 'no')
    groups = group_segments(records, gflag)
    top_n = max(1, min(200, int(params.get('top_n') or 20)))
    params = dict(params)
    params.setdefault('product_min', _PRODUCT[0])
    params.setdefault('product_max', _PRODUCT[1])

    os.makedirs(os.path.join(out_dir, OUT_SUBDIR), exist_ok=True)
    out_root = os.path.join(out_dir, OUT_SUBDIR)
    work_dir = os.path.join(out_root, '_work')
    os.makedirs(work_dir, exist_ok=True)

    all_sets = []
    seg_summary = {}
    consensus_by_seg = {}
    seg_names = sorted(groups)
    for si, seg in enumerate(seg_names):
        recs = groups[seg]
        base = 0.05 + 0.6 * (si / max(1, len(seg_names)))
        _prog('比对', base, f'片段 {seg}：比对与保守性')
        if cancel is not None and cancel.is_set():
            raise RuntimeError('用户取消')
        consensus, cons, n_used, msa_used, _cm, _na = \
            consensus_and_conservation(
                recs, work_dir,
                threads=int(params.get('threads') or 0) or None,
                logger=None, cancel=cancel)
        _prog('设计', base + 0.15, f'片段 {seg}：LAMP 引物组设计')
        sets = design_lamp_set(consensus, params=params, cons=cons,
                               cancel=cancel)
        consensus_by_seg[seg] = consensus
        for s in sets:
            s['segment'] = seg
        all_sets.extend(sets)
        seg_summary[seg] = {
            'n_isolates': len(recs), 'consensus_len': len(consensus),
            'msa': bool(msa_used), 'n_sets': len(sets),
        }
        _log(f'片段 {seg}: {len(recs)} 条 / 共识 {len(consensus)}bp → '
             f'{len(sets)} 组')

    all_sets.sort(key=lambda d: -d['score'])
    ranked = all_sets[:top_n]
    for i, s in enumerate(ranked, 1):
        s['rank'] = i
    _prog('产出', 0.9, '写出产物')
    files = {}
    summary = {
        'tool': 'lamp', 'ok': True, 'engine_version': ENGINE_VERSION,
        'target': os.path.basename(str(targets)),
        'n_input_records': len(records), 'n_segments': len(groups),
        'segments': seg_summary,
        'n_sets_total': len(all_sets), 'n_sets_output': len(ranked),
        'best_score': ranked[0]['score'] if ranked else 0.0,
        'out_dir': out_root,
        'elapsed_sec': round(time.time() - t0, 1),
        'evidence_note': (
            '证据边界：计算设计仅支持候选优先级排序，不构成扩增效率、'
            '引物间干扰或基质性能的证据；实验结论须按 L1-L5 逐级建立'
            '（LAMP 段：65°C Bst 酶体系、内外引物浓度比、环引物增益）'),
    }
    write_outputs(out_root, ranked, params, summary, files)
    files.update(_write_plots(out_root, ranked, logger=_log))
    files.update(_write_seqview(out_root, consensus_by_seg, ranked,
                                logger=_log))
    # 前端结果卡渲染用：前 3 组摘要（对齐 uni-shine 详情页的 FIP/BIP 高亮展示）
    summary['top_sets'] = [{
        'rank': s.get('rank'), 'score': s['score'],
        'segment': s.get('segment', 'main'),
        'seq_len': len(consensus_by_seg.get(s.get('segment') or 'main')
                       or ''),
        'product_bp': s['product_bp'],
        'fip': s['fip'], 'bip': s['bip'],
        'lf': s.get('lf'), 'lb': s.get('lb'),
        'lf_seq': s.get('lf_seq', ''), 'lb_seq': s.get('lb_seq', ''),
        'primers': {k: {'seq': s[k]['seq'], 'tm': s[k]['tm'],
                        'start': s[k]['start'], 'len': s[k]['len'],
                        'gc': s[k]['gc']}
                    for k in ('f3', 'f2', 'f1', 'b1', 'b2', 'b3')},
        'qc': s['qc'],
        'flags': ([f'自二聚{s["qc"]["max_self_pct"]}%']
                  if s['qc']['max_self_pct'] >= 70 else
                  []) + ([f'交叉{s["qc"]["max_cross_pct"]}%']
                        if s['qc']['max_cross_pct'] >= 70 else []),
        'penalty': round(100.0 - s['score'], 1),
        'f2_b2_span': s['f2_b2_span'],
    } for s in ranked[:12]]
    summary['files'] = {k: os.path.relpath(v, out_dir)
                        for k, v in files.items()}
    with safe_open(os.path.join(out_root, 'summary.json'), 'wt') as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    try:
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)
    except OSError:
        pass
    _prog('完成', 1.0, '完成')
    _log(f'LAMP 设计完成：{len(ranked)} 组输出（耗时 '
         f'{summary["elapsed_sec"]}s）')
    result = {'sets': ranked, 'summary': summary}
    return summary, result