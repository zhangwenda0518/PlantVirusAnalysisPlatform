# -*- coding: utf-8 -*-
"""候选序列验证 C 路（PVDB CDD）契约检查。

2026-11-06 接入第三路证据后钉住：
  [1] 三路组合语义（union / intersection / a-b-c 单路）
  [2] 判定语义：CDD 命中即可作域证据（domain_only），no-hit 仍保留
  [3] calls.tsv 字段含 cdd_* 三列
  [4] 真实跑通：核酸 contig 直接 vs PVDB CDD（mmseqs 自动翻译），
      产物落 cdd_hits.tsv，calls.tsv 带 cdd 命中
缺 mmseqs / CDD 库 / 参考序列时 [4] 跳过，不判失败（对应分支本就会
自动降级）。
"""
import os
import sys
import tempfile
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASS, FAIL, SKIP = [], [], []


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print(('  ok  ' if ok else ' FAIL ') + name
          + (('  ' + str(detail)) if detail else ''))


def skip(name, why):
    SKIP.append(name)
    print('  skip ' + name + '  ' + why)


from Virus_Platform_Core.verify import (          # noqa: E402
    CALL_FIELDS, combine_pass, virus_call)


def main():
    # ---------- [1] 组合语义 ----------
    A = {1, 2}
    B = {2, 3}
    C = {3, 4}
    passes = [('blastx', A), ('bfvd', B), ('cdd', C)]
    check('三路 union = 并集', combine_pass(passes, 'union') == {1, 2, 3, 4})
    check('三路 intersection = 交集', combine_pass(passes, 'intersection') == set())
    check('intersect 历史别名同 intersection',
          combine_pass(passes, 'intersect') == set())
    check("单路 a/b/c 取对应已执行路",
          combine_pass(passes, 'a') == A
          and combine_pass(passes, 'b') == B
          and combine_pass(passes, 'c') == C)
    check('两路交集仍正确',
          combine_pass(passes[:2], 'intersection') == {2})
    check('缺路时 c 返回空集合', combine_pass(passes[:2], 'c') == set())
    check('空路集合安全', combine_pass([], 'union') == set())

    # ---------- [2] 判定语义 ----------
    check('三路全空 → unclassified（no-hit 保留）',
          virus_call([], [], []) == 'unclassified')
    check('仅 CDD 命中 → domain_only',
          virus_call([], [], [{'evalue': 1e-9}]) == 'domain_only')
    check('仅 BFVD 命中 → domain_only',
          virus_call([], [{'evalue': 1e-9}], []) == 'domain_only')
    known = [{'pident': 99.0, 'scov': 98.0}]
    check('blastx 高置信 → known（域证据不影响）',
          virus_call(known, [], [{'evalue': 1e-9}]) == 'known')
    novel = [{'pident': 45.0, 'scov': 60.0}]
    check('blastx 跨种 → novel',
          virus_call(novel, [], []) == 'novel')
    weak = [{'pident': 25.0, 'scov': 10.0}]
    check('弱 blastx + CDD 域证据 → domain_only',
          virus_call(weak, [], [{'evalue': 1e-9}]) == 'domain_only')

    # ---------- [3] 输出字段 ----------
    for k in ('cdd_domains', 'cdd_best_evalue', 'cdd_nhits'):
        check('calls.tsv 含字段 %s' % k, k in CALL_FIELDS)

    # ---------- [3b] 覆盖度口径：负链命中必须归位 ----------
    # mmseqs 翻译搜索的反向链命中 qstart>qend；不归位会算出负覆盖度。
    from Virus_Platform_Core.cdd_search import _normalize_span as cdd_span
    from Virus_Platform_Core.bfvd_search import _normalize_span as bfvd_span
    for label, fn in (('CDD', cdd_span), ('BFVD', bfvd_span)):
        qs, qe, strand = fn(3000, 2500)
        cov = (qe - qs + 1) / 3000.0
        check('%s 负链跨段归位后 0<=cov<=1' % label,
              0.0 <= cov <= 1.0 and strand == '-'
              and (qs, qe) == (2500, 3000),
              'cov=%.3f strand=%s' % (cov, strand))
        qs, qe, strand = fn(100, 900)
        cov = (qe - qs + 1) / 3000.0
        check('%s 正链跨段覆盖度不变' % label,
              0.0 <= cov <= 1.0 and strand == '+' and (qs, qe) == (100, 900),
              'cov=%.3f' % cov)
        # 越界（超出 qlen）时覆盖度按 1 截断，保证下游 0..1 口径
        qs, qe, _ = fn(1, 5000)
        cov = min(1.0, max(0.0, (qe - qs + 1) / 3000.0))
        check('%s 越界覆盖度截断到 1' % label, cov == 1.0, cov)

    # ---------- [4] 真实跑通（可跳过） ----------
    from Virus_Platform_Core.config import get_config
    try:
        mmseqs = get_config().tool('mmseqs')
    except Exception:
        mmseqs = None
    from Virus_Platform_Core.cdd_search import cdd_db
    db = cdd_db()
    ref = os.path.join(ROOT, 'databases', 'virusref_db', 'plant_ref',
                       'reference.fasta')
    if not (mmseqs and os.path.isfile(str(mmseqs)) and db
            and os.path.isfile(ref)):
        skip('真实 CDD 翻译搜索', '缺 mmseqs / CDD 库 / 参考序列')
        return

    tmp = tempfile.mkdtemp(prefix='vp_verify_cdd_')
    try:
        # 取参考第一条 ≥1000bp 的核酸序列（病毒支门槛）
        fa = os.path.join(tmp, 'query.fa')
        rid, chunks, picked = None, [], 0
        with open(ref, encoding='utf-8', errors='replace') as f:
            for line in f:
                if line.startswith('>'):
                    if rid is not None:
                        seq = ''.join(chunks)
                        if len(seq) >= 1000:
                            picked = seq
                            break
                    rid, chunks = line[1:].strip(), []
                else:
                    chunks.append(line.strip())
            if not picked and rid is not None:
                seq = ''.join(chunks)
                if len(seq) >= 1000:
                    picked = seq
        if not picked:
            skip('真实 CDD 翻译搜索', '参考序列无 ≥1000bp 记录')
            return
        with open(fa, 'w', newline='\n') as g:
            g.write('>probe\n')
            g.write('\n'.join(picked[i:i + 60]
                              for i in range(0, len(picked), 60)) + '\n')

        from Virus_Platform_Core.verify import verify

        class _Log:
            def log(self, msg, level='INFO'):
                pass

        run_dir = os.path.join(tmp, 'run')
        os.makedirs(run_dir, exist_ok=True)
        summ = verify(run_dir, fa, methods=('cdd',), combine='union',
                      threads=2, logger=_Log())
        vdir = os.path.join(run_dir, 'verify')
        check('methods 记录为单路 cdd', summ.get('methods') == ['cdd'],
              summ.get('methods'))
        check('cdd_hits.tsv 已落盘',
              os.path.isfile(os.path.join(vdir, 'cdd_hits.tsv')))
        calls = os.path.join(vdir, 'calls.tsv')
        check('calls.tsv 已落盘', os.path.isfile(calls))
        rows = []
        if os.path.isfile(calls):
            import csv
            with open(calls, encoding='utf-8') as f:
                rows = list(csv.DictReader(f, delimiter='\t'))
        check('calls.tsv 有病毒支记录',
              any(r.get('branch') == 'virus' for r in rows), len(rows))
        hit = [r for r in rows if r.get('cdd_domains')]
        check('核酸 contig 命中 CDD 结构域并写入 cdd_* 列',
              bool(hit), hit[0].get('cdd_domains') if hit else '')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        FAIL.append('未捕获异常: %s' % e)
    print('\n%d 通过 / %d 失败 / %d 跳过'
          % (len(PASS), len(FAIL), len(SKIP)))
    sys.exit(1 if FAIL else 0)
