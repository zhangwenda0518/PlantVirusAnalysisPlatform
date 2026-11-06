# -*- coding: utf-8 -*-
"""病毒识别和分类「一键分析」· 三步链 + 目录批量 回归（virchain）。

2026-11-03 重构口径：旧步骤①病毒序列提取（识别分类）移出本链（挪入样本
处理组作独立快筛），链重编号 ①病毒组装 → ②病毒鉴定 → ③候选
序列验证，默认全选；输入只收去宿主 FASTA（FASTQ 请先到样本处理组提取）。

真实步骤都很重，这里把 `chains._tool_job_assemble/_contigs/_verify` 换成
假 job（只写关键产物），验证编排本身：

1) 工厂期校验：空 steps、② 缺 ①、③ 缺 ②、FASTQ 输入、缺输入；
2) 单文件：只跑 ① / ①② / ①②③ → 步骤按序、产物链接进汇总目录；
3) 目录批量：FASTA 目录成功；混入 FASTQ → 400 指引样本处理组；
4) 扫描接口 chain_scan(kind=virchain) 仍认 FASTA（n_fasta 计数）；
5) HTTP 侧 400 文案（只勾 ③ 验证 → 400）；
6) 卡片：三步勾选（无 vc_step4 / 识别步骤残留）、两段模式无样品库残留。
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
FA_DIR = os.path.join(ROOT, 'run', '_vcd_fa')
MIX_DIR = os.path.join(ROOT, 'run', '_vcd_mix')
for d in (FA_DIR, MIX_DIR):
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)

fails = []


def check(label, ok, extra=''):
    print('  [%s] %s%s' % ('PASS' if ok else 'FAIL', label,
                           ('  ' + str(extra)) if extra else ''))
    if not ok:
        fails.append(label)


def _write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(body)
    return path


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
    d = os.path.join(TROOT, '_vcd_chain_%s' % tag)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    return d


def _factory_aborts(params):
    try:
        chains._tool_job_virchain(_ctx(params, _mkchain('x')))
        return None
    except Exception as e:
        return getattr(e, 'code', None), str(getattr(e, 'description', e))


def _run(cdir, params):
    logs = []
    job = chains._tool_job_virchain(_ctx(params, cdir))
    out = job(logs.append, lambda k, f, m='': None, None)
    meta = json.load(open(os.path.join(cdir, '_chain.json'), encoding='utf-8'))
    return out, meta, logs


# ── 四个假步骤 job（只写关键产物；识别步骤已移出本链，无需假 identify） ──
def _fake_assemble(ctx):
    def job(log, prog, cancel):
        src = str(ctx.p.get('r1') or '')
        if 'bad' in os.path.basename(src).lower():
            raise RuntimeError('模拟组装失败')
        ctg = _write(os.path.join(str(ctx.run_dir), 'contigs.filtered.fasta'),
                     '>c1\nACGTACGTACGT\n')
        prog('assembly', 0.9, '假组装跑完')
        return {'n_contigs': 1, 'contigs': ctg, 'assembler': 'fake'}
    return job


def _fake_contigs(ctx):
    def job(log, prog, cancel):
        _write(os.path.join(str(ctx.run_dir), 'virus_classification.tsv'),
               'contig\tvirus\nc1\tX\n')
        vfa = _write(os.path.join(str(ctx.run_dir), 'viral_contigs.fasta'),
                     '>c1\nACGTACGTACGT\n')
        prog('contigs', 0.9, '假鉴定跑完')
        return {'n_contigs': 1, 'n_viral': 1, 'viral_fasta': vfa}
    return job


def _fake_verify(ctx):
    def job(log, prog, cancel):
        _write(os.path.join(str(ctx.run_dir), 'verify_summary.json'),
               '{"n_verified": 1}\n')
        prog('verify', 0.9, '假验证跑完')
        return {'n_verified': 1}
    return job


_real = {k: getattr(chains, k) for k in
         ('_tool_job_assemble', '_tool_job_contigs', '_tool_job_verify')}
chains._tool_job_assemble = _fake_assemble
chains._tool_job_contigs = _fake_contigs
chains._tool_job_verify = _fake_verify

TROOT = str(tool_runs_root())
BEFORE = set(os.listdir(TROOT))

# ── 1) 工厂期校验 ─────────────────────────────────────────────────
code, msg = _factory_aborts({'input': R1, 'steps': ''})
check('工厂：steps 为空 → 400 至少勾选一步',
      code == 400 and '至少勾选' in msg, (code, msg))
code, msg = _factory_aborts({'input': R1, 'steps': '3'})
check('工厂：③ 验证缺 ② 病毒鉴定 → 400',
      code == 400 and '一并勾选 ②' in msg, (code, msg))
code, msg = _factory_aborts({'input': R1, 'steps': '2'})
check('工厂：② 病毒鉴定缺 ① 组装 → 400',
      code == 400 and '一并勾选 ①' in msg, (code, msg))
code, msg = _factory_aborts({})
check('工厂：什么都不给 → 400', code == 400, (code, msg))
code, msg = _factory_aborts({'input': R1})
check('工厂：FASTQ 输入 → 400 指引样本处理组',
      code == 400 and 'FASTA' in msg, (code, msg))

# ── 2) 单文件：只跑 ① / ①② / ①②③ ──────────────────────────────
_write(os.path.join(FA_DIR, 'A.fasta'), '>a\nACGTACGT\n')

cdir = _mkchain('only1')
out, meta, _ = _run(cdir, {'input': os.path.join(FA_DIR, 'A.fasta'),
                           'steps': '1'})
keys = [s['key'] for s in meta.get('steps') or []]
check('单文件：只跑 ① → 只有 assembly 一步',
      keys == ['assembly'] and meta.get('mode') == 'single', (keys, meta.get('mode')))
check('单文件：① 的 contigs 链接进汇总目录',
      os.path.isfile(os.path.join(cdir, 'assembly__contigs.filtered.fasta')),
      os.listdir(cdir))

cdir = _mkchain('s12')
out, meta, _ = _run(cdir, {'input': os.path.join(FA_DIR, 'A.fasta'),
                           'steps': '1,2'})
keys = [s['key'] for s in meta.get('steps') or []]
check('单文件：①② → 两步按序', keys == ['assembly', 'contigs'], keys)
check('单文件：② 的 class/contigs 都链接进汇总目录',
      os.path.isfile(os.path.join(cdir, 'contigs__virus_classification.tsv'))
      and os.path.isfile(os.path.join(cdir, 'contigs__viral_contigs.fasta')),
      sorted(os.listdir(cdir)))
check('单文件：返回 steps/links', bool(out.get('steps')) and out.get('n_samples') == 1,
      (out.get('n_samples'), out.get('mode')))

cdir = _mkchain('s123')
out, meta, _ = _run(cdir, {'input': os.path.join(FA_DIR, 'A.fasta')})
keys = [s['key'] for s in meta.get('steps') or []]
check('单文件：默认全链 ①②③ 按序',
      keys == ['assembly', 'contigs', 'verify'], keys)
check('单文件：meta.params.steps 记录默认勾选',
      (meta.get('params') or {}).get('steps') == '1,2,3',
      meta.get('params'))
check('单文件：③ 的验证摘要链接进汇总目录',
      os.path.isfile(os.path.join(cdir, 'verify__verify_summary.json')),
      sorted(os.listdir(cdir)))

# ── 3) 目录批量：FASTA 目录成功；混入 FASTQ → 400 ─────────────────
_write(os.path.join(FA_DIR, 'B.fasta'), '>b\nACGTACGT\n')
_write(os.path.join(MIX_DIR, 'A.fasta'), '>a\nACGTACGT\n')
_write(os.path.join(MIX_DIR, 'B.fasta'), '>b\nACGTACGT\n')
if os.path.isfile(R1) and os.path.isfile(R2):
    shutil.copy(R1, os.path.join(MIX_DIR, 'C_R1.fastq.gz'))
    shutil.copy(R2, os.path.join(MIX_DIR, 'C_R2.fastq.gz'))

cdir = _mkchain('fa_batch')
out, meta, _ = _run(cdir, {'input_dir': FA_DIR})
snap = {s['name']: s for s in meta.get('samples') or []}
check('批量：FASTA 目录全部成功（默认全链）',
      meta.get('mode') == 'batch' and meta.get('n_samples') == 2
      and meta.get('ok_samples') == 2, (meta.get('n_samples'), meta.get('ok_samples')))
check('批量：每样本独立目录且产物按样本前缀命名',
      all(any(fn.endswith('assembly__contigs.filtered.fasta')
              for fn in os.listdir(os.path.join(cdir, n)))
          for n in ('A', 'B')), sorted(os.listdir(cdir)))
tsv = open(os.path.join(cdir, 'samples.tsv'), encoding='utf-8').read().strip()
check('批量：samples.tsv 表头含 steps 列、2 行样本',
      tsv.splitlines()[0].split('\t')[2:4] == ['status', 'bytes']
      and 'steps' in tsv.splitlines()[0] and len(tsv.splitlines()) == 3,
      tsv.splitlines()[0])

code, msg = _factory_aborts({'input_dir': MIX_DIR})
check('批量：混入 FASTQ → 400 指引样本处理组',
      code == 400 and 'FASTA' in msg, (code, msg))

# ── 4) 扫描接口：chain_scan 认 FASTA（口径未变） ──────────────────
if os.path.isfile(os.path.join(MIX_DIR, 'C_R1.fastq.gz')):
    d = c.post('/api/tool/chain_scan',
               json={'folder': MIX_DIR, 'kind': 'virchain'}).get_json() or {}
    check('chain_scan(kind=virchain)：认 FASTA，n_fasta=2 / 总数 3',
          d.get('n_samples') == 3 and d.get('n_fasta') == 2,
          (d.get('n_samples'), d.get('n_fasta')))

# ── 5) HTTP 侧文案 ────────────────────────────────────────────────
r = c.post('/api/tool/run', json={'tool': 'virchain',
                                  'params': {'input': R1, 'steps': '3'}})
check('HTTP：virchain 只勾 ③ 验证 → 400', r.status_code == 400,
      (r.get_json() or {}).get('error'))

# ── 6) 卡片：三步勾选、无识别步骤残留 ─────────────────────────────
_html = open(os.path.join(ROOT, 'webapp', 'templates', 'tools.html'),
             encoding='utf-8').read()
check('virchain 卡：三步勾选（无 vc_step4 / 识别步骤残留）',
      'id="vc_step4"' not in _html and 'tk.vcStepTip1' not in _html
      and _html.count('id="vc_step') == 3
      and '① 病毒序列提取' not in _html,
      _html.count('id="vc_step'))
_vc_blk = _html.split('id="t-virchain"')[-1].split('id="t-homrem"')[0]
check('virchain 卡：两段模式（单样本 / 目录批量），无样品库残留',
      'value="lib"' not in _html and 'vcLibPane' not in _html
      and 'libLoad(' not in _html and 'sample_names' not in _html
      and _html.count('name="vc_mode_sel"') == 2,
      _html.count('name="vc_mode_sel"'))
check('virchain 卡：不再有「⏩ 逐样品提交」按钮',
      'id="vc_samples"' not in _vc_blk, '')

# ── 清理 ─────────────────────────────────────────────────────────
for k, v in _real.items():
    setattr(chains, k, v)
for d in (FA_DIR, MIX_DIR):
    shutil.rmtree(d, ignore_errors=True)
for tag in ('x', 'only1', 's12', 's123', 'fa_batch'):
    shutil.rmtree(os.path.join(TROOT, '_vcd_chain_%s' % tag), ignore_errors=True)
for n in os.listdir(TROOT):
    if n not in BEFORE and any(t in n for t in
                               ('__assembly_', '__contigs_', '__verify_',
                                'assembly_', 'contigs_', 'verify_')):
        shutil.rmtree(os.path.join(TROOT, n), ignore_errors=True)

print('\n' + ('ALL PASS' if not fails else 'FAILED: %s' % fails))
sys.exit(0 if not fails else 1)
