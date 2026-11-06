# -*- coding: utf-8 -*-
"""模块切换不刷新（app-shell.js）——静态契约检查。

用户 2026-11-02：「你还是切换页面模块，你还是刷新，你到底能不能解决问题」。
这条守的是「切模块不再整页重载」的实现要点，浏览器行为由
`tests/_it_page_shell_ui.py` 实测（点导航 → 父页不跳转、iframe 保活、切回原样）。
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 直接 `python tests/_check_page_shell.py` 运行时 sys.path[0] 是 tests/，
# 运行时断言要 import Virus_Platform_Core；pytest 下 conftest 已插入，重复无害。
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
fails = []


def check(ok, msg):
    print(('  \u2714 ' if ok else '  \u2718 ') + msg)
    if not ok:
        fails.append(msg)


def read(rel):
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        return None
    return io.open(p, encoding='utf-8').read()


def main():
    print('模块切换保活外壳检查')
    js = read('webapp/static/app-shell.js')
    check(js is not None, 'app-shell.js 存在')
    if js is None:
        return 1

    check('window.VP_SHELL' in js, '导出 window.VP_SHELL（可观测/可测试）')
    check('postMessage' in js and 'vpNav' in js,
          'iframe 内导航回传父层（不套娃 iframe）')
    check("new URL(a.getAttribute('href')" in js, '按 URL 解析链接')
    check('u.origin !== location.origin' in js, '只拦同源链接')
    check("a.target && a.target !== '_self'" in js, 'target=_blank 等交给浏览器')
    check("a.hasAttribute('download')" in js, '下载链接不拦')
    check('ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey' in js,
          '按住 Ctrl/Cmd/Shift 时按原样开新标签')
    check('MAX_FRAMES' not in js and 'evictIfNeeded' not in js,
          '所有模块都保活，不设上限、不淘汰（2026-11-02 用户要求）')
    check('count: function' in js, '可观测当前保活了多少个模块')
    check("searchParams.set('__frame', '1')" in js, 'iframe 带 __frame=1')
    check('shell-mod' in js, '父层进外壳模式（主内容藏起来）')
    check('showOwn' in js, '点回本页地址时收回外壳')

    css = read('webapp/static/app.css') or ''
    check('body.shell-mod > .page' in css and 'body.shell-mod > main' in css,
      '外壳模式隐藏父文档主内容（main / .page 两种骨架都要盖住）')
    check('.vp-shell-host' in css and '.vp-shell-frame.on' in css,
          '外壳容器与 iframe 显隐样式')
    check('html.vp-frame body > .sidenav' in css, 'iframe 内隐藏自己的侧栏')
    # 2026-11-02 回归：iframe 里必须留着页内浮层（AI 入口 + 运行条），父层反过来别浮
    check('html.vp-frame body > .ai-dock' not in css,
          'iframe 内保留 AI 对话坞（藏掉就再没有 AI 入口）')
    check('html.vp-frame body > .ai-fab' not in css,
          'iframe 内保留 AI 悬浮球')
    check('html.vp-frame body > .runbar' not in css,
          'iframe 内保留运行条（父层运行条会压住 iframe 里的坞）')
    check('body.shell-mod > .ai-fab' in css and 'body.shell-mod > .ai-dock' in css
          and 'body.shell-mod > .runbar' in css
          and 'body.shell-mod > #vpAnchorBar' in css,
          '外壳模式父层不浮 AI/运行条/锚点条（浮层只由当前 iframe 出）')

    nav = read('webapp/templates/_nav.html') or ''
    check('vp-frame' in nav and "__frame" in nav,
          '模板提前打 vp-frame（避免切模块闪双层导航）')

    # 2026-11-02 定案：悬浮球改成**独立问答平台**，工作台入口搬到顶栏 🧠。
    # 两条通道必须都在，而且球绝不能再指向工作台。
    check('id="qaDock"' in nav, '导航模板里有问答面板 #qaDock')
    # 球走 QA.onBallClick() 而不是裸 QA.toggle()：单击延后 220ms，好让双击复位
    # 抢得到第二下（面板一开就盖住球）；工作台按钮才是直接 AI.toggle()。
    _fab_tag = re.search(r'<button[^>]*id="aiFab"[^>]*>', nav)
    check(bool(_fab_tag) and 'QA.onBallClick()' in _fab_tag.group(0)
          and 'AI.toggle' not in _fab_tag.group(0),
          '悬浮球打开的是问答面板 QA（不是工作台）')
    check('id="aiDockBtn"' in nav and 'onclick="AI.toggle()"' in nav,
          '顶栏有 AI 工作台入口按钮')
    check('/static/app-qa.js' in nav, '问答面板脚本已引入')
    check(nav.index('app-ai.js') < nav.index('app-qa.js'),
          'app-qa.js 在 app-ai.js 之后加载（互斥逻辑要能读到 window.AI）')
    check('不读取' in nav or 'does not read' in nav,
          '问答面板写明边界（不读数据/不跑任务）')

    qa = read('webapp/static/app-qa.js') or ''
    check("mode: 'qa'" in qa, '问答请求带 mode=qa')
    check('vp_qa_msgs' in qa and 'vp_qa_open' in qa,
          '问答用自己的存储键（与工作台的 vp_ai_msgs / vp_ai_open 分开）')
    check("fetch('/api/ai/chat'" in qa, '问答复用 /api/ai/chat 端点（靠 mode 分流）')
    check('ai-inline' in qa, '首页内嵌工作台不会被问答面板收掉')

    chat = read('Virus_Platform_Core/ai_agent/chat.py') or ''
    check("== 'qa'" in chat and '_QA_RULES_ZH' in chat,
          '后端识别 qa 模式并换成问答规则')
    # 2026-11-06 P0：只 grep `use_tools = not qa` 不够——实测
    # `_plan_tools(False)` 仍返回 17 个工具（含 propose_action / mcp_call），
    # 且 dispatch 不拦截。这里保留原 grep（标志仍存在），再直接跑一轮
    # qa 请求，断言真正传给 chat_stream 的 tools 是 None、dispatch 不被调用。
    check('use_tools = not qa' in chat, '问答模式不发 tools（物理上不驱动任务）')
    check('_plan_tools(use_tools and' not in chat,
          'qa 不能把 use_tools 当 _plan_tools 的 allow_plan 参数（会退回全量工具）')
    check('if not use_tools' in chat, '工具调用分支有 use_tools 兜底拦截')
    check('_qa_system_prompt' in chat and 'AGENTS.md' in chat,
          '问答说明来自 AGENTS.md（平台用法单一事实源）')

    # 运行时断言：qa 请求构造出的 tools 参数必须为 None，且幻觉 tool_calls
    # 不得进入 ai_tools.dispatch。
    try:
        from Virus_Platform_Core.ai_agent import chat as _chat
        from Virus_Platform_Core.ai_agent import tools as _tools
        seen = {}
        dispatched = []

        def _fake_stream(ai_cfg, convo, tools=None, max_tokens=None, **kw):
            seen['tools'] = tools
            if not seen.get('tool_emitted'):
                seen['tool_emitted'] = True
                yield {'type': 'tool_calls', 'tool_calls': [
                    {'id': 'tc-qa', 'type': 'function',
                     'function': {'name': 'read_result_table',
                                  'arguments': '{"sample":"S","relpath":"x.tsv"}'}}]}
                return
            yield {'type': 'delta', 'content': '好的'}
            yield {'type': 'finish', 'reason': 'stop'}

        _orig_stream = _chat.chat_stream
        _orig_prompt = _chat._qa_system_prompt
        _orig_dispatch = _tools.dispatch
        _chat.chat_stream = _fake_stream
        _chat._qa_system_prompt = lambda question='': 'qa stub'
        _tools.dispatch = lambda name, args: dispatched.append(name) or '{}'
        try:
            evs = list(_chat.run_chat(
                {'model': 'stub', 'max_tokens': 64},
                [{'role': 'user', 'content': '你好'}], mode='qa'))
        finally:
            _chat.chat_stream = _orig_stream
            _chat._qa_system_prompt = _orig_prompt
            _tools.dispatch = _orig_dispatch
        check(seen.get('tools') is None,
              'qa 请求构造出的 tools 参数为 None（不是"只删 plan 动作"）')
        check(not dispatched,
              'qa 模式模型幻觉 tool_calls 也不进 ai_tools.dispatch')
        check(any('"done"' in str(e) for e in evs), 'qa 请求正常收尾')
    except Exception as e:                     # noqa: BLE001 - 检查项本身失败要报红
        check(False, f'qa tools 运行时断言失败: {type(e).__name__}: {e}')

    app = read('webapp/static/app.js') or ''
    check("/static/app-shell.js" in app, 'app.js 动态挂载 app-shell.js')
    check('window.VP_SHELL' in app, '挂载前先判重')
    check('{snap: true}' in app, '悬浮球拖动后贴边（vpDraggable 的 snap）')

    # 2026-11-05：「设置页面需要刷新才生效」——保活外壳下设置页在 iframe 里，
    # 保存/切主题只改当前文档时，外壳与其它模块仍是旧状态。总线负责跨文档同步。
    check('window.VP_SYNC' in app, 'app.js 导出跨文档设置同步总线 VP_SYNC')
    check('vpSettingsChanged' in app and 'vpTheme' in app
          and 'vpReloadAll' in app,
          '总线覆盖设置变更 / 主题 / 整窗重载三种消息')
    check("document.addEventListener('vp:settings-changed'" in app,
          '各页监听设置变更并软刷新（不重载页面）')
    check('loadDefaults' in app and 'loadDbs' in app and 'AI.refreshReady' in app,
          '软刷新覆盖分析默认参数 / 库徽标 / AI 就绪态')
    check('envStripLoad' in app, '设置变更后顶栏环境条同步刷新')
    tools_html = read('webapp/templates/tools.html') or ''
    check('envStripLoad' in tools_html,
          '工具页切卡后主动刷新环境条口径（replaceState 不触发 hashchange）')

    i18n = read('webapp/static/i18n.js') or ''
    check('VP_SYNC.reloadAll' in i18n,
          'setLang 走整窗重载（外壳下不只刷新当前 iframe）')

    st = read('webapp/templates/settings.html') or ''
    check('VP_SYNC.theme' in st, '设置页主题按钮广播主题（外壳同步变色）')
    check('VP_SYNC.reloadAll' in st, '导航位置切换整窗重载')
    check(st.count('VP_SYNC.settingsChanged') >= 3,
          '保存设置 / 目录应用 / AI 保存后广播变更')
    check('pluginChanged' in st and 'VP_SYNC.settingsChanged' in st,
          '插件增删后广播（其它模块的工具状态同步刷新）')

    # 不许在快照层偷偷发请求的老规矩继续有效：外壳只在 iframe 里加载页面本身
    check('fetch(' not in js and 'XMLHttpRequest' not in js,
          '外壳不用 fetch 抓页面（直接 iframe，页内脚本照常执行）')

    print('')
    if fails:
        print('模块切换保活外壳检查未通过（%d 项）' % len(fails))
        return 1
    print('模块切换保活外壳检查通过 \u2714')
    return 0


if __name__ == '__main__':
    sys.exit(main())

