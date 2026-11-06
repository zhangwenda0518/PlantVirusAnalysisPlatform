# -*- coding: utf-8 -*-
"""比较基因组一键链（compare_suite / cmpchain）集成测试。

覆盖：
  1. 合成 GenBank 集合（3 条同源记录，固定随机种子）→ 能过 refs 阶段的
     ≥1 记录校验；
  2. run_pipeline(mode='collection') 全链：refs → cds → align → tree → sdt，
     断言返回 ok、run_summary.json 落盘且 ok=true、各阶段产物齐全；
  3. progress 契约：stage 只取 STAGE_ORDER 键、frac ∈ [0,1]、五阶段全覆盖；
  4. marker_group_of 纯函数边界（长别名优先 / nucleocapsid 不归 CP）；
  5. out_dir 向后兼容：extract_collection_features 不传 out_dir 时仍写集合
     extract/ 的 PhyloSuite 布局（旧调用方依赖的布局）。

沙箱：合成集合与一键链产物都建在 config.RUN_ROOT（跟随 VP_RUN_ROOT 隔离）
下的 _it_cmpsuite_tmp/，跑成功整体删除；失败保留产物便于排查。
不联网（collection 模式只读本地集合）。
用法：python tests/_it_cmpsuite.py
"""
import glob
import io
import json
import os
import random
import shutil
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('VP_NO_RECOVER', '1')

COLL = 'it_cmpsuite_coll'          # 合成集合名（白名单：字母/数字/_/-）
TMP = '_it_cmpsuite_tmp'           # 隔离区子目录（RUN_ROOT 下）
GEN_LEN = 1200                     # 合成基因组长度（小 → MAFFT/FastTree/SDT 秒级）
# (起点, 终点, 基因, product)——product 刻意命中保守基因别名表（RdRp/CP/MP）
CDS_LAYOUT = (
    (1, 400, 'ORF1', 'RNA-directed RNA polymerase'),
    (420, 800, 'ORF2', 'coat protein'),
    (820, 1180, 'ORF3', 'movement protein'),
)
ACCS = (('ITCMP_A', 'Synthetic test virus A'),
        ('ITCMP_B', 'Synthetic test virus B'),
        ('ITCMP_C', 'Synthetic test virus C'))

FAIL = []


def check(name, cond, extra=''):
    print(('  ✔ ' if cond else '  ✘ ') + name + (f'  {extra}' if extra else ''))
    if not cond:
        FAIL.append(name)


# ------------------------------------------------------------------ 合成记录

def _rand_seq(n, rng):
    """确定性随机核酸（GC≈55%，避免全 N/低复杂度让比对退化）。"""
    out = []
    for _ in range(n):
        r = rng.random()
        out.append('G' if r < 0.275 else 'C' if r < 0.55 else
                   'A' if r < 0.775 else 'T')
    return ''.join(out)


def _mutate(seq, rate, rng):
    """逐位以 rate 概率替换（与 make_example_synteny_gb 同口径）。"""
    return ''.join(rng.choice('ACGT') if rng.random() < rate else ch
                   for ch in seq)


def _rand_aa(n, rng):
    """确定性蛋白串（首 M；只用于 /translation，不需要与核酸严格对应）。"""
    return 'M' + ''.join(rng.choice('ACDEFGHIKLMNPQRSTVWY')
                         for _ in range(max(n - 1, 0)))


def _origin(seq):
    """GenBank ORIGIN：每组 10 bp、每行 60 bp、行首坐标。"""
    lines = ['ORIGIN']
    for start in range(0, len(seq), 60):
        chunk = seq[start:start + 60]
        groups = ' '.join(chunk[i:i + 10] for i in range(0, len(chunk), 10))
        lines.append(f'{start + 1:9d} {groups}')
    lines.append('//')
    return '\n'.join(lines)


def _wrap(text, width=60):
    return [text[i:i + width] for i in range(0, len(text), width)] or ['']


def _locus(acc, n):
    """定宽旧版 LOCUS 行（Biopython 按列解析：12:29 名字+长度、29:33 ' bp '、
    33:41 分子类型、42:51 拓扑、52:55 division、62:73 日期）——按空格分隔
    的写法会触发 BiopythonParserWarning，这里用标准列格式避开。"""
    ns = str(n)
    seg = (acc + ' ' * 17)[:17 - len(ns)] + ns
    return ('LOCUS       ' + seg + ' bp ' + 'DNA'.ljust(8) + ' '
            + 'linear'.ljust(9) + ' ' + 'VRL' + '       ' + '01-JAN-2026')


def _gb_text(acc, organism, seq, cds_rows):
    """单条 GenBank 平文（LOCUS/ACCESSION/FEATURES/ORIGIN，Bio 可解析）。"""
    lines = [
        _locus(acc, len(seq)),
        f'DEFINITION  {organism}, complete genome (synthetic).',
        f'ACCESSION   {acc}',
        f'VERSION     {acc}',
        'KEYWORDS    .',
        'SOURCE      .',
        f'  ORGANISM  {organism}',
        '            .',
        'FEATURES             Location/Qualifiers',
        f'     source          1..{len(seq)}',
        f'                     /organism="{organism}"',
    ]
    for start, end, gene, product, aa in cds_rows:
        parts = _wrap(aa)
        tr = [f'                     /translation="{parts[0]}']
        tr += [f'                     {p}' for p in parts[1:-1]]
        if len(parts) > 1:
            tr.append(f'                     {parts[-1]}"')
        else:
            tr[0] = tr[0] + '"'
        lines += [f'     CDS             {start}..{end}',
                  f'                     /product="{product}"',
                  f'                     /gene="{gene}"'] + tr
    lines.append(_origin(seq))
    return '\n'.join(lines) + '\n'


def _write_fixture(cdir):
    """3 条同源记录 → 集合目录（A=母本，B 2% 突变，C 6% 突变）。"""
    rng = random.Random(20261104)
    base = _rand_seq(GEN_LEN, rng)
    genomes = {'A': base,
               'B': _mutate(base, 0.02, random.Random(11)),
               'C': _mutate(base, 0.06, random.Random(12))}
    for (acc, organism), tag in zip(ACCS, 'ABC'):
        rr = random.Random(1000 + ord(tag))
        cds_rows = [(s, e, gene, product,
                     _rand_aa((e - s + 1) // 3 - 1, rr))
                    for s, e, gene, product in CDS_LAYOUT]
        path = os.path.join(cdir, f'{acc}.gb')
        with io.open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(_gb_text(acc, organism, genomes[tag], cds_rows))
    # 集合清单（list_gb_collections 会读；refs 阶段只数 .gb 文件）
    with io.open(os.path.join(cdir, 'query.json'), 'w', encoding='utf-8',
                 newline='\n') as f:
        json.dump({'source': 'synthetic',
                   'note': 'tests/_it_cmpsuite.py 合成集合（确定性，不联网）',
                   'date': '2026-11-04'}, f, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------ 各项断言

def _check_run_summary(out):
    print('[2] run_summary.json 与阶段返回', flush=True)
    rp = os.path.join(out, 'run_summary.json')
    check('run_summary.json 落盘', os.path.isfile(rp), rp)
    if not os.path.isfile(rp):
        return {}
    with io.open(rp, encoding='utf-8') as f:
        summary = json.load(f)
    check('run_summary.ok == true', summary.get('ok') is True,
          str(summary.get('error'))[:300])
    stages = summary.get('stages') or {}
    check('五个阶段都有记录', set(stages) == {'refs', 'cds', 'align', 'tree',
                                              'sdt'}, str(sorted(stages)))
    bad = [k for k, v in stages.items() if not (v or {}).get('ok')]
    check('每个阶段 ok=true', not bad, str(bad))
    check('config.resolved.json 落盘',
          os.path.isfile(os.path.join(out, 'config.resolved.json')))
    return stages


def _check_artifacts(out):
    print('[4] 产物存在性', flush=True)
    files = [
        ('00_refs/refs.fa', '参考集 FASTA'),
        ('00_refs/provenance.json', '参考集 provenance'),
        ('01_extract/genome.fa', '全基因组 FASTA'),
        ('01_extract/CDS.fa', '全 CDS FASTA'),
        ('01_extract/marker/marker_report.tsv', '保守基因报表'),
        ('02_align/genome/aln.trim.fasta', '基因组 MSA（trimAl 清剪）'),
        ('04_sdt/sdt_matrix.csv', 'SDT 精确矩阵'),
    ]
    for rel, label in files:
        p = os.path.join(out, *rel.split('/'))
        ok = os.path.isfile(p) and os.path.getsize(p) > 0
        check(f'{rel}（{label}）', ok,
              '' if ok else '缺失或空文件')
    check('01_extract/marker/ 目录存在',
          os.path.isdir(os.path.join(out, '01_extract', 'marker')))
    nwk = glob.glob(os.path.join(out, '03_tree', 'genome', '*.nwk'))
    check('03_tree/genome/ 下有 .nwk', bool(nwk), str([os.path.basename(p)
                                                       for p in nwk]))

    def _n_fa(p):
        with io.open(p, encoding='utf-8') as f:
            return sum(1 for ln in f if ln.startswith('>'))

    gf = os.path.join(out, '01_extract', 'genome.fa')
    if os.path.isfile(gf):
        check('genome.fa 含 3 条记录', _n_fa(gf) == 3, f'{_n_fa(gf)} 条')
    rf = os.path.join(out, '00_refs', 'refs.fa')
    if os.path.isfile(rf):
        check('refs.fa 含 3 条参考', _n_fa(rf) == 3, f'{_n_fa(rf)} 条')

    rep = os.path.join(out, '01_extract', 'marker', 'marker_report.tsv')
    if os.path.isfile(rep):
        with io.open(rep, encoding='utf-8') as f:
            rows = [ln.rstrip('\n') for ln in f if ln.strip()]
        groups = {ln.split('\t')[0] for ln in rows[1:]}
        check('marker_report.tsv 分组含 RdRp/CP/MP',
              {'RdRp', 'CP', 'MP'} <= groups, str(sorted(groups)))

    csv = os.path.join(out, '04_sdt', 'sdt_matrix.csv')
    if os.path.isfile(csv):
        with io.open(csv, encoding='utf-8') as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        # 表头 + n 行；3 条序列 → 4 行
        check('sdt_matrix.csv 为 3×3 矩阵（4 行）', len(lines) == 4,
              f'{len(lines)} 行')


def _check_marker_group_of():
    print('[5] marker_group_of 纯函数（长别名优先 / boundary 规则）', flush=True)
    from Virus_Platform_Core.gb_collection import marker_group_of
    cases = [
        ('coat protein', 'CP'),
        ('RNA-directed RNA polymerase', 'RdRp'),
        ('movement protein', 'MP'),
        # 长别名优先：helper component(16) 先于 protease(8) 命中
        ('helper component-protease', 'HC-Pro'),
        # boundary：polyprotein 不命中 polymerase/protease 等子串
        ('polyprotein', None),
        # boundary：nucleocapsid 的前邻 'o' 让 capsid protein 不命中
        ('nucleocapsid protein', None),
    ]
    for text, want in cases:
        got = marker_group_of(text)
        check(f'{text!r} → {want!r}', got == want, f'实际 {got!r}')


def main():
    from Virus_Platform_Core import compare_suite
    from Virus_Platform_Core.config import RUN_ROOT
    from Virus_Platform_Core.gb_collection import (extract_collection_features,
                                                   extract_dir,
                                                   gb_collection_dir)

    base = os.path.join(RUN_ROOT, TMP)
    out = os.path.join(base, 'cmp_out')
    cdir = gb_collection_dir(COLL)
    clean_ok = False
    try:
        shutil.rmtree(cdir, ignore_errors=True)
        shutil.rmtree(base, ignore_errors=True)
        os.makedirs(cdir, exist_ok=True)
        _write_fixture(cdir)

        print('[1] collection 模式跑比较基因组一键链', flush=True)
        from Bio import SeqIO
        n_gb = len([fn for fn in os.listdir(cdir)
                    if fn.lower().endswith('.gb')])
        n_rec = sum(1 for fn in sorted(os.listdir(cdir))
                    if fn.lower().endswith('.gb')
                    for _ in SeqIO.parse(os.path.join(cdir, fn), 'genbank'))
        check('合成集合 3 个 .gb / 3 条记录（过 ≥1 记录校验）',
              n_gb == 3 and n_rec == 3, f'{n_gb} 文件 / {n_rec} 记录')
        check('合成集合落在隔离运行根下',
              os.path.abspath(cdir).startswith(
                  os.path.abspath(RUN_ROOT) + os.sep), cdir)

        prog = []
        res = compare_suite.run_pipeline(
            out_dir=out, mode='collection', coll=COLL, tree_tool='fasttree',
            log=print, progress=lambda st, fr, msg: prog.append((st, fr, msg)))
        check('run_pipeline 返回 ok=True', res.get('ok') is True,
              str(res.get('error'))[:300])
        check('返回 out_dir 指向临时目录',
              os.path.abspath(res.get('out_dir') or '') == os.path.abspath(out))

        stages = _check_run_summary(out)
        refs = (stages.get('refs') or {}).get('outputs') or {}
        check("refs 阶段 mode=collection 且 n_records=3",
              refs.get('mode') == 'collection' and refs.get('n_records') == 3,
              str(refs))
        cds = (stages.get('cds') or {}).get('outputs') or {}
        check('cds 阶段 3 基因组 / 9 CDS / 9 PEP',
              (cds.get('n_genome'), cds.get('n_cds'), cds.get('n_pep'))
              == (3, 9, 9), f"{cds.get('n_genome')}/{cds.get('n_cds')}/"
                            f"{cds.get('n_pep')}")

        print('[3] progress 契约', flush=True)
        order = set(compare_suite.STAGE_ORDER)
        check('progress 有事件', len(prog) >= len(order), f'{len(prog)} 条')
        labels = {st for st, _fr, _msg in prog}
        check('progress.stage 全部 ∈ STAGE_ORDER', labels <= order,
              str(sorted(labels - order)))
        check('progress 覆盖五个阶段', labels == order, str(sorted(order - labels)))
        bad_frac = [fr for _st, fr, _msg in prog
                    if not isinstance(fr, (int, float)) or not 0.0 <= fr <= 1.0]
        check('progress.frac 全部 ∈ [0,1]', not bad_frac, str(bad_frac[:5]))

        _check_artifacts(out)
        _check_marker_group_of()

        print('[6] out_dir 向后兼容（旧调用方布局）', flush=True)
        r2 = extract_collection_features(COLL)      # 不传 out_dir
        exd = extract_dir(COLL)
        check('extract_collection_features 返回 n_genome=3',
              (r2 or {}).get('n_genome') == 3, str(r2))
        for rel in ('genome.fa', 'CDS.fa', 'PEP.fa'):
            p = os.path.join(exd, rel)
            ok = os.path.isfile(p) and os.path.getsize(p) > 0
            check(f'集合 extract/{rel} 仍生成（缺省布局）', ok)
        check('缺省布局不落进一键链 out_dir',
              not os.path.isdir(os.path.join(out, 'extract')),
              os.path.join(out, 'extract'))

        clean_ok = not FAIL
    finally:
        shutil.rmtree(cdir, ignore_errors=True)
        if clean_ok:
            shutil.rmtree(base, ignore_errors=True)
        else:
            print(f'  （失败产物保留用于排查：{base}）', flush=True)

    if FAIL:
        print(f'CMPCHAIN INTEGRATION TESTS FAILED（{len(FAIL)} 项）：'
              + '；'.join(FAIL), flush=True)
        return 1
    print('CMPCHAIN INTEGRATION TESTS PASSED', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
