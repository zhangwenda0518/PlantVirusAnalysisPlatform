# -*- coding: utf-8 -*-
"""重跑结果评分与新旧对照（2026-10-27）。

目的有三个，缺一不可：
  1. **口径自证**：用同一套评分逻辑重算归档的旧 `results.json`，必须复现
     论文里的 20/28（D1–D10 物种级灵敏度）与 8/8（D11–D18），否则说明我
     的评分实现与 `benchmark/compute_and_plot.py` 不等价，新数字不可比。
     校验基准为归档的 `benchmark/results/metrics_summary.csv` 逐行数值。
  2. **重跑评分**：对本轮 `results.json`（库=plant_ref）算同一套指标，逐集
     与聚合给新旧对照，尤其看 D7（旧快照缺 TSWV）是否回补。
  3. **定量 MAE**：从 `quant.sf` 按 accession 汇总到分离物层，对 D11–D18
     的期望占比算 MAE（百分点）——顺带结掉稿件里 D16 的 1.54 / 1.78 矛盾。

用法: python score.py [results.json 路径]
输出: score.json + compare.md
"""
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.join(os.path.dirname(HERE), 'benchmark')
NEW_RESULTS = os.path.normpath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(HERE, 'results.json')
OLD_RESULTS = os.path.join(BENCH, 'results', 'results.json')
OLD_METRICS = os.path.join(BENCH, 'results', 'metrics_summary.csv')
OLD_ART = os.path.join(BENCH, 'results', 'artifacts')
NEW_ART = os.path.join(HERE, 'artifacts')

GROUPS_D110 = json.load(open(os.path.join(BENCH, 'truth_viromock_d110_groups.json'),
                             encoding='utf-8'))
TRUTH_VM = json.load(open(os.path.join(BENCH, 'truth_viromock.json'), encoding='utf-8'))
VM_ALIAS = {
    '11': ['pepino mosaic virus', 'potexvirus pepini'],
    '12': ['african cassava mosaic virus', 'cassava mosaic virus',
           'begomovirus manihotis'],
    '13': ['banana streak'],
    '14': ['potato virus y', 'potyvirus yituberosi'],
    '15': ['eggplant mottled dwarf', 'alphanucleorhabdovirus melongenae'],
    '16': ['bell pepper endornavirus', 'bell pepper alphaendornavirus'],
    '17': ['little cherry virus 1'],
    '18': ['barley yellow dwarf', 'luteovirus pavhordei'],
}
# 作废运行（与 compute_and_plot.py 同口径）：minibwa 在 D5 提前退出
VOID = {'vm_d5_bwa'}


def norm(s):
    return re.sub(r'[^a-z0-9 ]', '', (s or '').lower()).strip()


def species_match(reported, aliases):
    r = norm(reported)
    return any(a in r or r in a for a in map(norm, aliases) if a)


def eval_agent_set(confirmed, groups):
    hit, fp_names = set(), {}
    for sp, _ in confirmed:
        nsp = norm(sp)
        matched = None
        for g in groups:
            for a in [norm(g['name'])] + [norm(x) for x in g.get('al', [])]:
                if a and (a in nsp or nsp in a):
                    matched = g['name']
                    break
            if matched:
                break
        if matched:
            hit.add(matched)
        else:
            fp_names[sp] = fp_names.get(sp, 0) + 1
    tp = len(hit)
    fn = len(groups) - tp
    return tp, sorted(fp_names, key=fp_names.get, reverse=True), max(fn, 0)


def prf(tp, fp, fn):
    s = tp / (tp + fn) if tp + fn else 0.0
    p = tp / (tp + fp) if tp + fp else 0.0
    f1 = 2 * p * s / (p + s) if p + s else 0.0
    fdr = fp / (tp + fp) if tp + fp else 0.0
    return {'sens': s, 'prec': p, 'fdr': fdr, 'f1': f1,
            'tp': tp, 'fp': fp, 'fn': fn}


def load_results(path):
    with open(path, encoding='utf-8') as f:
        return {r['exp']: r for r in json.load(f)}


def confirmed_of(rec):
    return [(c.get('species', ''), c) for c in (rec.get('confirmed') or [])]


def score_kv(R, bwa=False):
    """逐集 D1–D18 物种级评分（D1–10 用分组真值，D11–18 用 isolates 真值）。

    bwa=True 时评 minibwa 记录（exp 带 `_bwa` 后缀）。
    """
    rows = {}
    for d in range(1, 19):
        exp = f'vm_d{d}' + ('_bwa' if bwa else '')
        rec = R.get(exp)
        # 作废改成按记录判定（2026-10-27）：只有真的提前退出（wall_s < 5 s）才算 void。
        # 旧归档里 vm_d5_bwa 是 0.9 s 提前退出，仍会被判 void，口径自证不受影响；
        # 本次重跑 D5 正常完成（215 s），因此不再作废。
        early_exit = exp in VOID and float((rec or {}).get('wall_s') or 0) < 5
        if not rec or rec.get('void') or early_exit:
            rows[exp] = {'skipped': True,
                         'reason': 'void' if (exp in VOID) else 'no result'}
            continue
        if str(d) in GROUPS_D110:
            groups = GROUPS_D110[str(d)]
        else:
            t = TRUTH_VM.get(str(d)) or {}
            name = (t.get('species') or '').strip('*')
            groups = [{'name': name, 'al': VM_ALIAS.get(str(d), [])}]
        tp, fps, fn = eval_agent_set(confirmed_of(rec), groups)
        m = prf(tp, len(fps), fn)
        m.update({'exp': exp, 'dataset': f'D{d}',
                  'engine': rec.get('engine', 'salmon'),
                  'wall_s': rec.get('wall_s'), 'peak_rss_mb': rec.get('peak_rss_mb'),
                  'fp_species': fps[:6], 'truth_n': len(groups),
                  'confirmed_n': len(rec.get('confirmed') or [])})
        rows[exp] = m
    return rows


def agg(rows, exps):
    tp = sum(rows[e]['tp'] for e in exps if e in rows and 'tp' in rows[e])
    fp = sum(rows[e]['fp'] for e in exps if e in rows and 'fp' in rows[e])
    fn = sum(rows[e]['fn'] for e in exps if e in rows and 'fn' in rows[e])
    m = prf(tp, fp, fn)
    m['datasets'] = len([e for e in exps if e in rows and 'tp' in rows[e]])
    return m


def old_csv_rows():
    """归档 metrics_summary.csv → {(exp, engine): row}。"""
    out = {}
    # 归档 CSV 由 Excel 侧导出过，含 BOM（utf-8-sig），用普通 utf-8 读会把
    # 首列名读成 '\ufeff组'，整表静默跳过——2026-10-27 实测踩到。
    with open(OLD_METRICS, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            grp = row.get('组', '')
            meth = row.get('方法', '')
            ds = (row.get('数据集', '') or '').strip()
            m = re.match(r'D(\d+)', ds)
            if not m:
                continue
            d = int(m.group(1))
            if grp in ('VIROMOCK-D110', 'VIROMOCK-8'):
                exp = f'vm_d{d}'
                engine = 'minibwa' if 'minibwa' in meth else 'salmon'
            else:
                continue
            out[(exp, engine)] = row
    return out


def validate_old(R_old):
    """用本脚本口径重算旧结果，对比归档 metrics_summary.csv 逐行数值。"""
    rows = score_kv(R_old)
    rows_bwa = score_kv(R_old, bwa=True)
    csv_rows = old_csv_rows()
    checks = []
    for (exp, engine), row in csv_rows.items():
        exp_key = exp + ('_bwa' if engine == 'minibwa' else '')
        rec = R_old.get(exp_key)
        if not rec:
            checks.append({'exp': exp_key, 'engine': engine, 'status': 'archived-missing'})
            continue
        exp_rows = (rows_bwa if engine == 'minibwa' else rows).get(exp_key) or {}
        if exp_rows.get('skipped'):
            checks.append({'exp': exp, 'engine': engine, 'status': 'void'})
            continue
        ok_s = abs(exp_rows['sens'] - float(row['sens'])) < 1e-6
        ok_p = abs(exp_rows['prec'] - float(row['prec'])) < 1e-6
        checks.append({'exp': exp, 'engine': engine,
                       'status': 'ok' if (ok_s and ok_p) else 'MISMATCH',
                       'sens': exp_rows['sens'], 'sens_csv': float(row['sens']),
                       'prec': exp_rows['prec'], 'prec_csv': float(row['prec'])})
    # D1–D10 只评 salmon（csv 里 minibwa 行单独，种子脚本按 exp 取记录，
    # 旧 results.json 只有一份 vm_dD 记录：即 salmon 那次）
    d110 = agg(rows, [f'vm_d{d}' for d in range(1, 11)])
    d1118 = agg(rows, [f'vm_d{d}' for d in range(11, 19)])
    total = agg(rows, [f'vm_d{d}' for d in range(1, 19)])
    d110b = agg(rows_bwa, [f'vm_d{d}_bwa' for d in range(1, 11)])
    d1118b = agg(rows_bwa, [f'vm_d{d}_bwa' for d in range(11, 19)])
    return {'checks': checks,
            'aggregate': {'salmon': {'D1_D10': d110, 'D11_D18': d1118, 'total': total},
                          'minibwa': {'D1_D10': d110b, 'D11_D18': d1118b}}}


def quant_mae(art_root, R, label):
    """D11–D18 分离物层定量 MAE（百分点）。期望占比取 truth isolates 的 pct。"""
    out = {}
    ref_info = {}
    info_path = os.path.join(os.path.dirname(HERE), '..', '..', 'databases',
                             'virusref_db', 'plant_ref', 'reference.ref_info.tsv')
    try:
        with open(os.path.normpath(info_path), encoding='utf-8') as f:
            for row in csv.reader(f, delimiter='\t'):
                if row and not row[0].startswith('#') and len(row) > 2:
                    ref_info[row[0]] = row[2]
    except OSError:
        ref_info = {}
    for d in range(11, 19):
        t = TRUTH_VM.get(str(d)) or {}
        isolates = t.get('isolates') or []
        if not isolates:
            continue
        qf = None
        for cand in (os.path.join(art_root, f'vm_d{d}', 'quant', f'D{d}', 'quant.sf'),
                     os.path.join(art_root, f'vm_d{d}_bwa', 'quant', f'D{d}', 'quant.sf')):
            if os.path.isfile(cand):
                qf = cand
                break
        if not qf:
            out[f'D{d}'] = {'status': 'quant.sf missing'}
            continue
        num = {}
        with open(qf, encoding='utf-8') as f:
            f.readline()
            for line in f:
                p = line.rstrip('\n').split('\t')
                if len(p) >= 5:
                    num[p[0]] = float(p[4])
        tot = sum(num.values())
        if tot <= 0:
            out[f'D{d}'] = {'status': 'zero mapped'}
            continue
        diffs = []
        detail = []
        for iso in isolates:
            acc = iso.get('acc')
            exp_pct = float(iso.get('pct') or 0)
            obs = 0.0
            for k, v in num.items():
                if k.split('.')[0] == acc or k.startswith(acc):
                    obs += v
            obs_pct = 100.0 * obs / tot
            detail.append({'acc': acc, 'exp_pct': exp_pct,
                           'obs_pct': round(obs_pct, 2),
                           'abs_diff': round(abs(obs_pct - exp_pct), 2)})
            diffs.append(abs(obs_pct - exp_pct))
        out[f'D{d}'] = {'status': 'ok',
                        'mae_pp': round(sum(diffs) / len(diffs), 3) if diffs else None,
                        'n_isolates': len(isolates),
                        'detail': detail}
    return {'label': label, 'per_dataset': out}


def _pct(x):
    return f'{x:.1%}' if isinstance(x, (int, float)) else '—'


def _num(x, nd=1):
    return f'{x:.{nd}f}' if isinstance(x, (int, float)) else '—'


def write_compare(out):
    L = []
    L.append('# VIROMOCK discovery 面板重跑对照（库 = plant_ref）\n')
    L.append(f"生成时间：{out['generated_at']}　|　新结果：`{os.path.basename(out['new_results'])}`"
             f"　|　旧结果：`docs/paper/benchmark/results/results.json`\n")
    L.append('## 1. 评分口径自证\n')
    v = out.get('validation_of_scoring') or {}
    checks = v.get('checks') or []
    bad = [c for c in checks if c.get('status') == 'MISMATCH']
    L.append(f'- 与归档 `metrics_summary.csv` 逐行比对：**{len(checks)} 条，失配 {len(bad)} 条**。')
    L.append('- 该比对的意义：用**同一套评分实现**重算归档旧结果，若不能复现论文数字，'
             '则新数字与论文不可比。')
    for eng in ('salmon', 'minibwa'):
        a = (v.get('aggregate') or {}).get(eng) or {}
        for key in ('D1_D10', 'D11_D18'):
            b = a.get(key) or {}
            if b:
                L.append(f"- 旧[{eng}] {key}：{b['tp']}/{b['tp'] + b['fn']} "
                         f"= {_pct(b['sens'])}（prec {_pct(b['prec'])}，FP {b['fp']}）")
    if bad:
        L.append('\n失配明细：')
        for c in bad:
            L.append(f"- `{c}`")
    L.append('\n## 2. 逐集对照（物种级灵敏度 / 精确率）\n')
    for eng, suffix, title in (('salmon', '', 'salmon（默认引擎）'),
                               ('minibwa', '_bwa', 'minibwa（可计数审计路径）')):
        rows = [r for r in (out.get('new', {}).get('per_dataset') or {}).items()
                if r[0].endswith(suffix) and not r[1].get('skipped')
                and (suffix or not r[0].endswith('_bwa'))]
        if not rows:
            continue
        rows.sort(key=lambda r: int(re.search(r'\d+', r[0]).group()))
        L.append(f'\n### {title}\n')
        L.append('| 数据集 | 真值数 | 旧 TP/FP | 旧 sens | 旧 prec | 新 TP/FP | 新 sens | 新 prec | 新 wall | 新峰值内存 |')
        L.append('|---|---|---|---|---|---|---|---|---|---|')
        for exp, s in rows:
            o = (out.get('old', {}).get('per_dataset') or {}).get(exp) or {}
            L.append(f"| {s.get('dataset') or re.search(r'[Dd]\d+', exp).group()} | {s.get('truth_n')} "
                     f"| {o.get('tp', '—')}/{o.get('fp', '—')} | {_pct(o.get('sens'))} | {_pct(o.get('prec'))} "
                     f"| {s['tp']}/{s['fp']} | {_pct(s['sens'])} | {_pct(s['prec'])} "
                     f"| {_num(s.get('wall_s'))} s | {_num(s.get('peak_rss_mb'))} MB |")
    L.append('\n## 3. 聚合\n')
    L.append('| 面板 | 口径 | TP/(TP+FN) | sens | prec | FP 合计 |')
    L.append('|---|---|---|---|---|---|')
    for eng in ('salmon', 'minibwa'):
        agg_new = (out.get('new', {}).get('aggregate') or {})
        for key in ('D1_D10', 'D11_D18', 'total'):
            b = agg_new.get(key) if eng == 'salmon' else None
            if not b:
                continue
            L.append(f"| {key.replace('_', '–')} | {eng} | {b['tp']}/{b['tp'] + b['fn']} "
                     f"| {_pct(b['sens'])} | {_pct(b['prec'])} | {b['fp']} |")
        break
    L.append('\n## 4. 定量 MAE（D11–D18，分离物层，百分点）\n')
    L.append('| 数据集 | 重跑 MAE | 归档 MAE | 分离物数 |')
    L.append('|---|---|---|---|')
    qn = (out.get('quant_mae') or {}).get('rerun', {}).get('per_dataset') or {}
    qo = (out.get('quant_mae') or {}).get('archived') or {}
    qo = (qo.get('per_dataset') if qo else {}) or {}
    for d in range(11, 19):
        a = qn.get(f'D{d}') or {}
        b = qo.get(f'D{d}') or {}
        L.append(f"| D{d} | {_num(a.get('mae_pp'), 2)} | {_num(b.get('mae_pp'), 2)} "
                 f"| {a.get('n_isolates', '—')} |")
    L.append('\n## 5. 本轮与归档的已知差异（写进 Limitations 用）\n')
    L.append('- **库不同**：归档数字测于 `kv_index` 快照（缺 TSWV），本轮测于交付库 '
             '`plant_ref`（12,196 条参考，含 TSWV L/M/S）。D7 的差异主要来自这一点。')
    L.append('- **harness bug**：`benchmark/run_one.py` 原第 158 行 `load_ref_info()` '
             '未传参 → 恒读已删的 `kv_index` 路径，任何 `db_dir` 覆盖都会崩。'
             '本轮已修（改为 `load_ref_info(kv_info)`），归档的旧数字是在该 bug 潜伏期产生的'
             '（默认库存在时不影响数值）。')
    L.append('- **minibwa 索引**：`plant_ref/minibwa/ref_fingerprint.json` 记录的参考 '
             'size 与当前 fasta 不一致 → 首次 minibwa 运行会按 `index_ref_ok()` 自动重建索引'
             '（约 8 s），不影响可比性，但会抬高首个 minibwa 数据集的 wall time。')
    with open(os.path.join(HERE, 'compare.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')


def main():
    R_new = load_results(NEW_RESULTS)
    score_new = score_kv(R_new)
    new_agg = {'D1_D10': agg(score_new, [f'vm_d{d}' for d in range(1, 11)]),
               'D11_D18': agg(score_new, [f'vm_d{d}' for d in range(11, 19)]),
               'total': agg(score_new, [f'vm_d{d}' for d in range(1, 19)])}

    validation = None
    score_old = None
    old_agg = None
    if os.path.isfile(OLD_RESULTS):
        R_old = load_results(OLD_RESULTS)
        validation = validate_old(R_old)
        score_old = score_kv(R_old)
        old_agg = {'D1_D10': agg(score_old, [f'vm_d{d}' for d in range(1, 11)]),
                   'D11_D18': agg(score_old, [f'vm_d{d}' for d in range(11, 19)]),
                   'total': agg(score_old, [f'vm_d{d}' for d in range(1, 19)])}

    quant_new = quant_mae(NEW_ART, R_new, 'plant_ref rerun')
    quant_old = quant_mae(OLD_ART, None, 'archived kv_index run') if os.path.isdir(OLD_ART) else None

    out = {'generated_at': __import__('time').strftime('%Y-%m-%dT%H:%M:%S'),
           'new_results': NEW_RESULTS,
           'old_results': OLD_RESULTS if os.path.isfile(OLD_RESULTS) else None,
           'validation_of_scoring': validation,
           'new': {'per_dataset': score_new, 'aggregate': new_agg},
           'old': {'per_dataset': score_old, 'aggregate': old_agg},
           'quant_mae': {'rerun': quant_new, 'archived': quant_old}}
    with open(os.path.join(HERE, 'score.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    # ---- 控制台摘要 ----
    print('== 评分口径自证（对归档旧结果重算 vs metrics_summary.csv）==')
    if validation:
        bad = [c for c in validation['checks'] if c['status'] == 'MISMATCH']
        print(f'  逐行校验 {len(validation["checks"])} 条，失配 {len(bad)} 条')
        for c in bad:
            print('   MISMATCH', c)
        ag = validation['aggregate']
        for eng in ('salmon', 'minibwa'):
            a = ag.get(eng) or {}
            for key, pub in (('D1_D10', '20/28 = 71.4%'), ('D11_D18', '8/8 = 100%')):
                b = a.get(key) or {}
                if not b:
                    continue
                print(f"  旧[{eng}] {key} {b['tp']}/{b['tp'] + b['fn']}"
                      f" = {b['sens']:.1%}（论文 {pub}）")
    print('== 新跑（plant_ref）==')
    for name in ('D1_D10', 'D11_D18', 'total'):
        a = new_agg[name]
        print(f"  {name}: {a['tp']}/{a['tp'] + a['fn']} = {a['sens']:.1%}  "
              f"prec={a['prec']:.1%}  fp={a['fp']}")
    print('  逐集：')
    for d in range(1, 19):
        exp = f'vm_d{d}'
        s = score_new.get(exp) or {}
        o = (score_old or {}).get(exp) or {}
        if s.get('skipped'):
            print(f'   {exp}: skipped')
            continue
        print(f"   {exp}: sens {s['sens']:.0%} prec {s['prec']:.0%} "
              f"tp={s['tp']}/{s['truth_n']} fp={s['fp']} "
              f"（旧 sens {o.get('sens', float('nan')):.0%} tp={o.get('tp')}）")
    print('== 定量 MAE（D11–D18，百分点）==')
    for d in range(11, 19):
        n = quant_new['per_dataset'].get(f'D{d}') or {}
        o = (quant_old or {}).get('per_dataset', {}).get(f'D{d}') or {}
        print(f"   D{d}: 新 {n.get('mae_pp')} / 旧 {o.get('mae_pp')} "
              f"({n.get('status')})")
    print('[written] score.json')
    try:
        write_compare(out)
        print('[written] compare.md')
    except Exception as exc:                    # noqa: BLE001
        print(f'[warn] compare.md 生成失败：{exc!r}')


if __name__ == '__main__':
    main()
