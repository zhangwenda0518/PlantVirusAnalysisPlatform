# -*- coding: utf-8 -*-
"""页面巡检诊断：逐页访问并即时打印进度，定位 chromium 崩溃发生在哪一页。

用法: python tests/_diag_pages.py
"""
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from werkzeug.serving import make_server            # noqa: E402
import threading                                    # noqa: E402
import time                                         # noqa: E402

import app as app_module                            # noqa: E402

PORT = 18777
srv = make_server('127.0.0.1', PORT, app_module.app, threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(1.0)
BASE = f'http://127.0.0.1:{PORT}'


def pages():
    out = []
    for rule in app_module.app.url_map.iter_rules():
        if 'GET' not in rule.methods:
            continue
        r = rule.rule
        if (r.startswith('/api/') or r.startswith('/static')
                or '<' in r or r.rstrip('/').endswith('/virome')
                or r == '/virome'):
            continue
        out.append(r)
    return sorted(set(out))


from playwright.sync_api import sync_playwright      # noqa: E402

CHANNEL = os.environ.get('VP_CHANNEL') or None
print(f'浏览器 channel = {CHANNEL or "默认(headless shell)"}')

with sync_playwright() as pw:
    kw = {'headless': True,
          # 受限环境下 chromium 的沙箱与共享内存会引发随机段错误，
          # 关掉它们（只影响本机自动化测试，不影响平台本体）。
          'args': ['--no-sandbox', '--disable-dev-shm-usage',
                   '--disable-gpu']}
    if CHANNEL:
        kw['channel'] = CHANNEL
    kw.setdefault('args', []).append('--no-proxy-server')
    browser = pw.chromium.launch(**kw)

    all_pages = pages()
    for i, p in enumerate(all_pages, 1):
        # 每页独立 context：连续复用同一个 page 时，浏览器渲染进程会在
        # 第 6 页附近段错误（实测 headless shell 与完整 chromium 均如此），
        # 单页独立访问则始终正常 —— 属于累积状态问题，隔离后即稳定。
        ctx = browser.new_context(viewport={'width': 1440, 'height': 900})
        page = ctx.new_page()
        errs = []
        page.on('pageerror', lambda e: errs.append(('pageerror', str(e)[:160])))
        page.on('console', lambda m: errs.append(('console', m.text[:160]))
                if m.type == 'error' else None)
        page.on('response', lambda r: errs.append(('http' + str(r.status), r.url[:110]))
                if r.status >= 400 else None)
        try:
            page.goto(BASE + p, wait_until='load', timeout=25000)
            page.wait_for_timeout(700)
            n = len(errs)
            note = 'OK' if not n else f'{n} 问题: {errs[0][0]} {errs[0][1][:90]}'
        except Exception as e:
            note = f'EXC {str(e)[:120]}'
        print(f'[{i:2d}/{len(all_pages)}] {p:28s} {note}', flush=True)
        ctx.close()
    browser.close()
print('全部页面访问完成')
