#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 对话坞「跨页持久化」不变量守卫（静态，纯源码比对，秒级）。

为什么需要这个测试
------------------
2026-11-02 之前，「切模块回来问答空白」被当成过三次「已修」：

  1) 修 _saveHistory（结果：写进去了，但读不回来）
  2) 修 _restoreHistory（结果：能读，但读的动作被放在 await fetch 之后）
  3) 修 AI.clear 的镜像（结果：清空倒是干净了，恢复又被人改写）

每次都不是「同一个 bug 没修好」，而是**修另一个问题时把这条链上的某一环改回去了**。
行为测试（tests/_it_ai_persist_ui.py）能抓到结果，但要起浏览器、十几秒；
这个测试把「这条链必须长这样」写成断言，1 秒内跑完，改坏立刻红。

它守的是四件事
--------------
  A. 恢复不依赖网络：restoreOnce 体内不许出现 fetch/await，且 _ensureCtx 里
     「先 restoreOnce，再 fetch('/api/ai/context')」的顺序不许颠倒；
  B. 两处落盘：_saveHistory 同时写会话键与镜像键 vp_ai_msgs:__last；
  C. 兜底可读：_restoreHistory 读不到会话键时回落镜像，并把会话 id 认回来；
  D. 清空不留尾巴：AI.clear 连镜像一起删；镜像不占 _listConvs 的段数、
     不被 _pruneHistory 裁掉；pagehide 时落盘。

跑法：python tests/_check_ai_dock_persist.py   （退出码 0 = 通过）
"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'webapp', 'static', 'app-ai.js')

FAILS = []


def check(ok, msg):
    print(('  ✔ ' if ok else '  ✘ ') + msg)
    if not ok:
        FAILS.append(msg)


def body_of(src, header_regex):
    """取某个函数体（从 header 起，到下一个顶层 `};` 或下一个 `AI.xxx =` 为止）。"""
    m = re.search(header_regex, src)
    if not m:
        return None
    start = m.end()
    # 找匹配的结尾：下一个顶层赋值 / 注释块
    tail = re.search(r'\n(?:AI\.[A-Za-z_]+ = |/\* -{3,})', src[start:])
    end = start + (tail.start() if tail else len(src) - start)
    return src[start:end]


def main():
    print('AI 对话坞持久化不变量检查')
    if not os.path.isfile(SRC):
        print('  ✘ 找不到 ' + SRC)
        return 1
    with io.open(SRC, encoding='utf-8', errors='replace') as f:
        src = f.read()

    # ---------- A. 恢复不依赖网络 ----------
    check("AI_LAST_KEY = 'vp_ai_msgs:__last'" in src,
          "镜像键常量存在（AI_LAST_KEY = 'vp_ai_msgs:__last'）")
    once = body_of(src, r'AI\.restoreOnce = function \([^)]*\) \{')
    check(once is not None, 'restoreOnce() 仍然存在')
    if once is not None:
        check('fetch(' not in once, 'restoreOnce 体内不出现 fetch（恢复不等网络）')
        check('await ' not in once, 'restoreOnce 体内不出现 await（同步可读）')
        check('_restoreHistory' in once, 'restoreOnce 走的还是 _restoreHistory')

    ec = body_of(src, r'AI\._ensureCtx = async function \([^)]*\) \{')
    check(ec is not None, '_ensureCtx() 仍然存在')
    if ec is not None:
        i_restore = ec.find('AI.restoreOnce()')
        i_fetch = ec.find("/api/ai/context")
        check(i_restore >= 0, '_ensureCtx 里调用了 restoreOnce')
        check(i_fetch >= 0, '_ensureCtx 里仍然拉 /api/ai/context')
        check(i_restore >= 0 and i_fetch >= 0 and i_restore < i_fetch,
              '顺序正确：先 restoreOnce()，再 fetch(上下文)')
        check('catch' in ec and ec.count('restoreOnce') >= 2,
              '失败分支也恢复（catch 里再调一次 restoreOnce）')

    # ---------- B. 两处落盘 ----------
    save = body_of(src, r'AI\._saveHistory = function \([^)]*\) \{')
    check(save is not None, '_saveHistory() 仍然存在')
    if save is not None:
        check('AI.msgsKey()' in save, '_saveHistory 写会话键（AI.msgsKey()）')
        check('AI_LAST_KEY' in save, '_saveHistory 同时写镜像键（AI_LAST_KEY）')

    # ---------- C. 兜底可读 ----------
    restore = body_of(src, r'AI\._restoreHistory = function \([^)]*\) \{')
    check(restore is not None, '_restoreHistory() 仍然存在')
    if restore is not None:
        check('localStorage' in restore, '_restoreHistory 从 localStorage 读（同步、可离线）')
        check('fetch(' not in restore and 'await ' not in restore,
              '_restoreHistory 体内不出现 fetch/await')
        check('AI_LAST_KEY' in restore, '_restoreHistory 会读镜像键')
        check('AI_CONV_KEY' in restore or 'AI.convId =' in restore,
              '_restoreHistory 会把会话 id 认回来（写回 AI.convId / sessionStorage）')

    # ---------- D. 清空不留尾巴 ----------
    clr = body_of(src, r'AI\.clear = function \([^)]*\) \{')
    check(clr is not None, 'AI.clear() 仍然存在')
    if clr is not None:
        check('AI_LAST_KEY' in clr, 'AI.clear 连镜像键一起删（清空后不复活）')

    prune = body_of(src, r'AI\._pruneHistory = function \([^)]*\) \{')
    check(prune is not None and 'AI_LAST_KEY' in prune,
          '_pruneHistory 跳过镜像键（镜像不被当第 13 段裁掉）')

    convs = body_of(src, r'AI\._listConvs = function \([^)]*\) \{')
    check(convs is not None and 'AI_LAST_KEY' in convs,
          '_listConvs 跳过镜像键（镜像不占会话段数）')

    # pagehide 落盘：从 window.addEventListener('pagehide' 起取 800 字符
    m = re.search(r"addEventListener\('pagehide'", src)
    check(m is not None, "存在 pagehide 监听（离开页面兜底）")
    if m:
        seg = src[m.start():m.start() + 900]
        check('_saveHistory' in seg, 'pagehide 里会调用 _saveHistory（首 2s 内离开也不丢）')

    # boot 里恢复（换页回来即使坞没开过也要先恢复）：boot 是 `const boot = () => {`
    m2 = re.search(r'AI\.startEvents\(\)[\s\S]{0,800}?AI\.restoreOnce\(\)', src)
    check(m2 is not None, 'boot() 里调用 restoreOnce（页面加载即恢复，不等用户点开）')

    print()
    if FAILS:
        print('FAILED: %d 项不变量被改坏' % len(FAILS))
        return 1
    print('AI 对话坞持久化不变量通过 ✔')
    return 0


if __name__ == '__main__':
    sys.exit(main())
