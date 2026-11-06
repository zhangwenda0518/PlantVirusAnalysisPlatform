# -*- coding: utf-8 -*-
"""回归：在线获取（替代 SeqHarvester 的便利层）与**输出 schema 一致性**。

覆盖
----
[1] `build_query` 的检索式口径（taxid 优先 / 排除专利 / 全长过滤 / 日期范围 / term 原样）
[2] **离线**：用本地 GBK fixture 走数据接入 → 交付的 metadata 必须**与平台标准一致**
    （前五列固定 `name,date,location,lat,lon`；GenBank 带 host 时才追加 `host`），
    且 `geo_loc_name` 要**优先于** `country`、**不截断**（`Japan:Chiba` → `Japan, Chiba`）
[3] **在线**（无网自动 SKIP）：物种名 → taxid；小规模一键采集 → 输出仍符合上述 schema
[4] 负向：`geo_loc_name` 被漏读会让「有地点」的记录被误判（钉住这个缺口不再回来）

用法: python tests/_check_online_meta.py
"""
import csv
import io
import json
import os
import socket
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import app as appmod                                            # noqa: E402
from Virus_Platform_Core import gb_collection as gbc            # noqa: E402
from Virus_Platform_Core import phylodyn_kit as pk              # noqa: E402

WORK = os.path.join(ROOT, 'run', '_check_online_meta')
PASS, FAIL, SKIP = [], [], []


def chk(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  ok   ' if cond else '  FAIL ') + msg, flush=True)


def online() -> bool:
    try:
        socket.create_connection(('eutils.ncbi.nlm.nih.gov', 443), timeout=8).close()
        return True
    except OSError:
        return False


GBK = """LOCUS       TEST0001                 359 bp    RNA     linear   VRD 18-SEP-2026
DEFINITION  Potato spindle tuber viroid isolate test, complete genome.
ACCESSION   TEST0001
VERSION     TEST0001.1
FEATURES             Location/Qualifiers
     source          1..359
                     /organism="Potato spindle tuber viroid"
                     /geo_loc_name="Japan:Chiba"
                     /host="Dahlia sp."
                     /collection_date="2011"
                     /db_xref="taxon:12892"
ORIGIN
        1 cggaactaaa ctcgtggttc ctgtggttca cacctgacct cctgagcaga aaagaaaaag
       61 aaggcggctc ggaggagcgc ttcagggatc cccggggaaa cctggagcga actggcaaaa
      121 aaggacggtg ggggtgccca gcggccgaca ggagtaattc cgccgaaaac aggttttcca
      181 ccgggtagtg cgaaacccgc aggggttccc ggagagcttt catcgctctc acgaaacgcg
      241 tgggaagaga ttgttttccg tcggcccgtg gactctggct ccacatcgac cgaccgatca
      241 aagtcctcct gtggttcaca cctgacctcc tgagcagaaa agaaaaagaa ggcggctcgg
      301 aggagcgctt cagggatccc cggggaaacc tggagcgaac tggcaaaaaa ggacggtgg
//
LOCUS       TEST0002                 359 bp    RNA     linear   VRD 18-SEP-2026
DEFINITION  Potato spindle tuber viroid isolate old-style, complete genome.
ACCESSION   TEST0002
VERSION     TEST0002.1
FEATURES             Location/Qualifiers
     source          1..359
                     /organism="Potato spindle tuber viroid"
                     /country="China"
                     /collection_date="2015-06-01"
                     /db_xref="taxon:12892"
ORIGIN
        1 cggaactaaa ctcgtggttc ctgtggttca cacctgacct cctgagcaga aaagaaaaag
       61 aaggcggctc ggaggagcgc ttcagggatc cccggggaaa cctggagcga actggcaaaa
      121 aaggacggtg ggggtgccca gcggccgaca ggagtaattc cgggtagtgc gaaacccgca
      181 ggggttcccg gagagctttc atcgctctca cgaaacgcgt gggaagagat tgttttccgt
      241 cggcccgtgg actctggctc cacatcgacc gaccgatcaa agtcctcctg tggttcacac
      301 ctgacctcct gagcagaaaa gaaaaagaag gcggctcgga ggagcgcttc agggatcccc
//
"""


def run_tool(c, tool, params, timeout=900):
    r = c.post('/api/tool/run', json={'tool': tool, 'threads': 2, 'params': params})
    if r.status_code != 200:
        return None, f'HTTP {r.status_code}: {r.data[:160]}'
    j = r.get_json()
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = c.get(f"/api/task/{j['task']}?log_lines=0").get_json() or {}
        st = s.get('state') or s.get('status')
        if st in ('done', 'failed', 'error', 'cancelled'):
            return (j['run'] if st == 'done' else None), f'{st}: {s.get("error")}'
        time.sleep(2)
    return None, 'timeout'


def main():
    os.makedirs(WORK, exist_ok=True)

    print('[1] 检索式口径（build_query）')
    q = gbc.build_query(taxid='12892')
    chk(q.startswith('txid12892[Organism]') and 'NOT patent[Title]' in q,
        f'taxid 优先 + 排除专利：{q}')
    chk('complete genome[Title]' in gbc.build_query(taxid='1', full_length=True),
        '全长过滤拼进检索式')
    chk('2010:2024[PDAT]' in gbc.build_query(species='X', date_from='2010',
                                             date_to='2024'),
        '日期范围拼 [PDAT]')
    chk(gbc.build_query(term='LC1[Accession]') == 'LC1[Accession]',
        '手写 term 原样使用（不被拼装干扰）')
    try:
        gbc.build_query()
        chk(False, '三样都不给应报错')
    except ValueError as e:
        chk('species' in str(e), f'三样都不给时报错点明：{str(e)[:40]}')

    print('\n[2] 离线：本地 GBK → 数据接入 → 交付 metadata 必须与平台标准一致')
    gbk = os.path.join(WORK, 'fixture.gbk')
    io.open(gbk, 'w', encoding='utf-8').write(GBK)
    c = appmod.app.test_client()
    run, err = run_tool(c, 'pdprep', {'source': 'files2',
                                      'gb_files': gbk, 'strict': '0',
                                      'govern': '1'})
    if run is None and 'files2' in str(err):
        # 在线来源的本地文件走 source=genbank + gb_files
        run, err = run_tool(c, 'pdprep', {'source': 'genbank', 'gb_files': gbk,
                                          'strict': '0', 'govern': '1'})
    chk(run is not None, f'任务跑通（{err}）')
    from Virus_Platform_Core.config import DIRS
    md = os.path.join(DIRS['tool_runs'], run or '', 'pdprep', 'metadata.csv')
    rows = list(csv.DictReader(io.open(md, encoding='utf-8-sig'))) if os.path.isfile(md) else []
    cols = list(rows[0]) if rows else []
    chk(cols[:5] == ['name', 'date', 'location', 'lat', 'lon'],
        f'前五列 = 平台标准（实际 {cols}）')
    chk(set(cols[5:]) <= {'host'}, f'只允许追加 host（实际 {cols[5:]}）')
    if rows:
        # ⚠️ GenBank 的 rec.id **带版本号**（TEST0001.1）→ 按前缀取，别写死
        by = {r['name'].split('.')[0]: r for r in rows}
        t1 = by.get('TEST0001', {})
        chk(t1.get('location', '').startswith('Japan'),
            f"geo_loc_name 优先于 country：TEST0001.location={t1.get('location')!r}")
        chk(('Chiba' in (t1.get('location') or '')) or ('Chiba' in (t1.get('location') or '')),
            f"**不截断**到国家（geo_loc_name 是 Japan:Chiba，治理后 {t1.get('location')!r}）")
        chk((t1.get('host') or '').strip() == 'Dahlia sp.',
            f"host 带进数据集：{t1.get('host')!r}")
        chk(by.get('TEST0002', {}).get('location', '').startswith('China'),
            f'老式 country 记录仍能取到地点：{by.get("TEST0002", {}).get("location")!r}')

    print('\n[3] 负向：`geo_loc_name` 必须被引擎读到（曾整条漏读）')
    pairs, _missing = gbc.parse_flatfile(GBK), None
    meta = pairs[0][1] if pairs else {}
    chk(bool(meta.get('geo_loc_name')), f"引擎 meta['geo_loc_name'] 非空：{meta.get('geo_loc_name')!r}")
    chk('geo_loc_name' in meta, 'meta 字典里**有**这个键（只写进读取循环不够）')

    print('\n[4] 在线（无网自动 SKIP）')
    if not online():
        SKIP.append('NCBI 不可达')
        print('  SKIP NCBI 不可达（离线环境）')
    else:
        t = gbc.resolve_taxid(species='Potato spindle tuber viroid')
        chk(str(t) == '12892', f'物种名 → taxid = {t}（PSTVd 应为 12892）')
        t2 = gbc.resolve_taxid(accession='NC_002030.1')
        chk(str(t2) == '12892', f'accession → taxid = {t2}（两条独立通路应一致）')
        out = os.path.join(WORK, 'online')
        r = pk.import_from_genbank(out, taxid='12892', full_length=True,
                                   max_records=2, strict=False)
        chk(r['n_kept'] >= 1, f"一键采集收到 {r['n_kept']} 条")
        rr = list(csv.DictReader(io.open(r['metadata'], encoding='utf-8-sig')))
        c2 = list(rr[0]) if rr else []
        chk(c2[:5] == ['name', 'date', 'location', 'lat', 'lon'],
            f'在线输出前五列同样是标准（{c2}）')
        chk(all((x.get('location') or '').strip() for x in rr),
            '每条都有地点（geo_loc_name 优先）')

    print('\n' + '=' * 52)
    if FAIL:
        print(f'FAILED: {len(FAIL)} 项')
        for m in FAIL:
            print('  - ' + m)
        return 1
    print(f'ONLINE META CHECKS PASSED（{len(PASS)} 项'
          + (f'；SKIP {SKIP}' if SKIP else '') + '）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
