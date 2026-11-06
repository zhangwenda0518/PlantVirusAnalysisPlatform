# -*- coding: utf-8 -*-
"""模块切换不刷新（app-shell.js）——浏览器实测。

守的是用户 2026-11-02 的那句「你还是切换页面模块，你还是刷新」：

  1. 点左侧导航后，**父文档不跳转**（URL 不变、`window.__noReload` 还在）；
  2. 目标模块装进 iframe 且可见；
  3. 切到另一个模块，第一个 iframe 只是隐藏，**没有卸载**；
  4. 切回来，iframe 里自己写的变量还在（文档从未重新加载 = 真正的保活）；
  5. 点回本页地址（工作台）时外壳收起，父文档内容重新露出。

自己起一个 8907 临时实例，不去碰用户正在用的 8765。
跑法：python tests/_it_page_shell_ui.py（需要 selenium 与本机 Edge）
"""
import os
import sys
import threading
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('VP_NO_RECOVER', '1')

HOST, PORT = '127.0.0.1', 8907
BASE = 'http://%s:%d' % (HOST, PORT)


def _pick_links(drv, n=2):
    """挑「二级入口」链接：组标题（a.sn-link）是开合开关，点了不跳转，不能拿来测。"""
    return drv.execute_script("""
      var seen = {}, out = [];
      document.querySelectorAll('#sideNav .sn-sub a[href^="/"]').forEach(function (a) {
        var h = a.getAttribute('href');
        if (!h || h === '/' || h.indexOf('#') === 0) return;
        if (seen[h]) return;
        seen[h] = 1; out.push(h);
      });
      return out.slice(0, %d);
    """ % n)


def _click_link(drv, href):
    return drv.execute_script("""
      var a = null;
      document.querySelectorAll('#sideNav .sn-sub a[href^="/"]').forEach(function (x) {
        if (!a && x.getAttribute('href') === %r) a = x;
      });
      if (a) { a.click(); return true; }
      return false;
    """ % href)



def main():
    try:
        from werkzeug.serving import make_server
        import app as A
        from selenium import webdriver
        from selenium.webdriver.edge.options import Options
    except Exception as e:
        print('SKIP: 缺依赖（%s），跳过界面自测' % e)
        return 0

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
        return 0

    fails = 0
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

        links = _pick_links(drv, 2)
        if len(links) < 2:
            print('SKIP: 侧栏里找不到两个可用链接（%s）' % links)
            return 0
        a, b = links[0], links[1]

        drv.execute_script("window.__noReload = 'kept';")
        _click_link(drv, a)
        time.sleep(2.0)

        st = drv.execute_script("""
          var fr = document.querySelectorAll('.vp-shell-frame');
          var on = document.querySelector('.vp-shell-frame.on');
          return {noReload: window.__noReload || '', frames: fr.length,
                  on: !!on, shell: document.body.classList.contains('shell-mod'),
                  url: location.pathname};
        """)
        ok = (st['noReload'] == 'kept' and st['frames'] >= 1 and st['on'] and st['shell'])
        print(('  ✔ ' if ok else '  ✘ ') + '点导航后父文档未重载、模块装进 iframe（%s -> %s）' % (a, st))
        fails += 0 if ok else 1

        # 在第一个 iframe 里留个记号：文档没被卸载，变量就一直在
        drv.execute_script("""
          var f = document.querySelector('.vp-shell-frame.on');
          if (f && f.contentWindow) { f.contentWindow.__keepAlive = 'ALIVE-42'; }
        """)

        _click_link(drv, b)
        time.sleep(2.0)
        st2 = drv.execute_script("""
          var fr = document.querySelectorAll('.vp-shell-frame');
          var on = document.querySelector('.vp-shell-frame.on');
          return {frames: fr.length, onSrc: on ? on.getAttribute('src') : '',
                  noReload: window.__noReload || ''};
        """)
        ok2 = st2['frames'] >= 2 and st2['noReload'] == 'kept'
        print(('  ✔ ' if ok2 else '  ✘ ') + '切到另一个模块：第一个 iframe 只隐藏不卸载（%s）' % st2)
        fails += 0 if ok2 else 1

        _click_link(drv, a)
        time.sleep(1.5)
        mark = drv.execute_script("""
          var f = document.querySelector('.vp-shell-frame.on');
          try { return f && f.contentWindow ? (f.contentWindow.__keepAlive || '') : ''; }
          catch (e) { return 'ERR:' + e.message; }
        """)
        ok3 = (mark == 'ALIVE-42')
        print(('  ✔ ' if ok3 else '  ✘ ') + '切回来：iframe 里自己写的变量还在（%s）' % (mark or '空'))
        fails += 0 if ok3 else 1

        # 2026-11-05：「设置页面需要刷新才生效」——设置页装在外壳 iframe 里，
        # 切主题必须同步到外壳与其它保活模块（原先只改设置页自己的文档）。
        drv.execute_script("window.VP_SHELL.open('/settings')")
        deadline = time.time() + 20
        ready = False
        while time.time() < deadline:
            ready = drv.execute_script("""
              var f = document.querySelector('.vp-shell-frame.on');
              if (!f || !f.contentWindow) return false;
              var d = f.contentWindow.document;
              return !!(d.querySelector('#themeSeg button[data-theme="dark"]'));
            """)
            if ready:
                break
            time.sleep(0.3)
        if ready:
            drv.execute_script("""
              var f = document.querySelector('.vp-shell-frame.on');
              f.contentWindow.document
                .querySelector('#themeSeg button[data-theme="dark"]').click();
            """)
            time.sleep(0.8)
            dark = drv.execute_script("""
              var out = {parent: document.documentElement
                           .getAttribute('data-theme') || 'light', frames: []};
              document.querySelectorAll('.vp-shell-frame').forEach(function (f) {
                try {
                  out.frames.push(f.contentWindow.document.documentElement
                    .getAttribute('data-theme') || 'light');
                } catch (e) { out.frames.push('ERR'); }
              });
              return out;
            """)
            ok_theme = (dark.get('parent') == 'dark' and dark.get('frames')
                        and all(x == 'dark' for x in dark['frames']))
            print(('  ✔ ' if ok_theme else '  ✘ ')
                  + '设置页切深色：外壳与全部保活 iframe 同步（%s）' % dark)
            fails += 0 if ok_theme else 1
            # 复原：切回浅色并清掉本地偏好，避免污染后续用例与用户环境
            drv.execute_script("""
              var f = document.querySelector('.vp-shell-frame.on');
              f.contentWindow.document
                .querySelector('#themeSeg button[data-theme="light"]').click();
            """)
            time.sleep(0.5)
            drv.execute_script("document.documentElement.removeAttribute('data-theme');"
                               "localStorage.removeItem('vp_theme');")
        else:
            print('  ✘ 设置页 iframe 未就绪（#themeSeg 缺失）')
            fails += 1

        # 顶栏环境条口径验证（2026-11-05 建立；2026-11-06 随组口径语义变更
        # 更新，见下方断言处的说明）。工具页切卡走 history.replaceState
        # 不触发 hashchange —— 断言靠页面主动刷新的状态文本采样。
        drv.execute_script("window.VP_SHELL.open('/tools?g=sample')")
        deadline = time.time() + 30
        strip0 = ''
        while time.time() < deadline:
            strip0 = drv.execute_script("""
              var f = document.querySelector('.vp-shell-frame.on');
              if (!f || !f.contentWindow) return '';
              var d = f.contentWindow.document;
              var s = d.getElementById('envStrip');
              var b = d.getElementById('envStripBody');
              return (s && !s.hidden && b)
                ? b.textContent.replace(/\\s+/g, ' ').trim() : '';
            """)
            if strip0:
                break
            time.sleep(0.5)
        # 2026-11-06 语义变更（app.js envScopeParams + selfcheck scoped_status）：
        #   ① 环境条新增「Python 依赖」位、组内模块构成变化 → 计数断言不再
        #      写死（6/6 是旧组口径），改为与 /api/env_status?g=sample 的段
        #      逐段自校验（排除会漂移的 disk 段）；
        #   ② 组页（含切卡）一律保持组口径 —— 用户反馈「组内切卡不该把
        #      环境条切成单卡口径（1/1 · 全局依赖数）」，只有无组直链
        #      /tools#t-xxx 才是卡片口径。
        ref = drv.execute_script(
            "return fetch('/api/env_status?page=/tools&g=sample').then(r=>r.json())"
            ".then(d=>(d.bits||[]).filter(b=>b.id!=='disk')"
            ".map(b=>b.id+':'+(b.summary||'')).join('|'))")
        ref_segs = [s.split(':', 1)[1] for s in (ref or '').split('|') if s]
        has_group = bool(ref_segs) and all(s in strip0 for s in ref_segs)
        print(('  ✔ ' if has_group else '  ✘ ')
              + '样本处理组概览：环境条为组口径（与 /api/env_status?g=sample'
                ' 逐段一致）（%s）' % (strip0 or '未就绪'))
        fails += 0 if has_group else 1
        clicked = False
        strip1 = ''
        for _try in range(3):
            clicked = drv.execute_script("""
              var f = document.querySelector('.vp-shell-frame.on');
              if (!f || !f.contentWindow) return false;
              var a = f.contentWindow.document
                .querySelector('#groupLanding a[data-item="t-convert"]');
              if (!a) return false;
              a.click();
              return true;
            """)
            deadline = time.time() + 8
            while time.time() < deadline:
                strip1 = drv.execute_script("""
                  var f = document.querySelector('.vp-shell-frame.on');
                  if (!f || !f.contentWindow) return '';
                  var d = f.contentWindow.document;
                  var s = d.getElementById('envStrip');
                  var b = d.getElementById('envStripBody');
                  return (s && !s.hidden && b)
                    ? b.textContent.replace(/\\s+/g, ' ').trim() : '';
                """)
                if strip1 and '1/1' not in strip1:
                    break
                time.sleep(0.5)
            if strip1 and '1/1' not in strip1:
                break
            # 卡片视图下 #groupLanding 隐藏，点不到卡片：先回组概览再重试
            drv.execute_script("""
              var f = document.querySelector('.vp-shell-frame.on');
              if (f && f.contentWindow) f.contentWindow.location.hash = '';
            """)
            time.sleep(1.2)
        # 切卡后环境条应**保持组口径**（2026-11-06 语义变更；旧行为是切成 1/1）
        ok_scope = bool(clicked and strip1
                        and all(s in strip1 for s in ref_segs)
                        and '1/1' not in strip1)
        print(('  ✔ ' if ok_scope else '  ✘ ')
              + '切到「格式转换」卡：环境条保持组口径（2026-11-06 语义，'
                'clicked=%s, %s）' % (clicked, strip1 or '未就绪'))
        fails += 0 if ok_scope else 1
        drv.execute_script("window.VP_SHELL.open('/')")
        time.sleep(0.5)

        # 2026-11-02 回归：「悬浮机器人没了」——坞开着（vp_ai_open=1）切模块后，
        # iframe 里必须还能看见 AI（坞或悬浮球）。
        # 同日晚些时候定案：悬浮球改归「问答平台」，开工作台坞改为顶栏 🧠 按钮；
        # 两者互不串联，所以这里分别验：球在、坞能开、点球开的是问答面板。
        drv.execute_script("localStorage.setItem('vp_ai_open','1');")
        drv.refresh()
        deadline = time.time() + 20
        while time.time() < deadline:
            if drv.execute_script('return !!window.VP_SHELL;'):
                break
            time.sleep(0.3)
        _click_link(drv, a)
        time.sleep(2.0)
        ai = drv.execute_script("""
          var f = document.querySelector('.vp-shell-frame.on');
          if (!f || !f.contentWindow) return {err: '没有正在显示的 iframe'};
          var w = f.contentWindow, d = w.document;
          var vis = function (el) {
            if (!el || el.hidden) return false;
            var s = w.getComputedStyle(el);
            return s.display !== 'none' && s.visibility !== 'hidden';
          };
          return {dock: vis(d.getElementById('aiDock')),
                  fab: vis(d.getElementById('aiFab'))};
        """)
        ok5 = bool(ai.get('dock') or ai.get('fab'))
        print(('  ✔ ' if ok5 else '  ✘ ')
              + '坞开着进模块：iframe 里 AI 入口仍在（%s）' % ai)
        fails += 0 if ok5 else 1

        drv.execute_script("""
          var f = document.querySelector('.vp-shell-frame.on');
          if (!f || !f.contentWindow) return;
          var d = f.contentWindow.document, dock = d.getElementById('aiDock');
          var head = dock && dock.querySelector('.ai-dock-head .ai-ico');
          if (head && !dock.hidden) head.click();      /* 先收起，回到干净状态 */
          var fab = d.getElementById('aiFab');
          if (fab && !fab.hidden) fab.click();         /* 悬浮球 = 问答平台 */
        """)
        time.sleep(0.6)   # 单击有 220ms 防误触延迟（好让双击复位生效），等它落地
        fab_state = drv.execute_script("""
          var f = document.querySelector('.vp-shell-frame.on');
          if (!f || !f.contentWindow) return 'no-frame';
          var d = f.contentWindow.document;
          var dock = d.getElementById('aiDock');
          var qa = d.getElementById('qaDock');
          var qaOpen = !!(qa && !qa.hidden);
          var dockOpen = !!(dock && !dock.hidden);
          return qaOpen && !dockOpen ? 'qa' : ('qa=' + qaOpen + ',dock=' + dockOpen);
        """)
        ok6 = (fab_state == 'qa')
        print(('  ✔ ' if ok6 else '  ✘ ')
              + 'iframe 里点球开的是问答面板、不弹工作台（%s）' % fab_state)
        fails += 0 if ok6 else 1

        btn_state = drv.execute_script("""
          var f = document.querySelector('.vp-shell-frame.on');
          if (!f || !f.contentWindow) return 'no-frame';
          var w = f.contentWindow, d = w.document, dock = d.getElementById('aiDock');
          var qa = d.getElementById('qaDock');
          var btn = d.getElementById('aiDockBtn');
          if (!btn || btn.hidden) return 'no-btn';
          btn.click();                                  /* 顶栏 🧠 = 工作台 */
          var dockOpen = !!(dock && !dock.hidden);
          var qaOpen = !!(qa && !qa.hidden);
          return dockOpen && !qaOpen ? 'dock' : ('dock=' + dockOpen + ',qa=' + qaOpen);
        """)
        ok7 = (btn_state == 'dock')
        print(('  ✔ ' if ok7 else '  ✘ ')
              + 'iframe 里点顶栏 🧠 开工作台、且收起问答面板（%s）' % btn_state)
        fails += 0 if ok7 else 1
        drv.execute_script("localStorage.removeItem('vp_ai_open');"
                           "localStorage.removeItem('vp_qa_open');"
                           "localStorage.removeItem('vp_qa_msgs');")

        errs = [e for e in drv.get_log('browser') if e.get('level') == 'SEVERE']
        ok4 = len(errs) == 0
        print(('  ✔ ' if ok4 else '  ✘ ') + '控制台 SEVERE 0 条（实际 %d）' % len(errs))
        for e in errs[:3]:
            print('    ', e.get('message')[:160])
        fails += 0 if ok4 else 1
    finally:
        try:
            drv.quit()
        except Exception:
            pass
        srv.shutdown()

    print('')
    if fails:
        print('模块切换保活外壳实测未通过（%d 项）' % fails)
        return 1
    print('模块切换保活外壳实测通过 ✔')
    return 0


if __name__ == '__main__':
    sys.exit(main())
