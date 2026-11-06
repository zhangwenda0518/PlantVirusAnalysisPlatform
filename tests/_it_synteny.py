# -*- coding: utf-8 -*-
"""GenBank 集合集成测试：导入/巡检/清单 → 集合建树
→ 结果中心 MSA / 树 / SDT API 的 gb: 伪样品链路。"""
import io
import json
import os
import sys
import random
import shutil
# 控制台编码兜底：Windows 默认代码页是 GBK，本脚本的 ✔/✘/⚠ 等字符会让
# print 抛 UnicodeEncodeError（2026-09-11 实测多处踩过）。只改错误处理为
# replace（编码不动，中文照常可读），编不出的字符降级为 '?'。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Bio.Seq import Seq  # noqa: E402
from Bio.SeqRecord import SeqRecord  # noqa: E402
from Bio.SeqFeature import SeqFeature, FeatureLocation  # noqa: E402
from Bio import SeqIO  # noqa: E402

from Virus_Platform_Core.config import DIRS  # noqa: E402
from Virus_Platform_Core.utils import check_path  # noqa: E402

AA = 'ACDEFGHIKLMNPQRSTVWY'
COLL = 'it_synteny'


def check(cond, msg):
    print(('  ok ' if cond else '  FAIL ') + msg)
    assert cond, msg


class _Log:
    def log(self, m, level='INFO'):
        pass

    def close(self):
        pass


random.seed(11)
# 基准基因组：8 个同源基因（每个 ~300aa），beta 少突变、gamma 大量突变
base = [''.join(random.choice(AA) for _ in range(300)) for _ in range(8)]


def derive(mut):
    out = []
    for p in base:
        p = list(p)
        for _ in range(mut):
            j = random.randrange(len(p))
            p[j] = random.choice(AA)
        out.append(''.join(p))
    return out


def make_gb(acc, organism, prots):
    rec = SeqRecord(Seq('N' * (200 + len(prots) * 400)), id=acc, name=acc,
                    description=f'{organism} isolate X, complete genome')
    rec.annotations['organism'] = organism
    rec.annotations['molecule_type'] = 'DNA'
    rec.annotations['date'] = '01-JAN-2026'
    pos = 100
    for i, p in enumerate(prots):
        f = SeqFeature(FeatureLocation(pos, pos + 300, strand=1), type='CDS')
        f.qualifiers = {'product': ['coat protein' if i == 2 else f'protein{i}'],
                        'gene': [f'g{i}'], 'translation': [p]}
        rec.features.append(f)
        pos += 400
    return rec


# 工作目录必须以 '_' 开头：results/ 下非 '_' 开头的目录会被当成真实样品
# 出现在样品列表 / 项目筛选器里（这个目录名以前叫 it_synteny_work，
# 于是每次跑测试都在用户的样品列表里多一个假样品）。
work = check_path(os.path.join(DIRS['results'], '_it_synteny_work'),
                  in_platform=True)
shutil.rmtree(work, ignore_errors=True)
os.makedirs(work, exist_ok=True)

# 清掉同名集合（重跑幂等）
from Virus_Platform_Core.gb_collection import (build_collection_phylo, gb_collection_dir,  # noqa: E402
                              import_local_gb, inspect_collection,
                              read_manifest)
import shutil as _sh  # noqa: E402
_sh.rmtree(gb_collection_dir(COLL), ignore_errors=True)

# ---------- 1. 本机 .gb 导入 ----------
gb_dir = os.path.join(work, 'gb')
os.makedirs(gb_dir, exist_ok=True)
for acc, org, mut in [('NC_900001', 'Virus alpha', 0),
                      ('NC_900002', 'Virus beta', 60),
                      ('NC_900003', 'Virus gamma', 240)]:
    SeqIO.write(make_gb(acc, org, derive(mut)),
                os.path.join(gb_dir, f'{acc}.gb'), 'genbank')

res = import_local_gb(COLL, [os.path.join(gb_dir, f'NC_90000{i}.gb')
                             for i in (1, 2, 3)], logger=_Log())
check(res['imported'] == 3 and res['skipped'] == 0, f'导入 3 条: {res}')
print('  ok 导入完成')

# 重复导入 → 幂等跳过
res2 = import_local_gb(COLL, [os.path.join(gb_dir, 'NC_900001.gb')],
                       logger=_Log())
check(res2['imported'] == 0 and res2['skipped'] == 1, '重复 accession 跳过')

# ---------- 2. 巡检（全量重解析）与快速清单 ----------
st = inspect_collection(COLL)
check(len(st['records']) == 3, f'巡检记录数 = {len(st["records"])}')
check(all(r['cds_count'] == 8 for r in st['records']), '每记录 8 个 CDS')
check(st['warnings'] == [], '正常记录无警告')
check(os.path.isfile(os.path.join(gb_collection_dir(COLL), 'warnings.txt')),
      '巡检警告已持久化 warnings.txt')

mf = read_manifest(COLL)
check(len(mf['records']) == 3 and mf['warnings'] == [],
      'read_manifest 快速读取（不重解析 .gb）')

# 无 CDS 的记录应产生警告
rec_nocds = make_gb('NC_900009', 'Virus delta', derive(10))
rec_nocds.features = []
SeqIO.write(rec_nocds, os.path.join(gb_dir, 'NC_900009.gb'), 'genbank')
import_local_gb(COLL, [os.path.join(gb_dir, 'NC_900009.gb')], logger=_Log())
st2 = inspect_collection(COLL)
check(any('NC_900009' in w for w in st2['warnings']),
      f'无 CDS 记录进警告: {st2["warnings"]}')

# ---------- 4. 集合建树（MAFFT + FastTree） ----------
ph = build_collection_phylo(COLL, tree_tool='fasttree', logger=_Log())
pdir = os.path.join(gb_collection_dir(COLL), 'phylo')
check(os.path.isfile(os.path.join(pdir, 'aln.fasta')), '集合比对 aln.fasta')
check(os.path.isfile(os.path.join(pdir, 'tree.nwk')), '集合树 tree.nwk')
check(ph['n_seqs'] == 4, f'建树序列数 = {ph["n_seqs"]}')
with io.open(os.path.join(pdir, 'tree.nwk'), encoding='utf-8') as f:
    nwk = f.read().strip()
check(nwk.endswith(';') and nwk.count(':') >= 2, 'Newick 内容合法')

# ---------- 5. API：gb: 伪样品贯通 MSA / 树 / SDT 查看器 ----------
import app as appmod  # noqa: E402
c = appmod.app.test_client()

r = c.get('/api/gb/collections')
check(r.status_code == 200, '/api/gb/collections 200')
cols = r.get_json()
mine = next(x for x in cols if x['name'] == COLL)
check(mine['has_phylo'],
      f'集合标记 has_phylo = {mine["has_phylo"]}')
check(any('NC_900009' in w for w in mine['warnings']),
      '列表接口携带持久化警告')

r = c.get('/api/msa/samples')
samples = r.get_json()
gb_sample = next(x for x in samples if x['sample'] == f'gb:{COLL}')
check(gb_sample['label'].startswith('🧬'), f'伪样品标签 = {gb_sample["label"]}')
check(gb_sample['groups'][0]['group'] == COLL and gb_sample['groups'][0]['trees'],
      '伪样品分组含树文件列表')

r = c.get(f'/api/msa/data?sample=gb:{COLL}&group={COLL}')
check(r.status_code == 200, '/api/msa/data (gb:) 200')

r = c.get(f'/api/tree/data?sample=gb:{COLL}&group={COLL}')
check(r.status_code == 200 and r.get_json()['newick'].endswith(';'),
      '/api/tree/data (gb:) 返回 Newick')
check(r.get_json()['tool'] == 'FastTree', '树摘要工具 = FastTree')

r = c.get(f'/api/sdt/data?sample=gb:{COLL}&group={COLL}')
check(r.status_code == 200, '/api/sdt/data 200')
d = r.get_json()
check(len(d['names']) == 4 and len(d['matrix']) == 4
      and all(abs(d['matrix'][i][i] - 100.0) < 1e-9 for i in range(4)),
      'SDT 矩阵对角线 = 100')
check(abs(d['matrix'][0][1] - d['matrix'][1][0]) < 1e-9, '矩阵对称')

# 巡检任务接口（参数校验路径）
r = c.post('/api/gb/inspect', json={'name': COLL})
check(r.status_code == 200 and 'task' in r.get_json(), '/api/gb/inspect 启动')
r = c.post('/api/gb/phylo', json={'name': COLL, 'tree_tool': 'bad'})
check(r.status_code == 400, '非法 tree_tool 返回 400')

# ---------- 6. 独立工作区端点：MSA 数据与自定义树文件 ----------
import shutil as _s2  # noqa: E402
from Virus_Platform_Core.config import PLATFORM_ROOT  # noqa: E402

fake_run = 'structcmp_itsynteny'
rundir = os.path.join(DIRS['tool_runs'], fake_run)
os.makedirs(rundir, exist_ok=True)
_s2.copy(os.path.join(pdir, 'aln.fasta'), os.path.join(rundir, 'aln.fasta'))
r = c.get(f'/api/tool/msa_data?run={fake_run}')
check(r.status_code == 200 and r.get_json()['n_seq'] == 4,
      '/api/tool/msa_data 返回 SNP 展示数据')
r = c.get('/api/tool/msa_data?run=../evil')
check(r.status_code in (400, 404), 'msa_data 运行名注入被拒')
_s2.rmtree(rundir, ignore_errors=True)

r = c.get(f'/api/tree/file?path=run/gb_collections/{COLL}/phylo/tree.nwk')
check(r.status_code == 200 and r.get_json()['newick'].endswith(';'),
      '/api/tree/file 读取平台内树文件')
r = c.get('/api/tree/file?path=results/nothing.not')
check(r.status_code == 400, '非树扩展名返回 400')
r = c.get('/api/tree/file?path=../platform.json')
check(r.status_code == 400, '路径穿越/非树文件被拒')

# ---------- 7. 工具②④病毒库选择 ----------
import app as appmod2  # noqa: E402
d1 = appmod2._resolve_virus_db(None)
check(d1.endswith(('plant', 'virus_db')), '缺省 → 主病毒库')
d2 = appmod2._resolve_virus_db('refvirus')
check(d2.endswith(('ref', 'refvirus_db')), '内置键名 refvirus → refvirus_db')
d3 = appmod2._resolve_virus_db('databases/kunpeng_db/ref')   # RVDB 库已移除
check(d3.endswith(('ref', 'refvirus_db')), '平台相对路径解析')

# contigs 运行携带不存在的库 → 400（先于输入校验或任务启动被拒）
fa_tmp = os.path.join(work, 'two_contigs.fasta')
from Virus_Platform_Core.utils import write_fasta_record  # noqa: E402
with open(fa_tmp, 'wt') as f:
    write_fasta_record(f, 'ctg1', 'ACGT' * 200)
    write_fasta_record(f, 'ctg2', 'ACGT' * 150)
r = c.post('/api/tool/run', json={
    'tool': 'contigs', 'params': {'contigs': fa_tmp, 'db_virus': 'no_such_db'}})
check(r.status_code == 400 and '病毒库' in (r.get_json() or {}).get('error', ''),
      f'未知病毒库被拒: {r.status_code} {r.get_json()}')
r = c.post('/api/tool/run', json={
    'tool': 'contigs', 'params': {'contigs': fa_tmp, 'db_virus': 'k2viral'}})
check(r.status_code in (200, 400),
      '就绪库接受（k2viral 未就绪时 400，就绪时 200 启动任务）')
if r.status_code == 200:
    # 立即取消并删除运行目录：不留空 contigs_* 目录污染工具④下拉
    d2 = r.get_json()
    appmod2.tm.cancel(d2['task'])
    import shutil as _s3  # noqa: E402
    _s3.rmtree(os.path.join(DIRS['tool_runs'], d2['run']),
               ignore_errors=True)
    check(not os.path.isdir(os.path.join(DIRS['tool_runs'], d2['run'])),
          '测试运行目录已清理（不污染下拉列表）')

# 工作目录用完即删，不给用户留下任何测试残留
shutil.rmtree(work, ignore_errors=True)
check(not os.path.isdir(work), '测试工作目录已清理（不污染样品列表）')
print('SYNTENY INTEGRATION TESTS PASSED')
