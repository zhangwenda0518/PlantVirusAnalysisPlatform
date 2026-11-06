# -*- coding: utf-8 -*-
"""模块结果页（module_page）与 report_kit 修复的回归测试。

覆盖：
- module_page 六区生成（图表/表格/数据/交互页/文件/输入）与结构标记；
- PNG/SVG 孪生切换、灯箱、__VP_BASE__ 上下文改写脚本；
- render.table 二维行修复（回归：此前字符串行被逐字符拆成单元格）；
- render.figure 的 SVG 内联分支（残缺 SVG 不内嵌）；
- link_evidence 的层级参数（样品报告 depth=1 / 工具运行报告 depth=0）；
- viz._save_fig PNG+SVG 双格式落盘；
- model 阶段映射回归（fq2fa 指纹/summary 此前错位）。

运行：
    python -m pytest tests/_check_module_report.py -q
    python tests/_run_all.py --quick
"""
import gzip
import json
import os

import pytest

from Virus_Platform_Core.report_kit import module_page as MP
from Virus_Platform_Core.report_kit import render

_SVG_OK = ('<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">'
           '<rect width="40" height="40" fill="#17513c"/></svg>')
_SVG_BAD = '<svg width="10"><rect width="10"'      # 残缺 SVG（无闭合）


def _mk_png(path):
    from PIL import Image
    Image.new('RGB', (8, 8), (23, 81, 60)).save(str(path))


@pytest.fixture
def sample_dir(tmp_path):
    """合成样品：00_prep（图/表/JSON/交互页/gz）+ 01_host_removal（表）。"""
    root = tmp_path / 'S1'
    prep = root / '00_prep'
    prep.mkdir(parents=True)
    _mk_png(prep / 'fastp_qc.png')
    (prep / 'fastp_qc.svg').write_text(_SVG_OK, encoding='utf-8')
    (prep / 'fastp_report.html').write_text(
        '<html><body>fastp native report</body></html>', encoding='utf-8')
    (prep / 'fastp.json').write_text(
        json.dumps({'before_filtering': {'total_reads': 100},
                    'after_filtering': {'total_reads': 90}}), encoding='utf-8')
    with gzip.open(prep / 'sub_R1.fastq.gz', 'wt') as fh:
        fh.write('@r1\nACGT\n+\nIIII\n')
    (root / '01_host_removal').mkdir()
    (root / '01_host_removal' / 'stats.json').write_text(
        json.dumps({'total_pairs': 10, 'kept_pairs': 2}), encoding='utf-8')
    (root / 'project.json').write_text(json.dumps({
        'project': 'S1',
        'input': {'r1': 'X:/raw/S1_R1.fq.gz', 'r2': 'X:/raw/S1_R2.fq.gz'},
        'created': '2026-11-04T10:00:00'}), encoding='utf-8')
    return root


@pytest.fixture
def run_dir(tmp_path):
    """合成工具运行目录（miRNA 形态：表 + 图 + 交互页 + 输入）。"""
    d = tmp_path / 'mirna_20261104_000000_abcdef'
    d.mkdir()
    _mk_png(d / 'mirna_targets_overview.png')
    (d / 'mirna_targets_overview.svg').write_text(_SVG_OK, encoding='utf-8')
    (d / 'results.tsv').write_text(
        'miRNA\ttarget\tscore\nmiR-1\tvirusX\t0.9\nmiR-2\tvirusX\t0.8\n',
        encoding='utf-8')
    (d / 'mirna_interactive.html').write_text(
        '<html><body>interactive</body></html>', encoding='utf-8')
    (d / 'input.fasta').write_text('>s1\nACGT\n', encoding='utf-8')
    (d / 'run.log').write_text('[params] seed=1\n', encoding='utf-8')
    return d


# ==================================================================
# module_page
# ==================================================================


def test_build_all_pages(sample_dir):
    p = MP.build_stage_page(str(sample_dir))
    assert p and os.path.isfile(p)
    for d in ('00_prep', '01_host_removal'):
        h = (sample_dir / d / 'results.html').read_text(encoding='utf-8')
        assert 'mp-lightbox' in h
        assert 'mp-preview' in h              # 文件链接统一页内预览弹层
        assert 'mp-pv-open' in h              # 预览内再给「新标签打开」
        assert '__VP_BASE__' in h
        assert 'data-rel' in h
        assert '01_host_removal' in h        # 跨模块导航 chips
    prep = (sample_dir / '00_prep' / 'results.html').read_text(encoding='utf-8')
    assert 'sec-figs' in prep                # 图表区
    assert '<img' in prep
    assert 'mp-toc' in prep                  # 页内目录栏
    assert 'data-sec=' in prep               # 目录项带锚点
    assert 'mp-toc-sub' in prep              # 跨模块导航并入目录栏


def test_svg_twin_toggle(sample_dir):
    MP.build_stage_page(str(sample_dir))
    h = (sample_dir / '00_prep' / 'results.html').text if hasattr(
        sample_dir, 'text') else (sample_dir / '00_prep' / 'results.html').read_text(
        encoding='utf-8')
    # PNG 默认 + SVG 切换按钮（同名孪生）
    assert 'data-src="fastp_qc.svg"' in h
    assert 'data-rel="fastp_qc.png"' in h


def test_bad_svg_not_inlined(sample_dir):
    (sample_dir / '01_host_removal' / 'broken.svg').write_text(
        _SVG_BAD, encoding='utf-8')
    MP.build_stage_page(str(sample_dir))
    h = (sample_dir / '01_host_removal' / 'results.html').read_text(
        encoding='utf-8')
    assert 'broken.svg' in h                  # 仍以链接形式列出（文件区）
    assert '<rect width="10"' not in h        # 残缺 SVG 不作为图内嵌


def test_tool_run_page(run_dir):
    p = MP.build_tool_run_page(str(run_dir))
    assert p and os.path.isfile(p)
    h = (run_dir / 'results.html').read_text(encoding='utf-8')
    assert 'miRNA 靶标预测' in h              # 工具中文名（TOOL_INFO）
    assert 'miRNA 靶标概览' in h              # figdocs 图鉴读法已挂上
    assert 'input.fasta' in h                 # 输入区
    assert 'run.log' in h
    assert 'data-rel="mirna_interactive.html"' in h   # 交互页本页预览（不再 target=_blank）
    assert h.count('target="_blank"') == 2            # 只剩预览/灯箱里的「新标签打开」


def test_tool_run_page_shows_sample_meta(run_dir):
    """目录批量的 run_meta.json：样本名进标题、原始 R1/R2 进输入区。"""
    (run_dir / 'run_meta.json').write_text(json.dumps({
        'tool': 'assemble', 'sample': 'Dataset_8',
        'r1': 'D:/raw/Dataset_8_R1.fastq.gz',
        'r2': 'D:/raw/Dataset_8_R2.fastq.gz'}), encoding='utf-8')
    p = MP.build_tool_run_page(str(run_dir))
    h = (run_dir / 'results.html').read_text(encoding='utf-8')
    assert 'Dataset_8' in h
    assert 'Dataset_8_R1.fastq.gz' in h
    assert 'Dataset_8_R2.fastq.gz' in h
    assert 'run_meta.json' not in h            # 内部元数据不进文件清单


def test_build_batch_html(tmp_path):
    """批量合并页：多样本汇总 + 关键数字 + 模块页/产物链接。"""
    entries = []
    for sm, n in (('Dataset_8', 11), ('Dataset_11', 7)):
        d = tmp_path / f'assemble_20261106_000000_{sm}'
        d.mkdir()
        (d / 'run_meta.json').write_text(
            json.dumps({'sample': sm, 'batch': 'b1'}), encoding='utf-8')
        (d / 'contigs.filtered.fasta').write_text(
            '>a' + chr(10) + 'ACGT' + chr(10), encoding='utf-8')
        (d / 'contigs_stats.json').write_text(json.dumps({'stats': {
            'num_seqs': str(n), 'sum_len': '1000', 'N50': '250',
            'GC(%)': '50.00'}}), encoding='utf-8')
        entries.append({'run': d.name, 'dir': str(d)})
    h = MP.build_batch_html(entries, title='批量测试')
    assert '样本汇总' in h
    assert 'Dataset_8' in h and 'Dataset_11' in h
    assert 'N50 250' in h
    assert '/api/module_report?run=' in h
    assert 'contigs.filtered.fasta' in h
    assert 'contigs_stats.json' in h or 'contigs_stats.tsv' in h


# ==================================================================
# render 修复回归
# ==================================================================


def test_table_string_row_defense():
    # 回归：此前字符串行被逐字符拆成单元格（GQMIX 实测 1292 个坏单元格）
    out = render.table(['列'], ['abc'])
    assert '<td>abc</td>' in out
    assert '<td>a</td>' not in out
    out2 = render.table(['a', 'b'], [['x', 'y']])
    assert '<td>x</td>' in out2 and '<td>y</td>' in out2


def test_figure_svg_branch(tmp_path):
    p = tmp_path / 'g.svg'
    p.write_text(_SVG_OK, encoding='utf-8')
    out = render.figure(str(p), caption='示意')
    assert '<svg' in out and 'fig-svg' in out
    bad = tmp_path / 'bad.svg'
    bad.write_text(_SVG_BAD, encoding='utf-8')
    assert render.figure(str(bad), caption='x') == ''   # 残缺 SVG 不内嵌


def test_link_evidence_depth():
    assert 'href="a.tsv"' in render.link_evidence('a.tsv', depth=0)
    assert 'href="../a.tsv"' in render.link_evidence('a.tsv', depth=1)
    assert 'href="../../a.tsv"' in render.link_evidence('a.tsv', depth=2)


def test_viz_dual_format(tmp_path, monkeypatch):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from Virus_Platform_Core import viz
    monkeypatch.setattr(viz, 'safe_open', open)   # tmp 路径不在平台内，绕过守卫
    png = tmp_path / 'fig.png'
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1])
    viz._save_fig(fig, str(png))
    plt.close('all')
    assert png.is_file()
    assert (tmp_path / 'fig.svg').is_file()   # SVG 孪生落盘


def test_model_stage_mapping_regression():
    from Virus_Platform_Core.report_kit.model import (STAGE_DIR,
                                                      STAGE_FINGERPRINT,
                                                      STAGE_SUMMARY)
    assert STAGE_DIR['subsample'] == '00_prep'
    assert STAGE_FINGERPRINT['fq2fa'] == ['conv_R1.fa.gz', 'fq2fa.json']
    assert STAGE_SUMMARY['fq2fa'] == 'fq2fa.json'


def test_logger_adapter_accepts_plain_function(tmp_path):
    # 回归：TaskManager/工具 job 传的是裸函数 log(msg)，此前直接传会
    # AttributeError: 'function' object has no attribute 'log'
    d = tmp_path / 'mirna_20261105_000000_abcdef'
    d.mkdir()
    (d / 'results.tsv').write_text('a\tb\n1\t2\n', encoding='utf-8')
    calls = []
    p = MP.build_tool_run_page(str(d), logger=lambda m: calls.append(m))
    assert p and os.path.isfile(p)
    assert isinstance(calls, list)          # 不抛异常即通过
