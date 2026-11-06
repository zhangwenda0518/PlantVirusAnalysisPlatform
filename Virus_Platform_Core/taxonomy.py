# -*- coding: utf-8 -*-
"""
NCBI Taxonomy 准备与解析。
- prepare_taxonomy(): 下载 new_taxdump.tar.gz 并解压 nodes/names/merged 到平台 taxonomy 目录
- TaxonomyGraph: taxid -> 名称/rank/祖先链，供 kreport 解析与病毒注释使用
下载仅允许 https + NCBI 官方域名白名单，解析 IP 阻断私网/环回/链路本地地址，禁用重定向。
"""
import os
import json
import time
import socket
import ipaddress
import urllib.request
from urllib.parse import urlparse

from .config import DIRS
from .utils import check_path, safe_open

TAXDUMP_URL = "https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/new_taxdump/new_taxdump.tar.gz"
REQUIRED_MEMBERS = ('nodes.dmp', 'names.dmp', 'merged.dmp')
ALLOWED_HOSTS = {'ftp.ncbi.nlm.nih.gov', 'www.ncbi.nlm.nih.gov'}
TAXDUMP_FULL_NAME = 'new_taxdump.tar.gz'    # 官方全量档（~155MB；按钮/force 用）
SLIM_TAR_NAME = 'new_taxdump_slim.tar.gz'   # 分发精简档（pack_slim_archive 生成）


# ------------------------------------------------------------------
# SSRF 防护：URL 白名单 + IP 边界校验 + 禁止重定向
# ------------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None      # 一律不跟随重定向


def validate_ncbi_url(url):
    u = urlparse(str(url))
    if u.scheme != 'https':
        raise ValueError(f"仅允许 https 协议: {url}")
    if u.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"下载域名不在白名单({sorted(ALLOWED_HOSTS)}): {u.hostname}")
    infos = socket.getaddrinfo(u.hostname, 443, proto=socket.IPPROTO_TCP)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise ValueError(f"目标域名解析到受限地址 {ip}，已阻断")
    return str(url)


# ------------------------------------------------------------------
# taxonomy 准备
# ------------------------------------------------------------------
def taxonomy_ready(tax_dir=None):
    tax_dir = check_path(tax_dir or DIRS['taxonomy'], must_exist=False, in_platform=True)
    nodes = check_path(os.path.join(tax_dir, 'nodes.dmp'), must_exist=False, in_platform=True)
    names = check_path(os.path.join(tax_dir, 'names.dmp'), must_exist=False, in_platform=True)
    return os.path.isfile(nodes) and os.path.isfile(names)


def taxid_lookup(taxid, tax_dir=None):
    """校验 taxid 在 NCBI 分类中是否存在/已合并。

    返回:
      ('ok', None)            - nodes.dmp 中存在
      ('merged', new_taxid)   - 已合并到新 taxid（建议改用新值）
      ('missing', None)       - 不存在（无效或本地 taxonomy 过旧）
    """
    t = int(taxid)
    tax_dir = check_path(tax_dir or DIRS['taxonomy'],
                         must_exist=True, in_platform=True)
    nodes = check_path(os.path.join(tax_dir, 'nodes.dmp'),
                       must_exist=True, in_platform=True)
    with safe_open(nodes) as f:
        for line in f:
            first = line.split('\t', 1)[0]
            if first.isdigit() and int(first) == t:
                return 'ok', None
    merged = check_path(os.path.join(tax_dir, 'merged.dmp'),
                        must_exist=False, in_platform=True)
    if os.path.isfile(merged):
        with safe_open(merged) as f:
            for line in f:
                parts = line.split('\t|\t')
                if len(parts) >= 2 and parts[0].strip().isdigit() \
                        and int(parts[0].strip()) == t:
                    # merged.dmp 字段尾带 "\t|"，取首个制表符前的数字
                    new = parts[1].split('\t')[0].strip()
                    return 'merged', (int(new) if new.isdigit() else new)
    return 'missing', None


def taxid_name(taxid, tax_dir=None, default=''):
    """取 taxid 的学名（流式扫 names.dmp，命中即返回；查无时兜底在线核对缓存）。

    为什么不直接用 TaxonomyGraph().name()：建库清单只需要查一两个 taxid，
    而 TaxonomyGraph 会把整份 names.dmp（数百 MB）读成字典；这里按行扫描
    命中就停，代价可忽略。
    精简 taxonomy 只含病毒子树：宿主 taxid 本地查不到时读在线核对缓存
    （taxid_online_check 成功核对过即有学名），再没有就给 default。
    """
    try:
        t = str(int(taxid))
    except (TypeError, ValueError):
        return default
    tax_dir = tax_dir or DIRS['taxonomy']
    names = os.path.join(tax_dir, 'names.dmp')
    fallback = default
    if os.path.isfile(names):
        # safe_open 不接受 encoding/errors（它是给平台内 gz/路径校验用的），
        # 这里先用 check_path 校验路径、再用内建 open 指定容错编码。
        p = check_path(names, must_exist=True)
        try:
            with open(p, encoding='utf-8', errors='replace') as f:
                for line in f:
                    # taxid 后必须紧跟制表符，否则 112863 会命中 1128630
                    if not line.startswith(t + '\t'):
                        continue
                    # 按 '|' 切再 strip：names.dmp 行尾是"\t|"，用 '\t|\t' 切会让
                    # 末段变成 'scientific name\t|'，与 TaxonomyGraph.load() 口径不一致
                    parts = [x.strip() for x in line.split('|')]
                    if len(parts) < 4 or parts[0] != t:
                        continue
                    if parts[3] == 'scientific name':
                        return parts[1]
                    if fallback == default:
                        fallback = parts[1]
        except OSError:
            fallback = default
    if fallback != default:
        return fallback
    return _online_cache_name(t, default)


def prepare_taxonomy(logger=None, force=False):
    """确保 taxonomy 就绪；未就绪则下载解压。返回 taxonomy 目录。"""
    tax_dir = check_path(DIRS['taxonomy'], must_exist=False, in_platform=True)
    os.makedirs(tax_dir, exist_ok=True)

    if taxonomy_ready(tax_dir) and not force:
        if logger:
            logger.log(f"Taxonomy 已就绪: {tax_dir}")
        return tax_dir

    tar_path = _pick_taxdump_tar()
    if not tar_path or force:
        # 本地无任何 taxdump 档（或 --force 重拉）：下载官方全量档。
        # 下载按钮 / init-taxonomy（不带 --slim）维持全量口径不变。
        tar_path = check_path(os.path.join(DIRS['databases'], TAXDUMP_FULL_NAME),
                              must_exist=False, in_platform=True)
        if logger:
            logger.log(f"下载 NCBI new_taxdump (~160MB): {TAXDUMP_URL}")
        _download(TAXDUMP_URL, tar_path, logger)

    if logger:
        logger.log("解压 nodes.dmp / names.dmp / merged.dmp ...")
    import tarfile
    with tarfile.open(tar_path, 'r:gz') as tar:
        for member in tar.getmembers():
            base = os.path.basename(member.name)
            if base in REQUIRED_MEMBERS and member.isfile():
                member.name = base          # 重写为纯文件名，消除任何目录成分
                tar.extract(member, tax_dir, filter='data')   # data 过滤器阻断穿越/链接

    if not taxonomy_ready(tax_dir):
        raise RuntimeError("taxonomy 解压后校验失败（缺少 nodes.dmp/names.dmp），请检查下载文件")
    if logger:
        logger.log(f"Taxonomy 准备完成: {tax_dir}")
    return tax_dir


def _pick_taxdump_tar():
    """选本地 taxdump 压缩档：精简档优先（分发包携带的），其次官方全量档。"""
    for name in (SLIM_TAR_NAME, TAXDUMP_FULL_NAME):
        p = os.path.join(DIRS['databases'], name)
        if os.path.isfile(p):
            return p
    return None


def pack_slim_archive(dst=None, logger=None):
    """把当前 tax_db 三件套打包成分发档 databases/new_taxdump_slim.tar.gz。

    分发口径（2026-11-05）：安装包只带精简档——首启自解压直接得到病毒子树
    库，无需下载全量。仅当当前库是精简口径（<100 万 taxon）才允许打包，
    防止误把全量库（300 万+）打进分发档。
    返回压缩包路径。
    """
    tax_dir = check_path(DIRS['taxonomy'], must_exist=True, in_platform=True)
    n = 0
    with open(os.path.join(tax_dir, 'nodes.dmp'),
              encoding='utf-8', errors='replace') as f:
        for _ in f:
            n += 1
    if n >= 1_000_000:
        raise RuntimeError(
            f"当前 taxonomy 是全量口径（{n:,} taxon）——请先运行 "
            f"`python main.py init-taxonomy --slim` 裁剪后再打包分发档")
    import tarfile
    out = check_path(dst or os.path.join(DIRS['databases'], SLIM_TAR_NAME),
                     must_exist=False, in_platform=True)
    tmp = out + '.part'
    with tarfile.open(tmp, 'w:gz') as tar:
        for name in REQUIRED_MEMBERS:
            src = os.path.join(tax_dir, name)
            if os.path.isfile(src):
                tar.add(src, arcname=name)      # 扁平放包，与解压口径一致
    os.replace(tmp, out)
    if logger:
        logger.log(f"精简分发档已生成: {out}"
                   f"（{os.path.getsize(out) / 1048576:.0f}MB）")
    return out


# ------------------------------------------------------------------
# 精简 taxonomy（病毒子树）与在线核对
# ------------------------------------------------------------------
# 精简口径：只保留病毒域（Viruses, 2026-10 实测 30 万/303 万 taxon，约 10%）
# 子树 + 到根的祖先链 + merged.dmp 整份。宿主（植物/介体昆虫）taxid 不在
# 本地库是预期行为，由 kunpeng 建库入口的 taxid_online_check 在线核对兜底。
SLIM_VIRUS_ROOT = 10239        # NCBI「Viruses」域根 taxid

_ONLINE_CACHE_NAME = 'online_taxid_cache.json'


def _online_cache_path():
    return os.path.join(DIRS['taxonomy'], _ONLINE_CACHE_NAME)


def _online_cache_get(taxid):
    """读在线核对缓存 → (ok: bool, name: str)；无记录/文件损坏 → (False, '')。"""
    try:
        with open(_online_cache_path(), encoding='utf-8') as f:
            v = json.load(f).get(str(int(taxid)))
        if isinstance(v, dict):
            return bool(v.get('ok')), str(v.get('name') or '')
    except (OSError, ValueError, TypeError):
        pass
    return False, ''


def _online_cache_name(taxid, default=''):
    ok, name = _online_cache_get(taxid)
    return name if ok and name else default


def taxid_online_check(taxid, logger=None):
    """本地（精简）taxonomy 查无时，在线核对 taxid 存在性并缓存学名。

    场景：精简 taxonomy 只保留病毒子树，宿主（植物/传毒介体）taxid 查不到
    是预期行为。这里经白名单 eutils（esearch 确认存在 + esummary 取学名）
    在线核对，结果写 databases/tax_db/online_taxid_cache.json——之后离线
    也能过校验，宿主库清单的 species 也从这里兜底。
    返回 (ok: bool, name: str)；任何网络失败返回 (False, '')，不抛异常。
    """
    try:
        t = int(taxid)
    except (TypeError, ValueError):
        return False, ''
    ok, name = _online_cache_get(t)
    if ok:
        return True, name
    try:
        from .ncbi_download import esearch, esummary_taxonomy
        res = esearch(str(t), db='taxonomy', retmax=5)
        if str(t) not in [str(x) for x in res.get('ids', [])]:
            ok, name = False, ''
        else:
            names = esummary_taxonomy([str(t)])
            name = names.get(str(t)) or names.get(t) or ''
            ok = True
    except Exception:                     # noqa: BLE001  离线/限速等一律兜底
        ok, name = False, ''
    try:
        os.makedirs(DIRS['taxonomy'], exist_ok=True)
        cache = {}
        try:
            with open(_online_cache_path(), encoding='utf-8') as f:
                cache = json.load(f)
        except (OSError, ValueError):
            pass
        cache[str(t)] = {'ok': ok, 'name': name,
                         'checked_at': time.strftime('%Y-%m-%d %H:%M:%S')}
        tmp = _online_cache_path() + '.part'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _online_cache_path())
    except OSError:
        pass
    if logger and ok:
        logger.log(f"TaxID {t} = {name or '?'}（在线核对通过，已缓存）")
    return ok, name


def build_slim_taxonomy(src_dir=None, dst_dir=None, logger=None):
    """从全量 NCBI taxonomy 流式裁出病毒子树，生成同格式精简三件套。

    保留：病毒域(SLIM_VIRUS_ROOT)全部后代 + 到根的祖先链 + merged.dmp 整份
    （merged 才 1.9MB 且宿主/病毒旧 taxid 归一都靠它，不裁）。nodes/names
    只留 scientific name。输出与官方 dmp 格式完全一致，TaxonomyGraph 与
    外部工具零适配。
    内存设计：全库 300 万 taxon，全程只用定长 int32 数组（counting-sort
    定位子树），不建大字典，构建期内存 <100MB、不随全库规模膨胀。
    """
    from array import array
    src = check_path(src_dir or DIRS['taxonomy'], must_exist=True)
    dst = os.path.abspath(dst_dir or os.path.join(DIRS['databases'],
                                                  'tax_db_slim'))
    nodes_src = check_path(os.path.join(src, 'nodes.dmp'), must_exist=True)
    names_src = os.path.join(src, 'names.dmp')
    merged_src = os.path.join(src, 'merged.dmp')      # 可选（旧安装可能没有）
    os.makedirs(dst, exist_ok=True)
    n_total = 0

    # ---- pass1: 扫 taxid 上界（定长数组要先分配）----
    maxid = 0
    with open(nodes_src, encoding='utf-8', errors='replace') as f:
        for line in f:
            first = line.split('\t', 1)[0]
            if first.isdigit():
                v = int(first)
                if v > maxid:
                    maxid = v
    if maxid <= 0:
        raise RuntimeError(f"nodes.dmp 无有效行: {nodes_src}")

    # ---- pass2: 每个父节点的孩子计数（counting-sort 第一步）----
    cnt = array('i', bytes(4 * (maxid + 1)))
    with open(nodes_src, encoding='utf-8', errors='replace') as f:
        for line in f:
            p = line.split('|')
            if len(p) >= 3:
                t = int(p[0]); par = int(p[1])
                cnt[par] += 1
                n_total += 1
    if cnt[SLIM_VIRUS_ROOT] <= 0:
        raise RuntimeError(f"taxid {SLIM_VIRUS_ROOT} 子树为空——源库不是"
                           f"官方 new_taxdump 口径: {nodes_src}")

    # ---- pass3: 前缀和 → 扁平孩子表 + 父指针表 ----
    off = array('i', bytes(4 * (maxid + 2)))
    total = 0
    for t in range(maxid + 1):
        off[t] = total
        total += cnt[t]
    off[maxid + 1] = total
    kids = array('i', bytes(4 * total))
    cur = array('i', off)             # off 的副本做游标
    par_arr = array('i', bytes(4 * (maxid + 1)))
    # ---- pass3 续: 按行回填孩子表与父指针 ----
    with open(nodes_src, encoding='utf-8', errors='replace') as f:
        for line in f:
            p = line.split('|')
            if len(p) >= 3:
                t = int(p[0]); par = int(p[1])
                kids[cur[par]] = t
                cur[par] += 1
                par_arr[t] = par

    # ---- pass4: 从病毒根 BFS 保留集合（子树 + 到根祖先链）----
    keep = bytearray(maxid + 1)
    stack = [SLIM_VIRUS_ROOT]
    keep[SLIM_VIRUS_ROOT] = 1
    while stack:
        t = stack.pop()
        for i in range(off[t], off[t + 1]):
            c = kids[i]
            if not keep[c]:
                keep[c] = 1
                stack.append(c)
    t = par_arr[SLIM_VIRUS_ROOT]
    while t > 0 and not keep[t]:
        keep[t] = 1
        t = par_arr[t] if par_arr[t] != t else 0

    # ---- pass5: 写精简 nodes.dmp ----
    n_kept = 0
    with open(nodes_src, encoding='utf-8', errors='replace') as f, \
            open(os.path.join(dst, 'nodes.dmp'), 'w', encoding='utf-8',
                 newline='') as fo:
        for line in f:
            first = line.split('\t', 1)[0]
            if first.isdigit() and keep[int(first)]:
                fo.write(line)
                n_kept += 1

    # ---- pass6: 写精简 names.dmp（只留 scientific name）----
    n_names = 0
    with open(names_src, encoding='utf-8', errors='replace') as f, \
            open(os.path.join(dst, 'names.dmp'), 'w', encoding='utf-8',
                 newline='') as fo:
        for line in f:
            p = [x.strip() for x in line.split('|')]
            if len(p) >= 4 and p[0].isdigit() and keep[int(p[0])] \
                    and p[3] == 'scientific name':
                fo.write(line)
                n_names += 1

    # merged.dmp 整份复制（才 ~2MB，宿主/病毒旧 taxid 归一都靠它）
    if os.path.isfile(merged_src):
        import shutil
        shutil.copyfile(merged_src, os.path.join(dst, 'merged.dmp'))

    if logger:
        logger.log(f"  精简库写入: nodes {n_kept:,} 行 / names {n_names:,} 行"
                   f"（merged {'✓' if os.path.isfile(merged_src) else '无'}）")
    return {'src': src, 'dst': dst, 'n_total': n_total, 'n_nodes': n_kept,
            'n_names': n_names,
            'bytes_nodes': os.path.getsize(os.path.join(dst, 'nodes.dmp')),
            'bytes_names': os.path.getsize(os.path.join(dst, 'names.dmp'))}


def _download(url, dest, logger=None, chunk=1024 * 512):
    """带进度回调的安全下载（URL 白名单 + IP 校验 + 禁重定向）。"""
    url = validate_ncbi_url(url)
    tmp = check_path(str(dest) + '.part', must_exist=False, in_platform=True)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (vp-platform)'})
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(req, timeout=120) as resp, safe_open(tmp, 'wb') as out:
        total = int(resp.headers.get('Content-Length', 0))
        done, last_pct = 0, -1
        while True:
            block = resp.read(chunk)
            if not block:
                break
            out.write(block)
            done += len(block)
            if total:
                pct = done * 100 // total
                if pct != last_pct and pct % 5 == 0:
                    last_pct = pct
                    if logger:
                        logger.log(f"  下载进度 {pct}% ({done // 1048576}/{total // 1048576} MB)")
    final = check_path(dest, must_exist=False, in_platform=True)
    os.replace(tmp, final)


# ------------------------------------------------------------------
# Taxonomy 解析
# ------------------------------------------------------------------
class TaxonomyGraph:
    """轻量 NCBI taxonomy：parent/rank/name/merged，支持祖先链查询。"""

    def __init__(self, tax_dir=None):
        self.tax_dir = check_path(tax_dir or DIRS['taxonomy'], must_exist=True)
        self.parent = {}       # taxid -> parent_taxid
        self.rank = {}         # taxid -> rank
        self.names = {}        # taxid -> scientific name
        self.merged = {}       # old_taxid -> new_taxid
        self._loaded = False

    def load(self):
        if self._loaded:
            return
        nodes = check_path(os.path.join(self.tax_dir, 'nodes.dmp'), must_exist=True)
        names = check_path(os.path.join(self.tax_dir, 'names.dmp'), must_exist=True)
        with safe_open(nodes) as f:
            for line in f:
                parts = [x.strip() for x in line.split('|')]
                if len(parts) >= 3:
                    t, p, r = int(parts[0]), int(parts[1]), parts[2]
                    self.parent[t] = p
                    self.rank[t] = r
        with safe_open(names) as f:
            for line in f:
                parts = [x.strip() for x in line.split('|')]
                if len(parts) >= 4 and parts[3] in ('scientific name', ''):
                    self.names[int(parts[0])] = parts[1]
        merged = os.path.join(self.tax_dir, 'merged.dmp')
        if os.path.isfile(merged):
            with safe_open(merged) as f:
                for line in f:
                    parts = [x.strip() for x in line.split('|')]
                    if len(parts) >= 2:
                        try:
                            self.merged[int(parts[0])] = int(parts[1])
                        except ValueError:
                            pass
        self._loaded = True

    def resolve(self, taxid):
        taxid = int(taxid)
        return self.merged.get(taxid, taxid)

    def name(self, taxid):
        self.load()
        return self.names.get(self.resolve(taxid), f"taxid:{taxid}")

    def rank_of(self, taxid):
        self.load()
        return self.rank.get(self.resolve(taxid), 'no rank')

    def lineage(self, taxid):
        """从自身到根的 [(taxid, rank, name), ...]；环路与超长保护。"""
        self.load()
        chain, seen = [], set()
        t = self.resolve(taxid)
        for _ in range(64):
            if t in seen or t not in self.parent:
                break
            seen.add(t)
            chain.append((t, self.rank.get(t, 'no rank'), self.names.get(t, f'taxid:{t}')))
            if t == 1:
                break
            t = self.parent[t]
        return chain

    def lineage_names(self, taxid):
        """从根到自身的名称列表（GUI 显示用）。"""
        return [name for _, _, name in reversed(self.lineage(taxid))]

    def get_ancestor_at_rank(self, taxid, rank):
        """返回指定 rank 的祖先 taxid（如 'family' / 'genus' / 'species'），无则 None。"""
        for t, r, _ in self.lineage(taxid):
            if r == rank:
                return t
        return None
