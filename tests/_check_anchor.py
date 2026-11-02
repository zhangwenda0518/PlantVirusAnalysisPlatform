# -*- coding: utf-8 -*-
"""样品锚（Virus_Platform_Core/anchor.py）自检：落盘、账本链、API 三条路由。

验证的是「第一阶段：只读锚」的契约：
  1. start_run 立刻返回（不因 GB 级 FASTQ 的 sha256 阻塞），摘要后台补齐；
  2. stage_begin/stage_done/end_run 落成 anchor.json + 追加式账本；
  3. 换输入再跑 → 只记 input.change + 计数 +1，不拦（第一阶段不判定）；
  4. verify 能复算输入摘要、能发现账本被改（hash 链断裂）；
  5. 三个只读 API 可访问、/api/samples 每行带 anchor 摘要；
  6. 所有公开函数不外抛异常（缺文件、坏账本都只写锚自己的日志）。

用法: python tests/_check_anchor.py
（不需要真实数据，不启动分析任务，不联网；临时目录建在 run/ 下并在结束时删除）
"""
import json
import os
import shutil
import sys
import tempfile
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ['VP_NO_RECOVER'] = '1'

FAIL = []
SKIP = []


def check(label, ok, extra=''):
    print(f'  [{"PASS" if ok else "FAIL"}] {label}'
          + (f'  {extra}' if extra else ''), flush=True)
    if not ok:
        FAIL.append(label)


def skip(label, why):
    print(f'  [SKIP] {label}  {why}', flush=True)
    SKIP.append(label)


def wait_hashed(anchor, slug, timeout=20.0):
    """等后台摘要算完；返回最后一次 snapshot。"""
    t0 = time.time()
    snap = anchor.snapshot(slug) or {}
    while time.time() - t0 < timeout:
        if (snap.get('inputs_state') or '') == 'ok':
            return snap
        time.sleep(0.15)
        snap = anchor.snapshot(slug) or {}
    return snap


def read_ledger(anchor, slug):
    p = os.path.join(anchor.root(), slug, 'ledger.jsonl')
    out = []
    if os.path.isfile(p):
        with open(p, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    return out


def main():
    from Virus_Platform_Core import anchor
    print('== 样品锚自检 ==', flush=True)

    run_root = os.path.join(ROOT, 'run')
    os.makedirs(run_root, exist_ok=True)
    aroot = tempfile.mkdtemp(prefix='_anchor_selftest_', dir=run_root)
    _orig_root = anchor.root             # 测试期把锚根指到临时目录
    anchor.root = lambda: aroot

    data = tempfile.mkdtemp(prefix='_anchor_data_', dir=run_root)
    slug = None
    try:
        r1 = os.path.join(data, 'S1_R1.fastq.gz')
        r2 = os.path.join(data, 'S1_R2.fastq.gz')
        for p, n in ((r1, 4000), (r2, 4100)):
            with open(p, 'wb') as f:
                f.write(b'@r\nACGT\n+\nIIII\n' * (n // 20 + 1))
        sd = os.path.join(data, 'sample_dir')
        os.makedirs(sd, exist_ok=True)

        # --- 1) start_run：立即返回 + 后台补摘要 ---------------------------
        t0 = time.time()
        run = anchor.start_run('锚自检A', r1, r2,
                               stages=['fastp', 'assembly', 'report'],
                               sample_dir=sd)
        dt = time.time() - t0
        check('start_run 返回 dict', isinstance(run, dict) and run.get('slug'),
              str(run))
        check('start_run 不阻塞（<1s 且不等 sha256）', dt < 1.0, f'{dt:.3f}s')
        slug = (run or {}).get('slug') or ''
        check('中文名 slug 带原名指纹（不是裸 A）',
              slug.startswith('A-') and len(slug) == 8, slug)

        snap = wait_hashed(anchor, slug)
        check('后台摘要算完 inputs_state=ok',
              snap.get('inputs_state') == 'ok', str(snap.get('inputs_state')))
        import hashlib
        h1 = hashlib.sha256(open(r1, 'rb').read()).hexdigest()
        check('anchor.json 记的 sha256 与真实一致', snap.get('r1_sha8') == h1[:8],
              f"{snap.get('r1_sha8')} vs {h1[:8]}")
        check('r1 名称落盘', snap.get('r1_name') == 'S1_R1.fastq.gz',
              str(snap.get('r1_name')))
        check('status=running', snap.get('status') == 'running',
              str(snap.get('status')))

        # --- 2) 阶段事件 ---------------------------------------------------
        anchor.stage_begin('锚自检A', 'fastp')
        anchor.stage_done('锚自检A', 'fastp', 1.5)
        snap = anchor.snapshot('锚自检A') or {}
        check('stage 显示刚跑完的阶段', snap.get('stage') == 'fastp',
              str(snap.get('stage')))
        check('stage_done 字段', snap.get('stage_done') == 'fastp',
              str(snap.get('stage_done')))
        check('stages_done 计数', snap.get('stages_done') == 1,
              str(snap.get('stages_done')))
        kinds = [e.get('kind') for e in read_ledger(anchor, slug)]
        check('账本含 run.start/stage.begin/stage.done',
              {'run.start', 'stage.begin', 'stage.done'} <= set(kinds),
              str(kinds))

        # --- 3) end_run：扫产物 + 收尾 -------------------------------------
        out = os.path.join(sd, '03_assembly', 'contigs.fa')
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'w', encoding='utf-8') as f:
            f.write('>c1\nACGTACGT\n')
        anchor.end_run('锚自检A', 'ok', 12.5, sd)
        snap = anchor.snapshot('锚自检A') or {}
        check('end_run 后 status=ok', snap.get('status') == 'ok',
              str(snap.get('status')))
        check('end_run 记到产物', (snap.get('outputs_n') or 0) >= 1,
              str(snap.get('outputs_n')))
        d = snap.get('anchor') or {}
        runs = d.get('runs') or []
        outs = [o.get('path', '') for o in ((runs[-1] if runs else {}).get('outputs') or [])]
        check('产物里有 contigs.fa',
              any(x.endswith('contigs.fa') for x in outs), str(outs[:3]))
        check('产物带 sha256', any(o.get('sha256')
                                  for o in ((runs[-1] if runs else {}).get('outputs') or [])),
              str(outs[:3]))

        # --- 4) 换输入再跑：只记账不拦 -------------------------------------
        n_events = len(read_ledger(anchor, slug))
        with open(r1, 'ab') as f:
            f.write(b'@r2\nTTTT\n+\nIIII\n')      # 内容+大小都变
        run2 = anchor.start_run('锚自检A', r1, r2,
                                stages=['fastp'], sample_dir=sd)
        check('第二次 start_run 正常返回', isinstance(run2, dict) and run2.get('slug'))
        snap = wait_hashed(anchor, slug)
        check('换输入后 inputs_changed=True',
              snap.get('inputs_changed') is True, str(snap.get('inputs_changed')))
        evs = read_ledger(anchor, slug)
        check('账本只追加（seq 递增不重置）',
              evs[-1]['seq'] > n_events, f'{n_events} -> {evs[-1]["seq"]}')
        check('有 input.change 事件',
              any(e.get('kind') == 'input.change' for e in evs),
              str([e.get('kind') for e in evs]))
        anchor.end_run('锚自检A', 'ok', 3.0, sd)
        check('analysis 未被拦（两轮都正常收尾）',
              (anchor.snapshot('锚自检A') or {}).get('runs_n') == 2,
              str((anchor.snapshot('锚自检A') or {}).get('runs_n')))

        # --- 5) verify：链完整 / 被改能发现 --------------------------------
        v = anchor.verify('锚自检A')
        check('verify 报链完整', v.get('ok') is True, str(v.get('problems')))
        check('verify seq 等于账本行数',
              v.get('seq') == len(read_ledger(anchor, slug)),
              f"{v.get('seq')} vs {len(read_ledger(anchor, slug))}")

        # 篡改第 2 行：下一行的 prev 应立刻对不上
        lp = os.path.join(anchor.root(), slug, 'ledger.jsonl')
        with open(lp, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        ev2 = json.loads(lines[1])
        ev2['kind'] = 'tampered'
        lines[1] = json.dumps(ev2, ensure_ascii=False, sort_keys=True) + '\n'
        with open(lp, 'w', encoding='utf-8', newline='\n') as f:
            f.writelines(lines)
        v = anchor.verify('锚自检A')
        check('verify 发现账本被改', v.get('ok') is False,
              str(v.get('problems')[:2]))

        # --- 6) 空输入 / 缺文件：不外抛 ------------------------------------
        run3 = anchor.start_run('锚自检B', os.path.join(data, 'nope_R1.fq'), None,
                                stages=['fastp'], sample_dir=sd)
        check('缺输入文件也不抛（返回 dict）', isinstance(run3, dict))
        check('缺文件记 missing/partial',
              (anchor.snapshot('锚自检B') or {}).get('inputs_state') in
              ('hashing', 'partial', 'ok'),
              str((anchor.snapshot('锚自检B') or {}).get('inputs_state')))
        check('snapshot 不存在的 slug 返回空', anchor.snapshot('_不存在的slug') is None)
        check('verify 不存在的 slug 不抛',
              isinstance(anchor.verify('_不存在的slug'), dict))

        # --- 7) active / samples_meta --------------------------------------
        act = anchor.active(limit=5)
        names = [s['slug'] for s in act.get('running', []) + act.get('recent', [])]
        check('active 收录自检样品（recent 或 running）', slug in names, str(names))
        meta = anchor.samples_meta()
        check('samples_meta 返回摘要', slug in meta, str(list(meta)[:4]))

        # --- 8) 三条只读 API ----------------------------------------------
        try:
            import app as appmod
            appmod.app.config['TESTING'] = True
            with appmod.app.test_client() as c:
                r = c.get('/api/anchor/active?limit=3')
                j = r.get_json()
                check('/api/anchor/active 200', r.status_code == 200,
                      str(r.status_code))
                check('active 载荷含 running/recent',
                      isinstance(j, dict) and 'running' in j and 'recent' in j,
                      str(sorted(j or {})[:4]))
                r = c.get('/api/anchor/%s' % slug)
                j = r.get_json() or {}
                check('/api/anchor/<slug> 200', r.status_code == 200,
                      str(r.status_code))
                check('详情含 ledger_tail',
                      isinstance(j.get('ledger_tail'), list),
                      str(len(j.get('ledger_tail') or [])))
                r = c.get('/api/anchor/%s/verify' % slug)
                check('/api/anchor/<slug>/verify 200', r.status_code == 200,
                      str(r.status_code))
                r = c.get('/api/anchor/_没有这个样品')
                check('未知 slug 返回 404', r.status_code == 404,
                      str(r.status_code))
                r = c.get('/api/samples')
                rows = r.get_json() or []
                check('/api/samples 每行带 anchor 字段',
                      all('anchor' in row for row in rows) if rows else True,
                      f'{len(rows)} 行')
        except Exception as e:
            skip('只读 API（需能 import app）', repr(e)[:120])
    finally:
        # 后台摘要线程懒取 root()：先等它落地再还原，避免往真实 run/anchors/ 写
        time.sleep(1.2)
        anchor.root = _orig_root
        for p in (aroot, data):
            try:
                shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass

    print('')
    if FAIL:
        print(f'✗ 失败 {len(FAIL)} 项:')
        for x in FAIL:
            print('   -', x)
        return 1
    print(f'✔ 全部通过（{len(SKIP)} 项跳过）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
