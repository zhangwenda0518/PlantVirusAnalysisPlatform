# -*- coding: utf-8 -*-
"""执行单任务进度块 UI 巡检（Playwright）：批准后不再只剩「已启动」徽章。

运行: python tests/_check_ai_task_status_ui.py
自起 Flask（端口 18770），/api/task/* 用路由拦截返回可控进度，跑完即关。

验证：
  1. ai_action launched 带 tid → 卡片出现 .ai-task（任务深链 + 状态 + 进度条）
  2. 进度 42% → 条宽 42%、徽章从「已启动」变为「运行中」
  3. 任务转 done → 条宽 100%、状态「✓ 已完成」，轮询停止
  4. /api/ai/actions 对账（recent 带 tid）也能补出进度块
"""
import json
import os
import sys
import threading
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
except ImportError:                     # pragma: no cover
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
    from werkzeug.serving import make_server
    import app as app_module
    srv = make_server('127.0.0.1', 18770, app_module.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:18770'
    time.sleep(1.0)

    state = {'pct': 0.42, 'status': 'running', 'stage': 'kvsuite', 'msg': '识别已知病毒…'}
    tid = 'uitask123456'
    calls = {'n': 0}

    def task_api(route):
        calls['n'] += 1
        route.fulfill(status=200, content_type='application/json', body=json.dumps({
            'id': tid, 'status': state['status'], 'stage': state['stage'],
            'pct': state['pct'], 'msg': state['msg'], 'error': '',
            'log': ['[01:30:00] [INFO] 正在比对病毒库'],
        }, ensure_ascii=False))

    def actions_api(route):
        route.fulfill(status=200, content_type='application/json', body=json.dumps({
            'pending': [], 'recent': [{
                'id': 'act_hyd', 'status': 'running', 'tid': tid,
                'title': '病毒定量与共识', 'action': 'tool',
                'params': {'tool': 'kvchain'}, 'reason': '对账',
            }],
        }, ensure_ascii=False))

    try:
        with sync_playwright() as p:
            b = p.chromium.launch(args=['--no-proxy-server'])
            pg = b.new_page(viewport={'width': 1100, 'height': 900})
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.route('**/api/task/*', task_api)
            pg.goto(base + '/settings', wait_until='load')
            pg.wait_for_timeout(1200)

            pg.evaluate("""(tid) => {
              AI.toggle(true);
              document.getElementById('aiMsgs').innerHTML = '';
              AI.renderEvent({type:'ai_action', aid:'act_task', status:'pending',
                title:'病毒定量与共识', action:'tool', params:{tool:'kvchain'}});
              AI.renderEvent({type:'ai_action', aid:'act_task', status:'launched',
                title:'病毒定量与共识', action:'tool', tid: tid});
            }""", tid)
            pg.wait_for_timeout(900)
            info = pg.evaluate("""() => {
              const c = document.querySelector('.ai-action[data-aid="act_task"]');
              const t = c && c.querySelector('.ai-task');
              return {hasTask: !!t,
                      link: t && t.querySelector('.ai-task-link').getAttribute('href'),
                      st: t && t.querySelector('.ai-task-st').textContent,
                      width: t && t.querySelector('.ai-task-bar i').style.width,
                      msg: t && t.querySelector('.ai-task-msg').textContent,
                      badge: c && c.querySelector('.ai-gate-st').textContent};
            }""")
            check('launched 卡片出现任务进度块', info['hasTask'])
            check('任务深链指向任务中心', info['link'] == '/tasks?focus=' + tid, str(info['link']))
            check('运行中显示 42%', '42%' in (info['st'] or ''), str(info['st']))
            check('进度条宽度 42%', info['width'] == '42%', str(info['width']))
            check('显示阶段与日志行', 'kvsuite' in (info['msg'] or ''), str(info['msg']))
            check('徽章不再停在「已启动」', info['badge'] == '运行中', str(info['badge']))
            pg.locator('.ai-action[data-aid="act_task"]').screenshot(
                path=os.path.join('run', 'logs', 'ai_task_running.png'))

            state.update(pct=1.0, status='done', stage='report', msg='任务完成')
            pg.wait_for_timeout(3000)
            info2 = pg.evaluate("""() => {
              const t = document.querySelector('.ai-action[data-aid="act_task"] .ai-task');
              return {st: t.querySelector('.ai-task-st').textContent,
                      width: t.querySelector('.ai-task-bar i').style.width,
                      badge: document.querySelector(
                        '.ai-action[data-aid="act_task"] .ai-gate-st').textContent};
            }""")
            check('终态显示已完成', '完成' in (info2['st'] or ''), str(info2['st']))
            check('终态进度条 100%', info2['width'] == '100%', str(info2['width']))
            check('终态徽章同步完成', '完成' in (info2['badge'] or ''), str(info2['badge']))
            before = calls['n']
            pg.wait_for_timeout(3000)
            check('终态后停止轮询', calls['n'] == before, f"{before} → {calls['n']}")

            # 刷新对账：recent 里带 tid 的卡片也要补出进度块
            pg.unroute('**/api/task/*')
            pg.route('**/api/ai/actions', actions_api)
            pg.evaluate("""() => {
              document.getElementById('aiMsgs').innerHTML = '';
              AI.renderEvent({type:'ai_action', aid:'act_hyd', status:'pending',
                title:'病毒定量与共识', action:'tool', params:{tool:'kvchain'}});
            }""")
            pg.evaluate('AI.hydratePending()')
            pg.wait_for_timeout(700)
            info3 = pg.evaluate("""() => {
              const c = document.querySelector('.ai-action[data-aid="act_hyd"]');
              const t = c && c.querySelector('.ai-task');
              return {hasTask: !!t, link: t && t.querySelector('.ai-task-link').getAttribute('href')};
            }""")
            check('刷新对账补出任务进度块', info3['hasTask'])
            check('对账任务深链正确', tid in (info3['link'] or ''), str(info3['link']))
            check('全程无 JS 报错', not errs, '; '.join(errs[:2]))
            b.close()
    finally:
        srv.shutdown()

    print()
    if FAIL:
        print('✘ 失败 %d 项: %s' % (len(FAIL), ', '.join(FAIL)))
        return 1
    print('✔ 执行单任务进度 UI 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
