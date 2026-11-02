# -*- coding: utf-8 -*-
"""平台操作发现层（单一事实源）：工具箱参数 schema + 统一目录/搜索/描述。

为什么要有这个模块（2026-11-01，对标 organelleverse 的 operations 注册表）：
  工具箱 20+ 个工具的参数口径原本只活在 mcp_server.py 的 _TOOL_PARAM_SCHEMAS
  表里——CLI（main.py）与外部 agent 想查口径就得 import 一个会构造 FastMCP
  的 121KB 脚本，不现实。本模块把表迁来作为唯一权威，mcp_server 反过来
  import 本模块（行为零变化）；CLI 用 `python main.py agent-ops` 查询。

对外四件套：
  list_operations()        全量目录（工具箱/管道阶段/执行单动作）
  search(query)            关键词排序（中英标题+描述）
  describe(op_id)          单条详情 + 怎么调（vp_run_tool / vp_pipeline_start /
                           propose_action / HTTP 端点）
  parameter_schema(op_id)  参数 schema（工具箱=真实表，其余=白名单/说明）

约定：本模块只读、无副作用，import 即返回，不碰任何外部工具与网络。
"""
from __future__ import annotations

import re

__all__ = ['TOOL_PARAM_SCHEMAS', 'list_operations', 'search',
           'describe', 'parameter_schema', 'catalog_flat']

# ---------------------------------------------------------------------------
# 工具箱参数 schema（自 mcp_server._TOOL_PARAM_SCHEMAS 原样迁入，未改一字：
# 键=(参数名, 类型, 是否必填, 中文说明)；mcp_server 以别名 import 回去）
# ---------------------------------------------------------------------------
TOOL_PARAM_SCHEMAS = {
    'convert': [
        ('input', 'path', True, '输入文件（.sra 或 .fastq[.gz]）'),
        ('input2', 'path', False, 'R2 FASTQ（双端时给）'),
        ('target', 'str', False, '目标格式 fastq|fasta，默认 fastq（仅 FASTQ 输入可转 fasta）'),
    ],
    'fastp': [
        ('r1', 'path', True, 'R1 FASTQ'),
        ('r2', 'path', False, 'R2 FASTQ'),
        ('dedup', 'bool', False, '质控去重'),
    ],
    'hostremoval': [
        ('r1', 'path', True, 'R1 FASTQ'), ('r2', 'path', False, 'R2 FASTQ'),
        ('confidence', 'float', False, 'kunpeng 分类置信度，默认 0'),
        ('db', 'path', False, '宿主库目录，缺省平台主宿主库'),
        ('classify_r1', 'path', False, '预转换 R1 FASTA（跳过质量行解压，可选）'),
        ('classify_r2', 'path', False, '预转换 R2 FASTA'),
    ],
    'orf': [
        ('fasta', 'path', True, '输入 FASTA（核酸 contigs/基因组）'),
        ('min_aa', 'int', False, '最短 ORF 氨基酸数，默认 100（1~5000）'),
        ('annotate', 'bool', False, '连跑 ⑥b 功能注释'),
        ('orf_tool', 'str', False, 'ORF 预测引擎（透传引擎选择，缺省自动）'),
        ('model', 'str', False, 'CDS 模型 pyrodigal_rv|pyrodigal'),
        ('engine', 'str', False, '注释引擎（缺省自动）'),
        ('db', 'str', False, '注释库路径'),
        ('libs', 'str', False, '注释层 prot|pfam，空=全部'),
        ('min_pident', 'float', False, '层1 Identity% 门槛，0=不过滤'),
        ('min_qcov', 'float', False, '层1 覆盖% 门槛'),
    ],
    'orfa': [
        ('run', 'str', False, '已有 orf_ 运行名（与 fasta 二选一）'),
        ('fasta', 'path', False, 'FASTA 输入（预测+注释一步）'),
        ('model', 'str', False, 'pyrodigal_rv|pyrodigal'),
        ('engine', 'str', False, '注释引擎'), ('db', 'str', False, '注释库'),
        ('libs', 'str', False, 'prot|pfam，空=全部'),
        ('min_pident', 'float', False, '层1 Identity% 门槛'),
        ('min_qcov', 'float', False, '层1 覆盖% 门槛'),
    ],
    'genoplot': [
        ('fasta', 'path', False, 'FASTA（与 ann 至少给一）'),
        ('ann', 'path', False, 'GFF3（配 fasta）或 GenBank（自带注释，忽略 fasta）'),
        ('max_plots', 'int', False, '最多出图数，默认 12'),
        ('mode', 'str', False, 'circular|linear|both，默认 both'),
        ('gb_opts', 'dict', False, 'gbdraw CLI 透传参数字典'),
    ],
    'primer': [
        ('fasta', 'path', True, '输入 FASTA（plain=基因组全长分窗；conserved=已比对 MSA）'),
        ('mode', 'str', False, 'plain|conserved，默认 plain'),
        ('num_return', 'int', False, '每条返回引物对数，默认 3（1~20）'),
        ('specificity', 'bool', False, '宿主特异性 BLAST 检查（需先建宿主 BLAST 库）'),
    ],
    'identify': [
        ('input', 'path', True, '输入文件（FASTQ 或 contigs FASTA）'),
        ('input_type', 'str', False, 'pe|single|fasta，默认 pe'),
        ('input2', 'path', False, 'R2 FASTQ（pe 必填）'),
        ('confidence', 'float', False, '分类置信度，默认 0'),
        ('db_virus', 'str', False, '病毒库：内置键 virus|refvirus 或库目录路径'),
    ],
    'assemble': [
        ('r1', 'path', True, 'R1 FASTQ'),
        ('r2', 'path', False, 'R2 FASTQ（srna 模式可空，其余必填）'),
        ('mode', 'str', False, 'SPAdes 模式，默认 rnaviral（srna 单端组装）'),
        ('memory', 'int', False, '内存上限 GB，默认 64'),
        ('min_len', 'int', False, 'contig 最小长度，默认 200'),
    ],
    'contigs': [
        ('contigs', 'path', True, 'contigs FASTA'),
        ('min_len', 'int', False, '最小长度，默认 200'),
        ('confidence', 'float', False, '分类置信度'),
        ('db_virus', 'str', False, '病毒库：virus|refvirus 或库目录'),
    ],
    'structcmp': [
        ('seqs', 'path', True, '序列 FASTA（≥2 条）'),
        ('max_n', 'int', False, '最多条数，默认 30'),
    ],
    'verify': [
        ('run', 'str', False, 'contig 分类运行名（与 fasta 二选一）'),
        ('fasta', 'path', False, '独立候选 FASTA'),
        ('host', 'str', False, '宿主筛选 all|...，默认 all'),
        ('methods', 'str', False, '证据方法组合 blastx,bfvd，默认全开'),
        ('combine', 'str', False, 'union|intersection，默认 union'),
    ],
    'consensus': [
        ('run', 'str', False, 'contig 分类运行名（参考来源）'),
        ('fasta', 'path', False, '独立参考 FASTA'),
        ('kv_run', 'str', False, 'kvsuite 运行名（配 kv_refs 选参考）'),
        ('kv_refs', 'str', False, 'accession 列表，逗号分隔'),
        ('reads', 'str', False, '回贴 reads 文件/目录，逗号分隔；缺省自动取该运行输入'),
        ('ambig', 'str', False, '模糊碱基字符，默认 N'),
        ('min_qual', 'int', False, '碱基质量门槛，默认 20'),
        ('min_depth', 'int', False, '最小深度，默认 4'),
        ('min_freq', 'float', False, '共识频率门槛，默认 0.5'),
        ('min_mapq', 'int', False, '比对质量门槛，默认 10'),
        ('min_minor_freq', 'float', False, '次要变异频率门槛，默认 0.02'),
        ('min_cov_pct', 'float', False, '最小覆盖度%，默认 10'),
    ],
    'kvsuite': [
        ('stage', 'str', False, 'identify+filter（默认）|all|index|identify|filter|consensus|plot|variant'),
        ('samples', 'str', False, '样品名，逗号分隔（与 reads/sample_sheet 三选一）'),
        ('sample_sheet', 'path', False, '样本表 TSV（name,r1,r2）'),
        ('reads', 'str', False, 'FASTQ 目录/文件，逗号分隔（自动配对 R1/R2）'),
        ('kv_lib', 'str', False, '内置鉴定库键（选库优先，忽略零散 reference/index）'),
        ('reference', 'path', False, '参考序列 FASTA（未选库时）'),
        ('ref_info', 'path', False, '参考注释 TSV'),
        ('engine', 'str', False, 'salmon|minibwa，默认 salmon'),
        ('index_dir', 'path', False, '鉴定库目录（未选库时）'),
        ('min_cov', 'float', False, '最小覆盖%，默认 10'),
        ('min_depth', 'float', False, '最小深度，默认 0.5'),
        ('min_reads', 'int', False, '最小 reads 数，默认 10'),
        ('min_poisson', 'float', False, '泊松门槛，默认 0.3'),
        ('variant_qual', 'float', False, '变异质量门槛，默认 3.5'),
        ('min_freq', 'float', False, '变异频率门槛，默认 0.05'),
        ('aa_label_cutoff', 'float', False, 'AA 标签阈值，默认 0.5'),
        ('max_aa_labels', 'int', False, 'AA 标签上限，默认 40'),
        ('input_vcf', 'path', False, '变异段：外部 VCF（优先于 bam_dir）'),
        ('bam_dir', 'path', False, '变异段：BAM 目录'),
        ('ncbi_email', 'str', False, 'NCBI Entrez 邮箱'),
        ('ncbi_api_key', 'str', False, 'NCBI API key（仅环境变量传递，不进日志）'),
        ('no_genes', 'bool', False, '跳过基因注释'),
        ('no_variant_evo', 'bool', False, '跳过变异演化'),
        ('all_variants', 'bool', False, '输出全部变异'),
        ('no_snpgenie', 'bool', False, '跳过 SNPGenie'),
    ],
    'quicktree': [
        ('seqs', 'path', True, '序列 FASTA'),
        ('method', 'str', False, 'nj|fasttree，默认 nj'),
        ('aligned', 'bool', False, '输入已是比对结果（跳过 MAFFT）'),
        ('max_n', 'int', False, '最多条数，默认 100'),
    ],
    'align': [
        ('seqs', 'path', True, '序列 FASTA（≥2 条，自动判核酸/蛋白）'),
        ('seqs_extra', 'path', False, '第二 FASTA（与 seqs 合并比对）'),
        ('strategy', 'str', False, 'auto|linsi|fast，默认 auto'),
        ('trimal', 'str', False, 'automated1|gappyout|strict|none，默认 automated1'),
        ('max_n', 'int', False, '最多条数，默认 200'),
    ],
    'sdt': [
        ('seqs', 'path', True, '序列 FASTA'),
        ('max_n', 'int', False, '最多条数，默认 30'),
        ('orient', 'bool', False, '比对前定向，默认开'),
        ('seqtype', 'str', False, 'auto|nt|aa，默认 auto'),
        ('palette', 'str', False, '热图配色 sdt|viridis|cividis|coolwarm|magma 等'),
        ('aligned', 'bool', False, '输入为已比对 MSA（直接算公式）'),
    ],
    'identity': [
        ('nt_seqs', 'path', True, '核苷酸 FASTA'),
        ('aa_seqs', 'path', False, '氨基酸 FASTA（空=最长 ORF 翻译）'),
        ('max_n', 'int', False, '最多条数，默认 30'),
        ('palette', 'str', False, '复合热图配色'),
        ('aligned', 'bool', False, '输入为已比对 MSA'),
    ],
    'virchain': [
        ('input', 'path', True, '去宿主序列 FASTA'),
        ('input2', 'path', False, 'R2 FASTA'),
        ('run_identify', 'bool', False, '连跑 ②b 病毒识别段'),
        ('do_verify', 'bool', False, '连跑 ③b 候选验证'),
        ('mode', 'str', False, '组装模式，默认 rnaviral'),
        ('memory', 'int', False, '组装内存 GB，默认 64'),
        ('min_len', 'int', False, 'contig 最小长度，默认 200'),
        ('confidence', 'float', False, '分类置信度'),
        ('host', 'str', False, '验证宿主筛选，默认 all'),
        ('methods', 'str', False, '验证方法 blastx,bfvd'),
        ('combine', 'str', False, 'union|intersection'),
        ('db_virus', 'str', False, '病毒库：virus|refvirus 或库目录'),
    ],
    'kvchain': [],   # 运行时复用 kvsuite（stage 固定 all 全流程）
    'prepchain': [
        ('input', 'path', True, '输入文件（.sra 或 FASTQ[.gz]）'),
        ('input2', 'path', False, 'R2 FASTQ（.sra 输入不需要）'),
        ('confidence', 'float', False, '宿主去除分类置信度'),
        ('dedup', 'bool', False, '质控去重'),
        ('db', 'path', False, '宿主库目录，缺省平台主库'),
    ],
    'annochain': [
        ('fasta', 'path', True, '输入 FASTA（核酸 contigs/基因组）'),
        ('min_aa', 'int', False, '最短 ORF 氨基酸数，默认 100'),
    ],
    'rdp': [
        ('input', 'path', True, '比对 FASTA'),
        ('qc', 'bool', False, '比对质控开关，默认开'),
        ('qc_ref', 'str', False, '质控参考序列名'),
        ('qc_min_len', 'float', False, '最小覆盖长度比例，默认 0.90'),
        ('orf_csv', 'path', False, 'ORF 注释 CSV（可选）'),
    ],
    'phylogeo': [
        ('input', 'path', True, '比对 FASTA'),
        ('meta', 'str', False, '元数据 CSV 路径（性状列）'),
        ('trait', 'str', False, '离散性状列名'),
        ('method', 'str', False, 'nj|fasttree，默认 nj'),
        ('dated_tree', 'path', False, '定年树（可选）'),
        ('coords', 'path', False, '坐标表（样本点层，可选）'),
        ('beast_log', 'path', False, 'BEAST .log（导入）'),
        ('beast_trees', 'path', False, 'BEAST .trees（导入）'),
        ('date_trait', 'str', False, '日期列，默认 year'),
        ('motp_bin', 'float', False, '迁移概率分箱，默认 0'),
    ],
    'dsrna': [
        ('input', 'path', True, '目标序列 FASTA'),
        ('max_mm', 'int', False, 'siRNA 最大错配数'),
        ('off_targets', 'path', False, '脱靶评估库'),
        ('panel', 'str', False, '面板输出选项'),
        ('primer_scope', 'str', False, '引物范围选项'),
        ('isolates', 'str', False, '分离株比对集'),
    ],
    'crispr': [
        ('input', 'path', True, '目标序列 FASTA'),
        ('amp_mode', 'str', False, 'RPA|PCR，默认 RPA'),
        ('profile', 'str', False, '严格度 strict 等，默认 strict'),
        ('probe_type', 'str', False, '探针类型，默认 none'),
        ('nuclease', 'str', False, 'cas12a 默认'),
        ('use_host', 'bool', False, '宿主脱靶检查，默认开'),
        ('group_by_segment', 'bool', False, '按片段分组，默认开'),
        ('near_fasta', 'path', False, '近缘参考 FASTA'),
    ],
    # 2026-10-20：CONAN-SWIFT（Cas3 / I-E）crRNA 设计（t-conan 卡）。
    # 引擎 Virus_Platform_Core/conan_design.py；参数面与网页端表单同源。
    'conan': [
        ('input', 'path', True, '目标 FASTA（多分离物，可多分体）'),
        ('negative', 'path', False, '负组 FASTA（近缘非目标病毒，可选）'),
        ('pam', 'str', False, 'PAM（5′ 前置），默认 AAG'),
        ('spacer_len', 'int', False, 'spacer 长度，默认 32（24–36）'),
        ('seed_len', 'int', False, '种子长度（PAM+1 起），默认 8（4–12）'),
        ('ignored', 'str', False, '种子忽略位（逗号分隔 1 基位置），默认 6'),
        ('max_nonseed_mm', 'int', False, '目标非种子错配上限，默认 4'),
        ('exclude_terminal', 'bool', False, '末端排除开关，默认开'),
        ('terminal_margin', 'int', False, '末端排除 bp，默认 150'),
        ('select_gc', 'bool', False, 'GC 区间过滤开关，默认开'),
        ('gc_min', 'int', False, 'GC 下限 %，默认 35'),
        ('gc_max', 'int', False, 'GC 上限 %，默认 65'),
        ('exclude_homopolymer', 'bool', False, '种子同碱基 run 过滤，默认开'),
        ('homopolymer', 'int', False, '种子同碱基 run 阈值，默认 3'),
        ('group_by_segment', 'bool', False, '按片段标签分组，默认开'),
        ('top_n', 'int', False, '输出候选数，默认 20'),
    ],
    'rtt': [
        ('input', 'path', True, '比对 FASTA'),
        ('meta', 'path', True, '元数据 CSV'),
        ('date_trait', 'str', False, '日期列，默认 year'),
        ('method', 'str', False, 'nj 默认'),
    ],
    'phylodyn': [
        ('mode', 'str', False, 'pipeline|stage，默认 pipeline'),
        ('stage', 'str', False, 'stage 模式下的阶段名'),
        ('from_run', 'str', False, '上一轮专项运行名（断点续跑）'),
        ('input', 'path', False, '比对 FASTA（stage 模式）'),
        ('meta', 'path', False, '元数据 CSV'),
        ('tree', 'path', False, 'Newick 树'),
        ('rename_map', 'path', False, '重命名映射 TSV'),
        ('divergence_tree', 'path', False, '分歧时间树'),
        ('coord_table', 'path', False, '坐标表'),
        ('trait', 'str', False, '离散性状列，默认 region'),
        ('date_col', 'str', False, '日期列名'),
        ('date_trait', 'str', False, '日期解析口径'),
        ('seq_len', 'int', False, '序列长度'),
        ('methods', 'str', False, '方法列表，逗号分隔，默认 treetime'),
        ('reroot', 'str', False, '重新生根方式，默认 least-squares'),
        ('keep_root', 'bool', False, '保留原根'),
        ('relax_sigma', 'float', False, '松弛钟 sigma（0=关）'),
        ('relax_coupling', 'float', False, '松弛钟耦合'),
        ('drt', 'bool', False, 'DRT 回归，默认开'),
        ('drt_perm', 'int', False, 'DRT 置换次数，默认 200'),
        ('clock_rate', 'float', False, '固定速率 1e-12~1.0（与松弛钟互斥）'),
        ('skyline', 'bool', False, '天际线'),
        ('n_skyline', 'int', False, '天际线块数，默认 10'),
        ('gen_per_year', 'float', False, '每年世代数，默认 50'),
        ('prune', 'bool', False, '剪枝'),
        ('prune_method', 'str', False, 'fps 等'),
        ('prune_date_resolution', 'str', False, 'month 等'),
        ('prune_max_reps', 'int', False, '默认 3'),
        ('prune_min_snp_diff', 'int', False, '默认 2'),
        ('prune_clade_cutoff', 'int', False, '默认 5'),
        ('sampling_bias_correction', 'str', False, 'auto 等'),
        ('cross_check', 'bool', False, '交叉校验，默认开'),
        ('rssp_bs', 'int', False, 'RTT bootstrap，默认 0'),
        ('seed', 'int', False, '随机种子'),
        ('ancestral', 'bool', False, '祖先状态重构'),
        ('homoplasy', 'bool', False, '同塑性检查'),
        ('timeout', 'int', False, '超时秒，默认 3600'),
    ],
    'pdprep': [
        ('source', 'str', False, 'manual|gb|ncbi，默认 manual'),
        ('mode', 'str', False, 'preview＝只检索出清单（写 pdprep/preview.json）不导入；空＝导入'),
        ('fasta_text', 'str', False, '粘贴 FASTA（manual）'),
        ('meta_text', 'str', False, '粘贴元数据（manual）'),
        ('species', 'str', False, '物种名（ncbi）'),
        ('taxid', 'str', False, 'TaxID（ncbi）'),
        ('full_only', 'bool', False, '仅完整基因组'),
        ('date_mode', 'str', False, '日期解析 mid 等'),
        ('date_from', 'str', False, '日期下限'),
        ('date_to', 'str', False, '日期上限'),
        ('collection', 'str', False, 'GenBank 集合名（gb）'),
        ('gb_files', 'path', False, 'GenBank 文件路径'),
        ('accessions', 'str', False, 'accession 列表'),
        ('term', 'str', False, 'Entrez 检索式'),
        ('max_records', 'int', False, '最多条数，默认 200'),
        ('dedup', 'bool', False, '去冗余'),
        ('strict', 'bool', False, '严格元数据校验'),
        ('govern', 'bool', False, '治理规则，默认开'),
        ('allow_noloc', 'bool', False, '允许无定位'),
        ('qc', 'str', False, '比对质控 off 等，默认 off'),
        ('qc_max_gap', 'float', False, '默认 0.10'),
        ('qc_max_n', 'float', False, '默认 0.05'),
        ('qc_min_len', 'float', False, '默认 0.90'),
        ('threads', 'int', False, '比对线程，默认 4'),
        ('coords', 'str', False, '坐标表'),
        ('sub', 'bool', False, '抽样'),
        ('sub_mode', 'str', False, 'random 等'),
        ('sub_seed', 'int', False, '抽样种子'),
        ('sub_n', 'int', False, '抽样数'),
        ('sub_region', 'str', False, '抽样区域'),
    ],
    'mirna': [
        ('mirna_text', 'str', False, '粘贴 miRNA 序列（与 mirna_fasta 二选一）'),
        ('virus_text', 'str', False, '粘贴病毒序列（与 virus_fasta 二选一）'),
        ('mirna_fasta', 'path', False, 'miRNA FASTA'),
        ('virus_fasta', 'path', False, '病毒 FASTA'),
        ('full_mode', 'bool', False, '全模式'),
        ('algorithms', 'str', False, '算法列表，逗号/空格分隔'),
    ],
    'beastexport': [
        ('input', 'path', True, '比对 FASTA'),
        ('meta', 'str', False, '元数据 CSV 路径'),
        ('trait', 'str', False, '离散性状列，默认 region'),
        ('date_trait', 'str', False, '日期列，默认 year'),
        ('virus_tag', 'str', False, '病毒名标签'),
        ('iqtree_log', 'path', False, 'IQ-TREE 日志（定年用，可选）'),
        ('coords', 'path', False, '坐标表'),
        ('prior', 'str', False, '先验 bdsky 等，默认 bdsky'),
    ],
}

# kvchain 是 kvsuite 的一键封装（内部把 stage 固定成 all，参数就是 kvsuite
# 那套 reads/kv_lib/reference…）。以前这里留空 → list_platform_tools 对
# kvchain 报「0 个参数」，模型只能瞎猜 reads 的键名。
TOOL_PARAM_SCHEMAS['kvchain'] = list(TOOL_PARAM_SCHEMAS['kvsuite'])


# ---------------------------------------------------------------------------
# 统一目录（工具箱 / 管道阶段 / 执行单动作）
# ---------------------------------------------------------------------------
def _tool_entries():
    """工具箱条目（TOOL_REGISTRY + TOOL_PARAM_SCHEMAS 拼装，含调用口径）。"""
    from Virus_Platform_Core.web.tools_api import TOOL_REGISTRY, LIGHT_TOOLS
    out = []
    for name in sorted(TOOL_REGISTRY):
        spec = TOOL_REGISTRY[name]
        params = [{'key': k, 'type': t, 'required': bool(r), 'desc': d}
                  for (k, t, r, d) in TOOL_PARAM_SCHEMAS.get(name, ())]
        out.append({
            'id': name, 'kind': 'tool',
            'title': spec['title'], 'title_en': spec['title_en'],
            'desc': (spec['job'].__doc__ or '').strip()[:400],
            'need_db': bool(spec['need_db']),
            'weight': 'light' if name in LIGHT_TOOLS else 'heavy',
            'required_params': [p['key'] for p in params if p['required']],
            'params': params,
            'invoke': {'mcp': 'vp_run_tool', 'order': "propose_action(run_tool)",
                       'http': 'POST /api/tool/run'},
        })
    return out


def _stage_entries():
    """管道阶段条目（STAGE_ORDER × STAGE_NAMES，重跑口径见 invoke）。"""
    from Virus_Platform_Core.pipeline import STAGE_ORDER, STAGE_NAMES, \
        STAGE_NAMES_EN
    out = []
    for key in STAGE_ORDER:
        out.append({
            'id': key, 'kind': 'stage',
            'title': STAGE_NAMES.get(key, key),
            'title_en': STAGE_NAMES_EN.get(key, ''),
            'desc': '样品管道阶段；重跑走执行单 rerun_stage 或 analyze --stages',
            'params_note': '参数=分析请求体白名单键（见 parameter_schema），'
                           '边界值见 TUNABLE_PARAMS',
            'invoke': {'mcp': 'vp_pipeline_start',
                       'order': 'propose_action(rerun_stage)',
                       'cli': 'main.py analyze --sample S --stages <key>'},
        })
    return out


def _action_entries():
    """执行单动作条目（executors.ACTIONS）。"""
    from Virus_Platform_Core.ai_agent.executors import ACTIONS, PLAN_ACTIONS
    out = []
    for name, spec in sorted(ACTIONS.items()):
        out.append({
            'id': name, 'kind': 'action',
            'title': spec['title'][0], 'title_en': spec['title'][1],
            'desc': ('AI 只能起草；用户在对话坞批准后才执行'
                     + ('' if name in PLAN_ACTIONS else
                        '（不可进 plan 多步计划）')),
            # 2026-11-02：risk 是批准门槛的唯一口径（executors.ACTIONS 登记）。
            # 以前目录不暴露它，外部 agent 看不出哪些动作能进「自动重试」白名单，
            # 只能靠猜——现在随目录给出。
            'risk': spec.get('risk', 'confirm'),
            'in_plan': name in PLAN_ACTIONS,
            'invoke': {'order': f'propose_action({name})'},
        })
    return out


def list_operations(kind=None):
    """全量操作目录。kind: None=全部 / 'tool' | 'stage' | 'action'。"""
    items = _tool_entries() + _stage_entries() + _action_entries()
    if kind:
        items = [x for x in items if x['kind'] == kind]
    return {'total': len(items), 'items': items}


def catalog_flat():
    """扁平 [(id, kind, zh标题, 一句话)] —— 供 CLI/日志摘要用。"""
    for it in list_operations()['items']:
        yield it['id'], it['kind'], it['title'], (it.get('desc') or '')[:60]


def search(query, limit=8):
    """关键词搜索（中英标题/描述/id）。命中越多越靠前，稳定排序。"""
    q = str(query or '').strip().lower()
    if not q:
        return {'query': query, 'total': 0, 'items': []}
    toks = [t for t in re.split(r'[\s,，]+', q) if t]

    def _score(it):
        hay = ' '.join([it['id'], it.get('title', ''), it.get('title_en', ''),
                        it.get('desc', ''), it.get('title_zh', '')]).lower()
        s = 0
        for t in toks:
            if t in it['id']:
                s += 6
            if t in hay:
                s += 3
            # 中文无分词：整词不中时按字符覆盖度给部分分
            if any(ch in hay for ch in t):
                s += 1
        return s

    items = [it for it in list_operations()['items'] if _score(it) > 0]
    items.sort(key=lambda it: (-_score(it), it['kind'], it['id']))
    return {'query': query, 'total': len(items),
            'items': items[:max(1, int(limit))]}


def describe(op_id):
    """单条详情；找不到返回 None。"""
    for it in list_operations()['items']:
        if it['id'] == op_id:
            return it
    return None


def parameter_schema(op_id):
    """参数 schema：工具箱=真实表；阶段=分析请求体白名单+可调键边界；
    执行单动作=各自必填键说明。找不到返回 None。"""
    it = describe(op_id)
    if it is None:
        return None
    if it['kind'] == 'tool':
        return {'id': op_id, 'kind': 'tool',
                'required': it['required_params'],
                'params': it['params'],
                'note': '键值口径与网页端表单同源；未列出的键被忽略，'
                        '缺必填项提交时当场报中文原因'}
    if it['kind'] == 'stage':
        from Virus_Platform_Core.ai_agent.executors import TUNABLE_PARAMS, \
            BODY_PARAM_KEYS
        return {'id': op_id, 'kind': 'stage',
                'required': [],
                'params': [{'key': k, 'type': 'enum/range', 'required': False,
                            'desc': f'边界 {v}'}
                           for k, v in sorted(TUNABLE_PARAMS.items())],
                'note': '完整白名单键: ' + ', '.join(sorted(BODY_PARAM_KEYS))}
    # 执行单动作
    hints = {
        'rerun_stage': [('sample', 'str', True, '样品名'),
                        ('stages', 'list', True, '阶段键列表（见 stage 类目）')],
        'retry_failed': [('sample', 'str', True, '样品名')],
        'download_runs': [('runs', 'list', True, 'SRR/ERR/DRR/CRR 编号 1~500 条')],
        'run_tool': [('tool', 'str', True, '工具名（tool 类目里选）'),
                     ('params', 'dict', True, '该工具参数（见 tool 的 schema）')],
        'create_sample': [('r1', 'path', True, 'R1 FASTQ(.gz) 绝对路径'),
                          ('r2', 'path', False, 'R2 FASTQ'),
                          ('sample', 'str', False, '样品名（缺省按文件名推）')],
        'build_host_db': [('genome', 'path', True, '宿主基因组 FASTA'),
                          ('taxid', 'int', True, '宿主 TaxID（如 4081 番茄）')],
        'plan': [('steps', 'list', True,
                  '1~6 步，每步 {"action":…,"params":…}')],
    }
    return {'id': op_id, 'kind': 'action',
            'required': [k for (k, _t, r, _d) in hints.get(op_id, [])
                         if r],
            'params': [{'key': k, 'type': t, 'required': r, 'desc': d}
                       for (k, t, r, d) in hints.get(op_id, [])],
            'risk': it.get('risk', 'confirm'),
            'in_plan': bool(it.get('in_plan')),
            'note': '起草后待批；批准前不会运行（计划≠已执行）。'
                    + ('risk=auto_ok：仅「自动重试」二级白名单可用；'
                       'risk=confirm：任何级别都需人工确认'
                       if it.get('risk') != 'auto_ok' else
                       'risk=auto_ok：阶段失败后可在自动重试白名单内自动执行')}
