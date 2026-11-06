# -*- coding: utf-8 -*-
"""比较基因组一键链（cmpchain）：参考集获取 → CDS/PEP + 保守基因提取 →
MAFFT+trimAl 比对 → 建树（基因组树 + 保守基因树）→ SDT 同一性矩阵。

一条任务打通「比较基因组分析」组的上下游：给一个 GenBank 集合名 / ICTV
科属选择 / accession 列表，产出：

  00_refs/    参考集 provenance（来源、accession、taxonomy.tsv、refs.fa）
  01_extract/ 全量提取（genome.fa / CDS.fa / PEP.fa / CDS/ / PEP/ /
              genes.tsv）+ marker/ 保守基因归组（RdRp/CP/MP…）+ marker_report.tsv
  02_align/   genome/ 全基因组 MSA；gene/<基因>_<cds|pep>/ 基因 MSA
  03_tree/    genome/ 基因组树；gene/<基因>_<cds|pep>/tree.nwk 埇因树
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

STAGE_ORDER = ('refs', 'cds', 'align', 'tree', 'sdt')

STAGE_TITLES = {
    'refs': ('参考集获取', 'Reference acquisition'),
    'cds': ('CDS/PEP + 保守基因提取', 'CDS/PEP + marker extraction'),
    'align': ('MAFFT 比对 + trimAl 清剪', 'MAFFT + trimAl alignment'),
    'tree': ('进化树（基因组树 + 保守基因树）',
             'Phylogeny (genome + gene sets)'),
    'sdt': ('SDT 同一性矩阵', 'SDT identity matrix'),
}

STAGE_DIR_NAMES = {'refs': '00_refs', 'cds': '01_extract', 'align': '02_align',
                   'tree': '03_tree', 'sdt': '04_sdt'}

# 各阶段在总进度映射里的占比（工具 job 的高水位进度映射用）
STAGE_SPAN = {'refs': (0.00, 0.10), 'cds': (0.10, 0.30), 'align': (0.30, 0.55),
              'tree': (0.55, 0.85), 'sdt': (0.85, 1.00)}

_GB_EXTS = ('.gb', '.gbk', '.gbff', '.genbank')
_MIN_TREE_N = 4          # 基因树最少序列数（RAxML-NG 自举下限）
_MIN_ALIGN_N = 4         # 基因 MSA 最少序列数


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
    return log if callable(log) else (lambda *_a, **_k: None)


def _prog_adapter(progress):
    if callable(progress):
        return progress
    return lambda *_a, **_k: None


def _slug(name, cap=40):
    """集合名 slug（中英文/数字/_/-，与集合名白名单一致）。"""
    s = re.sub(r'[^A-Za-z0-9\u4e00-\u9fff_\-]+', '_', str(name or '').strip())
    return s.strip('_')[:cap] or 'set'


def _abort_if_cancelled(cancel):
    if cancel is not None and getattr(cancel, 'is_set', lambda: False)():
        raise RuntimeError('已取消（阶段之间检查）')


# === PART2 ===
