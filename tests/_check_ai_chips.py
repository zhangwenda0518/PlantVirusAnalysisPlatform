#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 对话坞「快捷提示词」契约守卫（静态，纯源码比对，秒级）。

为什么需要这个测试
-----------------
2026-11-02 用户两次提「这一行还空着」「多给些提示词」：原来 4 个 chip 是写死在
`_nav.html` 里的按钮，与处境无关。改成 `AI._chipDefs()` 动态生成之后，多了一条
**新的失效面**：加一条提示词要同时动三个地方（defs / i18n zh / i18n en），
漏一个就是「中文有、英文还是 key」或者「按钮点了没反应」。

它守的是五件事
-------------
  A. 模板里只有容器：`_nav.html` 有 `id="aiChips"`，不再写死 `AI.chip('...')` 按钮；
  B. 生成端在位：`AI._chipDefs` / `AI.renderChips` / `AI.chip` 三个函数都在，
     且 chip 条数 ≥ 12（用户要「多给些」）；
  C. 双语齐全：每条 chip 的标题键（`ai.dock.chip*`）与问题键（`ai.dock.q*`）
     在 i18n.js 里各出现两次（zh + en）；
  D. 挂在正确的时机：`_ensureCtx` 拿到 ctx 后调 renderChips、`AI.toggle` 开坞时调、
     `syncSample`（顶栏换样品）调；
  E. 折叠开关完整：「更多/收起」两个键存在，`AI_CHIPS_VISIBLE` 不超过总条数。

跑法：python tests/_check_ai_chips.py   （退出码 0 = 通过）
"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, 'webapp', 'static', 'app-ai.js')
NAV = os.path.join(ROOT, 'webapp', 'templates', '_nav.html')
I18N = os.path.join(ROOT, 'webapp', 'static', 'i18n.js')

FAILS = []


def check(ok, msg):
    print(('  ✔ ' if ok else '  ✘ ') + msg)
    if not ok:
        FAILS.append(msg)


def read(path):
    with io.open(path, encoding='utf-8') as f:
        return f.read()


def main():
    js = read(JS)
    nav = read(NAV)
    i18n = read(I18N)

    print('A. 模板只有容器')
    check('<div class="ai-chips" id="aiChips">' in nav, '_nav.html 有 id="aiChips" 容器')
    check("onclick=\"AI.chip('interpret')\"" not in nav,
          '模板不再写死 chip 按钮（否则动态生成会被它顶掉）')

    print('B. 生成端在位')
    check('AI._chipDefs = function' in js, 'AI._chipDefs 存在')
    check('AI.renderChips = function' in js, 'AI.renderChips 存在')
    check(re.search(r'AI\.chip = function \(kind\)', js) is not None, 'AI.chip(kind) 存在')
    ids = re.findall(r"\{ id: '([a-z0-9_]+)'", js)
    check(len(ids) >= 12, 'chip 条数 ≥ 12（实际 %d：%s）' % (len(ids), ' '.join(ids)))
    check(len(set(ids)) == len(ids), 'chip id 不重复')

    print('C. 双语齐全')
    label_keys = re.findall(r"k: '(ai\.dock\.chip[A-Za-z0-9_]+)'", js)
    q_keys = re.findall(r"t\('(ai\.dock\.q[A-Za-z0-9_]+)'", js)
    check(len(label_keys) >= 12, '标题键 ≥ 12（实际 %d）' % len(label_keys))
    missing = []
    for k in sorted(set(label_keys) | set(q_keys)):
        n = len(re.findall(r"'%s'\s*:" % re.escape(k), i18n))
        if n < 2:
            missing.append('%s（%d 处）' % (k, n))
    check(not missing, '每个 ai.dock.chip*/q* 键在 zh 与 en 各有一份'
          + ('' if not missing else '；缺：' + '，'.join(missing)))
    check("'ai.dock.chipMore'" in i18n and "'ai.dock.chipLess'" in i18n,
          '「更多/收起」两个文案键在 i18n 里')

    print('D. 挂在正确的时机')
    m = re.search(r'AI\._ensureCtx[\s\S]{0,3000}?AI\.renderChips\(\)', js)
    check(m is not None, '_ensureCtx 拿到上下文后重画提示词')
    m = re.search(r'if \(want\) \{[\s\S]{0,600}?AI\.renderChips\(\)', js)
    check(m is not None, 'AI.toggle 开坞时重画（否则首屏空行）')
    m = re.search(r'const syncSample = \(\) => \{[\s\S]{0,300}?AI\.renderChips\(\)', js)
    check(m is not None, '顶栏换样品（vp-ctx）后跟着重画')

    print('E. 折叠与上下文')
    m = re.search(r'AI_CHIPS_VISIBLE\s*=\s*(\d+)', js)
    check(m is not None and 4 <= int(m.group(1)) <= 8,
          'AI_CHIPS_VISIBLE 是 4~8 的合理值（默认露出条数）')
    check("localStorage.setItem(AI_CHIPS_MORE_KEY" in js, '「更多」开合状态持久化')
    check('when: c => c.running > 0' in js, '「还要多久」只在有任务在跑时出现')
    check('when: c => has() || c.failed > 0' in js, '「为什么失败」按样品/失败任务出现')

    if FAILS:
        print('\nFAILED: %d 项不变量被改坏' % len(FAILS))
        return 1
    print('\n快捷提示词契约检查通过 ✔')
    return 0


if __name__ == '__main__':
    sys.exit(main())
