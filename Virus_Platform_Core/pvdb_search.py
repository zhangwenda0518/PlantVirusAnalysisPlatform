# -*- coding: utf-8 -*-
"""PVDB 植物病毒专属注释库（⑥b 注释层 'pvdb'）。

PVDB（plant-virus-only database）v1.0 位于 databases/pvdb/：

    seq/pvdb-v1.0.prot.faa | .dmnd   23,292 条蛋白
                                     （20,621 基线 + 2,306 成熟肽 + 365 多聚蛋白）
    hmm/pvdb-v1.0.{pfam,vog,vfam}.hmm  Pfam 4,514 / VOG 522 / VFAM 1,502
    cdd/pvdb-v1.0.cdd                mmseqs profile 库（455 profiles）
    bfvd/pvdb-v1.0.bfvd              mmseqs 模型库（13,900 模型）
    meta/pvdb-v1.0.proteins.tsv      命名（canonical_function/species/genus/family）
    meta/pvdb-v1.0.ref_cuts.tsv      多聚蛋白参考切点与各段产物名

本模块实现平台的 pvdb 层，口径对齐构建侧 `24.orf_annotation/pvdb_annotate.py`：

  1. 序列同源：DIAMOND → MMseqs2 → BLAST+ 自动降级；e ≤ 1e-3 作漏斗，
     采用门槛沿用 PVDB 默认 **pident ≥ 40 且 query 覆盖度 ≥ 60**。
  2. 命名：meta/pvdb-v1.0.proteins.tsv 的 canonical_function（物种/属/科
     同表给出，不依赖 NCBI taxonomy 索引）。
  3. 分段（segments）：把 meta/pvdb-v1.0.ref_cuts.tsv 的参考切点按 HSP
     坐标映射到 query，三档阈值 strong ≥70/≥80、mid ≥50/≥70；strong 档
     直接采用，mid 档要求边界有 Q|X motif 支持（占比 ≥0.5）。段名用参考
     自身的 products 列，输出形如
     `1-244:P1 protein;245-704:HC-Pro protein;...`。

设计取舍
--------
- 切点映射走 pvdb_annotate 的 "linear" 路径（HSP sstart/send 线性缩放）；
  参考与 query 均为全长多聚蛋白时与全局比对等价，平台因此不额外依赖
  `global_align.py`。
- 不修改 hmm_annot.HMM_LIBS：PVDB 的 Pfam/VOG/VFAM 三库与平台既有
  Pfam-A-Viruses 层互不影响（HMM 文本已落盘，供后续域级证据扩展）。
- 库缺失时 search_orfs() 返回 {}，pvdb 层整体跳过，不报错（与 bfvd 层一致）。
"""
import csv
import io
import json
import os
import shutil

from .config import db_path, get_config
from .utils import run_cmd

NAME = 'pvdb'
VERSION = 'v1.0'

#: PVDB 默认命中门槛（pvdb_annotate.SEQ_MIN_PID / SEQ_MIN_QCOV）
SEQ_MIN_PIDENT = 40.0
SEQ_MIN_QCOV = 60.0
SEQ_MAX_EVALUE = 1e-3
SEQ_MAX_TARGETS = 25
#: 每行 pvdb_hits 最多展示几个参考命中
DISPLAY_HITS = 3

#: 切点投影三档（沿 PVDB 默认 balanced；strict/recall 由 --profile 切换）
PROJ_STRONG_PIDENT = 70.0
PROJ_STRONG_QCOV = 80.0
PROJ_MID_PIDENT = 50.0
PROJ_MID_QCOV = 70.0
PROJ_MIN_GAP = 20          # 相邻切点最小间距（aa）
PROJ_SNAP_WINDOW = 10      # 切点向 Q|X motif snap 的窗口（aa）
PROJ_MID_MIN_SUPPORT = 0.5  # mid 档：有 motif 支持的边界占比门槛

#: 分段投影档位（--profile）：(strong_pident, strong_qcov, mid_pident, mid_qcov)
PROJ_PROFILES = {
    'strict':   (80.0, 90.0, 60.0, 80.0),
    'balanced': (70.0, 80.0, 50.0, 70.0),
    'recall':   (60.0, 70.0, 40.0, 60.0),
}

PROJ_TIER_STRONG = 'strong'
PROJ_TIER_MID = 'mid'
PROJ_TIER_WEAK = 'weak'

#: 视为「无功能信息」的产物名（pvdb_annotate.UNINFORMATIVE）
UNINFORMATIVE = {
    '', 'hypothetical protein', 'unknown protein', 'uncharacterized protein',
    'putative uncharacterized protein', 'putative protein', 'protein',
    'predicted protein', 'orf', 'unnamed protein product',
    'conserved protein', 'refseq hypothetical protein', 'pseudoprotein',
}

#: 与 pvdb_annotate 相同：Q|X 切点的 P1' 常见残基
_P1PRIME = set('SGVYATMFLE')

#: 缓存：{(kind): ((abspath, mtime_ns, size), value)}
_CACHE = {}


# ------------------------------------------------------------------
# 库路径
# ------------------------------------------------------------------
def _f(layer, ext):
    """PVDB 文件命名：pvdb-v1.0.<layer>.<ext>。"""
    return '%s-%s.%s.%s' % (NAME, VERSION, layer, ext)


def pvdb_dir():
    """PVDB 根目录。platform.json databases.pvdb 优先（真实存在的目录），
    否则注册表 databases/pvdb。"""
    d = (getattr(get_config(), 'databases', {}) or {}).get('pvdb')
    if d and os.path.isdir(d):
        return d
    return db_path('annot', 'pvdb')


def paths(db=None):
    """PVDB 各层文件路径（与构建侧 pvdb_annotate.paths 同构）。"""
    d = db or pvdb_dir()
    return {
        'root': d,
        'prot_faa': os.path.join(d, 'seq', _f('prot', 'faa')),
        'prot_dmnd': os.path.join(d, 'seq', _f('prot', 'dmnd')),
        'pfam': os.path.join(d, 'hmm', _f('pfam', 'hmm')),
        'vog': os.path.join(d, 'hmm', _f('vog', 'hmm')),
        'vfam': os.path.join(d, 'hmm', _f('vfam', 'hmm')),
        'cdd': os.path.join(d, 'cdd', '%s-%s.cdd' % (NAME, VERSION)),
        'bfvd': os.path.join(d, 'bfvd', '%s-%s.bfvd' % (NAME, VERSION)),
        'manifest': os.path.join(d, 'meta', _f('manifest', 'json')),
        'proteins': os.path.join(d, 'meta', _f('proteins', 'tsv')),
        'ref_cuts': os.path.join(d, 'meta', _f('ref_cuts', 'tsv')),
    }


def pvdb_db():
    """序列搜索库（.dmnd）路径；不存在返回 None。"""
    p = paths()['prot_dmnd']
    return p if os.path.isfile(p) else None


def pvdb_ready():
    """pvdb 层是否可用：蛋白 FASTA（序列降级路径）或 .dmnd 任一存在。"""
    p = paths()
    return os.path.isfile(p['prot_dmnd']) or os.path.isfile(p['prot_faa'])


def refresh_paths():
    """切换数据库目录后失效全部缓存（config._refresh_module_paths 回调）。"""
    _CACHE.clear()


def layer_state(db=None):
    """各证据层文件是否存在（日志/自检用）。"""
    p = paths(db)
    return {
        'seq_dmnd': os.path.isfile(p['prot_dmnd']),
        'seq_faa': os.path.isfile(p['prot_faa']),
        'hmm_pfam': os.path.isfile(p['pfam']),
        'hmm_vog': os.path.isfile(p['vog']),
        'hmm_vfam': os.path.isfile(p['vfam']),
        'cdd': os.path.isfile(p['cdd']),
        'bfvd': os.path.isfile(p['bfvd']),
        'proteins': os.path.isfile(p['proteins']),
        'ref_cuts': os.path.isfile(p['ref_cuts']),
    }


def db_trace():
    """PVDB 库可追溯信息 → (db_files, db_versions)。

    覆盖 seq/cdd/bfvd/hmm/meta 关键文件；版本锚点取 manifest.json 的
    schema + built 字段（无 manifest 时退化为文件名）。
    """
    from .utils import db_trace as _trace
    p = paths()
    files, versions = _trace(
        [p['prot_dmnd'], p['prot_faa'], p['cdd'], p['bfvd'],
         p['pfam'], p['vog'], p['vfam'], p['proteins'], p['ref_cuts']],
        n_max=20)
    if os.path.isfile(p['manifest']):
        try:
            with io.open(p['manifest'], encoding='utf-8',
                         errors='replace') as fh:
                man = json.load(fh)
            versions[os.path.basename(p['manifest'])] = (
                '%s built %s' % (man.get('schema', NAME),
                                 man.get('built', ''))).strip()
        except (OSError, ValueError):
            pass
    return files, versions


# ------------------------------------------------------------------
# 元数据（按文件指纹缓存）
# ------------------------------------------------------------------
def _signature(path):
    st = os.stat(path)
    return (os.path.abspath(path),
            getattr(st, 'st_mtime_ns', int(st.st_mtime * 1e9)), st.st_size)


def read_tsv(path, key='id'):
    """按 key 列读 TSV → {key: row dict}；文件缺失返回 {}。"""
    out = {}
    if not os.path.isfile(path):
        return out
    with io.open(path, encoding='utf-8', errors='replace') as fh:
        for row in csv.DictReader(fh, delimiter='\t'):
            k = (row.get(key) or '').strip()
            if k:
                out[k] = row
    return out


def read_fasta(path):
    """FASTA → {id: seq}（id 取首个空白前的 token）。"""
    seqs, cur, buf = {}, None, []
    with io.open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            if line.startswith('>'):
                if cur:
                    seqs[cur] = ''.join(buf)
                cur = line[1:].split()[0]
                buf = []
            elif cur:
                buf.append(line.strip())
    if cur:
        seqs[cur] = ''.join(buf)
    return seqs


def _load_cached(kind, path, loader):
    try:
        sig = _signature(path)
    except OSError:
        return {}
    hit = _CACHE.get(kind)
    if hit and hit[0] == sig:
        return hit[1]
    val = loader(path)
    _CACHE[kind] = (sig, val)
    return val


def load_proteins(db=None):
    """meta/pvdb-v1.0.proteins.tsv → {id: row}。

    列：id accession protein_id species genus family canonical_function
        family_id evidence_tier
    """
    return _load_cached('proteins', paths(db)['proteins'], read_tsv)


def load_ref_cuts(db=None, proteins=None):
    """meta/pvdb-v1.0.ref_cuts.tsv → {protein_key: rec}。

    rec = {'cut_positions': [...], 'products': [...], 'family', 'acc',
           'protein_len', 'canonical_function'}
    canonical_function 从 proteins.tsv 补齐（ref_cuts 表本身没有该列），
    project_cuts 用它判断参考是否为多聚蛋白。
    """
    path = paths(db)['ref_cuts']
    cuts = _load_cached('ref_cuts', path, _read_ref_cuts)
    if not cuts:
        return cuts
    prot = proteins if proteins is not None else load_proteins(db)
    for key, rec in cuts.items():
        row = prot.get(key) or {}
        rec['canonical_function'] = (row.get('canonical_function') or '').strip()
    return cuts


def _read_ref_cuts(path):
    out = {}
    with io.open(path, encoding='utf-8', errors='replace') as fh:
        for row in csv.DictReader(fh, delimiter='\t'):
            k = (row.get('protein_key') or '').strip()
            if not k:
                continue
            cuts = []
            for tok in (row.get('cut_positions') or '').split(','):
                tok = tok.strip()
                if not tok:
                    continue
                try:
                    pos = int(tok)
                except ValueError:
                    continue
                if pos >= 1:
                    cuts.append(pos)
            try:
                plen = int(float((row.get('protein_len') or '').strip() or 0)) or None
            except ValueError:
                plen = None
            out[k] = {
                'cut_positions': sorted(set(cuts)),
                'products': [x.strip() for x in
                             (row.get('products') or '').split('|')],
                'family': (row.get('family') or '').strip(),
                'acc': (row.get('acc') or '').strip(),
                'protein_len': plen,
                'canonical_function': '',
            }
    return out


def load_ref_seqs(db=None):
    """seq/pvdb-v1.0.prot.faa → {target: seq}（切点投影取参考长度用）。"""
    return _load_cached('ref_seqs', paths(db)['prot_faa'], read_fasta)


# ------------------------------------------------------------------
# 序列搜索（DIAMOND → MMseqs2 → BLAST+ 自动降级）
# ------------------------------------------------------------------
def _pick_engine(prefer=None):
    """探测可用的蛋白搜索引擎，返回 (名称, 路径) 或 None。"""
    cfg = get_config()
    order = ['diamond', 'mmseqs', 'blastp']
    if prefer in order:
        order = [prefer] + [e for e in order if e != prefer]
    for name in order:
        try:
            exe = cfg.tool(name)
        except FileNotFoundError:
            continue
        if exe and os.path.isfile(exe):
            return name, exe
    return None


def _run_engine(engine, exe, faa, p, out_tsv, threads, logger=None):
    """统一输出列序：q seq target pid qlen qs qe ss se evalue bits。"""
    if engine == 'diamond' and os.path.isfile(p['prot_dmnd']):
        run_cmd([exe, 'blastp', '-d', p['prot_dmnd'], '-q', faa, '-o', out_tsv,
                 '--outfmt', '6', 'qseqid', 'sseqid', 'pident', 'qlen',
                 'qstart', 'qend', 'sstart', 'send', 'evalue', 'bitscore',
                 '--very-sensitive', '--max-target-seqs',
                 str(SEQ_MAX_TARGETS), '-e', str(SEQ_MAX_EVALUE),
                 '--threads', str(threads), '--quiet'], logger=logger)
        return 'diamond'
    # 走到这里只有两种可能：
    #   a) engine == 'mmseqs'（用户指定 mmseqs）；
    #   b) engine == 'diamond' 但 .dmnd 缺失（pvdb_ready 允许「仅 .faa」配置）。
    # 下面用的是 mmseqs easy-search 的参数语法，**exe 必须是 mmseqs 本身**。
    # 2026-11-06 修复：原实现直接复用 `_pick_engine()` 返回的 diamond 路径，
    # 等于拿 DIAMOND 可执行文件跑 mmseqs 参数（必然失败，且不会降级）。
    mmseqs_exe = exe if engine == 'mmseqs' else None
    if mmseqs_exe is None:
        pick = _pick_engine('mmseqs')
        if pick and pick[0] == 'mmseqs':
            mmseqs_exe = pick[1]
    if mmseqs_exe:
        if not os.path.isfile(p['prot_faa']):
            raise RuntimeError('PVDB 序列库缺失（seq/*.dmnd 与 *.faa 都不存在）')
        env = os.environ.copy()
        env['PATH'] = os.path.dirname(mmseqs_exe) + os.pathsep \
            + env.get('PATH', '')
        tmp = out_tsv + '.mmseqs_tmp'
        try:
            run_cmd([mmseqs_exe, 'easy-search', faa, p['prot_faa'], out_tsv,
                     tmp, '--format-output',
                     'query,target,pident,qlen,qstart,qend,tstart,tend,evalue,bits',
                     '-e', str(SEQ_MAX_EVALUE), '--max-seqs', str(SEQ_MAX_TARGETS),
                     '-s', '7.5', '--threads', str(threads)],
                    logger=logger, env=env)
        finally:
            # easy-search 的 tmp 含 Windows 无法 stat 的特殊文件，必须清理
            shutil.rmtree(tmp, ignore_errors=True)
        return 'mmseqs'
    # 既没有 mmseqs（或用户指定的 mmseqs 不可用），也不能用 diamond 库
    # → 降级 blastp。同样必须换成 blastp 的可执行文件，不能沿用 diamond 的。
    pick = _pick_engine('blastp')
    if not (pick and pick[0] == 'blastp'):
        dmnd_state = '存在' if os.path.isfile(p['prot_dmnd']) else '缺失'
        faa_state = '存在' if os.path.isfile(p['prot_faa']) else '缺失'
        raise RuntimeError(
            'PVDB 序列搜索无可用引擎：'
            f'seq/*.dmnd {dmnd_state}、seq/*.faa {faa_state}；'
            f'当前首选引擎 {engine} 无法执行本次搜索，'
            '且未探测到 MMseqs2 / BLAST+ 可执行文件。'
            '请在「设置 → 外部工具」配置 mmseqs 或 blastp 后再试。')
    if not os.path.isfile(p['prot_faa']):
        raise RuntimeError('PVDB 序列库缺失（seq/*.dmnd 与 *.faa 都不存在）')
    run_cmd([pick[1], '-query', faa, '-subject', p['prot_faa'],
             '-evalue', str(SEQ_MAX_EVALUE),
             '-max_target_seqs', str(SEQ_MAX_TARGETS),
             '-num_threads', str(threads),
             '-outfmt', '6 qseqid sseqid pident qlen qstart qend '
                        'sstart send evalue bitscore',
             '-out', out_tsv], logger=logger)
    return 'blastp'


def _parse(path, proteins, logger=None):
    """解析统一 TSV → {qid: [hit, ...]}（按 bitscore 降序，最多 25 条）。

    hit: {'lib','target','acc','function','species','genus','family',
          'pident','qcov','qlen','qs','qe','ss','se','ev','bits'}
    """
    by_q = {}
    if not os.path.isfile(path):
        return by_q
    with io.open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            p = line.rstrip('\n').split('\t')
            if len(p) < 10:
                continue
            try:
                pid = float(p[2])
                qlen = int(float(p[3]))
                qs, qe = int(float(p[4])), int(float(p[5]))
                ss, se = int(float(p[6])), int(float(p[7]))
                ev, bits = float(p[8]), float(p[9])
            except ValueError:
                continue
            if ev > SEQ_MAX_EVALUE:
                continue
            q, target = p[0].split()[0], p[1].split()[0]
            if qs > qe:
                qs, qe = qe, qs
            if ss > se:
                ss, se = se, ss
            qcov = round((qe - qs + 1) / max(qlen, 1) * 100, 1)
            row = proteins.get(target) or {}
            by_q.setdefault(q, []).append({
                'lib': 'pvdb', 'target': target,
                'acc': (row.get('accession') or target).strip(),
                'function': (row.get('canonical_function') or '').strip(),
                'species': (row.get('species') or '').strip(),
                'genus': (row.get('genus') or '').strip(),
                'family': (row.get('family') or '').strip(),
                'pident': pid, 'qcov': qcov, 'qlen': qlen,
                'qstart': qs, 'qend': qe, 'sstart': ss, 'send': se,
                'ev': ev, 'bits': bits,
            })
    for q in by_q:
        by_q[q].sort(key=lambda h: (-h['bits'], -h['pident']))
        del by_q[q][SEQ_MAX_TARGETS:]
    return by_q


def search_orfs(faa, out_dir, threads=None, logger=None, engine=None,
                min_pident=None, min_qcov=None, profile=None):
    """⑥b 注释层：ORF 蛋白 vs PVDB 序列库。

    min_pident/min_qcov: 序列命中门槛覆盖（默认 40/60）。
    profile: 分段投影档位 strict/balanced/recall（默认 balanced）。

    返回 {orf_id: [hit, ...]}；库/工具不可用时返回 {}（调用方按零命中处理）。
    """
    cfg = get_config()
    if not pvdb_ready():
        if logger:
            logger.log(f"未找到 PVDB 库（{pvdb_dir()}），跳过 pvdb 层", "WARN")
        return {}
    eng = _pick_engine(engine)
    if not eng:
        if logger:
            logger.log("未找到搜索引擎（DIAMOND/MMseqs2/blastp），跳过 pvdb 层",
                       "WARN")
        return {}
    profile = (profile or 'balanced').lower()
    if profile not in PROJ_PROFILES:
        profile = 'balanced'
    engine_name, exe = eng
    p = paths()
    out_tsv = os.path.join(out_dir, 'pvdb_hits.raw.tsv')
    threads = int(threads or cfg.threads or 1)
    db_name = os.path.basename(p['prot_dmnd']
                               if os.path.isfile(p['prot_dmnd'])
                               else p['prot_faa'])
    if logger:
        logger.log(f"  [pvdb] {engine_name} 搜索 {db_name}"
                   f"（pident≥{float(min_pident) if min_pident is not None else SEQ_MIN_PIDENT:g}"
                   f" & qcov≥{float(min_qcov) if min_qcov is not None else SEQ_MIN_QCOV:g}"
                   f"，投影档位 {profile}）")
    used = _run_engine(engine_name, exe, faa, p, out_tsv, threads,
                       logger=logger)
    by_q = _parse(out_tsv, load_proteins())
    # 把本次调用的门槛/档位随 hit 传递（annotate_rows 投影时读取），
    # 避免用模块级全局覆盖（长驻 Web 进程并发不安全）。
    for hits in by_q.values():
        for h in hits:
            h['_min_pident'] = min_pident
            h['_min_qcov'] = min_qcov
            h['_profile'] = profile
    n_q = len(by_q)
    n_ok = sum(1 for hits in by_q.values()
               if pick_best(hits, min_pident, min_qcov))
    if logger:
        logger.log(f"  [pvdb] {n_q} 个 ORF 有参考命中，其中 {n_ok} 个达到"
                   f" PVDB 命名门槛（{used}）")
    return by_q


# ------------------------------------------------------------------
# 命中判定与展示
# ------------------------------------------------------------------
def informative(text):
    """产物名是否含可用的功能信息（排除 hypothetical/unknown 等）。"""
    t = (text or '').strip()
    if not t:
        return False
    low = t.lower()
    return low not in UNINFORMATIVE and 'hypothetical' not in low \
        and 'uncharacterized' not in low


def qualifies(hit, min_pident=None, min_qcov=None):
    """PVDB 命名门槛：pident/qcov（百分数口径），默认 40 / 60。

    min_pident/min_qcov: 调用方覆盖（注释页「序列命中门槛」）。"""
    mp = SEQ_MIN_PIDENT if min_pident is None else float(min_pident)
    mq = SEQ_MIN_QCOV if min_qcov is None else float(min_qcov)
    try:
        return (float(hit.get('pident') or 0) >= mp
                and float(hit.get('qcov') or 0) >= mq)
    except (TypeError, ValueError):
        return False


def pick_best(hits, min_pident=None, min_qcov=None):
    """首个「过门槛 + 功能名可读」的命中；没有返回 None。"""
    for h in hits or []:
        if qualifies(h, min_pident, min_qcov) and informative(h.get('function')):
            return h
    return None


def format_hits(hits, max_hits=DISPLAY_HITS):
    """命中列表 → 紧凑单元格：`P1 protein(2e-45,cov0.92)|...`。"""
    out = []
    for h in (hits or [])[:max_hits]:
        name = h.get('function') or h.get('acc') or h.get('target') or 'pvdb'
        out.append('%s(%s,cov%.2f)' % (name, _fmt_e(h.get('ev')),
                                       float(h.get('qcov') or 0) / 100.0))
    return '|'.join(out)


def _fmt_e(ev):
    try:
        v = float(ev)
    except (TypeError, ValueError):
        return '?'
    if v <= 0:
        return '0'
    return '%.0e' % v


def format_segments(segs):
    """分段列表 → `1-244:P1 protein;245-704:HC-Pro protein;...`。"""
    return ';'.join('%d-%d:%s' % (s['qstart'], s['qend'], s['product'])
                    for s in (segs or []))


# ------------------------------------------------------------------
# 切点投影（复制自 24.orf_annotation/pvdb_annotate.py 的线性映射路径）
# ------------------------------------------------------------------
def projection_tier(pident, qcov, profile='balanced'):
    """按 (pident, qcov) 分档：strong / mid / weak。

    profile: strict / balanced / recall —— 决定 strong/mid 两档阈值组合
    （PROJ_PROFILES）。"""
    sp, sq, mp, mq = PROJ_PROFILES.get(
        profile, PROJ_PROFILES['balanced'])
    try:
        pid, cov = float(pident or 0.0), float(qcov or 0.0)
    except (TypeError, ValueError):
        return PROJ_TIER_WEAK
    if pid >= sp and cov >= sq:
        return PROJ_TIER_STRONG
    if pid >= mp and cov >= mq:
        return PROJ_TIER_MID
    return PROJ_TIER_WEAK


def project_cuts(query_len, hit, ref_cuts, ref_len):
    """参考多聚蛋白切点 → query 坐标。

    坐标映射：有 sstart/send 用 HSP 线性缩放（pvdb_annotate 的 linear），
    否则按参考长度缩放（scaled）。只保留落在 [qstart, qend] 内、且与
    已保留切点间隔 ≥ 20 aa 的点。返回
    (positions, n_positions, min_pident, method, tier)。
    """
    tier = projection_tier(hit.get('pident'), hit.get('qcov'))
    if tier == PROJ_TIER_WEAK:          # 弱档不投影，交给 motif 模型
        return ([], 0, 0.0, 'none', tier)
    target = (hit.get('target') or '').strip()
    entry = ref_cuts.get(target) if isinstance(ref_cuts, dict) else None
    ref_fn = (hit.get('canonical_function') or '').strip()
    if isinstance(entry, dict):
        cuts = entry.get('cut_positions') or []
        ref_fn = ref_fn or (entry.get('canonical_function') or '').strip()
        if not ref_len:
            ref_len = entry.get('protein_len') or 0
    else:
        cuts = entry or []
    if not entry and 'polyprotein' not in ref_fn.lower():
        return ([], 0, 0.0, 'none', tier)
    if not cuts:
        return ([], 0, 0.0, 'none', tier)
    try:
        qlen = int(query_len or 0)
        qs = int(hit.get('qstart'))
        qe = int(hit.get('qend'))
    except (TypeError, ValueError):
        return ([], 0, 0.0, 'none', tier)
    if qs > qe:
        qs, qe = qe, qs

    try:
        ref_len = int(float(ref_len or 0))
    except (TypeError, ValueError):
        ref_len = 0

    ss = se = None
    try:
        ss, se = int(hit.get('sstart')), int(hit.get('send'))
        if ss > se:
            ss, se = se, ss
        linear = se > ss
    except (TypeError, ValueError):
        linear = False
    if not linear and ref_len <= 0:
        return ([], 0, 0.0, 'none', tier)

    # 长度一致性：短蛋白命中长多聚蛋白（或反之）时切点无意义
    if ref_len > 0 and qlen > 0:
        ratio = float(qlen) / float(ref_len)
        if ratio < 0.5 or ratio > 2.0:
            return ([], 0, 0.0, 'none', tier)
    if linear and ref_len > 0:
        if float(se - ss + 1) / float(ref_len) < 0.5:
            return ([], 0, 0.0, 'none', tier)

    mapped = []
    for pos in cuts:
        try:
            pos = int(pos)
        except (TypeError, ValueError):
            continue
        if linear:
            if pos < ss or pos > se:
                continue
            q = qs + (pos - ss) * float(qe - qs) / float(se - ss)
        else:
            if pos < 1 or pos > ref_len:
                continue
            q = qs + (pos - 1) * float(qe - qs) / float(ref_len)
        mapped.append(int(q + 0.5))          # 四舍五入
    mapped.sort()

    kept = []
    for q in mapped:
        if q < qs or q > qe:
            continue
        if qlen > 0 and not (1 <= q <= qlen):
            continue
        if kept and q - kept[-1] < PROJ_MIN_GAP:
            continue
        kept.append(q)
    method = ('linear' if linear else 'scaled') if kept else 'none'
    return (kept, len(kept),
            float(hit.get('pident') or 0.0) if kept else 0.0, method, tier)


def _motif_near(seq, pos, window=PROJ_SNAP_WINDOW):
    """±window 内最近的 Q|X 位点；没有返回 None。"""
    if not seq:
        return None
    n = len(seq)
    for d in range(0, int(window) + 1):
        for cand in ((pos - d, pos + d) if d else (pos,)):
            i = cand - 1
            if 0 <= i < n - 1 and seq[i] == 'Q' and seq[i + 1] in _P1PRIME:
                return cand
    return None


def _snap_to_motif(seq, pos, window=PROJ_SNAP_WINDOW):
    found = _motif_near(seq, pos, window)
    return pos if found is None else found


def _filter_projected_cuts(query_len, positions, seq, tier=None,
                           window=None):
    """按档位过滤投影切点（strong 全留；mid 要求 motif 支持占比 ≥0.5）。"""
    w = PROJ_SNAP_WINDOW if window is None else int(window)
    cand = sorted({int(x) for x in (positions or []) if 1 <= int(x) <= query_len})
    if not cand:
        return [], []
    probed = [(p, (_motif_near(seq, p, w) if seq else None)) for p in cand]
    if tier == PROJ_TIER_MID:
        supported = sum(1 for _p, f in probed if f is not None)
        if supported / float(len(probed)) < PROJ_MID_MIN_SUPPORT:
            return [], [{'raw': p, 'cut': None,
                         'support': 'rejected_motif_ratio'} for p in cand]
    kept, details = [], []
    for p, found in probed:
        snap = p if found is None else found
        support = 'reference' if found is None else ('snap' if snap != p else 'motif')
        if kept and snap - kept[-1] < PROJ_MIN_GAP:
            details.append({'raw': p, 'cut': snap, 'support': 'gap'})
            continue
        kept.append(snap)
        details.append({'raw': p, 'cut': snap, 'support': support})
    return kept, details


def _segments_from_cuts(query_len, positions, ref_fn, products=None, seq=None,
                        window=None):
    """按投影切点切段；段名优先用参考的 products 列。"""
    w = PROJ_SNAP_WINDOW if window is None else int(window)
    base = (ref_fn or 'polyprotein').strip() or 'polyprotein'
    cuts = []
    for p in sorted({int(p) for p in positions if 1 <= int(p) <= query_len}):
        q = _snap_to_motif(seq, p, w) if seq else p
        if 1 <= q <= query_len and (not cuts or q - cuts[-1] >= PROJ_MIN_GAP):
            cuts.append(q)
    bounds = [0] + cuts + [query_len]
    out = []
    n_seg = len(bounds) - 1
    names = [x for x in (products or []) if x]
    for i in range(n_seg):
        start, end = bounds[i] + 1, bounds[i + 1]
        if start > end:
            continue
        prod = names[i] if len(names) == n_seg else base
        out.append({'qstart': start, 'qend': end, 'product': prod})
    return out


def _best_cut_projection(query_len, hits, proteins, ref_cuts):
    """挑 bitscore 最高、过投影档位的多聚蛋白参考做投影。

    返回 (proj, 参考功能名, ref_cuts 条目)；无可用投影返回 (None, '', {})。
    """
    for hh in sorted(hits or [],
                     key=lambda x: (-float(x.get('bits') or 0.0),
                                    -float(x.get('pident') or 0.0))):
        entry = ref_cuts.get(hh.get('target')) \
            if isinstance(ref_cuts, dict) else None
        row = (proteins or {}).get(hh.get('target')) or {}
        ref_fn = ((row.get('canonical_function') or '')
                  or (entry or {}).get('canonical_function') or '').strip()
        if projection_tier(hh.get('pident'), hh.get('qcov'),
                           (hh.get('_profile') or 'balanced')) == PROJ_TIER_WEAK:
            continue
        if not entry and 'polyprotein' not in ref_fn.lower():
            continue
        ref_len = 0
        if isinstance(entry, dict):
            ref_len = entry.get('protein_len') or 0
        if not ref_len:
            try:
                ref_len = int(float(row.get('protein_len') or 0))
            except (TypeError, ValueError):
                ref_len = 0
        enriched = dict(hh)
        enriched['canonical_function'] = ref_fn
        proj = project_cuts(query_len, enriched, ref_cuts, ref_len)
        if proj[1] >= 2:
            return proj, ref_fn or hh.get('target', ''), (entry or {})
    return None, '', {}


def project_segments(query_len, query_seq, hits, proteins, ref_cuts):
    """三档自适应投影 → {'segments','method','support','tier','rejected'}。

    strong → method='projection'，切点**直接采用**（不做 ±10aa motif 吸附，
    与模块头口径一致；2026-11-06 修复：此前 strong 也走了 _snap_to_motif，
    与「直接采用」不符，切点被窗口内最近的 Q|X 拉偏）。
    mid → 'projection+motif'（先按 motif 支持率过滤，再做 motif 吸附；
    支持边界不足整条回退）。weak / 非多聚蛋白 → 空结果。

    坐标口径：切点与 segments 的 qstart/qend 均为 **1 基闭区间**
    （_segments_from_cuts 里 bounds 两端 +1/不加）。
    """
    empty = {'segments': [], 'method': '', 'support': 0, 'tier': '',
             'rejected': False}
    if not ref_cuts or not hits:
        return empty
    proj, ref_fn, entry = _best_cut_projection(
        query_len, hits, proteins, ref_cuts)
    if proj is None or proj[1] < 2:
        return empty
    tier = proj[4] or PROJ_TIER_STRONG
    positions, method = list(proj[0]), 'projection'
    snap_seq = None
    if tier == PROJ_TIER_MID:
        positions, _details = _filter_projected_cuts(
            query_len, positions, query_seq, tier)
        method = 'projection+motif'
        snap_seq = query_seq          # 仅 mid 档做 motif 吸附
        if len(positions) < 2:
            empty.update({'tier': tier, 'rejected': True})
            return empty
    segs = _segments_from_cuts(query_len, positions, ref_fn,
                               products=(entry or {}).get('products'),
                               seq=snap_seq)
    if len(segs) < 2:
        empty.update({'tier': tier, 'rejected': True})
        return empty
    return {'segments': segs, 'method': method, 'support': len(positions),
            'tier': tier, 'rejected': False}


# ------------------------------------------------------------------
# 并回注释行
# ------------------------------------------------------------------
def annotate_rows(rows, pvdb_by_q, seqs=None, classify_product=None,
                  logger=None):
    """把 PVDB 结果并回 orf_annotation 行。

    命名优先级：PVDB 过门槛且功能名可读时，若当前行（层1 RefSeq）无注释、
    或当前产物名是泛化的 polyprotein、或 PVDB 的 identity 不低于当前命中，
    则采用 PVDB 的 canonical_function / species / genus / family / 指标。
    否则只补 pvdb_hits 与 segments，不动现有命名（避免低 identity 的
    PVDB 命中覆盖 RefSeq 的精确注释）。

    无论命名是否采用，只要命中能投影出 ≥2 段，就写入
    segments / seg_method / cs_support 三列。

    返回 (n_named, n_segments)。
    """
    if not pvdb_by_q:
        return 0, 0
    proteins = load_proteins()
    ref_cuts = load_ref_cuts(proteins=proteins)
    ref_seqs = load_ref_seqs() if ref_cuts else {}
    seqs = seqs or {}
    n_named = n_seg = 0
    for r in rows:
        hits = pvdb_by_q.get(r.get('orf_id')) or []
        if not hits:
            continue
        r['pvdb_hits'] = format_hits(hits)
        best = pick_best(hits,
                         hits[0].get('_min_pident'),
                         hits[0].get('_min_qcov'))
        if best:
            cur_inf = r.get('informative') == 'Y'
            cur_prod = (r.get('product') or '')
            try:
                cur_pid = float(r.get('pident') or 0)
            except (TypeError, ValueError):
                cur_pid = 0.0
            fn = best.get('function') or ''
            fn_low, cur_low = fn.lower(), cur_prod.lower()
            fn_generic = 'polyprotein' in fn_low
            cur_generic = 'polyprotein' in cur_low
            best_pid = float(best.get('pident') or 0)
            # 采用 PVDB 命名的条件（满足其一）：
            #   a) 当前行无注释 / 无 identity；
            #   b) 当前是泛化的 polyprotein，而 PVDB 给出具体产物名；
            #   c) 两边产物名同类（含 polyprotein 对 polyprotein），且 PVDB
            #      的 identity 不低于当前命中。
            # 反例保护：PVDB 只给出 "polyprotein" 时不覆盖 RefSeq 的具体
            # 产物名（如 coat protein），避免低特异性命名倒灌。
            prefer = (not cur_inf) or (cur_pid <= 0) \
                or (cur_generic and not fn_generic) \
                or ((not fn_generic or fn_low == cur_low) and best_pid >= cur_pid)
            if prefer:
                r.update({
                    'length_aa': best.get('qlen') or r.get('length_aa', 0),
                    'hit_accession': best.get('acc') or best.get('target', ''),
                    'product': fn,
                    'organism': best.get('species') or r.get('organism', ''),
                    'pident': best.get('pident'),
                    'qcov': best.get('qcov'),
                    'bitscore': best.get('bits'),
                    'evalue': best.get('ev'),
                    'genus': best.get('genus') or r.get('genus', ''),
                    'family': best.get('family') or r.get('family', ''),
                    'informative': 'Y',
                    'evidence': 'pvdb',
                })
                if classify_product:
                    r['category'] = classify_product(fn)
                n_named += 1
        seq = seqs.get(r.get('orf_id'))
        if seq and ref_cuts:
            res = project_segments(len(seq), seq, hits, proteins, ref_cuts)
            if res['segments']:
                r['segments'] = format_segments(res['segments'])
                r['seg_method'] = res['method']
                r['cs_support'] = res['support']
                n_seg += 1
    if logger:
        logger.log(f"  [pvdb] 命名 {n_named} 个 ORF，投影分段 {n_seg} 个")
    return n_named, n_seg
