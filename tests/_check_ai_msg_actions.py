#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 对话坞「导出/重试挪进消息流」结构守卫（静态，纯源码比对，秒级）。

为什么需要这个测试
------------------
2026-11-06 用户定案：底部工具条的「⬇ 导出会话」「↻ 重新上一句」挪进消息流——
  · 导出挂每条 AI 回复的操作条（_addCopyBar，与复制/👍👎并排）；
  · 重试挂每条用户提问下方（_addRetryRow，点旧提问=从那一问重来）。
把「必须长这样」写成断言，防止后续改坞时被人改回去（与
_check_ai_dock_persist.py 同款动机：行为测试要起浏览器，静态守卫秒级红绿）。

它守的是五件事
--------------
  A. 工具条干净：_nav.html 的 .ai-dock-foot-ops 不再含两按钮 id；
  B. 导出在复制条：_addCopyBar 含 ai-msg-export + AI.exportConv；
  C. 重试行三路径全覆盖：发送路径 / _restoreHistory / _serverTopUp 都调
     _addRetryRow，且行挂气泡内部（.ai-msg-actions 在 ub/b 内部）；
  D. 按气泡重试：AI.retryFrom 存在，菜单「原样重试」走 retryFrom(该气泡)，
     全文件无 $('aiRetryBtn') / $('aiExportBtn') 字面量残留；
  E. 不污染轮次导航：_turnLabel 剔除 .ai-msg-actions；i18n 双语含新 key。

跑法：python tests/_check_ai_msg_actions.py   （退出码 0 = 通过）
"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'webapp', 'static', 'app-ai.js')
NAV = os.path.join(ROOT, 'webapp', 'templates', '_nav.html')
I18N = os.path.join(ROOT, 'webapp', 'static', 'i18n.js')
CSS = os.path.join(ROOT, 'webapp', 'static', 'app.css')

FAILS = []


def check(ok, msg):
    print(('  ✔ ' if ok else '  ✘ ') + msg)
    if not ok:
        FAILS.append(msg)


def body_of(src, header_regex):
    """取某个函数体（从 header 起到下一个顶层 `AI.xxx =` 或 `/* ---` 注释块）。"""
    m = re.search(header_regex, src)
    if not m:
        return None
    start = m.end()
    tail = re.search(r'\n(?:AI\.[A-Za-z_]+ = |/\* -{3,})', src[start:])
    end = start + (tail.start() if tail else len(src) - start)
    return src[start:end]


def main():
    print('AI 对话坞「导出/重试挪进消息流」结构检查')
    missing = [p for p in (SRC, NAV, I18N, CSS) if not os.path.isfile(p)]
    if missing:
        print('  ✘ 找不到: ' + ', '.join(missing))
        return 1
    with io.open(SRC, encoding='utf-8', errors='replace') as f:
        src = f.read()
    with io.open(NAV, encoding='utf-8', errors='replace') as f:
        nav = f.read()
    with io.open(I18N, encoding='utf-8', errors='replace') as f:
        i18n = f.read()
    with io.open(CSS, encoding='utf-8', errors='replace') as f:
        css = f.read()

    # ---------- A. 工具条干净 ----------
    check('aiExportBtn' not in nav and 'aiRetryBtn' not in nav,
          'A1 _nav.html 工具条不再含 aiExportBtn / aiRetryBtn')
    check('AI.exportConv' in src,
          'A2 AI.exportConv 仍存在（消息级按钮复用同一实现）')

    # ---------- B. 导出在复制条 ----------
    bar = body_of(src, r'function _addCopyBar\([^)]*\) \{')
    check(bar is not None and 'ai-msg-export' in bar
          and 'AI.exportConv' in bar,
          'B1 _addCopyBar 含 ai-msg-export 按钮 + AI.exportConv')

    # ---------- C. 重试行三路径全覆盖 ----------
    check('function _addRetryRow' in src,
          'C1 _addRetryRow 已定义')
    check(src.count('_addRetryRow(b)') == 2,
          'C2 _restoreHistory / _serverTopUp 各调一次 _addRetryRow(b)')
    check(src.count('_addRetryRow(ub)') == 2,
          'C3 发送路径调 _addRetryRow(ub)（定义签名+调用点共 2 处）')
    row = body_of(src, r'function _addRetryRow\([^)]*\) \{')
    check(row is not None and 'ai-msg-actions' in row
          and 'ai-msg-act-retry' in row,
          'C4 行类名 ai-msg-actions / 按钮 ai-msg-act-retry')

    # ---------- D. 按气泡重试 + 无工具条字面量残留 ----------
    check('AI.retryFrom = function' in src,
          'D1 AI.retryFrom(bubbleEl) 已定义')
    menu = body_of(src, r'AI\.toggleRetryMenu = function \([^)]*\) \{')
    check(menu is not None and 'AI._retryBubble' in menu
          and 'AI.retryFrom(AI._retryBubble)' in menu,
          'D2 菜单「原样重试」按气泡走 AI.retryFrom(AI._retryBubble)')
    check("$('aiRetryBtn')" not in src and "$('aiExportBtn')" not in src,
          'D3 app-ai.js 无工具条按钮字面量残留')
    sync = body_of(src, r'AI\._syncRetryRows = function \([^)]*\) \{')
    check(sync is not None and '.ai-msg-act-retry' in sync,
          'D4 _syncRetryRows 同步所有 .ai-msg-act-retry 的 busy 状态')

    # ---------- E. 不污染轮次导航 + i18n ----------
    label = body_of(src, r'AI\._turnLabel = function \([^)]*\) \{')
    check(label is not None and '.ai-msg-actions' in label,
          'E1 _turnLabel 预览剔除 .ai-msg-actions')
    check(i18n.count("'ai.msg.retry'") == 2 and i18n.count("'ai.msg.export'") == 2,
          'E2 i18n zh/en 双语含 ai.msg.retry / ai.msg.export')
    check('.ai-msg-actions' in css and '.ai-msg-act-retry' in css,
          'E3 app.css 有 .ai-msg-actions / .ai-msg-act-retry 规则')

    print()
    if FAILS:
        print(f'✘ {len(FAILS)} 项不通过')
        return 1
    print('✔ 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
