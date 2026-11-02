# -*- coding: utf-8 -*-
"""平台工具链全流程评测（Bari sRNA 测试数据）：
  ① 病毒识别分类与提取（kvsuite identify+filter → 抽取病毒 reads）
  ② 提取序列组装（SPAdes srna 模式，对提取的病毒 reads）
  ③ 组装结果再鉴定（kunpeng 物种分类 → virus_classification.tsv）
  ④ 候选序列验证（DIAMOND blastx + CDD 结构域 + 类病毒 blastn）

每阶段独立计时，整树峰值内存采样。用法:
  python run_chain_bench.py <out_root>
结束打印 RESULT:{...}
"""
import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, REPO)

# kv_stage/assembly 的输出走 check_path(in_platform=True)：必须在平台根内。
# 用平台内 scratch，评测结束自动清理（关键产物已提取进 RESULT JSON）。
OUT_ROOT = os.path.join(REPO, '_zz_bench_chain')
BARI = r'E:\谷歌下载\测试数据验证\Raw-data-Bari.Workshop'
VM = r'E:\谷歌下载\测试数据验证'
BARI_TRUTH = {
    'S3': ['Potato virus X', 'Potato virus B'],
    'S9': ['Grapevine leafroll-associated virus 1', 'Grapevine virus A',
           'Grapevine virus B', 'Grapevine rupestris stem pitting-associated virus',
           'Grapevine red globe virus', 'Grapevine rupestris vein feathering virus',
           'Grapevine Syrah virus 1', 'Hop stunt viroid',
           'Grapevine yellow speckle viroid 1'],
}
VM_TRUTH = json.load(open(os.path.join(HERE, 'truth_viromock.json'),
                          encoding='utf-8'))
# 与 compute_and_plot.py 相同的 ICTV 改名别名
VM_ALIAS = {
    '11': ['pepino mosaic virus', 'potexvirus pepini'],
    '12': ['african cassava mosaic virus', 'cassava mosaic virus', 'begomovirus manihotis'],
    '13': ['banana streak'],
    '14': ['potato virus y', 'potyvirus yituberosi'],
    '15': ['eggplant mottled dwarf', 'alphanucleorhabdovirus melongenae'],
    '16': ['bell pepper endornavirus', 'bell pepper alphaendornavirus'],
    '17': ['little cherry virus 1'],
    '18': ['barley yellow dwarf', 'luteovirus pavhordei'],
}
# D1-D10 分组真值（含 ICTV 改名，与 compute_and_plot.D110_GROUPS 同源）
D110_GROUPS = json.load(open(os.path.join(HERE, 'truth_viromock_d110_groups.json'),
                             encoding='utf-8'))

# 全部链式样本：常规 reads（D1-D18）走 metaviral（单端自动降 rna）+200bp；
# Bari 小RNA走 srna + 60bp
CHAINS = [{'name': 'S3', 'r1': os.path.join(BARI, 'Sample_3.fastq'), 'r2': None,
           'mode': 'srna', 'min_len': 60, 'kind': 'bari'},
          {'name': 'S9', 'r1': os.path.join(BARI, 'Sample_9.fastq'), 'r2': None,
           'mode': 'srna', 'min_len': 60, 'kind': 'bari'}]
for d in range(1, 19):
    single = d in (5, 6, 10)
    r1 = os.path.join(VM, f'Dataset_{d}.fastq.gz') if single else \
        os.path.join(VM, f'Dataset_{d}_R1.fastq.gz')
    r2 = None if single else os.path.join(VM, f'Dataset_{d}_R2.fastq.gz')
    if d >= 11:
        groups = [{'name': VM_TRUTH[str(d)]['species'], 'al': VM_ALIAS[str(d)]}]
    else:
        groups = D110_GROUPS[str(d)]
    CHAINS.append({'name': f'D{d}', 'r1': r1, 'r2': r2,
                   'mode': 'rnaviral', 'min_len': 200, 'kind': 'vm',
                   'groups': groups})


def norm(s):
    import re
    return re.sub(r'[^a-z0-9 ]', '', (s or '').lower()).strip()


def match_species(name, truth):
    n = norm(name)
    return next((t for t in truth if norm(t) in n or n in norm(t)), None)


class Log:
    def __getattr__(self, attr):
        def _p(*a, **k):
            print(*a, flush=True)
        return _p


def main():
    os.makedirs(OUT_ROOT, exist_ok=True)
    peak = {'rss': 0}

    def _mon():
        import psutil
        p = psutil.Process()
        while not stop.is_set():
            try:
                t = p.memory_info().rss
                for c in p.children(recursive=True):
                    try:
                        t += c.memory_info().rss
                    except (psutil.Error, OSError):
                        pass
                peak['rss'] = max(peak['rss'], t)
            except (psutil.Error, OSError):
                pass
            time.sleep(0.5)

    stop = threading.Event()
    threading.Thread(target=_mon, daemon=True).start()

    from Virus_Platform_Core.config import get_config
    from Virus_Platform_Core import kv_stage, assembly, kunpeng, verify as verify_mod
    from Virus_Platform_Core.contig_annot import classify_rows, genus_avg_map, RANKS
    from Virus_Platform_Core.utils import safe_open, write_fasta_record, iter_fasta
    logger = Log()
    db_virus = get_config().databases['virus']

    # 断点续跑：已成功的样本跳过
    res_path = os.path.join(HERE, 'results', 'chain_results.json')
    results = {}
    if os.path.isfile(res_path):
        prev = json.load(open(res_path, encoding='utf-8'))
        results = {r['sample']: r for r in prev.get('samples', {}).values()
                   if not r.get('error')}
    done = set(results)
    todo = [c for c in CHAINS if c['name'] not in done]
    print(f'共 {len(CHAINS)} 链，已完成 {len(done)}，待跑 {len(todo)}', flush=True)
    for chain in todo:
      try:
        sample = chain['name']
        r1, r2 = chain['r1'], chain.get('r2')
        mode, min_len = chain['mode'], chain['min_len']
        groups = chain['groups']
        out = os.path.join(OUT_ROOT, sample)
        os.makedirs(out, exist_ok=True)
        sres = {'sample': sample, 'kind': chain['kind'],
                'mode': mode, 'min_len': min_len}

        # ① 识别+定量+提取
        t0 = time.time()
        s1 = kv_stage.run_kvsuite_stage(
            os.path.join(out, '02b'), r1, r2, sample=sample, threads=2,
            engine='salmon', need_viral_reads=True, logger=logger)
        sres['stage1_identify_extract_s'] = round(time.time() - t0, 1)
        sres['viral_reads'] = (s1.get('stats') or {}).get('viral_reads')
        conf_tsv = os.path.join(out, '02b', 'identify',
                                'all_viruses.best.summary.tsv')
        confirmed = []
        if os.path.isfile(conf_tsv):
            import csv
            with open(conf_tsv, encoding='utf-8') as f:
                confirmed = [row['Species'] for row in csv.DictReader(f,
                            delimiter='\t') if row.get('Species')]
        sres['stage1_confirmed'] = confirmed
        viral_r1 = s1.get('viral_r1')
        viral_r2 = s1.get('viral_r2')
        viral_se = s1.get('viral_se')
        if not viral_r1 or not os.path.isfile(viral_r1):
            raise RuntimeError(
                f"{sample}: ②b 未产出病毒 reads（viral_r1={viral_r1!r}）——"
                "鉴定/抽取失败，详见 02b/kvsuite.log")

        # ② 提取序列组装（模式/门槛按数据类型：小RNA srna+60，常规 metaviral+200）
        t0 = time.time()
        sp = assembly.run_spades(viral_r1, viral_r2, os.path.join(out, '03_assembly'),
                                 mode=mode, threads=4, memory_gb=8,
                                 extra_se=viral_se)
        contigs_fa = os.path.join(out, '03_assembly', 'contigs.filtered.fasta')
        assembly.filter_contigs(sp, contigs_fa, min_len=min_len, logger=logger)
        sres['stage2_assemble_s'] = round(time.time() - t0, 1)
        n_contigs = sum(1 for _ in open(contigs_fa, encoding='utf-8')
                        if _.startswith('>'))
        sres['stage2_contigs_ge_minlen'] = n_contigs

        # ③ 组装结果再鉴定（kunpeng 物种分类 → 谱系表 + viral_contigs.fasta）
        t0 = time.time()
        cls_out = os.path.join(out, '03_assembly', 'contig_classify')
        res = kunpeng.classify(db_virus, [contigs_fa], cls_out, paired=False,
                               threads=4, logger=logger)  # kunpeng 断言 n_threads>2
        contig_tax = {}
        if res.get('kraken'):
            from Virus_Platform_Core.kunpeng import parse_classify_output
            for flag, cid, taxid, _len, _path in parse_classify_output(res['kraken']):
                contig_tax[cid] = (flag, taxid)
        try:
            genus_map = genus_avg_map(logger=logger)
        except Exception:
            genus_map = {}
        cls_rows = classify_rows(res.get('kraken'), genus_map)
        header = (['contig', 'taxid', 'taxon'] + list(RANKS)
                  + ['length', 'genus_avg_len', 'ratio', 'near_complete',
                     'score', 'kmer_support', 'kmer_total'])
        with safe_open(os.path.join(out, 'virus_classification.tsv'), 'wt') as f:
            f.write('\t'.join(header) + '\n')
            for r in cls_rows:
                f.write('\t'.join(str(r.get(k, '')) for k in header) + '\n')
        viral_fa = os.path.join(out, 'viral_contigs.fasta')
        from Virus_Platform_Core.kunpeng import KreportTree, parse_kreport
        tree = KreportTree(parse_kreport(res.get('kreport'))) if res.get('kreport') else None
        reid_species = set()
        with safe_open(viral_fa, 'wt') as vf:
            for header_, seq in iter_fasta(contigs_fa):
                cid = header_.split()[0]
                flag, taxid = contig_tax.get(cid, ('-', 0))
                if flag == 'C' and taxid > 0:
                    write_fasta_record(vf, cid, seq)
                    sp_name = tree.name(taxid) if tree else ''
                    if sp_name:
                        reid_species.add(sp_name)
        sres['stage3_reidentify_s'] = round(time.time() - t0, 1)
        sres['stage3_reid_species'] = sorted(reid_species)
        sres['stage3_viral_contigs'] = sum(1 for _ in open(viral_fa, encoding='utf-8')
                                           if _.startswith('>'))

        # ④ 候选序列验证（blastx + CDD + 类病毒 blastn）
        t0 = time.time()
        vsum = verify_mod.verify(out, viral_fa, host='all',
                                 methods=('blastx', 'cdd'), combine='union',
                                 threads=2, logger=logger,
                                 assembly_dir=out)
        sres['stage4_verify_s'] = round(time.time() - t0, 1)
        sres['stage4_verify'] = {k: v for k, v in (vsum or {}).items()
                                 if isinstance(v, (int, float, str, list))}
        # ④ 的物种判定（验证明细里通常带 best-hit 物种）
        vdir = os.path.join(out, 'verify')
        v_species = set()
        for fn in os.listdir(vdir):
            if fn.endswith('.tsv') and 'blastx' in fn:
                import csv
                with open(os.path.join(vdir, fn), encoding='utf-8') as f:
                    for row in csv.DictReader(f, delimiter='\t'):
                        sp_name = (row.get('Species') or row.get('species')
                                   or row.get('stitle') or '')
                        if sp_name:
                            v_species.add(sp_name.split()[0])
        sres['stage4_species_preview'] = sorted(v_species)[:10]

        # ── 链级检出评估（①确诊 ∪ ③再鉴定，按真值物种去重）──
        detected, fps = set(), {}
        for sp_name in list(confirmed) + sorted(reid_species):
            n = norm(sp_name)
            hit = None
            for g in groups:
                gn = norm(g['name'])
                if gn in n or n in gn:
                    hit = g['name']
                    break
                for a in g.get('al', []):
                    an = norm(a)
                    if an and (an in n or n in an):
                        hit = g['name']
                        break
                if hit:
                    break
            if hit:
                detected.add(hit)
            else:
                fps[sp_name] = fps.get(sp_name, 0) + 1
        sres['chain_detected'] = sorted(detected)
        sres['chain_fp'] = sorted(fps, key=fps.get, reverse=True)
        sres['chain_sens'] = f"{len(detected)}/{len(groups)}"
        results[sample] = sres
        print(f"== {sample}: 链级检出 {sres['chain_sens']} "
              f"({', '.join(sres['chain_detected'])})", flush=True)
      except Exception as e:
        import traceback
        results[sample] = {'sample': sample,
                           'error': f'{type(e).__name__}: {e}'[:300]}
        traceback.print_exc()
        print(f"== {sample}: 失败 {e}", flush=True)

    stop.set()
    time.sleep(0.6)
    import shutil
    shutil.rmtree(OUT_ROOT, ignore_errors=True)
    out_json = {'peak_rss_mb': round(peak['rss'] / 1048576, 1),
                'samples': results}
    print('RESULT:' + json.dumps(out_json, ensure_ascii=False))
    sys.stdout.flush()
    os._exit(0)


if __name__ == '__main__':
    main()
