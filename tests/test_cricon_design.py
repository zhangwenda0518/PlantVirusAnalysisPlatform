# -*- coding: utf-8 -*-
"""cricon（detect_kit 编排器）纯函数单测 + UI 契约 + 合成数据端到端。

合成数据带已知答案：
- 目标 3 株埋 engineered 位点：Cas12a TTTA+sp20 与 CONAN AAG+sp32 各一组
  （两引擎对同一输入独立设计）；近缘负组 2 条（crispr 排除库筛查 +
  conan 同源位点评分双喂）。
- 断言：双引擎产物目录并存、cricon/summary.json 契约、覆盖矩阵、
  单路线失败不拖垮整体（monkeypatch 注错 → failed_routes 记录）、
  near_fasta 双喂生效（conan/negative_checks.tsv 生成）。

MAFFT / primer3 缺失时端到端用例自动跳过；纯函数用例无外部依赖。
"""
import json
import os
import shutil
import uuid

import pytest

from Virus_Platform_Core import detect_kit as dk

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
TOOLS = os.path.join(_ROOT, 'webapp', 'templates', 'tools.html')
_PLAT_TEST_DIR = os.path.join(_ROOT, 'run', 'tests_cricon')


@pytest.fixture()
def plat_dir():
    d = os.path.join(_PLAT_TEST_DIR, uuid.uuid4().hex[:10])
    os.makedirs(d, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _mafft_ok():
    try:
        from Virus_Platform_Core.config import get_config
        return bool(os.path.isfile(str(get_config().tool('mafft'))))
    except Exception:
        return False


def _primer3_ok():
    try:
        import primer3  # noqa: F401
        return True
    except Exception:
        return False


# ---------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------
def test_param_maps():
    assert dk._CR_MAP['cr_top_n'] == 'top_n'
    assert dk._CN_MAP['cn_pam'] == 'pam'
    assert dk._CR_MAP.keys() & dk._CN_MAP.keys() == set()
    for v in dk._CR_MAP.values():
        assert not v.startswith('cr_')
    for v in dk._CN_MAP.values():
        assert not v.startswith('cn_')


def test_preflight_warnings():
    recs = [('a', 'ACGT' * 100)]
    groups = {'main': recs}
    warns = dk._preflight(recs, groups, {}, run_conan=True)
    assert any('1 条序列' in w for w in warns)
    assert any('负组' in w for w in warns)
    mixed = [(f's{i}', 'ACGT' * (10 * (i + 1))) for i in range(3)]
    w2 = dk._preflight(mixed, {'main': mixed}, {}, run_conan=True)
    assert any('混入不同分子' in w for w in w2)


def test_coverage_matrix():
    cr = {'sets': [{'segment': 'main'}]}
    cn = {'segments': {'main': {'candidates': [
        {'strand': '+', 'cons_start': 1}, {'strand': '+', 'cons_start': 5},
        {'strand': '-', 'cons_start': 1}]}}}
    rows, segs = dk._coverage_matrix(cr, cn, ['main'])
    assert rows[0]['crispr_combos'] == 1
    assert rows[0]['conan_sites'] == 3  # 唯一位点 = (strand, start) 去重
    assert rows[0]['status'] == '双路线覆盖'


def test_convergence_and_skip():
    """同位点收敛：crispr 0 基闭区间必须先 +1，再与 conan 的 1 基闭区间求交。

    坐标口径：crispr `site_start/site_end` 是共识 0 基闭区间
    （crispr_design.find_pam_sites）；conan `cons_start/cons_end` 是
    共识 1 基闭区间（conan_design 落行时 span[0]+1 / span[1]）。
    本例 crispr 129-152（0 基闭）→ 1 基 130-153，与 conan 130-165 重叠
    130..153 = 24bp。修复前两口径直接相减得 23（整体偏 1）。
    """
    cr = {'sets': [{'segment': 'main',
                    'crrna': {'site_start': 129, 'site_end': 152,
                              'spacer': 'A' * 20}, 'score': 70}]}
    cn = {'segments': {'main': {'candidates': [
        {'candidate_id': 'c1', 'cons_start': 130, 'cons_end': 165}]}}}
    cr_s = {'segments': {'main': {'consensus_len': 432}}}
    cn_s = {'segments': [{'segment': 'main', 'consensus_len': 432}]}
    conv, skipped = dk._convergence(cr, cn, cr_s, cn_s, ['main'])
    assert len(conv) == 1 and conv[0]['overlap_bp'] == 24
    assert conv[0]['crispr_site'] == '130-153'   # 输出统一为 1 基闭区间
    cr_s2 = {'segments': {'main': {'consensus_len': 400}}}
    conv2, skipped2 = dk._convergence(cr, cn, cr_s2, cn_s, ['main'])
    # 坐标不可比（两路线共识长度不一致）→ 该片段跳过
    assert skipped2 == ['main'] and conv2 == []


def test_convergence_skips_missing_coords():
    """缺坐标的候选必须显式跳过，不能用 `or 0` 兜成 0 号位点造假重叠。"""
    cr = {'sets': [{'segment': 'main',
                    'crrna': {'site_start': 0, 'site_end': 30,
                              'spacer': 'A' * 20}, 'score': 70}]}
    cn = {'segments': {'main': {'candidates': [
        {'candidate_id': 'missing', 'cons_start': None, 'cons_end': None},
        {'candidate_id': 'partial', 'cons_start': 5},
        {'candidate_id': 'ok', 'cons_start': 1, 'cons_end': 10}]}}}
    cr_s = {'segments': {'main': {'consensus_len': 432}}}
    cn_s = {'segments': [{'segment': 'main', 'consensus_len': 432}]}
    conv, _ = dk._convergence(cr, cn, cr_s, cn_s, ['main'])
    assert [r['conan_candidate_id'] for r in conv] == ['ok']
    assert conv[0]['overlap_bp'] == 10           # 1基 1..31 ∩ 1..10


def test_order_rows_and_headers():
    sections = dk._order_rows(
        {'sets': [{'segment': 'main', 'crrna': {'spacer': 'A' * 20,
                                                'pam': 'TTTA',
                                                'site_start': 1,
                                                'site_end': 20},
                   'pair': {'f_seq': 'F', 'r_seq': 'R'},
                   'score': 80, 'flags': []}]},
        {'segments': {'main': {'candidates': [
            {'candidate_id': 'c1', 'guide_spacer_5to3_RC': 'G' * 32}]}}})
    assert len(sections['crispr_crrna'][0]) == len(
        dk._ORDER_HEADERS['crispr_crrna'])
    assert len(sections['crispr_primers'][0]) == len(
        dk._ORDER_HEADERS['crispr_primers'])
    assert len(sections['conan_guides'][0]) == len(
        dk._ORDER_HEADERS['conan_guides'])
    # conan 区订购注记必须含 DR 骨架提示
    assert any('DR' in str(cell) for r in sections['conan_guides']
               for cell in r)


def test_ui_contract():
    """t-cricon 已于 2026-11-05 归档摘除：本断言防止复活残留。

    保留引擎纯函数与端到端测试（detect_kit.py 编排器代码仍在，可随时
    恢复）；UI/注册面应保持「已摘除」状态。
    """
    src = open(TOOLS, encoding='utf-8').read()
    for gone in ('id="t-cricon"', 'runCricon', 'loadCriconResult',
                 'id="toolrun-cricon"', 'id="criconResult"'):
        assert gone not in src, gone
    from Virus_Platform_Core.web.tools_api import TOOL_REGISTRY
    assert 'cricon' not in TOOL_REGISTRY
    from Virus_Platform_Core.web.pages import NAV_GROUPS
    ids = [it.get('id') for g in NAV_GROUPS if isinstance(g, dict)
           for it in g.get('items', []) if isinstance(it, dict)]
    assert 't-cricon' not in ids

# ---------------------------------------------------------------
# 合成数据端到端（MAFFT/primer3 缺失自动 skip）
# ---------------------------------------------------------------
_SYNTH = (
    # RNA1：3 株，埋 CONAN 位点（AAG + 32nt spacer）与 crispr 位点（TTTA+sp20）
    '>iso_A RNA1\n'
    + 'A' * 60 + 'AAG' + 'ACGTGCAATGCGTAAGCTTACGATCGTACGTA'   # AAG+32nt
    + 'TTTA' + 'ACGTTGCACTGGATCAAGTC'                          # TTTA+sp20
    + 'C' * 60 + '\n'
    + '>iso_B RNA1\n'
    + 'A' * 60 + 'AAG' + 'ACGTGCAATGCGTAAGCTTACGATCGTACGTA'
    + 'TTTA' + 'ACGTTGCACTGGATCAAGTC'
    + 'C' * 60 + '\n'
    + '>iso_C RNA1\n'
    + 'A' * 60 + 'AAG' + 'ACGTGCAATGCGTAAGCTTACGATCGTACGTA'
    + 'TTTA' + 'ACGTTGCACTGGATCAAGTC'
    + 'C' * 60 + '\n'
    # RNA2：单序列对照组
    + '>iso_A RNA2\n' + 'GGTT' * 40 + '\n'
)
_NEAR = (
    # 近缘负组：一条含同源 AAG+spacer（leak 案），一条 PAM 已变（干净案）
    '>near_1 RNA1\n'
    + 'A' * 60 + 'AAG' + 'ACGTGCAATGCGTAAGCTTACGATCGTACGTA'
    + 'TTTA' + 'ACGTTGCACTGGATCAAGTC'
    + 'C' * 60 + '\n'
    + '>near_2 RNA1\n'
    + 'A' * 60 + 'AGG' + 'ACGTGCAATGCGTAAGCTTACGATCGTACGTA'
    + 'TTTA' + 'ACGTTGCACTGGATCAAGTC'
    + 'C' * 60 + '\n'
)


def test_run_chain_synthetic(plat_dir):
    tgt = os.path.join(plat_dir, 'targets.fa')
    near = os.path.join(plat_dir, 'near.fa')
    with open(tgt, 'w', encoding='utf-8') as f:
        f.write(_SYNTH)
    with open(near, 'w', encoding='utf-8') as f:
        f.write(_NEAR)
    summary, result = dk.run_chain(
        tgt,
        {'routes': 'both', 'near_fasta': near,
         'cr_use_host': '0', 'cr_profile': 'relaxed',
         'cr_top_n': 5, 'cn_top_n': 5},
        plat_dir)
    assert summary['tool'] == 'cricon'
    assert summary['ok'] is True
    assert summary['routes'] == 'both'
    assert summary['failed_routes'] == []
    # 双引擎产物目录并存
    assert os.path.isdir(os.path.join(plat_dir, 'crispr'))
    assert os.path.isdir(os.path.join(plat_dir, 'conan'))
    assert os.path.isfile(os.path.join(plat_dir, 'cricon', 'summary.json'))
    assert os.path.isfile(os.path.join(plat_dir, 'cricon', 'report.md'))
    assert os.path.isfile(os.path.join(plat_dir, 'cricon',
                                       'coverage_matrix.tsv'))
    assert os.path.isfile(os.path.join(plat_dir, 'cricon',
                                       'validation_checklist.md'))
    # near 双喂：conan 负组核查表生成 = 负组进了合并 MSA
    assert os.path.isfile(os.path.join(plat_dir, 'conan',
                                       'negative_checks.tsv'))
    # 统一订购表 xlsx 或 tsv
    assert (os.path.isfile(os.path.join(plat_dir, 'cricon',
                                        'order_all.xlsx'))
            or os.path.isfile(os.path.join(plat_dir, 'cricon',
                                           'order_all.tsv')))
    # 覆盖矩阵含两片段
    mtx = open(os.path.join(plat_dir, 'cricon', 'coverage_matrix.tsv'),
               encoding='utf-8').read()
    assert 'segRNA1' in mtx and 'segRNA2' in mtx
    # result 契约
    assert result['routes'] == 'both'
    assert result['summary']['tool'] == 'cricon'


@pytest.mark.skipif(not _mafft_ok(), reason='MAFFT 不可用')
@pytest.mark.skipif(not _primer3_ok(), reason='primer3 不可用')
def test_single_route_failure_isolated(plat_dir):
    tgt = os.path.join(plat_dir, 'targets.fa')
    with open(tgt, 'w', encoding='utf-8') as f:
        f.write(_SYNTH)
    import Virus_Platform_Core.detect_kit as dk_mod
    orig = dk_mod._conan_run_chain

    def _boom(*a, **k):
        raise RuntimeError('合成故障：conan 引擎注错')

    try:
        dk_mod._conan_run_chain = _boom
        summary, result = dk.run_chain(
            tgt, {'routes': 'both', 'cr_use_host': '0',
                  'cr_profile': 'relaxed', 'cr_top_n': 3, 'cn_top_n': 3},
            plat_dir)
        assert summary['failed_routes'] == ['conan']
        assert summary['crispr'] is not None
        assert os.path.isdir(os.path.join(plat_dir, 'crispr'))
        # cricon 统一产物仍然落盘
        assert os.path.isfile(os.path.join(plat_dir, 'cricon',
                                           'summary.json'))
        assert result['summary']['ok'] is True
    finally:
        dk_mod._conan_run_chain = orig


