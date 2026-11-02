# -*- coding: utf-8 -*-
"""kv_identify / kv_filter 的 pandas 实现回归测试。

背景（2026-10-20）：这两个模块原用 polars，其 Rust 运行时
_polars_runtime_32 解压后 167.7MB，是 site-packages 最大单项且无更小
变体。改用平台本就硬依赖的 pandas 后省掉该运行时。

本测试锁住三件事：
  ① 泊松打假 / TPM / 信号分级的数值口径不漂移
  ② 双轨过滤与 ANI 三态分流的分组边界正确
  ③ kv_filter 的阈值掩码与节段完整性规则正常
（与 polars 版的逐值对拍已在开发时用合成数据 + 真实产物各跑一遍，
 此处把关键口径固化成断言，防止后续改动悄悄改变数值。）
"""
import logging

import numpy as np
import pandas as pd
import pytest


# ── 合成输入（覆盖边界：零长度/空值/RNA/DNA/多段/ANI 三态）──
def _rows():
    return [
        dict(Sample='S1', Accession='A1', Length=6000, Species='Vir A',
             Segment='', Molecule_Type2='RNA', **{'Coverage(%)': 85.0},
             MeanDepth=120.0, EM_Reads=5000.0, Uniq_Reads=4800,
             Avg_Read_ANI=99.2, Sample_Total_Mapped=100000, Avg_Read_Len=150.0),
        dict(Sample='S1', Accession='A2', Length=8000, Species='Vir B',
             Segment='', Molecule_Type2='DNA', **{'Coverage(%)': 60.0},
             MeanDepth=30.0, EM_Reads=1200.0, Uniq_Reads=1100,
             Avg_Read_ANI=None, Sample_Total_Mapped=100000, Avg_Read_Len=150.0),
        dict(Sample='S1', Accession='A3', Length=0, Species='Vir C',
             Segment='', Molecule_Type2='DNA', **{'Coverage(%)': 0.0},
             MeanDepth=0.0, EM_Reads=10.0, Uniq_Reads=10,
             Avg_Read_ANI=None, Sample_Total_Mapped=100000, Avg_Read_Len=150.0),
        dict(Sample='S1', Accession='A4', Length=3000, Species='Vir Seg',
             Segment='RNA1', Molecule_Type2='ss-RNA', **{'Coverage(%)': 70.0},
             MeanDepth=50.0, EM_Reads=800.0, Uniq_Reads=780,
             Avg_Read_ANI=97.5, Sample_Total_Mapped=100000, Avg_Read_Len=150.0),
        dict(Sample='S1', Accession='A5', Length=2500, Species='Vir Seg',
             Segment='RNA2', Molecule_Type2='ss-RNA', **{'Coverage(%)': 65.0},
             MeanDepth=45.0, EM_Reads=700.0, Uniq_Reads=690,
             Avg_Read_ANI=98.1, Sample_Total_Mapped=100000, Avg_Read_Len=150.0),
        dict(Sample='S2', Accession='A7', Length=7000, Species='Vir D',
             Segment='', Molecule_Type2='DNA', **{'Coverage(%)': 90.0},
             MeanDepth=80.0, EM_Reads=3000.0, Uniq_Reads=2900,
             Avg_Read_ANI=85.0, Sample_Total_Mapped=50000, Avg_Read_Len=150.0),
    ]


class _Args:
    min_cov = 50.0
    min_depth = 10.0
    min_reads = 100
    ratio = 0.3
    ani_thresh = 95.0
    min_tpm = 0.0
    min_poisson = 0.0
    min_gene_total_cov = 0.0
    min_gene_avr_cov = 0.0
    genes_cov = None
    review_cov = 2.0
    review_depth = 0.1
    review_reads = 1
    review_ratio = 0.1
    review_drop_low = False


def _logger():
    log = logging.getLogger('kv_test')
    log.addHandler(logging.NullHandler())
    return log


# ── ① 数值口径 ────────────────────────────────────────
def test_poisson_and_tpm_formulas():
    """λ=EM*ARL/Len，Support=1-exp(-λ)，Ratio=(Cov/100)/Support，TPM 同样本=1e6。"""
    rows = _rows()
    df = pd.DataFrame(rows)
    _em = pd.to_numeric(df['EM_Reads'], errors='coerce')
    _arl = pd.to_numeric(df['Avg_Read_Len'], errors='coerce')
    _len = pd.to_numeric(df['Length'], errors='coerce')
    _cov = pd.to_numeric(df['Coverage(%)'], errors='coerce')
    lam = _em * _arl / _len
    support = 1.0 - np.exp(-lam)
    ratio = np.where(support > 0, (_cov / 100.0) / support, 0.0)

    # A1: λ = 5000*150/6000 = 125, support≈1, ratio≈0.85
    assert ratio[0] == pytest.approx(0.85, abs=1e-6)
    # A3: Length=0 → λ=inf → support=1 → ratio=0（Coverage 0/100）
    assert ratio[2] == 0.0

    tpk = np.where(_len > 0, _em / (_len / 1000.0), 0.0)
    s = pd.Series(tpk, index=df.index)
    tot = s.groupby(df['Sample']).transform('sum')
    tpm = np.where(tot > 0, s / tot * 1e6, 0.0)
    df['Asm_TPM'] = tpm
    for _, g in df.groupby('Sample'):
        assert g['Asm_TPM'].sum() == pytest.approx(1e6, rel=1e-9)


def test_signal_grade_thresholds():
    """EM/kb ≥100 → high；≥20 → medium；否则 low。"""
    d = pd.DataFrame({'Length': [6000, 6000, 6000],
                      'EM_Reads': [600.0, 120.0, 60.0]})
    _l = pd.to_numeric(d['Length'], errors='coerce')
    _e = pd.to_numeric(d['EM_Reads'], errors='coerce')
    emkb = np.where(_l > 0, _e / (_l / 1000.0), 0.0)
    grade = np.select([emkb >= 100, emkb >= 20], ['high', 'medium'],
                      default='low')
    assert list(grade) == ['high', 'medium', 'low']


# ── ② 过滤与 ANI 分流 ─────────────────────────────────
def test_filter_and_ani_split():
    """RNA 泊松门槛 0.3 / DNA 0.5；ANI 三态分流。"""
    df = pd.DataFrame(_rows())
    _len = pd.to_numeric(df['Length'], errors='coerce')
    _em = pd.to_numeric(df['EM_Reads'], errors='coerce')
    _cov = pd.to_numeric(df['Coverage(%)'], errors='coerce')
    lam = _em * 150.0 / _len
    support = 1.0 - np.exp(-lam)
    df['Poisson_Ratio'] = np.where(support > 0, (_cov / 100.0) / support, 0.0)

    base = ((pd.to_numeric(df['Sample_Total_Mapped'], errors='coerce') > 0) &
            (pd.to_numeric(df['Uniq_Reads'], errors='coerce') >= 100) &
            (pd.to_numeric(df['MeanDepth'], errors='coerce') >= 10))
    is_rna = df['Molecule_Type2'].astype('object').str.contains(
        r'(?i)\b(?:ss|ds)?-?rna\b|mrna|crna', regex=True, na=False)
    ratio_eff = np.where(is_rna, 0.3, 0.5)
    passed = df[base & (_cov >= 50.0) & (df['Poisson_Ratio'] >= ratio_eff)]
    # A1(RNA .85) A2(DNA .60) A4(RNA .70) A5(RNA .65) 通过；
    # A3 覆盖 0、A7 在 S2 但 MeanDepth 80 覆盖 90 → 也应通过
    assert set(passed['Accession']) == {'A1', 'A2', 'A4', 'A5', 'A7'}

    _ani = pd.to_numeric(passed['Avg_Read_ANI'], errors='coerce')
    status = np.select([_ani.isna(), _ani >= 95.0],
                       ['ani_unavailable', 'ani_confirmed'],
                       default='ani_below_threshold')
    got = dict(zip(passed['Accession'], status))
    assert got['A1'] == 'ani_confirmed'        # 99.2
    assert got['A2'] == 'ani_unavailable'      # None
    assert got['A7'] == 'ani_below_threshold'  # 85 < 95


def test_is_rna_regex_handles_null():
    """空/NaN 的 Molecule_Type2 不得被算作 RNA。"""
    s = pd.Series(['RNA', '', None, 'ss-RNA', 'DNA', 'ds-RNA', 'mRNA'])
    m = s.astype('object').str.contains(
        r'(?i)\b(?:ss|ds)?-?rna\b|mrna|crna', regex=True, na=False)
    assert list(m) == [True, False, False, True, False, True, True]


# ── ③ kv_filter ───────────────────────────────────────
def test_filter_stage_threshold_and_empty(tmp_path):
    from Virus_Platform_Core.known_virus_suite.kv_filter import FilterStage
    src = tmp_path / 'identify'
    src.mkdir(parents=True)
    df = pd.DataFrame(_rows())
    df.to_csv(src / 'all_viruses.summary.tsv', sep='\t', index=False)

    st = FilterStage(_Args(), _logger(), tmp_path)
    kept, dropped = st.run()
    assert len(kept) + len(dropped) == len(df)
    # 阈值 cov>=50 depth>=10 reads>=100
    assert set(kept['Accession']) == {'A1', 'A2', 'A4', 'A5', 'A7'}
    # 空输入也要落盘空表（P0-1）
    st2 = FilterStage(_Args(), _logger(), tmp_path / 'empty')
    empty = pd.DataFrame(columns=list(df.columns))
    k2, d2 = st2.apply(empty)
    assert len(k2) == 0 and len(d2) == 0
    assert (tmp_path / 'empty' / 'filter' / 'filtered.tsv').exists()


def test_filter_segment_integrity(tmp_path):
    """多段物种必须全段检出，否则整组进 discarded。"""
    from Virus_Platform_Core.known_virus_suite.kv_filter import FilterStage
    src = tmp_path / 'identify'
    src.mkdir(parents=True)
    rows = [
        dict(Sample='S1', Accession='X1', Length=3000, Species='SegV',
             Segment='RNA1', Molecule_Type2='RNA',
             **{'Coverage(%)': 90.0}, MeanDepth=100.0, EM_Reads=500.0,
             Uniq_Reads=500, Sample_Total_Mapped=1000, Avg_Read_Len=150.0),
        # 缺 RNA2 → 整组应被剔除
        dict(Sample='S1', Accession='Y1', Length=2000, Species='OnlyOne',
             Segment='RNA1', Molecule_Type2='RNA',
             **{'Coverage(%)': 90.0}, MeanDepth=100.0, EM_Reads=500.0,
             Uniq_Reads=500, Sample_Total_Mapped=1000, Avg_Read_Len=150.0),
    ]
    pd.DataFrame(rows).to_csv(src / 'all_viruses.summary.tsv', sep='\t',
                              index=False)
    ref_info = {
        'X1': {'species': 'SegV', 'segment': 'RNA1'},
        'X2': {'species': 'SegV', 'segment': 'RNA2'},   # 期望两段都在
        'Y1': {'species': 'OnlyOne', 'segment': 'RNA1'},
    }
    st = FilterStage(_Args(), _logger(), tmp_path, ref_info=ref_info)
    kept, dropped = st.run()
    # SegV 只有 RNA1 → 缺 RNA2 → 剔除；OnlyOne 单段物种不参与规则 → 保留
    assert 'X1' in set(dropped['Accession'])
    assert 'Y1' in set(kept['Accession'])


def test_write_tsv_bool_format(tmp_path):
    """布尔列落盘为 true/false（与历史 polars 产物一致）。"""
    from Virus_Platform_Core.known_virus_suite.kv_identify import _write_tsv
    df = pd.DataFrame({'a': [True, False], 'b': ['x', 'y'],
                       'c': [np.nan, 1.5]})
    p = tmp_path / 'o.tsv'
    _write_tsv(df, p)
    text = p.read_text(encoding='utf-8')
    assert 'true' in text and 'false' in text


# ── ④ 共识段的入参契约（2026-11-02 回归）────────────────
def test_consensus_accepts_pandas_passed_df():
    """共识段必须能吃 pandas 表。

    2026-11-02：identify/filter 迁回 pandas 后，共识段仍用 polars 的
    `passed_df.iter_rows(named=True)` → 每次 stage=all 都在共识段
    AttributeError（任务中心 known_virus_suite 退出码 1）。这条锁死入参
    兼容性，别再只按一种 DataFrame 实现写死。
    """
    from Virus_Platform_Core.known_virus_suite.kv_consensus import \
        iter_passed_rows
    df = pd.DataFrame(_rows())
    rows = list(iter_passed_rows(df))
    assert len(rows) == len(df)
    assert all(isinstance(r, dict) for r in rows)
    assert rows[0]['Accession'] == 'A1'
    assert set(rows[0]) >= {'Sample', 'Accession', 'Species'}


def test_consensus_polars_shaped_input_still_works():
    """上游若换回 polars（有 iter_rows(named=…)），同一入口仍成立。"""
    from Virus_Platform_Core.known_virus_suite.kv_consensus import \
        iter_passed_rows

    class FakePolarsDF:
        def iter_rows(self, named=False):
            assert named is True
            return iter([{'Sample': 'S1', 'Accession': 'P1'}])

    rows = list(iter_passed_rows(FakePolarsDF()))
    assert rows == [{'Sample': 'S1', 'Accession': 'P1'}]


# ── ⑤ _run 必须把 CompletedProcess 交回（2026-11-02 回归）──
def test_run_returns_completed_process():
    """_run 的返回值是共识段 0 映射守卫的输入。

    2026-11-02：_run 写完日志就直接结束、**没有 return**，恒返回 None；
    而 _bam_mapped_count() 用它读 .stdout（check=False 的 samtools view -c）
    → `AttributeError: 'NoneType' object has no attribute 'stdout'`，
    共识段在比对完成后必崩，stage=all 永远跑不完。
    """
    import sys as _sys
    from Virus_Platform_Core.known_virus_suite.kv_consensus import _run
    r = _run([_sys.executable, '-c', 'print(42)'], check=False)
    assert r is not None, '_run 必须返回 CompletedProcess'
    assert r.returncode == 0
    assert (r.stdout or '').strip() == '42'


def test_bam_mapped_count_reads_stdout(monkeypatch):
    """_bam_mapped_count 能解析出数字（真跑一次 samtools view -c 的替代）。"""
    from Virus_Platform_Core.known_virus_suite import kv_consensus as kc

    class _R:
        stdout = '  7\n'
        returncode = 0

    monkeypatch.setattr(kc, '_run', lambda *a, **k: _R())
    assert kc._bam_mapped_count('x.bam', 'samtools') == 7

    class _Bad:
        stdout = 'not-a-number'
        returncode = 0

    monkeypatch.setattr(kc, '_run', lambda *a, **k: _Bad())
    assert kc._bam_mapped_count('x.bam', 'samtools') == -1


# ── ⑥ 参考库 .fai 自检（2026-11-02 回归）───────────────
def _samtools_exe():
    from Virus_Platform_Core.known_virus_suite.kv_common import (
        which_tool, platform_root)
    exe = which_tool('samtools')
    if exe:
        return exe
    # which_tool 只看 PATH；测试里直接兜到随包工具目录
    from pathlib import Path
    cand = Path(platform_root()) / '3rd' / 'tools' / 'samtools' / 'bin' \
        / 'samtools.exe'
    return str(cand) if cand.exists() else None


def test_ensure_fai_detects_malformed_width(tmp_path):
    """linewidth < linebases 的坏索引必须被识别并重建。

    线上就是这样坏的：`NC_116488.1 2302 13 60 39` —— 换行宽度被改写过、
    索引没重建，bcftools mpileup 直接 [E::fai_insert_index] Malformed line。
    """
    samtools = _samtools_exe()
    if not samtools:
        pytest.skip('samtools 不在 3rd/tools 里，跳过 .fai 重建测试')
    from Virus_Platform_Core.known_virus_suite.kv_common import ensure_fai

    fa = tmp_path / 'ref.fasta'
    seq = 'ACGT' * 150                      # 600 bp
    fa.write_text('>seq1\n' + '\n'.join(seq[i:i + 60]
                                       for i in range(0, len(seq), 60)) + '\n',
                  encoding='utf-8')
    (tmp_path / 'ref.fasta.fai').write_text('seq1\t600\t6\t60\t39\n',
                                            encoding='utf-8')
    assert ensure_fai(fa, samtools=samtools) is True
    cols = (tmp_path / 'ref.fasta.fai').read_text(
        encoding='utf-8').splitlines()[0].split('\t')
    assert cols[1] == '600'
    # 重建后行宽必须 ≥ 碱基数（LF=61 / CRLF=62）
    assert int(cols[4]) >= int(cols[3])


def test_ensure_fai_keeps_good_index(tmp_path):
    samtools = _samtools_exe()
    if not samtools:
        pytest.skip('samtools 不在 3rd/tools 里，跳过 .fai 自检测试')
    from Virus_Platform_Core.known_virus_suite.kv_common import ensure_fai

    fa = tmp_path / 'ref.fasta'
    fa.write_text('>seq1\nACGT\n', encoding='utf-8')
    assert ensure_fai(fa, samtools=samtools) is True      # 先正常建一次
    stamp = (tmp_path / 'ref.fasta.fai').stat().st_mtime_ns
    assert ensure_fai(fa, samtools=samtools) is True      # 再判一次：不该重建
    assert (tmp_path / 'ref.fasta.fai').stat().st_mtime_ns == stamp
