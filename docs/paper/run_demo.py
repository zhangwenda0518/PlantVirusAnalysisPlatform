# -*- coding: utf-8 -*-
"""为论文生成真实、可复现的演示数据（不编造任何数字）。

实验 A：平台 align_qc 模块对捆绑示例 example_recomb_set.fasta（10 条 CMV RNA3，
等长 2292 nt）执行双模式预筛（默认 / 显式参考），并计算全对 SDT 口径 identity 矩阵。
实验 B：捆绑 minibwa 对 8,464 条参考库建索引并比对捆绑双端示例 reads，计时。
"""
import os, sys, time, platform, ctypes, tempfile, shutil, subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)
from Virus_Platform_Core.align_qc import qc_alignment, pairwise_identity, iter_fasta

OUT = os.path.join(os.path.dirname(__file__), 'demo_results')
os.makedirs(OUT, exist_ok=True)
lines = []
def log(s=''):
    print(s); lines.append(str(s))

# ---------- 硬件环境 ----------
lines_h = []
lines_h.append(f"OS = {platform.system()} {platform.release()} {platform.version()}")
lines_h.append(f"Python = {platform.python_version()}")
lines_h.append(f"CPU = {platform.processor()} | logical cores = {os.cpu_count()}")
try:
    k = ctypes.windll.kernel32

    class MEM(ctypes.Structure):
        _fields_ = [('v', ctypes.c_ulonglong)]
    k.GetPhysicallyInstalledSystemMemory(ctypes.byref(MEM()))
    lines_h.append(f"RAM_GB = {MEM.v / 1024**3:.1f}")
except Exception as e:
    lines_h.append(f"RAM unknown: {e}")
open(os.path.join(OUT, 'environment.txt'), 'w', encoding='utf-8').write('\n'.join(lines_h))
log('== environment ==');  [log(l) for l in lines_h]; log()

# ---------- 实验 A：align_qc + identity 矩阵 ----------
fasta = os.path.join(ROOT, 'examples', 'example_recomb_set.fasta')
recs = [(h, s.upper()) for h, s in iter_fasta(fasta)]
ids = [h.split('|')[0] for h, _ in recs]

# A1: 默认模式（无显式参考 → identity 判据关闭）
r_default = qc_alignment(fasta, os.path.join(OUT, 'qc_default'))
log('== align_qc default mode (no explicit reference) ==')
log(f"n_in={r_default['n_in']} keep={r_default['n_keep']} removed={r_default['n_removed']} "
    f"ref={r_default['ref_id']} ref_len={r_default['ref_len']} identity_checked={r_default['identity_checked']}")
log()

# A2: 显式参考 NC_001440.1（identity 判据开启，阈值 98%）
r_explicit = qc_alignment(fasta, os.path.join(OUT, 'qc_explicit'), reference_id='NC_001440.1')
log('== align_qc explicit-reference mode (identity gate 98%) ==')
log(f"n_in={r_explicit['n_in']} keep={r_explicit['n_keep']} removed={r_explicit['n_removed']} "
    f"identity_checked={r_explicit['identity_checked']}")
for row in r_explicit['removed']:
    log(f"  REMOVE {row['id']} identity={row['identity_to_ref']} reason={row['reason']}")
log()

# A3: 全对 SDT identity 矩阵（真实计算）
n = len(recs)
M = [[0.0] * n for _ in range(n)]
for i in range(n):
    for j in range(i + 1, n):
        M[i][j] = M[j][i] = round(pairwise_identity(recs[i][1], recs[j][1]), 2)
with open(os.path.join(OUT, 'identity_matrix.csv'), 'w', encoding='utf-8') as f:
    f.write(',' + ','.join(ids) + '\n')
    for i in range(n):
        f.write(ids[i] + ',' + ','.join(str(x) for x in M[i]) + '\n')
ref_row = M[0]
others = [x for j, x in enumerate(ref_row) if j != 0]
log('== pairwise SDT identity vs NC_001440.1 (RefSeq) ==')
log('min=%.2f max=%.2f mean=%.2f' % (min(others), max(others), sum(others) / len(others)))
tri = [M[i][j] for i in range(n) for j in range(i + 1, n)]
log('all-pairs: min=%.2f max=%.2f mean=%.2f' % (min(tri), max(tri), sum(tri) / len(tri)))
log()

# ---------- 实验 B：minibwa 基准 ----------
bin_mb = os.path.join(ROOT, '3rd', 'bin', 'minibwa.exe')
bin_st = os.path.join(ROOT, '3rd', 'tools', 'samtools', 'bin', 'samtools.exe')
ref = os.path.join(ROOT, 'databases', 'virusref_db', 'kv_index', 'reference.fasta')
r1 = os.path.join(ROOT, 'examples', 'example_R1.fastq.gz')
r2 = os.path.join(ROOT, 'examples', 'example_R2.fastq.gz')

nr = 0
with open(r2.replace('R2', 'R1'), 'rb') as f:  # count via gzip
    import gzip
    with gzip.open(r1, 'rt') as g:
        for i, _ in enumerate(g):
            pass
        nr = (i + 1) // 4
log('== minibwa benchmark ==')
log(f'reads = {nr} pairs | reference = 8464 seqs / 32.5 MB')

work = os.path.join(OUT, 'bench_work')
os.makedirs(work, exist_ok=True)
ref_local = os.path.join(work, 'reference.fasta')
shutil.copy(ref, ref_local)

t0 = time.time()
p = subprocess.run([bin_mb, 'index', ref_local], capture_output=True, text=True)
t_idx = time.time() - t0
log(f'index_build_s = {t_idx:.2f} (rc={p.returncode})')

sam = os.path.join(work, 'aln.sam')
t0 = time.time()
p = subprocess.run([bin_mb, 'mem', '-t', '8', ref_local, r1, r2, '-o', sam],
                   capture_output=True, text=True)
t_aln = time.time() - t0
mapped = 0
for ln in open(sam, encoding='utf-8', errors='ignore'):
    if ln.startswith('@'):
        continue
    flag = int(ln.split('\t')[1])
    if not (flag & 4):
        mapped += 1
log(f'align_s = {t_aln:.2f} (rc={p.returncode}) | aligned_records (not unmapped) = {mapped} of {nr*2}')
log(f'rate = {nr / t_aln:.0f} read-pairs/s at t=8')
shutil.rmtree(work, ignore_errors=True)

open(os.path.join(OUT, 'demo_log.txt'), 'w', encoding='utf-8').write('\n'.join(lines))
log('\nDONE — outputs in', OUT)
