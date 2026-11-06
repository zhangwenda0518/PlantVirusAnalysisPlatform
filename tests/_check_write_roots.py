# -*- coding: utf-8 -*-
"""自定义输出根 × 子进程写盘白名单（2026-11-02 用户实测的崩溃点）。

现场：用户把「输出目录」设成 G:\\植物病毒分析平台\\run\\fastq（平台外的另一个
副本目录），点公共检索。检索本身跑通（472 条 Run 汇总成功），最后写
query.json / download_links.csv 时报：

    ValueError: 路径越界（仅允许平台或输出目录内）:
    G:\\植物病毒分析平台\\run\\fastq\\meta_search\\Lycium_barbarum_...\\search\\download_links.csv

根因：公共检索的引擎脚本（public_meta/search_engine.py）是被 engine_cmd() 当
**裸脚本**拉起的子进程，里面没人构造 Config 单例，config 的模块级
_OUTPUT_ROOT 还是 ''，于是 write_roots() 只含平台根与 RUN_ROOT，用户自己配的
输出目录不在白名单里 —— 自己配的目录，自己判越界。

本用例守三件事：
1. _persisted_roots() 能从 platform.json 读出配置的根（不构造 Config）；
2. write_roots() 把这些根并入白名单（判重）；
3. **全新子进程**里不构造 Config 时，safe_open 写到该根下仍然成功
   （这是崩溃时的真实形态；只测函数不测子进程会漏）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

FAILS = []


def check(ok, msg):
    print(('  ✔ ' if ok else '  ✘ ') + msg)
    if not ok:
        FAILS.append(msg)


def main():
    sys.path.insert(0, ROOT)
    tmp = tempfile.mkdtemp(prefix='vp_roots_')
    out_root = os.path.join(tmp, '外部输出根')
    os.makedirs(out_root, exist_ok=True)
    cfg = os.path.join(tmp, 'platform.json')
    with open(cfg, 'w', encoding='utf-8') as f:
        json.dump({'output_root': out_root, 'input_root': ''}, f)

    try:
        from Virus_Platform_Core import config as C

        old_cfg = C.CONFIG_FILE
        old_cache = getattr(C, '_PERSISTED_ROOTS_CACHE', None)
        try:
            C.CONFIG_FILE = cfg
            C._PERSISTED_ROOTS_CACHE = (None, None)
            roots = C._persisted_roots()
            check(any(os.path.normcase(r) == os.path.normcase(out_root) for r in roots),
                  '_persisted_roots 读到平台外的输出根')
            wroots = C.write_roots()
            check(any(os.path.normcase(os.path.normpath(r)) == os.path.normcase(out_root)
                      for r in wroots),
                  'write_roots 把该输出根并入写白名单')
            check(len([r for r in wroots
                       if os.path.normcase(os.path.normpath(r)) == os.path.normcase(out_root)]) == 1,
                  '白名单里不重复登记同一个根')
        finally:
            C.CONFIG_FILE = old_cfg
            C._PERSISTED_ROOTS_CACHE = old_cache

        # ③ 真实形态：裸脚本子进程，不构造 Config
        script = (
            "import os,sys\n"
            "sys.path.insert(0, %r)\n"
            "from Virus_Platform_Core import config as C\n"
            "C.CONFIG_FILE = %r\n"
            "C._PERSISTED_ROOTS_CACHE = (None, None)\n"
            "from Virus_Platform_Core.utils import safe_open\n"
            "p = os.path.join(%r, 'meta_search', '_probe', 'download_links.csv')\n"
            "f = safe_open(p, 'wt'); f.write('ok\\n'); f.close()\n"
            "print('WROTE', os.path.getsize(p))\n"
        ) % (ROOT, cfg, out_root)
        r = subprocess.run([PY, '-c', script], capture_output=True, text=True,
                           cwd=ROOT, encoding='utf-8', errors='replace', timeout=180)
        check(r.returncode == 0 and 'WROTE' in (r.stdout or ''),
              '裸脚本子进程可写入自定义输出根')
        if r.returncode != 0:
            print((r.stdout or '')[-400:])
            print((r.stderr or '')[-800:])

        # ④ 清空输出根时不应再放行那个旧路径（防止配置清了还留后门）
        with open(cfg, 'w', encoding='utf-8') as f:
            json.dump({'output_root': ''}, f)
        from Virus_Platform_Core import config as C2
        C2.CONFIG_FILE = cfg
        C2._PERSISTED_ROOTS_CACHE = (None, None)
        check(not C2._persisted_roots(), '输出根清空后不再登记（无残留后门）')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if FAILS:
        print('FAILED: %d 项' % len(FAILS))
        return 1
    print('自定义输出根 × 子进程白名单检查通过 ✔')
    return 0


if __name__ == '__main__':
    sys.exit(main())
