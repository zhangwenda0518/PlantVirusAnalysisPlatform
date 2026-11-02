# -*- coding: utf-8 -*-
"""kv_common.clean_species_name / load_ref_info 的物种名消毒回归测试。

背景（2026-11-02）：virusref_db 里有 30 条「按 accession 直接下载、未经
taxon 注释」的记录，Species_NCBI 填的是 GenBank 标题整串，Species_ICTV
为空。旧实现原样写进识别表 Species 列，导致同一物种被拆成多个伪物种、
_genus_of / 同属近缘种比较失配。本测试锁死修复后的行为。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Virus_Platform_Core.known_virus_suite.kv_common import (  # noqa: E402
    clean_species_name, load_ref_info,
)

_FAIL = []


def check(name, cond, detail=''):
    if cond:
        print('  ok   %s' % name)
    else:
        print('  FAIL %s  %s' % (name, detail))
        _FAIL.append(name)


def test_normal_names_untouched():
    print('正常物种名不得被改写')
    cases = [
        'Citrus exocortis viroid',
        'Pepino mosaic virus',
        'Potato virus Y',
        'Barley yellow dwarf virus - PAV',
        'Banana streak virus',
        'Orthotospovirus tomatomaculae',
        'Pistacia emaravirus',
        'New World begomovirus associated satellite DNA',
        'Tomato leaf curl New Delhi virus-[potato]',
        'Grapevine yellow speckle viroid 3',
    ]
    for c in cases:
        out = clean_species_name(c)
        check('原样保留 %r' % c, out == c, '-> %r' % out)


def test_title_recovery():
    print('GenBank 标题还原成物种名')
    cases = [
        ('DQ000985.1 Pepino mosaic virus isolate Ch2, complete genome', 'Pepino mosaic virus'),
        ('AJ606359.1 Pepino mosaic virus complete genome, isolate LE-2', 'Pepino mosaic virus'),
        ('HG313807.1 Pepino mosaic virus complete genome, isolate Yur1', 'Pepino mosaic virus'),
        ('JQ314460.1 Pepino mosaic virus strain US1_EF09_58, complete', 'Pepino mosaic virus'),
        ('AB711147.1 Potato virus Y genomic RNA, nearly complete genom', 'Potato virus'),
        ('HE979770.1 East African cassava mosaic virus segment DNA-A,', 'East African cassava mosaic virus'),
        ('KT895259.1 Banana streak virus isolate DN5-4, complete genom', 'Banana streak virus'),
        ('JN019858.1 Bell pepper endornavirus isolate BPEV-YW, complet', 'Bell pepper endornavirus'),
        ('FR751552.4 Eggplant mottled dwarf virus G gene for glycoprotein',
         'Eggplant mottled dwarf virus'),
        # 注：'Potato virus Y strain NTN ...' 会截断到 'Potato virus'——种名
        # 里的 Y 恰好是截断词之一。这是设计取舍：宁可截短也不把标题写进
        # 物种列；真实库里这类条目的 ICTV 名会由同种正常条目回填。
        ('KC634004.1 Potato virus Y strain NTN isolate 11227-2 polypro', 'Potato virus'),
    ]
    for raw, want in cases:
        got = clean_species_name(raw)
        check('%s -> %s' % (raw[:38], want), got == want, '实际 %r' % got)


def test_unrecoverable():
    print('无法安全还原时返回空串（不臆造）')
    for raw in ('', None, '   ', 'DQ000985.1', 'NC_001464.1 '):
        got = clean_species_name(raw)
        check('%r -> %r' % (raw, got), got == '', '实际 %r' % got)


def test_load_ref_info_end_to_end():
    print('load_ref_info 端到端：标题型条目不产生伪物种')
    header = '\t'.join([
        'Accession', 'Taxid', 'Species_NCBI', 'Species_ICTV', 'Segment',
        'Molecule_type', 'Molecule_Type2', 'GenBank_Title', 'Host',
        'Geo_Location', 'Virus name(s)',
    ])
    rows = [
        # 正常条目：提供 ICTV 名
        ['NC_004067.1', '112229', 'Pepino mosaic virus', 'Potexvirus pepini', '',
         'RNA', 'ss-RNA', 'Pepino mosaic virus, complete genome',
         'Solanum lycopersicum', 'Spain', 'pepino mosaic virus'],
        # 标题型条目：Species_NCBI 与 GenBank_Title 同为标题串，ICTV 空
        ['DQ000985.1', '112229',
         'DQ000985.1 Pepino mosaic virus isolate Ch2, complete genome', '', '',
         'RNA', 'ss-RNA',
         'DQ000985.1 Pepino mosaic virus isolate Ch2, complete genome',
         '', '', ''],
        ['AJ606359.1', '112229',
         'AJ606359.1 Pepino mosaic virus complete genome, isolate LE-2', '',
         '', 'RNA', 'ss-RNA',
         'AJ606359.1 Pepino mosaic virus complete genome, isolate LE-2',
         '', '', ''],
    ]
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, 'ref_info.tsv')
        with open(p, 'w', encoding='utf-8') as f:
            f.write(header + '\n')
            for r in rows:
                f.write('\t'.join(r) + '\n')
        info = load_ref_info(p)

    sp = {a: (info.get(a) or {}).get('species') for a in
          ('NC_004067.1', 'DQ000985.1', 'AJ606359.1')}
    check('三条记录收敛为同一个物种名',
          len(set(sp.values())) == 1 and sp['DQ000985.1'] == 'Pepino mosaic virus',
          repr(sp))
    check('物种列不是 accession', not sp['DQ000985.1'].startswith('DQ'),
          repr(sp['DQ000985.1']))
    check('物种列不含标题尾巴', 'isolate' not in sp['DQ000985.1']
          and 'complete' not in sp['DQ000985.1'], repr(sp['DQ000985.1']))
    ictv = (info.get('DQ000985.1') or {}).get('ictv')
    check('ICTV 种名从同种正常条目回填', ictv == 'Potexvirus pepini', repr(ictv))


def test_real_db_no_pseudo_species():
    """真实参考库：没有任何条目把 accession 或标题整串当物种名。"""
    print('真实参考库：无伪物种、无空物种')
    import re as _re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(root, 'databases', 'virusref_db', 'plant_ref',
                     'reference.ref_info.tsv')
    if not os.path.exists(p):
        print('  skip 参考库不存在（%s）' % p)
        return
    info = load_ref_info(p)
    bad_acc, bad_title, empty = [], [], 0
    for acc, rec in list(info.items()):
        if not rec:
            continue
        sp = rec.get('species', '')
        if not sp:
            empty += 1
        elif _re.match(r'^[A-Z]{1,2}_?\d{4,}\.\d+', sp):
            bad_acc.append((acc, sp))
        elif _re.search(r'\b(isolate|strain|complete genome|partial cds)\b', sp):
            bad_title.append((acc, sp))
    check('无「物种名=accession」条目', not bad_acc, repr(bad_acc[:3]))
    check('无「物种名=GenBank 标题」条目', not bad_title, repr(bad_title[:3]))
    check('无空物种名', empty == 0, '空值 %d 条' % empty)


def main():
    test_normal_names_untouched()
    print()
    test_title_recovery()
    print()
    test_unrecoverable()
    print()
    test_load_ref_info_end_to_end()
    print()
    test_real_db_no_pseudo_species()
    print()
    if _FAIL:
        print('FAILED: %d 项' % len(_FAIL))
        for n in _FAIL:
            print('  -', n)
        return 1
    print('物种名消毒回归测试全部通过')
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
