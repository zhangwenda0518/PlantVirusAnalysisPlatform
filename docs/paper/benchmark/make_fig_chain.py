# -*- coding: utf-8 -*-
"""fig_b10：工具链全流程（识别提取→组装→再鉴定→验证）——全部 20 样本
链级检出灵敏度 + 阶段耗时堆叠"""
import json
import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei']
plt.rcParams['axes.unicode_minus'] = False

HERE = os.path.dirname(os.path.abspath(__file__))
data = json.load(open(os.path.join(HERE, 'results', 'chain_results.json'),
                      encoding='utf-8'))['samples']
FIGS = os.path.join(HERE, 'figs')
MID, GREEN, WARN, PURP = '#4C72B0', '#5B9A57', '#D9A441', '#7D5BA6'

ORDER = ['S3', 'S9'] + [f'D{d}' for d in range(1, 19)]
STAGES = [('stage1_identify_extract_s', '① 识别分类与提取'),
          ('stage2_assemble_s', '② 提取序列组装'),
          ('stage3_reidentify_s', '③ 组装结果再鉴定'),
          ('stage4_verify_s', '④ 候选序列验证')]
SC = [MID, GREEN, WARN, PURP]

# 每样本真值项数（链级检出分母）
TOT = {'S3': 2, 'S9': 9}
for d in range(1, 11):
    TOT[f'D{d}'] = {1: 5, 2: 5, 3: 5, 4: 5, 5: 1, 6: 1, 7: 1, 8: 2, 9: 1, 10: 2}[d]
for d in range(11, 19):
    TOT[f'D{d}'] = 1

fig, (axl, axr) = plt.subplots(1, 2, figsize=(13.5, 5.0),
                               gridspec_kw={'width_ratios': [1.25, 1]})
BAD = '#C0504D'

# ── 左：全部 20 样本阶段耗时堆叠（log y，D1/D2 的验证段 ~370s）──
bottoms = [0.0] * len(ORDER)
xs = range(len(ORDER))
for (key, label), c in zip(STAGES, SC):
    vals = [data[s].get(key, 0) or 0 for s in ORDER]
    axl.bar(xs, vals, 0.65, bottom=bottoms, color=c, label=label)
    bottoms = [b + v for b, v in zip(bottoms, vals)]
axl.set_yscale('log')
axl.set_ylim(5, 1500)
axl.set_xticks(list(xs))
axl.set_xticklabels(ORDER, fontsize=7.5, rotation=45, ha='right')
axl.set_ylabel('墙钟时间 (s，log)')
axl.set_title('四段阶段耗时（4 线程，全 20 样本）', fontsize=11)
axl.legend(fontsize=7.5, loc='upper left')
axl.spines[['top', 'right']].set_visible(False)

# ── 右：链级检出灵敏度 ──
sens = []
for s in ORDER:
    det = data[s].get('chain_detected', [])
    sens.append(len(det) / TOT[s] * 100)
colors = [GREEN if v == 100 else (MID if v >= 50 else BAD) for v in sens]
bars = axr.bar(range(len(ORDER)), sens, 0.65, color=colors)
for x, (s, v) in zip(range(len(ORDER)), zip(ORDER, sens)):
    tp = len(data[s].get('chain_detected', []))
    axr.text(x, v + 2, f'{tp}/{TOT[s]}', ha='center', fontsize=7.5)
axr.axhline(76, ls='--', lw=1.1, color='#D9A441')
axr.text(len(ORDER) - 0.5, 78, '论文 250K 均值 76%', ha='right', fontsize=8,
         color='#8a6414')
axr.set_xticks(range(len(ORDER)))
axr.set_xticklabels(ORDER, fontsize=7.5, rotation=45, ha='right')
axr.set_ylabel('链级检出灵敏度 (%)')
axr.set_ylim(0, 115)
axr.set_title('链级检出灵敏度（①确诊 ∪ ③再鉴定，20 样本）', fontsize=11)
axr.spines[['top', 'right']].set_visible(False)

fig.suptitle('工具链全流程：病毒识别分类与提取 → 提取序列组装 → 组装结果再鉴定 → 候选序列验证',
             fontsize=11.5)
fig.tight_layout(rect=(0, 0, 1, 0.93))
fig.savefig(os.path.join(FIGS, 'fig_b10_chain.png'), dpi=200)
fig.savefig(os.path.join(FIGS, 'fig_b10_chain.svg'))
print('fig_b10 ->', FIGS)
