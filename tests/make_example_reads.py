# -*- coding: utf-8 -*-
"""【已退役 2026-11-03】生成旧示例测序数据 example_R1/R2.fastq.gz。

平台内置示例 reads 已改用 examples/example_sim100x_R1/R2.fastq.gz（多病毒
混染模拟），本脚本产物不再随包分发；脚本保留备查（确定性、可重跑），如需
复现旧合成对可直接运行。以下为原始说明。

为什么单独造一份：tests/syn_R1/R2.fastq.gz 由 make_synthetic.py 生成时只
匹配到 1 个病毒（且其注释把 NC_003530 误标为 Pepper mild mottle virus，
实际是 Carnation ringspot virus），作为「病毒识别/组装/定量」示例偏单薄。

本脚本从平台病毒参考库（databases/virusref_db/final.cluster.ref.fasta）取 3 个不同科的
植物病毒完整基因组，模拟 Illumina 双端 reads（150bp，插入 ~300bp，Q22-39）：

    NC_077216.1  Pepper yellows virus      Solemoviridae   6096 bp  ← 与
                 example_contig_1 同种，保证示例之间能互相印证
    NC_076132.1  Potexvirus colombiense    Alphaflexiviridae 5419 bp
    NC_077008.1  Tymovirus naranjillae     Tymoviridae     6245 bp

三者在参考库与 kunpeng 植物病毒库中均存在 → 分类 / 定量 / 共识示例都能命中。
确定性生成（固定 seed），重跑幂等；用法: python tests/make_example_reads.py
"""
import gzip
import os
import random
import sys
# 控制台编码兜底：Windows 默认代码页是 GBK，本脚本的 ✔/✘/⚠ 等字符会让
# print 抛 UnicodeEncodeError（2026-09-11 实测多处踩过）。只改错误处理为
# replace（编码不动，中文照常可读），编不出的字符降级为 '?'。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Virus_Platform_Core.utils import iter_fasta  # noqa: E402

WANTED = {
    'NC_077216.1': 'Pepper yellows virus',
    'NC_076132.1': 'Potexvirus colombiense',
    'NC_077008.1': 'Tymovirus naranjillae',
}
READ_LEN = 150
INSERT_MEAN, INSERT_SD = 300, 60
N_PAIRS_PER_GENOME = 1200
ERROR_RATE = 0.001
COMP = str.maketrans('ACGTN', 'TGCAN')


def revcomp(s):
    return s.translate(COMP)[::-1]


def mutate(s):
    if random.random() >= ERROR_RATE * len(s):
        return s
    l = list(s)
    for i in range(len(l)):
        if random.random() < ERROR_RATE:
            l[i] = random.choice('ACGT')
    return ''.join(l)


def fake_qual():
    """Phred+33 Q22-Q39（贴近真实 Illumina；SPAdes 判定 offset 要求出现 <59 的字符）。"""
    return ''.join(chr(random.randint(55, 72)) for _ in range(READ_LEN))


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ref = os.path.join(root, 'databases', 'virusref_db',
                       'final.cluster.ref.fasta')
    ex = os.path.join(root, 'examples')
    os.makedirs(ex, exist_ok=True)
    out1 = os.path.join(ex, 'example_R1.fastq.gz')
    out2 = os.path.join(ex, 'example_R2.fastq.gz')

    genomes = {}
    for h, s in iter_fasta(ref):
        acc = h.split()[0]
        if acc in WANTED and acc not in genomes:
            genomes[acc] = s.upper()
    missing = sorted(set(WANTED) - set(genomes))
    if missing:
        print(f'  ✗ 参考库缺少: {missing}（先确认 databases/virusref_db/final.cluster.ref.fasta）')
        return 1
    print('  找到病毒基因组:', {a: len(s) for a, s in genomes.items()})

    random.seed(2026)
    n = 0
    with gzip.open(out1, 'wt', compresslevel=6) as w1, \
            gzip.open(out2, 'wt', compresslevel=6) as w2:
        for acc, g in genomes.items():
            for i in range(N_PAIRS_PER_GENOME):
                ins = max(READ_LEN * 2, int(random.gauss(INSERT_MEAN, INSERT_SD)))
                if len(g) <= ins:
                    start = random.randint(0, len(g) - 1)
                    frag = (g[start:] + g[:start])[:ins] if ins <= len(g) else g
                else:
                    start = random.randint(0, len(g) - ins)
                    frag = g[start:start + ins]
                r1 = mutate(frag[:READ_LEN])
                r2 = mutate(revcomp(frag[-READ_LEN:]))
                name = f'EX_{acc}_{i}'
                w1.write(f'@{name} 1:N:0:example\n{r1}\n+\n{fake_qual()}\n')
                w2.write(f'@{name} 2:N:0:example\n{r2}\n+\n{fake_qual()}\n')
                n += 1
    print(f'  生成 {n:,} 对模拟 reads -> examples/example_R{{1,2}}.fastq.gz')
    for p in (out1, out2):
        print(f'    {os.path.basename(p):26s} {os.path.getsize(p) / 1024:.0f} KB')

    # 覆盖度核对
    for acc, g in genomes.items():
        cov = N_PAIRS_PER_GENOME * READ_LEN * 2 / len(g)
        print(f'    {acc}  {WANTED[acc]:<24} {len(g):>6} bp  覆盖 ~{cov:.0f}x')
    return 0


if __name__ == '__main__':
    sys.exit(main())
