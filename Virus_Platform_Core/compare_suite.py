# -*- coding: utf-8 -*-
"""比较基因组一键链（cmpchain）：参考集获取 → CDS/PEP + 保守基因提取 →
MAFFT+trimAl 比对 → 建树（基因组树 + 保守基因树）→ SDT 同一性矩阵。

一条任务打通「比较基因组分析」组的上下游：给一个 GenBank 集合名 / ICTV
科属选择 / accession 列表，产出：

  00_refs/    参考集 provenance（来源、accession、taxonomy.tsv、refs.fa）
  01_extract/ 全量提取（genome.fa / CDS.fa / PEP.fa / CDS/ / PEP/ /
              genes.tsv）+ marker/ 保守基因归组（RdRp/CP/MP…）+ marker_report.tsv
  02_align/   genome/ 全基因组 MSA；gene/<基因>_<cds|pep>/ 基因 MSA
  03_tree/    genome/ 基因组树；gene/<基因>_<cds|pep>/tree.nwk 基因树
  04_sdt/     SDT 精确矩阵 + 聚类排序热图 + 分布图
  run_summary.json / config.resolved.json

阶段契约照 phylodyn_local.run_pipeline：每阶段返回
{'ok','outputs','error':None,'warnings':[]}，取消只在阶段之间检查，
阶段间只认文件（重跑某阶段直接给 stages 参数）。
"""

import json
import os
import re
import shutil
import time

from .config import get_config
from .utils import check_path, safe_open, iter_fasta, write_fasta_record

# 阶段顺序与显示名（前端阶段流按这个顺序渲染）
STAGE_ORDER = ('refs', 'cds', 'align', 'tree', 'sdt')

STAGE_TITLES = {
    'refs': ('参考集获取', 'Reference acquisition'),
    'cds': ('CDS/PEP + 保守基因提取', 'CDS/PEP + marker extraction'),
    'align': ('MAFFT 比对 + trimAl 清剪', 'MAFFT + trimAl alignment'),
    'tree': ('进化树（基因组树 + 保守基因树）', 'Phylogeny (genome + gene trees)'),
    'sdt': ('SDT 同一性矩阵', 'SDT identity matrix'),
}

STAGE_DIR_NAMES = {'refs': '00_refs', 'cds': '01_extract',
                   'align': '02_align', 'tree': '03_tree', 'sdt': '04_sdt'}

# 各阶段在总进度映射里的占比（工具 job 的高水位进度映射用）
STAGE_SPAN = {'refs': (0.00, 0.10), 'cds': (0.10, 0.30), 'align': (0.30, 0.55),
              'tree': (0.55, 0.73), 'sdt': (0.73, 1.00)}

_GB_EXTS = ('.gb', '.gbk', '.gbff', '.genbank')
_MIN_ALIGN_N = 4         # 基因 MSA 最少序列数（少于 4 条不做基因比对）

# ICTV 谱系列（由浅到深），用于 ictv 模式的集合命名「最深分类值」
_RANK_COLS = ('Realm', 'Subrealm', 'Kingdom', 'Subkingdom', 'Phylum',
              'Subphylum', 'Class', 'Subclass', 'Order', 'Suborder',
              'Family', 'Subfamily', 'Genus', 'Subgenus', 'Species')

_MODES = ('collection', 'ictv', 'accessions')


# ================================================================ 小工具

class _Logger:
    """callable → logger.log(msg, level='INFO') 适配器（给
    _run_mafft/_run_trimal/run_sdt_exact 等 logger 协议用）。"""

    def __init__(self, fn):
        self._fn = fn

    def log(self, msg, level='INFO'):
        if level in ('INFO', 'DEBUG'):
            self._fn(str(msg))
        else:
            self._fn(f'[{level}] {msg}')


def _say(log):
    """log 参数 → callable（None / 非 callable 一律 no-op 直通）。"""
    return log if callable(log) else (lambda *_a, **_k: None)


def _slug(name, cap=40):
    """集合名 slug（中英文/数字/_/-，与集合名白名单一致）。"""
    s = re.sub(r'[^A-Za-z0-9\u4e00-\u9fff_\-]+', '_', str(name or '').strip())
    return s.strip('_')[:cap] or 'set'


def _abort_if_cancelled(cancel):
    """阶段之间检查取消：只抛 RuntimeError，不硬杀子进程。"""
    if cancel is not None and getattr(cancel, 'is_set', lambda: False)():
        raise RuntimeError('已取消（阶段之间检查）')


def _rel(path, root):
    """产物路径口径：一律相对 out_dir + 正斜杠（Windows 可移植）。"""
    try:
        return os.path.relpath(path, root).replace('\\', '/')
    except ValueError:                 # 不同盘符时退化为绝对路径
        return str(path).replace('\\', '/')


def _count_gb(cdir):
    """集合目录里的 GenBank 文件数（目录不存在 → 0）。"""
    try:
        return sum(1 for fn in os.listdir(cdir)
                   if fn.lower().endswith(_GB_EXTS))
    except OSError:
        return 0


def _deepest_rank(ranks):
    """ranks={列名: 值} → 最深的非空分类值（集合命名用，如 Begomovirus）。"""
    vals = {str(k).strip().lower(): str(v).strip()
            for k, v in (ranks or {}).items() if str(v or '').strip()}
    for col in reversed(_RANK_COLS):
        if vals.get(col.lower()):
            return vals[col.lower()]
    return next(iter(vals.values()), 'set')


def _marker_files(mdir):
    """marker/ → [(组名, 'cds'|'pep', 路径)]（按文件名排序，保证可复现）。"""
    out = []
    for fn in sorted(os.listdir(mdir)):
        low = fn.lower()
        if low.endswith('_cds.fa'):
            out.append((fn[:-len('_CDS.fa')], 'cds', os.path.join(mdir, fn)))
        elif low.endswith('_pep.fa'):
            out.append((fn[:-len('_PEP.fa')], 'pep', os.path.join(mdir, fn)))
    return out


def _pick_aln(d):
    """比对目录里实际使用的 FASTA：优先 trimAl 清剪版，缺则原始版。"""
    trimmed = os.path.join(d, 'aln.trim.fasta')
    if os.path.isfile(trimmed):
        return trimmed
    raw = os.path.join(d, 'aln.fasta')
    return raw if os.path.isfile(raw) else None


def _skip_result(msg):
    """被开关跳过的阶段结果（照阶段契约，ok=True + warning 说明原因）。"""
    return {'ok': True, 'outputs': {}, 'warnings': [msg], 'error': None}


def _scoped_prog(prog, key):
    """把子引擎的任意阶段标签（extract/fetch/done…）收敛到当前阶段键，
    保证 progress 契约的 stage 始终 ∈ STAGE_ORDER（调用方要按 STAGE_SPAN
    映射总进度，多余标签会 KeyError）。"""
    return lambda _stage, frac, msg: prog(key, frac, msg)


# ================================================================ 主入口

def run_pipeline(out_dir, *, mode=None, coll=None, ranks=None, accessions=None,
                 db='plant', genome='complete', limit=200, per_genus=0,
                 per_species=0, tree_tool='fasttree', mafft_strategy='auto',
                 trimal='automated1', sdt_max_n=30, do_cds=True,
                 do_gene_cds=True, do_gene_pep=True, do_sdt=True,
                 threads=None, stages=None, log=None, progress=None,
                 cancel=None) -> dict:
    """跑比较基因组全链（或指定阶段）。返回
    `{'ok','out_dir','stages':{…},'error'}`。

    输入三选一（`mode` 缺省时按顺序推断）：
      - `coll`：已有 GenBank 集合名（推荐；集合目录里至少 1 个 .gb）
      - `ranks`：ICTV 分类过滤 `{'Family': 'Geminiviridae', …}`
      - `accessions`：逗号/空格分隔的 accession 列表

    `do_cds=False` 时 cds 及下游 align/tree/sdt 全部跳过并记 warning——
    一键链里 cds 的 `01_extract/` 是后续三个阶段的**唯一输入**。
    `do_sdt=False` 单独关掉 SDT 阶段。

    `progress(stage, frac, msg)`：stage 取 STAGE_ORDER 键，frac 为该阶段
    内的 0–1 分位；调用方可用 STAGE_SPAN 映射成总进度。`cancel` 只在
    **阶段之间**检查（单个 MAFFT/RAxML 子进程自带超时，不硬中断）。
    """
    say = _say(log)
    logger = _Logger(say)
    prog = progress if callable(progress) else (lambda *_a, **_k: None)
    threads = int(threads or get_config().threads)
    out_dir = os.path.abspath(str(out_dir))
    coll = str(coll or '').strip() or None

    # ---- 模式推断（coll 有记录 > ranks > accessions） ----
    from .gb_collection import gb_collection_dir
    if not mode:
        if coll and _count_gb(gb_collection_dir(coll)) >= 1:
            mode = 'collection'
        elif isinstance(ranks, dict) and ranks:
            mode = 'ictv'
        elif accessions:
            mode = 'accessions'
        else:
            raise RuntimeError('请提供比较基因组输入：已有集合 coll / ICTV 分类 ranks '
                               '/ accession 列表 accessions 之一')
    if mode not in _MODES:
        raise ValueError(f'未知模式：{mode}')

    # ---- 集合名（ictv/accessions 未给 coll 时自动命名） ----
    if not coll:
        if mode == 'ictv':
            coll = f'cmp_{_slug(_deepest_rank(ranks))}'
        elif mode == 'accessions':
            coll = f'cmp_acc_{int(time.time())}'
        else:
            raise RuntimeError('collection 模式需要已有集合名 coll')

    # ---- 阶段选择 ----
    wanted = tuple(stages or STAGE_ORDER)
    bad = [s for s in wanted if s not in STAGE_ORDER]
    if bad:
        raise ValueError('未知阶段：' + '、'.join(bad))

    os.makedirs(out_dir, exist_ok=True)
    dirs = {k: os.path.join(out_dir, v) for k, v in STAGE_DIR_NAMES.items()}
    res = {'ok': False, 'out_dir': out_dir, 'stages': {}, 'error': None}
    say(f'比较基因组一键链：mode={mode} coll={coll} → {out_dir}')

    all_ok = True
    for i, key in enumerate(STAGE_ORDER, 1):
        if key not in wanted:
            continue
        cn = STAGE_TITLES[key][0]
        _abort_if_cancelled(cancel)         # 只在阶段之间检查
        prog(key, STAGE_SPAN[key][0], f'B{i} {cn}')
        say(f'B{i} {cn}')
        sprog = _scoped_prog(prog, key)     # 子引擎的阶段标签 → 当前阶段键

        try:
            if key in ('align', 'tree', 'sdt') and not do_cds:
                sr = _skip_result('跳过（do_cds=False：cds 是后续阶段唯一输入）')
            elif key == 'cds' and not do_cds:
                sr = _skip_result('跳过（do_cds=False）')
            elif key == 'sdt' and not do_sdt:
                sr = _skip_result('跳过（do_sdt=False）')
            elif key == 'refs':
                sr = _stage_refs(
                    out_dir, mode=mode, coll=coll, ranks=ranks,
                    accessions=accessions, db=db, genome=genome,
                    limit=limit, per_genus=per_genus,
                    per_species=per_species, logger=logger, prog=sprog,
                    cancel=cancel)
            elif key == 'cds':
                sr = _stage_cds(dirs['cds'], coll, logger, sprog, cancel)
            elif key == 'align':
                sr = _stage_align(
                    dirs['align'], dirs['cds'], mafft_strategy=mafft_strategy,
                    trimal_method=trimal, do_gene_cds=do_gene_cds,
                    do_gene_pep=do_gene_pep, threads=threads,
                    logger=logger, prog=sprog, cancel=cancel)
            elif key == 'tree':
                sr = _stage_tree(
                    dirs['tree'], dirs['align'], tree_tool=tree_tool,
                    threads=threads, logger=logger, prog=sprog, cancel=cancel)
            else:                               # sdt
                sr = _stage_sdt(
                    dirs['sdt'], dirs['cds'], sdt_max_n=sdt_max_n,
                    threads=threads, logger=logger, prog=sprog, cancel=cancel)
        except Exception as e:
            # 阶段内部以异常终止（如 ICTV 谱系表缺失、集合名非法）时也要
            # 落成失败结果：调用方只认返回值与 run_summary.json，异常逃逸
            # 就只剩一行栈回溯、没有任何可追溯的阶段记录。
            sr = {'ok': False, 'outputs': {}, 'error': str(e), 'warnings': []}

        res['stages'][key] = sr
        if not sr.get('ok'):
            all_ok = False
            res['error'] = f'B{i} {cn}失败：{sr.get("error")}'
            say(f'[ERROR] {res["error"]}')
            break
        for w in (sr.get('warnings') or []):
            say(f'[WARN] {w}')
        prog(key, STAGE_SPAN[key][1], f'B{i} {cn}完成')

    res['ok'] = all_ok
    params = {
        'tool': 'compare_suite', 'mode': mode, 'coll': coll, 'ranks': ranks,
        'accessions': accessions, 'db': db, 'genome': genome,
        'limit': int(limit), 'per_genus': int(per_genus),
        'per_species': int(per_species), 'tree_tool': tree_tool,
        'mafft_strategy': mafft_strategy, 'trimal': trimal,
        'sdt_max_n': int(sdt_max_n), 'do_cds': bool(do_cds),
        'do_gene_cds': bool(do_gene_cds), 'do_gene_pep': bool(do_gene_pep),
        'do_sdt': bool(do_sdt), 'threads': threads,
        'stages': list(wanted),
        'resolved_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    say('比较基因组一键链完成' if all_ok
        else f'比较基因组一键链中止：{res.get("error")}')
    return _finish(res, out_dir, params, say)


def _finish(res, out_dir, params, say):
    """汇总落盘：`run_summary.json`（阶段结果原文）+ `config.resolved.json`
    （全部入参快照，含实际生效的 threads 等）。返回 res。"""
    try:
        with safe_open(os.path.join(out_dir, 'run_summary.json'), 'wt') as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
    except Exception as e:
        say(f'[ERROR] run_summary.json 写入失败：{e}')
    try:
        with safe_open(os.path.join(out_dir, 'config.resolved.json'), 'wt') as f:
            json.dump(params, f, ensure_ascii=False, indent=2)
    except Exception as e:
        say(f'[ERROR] config.resolved.json 写入失败：{e}')
    return res


# ================================================================ B0 refs

def _stage_refs(out_dir, *, mode, coll, ranks, accessions, db, genome, limit,
                per_genus, per_species, logger, prog, cancel):
    """B0 参考集获取：collection / ictv / accessions 三模式 → `00_refs/`。

    ictv 与 accessions 模式会**重建**同名集合（rmtree + 重新下载，与
    「参考序列获取」页的下载口径一致，避免新旧记录混在一起）；collection
    模式只读既有集合、不写 databases/。
    """
    from . import gb_collection as gbc

    rdir = os.path.join(out_dir, STAGE_DIR_NAMES['refs'])
    os.makedirs(rdir, exist_ok=True)
    _abort_if_cancelled(cancel)
    prov_path = os.path.join(rdir, 'provenance.json')
    fa_path = os.path.join(rdir, 'refs.fa')

    def _write_prov(d):
        d = {'tool': 'compare_suite', 'stage': 'refs',
             'created': time.strftime('%Y-%m-%d %H:%M:%S'), **d}
        with safe_open(prov_path, 'wt') as f:
            json.dump(d, f, ensure_ascii=False, indent=2)

    def _dump_fasta():
        """集合记录 → 00_refs/refs.fa；返回写出的序列条数。"""
        n_fa = 0
        with safe_open(fa_path, 'wt') as f:
            for acc, _org, seq in gbc.collection_records(coll):
                if seq:
                    write_fasta_record(f, acc, seq)
                    n_fa += 1
        return n_fa

    # ---- ictv：分类选参（本地优先）→ 下载集合 ----
    if mode == 'ictv':
        from . import ictv_db
        if not ictv_db.available():
            raise RuntimeError('ICTV 谱系表未就绪（先在「参考序列获取」页更新，'
                               '或改用已有集合/accession 模式）')
        prog('refs', 0.05, f'ICTV 选参：{ranks}')
        rows, total = ictv_db.select_refs(
            ranks=ranks, genome=genome, db=db,
            limit=max(int(limit) * 20, 2000))
        # 抽样上限先于 limit 生效（先每属/每种封顶，再取前 limit 条）
        rows, dropped = ictv_db.cap_per_rank(rows, per_genus, per_species)
        rows = rows[:max(1, int(limit))]
        accs = [r.get('Genbank', '') for r in rows if r.get('Genbank')]
        if not accs:
            raise RuntimeError(f'ICTV {ranks} 无匹配参考（换一个分类或 genome=any）')
        shutil.rmtree(gbc.gb_collection_dir(coll), ignore_errors=True)
        prog('refs', 0.15, f'下载 GenBank 集合 {coll}（{len(accs)} 条 accession）')
        dl = gbc.download_gb_collection(
            coll, accessions=','.join(accs), max_records=len(accs),
            logger=logger, prog=prog, cancel=cancel)
        # 参考集分类台账（选参预览与最终下载是同一批 rows）
        with safe_open(os.path.join(rdir, 'taxonomy.tsv'), 'wt') as f:
            f.write('accession\tspecies\tgenus\tfamily\tcoverage\n')
            for r in rows:
                cells = [str(r.get(k, '') or '').replace('\t', ' ')
                         for k in ('Genbank', 'Species', 'Genus', 'Family',
                                   'Genome_Coverage')]
                f.write('\t'.join(cells) + '\n')
        n_fa = _dump_fasta()
        _write_prov({'mode': 'ictv', 'coll': coll, 'ranks': ranks, 'db': db,
                     'genome': genome, 'limit': int(limit),
                     'per_genus': int(per_genus),
                     'per_species': int(per_species), 'n_matched': total,
                     'n_target': len(accs), 'dropped_by_cap': dropped,
                     'n_fasta': n_fa,
                     'download': {k: dl.get(k) for k in
                                  ('downloaded', 'skipped', 'missing')}})
        logger.log(f'参考集就绪：{coll} · {n_fa} 条 FASTA')
        prog('refs', 1.0, f'完成：{n_fa} 条参考')
        return {'ok': True, 'error': None, 'warnings': [],
                'outputs': {'mode': 'ictv', 'coll': coll, 'ranks': ranks,
                            'n_target': len(accs), 'n_hit': len(rows),
                            'n_fasta': n_fa, 'dropped_by_cap': dropped,
                            'provenance': 'provenance.json',
                            'taxonomy_tsv': 'taxonomy.tsv',
                            'refs_fa': 'refs.fa'}}

    # ---- accessions：accession 直取 ----
    if mode == 'accessions':
        raw = (list(accessions) if isinstance(accessions, (list, tuple))
               else re.split(r'[\s,;]+', str(accessions or '')))
        accs = []
        for a in raw:                       # 去重保序
            a = str(a or '').strip()
            if a and a not in accs:
                accs.append(a)
        if not accs:
            raise RuntimeError('accession 模式需要 accessions'
                               '（逗号/空格分隔的 accession 列表）')
        prog('refs', 0.10, f'按 accession 直取 {len(accs)} 条')
        shutil.rmtree(gbc.gb_collection_dir(coll), ignore_errors=True)
        dl = gbc.download_gb_collection(
            coll, accessions=','.join(accs), max_records=len(accs),
            logger=logger, prog=prog, cancel=cancel)
        n_fa = _dump_fasta()
        _write_prov({'mode': 'accessions', 'coll': coll,
                     'accessions': accs, 'n_target': len(accs),
                     'n_fasta': n_fa,
                     'download': {k: dl.get(k) for k in
                                  ('downloaded', 'skipped', 'missing')}})
        logger.log(f'参考集就绪：{coll} · {n_fa} 条 FASTA')
        prog('refs', 1.0, f'完成：{n_fa} 条参考')
        return {'ok': True, 'error': None, 'warnings': [],
                'outputs': {'mode': 'accessions', 'coll': coll,
                            'n_target': len(accs), 'n_fasta': n_fa,
                            'provenance': 'provenance.json'}}

    # ---- collection：已有集合直接读 ----
    cdir = gbc.gb_collection_dir(coll)
    n_gb = _count_gb(cdir)
    if n_gb < 1:
        raise RuntimeError(
            f'集合 [{coll}] 不存在或没有 GenBank 记录'
            '（先在「参考序列获取」页下载，或改用 ICTV/accession 模式）')
    prog('refs', 0.10, f'读取集合 {coll}（{n_gb} 个 GenBank 文件）')
    n_fa = _dump_fasta()
    _write_prov({'mode': 'collection', 'coll': coll, 'dir': cdir,
                 'n_gb_files': n_gb, 'n_fasta': n_fa})
    logger.log(f'参考集就绪：{coll} · {n_fa} 条 FASTA（既有集合，未改动 databases/）')
    prog('refs', 1.0, f'完成：{n_fa} 条参考')
    return {'ok': True, 'error': None, 'warnings': [],
            'outputs': {'mode': 'collection', 'coll': coll,
                        'n_records': n_fa, 'refs_fa': 'refs.fa',
                        'provenance': 'provenance.json'}}


# ================================================================ B1 cds

def _stage_cds(out_dir, coll, logger, prog, cancel):
    """B1 CDS/PEP + 保守基因提取（PhyloSuite 布局，产物直落 `01_extract/`）。

    读集合 .gb，产出 genome.fa / CDS.fa / PEP.fa / CDS/<基因>.fa /
    PEP/<基因>.fa / genes.tsv / marker/。序列 id = `<accession>|<基因>`，
    下游 align/tree 全靠这个目录，所以 cds 是整条链的唯一入口产物。
    """
    os.makedirs(out_dir, exist_ok=True)
    _abort_if_cancelled(cancel)
    from .gb_collection import extract_collection_features
    prog('cds', 0.02, '解析 GenBank 记录（CDS/PEP + 保守基因）')
    res = extract_collection_features(coll, logger=logger, prog=prog,
                                      out_dir=out_dir)
    return {'ok': True, 'outputs': res, 'error': None, 'warnings': []}


# ================================================================ B2 align

def _stage_align(out_dir, extract_dir, *, mafft_strategy, trimal_method,
                 do_gene_cds, do_gene_pep, threads, logger, prog, cancel):
    """B2 MAFFT 比对 + trimAl 清剪：基因组 MSA（`genome/`）+ 保守基因 MSA
    （`gene/<基因>_<cds|pep>/`）。

    基因 MSA 只做 ≥`_MIN_ALIGN_N` 条的保守基因（过少时比对无信息量，
    记 warning 跳过）；基因组长度 max/min>3 时告警提示改用保守基因树。
    """
    from .phylo import _run_mafft, _run_trimal

    warnings = []
    os.makedirs(out_dir, exist_ok=True)
    _abort_if_cancelled(cancel)

    # ---- 基因组 MSA ----
    genome_fa = os.path.join(extract_dir, 'genome.fa')
    if not os.path.isfile(genome_fa):
        return {'ok': False, 'error': f'缺少 {genome_fa}（先跑 cds 阶段）',
                'outputs': {}, 'warnings': warnings}
    lens = [len(s) for _h, s in iter_fasta(genome_fa)]
    if len(lens) < 2:
        return {'ok': False, 'error': '参考集不足 2 条基因组',
                'outputs': {}, 'warnings': warnings}
    minl, maxl = min(lens), max(lens)
    if maxl > 3 * max(minl, 1):
        warnings.append(
            f'基因组长度差异极大（{minl}–{maxl} bp，'
            f'max/min={maxl / max(minl, 1):.1f}），基因组树拓扑可能失真；'
            '科/属级比较建议用保守基因树')

    gdir = os.path.join(out_dir, 'genome')
    os.makedirs(gdir, exist_ok=True)
    prog('align', 0.08, f'基因组 MSA（MAFFT {mafft_strategy}）')
    aln = _run_mafft(genome_fa, os.path.join(gdir, 'aln.fasta'),
                     threads=threads, logger=logger, strategy=mafft_strategy)
    if trimal_method and trimal_method != 'none':
        prog('align', 0.30, f'trimAl 清剪（{trimal_method}）')
        aln_used, trim_info = _run_trimal(
            aln, os.path.join(gdir, 'aln.trim.fasta'), logger=logger,
            method=trimal_method)
    else:
        aln_used, trim_info = aln, {'applied': False}
    logger.log(f'基因组 MSA 完成：{len(lens)} 条 · 长度 {minl}–{maxl} bp')

    outputs = {'genome': {'n_seqs': len(lens), 'min_len': minl,
                          'max_len': maxl,
                          'input': _rel(genome_fa, out_dir),
                          'aln': _rel(aln, out_dir),
                          'aln_used': _rel(aln_used, out_dir),
                          'trim': trim_info},
               'genes': {}}

    # ---- 保守基因 MSA ----
    mdir = os.path.join(extract_dir, 'marker')
    genes = _marker_files(mdir) if os.path.isdir(mdir) else []
    total = max(len(genes), 1)
    for idx, (grp, kind, src) in enumerate(genes, 1):
        _abort_if_cancelled(cancel)
        if kind == 'cds' and not do_gene_cds:
            continue
        if kind == 'pep' and not do_gene_pep:
            continue
        n_g = sum(1 for _h, _s in iter_fasta(src))
        if n_g < _MIN_ALIGN_N:
            warnings.append(f'保守基因 {grp} {kind} 仅 {n_g} 条'
                            f'（<{_MIN_ALIGN_N}），跳过比对')
            continue
        key = f'{grp}_{kind}'
        gdir2 = os.path.join(out_dir, 'gene', key)
        os.makedirs(gdir2, exist_ok=True)
        prog('align', 0.35 + 0.60 * idx / total,
             f'保守基因 MSA {key}（{n_g} 条）')
        # 2026-11-06 修复：逐基因 try（与 tree 阶段同款）。此前任一保守基因
        # MSA/trimAl 失败会抛异常拖垮整条 align 阶段，连带已完成的基因组
        # MSA 结果也拿不到；现在单基因失败只记 warning 并跳过该基因。
        try:
            a = _run_mafft(src, os.path.join(gdir2, 'aln.fasta'),
                           threads=threads, logger=logger,
                           strategy=mafft_strategy)
            if trimal_method and trimal_method != 'none':
                a_used, t_info = _run_trimal(
                    a, os.path.join(gdir2, 'aln.trim.fasta'), logger=logger,
                    method=trimal_method)
            else:
                a_used, t_info = a, {'applied': False}
            outputs['genes'][key] = {'ok': True, 'n_seqs': n_g,
                                     'molecule': kind,
                                     'input': _rel(src, out_dir),
                                     'aln': _rel(a, out_dir),
                                     'aln_used': _rel(a_used, out_dir),
                                     'trim': t_info}
        except Exception as e:
            outputs['genes'][key] = {'ok': False, 'n_seqs': n_g,
                                     'molecule': kind,
                                     'input': _rel(src, out_dir),
                                     'error': str(e)}
            warnings.append(f'保守基因 {key} 比对失败（已跳过）：{e}')
            logger.log(f'保守基因 {key} 比对失败（已跳过）：{e}', 'WARN')
    prog('align', 1.0,
         f'完成：基因组 MSA + {len(outputs["genes"])} 个保守基因 MSA')
    return {'ok': True, 'outputs': outputs, 'error': None,
            'warnings': warnings}


# ================================================================ B3 tree

def _stage_tree(out_dir, align_dir, *, tree_tool, threads, logger, prog,
                cancel):
    """B3 建树：基因组树（`genome/`）+ 保守基因树（`gene/<基因>_<cds|pep>/`）。

    输入是 02_align 下每个目标的 `aln.trim.fasta`（无则 `aln.fasta`），
    建树引擎分支与 gb_collection.build_collection_phylo 完全一致
    （raxml-ng <4 条降级 fasttree、raxml 失败降级 fasttree、pep 走
    FastTree 蛋白模式 / RAxML 'LG+G'、nt 走 _run_fasttree / 'GTR+G'、nj）。
    基因树失败只记 warning 不拖垮整阶段；**基因组树失败才整阶段失败**。
    """
    warnings = []
    outputs = {}
    os.makedirs(out_dir, exist_ok=True)
    _abort_if_cancelled(cancel)

    # 目标清单：genome + gene/*（目录名以 _pep 结尾 → 蛋白，否则核酸）
    targets = []
    galn = _pick_aln(os.path.join(align_dir, 'genome'))
    if galn:
        targets.append(('genome', galn,
                        os.path.join(out_dir, 'genome'), 'nt'))
    gene_root = os.path.join(align_dir, 'gene')
    if os.path.isdir(gene_root):
        for name in sorted(os.listdir(gene_root)):
            sub = os.path.join(gene_root, name)
            if not os.path.isdir(sub):
                continue
            aln = _pick_aln(sub)
            if not aln:
                continue
            molecule = 'pep' if name.lower().endswith('_pep') else 'nt'
            targets.append((name, aln,
                            os.path.join(out_dir, 'gene', name), molecule))
    if not galn:
        return {'ok': False,
                'error': f'缺少基因组比对 {os.path.join(align_dir, "genome")}'
                         '（先跑 align 阶段）',
                'outputs': {}, 'warnings': warnings}

    total = max(len(targets), 1)
    for idx, (label, aln, tdir, molecule) in enumerate(targets, 1):
        _abort_if_cancelled(cancel)
        prog('tree', 0.05 + 0.90 * (idx - 1) / total, f'建树 {label}')
        try:
            n = sum(1 for _h, _s in iter_fasta(aln))
            if n < 2:
                raise RuntimeError(f'比对仅 {n} 条序列（<2），无法建树')
            os.makedirs(tdir, exist_ok=True)
            tree_file, tool_used, extra = _build_tree(
                aln, tdir, tree_tool, molecule, threads, logger, prog,
                cancel)
            outputs[label] = {'ok': True, 'n_seqs': n,
                              'tree_tool_used': tool_used,
                              'tree': _rel(tree_file, out_dir), **extra}
            logger.log(f'{label} 建树完成：{n} 条 · {tool_used}')
        except Exception as e:
            if label == 'genome':
                return {'ok': False, 'outputs': outputs,
                        'error': f'基因组树失败：{e}', 'warnings': warnings}
            outputs[label] = {'ok': False, 'error': str(e)}
            warnings.append(f'保守基因 {label} 建树失败（已跳过）：{e}')

    prog('tree', 1.0, f'完成：{sum(1 for v in outputs.values() if v.get("ok"))}'
                      f'/{len(targets)} 棵')
    return {'ok': True, 'outputs': outputs, 'error': None,
            'warnings': warnings}


def _build_tree(aln, tdir, tree_tool, molecule, threads, logger, prog,
                cancel):
    """单个比对建树。返回 `(tree_file, tool_used, extra)`。

    分支照 `gb_collection.build_collection_phylo`（928-975 行）：
      - raxml-ng 且 n<4 → 降级 fasttree（RAxML-NG 自举要求 ≥4 条）
      - raxml-ng 异常 → 降级 fasttree（无信息比对会让 pll 断言崩溃）
      - nj → _run_nj；fasttree：pep 用蛋白模式（-gamma，默认 LG），
        nt 用 _run_fasttree（-nt -gtr -gamma）
      extra 来自 _parse_raxml_log（model / logl）。
    """
    from .phylo import (_run_fasttree, _run_raxml_ng, _parse_raxml_log,
                        _run_nj)

    os.makedirs(tdir, exist_ok=True)
    _abort_if_cancelled(cancel)
    n = sum(1 for _h, _s in iter_fasta(aln))
    if n < 2:
        raise RuntimeError(f'比对仅 {n} 条序列（<2），无法建树')

    tree_tool_used = (tree_tool or 'fasttree').strip().lower()
    if tree_tool_used not in ('fasttree', 'raxml-ng', 'nj'):
        raise ValueError(f'未知建树工具：{tree_tool}')
    extra = {}
    if tree_tool_used == 'raxml-ng' and n < 4:
        tree_tool_used = 'fasttree'
        logger.log(f'仅 {n} 条序列（RAxML-NG 自举要求 ≥4 条），'
                   '降级为 FastTree 建树', 'WARN')
    if tree_tool_used == 'raxml-ng':
        prog('tree', 0.40, 'RAxML-NG 建树中（较慢：ML + 100 次自举）')
        try:
            tree_file = _run_raxml_ng(
                aln, os.path.join(tdir, 'raxml'), threads=threads,
                logger=logger,
                model=('LG+G' if molecule == 'pep' else 'GTR+G'))
            extra = _parse_raxml_log(os.path.join(tdir, 'raxml'))
        except Exception as e:
            # 无信息比对（全 N / 全 gap）会让 RAxML-NG 底层 pll 断言崩溃，
            # FastTree 对同款比对可正常出树（退化枝长 0）——降级保树
            tree_tool_used = 'fasttree'
            extra = {}
            logger.log(f'RAxML-NG 建树失败({e})，降级为 FastTree 建树', 'WARN')

    if tree_tool_used == 'nj':
        tree_file = _run_nj(aln, os.path.join(tdir, 'tree.nwk'),
                            logger=logger)
    elif tree_tool_used == 'fasttree':
        if molecule == 'pep':              # 蛋白树：FastTree 蛋白模式（无 -nt）
            from .utils import run_cmd_redirect
            ft = get_config().tool('fasttree')
            logger.log('FastTree 蛋白建树（默认 LG 模型）')
            run_cmd_redirect([ft, '-gamma',
                              check_path(aln, must_exist=True)],
                             os.path.join(tdir, 'tree.nwk'),
                             logger=logger)
            tree_file = check_path(os.path.join(tdir, 'tree.nwk'),
                                   must_exist=True)
        else:
            tree_file = _run_fasttree(aln, os.path.join(tdir, 'tree.nwk'),
                                      logger=logger)
    # raxml-ng 成功时 tree_file 已在上面赋好；nj / fasttree 在此分支出树
    return tree_file, tree_tool_used, extra


# ================================================================ B4 sdt

def _stage_sdt(out_dir, extract_dir, *, sdt_max_n, threads, logger, prog,
               cancel):
    """B4 SDT 精确同一性矩阵（纯 Python 复刻 SDTv1.3 口径）。

    输入 01_extract/genome.fa（未比对序列，orient=True 自动定向）；
    产物 sdt_matrix.csv / sdt_heatmap.png / sdt_distribution.png /
    sdt_matrix.json，全部落在 `04_sdt/` 下，outputs 转相对路径。
    """
    from .config import get_config
    genome_fa = os.path.join(extract_dir, 'genome.fa')
    n = (sum(1 for _h, _s in iter_fasta(genome_fa))
         if os.path.isfile(genome_fa) else 0)
    if n < 2:
        return {'ok': False, 'error': '参考集不足 2 条', 'outputs': {},
                'warnings': []}
    try:
        mafft = get_config().tool('mafft')
        from .sdt_exact import run_sdt_exact
        res = run_sdt_exact(
            genome_fa, out_dir, mafft=mafft, max_n=sdt_max_n, orient=True,
            threads=threads, logger=logger,
            progress=lambda fr, msg: prog('sdt', fr, msg),
            cancel=cancel, aligned=False, seqtype='nt')
        outputs = {}
        for key in ('csv', 'heatmap', 'distribution', 'json'):
            v = res.get(key)
            if isinstance(v, str):
                p = v if os.path.isabs(v) else os.path.join(out_dir, v)
                outputs[key] = _rel(p, out_dir)
        return {'ok': True, 'error': None, 'warnings': [],
                'outputs': outputs}
    except Exception as e:
        return {'ok': False, 'error': str(e), 'outputs': {}, 'warnings': []}
