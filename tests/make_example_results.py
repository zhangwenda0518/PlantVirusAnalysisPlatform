# -*- coding: utf-8 -*-
"""阶段 B：跑一遍各模块，把产物固化到 examples/results/<模块>/。

做法：用平台自己的 /api/tool/run 提交任务（与用户点击「运行」完全同一条
链路），轮询到完成后把 tool_runs/<run>/ 复制到示例结果目录，并写
manifest.json 供前端「👁 示例结果」读取。与真实数据完全隔离。

用法:
    python tests/make_example_results.py                # 跑全部（跳过已存在）
    python tests/make_example_results.py --only identify,orf
    python tests/make_example_results.py --force        # 重跑覆盖
"""
import argparse
import io
import json
import os
import shutil
import sys
import time
# 控制台编码兜底：Windows 默认代码页是 GBK，本脚本的 ✔/✘/⚠ 等字符会让
# print 抛 UnicodeEncodeError（2026-09-11 实测多处踩过）。只改错误处理为
# replace（编码不动，中文照常可读），编不出的字符降级为 '?'。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass


# 平台根：默认自动推导（本文件上溯 2 层 = 平台根），可用 VP_ROOT 覆盖
ROOT = (os.environ.get('VP_ROOT')
        or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from Virus_Platform_Core.web.state import tool_runs_root  # noqa: E402

EX = 'examples/'
EXR = os.path.join(ROOT, 'examples', 'results')

# (模块名, 工具键, 标题, 参数)  —— 参数键名取自各 _tool_job_* 的 ctx.req/ctx.p
JOBS = [
    ('identify', 'identify', '病毒识别分类与提取', {
        'input': EX + 'example_sim100x_R1.fastq.gz',
        'input2': EX + 'example_sim100x_R2.fastq.gz',
        'input_type': 'pe',
        'db_virus': 'databases/kunpeng_db/virus_db'}),
    # db 缺省=平台主宿主库（active_host_db）；旧硬编码 host-db/host/classify
    # 已不存在（宿主库迁移后），2026-11-03 删掉该参数改走缺省
    ('hostremoval', 'hostremoval', '宿主去除与序列提取', {
        'r1': EX + 'example_sim100x_R1.fastq.gz',
        'r2': EX + 'example_sim100x_R2.fastq.gz'}),
    ('fastp', 'fastp', '序列质控（fastp）', {
        'r1': EX + 'example_sim100x_R1.fastq.gz',
        'r2': EX + 'example_sim100x_R2.fastq.gz'}),
    ('convert', 'convert', '格式转换（FASTQ→FASTA）', {
        'input': EX + 'example_sim100x_R1.fastq.gz', 'target': 'fasta'}),
    ('orf', 'orf', 'ORF 预测', {'fasta': EX + 'example_viral_contigs.fasta'}),
    ('genoplot', 'genoplot', '基因组图谱', {
        'ann': EX + 'example_genome.gb', 'mode': 'both', 'max_plots': 2,
        'gb_opts': {'labels': 'out'}}),
    ('primer', 'primer', '引物设计（保守区）', {
        'fasta': EX + 'example_conserved_set.fasta', 'mode': 'conserved'}),
    ('align', 'align', '序列比对（MAFFT + trimAl）', {
        'seqs': EX + 'example_virus_set.fasta'}),
    ('quicktree', 'quicktree', '进化树构建（快速）', {
        'seqs': EX + 'example_virus_set.fasta', 'method': 'fasttree'}),
    ('sdt', 'sdt', 'SDT 同一性分析', {
        'seqs': EX + 'example_virus_set.fasta'}),
    ('structcmp', 'structcmp', '结构比较', {
        'seqs': EX + 'example_virus_set.fasta'}),
    ('identity', 'identity', 'NT+AA 同一性表', {
        'nt_seqs': EX + 'example_virus_set.fasta'}),
    ('assemble', 'assemble', '病毒组装（SPAdes）', {
        'r1': EX + 'example_sim100x_R1.fastq.gz',
        'r2': EX + 'example_sim100x_R2.fastq.gz', 'mode': 'rnaviral'}),
    ('contigs', 'contigs', 'contig 分类（病毒鉴定）', {
        'contigs': EX + 'example_viral_contigs.fasta',
        'db_virus': 'databases/kunpeng_db/virus_db'}),
    ('orfa', 'orfa', 'ORF 功能注释', {
        'fasta': EX + 'example_viral_contigs.fasta'}),
    ('verify', 'verify', '候选序列验证', {
        'fasta': EX + 'example_viral_contigs.fasta'}),
    # hostpredict（独立宿主预测工具）2026-09-27 撤出 TOOL_REGISTRY（孤儿页删除，
    # 功能保留为 contigs 后自动运行与管道 ④）——不再生成示例；已有快照保留。
    ('consensus', 'consensus', '共识序列与变异', {
        'fasta': EX + 'example_viral_contigs.fasta',
        'reads': EX + 'example_sim100x_R1.fastq.gz'}),
    ('kvsuite', 'kvsuite', '已知病毒识别与定量', 'KVSUITE'),
    ('variant', 'kvsuite', '病毒变异分析', 'VARIANT'),
    # 2026-11-02 重构后 virchain 只收去宿主/病毒序列 FASTA（原始 FASTQ 报
    # 400），改用专用示例 example_hostfree.fasta（见 _virchain_params）
    ('virchain', 'virchain', '病毒识别分类·一键（①组装→②鉴定→③验证）', 'VIRCHAIN'),
    ('kvchain', 'kvchain', '病毒定量与共识·一键（全流程）', 'KVCHAIN'),
    # ---- 进化动力学（2026-09-21 补：四卡链 + 数据接入的示例结果）----
    ('pdprep', 'pdprep', '进化动力学数据接入', {
        'source': 'files',
        'fasta': EX + 'example_phylogeo.fasta',
        # pdprep 治理要 name/date/location 三列（seq_id/year/region 那份是
        # rtt/phylogeo 用的旧口径，治理不认 → 0 行通过）
        'meta': EX + 'example_pdprep.meta.csv'}),
    ('rdp', 'rdp', '🔁 RDP5 重组分析', {
        # 用带嵌合体的专用示例（2026-09-21 造：5 条×1200bp，Gansu_REC 前半 A 谱系
        # 后半 B 谱系）——CMV example_recomb_set 跑出来分布全 0，无演示价值；
        # 这份有明确重组信号，断点分布图/事件表都有内容
        'input': EX + 'example_rdp.fasta',
        'meta': EX + 'example_rdp.meta.csv'}),
    ('rtt', 'rtt', '⏱ 时间信号与定年', {
        'input': EX + 'example_phylogeo.fasta',
        'meta': EX + 'example_phylogeo.meta.csv'}),
    ('phylogeo', 'phylogeo', '🌍 系统地理分析', {
        'input': EX + 'example_phylogeo.fasta',
        # 统一元数据（name=完整 FASTA 头，region 列与卡默认区划列名一致）
        'meta': EX + 'example_pdprep.meta.csv'}),
    ('phylodyn', 'phylodyn', '⚡ 一键分析', {
        'input': EX + 'example_phylogeo.fasta',
        'meta': EX + 'example_phylogeo.meta.csv'}),
    # dsRNA 设计全链路（2026-09-19 入编；2026-10-03 随功能恢复重新挂回）。
    # panel='' = 显式跳过脱靶扫描：示例不依赖 databases/dsrna/panel/ 的物种
    # 面板数据（GB 级、按需自带），其余链路（选窗→效价→T7 引物→成品 QC）
    # 全部真跑；seed=42 结果可复现，约 3 秒。
    ('dsrna', 'dsrna', 'dsRNA 设计（RNAi 臂序列优化）', {
        'input': EX + 'example_cmv.fasta',
        'construct_len': 300, 'kmer_len': 21, 'iterations': 100, 'seed': 42,
        'candidates': 3, 'n_windows': 1, 'max_mm': 1, 'panel': '',
        'primer_scope': 'best_window',
        'primer_product_min': 200, 'primer_product_max': 300}),
    # ---- 2026-11-05 补齐：CRISPR 检测设计三卡 + 注释一键链的示例结果 ----
    # crispr 用 ACMV 单序列目标（不用宿主脱靶检查，示例不依赖宿主库）；
    # conan 用 ORSV 五条模拟分离物（多分离物 + MAFFT 比对演示）；cricon 双
    # 路线联合（crispr+conan）吃同一份 ORSV 分离物；annochain 吃 2 条病毒
    # contig，classify=1 走 kunpeng 病毒库分类（库未就绪时链内自动降级为
    # 留空手动填写，与页面行为一致）。
    ('crispr', 'crispr', 'CRISPR 检测设计（Cas12a/RPA 协同）', {
        'input': EX + 'example_acmv.fasta', 'use_host': False}),
    ('conan', 'conan', 'CONAN-SWIFT crRNA 设计（Cas3 / I 型 I-E）', {
        'input': EX + 'example_conserved_set.fasta'}),
    ('cricon', 'cricon', 'CRISPR 双路线联合检测设计（Cas12a + Cas3）', {
        'input': EX + 'example_acmv.fasta'}),
    # 2026-11-05：LAMP 引物组设计（t-lamp）——ACMV 单序列，环引物开，
    # 固定种子可复现；与 t-crispr 的 --amp LAMP 档共用内核。
    ('lamp', 'lamp', 'LAMP 引物组设计（六引物 + 环引物）', {
        'input': EX + 'example_acmv.fasta', 'top_n': 10,
        'product_min': 120, 'product_max': 180}),
    ('annochain', 'annochain', '注释一键链（功能注释 → 图谱 → NCBI 提交准备）', {
        'fasta': EX + 'example_viral_contigs.fasta', 'classify': '1'}),
]

# 示例 manifest 条目的补充说明（键 = 模块名；collect() 不含此字段）
MANIFEST_NOTES = {
    'dsrna': 'dsRNA 设计全链（dsrnamax 选窗 + siRNA 效价 + T7 引物）：'
             'CMV 三条 RNA，seed=42 可复现',
    'crispr': 'CRISPR 检测设计：ACMV 单序列目标，RPA 引物 + crRNA 全链'
              '（宿主脱靶检查关闭——示例不依赖宿主库）',
    'conan': 'CONAN-SWIFT：ORSV 五条模拟分离物，MAFFT 比对 + 同源位点评分'
             '（ViennaRNA 缺席时 MFE 列自动留空）',
    'cricon': '双路线联合：crispr + conan 同时出报告，ACMV 单序列目标'
              '（cricon_near 双喂留空——可用近缘参考集尚未内置）',
    'lamp': 'LAMP 六引物组设计：ACMV 单序列（~2.8kb），F3/F2/F1/B1/B2/B3 + LF/LB，'
            '分段 Tm/GC/间距约束 + 二聚体快筛',
    'annochain': '注释一键链：2 条病毒 contig → ORF 注释 → 基因组图谱 → '
                 'NCBI 提交准备（kunpeng 分类，库缺时 organism/family 留空）',
}

# 复制产物时排除的中间目录/大文件（体积大且对示例无价值）
# mmseqs_tmp 里含 Windows 无法 stat 的 latest 符号链接（遍历会抛 WinError 1920）
SKIP_DIRS = {'_chunk', 'spades', 'mmseqs_tmp', '__pycache__', '_fq2fa',
             'logs', 'snpeff_work'}
# kunpeng 原始输出（每 read 一行，几百 KB，仅中间产物；kreport/汇总表才是结果）
# plotly.min.js 是报告内嵌的前端库副本（4.6MB），不进示例结果
SKIP_NAMES = {'.done', 'run.log', 'output_1.txt', 'output_2.txt',
              'output_1-2.txt', 'plotly.min.js'}
SKIP_SUFFIX = ('.fastq.gz', '.fq.gz', '.fasta.gz', '.tmp', '.k2', '.bam',
               '.sam', '.sorted.bam', '.bai')

# 模块级总量护栏（2026-11-03）：sim100x 的 kvsuite/variant 一次产出 134 个
# 参考基因组的全量图/表（实测 109MB/1473 文件、45MB/698 文件），无上限会把
# 示例结果目录与打包示例包撑爆。每模块收集 ≤20MB（目录按名排序，截取子集
# 确定；截断在 manifest note 里注明）。
MODULE_TOTAL_MB = 20


def _kvsuite_params():
    """kvsuite 需要样品表：临时生成一份指向示例 reads 的 TSV（不建真样品，
    避免示例数据混进用户的样品列表）。"""
    d = os.path.join(tool_runs_root(), '_example_inputs')
    os.makedirs(d, exist_ok=True)
    sheet = os.path.join(d, 'example_sample_sheet.tsv')
    r1 = os.path.join(ROOT, 'examples', 'example_sim100x_R1.fastq.gz')
    r2 = os.path.join(ROOT, 'examples', 'example_sim100x_R2.fastq.gz')
    with io.open(sheet, 'w', encoding='utf-8', newline='\n') as f:
        f.write('name\tr1\tr2\nEXAMPLE\t' + r1 + '\t' + r2 + '\n')
    return {'sample_sheet': sheet, 'stage': 'all'}


def _virchain_params():
    """virchain 新契约（2026-11-02）：只收去宿主/病毒序列 FASTA。
    用专用示例 examples/example_hostfree.fasta（sim100x 病毒序列提取产物
    降采样 2 万条）；文件缺失时报可读的中文错，而不是 400 谜语。"""
    fasta = os.path.join(ROOT, 'examples', 'example_hostfree.fasta')
    if not os.path.isfile(fasta):
        raise RuntimeError('缺少示例输入 examples/example_hostfree.fasta'
                           '（sim100x 病毒序列提取降采样版）')
    return {'input': fasta, 'mode': 'rnaviral', 'memory': 32}


def _variant_params():
    """变异段（kvsuite stage=variant）不跑 reads，只吃上游 BAM/VCF。
    取最近一次 kvsuite 示例运行的 bam 目录。"""
    import glob
    cands = sorted(glob.glob(os.path.join(tool_runs_root(), 'kvsuite_*',
                                          'kvsuite', 'bam')),
                   key=os.path.getmtime, reverse=True)
    for d in cands:
        if glob.glob(os.path.join(d, '*.bam')):
            return {'stage': 'variant', 'bam_dir': d}
    raise RuntimeError('未找到上游 BAM（先跑 kvsuite 示例）')


def run_one(client, tool, params, timeout=3600):
    r = client.post('/api/tool/run',
                    json={'tool': tool, 'threads': 8, 'params': params})
    if r.status_code != 200:
        raise RuntimeError(f'提交失败 HTTP {r.status_code}: '
                           f'{r.get_data(as_text=True)[:200]}')
    d = r.get_json() or {}
    tid, run = d.get('task'), d.get('run')
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(2)
        j = client.get(f'/api/task/{tid}').get_json() or {}
        st = j.get('status')
        if st == 'done':
            return run, j.get('result'), time.time() - t0
        if st in ('failed', 'cancelled'):
            raise RuntimeError(f'任务{st}: {(j.get("error") or "")[:200]}')
    raise RuntimeError(f'超时 {timeout}s')


def collect(run, module, title, meta, sub_runs=None):
    # 跟随 output_root：任务真实落盘在 tool_runs_root()（平台根 run/ 或
    # 自定义输出根 <output_root>/tool_runs），不能写死 ROOT/run/tool_runs
    src = os.path.join(tool_runs_root(), run)
    dst = os.path.join(EXR, module)
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    os.makedirs(dst, exist_ok=True)
    files = []
    total = 0
    truncated = False

    def _tree(sroot, prefix=''):
        # 把一个 run 目录（sroot）的产物复制到示例目录（prefix = module 内
        # 相对前缀）。文件大小/名字过滤与总量护栏全走同一套规则。
        nonlocal files, total, truncated
        for cur, dirs, fs in os.walk(sroot):
            dirs[:] = sorted(d for d in dirs
                             if d not in SKIP_DIRS and not d.endswith('.mmseqs_tmp'))
            rel = os.path.relpath(cur, sroot)
            for fn in sorted(fs):
                if truncated:
                    return
                if fn in SKIP_NAMES or fn.endswith(SKIP_SUFFIX):
                    continue
                s = os.path.join(cur, fn)
                try:
                    if os.path.islink(s):        # mmseqs 的 latest 等符号链接
                        continue
                    # 12MB 单文件上限：sim100x 换代后 convert 产物
                    # example_sim100x_R1.fa.gz 约 8.9MB，旧 8MB 会把转换示例卡掏空
                    if os.path.getsize(s) > 12 * 1024 * 1024:
                        continue
                    if total + os.path.getsize(s) > MODULE_TOTAL_MB * 1024 * 1024:
                        truncated = True
                        return
                except OSError:
                    continue
                d = os.path.join(dst, prefix, rel) if rel != '.' else \
                    (os.path.join(dst, prefix) if prefix else dst)
                os.makedirs(d, exist_ok=True)
                shutil.copy2(s, os.path.join(d, fn))
                total += os.path.getsize(s)
                files.append(os.path.relpath(os.path.join(d, fn), dst).replace('\\', '/'))

    _tree(src)
    if sub_runs:
        for key, sub in sub_runs:
            _tree(os.path.join(tool_runs_root(), sub), prefix=key)
    entry = {'module': module, 'title': title, 'run': run,
             'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
             'source': 'examples/'}
    # meta（任务 API 返回）只补缺，不覆盖 curated 键——尤其 files：meta 带
    # 的是 run 目录全量清单（含被尺寸/名字过滤掉的大文件），覆盖后 manifest
    # 会声明一批盘上不存在的文件，归账时全被剔成「已缺失」注记（2026-11-03）
    for k, v in (meta or {}).items():
        if k not in entry:
            entry[k] = v
    if truncated:
        entry['note'] = (f'（产物超 {MODULE_TOTAL_MB}MB 模块上限，示例只截取'
                         f'前 {len(files)} 个文件）')
    entry['files'] = sorted(files)
    return entry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', default='')
    ap.add_argument('--force', action='store_true')
    args = ap.parse_args()
    only = {x.strip() for x in args.only.split(',') if x.strip()}

    os.makedirs(EXR, exist_ok=True)
    import app as appmod
    client = appmod.app.test_client()

    manifest_path = os.path.join(EXR, 'manifest.json')
    manifest = {}
    if os.path.isfile(manifest_path):
        try:
            with io.open(manifest_path, encoding='utf-8') as f:
                manifest = json.load(f)
        except ValueError:
            manifest = {}

    ok, fail = [], []
    for module, tool, title, params in JOBS:
        if only and module not in only:
            continue
        if not args.force and os.path.isdir(os.path.join(EXR, module)):
            print(f'  [跳过] {module}（已存在）', flush=True)
            ok.append(module)
            continue
        print(f'  [运行] {module} <- {tool} …', flush=True)
        try:
            if params in ('KVSUITE', 'KVCHAIN'):
                params = _kvsuite_params()
            elif params == 'VARIANT':
                params = _variant_params()
            elif params == 'VIRCHAIN':
                params = _virchain_params()
            run, result, dt = run_one(client, tool, params)
            meta = result if isinstance(result, dict) else {}
            # 链式任务（annochain 等）：主 run 目录只有 _chain.json +
            # results.html，各步产物在子 run 目录 → 读 _chain.json 把每个
            # step 的 run 目录按 step key 前缀一并收集
            chain_json = os.path.join(tool_runs_root(), run, '_chain.json')
            sub_runs = None
            if os.path.isfile(chain_json):
                try:
                    chain = json.load(io.open(chain_json, encoding='utf-8'))
                    sub_runs = [(s.get('key') or f'step{i}', s.get('run'))
                                for i, s in enumerate(chain.get('steps') or [])
                                if s.get('run')]
                except ValueError:
                    sub_runs = None
            manifest[module] = collect(run, module, title, meta,
                                       sub_runs=sub_runs)
            if module in MANIFEST_NOTES:
                manifest[module]['note'] = MANIFEST_NOTES[module]
            n = len(manifest[module]['files'])
            print(f'         ✓ {dt:.0f}s  产物 {n} 个 -> {run}', flush=True)
            ok.append(module)
        except Exception as e:
            print(f'         ✗ 失败: {e}', flush=True)
            fail.append((module, str(e)[:160]))

    # manifest 收尾归一（2026-10-04 审查 E2）：
    # ① 旧条目 files 曾是字符串数组、新条目是 {path,size}——schema 混用，
    #    统一为对象数组（size 未知置 null）；
    # ② 过滤「声明了但磁盘不存在」的文件（快照与源脱节时避免误导前端）。
    for mod, ent in manifest.items():
        if not isinstance(ent, dict) or not isinstance(ent.get('files'), list):
            continue
        norm, kept = [], 0
        for item in ent['files']:
            if isinstance(item, str):
                item = {'path': item, 'size': None}
            p = item.get('path')
            if p and os.path.isfile(os.path.join(EXR, mod, p)):
                norm.append(item)
                kept += 1
            elif not p:
                norm.append(item)
        dropped = len(ent['files']) - kept
        if dropped:
            ent['note'] = ((ent.get('note') or '') +
                           f'（{dropped} 个声明文件已缺失，从清单剔除）').strip()
        ent['files'] = norm

    with io.open(manifest_path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f'\n  完成 {len(ok)} 个，失败 {len(fail)} 个')
    for m, e in fail:
        print(f'    ✗ {m}: {e}')
    print(f'  manifest -> {os.path.relpath(manifest_path, ROOT)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
