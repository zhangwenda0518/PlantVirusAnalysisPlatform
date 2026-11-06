# -*- coding: utf-8 -*-
"""一键前处理（prepchain）「输入目录 + 批量」回归。

覆盖三件事：
1) 目录解析（扫描器纯逻辑）：`_R1` / Illumina `_R1_001` / ENA `_1` 都能配对，
   单独 FASTQ 判单端，没有 R1 的 R2 记孤儿，.sra 各自成样本；顶层无样本时
   下探一层子目录；
2) POST /api/tool/prepchain_scan 返回可用预览（计数 + 样本 + 孤儿）；
3) 真实批量运行：一目录多样本（含一个坏 FASTQ）→ 好样本各自产出 FASTA 链接，
   坏样本标记 failed 但不拖垮任务；_chain.json 带 mode/input_dir/samples[]，
   samples.tsv 落盘，汇总表经 chain_result 可读、产物经 /tool_runs/ 可下载。

宿主库这一步用空目录顶掉（db_ready=False → 自动跳过），只验批量编排本身。
"""
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.getcwd())
os.environ['VP_NO_RECOVER'] = '1'

import app as appmod
from Virus_Platform_Core.web.chains import _scan_prep_inputs
from Virus_Platform_Core.web.state import tool_runs_root

c = appmod.app.test_client()
ROOT = os.getcwd()
R1 = os.path.join(ROOT, 'examples', 'example_sim100x_R1.fastq.gz')
R2 = os.path.join(ROOT, 'examples', 'example_sim100x_R2.fastq.gz')
SRC = os.path.join(ROOT, 'run', '_pctest_src')
OUT = os.path.join(ROOT, 'run', '_pctest_out')
shutil.rmtree(SRC, ignore_errors=True)
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(SRC, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

fails = []


def check(label, ok, extra=''):
    print('  [%s] %s%s' % ('PASS' if ok else 'FAIL', label,
                           ('  ' + str(extra)) if extra else ''))
    if not ok:
        fails.append(label)


# ── 1) 扫描器纯逻辑：三种命名各配一对 + 单端 + 孤儿 + SRA ──────────
shutil.copy(R1, os.path.join(SRC, 'S1_R1.fastq.gz'))
shutil.copy(R2, os.path.join(SRC, 'S1_R2.fastq.gz'))
shutil.copy(R1, os.path.join(SRC, 'NX-5_S2_L001_R1_001.fastq.gz'))
shutil.copy(R2, os.path.join(SRC, 'NX-5_S2_L001_R2_001.fastq.gz'))
shutil.copy(R1, os.path.join(SRC, 'DRR1175147_1.fastq.gz'))
shutil.copy(R2, os.path.join(SRC, 'DRR1175147_2.fastq.gz'))
shutil.copy(R1, os.path.join(SRC, 'solo.fastq.gz'))
shutil.copy(R2, os.path.join(SRC, 'lone_R2.fastq.gz'))
open(os.path.join(SRC, 'SRR000001.sra'), 'wb').close()

scan = _scan_prep_inputs(SRC)
by_name = {s['name']: s for s in scan['samples']}
check('扫描：三种命名都配成双端（S1 / NX-5_S2 / DRR1175147）',
      all(by_name.get(n, {}).get('r2') for n in
          ('S1', 'NX-5_S2', 'DRR1175147')), sorted(by_name))
check('扫描：单独 FASTQ 判单端', bool(by_name.get('solo')) and
      not by_name['solo']['r2'])
check('扫描：.sra 各自成样本（kind=sra）',
      by_name.get('SRR000001', {}).get('kind') == 'sra')
check('扫描：无 R1 的 R2 记孤儿而不是单端',
      scan['orphan_r2'] == ['lone_R2.fastq.gz'], scan['orphan_r2'])
check('扫描：样本总数 5（4 fastq + 1 sra）', len(scan['samples']) == 5,
      len(scan['samples']))
check('扫描：样本带文件大小（r1_size / size / total_bytes）',
      by_name['solo']['r1_size'] > 0 and
      by_name['solo']['size'] == by_name['solo']['r1_size'] and
      by_name['S1']['size'] == (by_name['S1']['r1_size']
                                + by_name['S1']['r2_size']) and
      scan['total_bytes'] >= by_name['S1']['size'], scan['total_bytes'])

# 命名：`Dataset_1_R1` 的 `_1` 属于样本名，不能被当 ENA 配对标记剥掉
NAME = os.path.join(ROOT, 'run', '_pctest_name')
shutil.rmtree(NAME, ignore_errors=True)
os.makedirs(NAME, exist_ok=True)
for stem in ('Dataset_1', 'Dataset_2'):
    shutil.copy(R1, os.path.join(NAME, stem + '_R1.fastq.gz'))
    shutil.copy(R2, os.path.join(NAME, stem + '_R2.fastq.gz'))
shutil.copy(R1, os.path.join(NAME, 'SRR9_1.fastq.gz'))
shutil.copy(R2, os.path.join(NAME, 'SRR9_2.fastq.gz'))
nscan = _scan_prep_inputs(NAME)
nnames = sorted(s['name'] for s in nscan['samples'])
check('命名：Dataset_1_R1 / Dataset_2_R1 不被误剥成 Dataset',
      nnames == ['Dataset_1', 'Dataset_2', 'SRR9'], nnames)
check('命名：ENA `_1/_2` 仍按配对标记剥掉（SRR9）',
      any(s['name'] == 'SRR9' and s['r2'] for s in nscan['samples']))

# 顶层无样本文件 → 下探一层子目录
NEST = os.path.join(SRC, 'nested_proj')
os.makedirs(NEST, exist_ok=True)
shutil.copy(R1, os.path.join(NEST, 'A_R1.fq'))
shutil.copy(R2, os.path.join(NEST, 'A_R2.fq'))
SUBP = os.path.join(ROOT, 'run', '_pctest_sub')
shutil.rmtree(SUBP, ignore_errors=True)
os.makedirs(os.path.join(SUBP, 'proj'), exist_ok=True)
shutil.copy(R1, os.path.join(SUBP, 'proj', 'B_R1.fq'))
shutil.copy(R2, os.path.join(SUBP, 'proj', 'B_R2.fq'))
sub = _scan_prep_inputs(SUBP)
check('扫描：顶层无样本时下探一层子目录',
      len(sub['samples']) == 1 and sub['samples'][0]['name'] == 'B'
      and bool(sub['samples'][0]['r2']) and sub['scanned_dirs'] >= 1,
      [(s['name'], s['scanned_dirs'] if 'scanned_dirs' in s else '') for s in sub['samples']])

# 大小写不同的同名样本（Windows 目录名不敏感）不能撞到同一个产物目录
from Virus_Platform_Core.web.chains import _pc_dedupe
_dd = _pc_dedupe([{'name': 'A'}, {'name': 'a'}, {'name': 'A'}])
check('重名样本按大小写不敏感去重', [s['name'] for s in _dd] == ['A', 'a_2', 'A_3'],
      [s['name'] for s in _dd])

# ── 2) HTTP 预览接口 ────────────────────────────────────────────
r = c.post('/api/tool/prepchain_scan', json={'folder': SRC})
d = r.get_json() or {}
check('scan 接口 200', r.status_code == 200, r.status_code)
check('scan 接口计数：5 样本 / 3 双端 / 1 单端 / 1 SRA',
      d.get('n_samples') == 5 and d.get('n_paired') == 3
      and d.get('n_single') == 1 and d.get('n_sra') == 1,
      (d.get('n_samples'), d.get('n_paired'), d.get('n_single'), d.get('n_sra')))
check('scan 接口回孤儿清单', d.get('orphan_r2') == ['lone_R2.fastq.gz'],
      d.get('orphan_r2'))
check('scan 接口回样本大小（total_bytes 与逐样本 size）',
      d.get('total_bytes', 0) > 0 and
      all((s.get('size') or 0) > 0
          for s in (d.get('samples') or []) if s.get('kind') == 'fastq'),
      d.get('total_bytes'))
r = c.post('/api/tool/prepchain_scan', json={'folder': os.path.join(SRC, 'nope')})
check('scan 接口：不存在的目录 400', r.status_code == 400, r.status_code)

# ── 3) 真实批量运行（含一个坏样本，宿主去除用空库跳过）─────────────
BATCH = os.path.join(ROOT, 'run', '_pctest_batch')
shutil.rmtree(BATCH, ignore_errors=True)
os.makedirs(BATCH, exist_ok=True)
shutil.copy(R1, os.path.join(BATCH, 'P1_R1.fastq.gz'))
shutil.copy(R2, os.path.join(BATCH, 'P1_R2.fastq.gz'))
shutil.copy(R1, os.path.join(BATCH, 'Illu_S9_L001_R1_001.fastq.gz'))
shutil.copy(R2, os.path.join(BATCH, 'Illu_S9_L001_R2_001.fastq.gz'))
shutil.copy(R1, os.path.join(BATCH, 'ENA_77_1.fastq.gz'))
shutil.copy(R2, os.path.join(BATCH, 'ENA_77_2.fastq.gz'))
shutil.copy(R1, os.path.join(BATCH, 'solo.fastq.gz'))
with open(os.path.join(BATCH, 'bad_R1.fastq.gz'), 'wb') as f:
    f.write(b'this is not gzip\n')
with open(os.path.join(BATCH, 'bad_R2.fastq.gz'), 'wb') as f:
    f.write(b'this is not gzip\n')
EMPTY_DB = os.path.join(BATCH, '_empty_host_db')
os.makedirs(EMPTY_DB, exist_ok=True)

r = c.post('/api/tool/run', json={
    'tool': 'prepchain', 'threads': 2, 'out_dir': OUT,
    'params': {'input_dir': BATCH, 'db': EMPTY_DB, 'dedup': False,
               'confidence': 0, 'do_identify': False}})
check('批量创建 200', r.status_code == 200,
      r.get_json() if r.status_code != 200 else '')
d = r.get_json() or {}
run = d.get('run')
tid = d.get('task')
check('批量返回 task + run', bool(tid and run))
status, task = None, {}
deadline = time.time() + 600
while time.time() < deadline:
    task = c.get('/api/task/%s' % tid).get_json() or {}
    status = task.get('status')
    if status != 'running':
        break
    time.sleep(2)
check('批量任务完成（done）', status == 'done',
      (task.get('error') or status or '')[:120])

meta = {}
cdir = os.path.join(OUT, run or '')
cj = os.path.join(cdir, '_chain.json')
if os.path.isfile(cj):
    with open(cj, encoding='utf-8') as f:
        meta = json.load(f)
check('_chain.json：mode=batch', meta.get('mode') == 'batch', meta.get('mode'))
check('_chain.json：do_identify=false 已记录（第⑤步关闭）',
      (meta.get('params') or {}).get('do_identify') is False
      and meta.get('do_identify') is False,
      meta.get('params'))
check('_chain.json：n_samples=5 / ok=4 / failed=1',
      meta.get('n_samples') == 5 and meta.get('ok_samples') == 4
      and meta.get('failed_samples') == 1,
      (meta.get('n_samples'), meta.get('ok_samples'), meta.get('failed_samples')))
snap = {s['name']: s for s in (meta.get('samples') or [])}
check('_chain.json：坏样本标 failed、好样本 done',
      snap.get('bad', {}).get('status') == 'failed'
      and all(snap.get(n, {}).get('status') == 'done'
              for n in ('P1', 'Illu_S9', 'ENA_77', 'solo')),
      {k: v.get('status') for k, v in snap.items()})
check('_chain.json：每样本有产物目录（含 bad 的隔离目录）',
      all(os.path.isdir(os.path.join(cdir, n)) for n in
          ('P1', 'Illu_S9', 'ENA_77', 'solo')))
check('_chain.json：样本记录大小（size > 0）',
      all((snap.get(n, {}).get('size') or 0) > 0 for n in
          ('P1', 'Illu_S9', 'ENA_77', 'solo', 'bad')),
      {k: v.get('size') for k, v in snap.items()})
_tsv = os.path.join(cdir, 'samples.tsv')
_tsvtxt = (open(_tsv, encoding='utf-8').read() if os.path.isfile(_tsv) else '')
check('samples.tsv 落盘且含 5 行样本',
      bool(_tsvtxt.strip()) and len(_tsvtxt.strip().splitlines()) == 6,
      _tsv)
check('samples.tsv 表头含字节列', _tsvtxt.splitlines()[:1] == [
    'sample\ttype\tstatus\tbytes\tR1\tR2\tkept_fastq\tfasta\terror'],
    _tsvtxt.splitlines()[:1])
good = snap.get('P1', {}).get('fasta') or []
check('好样本产出 FASTA（双端各一份）', len(good) == 2, good)
check('好样本 kept reads 为空（宿主库跳过时不伪造产物）',
      not (snap.get('P1', {}).get('fastq') or []))

# 汇总接口：chain_result 支持 prepchain，并返回 samples
cr = c.get('/api/tool/chain_result?chain=prepchain&run=%s' % run).get_json() or {}
check('chain_result 支持 prepchain 且有 meta.samples',
      cr.get('run') == run and len((cr.get('meta') or {}).get('samples') or []) == 5,
      cr.get('run') is not None)
# 产物下载：/tool_runs/<run>/<sample>/<file> 命中（汇总目录里的链接）
plinks = snap.get('P1', {}).get('links') or []
check('好样本记录汇总链接（links 非空）', len(plinks) == 2, plinks)
if plinks:
    dl = c.get('/tool_runs/%s/P1/%s' % (run, plinks[0]))
    check('批量产物可下载 /tool_runs/<run>/<sample>/<file>',
          dl.status_code == 200 and len(dl.data) > 0, dl.status_code)
    dl.close()

# ── 4) 只跑勾选样本（samples 子集）与勾选名校验 ────────────────────
r = c.post('/api/tool/run', json={
    'tool': 'prepchain', 'threads': 2, 'out_dir': OUT,
    'params': {'input_dir': SRC, 'db': EMPTY_DB, 'samples': ['solo'],
               'do_identify': False}})
check('子集创建 200', r.status_code == 200,
      r.get_json() if r.status_code != 200 else '')
d2 = r.get_json() or {}
run2, tid2 = d2.get('run'), d2.get('task')
status2 = None
deadline = time.time() + 300
while time.time() < deadline:
    t2 = c.get('/api/task/%s' % tid2).get_json() or {}
    status2 = t2.get('status')
    if status2 != 'running':
        break
    time.sleep(2)
check('子集任务完成（done）', status2 == 'done', status2)
meta2 = {}
cj2 = os.path.join(OUT, run2 or '', '_chain.json')
if os.path.isfile(cj2):
    with open(cj2, encoding='utf-8') as f:
        meta2 = json.load(f)
names2 = [s['name'] for s in (meta2.get('samples') or [])]
check('子集：只跑勾选的 solo（1 个样本）',
      meta2.get('n_samples') == 1 and names2 == ['solo']
      and meta2.get('ok_samples') == 1, names2)
r = c.post('/api/tool/run', json={
    'tool': 'prepchain', 'out_dir': OUT,
    'params': {'input_dir': SRC, 'samples': ['nope']}})
check('子集：勾选名在目录里不存在 → 400', r.status_code == 400,
      (r.get_json() or {}).get('error'))
r = c.post('/api/tool/run', json={
    'tool': 'prepchain', 'out_dir': OUT,
    'params': {'input': R1, 'input_dir': SRC}})
check('单文件与目录同时填 → 400', r.status_code == 400,
      (r.get_json() or {}).get('error'))

# ── 5) 单文件模式未被目录改造破坏（产物链接在 chain 根）────────────
r = c.post('/api/tool/run', json={
    'tool': 'prepchain', 'threads': 2, 'out_dir': OUT,
    'params': {'input': R1, 'input2': R2, 'db': EMPTY_DB,
               'do_identify': False}})
check('单文件创建 200', r.status_code == 200,
      r.get_json() if r.status_code != 200 else '')
d3 = r.get_json() or {}
run3, tid3 = d3.get('run'), d3.get('task')
status3 = None
deadline = time.time() + 300
while time.time() < deadline:
    t3 = c.get('/api/task/%s' % tid3).get_json() or {}
    status3 = t3.get('status')
    if status3 != 'running':
        break
    time.sleep(2)
check('单文件任务完成（done）', status3 == 'done', status3)
meta3 = {}
cj3 = os.path.join(OUT, run3 or '', '_chain.json')
if os.path.isfile(cj3):
    with open(cj3, encoding='utf-8') as f:
        meta3 = json.load(f)
s3 = (meta3.get('samples') or [{}])[0]
check('单文件：mode=single 且 1 个样本（双端）',
      meta3.get('mode') == 'single' and s3.get('status') == 'done'
      and s3.get('pe') is True, (meta3.get('mode'), s3.get('status')))
check('单文件：FASTA 链接在 chain 根（links 2 个）',
      len(s3.get('links') or []) == 2, s3.get('links'))
if s3.get('links'):
    dl = c.get('/tool_runs/%s/%s' % (run3, s3['links'][0]))
    check('单文件产物可下载 /tool_runs/<run>/<file>', dl.status_code == 200,
          dl.status_code)
    dl.close()

# ── 6) 第⑤步病毒序列提取（识别分类）· 假 job 编排断言 ─────────────
# 2026-11-03 并入的第⑤步：默认开；宿主库空 → 宿主步骤跳过时输入退回
# 质控后 FASTQ。这里把 chains._tool_job_identify 换成假 job（只写产物），
# 验证 kept→identify 回退、viral_ 产物链接与 meta 记账。
import Virus_Platform_Core.web.chains as _chains_mod

_fake_id_state = {'ran': False}


def _fake_identify(ctx):
    def job(log, prog, cancel):
        _fake_id_state['ran'] = True
        _fake_id_state['input'] = str(ctx.p.get('input') or '')
        _fake_id_state['pe'] = bool(ctx.p.get('input2'))
        with open(os.path.join(str(ctx.run_dir), 'viral_sequences.fasta'),
                  'w', encoding='utf-8') as f:
            f.write('>v1\nACGTACGT\n')
        with open(os.path.join(str(ctx.run_dir), 'viral_ids.tsv'),
                  'w', encoding='utf-8') as f:
            f.write('id\tvirus\nv1\tX\n')
        prog('identify', 0.9, '假识别跑完')
        return {'n_classified': 1, 'n_extracted': 1}
    return job


_real_identify = _chains_mod._tool_job_identify
_chains_mod._tool_job_identify = _fake_identify
try:
    ID_RUN = os.path.join(ROOT, 'run', '_pctest_identify')
    shutil.rmtree(ID_RUN, ignore_errors=True)
    os.makedirs(ID_RUN, exist_ok=True)
    r4 = c.post('/api/tool/run', json={
        'tool': 'prepchain', 'threads': 2, 'out_dir': ID_RUN,
        'params': {'input': R1, 'input2': R2, 'db': EMPTY_DB,
                   'do_identify': True}})
    check('⑤ 创建 200', r4.status_code == 200,
          r4.get_json() if r4.status_code != 200 else '')
    d4 = r4.get_json() or {}
    run4, tid4 = d4.get('run'), d4.get('task')
    status4 = None
    deadline = time.time() + 300
    while time.time() < deadline:
        t4 = c.get('/api/task/%s' % tid4).get_json() or {}
        status4 = t4.get('status')
        if status4 != 'running':
            break
        time.sleep(2)
    check('⑤ 任务完成（done）', status4 == 'done',
          (t4.get('error') or status4 or '')[:160])
    check('⑤ 假 identify 被调用（双端输入）', _fake_id_state['ran']
          and _fake_id_state['pe'], _fake_id_state)
    cdir4 = os.path.join(ID_RUN, run4 or '')
    meta4 = {}
    cj4 = os.path.join(cdir4, '_chain.json')
    if os.path.isfile(cj4):
        with open(cj4, encoding='utf-8') as f:
            meta4 = json.load(f)
    keys4 = [st.get('key') for st in (meta4.get('steps') or [])
             if not str(st.get('key', '')).startswith('id__')]
    check('⑤ steps 含 identify 步', 'identify' in keys4, keys4)
    check('⑤ meta 记账 do_identify/identify_db_ready',
          (meta4.get('params') or {}).get('do_identify') is True,
          meta4.get('params'))
    check('⑤ viral_ 产物链接进 chain 根',
          any(fn.startswith('viral_') for fn in os.listdir(cdir4)),
          sorted(os.listdir(cdir4))[:8])
finally:
    _chains_mod._tool_job_identify = _real_identify

# 清理
for rr in (run, run2, run3):
    if rr:
        c.post('/api/tool/runs/%s/delete' % rr)
if 'run4' in dir() and run4:
    c.post('/api/tool/runs/%s/delete' % run4)
shutil.rmtree(ID_RUN, ignore_errors=True) if 'ID_RUN' in dir() else None
shutil.rmtree(SRC, ignore_errors=True)
shutil.rmtree(SRC, ignore_errors=True)
shutil.rmtree(SUBP, ignore_errors=True)
shutil.rmtree(BATCH, ignore_errors=True)
shutil.rmtree(OUT, ignore_errors=True)

print('\n' + ('ALL PASS' if not fails else 'FAILED: %s' % fails))
sys.exit(0 if not fails else 1)
