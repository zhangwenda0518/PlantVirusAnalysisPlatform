# -*- coding: utf-8 -*-
"""cricon —— 「CRISPR 检测试剂设计（Cas12a/Cas13a）+ CONAN-SWIFT（Cas3/I-E）
crRNA 设计」双路线联合编排器（工具卡 t-cricon / CLI cricon-design）。

⚠ 归档（2026-11-05 起）：本模块是 conan/cricon 联合编排器，网页/CLI 的
入口已于 2026-11-05 摘除（见 Virus_Platform_Core/web/tools_api.py 的
conan/cricon 归档注记），**非线上工具**、当前线上链路不调用；保留待恢复。
文件与导入路径刻意不移动，以免破坏 tests/test_cricon_design.py 等既有测试的
导入。恢复入口前请先复核本模块的交叉审核口径与坐标约定（尤其
`_convergence` 的 1 基闭区间口径）。

为什么编排层整合、引擎层不合并：见 conan_design.py 模块头「为什么独立成
模块」四条（评分模型/输出形态/种子口径/比对方式都不同）——编排器只做
调度、交叉审核与统一产出，不碰两引擎内部逻辑。

交叉审核语义边界（报告固定注记）：
- near_fasta 双喂：crispr 路线当排除库（k-mer/BLAST 序列级筛查）；conan
  路线当负组（进合并 MSA 做同源位点双重失配判定）。同一文件两条语义、
  两套独立判定，不合并成单一"特异性"数字。
- CONAN 向导宿主筛查 MVP 未做：CONAN 特异性判据是负组双重失配；序列级
  筛查仅对近缘文件补位。tier0 k-mer 命中可能即负组同源位点（PAM 已变），
  需对照 conan/negative_checks.tsv 判读，非必然 veto——只标注、不否决。
- 同位点收敛标记前提：两引擎共识规则同口径且坐标同为无 gap 目标共识坐标；
  两路线共识长度不一致时跳过（坐标不可比）。
- 建议表只给参数建议文本，不自动重跑（计划不等于已执行）。
- 证据边界（双路线同一口径）：计算设计仅支持候选优先级排序，不构成 crRNA
  活性、错配区分度、扩增兼容性或基质性能的证据；实验结论须按
  cricon/validation_checklist.md 的 L1-L5 逐级建立。

工程约定：全程离线，仅复用已纳管工具（经两引擎间接使用）；进程可取消
（cancel 置位即抛 RuntimeError）；中文输出统一 utf-8 落盘。
"""
import json
import os
import time

from .crispr_design import (CrisprDesignError, EVIDENCE_NOTE, SCREEN_K,
                            build_exclusion_index, group_segments,
                            read_targets, screen_windows)
from .crispr_design import run_chain as _crispr_run_chain
from .conan_design import run_chain as _conan_run_chain
from .utils import safe_open

ENGINE_VERSION = '1.0.0'
TOOL_ID = 'cricon'
OUT_SUBDIR = 'cricon'

# 进度带：crispr 0.50 / conan 0.30 / 交叉审核与统一产出 0.16
_BAND_CRISPR = (0.02, 0.52)
_BAND_CONAN = (0.52, 0.82)
_BAND_AUDIT = (0.82, 0.98)

# ---- 参数映射（cricon 参数名 -> 引擎参数名，cr_/cn_ 前缀映射回原名）----
_CR_MAP = {
    'cr_nuclease': 'nuclease',
    'cr_amp_mode': 'amp_mode',
    'cr_profile': 'profile',
    'cr_probe_type': 'probe_type',
    'cr_use_host': 'use_host',
    'cr_spacer_len': 'spacer_len',
    'cr_top_n': 'top_n',
    'cr_flank': 'flank',
    'cr_site_margin': 'site_margin',
    'cr_product_min': 'product_min',
    'cr_product_max': 'product_max',
    'cr_max_sites': 'max_sites',
}
_CN_MAP = {
    'cn_pam': 'pam',
    'cn_spacer_len': 'spacer_len',
    'cn_seed_len': 'seed_len',
    'cn_ignored': 'ignored',
    'cn_max_nonseed_mm': 'max_nonseed_mm',
    'cn_terminal_margin': 'terminal_margin',
    'cn_exclude_terminal': 'exclude_terminal',
    'cn_select_gc': 'select_gc',
    'cn_gc_min': 'gc_min',
    'cn_gc_max': 'gc_max',
    'cn_exclude_homopolymer': 'exclude_homopolymer',
    'cn_homopolymer': 'homopolymer',
    'cn_top_n': 'top_n',
}

_CROSS_SEMANTICS_NOTE = (
    'near_fasta 双喂语义：crispr 路线当排除库（k-mer/BLAST 序列级筛查）；'
    'conan 路线当负组（进合并 MSA 做同源位点双重失配判定）。同一文件两条'
    '语义、两套独立判定，不合并成单一"特异性"数字。')

_EVIDENCE_NOTE = (
    '证据边界（ACS Sensors 2026 综述口径，双路线同一口径）：本结果为序列级'
    '候选优先级排序，不构成 crRNA 活性、错配区分度、扩增兼容性或基质性能'
    '的证据；实验结论须按 cricon/validation_checklist.md 的 L1-L5 逐级建立。')

_CITATIONS = [
    'Wang H. et al., ACS Sensors 2026（证据边界框架）',
    'Low S.J. et al., Commun Biol 2025 PathoGD（群体覆盖与近缘筛查口径）',
    'CASPER, iGEM 2025 Lambert GA（crRNA x 引物复合评分特征体系）',
    'AutoCORDSv2 / RPA-Primer-Design / CRISPRPrimerDesigner（参数口径参考）',
    'Yoshimi et al. 2022, Commun Biol（CONAN：CRISPR-Cas3 诊断）',
    'CONAN-SWIFT, bioRxiv 2026.08.25.746999（RT-LAMP + Cas3 反式切割 + '
    '侧流层析现场路线）',
]


class _Log:
    """日志适配：兼容 callable 与 TaskLogger 口径；异常吞掉不阻塞。"""

    def __init__(self, cb):
        self._cb = cb

    def __call__(self, msg, level='INFO'):
        if not self._cb:
            return
        try:
            try:
                self._cb.log(msg, level)
            except (AttributeError, TypeError):
                self._cb(msg)
        except Exception:
            pass


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise RuntimeError('用户取消')


def _flag(v, default='1'):
    return str(v if v is not None else default).strip().lower() \
        not in ('0', 'false', 'no')


# ---------------------------------------------------------------
# 输入预检（警告只提示、不否决——否决口径归引擎）
# ---------------------------------------------------------------
def _preflight(records, groups, params, run_conan):
    warns = []
    if len(records) == 1:
        warns.append('输入仅 1 条序列：群体覆盖率平凡（n=1，覆盖率恒 100%），'
                     '真实设计建议 >=5-10 条分离物。')
    if len(records) > 2 and len(groups) == 1:
        lens = [len(s) for _, s in records]
        if max(lens) >= 2 * min(lens):
            warns.append(
                '多条序列全落同一片段组（无 RNA1/S2 等标签）且长度差异大'
                f'（{min(lens)}-{max(lens)}bp）：可能混入不同分子，同一次比对'
                '共识会被非多数列稀释（PAM/引物扫描被跳过）。建议按分子分文件'
                '或多分体 header 打片段标签。')
    if run_conan and not (params.get('near_fasta') or '').strip():
        warns.append('CONAN 路线未提供负组（near_fasta）：负组同源位点双重失配'
                     '判据缺位，本轮仅通用 Cas3 设计，无近缘区分能力。')
    if not _flag(params.get('cr_use_host'), '1'):
        warns.append('crispr 路线宿主筛查已关闭（cr_use_host=0）：宿主脱靶仅剩'
                     '序列级近缘筛查（若提供 near_fasta），需在 L2 实验段补宿主'
                     '核酸背景验证。')
    return warns


# ---------------------------------------------------------------
# 交叉审核（数据全部来自引擎内存返回值，不重读产物文件）
# ---------------------------------------------------------------
def _coverage_matrix(cr_result, cn_result, seg_order):
    """片段 x 路线覆盖矩阵。返回 (rows, segs)。"""
    segs = (list(seg_order or [])
            + [s['segment'] for s in (cr_result['sets'] if cr_result else [])]
            + list(cn_result['segments'].keys() if cn_result else []))
    segs = list(dict.fromkeys(segs))
    rows = []
    for seg in segs:
        n_cr = sum(1 for s in (cr_result['sets'] if cr_result else [])
                   if s['segment'] == seg)
        n_cn = 0
        if cn_result:
            cands = (cn_result['segments'].get(seg) or {}).get('candidates') or []
            n_cn = len({(c.get('strand'), c.get('cons_start')) for c in cands})
        if n_cr and n_cn:
            status = '双路线覆盖'
        elif n_cr:
            status = '仅 crispr'
        elif n_cn:
            status = '仅 conan'
        else:
            status = '-'
        rows.append({'segment': seg, 'crispr_combos': n_cr,
                     'conan_sites': n_cn, 'status': status})
    return rows, segs


def _screen_one(seq, idx):
    r = dict(screen_windows(seq, idx, k=SCREEN_K))
    return {'min_tier': r.get('min_tier'), 'n_mm0': r.get('n_mm0', 0),
            'n_mm1': r.get('n_mm1', 0), 'n_mm2': r.get('n_mm2', 0),
            'three_prime_mm1': bool(r.get('three_prime_mm1'))}


def _cross_screen_conan(near_fasta, cn_result, work_dir, logger, cancel):
    """CONAN 候选 x 近缘文件 k-mer 补筛查（复用 crispr 引擎排除库机制）。

    返回 (rows, warn)；rows=None 表示索引构建失败/跳过。
    """
    if not near_fasta or not cn_result:
        return None, None
    idx = build_exclusion_index('cricon_near', near_fasta, SCREEN_K,
                                cache_dir=work_dir, logger=logger,
                                cancel=cancel)
    if 'skipped' in idx:
        return None, f'CONAN 向导补筛查索引构建跳过：{idx["skipped"]}'
    rows = []
    for seg, d in sorted(cn_result['segments'].items()):
        for cand in d.get('candidates') or []:
            proto = cand.get('protospacer_5to3_after_PAM') or ''
            guide = cand.get('guide_spacer_5to3_RC') or ''
            rs = [_screen_one(proto, idx), _screen_one(guide, idx)]
            worst = None
            n_mm0 = n_mm1 = 0
            for r in rs:
                if r['min_tier'] is None:
                    continue
                if worst is None or r['min_tier'] < worst:
                    worst = r['min_tier']
                n_mm0 += r['n_mm0']
                n_mm1 += r['n_mm1']
            note = ''
            if worst == 0:
                note = ('tier0 命中可能即负组同源位点本身（PAM 已变），需对照 '
                        'conan/negative_checks.tsv 的 negative_locus_pass 判读，'
                        '非必然 veto')
            rows.append({
                'segment': seg,
                'candidate_id': cand.get('candidate_id', ''),
                'rank': cand.get('rank', ''),
                'protospacer': proto,
                'guide': guide,
                'min_tier': worst if worst is not None else '',
                'n_mm0': n_mm0, 'n_mm1': n_mm1,
                'negative_locus_pass': cand.get('all_negatives_locus_pass', ''),
                'note': note,
            })
    n_hit = sum(1 for r in rows if r['min_tier'] == 0)
    if n_hit:
        logger(f'交叉筛查：CONAN 候选 {len(rows)} 条中 {n_hit} 条近缘 mm0 命中'
               '（可能为同源位点，判读见 cross_screen.tsv note 列）')
    return rows, None


def _convergence(cr_result, cn_result, cr_summary, cn_summary, segs):
    """同位点收敛标记：两路线在同一共识坐标独立命中 -> 高置信。

    坐标口径（2026-11-06 修正）：
    - crispr 引擎 `site_start/site_end` 是共识**正链 0 基闭区间**
      （见 crispr_design.find_pam_sites 文档；落盘 ranked_sets.tsv 时 +1→1 基闭区间）。
    - conan 引擎 `cons_start/cons_end` 是共识**1 基闭区间**
      （conan_design span 为 0 基半开 [start,end)，落行时 start+1 / end）——
      与 candidates.tsv、报告图（axvspan(cons_start-1, cons_end)）同口径。
    两者不能直接相减：统一到 1 基闭区间后再求交并输出 crispr_site。
    缺坐标的候选一律跳过（不再用 `or 0` 兜成 0 号位点，避免假重叠）。

    前提：两路线共识长度一致（坐标可比）；不一致则该片段跳过并注记。
    返回 (rows, skipped)。
    """
    rows, skipped = [], []
    if not (cr_result and cn_result):
        return rows, skipped
    for seg in segs:
        cr_seg = (cr_summary.get('segments') or {}).get(seg) or {}
        cn_seg = next((x for x in (cn_summary.get('segments') or [])
                       if x.get('segment') == seg), {})
        L_cd = cr_seg.get('consensus_len')
        L_cn = cn_seg.get('consensus_len')
        if not (L_cd and L_cn and int(L_cd) == int(L_cn)):
            skipped.append(seg)
            continue
        for s in cr_result['sets']:
            if s['segment'] != seg:
                continue
            c = s['crrna']
            # 0 基闭区间 -> 1 基闭区间（与 conan 口径统一）
            a0 = int(c['site_start']) + 1
            a1 = int(c['site_end']) + 1
            for cand in (cn_result['segments'].get(seg) or {}).get(
                    'candidates') or []:
                if cand.get('cons_start') is None \
                        or cand.get('cons_end') is None:
                    continue
                try:
                    b0 = int(cand['cons_start'])
                    b1 = int(cand['cons_end'])
                except (TypeError, ValueError):
                    continue
                ov = max(0, min(a1, b1) - max(a0, b0) + 1)
                if ov > 0:
                    rows.append({
                        'segment': seg,
                        'crispr_spacer': c.get('spacer', ''),
                        'crispr_site': f'{a0}-{a1}',
                        'crispr_score': s.get('score'),
                        'conan_candidate_id': cand.get('candidate_id', ''),
                        'conan_rank': cand.get('rank', ''),
                        'conan_guide': cand.get('guide_spacer_5to3_RC', ''),
                        'overlap_bp': ov,
                    })
    return rows, skipped


# ---------------------------------------------------------------
# 场景建议表（只给建议文本，不自动重跑）
# ---------------------------------------------------------------
def _recommendations(cr_result, cn_result, cr_summary, cn_summary,
                     near_given, params):
    recs = []
    # 1) 种内通用筛查：crispr rank1
    r1 = (cr_result['sets'][0] if cr_result and cr_result['sets'] else None)
    if r1:
        c = r1['crrna']
        p = r1.get('pair') or {}
        recs.append({
            'scene': '种内通用筛查',
            'route': 'crispr',
            'pick': (f"{c.get('spacer', '')}（PAM {c.get('pam', '')}，"
                     f"位点 {c.get('site_start')}-{c.get('site_end')}）"),
            'with': (f"F {p.get('f_seq', '')} / R {p.get('r_seq', '')}"
                     f"（产物 {p.get('product', '?')}bp）"),
            'why': 'crRNA+引物成套试剂 + 群体覆盖因子，双库筛查后评分最高',
        })
    else:
        recs.append({
            'scene': '种内通用筛查',
            'route': '-',
            'pick': 'crispr 路线 0 组合',
            'with': '见报告排障节',
            'why': ('常见原因：混分子输入 / 宿主库 mm0 误杀（小分子病原可 '
                    'cr_use_host=0）/ strict 产物窗口 vs 小分子'
                    '（cr_profile=relaxed）'),
        })
    # 2) 近缘区分：CONAN 负组已应用且非空
    cn_has_cands = bool(cn_result and any(
        d.get('candidates') for d in cn_result['segments'].values()))
    neg_applied = bool(cn_summary and any(
        x.get('negative_applied') for x in (cn_summary.get('segments') or [])))
    if cn_has_cands and neg_applied:
        best = None
        for seg, d in sorted(cn_result['segments'].items()):
            for c in d.get('candidates') or []:
                sc = float(c.get('score') or 0)
                if best is None or sc > float(best.get('score') or 0):
                    best = c
        if best:
            recs.append({
                'scene': '近缘种/株系区分',
                'route': 'conan',
                'pick': (f"{best.get('guide_spacer_5to3_RC', '')}"
                         f"（{best.get('candidate_id', '')}，"
                         f"score {best.get('score')}）"),
                'with': 'I-E Cascade+Cas3 体系；DR 骨架订购前按系统 Protocol 补齐',
                'why': '负组同源位点双重失配判据直接建模近缘误检（leak=0）',
            })
    elif near_given and not cn_has_cands:
        recs.append({
            'scene': '近缘区分',
            'route': '-',
            'pick': 'CONAN 0 候选',
            'with': '放宽 cn_gc_min/cn_gc_max、cn_homopolymer 等口径单步重跑',
            'why': '负组已应用但候选被过滤链全灭，建议放宽 CONAN 过滤参数',
        })
    # 3) 现场读出
    probe_on = str(params.get('cr_probe_type') or 'none').strip().lower() \
        not in ('', 'none')
    if r1 and probe_on:
        recs.append({
            'scene': '现场侧流（LFD）',
            'route': 'crispr',
            'pick': 'rank1 组合（探针已设计）',
            'with': 'EXO=荧光 / NFO=侧流，NFO 配对生物素标记反向引物',
            'why': '探针序列见 crispr/ranked_sets.tsv probe 列',
        })
    elif r1:
        recs.append({
            'scene': '现场侧流（LFD）',
            'route': 'crispr',
            'pick': 'rank1 组合（本轮未设计探针）',
            'with': '需 LFD 时用 cr_probe_type=nfo 重跑',
            'why': 'EXO=荧光 / NFO=侧流试纸读出',
        })
    # 4) CONAN-SWIFT 论文路线注记
    if cn_has_cands:
        recs.append({
            'scene': '现场免仪器（CONAN-SWIFT 论文路线）',
            'route': 'conan',
            'pick': 'RT-LAMP + Cas3 反式切割 + 侧流层析',
            'with': 'RT-LAMP 兼容性/ATP/LFD 配对需实验建立（见验证清单 CONAN 段）',
            'why': 'SWIFT = Simple Workflow for Isothermal Field Testing',
        })
    return recs


# ---------------------------------------------------------------
# 统一产出
# ---------------------------------------------------------------
def _write_matrix_tsv(path, rows):
    cols = ['segment', 'crispr_combos', 'conan_sites', 'status']
    with safe_open(path, 'wt') as f:
        f.write('\t'.join(cols) + '\n')
        for r in rows:
            f.write('\t'.join(str(r.get(c, '')) for c in cols) + '\n')


def _write_cross_screen_tsv(path, rows):
    cols = ['segment', 'candidate_id', 'rank', 'protospacer', 'guide',
            'min_tier', 'n_mm0', 'n_mm1', 'negative_locus_pass', 'note']
    with safe_open(path, 'wt') as f:
        f.write('\t'.join(cols) + '\n')
        for r in rows:
            f.write('\t'.join(str(r.get(c, '')) for c in cols) + '\n')


_ORDER_HEADERS = {
    'crispr_crrna': ['rank', 'segment', 'spacer', 'PAM', 'site', 'score',
                     'flags', 'crRNA_spacer_DNA', 'crRNA_DR_spacer_DNA',
                     'T7_template_DNA', 'T7_note'],
    'crispr_primers': ['rank', 'segment', 'forward_primer', 'f_Tm', 'f_GC%',
                       'reverse_primer', 'r_Tm', 'r_GC%', 'product_bp',
                       'route', 'probe'],
    'conan_guides': ['rank', 'segment', 'candidate_id', 'strand', 'guide(5to3)',
                     'protospacer(5to3)', 'PAM', 'site', 'GC%',
                     'coverage%', 'leak%', 'score', 'ordering_note'],
}


def _order_rows(cr_result, cn_result):
    """三个区的数据行。返回 {区名: (rows)}，行内顺序与 _ORDER_HEADERS 对齐。"""
    crrna, primers, guides = [], [], []
    if cr_result:
        rank = 0
        for s in cr_result['sets']:
            rank += 1
            c = s.get('crrna') or {}
            p = s.get('pair') or {}
            of = s.get('order_formats') or {}
            crrna.append([
                rank, s.get('segment', ''), c.get('spacer', ''),
                c.get('pam', ''),
                f"{c.get('site_start', '')}-{c.get('site_end', '')}",
                s.get('score', ''), ';'.join(s.get('flags') or []),
                of.get('crRNA_spacer_DNA', ''),
                of.get('crRNA_DR_spacer_DNA', ''),
                of.get('T7_template_DNA', ''),
                of.get('T7_note', ''),
            ])
            probe_info = '-'
            if isinstance(s.get('probe'), dict) \
                    and 'skipped' not in (s.get('probe') or {}):
                probe_info = '已设计'
            primers.append([
                rank, s.get('segment', ''),
                p.get('f_seq', ''), p.get('f_tm', ''), p.get('f_gc', ''),
                p.get('r_seq', ''), p.get('r_tm', ''), p.get('r_gc', ''),
                p.get('product', ''), p.get('route', ''), probe_info,
            ])
    if cn_result:
        for seg, d in sorted(cn_result['segments'].items()):
            for c in d.get('candidates') or []:
                guides.append([
                    c.get('rank', ''), seg, c.get('candidate_id', ''),
                    c.get('strand', ''),
                    c.get('guide_spacer_5to3_RC', ''),
                    c.get('protospacer_5to3_after_PAM', ''),
                    c.get('PAM', ''),
                    f"{c.get('cons_start', '')}-{c.get('cons_end', '')}",
                    c.get('spacer_GC_percent', ''),
                    c.get('target_coverage_rate', ''),
                    c.get('negative_leak_rate', ''),
                    c.get('score', ''),
                    'DR 骨架不在计算输出范围：订购前按所用 Cascade/Cas3 系统 '
                    'Protocol 补齐 DR-spacer 完整序列',
                ])
    return {'crispr_crrna': crrna, 'crispr_primers': primers,
            'conan_guides': guides}


def _sheet_from_rows(ws, headers, rows):
    ws.append(list(headers))
    for r in rows:
        ws.append([v if v is not None else '' for v in r])


def _write_order_all(out_dir, cr_result, cn_result, files):
    """合并订购表。xlsx（三 sheet）优先；openpyxl 不可用回退 tsv。

    返回 (path, fmt)。
    """
    sections = _order_rows(cr_result, cn_result)
    xlsx_path = os.path.join(out_dir, 'order_all.xlsx')
    tsv_path = os.path.join(out_dir, 'order_all.tsv')
    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws1 = wb.active
        ws1.title = 'crispr_crrna'
        _sheet_from_rows(ws1, _ORDER_HEADERS['crispr_crrna'],
                         sections['crispr_crrna'])
        ws2 = wb.create_sheet('crispr_primers')
        _sheet_from_rows(ws2, _ORDER_HEADERS['crispr_primers'],
                         sections['crispr_primers'])
        ws3 = wb.create_sheet('conan_guides')
        _sheet_from_rows(ws3, _ORDER_HEADERS['conan_guides'],
                         sections['conan_guides'])
        wb.save(xlsx_path)
        files['order'] = xlsx_path
        return xlsx_path, 'xlsx'
    except Exception:
        with safe_open(tsv_path, 'wt') as f:
            for zone in ('crispr_crrna', 'crispr_primers', 'conan_guides'):
                f.write('route\t' + '\t'.join(_ORDER_HEADERS[zone]) + '\n')
                for r in sections[zone]:
                    f.write(zone.split('_')[0] + '\t'
                            + '\t'.join(str(v) for v in r) + '\n')
        files['order'] = tsv_path
        return tsv_path, 'tsv'


# ---------------------------------------------------------------
# 统一报告与验证清单
# ---------------------------------------------------------------
def _write_report_md(path, ctx):
    """统一中文报告。ctx 为 dict（避免长参数表）。"""
    target = ctx.get('target', '')
    routes = ctx.get('routes', 'both')
    failed = ctx.get('failed') or []
    warns = ctx.get('warns') or []
    matrix_rows = ctx.get('matrix') or []
    screen_rows = ctx.get('screen') or []
    conv_rows = ctx.get('conv') or []
    conv_skipped = ctx.get('conv_skipped') or []
    recs = ctx.get('recs') or []
    cr_s = ctx.get('cr_summary')
    cn_s = ctx.get('cn_summary')
    params = ctx.get('params') or {}
    elapsed = ctx.get('elapsed', 0)
    n_rec = ctx.get('n_records', 0)
    n_seg = ctx.get('n_segments', 0)

    lines = []
    lines.append(f'# cricon 双路线联合检测设计报告 — {target}')
    lines.append('')
    lines.append(f'- 时间：{time.strftime("%Y-%m-%d %H:%M:%S")}，'
                 f'耗时 {elapsed}s')
    lines.append(f'- routes：{routes}；失败路线：'
                 f'{", ".join(failed) if failed else "无"}')
    lines.append(f'- 输入：{n_rec} 条序列 / {n_seg} 个片段组')
    if warns:
        lines.append('')
        lines.append('## 预检警告')
        for w in warns:
            lines.append(f'- ⚠ {w}')
    if failed:
        lines.append('')
        lines.append('## 失败路线')
        for r in failed:
            lines.append(f'- {r}')
    lines.append('')
    lines.append('## 路线概览')
    lines.append('')
    lines.append('### crispr（Cas12a/Cas13a crRNA×引物协同）')
    lines.append('')
    if cr_s:
        segs = cr_s.get('segments') or {}
        n_total = sum(v.get('n_pam_sites', 0) for v in segs.values())
        n_kept = sum(v.get('n_after_funnel', 0) for v in segs.values())
        host = '开' if _flag(params.get('cr_use_host'), '1') else '关'
        lines.append('| 指标 | 值 |')
        lines.append('|---|---|')
        lines.append(f"| 组合数 | {cr_s.get('n_sets_output', 0)}"
                     f"（PAM 位点漏斗 {n_total} -> {n_kept}） |")
        lines.append(f"| 最优评分 | {cr_s.get('best_score')} |")
        lines.append(f"| 效应酶 | {cr_s.get('nuclease', '?')}"
                     f"（amp={cr_s.get('amp_mode', '?')}，"
                     f"profile={cr_s.get('profile', '?')}，"
                     f"宿主筛查={host}） |")
        lines.append('| 产物 | crispr/（report.html 交互报告 + '
                     'ranked_sets.tsv + 订购表） |')
    else:
        lines.append('| 指标 | 值 |')
        lines.append('|---|---|')
        lines.append('| - | 未运行或失败 |')
    lines.append('')
    lines.append('### conan（Cas3/I-E 负组双重失配）')
    lines.append('')
    if cn_s:
        segs = cn_s.get('segments') or []
        neg = any(x.get('negative_applied') for x in segs)
        top = cn_s.get('top') or []
        n_leak0 = sum(1 for x in top if not x.get('leak'))
        lines.append('| 指标 | 值 |')
        lines.append('|---|---|')
        lines.append(f"| 候选数 | {cn_s.get('n_candidates', 0)}；"
                     f"Top 中 leak=0 {n_leak0}/{len(top)} |")
        lines.append(f"| 负组 | {'已应用' if neg else '未应用'}"
                     f"（同源位点双重失配判据"
                     f"{'生效' if neg else '缺位'}） |")
        cn_pam = params.get('cn_pam') or 'AAG'
        cn_sp = params.get('cn_spacer_len') or 32
        lines.append(f"| 口径 | PAM {cn_pam} + spacer {cn_sp}nt，"
                     '种子 +6 忽略位（I-E 口径） |')
        lines.append('| 产物 | conan/（report.md + candidates.tsv + '
                     'target_checks.tsv + negative_checks.tsv） |')
    else:
        lines.append('| 指标 | 值 |')
        lines.append('|---|---|')
        lines.append('| - | 未运行或失败 |')
    lines.append('')
    lines.append('## 覆盖矩阵（片段 x 路线）')
    lines.append('')
    lines.append('| 片段 | crispr 组合 | conan 位点 | status |')
    lines.append('|---|---|---|---|')
    for r in matrix_rows:
        lines.append(f"| {r['segment']} | {r['crispr_combos']} | "
                     f"{r['conan_sites']} | {r['status']} |")
    if screen_rows:
        lines.append('')
        lines.append('## CONAN 候选近缘补筛查（k-mer，复用 crispr 排除库机制）')
        lines.append('')
        n_t0 = sum(1 for r in screen_rows if r.get('min_tier') == 0)
        lines.append(f'- 候选 {len(screen_rows)} 条；mm0 命中 {n_t0} 条'
                     '（tier0 命中可能即负组同源位点（PAM 已变），'
                     '逐条判读见 cross_screen.tsv）')
        lines.append('')
        lines.append('| 片段/候选 | min_tier | n_mm0 | negative_locus_pass |')
        lines.append('|---|---|---|---|')
        for r in screen_rows[:8]:
            lines.append(f"| {r.get('segment')}/{r.get('candidate_id')} | "
                         f"{r.get('min_tier', '')} | {r.get('n_mm0')} | "
                         f"{r.get('negative_locus_pass', '')} |")
        if len(screen_rows) > 8:
            lines.append(f'……其余 {len(screen_rows) - 8} 条见 cross_screen.tsv')
    if conv_rows:
        lines.append('')
        lines.append('## 同位点收敛（双路线独立命中同一共识区间 -> 高置信）')
        for r in conv_rows[:8]:
            lines.append(
                f"- {r['segment']}：crispr {r['crispr_spacer']} "
                f"@{r['crispr_site']} 与 conan {r['conan_candidate_id']} "
                f"@（{r['conan_rank']}）重叠 {r['overlap_bp']}bp")
        if len(conv_rows) > 8:
            lines.append(f'……其余 {len(conv_rows) - 8} 对见 summary.json')
    if conv_skipped:
        lines.append('')
        lines.append(f'- 坐标不可比跳过片段：{", ".join(conv_skipped)}'
                     '（两路线共识长度不一致）')
    lines.append('')
    lines.append('## 场景建议表（只给建议，不自动重跑）')
    lines.append('')
    lines.append('| 场景 | 路线 | 推荐组合/候选 | 配套 | 理由 |')
    lines.append('|---|---|---|---|---|')
    for r in recs:
        lines.append(f"| {r.get('scene', '')} | {r.get('route', '')} | "
                     f"{r.get('pick', '')} | {r.get('with', '')} | "
                     f"{r.get('why', '')} |")
    lines.append('')
    lines.append('## 0 组合排障（历史真实教训）')
    lines.append('- 混分子输入：不同分子混进一次 MAFFT -> 共识 N 列稀释 -> '
                 'PAM/引物扫描被跳过。对策：按分子分文件或 header 打片段标签'
                 '（RNA1/S2）。')
    lines.append('- 宿主库 mm0 误杀：大宿主库高灵敏筛查对类病毒等小分子病原'
                 '过严 -> cr_use_host=0，并在 L2 实验段补宿主背景验证。')
    lines.append('- strict 产物窗口 vs 小分子：432nt 级小分子上 strict 档'
                 '（100-200bp + 25bp 边距）引物难落位 -> cr_profile=relaxed'
                 '（90-300bp）。')
    lines.append('')
    lines.append('## 语义注记')
    lines.append('')
    lines.append('- ' + _CROSS_SEMANTICS_NOTE)
    lines.append('- CONAN 向导宿主筛查未做：CONAN 特异性判据是负组双重失配；'
                 '序列级筛查仅对近缘文件补位（若提供）。')
    lines.append('- 同位点收敛标记：两路线共识长度不一致时跳过（坐标不可比）。')
    lines.append('')
    lines.append('## 证据边界')
    lines.append('')
    lines.append('> ' + _EVIDENCE_NOTE)
    lines.append('')
    lines.append('## 引用')
    for c in _CITATIONS:
        lines.append(f'- {c}')
    body = '\n'.join(lines) + '\n'
    with safe_open(path, 'wt') as f:
        f.write(body)


def _write_checklist_md(path, target, routes, cr_best, cn_n_cands):
    """L1-L5 合并验证清单（crispr 模板结构 + CONAN 特有项 + 双路线互证）。"""
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    lines = []
    lines.append('# 联合检测设计验证清单（L1-L5）')
    lines.append('')
    lines.append(f'目标：{target}')
    lines.append(f'设计时间：{ts}')
    lines.append(f'routes：{routes}；crispr 最优评分：{cr_best}；'
                 f'conan 候选数：{cn_n_cands}')
    lines.append('')
    lines.append('> ' + _EVIDENCE_NOTE)
    lines.append('')
    lines.append('## L1 纯化靶标')
    lines.append('- [ ] 合成含 protospacer 的 dsDNA（或体外转录 RNA），'
                 '梯度稀释测 aLoD（cas12a/cas13a 路线）')
    lines.append('- [ ] time-to-positive：记录不同拷贝数下荧光/侧流出峰时间')
    lines.append('- [ ] crRNA 活性初筛：与 1-2 个备选 crRNA 并排比较')
    lines.append('- [ ] 容错 PAM（如有）：确认非 TTTV PAM 位点的实际活性')
    lines.append('- [ ] （CONAN）Cascade-crRNA 复合体结合 + Cas3 降解活性'
                 '（ATP 依赖）：阳性靶标出信号')
    lines.append('')
    lines.append('## L2 加标基质')
    lines.append('- [ ] 健康植株粗提液（纸片提取 / 碱裂解两种前处理各一版）'
                 '加标梯度')
    lines.append('- [ ] 测回收率与抑制情况（与纯化靶标 aLoD 对比）')
    lines.append('- [ ] 近缘交叉反应：近缘病毒核酸加标，验证不检出'
                 '（对应 conan 负组判据与 crispr 近缘库筛查）')
    lines.append('- [ ] 宿主核酸背景：健康植株提取液不检出'
                 '（对应 crispr 宿主筛查；CONAN 路线宿主筛查未做，必须在此补验）')
    lines.append('- [ ] （CONAN）RT-LAMP 产物与 Cascade/Cas3 反应兼容性'
                 '（稀释/纯化/直接加样三方案）')
    lines.append('')
    lines.append('## L3 回顾性田间样本')
    lines.append('- [ ] 用平台历史阳性样品（02b_kvsuite 定量结果召回清单）盲测')
    lines.append('- [ ] 以 RT-qPCR 为对照，算一致性（敏感度/特异度/kappa）')
    lines.append('- [ ] 低滴度样本单独统计（Ct>30 段）')
    lines.append('- [ ] （CONAN）真实近缘病毒样本（非仅序列）测试近缘不检出')
    lines.append('')
    lines.append('## L4 前瞻性田间试验')
    lines.append('- [ ] 多地点 / 多品种 / 不同生育期')
    lines.append('- [ ] 操作者为非开发人员；记录单样 <20min 出结果比例')
    lines.append('- [ ] 现场温度（非恒温实验室条件）下复测')
    lines.append('')
    lines.append('## L5 实际使用')
    lines.append('- [ ] 田间站 / 检测点实际人员操作')
    lines.append('- [ ] 储存稳定性：37 C 加速老化 + 常温运输模拟')
    lines.append('- [ ] 批次一致性（>=3 个独立合成批次）')
    lines.append('- [ ] 单样有效结果成本核算')
    lines.append('- [ ] 结果的处置决策记录（诊断结论如何指导防控）')
    lines.append('')
    lines.append('## 双路线互证（cricon 特有）')
    lines.append('- [ ] 同批样本两路线平行检测，统计一致性'
                 '（阳性符合率 / 阴性符合率 / kappa）')
    lines.append('- [ ] 收敛位点（见 summary.json convergence）优先入组：'
                 '两算法同位点独立命中为增强证据，非活性保证')
    lines.append('')
    lines.append('引用：Wang H. et al. ACS Sensors 2026（框架）；'
                 'Low S.J. et al. Commun Biol 2025 PathoGD（覆盖口径）；'
                 'CASPER（评分权重）；Yoshimi et al. 2022（CONAN Cas3）；'
                 'CONAN-SWIFT bioRxiv 2026.08.25.746999。')
    body = '\n'.join(lines) + '\n'
    with safe_open(path, 'wt') as f:
        f.write(body)


# ---------------------------------------------------------------
# 主入口（签名与两引擎一致）
# ---------------------------------------------------------------
def run_chain(targets, params, out_dir, log=None, prog=None, cancel=None):
    """cricon 联合编排入口。

    params 键：routes（both|crispr|conan）、near_fasta、group_by_segment、
    threads、以及 _CR_MAP/_CN_MAP 中的 cr_*/cn_* 键。引擎收过滤子 dict，
    未知键引擎静默忽略。
    返回 (summary, result)。
    """
    t0 = time.time()
    _log = _Log(log)

    routes = str(params.get('routes') or 'both').strip().lower()
    if routes not in ('both', 'crispr', 'conan'):
        routes = 'both'
    run_crispr = routes in ('both', 'crispr')
    run_conan = routes in ('both', 'conan')

    records = read_targets(targets)
    gflag = _flag(params.get('group_by_segment'), '1')
    groups = group_segments(records, gflag)
    seg_order = list(groups.keys())

    near = (params.get('near_fasta') or '').strip()

    warns = _preflight(records, groups, params, run_conan)
    for w in warns:
        _log('[WARN] ' + w, 'WARN')

    os.makedirs(os.path.join(out_dir, OUT_SUBDIR), exist_ok=True)
    out_root = os.path.join(out_dir, OUT_SUBDIR)
    work_dir = os.path.join(out_root, '_work')
    kmer_cache = os.path.join(work_dir, 'kmer_cache')
    os.makedirs(os.path.join(work_dir, 'kmer_cache'), exist_ok=True)

    cr_summary = cr_result = cn_summary = cn_result = None
    failed = []

    def _band_prog(lo, hi):
        def _p(stage, frac, msg):
            if prog:
                try:
                    f = lo + max(0.0, min(1.0, float(frac))) * (hi - lo)
                    prog(stage, f, f'[{TOOL_ID}] {stage}: {msg}')
                except Exception:
                    prog(stage, lo, msg)
        return _p

    # ---- 双引擎调度（单路线失败不拖垮整体）----
    if run_crispr:
        _log('路线 crispr：开始（Cas12a/Cas13a crRNA×引物协同设计）')
        cr_params = {'group_by_segment': params.get('group_by_segment', '1'),
                     'threads': params.get('threads') or 0,
                     'near_fasta': near}
        for k, v in _CR_MAP.items():
            raw = params.get(k)
            if raw is not None and str(raw).strip() != '':
                cr_params[v] = raw
        try:
            cr_summary, cr_result = _crispr_run_chain(
                targets, cr_params, out_dir, log=_log,
                prog=_band_prog(*_BAND_CRISPR), cancel=cancel)
            _log(f'路线 crispr：完成（组合 {cr_summary.get("n_sets_output", 0)}，'
                 f'最优评分 {cr_summary.get("best_score")}）')
        except (CrisprDesignError, RuntimeError, OSError) as e:
            if cancel and cancel.is_set():
                raise
            failed.append('crispr')
            _log('路线 crispr 失败：' + str(e), 'WARN')
    _check_cancel(cancel)

    if run_conan:
        _log('路线 conan：开始（Cas3/I-E 负组双重失配设计）')
        cn_params = {'negative': near,
                     'group_by_segment': params.get('group_by_segment', '1'),
                     'threads': params.get('threads') or 0}
        for k, v in _CN_MAP.items():
            raw = params.get(k)
            if raw is not None and str(raw).strip() != '':
                cn_params[v] = raw
        try:
            cn_summary, cn_result = _conan_run_chain(
                targets, cn_params, out_dir, log=_log,
                prog=_band_prog(*_BAND_CONAN), cancel=cancel)
            _log(f'路线 conan：完成（候选 {cn_summary.get("n_candidates", 0)}）')
        except (CrisprDesignError, RuntimeError, OSError) as e:
            if cancel is not None and cancel.is_set():
                raise
            failed.append('conan')
            _log('路线 conan 失败：' + str(e), 'WARN')
    _check_cancel(cancel)

    # ---- 交叉审核（AUDIT 进度带内细分）----
    def _audit_prog(frac, msg):
        if prog:
            lo, hi = _BAND_AUDIT
            f = lo + max(0.0, min(1.0, frac)) * (hi - lo)
            prog('交叉审核', f, msg)

    segs = list(groups.keys())
    _audit_prog(0.0, '覆盖矩阵')
    matrix_rows, segs = _coverage_matrix(cr_result, cn_result, segs)
    screen_rows = None
    screen_warn = None
    if run_conan and cn_result and near:
        _audit_prog(0.3, 'CONAN 向导补筛查')
        screen_rows, screen_warn = _cross_screen_conan(
            near, cn_result, kmer_cache, _log, cancel)
        if screen_warn:
            warns.append(screen_warn)
    _check_cancel(cancel)
    conv_rows, conv_skipped = [], []
    if cr_result and cn_result:
        _audit_prog(0.5, '同位点收敛标记')
        conv_rows, conv_skipped = _convergence(
            cr_result, cn_result, cr_summary, cn_summary, segs)
    _audit_prog(0.7, '场景建议表')
    recs = _recommendations(cr_result, cn_result, cr_summary, cn_summary,
                            bool(near), params)
    _check_cancel(cancel)

    # ---- 统一产出 ----
    _audit_prog(0.8, '统一产出')
    files = {}
    _write_matrix_tsv(os.path.join(out_root, 'coverage_matrix.tsv'),
                      matrix_rows)
    files['matrix'] = os.path.join(out_root, 'coverage_matrix.tsv')
    if screen_rows is not None:
        _write_cross_screen_tsv(os.path.join(out_root, 'cross_screen.tsv'),
                                screen_rows)
        files['cross_screen'] = os.path.join(out_root, 'cross_screen.tsv')
    order_path, order_fmt = _write_order_all(out_root, cr_result, cn_result,
                                             files)
    files['order'] = order_path
    _write_checklist_md(os.path.join(out_root, 'validation_checklist.md'),
                        os.path.basename(str(targets)), routes,
                        (cr_summary or {}).get('best_score'),
                        (cn_summary or {}).get('n_candidates', 0))
    files['checklist'] = os.path.join(out_root, 'validation_checklist.md')
    _write_report_md(os.path.join(out_root, 'report.md'), {
        'target': os.path.basename(str(targets)),
        'routes': routes, 'failed': failed, 'warns': warns,
        'matrix': matrix_rows, 'screen': screen_rows or [],
        'conv': conv_rows, 'conv_skipped': conv_skipped,
        'recs': recs,
        'cr_summary': cr_summary, 'cn_summary': cn_summary,
        'params': params,
        'elapsed': round(time.time() - t0, 1),
        'n_records': len(records), 'n_segments': len(groups),
    })
    files['report'] = os.path.join(out_root, 'report.md')

    # ---- 统一 summary ----
    def _crispr_mini(s):
        if not s:
            return None
        return {
            'n_sets_total': s.get('n_sets_total', 0),
            'n_sets_output': s.get('n_sets_output', 0),
            'best_score': s.get('best_score'),
            'nuclease': s.get('nuclease'),
            'amp_mode': s.get('amp_mode'),
            'profile': s.get('profile'),
        }

    def _conan_mini(s):
        if not s:
            return None
        return {
            'n_candidates': s.get('n_candidates', 0),
            'negative_applied': any(
                x.get('negative_applied')
                for x in (s.get('segments') or [])),
        }

    summary = {
        'tool': 'cricon', 'ok': True, 'engine_version': ENGINE_VERSION,
        'target': os.path.basename(str(targets)),
        'n_input_records': len(records), 'n_segments': len(groups),
        'routes': routes, 'failed_routes': failed,
        'warnings': warns,
        'crispr': _crispr_mini(cr_summary),
        'conan': _conan_mini(cn_summary),
        'convergence': conv_rows,
        'conv_skipped': conv_skipped,
        'out_dir': out_root,
        'elapsed_sec': round(time.time() - t0, 1),
        'evidence_note': _EVIDENCE_NOTE,
        'semantic_notes': [_CROSS_SEMANTICS_NOTE,
                           'CONAN 向导宿主筛查未做（判据是负组双重失配）；'
                           'tier0 补筛查命中可能即同源位点，非必然 veto'],
    }
    summary['files'] = {k: os.path.relpath(v, out_dir)
                        for k, v in files.items() if v}
    summary['files']['crispr_out'] = 'crispr'
    summary['files']['conan_out'] = 'conan'
    with safe_open(os.path.join(out_root, 'summary.json'), 'wt') as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    import shutil
    shutil.rmtree(work_dir, ignore_errors=True)
    _audit_prog(0.98, '完成')
    _log(f'cricon 联合设计完成：crispr 组合 '
         f'{(cr_summary or {}).get("n_sets_output", 0)} / '
         f'conan 候选 {(cn_summary or {}).get("n_candidates", 0)}'
         f'（耗时 {summary["elapsed_sec"]}s）')
    result = {
        'target_label': os.path.basename(str(targets)),
        'routes': routes, 'failed_routes': failed,
        'warnings': warns,
        'crispr_result': cr_result,
        'conan_result': cn_result,
        'matrix': matrix_rows, 'cross_screen': screen_rows,
        'convergence': conv_rows, 'conv_skipped': conv_skipped,
        'recommendations': recs,
        'summary': summary,
    }
    prog and prog('完成', 1.0, '完成')
    return summary, result
