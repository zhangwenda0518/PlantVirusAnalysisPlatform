# -*- coding: utf-8 -*-
"""模块切换不刷新（app-shell.js）——浏览器「历史 / 刷新」实测。

守的是外壳模式最容易漏的两处：

  1. **后退/前进**：父页 URL 一直停在外壳自己的页面，如果切模块不写历史记录，
     用户按一下后退就直接离开平台（跳到上一个网站 / 空白页）。正确行为是按后退
     回到上一个看过的模块，再按才回到外壳自己的页面；
  2. **F5 刷新**：刷新不应该把用户扔回工作台、逼他重新点一遍 —— 用
     history.state 记住当前模块，加载时装回来。

顺带断言全程父页 URL 不变（这是外壳的设计前提：刷新/收藏仍落在外壳页）。

自己起一个 8911 临时实例，不碰用户正在用的 8765/8900。
跑法：python tests/_it_shell_history.py（需要 selenium 与本机 Edge）
"""
import os
import sys
import threading
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('VP_NO_RECOVER', '1')

HOST, PORT = '127.0.0.1', 8911
BASE = 'http://%s:%d' % (HOST, PORT)


def static_checks():
    """先跑静态契约：没装 Edge 的机器上也守得住这几行关键实现。"""
    p = os.path.join(ROOT, 'webapp', 'static', 'app-shell.js')
    js = open(p, encoding='utf-8').read()
    fails = 0
    for label, ok in [
        ('切模块压一条同 URL 历史记录（history.pushState 只带 state）',
         "history.pushState({ vp: key || null }, '')" in js),
        ('回外壳自己的页面也记一条（不带 state）',
         'if (!fromPop) pushVp(null);' in js),
        ('同模块重复点不重复记（prev !== key；2026-11-03 起键含 hash）',
         'if (!fromPop && (prev !== key || prevHash !== hash)) '
         'pushVp(key + hash);' in js),
        ('popstate 按 state.vp 切回模块 / 否则回外壳页',
         "window.addEventListener('popstate'" in js and 'open(st.vp, true)' in js
         and 'showOwn(true)' in js),
        ('加载时按 history.state 恢复当前模块',
         'function restoreOnLoad()' in js and 'restoreOnLoad();' in js
         and "document.addEventListener('DOMContentLoaded', restoreOnLoad)" in js),
    ]:
        print(('  ✔ ' if ok else '  ✘ ') + label)
        fails += 0 if ok else 1
    return fails


def main():
    fails = 0
    if '--static' in sys.argv:
        return 1 if static_checks() else 0
    fails += static_checks()

    try:
        from werkzeug.serving import make_server
        import app as A
        from selenium import webdriver
        from selenium.webdriver.edge.options import Options
    except Exception as e:
        print('SKIP: 缺依赖（%s），静态部分已跑，跳过界面自测' % e)
        return 1 if fails else 0

    srv = make_server(HOST, PORT, A.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(1.0)

    opt = Options()
    opt.add_argument('--headless=new')
    opt.add_argument('--window-size=1400,900')
    try:
        drv = webdriver.Edge(options=opt)
    except Exception as e:
        print('SKIP: 没有可用的 Edge/Selenium（%s）' % e)
        srv.shutdown()
        return 1 if fails else 0

    try:
        drv.get(BASE + '/')
        deadline = time.time() + 20
        while time.time() < deadline:
            if drv.execute_script('return !!window.VP_SHELL;'):
                break
            time.sleep(0.3)
        if not drv.execute_script('return !!window.VP_SHELL;'):
            print('  ✘ 首页没有加载 app-shell.js（window.VP_SHELL 缺失）')
            return 1

        def st():
            return drv.execute_script("""
              return {shell: document.body.classList.contains('shell-mod'),
                      active: window.VP_SHELL.active(),
                      url: location.pathname,
                      frames: window.VP_SHELL.count()};
            """)

        drv.execute_script("window.VP_SHELL.open('/refdata');")
        time.sleep(1.6)
        drv.execute_script("window.VP_SHELL.open('/tools');")
        time.sleep(1.6)
        s0 = st()
        ok0 = s0['shell'] and s0['active'] == '/tools' and s0['url'] == '/'
        print(('  ✔ ' if ok0 else '  ✘ ') + '切两个模块后：在外壳里、父页 URL 未变（%s）' % s0)
        fails += 0 if ok0 else 1

        drv.back()
        time.sleep(1.2)
        s1 = st()
        ok1 = s1['shell'] and s1['active'] == '/refdata' and s1['url'] == '/'
        print(('  ✔ ' if ok1 else '  ✘ ') + '后退一次回到上一个模块（%s）' % s1)
        fails += 0 if ok1 else 1

        drv.back()
        time.sleep(1.2)
        s2 = st()
        ok2 = (not s2['shell']) and s2['active'] is None and s2['url'] == '/'
        print(('  ✔ ' if ok2 else '  ✘ ') + '再后退一次回到外壳自己的页面（%s）' % s2)
        fails += 0 if ok2 else 1

        drv.forward()
        time.sleep(1.4)
        s3 = st()
        ok3 = s3['shell'] and s3['active'] == '/refdata'
        print(('  ✔ ' if ok3 else '  ✘ ') + '前进回到模块（%s）' % s3)
        fails += 0 if ok3 else 1

        drv.refresh()
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                if drv.execute_script('return !!window.VP_SHELL;'):
                    break
            except Exception:
                pass
            time.sleep(0.3)
        time.sleep(2.2)
        s4 = st()
        ok4 = s4['shell'] and s4['active'] == '/refdata' and s4['url'] == '/'
        print(('  ✔ ' if ok4 else '  ✘ ') + 'F5 刷新后仍在原模块，不用重新点（%s）' % s4)
        fails += 0 if ok4 else 1

        errs = [e for e in drv.get_log('browser') if e.get('level') == 'SEVERE']
        ok5 = len(errs) == 0
        print(('  ✔ ' if ok5 else '  ✘ ') + '控制台 SEVERE 0 条（实际 %d）' % len(errs))
        for e in errs[:3]:
            print('    ', e.get('message')[:160])
        fails += 0 if ok5 else 1
    finally:
        try:
            drv.quit()
        except Exception:
            pass
        srv.shutdown()

    print('')
    if fails:
        print('模块切换外壳「历史/刷新」实测未通过（%d 项）' % fails)
        return 1
    print('模块切换外壳「历史/刷新」实测通过 ✔')
    return 0


if __name__ == '__main__':
    sys.exit(main())
