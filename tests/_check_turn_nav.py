# -*- coding: utf-8 -*-
"""AI 工作台右侧「历史提问导航」回归（2026-11-02）。

背景：坞里消息一多就翻不回上次的提问。导航条按"用户提问"逐条打点，
点击跳到那一轮，滚动时高亮当前轮，悬停给 120 字预览。

本测试自起 Flask（随机端口）并用 Playwright 真开页面，断言：
  1) 只有 .ai-msg.ai-user 直接子节点打点（回放折叠块 / 播报卡 / 系统提示不算）；
  2) 贴底高亮最后一轮，滚到顶高亮第一轮；
  3) 单击第 N 个点 → 该轮提问落到视口顶部、气泡闪光、高亮切换；
  4) 悬停出预览（含轮次与提问原文），移开收起，点完不残留；
  5) 只剩一轮 / 清空对话时整条隐藏；
  6) 全程无 pageerror。

无 playwright 时 SKIP（退出码 0）。用法: python tests/_check_turn_nav.py
"""
import os
import pathlib
import sys
import threading
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print('SKIP: 未安装 playwright（开发依赖）')
    sys.exit(0)

from werkzeug.serving import make_server  # noqa: E402
import app as app_module  # noqa: E402

MD = """### 结论

这批数据最可能是 **黄瓜花叶病毒 (CMV)**，建议先做快速再鉴定。

| 方案 | 耗时 | 产出 |
|:---|:---:|---|
| A 全流程 | 30 min | 组装 + 注释 |

```bash
run_tool: kvsuite
```
"""

SEED_JS = """([md, count]) => {
  AI.toggle(true);
  /* 掐掉首屏的异步渲染：历史恢复 / 待决执行单 / SSE 回放会在种子之后继续
     往消息区塞卡片，断言就不可重复了。 */
  AI.ctxLoaded = true;
  AI.action = function () {};
  try { AI._es && AI._es.close && AI._es.close(); } catch (e) {}
  AI.messages = [];
  const box = document.getElementById('aiMsgs');
  box.innerHTML = '';
  const N = count || 5;
  const qs = ['从 GQIMX.R1/R2 创建样品，跑全流程',
   '直接对 spades.virus.fasta 做病毒再鉴定',
   'kvsuite 为什么报错？上次那批 contig 一个都没识别出来',
   '帮我把这批样品的结果汇总成一张表，要看覆盖度和 reads 数',
   '下一步建议'
  ];
  for (let i = 0; i < N; i++) {
    const q = N > 5 ? ('第 ' + (i + 1) + ' 轮：' + qs[i % qs.length]) : qs[i];
    const u = document.createElement('div');
    u.className = 'ai-msg ai-user';
    u.textContent = q;
    box.appendChild(u);
    const b = document.createElement('div');
    b.className = 'ai-msg ai-bot';
    const ans = document.createElement('div');
    ans.className = 'ai-answer';
    ans.innerHTML = AI.fmt(md);
    b.appendChild(ans);
    box.appendChild(b);
  }
  const fold = document.createElement('details');
  fold.className = 'ai-backlog';
  fold.innerHTML = '<summary>更早的 8 条记录</summary><div>x</div>';
  box.insertBefore(fold, box.firstChild);
  const ev = document.createElement('div');
  ev.className = 'ai-event';
  ev.textContent = '任务 6f2c 已完成';
  box.appendChild(ev);
  box.scrollTop = box.scrollHeight;
}"""

TICKS_ON = ("[...document.querySelectorAll('.ai-turn-tick')]"
            ".map(b => b.classList.contains('on'))")
TICKS_N = "document.querySelectorAll('#aiTurnNav .ai-turn-tick').length"
NAV_HIDDEN = "document.getElementById('aiTurnNav').hidden"

fails = []
pageerrors = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        fails.append(msg)


def main():
    srv = make_server('127.0.0.1', 0, app_module.app, threaded=True)
    base = 'http://127.0.0.1:%d' % srv.server_port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(1.0)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={'width': 1280, 'height': 900})
        ctx.add_init_script(
            "try { Object.keys(localStorage)"
            ".filter(k => k.indexOf('vp_ai_msgs:') === 0)"
            ".forEach(k => localStorage.removeItem(k)); } catch (e) {}")
        ctx.route('**/api/ai/actions*', lambda r: r.fulfill(
            status=200, content_type='application/json', body='{"actions": []}'))
        pg = ctx.new_page()
        pg.on('pageerror', lambda e: pageerrors.append(str(e)))
        pg.goto(base + '/settings', wait_until='domcontentloaded')
        pg.wait_for_timeout(2200)
        pg.evaluate(SEED_JS, [MD])
        pg.wait_for_timeout(300)

        print('[1] 打点口径')
        check(pg.evaluate(TICKS_N) == 5,
              '5 轮提问 → 5 个点（回放块/播报卡不算）')
        check(not pg.evaluate(NAV_HIDDEN), '导航条可见')
        check(pg.evaluate(TICKS_ON) == [False, False, False, False, True],
              '贴底只高亮最后一轮')

        print('[2] 悬停预览')
        pg.locator('#aiTurnNav .ai-turn-tick').nth(2).hover()
        pg.wait_for_timeout(150)
        tip = pg.evaluate("(() => { const t = document.getElementById('aiTurnTip');"
                          "return {hidden: t.hidden, text: t.textContent}; })()")
        check(not tip['hidden'], '预览出现')
        check('第 3 轮' in tip['text'] and 'kvsuite' in tip['text'],
              '预览含轮次与提问原文')
        pg.mouse.move(20, 300)
        pg.wait_for_timeout(120)
        check(pg.evaluate("document.getElementById('aiTurnTip').hidden"),
              '移开收起')

        print('[3] 点击跳转')
        pg.locator('#aiTurnNav .ai-turn-tick').nth(0).click()
        pg.wait_for_timeout(900)
        geo = pg.evaluate("""(() => {
          const box = document.getElementById('aiMsgs').getBoundingClientRect();
          const u = document.querySelectorAll('#aiMsgs > .ai-msg.ai-user')[0];
          return {off: Math.round(u.getBoundingClientRect().top - box.top),
                  flash: u.classList.contains('ai-turn-flash'),
                  tipHidden: document.getElementById('aiTurnTip').hidden};
        })()""")
        check(-2 <= geo['off'] <= 40, '第一轮落到视口顶部（偏移 %dpx）' % geo['off'])
        check(geo['flash'], '落点气泡闪光')
        check(geo['tipHidden'], '点完不残留预览')
        check(pg.evaluate(TICKS_ON)[0], '高亮切到第 1 轮')

        print('[4] 滚动跟随')
        pg.evaluate("const b = document.getElementById('aiMsgs');"
                    "b.scrollTop = b.scrollHeight;")
        pg.wait_for_timeout(200)
        check(pg.evaluate(TICKS_ON)[-1], '回到底部高亮最后一轮')
        pg.evaluate("document.getElementById('aiMsgs').scrollTop = 0")
        pg.wait_for_timeout(200)
        check(pg.evaluate(TICKS_ON)[0], '回到顶部高亮第一轮')

        print('[5] 不压提问气泡')
        pg.evaluate("const b = document.getElementById('aiMsgs');"
                    "b.scrollTop = b.scrollHeight;")
        pg.wait_for_timeout(150)
        gap = pg.evaluate("""(() => {
          const nav = document.getElementById('aiTurnNav').getBoundingClientRect();
          const us = [...document.querySelectorAll('#aiMsgs > .ai-msg.ai-user')];
          return Math.round(nav.left - Math.max(
            ...us.map(u => u.getBoundingClientRect().right)));
        })()""")
        check(gap >= 0, '气泡右缘与轨道留白 %dpx' % gap)

        print('[6] 收尾')
        pg.evaluate("""(() => {
          const box = document.getElementById('aiMsgs');
          [...box.querySelectorAll('.ai-msg.ai-user')].slice(1).forEach(n => n.remove());
          [...box.querySelectorAll('.ai-msg.ai-bot')].slice(1).forEach(n => n.remove());
        })()""")
        pg.wait_for_timeout(200)
        check(pg.evaluate(NAV_HIDDEN), '只剩 1 轮时隐藏')
        pg.evaluate("document.getElementById('aiMsgs').innerHTML = ''")
        pg.wait_for_timeout(200)
        check(pg.evaluate(NAV_HIDDEN) and pg.evaluate(TICKS_N) == 0,
              '清空对话后不留点')
        pg.evaluate(SEED_JS, [MD])
        pg.wait_for_timeout(300)
        check(pg.evaluate(TICKS_N) == 5, '重新开对话后打点恢复')

        print('[7] 轮次很多时点距收紧，不越出消息区')
        pg.evaluate(SEED_JS, [MD, 30])
        pg.wait_for_timeout(400)
        many = pg.evaluate("""(() => {
          const nav = document.getElementById('aiTurnNav');
          const nr = nav.getBoundingClientRect();
          const ts = [...nav.querySelectorAll('.ai-turn-tick')];
          const rs = ts.map(t => t.getBoundingClientRect());
          return {n: ts.length,
                  gap: parseFloat(getComputedStyle(nav).rowGap || 0),
                  inside: rs.every(r => r.top >= nr.top - 1 && r.bottom <= nr.bottom + 1),
                  spread: Math.round(rs[rs.length - 1].bottom - rs[0].top)};
        })()""")
        check(many['n'] == 30, '30 轮 → 30 个点')
        check(many['inside'], '所有点都在轨道高度内（纵向 %dpx）' % many['spread'])
        check(1 <= many['gap'] <= 8, '点距收紧到 %spx' % many['gap'])
        ctx.close()
        browser.close()

    check(not pageerrors, '无 pageerror%s' % ('' if not pageerrors else
                                              '：' + '; '.join(pageerrors)))
    print('\n== %s (%d fail)' % ('PASS' if not fails else 'FAIL', len(fails)))
    return 0 if not fails else 1


if __name__ == '__main__':
    sys.exit(main())
