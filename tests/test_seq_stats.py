# -*- coding: utf-8 -*-
"""seq_stats 回归：组装结果基本统计（seqkit stats 同列）。

覆盖：
- FASTA：num_seqs/sum_len/min/avg/max/Q1-Q3/sum_gap/N50/L50/GC/sum_n；
- FASTQ：Q20/Q30/AvgQual 与 GC；
- 空文件/单序列边界；
- .gz 输入；
- write_stats 落盘（TSV 列顺序 = seqkit stats -a；JSON 结构）。
"""
import gzip
import json
import os

import pytest

from Virus_Platform_Core import seq_stats as S


def _metrics(path):
    info = S.compute(path)
    return dict(zip(info['columns'], info['row']))


def test_fasta_metrics(tmp_path):
    p = tmp_path / 'a.fa'
    p.write_text('>a\nACGTNN--\n>b\nGGCC\n>c\nAT\n', encoding='utf-8')
    m = _metrics(p)
    assert m['format'] == 'FASTA'
    assert m['type'] == 'DNA'
    assert m['num_seqs'] == '3'
    assert m['sum_len'] == '14'          # 8 + 4 + 2，含 gap 与 N
    assert m['min_len'] == '2'
    assert m['max_len'] == '8'
    assert m['avg_len'] == '4.7'
    assert m['sum_gap'] == '2'           # -- 两个
    assert m['sum_n'] == '2'             # NN 两个
    assert m['N50'] == '8'               # 排序 8,4,2 → 累计 8 ≥ 7
    assert m['N50_num'] == '1'           # 标准 L50
    assert m['GC(%)'] == '42.86'         # (G+C)=6/14
    assert m['Q20(%)'] == '0' and m['AvgQual'] == '0.00'


def test_fastq_quality(tmp_path):
    p = tmp_path / 'a.fq'
    p.write_text('@r1\nACGT\n+\nIIII\n@r2\nACGTAC\n+\n!!!!!!\n', encoding='utf-8')
    m = _metrics(p)
    assert m['format'] == 'FASTQ'
    assert m['num_seqs'] == '2'
    assert m['sum_len'] == '10'
    # r1: 4×40；r2: 6×0 → Q20 = 4/10 = 40%，平均质量 (160+0)/10 = 16
    assert m['Q20(%)'] == '40'
    assert m['Q30(%)'] == '40'
    assert m['AvgQual'] == '16.00'
    assert m['GC(%)'] == '50.00'


def test_edge_single_and_empty(tmp_path):
    one = tmp_path / 'one.fa'
    one.write_text('>x\nACGT\n', encoding='utf-8')
    m = _metrics(one)
    assert m['Q1'] == m['Q2'] == m['Q3'] == '4'
    assert m['N50'] == '4' and m['N50_num'] == '1'
    empty = tmp_path / 'empty.fa'
    empty.write_text('', encoding='utf-8')
    m = _metrics(empty)
    assert m['num_seqs'] == '0' and m['sum_len'] == '0'
    assert m['N50'] == '0' and m['GC(%)'] == '0.00'


def test_gzip_input(tmp_path):
    p = tmp_path / 'a.fa.gz'
    with gzip.open(p, 'wt') as fh:
        fh.write('>a\nACGT\n')
    assert _metrics(p)['num_seqs'] == '1'


def test_write_stats_files(tmp_path):
    src = tmp_path / 'contigs.fa'
    src.write_text('>a\nACGTACGT\n>b\nGGCC\n', encoding='utf-8')
    out = S.write_stats(src, tmp_path / 'run', stem='contigs')
    assert os.path.isfile(out['tsv']) and os.path.isfile(out['json'])
    lines = open(out['tsv'], encoding='utf-8').read().splitlines()
    assert lines[0].split('\t') == S.COLUMNS      # 与 seqkit stats -a 同列同序
    row = dict(zip(lines[0].split('\t'), lines[1].split('\t')))
    assert row['num_seqs'] == '2' and row['N50'] == '8'
    js = json.load(open(out['json'], encoding='utf-8'))
    assert js['stats']['num_seqs'] == '2'
    # 任务卡 chips：数值已转回 int/float
    assert out['chips']['n_contigs'] == 2
    assert out['chips']['total_bp'] == 12
    assert out['chips']['N50'] == 8


def test_seqkit_row_shape_if_available():
    """seqkit 兜底路径的列形状（机器上没 seqkit 时跳过）。"""
    exe = S._seqkit_exe()
    if not exe:
        pytest.skip('seqkit 未安装')
    import subprocess
    import tempfile
    p = os.path.join(tempfile.mkdtemp(), 'a.fa')
    open(p, 'w', encoding='utf-8').write('>a\nACGT\n')
    try:
        info = S._seqkit_stats(p)
    finally:
        os.remove(p)
    assert info and info['source'] == 'seqkit'
    assert len(info['columns']) == len(S.COLUMNS)
    assert len(info['row']) == len(S.COLUMNS)
