# -*- coding: utf-8 -*-
"""一键流程「规范报告」（材料 / 方法 / 结果）回归。

不跑真流程：在 tool_runs 下造三个最小 _chain.json 目录（prepchain 批量
含一个失败样本、kvchain 批量含一条鉴定命中、virchain 单样本），直接验证：

1) collect_chain / build_chain_report：章节齐全、样本/参数/命中/缺口进模型，
   mtime 未变时复用、force 重建；
2) GET /api/tool/chain_report：200 text/html、download=1 附件、坏运行名 400、
   非链目录 404；/api/tool/chain_result 的 report_exists 跟随生成；
3) MCP vp_build_report 对 _chain.json 目录自动走规范报告分支。
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.getcwd())
os.environ['VP_NO_RECOVER'] = '1'

import app as appmod
from Virus_Platform_Core.chain_report import (OUT_NAME, build_chain_report,
                                              collect_chain)
from Virus_Platform_Core.web.state import tool_runs_root

c = appmod.app.test_client()
ROOT = os.getcwd()
TROOT = tool_runs_root()
PREP = os.path.join(TROOT, 'prepchain_cr_demo')
KV = os.path.join(TROOT, 'kvchain_cr_demo')
VIR = os.path.join(TROOT, 'virchain_cr_demo')

for d in (PREP, KV, VIR):
    shutil.rmtree(d, ignore_errors=True)

fails = []


def check(label, ok, extra=''):
    print('  [%s] %s%s' % ('PASS' if ok else 'FAIL', label,
                           ('  ' + str(extra)) if extra else ''))
    if not ok:
        fails.append(label)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


def _meta(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# ── 1) prepchain 批量：一个成功、一个失败 ───────────────────────────
os.makedirs(os.path.join(PREP, 'S1'), exist_ok=True)
_write(os.path.join(PREP, 'S1', 'S1__fastp_R1.fastq.gz'), 'x' * 4096)
_write(os.path.join(PREP, 'S1', 'S1.kept.fastq.gz'), 'x' * 2048)
_write(os.path.join(PREP, 'samples.tsv'),
       'sample\ttype\tstatus\tbytes\tR1\tR2\tkept_fastq\tfasta\terror\n'
       'S1\t双端\tdone\t12345678\tS1_R1.fastq.gz\tS1_R2.fastq.gz\t'
       'S1/S1.kept.fastq.gz\tS1/S1.fasta\t\n'
       'S2\t单端\tfailed\t3456789\tS2.fastq.gz\t\t\t\t质控未产出 fastp_R1\n')
_meta(os.path.join(PREP, '_chain.json'), {
    'chain': 'prepchain', 'run': 'prepchain_cr_demo', 'label': '样本处理 一键前处理',
    'mode': 'batch', 'input_dir': 'E:/demo', 'n_samples': 2,
    'host_db_ready': True, 'fastp_available': True, 'done': True,
    'created': '2026-11-02 18:00:00',
    'params': {'confidence': 0.5, 'dedup': True, 'host_db': '（平台主库）',
               'host_db_ready': True, 'fastp_available': True},
    'steps': [{'key': 's1__fastp', 'run': 's1__fastp_20261102',
               'title': 'S1 · fastp 质控',
               'summary': {'dedup': True, 'q30': 0.93,
                           'out': 'G:/x/y/z/very/long/path/fastp'}}],
    'samples': [
        {'name': 'S1', 'kind': 'fastq', 'pe': True, 'r1': 'S1_R1.fastq.gz',
         'r2': 'S1_R2.fastq.gz', 'size': 12345678, 'status': 'done',
         'links': ['S1__fastp_R1.fastq.gz', 'S1.kept.fastq.gz']},
        {'name': 'S2', 'kind': 'fastq', 'pe': False, 'r1': 'S2.fastq.gz',
         'r2': '', 'size': 3456789, 'status': 'failed',
         'error': '质控未产出 fastp_R1', 'links': []},
    ]})

model = collect_chain(PREP)
check('prep 模型样本数', model['n_samples'] == 2, model['n_samples'])
check('prep 成功/失败计数', (model['n_ok'], model['n_failed']) == (1, 1),
      (model['n_ok'], model['n_failed']))
check('prep 缺口含失败样本', any('S2' in g for g in model['gaps']))
check('prep 步骤摘要不含长路径',
      'out=' not in (model['steps'][0]['summary'] or ''),
      model['steps'][0]['summary'])
check('prep 产物体积采集', any(f['size'] == 4096
                              for f in model['samples'][0]['files']))

out = build_chain_report(PREP)
html = open(out['html'], encoding='utf-8').read()
check('prep 报告落盘', os.path.isfile(os.path.join(PREP, OUT_NAME)))
check('prep 报告 5 章 + 章节标题',
      all(x in html for x in ('一、材料', '二、方法', '三、结果',
                              '四、产物与可追溯', '五、局限与口径')))
check('prep 报告含样本名与失败原因', 'S1' in html and '质控未产出' in html)
check('prep 报告含参数节', '2.3 参数' in html and 'confidence' in html)
check('prep 报告含失败样本处置', '失败' in html)
check('prep 报告体积', os.path.getsize(out['html']) > 8000,
      os.path.getsize(out['html']))

out2 = build_chain_report(PREP)
check('未变化时复用报告', bool(out2.get('reused')))
out3 = build_chain_report(PREP, force=True)
check('force 强制重建', not out3.get('reused'))

# ── 2) kvchain：一条鉴定命中 + 深度图 ───────────────────────────────
os.makedirs(os.path.join(KV, 'K1'), exist_ok=True)
_write(os.path.join(KV, 'K1', 'identify_summary.tsv'),
       'Sample\tAccession\tLength\ttaxid\tSpecies\tCoverage(%)\tMeanDepth\n'
       'K1\tNC_000001.1\t8000\t12345\tTomato spotted wilt virus\t99.5\t120.3\n'
       'K1\tNC_000002.1\t7000\t23456\tPepper mild mottle virus\t88.1\t45.0\n')
_write(os.path.join(KV, 'K1', 'filtered.tsv'), 'Sample\tAccession\nK1\tNC_000001.1\n')
_meta(os.path.join(KV, '_chain.json'), {
    'chain': 'kvchain', 'run': 'kvchain_cr_demo', 'label': '病毒定量与共识 一键流程',
    'mode': 'batch', 'input_dir': 'E:/demo', 'n_samples': 1, 'done': True,
    'created': '2026-11-02 18:10:00',
    'params': {'engine': 'salmon', 'min_cov': 10, 'min_depth': 0.5},
    'steps': [{'key': 'K1__kvsuite', 'run': 'K1__kvsuite_20261102',
               'title': 'K1 · 已知病毒识别与定量（全流程）',
               'summary': {'stage': 'all', 'engine': 'salmon',
                           'n_confirmed': 2}}],
    'samples': [{'name': 'K1', 'kind': 'fastq', 'pe': True,
                 'r1': 'K1_R1.fastq.gz', 'r2': 'K1_R2.fastq.gz',
                 'size': 7000000, 'status': 'done',
                 'links': ['identify_summary.tsv', 'filtered.tsv']}]})

model = collect_chain(KV)
check('kv 命中解析', len(model['hits']) == 2, len(model['hits']))
check('kv 物种汇总', model['species_n'] == 2, model['species_n'])
out = build_chain_report(KV)
html = open(out['html'], encoding='utf-8').read()
check('kv 报告含检出病毒表', 'Tomato spotted wilt virus' in html)
check('kv 报告含物种汇总', '物种汇总' in html)
check('kv 报告含参数（engine=salmon）', 'salmon' in html)
check('kv 报告含覆盖度列', '覆盖' in html)

# ── 3) virchain 单样本（无 samples[] → 单样本兼容） ─────────────────
os.makedirs(VIR, exist_ok=True)
_meta(os.path.join(VIR, '_chain.json'), {
    'chain': 'virchain', 'run': 'virchain_cr_demo', 'label': '病毒识别和分类分析 一键流程',
    'done': True, 'created': '2026-11-02 18:20:00',
    'run_identify': '1',
    'params': {'run_identify': '1', 'mode': 'single', 'assembler': 'spades',
               'memory_gb': 8, 'min_len': 500, 'confidence': 0.5,
               'host': True, 'combine': True, 'methods': 'minibwa, salmon',
               'do_verify': True},
    'steps': [{'key': 'identify', 'run': 'identify_20261102',
               'title': '已知病毒识别', 'summary': {'n_confirmed': 3}}],
    'samples': []})
model = collect_chain(VIR)
check('vir 单样本兼容', model['n_samples'] == 1 and model['mode'] == 'single')
out = build_chain_report(VIR)
html = open(out['html'], encoding='utf-8').read()
check('vir 报告含装配参数', 'spades' in html and '500' in html)

# ── 4) HTTP 端点 ────────────────────────────────────────────────────
r = c.get('/api/tool/chain_report?run=prepchain_cr_demo')
check('端点 200 text/html', r.status_code == 200
      and 'text/html' in (r.headers.get('Content-Type') or ''), r.status_code)
check('端点正文含材料章', '材料' in r.get_data(as_text=True))
r = c.get('/api/tool/chain_report?run=prepchain_cr_demo&download=1')
check('download=1 走附件',
      'attachment' in (r.headers.get('Content-Disposition') or ''),
      r.headers.get('Content-Disposition'))
r = c.get('/api/tool/chain_report?run=../etc')
check('坏运行名 400', r.status_code == 400, r.status_code)
r = c.get('/api/tool/chain_report?run=prepchain_missing')
check('非链目录 404', r.status_code == 404, r.status_code)
r = c.get('/api/tool/chain_result?chain=prepchain&run=prepchain_cr_demo')
check('chain_result 报 report_exists', r.status_code == 200
      and (r.get_json() or {}).get('report_exists') is True, r.status_code)

# ── 5) MCP vp_build_report 自动分流 ─────────────────────────────────
import mcp_server as M                                     # noqa: E402
res = M._build_report_impl('prepchain_cr_demo', 'auto', False, 0)
robj = (res.get('result') or {})
check('MCP 走链报告分支', robj.get('kind') == 'chain'
      and robj.get('chain') == 'prepchain', robj.get('kind'))
check('MCP 结果含 html 路径', bool(robj.get('html')))

# ── 清理 ────────────────────────────────────────────────────────────
for d in (PREP, KV, VIR):
    shutil.rmtree(d, ignore_errors=True)

print()
if fails:
    print('FAILED %d/%d' % (len(fails), len(fails)))
    for f in fails:
        print('  -', f)
    sys.exit(1)
print('ALL PASS')
