# -*- coding: utf-8 -*-
"""全站巡检：每个模块在保活外壳里都能开、都不刷新、切回来还是原来那份。

用户 2026-11-02 原话：「其他页面模块呢，全部 修正」。所以这里**逐个模块**过一遍：

1. 父文档停在 `/tasks`，逐个 `VP_SHELL.open(route)`；
2. 每个 iframe 里写一个只属于它的标记（`__mark = route`）；
3. 切到别的模块再切回，标记必须还在 —— 证明 iframe 没被卸载/重建；
4. 顺带跑一次「页面内 JS 跳模块」（`gotoModule`）：在 A 帧里调它去 B，A 帧必须
   原样留着，父层多出一个 B 帧；
5. 父文档自身的模块（`/tasks`）点进去应当收回外壳（`showOwn`），不能套 iframe。

跑法：python tests/_it_page_shell_all.py    （自起 8923；缺 Edge 自动 SKIP）
"""
import os
import sys
import threading
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('VP_NO_RECOVER', '1')

from werkzeug.serving import make_server  # noqa: E402
import app as A  # noqa: E402

# 全部页面路由（来自 app.url_map 里无参数、非 /api、非 /static 的那批）
ROUTES = ['/', '/samples', '/tools', '/refdata', '/meta', '/prescreen', '/virome',
          '/download', '/build', '/results', '/submit', '/primer', '/orf', '/genome',
          '/annotation', '/cds-export', '/hostremoval', '/logan', '/settings', '/mcp',
          '/help']
OWN_ROUTE = '/tasks'      # 外壳自己的页面
HOST, PORT = '127.0.0.1', 8923
BASE = 'http://%s:%d' % (HOST, PORT)


def main():
    try:
        from selenium import webdriver
        from selenium.webdriver.edge.options import Options
    except Exception as e:
        print('SKIP: 没有 selenium（%s）' % e)
        return 0

    srv = make_server(HOST, PORT, A.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(1.0)

    opt = Options()
    opt.add_argument('--headless=new')
    opt.add_argument('--window-size=1400,780')
    opt.add_argument('--log-level=3')
    try:
        drv = webdriver.Edge(options=opt)
    except Exception as e:
        srv.shutdown()
        print('SKIP: 没有可用的 Edge（%s）' % e)
        return 0

    fails = []
    try:
        drv.get(BASE + '/tasks')
        time.sleep(2.5)
        drv.execute_script("window.__noReload = 'SWEEP';")

        for route in ROUTES:
            drv.execute_script("window.VP_SHELL.open(arguments[0]);", route)
            time.sleep(2.2)
            n = drv.execute_script('return document.querySelectorAll(".vp-shell-frame").length;')
            alive = drv.execute_script('return window.__noReload || "";')
            url = drv.current_url
            src = drv.execute_script("""
              var fs = document.querySelectorAll('.vp-shell-frame');
              for (var i = 0; i < fs.length; i++) {
                if (fs[i].classList.contains('on')) {
                  try { fs[i].contentWindow.__mark = arguments[0]; } catch (e) { return 'XORIGIN'; }
                  return (fs[i].getAttribute('src') || '');
                }
              }
              return 'NO-ON-FRAME';
            """, route)

            # 切走再切回：同一份 DOM 必须还在
            drv.execute_script("window.VP_SHELL.open('/meta');")
            time.sleep(0.8)
            drv.execute_script("window.VP_SHELL.open(arguments[0]);", route)
            time.sleep(0.9)
            kept = drv.execute_script("""
              var fs = document.querySelectorAll('.vp-shell-frame');
              for (var i = 0; i < fs.length; i++) {
                if (fs[i].classList.contains('on')) {
                  try { return fs[i].contentWindow.__mark || 'LOST'; }
                  catch (e) { return 'XORIGIN'; }
                }
              }
              return 'NO-ON';
            """)
            shella = (alive == 'SWEEP' and url.endswith(OWN_ROUTE)
                      and isinstance(src, str) and src != 'NO-ON-FRAME' and kept == route)
            if not shella:
                fails.append((route, alive, url, src, kept))
            print('  [%s] %-14s frames=%d kept=%s' %
                  ('PASS' if shella else 'FAIL', route, n, kept))

        # 页面内 JS 跳模块（gotoModule）：调用方那一帧不能被重建
        drv.execute_script("window.VP_SHELL.open('/download');")
        time.sleep(2.2)
        before = drv.execute_script("""
          var fs = document.querySelectorAll('.vp-shell-frame');
          for (var i = 0; i < fs.length; i++) {
            if (fs[i].classList.contains('on')) {
              var f = fs[i];
              f.contentWindow.__caller = 'KEEP-ME';
              f.contentWindow.gotoModule('/virome');
              return hi(f);
            }
          }
          function hi(f) { return f.getAttribute('src') || ''; }
          return '';
        """)
        time.sleep(2.5)
        jsnav = drv.execute_script("""
          var fs = document.querySelectorAll('.vp-shell-frame');
          var caller = null, target = null;
          for (var i = 0; i < fs.length; i++) {
            var src = fs[i].getAttribute('src') || '';
            if (src.indexOf('/download') === 0) caller = fs[i];
            if (src.indexOf('/virome') === 0) target = fs[i];
          }
          var keep = null;
          try { keep = caller && caller.contentWindow.__caller; } catch (e) { keep = 'XORIGIN'; }
          return { keep: keep,
                   targetOn: !!(target && target.classList.contains('on')),
                   url: location.pathname };
        """)
        ok_js = (jsnav.get('keep') == 'KEEP-ME' and jsnav.get('targetOn')
                 and jsnav.get('url') == OWN_ROUTE)
        print('  [%s] 页面内 gotoModule 跳模块（调用帧保留=%s，目标帧已激活=%s）' %
              ('PASS' if ok_js else 'FAIL', jsnav.get('keep'), jsnav.get('targetOn')))
        if not ok_js:
            fails.append(('gotoModule', before, jsnav))

        # 外壳自己的页面：应当 showOwn，不能再套一层 iframe
        drv.execute_script("window.VP_SHELL.open('/tasks');")
        time.sleep(1.0)
        own = drv.execute_script("""
          var on = 0;
          document.querySelectorAll('.vp-shell-frame.on').forEach(function(){ on += 1; });
          return { on: on, mod: document.body.classList.contains('shell-mod'),
                   pageShown: (function(){
                     var p = document.querySelector('.page');
                     return p ? getComputedStyle(p).display !== 'none' : false;
                   })() };
        """)
        ok_own = (own.get('on') == 0 and not own.get('mod') and own.get('pageShown'))
        print('  [%s] 回到外壳自己的页面（/tasks 收回外壳，页面可见）' % ('PASS' if ok_own else 'FAIL'))
        if not ok_own:
            fails.append(('own', own))

        try:
            logs = drv.get_log('browser')
        except Exception:
            logs = []
        severe = [l for l in logs if l.get('level') == 'SEVERE']
        print('  控制台 SEVERE: %d' % len(severe))
        for l in severe[:10]:
            print('    ', (l.get('message') or '')[:160])
        if severe:
            fails.append(('console', len(severe), severe[0].get('message')))
    finally:
        try:
            drv.quit()
        except Exception:
            pass
        srv.shutdown()

    print()
    if fails:
        print('失败 %d 处：' % len(fails))
        for f in fails:
            print('   ', f)
        return 1
    print('全站模块保活巡检通过 ✔（%d 个模块 + 页面内跳转 + 自身页面收回）' % len(ROUTES))
    return 0


if __name__ == '__main__':
    sys.exit(main())
