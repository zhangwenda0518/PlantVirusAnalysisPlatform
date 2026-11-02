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
import socket
import subprocess
import sys
import time

ROOT = r'D:\桌面\植物病毒分析平台'
PY = sys.executable


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


PASS, FAIL = [], []


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print(('  ok  ' if ok else ' FAIL ') + name + (('  ' + str(detail)) if detail else ''))


def main():
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

        # C：富结果重放（kvsuite 有真实运行）
        drv.get(base + '/tools#t-kvsuite')
        time.sleep(4.0)
        shown = drv.execute_script(
            "const e=document.getElementById('kvResult');"
            "return e ? {vis: e.style.display, len: e.innerHTML.length} : null")
        check('富结果：kvsuite 刷新后重放表格/图',
              bool(shown and shown['vis'] != 'none' and shown['len'] > 200), shown)
        title = drv.execute_script(
            "const e=document.getElementById('kvTitle'); return e ? e.textContent : ''")
        check('富结果：kvsuite 标题带运行名', 'kvsuite_' in (title or ''), title)

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

    print('\n%d 通过 / %d 失败' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
