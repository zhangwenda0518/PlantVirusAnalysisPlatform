# -*- coding: utf-8 -*-
"""页面状态快照（PS）契约检查。

为什么需要这个测试：
    平台一级导航是整页刷新，页面级状态（表单值、查询结果表、单次运行输出）
    只活在 JS 内存里，切走再回来就是空白 —— 用户 2026-11-02 明确抱怨过
    「其他页面也是这样」。修法是在 `webapp/static/app-state.js` 做一层
    sessionStorage 快照：表单自动记，结果面板靠模板里的 `data-ps` 标记。
    这层东西有两个容易退化的点，必须机器守住：
      ① 面板忘了标 `data-ps` → 看着像修好了，其实那页还是不记；
      ② 快照层偷偷发起请求（fetch / XHR）→ 恢复页面时重跑查询甚至重跑任务。
    所以这里断言「关键页确实标了」「快照层不碰网络」这类不变量，而不是跑浏览器
    （浏览器行为由 `tests/_it_page_state_ui.py` 守）。

跑法：python tests/_check_page_state.py
"""
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:          # noqa: BLE001
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(ok, msg):
    print(('  ✔ ' if ok else '  ✘ ') + msg)
    if not ok:
        FAILS.append(msg)


def read(rel):
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        return None
    with open(p, encoding='utf-8') as f:
        return f.read()


def marked_panes(html):
    """模板里标了 data-ps 的容器 id（<div id="x" ... data-ps>）。"""
    out = []
    for m in re.finditer(r'<[a-z]+[^>]*\bid="([A-Za-z0-9_\-]+)"[^>]*\bdata-ps\b[^>]*>', html or ''):
        out.append(m.group(1))
    return out


def main():
    ps = read('webapp/static/app-state.js')
    check(ps is not None, 'app-state.js 存在')
    if ps is None:
        print('FAILED: %d 项' % len(FAILS))
        return 1

    # ① 快照层自身
    check('window.PS' in ps, '导出 window.PS')
    check('sessionStorage' in ps, '用 sessionStorage（按标签页隔离）')
    check('pagehide' in ps, '离开页面前补一次落盘')
    check(('MAX_PAGE' in ps and 'MAX_ONE' in ps and 'TTL' in ps), '有体积上限与过期时间')
    check('data-ps' in ps, '支持 data-ps 面板快照')
    check(re.search(r'var VOLATILE\s*=', ps) is not None,
          '缓存击穿参数（t/_/ts）不参与页面键')
    # 恢复只能「显示」，不许发起任何请求（否则等于偷偷重跑查询/任务）
    check('fetch(' not in ps and 'XMLHttpRequest' not in ps,
          '快照层不发起任何网络请求')
    check('removeItem' in ps and 'forget' in ps, '提供丢弃本页快照的入口')
    # 面板是「自动跟踪」的（用户 2026-11-02：全部都做，都保留）：钩子必须挂在
    # Element.prototype 上。挂到 HTMLHtmlElement.prototype 上会拿不到 descriptor，
    # 钩子静默不装，页面一条面板都不会存（第一版就栽在这，浏览器实测两项红）。
    check("getOwnPropertyDescriptor(Element.prototype, 'innerHTML')" in ps,
          '面板自动跟踪：innerHTML 钩子挂在 Element.prototype 上')
    check('schedulePaneSave' in ps and 'function trackable' in ps,
          '面板自动跟踪：有写入即记录（无需模板标 data-ps）')
    check('MutationObserver' not in ps,
          '不再用 MutationObserver（整页重绘时监听会丢，自动跟踪已取代）')
    check('placeholderish' in ps and 'data-ps-off' in ps,
          '恢复只填空/占位容器，且支持 data-ps-off 排除')
    # 示例自动填充（examples.js autoFill）在 400ms 后填「空表单」，会把刚恢复的
    # 参数盖掉（用户 2026-11-02 第二次报「参数变了」的真凶）。有快照时必须让路。
    check('window.__vpNoAutoFill = true' in ps,
          '有快照时让示例自动填充让路（否则恢复的参数会被示例值盖回去）')
    check('volatileParam' in ps and re.search(r'p:\s*1', ps) is not None,
          'p=（页内会被 replaceState 改写的选中项）不参与页面键')

    # ② 挂载点：21 个模板都引 app.js，所以从 app.js 里动态挂载
    app = read('webapp/static/app.js') or ''
    check('/static/app-state.js' in app, 'app.js 动态挂载 app-state.js')
    check('window.PS' in app, '挂载前先判重（避免重复执行）')

    # ③ 关键页面真的标了面板
    refdata = read('webapp/templates/refdata.html') or ''
    rp = marked_panes(refdata)
    check('dsGenQryResult' in rp, '基因组查询页：组装查询结果面板已标记')
    check('dsQryResult' in rp, '基因组查询页：病毒序列查询结果面板已标记')
    check('data-ps-onchange="1"' in refdata,
          '模式下拉框恢复后要重跑 onchange（否则分区不显示）')

    tools = read('webapp/templates/tools.html') or ''
    tp = marked_panes(tools)
    check(len(tp) >= 5, '工具箱页至少 5 个结果面板已标记（实际 %d）' % len(tp))

    # ④ 样式：恢复提示条
    css = read('webapp/static/app.css') or ''
    check('.ps-restored' in css, '恢复提示条有样式')

    # ⑤ i18n 不需要键（提示语写死中文），但必须能中英混排不炸
    check('已恢复' in ps, '恢复提示为中文字面量（不依赖 i18n 表）')

    if FAILS:
        print('FAILED: %d 项' % len(FAILS))
        return 1
    print('页面状态快照契约检查通过 ✔')
    return 0


if __name__ == '__main__':
    sys.exit(main())
