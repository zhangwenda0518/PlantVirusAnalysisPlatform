# -*- coding: utf-8 -*-
"""长耗时同步接口「后台任务化」与重 IO 缓存的回归测试（离线，秒级）。

锁定 2026-09-11 的一批改动：把这些原本同步阻塞 HTTP 的长耗时请求改成
任务中心后台任务，并给无缓存的重 IO 端点补缓存/上限：

  1. .sqn 生成     —— 原最坏 ~45 分钟阻塞一个工作线程，无进度、无法取消
  2. CDS/PEP 导出  —— 原全量重解析集合 + 现场翻译
  3. AI 元数据总结 —— 原单次调用客户端超时 180s
  4. 物种名校验    —— 原每物种一次 esearch（每次最长 120s）且串行
  5. CDS 明细表磁盘缓存（集合指纹失效）
  6. 分类报告 / taxburst 产物复用（docstring 声称有缓存，实际每次都重建）
  7. /api/seqview 扫描字节上限、/api/align/file|save 文件大小上限

全部离线：suvtk / DeepSeek / NCBI 三个外部依赖都用桩替换，只验证
「任务化管道 + 进度口径 + 结果回传 + 缓存语义」本身。
用法: python tests/_it_bg_tasks.py
"""
import json
import os
import shutil
import sys
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault('VP_NO_RECOVER', '1')

FAIL = []
CLEAN = []


def check(label, ok, extra=''):
    print(f'  [{"PASS" if ok else "FAIL"}] {label}' + (f'  {extra}' if extra else ''),
          flush=True)
    if not ok:
        FAIL.append(label)


def wait_task(tid, timeout=90, samples=None):
    """轮询任务到终态，返回快照。

    samples: 可选 list，逐轮追加 pct —— 用来断言阶段进度**确实推进过**，
    而不是只在收尾时被置成 1.0。
    """
    from Virus_Platform_Core.web.tasks import tm
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = tm.snapshot(tid, log_lines=80)
        if samples is not None and s:
            samples.append(s.get('pct'))
        if s and s['status'] != 'running':
            return s
        time.sleep(0.25)
    return tm.snapshot(tid, log_lines=80)


import app as appmod                                             # noqa: E402
from Virus_Platform_Core.config import DIRS, PLATFORM_ROOT       # noqa: E402
from Virus_Platform_Core.web.tasks import tm                     # noqa: E402

client = appmod.app.test_client()

# ══════════════ 5) CDS 明细表磁盘缓存 ══════════════
print('\n== CDS 明细表缓存 ==', flush=True)
from Virus_Platform_Core import cds_export                       # noqa: E402
from Virus_Platform_Core.gb_collection import gb_collection_dir   # noqa: E402

COLL = 'ittest_bgtask_cds'
cdir = gb_collection_dir(COLL)
CLEAN.append(cdir)
shutil.rmtree(cdir, ignore_errors=True)
os.makedirs(cdir, exist_ok=True)
GB = os.path.join(cdir, 'seq1.gb')
with open(GB, 'w', encoding='utf-8') as f:
    f.write("""LOCUS       IT1                       60 bp    RNA     linear
ACCESSION   IT1
ORGANISM  Test virus
  Viruses.
FEATURES             Location/Qualifiers
     source          1..60
                     /organism="Test virus"
     CDS             1..60
                     /gene="cp"
                     /product="coat protein"
                     /translation="MKLT"
ORIGIN
        1 atggctaaat tgacttaata a
//
""")
a = cds_export.build_cds_table(COLL)
check('首次构建未走缓存且识别出 CDS',
      a.get('cached') is False and a.get('n_cds') == 1,
      f"cached={a.get('cached')} n_cds={a.get('n_cds')}")
b = cds_export.build_cds_table(COLL)
check('二次调用命中磁盘缓存', b.get('cached') is True)
check('缓存内容与首次一致', b.get('rows') == a.get('rows'))
with open(GB, 'a', encoding='utf-8') as f:
    f.write('\n')
time.sleep(0.02)
c = cds_export.build_cds_table(COLL)
check('GB 变更后缓存自动失效', c.get('cached') is False)

# 缓存文件必须落在集合目录内且可被清理
check('缓存文件写在集合目录',
      os.path.isfile(os.path.join(cdir, 'cds_table_cache.json')))

# ══════════════ 6) 分类报告 / taxburst 复用 ══════════════
print('\n== 分类报告与 taxburst 产物复用 ==', flush=True)
from Virus_Platform_Core import viz                              # noqa: E402
from Virus_Platform_Core.kunpeng import parse_kreport             # noqa: E402

RUN = os.path.join(DIRS.get('tool_runs') or
                   os.path.join(PLATFORM_ROOT, 'run', 'tool_runs'),
                   'ittest_bgtask_report')
CLEAN.append(RUN)
shutil.rmtree(RUN, ignore_errors=True)
os.makedirs(RUN, exist_ok=True)
KREP = os.path.join(RUN, 'sample.kreport2')
with open(KREP, 'w', encoding='utf-8') as f:
    f.write('100.00\t10\t10\tU\t0\tunclassified\n'
            ' 60.00\t6\t6\tR\t1\troot\n'
            '  60.00\t6\t6\tG\t11000\tTestvirus\n'
            '  50.00\t5\t5\tS\t11001\t  Test virus A\n')
with open(os.path.join(RUN, 'virus_classification.tsv'),
          'w', encoding='utf-8') as f:
    f.write('contig\ttaxid\ttaxon\tlength\tlength_ratio\n'
            'c1\t11001\tTest virus A\t300\t1.0\n')

p1 = viz.build_contig_report(RUN)
m1 = os.path.getmtime(p1)
check('report.html 已生成且非空',
      os.path.isfile(p1) and os.path.getsize(p1) > 1000,
      f'{os.path.getsize(p1)} B')
time.sleep(0.05)
viz.build_contig_report(RUN)
check('输入未变时复用 report.html（mtime 不变）', os.path.getmtime(p1) == m1)

tb1 = os.path.join(RUN, 'taxburst.html')
if os.path.isfile(tb1):
    t_m1 = os.path.getmtime(tb1)
    time.sleep(0.05)
    viz.build_taxburst(RUN, parse_kreport(KREP))
    check('taxburst.html 复用（mtime 不变）', os.path.getmtime(tb1) == t_m1)
else:
    print('  (taxburst 未安装，跳过该子项)', flush=True)

time.sleep(0.05)
with open(KREP, 'a', encoding='utf-8') as f:
    f.write('  10.00\t1\t1\tS\t11002\t  Test virus B\n')
viz.build_contig_report(RUN)
check('输入变更后 report.html 重建', os.path.getmtime(p1) != m1)

# 走 HTTP：?force=1 必须强制重建
m2 = os.path.getmtime(p1)
time.sleep(0.05)
r = client.get('/api/tool/report', query_string={
    'run': os.path.basename(RUN), 'force': '1'})
check('/api/tool/report?force=1 返回重定向', r.status_code in (301, 302, 303),
      f'status={r.status_code}')

# ══════════════ 7) seqview / align 上限 ══════════════
print('\n== 重 IO 端点上限 ==', flush=True)
from Virus_Platform_Core.web import io_api, tool_results          # noqa: E402

FA = os.path.join(RUN, 'big.fasta')
with open(FA, 'w', encoding='utf-8') as f:
    for i in range(400):
        f.write(f'>seq{i}\n' + 'ACGT' * 50 + '\n')

io_api._SEQVIEW_MAX_BYTES = 2048
r = client.get('/api/seqview', query_string={'path': FA})
j = r.get_json() or {}
check('seqview 触及字节上限即标记截断',
      r.status_code == 200 and j.get('byte_capped') is True,
      f"status={r.status_code} capped={j.get('byte_capped')}")
io_api._SEQVIEW_MAX_BYTES = 64 << 20
j2 = (client.get('/api/seqview', query_string={'path': FA})).get_json() or {}
check('上限放开后完整扫描', j2.get('byte_capped') is False and j2.get('total') == 400,
      f"total={j2.get('total')}")

tool_results._ALIGN_MAX_MB = 0
r3 = client.get('/api/align/file', query_string={'path': FA})
check('align/file 超限返回 400', r3.status_code == 400,
      str((r3.get_json() or {}).get('error'))[:70])
r4 = client.post('/api/align/save', json={'path': FA, 'content': '>a\nACGT\n>b\nACGT\n'})
check('align/save 超限返回 400', r4.status_code == 400,
      str((r4.get_json() or {}).get('error'))[:70])
tool_results._ALIGN_MAX_MB = 128

# ══════════════ 1) .sqn 后台任务化 ══════════════
print('\n== .sqn 生成后台任务化（suvtk 用桩替换）==', flush=True)
from Virus_Platform_Core.ncbi_submit import store as sstore       # noqa: E402
from Virus_Platform_Core import suvtk_submit as ss                # noqa: E402

PROJ = 'ittest_bgtask_sqn'
RUNDIR = os.path.join(DIRS.get('tool_runs') or
                      os.path.join(PLATFORM_ROOT, 'run', 'tool_runs'),
                      'orf_ittest_bgtask')
CLEAN += [PROJ, RUNDIR]

r = client.post(f'/api/submit/table/{PROJ}/sqn', json={'async': True})
check('未关联 orf 运行 → 明确 400（不静默挂住）', r.status_code == 400,
      str((r.get_json() or {}).get('error'))[:60])

# 造最小项目 + 假 orf 运行，让请求通过前置校验
shutil.rmtree(RUNDIR, ignore_errors=True)
os.makedirs(os.path.join(RUNDIR, '03_assembly'), exist_ok=True)
with open(os.path.join(RUNDIR, '03_assembly', 'viral_contigs.fasta'),
          'w', encoding='utf-8') as f:
    f.write('>contig1 taxid=11001 taxon=Test_virus\n' + 'ACGT' * 120 + '\n')
shutil.rmtree(sstore.table_dir(PROJ), ignore_errors=True)
sstore.create_table(PROJ, sample='demo')
df = sstore.load_table(PROJ)
df.loc[df.index[0], 'sequence_name'] = 'contig1'
if 'organism' in df.columns:
    df.loc[df.index[0], 'organism'] = 'Test virus'
sstore.save_table(PROJ, df)
sstore._write_link(PROJ, 'orf', {'run': os.path.basename(RUNDIR)})

_orig_build_sqn = ss.build_sqn
_stages = []


def _stub_build_sqn(*a, **kw):
    """替代 suvtk 全链：按真实顺序吐出关键字并留出采样窗口，
    用来验证「阶段进度按日志关键字推进」这条口径确实生效。"""
    log = kw.get('log') or (lambda m: None)
    log('  提交 FASTA 准备完成')
    time.sleep(0.6)
    log('  $ suvtk features -i ...')
    time.sleep(0.6)
    log('  suvtk comments ...')
    time.sleep(0.6)
    log('  $ table2asn ...')
    time.sleep(0.6)
    return {'sqn': os.path.join(sstore.table_dir(PROJ), 'sqn.sqn'),
            'stats': {'error': 0, 'warning': 1, 'info': 0},
            'val_head': ['Warning: test']}


_orig_chain_ready = ss.suvtk_chain_ready
ss.suvtk_chain_ready = lambda: True   # BFVD 链开发机专属；测试桩直通 build_sqn
ss.build_sqn = _stub_build_sqn
try:
    r = client.post(f'/api/submit/table/{PROJ}/sqn',
                    json={'async': True, 'author': {}})
    d = r.get_json() or {}
    check('异步分支立即返回 task id',
          r.status_code == 200 and bool(d.get('task')),
          f'status={r.status_code} {str(d)[:80]}')
    tid = d.get('task')
    pcts = []
    snap = wait_task(tid, samples=pcts) if tid else {}
    check('任务收敛为成功', snap.get('status') == 'done',
          f"status={snap.get('status')} err={str(snap.get('error'))[:60]}")
    check('任务出现在任务中心列表',
          any(x['id'] == tid for x in tm.list_all()))
    check('任务卡带「前往」链接', snap.get('link') == '/submit')
    # 采样到的 pct 里必须出现中间档（0.10 / 0.62 / 0.85），
    # 证明进度是按 suvtk 阶段推进的，而不是只有收尾的 1.0
    mids = [p for p in pcts if p is not None and p < 1.0]
    check('阶段进度确实逐档推进（采样到中间档）',
          len(mids) >= 2 and max(mids) >= 0.6,
          f'samples={pcts}')
    check('日志实时可见（suvtk 输出进任务日志）',
          any('table2asn' in x for x in (snap.get('log') or [])),
          str((snap.get('log') or [])[-2:])[:100])
finally:
    ss.build_sqn = _orig_build_sqn
    # 就绪门桩保留到同步分支跑完（下方同步 POST 同样要过门），最后统一恢复

# 同步分支语义未变（不传 async 仍直接返回结果体）
ss.build_sqn = _stub_build_sqn
try:
    r = client.post(f'/api/submit/table/{PROJ}/sqn', json={'author': {}})
    j = r.get_json() or {}
    check('同步分支仍直接返回产物字段（向后兼容）',
          r.status_code == 200 and 'sqn' in j and 'stats' in j,
          str(sorted(j.keys()))[:90])
finally:
    ss.build_sqn = _orig_build_sqn
    ss.suvtk_chain_ready = _orig_chain_ready

# ══════════════ 2) CDS/PEP 导出后台任务化 ══════════════
print('\n== CDS/PEP 导出后台任务化 ==', flush=True)
rid = cds_export.build_cds_table(COLL)['rows'][0]['rid']
rs = client.post('/api/cds/export',
                 json={'name': COLL, 'items': [{'rid': rid, 'gene': 'cp'}]})
check('同步分支仍返回完整产物字段',
      rs.status_code == 200 and 'n_cds' in (rs.get_json() or {}),
      str(sorted((rs.get_json() or {}).keys()))[:90])
ra = client.post('/api/cds/export',
                 json={'name': COLL, 'items': [{'rid': rid, 'gene': 'cp'}],
                       'async': True})
da = ra.get_json() or {}
snap = wait_task(da.get('task')) if da.get('task') else {}
check('导出异步分支返回 task 并成功',
      ra.status_code == 200 and snap.get('status') == 'done',
      f"status={ra.status_code} task_status={snap.get('status')}")
check('导出结果回到前端可读的 result.stats.n_cds',
      ((snap.get('result') or {}).get('stats') or {}).get('n_cds') == 1,
      str((snap.get('result') or {}).get('stats'))[:90])
check('导出任务卡链接指向 /cds-export', snap.get('link') == '/cds-export')

# ══════════════ 3) AI 总结后台任务化 ══════════════
print('\n== AI 元数据总结后台任务化（DeepSeek 用桩替换）==', flush=True)
import pandas as pd                                               # noqa: E402
from Virus_Platform_Core.web import meta as vpm                    # noqa: E402

_orig = (vpm._meta_load_df, vpm._meta_deepseek_key, vpm._meta_ai_chat)
vpm._meta_load_df = lambda name, which: pd.DataFrame(
    {'ScientificName': ['Test virus', 'Test virus'],
     'Database': ['SRA', 'GSA'],
     'FileSize_GB': [1.0, 2.0],
     'CollectionDate': ['2024-01-01', '2024-02-01']})
vpm._meta_deepseek_key = lambda: 'test-key'
vpm._meta_ai_chat = lambda *a, **k: 'CANNED SUMMARY PARAGRAPH.'
try:
    rai = client.post('/api/meta/ai_summary',
                      json={'name': 'anyproj', 'which': 'core14',
                            'async': True})
    dai = rai.get_json() or {}
    snap = wait_task(dai.get('task')) if dai.get('task') else {}
    check('AI 总结异步分支返回 task 并成功',
          rai.status_code == 200 and snap.get('status') == 'done',
          f"status={rai.status_code} task_status={snap.get('status')}")
    st = (snap.get('result') or {}).get('stats') or {}
    check('摘要经 result.stats.summary 回传',
          st.get('summary') == 'CANNED SUMMARY PARAGRAPH.', str(st)[:90])
    check('AI 任务卡链接指向 /meta', snap.get('link') == '/meta')
    # 同步分支语义保持
    rsi = client.post('/api/meta/ai_summary',
                      json={'name': 'anyproj', 'which': 'core14'})
    check('AI 总结同步分支仍返回 {summary}',
          rsi.status_code == 200
          and (rsi.get_json() or {}).get('summary') == 'CANNED SUMMARY PARAGRAPH.')
finally:
    (vpm._meta_load_df, vpm._meta_deepseek_key, vpm._meta_ai_chat) = _orig

# ══════════════ 4) 物种名校验后台任务化 ══════════════
print('\n== 物种名校验后台任务化（无物种 → 不触网）==', flush=True)
PROJ2 = 'ittest_bgtask_tax'
CLEAN.append(PROJ2)
shutil.rmtree(sstore.table_dir(PROJ2), ignore_errors=True)
sstore.create_table(PROJ2, sample='demo')
df2 = sstore.load_table(PROJ2)
if 'organism' in df2.columns:
    df2['organism'] = ''            # 空值被 is_placeholder 过滤 → orgs=[] → 零网络
sstore.save_table(PROJ2, df2)

rt = client.post(f'/api/submit/table/{PROJ2}/taxonomy_check',
                 json={'async': True})
dt = rt.get_json() or {}
check('校验异步分支返回 task',
      rt.status_code == 200 and bool(dt.get('task')),
      f'status={rt.status_code} {str(dt)[:80]}')
snap = wait_task(dt.get('task')) if dt.get('task') else {}
check('校验任务收敛为成功', snap.get('status') == 'done',
      f"status={snap.get('status')} err={str(snap.get('error'))[:60]}")
# 结果落项目目录，前端经既有 file 接口读回
rf = client.get(f'/api/submit/table/{PROJ2}/file',
                query_string={'name': 'taxonomy_check.json'})
check('结果文件可经既有 /file 接口读回', rf.status_code == 200,
      f'status={rf.status_code}')
if rf.status_code == 200:
    payload = json.loads((rf.get_json() or {}).get('content') or '{}')
    check('结果文件结构与前端约定一致',
          'rows' in payload and 'missing' in payload,
          str(sorted(payload.keys())))
check('校验任务卡链接指向 /submit', snap.get('link') == '/submit')

# ══════════════ 5) /api/meta/overview：正确性 + 缓存 + 热点 ══════════════
# 原实现每列重复算 2~3 次 isin，最后用 df.iterrows() 逐行判断完整度。
# 实测在真实 15851×11 检索表上那一段是 847 ms/次（占该接口绝大部分），
# 向量化后 0.5 ms。这里用合成表把「语义等价 + 缓存 + 失效」钉死。
print('\n== /api/meta/overview 正确性与缓存 ==', flush=True)
import pandas as pd                                               # noqa: E402
from Virus_Platform_Core.web import meta as vpm                    # noqa: E402

MPROJ = 'ittest_bgtask_meta'
mtab = os.path.join(vpm._meta_proj(MPROJ), 'search')
CLEAN.append(os.path.join(vpm._meta_proj(MPROJ)))
shutil.rmtree(vpm._meta_proj(MPROJ), ignore_errors=True)
os.makedirs(mtab, exist_ok=True)

# 造表：混入真实值、NA 占位词、空串 —— 覆盖 complete/missing 的判定边界
NA_WORDS = ['', 'unknown', 'missing', 'N/A', 'not collected', '-']
rows = []
for i in range(1200):
    rows.append({
        'Run': f'SRR{i:06d}',
        'ScientificName': NA_WORDS[i % len(NA_WORDS)] if i % 3 == 0
        else 'Test virus',
        'Tissue': 'leaf' if i % 2 else '',
        'Location': 'China:Ningxia' if i % 5 else 'unknown',
        'Database': 'SRA' if i % 2 else 'GSA',
        'CollectionDate': f'20{10 + i % 12:02d}-01-01',
        'FileSize_GB': str(round(0.5 + (i % 7) * 0.25, 2)),
    })
dfm = pd.DataFrame(rows)
vpm._meta_store_df(MPROJ, 'search', dfm)
check('合成表已写入（走 _meta_store_df）',
      os.path.isfile(os.path.join(mtab, 'SRA_GSA_Merged_Final.csv')))

t0 = time.perf_counter()
ro = client.get('/api/meta/overview',
                query_string={'name': MPROJ, 'which': 'search'})
t_cold = time.perf_counter() - t0
jo = ro.get_json() or {}
check('overview 返回 200', ro.status_code == 200, f'status={ro.status_code}')

# 独立参照实现（逐行循环，与被替换掉的 iterrows 同语义但更直白）
ref_na = vpm._META_NA
ref_complete = 0
ref_missing = 0
for _, r in dfm.iterrows():
    cells = [str(v).strip().lower() for v in r]
    miss_row = sum(1 for v in cells if v in ref_na)
    ref_missing += miss_row
    if miss_row == 0:
        ref_complete += 1
s = jo.get('summary') or {}
check('complete 与独立参照一致', s.get('complete') == ref_complete,
      f"接口={s.get('complete')} 参照={ref_complete}")
check('missing 与独立参照一致', s.get('missing') == ref_missing,
      f"接口={s.get('missing')} 参照={ref_missing}")
check('filled = 总格数 - missing',
      s.get('filled') == len(dfm) * len(dfm.columns) - ref_missing,
      f"filled={s.get('filled')}")
check('fields 覆盖全部列', sorted((jo.get('fields') or {}).keys())
      == sorted(dfm.columns))
# 抽查一列的 counts/unique 与参照一致（防止掩码口径漂移）
col = 'ScientificName'
ser = dfm[col].astype(str).str.strip()
ser = ser[~ser.str.lower().isin(ref_na)]
exp_counts = {k: int(v) for k, v in ser.value_counts().head(50).items()}
got_counts = {k: int(v) for k, v in (jo['fields'][col]['counts'])}
check(f'fields[{col}].counts 与参照一致', got_counts == exp_counts,
      f'{got_counts} vs {exp_counts}')
check(f'fields[{col}].unique 与参照一致',
      jo['fields'][col]['unique'] == int(ser.nunique()))

t0 = time.perf_counter()
ro2 = client.get('/api/meta/overview',
                 query_string={'name': MPROJ, 'which': 'search'})
t_warm = time.perf_counter() - t0
check('二次调用命中响应缓存且内容一致',
      ro2.get_json() == jo and t_warm < t_cold,
      f'冷 {t_cold*1000:.0f}ms → 热 {t_warm*1000:.2f}ms')
check('响应缓存生效（热调用 <5ms）', t_warm < 0.005, f'{t_warm*1000:.2f} ms')
ro3 = client.get('/api/meta/overview',
                 query_string={'name': MPROJ, 'which': 'search',
                               'force': '1'})
check('force=1 绕过缓存但结果一致', ro3.get_json() == jo)

# 内容变更 → 必须重新计算并反映出来
dfm2 = dfm.copy()
dfm2.loc[0, 'ScientificName'] = 'Changed virus'
vpm._meta_store_df(MPROJ, 'search', dfm2)
jo4 = (client.get('/api/meta/overview',
                  query_string={'name': MPROJ, 'which': 'search'}).get_json()
       or {})
names = {k for k, _ in jo4['fields']['ScientificName']['counts']}
check('表内容变更后缓存失效并反映新值', 'Changed virus' in names, str(names))
# 变更后 complete/missing 也要重新算对（对 dfm2 重跑参照实现）
ref_c2 = ref_m2 = 0
for _, r in dfm2.iterrows():
    cells = [str(v).strip().lower() for v in r]
    m2 = sum(1 for v in cells if v in ref_na)
    ref_m2 += m2
    if m2 == 0:
        ref_c2 += 1
check('变更后 complete 与参照一致',
      jo4['summary']['complete'] == ref_c2,
      f"接口={jo4['summary']['complete']} 参照={ref_c2}")
check('变更后 missing 与参照一致',
      jo4['summary']['missing'] == ref_m2,
      f"接口={jo4['summary']['missing']} 参照={ref_m2}")
check('overview 冷调用在合理量级（1200 行 < 2s）', t_cold < 2.0,
      f'{t_cold*1000:.0f} ms')

# ══════════════ 收尾：无新路由 ══════════════
print('\n== 接口面 ==', flush=True)
rules = [r.rule for r in appmod.app.url_map.iter_rules()]
for p in ('/api/submit/table/<name>/sqn',
          '/api/submit/table/<name>/sqn_status',
          '/api/submit/table/<name>/taxonomy_check',
          '/api/cds/export', '/api/meta/ai_summary',
          '/api/tool/report', '/api/tool/report_data',
          '/api/seqview', '/api/align/file', '/api/align/save'):
    check(f'既有路由仍在 {p}', p in rules)
check('后台任务化未新增任何路由',
      not any(k in r for r in rules
              for k in ('async', 'bg_task', 'job_status')))

# 清理
for p in CLEAN:
    try:
        full = p if os.path.isabs(p) else os.path.join(
            ROOT, 'run', 'submissions', p)
        shutil.rmtree(full, ignore_errors=True)
    except OSError:
        pass

print('\n' + ('ALL PASS' if not FAIL else f'{len(FAIL)} FAIL: {FAIL}'), flush=True)
sys.exit(0 if not FAIL else 1)
