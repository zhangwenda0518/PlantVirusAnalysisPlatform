# -*- coding: utf-8 -*-
"""LAMP 设计正确性独立复核（审稿级守卫，2026-11-05）。

对 lamp_design 的真实输出做**独立复核**（不调用引擎内部校验函数，
全部用本脚本自带实现重算），验证设计规则的数值正确性：

  1. 序列回取：输出坐标回源序列与输出序列逐字节一致（六段）
  2. GC%：独立重算与输出一致
  3. Tm：落在分段目标区间内；Biopython 可用时用 Tm_NN 独立重算并
     检查与输出偏差 ≤6°C（两套热力学口径的合理容差）
  4. FIP/BIP 拼接：revcomp(F1区)+TTTT+F2区 / B1区+TTTT+revcomp(B2区)
  5. 间距与产物：五个间距区间 + F2-B2 产物区间全部合规
  6. 段序：F3 < F2 < F1 < B1 < B2 < B3（正链坐标不重叠）
  7. Tm 秩序：外引物（F3/B3）平均 Tm 低于内引物（F2/B2/F1/B1）平均
  8. 二聚体 qc：独立实现（滑动重叠配对率）重算自二聚，与输出偏差 ≤5%
  9. anchor（方案 A 预演）：F2-B2 产物包住给定锚区间

用法: python tests/_check_lamp_correctness.py [--input FASTA] [--top 5]
退出码 0 = 全部通过；1 = 有 FAIL。
"""
import argparse
import io
import sys
import os

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from Virus_Platform_Core import lamp_design as ld   # noqa: E402

fails = []


def check(ok, msg):
    print(('  ok  ' if ok else '  FAIL ') + msg)
    if not ok:
        fails.append(msg)


# ---- 独立实现（不 import 引擎的复核原语）----
def rc(s):
    return s.translate(str.maketrans('ACGTRYMKSWBDHVN',
                                     'TGCAYRKMSWVHDBN'))[::-1]


def gc_pct(s):
    return round(100.0 * (s.count('G') + s.count('C')) / max(1, len(s)), 1)


def dimer_pct_independent(a, b):
    """a 与 revcomp(b) 的全部错位重叠里最高配对比例（≥8bp 重叠才计）。"""
    rb = rc(b)
    best = 0.0
    for off in range(-(len(rb) - 1), len(a)):
        s0 = max(0, -off)
        e0 = min(len(a), len(rb) - off)
        if e0 - s0 < 8:
            continue
        matched = sum(1 for i in range(s0, e0)
                      if a[i] + rb[i + off] in ('AT', 'TA', 'GC', 'CG'))
        best = max(best, matched / float(e0 - s0))
    return round(best * 100.0, 1)


def read_first_fasta(path):
    name, seq = None, []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line.startswith('>'):
                if name:
                    break
                name = line[1:]
            elif line:
                seq.append(line)
    return name, ''.join(seq).upper()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', default='examples/example_acmv.fasta')
    ap.add_argument('--top', type=int, default=5)
    args = ap.parse_args()

    _, seq = read_first_fasta(args.input)
    print(f'输入: {args.input}（{len(seq)}bp）')
    sets = ld.design_lamp_set(seq)
    print(f'设计组数: {len(sets)}\n')
    check(bool(sets), '至少出组')

    for si, s in enumerate(sets[:args.top], 1):
        print(f'## 组 {si}（score {s["score"]}，产物 {s["product_bp"]}bp）')
        # 1. 序列回取
        for key in ('f3', 'f2', 'f1', 'b1', 'b2', 'b3'):
            d = s[key]
            got = seq[d['start'] - 1:d['start'] - 1 + d['len']]
            check(got == d['seq'], f'{key.upper()} 坐标回取一致')
        # 2. GC
        for key in ('f3', 'f2', 'f1', 'b1', 'b2', 'b3'):
            d = s[key]
            check(abs(gc_pct(d['seq']) - d['gc']) <= 0.2,
                  f'{key.upper()} GC% 独立重算一致（{d["gc"]} vs '
                  f'{gc_pct(d["seq"])}）')
        # 3. Tm 区间 + 独立重算
        # 2026-11-05 Tm 区间对齐 uni-shine 口径（宽区间、target 排序）
        sp_lo = {'f3': 53.0, 'b3': 53.0, 'f2': 56.0, 'b2': 56.0,
                 'f1': 59.0, 'b1': 59.0}
        sp_hi = {'f3': 62.5, 'b3': 62.5, 'f2': 66.0, 'b2': 66.0,
                 'f1': 68.0, 'b1': 68.0}
        for key in ('f3', 'f2', 'f1', 'b1', 'b2', 'b3'):
            d = s[key]
            check(sp_lo[key] <= d['tm'] <= sp_hi[key],
                  f'{key.upper()} Tm {d["tm"]} 在目标区间 '
                  f'[{sp_lo[key]}, {sp_hi[key]}]')
        try:
            from Bio.SeqUtils import MeltingTemp as mt
            for key in ('f3', 'f2'):
                d = s[key]
                # 盐条件对齐 primer3 口径（mv=50, dv=1.5, dntp=0.8）：
                # Mg 按 dv_conc=1.5 传——此前用 Mg=8（LAMP 实测体系）
                # 会把有效盐抬到系统 +6°C，那是口径差不是计算错误
                tm2 = mt.Tm_NN(d['seq'], Na=50, Mg=1.5, dNTPs=0.8, dnac1=50)
                check(abs(tm2 - d['tm']) <= 2.5,
                      f'{key.upper()} Tm 独立口径对照 |Δ|={abs(tm2 - d["tm"]):.1f}°C '
                      f'≤2.5（primer3 {d["tm"]} vs Tm_NN {tm2:.1f}，同盐条件）')
        except ImportError:
            pass
        # 4. FIP/BIP 拼接
        f1 = seq[s['f1']['start'] - 1:s['f1']['start'] - 1 + s['f1']['len']]
        b2 = seq[s['b2']['start'] - 1:s['b2']['start'] - 1 + s['b2']['len']]
        check(s['fip'] == rc(f1) + 'TTTT' + s['f2']['seq'],
              'FIP = revcomp(F1区)+TTTT+F2区')
        check(s['bip'] == s['b1']['seq'] + 'TTTT' + rc(b2),
              'BIP = B1区+TTTT+revcomp(B2区)')
        if s.get('lf_seq') is not None:
            check(True, 'LF 存在（可选段）')
        if s.get('lb_seq') is not None:
            check(True, 'LB 存在（可选段）')
        # 5. 间距与产物
        g = {
            'F3-F2': s['f2']['start'] - (s['f3']['start'] + s['f3']['len']),
            'F2-F1': s['f1']['start'] - (s['f2']['start'] + s['f2']['len']),
            'F1-B1': s['b1']['start'] - (s['f1']['start'] + s['f1']['len']),
            'B1-B2': s['b2']['start'] - (s['b1']['start'] + s['b1']['len']),
            'B2-B3': s['b3']['start'] - (s['b2']['start'] + s['b2']['len']),
        }
        for k, v in g.items():
            check(v >= 0, f'间距 {k} = {v} 非负')
        check(ld._GAP_F3F2[0] <= g['F3-F2'] <= ld._GAP_F3F2[1],
              f'F3-F2 间距 {g["F3-F2"]} 在 {ld._GAP_F3F2}')
        check(ld._GAP_F2F1[0] <= g['F2-F1'] <= ld._GAP_F2F1[1],
              f'F2-F1 间距 {g["F2-F1"]} 在 {ld._GAP_F2F1}')
        check(ld._GAP_CORE[0] <= g['F1-B1'] <= ld._GAP_CORE[1],
              f'F1-B1 间距 {g["F1-B1"]} 在 {ld._GAP_CORE}')
        check(ld._GAP_B1B2[0] <= g['B1-B2'] <= ld._GAP_B1B2[1],
              f'B1-B2 间距 {g["B1-B2"]} 在 {ld._GAP_B1B2}')
        check(ld._GAP_B2B3[0] <= g['B2-B3'] <= ld._GAP_B2B3[1],
              f'B2-B3 间距 {g["B2-B3"]} 在 {ld._GAP_B2B3}')
        prod = (s['b2']['start'] - 1) - (s['f2']['start'] - 1 + s['f2']['len']) + 1
        check(prod == s['product_bp'] and ld._PRODUCT[0] <= prod <= ld._PRODUCT[1],
              f'产物 {prod}bp = 输出值且 {ld._PRODUCT[0]}-{ld._PRODUCT[1]} 区间')
        # 6. 段序
        order = [s[k]['start'] for k in ('f3', 'f2', 'f1', 'b1', 'b2', 'b3')]
        check(order == sorted(order), '段序 F3<F2<F1<B1<B2<B3')
        # 7. Tm 秩序（外 < 内）
        outer = (s['f3']['tm'] + s['b3']['tm']) / 2.0
        inner = (s['f2']['tm'] + s['b2']['tm'] + s['f1']['tm'] + s['b1']['tm']) / 4.0
        check(outer < inner, f'外引物平均 Tm {outer:.1f} < 内引物 '
                             f'{inner:.1f}（外低内高）')
        # 8. 二聚体 qc 独立复算（自二聚）
        worst_self = max(dimer_pct_independent(s[k]['seq'], s[k]['seq'])
                         for k in ('f3', 'f2', 'f1', 'b1', 'b2', 'b3'))
        check(abs(worst_self - s['qc']['max_self_pct']) <= 5.0,
              f'自二聚独立复算 {worst_self}% vs 输出 '
              f'{s["qc"]["max_self_pct"]}%（|Δ|≤5）')
        print()

    # 9. anchor 预演
    a0 = len(seq) // 2
    a1 = a0 + 20
    anchored = ld.design_lamp_set(seq, anchor_span=(a0, a1))
    if anchored:
        ok = all(
            (s['f2']['start'] - 1 + s['f2']['len']) < a0
            and a1 < (s['b2']['start'] - 1)
            for s in anchored)
        check(ok, f'anchor [{a0}, {a1}) 被全部 {len(anchored)} 组产物包住')
    else:
        check(True, f'anchor [{a0}, {a1}) 位置无可行组（剪枝正常，不算失败）')

    print('\n== %s（%d fail）==' % ('PASS' if not fails else 'FAIL', len(fails)))
    return 0 if not fails else 1


if __name__ == '__main__':
    sys.exit(main())
