#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""子进程事件回传（spool → relay → 本地广播缓冲）自检。

为什么需要这个测试
------------------
对话坞里发起的跑批走 `mcp_call` → `mcp_console` 用 stdio 拉起的
`mcp_server.py` 子进程，流水线在子进程里跑，`broadcast` 的环形缓冲也是子进程
自己一份 —— 网页进程的 SSE 流看不到它的「📖 阶段开始 / ✔ 阶段完成 / 🏁 全部
完成」，对话坞里就没有卡片（2026-11-02 用户点名要补）。

补法：父进程拉起子进程时注入 `VP_EVENT_SPOOL=<dir>`，子进程把每条事件追加成
一行 JSON；网页进程的 relay 线程 tail 这个目录、`broadcast.ingest()` 回本地
缓冲（保留原 ts、打 relay=True、不重复写审计、不再回写 spool 造成回环）。

这个测试守的就是这几条边界：
  ① 未设置 env 时 publish 不落 spool（不污染正常进程）；
  ② 设置 env 后 publish 追加到 <dir>/<pid>.jsonl；
  ③ 真实**子进程**发布的事件，能被 relay 读回本地缓冲（端到端）；
  ④ relay 首次见到文件时从文件末尾开始（不回放历史）；
  ⑤ ingest 保留原始 ts、标记 relay、拒绝 relay 回环输入。

跑法：python tests/_check_ai_events_relay.py    （退出码 0 = 通过）
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from Virus_Platform_Core.ai_agent import broadcast, spool_relay  # noqa: E402

FAILS = []


def check(ok, msg):
    print(('  ✔ ' if ok else '  ✘ ') + msg)
    if not ok:
        FAILS.append(msg)


def _mk_tmp():
    base = os.path.join(ROOT, 'run', 'tmp')
    os.makedirs(base, exist_ok=True)
    return tempfile.mkdtemp(prefix='_it_relay_', dir=base)


def _find(marker):
    for ev in broadcast.events_since(0):
        if ev.get('sample') == marker:
            return ev
    return None


def main():
    print('子进程事件回传（spool → relay）检查')
    tmp = _mk_tmp()
    old_env = os.environ.pop(broadcast.SPOOL_ENV, None)
    old_root = spool_relay._spool_root
    try:
        spool_relay._spool_root = lambda: tmp
        spool_relay.reset_for_test()

        # ① 没设置 env：publish 不该产生任何 spool 文件
        broadcast.publish('stage_done', sample='RELAY-A', stage='fastp')
        check(os.listdir(tmp) == [], '未设置 VP_EVENT_SPOOL 时不落回传文件')

        # ② 设置 env 后 publish 追加到 <dir>/<pid>.jsonl
        os.environ[broadcast.SPOOL_ENV] = tmp
        broadcast.publish('stage_done', sample='RELAY-B', stage='fastp',
                          name='⓪ Fastp 质控')
        path = os.path.join(tmp, '%d.jsonl' % os.getpid())
        check(os.path.isfile(path), '设置 VP_EVENT_SPOOL 后写入 <pid>.jsonl')
        with open(path, encoding='utf-8') as f:
            first = json.loads(f.readline())
        check(first.get('type') == 'stage_done' and first.get('sample') == 'RELAY-B',
              '回传行内容是完整事件（type/sample 都在）')

        # ④ relay 首次见到文件从末尾开始：上面那条不该被读回
        spool_relay.reset_for_test()
        before = len(broadcast.events_since(0))
        spool_relay.poll_once()
        check(len(broadcast.events_since(0)) == before,
              'relay 首见文件从末尾起读，不回放已有历史')

        # ③ 真实子进程发布 → relay 读回本地缓冲
        marker = 'RELAY-C%d' % (int(time.time()) % 100000)
        code = (
            "import sys\n"
            "sys.path.insert(0, %r)\n"      # 本机 3rd\python 的 ._pth 会无视 PYTHONPATH
            "from Virus_Platform_Core.ai_agent import broadcast\n"
            "broadcast.publish('stage_done', sample=%r, stage='assembly',"
            " name='③ 组装·分类·提取')\n" % (ROOT, marker))
        env = dict(os.environ)
        env[broadcast.SPOOL_ENV] = tmp
        cp = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env,
                            capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=120)
        check(cp.returncode == 0,
              '子进程发布事件成功（returncode=0）' + (
                  '' if cp.returncode == 0 else '  stderr: ' + (cp.stderr or '')[-400:]))
        got = None
        for _ in range(100):                     # 最多等 ~20s（机器忙时子进程启动慢）
            spool_relay.poll_once()
            got = _find(marker)
            if got:
                break
            time.sleep(0.2)
        check(got is not None, '子进程事件经 relay 进入本进程缓冲')
        if got:
            check(got.get('relay') is True, '回传事件带 relay=True 标记')
            check(got.get('name') == '③ 组装·分类·提取', '事件字段原样保留（name）')

        # ⑤ ingest 语义：保留 ts、拒绝回环
        ev = broadcast.ingest({'id': 999, 'type': 'run_done', 'ts': 12345.5,
                               'sample': 'RELAY-D'})
        check(ev is not None and abs(float(ev['ts']) - 12345.5) < 0.001,
              'ingest 保留原始 ts（不改成"刚刚发生"）')
        check(ev is not None and ev.get('relay') is True, 'ingest 打上 relay=True')
        check(broadcast.ingest({'type': 'run_done', 'relay': True}) is None,
              'ingest 拒绝 relay 回环输入')
        check(broadcast.ingest({'sample': 'x'}) is None, 'ingest 拒绝无 type 输入')
        check(broadcast.ingest('nope') is None, 'ingest 拒绝非 dict 输入')
    finally:
        spool_relay._spool_root = old_root
        spool_relay.reset_for_test()
        if old_env is None:
            os.environ.pop(broadcast.SPOOL_ENV, None)
        else:
            os.environ[broadcast.SPOOL_ENV] = old_env
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILS:
        print('FAILED: %d 项' % len(FAILS))
        return 1
    print('子进程事件回传检查通过 ✔')
    return 0


if __name__ == '__main__':
    sys.exit(main())
