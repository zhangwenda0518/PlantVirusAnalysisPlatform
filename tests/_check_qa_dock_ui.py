# -*- coding: utf-8 -*-
"""问答平台（悬浮机器人）UI 巡检（Playwright）：真实渲染 + 交互 + 通道分离。

运行: python tests/_check_qa_dock_ui.py
自起 Flask 服务（werkzeug 线程，端口 18768），跑完即关。

背景（2026-11-02 用户定案）：右下角那颗悬浮机器人**不是** AI 工作台，
它开的是独立「问答平台」——只答平台用法与概念，不读当前数据、不驱动
任何任务。工作台入口在顶栏 🧠（首页在页内），两者互不串联。

验证点：
  1. 首页问答球可见、顶栏工作台按钮隐藏、页内工作台仍然常开
  2. 点球只开问答面板，绝不弹 AI 工作台；面板里没有工作台那套元素
  3. 发问 → 请求体带 mode='qa' → 回答渲染进气泡（/api/ai/chat 被打桩，
     必须**保留真实入参**，这才是「通道分离」的证据）
  4. 问答历史落在 vp_qa_msgs，不污染工作台的 vp_ai_msgs:*
  5. 悬浮球可拖动 + 松手贴边（opts.snap），双击复位
  6. 全程无 JS 控制台错误；截图存 run/logs/qa_dock_*.png 供目检
"""
import json
import os
import sys
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('VP_NO_RECOVER', '1')
os.environ.setdefault('VP_AI_AUDIT', '0')

try:
    from playwright.sync_api import sync_playwright
except ImportError:                     # pragma: no cover - 取决于本机环境
    sync_playwright = None

FAIL = []


def check(name, cond, detail=''):
    print(('  ✔ ' if cond else '  ✘ ') + name
          + (f' — {detail}' if detail and not cond else ''))
    if not cond:
        FAIL.append(name)


STUB_ANSWER = '问答桩回复：执行单就是「先起草、再批准、才会跑」的凭据。'


def main():
    if sync_playwright is None:
        print('[SKIP] 未安装 playwright，跳过真浏览器验证')
        print('       安装: python -m pip install -r requirements-dev.txt')
        return 0

    from werkzeug.serving import make_server
    import threading
    import app as app_module

    srv = make_server('127.0.0.1', 18768, app_module.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:18768'
    time.sleep(1.0)

    shot_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'run', 'logs')
    os.makedirs(shot_dir, exist_ok=True)

    print('问答平台 UI 巡检')
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=['--no-proxy-server', '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'])
            ctx = browser.new_context(viewport={'width': 1440, 'height': 900})
            page = ctx.new_page()
            # 干净初态：别继承上一轮的面板开合 / 历史 / 拖动位置
            page.add_init_script(
                "try{['vp_qa_open','vp_qa_msgs','vp_ai_open','vp_ui_drag']"
                ".forEach(function(k){localStorage.removeItem(k)});}catch(e){}")
            errors = []
            page.on('console', lambda m: errors.append(m.text)
                    if m.type == 'error' else None)
            page.on('pageerror', lambda e: errors.append(str(e)))

            # ---- 1) 首页：问答球在、顶栏工作台按钮藏起来、页内台常开 ----
            page.goto(base + '/', wait_until='load')
            page.wait_for_timeout(800)
            check('首页问答球可见', page.locator('#aiFab').is_visible())
            check('首页页内工作台仍常开', page.locator('#aiDock').is_visible())
            check('首页顶栏工作台按钮隐藏',
                  not page.locator('#aiDockBtn').is_visible())
            check('首页问答面板默认收起',
                  not page.locator('#qaDock').is_visible())

            # ---- 2) 模块页：点球 = 问答，不是工作台 ----
            page.goto(base + '/samples', wait_until='load')
            page.wait_for_timeout(600)
            check('模块页工作台浮坞默认收起',
                  not page.locator('#aiDock').is_visible())
            check('模块页顶栏工作台按钮可见',
                  page.locator('#aiDockBtn').is_visible())
            page.click('#aiFab')
            page.wait_for_timeout(400)
            check('点球 → 问答面板展开', page.locator('#qaDock').is_visible())
            check('点球不弹 AI 工作台', not page.locator('#aiDock').is_visible())
            check('问答面板里没有工作台结构',
                  page.locator('#qaDock .ai-dock-head, #qaDock #aiChips, '
                               '#qaDock #aiWorkspaceBar, #qaDock #aiAttachChips'
                               ).count() == 0)
            hint = page.locator('.qa-hint').inner_text()
            check('面板常显边界声明',
                  ('不读取' in hint or 'does not read' in hint), hint)
            page.screenshot(path=os.path.join(shot_dir, 'qa_dock_open.png'))

            # ---- 3) 发问：请求体必须带 mode='qa'，回答渲染进气泡 ----
            seen = {}

            def _stub(route):
                try:
                    seen['body'] = json.loads(route.request.post_data or '{}')
                except Exception:
                    seen['body'] = {}
                route.fulfill(
                    status=200, content_type='text/event-stream',
                    body=('data: {"type": "delta", "content": %s}\n\n'
                          'data: {"type": "done"}\n\n')
                         % json.dumps(STUB_ANSWER, ensure_ascii=False))

            page.route('**/api/ai/chat', _stub)
            page.fill('#qaInput', '执行单是什么？')
            page.click('#qaSendBtn')
            page.wait_for_timeout(1200)
            body = seen.get('body') or {}
            check('请求体带 mode=qa', body.get('mode') == 'qa',
                  str(body)[:200])
            check('请求体不带样品/工作空间上下文',
                  not body.get('sample') and not body.get('attachments')
                  and not body.get('workspace'), str(body)[:200])
            check('问答历史只有角色+内容（无工具/执行单字段）',
                  all(sorted(m.keys()) == ['content', 'role']
                      for m in (body.get('messages') or [])),
                  str(body.get('messages'))[:200])
            txt = page.locator('#qaMsgs').inner_text()
            check('模型回答渲染进面板', '执行单就是' in txt, txt[:160])
            check('回答没有产出执行单/结果卡',
                  page.locator('#qaMsgs .ai-action, #qaMsgs .ai-arts').count() == 0)
            check('回答过程中工作台没被打开',
                  not page.locator('#aiDock').is_visible())
            page.screenshot(path=os.path.join(shot_dir, 'qa_dock_answer.png'))

            # ---- 4) 存储隔离 ----
            keys = page.evaluate("Object.keys(localStorage)")
            check('问答历史落在 vp_qa_msgs', 'vp_qa_msgs' in keys, str(keys))
            check('不污染工作台历史键',
                  not any(k.startswith('vp_ai_msgs') for k in keys), str(keys))

            # ---- 5) 拖动：中间能停住、贴边才吸附、位置存档一致 ----
            box = page.locator('#aiFab').bounding_box()
            cx, cy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
            page.mouse.move(cx, cy)
            page.mouse.down()
            page.mouse.move(cx - 700, cy - 260, steps=12)
            page.mouse.up()
            page.wait_for_timeout(500)
            mid = page.locator('#aiFab').bounding_box()
            vw = page.evaluate('window.innerWidth')
            check('拖到屏幕中间就停在中间（不再被拽回角落）',
                  40 < mid['x'] < vw - 120, f"x={mid['x']:.0f} vw={vw}")
            saved_mid = page.evaluate(
                "JSON.parse(localStorage.getItem('vp_ui_drag')||'{}').fab||null")
            check('中间落点按实际位置存档（不是吸附后的假坐标）',
                  bool(saved_mid) and abs(saved_mid.get('x', -999) - mid['x']) <= 3,
                  f"saved={saved_mid} 实际x={mid['x']:.0f}")

            # 再拖到贴近左边缘：这次才吸附到边。先把问答面板收起来——面板开在球旁边，
            # 球拖到屏幕中间后面板会挡在球上方，按下去拖到的是面板不是球。
            page.evaluate("QA.toggle(false)")
            page.wait_for_timeout(200)
            page.mouse.move(mid['x'] + 26, mid['y'] + 26)
            page.mouse.down()
            page.mouse.move(20, mid['y'] + 26, steps=12)
            page.mouse.up()
            page.wait_for_timeout(500)
            pos = page.locator('#aiFab').bounding_box()
            check('拖到左边缘才吸附（x≤24）', pos['x'] <= 24, f"x={pos['x']:.0f}")
            saved = page.evaluate(
                "JSON.parse(localStorage.getItem('vp_ui_drag')||'{}').fab||null")
            check('拖动位置已存档', bool(saved) and saved.get('x') is not None,
                  str(saved))
            page.screenshot(path=os.path.join(shot_dir, 'qa_dock_snapped.png'))
            # 重新打开面板：它得贴着球（球现在贴左边）
            page.evaluate("QA.toggle(true)")
            page.wait_for_timeout(400)
            dock = page.locator('#qaDock').bounding_box()
            check('重开面板贴着球（球贴左边→面板也在左侧）', dock['x'] <= 24,
                  f"x={dock['x']:.0f}")

            # ---- 5b) 问答面板本身也能拖，且记得住 ----
            head = page.locator('#qaDock .qa-title').bounding_box()
            p_before = page.locator('#qaDock').bounding_box()
            page.mouse.move(head['x'] + 20, head['y'] + 10)
            page.mouse.down()
            # 往下往右拖：面板本来就在顶部安全线下，往上拖是徒劳（会被夹回去）
            page.mouse.move(head['x'] + 20 + 220, head['y'] + 10 + 150, steps=12)
            page.mouse.up()
            page.wait_for_timeout(500)
            p_after = page.locator('#qaDock').bounding_box()
            check('按住标题栏能拖走问答面板',
                  p_after['y'] - p_before['y'] > 100
                  and p_after['x'] - p_before['x'] > 100,
                  f"({p_before['x']:.0f},{p_before['y']:.0f}) → "
                  f"({p_after['x']:.0f},{p_after['y']:.0f})")
            saved_qa = page.evaluate(
                "JSON.parse(localStorage.getItem('vp_ui_drag')||'{}').qa||null")
            check('面板位置单独存档（vp_ui_drag.qa）',
                  bool(saved_qa) and abs(saved_qa.get('y', 0) - p_after['y']) <= 3,
                  str(saved_qa))
            page.evaluate("QA.toggle(false)")
            page.wait_for_timeout(200)
            page.evaluate("QA.toggle(true)")
            page.wait_for_timeout(400)
            p_reopen = page.locator('#qaDock').bounding_box()
            check('重开后仍在拖到的地方',
                  abs(p_reopen['y'] - p_after['y']) <= 3,
                  f"y={p_reopen['y']:.0f}")
            page.dblclick('#qaDock .qa-title')
            page.wait_for_timeout(500)
            check('双击标题栏＝回贴着球的位置',
                  not page.evaluate(
                      "!!(JSON.parse(localStorage.getItem('vp_ui_drag')||'{}').qa)"))

            # 双击球回默认位（右下角），别把这个位置留给用户。
            # 先把面板收掉：球贴边时面板会压在球上，直接双击点到的是面板。
            page.evaluate("QA.toggle(false)")
            page.wait_for_timeout(200)
            page.dblclick('#aiFab')
            page.wait_for_timeout(400)
            check('双击复位清掉存档',
                  page.evaluate(
                      "!(JSON.parse(localStorage.getItem('vp_ui_drag')||'{}').fab)"))

            # ---- 6) 工作台仍由顶栏按钮打开，且与问答互斥 ----
            page.click('#aiDockBtn')
            page.wait_for_timeout(600)
            check('顶栏按钮打开工作台', page.locator('#aiDock').is_visible())
            check('开工作台时问答面板收起',
                  not page.locator('#qaDock').is_visible())
            page.locator('#aiDock .ai-dock-head .ai-ico').first.click()
            page.wait_for_timeout(300)
            check('工作台可收起', not page.locator('#aiDock').is_visible())

            real_errors = [e for e in errors
                           if 'favicon' not in e and '404' not in e]
            check(f'无 JS 控制台错误（{len(real_errors)} 条）',
                  not real_errors, '; '.join(real_errors[:3]))
            browser.close()
    finally:
        srv.shutdown()

    print()
    if FAIL:
        print(f'✗ {len(FAIL)} 项失败: {FAIL}')
        return 1
    print('✔ 问答平台 UI 巡检全部通过')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
