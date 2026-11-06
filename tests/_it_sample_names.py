# -*- coding: utf-8 -*-
"""样品名归一化统一 + 磁盘真实目录名解析的回归测试。

修复前的问题：
  - 建目录走 pipeline._safe_sample_name（连续非法字符折叠成一个 '_'、
    去首尾 '._-'、截断 50）
  - 读路径走 web/common._safe_sample（逐字符替换，不去首尾、不截断）
  两套口径在中文/连续特殊字符/超长名下不一致：
    「样品A」→ 目录 `A`，读路径找 `__A` → 读不到自己刚建的样品；
  且规范化是**静默**的：前端 toast 显示用户输入的名字，实际目录却是另一个。

本脚本覆盖：
  1. safe_sample_name 的规则与幂等性（唯一权威实现）
  2. resolve_sample_name 以磁盘真实目录名为准（含手工/中文/带空格目录）
  3. 归一化不再是静默的（创建接口回 note + typed）
  4. 撞名错误可读（说明输入名会被规范化为哪个名字）
  5. 端到端：非规范命名的样品能被 /api/samples、/api/pipeline/<s>、
     /api/samples/<s>/<rel>、/clear 正常读写

沙箱约束：数据必须建在平台内（safe_open/check_path 限制），
故用 run/_it_sample_names_tmp/，跑完整体删除。
用法：python tests/_it_sample_names.py
"""
import io
import json
import os
import shutil
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


def main():
    from Virus_Platform_Core import config
    from Virus_Platform_Core.utils import (SAFE_SAMPLE_MAX, resolve_sample_name,
                                           safe_sample_name)

    print('[1] safe_sample_name 规则（唯一权威实现）')
    cases = [
        ('NX-5', 'NX-5'),                    # 规范名原样保留
        ('样品A', 'A'),                        # 中文折叠 + 去首下划线
        ('番茄 1号', '1'),                      # 空格与中文一起折叠
        ('my sample', 'my_sample'),           # 单个空格 → 下划线
        # '-' 属于合法字符集，不会被折叠；只有空格这种非法字符连续段折叠成 '_'
        ('a  --  b', 'a_--_b'),
        ('__x__', 'x'),                        # 去首尾下划线
        ('..hidden..', 'hidden'),              # 去首尾点
        ('a/b', 'a_b'),                        # 分隔符也算非法字符
        ('A' * 60, 'A' * SAFE_SAMPLE_MAX),    # 截断
        ('', 'sample'),                        # 空 → 兜底
        ('///', 'sample'),                     # 全是非法字符 → 兜底
    ]
    for raw, want in cases:
        got = safe_sample_name(raw)
        check(f'{raw!r} → {want!r}', got == want, f'实际 {got!r}')

    print('[2] 幂等：规范化过的名字再规范化不变')
    for raw, _w in cases:
        once = safe_sample_name(raw)
        check(f'idempotent({raw!r})', safe_sample_name(once) == once)

    print('[3] resolve_sample_name：以磁盘真实目录名为准')
    root = os.path.join(config.RUN_ROOT, '_it_sample_names_tmp')
    real_results = config.DIRS['results']
    try:
        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(root, exist_ok=True)
        # 各种"跟字符替换不一致"的目录名
        for d in ('CANON-1', 'my sample', '样品A', 'A' * 60, '_x'):
            os.makedirs(os.path.join(root, d), exist_ok=True)

        check('规范名原样命中', resolve_sample_name('CANON-1', root) == 'CANON-1')
        check('带空格的目录名原样命中（旧读路径会改成 my_sample）',
              resolve_sample_name('my sample', root) == 'my sample')
        check('中文目录名原样命中（旧读路径会改成 __A）',
              resolve_sample_name('样品A', root) == '样品A')
        check('超长目录名原样命中（旧读路径不截断，正好也命中）',
              resolve_sample_name('A' * 60, root) == 'A' * 60)
        check('以 _ 开头的目录名原样命中（旧实现会去掉下划线）',
              resolve_sample_name('_x', root) == '_x')
        check('不在磁盘上的名字 → 退回规范名',
              resolve_sample_name('NX-9', root) == 'NX-9')
        check('不在磁盘上的中文名 → 退回规范名（不会造出中文路径）',
              resolve_sample_name('新样品', root) == '新样品'.replace('新样品', 'sample')
              or resolve_sample_name('新样品', root) == 'sample',
              resolve_sample_name('新样品', root))

        print('[3b] 路径穿越/分隔符一律不原样接受')
        for bad in ('../secret', '..\\secret', 'a/b', 'a\\b', '..', '.', ''):
            got = resolve_sample_name(bad, root)
            check(f'{bad!r} → 规范名（不含分隔符/上跳）',
                  '/' not in got and '\\' not in got and got not in ('.', '..'),
                  f'实际 {got!r}')
        check('base 不存在时不抛异常（退回规范名）',
              resolve_sample_name('x', os.path.join(root, 'nope')) == 'x')

        print('[4] 两个历史实现的口径已一致（同一个权威实现）')
        from Virus_Platform_Core.pipeline import _safe_sample_name
        from Virus_Platform_Core.web.common import _safe_sample
        same = True
        for raw in ('NX-5', 'my sample', '_x', 'A' * 60, ''):
            if _safe_sample_name(raw) != safe_sample_name(raw):
                same = False
        check('pipeline._safe_sample_name 就是 safe_sample_name 的别名', same)
        # _safe_sample 现在是"解析器"：对不存在的规范名应与纯转换一致
        check('web._safe_sample 对不存在的名字等价于纯转换',
              _safe_sample('NX-9') == safe_sample_name('NX-9'))

        print('[5] 端到端：非规范命名的样品能被正常读写')
        # 造一个带空格目录名的"已存在样品"
        sdir = os.path.join(root, 'my sample', '00_prep')
        os.makedirs(sdir, exist_ok=True)
        with io.open(os.path.join(sdir, 'input.json'), 'w',
                     encoding='utf-8') as f:
            json.dump({'sample': 'my sample', 'r1': 'r1.fq', 'r2': '',
                       'project': 'P'}, f, ensure_ascii=False)
        with io.open(os.path.join(root, 'my sample', 'marker.txt'), 'w',
                     encoding='utf-8') as f:
            f.write('hello')

        # 先把配置首次加载走完（get_config 单例），否则下面 import app 的
        # 导入链会触发 _load → apply_output_root 重写 DIRS，把这里的临时
        # 覆盖冲回默认（2026-11-03：/api/samples 因此扫不到临时样品）。
        from Virus_Platform_Core.config import get_config
        get_config()
        config.DIRS['results'] = root
        import app as appmod
        cli = appmod.app.test_client()

        r = cli.get('/api/samples')
        names = [x['name'] for x in (r.get_json() or [])]
        check('HTTP 200 (/api/samples)', r.status_code == 200)
        check('/api/samples 列出真实目录名 my sample', 'my sample' in names,
              str(names))

        r = cli.get('/api/pipeline/my%20sample')
        check('HTTP 200 (/api/pipeline/my sample) —— 旧实现会 404',
              r.status_code == 200, str(r.status_code))
        if r.status_code == 200:
            d = r.get_json()
            check('接口回显的样品名是真实目录名',
                  d.get('sample') == 'my sample', str(d.get('sample')))
            check('接口读到了 input.json 里的 R1', d.get('r1') == 'r1.fq',
                  str(d.get('r1')))

        r = cli.get('/api/samples/my%20sample/marker.txt')
        check('HTTP 200 (/api/samples/my sample/marker.txt)',
              r.status_code == 200, str(r.status_code))
        check('文件内容正确', r.get_data(as_text=True) == 'hello')

        r = cli.get('/api/sample_files/my%20sample')
        check('HTTP 200 (/api/sample_files/my sample)', r.status_code == 200)
        check('文件列表含 marker.txt 与输入档案',
              {x['path'] for x in (r.get_json() or {}).get('files', [])}
              >= {'marker.txt', '00_prep/input.json'},
              str([x['path'] for x in (r.get_json() or {}).get('files', [])]))

        # 基于真实目录名派生的路由也要通
        r = cli.get('/api/pipeline/' + 'my%20sample' + '/run', )
        check('run 路由存在（错误码非 404）', r.status_code in (400, 405),
              str(r.status_code))

        print('[6] 归一化不再是静默的：创建接口回 note')
        r1 = os.path.join(ROOT, 'tests', 'mix_R1.fastq.gz')
        r = cli.post('/api/pipeline/create',
                     json={'sample': '样品A', 'r1': r1, 'r2': None,
                           'project': 'P'})
        check('HTTP 200 (create 样品A)', r.status_code == 200,
              str(r.status_code) + ' ' + r.get_data(as_text=True)[:120])
        d = r.get_json() or {}
        check('返回实际登记名 A', d.get('sample') == 'A', str(d.get('sample')))
        check('返回用户输入名 样品A', d.get('typed') == '样品A',
              str(d.get('typed')))
        check('返回非空 note 说明被规范化', bool(d.get('note')), str(d.get('note')))
        check('note 里同时出现输入名与实际名',
              '样品A' in (d.get('note') or '') and 'A' in (d.get('note') or ''),
              str(d.get('note')))
        check('磁盘上真的建了 A 目录',
              os.path.isdir(os.path.join(root, 'A')))

        print('[7] 输入名不需要规范化时不回 note')
        r = cli.post('/api/pipeline/create',
                     json={'sample': 'NX-77', 'r1': r1, 'r2': None})
        d = r.get_json() or {}
        check('sample == typed',
              d.get('sample') == 'NX-77' and d.get('typed') == 'NX-77', str(d))
        check('note 为空', not d.get('note'), str(d.get('note')))

        print('[8] 撞名错误把"输入名 → 规范名"讲清楚')
        r = cli.post('/api/pipeline/create',
                     json={'sample': '样品A', 'r1': r1, 'r2': None})
        check('HTTP 400', r.status_code == 400, str(r.status_code))
        msg = (r.get_json() or {}).get('error', '')
        check('错误里同时给出输入名与规范名',
              '样品A' in msg and 'A' in msg, msg)

        print('[9] 真实样品目录全部可解析（不回归）')
        config.DIRS['results'] = real_results
        real = sorted(n for n in os.listdir(real_results)
                      if os.path.isdir(os.path.join(real_results, n)))
        bad = [n for n in real if resolve_sample_name(n, real_results) != n]
        check(f'{len(real)} 个真实样品目录名解析后不变', not bad, str(bad))
    finally:
        config.DIRS['results'] = real_results
        shutil.rmtree(root, ignore_errors=True)

    print()
    if FAIL:
        print(f'✘ {len(FAIL)} 项未通过: ' + ', '.join(FAIL))
        return 1
    print('✔ 全部通过')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
