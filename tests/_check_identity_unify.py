# -*- coding: utf-8 -*-
"""identity 公式统一等价性守卫（2026-11-04）。

背景
----
平台原有 5 处独立的 identity 公式拷贝，2026-11-04 统一到
`Virus_Platform_Core/seq_identity.py`（唯一权威实现），其余 5 处改为委托。
本脚本证明「重构前后数值行为一致」，即委托后各处仍符合自己的原契约。

判据
----
A. 计数：`seq_identity.count_matches` == 脚本内重写的独立参考循环（逐位、整数相等）。
B. 各处契约：把**重构前的原式逐字重写**成参考实现，与委托后的实现对拍：
     sdt_exact._sdt_formula      → gap '-.'，百分比，空分母 NaN
     align_qc.pairwise_identity  → gap '-'，百分比，空分母 0.0
     phylodyn_alignqc.pairwise_identity → gap '-'，小数 0-1，空分母 None
     structcmp 内联               → gap '-'，round(...,2)，空分母 None
   输入：200 对等长 60bp + 20 对不等长（zip 截断路径）+ 空串/全 gap 边界。
C. 真实比对（run/tool_runs 下现成的 aln.trim.fasta，另加注 gap 版本）：
   `phylo.pairwise_identity_matrix` 逐对 == `sdt_exact._sdt_formula`（容差 1e-9），
   且 `phylo._nj_distance_matrix` == 1 - identity。
D. 空分母 / 空串的契约逐处钉死（NaN / 0.0 / None），这是最容易被"统一"改错的一档。

容差说明（重要）
--------------
重构前四种写法是 `(1 - dist/denom)*100`、`match*100/comp`、`match/comp`，
统一后是 `match/comp*scale`。它们在 IEEE754 下**不保证逐位相同**：实测约
35% 的 (match, comp) 会差 1 ulp（最大 1.4e-14）。平台所有输出精度都是
0.1 或 0.01，故以 rel 1e-12 断言，同时打印「逐位相同 / 1-ulp 级差异」计数，
让差异面可见而不是被容差掩盖。

用法: python tests/_check_identity_unify.py
退出码: 0 = 通过；1 = 失败。
"""
import math
import os
import random
import sys
import tempfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 任何 Virus_Platform_Core 导入之前隔离运行根：本脚本只读真实产物，
# 万一有模块在导入期建目录，也落临时区而不是 run/。
os.environ.setdefault('VP_RUN_ROOT', tempfile.mkdtemp(prefix='vp_unify_'))

from Virus_Platform_Core import seq_identity  # noqa: E402
from Virus_Platform_Core.align_qc import pairwise_identity as aq_identity  # noqa: E402
from Virus_Platform_Core.phylo import (_nj_distance_matrix,  # noqa: E402
                                       pairwise_identity_matrix)
from Virus_Platform_Core.phylodyn_alignqc import pairwise_identity as pd_identity  # noqa: E402
from Virus_Platform_Core.sdt_exact import _sdt_formula  # noqa: E402

FAILS = []

# 真实比对（tool 运行目录里的现成产物；不存在则整段跳过）
REAL_ALN = os.path.join(ROOT, 'run', 'tool_runs',
                        'align_20261104_153946_4b4f4f', 'aln.trim.fasta')

TOL = 1e-12      # 相对容差：远小于任何输出精度（0.1 / 0.01）
ULP = 1e-12      # 只用于统计"逐位相同与否"


def _check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


def _same(x, y, tol=TOL):
    """None / NaN 感知的数值等价。"""
    if x is None or y is None:
        return x is None and y is None
    if isinstance(x, float) and math.isnan(x):
        return isinstance(y, float) and math.isnan(y)
    if isinstance(y, float) and math.isnan(y):
        return False
    return math.isclose(x, y, rel_tol=tol, abs_tol=tol)


def _exact(x, y):
    """逐位相同（NaN 视为相同）。"""
    if isinstance(x, float) and math.isnan(x):
        return isinstance(y, float) and math.isnan(y)
    return x == y


# ══════════════════════════════════════════════════════════════════════
# 独立参考实现：**重构前原式的逐字重写**（不复用被测代码）
# ══════════════════════════════════════════════════════════════════════

def ref_count(a, b, gap_chars='-.'):
    """逐位计数参考循环（手写，不调 seq_identity）。"""
    same = comp = 0
    for x, y in zip(a, b):
        if x in gap_chars or y in gap_chars:
            continue
        comp += 1
        if x == y:
            same += 1
    return same, comp


def ref_sdt(a, b):
    """重构前 sdt_exact._sdt_formula 原式。"""
    dist = gaps = 0
    for x, y in zip(a, b):
        if x in '-.' or y in '-.':
            gaps += 1
        elif x != y:
            dist += 1
    denom = min(len(a), len(b)) - gaps
    return (1.0 - dist / denom) * 100.0 if denom > 0 else float('nan')


def ref_align_qc(s1, s2):
    """重构前 align_qc.pairwise_identity 原式（仅 '-' 为 gap）。"""
    dist, gaps = 0, 0
    for a, b in zip(s1, s2):
        if a != '-' and b != '-':
            if a != b:
                dist += 1
        else:
            gaps += 1
    denom = len(s1) - gaps
    return (1 - dist / denom) * 100 if denom > 0 else 0.0


def ref_phylodyn(a, b):
    """重构前 phylodyn_alignqc.pairwise_identity 原式（0-1 小数）。"""
    if not a or not b:
        return None
    m = min(len(a), len(b))
    same = tot = 0
    for i in range(m):
        x, y = a[i], b[i]
        if x == '-' or y == '-':
            continue
        tot += 1
        if x == y:
            same += 1
    return (same / tot) if tot else None


def ref_structcmp(a, b):
    """重构前 web/tool_jobs.py structcmp 内联口径原式。"""
    same = comp = 0
    for x, y in zip(a, b):
        if x in '-.' or y in '-.':
            continue
        comp += 1
        if x == y:
            same += 1
    return round(same / comp * 100, 2) if comp else None


def new_structcmp(a, b):
    """重构后 structcmp 的写法（与 tool_jobs.py 内联代码同构）。"""
    same, comp = seq_identity.count_matches(a, b)
    return round(same / comp * 100, 2) if comp else None


# ══════════════════════════════════════════════════════════════════════
# 随机"已比对"序列
# ══════════════════════════════════════════════════════════════════════

BASES = 'ACGTN'
GAPS = '-.'


def rand_seq(rng, length, gap_p=0.15):
    out = []
    for _ in range(length):
        if rng.random() < gap_p:
            out.append(rng.choice(GAPS))
        else:
            out.append(rng.choice(BASES))
    return ''.join(out)


def read_fasta_seq(path):
    """读 FASTA → [(name, seq)]（只用于本脚本的对拍）。"""
    rows, name, buf = [], None, []
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if line.startswith('>'):
                if name is not None:
                    rows.append((name, ''.join(buf)))
                name = line[1:].split()[0] if line[1:].split() else f'seq{len(rows) + 1}'
                buf = []
            elif line:
                buf.append(line)
    if name is not None:
        rows.append((name, ''.join(buf)))
    return rows


def write_fasta(path, rows):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        for nm, s in rows:
            f.write(f'>{nm}\n{s}\n')


# ══════════════════════════════════════════════════════════════════════

def section_a(rng):
    """200 对等长 + 20 对不等长：count_matches vs 独立参考（整数逐位）。"""
    print('\n[A] count_matches == 独立参考循环（等长 200 对 + 不等长 20 对）')
    pairs = [(rand_seq(rng, 60), rand_seq(rng, 60)) for _ in range(200)]
    pairs += [(rand_seq(rng, rng.randint(10, 60)),
               rand_seq(rng, rng.randint(10, 60))) for _ in range(20)]
    # 边界：全 gap（空分母）、空串、单字符
    pairs += [('-' * 60, '-' * 60), ('.' * 30, 'A' * 30), ('', 'ACGT'), ('', ''),
              ('A', 'A'), ('A', 'C')]
    bad = 0
    for a, b in pairs:
        got = seq_identity.count_matches(a, b)
        exp = ref_count(a, b, seq_identity.GAP_CHARS)
        if got != exp:
            bad += 1
            if bad <= 3:
                print(f'         {a[:20]}… vs {b[:20]}…: got {got} exp {exp}')
        # 显式 gap 集版本也要一致
        got2 = seq_identity.count_matches(a, b, '-')
        exp2 = ref_count(a, b, '-')
        if got2 != exp2:
            bad += 1
    _check(bad == 0, f'{len(pairs)} 对：默认 gap 集与 "-" 集计数全部逐位一致'
                     f'（不一致 {bad}）')
    # GAP_CHARS / GAP_NT_ONLY 常量契约
    _check(seq_identity.GAP_CHARS == '-.' and seq_identity.GAP_NT_ONLY == '-',
           '常量 GAP_CHARS="-" + "." / GAP_NT_ONLY="-"')
    # 默认参数必须引用 GAP_CHARS（防止以后有人改常量忘了同步默认值）
    _check(seq_identity.count_matches.__defaults__ == (seq_identity.GAP_CHARS,),
           'count_matches 的 gap_chars 默认值 == GAP_CHARS')


def section_b(pairs):
    """各处委托后 vs 重构前原式（逐处契约）。"""
    print('\n[B] 委托后 vs 重构前原式（逐处契约对拍）')

    # ---- sdt_exact：必须与 pair_identity 逐位相同（同一代码路径）----
    bad_exact = 0
    ulp_only = 0
    max_delta = 0.0
    for a, b in pairs:
        got = _sdt_formula(a, b)
        auth = seq_identity.pair_identity(a, b, empty=float('nan'))
        ref = ref_sdt(a, b)
        if not _exact(got, auth):
            bad_exact += 1
        if not _same(got, ref, TOL):
            bad_exact += 1
        elif not _exact(got, ref):
            ulp_only += 1
            max_delta = max(max_delta, abs(got - ref))
    _check(bad_exact == 0, f'sdt_exact._sdt_formula == pair_identity(empty=NaN) '
                           f'且 == 原式（{len(pairs)} 对，逐位不一致 {bad_exact}）')
    print(f'         其中 1-ulp 级差异 {ulp_only} 对，最大 |Δ| = {max_delta:.2e}'
          f'（容差 {TOL:g}；输出精度 0.1 不受影响）')

    # ---- align_qc：等长输入（qc_alignment 强制等长）----
    eq = [(a, b) for a, b in pairs if len(a) == len(b)]
    bad = 0
    ulp_only = 0
    max_delta = 0.0
    for a, b in eq:
        got = aq_identity(a, b)
        ref = ref_align_qc(a, b)
        auth = seq_identity.pair_identity(a, b, gap_chars='-', empty=0.0)
        if not _exact(got, auth):
            bad += 1
        if not _same(got, ref, TOL):
            bad += 1
        elif not _exact(got, ref):
            ulp_only += 1
            max_delta = max(max_delta, abs(got - ref))
    _check(bad == 0, f'align_qc.pairwise_identity 等长 {len(eq)} 对 == 原式'
                     f'（仅 "-" 为 gap、百分比；逐位不一致 {bad}）')
    print(f'         其中 1-ulp 级差异 {ulp_only} 对，最大 |Δ| = {max_delta:.2e}')
    # 非等长：委托后按 zip 截断（与原式 len(s1)-gaps 的口径**有意不同**，
    # 但 qc_alignment 先 raise 等长错误，故生产路径不可达；这里只记录不判定）
    neq = [(a, b) for a, b in pairs if len(a) != len(b)]
    n_diff = sum(1 for a, b in neq if not _same(aq_identity(a, b),
                                              ref_align_qc(a, b), TOL))
    print(f'         非等长 {len(neq)} 对（生产不可达）：{n_diff} 对与原式不同'
          f'（原式分母用 len(s1)，委托版用 zip 长度；qc_alignment 已强制等长）')

    # ---- phylodyn：小数 0-1 / None ----
    bad = 0
    ulp_only = 0
    for a, b in pairs:
        got = pd_identity(a, b)
        ref = ref_phylodyn(a, b)
        auth = seq_identity.pair_identity(a, b, gap_chars='-', empty=None, scale=1.0)
        if got is not None:
            got_f = float(got)
        else:
            got_f = None
        if not _same(got_f, auth, TOL):
            bad += 1
        if not _same(got_f, ref, TOL):
            bad += 1
        elif got_f is not None and not _exact(got_f, ref):
            ulp_only += 1
    _check(bad == 0, f'phylodyn_alignqc.pairwise_identity == 原式'
                     f'（{len(pairs)} 对，小数 0-1 / None；不一致 {bad}）')
    print(f'         其中 1-ulp 级差异 {ulp_only} 对')
    # 空串必须 None，而不是 0.0
    _check(pd_identity('', 'ACGT') is None, '空串 → None（不是 0，也不是 0.0）')

    # ---- structcmp：round(...,2) / None ----
    bad = 0
    for a, b in pairs:
        if not _exact(new_structcmp(a, b), ref_structcmp(a, b)):
            bad += 1
    _check(bad == 0, f'structcmp 口径 == 原式（{len(pairs)} 对，round 2 位；不一致 {bad}）')
    _check(new_structcmp('---', '---') is None, '无可比位点 → None（JSON null）')


def section_c(tmpdir):
    """真实比对逐个产物对拍；另造注 gap 版本覆盖 gap 路径。"""
    print('\n[C] 真实比对：phylo 矩阵 == sdt 公式，且 NJ 距离 == 1 - identity')
    if not os.path.isfile(REAL_ALN):
        print(f'         跳过：找不到 {os.path.relpath(REAL_ALN, ROOT)}')
        _check(True, '真实比对缺失 → 跳过（非失败）')
        return
    rows = read_fasta_seq(REAL_ALN)
    print(f'         真实产物：{os.path.relpath(REAL_ALN, ROOT)}'
          f'（{len(rows)} 条 × {len(rows[0][1])} 列）')

    variants = [('原样', rows)]
    # 注 gap 版本：同一批真实序列内容，按 8% 注入 gap（'-' / '.'），覆盖 gap 路径
    rng = random.Random(20261104)
    gapped = [(nm, ''.join(rng.choice('-') if rng.random() < 0.04
                           else rng.choice('.') if rng.random() < 0.04
                           else ch for ch in s)) for nm, s in rows]
    variants.append(('注 gap 8%', gapped))

    for tag, rs in variants:
        p = os.path.join(tmpdir, 'aln_%s.fasta' % ('raw' if tag == '原样' else 'gap'))
        write_fasta(p, rs)
        names, mat = pairwise_identity_matrix(p)
        seqs = dict(rs)
        worst = 0.0
        bad = 0
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                got = _sdt_formula(seqs[names[i]], seqs[names[j]])
                exp = mat[i][j]
                d = abs(got - exp)
                worst = max(worst, d)
                if d > 1e-9:
                    bad += 1
        _check(bad == 0,
               f'{tag}：{len(names) * (len(names) - 1) // 2} 对 sdt == phylo 矩阵'
               f'（最大偏差 {worst:.2e} < 1e-9）')

        # NJ 向量化路径：距离必须 == 1 - identity（同一口径的等价实现）
        njn, dist = _nj_distance_matrix(p)
        assert njn == names, 'NJ 与矩阵的序列顺序不一致'
        w2 = 0.0
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                w2 = max(w2, abs(dist[i][j] - (1.0 - mat[i][j] / 100.0)))
        _check(w2 < 1e-12, f'{tag}：_nj_distance_matrix == 1 - identity'
                           f'（最大偏差 {w2:.2e} < 1e-12）')


def section_d():
    """空分母 / 空串契约逐处钉死（最易被"统一"改错的一档）。"""
    print('\n[D] 空分母 / 空串契约（NaN / 0.0 / None 三档互不串味）')
    a, b = '-' * 20, '.' * 20          # 无任何可比位点
    s = _sdt_formula(a, b)
    _check(isinstance(s, float) and math.isnan(s), 'sdt_exact：空分母 → NaN')
    _check(aq_identity(a, b) == 0.0, 'align_qc：空分母 → 0.0')
    _check(pd_identity(a, b) is None, 'phylodyn：空分母 → None')
    _check(new_structcmp(a, b) is None, 'structcmp：空分母 → None')
    _check(_same(seq_identity.pair_identity('', ''), 0.0) and
           seq_identity.pair_identity('', '', empty=None) is None,
           'pair_identity：empty 由调用方决定（默认 0.0 / 传 None 得 None）')
    # scale 语义：100 → 百分比；1.0 → 小数
    _check(seq_identity.pair_identity('ACGT', 'ACGT', scale=1.0) == 1.0 and
           seq_identity.pair_identity('ACGT', 'ACGT', scale=100.0) == 100.0,
           'pair_identity：scale=1.0 → 1.0；scale=100 → 100.0')


def main():
    print('identity 公式统一等价性守卫（seq_identity 唯一实现）')
    print(f'权威模块：{os.path.relpath(seq_identity.__file__, ROOT)}')

    rng = random.Random(20261104)
    pairs = [(rand_seq(rng, 60), rand_seq(rng, 60)) for _ in range(200)]
    pairs += [(rand_seq(rng, rng.randint(10, 60)),
               rand_seq(rng, rng.randint(10, 60))) for _ in range(20)]
    pairs += [('-' * 60, '-' * 60), ('.' * 30, 'A' * 30),
              ('', 'ACGT'), ('', ''), ('A', 'A'), ('A', 'C')]

    section_a(rng)
    section_b(pairs)
    tmpdir = tempfile.mkdtemp(prefix='vp_unify_aln_')
    try:
        section_c(tmpdir)
    finally:
        for f in os.listdir(tmpdir):
            os.remove(os.path.join(tmpdir, f))
        os.rmdir(tmpdir)
    section_d()

    print()
    if FAILS:
        print(f'✘ identity 统一守卫失败（{len(FAILS)} 条）')
        for m in FAILS:
            print(f'   - {m}')
        return 1
    print('UNIFY OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
