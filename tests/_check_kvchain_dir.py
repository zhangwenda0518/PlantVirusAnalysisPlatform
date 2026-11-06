# -*- coding: utf-8 -*-
"""病毒定量与共识「一键分析」输入目录批量回归（kvchain）。

真实 kvsuite 全流程要跑鉴定/过滤/共识/绘图/变异，测试里太重，这里把
`chains._tool_job_kvsuite` 换成假 job（只写 filtered.tsv / 汇总 / variant
json），直接验证「一键分析」自己的批量编排：

1) 工厂期校验：目录内 .sra 拒绝、目录与 reads 互斥、空目录 400；
2) 批量 job：逐样本建 <chain>/<样本>/、写 input.tsv、跑一步 kvsuite、
   链接三个关键产物；坏样本标 failed 但不拖垮其余样本；全失败抛 RuntimeError；
3) _chain.json / samples.tsv 字段，以及 /api/tool/chain_scan 与
   /api/tool/prepchain_scan 返回一致（两卡共用同一个解析器）。
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.getcwd())
os.environ['VP_NO_RECOVER'] = '1'

from types import SimpleNamespace

import app as appmod
import Virus_Platform_Core.web.chains as chains
from Virus_Platform_Core.web.state import tool_runs_root

c = appmod.app.test_client()
ROOT = os.getcwd()
R1 = os.path.join(ROOT, 'examples', 'example_sim100x_R1.fastq.gz')
R2 = os.path.join(ROOT, 'examples', 'example_sim100x_R2.fastq.gz')
SRC = os.path.join(ROOT, 'run', '_kvd_src')
BADONLY = os.path.join(ROOT, 'run', '_kvd_bad')
SRASONLY = os.path.join(ROOT, 'run', '_kvd_sra')
for d in (SRC, BADONLY, SRASONLY):
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)

fails = []


def check(label, ok, extra=''):
    print('  [%s] %s%s' % ('PASS' if ok else 'FAIL', label,
                           ('  ' + str(extra)) if extra else ''))
    if not ok:
        fails.append(label)


# ── 假 kvsuite job：写固定产物，样本名带 bad 就抛异常 ────────────────
def _fake_kvsuite(ctx):
    def job(log, prog, cancel):
        sheet = ctx.p.get('sample_sheet') or ''
        txt = open(sheet, encoding='utf-8').read() if sheet and os.path.isfile(sheet) else ''
        rows = [ln for ln in txt.splitlines()[1:] if ln.strip()]
        if rows and 'bad' in rows[0].split('\t')[0]:
            raise RuntimeError('模拟定量失败')
        out = os.path.join(str(ctx.run_dir), 'kvsuite')
        for rel, body in (
                ('filter/filtered.tsv', 'virus\tcov\nA\t99\n'),
                ('identify/all_viruses.summary.tsv', 'virus\treads\nA\t10\n'),
                ('variant_summary.json', '{"n_variant_genomes": 1}\n')):
            p = os.path.join(out, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'w', encoding='utf-8') as f:
                f.write(body)
        prog('kvsuite', 0.5, '假 kvsuite 跑完')
        return {'stage': 'all', 'n_confirmed': 1, 'engine': 'fake'}
    return job


real_kvsuite = chains._tool_job_kvsuite
chains._tool_job_kvsuite = _fake_kvsuite


def _ctx(params, run_dir):
    def req(k, what=''):
        v = params.get(k)
        if v in (None, ''):
            from flask import abort
            abort(400, '请选择%s' % what)
        return v

    def opt(k, default=None):
        return params.get(k) or default

    return SimpleNamespace(p=params, run_dir=run_dir, threads=1,
                           db_virus=None, req=req, opt=opt)


def _mkchain(tag):
    d = os.path.join(str(tool_runs_root()), '_kvd_chain_%s' % tag)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    return d


TROOT = str(tool_runs_root())
BEFORE = set(os.listdir(TROOT))


def _factory_aborts(params):
    try:
        chains._tool_job_kvchain(_ctx(params, _mkchain('x')))
        return None
    except Exception as e:                     # werkzeug HTTPException
        return getattr(e, 'code', None), str(getattr(e, 'description', e))


# ── 1) 目录内容与工厂期校验 ───────────────────────────────────────
shutil.copy(R1, os.path.join(SRC, 'K1_R1.fastq.gz'))
shutil.copy(R2, os.path.join(SRC, 'K1_R2.fastq.gz'))
shutil.copy(R1, os.path.join(SRC, 'solo.fastq.gz'))
shutil.copy(R1, os.path.join(BADONLY, 'bad_R1.fastq.gz'))
shutil.copy(R2, os.path.join(BADONLY, 'bad_R2.fastq.gz'))
shutil.copy(R1, os.path.join(SRASONLY, 'DRR1_1.fastq.gz'))
shutil.copy(R2, os.path.join(SRASONLY, 'DRR1_2.fastq.gz'))
open(os.path.join(SRASONLY, 'SRR000002.sra'), 'wb').close()

code, msg = _factory_aborts({'input_dir': SRASONLY})
check('工厂：目录含 .sra → 400 且提示先转 FASTQ',
      code == 400 and '转 FASTQ' in msg, (code, msg))
code, msg = _factory_aborts({'input_dir': SRC, 'reads': R1})
check('工厂：目录批量与 reads 同时给 → 400', code == 400, (code, msg))
code, msg = _factory_aborts({'input_dir': os.path.join(SRC, 'nope')})
check('工厂：目录不存在 → 400', code == 400, (code, msg))
code, msg = _factory_aborts({})
check('工厂：什么都不给 → 400', code == 400, (code, msg))

# 勾选名不存在 → 400
code, msg = _factory_aborts({'input_dir': SRC, 'samples': 'nope'})
check('工厂：勾选名不在目录里 → 400', code == 400, (code, msg))

# ── 2) 批量 job：成功样本 + 失败样本隔离 ──────────────────────────
BATCH = os.path.join(ROOT, 'run', '_kvd_batch')
shutil.rmtree(BATCH, ignore_errors=True)
os.makedirs(BATCH, exist_ok=True)
shutil.copy(R1, os.path.join(BATCH, 'K1_R1.fastq.gz'))
shutil.copy(R2, os.path.join(BATCH, 'K1_R2.fastq.gz'))
shutil.copy(R1, os.path.join(BATCH, 'solo.fastq.gz'))
shutil.copy(R1, os.path.join(BATCH, 'bad_R1.fastq.gz'))
shutil.copy(R2, os.path.join(BATCH, 'bad_R2.fastq.gz'))

cdir = _mkchain('batch')
params = {'input_dir': BATCH}
job = chains._tool_job_kvchain(_ctx(params, cdir))
logs = []
out = job(logs.append, lambda k, f, m='': None, None)
meta = json.load(open(os.path.join(cdir, '_chain.json'), encoding='utf-8'))
snap = {s['name']: s for s in meta.get('samples') or []}
check('批量：mode=batch / n_samples=3', meta.get('mode') == 'batch'
      and meta.get('n_samples') == 3, (meta.get('mode'), meta.get('n_samples')))
check('批量：成功 2 / 失败 1', meta.get('ok_samples') == 2
      and meta.get('failed_samples') == 1,
      (meta.get('ok_samples'), meta.get('failed_samples')))
check('批量：坏样本标 failed 且带错误信息',
      snap.get('bad', {}).get('status') == 'failed'
      and '模拟定量失败' in (snap.get('bad', {}).get('error') or ''),
      snap.get('bad', {}).get('status'))
check('批量：好样本 done 且记 n_confirmed',
      all(snap.get(n, {}).get('status') == 'done'
          and snap[n].get('n_confirmed') == 1 for n in ('K1', 'solo')),
      {k: v.get('status') for k, v in snap.items()})
check('批量：每样本有独立目录与 input.tsv',
      all(os.path.isfile(os.path.join(cdir, n, 'input.tsv'))
          for n in ('K1', 'solo')))
check('批量：三个关键产物都链接进样本目录',
      sorted(snap.get('K1', {}).get('links') or []) ==
      ['filtered.tsv', 'identify_summary.tsv', 'variant_summary.json'],
      snap.get('K1', {}).get('links'))
for n in ('K1', 'solo'):
    for link in snap.get(n, {}).get('links') or []:
        check('批量：%s 的 %s 链接可读' % (n, link),
              os.path.isfile(os.path.join(cdir, n, link)))
check('批量：返回 out 带 samples_tsv / total_bytes',
      bool(out.get('samples_tsv')) and out.get('total_bytes', 0) > 0)
tsv = open(os.path.join(cdir, 'samples.tsv'), encoding='utf-8').read().strip()
check('samples.tsv：表头与 3 行样本',
      tsv.splitlines()[0] ==
      'sample\ttype\tstatus\tbytes\tconfirmed\tR1\tR2\tfiles\terror'
      and len(tsv.splitlines()) == 4, tsv.splitlines()[:1])

# 全部失败 → RuntimeError（调用方把任务标红，样本记录保留）
bad_dir = _mkchain('badonly')
job2 = chains._tool_job_kvchain(_ctx({'input_dir': BADONLY}, bad_dir))
try:
    job2(lambda s='': None, lambda k, f, m='': None, None)
    check('批量：全部失败抛 RuntimeError', False)
except RuntimeError as e:
    check('批量：全部失败抛 RuntimeError', '全部失败' in str(e), str(e))

# ── 3) 扫描接口：chain_scan 与 prepchain_scan 一致 ─────────────────
a = c.post('/api/tool/chain_scan', json={'folder': SRC}).get_json() or {}
b = c.post('/api/tool/prepchain_scan', json={'folder': SRC}).get_json() or {}
check('chain_scan 200 且采样与 prepchain_scan 一致',
      a.get('n_samples') == b.get('n_samples') == 2
      and [s['name'] for s in a.get('samples') or []]
      == [s['name'] for s in b.get('samples') or []],
      (a.get('n_samples'), b.get('n_samples')))
check('chain_scan：目录为空串 → 400',
      c.post('/api/tool/chain_scan', json={}).status_code == 400)

# HTTP 侧同样拒绝 .sra 目录（走 /api/tool/run 的工厂期 abort）
r = c.post('/api/tool/run', json={
    'tool': 'kvchain', 'params': {'input_dir': SRASONLY}})
check('HTTP：kvchain 目录含 .sra → 400', r.status_code == 400,
      (r.get_json() or {}).get('error'))

# 清理（只删本次新建的 step run 目录，别碰历史运行）
chains._tool_job_kvsuite = real_kvsuite
for d in (SRC, BADONLY, SRASONLY, BATCH):
    shutil.rmtree(d, ignore_errors=True)
for n in os.listdir(TROOT):
    if n not in BEFORE and '__kvsuite_' in n:
        shutil.rmtree(os.path.join(TROOT, n), ignore_errors=True)
for tag in ('batch', 'badonly', 'x'):
    shutil.rmtree(os.path.join(TROOT, '_kvd_chain_%s' % tag),
                  ignore_errors=True)

print('\n' + ('ALL PASS' if not fails else 'FAILED: %s' % fails))
sys.exit(0 if not fails else 1)
