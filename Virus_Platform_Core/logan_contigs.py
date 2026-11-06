# -*- coding: utf-8 -*-
"""LOGAN 数据下载：按 SRA accession 从 Logan 公共 S3 桶拉取预组装 contigs。

数据源：AWS Open Data 项目 Logan（桶 logan-pub，匿名访问）——约 2340 万公共
SRA 样本的统一组装产物，与 Logan-Search 溯源同一数据集
（引用：Chikhi et al. 2025, bioRxiv 2024.07.30.605881）。

边界与安全：
- 域名白名单硬编码：仅 logan-pub.s3.amazonaws.com；出站前解析 DNS，
  解析到私网/环回/保留地址直接拒绝（口径同 public_data._check_url）。
- 下载走 boto3 UNSIGNED（匿名），流式下载 + zstd 流式解压 + FASTA 流式解析，
  不落压缩原件；内存占用 = 单条序列 + 4KB 读缓冲。
- 过滤器与参考实现（github.com/apcamargo/retrieve-logan-contigs, MIT）同口径：
  min_len 最短长度 / circles_only 仅环形（DTR 判定）/ fix_circles 修
  concatemeric 环形序列（31-mer 首重复截断）。
"""
import io
import os
import re
import socket


from .utils import check_path, safe_open

BUCKET = 'logan-pub'
HOST = 'logan-pub.s3.amazonaws.com'
ACC_RE = re.compile(r'^[A-Za-z]{3}[0-9]+(\.[0-9]+)?$')
ACC_MAX = 50


def _check_host():
    """出站前 DNS 校验：主机必须解析到公网地址（拒私网/环回/保留段）。"""
    import ipaddress
    addrs = set()
    for fam, _st, _proto, _cn, sa in socket.getaddrinfo(HOST, 443):
        addrs.add(sa[0])
    if not addrs:
        raise RuntimeError(f'DNS 解析失败: {HOST}')
    for ip in addrs:
        if (ipaddress.ip_address(ip).is_private
                or ipaddress.ip_address(ip).is_loopback
                or ipaddress.ip_address(ip).is_reserved):
            raise RuntimeError(f'{HOST} 解析到私有/保留地址 {ip}，已拒绝')

def _s3_client():
    """匿名 S3 客户端（签名版本 UNSIGNED，指向白名单主机）。"""
    try:
        import boto3
        from botocore import UNSIGNED
        from botocore.config import Config
    except ModuleNotFoundError as e:
        raise RuntimeError(
            '缺少 Python 依赖 boto3（Logan S3 匿名访问用）——'
            '请在平台 Python 里执行: python -m pip install boto3'
            '（requirements.txt 已声明，可 pip install -r requirements.txt）') from e
    _check_host()
    # 连接/读取超时：大陆访问 s3.amazonaws.com 可能被墙或极慢——
    # 不设超时会无限挂起，任务卡看起来就是「长时间没动静」（2026-11-06 用户反馈）。
    return boto3.client('s3', endpoint_url='https://s3.amazonaws.com',
                        config=Config(signature_version=UNSIGNED,
                                      retries={'max_attempts': 2},
                                      connect_timeout=10, read_timeout=120))


def _acc_clean(s):
    """accession 清洗：去空白/小写转大写 + 白名单正则校验。"""
    s = re.sub(r'\s+', '', str(s or '')).upper()
    if not ACC_RE.fullmatch(s):
        raise ValueError(f'非法 accession: {s!r}（应为 SRR/ERR/DRR+数字）')
    return s


def _parse_accessions(raw):
    """支持 换行/逗号/空格 分隔的多 accession 输入，去重保序。"""
    accs, seen = [], set()
    for tok in re.split(r'[\s,;]+', str(raw or '')):
        if not tok:
            continue
        a = _acc_clean(tok)
        if a not in seen:
            seen.add(a)
            accs.append(a)
    if not accs:
        raise ValueError('未解析出任何 accession（如 SRR24759446）')
    if len(accs) > ACC_MAX:
        raise ValueError(f'一次最多 {ACC_MAX} 个 accession（防误贴整列）')
    return accs


def _cancelled(cancel):
    """取消旗标兼容：任务框架传 threading.Event（.is_set()），
    其他调用方可能传 callable（cancel()）。"""
    if cancel is None:
        return False
    if hasattr(cancel, 'is_set'):
        return cancel.is_set()
    return bool(cancel())


def _revcomp(s):
    tab = str.maketrans('ACGTNacgtn', 'TGCANtgcan')
    return s.translate(tab)[::-1]


def _has_dtr(seq):
    """DTR 判定（环形信号）：最长后缀等于前缀。rfind 是 C 速度，沿用参考实现。"""
    if len(seq) < 60:
        return False, 0
    pre = seq[:30]
    pos = seq.rfind(pre)
    if pos < len(seq) // 2:
        return False, 0
    suf = seq[pos:]
    return seq[:len(suf)] == suf, len(suf)


def _has_itr(seq):
    """ITR 判定：序列前缀与其反向互补的前缀完美匹配（反向末端重复）。"""
    if len(seq) < 60:
        return False, 0
    rev = _revcomp(seq)
    if seq[:30] != rev[:30]:
        return False, 0
    i = 31
    while i <= len(seq) // 2 and seq[:i] == rev[:i]:
        i += 1
    return True, i - 1


def _fix_circle(seq, k=31):
    """修 concatemeric 环形序列：找首个重复 k-mer，截断到其结束位置。

    参考实现同款口径（k=31）；正常环形（无内部重复）保持原样。"""
    seen = set()
    for i in range(len(seq) - k + 1):
        km = seq[i:i + k]
        if km in seen:
            return seq[:i + k - 1]
        seen.add(km)
    return seq

def _iter_fasta_stream(reader, chunk=4096):
    """流式 FASTA 解析：从 zstd 解压 reader 逐块 yield (header, seq)。
    header 保留原样（含 Logan 的 ka:/L: 标签）。"""
    name, parts, buf = None, [], ''
    while True:
        chunk_b = reader.read(chunk)
        if not chunk_b:
            break
        buf += chunk_b.decode('utf-8', errors='replace')
        lines = buf.split('\n')
        buf = lines[-1]
        for line in lines[:-1]:
            line = line.strip()
            if not line:
                continue
            if line.startswith('>'):
                if name is not None:
                    yield name, ''.join(parts)
                name, parts = line[1:], []
            elif name is not None:
                parts.append(line)
    if name is not None:
        yield name, ''.join(parts) + buf.strip()


class _CountingBody:
    """包装 botocore StreamingBody：统计已读压缩字节 → 真实下载进度。"""

    def __init__(self, body, total, on_progress):
        self._body = body
        self._total = max(1, int(total or 1))
        self._n = 0
        self._cb = on_progress

    def read(self, amt=8192):
        b = self._body.read(amt)
        if b:
            self._n += len(b)
            if self._cb:
                try:
                    self._cb(min(0.99, self._n / self._total))
                except Exception:
                    pass
        return b

    def __getattr__(self, name):          # 其余属性透传（close 等）
        return getattr(self._body, name)


def _fetch_one(acc, out_fa, min_len=0, circles_only=False, fix_circles=False,
               progress=None, cancel=None, logger=None):
    """拉取并过滤单个 accession。返回统计 dict。"""
    try:
        import zstandard as zstd
    except ModuleNotFoundError as e:      # 缺依赖时给人话，不给裸 ModuleNotFoundError
        raise RuntimeError(
            '缺少 Python 依赖 zstandard（Logan contigs 的 zst 解压用）——'
            '请在平台 Python 里执行: python -m pip install zstandard'
            '（requirements.txt 已声明，可 pip install -r requirements.txt）') from e
    if logger:
        logger.log(f'{acc}: 解析 DNS 并连接 {HOST} …')
    s3 = _s3_client()
    key = f'c/{acc}/{acc}.contigs.fa.zst'
    if logger:
        logger.log(f'{acc}: GET {key}')
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    total = obj.get('ContentLength') or 0
    if logger:
        logger.log(f'{acc}: HTTP 200，压缩大小 {total / 1048576:.1f} MB，流式解压中…')
    last_pct = [0]

    def _on_bytes(frac):
        if progress:
            progress(frac, f'{acc}: 下载/解压 {round(frac * 100)}%')
        pct = int(frac * 100)
        if logger and pct >= last_pct[0] + 25:
            last_pct[0] = pct - (pct % 25)
            logger.log(f'{acc}: 已传输约 {pct}%')

    body = _CountingBody(obj['Body'], total, _on_bytes)
    dctx = zstd.ZstdDecompressor()
    n_total = n_kept = n_bases = n_circle = 0
    with safe_open(out_fa, 'wt') as w:
        stream = dctx.stream_reader(body)
        for name, seq in _iter_fasta_stream(stream):
            n_total += 1
            if _cancelled(cancel):
                raise RuntimeError('已取消')
            seq = seq.upper()
            dtr, _ = _has_dtr(seq)
            itr, _ = _has_itr(seq)
            circular = dtr or itr
            if circles_only and not circular:
                continue
            if fix_circles and dtr:
                seq = _fix_circle(seq)
            if len(seq) >= min_len:
                w.write(f'>{name}\n')
                for j in range(0, len(seq), 70):
                    w.write(seq[j:j + 70] + '\n')
                n_kept += 1
                n_bases += len(seq)
                if dtr or itr:
                    n_circle += 1

    if progress:
        progress(1.0, f'{acc}: contigs {n_total} 条 → 保留 {n_kept} 条')
    return {'accession': acc, 'file': os.path.abspath(out_fa),
            'n_total': n_total, 'n_kept': n_kept, 'n_bases': n_bases,
            'n_circular': n_circle, 'bytes': os.path.getsize(out_fa)
            if os.path.exists(out_fa) else 0}


def _missing(acc):
    """区分「Logan 里没有该 accession」的明确错误文案。"""
    return (f'Logan 桶中不存在 {acc} 的组装（该样本可能不在 2340 万覆盖内，'
            f'或 accession 拼写有误）')


def fetch_logan_contigs(accessions, out_dir, min_len=0, circles_only=False,
                        fix_circles=False, host_filter=False, db_host=None,
                        threads=None, logger=None, progress=None, cancel=None):
    """主入口：多 accession 逐一拉取 → 可选宿主过滤 → 汇总 all.fasta。

    accessions: str 或 list[str]；out_dir: 产物目录（工具运行目录）。
    返回 {'results': [每 acc 统计], 'all_file': 汇总FASTA路径|None,
          'host_removed_total': int}。
    """
    import time
    accs = _parse_accessions(accessions)
    out_dir = check_path(out_dir, must_exist=False, in_platform=True)
    os.makedirs(out_dir, exist_ok=True)
    results = []
    n_acc = len(accs)
    for i, acc in enumerate(accs):
        if _cancelled(cancel):
            raise RuntimeError('已取消')
        out_fa = os.path.join(out_dir, f'{acc}.logan_contigs.fasta')
        if progress:
            progress((i + 0.05) / n_acc, f'({i + 1}/{n_acc}) 下载 {acc} …')
        # 单 acc 内部进度（0-1）映射到本 acc 在整批里的进度段
        acc_prog = ((lambda f, m: progress(
            (i + 0.05 + min(0.9, f) * 0.9) / n_acc,
            f'({i + 1}/{n_acc}) {m}')) if progress else None)
        # 网络中断自动重试（IncompleteReadError / 超时等传输类错误）：
        # 大陆访问 s3.amazonaws.com 偶发断流——2026-11-06 用户实测
        # IncompleteReadError: 1896955 read, but total bytes expected is 24600431。
        st = None
        for attempt in (1, 2, 3):
            try:
                st = _fetch_one(acc, out_fa, min_len=min_len,
                                circles_only=circles_only,
                                fix_circles=fix_circles,
                                progress=acc_prog, cancel=cancel, logger=logger)
                break
            except Exception as e:
                if logger:
                    logger.log(f'{acc}: 失败（第 {attempt} 次）- {e}')
                resp = getattr(e, 'response', None)
                code = (resp.get('Error', {}) or {}).get('Code', '') \
                    if isinstance(resp, dict) else ''
                if code == 'NoSuchKey':
                    raise RuntimeError(_missing(acc)) from e
                # 取消 / 明确的配置类错误（缺依赖、非法参数）不重试；
                # 传输类错误（断流/超时）自动重试，重建 S3 客户端。
                if attempt >= 3 or _cancelled(cancel) \
                        or isinstance(e, (RuntimeError, ValueError)):
                    raise
                if logger:
                    logger.log(f'{acc}: 网络中断，自动重试 {attempt + 1}/3 …')
                time.sleep(2)
        if logger:
            logger.log(f"{acc}: contigs {st['n_total']} 条 → 保留 {st['n_kept']} 条"
                       f"（环形 {st['n_circular']}）")
        if host_filter:
            st2 = filter_host_contigs(st['file'], db_host, out_dir,
                                      threads=threads, logger=logger,
                                      cancel=cancel)
            st['host_removed'] = st2['n_removed']
            st['n_kept'] = st2['n_kept']
            st['n_bases'] = st2['n_bases']
            st['file'] = st2['file']
            if logger:
                logger.log(f"{acc}: 宿主过滤移除 {st2['n_removed']} 条")
        results.append(st)
    all_file = None
    if len(results) > 1:
        all_file = os.path.join(out_dir, 'all.logan_contigs.fasta')
        with safe_open(all_file, 'wt') as w:
            for st in results:
                with safe_open(st['file']) as r:
                    w.write(r.read())
        for st in results:
            st['file'] = os.path.abspath(st['file'])
        if logger:
            logger.log(f"汇总 FASTA: {all_file}")
    return {'results': results, 'all_file': all_file,
            'n_kept_total': sum(s['n_kept'] for s in results)}

def filter_host_contigs(fasta_path, db_host, out_dir, threads=None,
                        logger=None, cancel=None):
    """宿主 contig 过滤：kunpeng 主宿主库分类，丢弃判定为宿主 taxid 的 contig。

    输入是 contigs FASTA（无质量行），kunpeng.classify 原生吃 FASTA。
    宿主 taxid 从宿主库清单 host_db.json 读（缺清单时退化为「保留全部 +
    WARN」，不静默误删）。返回 {file, n_removed, n_kept, n_bases}。"""
    from .kunpeng import classify, parse_classify_output
    import json as _json
    fasta_path = check_path(fasta_path, must_exist=True)
    db_host = check_path(db_host or _main_host_db(), must_exist=True,
                         in_platform=True)
    taxid = None
    mf = os.path.join(db_host, 'host_db.json')
    if os.path.isfile(mf):
        try:
            with open(mf, encoding='utf-8') as f:
                taxid = int(_json.load(f).get('taxid') or 0) or None
        except (ValueError, OSError):
            taxid = None
    if taxid is None and logger:
        logger.log('WARN: 宿主库缺 host_db.json 清单，无法识别宿主 taxid，'
                   '本次过滤退化为不过滤（保留全部 contigs）')
        return {'file': fasta_path, 'n_removed': 0,
                'n_kept': _count_fasta(fasta_path),
                'n_bases': _count_bases(fasta_path)}
    out_clean = os.path.join(check_path(out_dir, must_exist=False, in_platform=True),
                             os.path.basename(fasta_path).replace('.fasta', '_hostclean.fasta'))
    cls_dir = os.path.join(out_dir, 'classify_host')
    os.makedirs(cls_dir, exist_ok=True)
    res = classify(db_host, [fasta_path], cls_dir, paired=False,
                   threads=threads, confidence=0, logger=logger,
                   allow_convert=False)
    drop = set()
    if res['kraken']:
        for flag, rid, tx, _l, _m in parse_classify_output(res['kraken']):
            if flag == 'C' and taxid and int(tx) == int(taxid):
                drop.add(rid)
    n_kept = n_rm = n_bases = 0
    name = None
    parts = []
    with safe_open(fasta_path) as r, \
         safe_open(out_clean, 'wt') as w:
        def flush():
            nonlocal n_kept, n_rm, n_bases
            if name is None:
                return
            seq = ''.join(parts)
            if name in drop:
                n_rm += 1
            else:
                n_kept += 1
                n_bases += len(seq)
                w.write(f'>{name}\n{seq}\n')
        for line in r:
            line = line.strip()
            if not line:
                continue
            if line.startswith('>'):
                flush()
                name, parts = line[1:], []
            else:
                parts.append(line)
        flush()
    return {'file': os.path.abspath(out_clean), 'n_removed': n_rm,
            'n_kept': n_kept, 'n_bases': n_bases}

def _main_host_db():
    """主宿主库目录（cfg.databases['host']，延迟取避免顶部循环导入）。"""
    from .config import get_config
    return get_config().databases['host']


def _count_fasta(path):
    n = 0
    with safe_open(path) as f:
        for line in f:
            if line.startswith('>'):
                n += 1
    return n


def _count_bases(path):
    n = 0
    with safe_open(path) as f:
        for line in f:
            if not line.startswith('>'):
                n += len(line.strip())
    return n
