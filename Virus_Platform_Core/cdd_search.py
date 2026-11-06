"""PVDB CDD 结构域层（mmseqs profile 库，455 个模型）。

2026-11-02 接入：平台原 CDD 层因 ~5 GB prefilter 索引反复 OOM，已于
2026-09-29 下线；PVDB 只带植物病毒相关的 455 个 CDD profile（3.1 MB），
mmseqs 直接查询，无内存压力。

口径：easy-search，-s 5，e ≤ 1e-3，最多 25 个目标（取前 5 进表）。
CDD 模型号（pfam00946 / TIGR04198 …）没有可读产物名，因此本层只作域
证据（cdd_hits 列），不参与命名。

两条使用路径：
  search_orfs    ⑥b ORF 注释层：ORF 蛋白 vs CDD
  search_contigs ③b 候选序列验证 C 路：核酸 contig vs CDD（mmseqs 自动
                 翻译，不落 ORF 中间文件，与 BFVD 路同口径）
"""
import os

from .config import PLATFORM_ROOT, db_path, get_config
from .bfvd_search import _mmseqs_bin, easy_search

CDD_EVALUE_MAX = 1e-3
CDD_MAX_HITS = 5


def _resolve_db(p):
    """platform.json 里的库路径允许相对写法：相对平台根解析。"""
    if not p:
        return None
    p = str(p)
    if os.path.isabs(p):
        return p
    return os.path.join(PLATFORM_ROOT, p)


def cdd_db():
    """mmseqs profile 库前缀。优先 platform.json databases.cdd，
    默认 PVDB 自带 CDD 子集（databases/pvdb/cdd）。"""
    cfg = get_config()
    p = _resolve_db((getattr(cfg, 'databases', {}) or {}).get('cdd'))
    if p and os.path.isfile(p):
        return p
    prefix = os.path.join(db_path('annot', 'pvdb'), 'cdd', 'pvdb-v1.0.cdd')
    return prefix if os.path.isfile(prefix) else None


def search_orfs(faa, out_dir, threads=None, logger=None):
    """ORF 蛋白 vs PVDB CDD。返回 {orf_id: [{'target','evalue','pident'}]}；
    库/工具不可用时返回 None（调用方按跳过处理）。"""
    cfg = get_config()
    try:
        mmseqs = _mmseqs_bin()
    except FileNotFoundError:
        if logger:
            logger.log("mmseqs2 未安装，跳过 CDD 结构域层", "WARN")
        return None
    db = cdd_db()
    if not db:
        if logger:
            logger.log("未找到 PVDB CDD 库（databases/pvdb/cdd），跳过", "WARN")
        return None
    if logger:
        logger.log(f"  [cdd] 使用库: {db}")
    out_tsv = os.path.join(out_dir, 'cdd_hits.raw.tsv')
    tmp = out_tsv + '.mmseqs_tmp'
    easy_search(mmseqs, faa, db, out_tsv, tmp,
                format_output='query,target,evalue,pident',
                evalue=CDD_EVALUE_MAX, max_seqs=25,
                threads=threads or cfg.threads,
                extra=['-s', '5'], logger=logger, label='CDD')
    by_q = {}
    if not os.path.isfile(out_tsv):
        if logger:
            logger.log(f"  [cdd] 未生成结果文件，按零命中处理", "WARN")
        return by_q
    with open(out_tsv, encoding='utf-8', errors='replace') as f:
        for line in f:
            p = line.rstrip('\n').split('\t')
            if len(p) < 4:
                continue
            q = p[0].split()[0]
            t = p[1].split()[0]
            if t.startswith('rmdb '):
                continue
            by_q.setdefault(q, []).append({'target': t,
                                           'evalue': float(p[2]),
                                           'pident': float(p[3])})
    for q in by_q:
        by_q[q].sort(key=lambda h: h['evalue'])
        del by_q[q][CDD_MAX_HITS:]
    return by_q


def format_hit_cell(hits, max_targets=CDD_MAX_HITS):
    """命中列表 → 'pfam00946(4e-88)|TIGR04198(2e-31)'。"""
    return '|'.join('%s(%.0e)' % (h['target'], h['evalue'])
                    for h in (hits or [])[:max_targets])


def _normalize_span(qs, qe):
    """mmseqs query 跨段归位 → (低, 高, strand)。

    翻译搜索（核酸 query vs profile 库）在**反向链**命中时会输出
    qstart > qend。若直接 `(qe-qs+1)/qlen` 会得到**负覆盖度**并把下游
    区间判断全部带偏。入表前统一 min/max 归位，与
    pvdb_search._parse() 的 qs/qe 口径一致；strand 单独保留供展示。
    """
    qs, qe = int(qs), int(qe)
    if qs > qe:
        return qe, qs, '-'
    return qs, qe, '+'


def search_contigs(fa, out_tsv, threads=None, logger=None):
    """③b 验证 C 路：mmseqs easy-search 核酸 contig vs PVDB CDD（自动翻译）。

    CDD 库是 mmseqs profile 库（455 个植物病毒相关模型），easy-search
    对核酸查询自动走 translated search，不需要 ORF 中间文件——与 BFVD
    路同口径。命中即结构域证据（模型号无可读产物名，不参与命名）。

    返回 {contig: [{'target','evalue','pident','cov'}]}；库/工具不可用
    返回 None（调用方按跳过该路处理）。
    """
    cfg = get_config()
    try:
        mmseqs = _mmseqs_bin()
    except FileNotFoundError:
        if logger:
            logger.log('mmseqs2 未安装，跳过 CDD 结构域分支', 'WARN')
        return None
    db = cdd_db()
    if not db:
        if logger:
            logger.log('未找到 PVDB CDD 库（databases/pvdb/cdd），跳过', 'WARN')
        return None
    if logger:
        logger.log(f'  [cdd] 使用库: {db}')
    tmp = out_tsv + '.mmseqs_tmp'
    easy_search(mmseqs, fa, db, out_tsv, tmp,
                format_output='query,target,evalue,pident,qlen,qstart,qend',
                evalue=CDD_EVALUE_MAX, max_seqs=25,
                threads=threads or cfg.threads,
                extra=['-s', '5'], logger=logger, label='CDD')
    by_q = {}
    if not os.path.isfile(out_tsv):
        if logger:
            logger.log('  [cdd] 未生成结果文件，按零命中处理', 'WARN')
        return by_q
    with open(out_tsv, encoding='utf-8', errors='replace') as f:
        for line in f:
            p = line.rstrip('\n').split('\t')
            if len(p) < 7:
                continue
            q = p[0].split()[0]
            t = p[1].split()[0]
            if t.startswith('rmdb '):
                continue
            try:
                ev, pid, qlen, qs, qe = (float(p[2]), float(p[3]),
                                         int(p[4]), int(p[5]), int(p[6]))
            except ValueError:
                continue
            if ev > CDD_EVALUE_MAX:
                continue
            qs, qe, strand = _normalize_span(qs, qe)
            cov = round((qe - qs + 1) / max(qlen, 1), 3)
            cov = min(1.0, max(0.0, cov))       # 覆盖度口径：0..1
            by_q.setdefault(q, []).append({
                'target': t, 'evalue': ev, 'pident': pid,
                # 核酸 query 坐标覆盖（翻译搜索下 mmseqs 输出的是
                # 原始核酸 qstart/qend，与 BFVD 路同口径）
                'cov': cov, 'strand': strand,
            })
    for q in by_q:
        by_q[q].sort(key=lambda h: h['evalue'])
        del by_q[q][CDD_MAX_HITS:]
    if logger:
        logger.log('  [cdd] %d 条命中结构域' % len(by_q))
    return by_q
