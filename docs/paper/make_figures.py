# -*- coding: utf-8 -*-
"""Generate Figures 1-3 for the VirusPlatform manuscript (matplotlib, English labels)."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import os

OUT = os.path.join(os.path.dirname(__file__), 'figures')
os.makedirs(OUT, exist_ok=True)

COL = dict(
    input_fc='#E8EEF7', input_ec='#4C72B0',
    stage_fc='#DDE7F2', stage_ec='#4C72B0',
    group_fc='#F3F6FA', group_ec='#8FA8C8',
    dec_fc='#FDF1DC', dec_ec='#D9A441',
    out_fc='#DFF0DC', out_ec='#5B9A57',
    eng_fc='#EADDF3', eng_ec='#7D5BA6',
    warn_fc='#FBE3E3', warn_ec='#C0504D',
    gray_fc='#F2F2F2', gray_ec='#AAAAAA',
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

# ============================ Figure 1: architecture & pipeline ============================
fig, ax = canvas(7.6, 9.0)
box(ax, 0.03, 0.935, 0.42, 0.05, 'Paired/single-end reads (FASTQ[.gz])',
    C['input_fc'], C['input_ec'], fs=8.6, weight='bold')
box(ax, 0.52, 0.925, 0.45, 0.065, 'Loopback-only Flask app (212 routes)\npywebview window / browser, zh-en UI',
    C['gray_fc'], C['gray_ec'], fs=7.4)

groups = [
    ('1  Preprocessing', ['subsample', 'fastp QC', 'seqkit fq2fa', 'kunpeng host removal']),
    ('2  Virus identification', ['kvsuite: salmon / minibwa quantification | two-track filter | consensus | variants']),
    ('3  Assembly', ['SPAdes (rnaviral)', 'contig classification']),
    ('4  Verification', ['DIAMOND blastx + CDD domains  ->  known / novel / domain_only / unclassified']),
    ('5  Host & annotation', ['ICTV host prediction', 'pyrodigal(-rv) ORF', 'RefSeq / HMM annotation']),
    ('6  Downstream & report', ['MAFFT + trimAl', 'FastTree / IQ-TREE 3', 'SDT matrix', 'primer3', 'gbdraw + HTML report']),
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
        box(ax, x0 + j * cw, y + 0.013, cw - 0.015, BH - 0.035, c,
            C['stage_fc'], C['stage_ec'], fs=6.9)
    if i == 0:
        arrow(ax, 0.24, 0.935, 0.24, top)
    if i > 0:
        arrow(ax, 0.5, top + GAP, 0.5, top)

box(ax, 0.03, 0.030, 0.455, 0.056, 'Task manager: heavy=2 / light=4 semaphores,\nFIFO queue, SSE progress, restart recovery',
    C['gray_fc'], C['gray_ec'], fs=7.2)
box(ax, 0.515, 0.030, 0.455, 0.056, 'Resumable stages (.done + input fingerprints),\nproject.json provenance, EMA progress/ETA',
    C['gray_fc'], C['gray_ec'], fs=7.2)
box(ax, 0.03, 0.0, 0.94, 0.024, 'PyInstaller distribution: 30 bundled external tools  |  program ~1 GB  |  databases ~3.6 GB (optional)',
    C['out_fc'], C['out_ec'], fs=7.4)
fig.savefig(os.path.join(OUT, 'figure1_architecture.png'), dpi=300, bbox_inches='tight')
fig.savefig(os.path.join(OUT, 'figure1_architecture.svg'), bbox_inches='tight')
plt.close(fig)

# ============================ Figure 2: known-virus suite ============================
fig, ax = canvas(7.6, 8.0)
# main flow occupies x in [0.03, 0.66]; Poisson panel x in [0.69, 0.995]
box(ax, 0.14, 0.945, 0.42, 0.042, 'Host-removed reads (FASTQ)', C['input_fc'], C['input_ec'], fs=8.6, weight='bold')
arrow(ax, 0.35, 0.945, 0.19, 0.918, rad=0.12)
arrow(ax, 0.35, 0.945, 0.52, 0.918, rad=-0.12)
box(ax, 0.04, 0.848, 0.30, 0.070, 'Probabilistic engine\nsalmon k=31 EM quantification\n(+ pseudo-alignment BAM)', C['eng_fc'], C['eng_ec'], fs=7.5)
box(ax, 0.37, 0.848, 0.30, 0.070, 'Alignment engine\nminibwa mem true alignments\n(per-reference unique counts)', C['eng_fc'], C['eng_ec'], fs=7.5)
arrow(ax, 0.19, 0.848, 0.28, 0.812)
arrow(ax, 0.52, 0.848, 0.43, 0.812)
box(ax, 0.05, 0.768, 0.60, 0.044, 'Coverage / depth backends:  pandepth | samtools | built-in  (bit-identical outputs)', C['gray_fc'], C['gray_ec'], fs=7.4)
arrow(ax, 0.35, 0.768, 0.35, 0.738)
box(ax, 0.08, 0.692, 0.54, 0.046, 'Base gate:  unique reads >= 10   AND   mean depth >= 0.5x', C['dec_fc'], C['dec_ec'], fs=8.2)
arrow(ax, 0.35, 0.692, 0.20, 0.660, rad=0.08)
arrow(ax, 0.35, 0.692, 0.51, 0.660, rad=-0.08)
ax.text(0.245, 0.672, 'any one', fontsize=6.8, ha='center', color='#666666')
ax.text(0.455, 0.672, 'or', fontsize=6.8, ha='center', color='#666666')
box(ax, 0.04, 0.545, 0.30, 0.115, 'Track A (genomic)\ncoverage >= 10%\nPoisson ratio >= 0.3\nmean depth >= 0.5x', C['dec_fc'], C['dec_ec'], fs=7.8)
box(ax, 0.37, 0.545, 0.30, 0.115, 'Track B (rescue, RNA genomes)\ngene-region total coverage >= 80%\ngene-region mean coverage >= 5%', C['dec_fc'], C['dec_ec'], fs=7.6)
arrow(ax, 0.19, 0.545, 0.28, 0.512)
arrow(ax, 0.52, 0.545, 0.43, 0.512)
box(ax, 0.10, 0.440, 0.50, 0.072, 'Segment completeness: all reference segments\nof a species must be detected', C['dec_fc'], C['dec_ec'], fs=7.8)
arrow(ax, 0.28, 0.440, 0.20, 0.408)
arrow(ax, 0.43, 0.440, 0.51, 0.408)
box(ax, 0.04, 0.315, 0.30, 0.093, 'ANI >= 95%\nCONFIRMED\nknown virus', C['out_fc'], C['out_ec'], fs=8.4, weight='bold')
box(ax, 0.37, 0.315, 0.30, 0.093, 'ANI < 95%\nSUSPECTED NOVEL\ncandidate variant', C['warn_fc'], C['warn_ec'], fs=8.4, weight='bold')
arrow(ax, 0.19, 0.315, 0.19, 0.283)
arrow(ax, 0.52, 0.315, 0.52, 0.283)
box(ax, 0.04, 0.175, 0.63, 0.108, 'Outputs:  full summary | best hits | suspected-novel | discards with reasons\n'
    'consensus (viral_consensus: q20, d5, f0.5)  |  variants (bcftools) + SnpEff annotation\n'
    'viral reads extracted from BAM  ->  targeted assembly', C['out_fc'], C['out_ec'], fs=7.5)
ax.text(0.845, 0.80, 'Poisson safeguard', fontsize=8.4, weight='bold', ha='center')
ax.text(0.845, 0.545,
        r'$\lambda = N \cdot L_{read} / L_{ref}$' + '\n\n'
        + r'$P = 1 - e^{-\lambda}$' + '\n(expected support)\n\n'
        + r'$R = (\mathrm{Cov}/100)\,/\,P$' + '\n\n'
        + 'R ~ 1: coverage consistent\nwith random placement\n\n'
        + 'R << 1: coverage\nconcentrated -> spurious\nlow-level hit',
        fontsize=7.2, ha='center', va='center',
        bbox=dict(boxstyle='round,pad=0.45', fc='white', ec='#BBBBBB'))
fig.savefig(os.path.join(OUT, 'figure2_kvsuite.png'), dpi=300, bbox_inches='tight')
fig.savefig(os.path.join(OUT, 'figure2_kvsuite.svg'), bbox_inches='tight')
plt.close(fig)

# ============================ Figure 3: evolutionary dynamics ============================
fig, ax = canvas(7.6, 7.6)
ax.text(0.02, 0.975, 'A  Recombination', fontsize=9.5, weight='bold')
steps = [
    ('MSA (homologous, equal length)', C['input_fc'], C['input_ec']),
    ('align_qc pre-filter\nlen<0.90 | gap>0.10 | N>0.05\n| identity<98% (explicit ref only)', C['dec_fc'], C['dec_ec']),
    ('RDP5CL: 9 methods\nRDP GENECONV Bootscan MaxChi\nChimaera SiScan PhylPro LARD 3Seq', C['eng_fc'], C['eng_ec']),
    ('events: methods with p<0.05, best-p = min;\nbreakpoint merge (incl. circular split ends)', C['dec_fc'], C['dec_ec']),
    ('recombination masking\n-> clean MSA for tree building', C['out_fc'], C['out_ec']),
]
y = 0.955
SH, SG = 0.082, 0.036
for i, (t, fc, ec) in enumerate(steps):
    box(ax, 0.03, y - SH, 0.44, SH, t, fc, ec, fs=7.0)
    if i < len(steps) - 1:
        arrow(ax, 0.25, y - SH, 0.25, y - SH - SG)
    y -= SH + SG
ax.text(0.03, y - 0.005, 'Measured on bundled CMV RNA3 set (10 isolates, 2,292 nt MSA):\n'
        'default filter 10 -> 10 kept; explicit-reference mode 10 -> 4 kept\n'
        '(6 removed, identity 75.6-92.9% vs 98% gate)',
        fontsize=7.0, ha='left', va='top',
        bbox=dict(boxstyle='round,pad=0.35', fc='#FDF7E8', ec='#D9A441'))

ax.text(0.545, 0.975, 'B  Temporal signal (root-to-tip)', fontsize=9.5, weight='bold')
box(ax, 0.53, 0.880, 0.44, 0.062, 'tree + sampling dates\n(CSV/TSV metadata or FASTA headers)', C['input_fc'], C['input_ec'], fs=7.3)
arrow(ax, 0.75, 0.880, 0.75, 0.848)
box(ax, 0.53, 0.763, 0.44, 0.085, 'midpoint rerooting search\nmaximize R-squared\n(>= 5 dated taxa required)', C['eng_fc'], C['eng_ec'], fs=7.3)
arrow(ax, 0.75, 0.763, 0.75, 0.731)
box(ax, 0.53, 0.646, 0.44, 0.085, 'rate (subs/site/year) = slope\nR-squared = temporal-signal strength\n(TempEst workflow, in-process)', C['out_fc'], C['out_ec'], fs=7.3)

ax.text(0.545, 0.585, 'C  Phylogeography (Fitch parsimony)', fontsize=9.5, weight='bold')
box(ax, 0.53, 0.490, 0.44, 0.062, 'tree + discrete regions\n(CSV/TSV metadata or FASTA headers)', C['input_fc'], C['input_ec'], fs=7.3)
arrow(ax, 0.75, 0.490, 0.75, 0.458)
box(ax, 0.53, 0.373, 0.44, 0.085, 'bottom-up minimal state sets ->\ntop-down internal states\nall parent->child transitions tallied', C['eng_fc'], C['eng_ec'], fs=7.3)
arrow(ax, 0.75, 0.373, 0.75, 0.341)
box(ax, 0.53, 0.256, 0.44, 0.085, 'annotated Newick (internal states) +\nmigration matrix + per-tip states\n+ full transition list', C['out_fc'], C['out_ec'], fs=7.3)

ax.text(0.02, 0.175, 'D  Population genetics (within-sample variants)', fontsize=9.5, weight='bold')
box(ax, 0.03, 0.055, 0.94, 0.09, 'Ts/Tv spectra  |  allele-frequency spectrum  |  piN/piS (SNPGenie-style, per CDS)\nsliding-window pi  |  Tajima\'s D  (pooled-sequencing model)', C['gray_fc'], C['gray_ec'], fs=7.8)
fig.savefig(os.path.join(OUT, 'figure3_evo_dynamics.png'), dpi=300, bbox_inches='tight')
fig.savefig(os.path.join(OUT, 'figure3_evo_dynamics.svg'), bbox_inches='tight')
plt.close(fig)

print('figures written to', OUT)
