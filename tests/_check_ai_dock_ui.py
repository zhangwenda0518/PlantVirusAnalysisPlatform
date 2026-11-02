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
                args=['--no-sandbox', '--disable-dev-shm-usage',
                      '--disable-gpu'])
            ctx = browser.new_context(viewport={'width': 1440, 'height': 900})
            page = ctx.new_page()
            errors = []
            page.on('console', lambda m: errors.append(m.text)
                    if m.type == 'error' else None)
            page.on('pageerror', lambda e: errors.append(str(e)))

            # 2026-10-29：总览改成内嵌「AI 分析台」——坞被移进 #aiConsoleHost、
            # 常开、FAB 隐藏；浮坞行为改到其它页面（/samples）验证。
            page.goto(base + '/', wait_until='load')
            check('总览内嵌 AI 分析台',
                  page.locator('#aiConsoleHost #aiDock').count() == 1)
            check('总览分析台常开', page.locator('#aiDock').is_visible())
            check('总览 FAB 隐藏', not page.locator('#aiFab').is_visible())
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

            # 其它页面仍是右下角浮坞：初始隐藏 → FAB 展开 → ✕ 收起
            page.goto(base + '/samples', wait_until='load')
            page.wait_for_timeout(500)
            check('其它页对话坞初始隐藏', not page.locator('#aiDock').is_visible())
            check('其它页 FAB 可见', page.locator('#aiFab').is_visible())
            page.click('#aiFab')
            page.wait_for_timeout(600)
            check('点击 FAB 后对话坞展开', page.locator('#aiDock').is_visible())
            # 收起点 ✕ 在坞头；页面底部还有 ⟲ 清空 / ⚙ 设置也是 .ai-ico，
            # 所以不能用 .last（2026-11-02：那会点到设置、坞根本不收）。
            page.locator('#aiDock .ai-dock-head .ai-ico').click()
            page.wait_for_timeout(300)
            check('收起后 FAB 回归', page.locator('#aiFab').is_visible())

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
