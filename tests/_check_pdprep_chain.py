# -*- coding: utf-8 -*-
"""回归：数据准备合并后的**一条链**（2026-09-18）。

四张数据准备类卡合并后，这一层必须证明：
  [1] 导入校验仍按老口径工作（+ 列名自动择优），并给出**绝对路径**（接力 A0 要用）；
  [2] 可选「区域坐标表」→ 概览地图真的有点位（`summary.map_points` 非空），
      且落点来源写成 `coords`；给不匹配的区划要**如实报数**（unplaced_regions）；
  [3] 可选「时空降采样」→ 产物子集落盘、`n_in/n_out` 对账、
      **没有区划信息的样本被排除并报数**（`excluded_no_region`）；
  [4] `equal` 模式在「Unknown 占绝大多数」时**不能**把结果塌成几十条
      （这是合并前实测到的坑：2,967 条 85% 是 Unknown → equal 只出 47 条）；
  [5] 负向：时空调度那两张卡（pdspacetime / pdsub）已从注册表与页面摘除。

用任务层（POST /api/tool/run + 轮询）真跑，不 mock。
用法: python tests/_check_pdprep_chain.py
"""
import csv
import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import app as appmod                                        # noqa: E402
from Virus_Platform_Core.config import DIRS                 # noqa: E402

WORK = os.path.join(ROOT, 'run', '_check_prep_chain')
PASS, FAIL = [], []


def chk(cond, msg):
    (PASS if cond else FAIL).append(msg)
    print(('  ok   ' if cond else '  FAIL ') + msg, flush=True)


def run_tool(c, tool, params, timeout=300):
    r = c.post('/api/tool/run', json={'tool': tool, 'threads': 2,
                                      'params': params})
    if r.status_code != 200:
        return None, f'HTTP {r.status_code}: {r.data[:200]}'
    j = r.get_json()
    tid, run = j.get('task'), j.get('run')
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = c.get(f'/api/task/{tid}?log_lines=0').get_json() or {}
        st = s.get('state') or s.get('status')
        if st in ('done', 'failed', 'error', 'cancelled'):
            return (run if st == 'done' else None), f'{st}: {s.get("error")}'
        time.sleep(2)
    return None, 'timeout'


def readsum(run, name='summary.json'):
    p = os.path.join(DIRS['tool_runs'], run, 'pdprep', name)
    return json.load(io.open(p, encoding='utf-8')) if os.path.isfile(p) else None


def main():
    os.makedirs(WORK, exist_ok=True)
    names = [ln[1:].strip().split()[0] for ln in
             io.open(os.path.join(ROOT, 'examples', 'example_phylogeo.fasta'),
                     encoding='utf-8') if ln.startswith('>')]
    # 抄一份等长比对（A0 要等长；本卡只要 FASTA 合法）
    fa = os.path.join(WORK, 'aln.fasta')
    with io.open(fa, 'w', encoding='utf-8') as f:
        for n in names:
            f.write(f'>{n}\n' + 'ACGT' * 10 + '\n')
    # 元数据：3 个真区划 + 一堆 Unknown（复现"Unknown 占多数"的坑）
    mt = os.path.join(WORK, 'meta.csv')
    with io.open(mt, 'w', encoding='utf-8-sig', newline='') as f:
        f.write('name,date,location\n')
        for i, n in enumerate(names):
            loc = ('China' if i < 3 else 'Japan' if i < 6 else 'Unknown')
            f.write(f'{n},20{10 + i}-01-01,{loc}\n')
    # 坐标表（Spatial.txt 口径）
    cd = os.path.join(WORK, 'coords.tsv')
    with io.open(cd, 'w', encoding='utf-8') as f:
        f.write('Region\tLatitude\tLongitude\nChina\t35.0\t105.0\nJapan\t36.0\t138.0\n'
                'Nowhere\t0.0\t0.0\n')

    c = appmod.app.test_client()

    # ⚠️ §1~§4 刻意 `govern:'0'`：这四节测的是**原有链路**（含降采样自身的
    # Unknown 防护）；治理默认开会把这些 Unknown 行在校验前就清掉，
    # 那不是这四节要测的东西。治理/去重/QC 在 §6~§9。
    print('[1] 纯导入（老口径 + 绝对路径）')
    run, err = run_tool(c, 'pdprep', {'source': 'files', 'fasta': fa, 'meta': mt,
                                      'strict': '0', 'govern': '0'})
    chk(run is not None, f'任务跑通（{err}）')
    s = readsum(run) or {}
    chk(s.get('n_kept') == len(names), f"全收 {s.get('n_kept')}/{len(names)}")
    chk(bool(s.get('abs', {}).get('seq')) and bool(s.get('abs', {}).get('meta')),
        '摘要给出绝对路径（接力 A0 要用）')
    chk(not (s.get('summary') or {}).get('map_points'),
        '没给坐标表时地图无点位（元数据也没有 lat/lon）')

    print('\n[2] 加「区域坐标表」→ 地图真有点位')
    run2, err2 = run_tool(c, 'pdprep', {'source': 'files', 'fasta': fa, 'meta': mt,
                                        'strict': '0', 'coords': cd,
                                        'govern': '0'})
    chk(run2 is not None, f'任务跑通（{err2}）')
    s2 = readsum(run2) or {}
    co = s2.get('coords') or {}
    mp = (s2.get('summary') or {}).get('map_points') or []
    chk(co.get('n_matched', 0) >= 6, f'匹配到 {co.get("n_matched")} 行（China/Japan 各 3）')
    chk(len(mp) >= 6, f'地图点位 {len(mp)} 个')
    chk([x for x in co.get('unplaced_regions') or [] if x[0] == 'Unknown'],
        f"没落点的区划如实报数：{co.get('unplaced_regions')}")
    chk('Nowhere' in (co.get('missing_regions') or []),
        f"坐标表里没用到的区划也报：{co.get('missing_regions')}")

    print('\n[3] 加「时空降采样」（random）')
    run3, err3 = run_tool(c, 'pdprep', {'source': 'files', 'fasta': fa, 'meta': mt,
                                        'strict': '0', 'sub': '1', 'govern': '0',
                                        'sub_mode': 'random', 'sub_n': '6',
                                        'sub_seed': '7'})
    chk(run3 is not None, f'任务跑通（{err3}）')
    s3 = readsum(run3) or {}
    sb = s3.get('subsample') or {}
    chk(sb.get('n_in') == len(names) and sb.get('n_out') == 6,
        f"降采样 {sb.get('n_in')} → {sb.get('n_out')}（random n=6）")
    sf = os.path.join(DIRS['tool_runs'], run3, 'pdprep',
                      'sequences.subsampled.fasta')
    sm = os.path.join(DIRS['tool_runs'], run3, 'pdprep',
                      'metadata.subsampled.csv')
    chk(os.path.isfile(sf) and os.path.isfile(sm), '子集 fasta + metadata 都落盘')
    if os.path.isfile(sm):
        n = max(0, sum(1 for _ in io.open(sm, encoding='utf-8-sig')) - 1)
        chk(n == 6, f'子集元数据同步过滤到 {n} 行')
    chk((s3.get('abs') or {}).get('sub_seq'), '摘要给子集绝对路径（接力 A0 优先用它）')

    print('\n[4] equal 模式不能把结果塌掉（Unknown 占多数的坑）')
    run4, err4 = run_tool(c, 'pdprep', {'source': 'files', 'fasta': fa, 'meta': mt,
                                        'strict': '0', 'sub': '1', 'govern': '0',
                                        'sub_mode': 'equal', 'sub_seed': '1'})
    chk(run4 is not None, f'任务跑通（{err4}）')
    s4 = readsum(run4) or {}
    sb4 = s4.get('subsample') or {}
    chk(sb4.get('n_excluded_no_region', 0) > 0,
        f"Unknown 被排除并报数（{sb4.get('n_excluded_no_region')} 条）")
    chk((sb4.get('per_region') or {}).get('Unknown') is None,
        f"per_region 里不含 Unknown：{sb4.get('per_region')}")
    chk(sb4.get('n_out', 0) >= 6,
        f"equal 出 {sb4.get('n_out')} 条（两区各 3 → 至少 6，而不是塌成个位数）")
    chk(any('没有区划信息' in w for w in s4.get('warnings') or []),
        '摘要里给「排除了无区划样本」的告警')

    print('\n[6] 上游预处理①治理：占位符 / DD-Mon-YYYY / Unknown 层级')
    bad = os.path.join(WORK, 'gov_meta.csv')
    with io.open(bad, 'w', encoding='utf-8-sig', newline='') as f:
        f.write('Accession,Collection_Date,Release_Date,Geo_Location,Country\n')
        f.write('g1,2013-05-01,2014-01-01,"China: Yunnan",China\n')
        f.write('g2,,2014-01-01,"China, Unknown, Unknown_AI",China\n')
        f.write('g3,Yyyy-Mm-Dd,,Unknown,Unknown\n')
        f.write('g4,01-May-2013,,Russia: Stavropol,Russia\n')
        f.write('g5,2013,,,\n')
        f.write('g6,2020-13-01,,Japan,Japan\n')
    gfa = os.path.join(WORK, 'gov.fasta')
    with io.open(gfa, 'w', encoding='utf-8') as f:
        for i in range(1, 7):
            f.write(f'>g{i}\n' + 'ACGT' * 25 + '\n')
    r6, e6 = run_tool(c, 'pdprep', {'source': 'files', 'fasta': gfa, 'meta': bad,
                                    'strict': '0', 'govern': '1'})
    chk(r6 is not None, f'任务跑通（{e6}）')
    s6 = readsum(r6) or {}
    gv = s6.get('govern') or {}
    chk(gv.get('n_date_ok') == 4,
        f"日期可解析 4/6（g3 占位符、g6 越界应认不出）：{gv.get('date_issue')}")
    chk((gv.get('date_format') or {}).get('genbank') == 1,
        f"DD-Mon-YYYY 被识别并归一（格式分布 {gv.get('date_format')}）")
    chk('country_only' in (gv.get('location_issue') or {}),
        f"删掉 Unknown 层级后只剩国名 → country_only：{gv.get('location_issue')}")
    chk(any('占位符' in w for w in gv.get('warnings') or []),
        '地点被清空有明确告警（不静默）')
    md = os.path.join(DIRS['tool_runs'], r6, 'pdprep', 'metadata.csv')
    txt = io.open(md, encoding='utf-8-sig').read() if os.path.isfile(md) else ''
    chk('2013-05-01' in txt and 'Yyyy-Mm-Dd' not in txt,
        '归一后的元数据里是 ISO，且占位符行没混进来')
    chk('China, Yunnan' in txt, '地点也归一（冒号 → 逗号）')

    print('\n[7] 上游预处理②三元组去重（无地点的行不参与）')
    dfa = os.path.join(WORK, 'dup.fasta')
    with io.open(dfa, 'w', encoding='utf-8') as f:
        f.write('>d1\nACGTACGT\n>d2\nACGTACGT\n>d3\nACGTACGT\n>d4\nTTTTTTTT\n')
    dmt = os.path.join(WORK, 'dup_meta.csv')
    with io.open(dmt, 'w', encoding='utf-8-sig', newline='') as f:
        f.write('name,date,location\n')
        f.write('d1,2010,China\n')
        f.write('d2,2010,China\n')      # 与 d1 完全同（日期+地点+序列）→ 应剔
        f.write('d3,2011,China\n')      # 日期不同 → 保留
        f.write('d4,2010,China\n')      # 序列不同 → 保留
    r7, e7 = run_tool(c, 'pdprep', {'source': 'files', 'fasta': dfa, 'meta': dmt,
                                    'strict': '0', 'dedup': '1'})
    chk(r7 is not None, f'任务跑通（{e7}）')
    dd = (readsum(r7) or {}).get('dedup') or {}
    chk(dd.get('by') == 'date+location+sequence',
        f"去重键含序列：{dd.get('by')}")
    chk(dd.get('n_in') == 4 and dd.get('n_dropped') == 1,
        f"剔掉唯一的完全重复（{dd.get('n_in')} → {dd.get('n_kept')}）")
    # 无地点的行不参与去重（不拿空地点当键）
    nmt = os.path.join(WORK, 'noloc_meta.csv')
    with io.open(nmt, 'w', encoding='utf-8-sig', newline='') as f:
        f.write('name,date,location\n')
        for i in range(1, 5):
            f.write(f'd{i},{2010 + (i % 2)},Unknown\n')
    r7b, e7b = run_tool(c, 'pdprep', {'source': 'files', 'fasta': dfa, 'meta': nmt,
                                      'strict': '0', 'dedup': '1',
                                      'allow_noloc': '1'})
    chk(r7b is not None, f'（允无地点）任务跑通（{e7b}）')
    dd2 = (readsum(r7b) or {}).get('dedup') or {}
    chk(dd2.get('n_skipped_no_loc', 0) == 4,
        f"地点全 Unknown → 4 条都**不参与**去重（实测 {dd2.get('n_skipped_no_loc')}）")

    qmt = os.path.join(WORK, 'qc_meta.csv')
    with io.open(qmt, 'w', encoding='utf-8-sig', newline='') as f:
        f.write('name,date,location\n')
        for i in range(1, 5):
            f.write(f'q{i},2010,China\n')
    print('\n[8] 上游预处理③比对 QC（含"先 MAFFT 再 QC"真跑）')
    qfa = os.path.join(WORK, 'qc.fasta')
    with io.open(qfa, 'w', encoding='utf-8') as f:
        f.write('>q1\n' + 'ACGT' * 25 + '\n')
        f.write('>q2\n' + 'ACGT' * 20 + '-' * 20 + '\n')      # gap 20% → too_gappy
        f.write('>q3\n' + 'ACGT' * 10 + '\n')                 # 40bp → too_short
        f.write('>q4\n' + 'ACGT' * 25 + '\n')
    r8, e8 = run_tool(c, 'pdprep', {'source': 'files', 'fasta': qfa,
                                    'meta': os.path.join(WORK, 'qc_meta.csv'),
                                    'strict': '0', 'qc': 'qc'})
    chk(r8 is not None, f'任务跑通（{e8}）')
    qc = (readsum(r8) or {}).get('align_qc') or {}
    chk(qc.get('n_drop') == 2 and qc.get('n_keep') == 2,
        f"QC 剔 2 留 2（{qc.get('drop_reasons')}）")
    chk(qc.get('aligned') is False, '不等长被判出来（A0 的硬前置）')
    outs = qc.get('outputs') or {}
    chk(bool(outs.get('clean')) and bool(outs.get('removed')),
        f"clean / removed 另存（{sorted(outs)}）")

    print('\n[9] 契约：允许没有地点（只做定年时用）')
    r9, e9 = run_tool(c, 'pdprep', {'source': 'files', 'fasta': fa, 'meta': mt,
                                    'strict': '0', 'govern': '1',
                                    'allow_noloc': '1'})
    chk(r9 is not None, f'任务跑通（{e9}）')
    s9 = readsum(r9) or {}
    chk(s9.get('n_kept') == len(names),
        f"允许无地点后全收 {s9.get('n_kept')}/{len(names)}"
        f"（默认要求地点时会掉到 6 条：{s9.get('govern', {}).get('location_issue')}）")

    print('\n[10] 负向：被合并的两张卡已摘除')
    from Virus_Platform_Core.web.tools_api import TOOL_REGISTRY as TR
    chk('pdspacetime' not in TR and 'pdsub' not in TR,
        '注册表里没有 pdspacetime / pdsub')
    html = c.get('/tools?g=phylodyn').data.decode('utf-8', 'replace')
    left = [m for m in ('id="t-pdspacetime"', 'id="t-pdsub"', 'id="pdst_temporal"',
                        'id="pdsub_input"') if m in html]
    chk(not left, f'页面里没有它们的标记（残留: {left or "无"}）')
    for m in ('id="pd_sub_on"', 'id="pd_coords_tsv"', 'id="pdprepToA0"',
              'function pdSubToggle(', 'function pdPrepToA0('):
        chk(m in html, f'数据准备卡有新控件: {m}')

    print('\n' + '=' * 52)
    if FAIL:
        print(f'FAILED: {len(FAIL)} 项')
        for m in FAIL:
            print('  - ' + m)
        return 1
    print(f'PREP CHAIN CHECKS PASSED（{len(PASS)} 项）')
    print('临时产物:', WORK)
    return 0


if __name__ == '__main__':
    sys.exit(main())
