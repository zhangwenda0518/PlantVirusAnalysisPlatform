# -*- coding: utf-8 -*-
"""Generate Chinese-language Figures 1-3 (redrawn) + Figure 4 (real measured data,
SDT identity heatmap of the bundled CMV RNA3 set, in zh and en)."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import os, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)
from Virus_Platform_Core.align_qc import pairwise_identity, iter_fasta

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei']
plt.rcParams['axes.unicode_minus'] = False

OUT = os.path.join(os.path.dirname(__file__), 'figures_zh')
os.makedirs(OUT, exist_ok=True)

COL = dict(
    input_fc='#E8EEF7', input_ec='#4C72B0', stage_fc='#DDE7F2', stage_ec='#4C72B0',
    group_fc='#F3F6FA', group_ec='#8FA8C8', dec_fc='#FDF1DC', dec_ec='#D9A441',
    out_fc='#DFF0DC', out_ec='#5B9A57', eng_fc='#EADDF3', eng_ec='#7D5BA6',
    warn_fc='#FBE3E3', warn_ec='#C0504D', gray_fc='#F2F2F2', gray_ec='#AAAAAA',
)

def box(ax, x, y, w, h, text, fc, ec, fs=8.2, weight='normal', lw=1.1):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle='round,pad=0.004,rounding_size=0.012',
                                fc=fc, ec=ec, lw=lw, mutation_aspect=1))
    ax.text(x + w / 2, y + h / 2, text, ha='center', va='center', fontsize=fs, weight=weight)

def arrow(ax, x1, y1, x2, y2, color='#555555', lw=1.2, rad=0.0):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle='-|>', mutation_scale=10,
                                 color=color, lw=lw, connectionstyle=f'arc3,rad={rad}'))

def canvas(w, h):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis('off')
    return fig, ax

C = COL

# =============== 图1 平台总体架构与15阶段流水线 ===============
fig, ax = canvas(7.6, 9.0)
box(ax, 0.03, 0.935, 0.42, 0.05, '配对/单端测序数据（FASTQ[.gz]）', C['input_fc'], C['input_ec'], fs=8.6, weight='bold')
box(ax, 0.52, 0.925, 0.45, 0.065, '仅回环监听的 Flask 应用（212 条路由）\npywebview 桌面窗口 / 浏览器，中英双语', C['gray_fc'], C['gray_ec'], fs=7.4)
groups = [
    ('1  测序数据预处理', ['子采样', 'fastp 质控', 'seqkit fq2fa', 'kunpeng 宿主去除']),
    ('2  病毒鉴定', ['kvsuite：salmon / minibwa 定量 | 双轨过滤 | 共识序列 | 变异检测']),
    ('3  病毒组装', ['SPAdes（rnaviral）', 'contig 分类']),
    ('4  候选验证', ['DIAMOND blastx + CDD 结构域 →  known / novel / domain_only / unclassified']),
    ('5  宿主预测与注释', ['ICTV 宿主预测', 'pyrodigal(-rv) ORF', 'RefSeq / HMM 功能注释']),
    ('6  下游分析与报告', ['MAFFT + trimAl', 'FastTree / IQ-TREE 3', 'SDT 矩阵', 'primer3 引物', 'gbdraw + HTML 报告']),
]
BH, GAP, TOP = 0.105, 0.032, 0.885
for i, (name, chips) in enumerate(groups):
    top = TOP - i * (BH + GAP)
    y = top - BH
    box(ax, 0.03, y, 0.94, BH, '', C['group_fc'], C['group_ec'], lw=1.0)
    ax.text(0.045, top - 0.018, name, fontsize=8.8, weight='bold', va='center')
    n = len(chips)
    x0, x1 = 0.06, 0.955
    cw = (x1 - x0) / n
    for j, c in enumerate(chips):
        box(ax, x0 + j * cw, y + 0.013, cw - 0.015, BH - 0.035, c, C['stage_fc'], C['stage_ec'], fs=6.9)
    if i == 0:
        arrow(ax, 0.24, 0.935, 0.24, top)
    if i > 0:
        arrow(ax, 0.5, top + GAP, 0.5, top)
box(ax, 0.03, 0.030, 0.455, 0.056, '任务引擎：重任务=2 / 轻任务=4 并发闸门，\nFIFO 排队，SSE 实时进度，断电重启恢复', C['gray_fc'], C['gray_ec'], fs=7.2)
box(ax, 0.515, 0.030, 0.455, 0.056, '阶段断点续跑（.done 标记 + 输入指纹），\nproject.json 全程留痕，EMA 进度/剩余时间预估', C['gray_fc'], C['gray_ec'], fs=7.2)
box(ax, 0.03, 0.0, 0.94, 0.024, 'PyInstaller 一键分发：内置 30 个外部工具  |  程序包约 1 GB  |  数据库包约 3.6 GB（可选）', C['out_fc'], C['out_ec'], fs=7.4)
fig.savefig(os.path.join(OUT, 'figure1_architecture_zh.png'), dpi=300, bbox_inches='tight')
fig.savefig(os.path.join(OUT, 'figure1_architecture_zh.svg'), bbox_inches='tight')
plt.close(fig)

# =============== 图2 已知病毒鉴定与定量套件 ===============
fig, ax = canvas(7.6, 8.0)
box(ax, 0.14, 0.945, 0.42, 0.042, '去宿主测序数据（FASTQ）', C['input_fc'], C['input_ec'], fs=8.6, weight='bold')
arrow(ax, 0.35, 0.945, 0.19, 0.918, rad=0.12)
arrow(ax, 0.35, 0.945, 0.52, 0.918, rad=-0.12)
box(ax, 0.04, 0.848, 0.30, 0.070, '概率引擎\nsalmon k=31 EM 定量\n（多映射读段概率分摊）', C['eng_fc'], C['eng_ec'], fs=7.5)
box(ax, 0.37, 0.848, 0.30, 0.070, '比对引擎\nminibwa mem 真实比对\n（逐参考唯一比对计数）', C['eng_fc'], C['eng_ec'], fs=7.5)
arrow(ax, 0.19, 0.848, 0.28, 0.812)
arrow(ax, 0.52, 0.848, 0.43, 0.812)
box(ax, 0.05, 0.768, 0.60, 0.044, '覆盖度/深度后端：pandepth | samtools | 内置实现（三者逐位一致）', C['gray_fc'], C['gray_ec'], fs=7.4)
arrow(ax, 0.35, 0.768, 0.35, 0.738)
box(ax, 0.08, 0.692, 0.54, 0.046, '基础门槛：唯一比对读段 ≥ 10  且  平均深度 ≥ 0.5×', C['dec_fc'], C['dec_ec'], fs=8.2)
arrow(ax, 0.35, 0.692, 0.20, 0.660, rad=0.08)
arrow(ax, 0.35, 0.692, 0.51, 0.660, rad=-0.08)
ax.text(0.245, 0.672, '满足其一', fontsize=6.8, ha='center', color='#666666')
ax.text(0.455, 0.672, '或', fontsize=6.8, ha='center', color='#666666')
box(ax, 0.04, 0.545, 0.30, 0.115, 'A 轨（基因组级）\n覆盖度 ≥ 10%\n泊松比 ≥ 0.3\n平均深度 ≥ 0.5×', C['dec_fc'], C['dec_ec'], fs=7.8)
box(ax, 0.37, 0.545, 0.30, 0.115, 'B 轨（RNA 基因组救援）\n基因区总覆盖度 ≥ 80%\n基因区平均覆盖度 ≥ 5%', C['dec_fc'], C['dec_ec'], fs=7.6)
arrow(ax, 0.19, 0.545, 0.28, 0.512)
arrow(ax, 0.52, 0.545, 0.43, 0.512)
box(ax, 0.10, 0.440, 0.50, 0.072, '节段完整性：物种在参考库中的全部节段\n必须同时检出', C['dec_fc'], C['dec_ec'], fs=7.8)
arrow(ax, 0.28, 0.440, 0.20, 0.408)
arrow(ax, 0.43, 0.440, 0.51, 0.408)
box(ax, 0.04, 0.315, 0.30, 0.093, 'ANI ≥ 95%\n确诊\n已知病毒', C['out_fc'], C['out_ec'], fs=8.4, weight='bold')
box(ax, 0.37, 0.315, 0.30, 0.093, 'ANI < 95%\n疑似新种\n候选变异株', C['warn_fc'], C['warn_ec'], fs=8.4, weight='bold')
arrow(ax, 0.19, 0.315, 0.19, 0.283)
arrow(ax, 0.52, 0.315, 0.52, 0.283)
box(ax, 0.04, 0.175, 0.63, 0.108, '输出：全量汇总 | 最佳命中 | 疑似新种 | 带原因的剔除清单\n共识序列（viral_consensus：q20、d5、f0.5）| 变异检测（bcftools）+ SnpEff 注释\n从 BAM 提取病毒读段 → 靶向组装', C['out_fc'], C['out_ec'], fs=7.5)
ax.text(0.845, 0.80, '泊松防伪模型', fontsize=8.4, weight='bold', ha='center')
ax.text(0.845, 0.545,
        r'$\lambda = N \cdot L_{read} / L_{ref}$' + '\n\n'
        + r'$P = 1 - e^{-\lambda}$' + '\n（期望支持度）\n\n'
        + r'$R = (\mathrm{Cov}/100)\,/\,P$' + '\n\n'
        + 'R ≈ 1：覆盖分布与随机\n放置一致\n\n'
        + 'R << 1：覆盖集中\n→ 提示低水平假信号',
        fontsize=7.2, ha='center', va='center',
        bbox=dict(boxstyle='round,pad=0.45', fc='white', ec='#BBBBBB'))
fig.savefig(os.path.join(OUT, 'figure2_kvsuite_zh.png'), dpi=300, bbox_inches='tight')
fig.savefig(os.path.join(OUT, 'figure2_kvsuite_zh.svg'), bbox_inches='tight')
plt.close(fig)

# =============== 图3 进化动力学模块 ===============
fig, ax = canvas(7.6, 7.6)
ax.text(0.02, 0.975, 'A  重组检测', fontsize=9.5, weight='bold')
steps = [
    ('多序列比对（同源等长）', C['input_fc'], C['input_ec']),
    ('align_qc 预筛\n长度比<0.90 | gap>0.10 | N>0.05\n| identity<98%（仅显式指定参考时启用）', C['dec_fc'], C['dec_ec']),
    ('RDP5CL：九种方法\nRDP GENECONV Bootscan MaxChi\nChimaera SiScan PhylPro LARD 3Seq', C['eng_fc'], C['eng_ec']),
    ('事件：p<0.05 的方法集合，best-p 取最小值；\n断点区间合并（含环形基因组的跨末端拆分）', C['dec_fc'], C['dec_ec']),
    ('重组掩蔽\n→ 干净比对直接用于建树', C['out_fc'], C['out_ec']),
]
y = 0.955
SH, SG = 0.082, 0.036
for i, (t, fc, ec) in enumerate(steps):
    box(ax, 0.03, y - SH, 0.44, SH, t, fc, ec, fs=7.0)
    if i < len(steps) - 1:
        arrow(ax, 0.25, y - SH, 0.25, y - SH - SG)
    y -= SH + SG
ax.text(0.03, y - 0.005, '实测（捆绑 CMV RNA3 示例集：10 条分离物，2,292 列比对）：\n'
        '默认过滤 10 → 10 全部保留；显式参考模式 10 → 4 保留\n（剔除 6 条，identity 75.6–92.9%，未达 98% 门槛）',
        fontsize=7.0, ha='left', va='top',
        bbox=dict(boxstyle='round,pad=0.35', fc='#FDF7E8', ec='#D9A441'))
ax.text(0.545, 0.975, 'B  时间信号（根到尾回归）', fontsize=9.5, weight='bold')
box(ax, 0.53, 0.880, 0.44, 0.062, '进化树 + 采样日期\n（CSV/TSV 元数据或 FASTA 头）', C['input_fc'], C['input_ec'], fs=7.3)
arrow(ax, 0.75, 0.880, 0.75, 0.848)
box(ax, 0.53, 0.763, 0.44, 0.085, '中点重根搜索\n最大化 R²\n（需 ≥ 5 个有日期分类群）', C['eng_fc'], C['eng_ec'], fs=7.3)
arrow(ax, 0.75, 0.763, 0.75, 0.731)
box(ax, 0.53, 0.646, 0.44, 0.085, '替换速率（每位点每年）= 斜率\nR² = 时间信号强度\n（TempEst 流程的进程内实现）', C['out_fc'], C['out_ec'], fs=7.3)
ax.text(0.545, 0.585, 'C  系统地理学（Fitch 最大简约）', fontsize=9.5, weight='bold')
box(ax, 0.53, 0.490, 0.44, 0.062, '进化树 + 离散地理区划\n（CSV/TSV 元数据或 FASTA 头）', C['input_fc'], C['input_ec'], fs=7.3)
arrow(ax, 0.75, 0.490, 0.75, 0.458)
box(ax, 0.53, 0.373, 0.44, 0.085, '自底向上最小状态集 →\n自顶向下确定内部节点状态\n统计全部父→子状态转移', C['eng_fc'], C['eng_ec'], fs=7.3)
arrow(ax, 0.75, 0.373, 0.75, 0.341)
box(ax, 0.53, 0.256, 0.44, 0.085, '带区划标注的 Newick 树 +\n迁移矩阵 + 逐叶状态表\n+ 全部转移清单', C['out_fc'], C['out_ec'], fs=7.3)
ax.text(0.02, 0.175, 'D  群体遗传学（样品内变异）', fontsize=9.5, weight='bold')
box(ax, 0.03, 0.055, 0.94, 0.09, 'Ts/Tv 谱  |  等位频率谱  |  πN/πS（逐 CDS，Pool-Seq 口径）\n滑窗核苷酸多样性 π  |  Tajima\'s D', C['gray_fc'], C['gray_ec'], fs=7.8)
fig.savefig(os.path.join(OUT, 'figure3_evo_dynamics_zh.png'), dpi=300, bbox_inches='tight')
fig.savefig(os.path.join(OUT, 'figure3_evo_dynamics_zh.svg'), bbox_inches='tight')
plt.close(fig)

# =============== 图4 实测：CMV RNA3 两两 SDT 一致性热图（中英双版本） ===============
fasta = os.path.join(ROOT, 'examples', 'example_recomb_set.fasta')
recs = [(h, s.upper()) for h, s in iter_fasta(fasta)]
n = len(recs)
M = [[0.0] * n for _ in range(n)]
for i in range(n):
    for j in range(i + 1, n):
        M[i][j] = M[j][i] = pairwise_identity(recs[i][1], recs[j][1])
countries = [h.split('|')[1].rsplit('_', 1)[1] if '|' in h else h for h, _ in recs]
countries[0] = 'RefSeq'
ZH = {'China': '中国', 'India': '印度', 'Slovenia': '斯洛文尼亚', 'Korea': '韩国',
      'Australia': '澳大利亚', 'Iran': '伊朗', 'France': '法国', 'Germany': '德国', 'Poland': '波兰'}
labels_zh = [ZH.get(c, c) for c in countries]
labels_en = list(countries)
accessions = [h.split('|')[0] for h, _ in recs]

for lang, labels, title in (
    ('zh', labels_zh, '图4 捆绑 CMV RNA3 演示集两两 SDT 一致性矩阵（实测）'),
    ('en', labels_en, 'Pairwise SDT identity matrix, bundled CMV RNA3 set (measured)'),
):
    fig, ax = plt.subplots(figsize=(7.4, 6.4))
    im = ax.imshow(M, cmap='RdYlGn_r', vmin=70, vmax=100)
    ax.set_xticks(range(n)); ax.set_yticks(range(n))
    ax.set_xticklabels([f'{l}\n{a}' for l, a in zip(labels, accessions)], rotation=45, ha='right', fontsize=7)
    ax.set_yticklabels([f'{l}\n{a}' for l, a in zip(labels, accessions)], fontsize=7)
    for i in range(n):
        for j in range(n):
            v = M[i][j]
            ax.text(j, i, f'{v:.1f}', ha='center', va='center', fontsize=5.6,
                    color='white' if (v < 82 or v > 96) else 'black')
    cb = fig.colorbar(im, ax=ax, shrink=0.82)
    cb.set_label('SDT identity (%)', fontsize=8)
    ax.set_title(title, fontsize=10.5, weight='bold', pad=12)
    ax.tick_params(length=0)
    for sp in ax.spines.values():
        sp.set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, f'figure4_identity_{lang}.png'), dpi=300, bbox_inches='tight')
    fig.savefig(os.path.join(OUT, f'figure4_identity_{lang}.svg'), bbox_inches='tight')
    plt.close(fig)

print('Chinese figures written to', OUT)
