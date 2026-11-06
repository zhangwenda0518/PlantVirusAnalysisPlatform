# -*- coding: utf-8 -*-
"""成对序列同一性（identity）唯一权威实现。

历史背景：平台曾有 5 处独立的 identity 公式拷贝（sdt_exact / phylo /
align_qc / phylodyn_alignqc / structcmp），2026-11-03 已把数值口径对齐，
2026-11-04 统一为本模块。gap 集、空分母语义、百分比/小数由调用方按契约
选择参数；核心计数逻辑只有这一份。

代数恒等：对等长序列，SDT Get_Similarity 公式
  (1 - dist/(min_len - gaps)) * 100  ≡  match / comparable * 100
（denom = min_len - gaps = 双方均非 gap 的列数；dist = comp - match）。
不等长输入按 zip 截断到较短序列（与原 sdt_exact._sdt_formula 一致）。

调用方契约（数值行为与重构前逐处一致）：
  · sdt_exact._sdt_formula   gap='-.'  empty=nan   scale=100  → 百分比，空分母 NaN
  · phylo.pairwise_identity_matrix  gap='-.'  comp=0→0.0      → 百分比矩阵
  · align_qc.pairwise_identity      gap='-'   empty=0.0       → 百分比（参照管道口径）
  · phylodyn_alignqc.pairwise_identity gap='-' empty=None     → 小数 0-1 / None
  · web/tool_jobs structcmp         gap='-.'  comp=0→None     → 百分比 round 2
  · phylo._nj_distance_matrix 为 count_matches 的 numpy 向量化等价（性能路径）
"""

GAP_CHARS = '-.'          # '-' 与 '.' 同为 gap（trimAl/BioEdit 产物常见）
GAP_NT_ONLY = '-'


def count_matches(a, b, gap_chars=GAP_CHARS):
    """zip(a,b) 逐位计数 → (match, comparable)。

    任一侧属于 gap_chars → 该位点从分母排除；否则 comparable+1，
    两字符相同再 match+1。
    """
    match = comp = 0
    for x, y in zip(a, b):
        if x in gap_chars or y in gap_chars:
            continue
        comp += 1
        if x == y:
            match += 1
    return match, comp


def pair_identity(a, b, *, gap_chars=GAP_CHARS, empty=0.0, scale=100.0):
    """成对 identity = match / comparable * scale。

    empty：无可比位点（含任一侧为空串）时的返回值，由调用方按契约选择
    （NaN / 0.0 / None），本函数不做二次包装。
    scale：100.0 → 百分比；1.0 → 0-1 小数。
    """
    match, comp = count_matches(a, b, gap_chars)
    if comp == 0:
        return empty
    return match / comp * scale
