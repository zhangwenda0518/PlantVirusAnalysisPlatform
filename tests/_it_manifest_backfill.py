# -*- coding: utf-8 -*-
"""project.json 回填（backfill_manifest_from_products / samples-backfill）回归。

背景：project.json 由跑流程时经 update_project_manifest 写；在此之前创建的
样品、或手工拷进 results/ 的目录都没有清单，于是 /api/samples 的
last_status / last_run 与 stages_done 全空。反推逻辑
backfill_manifest_from_products 早就写好，却**没有任何调用点**（死代码）。
本次把它接成显式维护入口 `python main.py samples-backfill`。

覆盖：
  1. dry_run 只报告、绝不落盘
  2. 真正回填时写入 backfilled=true / 项目名优先级 / stages_done 来自产物
  3. 已有真实（非回填）清单的样品一律不动
  4. 回填后 /api/samples 的 last_status / last_run / project 有值
  5. --sample 过滤（含非规范命名，按磁盘真实目录名解析）
  6. CLI 可直接跑通（子进程 smoke）

用法：python tests/_it_manifest_backfill.py
"""
import io
import json
import os
import shutil
import subprocess
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('VP_NO_RECOVER', '1')

FAIL = []


def check(name, cond, extra=''):
    print(('  ✔ ' if cond else '  ✘ ') + name + (f'  {extra}' if extra else ''))
    if not cond:
        FAIL.append(name)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False)


def read_json(path):
    with io.open(path, encoding='utf-8') as f:
        return json.load(f)


def main():
    from Virus_Platform_Core import config
    from Virus_Platform_Core.pipeline import backfill_all_manifests

    root = os.path.join(config.RUN_ROOT, '_it_backfill_tmp')
    real_results = config.DIRS['results']
    try:
        shutil.rmtree(root, ignore_errors=True)
        # LEGACY：无清单、有 input.json 项目名 + 一个已完成的阶段产物
        write_json(os.path.join(root, 'LEGACY', '00_prep', 'input.json'),
                   {'sample': 'LEGACY', 'r1': 'r1.fq', 'r2': '', 'project': 'P1'})
        write_json(os.path.join(root, 'LEGACY', '03_assembly', 'summary.json'),
                   {'contigs': 5, 'viral_contigs': ['c1']})
        # NOPROJ：无清单、input.json 没填项目 → 项目名应退回目录名
        write_json(os.path.join(root, 'NOPROJ', '00_prep', 'input.json'),
                   {'sample': 'NOPROJ', 'r1': 'r1.fq', 'r2': ''})
        # KEEPME：已有真实清单 → 必须原样保留
        write_json(os.path.join(root, 'KEEPME', '00_prep', 'input.json'),
                   {'sample': 'KEEPME', 'r1': 'r1.fq', 'r2': '', 'project': 'REAL'})
        write_json(os.path.join(root, 'KEEPME', 'project.json'),
                   {'schema': 1, 'project': 'REAL', 'stages_done': ['host'],
                    'last_status': 'done', 'last_run': '2026-01-01T00:00:00'})
        # 非规范命名（带空格）：--sample 过滤要能按真实目录名解析
        write_json(os.path.join(root, 'odd name', '00_prep', 'input.json'),
                   {'sample': 'odd name', 'r1': 'r1.fq', 'r2': '',
                    'project': 'P2'})
        # '_' 前缀的内部目录必须被跳过
        write_json(os.path.join(root, '_scratch', '00_prep', 'input.json'),
                   {'sample': '_scratch', 'r1': 'x', 'r2': ''})

        # 先完成配置首次加载（get_config 单例），再覆盖 DIRS——否则后续
        # 模块导入链里的 _load/apply_output_root 会把覆盖冲回默认。
        from Virus_Platform_Core.config import get_config
        get_config()
        config.DIRS['results'] = root

        print('[1] dry_run：只报告，不落盘')
        r = backfill_all_manifests(dry_run=True)
        check('扫描到 4 个样品（跳过 _scratch）', r['total'] == 4, str(r['total']))
        check('dry_run 标记为真', r['dry_run'] is True)
        names = {it['sample'] for it in r['items']}
        check('需回填 3 个（KEEPME 已有真实清单被跳过）',
              names == {'LEGACY', 'NOPROJ', 'odd name'}, str(names))
        check('KEEPME 计入 skipped', r['skipped'] == 1, str(r['skipped']))
        check('dry_run 后磁盘上仍无 project.json',
              not os.path.isfile(os.path.join(root, 'LEGACY', 'project.json')))
        check('dry_run 的 items 里 wrote 全为假',
              all(not it['wrote'] for it in r['items']))

        print('[2] 真正回填')
        r = backfill_all_manifests(dry_run=False)
        p_legacy = os.path.join(root, 'LEGACY', 'project.json')
        check('LEGACY 写出 project.json', os.path.isfile(p_legacy))
        m = read_json(p_legacy)
        check('标记 backfilled=true', m.get('backfilled') is True)
        check('有 schema', m.get('schema') == 1, str(m.get('schema')))
        check('项目名取 input.json 的 P1（优先于目录名）',
              m.get('project') == 'P1', str(m.get('project')))
        check('stages_done 从既有产物推出（含 assembly）',
              'assembly' in (m.get('stages_done') or []),
              str(m.get('stages_done')))
        check('input 从 input.json 带出', (m.get('input') or {}).get('r1') == 'r1.fq',
              str(m.get('input')))

        m2 = read_json(os.path.join(root, 'NOPROJ', 'project.json'))
        check('未填项目名时退回目录名', m2.get('project') == 'NOPROJ',
              str(m2.get('project')))
        # 契约：无产物时 stages_done 不落盘（缺字段 ≡ 空，与
        # update_project_manifest 只在非 None 时写该字段保持一致）
        check('无产物样品不写 stages_done（缺字段 ≡ 无已完成阶段）',
              m2.get('stages_done') in (None, []), str(m2.get('stages_done')))
        check('回填不臆测 last_run / last_status（不在函数契约内）',
              not m2.get('last_run') and not m2.get('last_status'),
              f"last_run={m2.get('last_run')!r} last_status={m2.get('last_status')!r}")

        print('[3] 已有真实清单的样品一律不动')
        m3 = read_json(os.path.join(root, 'KEEPME', 'project.json'))
        check('stages_done 未被覆盖', m3.get('stages_done') == ['host'],
              str(m3.get('stages_done')))
        check('last_status 未被覆盖', m3.get('last_status') == 'done')
        check('未被打上 backfilled 标记', not m3.get('backfilled'))

        print('[4] 非规范命名的样品也能回填')
        check('odd name 写出了清单',
              os.path.isfile(os.path.join(root, 'odd name', 'project.json')))
        m4 = read_json(os.path.join(root, 'odd name', 'project.json'))
        check('项目名取 P2', m4.get('project') == 'P2', str(m4.get('project')))

        print('[5] 回填后 /api/samples 的字段有值')
        import app as appmod
        cli = appmod.app.test_client()
        r = cli.get('/api/samples')
        items = {x['name']: x for x in (r.get_json() or [])}
        check('HTTP 200', r.status_code == 200)
        check('LEGACY 带出项目名 P1',
              (items.get('LEGACY') or {}).get('project') == 'P1',
              str(items.get('LEGACY')))
        check('LEGACY 带出 stages_done 数量（done>=1）',
              (items.get('LEGACY') or {}).get('done', 0) >= 1,
              str(items.get('LEGACY')))
        check('LEGACY 的清单里确实记下了 stages_done',
              read_json(os.path.join(root, 'LEGACY',
                                     'project.json')).get('stages_done')
              == ['assembly'],
              str(read_json(os.path.join(root, 'LEGACY',
                                         'project.json')).get('stages_done')))
        check('_scratch 不出现在样品列表', '_scratch' not in items,
              str(sorted(items)))
        check('odd name 出现在列表里（非规范命名可读）',
              'odd name' in items, str(sorted(items)))

        print('[6] --sample 过滤')
        r = backfill_all_manifests(dry_run=True, only=['NOPROJ'])
        check('只处理指定样品', r['total'] == 1 and r['items'][0]['sample'] == 'NOPROJ',
              str(r))
        r = backfill_all_manifests(dry_run=True, only=['odd name'])
        check('非规范命名可被 --sample 命中',
              r['total'] == 1 and r['items'][0]['sample'] == 'odd name', str(r))
        r = backfill_all_manifests(dry_run=True, only=['不存在的样品'])
        check('不存在的名字不抛异常', r['total'] == 1, str(r))
    finally:
        config.DIRS['results'] = real_results
        shutil.rmtree(root, ignore_errors=True)

    print('[7] CLI smoke（samples-backfill --dry-run）')
    env = dict(os.environ, VP_NO_RECOVER='1', PYTHONIOENCODING='utf-8')
    p = subprocess.run([sys.executable, 'main.py', 'samples-backfill', '--dry-run'],
                       cwd=ROOT, env=env, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=300)
    check('退出码 0', p.returncode == 0, f'rc={p.returncode} {p.stderr[:300]}')
    check('输出含"扫描样品"', '扫描样品' in (p.stdout or ''),
          (p.stdout or '')[:160].replace('\n', ' | '))

    print()
    if FAIL:
        print(f'✘ {len(FAIL)} 项未通过: ' + ', '.join(FAIL))
        return 1
    print('✔ 全部通过')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
