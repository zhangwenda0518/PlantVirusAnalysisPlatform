# -*- coding: utf-8 -*-
"""fig_b8：共识序列比较（模拟自洽 / sRNA 实测 / 组装替代路线）"""
import json
import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei']
plt.rcParams['axes.unicode_minus'] = False

HERE = os.path.dirname(os.path.abspath(__file__))
data = json.load(open(os.path.join(HERE, 'results', 'cons_results.json'),
                      encoding='utf-8'))
FIGS = os.path.join(HERE, 'figs')
OK, BAD, MID, WARN = '#5B9A57', '#C0504D', '#4C72B0', '#D9A441'

# ── 左：覆盖率；右：一致率（无一致率=纯N产物，画 0 并标注）──
labels, covs, ids, notes = [], [], [], []
for t in data['targets']:
    labels.append(f"模拟自洽\n{t['acc']}\n(150bp PE 200×)")
    covs.append(t['coverage_pct'])
    ids.append(t['identity_pct'])
labels.append('sRNA实测\nS3→PVB两段\n(minibwa)')
covs.append(data['srna']['eval']['coverage_pct'])
ids.append(0.0)
notes.append('产物为100% N')
for acc, r in (data.get('assembly_route', {}).get('targets') or {}).items():
    labels.append(f"组装路线\nS3→{acc}\n(srna contigs)")
    covs.append(r['coverage_pct'])
    ids.append(r['identity_pct'])

n = len(labels)
xs = range(n)
fig, (axl, axr) = plt.subplots(1, 2, figsize=(11.8, 4.6))
bar_colors = ([MID] * 3 + [BAD] + [WARN] * (n - 4))
axl.bar(xs, covs, 0.62, color=bar_colors)
for x, v in zip(xs, covs):
    axl.text(x, v + 1.5, f'{v:.1f}%', ha='center', fontsize=9, weight='bold')
axl.axhline(100, ls=':', lw=0.8, color='#999999')
axl.set_xticks(list(xs))
axl.set_xticklabels(labels, fontsize=7.6)
axl.set_ylabel('参考基因组覆盖率 (%)')
axl.set_ylim(0, 112)
axl.set_title('共识/组装序列的参考覆盖', fontsize=11)
axl.spines[['top', 'right']].set_visible(False)

axr.bar(xs, ids, 0.62, color=bar_colors)
for x, v, nt in zip(xs, ids, notes + [None] * (n - len(notes))):
    axr.text(x, v + 1.5, f'{v:.1f}%' if v else ('0%（全N）' if nt else '0%'),
             ha='center', fontsize=9, weight='bold')
axr.annotate('sRNA 产物为 100% N\n（0 映射 → 静默空转）',
             xy=(3, 2), xytext=(3, 40), fontsize=8.5, color=BAD,
             ha='center',
             arrowprops=dict(arrowstyle='->', color=BAD, lw=1.1))
axr.set_xticks(list(xs))
axr.set_xticklabels(labels, fontsize=7.6)
axr.set_ylabel('与真值参考的一致率 (%)')
axr.set_ylim(0, 112)
axr.set_title('序列一致性', fontsize=11)
axr.spines[['top', 'right']].set_visible(False)

fig.suptitle('共识序列分析比较：常规 reads 自洽 / 小RNA 实测 / 组装替代（Bari S3）',
             fontsize=11.5)
fig.tight_layout(rect=(0, 0, 1, 0.94))
fig.savefig(os.path.join(FIGS, 'fig_b8_consensus.png'), dpi=200)
fig.savefig(os.path.join(FIGS, 'fig_b8_consensus.svg'))
print('fig_b8 ->', FIGS)
