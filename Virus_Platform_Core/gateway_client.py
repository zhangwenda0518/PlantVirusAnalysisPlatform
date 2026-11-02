# -*- coding: utf-8 -*-
"""自建在线库（39.106.101.94）REST 网关客户端 —— 只读 GET，国内可达。

服务端：plant_virus_db_pipeline 的 19.api_gateway（Flask，nginx 反代 /api/v1，
CORS 全开，限速 120 req/min/IP）。本地源码副本：
D:/桌面/C-host_classify/plant_virus_db_pipeline/19.api_gateway

当前消费者：
  pdprep（进化动力学数据接入）source=genbank + gateway=1：先在本库圈定
  accession 清单，再走既有 Entrez efetch 管道取正文与完整特征表
  （collection_date / geo_loc_name）。本库序列记录不含序列正文，也不保证
  日期/地点齐全，所以只当「圈定器」用。
"""

import json
import os
import urllib.request
import urllib.parse

GW_BASE = os.environ.get('VP_GATEWAY_BASE', 'http://39.106.101.94/api/v1').rstrip('/')
TIMEOUT = 25          # 每页超时（秒）
PAGE_SIZE = 500
MAX_PAGES = 6         # 上限 3000 条圈定结果，够 pdprep max_records(≤2000) 用


def _get(path, params=None):
    """单次 GET，返回解析后的 JSON；网络/HTTP 异常统一转 RuntimeError（带可操作文案）。"""
    url = GW_BASE + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode('utf-8', 'replace'))
    except Exception as e:
        raise RuntimeError(f'在线库请求失败（{url}）：{e.__class__.__name__}: {e}') from e


def health():
    """网关健康检查 → dict（status/datasets/documents）或 RuntimeError。"""
    return _get('/health')


def _dataset_rows(dataset, params, want, log):
    """翻页拉数据集，返回 (行列表, 数据集总条数)。want=0 表示全量（受 MAX_PAGES 保护）。"""
    rows, page, total = [], 1, 0
    while page <= MAX_PAGES:
        d = _get('/' + dataset, dict(params, page=page, page_size=PAGE_SIZE))
        data = d.get('data') or []
        if not data:
            break
        rows.extend(data)
        total = d.get('meta', {}).get('total', 0)
        if log:
            log(f'在线库 {dataset}: 第 {page}/{max(1, -(-total // PAGE_SIZE))} 页，'
                f'累计 {len(rows)}/{total} 条')
        if want and len(rows) >= want:
            break
        if len(data) < PAGE_SIZE:
            break
        page += 1
    return rows, total


def resolve_accessions(species=None, taxid=None, full_only=False,
                       max_records=200, log=None):
    """按物种/taxid 在在线库圈定 accession 清单。

    数据集 reference/sequences（GenBank 记录级）；full_only 改用
    reference/complete（全长基因组）。匹配策略按序回退：
      1. 学名等值 ?Species_NCBI=<species>（canonical 列，最准）
      2. 模糊 ?q=<species>（覆盖俗名/异名）
      3. taxid 等值 ?Taxid=<taxid>
      4. 仍 0 条 → RuntimeError（带可操作建议）

    返回 (accessions 列表, info dict)；info 供任务日志播报匹配情况。
    """
    if not species and not taxid:
        raise ValueError('需要物种学名或 taxid 之一')
    # full_only 不切换到 reference/complete 数据集（它只有少量 RefSeq/代表记录），
    # 而是 sequences 数据集 + Nuc_Completeness=complete 等值过滤（实测 TMV：
    # complete 数据集 2 条 vs 组合过滤 98 条）。
    dataset = 'reference/sequences'
    base = {'page_size': PAGE_SIZE}
    if full_only:
        base['Nuc_Completeness'] = 'complete'
    cand = []
    if species:
        cand.append(('学名等值', dict(base, Species_NCBI=species)))
        cand.append(('模糊检索', dict(base, q=species)))
    if taxid:
        cand.append(('taxid 等值', dict(base, Taxid=str(taxid))))
    rows, total, how = [], 0, ''
    for how, params in cand:
        rows, total = _dataset_rows(dataset, params, max_records, log)
        if rows:
            break
    if not rows:
        raise RuntimeError(
            f'在线库没有匹配「{species or taxid}」的参考序列；'
            '可以取消勾选「在线库圈定」改走 NCBI 检索，或换个学名/俗名再试。')
    accs = [str(r.get('Accession') or '').strip() for r in rows]
    accs = [a for a in accs[:max_records] if a]
    species_hit = ''
    for r in rows:
        if r.get('Species_NCBI'):
            species_hit = r['Species_NCBI']
            break
    info = {'dataset': dataset, 'matched': len(accs), 'strategy': how,
            'species_hit': species_hit, 'total_in_dataset': total}
    if log:
        log(f'在线库命中 {len(accs)} 条 accession（策略: {how}，数据集: {dataset}，'
            f'代表物种: {species_hit or "-"}）')
    return accs, info
