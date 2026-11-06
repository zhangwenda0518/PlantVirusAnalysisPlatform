# -*- coding: utf-8 -*-
"""总览页 AI 对话坞 UI 巡检（Playwright）：真实渲染 + 交互 + 控制台错误收集。

运行: python tests/_check_ai_dock_ui.py
自起 Flask 服务（werkzeug 线程，端口 18767），跑完即关。

验证点：
  1. FAB 浮钮存在，点击后对话坞展开
  2. 样品下拉框被 /api/ai/context 的样品清单填充
  3. 未配置 AI 时显示「去配置」引导条
  4. 发送消息 → 未配置降级路径给出可读错误（不发真实网络请求）
  5. 全程无 JS 控制台错误；截图存 run/logs/ai_dock_*.png 供目检
"""
import os
import sys
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _fixture_smoke_e2e import ensure as _ensure_smoke_e2e  # noqa: E402
os.environ.setdefault('VP_NO_RECOVER', '1')
# 本守卫会发布大量事件 / 建执行单；关掉审计落盘，避免往 run/ai_audit/
# 写测试垃圾（审计本身的正确性由 tests/_check_ai_audit.py 在临时目录专测）。
os.environ.setdefault('VP_AI_AUDIT', '0')

# 与其他浏览器探针（_check_kvchain_ui / _it_download_ui 等）约定一致：
# 未装 playwright（开发依赖缺失）时 SKIP 并返回 0，不让加强项把整批测试变红。
try:
    from playwright.sync_api import sync_playwright
except ImportError:                     # pragma: no cover - 取决于本机环境
    sync_playwright = None

FAIL = []


def check(name, cond, detail=''):
    print(('  ✔ ' if cond else '  ✘ ') + name + (f' — {detail}' if detail and not cond else ''))
    if not cond:
        FAIL.append(name)


def main():
    if sync_playwright is None:
        print('[SKIP] 未安装 playwright，跳过真浏览器验证')
        print('       安装: python -m pip install -r requirements-dev.txt')
        return 0
    _ensure_smoke_e2e()
    from werkzeug.serving import make_server
    import threading
    import app as app_module
    srv = make_server('127.0.0.1', 18767, app_module.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:18767'
    time.sleep(1.0)

    from Virus_Platform_Core.web.state import cfg
    orig_ai = dict(cfg.ai)
    cfg.ai = {'enabled': False, 'provider': 'custom', 'base_url': '',
              'api_key': '', 'model': '', 'temperature': 0.3,
              'max_tokens': 2048}          # 强制走未配置降级路径

    shot_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'run', 'logs')
    os.makedirs(shot_dir, exist_ok=True)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=['--no-proxy-server', '--no-sandbox', '--disable-dev-shm-usage',
                      '--disable-gpu'])
            ctx = browser.new_context(viewport={'width': 1440, 'height': 900})
            page = ctx.new_page()
            errors = []
            page.on('console', lambda m: errors.append(m.text)
                    if m.type == 'error' else None)
            page.on('pageerror', lambda e: errors.append(str(e)))

            # 2026-10-29：总览改成内嵌「AI 分析台」——坞被移进 #aiConsoleHost、常开。
            # 2026-11-02：悬浮球改成独立「问答平台」（app-qa.js），首页也常显；
            # 工作台在总览页就在页内，顶栏那颗 🧠 按钮在这儿反而多余，隐藏。
            page.goto(base + '/', wait_until='load')
            check('总览内嵌 AI 分析台',
                  page.locator('#aiConsoleHost #aiDock').count() == 1)
            check('总览分析台常开', page.locator('#aiDock').is_visible())
            check('总览问答球可见', page.locator('#aiFab').is_visible())
            check('总览顶栏工作台按钮隐藏',
                  not page.locator('#aiDockBtn').is_visible())
            # 2026-10-30：坞里不再有独立样品下拉（跟随顶栏全局胶囊），标题改名 AI 工作台
            check('坞内不再有独立样品下拉',
                  page.locator('#aiSampleSel').count() == 0)
            check('标题已改为 AI 工作台',
                  '工作台' in page.locator('.ai-dock-title').inner_text())
            check('未配置引导条出现',
                  page.locator('.ai-notice').count() >= 1)
            page.screenshot(path=os.path.join(shot_dir, 'ai_console_home.png'))

            page.fill('#aiInput', '这个平台能做什么？')
            page.click('#aiSendBtn')
            page.wait_for_selector('.ai-err', timeout=15000)
            err_txt = page.locator('.ai-err').first.inner_text()
            check('未配置发送 → 降级错误引导',
                  '配置' in err_txt or '设置' in err_txt, err_txt)
            page.screenshot(
                path=os.path.join(shot_dir, 'ai_dock_degraded.png'))

            # 其它页面：工作台浮坞由顶栏 🧠 打开；悬浮球归问答，两者互不串联。
            page.goto(base + '/samples', wait_until='load')
            page.wait_for_timeout(500)
            check('其它页对话坞初始隐藏', not page.locator('#aiDock').is_visible())
            check('其它页问答球可见', page.locator('#aiFab').is_visible())
            check('其它页顶栏工作台按钮可见',
                  page.locator('#aiDockBtn').is_visible())

            # ① 悬浮球 = 独立问答平台（2026-11-02 用户定案）：点开的是 #qaDock，
            #    绝不能弹 AI 工作台；面板里也不能有执行单/样品那套东西。
            page.click('#aiFab')
            page.wait_for_timeout(400)
            check('点问答球 → 问答面板展开', page.locator('#qaDock').is_visible())
            check('点问答球不弹 AI 工作台',
                  not page.locator('#aiDock').is_visible())
            hint = page.locator('.qa-hint').inner_text()
            check('问答面板写明边界（不读数据/不跑任务）',
                  ('不读取' in hint or 'does not read' in hint), hint)
            check('问答面板里没有工作台元素',
                  page.locator('#qaDock .ai-dock-head, #qaDock #aiChips, '
                               '#qaDock #aiWorkspaceBar').count() == 0)
            page.fill('#qaInput', '执行单是什么？批准之后会发生什么？')
            page.click('#qaSendBtn')
            page.wait_for_timeout(2500)
            check('问答面板给出回复气泡（未配置时也应说明原因）',
                  page.locator('#qaMsgs .ai-msg').count() >= 2)
            shot_qa = os.path.join(shot_dir, 'qa_dock.png')
            page.screenshot(path=shot_qa)
            page.locator('#qaDock .qa-head-ops .qa-ico').nth(1).click()
            page.wait_for_timeout(200)
            check('问答面板可收起', not page.locator('#qaDock').is_visible())

            # ② 顶栏 🧠 = AI 工作台：开坞、✕ 收起；球一直在（不再被坞吃掉）
            page.click('#aiDockBtn')
            page.wait_for_timeout(600)
            check('点顶栏工作台按钮 → 对话坞展开',
                  page.locator('#aiDock').is_visible())
            check('工作台开着时问答球仍在', page.locator('#aiFab').is_visible())
            # 收起点 ✕ 在坞头；页面底部还有 ⟲ 清空 / ⚙ 设置也是 .ai-ico，
            # 所以不能用 .last（2026-11-02：那会点到设置、坞根本不收）。
            page.locator('#aiDock .ai-dock-head .ai-ico').click()
            page.wait_for_timeout(300)
            check('收起后问答球仍可见', page.locator('#aiFab').is_visible())
            check('收起后对话坞隐藏', not page.locator('#aiDock').is_visible())

            # 2026-11-02 回归：保活外壳里 AI 入口不能消失。
            # 用户原话：「我们的之前的那个AI的悬浮机器人去哪了，怎么没了」——
            # 坞开着（vp_ai_open=1）从首页点模块进外壳 iframe 时，父层坞随 main
            # 藏起、父层 FAB 被状态隐掉、iframe 里的坞又被 vp-frame CSS 藏掉，
            # 三处各自合理、叠加起来整页没有任何 AI 入口。
            page.goto(base + '/', wait_until='load')
            # 问答面板先收干净：否则 iframe 里 QA.restore() 会（按互斥规则）把
            # 工作台关掉，和这一段的断言抢时序。
            page.evaluate("localStorage.setItem('vp_ai_open','1');"
                          "localStorage.removeItem('vp_qa_open');"
                          "localStorage.removeItem('vp_qa_msgs')")
            page.reload(wait_until='load')
            href = page.evaluate("""
              (function () {
                var out = '';
                document.querySelectorAll('#sideNav .sn-sub a[href^="/"]').forEach(
                  function (a) {
                    var h = a.getAttribute('href');
                    if (!out && h && h !== '/' && h.indexOf('#') !== 0) out = h;
                  });
                return out;
              })()
            """)
            page.evaluate("""
              (h) => {
                var a = null;
                document.querySelectorAll('#sideNav .sn-sub a[href^="/"]').forEach(
                  function (x) {
                    if (!a && x.getAttribute('href') === h) a = x;
                  });
                if (a) a.click();
              }
            """, href)
            page.wait_for_selector('.vp-shell-frame.on', timeout=20000)
            page.wait_for_timeout(1500)
            shell = page.locator('.vp-shell-frame.on').element_handle()
            sframe = shell.content_frame() if shell else None
            check('外壳模式：模块装进 iframe', sframe is not None,
                  f'href={href}')
            if sframe is not None:
                ai_visible = (sframe.locator('#aiDock').is_visible()
                              or sframe.locator('#aiFab').is_visible())
                check('坞开着进模块，iframe 里仍有 AI 入口', ai_visible)
                check('外壳模式父层不重复浮 AI 入口',
                      not page.locator('#aiFab').is_visible())
                check('外壳模式父层不重复浮运行条（会压住 iframe 里的坞）',
                      not page.locator('#vpRunBar').is_visible())
                check('外壳模式父层也不浮问答面板',
                      not page.locator('#qaDock').is_visible())
                page.screenshot(path=os.path.join(
                    shot_dir, 'ai_shell_module_open1.png'))
                # 2026-11-02：iframe 里两个入口各司其职 ——
                #   🧠 顶栏按钮 = 工作台（弹 #aiDock）；悬浮球 = 问答（弹 #qaDock）。
                if not sframe.locator('#aiDock').is_visible():
                    sframe.locator('#aiDockBtn').click()
                    page.wait_for_timeout(600)
                if not sframe.locator('#aiDock').is_visible():
                    print('    [dbg]', sframe.evaluate("""() => {
                      const d = document.getElementById('aiDock');
                      const b = document.getElementById('aiDockBtn');
                      return {url: location.pathname, aiOpen: (window.AI && AI.open),
                              ready: (window.AI && AI.ready), hidden: d.hidden,
                              display: getComputedStyle(d).display,
                              lsOpen: localStorage.getItem('vp_ai_open'),
                              qaOpen: (window.QA && QA.open),
                              lsQa: localStorage.getItem('vp_qa_open'),
                              btn: b ? [b.offsetWidth, b.offsetHeight, b.hidden] : null,
                              cls: d.className};
                    }"""))
                check('iframe 内对话坞能展开', sframe.locator('#aiDock').is_visible())
                sframe.locator('#aiDock .ai-dock-head .ai-ico').click()
                page.wait_for_timeout(400)
                check('iframe 内收起后对话坞隐藏',
                      not sframe.locator('#aiDock').is_visible())
                check('iframe 内问答球始终在', sframe.locator('#aiFab').is_visible())
                # 点球是问答，不能弹工作台（两条通道彻底分开）
                sframe.locator('#aiFab').click()
                page.wait_for_timeout(600)
                check('iframe 内点球开的是问答面板（不是工作台）',
                      sframe.locator('#qaDock').is_visible()
                      and not sframe.locator('#aiDock').is_visible())
                page.screenshot(path=os.path.join(
                    shot_dir, 'ai_shell_module_fab.png'))
            page.goto(base + '/', wait_until='load')
            page.evaluate("localStorage.removeItem('vp_ai_open');"
                          "localStorage.removeItem('vp_qa_open');"
                          "localStorage.removeItem('vp_qa_msgs')")

            real_errors = [e for e in errors
                           if 'favicon' not in e and '404' not in e]
            check(f'无 JS 控制台错误（{len(real_errors)} 条）',
                  not real_errors, '; '.join(real_errors[:3]))
            browser.close()
    finally:
        cfg.ai = orig_ai
        cfg.save()
        srv.shutdown()

    print()
    if FAIL:
        print(f'✗ {len(FAIL)} 项失败: {FAIL}')
        return 1
    print('✔ 对话坞 UI 巡检全部通过')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
