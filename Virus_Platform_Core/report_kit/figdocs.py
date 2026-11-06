# -*- coding: utf-8 -*-
"""figdocs — 模块结果页的"图鉴"与说明表（单一数据源）。

模块结果页（module_page.py）把每个阶段/工具运行目录里的文件整理成
「输入 → 图表 → 表格 → 数据/序列/交互页 → 各类文件」六大区。本模块回答
"这张页面上人一定会问"的三个问题：

1. 这张图是什么、怎么读？      → FIGURE_DOCS（按文件名模式匹配）
2. 这个阶段吃的是什么输入？    → STAGE_INPUTS（相对样品根的候选路径）
3. 这个文件类型是什么？        → EXT_DESC / INTERACTIVE_DOCS

纪律：只登记**平台确实会产出的文件名模式**；未登记的图退化成
"文件名当标题"，绝不编造读法。阶段输入模式与 pipeline._stage_* 的
真实实现对照过（2026-11-04）；平台升级增删阶段时要同步改这里。
"""
from __future__ import annotations

import fnmatch
import os

# ------------------------------------------------------------------
# 图鉴：文件名模式（不区分大小写，fnmatch）→ (标题, 读法/图例)
# 顺序即优先级：先命中先用，所以具体模式在前、宽泛模式在后。
# ------------------------------------------------------------------

FIGURE_DOCS = [
    # ---- 流程级（07_report）----
    ('stage_funnel_retention*', '阶段留存率',
     '每一步相对首层（原始 reads）的保留百分比曲线。读法：关注骤降点——'
     '正常样品在「宿主去除」处大幅下降是常态（植物样品宿主常占 80–99%）；'
     '其他步骤的骤降多半是过滤阈值把真信号也切掉了。'),
    ('stage_funnel*', '流程漏斗',
     '从原始 reads 到最终产物的逐层数量条形图。读法：条形长度=该层数量；'
     '相邻两层之间的落差就是该步骤"吃掉"的量，配合 stage_funnel.tsv 逐层核对。'),

    # ---- 进化（05_phylo / quicktree / rtt）----
    ('*roottotip*', 'Root-to-Tip 回归',
     '根到顶端遗传距离 vs 采样时间散点（含拟合线）。读法：显著正斜率=数据'
     '带有可用的时间信号（可做分子定年）；散点乱飞=时间信号弱，'
     '定年结论不可靠。'),
    ('*tree*', '系统进化树',
     '样品序列与公共参考在同一模型下建出的树。读法：枝长≈每位点替代数；'
     '支持值高（≥70%）的分支才可信；样品序列与哪条参考聚在一起，'
     '就说明与它亲缘最近——树回答相似性，不回答"是不是新种"。'),
    ('genome_*', 'contig 基因组图',
     'contig 的 ORF 图谱 + GC 含量曲线（⑩ 报告阶段补画）。读法：'
     '箭头=CDS 与方向；GC 曲线骤变处常对应组装接缝或水平转移区。'),

    # ---- 基因组图（⑨ gbdraw / DFV / ⑥b 示意图）----
    ('*circular*', '基因组圈图',
     '环形基因组结构图（gbdraw/DFV/pycirclize 引擎）：外弧=CDS（颜色按'
     '功能类别或链方向），内层=GC 含量等轨道。读法：先看 CDS 是否覆盖'
     '大部分基因组、正负链是否均衡，再看 GC 曲线有无异常断层。'),
    ('*circle*', '基因组圈图',
     '环形基因组结构图：外弧=CDS（按功能类别/链着色），内层=GC 含量轨道。'
     '读法：CDS 覆盖密度与链分布，GC 断层提示组装缺口或污染。'),
    ('*linear*', '基因组线性图',
     '线性排布的基因组结构图：每个箭头=一条 CDS（方向即箭头方向），'
     '下方为 GC 含量曲线。读法：适合看 ORF 排布是否连续、有无大段无特征区。'),
    ('*.svg', '基因组结构示意图',
     '⑥b ORF 功能注释后的手绘示意图（genome_diagrams/）：每条轨道=一条 '
     'ORF，颜色=证据层级（见图底图例），深浅交替的方块=结构域命中；'
     'SVG 交互版悬停可看域名。'),

    # ---- ②b 已知病毒定量（kv_plot / kv_fig）----
    ('*_depth.*', '病毒参考覆盖深度图',
     'reads 沿参考基因组的覆盖深度分布。读法：均匀且深（≥20×）=定量可信、'
     '可做共识；只在参考局部堆高=可能只是同源区段被拉比，不能算全长感染。'),
    ('freq_multi_metrics*', '物种丰度多指标图',
     '检出物种的多维定量对比（reads 数 / 覆盖 / 深度等）。读法：'
     '单一指标高不足为凭，多条指标同高的物种才值得写进结论。'),
    ('coabundance_*heatmap*', '共丰度热图',
     '物种对之间的丰度相关性（Pearson/Spearman）。读法：高正相关提示'
     '两者常同时出现（可能同一寄主/同批污染），不代表互相作用。'),
    ('coabundance_*stacked*', '相对丰度堆叠图',
     '各样品（或批次）中检出物种的相对丰度构成。读法：看优势物种随'
     '样品的变化，而不是绝对量。'),
    ('coabundance_*', '共丰度分析图',
     '检出物种丰度之间的关系或构成图，具体口径见同名 TSV/JSON。'),

    # ---- 变异与进化动力学（kv_variant_plot / kv_variant_evo）----
    ('*_variant_map.*', '变异位点分布图',
     '变异（SNV/iSNV）沿参考基因组的位置分布。读法：点位密集区可能是'
     '高变区或引物/比对伪影，需结合深度与链支持核对。'),
    ('*_variant_effect.*', '变异效应谱',
     '变异按效应类型（同义/错义/无义等）的分类统计。读法：错义比例高'
     '提示正选择或功能扰动，但先排除测序错误率背景。'),
    ('*_variant_af_dp.*', '变异频率-深度图',
     '每个变异的等位基因频率（AF）vs 覆盖深度（DP）散点。读法：'
     '低深度位点的 AF 不可信；高深度低 AF 的位点才是真正的 iSNV 候选。'),
    ('*_variant_overview.*', '变异总览',
     '该参考序列变异信息的组合概览（位点/类型/频率一图聚合）。'),
    ('*_snipit*', 'SNP 累积对比图',
     '各序列相对参考的 SNP 数量对比（snipit）。读法：柱越短=越接近参考；'
     '用于快速目检"哪条序列最像哪个参考"。'),
    ('*_evo_spectrum*', '分子突变谱',
     '突变类型频谱（如 C>U、G>U 等）。读法：RNA 病毒常见的 C>U 偏移是'
     '预期现象；异常谱型提示比对或去重环节有问题。'),
    ('*_evo_landscape*', '突变景观图',
     '突变沿基因组与沿时间的分布景观。读法：热区=高变位点；'
     '时间轴上的累积趋势反映流行/传播动态。'),
    ('*_evo_density*', '变异密度图',
     '单位长度变异数沿基因组/分组的密度分布。读法：密度峰常落在'
     '抗原区或非结构区，需结合注释判断。'),
    ('*_evo_snpgenie*', 'SNPGenie 位点分析',
     'SNPGenie 口径的位点 dN/dS 与多态性。读法：dN/dS 显著<1=纯化选择；'
     '>1 的位点提示适应选择，样本少时勿过度解读。'),
    ('*_evo_popgen*', '群体遗传统计图',
     '群体遗传指标（核型多样度/连锁等）汇总图，口径见同名 JSON/TSV。'),

    # ---- 同一性与 SDT（sdt_exact / structcmp）----
    ('sdt_heatmap*', 'SDT 全基因组 identity 热图',
     '成对序列的全基因组相似度矩阵（SDT 口径）。读法：对角线=100%；'
     '与物种界定阈值（如 ICTV 常用 ~90%）对照，低于阈值的配对支持'
     '"不同种"的判定。'),
    ('nt_heatmap*', '核酸同一性热图',
     '成对核酸序列同一性矩阵。读法：同 SDT 热图；这是全局比对口径，'
     '局部相似不会拉高数值。'),
    ('sdt_distribution*', 'identity 分布直方图',
     '所有成对 identity 值的分布。读法：双峰分布常提示"组内近缘、组间'
     '远缘"的聚类结构。'),
    ('identity_composite*', '同一性组合图',
     '热图 + 分布的组合视图（nt/aa 两个口径并列）。读法：核酸与蛋白'
     '同一性差异大时，以蛋白口径辅助判断保守性。'),
    ('identity_matrix_heatmap*', '同一性矩阵热图',
     '结构比较得到的同一性矩阵热图，读法同 SDT 热图。'),

    # ---- 比对/重组/地理（toolbox_plots 系列）----
    ('align_qc_overview*', '比对质检概览',
     '多序列比对的质量概览（列缺口率、保留列数等）。读法：缺口率过高'
     '的区间会在建树时引入长枝吸引，先清剪再建树。'),
    ('rdp_events_overview*', '重组事件概览',
     'RDP5 检出的重组事件汇总（受体/供体/断点/P 值）。读法：'
     '只有多重方法同时支持且 P 值显著的断点才值得写进结论。'),
    ('rdp_breakpoint_distribution*', '重组断点分布',
     '断点在基因组上的位置分布与概率曲线。读法：断点聚集区=潜在重组'
     '热区，需与注释特征对照。'),
    ('phylogeo_migration_overview*', '地理迁移概览',
     '祖先状态重建推断的地理迁移事件汇总图。读法：迁移方向是统计推断，'
     '采样偏差会直接影响箭头方向，不要当作传播路径的实证。'),
    ('metadata_coverage_overview*', '元数据覆盖概览',
     '序列元数据（时间/地点/宿主等字段）的覆盖率。读法：字段覆盖率低'
     '会限制下游（定年/地理）分析，先补数再建模。'),
    ('mot_route_year_heatmap*', '路线×年份热图',
     '迁移路线按年份的强度热图，口径见同名 TSV。'),

    # ---- 检测设计（primer / crispr / conan / dsrna / mirna）----
    ('primer_design_overview*', '引物设计概览',
     '设计的引物对在目标序列上的位置与 Tm/GC 分布。读法：先确认'
     '产物长度落在检测窗口（如 qPCR 80–200 bp），再看 Tm 是否成对接近。'),
    ('crispr_design_overview*', 'CRISPR 设计概览',
     'crRNA 候选在目标与脱靶检查上的综合视图。读法：优先取'
     'on-target 得分高且 off-target 为 0 的区间。'),
    ('crispr_top_sets*', 'CRISPR 推荐组合',
     '推荐检测组合（panel）的可视化，配套 ranked_sets.tsv 使用。'),
    ('top_candidates*', 'CONAN 候选位点得分',
     'CONAN-SWIFT（Cas3）候选位点的排序得分图。读法：得分靠前且'
     'negative_checks 无信号的位点优先进入验证。'),
    ('locus_overview*', 'CONAN 位点示意',
     '候选位点附近的序列结构示意（PAM/原型间隔区位置）。'),
    ('mirna_targets_overview*', 'miRNA 靶标概览',
     'miRNA–靶标预测的总体命中统计。读法：预测是序列互补规则打分，'
     '不是实验证据；Top 候选需再查保守性与结合能。'),
    ('mirna_relations*', 'miRNA–靶标关系图',
     'miRNA 与病毒靶标的关系网络/配对图。读法：一 miRNA 多靶标是常态，'
     '重点看是否有跨物种共享的热点 miRNA。'),
    ('mirna_duplex*', '双链配对图',
     'miRNA–靶标双链互补配对详情（种子区/错配位置）。'),

    # ---- 预筛与公共检索（prescreen / landscape）----
    ('prescreen_viral_pct_hist*', '病毒 reads 占比分布',
     '候选 run 的病毒序列占比直方图。读法：用于设定下载优先级——'
     '占比高的 run 更可能产出可用病毒数据。'),
    ('prescreen_depth_vs_viral*', '深度-病毒占比散点',
     '每个 run 的测序深度 vs 病毒占比。读法：右上方（既深又富）优先下载。'),
    ('prescreen_top_viral_taxa*', '主要病毒类群',
     '预筛命中的 top 病毒类群频次。'),
    ('prescreen_funnel_status*', '预筛漏斗',
     '预筛各判定状态（通过/未决）的漏斗图。'),
    ('*landscape*', '公共元数据景观图',
     '公共检索集合的元数据全景面板（时间/地理/宿主分布）。'),

    # ---- 兜底（未登记读法，绝不编造）----
    ('*', '', ''),
]


def figure_doc(name):
    """文件名 → (标题, 读法)。未登记的图返回（文件名去扩展名, 通用提示）。"""
    low = os.path.basename(str(name)).lower()
    for pat, title, read in FIGURE_DOCS:
        if fnmatch.fnmatch(low, pat):
            if not title:
                base = os.path.splitext(low)[0]
                return (base, '本模块产物图（未登记读法，图例以图内为准）。')
            return (title, read)
    return (os.path.splitext(low)[0], '')


# ------------------------------------------------------------------
# 阶段说明与输入（相对样品根；('标签', [候选相对路径/glob])，
# 特殊 token 'project:r1' / 'project:r2' 解析 project.json 登记的原始输入；
# 值为 None 表示"参考库类"输入，只展示说明不找文件）
# ------------------------------------------------------------------

STAGE_PURPOSE = {
    'subsample': '下采样：把巨量 reads 抽稀到可计算规模，保持物种组成不变。'
                 '看抽稀后的 reads 数是否仍够后续深度要求。',
    'fastp': '质控（fastp）：去接头、低质量碱基与重复，产出干净 reads。'
             '质控后数量是后面所有结论的天花板。',
    'fq2fa': '序列转换：FASTQ→FASTA（gzip），喂给只吃 FASTA 的分类/比对程序。'
             '不筛选，只需确认转换前后条数一致。',
    'host': '宿主去除：把宿主 reads 挖掉，剩下的"非宿主池"是病毒的唯一来源。'
            '盯宿主占比（叶/果组织 80–99% 属正常）与保留量。',
    'kvsuite': '已知病毒识别与定量：按参考库比对定量，回答"有哪些已知病毒、'
               '各有多少"。库里没有的病毒这一步一定看不见。',
    'assembly': '组装·分类·提取：把 reads 拼成 contig 并判定病毒来源、'
                '提取病毒序列。contig 数量与长度决定后续一切。',
    'hostana': '宿主预测（ICTV 口径）：给每条病毒 contig 做"宿主是谁"的独立'
               '体检，筛掉植物/细菌/真菌污染。',
    'verify': '候选序列验证：换一套证据源（蛋白库+结构库）复核 contig 身份，'
              '四档判定 known / domain_only / unclassified / novel。',
    'consensus': '共识序列与变异：reads 回比参考得到覆盖与变异，产出共识序列。'
                 '深度不足的位置是 N，不是变异。',
    'orf': 'ORF 预测：找每条序列"从哪开始翻译、翻出多长蛋白"。',
    'orfa': 'ORF 功能注释：按蛋白同源给 ORF 标功能与病毒科/属。'
            '同源=功能像，不等于完整基因组归属。',
    'phylo': '进化树与 SDT：样品序列与公共参考同树比较，回答"跟谁最近"。'
             '树回答相似性，不回答新种。',
    'primer': '引物设计：把发现变成可验证（qPCR/RT-PCR）。失败常因目标序列'
              '太短太碎、保守区凑不出成对引物。',
    'gbdraw': '基因组图：给完整/近完整基因组画线性/环形结构图，'
              '要求有可用的注释特征（GFF）。',
    'report': '可视化报告：只汇总与可视化，不产生新证据；'
              '本页整理的就是它的产物（漏斗、圈图与两份报告入口）。',
}

STAGE_INPUTS = {
    'subsample': [('原始 FASTQ（project.json 登记）', ['project:r1', 'project:r2'])],
    'fastp': [('下采样 R1', ['00_prep/sub_R1.fastq.gz']),
              ('下采样 R2', ['00_prep/sub_R2.fastq.gz']),
              ('原始 R1（未下采样时直接用）', ['project:r1']),
              ('原始 R2（未下采样时直接用）', ['project:r2'])],
    'fq2fa': [('质控后 R1', ['00_prep/fastp_R1.fastq.gz']),
              ('质控后 R2', ['00_prep/fastp_R2.fastq.gz'])],
    'host': [('质控后 R1', ['00_prep/fastp_R1.fastq.gz', '00_prep/sub_R1.fastq.gz']),
             ('质控后 R2', ['00_prep/fastp_R2.fastq.gz', '00_prep/sub_R2.fastq.gz']),
             ('宿主参考库（databases/）', None)],
    'kvsuite': [('去宿主 R1', ['01_host_removal/forward_output_reads.fastq.gz',
                               '01_host_removal/kept_R1.fastq.gz']),
                ('去宿主 R2', ['01_host_removal/kept_R2.fastq.gz']),
                ('已知病毒参考库与索引（databases/）', None)],
    'assembly': [('非宿主 reads R1', ['01_host_removal/kept_R1.fastq.gz',
                                      '02b_kvsuite/viral_R1.fastq.gz']),
                 ('非宿主 reads R2', ['01_host_removal/kept_R2.fastq.gz',
                                      '02b_kvsuite/viral_R2.fastq.gz'])],
    'hostana': [('病毒 contigs', ['03_assembly/viral_contigs.fasta',
                                  '03_assembly/contigs.filtered.fasta'])],
    'verify': [('候选病毒序列', ['03_assembly/viral_contigs.fasta']),
               ('蛋白/结构参考库（databases/）', None)],
    'consensus': [('非宿主 reads R1', ['01_host_removal/kept_R1.fastq.gz']),
                  ('非宿主 reads R2', ['01_host_removal/kept_R2.fastq.gz']),
                  ('病毒序列', ['03_assembly/viral_contigs.fasta'])],
    'orf': [('病毒序列', ['03_assembly/viral_contigs.fasta',
                          '03_assembly/contigs.filtered.fasta'])],
    'orfa': [('ORF 蛋白', ['04_orf/pyrodigal.faa', '04_orf/pyrodigal_rv.faa']),
             ('ORF 核酸', ['04_orf/pyrodigal.ffn', '04_orf/pyrodigal_rv.ffn']),
             ('GFF 坐标', ['04_orf/pyrodigal.gff', '04_orf/pyrodigal_rv.gff'])],
    'phylo': [('病毒序列', ['03_assembly/viral_contigs.fasta',
                            '03c_consensus/consensus.fa']),
              ('公共参考库（数据库根）', None)],
    'primer': [('目标序列', ['03_assembly/viral_contigs.fasta',
                             '05_phylo/*/aln.fasta'])],
    'gbdraw': [('基因组 FASTA', ['03_assembly/viral_contigs.fasta']),
               ('注释 GFF3', ['04b_orf_annot/orf_annotation.gff3'])],
    'report': [('前面各阶段的落盘产物（本样品目录）', None)],
}

# ------------------------------------------------------------------
# 交互页说明（html 文件名（尾段匹配）→ 一句话）
# ------------------------------------------------------------------

INTERACTIVE_DOCS = [
    ('results.html', '本模块的结果整理页'),
    ('report.html', '图表交互报告（viz，plotly 桑基/旭日/热图）'),
    ('report_v2.html', '结论文档（report_kit，学术论文风）'),
    ('chain_report.html', '一键流程规范报告（材料/方法/结果）'),
    ('fastp_report.html', 'fastp 原生质控报告（官方交互版）'),
    ('sankey_host.html', '宿主预测流向桑基图（交互）'),
    ('sunburst_host.html', '宿主类别旭日图（交互）'),
    ('taxburst.html', '分类旭日图（Krona 替代，交互）'),
    ('taxburst_unc.html', '未分类序列旭日图（交互）'),
    ('mirna_targets_table.html', 'miRNA 靶标明细表（可排序）'),
    ('mirna_interactive.html', 'miRNA 靶标交互页（双链/网络/概览）'),
    ('mirna_report.html', 'miRNA 合并报告'),
    ('trace_report.html', 'LOGAN 溯源报告（交互）'),
    ('index.html', '公共元数据交互报告（datavzrd）'),
    ('crispr/report.html', 'CRISPR 设计交互报告'),
]


def interactive_doc(name):
    """html 文件名（含相对路径尾段）→ 说明；未登记返回 ''。"""
    low = str(name).lower().replace('\\', '/')
    for pat, desc in INTERACTIVE_DOCS:
        if low.endswith(pat):
            return desc
    return ''


# ------------------------------------------------------------------
# 文件类型说明（扩展名 → 一句话；"各类文件"区用）
# ------------------------------------------------------------------

EXT_DESC = {
    '.fastq.gz': 'FASTQ 测序 reads（gzip 压缩）',
    '.fq.gz': 'FASTQ 测序 reads（gzip 压缩）',
    '.fa.gz': 'FASTA 序列（gzip 压缩）',
    '.fasta': 'FASTA 序列', '.fa': 'FASTA 序列', '.fna': 'FASTA 序列（核酸）',
    '.faa': 'FASTA 序列（蛋白）', '.ffn': 'FASTA 序列（ORF 核酸）',
    '.nwk': 'Newick 进化树文本', '.nhx': 'Newick 进化树文本',
    '.aln': '多序列比对（FASTA 格式）',
    '.gff': 'GFF 注释坐标', '.gff3': 'GFF3 注释坐标', '.gff2': 'GFF2 注释坐标',
    '.vcf': 'VCF 变异记录', '.bed': 'BED 区间坐标',
    '.m8': 'BLAST tabular 比对结果',
    '.bam': 'BAM 比对文件（二进制）', '.bai': 'BAM 索引',
    '.nexus': 'NEXUS 文件（BEAST 等）',
    '.pdf': 'PDF（矢量出版格式）',
    '.xlsx': 'Excel 表格', '.emf': 'Windows 矢量图（Office 可插）',
    '.tsv.gz': '压缩表格', '.csv.gz': '压缩表格',
    '.rdp5': 'RDP5 工程文件', '.csv': 'CSV 表格', '.json': 'JSON 数据',
}


#: 特定文件名 → 说明（优先于扩展名匹配；外部引擎伴随产物等）
FILE_DOCS = {
    'ambig_info.tsv': 'salmon 伴随产物：每条参考的唯一比对 reads 数（UniqueCount）'
                      '与仅多比对 reads 数（AmbigCount）——判断定量可信度',
    'meta_info.json': 'salmon 伴随产物：本次定量运行元信息（版本/参数/耗时）',
    'cmd_info.json': 'salmon 伴随产物：完整命令行记录',
    'lib_format_counts.json': 'salmon 伴随产物：文库类型推断统计',
    'quant.sf': 'salmon 定量主表：每条参考的 TPM/NumReads 等',
    'expected_bias.gz': 'salmon 伴随产物：片段序列偏好模型',
    'observed_bias.gz': 'salmon 伴随产物：实测序列偏好',
    'fld.gz': 'salmon 伴随产物：片段长度分布',
}


def ext_desc(name):
    """文件名 → 类型一句话（文件名精确匹配优先，长名后缀次之）。"""
    low = os.path.basename(str(name)).lower()
    hit = FILE_DOCS.get(low)
    if hit:
        return hit
    for ext, desc in EXT_DESC.items():
        if low.endswith(ext):
            return desc
    return ''


# ------------------------------------------------------------------
# 工具箱信息（工具 id → (中文名, 一句话说明)）
# ------------------------------------------------------------------

TOOL_INFO = {
    'convert': ('格式转换', 'SRA/FASTQ/FASTA 等格式互转'),
    'loganfetch': ('LOGAN 数据下载',
                   '按 SRA accession 从 Logan 公共 S3 桶拉取预组装 contigs'
                   '（可送序列提取/ORF/溯源等下游）'),
    'fastp': ('质控预处理', 'fastp 去接头、低质量与重复序列'),
    'hostremoval': ('非宿主序列提取', '分类去除宿主 reads，保留非宿主池'),
    'prepchain': ('一键前处理', '质控/转换/去宿主的一键链'),
    'assemble': ('病毒组装', 'SPAdes/MEGAHIT 组装出 contigs'),
    'contigs': ('contig 分类', '对 contigs 做病毒分类并提取病毒序列'),
    'identify': ('病毒序列提取', '分类后提取病毒 reads 与序列'),
    'verify': ('候选序列验证', '蛋白/结构库复核候选 contig 的病毒身份'),
    'hom': ('同源比对', 'BLASTN/BLASTX 本地同源搜索'),
    'kvsuite': ('已知病毒识别与定量', '参考库定量+过滤+深度图+变异全套'),
    'virchain': ('组装验证·一键', '组装→分类→验证链'),
    'kvchain': ('病毒定量与共识·一键', 'kvsuite 全套输出链'),
    'annochain': ('注释三连·一键', 'ORF 预测→功能注释→基因组示意图'),
    'cmpchain': ('⚡ 一键分析',
                 '参考获取→CDS/PEP+保守基因提取→MAFFT+trimAl→建树→SDT'),
    'consensus': ('共识序列与变异', 'reads 回比生成共识与变异清单'),
    'identity': ('NT+AA 同一性表', '成对核酸/蛋白同一性矩阵与热图'),
    'sdt': ('SDT 精确分析', '全基因组成对 identity（SDT 口径）'),
    'orf': ('ORF 预测', 'pyrodigal/orfipy 预测编码区'),
    'orfa': ('ORF 功能注释', '蛋白同源注释 ORF 功能与科属'),
    'genoplot': ('基因组图谱', 'gbdraw 出线性/环形基因组结构图'),
    'structcmp': ('结构比较', '两两结构同一性矩阵与热图'),
    'align': ('序列比对', 'MAFFT 多序列比对（可选 trimAl 清剪）'),
    'quicktree': ('快速建树', '比对后 FastTree/Neighbor-Joining 建树'),
    'rtt': ('时间信号检验', 'Root-to-Tip 回归检验数据时间信号'),
    'rdp': ('RDP 重组分析', 'RDP5 检测重组事件与断点'),
    'phylogeo': ('系统地理分析', '祖先状态重建推断地理迁移'),
    'phylodyn': ('⚡ 一键分析', '定年→地理迁移→汇总报告'),
    'pdprep': ('进化动力学数据接入', '序列+元数据接入、抽样与质检'),
    'beastexport': ('BEAST 作业包导出', '导出 BEAST 分析所需序列/元数据包'),
    'primer': ('引物设计', '目标序列保守区设计 PCR 引物'),
    'crispr': ('CRISPR 检测试剂设计', 'crRNA 候选、组合与脱靶检查'),
    'conan': ('CONAN-SWIFT 检测设计', 'Cas3 检测位点候选与负筛'),
    'cricon': ('CRISPR+CONAN 联合检测设计',
               '双路线编排：交叉审核（覆盖矩阵/近缘补筛查/收敛标记）+ '
               '合并订购表与验证清单'),
    'lamp': ('LAMP 引物组设计', '六引物组 + 环引物（等温扩增）'),
    'dsrna': ('dsRNA 设计', 'dsRNA 检测靶标与臂序列设计'),
    'mirna': ('miRNA 靶标预测', 'miRNA 对病毒基因组的靶标预测'),
}


def tool_info(run_name):
    """运行目录名 → (工具中文名, 说明)；未登记按前缀原样返回。"""
    stem = os.path.basename(str(run_name)).split('_')[0].lower()
    return TOOL_INFO.get(stem, (stem or '工具运行', '工具箱单次运行'))
