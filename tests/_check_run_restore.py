# -*- coding: utf-8 -*-
"""结果「刷新/切页不丢」的浏览器回归（2026-10-28）。

背景：工具结果原本只在任务完成那一刻渲染进 DOM，跳走再回来就空了。
`restoreLastResults()` 之前有两处短板，本脚本各给一条断言：

  A. 只认 `/tools` 的 `section.card[id^=t-]` —— 独立模块页
     （/hostremoval、/genome …）根本没有这种 section，直接 early-return。
  B. 拉的是默认 `/api/tool/runs`（全局最近 30 条）—— 老模块的最近一次
     运行早被新模块挤出 30 名，查不到就不恢复。

另有 C：富结果卡（kvsuite / consensus …）即便恢复了也只给「下载列表」，
   看不到表格与图 → 断言必须重放专用渲染函数（#kvResult 有内容）。

另有 D：历史区用同一份 30 条数据，老模块展开是空的 → 断言点击后条目数 > 0。

用法：python tests/_check_run_restore.py   （需要本机 Edge/Chrome）
Side effect：只读；不启动任何分析任务。
"""
import glob
import gzip
import json
import os
import shutil
import socket
import subprocess
import sys
import time

ROOT = r'D:\桌面\植物病毒分析平台'
PY = sys.executable

# 环境独立性（2026-11-06）：本检查原先假设「真实根盘上已有各工具历史运行」，
# 批量门禁给每个检查设 VP_RUN_ROOT=隔离根后，隔离根里未必有这些运行，
# 恢复/历史断言会全数翻红。改为：播种进**活动根**（VP_RUN_ROOT 或仓库根），
# 缺失才造最小假运行，结束时删除；kvsuite 富重放依赖真实分析产物结构，
# 无法用假数据保真 —— 活动根没有真实 kvsuite 运行时显式 SKIP。
SEED_DIRS = []


def _tool_runs_dir():
    # VP_RUN_ROOT 语义：直接替换 run/ 目录本身（门禁隔离根 = <tmp>/run），
    # 不是平台根 —— 2026-11-06 实测播种差一层导致隔离语境全空。
    root = os.environ.get('VP_RUN_ROOT') or os.path.join(ROOT, 'run')
    tr = os.path.join(root, 'tool_runs')
    os.makedirs(tr, exist_ok=True)
    return tr


def _seed_run(prefix, files):
    tr = _tool_runs_dir()
    name = f'{prefix}_20261106_000000_seedprobe'
    d = os.path.join(tr, name)
    if os.path.isdir(d):
        return name
    os.makedirs(d, exist_ok=True)
    SEED_DIRS.append(d)
    for fn, data in files.items():
        p = os.path.join(d, fn)
        if isinstance(data, bytes):          # gzip/二进制（PNG 魔数等）
            with open(p, 'wb') as f:
                f.write(data)
        else:
            with open(p, 'w', encoding='utf-8') as f:
                f.write(data)
    return name


def seed_runs():
    """活动根缺哪类运行就补哪类最小假运行（文件名满足各卡前缀识别即可）。"""
    if not glob.glob(os.path.join(_tool_runs_dir(), 'hostremoval_*')):
        _seed_run('hostremoval', {
            'kept_R1.fastq.gz': gzip.compress(b'@r1\nACGT\n+\nIIII\n'),
            'kept_R2.fastq.gz': gzip.compress(b'@r1\nACGT\n+\nIIII\n'),
            'summary.json': '{"n_kept": 1, "note": "run_restore 检查播种"}',
        })
    if not glob.glob(os.path.join(_tool_runs_dir(), 'align_*')):
        _seed_run('align', {
            'input.fasta': '>s1\nACGT\n',
            'summary.json': '{"note": "run_restore 检查播种"}',
        })
    if not glob.glob(os.path.join(_tool_runs_dir(), 'genoplot_*')):
        _seed_run('genoplot', {
            'genome.png': ('\x89PNG\r\n\x1a\n').encode('latin-1'),
            'summary.json': '{"note": "run_restore 检查播种"}',
        })


def real_kvsuite_run():
    """活动根里最新的「真实」kvsuite 运行（有 identify/ 分析产物结构才算；
    播种壳跑不了富重放）。返回目录名或 None。"""
    tr = _tool_runs_dir()
    for d in sorted(glob.glob(os.path.join(tr, 'kvsuite_*')), reverse=True):
        if os.path.isdir(os.path.join(d, 'identify')):
            return os.path.basename(d)
    return None


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


PASS, FAIL = [], []


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print(('  ok  ' if ok else ' FAIL ') + name + (('  ' + str(detail)) if detail else ''))


def main():
    seed_runs()
    port = free_port()
    srv = subprocess.Popen(
        [PY, '-c',
         'import sys\n'
         f'sys.path.insert(0, {ROOT!r})\n'
         'from werkzeug.serving import make_server\n'
         'import app\n'
         f"s = make_server('127.0.0.1', {port}, app.app, threaded=True)\n"
         "print('ready', flush=True)\n"
         's.serve_forever()'],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    drv = None
    try:
        for _ in range(150):
            with socket.socket() as s:
                if s.connect_ex(('127.0.0.1', port)) == 0:
                    break
            time.sleep(0.3)
        base = 'http://127.0.0.1:%d' % port

        from selenium import webdriver
        from selenium.webdriver.edge.options import Options
        o = Options()
        o.add_argument('--headless=new')
        o.add_argument('--window-size=1440,1100')
        drv = webdriver.Edge(options=o)

        def text_of(sel):
            return drv.execute_script(
                "const e=document.querySelector(arguments[0]);"
                "return e ? (e.innerText||'') : null", sel)

        def wait_filled(sel, timeout=8.0):
            end = time.time() + timeout
            while time.time() < end:
                t = text_of(sel)
                if t and t.strip():
                    return t
                time.sleep(0.4)
            return text_of(sel)

        # A + B：独立模块页 / 老模块卡
        cases = [
            ('/hostremoval', '#toolrun-hostremoval', '宿主去除（独立页）'),
            ('/genome', '#toolrun-genoplot', '基因组图（独立页）'),
            ('/tools#t-align', '#toolrun-align', '比对（老模块，曾被挤出 30 条）'),
        ]
        for path, sel, label in cases:
            drv.get(base + path)
            time.sleep(3.5)
            txt = wait_filled(sel)
            ok = bool(txt and ('上次运行' in txt or '恢复' in txt or len(txt) > 20))
            check('恢复：' + label + ' ' + path, ok, (txt or '')[:60].replace('\n', ' '))

        # C：富结果重放（需要活动根里有「真实」kvsuite 运行；播种壳无法
        # 保真重放）。重放是异步 fetch，负载下固定等待会翻红：轮询等展开。
        kvrun = real_kvsuite_run()
        if not kvrun:
            check('富结果：kvsuite 刷新后重放表格/图', True, '[SKIP] 活动根无真实 kvsuite 运行')
            check('富结果：kvsuite 标题带运行名', True, '[SKIP] 活动根无真实 kvsuite 运行')
        else:
            drv.get(base + '/tools#t-kvsuite')
            shown = None
            _end = time.time() + 10
            while time.time() < _end:
                shown = drv.execute_script(
                    "const e=document.getElementById('kvResult');"
                    "return e ? {vis: e.style.display, len: e.innerHTML.length} : null")
                if shown and shown['vis'] != 'none' and shown['len'] > 200:
                    break
                time.sleep(0.5)
            check('富结果：kvsuite 刷新后重放表格/图',
                  bool(shown and shown['vis'] != 'none' and shown['len'] > 200), shown)
            title = drv.execute_script(
                "const e=document.getElementById('kvTitle'); return e ? e.textContent : ''")
            check('富结果：kvsuite 标题带运行名', 'kvsuite_' in (title or ''), title)

        # E：恢复结果要能扛过空闲轮询（2026-11-05 用户报告「刚打开有结果，
        # 过一会儿自动消失」：injectStageLogs 每轮把所有 .toolrun 清空，任务
        # 一出列 /api/tasks 最近 120 条，恢复内容 ~8s 后被擦掉且无人回填）。
        # 用磁盘上有真实运行的比对卡验证：打开页 12 秒后（跨过 t≈8s 的
        # 首个空闲轮询）输出区仍有内容。
        drv.get(base + '/tools#t-align')
        time.sleep(3.5)
        first = text_of('#toolrun-align')
        check('持久：比对卡恢复出内容', bool(first and first.strip()),
              (first or '')[:40].replace('\n', ' '))
        time.sleep(8.5)      # 跨过 t≈8s 的空闲轮询（POLL_IDLE_MS = 8000）
        after = text_of('#toolrun-align')
        check('持久：空闲轮询后内容未被清空',
              bool(after and after.strip()), (after or '')[:40].replace('\n', ' '))

        # D：历史区（老模块不再空）
        drv.get(base + '/hostremoval')
        time.sleep(2.5)
        drv.execute_script(
            "document.querySelector('#rh-hostremoval .rh-head button').click()")
        time.sleep(2.0)
        n = drv.execute_script(
            "const b=document.querySelector('#rh-hostremoval .rh-body');"
            "return b ? b.querySelectorAll('div[style*=\"dashed\"]').length : -1")
        check('历史区：宿主去除列出历史条目', n and n > 0, n)

        # D2：归档入口存在（清理/归档区按钮）—— 中英两种界面都要认
        btns = drv.execute_script(
            "return [...document.querySelectorAll('#rh-hostremoval .rh-head button')]"
            ".map(b => b.textContent.trim())")
        joined = ' '.join(btns)
        check('历史区：有「清理…」与「归档区」入口',
              ('清理' in joined or 'Clean up' in joined)
              and ('归档' in joined or 'Archive' in joined), btns)
    finally:
        if drv:
            drv.quit()
        srv.terminate()
        time.sleep(1)
        for d in SEED_DIRS:
            shutil.rmtree(d, ignore_errors=True)

    print('\n%d 通过 / %d 失败' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
