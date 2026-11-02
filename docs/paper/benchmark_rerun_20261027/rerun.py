# -*- coding: utf-8 -*-
"""VIROMOCK discovery 面板重跑 + 归档（2026-10-27）。

背景：2026-09 的 20/28（71.4%）物种级灵敏度是在 kv_index 快照库上测的，
该快照缺 TSWV（D7 两个真值被判 FN），且库目录已于 2026-09-29 被
plant_ref 取代、`_bench_kvs2/`、`_bench_cons/` 从未归档 —— 头条数字
无法原位复跑。本脚本用**当前交付库** `databases/virusref_db/plant_ref`
重跑 discovery 18 个数据集，并把「库指纹 + 每集输入 sha256 + 产物 hash」
一并归档，使 §3.3 的数字可复核。

与 `benchmark/run_bench.py` 的关系：逐集仍调用同一个 `run_one.py`
（保证被测对象、命令、`--align-threads 4` 完全一致），差别只有三点：
  1. spec 显式带 `db_dir`（plant_ref，而不是已删的 kv_index）；
  2. 每集记录输入 sha256、峰值内存、耗时，并写 manifest.json 库指纹；
  3. 收尾把关键产物复制到 `artifacts/<exp>/` 后删除中间文件。

用法:
  python rerun.py --dry-run                      # 只打印计划
  python rerun.py --datasets D11                 # 先冒烟一集
  python rerun.py --engine salmon                # 18 集 salmon（默认）
  python rerun.py --engine minibwa               # 18 集 minibwa
  python rerun.py --engine both                  # 两引擎全跑
续跑：results.json 里已有的 exp 默认跳过，`--force` 强制重跑。
"""
import argparse
import glob
import hashlib
import json
import os
import platform as _platform
import shutil
import subprocess
import sys
import time

import psutil

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
BENCH = os.path.join(REPO, 'docs', 'paper', 'benchmark')
RUN_ONE = os.path.join(BENCH, 'run_one.py')

RUNS = HERE
LIB = os.path.join(REPO, 'databases', 'virusref_db', 'plant_ref')
VM = os.environ.get('VP_BENCH_VM') or r'E:\谷歌下载\测试数据验证'

RESULTS = os.path.join(RUNS, 'results.json')
MANIFEST = os.path.join(RUNS, 'manifest.json')
DIGESTS = os.path.join(RUNS, '_input_digests.json')
LOGDIR = os.path.join(RUNS, 'logs')
SPECS = os.path.join(RUNS, '_specs')
ART = os.path.join(RUNS, 'artifacts')

SINGLE_ENDED = (5, 6, 10)         # 与 benchmark/run_bench.py 保持一致
DATASETS = list(range(1, 19))

# 关键产物（与 run_bench.py cleanup_dir 的 keep_patterns 一致）
KEEP = [
    os.path.join('identify', 'all_viruses.best.summary.tsv'),
    # 2026-10-28：确诊档之外再留一张疑似档表，便于按两级口径复核（平台默认就写这张表）
    os.path.join('identify', 'all_viruses.review.tsv'),
    os.path.join('quant', '*', 'quant.sf'),
    os.path.join('stats', '*.refstats.tsv'),
    'identify_run.log',
    '*.log',
    'sheet.tsv',
]


# ---------------------------------------------------------------- 输入与指纹
def sha256_file(path, chunk=8 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def input_digest(path, cache):
    """返回输入文件摘要；按 (path, size, mtime_ns) 缓存，避免重复算 GB 级哈希。"""
    if not path or not os.path.isfile(path):
        return {'path': path, 'exists': False}
    st = os.stat(path)
    key = os.path.abspath(path).lower()
    rec = cache.get(key)
    if rec and rec.get('size') == st.st_size and rec.get('mtime_ns') == st.st_mtime_ns:
        return rec
    rec = {'path': os.path.abspath(path), 'exists': True,
           'size': st.st_size, 'mtime_ns': st.st_mtime_ns,
           'sha256': sha256_file(path)}
    cache[key] = rec
    return rec


def count_fasta_headers(path):
    n = 0
    with open(path, 'rb') as f:
        for line in f:
            if line.startswith(b'>'):
                n += 1
    return n


def count_lines(path):
    n = 0
    with open(path, 'rb') as f:
        for _ in f:
            n += 1
    return n


def dir_stat(path, top=6):
    """目录指纹：文件数、总字节、最新 mtime（用于索引新鲜度）。"""
    if not os.path.isdir(path):
        return {'path': path, 'exists': False}
    n = 0
    total = 0
    newest = 0.0
    for root, _dirs, files in os.walk(path):
        for fn in files:
            fp = os.path.join(root, fn)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            n += 1
            total += st.st_size
            newest = max(newest, st.st_mtime)
    files = sorted(os.listdir(path))[:top]
    return {'path': path, 'exists': True, 'files_n': n, 'bytes': total,
            'newest_mtime': time.strftime('%Y-%m-%dT%H:%M:%S',
                                          time.localtime(newest)) if newest else None,
            'newest_mtime_epoch': newest, 'sample_files': files}


def git_rev():
    try:
        out = subprocess.run(['git', '-C', REPO, 'rev-parse', 'HEAD'],
                             capture_output=True, text=True, timeout=60)
        rev = (out.stdout or '').strip()
        dirty = subprocess.run(['git', '-C', REPO, 'status', '--porcelain'],
                               capture_output=True, text=True, timeout=60)
        return {'git_rev': rev, 'dirty_files': len([l for l in (dirty.stdout or '').splitlines() if l.strip()])}
    except Exception as e:                      # noqa: BLE001
        return {'git_rev': None, 'error': repr(e)}


def build_manifest(digest_cache):
    """库/环境指纹。库指纹是本次归档的核心：没有它，数字与库的对应关系无从谈起。"""
    ref = os.path.join(LIB, 'reference.fasta')
    info = os.path.join(LIB, 'reference.ref_info.tsv')
    lib = {
        'dir': LIB,
        'reference_fasta': {'path': ref, 'bytes': os.path.getsize(ref),
                            'headers': count_fasta_headers(ref),
                            'sha256': input_digest(ref, digest_cache)['sha256']},
        'ref_info_tsv': {'path': info, 'bytes': os.path.getsize(info),
                         'lines': count_lines(info),
                         'sha256': input_digest(info, digest_cache)['sha256']},
        'indexes': {name: dir_stat(os.path.join(LIB, name))
                    for name in ('salmon_k31', 'salmon_k15', 'minibwa')},
        'manifest_json': None,
    }
    mj = os.path.join(LIB, 'manifest.json')
    if os.path.isfile(mj):
        try:
            lib['manifest_json'] = json.load(open(mj, encoding='utf-8'))
        except Exception:                       # noqa: BLE001
            lib['manifest_json'] = {'error': 'unparsable'}
    prev = {}
    if os.path.isfile(MANIFEST):
        try:
            prev = json.load(open(MANIFEST, encoding='utf-8'))
        except Exception:                       # noqa: BLE001
            prev = {}
    man = {
        'created_at': prev.get('created_at') or time.strftime('%Y-%m-%dT%H:%M:%S'),
        'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'note': 'VIROMOCK discovery 重跑归档：库=当前交付库 plant_ref；'
                '被测对象与 benchmark/run_one.py 完全一致',
        'host': {'node': _platform.node(), 'platform': _platform.platform(),
                 'cpu_logical': os.cpu_count(),
                 'ram_gb': round(psutil.virtual_memory().total / 2 ** 30, 1)},
        'python': {'version': sys.version.split()[0], 'executable': sys.executable,
                   'psutil': psutil.__version__},
        'repo': git_rev(),
        'library': lib,
        'harness': {'run_one': RUN_ONE,
                    'run_one_sha256': sha256_file(RUN_ONE),
                    'align_threads': 4,
                    'engine_default': 'salmon'},
    }
    return man


def save_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp, path)


# ------------------------------------------------------------------ 单集执行
def spec_for(d, engine):
    single = d in SINGLE_ENDED
    r1 = os.path.join(VM, f'Dataset_{d}.fastq.gz' if single
                      else f'Dataset_{d}_R1.fastq.gz')
    r2 = None if single else os.path.join(VM, f'Dataset_{d}_R2.fastq.gz')
    exp = f'vm_d{d}' if engine == 'salmon' else f'vm_d{d}_bwa'
    spec = {'type': 'kv', 'name': f'D{d}', 'r1': r1, 'r2': r2,
            'engine': engine, 'db_dir': LIB,
            'out': os.path.join(RUNS, exp)}
    return exp, spec


def monitor(proc, timeout_s):
    """轮询整棵进程树的 RSS；超时杀树。返回 (峰值字节, 是否超时)。"""
    peak = 0
    p = psutil.Process(proc.pid)
    t0 = time.time()
    while proc.poll() is None:
        try:
            total = p.memory_info().rss
            for c in p.children(recursive=True):
                try:
                    total += c.memory_info().rss
                except (psutil.Error, OSError):
                    pass
            peak = max(peak, total)
        except (psutil.Error, OSError):
            pass
        if time.time() - t0 > timeout_s:
            try:
                for c in p.children(recursive=True):
                    c.kill()
                p.kill()
            except psutil.Error:
                pass
            return peak, True
        time.sleep(1.0)
    return peak, False


def keep_artifacts(exp, out_dir):
    """复制关键产物到 artifacts/<exp>/ 后删除实验目录（避免几十 GB 中间文件）。"""
    art = os.path.join(ART, exp)
    saved = []
    for pat in KEEP:
        for src in glob.glob(os.path.join(out_dir, pat)):
            if not os.path.isfile(src):
                continue
            rel = os.path.relpath(src, out_dir)
            dst = os.path.join(art, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                shutil.copy2(src, dst)
                if rel not in saved:
                    saved.append(rel)
            except OSError:
                pass
    shutil.rmtree(out_dir, ignore_errors=True)
    return saved


def run_one(exp, spec, timeout_s):
    os.makedirs(LOGDIR, exist_ok=True)
    os.makedirs(SPECS, exist_ok=True)
    spec_path = os.path.join(SPECS, exp + '.json')
    save_json(spec_path, spec)
    log_path = os.path.join(LOGDIR, exp + '.driver.txt')
    t0 = time.time()
    with open(log_path, 'wb') as lf:
        proc = subprocess.Popen([sys.executable, RUN_ONE, spec_path],
                                stdout=lf, stderr=subprocess.STDOUT, cwd=REPO)
        peak, timed_out = monitor(proc, timeout_s)
        rc = proc.wait()
    wall = round(time.time() - t0, 1)
    text = ''
    try:
        text = open(log_path, encoding='utf-8', errors='replace').read()
    except OSError:
        pass
    res = {}
    for line in text.splitlines():
        if line.startswith('RESULT:'):
            try:
                res = json.loads(line[len('RESULT:'):])
            except ValueError:
                res = {}
    out_dir = spec['out']
    artifacts = keep_artifacts(exp, out_dir)
    rec = {'exp': exp, 'dataset': spec['name'], 'engine': spec['engine'],
           'driver_wall_s': wall, 'peak_rss_mb': round(peak / 2 ** 20, 1),
           'returncode': rc, 'timed_out': timed_out,
           'artifacts': artifacts, 'finished_at': time.strftime('%Y-%m-%dT%H:%M:%S')}
    rec.update(res)
    return rec, text


# ---------------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--engine', default='salmon',
                    choices=['salmon', 'minibwa', 'both'])
    ap.add_argument('--datasets', default='',
                    help='逗号分隔，如 D1,D3,D11；留空=全部 18 集')
    ap.add_argument('--force', action='store_true', help='已有结果也重跑')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--timeout-salmon', type=int, default=3600)
    ap.add_argument('--timeout-minibwa', type=int, default=7200)
    args = ap.parse_args()

    ds = DATASETS
    if args.datasets.strip():
        ds = [int(x.strip().lstrip('Dd')) for x in args.datasets.split(',') if x.strip()]

    engines = ['salmon', 'minibwa'] if args.engine == 'both' else [args.engine]

    if not os.path.isfile(os.path.join(LIB, 'reference.fasta')):
        sys.exit(f'[库缺失] {LIB}\\reference.fasta 不存在')

    digest_cache = {}
    if os.path.isfile(DIGESTS):
        try:
            digest_cache = json.load(open(DIGESTS, encoding='utf-8'))
        except Exception:                       # noqa: BLE001
            digest_cache = {}

    man = build_manifest(digest_cache)
    save_json(DIGESTS, digest_cache)
    save_json(MANIFEST, man)
    print(f"[manifest] 库={LIB}")
    print(f"[manifest] fasta {man['library']['reference_fasta']['bytes']} B / "
          f"{man['library']['reference_fasta']['headers']} 条 / "
          f"sha256={man['library']['reference_fasta']['sha256'][:16]}…")
    print(f"[manifest] ref_info {man['library']['ref_info_tsv']['lines']} 行 / "
          f"sha256={man['library']['ref_info_tsv']['sha256'][:16]}…")
    for name, st in man['library']['indexes'].items():
        print(f"[manifest] 索引 {name}: files={st.get('files_n')} "
              f"newest={st.get('newest_mtime')}")

    # 载入已有全部记录（含另一引擎的），--force 只影响"是否跳过"，不应该丢历史记录。
    existing = {}
    if os.path.isfile(RESULTS):
        try:
            for r in json.load(open(RESULTS, encoding='utf-8')):
                if r.get('exp'):
                    existing[r['exp']] = r
        except Exception:                       # noqa: BLE001
            existing = {}
    done = {} if args.force else {
        k: v for k, v in existing.items()
        if v.get('confirmed') is not None and not v.get('returncode')}
    records = list(existing.values())

    plan = []
    for engine in engines:
        for d in ds:
            exp, spec = spec_for(d, engine)
            if exp in done:
                print(f'[skip] {exp} 已有结果（--force 可重跑）')
                continue
            plan.append((exp, spec, engine, d))
    if args.dry_run:
        for exp, spec, engine, _d in plan:
            print(f'[plan] {exp} engine={engine} r1={spec["r1"]} r2={spec["r2"]}')
        print(f'[plan] 共 {len(plan)} 集')
        return
    if not plan:
        print('[done] 没有待跑实验')
        return

    t_all = time.time()
    for i, (exp, spec, engine, d) in enumerate(plan, 1):
        # 输入摘要（首次算，之后命中缓存）
        r1d = input_digest(spec['r1'], digest_cache)
        r2d = input_digest(spec['r2'], digest_cache) if spec.get('r2') else None
        save_json(DIGESTS, digest_cache)
        if not r1d.get('exists'):
            print(f'[{i}/{len(plan)}] {exp} 输入缺失：{spec["r1"]} → 跳过')
            continue
        print(f'[{i}/{len(plan)}] {exp} engine={engine} '
              f'r1={os.path.basename(spec["r1"])} ({r1d["size"] / 2 ** 20:.1f} MB)'
              + (f' r2={os.path.basename(spec["r2"])}' if spec.get('r2') else ''),
              flush=True)
        timeout_s = args.timeout_minibwa if engine == 'minibwa' else args.timeout_salmon
        rec, _text = run_one(exp, spec, timeout_s)
        rec['inputs'] = {'r1': r1d, 'r2': r2d}
        rec['library_sha256'] = man['library']['reference_fasta']['sha256']
        rec['library_headers'] = man['library']['reference_fasta']['headers']
        records = [r for r in records if r.get('exp') != exp] + [rec]
        save_json(RESULTS, records)
        ok = 'ok' if rec.get('returncode') == 0 and rec.get('confirmed') is not None else 'FAIL'
        n = len(rec.get('confirmed') or [])
        print(f'    → {ok} wall={rec.get("wall_s")}s driver={rec["driver_wall_s"]}s '
              f'peak={rec["peak_rss_mb"]}MB confirmed={n} '
              f'artifacts={len(rec.get("artifacts") or [])}', flush=True)

    print(f'[done] {len(plan)} 集，总耗时 {round((time.time() - t_all) / 60, 1)} min')


if __name__ == '__main__':
    main()
